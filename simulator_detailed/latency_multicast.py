"""One packet injection with real shared-prefix multicast FIFO occupancy."""
import json
import math
import simpy
from .latency_fifo import _edge
from .configs.schemas.generic_transactions import GenericHopSpan


def transfer_multicast(context,transaction):
    try:
        yield from context._start(transaction.depends_on,transaction.start_cycles)
    except simpy.Interrupt:
        return
    start=float(context.env.now)
    context._emit('transaction_start',transaction.transaction_id)
    groups=getattr(context,'_fifo_multicast_groups',None)
    if groups is None:groups={};context._fifo_multicast_groups=groups
    group=transaction.multicast_group
    if group not in groups:
        members=[t for t in context.plan.content.transactions if getattr(t,'multicast_group',None)==group]
        state=dict(members=members,done={t.transaction_id:context.env.event() for t in members},started=False,
                   spans={t.transaction_id:[[] for h in t.hops] for t in members})
        groups[group]=state
        state['process']=context.spawn_background(_run_group(context,state), deadline=context.plan.content.max_cycles)
    state=groups[group]
    try:
        yield state['done'][transaction.transaction_id]
        spans=[]
        for h,rows in zip(transaction.hops,state['spans'][transaction.transaction_id]):
            spans.append(GenericHopSpan(network_id=h.network_id,link_id=h.link_id,
                queued_cycles=min(x[0] for x in rows),serialization_start_cycles=min(x[1] for x in rows),
                serialization_end_cycles=max(x[2] for x in rows),arrival_cycles=max(x[3] for x in rows)))
        context._finish(transaction.transaction_id,'transfer',start,0.0,tuple(spans))
    except simpy.Interrupt:
        if state['process'].is_alive and not state.get('cancelling'):
            state['cancelling']=True;state['process'].interrupt()


def _run_group(context,state):
    members=state['members'];tree={}
    context._fifo_events=getattr(context,'_fifo_events',[])
    for member in members:
        current=tree
        for hi,hop in enumerate(member.hops):
            node=current.setdefault(hop.link_id,dict(hop=hop,children={},members=[]))
            node['members'].append((member.transaction_id,hi));current=node['children']
    packet_bytes=members[0].hops[0].timing.packet_bytes
    count=math.ceil(members[0].payload_bytes/packet_bytes)
    processes=[];delivered={t.transaction_id:0 for t in members}
    admits=[context.env.event() for _ in range(count)]

    def emit(node,action,packet,rid):
        owner=min(tid for tid,hi in node['members'])
        for tid,hi in node['members']:
            context._fifo_events.append(dict(action=action,transaction_id=tid,packet_index=packet,
                stage_index=hi,resource_id=rid,time_cycles=float(context.env.now),owner=owner))
            context._emit('fifo_event',tid,rid,json.dumps(dict(action=action,packet=packet,stage=hi,owner=owner),
                sort_keys=True,separators=(',',':')))

    def node_run(node,index,sent_event,fork_ready=None,fork_all=None):
        t=node['hop'].timing;queued=float(context.env.now);held=[]
        local=None
        try:
            if t.stage=='ingress':
                entry=context.registry.get(t.input_resource_id);slot=entry.credits.request();held.append((entry.credits,slot))
                yield slot;emit(node,'queue_acquire',index,entry.resource_id)
                yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                emit(node,'fifo_admit',index,entry.resource_id)
                if not admits[index].triggered:admits[index].succeed()
                sent=float(context.env.now)
                yield context.env.timeout(t.input_latency_cycles);emit(node,'fifo_visible',index,entry.resource_id)
                local=(entry.credits,slot,entry.resource_id)
                if not sent_event.triggered:sent_event.succeed()
            else:
                issue_entry=context.registry.get(t.output_resource_id);channel=context.registry.get(t.channel_resource_id)
                issue=issue_entry.credits.request();held.append((issue_entry.credits,issue));yield issue
                slot=channel.credits.request();held.append((channel.credits,slot));yield slot
                emit(node,'queue_acquire',index,channel.resource_id)
                if fork_ready is not None:
                    fork_ready.succeed();yield fork_all
                now=float(context.env.now)
                if now%t.issue_cycles:yield context.env.timeout(t.issue_cycles-now%t.issue_cycles)
                sent=float(context.env.now);emit(node,'packet_forward',index,issue_entry.resource_id)
                issue_entry.service_count+=1;issue_entry.busy_cycles+=t.issue_cycles
                sent_event.succeed()
                def release_issue():
                    try:yield context.env.timeout(t.issue_cycles)
                    finally:context.registry.release(issue_entry.credits,issue)
                context.spawn_background(release_issue(),deadline=sent+t.issue_cycles);held.remove((issue_entry.credits,issue))
                yield context.env.timeout(t.channel_latency_cycles)
                if t.stage=='transit':
                    entry=context.registry.get(t.input_resource_id);input_slot=entry.credits.request();held.append((entry.credits,input_slot));yield input_slot
                    emit(node,'queue_acquire',index,entry.resource_id)
                    yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                    emit(node,'fifo_admit',index,entry.resource_id)
                    emit(node,'queue_release',index,channel.resource_id);held.remove((channel.credits,slot));context.registry.release(channel.credits,slot)
                    yield context.env.timeout(t.input_latency_cycles);emit(node,'fifo_visible',index,entry.resource_id)
                    local=(entry.credits,input_slot,entry.resource_id)
                else:
                    yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                    emit(node,'queue_release',index,channel.resource_id);held.remove((channel.credits,slot));context.registry.release(channel.credits,slot)
                    emit(node,'packet_delivered',index,channel.resource_id)
                    for tid,hi in node['members']:
                        delivered[tid]+=1
                        if delivered[tid]==count:state['done'][tid].succeed()
            arrived=float(context.env.now)
            for tid,hi in node['members']:state['spans'][tid][hi].append((queued,sent,sent if t.stage=='ingress' else sent+t.issue_cycles,arrived))
            children=[];signals=[]
            readiness=[context.env.event()for _ in node['children']]if len(node['children'])>1 else []
            joint=context.env.all_of(readiness)if readiness else None
            for ci,child in enumerate(node['children'].values()):
                signal=context.env.event();signals.append(signal)
                process=context.env.process(node_run(child,index,signal,readiness[ci]if readiness else None,joint));children.append(process);processes.append(process)
            if signals:yield context.env.all_of(signals)
            if local:
                resource,request,rid=local;emit(node,'queue_release',index,rid)
                held.remove((resource,request));context.registry.release(resource,request)
            if children:yield context.env.all_of(children)
        except simpy.Interrupt:pass
        finally:
            for resource,request in held:
                if request.triggered:
                    rid=next(e.resource_id for e in context.registry.entries() if e.credits is resource)
                    if rid!=t.output_resource_id:emit(node,'queue_release',index,rid)
                    context.registry.release(resource,request)
                else:request.cancel()

    def packet(index):
        try:
            if index:yield admits[index-1]
            starts=[]
            for node in tree.values():
                signal=context.env.event();p=context.env.process(node_run(node,index,signal));starts.append(p);processes.append(p)
            yield context.env.all_of(starts)
        except simpy.Interrupt:pass
    try:
        roots=[context.env.process(packet(i)) for i in range(count)];processes.extend(roots)
        yield context.env.all_of(roots)
    except simpy.Interrupt:
        pass
    finally:
        for p in processes:
            if p.is_alive:p.interrupt()
        try:
            if processes:yield context.env.all_of(processes)
        finally:
            context._fifo_multicast_groups.pop(members[0].multicast_group,None)
