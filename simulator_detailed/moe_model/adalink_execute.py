"""M08 lowering/execution through SystemSpec, compile_system and RuntimeContext."""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
from types import SimpleNamespace
import json,math
from .adalink import AdalinkSchedule
from .adalink_binding import SuppliedAdalinkBinding,AdalinkCostTable
from .hardware import HardwareParameterSet,apply_mutations
from .workload import stable_sha256
from .receipt import ResourceObserver
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system
from ..runtime_context import RuntimeContext

@dataclass(frozen=True)
class CompiledAdalink:
    schedule: AdalinkSchedule
    binding: SuppliedAdalinkBinding
    hardware: HardwareParameterSet
    costs: AdalinkCostTable
    spec: SystemSpec
    plan: object
    effective_timing: list

@dataclass(frozen=True)
class AdalinkExecution:
    compiled: CompiledAdalink
    runtime: object
    result: object
    receipt: dict
    error: str | None

def compile_adalink(schedule,binding,hardware,costs,*,production=True,max_cycles=1000000):
    schedule=AdalinkSchedule.model_validate_json(schedule.model_dump_json());binding=SuppliedAdalinkBinding.model_validate_json(binding.model_dump_json())
    hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json());costs=AdalinkCostTable.model_validate_json(costs.model_dump_json())
    if schedule.rank_count!=len(binding.ranks):raise ValueError('schedule and supplied rank map disagree')
    if production and (schedule.fixture_only or binding.fixture_only):raise ValueError('production M08 forbids fixture schedule/binding')
    transactions=[];counter_ids=set()
    for action in schedule.actions:
        row=dict(transaction_id=action.action_id,depends_on=list(action.depends_on),start_cycles=0)
        if action.kind=='transfer':
            route=binding.route(action.rank,action.peer_rank);row.update(kind='transfer',network_id=route.network_id,source=binding.rank(action.rank).endpoint_id,
              destination=binding.rank(action.peer_rank).endpoint_id,payload_bytes=action.work_bytes)
        elif action.kind=='local_reduce':
            cost=costs.cost(action.rank);duration=cost.duration(action.work_bytes)
            if production and cost.status!='calibrated':raise ValueError('production local reduce cost must be calibrated')
            if duration<=0:raise ValueError('local reduce duration must be positive')
            row.update(kind='compute',unit_id=binding.rank(action.rank).reduce_unit_id,duration_cycles=duration)
        elif action.kind=='signal':
            counter_ids.add(action.counter_id);row.update(kind='signal',counter_id=action.counter_id,delta=action.delta)
        else:
            counter_ids.add(action.counter_id);row.update(kind='wait',counter_id=action.counter_id,threshold=action.threshold)
        transactions.append(row)
    effective=binding.effective_timing(hardware)
    batch_raw=dict(kind='generic_transaction_batch',schema_version=1,batch_id=schedule.schedule_id,timing=effective,
      counters=[dict(counter_id=x,initial_value=0) for x in sorted(counter_ids)],transactions=transactions,max_cycles=max_cycles,
      operation_envelopes=[dict(operation_id=schedule.schedule_id+'.collective',member_transactions=[x.action_id for x in schedule.actions],boundary_ref='handoff-to-all-chunks-complete')])
    batch=GenericTransactionBatch.model_validate_json(json.dumps(batch_raw));spec=SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec',schema_version=1,
      spec_id=schedule.schedule_id,graph=binding.graph,batch=batch.model_dump(mode='json'))));plan=compile_system(spec)
    return CompiledAdalink(schedule,binding,hardware,costs,spec,plan,effective)

def _critical_to_completion(graph,completion):
    nodes={x['id']:x for x in graph['nodes']};incoming=defaultdict(list)
    target=completion+'.complete'
    if target not in nodes:return []
    for edge in graph['edges']:incoming[edge['target']].append((edge['source'],edge['kind']))
    ordered=sorted(nodes,key=lambda key:nodes[key]['causal_order']);score={};parent={};depth={}
    for key in ordered:
        options=[]
        for source,kind in incoming[key]:
            options.append((score[source]+nodes[key]['time']-nodes[source]['time'],source,kind))
        if options:
            value,source,kind=max(options,key=lambda option:(option[0],nodes[option[1]]['time'],depth[option[1]],option[1]))
            score[key]=value;parent[key]=(source,kind);depth[key]=depth[source]+1
        else:score[key]=0.0;depth[key]=0
    result=[];key=target
    while True:
        result.append(dict(nodes[key],edge_from_parent=None if key not in parent else parent[key][1]))
        if key not in parent:break
        key=parent[key][0]
    result.reverse();return result

