"""Clocked bounded packet FIFO transport on the unified RuntimeContext.

Explicit input/channel queues retain packet ownership until downstream
admission. A slot release has no separate delayed credit event. Packets are
pipelined; only per-output issue is serialized. No private device vocabulary
or default service value is embedded here.
"""
import math
import simpy
import json
from .configs.schemas.generic_transactions import GenericHopSpan


def _edge(now, interval):
    return (math.floor(now / interval) + 1) * interval


def transfer_latency_fifo(context, transaction):
    try:
        yield from context._start(transaction.depends_on, transaction.start_cycles)
    except simpy.Interrupt:
        return
    flow_owner=None
    if transaction.hops[-1].timing.flow_serialized:
        entry=context.registry.get(transaction.hops[-1].timing.output_resource_id)
        request=entry.serializer.request(priority=transaction.flow_priority)
        try:yield request
        except simpy.Interrupt:
            if request.triggered:context.registry.release(entry.serializer,request)
            else:request.cancel()
            return
        flow_owner=(entry.serializer,request)
    start = float(context.env.now)
    context._emit('transaction_start',transaction.transaction_id)
    hops = transaction.hops
    packet_bytes = hops[0].timing.packet_bytes
    if any(getattr(h.timing,'mode',None)!='latency_fifo' or h.timing.packet_bytes!=packet_bytes for h in hops):
        raise ValueError('LATENCY_FIFO_MIXED_PACKET_CONTRACT')
    spans=[[] for _ in hops]
    packet_processes=[]
    context._fifo_events=getattr(context,'_fifo_events',[])
    previous_input=None
    admitted=[context.env.event() for _ in range(math.ceil(transaction.payload_bytes/packet_bytes))]

    def record(action,packet,stage,resource):
        row=dict(action=action,transaction_id=transaction.transaction_id,
            packet_index=packet,stage_index=stage,resource_id=resource,time_cycles=float(context.env.now))
        entry=context.registry.get(resource)
        if entry.credits is not None:
            row.update(occupancy=len(entry.credits.users),queued=len(entry.credits.queue),capacity=entry.credits.capacity)
        context._fifo_events.append(row)
        context._emit('fifo_event',transaction.transaction_id,resource,
                      json.dumps(dict(action=action,packet=packet,stage=stage),sort_keys=True,separators=(',',':')))

    def record_wait(packet,stage,resource,requested):
        wait=float(context.env.now)-requested
        if wait<=0:return
        entry=context.registry.get(resource)
        row=dict(action='fifo_wait',transaction_id=transaction.transaction_id,packet_index=packet,
                 stage_index=stage,resource_id=resource,time_cycles=float(context.env.now),wait_cycles=wait,
                 occupancy=len(entry.credits.users),queued=len(entry.credits.queue),capacity=entry.credits.capacity)
        context._fifo_events.append(row)
        context._emit('fifo_wait',transaction.transaction_id,resource,
                      json.dumps(dict(action='fifo_wait',packet=packet,stage=stage,wait_cycles=wait),sort_keys=True,separators=(',',':')))

    def packet_run(index, payload):
        held=[]
        try:
            if index:
                yield admitted[index-1]
            previous=None
            for hi,hop in enumerate(hops):
                t=hop.timing
                queued=float(context.env.now)
                if t.stage=='ingress':
                    entry=context.registry.get(t.input_resource_id)
                    requested=float(context.env.now);reservation=entry.credits.request();held.append((entry.credits,reservation))
                    yield reservation
                    record_wait(index,hi,entry.resource_id,requested)
                    record('queue_acquire',index,hi,entry.resource_id)
                    yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                    record('fifo_admit',index,hi,entry.resource_id)
                    if not admitted[index].triggered:admitted[index].succeed()
                    previous=(entry.credits,reservation)
                    yield context.env.timeout(t.input_latency_cycles)
                    record('fifo_visible',index,hi,entry.resource_id)
                    spans[hi].append((queued,queued,float(context.env.now),float(context.env.now)))
                    continue
                output=context.registry.get(t.output_resource_id)
                channel=context.registry.get(t.channel_resource_id)
                requested=float(context.env.now);issue=output.credits.request();held.append((output.credits,issue));yield issue
                record_wait(index,hi,output.resource_id,requested)
                requested=float(context.env.now);slot=channel.credits.request();held.append((channel.credits,slot));yield slot
                record_wait(index,hi,channel.resource_id,requested)
                record('queue_acquire',index,hi,channel.resource_id)
                # Forward on the eligible current edge once an input is ready.
                now=float(context.env.now)
                remainder=now%t.issue_cycles
                if remainder:
                    yield context.env.timeout(t.issue_cycles-remainder)
                sent=float(context.env.now)
                output.service_count+=1;output.busy_cycles+=t.issue_cycles
                record('packet_forward',index,hi,output.resource_id)
                if previous is not None:
                    record('queue_release',index,hi-1,hops[hi-1].timing.input_resource_id)
                    held.remove(previous);context.registry.release(*previous);previous=None
                # Issue ownership lasts one packet interval; propagation can
                # overlap the next packet's issue on the same physical port.
                def release_issue(resource,request,delay):
                    try:yield context.env.timeout(delay)
                    finally:context.registry.release(resource,request)
                context.spawn_background(release_issue(output.credits,issue,t.issue_cycles),
                                         deadline=sent+t.issue_cycles)
                held.remove((output.credits,issue))
                yield context.env.timeout(t.channel_latency_cycles)
                if t.stage=='transit':
                    incoming=context.registry.get(t.input_resource_id)
                    requested=float(context.env.now);input_slot=incoming.credits.request();held.append((incoming.credits,input_slot));yield input_slot
                    record_wait(index,hi,incoming.resource_id,requested)
                    record('queue_acquire',index,hi,incoming.resource_id)
                    yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                    record('fifo_admit',index,hi,incoming.resource_id)
                    held.remove((channel.credits,slot));context.registry.release(channel.credits,slot)
                    record('queue_release',index,hi,channel.resource_id)
                    previous=(incoming.credits,input_slot)
                    yield context.env.timeout(t.input_latency_cycles)
                    record('fifo_visible',index,hi,incoming.resource_id)
                elif t.stage=='egress':
                    yield context.env.timeout(_edge(context.env.now,t.issue_cycles)-context.env.now)
                    held.remove((channel.credits,slot));context.registry.release(channel.credits,slot)
                    record('queue_release',index,hi,channel.resource_id)
                    record('packet_delivered',index,hi,channel.resource_id)
                else:raise ValueError('LATENCY_FIFO_STAGE_ORDER')
                arrived=float(context.env.now)
                channel.service_count+=1
                channel.queue_wait_cycles+=sent-queued
                spans[hi].append((queued,sent,sent+t.issue_cycles,arrived))
            if previous is not None:
                held.remove(previous);context.registry.release(*previous)
            return payload
        except simpy.Interrupt:
            return None
        finally:
            for resource,request in held:
                if request.triggered:
                    # Close every queue record before the transaction stop.
                    resource_id=next((e.resource_id for e in context.registry.entries() if e.credits is resource),None)
                    if resource_id:
                        opened=[e for e in context._fifo_events if e['transaction_id']==transaction.transaction_id and
                            e['packet_index']==index and e['resource_id']==resource_id and e['action']=='queue_acquire']
                        closed=[e for e in context._fifo_events if e['transaction_id']==transaction.transaction_id and
                            e['packet_index']==index and e['resource_id']==resource_id and e['action']=='queue_release']
                        if len(opened)>len(closed):record('queue_release',index,opened[-1]['stage_index'],resource_id)
                    context.registry.release(resource,request)
                else:request.cancel()

    try:
        for index,offset in enumerate(range(0,transaction.payload_bytes,packet_bytes)):
            process=context.env.process(packet_run(index,min(packet_bytes,transaction.payload_bytes-offset)))
            packet_processes.append(process)
        yield context.env.all_of(packet_processes)
        if sum(p.value for p in packet_processes)!=transaction.payload_bytes:
            raise ValueError('LATENCY_FIFO_PACKET_BYTE_COVERAGE')
        aggregated=[]
        for hop,rows in zip(hops,spans):
            aggregated.append(GenericHopSpan(network_id=hop.network_id,link_id=hop.link_id,
                queued_cycles=min(x[0] for x in rows),serialization_start_cycles=min(x[1] for x in rows),
                serialization_end_cycles=max(x[2] for x in rows),arrival_cycles=max(x[3] for x in rows)))
        context._finish(transaction.transaction_id,'transfer',start,0.0,tuple(aggregated))
        if flow_owner is not None and transaction.flow_release_delay_cycles:
            resource,request=flow_owner;delay=transaction.flow_release_delay_cycles
            def release_flow_owner():
                try:yield context.env.timeout(delay)
                finally:context.registry.release(resource,request)
            context.spawn_background(release_flow_owner(),deadline=float(context.env.now)+delay);flow_owner=None
    except simpy.Interrupt:
        for process in packet_processes:
            if process.is_alive:process.interrupt()
        yield context.env.all_of(packet_processes)
    except Exception:
        for process in packet_processes:
            if process.is_alive:process.interrupt()
        raise
    finally:
        if flow_owner is not None:context.registry.release(*flow_owner)
