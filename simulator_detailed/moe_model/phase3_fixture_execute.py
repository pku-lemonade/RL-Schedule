"""Unified fixture-only M01-M08 lowering, execution, receipts and mutations."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
import json, math

from .adalink_execute import compile_adalink
from .component_service import ComponentAction
from .gm_service import GMFormula, conflict_groups, gm_actions
from .hardware import HardwareMutation, HardwareParameterSet, MutationScope, apply_mutations
from .lowering import compile_network
from .phase3_fixture import Phase3FixtureBundle
from .receipt import ResourceObserver
from .workload import stable_sha256
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..runtime_context import RuntimeContext
from ..system_compile import compile_system


@dataclass(frozen=True)
class CompiledPhase3Fixture:
    bundle: Phase3FixtureBundle
    hardware: HardwareParameterSet
    spec: SystemSpec
    plan: object
    stage_members: dict
    flow_members: dict
    action_categories: dict
    gm_actions: tuple[ComponentAction, ...]
    effective_timing: list


@dataclass(frozen=True)
class Phase3FixtureExecution:
    compiled: CompiledPhase3Fixture
    runtime: object
    result: object
    receipt: dict
    error: str | None


def _component_row(action):
    if action.status != 'calibrated' or action.duration_cycles is None or action.duration_cycles <= 0:
        raise ValueError('fixture component action requires positive explicit fixture duration')
    return dict(transaction_id=action.action_id,kind='compute',unit_id=action.resource_id,
        duration_cycles=action.duration_cycles,depends_on=list(action.dependencies),start_cycles=action.earliest_start)


def _gm_actions(bundle, hardware):
    path='gm.controllers.gm0.bytes_per_cycle';parameter=hardware.by_path().get(path)
    if parameter is None or parameter.quantity.value is None or parameter.quantity.unit!='byte/cycle' or parameter.quantity.value<=0:
        raise ValueError('fixture GM service parameter unavailable')
    formula=GMFormula(formula_id='phase3-fixture-gm-linear',startup_cycles=1.0,cycles_per_byte=1.0/parameter.quantity.value,
        rmw_cycles_per_contribution=1.0,status='calibrated',calibration_sha256='5'*64,
        applicability='fixture-only CPU composition',source_refs=('synthetic fixture service; no physical timing',))
    return gm_actions(bundle.gm_transactions,formula)


def compile_phase3_fixture(bundle, *, hardware=None, production=True, max_cycles=1000000):
    bundle=Phase3FixtureBundle.model_validate_json(bundle.model_dump_json())
    if production:bundle.require_production_ready()
    hardware=bundle.hardware if hardware is None else HardwareParameterSet.model_validate_json(hardware.model_dump_json())
    network=compile_network(bundle.network_workload,bundle.data_binding,hardware,production=False,max_cycles=max_cycles)
    adalink=compile_adalink(bundle.adalink_schedule,bundle.adalink_binding,hardware,bundle.adalink_costs,production=False,max_cycles=max_cycles)
    network_batch=network.spec.batch.model_dump(mode='json');adalink_batch=adalink.spec.batch.model_dump(mode='json')
    flow_stage={row.flow_id:row.stage_id for row in bundle.stage_flows};action_stage={row.action_id:row.stage_id for row in bundle.stage_actions}
    native_flow={native:flow for flow,members in network.native_members.items() for native in (*members,flow)}
    stage_rows=defaultdict(list);stage_members=defaultdict(list);categories={}
    for row in network_batch['transactions']:
        flow=native_flow[row['transaction_id']];stage=flow_stage[flow];stage_rows[stage].append(row);stage_members[stage].append(row['transaction_id']);categories[row['transaction_id']]='network'
    for action in bundle.component_actions:
        row=_component_row(action);stage=action_stage[action.action_id];stage_rows[stage].append(row);stage_members[stage].append(action.action_id);categories[action.action_id]=next(x.category for x in bundle.stage_actions if x.action_id==action.action_id)
    gm_rows=_gm_actions(bundle,hardware)
    for action in gm_rows:
        row=_component_row(action);stage=action_stage[action.action_id];stage_rows[stage].append(row);stage_members[stage].append(action.action_id);categories[action.action_id]='gm'
    for node in bundle.sync_plan.nodes:
        row=dict(transaction_id=node.node_id,depends_on=list(node.depends_on),start_cycles=node.start_cycles,counter_id=node.counter_id)
        if node.delta is not None:row.update(kind='signal',delta=node.delta)
        else:row.update(kind='wait',threshold=node.threshold)
        stage=node.stage;stage_rows[stage].append(row);stage_members[stage].append(node.node_id);categories[node.node_id]='sync'
    for row in adalink_batch['transactions']:
        stage_rows['E8'].append(row);stage_members['E8'].append(row['transaction_id']);categories[row['transaction_id']]='adalink'

    counters={row['counter_id']:row for row in (*network_batch['counters'],*adalink_batch['counters'])}
    for counter in {node.counter_id for node in bundle.sync_plan.nodes}:
        counters.setdefault(counter,dict(counter_id=counter,initial_value=0))
    transactions=[];complete_ids=[]
    for stage in bundle.bounded_plan.stages:
        predecessors=[dependency+'.complete' for dependency in stage.dependencies]
        rows=stage_rows[stage.stage_id]
        for row in rows:
            row=dict(row);row['depends_on']=list(dict.fromkeys([*row.get('depends_on',[]),*predecessors]));transactions.append(row)
        complete=stage.stage_id+'.complete';members=list(stage_members[stage.stage_id]);deps=members or predecessors
        counter='stage.'+stage.stage_id;counters[counter]=dict(counter_id=counter,initial_value=0)
        transactions.append(dict(transaction_id=complete,kind='signal',counter_id=counter,delta=1,depends_on=deps,start_cycles=0));stage_members[stage.stage_id].append(complete)
        categories[complete]='stage_boundary';complete_ids.append(complete)
    all_ids=[row['transaction_id'] for row in transactions]
    timing=[*network.effective_timing,*adalink.effective_timing]
    batch_raw=dict(kind='generic_transaction_batch',schema_version=1,batch_id=bundle.fixture_id,timing=timing,counters=list(counters.values()),
      transactions=transactions,max_cycles=max_cycles,operation_envelopes=[dict(operation_id=bundle.fixture_id+'.whole',member_transactions=all_ids,boundary_ref='fixture-composed-live-event-closure')])
    batch=GenericTransactionBatch.model_validate_json(json.dumps(batch_raw))
    spec=SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec',schema_version=1,spec_id=bundle.fixture_id,graph=bundle.data_binding.graph,batch=batch.model_dump(mode='json'))))
    plan=compile_system(spec)
    return CompiledPhase3Fixture(bundle,hardware,spec,plan,dict(stage_members),network.native_members,categories,gm_rows,timing)


def _critical_to(graph,target):
    nodes={row['id']:row for row in graph['nodes']};incoming=defaultdict(list)
    for edge in graph['edges']:incoming[edge['target']].append((edge['source'],edge['kind']))
    score={};parent={}
    for key,node in sorted(nodes.items(),key=lambda item:(item[1]['time'],item[1].get('order',0),item[0])):
        options=[(score[source]+node['time']-nodes[source]['time'],source,kind) for source,kind in incoming[key] if source in score]
        if options:
            value,source,kind=max(options,key=lambda item:(item[0],item[1]));score[key]=value;parent[key]=(source,kind)
        else:score[key]=0.0
    key=target+'.complete'
    if key not in nodes:return []
    path=[]
    while True:
        path.append(dict(nodes[key],edge_from_parent=None if key not in parent else parent[key][1]))
        if key not in parent:break
        key=parent[key][0]
    path.reverse();return path


def _build_receipt(compiled,runtime,result,observer,error):
    bundle=compiled.bundle;spans={} if result is None else {row.transaction_id:row for row in result.transactions}
    resources=observer.summary();requests=observer.requests;native={row.transaction_id:row for row in compiled.plan.content.transactions};fifo=getattr(runtime,'_fifo_events',[])
    native_rows=[]
    for identity in [row.transaction_id for row in compiled.plan.content.transactions]:
        span=spans.get(identity);owned=[row for row in requests if row.get('transaction_id')==identity]
        native_rows.append(dict(transaction_id=identity,category=compiled.action_categories[identity],status='incomplete' if span is None else span.status,
          start_cycles=None if span is None else span.start_cycles,end_cycles=None if span is None else span.end_cycles,
          resource_wait_cycles=sum(row['grant']-row['requested'] for row in owned if row['grant'] is not None),
          resources=[dict(resource_id=row['resource_id'],mode=row['mode'],requested=row['requested'],grant=row['grant'],released=row['released'],capacity=row['capacity']) for row in owned]))
    flows=[];logical_bytes=0;endpoint_bytes=0;packets=0
    for flow in bundle.network_workload.flows:
        endpoints=[]
        for identity in compiled.flow_members[flow.flow_id]:
            tx=native[identity];packet=tx.hops[0].timing.packet_bytes;delivered=[row for row in fifo if row['transaction_id']==identity and row['action']=='packet_delivered'];span=spans.get(identity)
            endpoints.append(dict(transaction_id=identity,destination=tx.destination,route=[hop.link_id for hop in tx.hops],status='incomplete' if span is None else span.status,
              end_cycles=None if span is None else span.end_cycles,delivered_packets=len(delivered),delivered_bytes=sum(min(packet,flow.payload_bytes-row['packet_index']*packet) for row in delivered)))
        expected=math.ceil(flow.payload_bytes/native[compiled.flow_members[flow.flow_id][0]].hops[0].timing.packet_bytes)
        flows.append(dict(flow_id=flow.flow_id,stage_id=next(row.stage_id for row in bundle.stage_flows if row.flow_id==flow.flow_id),primitive=flow.primitive,
          payload_bytes=flow.payload_bytes,fanout=len(flow.destination_roles),expected_packets=expected,source_packet_injections=len({(row.get('owner',row['transaction_id']),row['packet_index']) for row in fifo if row['transaction_id'] in compiled.flow_members[flow.flow_id] and row['action']=='fifo_admit' and row['stage_index']==0}),endpoints=endpoints))
        logical_bytes+=flow.payload_bytes;endpoint_bytes+=flow.payload_bytes*len(flow.destination_roles);packets+=expected
    matrix=[]
    for work in bundle.matrix_work:matrix.append(dict(work.model_dump(mode='json'),shape=work.shape(),precision=work.precision(),issued_flops=work.issued_flops(),weight_gather=work.weight_gather()))
    vector=[]
    for work in bundle.vector_work:vector.append(dict(work.model_dump(mode='json'),shape=work.shape(),physical_sram_bytes=work.physical_sram_bytes(),real_stage=work.real_stage()))
    gm=[row.model_dump(mode='json') for row in bundle.gm_transactions]
    sync_counters=[] if result is None else [row.model_dump(mode='json') for row in result.counters if row.counter_id in {node.counter_id for node in bundle.sync_plan.nodes}]
    adalink=[]
    for action in bundle.adalink_schedule.actions:
        span=spans.get(action.action_id);row=dict(action.model_dump(mode='json'),status='incomplete' if span is None else span.status,start_cycles=None if span is None else span.start_cycles,end_cycles=None if span is None else span.end_cycles)
        if action.kind=='transfer':
            tx=native[action.action_id];row.update(route_link_ids=[hop.link_id for hop in tx.hops],port_link_ids=list(bundle.adalink_binding.route(action.rank,action.peer_rank).port_link_ids),
              send_rank=action.rank,receive_rank=action.peer_rank,logical_bytes=action.work_bytes)
        adalink.append(row)
    adalink_logical_bytes=sum(action.work_bytes for action in bundle.adalink_schedule.actions if action.kind=='transfer')
    physical_forwards=[]
    for event in fifo:
        if event['action']!='packet_forward':continue
        tx=native[event['transaction_id']];packet=tx.hops[0].timing.packet_bytes
        physical_forwards.append(min(packet,tx.payload_bytes-event['packet_index']*packet))
    rank_flops=next(row for row in bundle.bounded_workload.flops.per_rank if row.ep_rank==0);stage_flops={'R6':(rank_flops.router_useful_flops,rank_flops.router_issued_flops),'E0':(rank_flops.gate_up_useful_flops,rank_flops.gate_up_issued_flops),'E4':(rank_flops.down_useful_flops,rank_flops.down_issued_flops)}
    flow_by_stage=defaultdict(list)
    for flow in flows:flow_by_stage[flow['stage_id']].append(flow)
    stage_boundaries=[]
    for stage in bundle.bounded_plan.stages:
        ids=compiled.stage_members[stage.stage_id];rows=[spans.get(identity) for identity in ids];complete=[row for row in rows if row is not None and row.start_cycles is not None and row.end_cycles is not None]
        useful,issued=stage_flops.get(stage.stage_id,(None,None))
        executed_source=sum(row['payload_bytes'] for row in flow_by_stage[stage.stage_id]);executed_endpoint=sum(row['payload_bytes']*row['fanout'] for row in flow_by_stage[stage.stage_id])
        if stage.stage_id=='E8':executed_source+=adalink_logical_bytes;executed_endpoint+=adalink_logical_bytes
        stage_boundaries.append(dict(stage_id=stage.stage_id,label=stage.label,component=stage.component,resource_kind=stage.resource_kind,active=stage.active,dependencies=list(stage.dependencies),
          participants=list(stage.participants),profile_logical_source_bytes=stage.logical_source_bytes,profile_endpoint_delivered_bytes=stage.endpoint_delivered_bytes,
          executed_logical_source_bytes=executed_source,executed_endpoint_delivered_bytes=executed_endpoint,
          useful_flops=useful,issued_flops=issued,flops_not_applicable_reason=None if useful is not None else 'non-Matrix stage',transaction_ids=ids,
          start_cycles=min((row.start_cycles for row in complete),default=None),end_cycles=max((row.end_cycles for row in complete),default=None)))
    causal=observer.causal_graph(SimpleNamespace(plan=compiled.plan),spans)
    full_static=dict(workload_sha256=bundle.full_workload.workload_sha256,rank_valid_counts=[row.model_dump(mode='json') for row in bundle.full_workload.rank_valid_counts],
      group_useful_flops=bundle.full_workload.flops.group_useful_flops,group_issued_flops=bundle.full_workload.flops.group_issued_flops,
      plan_sha256_by_rank={str(plan.ep_rank):plan.plan_sha256 for plan in bundle.full_plans},stage_inventory=[stage.stage_id for stage in bundle.full_plans[0].stages],
      stage_count=len(bundle.full_plans[0].stages),rank_plans={str(plan.ep_rank):dict(V=plan.V,plan_sha256=plan.plan_sha256,
        stages=[dict(stage_id=stage.stage_id,component=stage.component,resource_kind=stage.resource_kind,dependencies=list(stage.dependencies),active=stage.active,
          logical_source_bytes=stage.logical_source_bytes,endpoint_delivered_bytes=stage.endpoint_delivered_bytes,attributes=stage.attributes) for stage in plan.stages]) for plan in bundle.full_plans},
      executed=False,fixture_only=True)
    provenance=dict(workload_sha256=bundle.bounded_workload.workload_sha256,full_workload_sha256=bundle.full_workload.workload_sha256,
      operation_plan_sha256=bundle.bounded_plan.plan_sha256,binding_sha256=bundle.combined_binding_sha256,hardware_sha256=compiled.hardware.parameter_sha256,
      component_sha256=bundle.component_sha256,bundle_sha256=bundle.bundle_sha256,compiled_plan_sha256=compiled.plan.plan_sha256)
    return dict(schema='t81.phase3.fixture-closure.receipt.v1',status='failed' if error else result.status,error=error,fixture_only=True,
      evidence_status='FIXTURE_ONLY_COMPOSITION_CLOSURE',provenance=provenance,full_profile_static=full_static,
      bounded_execution=dict(workload_sha256=bundle.bounded_workload.workload_sha256,V=rank_flops.V,adalink_payload_bytes_per_rank=bundle.adalink_schedule.payload_bytes_per_rank),
      stage_boundaries=stage_boundaries,logical_flows=flows,native_transactions=native_rows,logical_transaction_count=len(bundle.network_workload.flows)+len(bundle.component_actions)+len(bundle.gm_transactions)+len(bundle.sync_plan.nodes)+len(bundle.adalink_schedule.actions),
      native_transaction_count=len(compiled.plan.content.transactions),logical_network_source_bytes=logical_bytes,endpoint_network_bytes=endpoint_bytes,
      adalink_logical_transfer_bytes=adalink_logical_bytes,total_logical_transfer_bytes=logical_bytes+adalink_logical_bytes,
      native_transfer_bytes=sum(tx.payload_bytes for tx in compiled.plan.content.transactions if getattr(tx,'kind',None)=='transfer'),
      logical_packet_count=packets,physical_packet_forwards=len(physical_forwards),physical_link_bytes=sum(physical_forwards),
      matrix_work=matrix,vector_work=vector,gm_transactions=gm,write_sum_conflict_groups=conflict_groups(bundle.gm_transactions),sync_nodes=[row.model_dump(mode='json') for row in bundle.sync_plan.nodes],sync_counters=sync_counters,
      adalink_actions=adalink,adalink_bytes_by_phase=bundle.adalink_schedule.bytes_by_phase(),resources=resources,resource_requests=requests,
      causal_graph=causal,event_resource_critical_path=_critical_to(causal,'E8.complete'),whole_boundary=[] if result is None else list(result.operation_envelopes),
      cleanup=dict(resources_released=runtime.registry.all_released(),multicast_groups_empty=not bool(getattr(runtime,'_fifo_multicast_groups',{})),background_alive=sum(process.is_alive for process in runtime._background)),
      current_architecture_closure=False,physical_latency_claim=False,mfu_claim=False,architecture_benefit_claim=False)


def execute_phase3_fixture(bundle, *, hardware=None, production=True, max_cycles=1000000, cancel=None, inject_failure=False):
    compiled=compile_phase3_fixture(bundle,hardware=hardware,production=production,max_cycles=max_cycles);runtime=RuntimeContext(compiled.plan);observer=ResourceObserver(runtime)
    if cancel:
        target,when=cancel;known={row.transaction_id for row in compiled.plan.content.transactions}|{bundle.fixture_id+'.whole'}
        if target not in known:raise ValueError('unknown fixture cancellation target')
        def cancelling():yield runtime.env.timeout(when);runtime.cancel(target)
        runtime.env.process(cancelling())
    if inject_failure:
        original=runtime._emit
        def emit(action,transaction_id=None,resource_id=None,detail=None):
            if action=='fifo_event' and detail and 'packet_forward' in detail:raise RuntimeError('PHASE3_FIXTURE_INJECTED_FAILURE')
            return original(action,transaction_id,resource_id,detail)
        runtime._emit=emit
    result=None;error=None
    try:result=runtime.run()
    except RuntimeError as exc:error=str(exc)
    return Phase3FixtureExecution(compiled,runtime,result,_build_receipt(compiled,runtime,result,observer,error),error)


def compare_fixture_mutation(bundle, mutation: HardwareMutation, scope: MutationScope):
    updated=apply_mutations(bundle.hardware,(mutation,),scope);base=execute_phase3_fixture(bundle,hardware=bundle.hardware,production=False);changed=execute_phase3_fixture(bundle,hardware=updated,production=False)
    path=mutation.path;direct=set()
    if path.startswith('data_noc.links.'):
        target=next((row for row in bundle.data_binding.parameters if row.parameter_path==path),None)
        if target is None:raise ValueError('data mutation lacks exact binding')
        links=set(target.link_ids)
        direct={tx.transaction_id for tx in base.compiled.plan.content.transactions if getattr(tx,'kind',None)=='transfer' and tx.network_id=='data-noc' and any(hop.link_id in links for hop in tx.hops)}
    elif path.startswith('adalink.links.'):
        target=next((row for row in bundle.adalink_binding.parameters if row.parameter_path==path),None)
        if target is None:raise ValueError('ADALINK mutation lacks exact binding')
        direct={tx.transaction_id for tx in base.compiled.plan.content.transactions if getattr(tx,'kind',None)=='transfer' and tx.network_id=='adalink-net' and any(hop.link_id==target.link_id for hop in tx.hops)}
    elif path=='gm.controllers.gm0.bytes_per_cycle':
        direct={tx.transaction_id for tx in bundle.gm_transactions if tx.controller_id=='gm0-SERVICE'}
    else:raise ValueError('unsupported fixture mutation path')
    a={row['transaction_id']:row['end_cycles'] for row in base.receipt['native_transactions']};b={row['transaction_id']:row['end_cycles'] for row in changed.receipt['native_transactions']}
    deltas={key:b[key]-a[key] for key in a};changed_ids={key for key,value in deltas.items() if value!=0}
    dependents=defaultdict(set)
    for tx in base.compiled.plan.content.transactions:
        for dependency in tx.depends_on:dependents[dependency].add(tx.transaction_id)
    downstream=set(direct);front=list(direct)
    while front:
        current=front.pop()
        for child in dependents[current]:
            if child not in downstream:downstream.add(child);front.append(child)
    # A delayed action can also move a peer through a shared FIFO/engine queue.
    # Treat that observed resource edge as downstream causality, not as an
    # unrelated change, and close declared dependencies again.
    plan_tx={tx.transaction_id:tx for tx in base.compiled.plan.content.transactions}
    def resource_keys(identity):
        tx=plan_tx[identity]
        if getattr(tx,'kind',None)=='compute':return {('unit',tx.unit_id)}
        if getattr(tx,'kind',None)=='transfer':
            keys=set()
            for hop in tx.hops:
                timing=hop.timing
                for field in ('input_resource_id','output_resource_id','channel_resource_id'):
                    value=getattr(timing,field,None)
                    if value is not None:keys.add(('transport',value))
            return keys
        return set()
    allowed=set(downstream);resource_downstream=set()
    progress=True
    while progress:
        progress=False;claimed=set().union(*(resource_keys(identity) for identity in allowed)) if allowed else set()
        additions={identity for identity in changed_ids-allowed if resource_keys(identity)&claimed}
        if additions:
            resource_downstream|=additions;allowed|=additions;front=list(additions);progress=True
            while front:
                current=front.pop()
                for child in dependents[current]:
                    if child not in allowed:allowed.add(child);front.append(child)
    return dict(path=path,mutation=mutation.model_dump(mode='json'),base_hardware_sha256=bundle.hardware.parameter_sha256,changed_hardware_sha256=updated.parameter_sha256,
      workload_unchanged=base.receipt['provenance']['workload_sha256']==changed.receipt['provenance']['workload_sha256'],
      plan_binding_unchanged=base.receipt['provenance']['binding_sha256']==changed.receipt['provenance']['binding_sha256'],directly_targeted_actions=sorted(direct),
      declared_downstream_actions=sorted(downstream-direct),resource_downstream_actions=sorted(resource_downstream),changed_actions=sorted(changed_ids),
      unexpected_changed_actions=sorted(changed_ids-allowed),completion_delta_cycles=deltas,
      base_receipt=base.receipt,changed_receipt=changed.receipt,fixture_only=True,architecture_benefit_claim=False)


__all__=['CompiledPhase3Fixture','Phase3FixtureExecution','compare_fixture_mutation','compile_phase3_fixture','execute_phase3_fixture']