def _build_receipt(compiled,runtime,result,observer,error):
    schedule=compiled.schedule;spans={} if result is None else {x.transaction_id:x for x in result.transactions};native={x.transaction_id:x for x in compiled.plan.content.transactions};fifo=getattr(runtime,'_fifo_events',[]);actions=[]
    # summary() resolves observer process ownership back to transaction ids;
    # do it before constructing the per-action resource view.
    resource_summary=observer.summary()
    total_logical=0;total_link_bytes=0;packet_count=0;packet_hops=0
    for action in schedule.actions:
        span=spans.get(action.action_id);requests=[x for x in observer.requests if x.get('transaction_id')==action.action_id]
        occupied=[]
        for request in requests:
            occupied.append(dict(resource_id=request['resource_id'],mode=request['mode'],requested_cycles=request['requested'],grant_cycles=request['grant'],released_cycles=request['released'],
              wait_cycles=None if request['grant'] is None else request['grant']-request['requested'],capacity=request['capacity'],users_before=request['users_before'],queue_before=request['queue_before']))
        row=dict(action.model_dump(mode='json'),logical_transaction_id=action.action_id,native_transaction_ids=[action.action_id],status='incomplete' if span is None else span.status,
          start_cycles=None if span is None else span.start_cycles,end_cycles=None if span is None else span.end_cycles,
          service_span_cycles=None if span is None or span.start_cycles is None or span.end_cycles is None else span.end_cycles-span.start_cycles,
          resource_wait_cycles=sum(x['wait_cycles'] for x in occupied if x['wait_cycles'] is not None),resource_occupancy=occupied)
        if action.kind=='transfer':
            tx=native[action.action_id];route=[x.link_id for x in tx.hops];packet=tx.hops[0].timing.packet_bytes;expected=math.ceil(action.work_bytes/packet)
            delivered=[x for x in fifo if x['transaction_id']==action.action_id and x['action']=='packet_delivered'];row.update(
              primitive='ADALINK_WRITE_SUM' if action.phase=='reduce_scatter' else 'ADALINK_WRITE',send_rank=action.rank,receive_rank=action.peer_rank,
              source_endpoint=tx.source,destination_endpoint=tx.destination,route_link_ids=route,port_link_ids=list(compiled.binding.route(action.rank,action.peer_rank).port_link_ids),
              logical_bytes=action.work_bytes,physical_route_bytes=action.work_bytes*len(route),packet_bytes=packet,expected_packets=expected,delivered_packets=len(delivered),
              delivered_bytes=sum(min(packet,action.work_bytes-x['packet_index']*packet) for x in delivered))
            total_logical+=action.work_bytes;total_link_bytes+=action.work_bytes*len(route);packet_count+=expected;packet_hops+=expected*len(route)
        elif action.kind=='local_reduce':
            row.update(primitive='LOCAL_REDUCE',reduce_unit_id=compiled.binding.rank(action.rank).reduce_unit_id,logical_bytes=action.work_bytes)
        actions.append(row)
    causal=observer.causal_graph(SimpleNamespace(plan=compiled.plan),spans)
    calibration_sha256=stable_sha256(dict(binding_calibration_sha256=compiled.binding.calibration_sha256,
      local_reduce_calibration_sha256=compiled.costs.table_sha256))
    provenance=dict(workload_sha256=schedule.workload_sha256,schedule_sha256=schedule.schedule_sha256,binding_sha256=compiled.binding.binding_sha256,
      hardware_sha256=compiled.hardware.parameter_sha256,binding_calibration_sha256=compiled.binding.calibration_sha256,
      local_reduce_calibration_sha256=compiled.costs.table_sha256,calibration_sha256=calibration_sha256,plan_sha256=compiled.plan.plan_sha256)
    return dict(schema='t81.m08.receipt.v1',status='failed' if error else result.status,error=error,provenance=provenance,**provenance,algorithm=schedule.algorithm,rank_count=schedule.rank_count,
      payload_bytes_per_rank=schedule.payload_bytes_per_rank,actions=actions,logical_transaction_count=len(schedule.actions),native_transaction_count=len(compiled.plan.content.transactions),
      logical_transfer_count=sum(x.kind=='transfer' for x in schedule.actions),native_transfer_count=sum(getattr(x,'kind',None)=='transfer' for x in compiled.plan.content.transactions),
      transfer_packet_count=packet_count,physical_packet_hop_count=packet_hops,logical_transfer_bytes=total_logical,physical_link_bytes=total_link_bytes,bytes_by_phase=schedule.bytes_by_phase(),resources=resource_summary,
      counters=[] if result is None else [x.model_dump(mode='json') for x in result.counters],whole_boundary=[] if result is None else list(result.operation_envelopes),
      causal_graph=causal,event_resource_critical_path=_critical_to_completion(causal,schedule.completion_action_id),
      cleanup=dict(resources_released=runtime.registry.all_released(),multicast_groups_empty=not bool(getattr(runtime,'_fifo_multicast_groups',{})),background_alive=sum(x.is_alive for x in runtime._background)),
      fixture_only=schedule.fixture_only or compiled.binding.fixture_only,applicability='fixture-only-cpu-mechanism' if schedule.fixture_only or compiled.binding.fixture_only else 'supplied-calibrated-binding',
      evidence_status='CPU_ONLY_WITH_BINDING_GATE' if schedule.fixture_only or compiled.binding.fixture_only else 'SUPPLIED_BINDING_EXECUTION',
      whole_operation_black_box=False,architecture_benefit_claim=False,real_moe_input_ready=False)

