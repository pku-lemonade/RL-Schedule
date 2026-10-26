from __future__ import annotations

import math

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system


PACKET_BYTES=8


def timing(stage,input_id,output_id,channel_id,*,fifo=1,channel=1,input_latency=0,channel_latency=0,issue=1):
    return dict(mode='latency_fifo',stage=stage,packet_bytes=PACKET_BYTES,fifo_slots=fifo,channel_slots=channel,
      issue_cycles=issue,input_latency_cycles=input_latency,channel_latency_cycles=channel_latency,
      input_resource_id=input_id,output_resource_id=output_id,channel_resource_id=channel_id)


def transfer(identity,source,destination,payload,start=0,depends=(),**extra):
    return dict(transaction_id=identity,kind='transfer',network_id='noc',source=source,destination=destination,
      payload_bytes=payload,start_cycles=start,depends_on=list(depends),**extra)


def signal(identity,depends,counter='done'):
    return dict(transaction_id=identity,kind='signal',counter_id=counter,delta=1,start_cycles=0,depends_on=list(depends))


def _graph(paths):
    nodes=[];links=[];units=[];routes=[];ports=[]
    for flow,hops in enumerate(paths):
        flow_nodes=[f'f{flow}n{index}' for index in range(hops+1)];nodes.extend(flow_nodes)
        for node in flow_nodes:
            ports.extend([dict(port_id='out',node_id=node,network_id='noc',kind='network'),dict(port_id='in',node_id=node,network_id='noc',kind='network'),dict(port_id='local',node_id=node,network_id='noc',kind='local')])
        link_ids=[]
        for hop in range(hops):
            identity=f'f{flow}h{hop}';link_ids.append(identity);links.append(dict(link_id=identity,network_id='noc',src_node=flow_nodes[hop],src_port='out',dst_node=flow_nodes[hop+1],dst_port='in'))
        units.extend([dict(unit_id=f'src-{flow}',node_id=flow_nodes[0],network_id='noc',port_id='local'),dict(unit_id=f'dst-{flow}',node_id=flow_nodes[-1],network_id='noc',port_id='local')])
        routes.append(dict(network_id='noc',source=f'src-{flow}',destination=f'dst-{flow}',link_ids=link_ids))
    return dict(kind='generic_system_graph',schema_version=1,system_id='t64-step2',
      nodes=[dict(node_id=node,x=index,y=0,role='compute') for index,node in enumerate(nodes)],networks=[dict(network_id='noc')],
      ports=ports,links=links,dma_endpoints=[],execution_units=units,memory_resources=[],static_routes=routes)


def _compile(paths,flows,timings,extra_transactions=(),counters=(),max_cycles=10000):
    graph=_graph(paths);all_links=[link['link_id'] for link in graph['links']];default=timings[all_links[0]]
    overrides=[dict(link_id=identity,timing=timings[identity]) for identity in all_links[1:] if timings[identity]!=default]
    transactions=[transfer(flow['id'],f'src-{index}',f'dst-{index}',flow['payload'],flow.get('start',0),flow.get('depends',())) for index,flow in enumerate(flows)]
    transactions.extend(extra_transactions)
    batch=dict(kind='generic_transaction_batch',schema_version=1,batch_id='t64-step2',graph_path=None,
      timing=[dict(network_id='noc',link=default,overrides=overrides)],counters=list(counters),transactions=transactions,max_cycles=max_cycles)
    spec=SystemSpec.model_validate(dict(kind='system_spec',schema_version=1,spec_id='t64-step2',graph=graph,batch=batch))
    return compile_system(spec)


def path_timings(paths,resource_map=None,final_latency=0,capacity=1):
    resource_map=resource_map or {};result={}
    for flow,hops in enumerate(paths):
        for hop in range(hops):
            stage='ingress' if hop==0 else ('egress' if hop==hops-1 else 'transit')
            overrides=resource_map.get((flow,hop),{})
            result[f'f{flow}h{hop}']=timing(stage,overrides.get('input',f'in-{flow}-{hop}'),overrides.get('output',f'out-{flow}-{hop}'),
              overrides.get('channel',f'ch-{flow}-{hop}'),fifo=overrides.get('fifo',capacity),channel=overrides.get('channel_slots',capacity),
              channel_latency=overrides.get('channel_latency',final_latency if stage=='egress' else 0),issue=overrides.get('issue',1))
    return result


