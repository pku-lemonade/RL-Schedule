"""Fused R10 composition of three non-overlapping, observed primitives.

Composite service resources own complete counter intervals. Underlying NoC,
chain or sync costs must not also be charged within these intervals.
"""
from typing import Literal
import json
import math
from pydantic import Field, model_validator, TypeAdapter
from .workload import FrozenRecord, stable_sha256
from .timebase import TimebaseDomain, convert_time_value, freeze_timebase_bundle
from .write_sum_primitive import WriteSumPrimitiveDomain, resolve_write_sum_primitive
from .component_service import ComponentAction, execute_actions
from .binding import SuppliedBinding

ZERO_BOUNDARY='GM_WDMA0 counter before zero receive -> after GM fence_all visible zero'
POST_BOUNDARY='GM_RDMA0 counter before rdma_wait_wdma -> max of 32 PE counters after native_receive/release/fence_download1'

class R10BoundaryWork(FrozenRecord):
    profile: Literal['transformer-fused']='transformer-fused'
    consumer_stage: Literal['R10']='R10'
    operation: Literal['zero_init','reduce_done_broadcast_consumer']
    payload_bytes: int=Field(gt=0)
    channel: Literal[1]=1
    source_role: Literal['GM_RDMA0']='GM_RDMA0'
    destination_role: str
    counter_node: str
    boundary: str
    timing_ownership: str
    buffer_binding: Literal['source-fused-expert-reduce-zero-and-valid-count-channel1']='source-fused-expert-reduce-zero-and-valid-count-channel1'
    source_commit: Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    @model_validator(mode='after')
    def valid(self):
        post=self.operation=='reduce_done_broadcast_consumer'
        expected=('PE0..31','GM_RDMA0->max(PE0..31)',POST_BOUNDARY,'R10.reduce_done_valid_count_broadcast_first_consumer') if post else ('GM_WDMA0','GM_WDMA0',ZERO_BOUNDARY,'R10.zero_init_transport_and_visibility')
        if self.payload_bytes%4 or (self.destination_role,self.counter_node,self.boundary,self.timing_ownership)!=expected:
            raise ValueError('R10 boundary/work/participant identity mismatch')
        return self
    @property
    def participants(self):
        return ('GM_WDMA0','GM_RDMA0')+tuple(f'PE{i}' for i in range(32)) if self.operation=='reduce_done_broadcast_consumer' else ('GM_RDMA0','GM_WDMA0')
    @property
    def delivered_bytes(self):
        return self.payload_bytes*(32 if self.operation=='reduce_done_broadcast_consumer' else 1)

