"""Observed resource/event receipts; no stage-only critical-path shortcut."""
import json
import math
from collections import defaultdict
from heapq import heappop, heappush

class ResourceObserver:
    def __init__(self,runtime):
        self.runtime=runtime;self.requests=[];self.events=[];self._process_ids={};self._request_ids={}
        self._owners={};self._order=0
        original=runtime._emit
        def emit(action,transaction_id=None,resource_id=None,detail=None):
            pid=self.process_id()
            if transaction_id:self._owners[pid]=transaction_id
            self._order+=1
            self.events.append(dict(id=f'e{self._order}',order=self._order,process=pid,time=float(runtime.env.now),
                action=action,transaction_id=transaction_id,resource_id=resource_id,detail=detail))
            return original(action,transaction_id,resource_id,detail)
        runtime._emit=emit
        for entry in runtime.registry.entries():
            for mode,res in (('credit',entry.credits),('serializer',entry.serializer)):
                if res is not None:self.wrap(entry.resource_id,mode,res)

    def process_id(self):
        p=self.runtime.env.active_process
        if p not in self._process_ids:self._process_ids[p]=f'p{len(self._process_ids)}'
        return self._process_ids[p]

    def wrap(self,rid,mode,resource):
        request=resource.request;release=resource.release
        def granted(row):
            if row['grant'] is not None:return
            self._order+=1;row['grant_order']=self._order;row['grant']=float(self.runtime.env.now)
            row['users_at_grant']=len(resource.users)
        def req(*args,**kwargs):
            obj=request(*args,**kwargs);pid=self.process_id();self._order+=1
            row=dict(id=f'r{len(self.requests)}',resource_id=rid,mode=mode,process=pid,
                requested=float(self.runtime.env.now),grant=None,released=None,capacity=resource.capacity,
                users_before=len(resource.users),queue_before=len(resource.queue),request_order=self._order,
                queued=not obj.triggered)
            self.requests.append(row);self._request_ids[obj]=row
            if obj.triggered:granted(row)
            else:obj.callbacks.append(lambda event:granted(row))
            return obj
        def rel(obj):
            if obj in self._request_ids:
                row=self._request_ids[obj]
                if obj.triggered:granted(row)
                self._order+=1
                row['released']=float(self.runtime.env.now);row['release_order']=self._order
                row['release_process']=self.process_id()
            return release(obj)
        resource.request=req;resource.release=rel

    def summary(self):
        result={}
        for r in self.requests:
            r['transaction_id']=self._owners.get(r['process'])
            key=r['resource_id']+'/'+r['mode']
            q=result.setdefault(key,dict(resource_id=r['resource_id'],mode=r['mode'],capacity=r['capacity'],
                requests=0,wait_cycles=0.0,wait_count=0,max_occupancy=0,final_users=0,final_queue=0))
            q['requests']+=1
            if r['grant'] is not None:
                wait=r['grant']-r['requested'];q['wait_cycles']+=wait;q['wait_count']+=wait>0
                q['max_occupancy']=max(q['max_occupancy'],r.get('users_at_grant',0))
        for entry in self.runtime.registry.entries():
            for mode,res in (('credit',entry.credits),('serializer',entry.serializer)):
                key=entry.resource_id+'/'+mode
                if key in result:result[key]['final_users']=len(res.users);result[key]['final_queue']=len(res.queue)
        return list(result.values())

    def causal_graph(self, compiled=None, spans=None):
        """Use causal_order for DAG traversal; order only anchors observed events."""
        nodes={};edges=[];processes=defaultdict(list)
        for e in self.events:
            nodes[e['id']]=dict(e)
            processes[e['process']].append((e['order'],e['id']))
        resources=defaultdict(list)
        for r in self.requests:
            for suffix,field,order in (('request','requested','request_order'),('grant','grant','grant_order'),('release','released','release_order')):
                if r[field] is None:continue
                k=r['id']+'.'+suffix
                process=r.get('release_process',r['process']) if suffix=='release' else r['process']
                nodes[k]=dict(id=k,time=r[field],order=r[order],action='resource_'+suffix,
                    resource_id=r['resource_id'],mode=r['mode'],request_id=r['id'],
                    transaction_id=r.get('transaction_id') or self._owners.get(r['process']),process=process)
                processes[process].append((r[order],k))
            resources[r['resource_id'],r['mode']].append(r)
            if r['grant'] is not None:
                edges.append((r['id']+'.request',r['id']+'.grant','queue'))
            if r['released'] is not None and r['grant'] is not None:
                edges.append((r['id']+'.grant',r['id']+'.release','ownership'))
        for rows in processes.values():
            rows.sort()
            edges.extend((a[1],b[1],'process') for a,b in zip(rows,rows[1:]))
        for rows in resources.values():
            releases=defaultdict(list)
            for row in rows:
                if row['grant'] is not None and row['released'] is not None:releases[row['released']].append(row)
            for r in rows:
                if r['grant'] is None or not r.get('queued',r['grant']>r['requested']):continue
                blockers=[blocker for blocker in releases[r['grant']]
                    if r['request_order']<blocker['release_order']<r['grant_order']]
                if blockers:
                    blocker=max(blockers,key=lambda b:(b['released'],b['release_order']))
                    edges.append((blocker['id']+'.release',r['id']+'.grant','resource_blocker'))
        # Physical packet ownership connects multicast parent/fork processes.
        packets=defaultdict(list)
        for e in self.events:
            if e['action']!='fifo_event':continue
            d=json.loads(e['detail']);key=(d.get('owner',e['transaction_id']),d['packet'])
            packets[key].append((d['stage'],e['order'],e['id']))
        for events in packets.values():
            forwards=[x for x in events if 'packet_forward' in nodes[x[2]].get('detail','')]
            for stage,order,k in forwards:
                previous=[x for x in events if x[0]==stage-1 and x[1]<order and
                    ('fifo_visible' in nodes[x[2]].get('detail','') or 'packet_forward' in nodes[x[2]].get('detail',''))]
                if previous:edges.append((max(previous,key=lambda x:x[1])[2],k,'packet_handoff'))
        if compiled is not None and spans is not None:
            boundaries={}
            observed=defaultdict(list)
            for node in nodes.values():
                if node['action']!='resource_release':observed[node.get('transaction_id')].append(node)
            for tx in compiled.plan.content.transactions:
                span=spans.get(tx.transaction_id)
                if span is None or span.status!='complete':continue
                start,end=tx.transaction_id+'.start',tx.transaction_id+'.complete'
                events=[event for event in observed[tx.transaction_id]
                    if span.start_cycles<=event['time']<=span.end_cycles]
                first=min(events,key=lambda event:(event['time'],event['order'])) if events else None
                last=max(events,key=lambda event:(event['time'],event['order'])) if events else None
                nodes[start]=dict(id=start,time=span.start_cycles,order=first['order'] if first else 0,
                    action='transaction_start_boundary',transaction_id=tx.transaction_id)
                nodes[end]=dict(id=end,time=span.end_cycles,order=last['order'] if last else 0,
                    action='transaction_completion_boundary',transaction_id=tx.transaction_id)
                boundaries[tx.transaction_id]=(start,end)
                if events:
                    edges.extend(((start,first['id'],'transaction_boundary'),(last['id'],end,'transaction_boundary')))
                else:edges.append((start,end,'zero_service_join'))
            for tx in compiled.plan.content.transactions:
                if tx.transaction_id not in boundaries:continue
                for dep in tx.depends_on:
                    if dep in boundaries:edges.append((boundaries[dep][1],boundaries[tx.transaction_id][0],'declared_dependency'))
        incoming=defaultdict(list);outgoing=defaultdict(list);indegree=dict.fromkeys(nodes,0)
        valid=[]
        for source,target,kind in edges:
            if nodes[source]['time']<=nodes[target]['time']:
                incoming[target].append((source,kind));outgoing[source].append(target);indegree[target]+=1
                valid.append(dict(source=source,target=target,kind=kind))
        ready=[]
        for identity,node in nodes.items():
            if not indegree[identity]:heappush(ready,(node['time'],node['order'],identity))
        scores={};parents={};depths={}
        while ready:
            _,_,identity=heappop(ready);node=nodes[identity];node['causal_order']=len(scores)
            options=[(scores[source]+node['time']-nodes[source]['time'],source,kind) for source,kind in incoming[identity]]
            if options:
                score,parent,kind=max(options,key=lambda option:(option[0],nodes[option[1]]['time'],depths[option[1]],option[1]))
                scores[identity]=score;parents[identity]=(parent,kind);depths[identity]=depths[parent]+1
            else:scores[identity]=0.0;depths[identity]=0
            for target in outgoing[identity]:
                indegree[target]-=1
                if not indegree[target]:heappush(ready,(nodes[target]['time'],nodes[target]['order'],target))
        if len(scores)!=len(nodes):raise ValueError('observed causal graph contains a cycle')
        if compiled is not None and spans is not None:
            terminals=[identity for identity,node in nodes.items()
                if node['action']=='transaction_completion_boundary' and not outgoing[identity]]
        else:
            terminals=[identity for identity,node in nodes.items()
                if node['action'] in ('transaction_complete','unit_release','operation_end','wait_resume','counter_publish')
                or (node['action']=='fifo_event' and 'packet_delivered' in (node.get('detail') or ''))]
        path=[]
        if terminals:
            k=max(terminals,key=lambda identity:(nodes[identity]['time'],scores[identity],nodes[identity]['causal_order']))
            while True:
                path.append(nodes[k])
                if k not in parents:break
                k=parents[k][0]
            path.reverse()
        return dict(nodes=list(nodes.values()),edges=valid,critical_path=path,
            method='observed process, packet handoff, dependency and resource-blocker DAG; topological completion path; no stage-duration fit')