def same_path_plan(payloads,starts=None,with_successor=False,max_cycles=10000):
    starts=starts or [0]*len(payloads);paths=[2]
    timings=path_timings(paths,{(0,0):dict(input='shared-in'),(0,1):dict(output='shared-out',channel='shared-channel')})
    graph=_graph(paths);default=timings['f0h0'];overrides=[dict(link_id='f0h1',timing=timings['f0h1'])]
    transactions=[transfer(f'flow-{index}','src-0','dst-0',payload,starts[index]) for index,payload in enumerate(payloads)]
    counters=[]
    if with_successor:
        transactions.append(signal('after',tuple(f'flow-{index}' for index in range(len(payloads)))));counters=[dict(counter_id='done',initial_value=0)]
    batch=dict(kind='generic_transaction_batch',schema_version=1,batch_id='same-path',graph_path=None,
      timing=[dict(network_id='noc',link=default,overrides=overrides)],counters=counters,transactions=transactions,max_cycles=max_cycles)
    spec=SystemSpec.model_validate(dict(kind='system_spec',schema_version=1,spec_id='same-path',graph=graph,batch=batch))
    return compile_system(spec)


def parallel_plan(flows,hops=2,resource_map=None,extra_transactions=(),counters=(),final_latency=0,max_cycles=10000):
    paths=[hops]*len(flows);return _compile(paths,flows,path_timings(paths,resource_map,final_latency),extra_transactions,counters,max_cycles)


def multicast_plan():
    graph=dict(kind='generic_system_graph',schema_version=1,system_id='t64-multicast',
      nodes=[dict(node_id=node,x=index,y=0,role='compute') for index,node in enumerate(('s','branch','d0','d1'))],networks=[dict(network_id='noc')],
      ports=[dict(port_id='out',node_id=node,network_id='noc',kind='network') for node in ('s','branch','d0','d1')]
        +[dict(port_id='in',node_id=node,network_id='noc',kind='network') for node in ('s','branch','d0','d1')]
        +[dict(port_id='local',node_id=node,network_id='noc',kind='local') for node in ('s','branch','d0','d1')]
        +[dict(port_id='out-1',node_id='branch',network_id='noc',kind='network')],
      links=[dict(link_id='prefix',network_id='noc',src_node='s',src_port='out',dst_node='branch',dst_port='in'),
        dict(link_id='leaf-0',network_id='noc',src_node='branch',src_port='out',dst_node='d0',dst_port='in'),
        dict(link_id='leaf-1',network_id='noc',src_node='branch',src_port='out-1',dst_node='d1',dst_port='in')],dma_endpoints=[],
      execution_units=[dict(unit_id='src',node_id='s',network_id='noc',port_id='local'),dict(unit_id='dst-0',node_id='d0',network_id='noc',port_id='local'),dict(unit_id='dst-1',node_id='d1',network_id='noc',port_id='local')],memory_resources=[],
      static_routes=[dict(network_id='noc',source='src',destination='dst-0',link_ids=['prefix','leaf-0']),dict(network_id='noc',source='src',destination='dst-1',link_ids=['prefix','leaf-1'])])
    root=timing('ingress','prefix-in','unused-prefix-out','unused-prefix-channel',fifo=2)
    leaf0=timing('egress','unused-leaf0','leaf-out0','leaf-channel0',channel_latency=0)
    leaf1=timing('egress','unused-leaf1','leaf-out1','leaf-channel1',channel_latency=5)
    transactions=[transfer('m0','src','dst-0',24,multicast_group='group'),transfer('m1','src','dst-1',24,multicast_group='group'),signal('after-all',('m0','m1'))]
    batch=dict(kind='generic_transaction_batch',schema_version=1,batch_id='multicast',graph_path=None,
      timing=[dict(network_id='noc',link=root,overrides=[dict(link_id='leaf-0',timing=leaf0),dict(link_id='leaf-1',timing=leaf1)])],
      counters=[dict(counter_id='done',initial_value=0)],transactions=transactions,max_cycles=1000)
    spec=SystemSpec.model_validate(dict(kind='system_spec',schema_version=1,spec_id='multicast',graph=graph,batch=batch))
    return compile_system(spec)