def execute_adalink(schedule,binding,hardware,costs,*,production=True,max_cycles=1000000,cancel=None,inject_failure=False):
    compiled=compile_adalink(schedule,binding,hardware,costs,production=production,max_cycles=max_cycles);runtime=RuntimeContext(compiled.plan);observer=ResourceObserver(runtime)
    if cancel:
        target,when=cancel;valid={x.action_id for x in compiled.schedule.actions}|{compiled.schedule.schedule_id+'.collective'}
        if target not in valid:raise ValueError('unknown M08 cancellation target')
        def cancelling():yield runtime.env.timeout(when);runtime.cancel(target)
        runtime.env.process(cancelling())
    if inject_failure:
        original=runtime._emit
        def emit(action,transaction_id=None,resource_id=None,detail=None):
            if action=='fifo_event' and detail and 'packet_forward' in detail:raise RuntimeError('M08_INJECTED_PACKET_FAILURE')
            return original(action,transaction_id,resource_id,detail)
        runtime._emit=emit
    result=None;error=None
    try:result=runtime.run()
    except RuntimeError as exc:error=str(exc)
    return AdalinkExecution(compiled,runtime,result,_build_receipt(compiled,runtime,result,observer,error),error)

def compare_link_mutation(schedule,binding,hardware,costs,mutation,scope,*,production=True):
    updated=apply_mutations(hardware,(mutation,),scope);base=execute_adalink(schedule,binding,hardware,costs,production=production);changed=execute_adalink(schedule,binding,updated,costs,production=production)
    target=next(x for x in binding.parameters if x.parameter_path==mutation.path);direct={a.action_id for a in schedule.actions if a.kind=='transfer' and target.link_id in binding.route(a.rank,a.peer_rank).link_ids}
    a={x['action_id']:x['end_cycles'] for x in base.receipt['actions']};b={x['action_id']:x['end_cycles'] for x in changed.receipt['actions']}
    deltas={key:b[key]-a[key] for key in a};changed_actions=sorted(key for key,value in deltas.items() if value!=0)
    return dict(schedule_sha256=schedule.schedule_sha256,binding_sha256=binding.binding_sha256,base_hardware_sha256=hardware.parameter_sha256,changed_hardware_sha256=updated.parameter_sha256,
      mutation=mutation.model_dump(mode='json'),target_network_id=target.network_id,target_link_id=target.link_id,directly_targeted_actions=sorted(direct),
      changed_actions=changed_actions,completion_delta_cycles=deltas,base_receipt=base.receipt,changed_receipt=changed.receipt)
