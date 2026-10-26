"""Timing-free R0-R12/E0-E8 composition over a frozen route workload."""
from pydantic import Field,model_validator
from .workload import FrozenRecord,FrozenMoeWorkload,stable_sha256

class OperationStage(FrozenRecord):
    stage_id: str
    label: str
    component: str
    resource_kind: str
    dependencies: tuple[str,...]
    participants: tuple[str,...]
    logical_source_bytes: int = Field(ge=0)
    endpoint_delivered_bytes: int = Field(ge=0)
    protocols: tuple[str,...]
    active: bool
    skip_reason: str | None=None
    attributes: dict={}
    @model_validator(mode='after')
    def semantics(self):
        if not self.stage_id or not self.label or not self.component or not self.resource_kind or not self.participants:raise ValueError('stage identity/resource/participants required')
        if self.active==(self.skip_reason is not None):raise ValueError('active/skip reason mismatch')
        return self

class MoeOperationPlan(FrozenRecord):
    schema_version: int=1
    profile: str
    ep_rank: int
    V: int
    workload_sha256: str
    binding_sha256: str | None
    fixture_only: bool
    production_ready: bool
    stages: tuple[OperationStage,...]
    plan_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        known=set()
        for stage in self.stages:
            if stage.stage_id in known or not set(stage.dependencies)<=known:raise ValueError('operation stages must be unique and topological')
            known.add(stage.stage_id)
        if tuple(s.stage_id for s in self.stages)!=tuple([f'R{i}' for i in range(13)]+[f'E{i}' for i in range(9)]):raise ValueError('complete R0-R12/E0-E8 stage inventory required')
        if self.production_ready!=(not self.fixture_only and self.binding_sha256 is not None):raise ValueError('production readiness mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'plan_sha256'}))!=self.plan_sha256:raise ValueError('operation plan hash mismatch')
        return self
    def require_production_ready(self):
        if not self.production_ready:raise ValueError('real route trace and supplied physical binding required')
        return self

def build_operation_plan(workload,ep_rank,*,binding_sha256=None,include_allreduce=False,allow_fixture=False):
    workload=FrozenMoeWorkload.model_validate_json(workload.model_dump_json());profile=workload.profile
    if ep_rank not in workload.observed_ep_ranks:raise ValueError('EP rank absent from frozen workload')
    fixture=workload.trace_kind!='real_decode'
    if fixture and not allow_fixture:raise ValueError('synthetic workload may only build fixture plan')
    V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==ep_rank);pes=tuple(f'pe{i}' for i in range(32));roots=tuple(f'pe{i}' for i in range(28,32))
    gate_n=profile.gate_up_per_pe[1];gate_k=profile.gate_up_per_pe[2];down_n=profile.down_per_pe[1];down_k=profile.down_per_pe[2]
    rms=32*profile.hidden_gating*2;quant=32*8*(gate_k+128);router_payload=8*profile.global_experts*4;metadata_buffer=3*32*profile.top_k*4
    stages=[]
    def add(stage_id,label,component,resource,deps,participants,source=0,delivered=0,protocols=(),active=True,skip=None,**attrs):
        stages.append(OperationStage(stage_id=stage_id,label=label,component=component,resource_kind=resource,dependencies=tuple(deps),participants=tuple(participants),
          logical_source_bytes=source,endpoint_delivered_bytes=delivered,protocols=tuple(protocols),active=active,skip_reason=skip,attributes=attrs))
    add('R0','activation input','M01','Data NoC',(),pes,0 if profile.entry_mode.startswith('bridge') else 32*profile.output_hidden*4,
        0 if profile.entry_mode.startswith('bridge') else 32*profile.output_hidden*4,('point-to-point','outer-sync'),active=not profile.entry_mode.startswith('bridge'),
        skip='fused bridge already placed activation' if profile.entry_mode.startswith('bridge') else None,gm_operation='read',gm_payload_per_transaction=profile.output_hidden*4,
        gm_controller_count=4,gm_transaction_count=32,gm_address_distribution='disjoint',datanoc_flows=[dict(flow_role='activation',primitive='point_to_point',direction='gm_to_pe',flow_count=32,payload_per_flow=profile.output_hidden*4,fanout=1,source_count=4,concurrency='controlled_concurrent',route_group='gm-rdma-quarter-to-pe')])
    add('R1','residual add and RMSNorm','M06','Vector',('R0',),pes,protocols=('inner-sync','fence-calc'))
    add('R2','RMS feature gather','M01','Data NoC',('R1',),pes+('gm_wdma0..3',),rms,rms,('point-to-point','outer-sync'),gm_operation='write',gm_payload_per_transaction=profile.hidden_gating*2,
        gm_controller_count=4,gm_transaction_count=32,gm_address_distribution='disjoint',datanoc_flows=[dict(flow_role='rms-upload',primitive='point_to_point',direction='pe_to_gm',flow_count=32,payload_per_flow=profile.hidden_gating*2,fanout=1,source_count=32,concurrency='controlled_concurrent',route_group='pe-to-gm-wdma-column')])
    add('R3','per-token quantization','M06','Vector',('R1',),pes,protocols=('inner-self-sync',))
    add('R4','quantized activation gather','M01','Data NoC',('R3',),pes+('gm_wdma0..3',),quant,quant,('point-to-point','outer-sync'),gm_operation='write',gm_payload_per_transaction=8*(gate_k+128),
        gm_controller_count=4,gm_transaction_count=32,gm_address_distribution='disjoint',datanoc_flows=[dict(flow_role='quant-upload',primitive='point_to_point',direction='pe_to_gm',flow_count=32,payload_per_flow=8*(gate_k+128),fanout=1,source_count=32,concurrency='controlled_concurrent',route_group='pe-to-gm-wdma-column')])
    add('R5','RMS and quant masked multicast','M02','Data NoC',('R2','R4'),pes+('gm_rdma0..3',),rms+quant,rms*8+quant*4,('masked-multicast','wdma-rdma-after-all'),rms_fanout=8,quant_fanout=4,
        datanoc_flows=[dict(flow_role='rms',primitive='masked_multicast',direction='gm_to_pe',flow_count=4,payload_per_flow=rms//4,fanout=8,source_count=4,concurrency='controlled_concurrent',route_group='gm-rdma-column-strided8'),
          dict(flow_role='quant',primitive='masked_multicast',direction='gm_to_pe',flow_count=8,payload_per_flow=quant//8,fanout=4,source_count=4,concurrency='controlled_concurrent',route_group='gm-rdma-halfrow-contiguous4')])
    add('R6','router BF16 GEMM','M05','Matrix',('R5',),pes,protocols=('engine-fifo',))
    add('R7','router column reduce and broadcast','M03','PE chain',('R6',),pes,4*14*router_payload,4*14*router_payload,('chain-reduce','chain-broadcast','outer-sync'),
        chain_variant='column_reduce_broadcast',chain_axis='column',chain_groups=4,chain_participants_per_group=8,chain_reduce_hops=7,chain_broadcast_fanout=7,chain_payload_bytes=router_payload)
    add('R8','softmax and global top-k','M06','Vector',('R7',),pes,protocols=('inner-sync','fence-calc'))
    add('R9','EP filter and valid count','M06/M07','Vector+scalar',('R8',),pes,protocols=('sentinel','stable-topk-order'))
    add('R10','valid-count prefix write-sum','M04/M07','GM write-sum',('R9',),pes+('gm_wdma0',),32*32*4,32*32*4,('gm-write-sum','counted-after-all'),gm_operation='write_sum',
        gm_payload_per_transaction=128,gm_controller_count=1,gm_transaction_count=32,gm_address_distribution='same-address')
    add('R11','top-k metadata allgather','M01/M02/M07','Data NoC',('R10',),pes+('gm_rdma0',),12*V+metadata_buffer,12*V+metadata_buffer*32,('variable-count-upload','masked-multicast','fence-io'),
        datanoc_flows=[dict(flow_role='metadata-upload',primitive='point_to_point',direction='pe_to_gm',flow_count=32,payload_per_flow=None,fanout=1,source_count=32,concurrency='controlled_concurrent',route_group='dynamic-prefix-offset-upload'),
          dict(flow_role='metadata-broadcast',primitive='masked_multicast',direction='gm_to_pe',flow_count=1,payload_per_flow=metadata_buffer,fanout=32,source_count=1,concurrency='isolated',route_group='gm-rdma0-all-pe')])
    add('R12','address-table generation','M06','Vector',('R11',),pes,protocols=('weight-sram-table','gm-scatter-table'),weight_payload_moved=False,data_noc=False)
    body=V>0;skip='V=0 skips expert body'
    add('E0','gate-up W4A8 GEMM','M05','Matrix',('R12',),pes,protocols=('32-PE cooperative','weight-sram-gather'),active=body,skip=None if body else skip,cooperative_pes=32)
    gate_bytes=4*7*V*gate_n*4
    add('E1','gate-up column reduce','M03','PE chain',('E0',),pes,gate_bytes,gate_bytes,('chain-reduce','outer-sync'),active=body,skip=None if body else skip,
        chain_variant='column_reduce_only',chain_axis='column',chain_groups=4,chain_participants_per_group=8,chain_reduce_hops=7,chain_broadcast_fanout=0,chain_payload_bytes=V*gate_n*4)
    add('E2','SiLU score quant','M06','Vector',('E1',),roots,protocols=('root-only','four-TPC'),active=body,skip=None if body else skip)
    down_activation=4*V*(down_k+128)
    add('E3','down activation multicast','M02','Data NoC',('E2',),pes,down_activation,down_activation*7,('masked-multicast','source-once'),active=body,skip=None if body else skip,fanout_per_root=7,
        datanoc_flows=[dict(flow_role='down-activation',primitive='masked_multicast',direction='pe_to_pe',flow_count=4,payload_per_flow=V*(down_k+128),fanout=7,source_count=4,concurrency='controlled_concurrent',route_group='pe-root-column-fanout7')])
    add('E4','down W4A8 GEMM','M05','Matrix',('E3',),pes,protocols=('32-PE cooperative','weight-sram-gather'),active=body,skip=None if body else skip,cooperative_pes=32)
    down_chain=8*6*V*down_n*4
    add('E5','down row reduce and broadcast','M03','PE chain',('E4',),pes,down_chain,down_chain,('chain-reduce','chain-broadcast','outer-sync'),active=body,skip=None if body else skip,
        chain_variant='row_reduce_broadcast',chain_axis='row',chain_groups=8,chain_participants_per_group=4,chain_reduce_hops=3,chain_broadcast_fanout=3,chain_payload_bytes=V*down_n*4)
    scatter=V*profile.output_hidden*4;hotspots=[x.model_dump(mode='json') for x in workload.token_hotspots if x.ep_rank==ep_rank]
    add('E6','GM scatter write-sum','M04','GM write-sum',('E5',),pes+('gm_wdma0..3',),scatter,scatter,('gm-write-sum','address-conflict-groups'),active=body,skip=None if body else skip,hotspot_groups=hotspots,
        gm_operation='scatter_write_sum',gm_payload_per_transaction=down_n*4,gm_controller_count=4,gm_transaction_count=8*V,gm_address_distribution='real-route-trace')
    add('E7','output completion join','M07','counter protocol',('E6',),pes,protocols=('counted-after-all','V0-token-preserved'))
    add('E8','cross-rank merge','M08','ADALINK',('E7',),tuple(f'rank{i}' for i in range(profile.ep_size)),profile.output_buffer_bytes_per_rank if include_allreduce else 0,
        profile.output_buffer_bytes_per_rank if include_allreduce else 0,('reduce-scatter','barrier','all-gather'),active=include_allreduce,
        skip=None if include_allreduce else 'disabled or missing ADALINK binding',data_noc=False)
    raw=dict(schema_version=1,profile=profile.name,ep_rank=ep_rank,V=V,workload_sha256=workload.workload_sha256,binding_sha256=binding_sha256,
      fixture_only=fixture,production_ready=not fixture and binding_sha256 is not None,stages=tuple(stages))
    from pydantic import TypeAdapter
    frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return MoeOperationPlan(**raw,plan_sha256=stable_sha256(frozen))