class R10BoundaryFormula(FrozenRecord):
    revision: Literal['M04-fused-R10-boundaries-r5']='M04-fused-R10-boundaries-r5'
    operation: Literal['zero_init','reduce_done_broadcast_consumer']
    source_unit: Literal['tsim_counter_default_unit']='tsim_counter_default_unit'
    startup_counter: float=Field(ge=0)
    extra_packet_counter: float=Field(ge=0)
    calibration_payloads: tuple[int,...]
    calibration_scene_ids: tuple[str,...]
    applicability: str
    formula_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if not all(math.isfinite(x) for x in (self.startup_counter,self.extra_packet_counter)) or self.startup_counter<=0 or len(self.calibration_payloads)<2 or not self.calibration_scene_ids or not self.applicability:
            raise ValueError('R10 formula evidence incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('R10 formula hash drift')
        return self
    def parameter_paths(self):
        return tuple('gm.r10.'+self.operation+'.'+x for x in ('startup_counter','extra_packet_counter'))
    def duration(self,work,changes=None):
        work=R10BoundaryWork.model_validate_json(work.model_dump_json())
        if work.operation!=self.operation:raise ValueError('R10 formula operation mismatch')
        changes=changes or {}
        if not set(changes)<=set(self.parameter_paths()):raise ValueError('R10 requires exact local parameter path')
        values=[changes.get(path,getattr(self,name)) for path,name in zip(self.parameter_paths(),('startup_counter','extra_packet_counter'))]
        if any(isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values):raise ValueError('R10 local parameter must be finite nonnegative')
        value=values[0]+max(0,math.ceil(work.payload_bytes/128)-1)*values[1]
        if value<=0:raise ValueError('R10 duration must be positive')
        return value

class R10BoundaryDomain(FrozenRecord):
    work: R10BoundaryWork
    formula: R10BoundaryFormula
    timebase: TimebaseDomain
    observer_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite: int=Field(ge=0)
    terminal_counter_nodes: tuple[str,...]
    source_refs: tuple[str,...]
    domain_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        post=self.work.operation=='reduce_done_broadcast_consumer'
        if (self.formula.operation!=self.work.operation or self.timebase.component_ids!=('M04',)
            or self.timebase.operation!=self.work.operation or self.timebase.profile!=self.work.profile
            or self.timebase.boundary_id!=self.work.boundary or self.timebase.accepted_work_values!=(self.work.payload_bytes,)
            or self.timebase.source_unit!=self.formula.source_unit or self.timebase.status!='validated_simulation'
            or self.terminal_counter_nodes!=(tuple(f'PE{i}' for i in range(32)) if post else ('GM_WDMA0',)) or not self.source_refs):
            raise ValueError('R10 domain formula/timebase/terminal mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('R10 domain hash drift')
        return self

class R10StageDomain(FrozenRecord):
    zero: R10BoundaryDomain
    write_sum: WriteSumPrimitiveDomain
    post: R10BoundaryDomain
    first_downstream: Literal['receive_valid_expert_num_from_gm_rdma/native_receive/release/fence_download1']='receive_valid_expert_num_from_gm_rdma/native_receive/release/fence_download1'
    next_stage: Literal['R11.topk_metadata_upload']='R11.topk_metadata_upload'
    timing_owners: tuple[str,...]
    source_refs: tuple[str,...]
    stage_sha256: str
    @model_validator(mode='after')
    def valid(self):
        if (self.zero.work.operation!='zero_init' or self.post.work.operation!='reduce_done_broadcast_consumer'
            or self.zero.work.payload_bytes!=128 or self.post.work.payload_bytes!=128
            or self.write_sum.work.contributors!=32 or self.write_sum.work.payload_bytes!=128
            or self.timing_owners!=(self.zero.work.timing_ownership,'R10.combined_write_sum',self.post.work.timing_ownership)):
            raise ValueError('R10 requires exact fused C32/B128 three-segment closure')
        identities={(d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite,d.timebase.active_default_unit_seconds,d.timebase.active_resolution_seconds) for d in (self.zero,self.write_sum,self.post)}
        if len(identities)!=1:raise ValueError('R10 cross-segment provider/counter identity conflict')
        if stable_sha256(self.model_dump(mode='json',exclude={'stage_sha256'}))!=self.stage_sha256:raise ValueError('R10 stage hash drift')
        return self

class R10StageBinding(FrozenRecord):
    binding: SuppliedBinding
    resources: dict[str,str]
    buffer_binding: Literal['source-fused-expert-reduce-zero-and-valid-count-channel1']='source-fused-expert-reduce-zero-and-valid-count-channel1'
    channel: Literal[1]=1
    timing_owners: tuple[str,...]
    service_domain_hashes: tuple[str,...]
    source_refs: tuple[str,...]
    binding_sha256: str
    @model_validator(mode='after')
    def valid(self):
        required={'zero_init','write_sum','reduce_done_broadcast_consumer'}
        units={u['unit_id'] for u in self.binding.graph['execution_units']}
        if set(self.resources)!=required or len(set(self.resources.values()))!=3 or not set(self.resources.values())<=units or len(self.service_domain_hashes)!=3 or not self.source_refs:raise ValueError('R10 supplied composite resource binding incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('R10 stage binding hash drift')
        return self

def _freeze(cls,hash_field,**kwargs):
    raw=TypeAdapter(dict).dump_python(kwargs,mode='json');raw[hash_field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_r10_formula(**kwargs):return _freeze(R10BoundaryFormula,'formula_sha256',revision='M04-fused-R10-boundaries-r5',source_unit='tsim_counter_default_unit',**kwargs)
def freeze_r10_domain(**kwargs):return _freeze(R10BoundaryDomain,'domain_sha256',**kwargs)
def freeze_r10_stage(**kwargs):return _freeze(R10StageDomain,'stage_sha256',first_downstream='receive_valid_expert_num_from_gm_rdma/native_receive/release/fence_download1',next_stage='R11.topk_metadata_upload',**kwargs)
def freeze_r10_binding(**kwargs):return _freeze(R10StageBinding,'binding_sha256',buffer_binding='source-fused-expert-reduce-zero-and-valid-count-channel1',channel=1,**kwargs)

def resolve_r10_boundary(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=R10BoundaryWork.model_validate_json(work.model_dump_json())
    matches=[d for d in domains if R10BoundaryDomain.model_validate_json(d.model_dump_json()).work==work and d.timebase.model_library_sha256==model_library_sha256 and d.timebase.systemc_library_sha256==systemc_library_sha256 and d.observer_sha256==observer_sha256 and d.counter_callsite==counter_callsite and not physical]
    if len(matches)!=1:raise ValueError('R10 boundary requires one exact simulation domain, found '+str(len(matches)))
    return matches[0]

def convert_r10_duration(domain,raw_value,**identity):
    d=resolve_r10_boundary((domain,),**identity)
    return convert_time_value(d.timebase,raw_value)

def r10_actions(domain,binding,*,changes=None):
    domain=R10StageDomain.model_validate_json(domain.model_dump_json());binding=R10StageBinding.model_validate_json(binding.model_dump_json())
    domains=(domain.zero,domain.write_sum,domain.post)
    if binding.timing_owners!=domain.timing_owners or binding.service_domain_hashes!=tuple(d.domain_sha256 for d in domains):raise ValueError('R10 binding timing ownership/domain mismatch')
    allowed=set(domain.zero.formula.parameter_paths()+domain.write_sum.formula.parameter_paths()+domain.post.formula.parameter_paths())
    changes=changes or {}
    if not set(changes)<=allowed:raise ValueError('R10 mutation must target a declared segment')
    owners=[];actions=[];previous=()
    for operation,d in zip(('zero_init','write_sum','reduce_done_broadcast_consumer'),domains):
        local={k:v for k,v in changes.items() if k in d.formula.parameter_paths()}
        duration=d.formula.duration(d.work,local)
        owner=domain.timing_owners[len(owners)];owners.append(owner)
        action_id='R10.'+operation+'.combined'
        participants=d.work.participants if operation!='write_sum' else tuple(f'PE{i}' for i in range(32))+('GM_WDMA0',)
        byte_count=d.work.payload_bytes if operation!='write_sum' else d.work.total_payload_bytes
        actions.append(ComponentAction(action_id=action_id,stage='R10',component='M04',resource_id=binding.resources[operation],work=float(byte_count),work_unit='source-payload-byte',duration_cycles=duration,duration_source=owner+'; composite boundary owns service once; raw simulation-counter axis',calibration_sha256=d.formula.formula_sha256,dependencies=previous,bytes=byte_count,participants=participants,status='calibrated'))
        previous=(action_id,)
    actions.append(ComponentAction(action_id='R10.complete',stage='R10',component='M04',resource_id=binding.resources['reduce_done_broadcast_consumer'],work=1,work_unit='all-first-consumer-terminal',duration_cycles=0,duration_source='zero-cost dependency; PE consumer fences already owned by post segment',calibration_sha256=domain.stage_sha256,dependencies=previous,participants=domain.post.work.participants,status='calibrated'))
    return tuple(actions)

def execute_r10_stage(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    actions=r10_actions(domain,binding,changes=changes)
    if production and binding.binding.fixture_only:raise ValueError('production R10 requires real supplied binding')
    if any(a.stage=='R10' for a in extra_actions):raise ValueError('R10 composite ownership forbids duplicate chain/sync/NoC charging')
    result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    durations=[a.duration_cycles for a in actions[:3]]
    result.receipt.update(r10_stage_sha256=domain.stage_sha256,r10_binding_sha256=binding.binding_sha256,component_domain_sha256=[d.domain_sha256 for d in (domain.zero,domain.write_sum,domain.post)],source_unit='tsim_counter_default_unit',model_cycle_mapping=False,physical_publishable=False,timing_owners=list(domain.timing_owners),first_downstream_consumer_in_post=True,next_stage=domain.next_stage,logical_source_bytes=128+4096+128,endpoint_delivered_bytes=128+4096+4096,segment_simulation_seconds=[convert_time_value(d.timebase,v) for d,v in zip((domain.zero,domain.write_sum,domain.post),durations)],combined_internal_costs_separable=False)
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['r10_event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'R10.complete')
        result.receipt['r10_simulation_seconds']=sum(x['converted_seconds'] for x in result.receipt['segment_simulation_seconds'])
    return result

def append_r10_timebases(base,domains):
    additions=tuple(d.timebase for d in domains)
    if {d.domain_id for d in base.domains}&{d.domain_id for d in additions}:raise ValueError('R10 duplicate timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-R10-exact-counter-boundaries',fixture_only=False,domains=base.domains+additions,source_refs=base.source_refs+tuple(ref for d in domains for ref in d.source_refs))
