"""Strictly fixture-only full M01-M08 composition inputs.

The full workload/plans preserve source-backed profile sizes and formulas.
The executable work is deliberately bounded and carries a different identity.
No topology, timing, route, or result in this module is a production claim.
"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator

from .adalink import AdalinkSchedule, build_direct_schedule
from .adalink_binding import (
    AdalinkCostTable, AdalinkParameterBinding, AdalinkRankBinding,
    AdalinkRouteBinding, LocalReduceCost, RankCclConfig,
    SuppliedAdalinkBinding, freeze_adalink_binding, freeze_adalink_cost_table,
)
from .binding import (
    EndpointBinding, LogicalFlow, NetworkWorkload, ParameterBinding,
    SuppliedBinding, freeze_binding, freeze_network_workload,
)
from .collective import PEChain
from .component_service import ComponentAction
from .engine import EngineFormula, EngineSlice
from .gm_service import GMTransaction
from .hardware import (
    ApplicabilityDomain, HardwareParameter, HardwareParameterSet,
    ParameterValue, freeze_hardware_parameters,
)
from .matrix import MatrixWork
from .operation_plan import MoeOperationPlan, build_operation_plan
from .sync import SyncNode, SyncPlan, freeze_sync_plan
from .vector import VectorWork
from .workload import (
    FrozenMoeWorkload, FrozenRecord, PROFILES, RouteRecord, RouteTraceIdentity,
    freeze_workload, stable_sha256,
)

ProfileName = Literal['standalone', 'transformer-fused']


class StageFlowMap(FrozenRecord):
    flow_id: str
    stage_id: str


class StageActionMap(FrozenRecord):
    action_id: str
    stage_id: str
    category: Literal['matrix', 'vector', 'gm', 'sync']


class Phase3FixtureBundle(FrozenRecord):
    schema_version: Literal[1] = 1
    fixture_id: str
    profile: ProfileName
    fixture_only: Literal[True] = True
    full_workload: FrozenMoeWorkload
    bounded_workload: FrozenMoeWorkload
    full_plans: tuple[MoeOperationPlan, ...]
    bounded_plan: MoeOperationPlan
    network_workload: NetworkWorkload
    data_binding: SuppliedBinding
    adalink_schedule: AdalinkSchedule
    adalink_binding: SuppliedAdalinkBinding
    adalink_costs: AdalinkCostTable
    hardware: HardwareParameterSet
    chains: tuple[PEChain, ...]
    matrix_work: tuple[MatrixWork, ...]
    vector_work: tuple[VectorWork, ...]
    gm_transactions: tuple[GMTransaction, ...]
    sync_plan: SyncPlan
    component_actions: tuple[ComponentAction, ...]
    stage_flows: tuple[StageFlowMap, ...]
    stage_actions: tuple[StageActionMap, ...]
    combined_binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    component_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_refs: tuple[str, ...]
    bundle_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def integrity(self):
        if self.full_workload.profile.name != self.profile or self.bounded_workload.profile.name != self.profile:
            raise ValueError('fixture profile/workload mismatch')
        if self.full_workload.trace_kind != 'synthetic_fixture' or self.bounded_workload.trace_kind != 'synthetic_fixture':
            raise ValueError('Phase3 fixture cannot contain a real trace label')
        profile = PROFILES[self.profile]
        if tuple(plan.ep_rank for plan in self.full_plans) != tuple(range(profile.ep_size)):
            raise ValueError('full fixture plans must cover every EP rank')
        inventory = tuple([f'R{i}' for i in range(13)] + [f'E{i}' for i in range(9)])
        if any(tuple(stage.stage_id for stage in plan.stages) != inventory for plan in (*self.full_plans, self.bounded_plan)):
            raise ValueError('fixture plan stage inventory incomplete')
        if any(plan.production_ready or not plan.fixture_only for plan in (*self.full_plans, self.bounded_plan)):
            raise ValueError('fixture plans cannot be production ready')
        if not (self.network_workload.fixture_only and self.data_binding.fixture_only and self.adalink_schedule.fixture_only and self.adalink_binding.fixture_only and self.sync_plan.fixture_only):
            raise ValueError('every composed input must remain fixture-only')
        if self.adalink_schedule.workload_sha256 != self.bounded_workload.workload_sha256:
            raise ValueError('ADALINK schedule must bind the bounded workload identity')
        expected_binding = stable_sha256({'data': self.data_binding.binding_sha256, 'adalink': self.adalink_binding.binding_sha256})
        if self.combined_binding_sha256 != expected_binding:
            raise ValueError('combined binding identity mismatch')
        flow_ids = {flow.flow_id for flow in self.network_workload.flows}
        if {row.flow_id for row in self.stage_flows} != flow_ids:
            raise ValueError('every logical flow requires one stage mapping')
        action_ids = {action.action_id for action in self.component_actions} | {tx.transaction_id for tx in self.gm_transactions} | {node.node_id for node in self.sync_plan.nodes}
        if {row.action_id for row in self.stage_actions} != action_ids:
            raise ValueError('every component/control action requires one stage mapping')
        if {chain.collective_id for chain in self.chains} != {'R7.chain', 'E1.chain', 'E5.chain'}:
            raise ValueError('all source chain roles required')
        components = ''.join(stage.component for stage in self.bounded_plan.stages)
        if any(f'M{i:02d}' not in components for i in range(1, 9)):
            raise ValueError('M01-M08 consumers must all be present')
        if not self.source_refs:
            raise ValueError('fixture sources required')
        if stable_sha256(self.model_dump(mode='json', exclude={'bundle_sha256'})) != self.bundle_sha256:
            raise ValueError('fixture bundle hash mismatch')
        return self

    def require_production_ready(self):
        raise ValueError('fixture-only Phase3 closure cannot enter production')


def _identity(profile, suffix):
    return RouteTraceIdentity(
        model_id='fixture-model', checkpoint_id='fixture-checkpoint', request_id=f'phase3-{suffix}', seed=81,
        source_commit='094bcd25e985a4c836fd5521cccd61b14d911aac', profile=profile,
        capture_point='post_ep_filter_pre_metadata', source_kind='cpu_reference')


def _full_records(profile):
    p = PROFILES[profile]; rows = []
    for token in range(p.token_count):
        for slot in range(p.top_k):
            flat = token * p.top_k + slot; rank = flat % p.ep_size; local = flat // p.ep_size
            rows.append(RouteRecord(sequence_index=len(rows), token_id=token, topk_slot=slot,
                global_expert_id=rank * p.experts_per_rank + local, ep_rank=rank, local_expert_id=local,
                score=1.0 / p.top_k, status='valid'))
    return tuple(rows)


def _bounded_records(profile):
    p = PROFILES[profile]; rows = []
    for rank in range(p.ep_size):
        count = 2 if rank == 0 else 1
        for slot in range(count):
            rows.append(RouteRecord(sequence_index=len(rows), token_id=rank, topk_slot=slot,
                global_expert_id=rank * p.experts_per_rank + slot, ep_rank=rank, local_expert_id=slot,
                score=0.75 - 0.1 * slot, status='valid'))
    return tuple(rows)


def _workloads(profile):
    refs = ('T81 Phase3 fixture-only composition; not a captured decode route',)
    full = freeze_workload(_identity(profile, 'full-static'), _full_records(profile), trace_kind='synthetic_fixture', trace_scope='ep_group', source_refs=refs)
    bounded = freeze_workload(_identity(profile, 'bounded-exec'), _bounded_records(profile), trace_kind='synthetic_fixture', trace_scope='ep_group', source_refs=refs)
    return full, bounded


def _flows(profile):
    flows = []; maps = []
    def add(stage, flow):
        flows.append(flow); maps.append(StageFlowMap(flow_id=flow.flow_id, stage_id=stage))
    if profile == 'standalone':
        add('R0', LogicalFlow(flow_id='R0.activation', source_role='gm_rdma0', destination_roles=('pe0',), payload_bytes=64, primitive='point_to_point'))
    add('R2', LogicalFlow(flow_id='R2.rms-upload', source_role='pe0', destination_roles=('gm_wdma0',), payload_bytes=64, primitive='point_to_point'))
    add('R4', LogicalFlow(flow_id='R4.quant-upload', source_role='pe1', destination_roles=('gm_wdma1',), payload_bytes=64, primitive='point_to_point'))
    add('R5', LogicalFlow(flow_id='R5.rms-multicast', source_role='gm_rdma0', destination_roles=tuple(f'pe{i}' for i in range(4)), payload_bytes=64, primitive='masked_multicast'))
    add('R5', LogicalFlow(flow_id='R5.quant-multicast', source_role='gm_rdma1', destination_roles=tuple(f'pe{i}' for i in range(4, 8)), payload_bytes=64, primitive='masked_multicast'))
    chains = (
        PEChain(collective_id='R7.chain', axis='column', payload_bytes=16, packet_bytes=16),
        PEChain(collective_id='E1.chain', axis='column', payload_bytes=16, packet_bytes=16),
        PEChain(collective_id='E5.chain', axis='row', payload_bytes=16, packet_bytes=16),
    )
    for stage, chain in zip(('R7', 'E1', 'E5'), chains):
        for flow in chain.streaming_flows(): add(stage, flow)
    add('R11', LogicalFlow(flow_id='R11.metadata-upload', source_role='pe0', destination_roles=('gm_wdma0',), payload_bytes=32, primitive='point_to_point'))
    add('R11', LogicalFlow(flow_id='R11.metadata-multicast', source_role='gm_rdma2', destination_roles=tuple(f'pe{i}' for i in range(8)), payload_bytes=32, primitive='masked_multicast'))
    add('E3', LogicalFlow(flow_id='E3.down-multicast', source_role='pe28', destination_roles=tuple(f'pe{i}' for i in range(7)), payload_bytes=32, primitive='masked_multicast'))
    return tuple(flows), tuple(maps), chains


def _timing(stage, identity, packet=16):
    return dict(mode='latency_fifo', stage=stage, packet_bytes=packet, issue_cycles=2,
        input_latency_cycles=1, channel_latency_cycles=2, fifo_slots=2, channel_slots=1,
        input_resource_id=identity+'.input', output_resource_id=identity+'.output',
        channel_resource_id=identity+'.channel', flow_serialized=False)


def _graph_and_bindings(profile, flows, bounded_hash):
    p = PROFILES[profile]; nodes=[]; ports=[]; links=[]; endpoints=[]; units=[]; routes=[]; cursor=0
    def node(identity, role='compute'):
        nonlocal cursor
        nodes.append(dict(node_id=identity, x=cursor, y=0, role=role));cursor += 1
    data_nodes=[]; role_endpoint={}; endpoint_node={}
    for pe in range(32):
        identity=f'pe-node-{pe}';node(identity);data_nodes.append(identity)
        ports.append(dict(port_id='data-local',node_id=identity,network_id='data-noc',kind='local'))
        endpoint=f'pe{pe}.data';endpoints.append(dict(endpoint_id=endpoint,node_id=identity,network_id='data-noc',port_id='data-local'));role_endpoint[f'pe{pe}']=endpoint;endpoint_node[endpoint]=identity
        for unit_name in ('MATRIX','VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'):
            port=f'{unit_name.lower()}-local';ports.append(dict(port_id=port,node_id=identity,network_id='data-noc',kind='local'))
            units.append(dict(unit_id=f'pe{pe}-{unit_name}',node_id=identity,network_id='data-noc',port_id=port))
    for gm in range(4):
        identity=f'gm-node-{gm}';node(identity);data_nodes.append(identity)
        for kind in ('rdma','wdma'):
            port=f'{kind}-local';endpoint=f'gm_{kind}{gm}.data';ports.append(dict(port_id=port,node_id=identity,network_id='data-noc',kind='local'))
            endpoints.append(dict(endpoint_id=endpoint,node_id=identity,network_id='data-noc',port_id=port));role_endpoint[f'gm_{kind}{gm}']=endpoint;endpoint_node[endpoint]=identity
        ports.append(dict(port_id='service-local',node_id=identity,network_id='data-noc',kind='local'))
        units.append(dict(unit_id=f'gm{gm}-SERVICE',node_id=identity,network_id='data-noc',port_id='service-local'))
    node('gsram-node');data_nodes.append('gsram-node');ports.append(dict(port_id='data-local',node_id='gsram-node',network_id='data-noc',kind='local'))
    endpoints.append(dict(endpoint_id='gsram.data',node_id='gsram-node',network_id='data-noc',port_id='data-local'));role_endpoint['gsram']='gsram.data';endpoint_node['gsram.data']='gsram-node'
    node('data-hub-in','transit');node('data-hub-out','transit')
    data_timing={}
    for identity in data_nodes:
        ports.extend([dict(port_id='data-out',node_id=identity,network_id='data-noc',kind='network'),dict(port_id='data-in',node_id=identity,network_id='data-noc',kind='network'),
          dict(port_id='from-'+identity,node_id='data-hub-in',network_id='data-noc',kind='network'),dict(port_id='to-'+identity,node_id='data-hub-out',network_id='data-noc',kind='network')])
        inject='data.inject.'+identity;egress='data.egress.'+identity
        links.extend([dict(link_id=inject,network_id='data-noc',src_node=identity,src_port='data-out',dst_node='data-hub-in',dst_port='from-'+identity),
          dict(link_id=egress,network_id='data-noc',src_node='data-hub-out',src_port='to-'+identity,dst_node=identity,dst_port='data-in')])
        data_timing[inject]=_timing('ingress',inject);data_timing[egress]=_timing('egress',egress)
    ports.extend([dict(port_id='central-out',node_id='data-hub-in',network_id='data-noc',kind='network'),dict(port_id='central-in',node_id='data-hub-out',network_id='data-noc',kind='network')])
    links.append(dict(link_id='data.central',network_id='data-noc',src_node='data-hub-in',src_port='central-out',dst_node='data-hub-out',dst_port='central-in'));data_timing['data.central']=_timing('transit','data.central')
    pair_routes=set()
    for flow in flows:
        source=role_endpoint[flow.source_role]
        for role in flow.destination_roles:
            destination=role_endpoint[role];key=(source,destination)
            if key in pair_routes:continue
            pair_routes.add(key);sn=endpoint_node[source];dn=endpoint_node[destination]
            routes.append(dict(network_id='data-noc',source=source,destination=destination,link_ids=[f'data.inject.{sn}','data.central',f'data.egress.{dn}']))

    ada_timing={};ada_routes=[];rank_bindings=[];ccl=[]
    for rank in range(p.ep_size):
        identity=f'ada-rank-{rank}';node(identity)
        ports.extend([dict(port_id='dma-local',node_id=identity,network_id='adalink-net',kind='local'),dict(port_id='reduce-local',node_id=identity,network_id='adalink-net',kind='local')])
        endpoints.append(dict(endpoint_id=f'rank-{rank}.dma',node_id=identity,network_id='adalink-net',port_id='dma-local'))
        units.append(dict(unit_id=f'rank-{rank}.reduce',node_id=identity,network_id='adalink-net',port_id='reduce-local'))
        rank_bindings.append(AdalinkRankBinding(rank=rank,endpoint_id=f'rank-{rank}.dma',reduce_unit_id=f'rank-{rank}.reduce',device_id=f'fixture-device-{rank}',source_ref='fixture-only rank map'))
        node_ids=tuple(f'fixture-r{rank}-peer{peer}-p{lane}' for peer in range(p.ep_size) if peer!=rank for lane in range(2))
        ccl.append(RankCclConfig(rank=rank,commid_slot_idx=0,rank_info_offset=rank*p.ep_size,reduce_ordered=True,port_num=len(node_ids),
          send_ranks=tuple(range(p.ep_size)),recv_ranks=tuple(range(p.ep_size)),fullmesh_node_ids=node_ids,source_ref='fixture-only CclConfig'))
    ada_parameters=[]
    for source in range(p.ep_size):
        for destination in range(p.ep_size):
            if source==destination:continue
            middle=f'ada-mid-{source}-{destination}';node(middle,'transit')
            ports.extend([dict(port_id=f'out-{destination}',node_id=f'ada-rank-{source}',network_id='adalink-net',kind='network'),
              dict(port_id=f'in-{source}',node_id=f'ada-rank-{destination}',network_id='adalink-net',kind='network'),
              dict(port_id='in',node_id=middle,network_id='adalink-net',kind='network'),dict(port_id='out',node_id=middle,network_id='adalink-net',kind='network')])
            inject=f'ada.{source}.{destination}.inject';data=f'ada.{source}.{destination}.data'
            links.extend([dict(link_id=inject,network_id='adalink-net',src_node=f'ada-rank-{source}',src_port=f'out-{destination}',dst_node=middle,dst_port='in'),
              dict(link_id=data,network_id='adalink-net',src_node=middle,src_port='out',dst_node=f'ada-rank-{destination}',dst_port=f'in-{source}')])
            ada_timing[inject]=_timing('ingress',inject);ada_timing[data]=_timing('egress',data)
            path=(inject,data);routes.append(dict(network_id='adalink-net',source=f'rank-{source}.dma',destination=f'rank-{destination}.dma',link_ids=list(path)))
            ada_routes.append(AdalinkRouteBinding(source_rank=source,destination_rank=destination,network_id='adalink-net',link_ids=path,port_link_ids=(data,),source_ref='fixture-only route'))
            for link in path:
                ada_parameters.extend([AdalinkParameterBinding(parameter_path=f'adalink.links.{link}.bytes_per_cycle',network_id='adalink-net',link_id=link,effect='bandwidth'),
                  AdalinkParameterBinding(parameter_path=f'adalink.links.{link}.channel_latency_cycles',network_id='adalink-net',link_id=link,effect='channel_latency')])
    graph=dict(kind='generic_system_graph',schema_version=1,system_id=f'phase3-{profile}-fixture',nodes=nodes,
      networks=[dict(network_id='data-noc'),dict(network_id='adalink-net')],ports=ports,links=links,dma_endpoints=endpoints,execution_units=units,memory_resources=[],static_routes=routes)
    def timing(network, rows):
        keys=sorted(rows);return (dict(network_id=network,link=rows[keys[0]],overrides=[dict(link_id=k,timing=rows[k]) for k in keys[1:]]),)
    endpoint_bindings=[]
    for role,endpoint in sorted(role_endpoint.items()):
        kind='pe' if role.startswith('pe') else ('gm_rdma' if 'rdma' in role else ('gm_wdma' if 'wdma' in role else 'gsram'))
        endpoint_bindings.append(EndpointBinding(role_id=role,endpoint_id=endpoint,kind=kind))
    data_binding=freeze_binding(binding_id=f'phase3-{profile}-data-fixture',fixture_only=True,graph=graph,timing=timing('data-noc',data_timing),endpoints=tuple(endpoint_bindings),
      parameters=(ParameterBinding(parameter_path='data_noc.links.data.central.bytes_per_cycle',network_id='data-noc',link_ids=('data.central',),allowed_component='M01'),),
      source_refs=('synthetic hub fixture; never a physical topology',),calibration_sha256='1'*64)
    adalink_binding=freeze_adalink_binding(binding_id=f'phase3-{profile}-adalink-fixture',fixture_only=True,graph=graph,timing=timing('adalink-net',ada_timing),
      ccl_configs=tuple(ccl),ranks=tuple(rank_bindings),routes=tuple(ada_routes),parameters=tuple(ada_parameters),
      source_refs=('synthetic direct-link fixture; never a physical ADALINK map',),calibration_sha256='2'*64)
    domain=ApplicabilityDomain(description='fixture-only CPU composition')
    parameters=[HardwareParameter(path='data_noc.links.data.central.bytes_per_cycle',resource_id='data-noc/data.central',component='M01',
      quantity=ParameterValue(value=8.0,unit='byte/cycle',source_ref='fixture-only',calibration_status='uncalibrated',applicability=domain,minimum=.125)),
      HardwareParameter(path='gm.controllers.gm0.bytes_per_cycle',resource_id='gm0-SERVICE',component='M04',
      quantity=ParameterValue(value=8.0,unit='byte/cycle',source_ref='fixture-only',calibration_status='uncalibrated',applicability=domain,minimum=.125))]
    seen=set()
    for binding in ada_parameters:
        if binding.parameter_path in seen:continue
        seen.add(binding.parameter_path);bandwidth=binding.effect=='bandwidth'
        parameters.append(HardwareParameter(path=binding.parameter_path,resource_id=f'adalink-net/{binding.link_id}',component='M08',
          quantity=ParameterValue(value=8.0 if bandwidth else 2.0,unit='byte/cycle' if bandwidth else 'cycle',source_ref='fixture-only',
          calibration_status='uncalibrated',applicability=domain,minimum=.125 if bandwidth else 0.0)))
    hardware=freeze_hardware_parameters(f'phase3-{profile}-fixture','fixture-cycle',parameters,('fixture-only parameters; no physical interpretation',))
    return graph,data_binding,adalink_binding,hardware


def _components(profile, bounded):
    p=PROFILES[profile];rank0=next(row for row in bounded.flops.per_rank if row.ep_rank==0);V=rank0.V
    experts=tuple(row.local_expert_id for row in bounded.records if row.ep_rank==0 and row.status=='valid')
    matrix=(MatrixWork(work_id='R6.router',profile=profile,operation='router',V=0,pe_id=0),
      MatrixWork(work_id='E0.gate-up',profile=profile,operation='gate_up',V=V,pe_id=0,expert_sequence=experts),
      MatrixWork(work_id='E4.down',profile=profile,operation='down',V=V,pe_id=0,expert_sequence=experts))
    vectors=(VectorWork(work_id='R1.rmsnorm',profile=profile,operation='rmsnorm',work_items=1,pe_id=0),
      VectorWork(work_id='R3.quant',profile=profile,operation='quant',work_items=1,pe_id=1),
      VectorWork(work_id='R8.softmax',profile=profile,operation='softmax_topk',work_items=1,pe_id=0),
      VectorWork(work_id='R9.filter',profile=profile,operation='softmax_topk',work_items=1,pe_id=0),
      VectorWork(work_id='R12.table',profile=profile,operation='table',work_items=V,pe_id=0),
      VectorWork(work_id='E2.silu',profile=profile,operation='silu_score_quant',work_items=V,pe_id=28))
    actions=[];maps=[]
    matrix_stages=('R6','E0','E4')
    for stage,work in zip(matrix_stages,matrix):
        formula=EngineFormula(component='M05',operation=work.operation,shape_key=str(work.shape()),model_revision='fixture-only',
          slices=(EngineSlice(engine='MATRIX',start_offset_cycles=0,duration_cycles=3,repeat_start_offset_cycles=4,repeat_interval_cycles=4,repeat_duration_cycles=3,parameter_path='fixture.matrix'),),
          calibration_sha256='3'*64,source_refs=('fixture-only Matrix cost',),applicability='CPU composition only')
        rows=formula.actions(work.work_id,work.pe_id,1 if work.operation=='router' else V,(f'pe{work.pe_id}',))
        actions.extend(rows);maps.extend(StageActionMap(action_id=a.action_id,stage_id=stage,category='matrix') for a in rows)
    vector_stages=('R1','R3','R8','R9','R12','E2')
    for stage,work in zip(vector_stages,vectors):
        formula=EngineFormula(component='M06',operation=work.operation,shape_key=str(work.shape()),model_revision='fixture-only',
          slices=tuple(EngineSlice(engine=engine,start_offset_cycles=index,duration_cycles=1,repeat_start_offset_cycles=index+3,repeat_interval_cycles=3,repeat_duration_cycles=1,parameter_path='fixture.vector.'+engine)
            for index,engine in enumerate(('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))),
          calibration_sha256='4'*64,source_refs=('fixture-only Vector cost',),applicability='CPU composition only')
        rows=formula.actions(work.work_id,work.pe_id,1,(f'pe{work.pe_id}',))
        actions.extend(rows);maps.extend(StageActionMap(action_id=a.action_id,stage_id=stage,category='vector') for a in rows)
    gm=[]
    gm.append(GMTransaction(transaction_id='R2.gm-read',source_pe=0,token_id=0,output_shard=0,address=0,payload_bytes=32,operation='read',controller_id='gm0-SERVICE'))
    for pe in range(4):gm.append(GMTransaction(transaction_id=f'R10.prefix.pe{pe}',source_pe=pe,token_id=0,output_shard=0,address=64,payload_bytes=4,operation='write_sum',controller_id='gm0-SERVICE'))
    gm.append(GMTransaction(transaction_id='R11.gm-write',source_pe=0,token_id=0,output_shard=0,address=128,payload_bytes=32,operation='write',controller_id='gm0-SERVICE'))
    for index,row in enumerate(x for x in bounded.records if x.ep_rank==0 and x.status=='valid'):
        gm.append(GMTransaction(transaction_id=f'E6.scatter.{index}',source_pe=index,token_id=row.token_id,output_shard=0,address=256+row.token_id*p.output_hidden*4,
          payload_bytes=32,operation='write_sum',controller_id='gm0-SERVICE'))
    for tx in gm:
        stage='R2' if tx.transaction_id.startswith('R2') else ('R10' if tx.transaction_id.startswith('R10') else ('R11' if tx.transaction_id.startswith('R11') else 'E6'))
        maps.append(StageActionMap(action_id=tx.transaction_id,stage_id=stage,category='gm'))
    sync_nodes=(
      SyncNode(node_id='R1.inner-release',stage='R1',kind='inner_release',counter_id='R1.inner',delta=1,participants=('pe0',)),
      SyncNode(node_id='R1.inner-acquire',stage='R1',kind='inner_acquire',counter_id='R1.inner',threshold=1,depends_on=('R1.inner-release',),participants=('pe0',)),
      SyncNode(node_id='R1.fence-calc',stage='R1',kind='fence_calc',counter_id='R1.fence',delta=1,depends_on=('R1.inner-acquire',),participants=('pe0',)),
      *(SyncNode(node_id=f'R10.count.{i}',stage='R10',kind='count_signal',counter_id='R10.after-all',delta=1,participants=(f'pe{i}',)) for i in range(4)),
      SyncNode(node_id='R10.after-all',stage='R10',kind='count_wait',counter_id='R10.after-all',threshold=4,depends_on=tuple(f'R10.count.{i}' for i in range(4)),participants=('gm_wdma0',)),
      SyncNode(node_id='R11.fence-io',stage='R11',kind='fence_io',counter_id='R11.fence',delta=1,participants=('gm_rdma0','pe0')),
      *(SyncNode(node_id=f'E7.count.{i}',stage='E7',kind='count_signal',counter_id='E7.after-all',delta=1,participants=(f'pe{i}',)) for i in range(4)),
      SyncNode(node_id='E7.after-all',stage='E7',kind='count_wait',counter_id='E7.after-all',threshold=4,depends_on=tuple(f'E7.count.{i}' for i in range(4)),participants=('pe0','pe1','pe2','pe3')))
    sync=freeze_sync_plan(protocol_id=f'phase3-{profile}-fixture-sync',fixture_only=True,nodes=sync_nodes,source_refs=('fixture-only M07 composition protocol',))
    maps.extend(StageActionMap(action_id=node.node_id,stage_id=node.stage,category='sync') for node in sync.nodes)
    return matrix,vectors,tuple(gm),sync,tuple(actions),tuple(maps)


def build_phase3_fixture(profile: ProfileName):
    if profile not in PROFILES:raise ValueError('unknown MoE profile')
    full,bounded=_workloads(profile);flows,stage_flows,chains=_flows(profile)
    graph,data_binding,adalink_binding,hardware=_graph_and_bindings(profile,flows,bounded.workload_sha256)
    network=freeze_network_workload(workload_id=f'phase3-{profile}-bounded-network',fixture_only=True,flows=flows,source_refs=('bounded fixture network flows',))
    p=PROFILES[profile]
    adalink=build_direct_schedule(f'phase3-{profile}-bounded-adalink',p.ep_size,256,fixture_only=True,workload_sha256=bounded.workload_sha256,
      source_refs=('bounded fixture collective schedule; full payload remains in static plan',),pe_completion_count_per_rank=32)
    costs=freeze_adalink_cost_table(table_id=f'phase3-{profile}-fixture-reduce',costs=tuple(LocalReduceCost(rank=r,status='calibrated',startup_cycles=1,cycles_per_byte=.01,source_refs=('fixture-only local reduce cost',)) for r in range(p.ep_size)),source_refs=('fixture-only; not TSim calibrated',))
    combined=stable_sha256({'data':data_binding.binding_sha256,'adalink':adalink_binding.binding_sha256})
    full_plans=tuple(build_operation_plan(full,rank,binding_sha256=combined,include_allreduce=True,allow_fixture=True) for rank in range(p.ep_size))
    bounded_plan=build_operation_plan(bounded,0,binding_sha256=combined,include_allreduce=True,allow_fixture=True)
    matrix,vectors,gm,sync,actions,stage_actions=_components(profile,bounded)
    component=stable_sha256({'chains':[x.model_dump(mode='json') for x in chains],'matrix':[x.model_dump(mode='json') for x in matrix],
      'vector':[x.model_dump(mode='json') for x in vectors],'gm':[x.model_dump(mode='json') for x in gm],'sync':sync.plan_sha256,
      'actions':[x.model_dump(mode='json') for x in actions],'adalink':adalink.schedule_sha256})
    raw=dict(schema_version=1,fixture_id=f'phase3-{profile}-fixture-closure',profile=profile,fixture_only=True,full_workload=full,bounded_workload=bounded,
      full_plans=full_plans,bounded_plan=bounded_plan,network_workload=network,data_binding=data_binding,adalink_schedule=adalink,adalink_binding=adalink_binding,
      adalink_costs=costs,hardware=hardware,chains=chains,matrix_work=matrix,vector_work=vectors,gm_transactions=gm,sync_plan=sync,
      component_actions=actions,stage_flows=stage_flows,stage_actions=stage_actions,combined_binding_sha256=combined,component_sha256=component,
      source_refs=('T79 R0-R12/E0-E8 decomposition','M01-M08 fixture-only component interfaces'))
    return Phase3FixtureBundle(**raw,bundle_sha256=stable_sha256({k:(v.model_dump(mode='json') if hasattr(v,'model_dump') else [x.model_dump(mode='json') for x in v] if isinstance(v,tuple) and v and hasattr(v[0],'model_dump') else v) for k,v in raw.items()}))


__all__=['Phase3FixtureBundle','StageActionMap','StageFlowMap','build_phase3_fixture']
