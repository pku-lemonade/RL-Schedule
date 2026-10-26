"""Source-exact EP filter completion to local valid-count initialization readiness."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,TimebaseBundle,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

PARTICIPANTS=tuple(range(32))
OWNER='R9.filter_to_valid_count_init.local_self_acquire_tail'
BOUNDARY='per PE final topk_stage2 VECTOR_WRITE completion -> exact VECTOR self acquire/API completion before valid_count fill'
CALLSITE='router_gemm.hpp:462-469;softmax_topk.hpp:251-294;ada_io.hpp:621-624;vector_fill.hpp:17-45'

class R9CountHandoffWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    producer_stage:Literal['R9.filter']='R9.filter'
    consumer_stage:Literal['R9.valid_count_init']='R9.valid_count_init'
    operation:Literal['filter_valid_count_readiness']='filter_valid_count_readiness'
    V:Literal[64]=64
    ep_rank:int=Field(ge=0,lt=4)
    pe_count:Literal[32]=32
    owned_pes:tuple[int,...]=PARTICIPANTS
    payload_bytes:Literal[256]=256
    input_elements:Literal[32]=32
    valid_slots:Literal[8]=8
    expert_per_rank:Literal[96]=96
    fill_elements:Literal[32]=32
    fill_bytes:Literal[128]=128
    buffer:Literal['moe.topk_score+topk_idx->valid_expert_num']='moe.topk_score+topk_idx->valid_expert_num'
    local_resource:Literal['PE_VECTOR->PE_VECTOR']='PE_VECTOR->PE_VECTOR'
    first_consumer:Literal['tl_vector_fill::fill_value<int32_t>(valid_expert_num,0)']='tl_vector_fill::fill_value<int32_t>(valid_expert_num,0)'
    terminal_kind:Literal['local_vector_self_acquire']='local_vector_self_acquire'
    source_callsite:Literal[CALLSITE]=CALLSITE
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    timing_ownership:Literal[OWNER]=OWNER
    boundary:Literal[BOUNDARY]=BOUNDARY
    @model_validator(mode='after')
    def exact(self):
        if self.owned_pes!=PARTICIPANTS:raise ValueError('R9/count exact source participants mismatch')
        return self

def extract_r9_count_source(source,filter_source,io_source,fill_source,ep_rank):
    compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
    code=compact(source)
    required=('topk_stage_2<EXPERT_NUM>(topk_score,topk_idx,ep_rank*expert_num_per_ep_rank,expert_num_per_ep_rank);sync_vector_self();','tl_vector_fill::fill_value<int32_t>(valid_expert_num,0);')
    if any(x not in code for x in required) or 'topk_stage_2_filter_vkernel' not in filter_source or 'sync_inner_release_acquire(PE_VECTOR, PE_VECTOR' not in io_source or 'vec_write(output, 0)' not in fill_source:
        raise ValueError('R9/count source/callsite drift')
    return R9CountHandoffWork(ep_rank=ep_rank)

class R9CountObservedEvent(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    ep_rank:int=Field(ge=0,lt=4)
    vector_write_end_ns:float=Field(ge=0)
    acquire_start_ns:float=Field(ge=0)
    acquire_end_ns:float=Field(ge=0)
    api_begin_ns:float=Field(ge=0)
    api_return_ns:float=Field(ge=0)
    consumer_start_ns:float=Field(ge=0)
    resource:Literal['PE_VECTOR->PE_VECTOR']='PE_VECTOR->PE_VECTOR'
    @model_validator(mode='after')
    def exact(self):
        vs=(self.vector_write_end_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.api_return_ns,self.consumer_start_ns)
        if not all(math.isfinite(x) for x in vs) or self.api_return_ns<self.api_begin_ns or self.acquire_end_ns<max(self.vector_write_end_ns,self.acquire_start_ns) or self.consumer_start_ns<max(self.acquire_end_ns,self.api_return_ns):raise ValueError('R9/count event identity/order mismatch')
        return self
    def terminal_ns(self):return max(self.acquire_end_ns,self.api_return_ns)
    def tail_ns(self):return self.terminal_ns()-self.vector_write_end_ns

def validate_r9_count_events(events,ep_rank):
    rows=tuple(R9CountObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(rows)!=32 or {x.pe for x in rows}!=set(PARTICIPANTS) or any(x.ep_rank!=ep_rank for x in rows):raise ValueError('R9/count duplicate/missing/cross-rank events')
    return tuple(sorted(rows,key=lambda x:x.pe))

class R9CountPeTiming(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    local_tail_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        if not math.isfinite(self.local_tail_ns):raise ValueError('R9/count nonfinite local tail')
        return self

class R9CountHandoffFormula(FrozenRecord):
    revision:Literal['M07-fused-R9-count-local-handoff-r1']='M07-fused-R9-count-local-handoff-r1'
    source_unit:Literal['reported_ns']='reported_ns'
    ep_rank:int=Field(ge=0,lt=4)
    pe_timings:tuple[R9CountPeTiming,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_timings)!=PARTICIPANTS or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:
            raise ValueError('R9/count incomplete local formula')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('R9/count formula hash drift')
        return self
    def parameter_paths(self):return tuple(f'sync.r9_count.rank{self.ep_rank}.pe{x.pe}.vector_self.local_tail_reported_ns' for x in self.pe_timings)
    def duration(self,pe,changes=None):
        changes=changes or {}
        if type(pe) is not int or pe not in PARTICIPANTS or not set(changes)<=set(self.parameter_paths()) or len(changes)>1:raise ValueError('R9/count exact local parameter required')
        value=changes.get(self.parameter_paths()[pe],self.pe_timings[pe].local_tail_ns)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('R9/count invalid local parameter')
        return float(value)
    def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in PARTICIPANTS)

class R9CountHandoffDomain(FrozenRecord):
    work:R9CountHandoffWork
    formula:R9CountHandoffFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):
            raise ValueError('R9/count timebase/work mismatch')
        if self.formula.ep_rank!=self.work.ep_rank:raise ValueError('R9/count formula/rank mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('R9/count domain hash drift')
        return self

class R9CountHandoffBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str
    source_refs:tuple[str,...]
    binding_sha256:str
    @model_validator(mode='after')
    def exact(self):
        units={x['unit_id'] for x in self.binding.graph['execution_units']}
        if set(self.resources)!={f'PE{pe}.local_vector_ready' for pe in PARTICIPANTS} or len(set(self.resources.values()))!=32 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('R9/count supplied local resources incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('R9/count binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_r9_count_formula(**kwargs):return _freeze(R9CountHandoffFormula,'formula_sha256',dict(revision='M07-fused-R9-count-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_r9_count_domain(**kwargs):return _freeze(R9CountHandoffDomain,'domain_sha256',kwargs)
def freeze_r9_count_binding(**kwargs):return _freeze(R9CountHandoffBinding,'binding_sha256',kwargs)

def resolve_r9_count_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=R9CountHandoffWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        d=R9CountHandoffDomain.model_validate_json(raw.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError(f'R9/count requires one exact local domain, found {len(matches)}')
    return matches[0]

def r9_count_handoff_actions(domain,binding,*,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('R9/count binding/domain mismatch')
    actions=[ComponentAction(action_id=f'R9_COUNT.handoff.pe{pe}',stage='R9_COUNT.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.local_vector_ready'],work=256,work_unit='filtered-topk-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=(f'R9.rank{domain.work.ep_rank}.pe{pe}.filter_write.complete',),participants=(f'PE{pe}',),status='calibrated') for pe in PARTICIPANTS]
    deps=tuple(x.action_id for x in actions)
    actions.append(ComponentAction(action_id='R9_COUNT.handoff.complete',stage='R9_COUNT.handoff',component='M07',resource_id=binding.resources['PE0.local_vector_ready'],work=32,work_unit='readiness-after-all',duration_cycles=0,duration_source='zero-cost max of 32 already-owned local tails',calibration_sha256=domain.domain_sha256,dependencies=deps,participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
    return tuple(actions)

def execute_r9_count_handoff(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=R9CountHandoffDomain.model_validate_json(domain.model_dump_json());binding=R9CountHandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production R9/count requires supplied binding')
    if any(x.stage=='R9_COUNT.handoff' or x.action_id.startswith('R9_COUNT.handoff') for x in extra_actions):raise ValueError('R9/count duplicate timing owner')
    result=execute_actions(r9_count_handoff_actions(domain,binding,changes=changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    result.receipt.update(timing_owner=OWNER,r9_count_domain_sha256=domain.domain_sha256,profile='transformer-fused',V=64,pe_count=32,owned_pes=list(PARTICIPANTS),ep_rank=domain.work.ep_rank,parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['R8.softmax_topk','R9.filter_Vector','R9.valid_count_fill','R10','Matrix','DataNoC','GM'],physical_route_resolved=False,physical_publishable=False,model_cycle_mapping=False,simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)))
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'R9_COUNT.handoff.complete')
    return result
def append_r9_count_timebase(base,domain):
    if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate R9/count timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-R9-count-local-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