def build_receipt(compiled,runtime,result,observer,error):
    spans={} if result is None else {s.transaction_id:s for s in result.transactions}
    fifo=getattr(runtime,'_fifo_events',[])
    logical=[];physical=set();physical_bytes=0
    native={t.transaction_id:t for t in compiled.plan.content.transactions}
    for flow in compiled.workload.flows:
        endpoint=[];packet=0;source_injections=set()
        for tid in compiled.native_members[flow.flow_id]:
            tx=native[tid];packet=tx.hops[0].timing.packet_bytes
            delivered=[e for e in fifo if e['transaction_id']==tid and e['action']=='packet_delivered']
            span=spans.get(tid)
            endpoint.append(dict(transaction_id=tid,destination=tx.destination,route=[h.link_id for h in tx.hops],
                status='incomplete' if span is None else span.status,
                start_cycles=None if span is None else span.start_cycles,end_cycles=None if span is None else span.end_cycles,
                delivered_packets=len(delivered),delivered_bytes=sum(min(packet,flow.payload_bytes-e['packet_index']*packet) for e in delivered)))
            for e in fifo:
                if e['transaction_id']!=tid:continue
                if e['action']=='fifo_admit' and e['stage_index']==0:
                    source_injections.add((e.get('owner',tid),e['packet_index']))
                if e['action']=='packet_forward':
                    key=(e.get('owner',tid),e['packet_index'],e['stage_index'],e['resource_id'])
                    if key not in physical:
                        physical.add(key);physical_bytes+=min(packet,flow.payload_bytes-e['packet_index']*packet)
        logical.append(dict(flow_id=flow.flow_id,primitive=flow.primitive,payload_bytes=flow.payload_bytes,
            logical_source_bytes=flow.payload_bytes,endpoint_delivered_bytes=sum(e['delivered_bytes'] for e in endpoint),
            expected_packets=math.ceil(flow.payload_bytes/packet),source_packet_injections=len(source_injections),
            participants=[flow.source_role,*flow.destination_roles],dependencies=list(flow.depends_on),endpoints=endpoint))
    resources=observer.summary()
    return dict(schema_version=1,status='failed' if error else result.status,error=error,
        fixture_only=compiled.binding.fixture_only or compiled.workload.fixture_only,
        hardware_parameters=[p.model_dump(mode='json') for p in compiled.hardware.parameters],
        effective_timing=compiled.effective_timing,
        provenance=dict(workload_sha256=compiled.workload.workload_sha256,binding_sha256=compiled.binding.binding_sha256,
            hardware_sha256=compiled.hardware.parameter_sha256,calibration_sha256=compiled.binding.calibration_sha256,
            plan_sha256=compiled.plan.plan_sha256),
        logical_flows=logical,native_transactions=[] if result is None else [s.model_dump(mode='json') for s in result.transactions],
        physical_packet_forwards=len(physical),physical_payload_link_bytes=physical_bytes,
        resources=resources,resource_requests=observer.requests,causal_graph=observer.causal_graph(compiled,spans),
        whole_boundary=[] if result is None else list(result.operation_envelopes),
        cleanup=dict(resources_released=runtime.registry.all_released(),multicast_groups_empty=not bool(getattr(runtime,'_fifo_multicast_groups',{})),
            background_alive=sum(p.is_alive for p in runtime._background)),
        applicability='fixture-only-unvalidated' if compiled.binding.fixture_only else 'supplied-component-binding',
        architecture_benefit_claim=False,real_moe_input_ready=False)