class ResourceProbe:
    def __init__(self,runtime,resource_ids=None):
        self.runtime=runtime;self.rows=[];self.sequence=0;self.by_request={};self.resources={}
        selected=set(resource_ids) if resource_ids is not None else {entry.resource_id for entry in runtime.registry.entries() if entry.credits is not None}
        for identity in sorted(selected):
            entry=runtime.registry.get(identity);resource=entry.credits
            if resource is None:continue
            self.resources[identity]=resource;self._wrap(identity,resource)
    def _wrap(self,identity,resource):
        original_request=resource.request;original_release=resource.release
        def request(*args,**kwargs):
            self.sequence+=1;row=dict(sequence=self.sequence,resource_id=identity,request_time=float(self.runtime.env.now),
              users_before=len(resource.users),queue_before=len(resource.queue),capacity=resource.capacity,grant_time=None,release_time=None)
            request=original_request(*args,**kwargs);self.rows.append(row);self.by_request[id(request)]=row
            def granted(_event):
                row['grant_time']=float(self.runtime.env.now);row['wait_cycles']=row['grant_time']-row['request_time'];row['users_at_grant']=len(resource.users);row['queue_at_grant']=len(resource.queue)
            request.callbacks.append(granted);return request
        def release(request):
            event=original_release(request);row=self.by_request.get(id(request))
            if row is not None:
                row['release_time']=float(self.runtime.env.now);row['users_after_release']=len(resource.users);row['queue_after_release']=len(resource.queue)
            return event
        resource.request=request;resource.release=release
    def summary(self):
        result={}
        for identity,resource in self.resources.items():
            rows=[row for row in self.rows if row['resource_id']==identity];waits=[row.get('wait_cycles',0) for row in rows if row.get('grant_time') is not None]
            result[identity]=dict(requests=len(rows),wait_count=sum(wait>0 for wait in waits),wait_total_cycles=sum(waits),
              max_users=max([row.get('users_at_grant',row['users_before']) for row in rows]+[0]),max_queue=max([row['queue_before'] for row in rows]+[0]),
              capacity=resource.capacity,final_users=len(resource.users),final_queue=len(resource.queue))
        return result


def execute(plan,resource_ids=None,cancel=None,inject_failure=False):
    runtime=RuntimeContext(plan);probe=ResourceProbe(runtime,resource_ids);guard={'left':'T64-GUARD-L','right':'T64-GUARD-R'};before=dict(guard)
    if cancel:
        def cancellation():yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
        runtime.env.process(cancellation())
    if inject_failure:
        original=runtime._emit;seen={'value':False}
        def failing(action,*args,**kwargs):
            if action=='fifo_event' and not seen['value']:
                detail=kwargs.get('detail') if 'detail' in kwargs else (args[2] if len(args)>2 else None)
                if detail and 'packet_forward' in detail:seen['value']=True;raise RuntimeError('T64_INJECTED_PACKET_FAILURE')
            return original(action,*args,**kwargs)
        runtime._emit=failing
    error=None;result=None
    try:result=runtime.run()
    except Exception as exc:error=exc
    return runtime,result,probe,before,guard,error


def transfer_summary(runtime,result,probe,transaction_ids,guards):
    spans={span.transaction_id:span for span in result.transactions};flows={}
    for identity in transaction_ids:
        events=[event for event in runtime._fifo_events if event['transaction_id']==identity]
        transaction=next(tx for tx in runtime.plan.content.transactions if tx.transaction_id==identity)
        admissions=[event['time_cycles'] for event in events if event['action']=='fifo_admit']
        delivered=[event for event in events if event['action']=='packet_delivered']
        flows[identity]=dict(payload_bytes=transaction.payload_bytes,expected_packets=math.ceil(transaction.payload_bytes/PACKET_BYTES),
          delivered_packets=len(delivered),admission=min(admissions) if admissions else None,completion=spans[identity].end_cycles,
          last_network_event=max((event['time_cycles'] for event in delivered),default=None))
    return dict(flows=flows,overall_completion=max(value['completion'] for value in flows.values()),
      completion_order=[key for key,_ in sorted(flows.items(),key=lambda item:(item[1]['completion'],item[0]))],resources=probe.summary(),
      guard_before=guards[0],guard_after=guards[1],guard_pass=guards[0]==guards[1],cleanup=runtime.registry.all_released())
