"""Source-exact valid-count WRITE to token-ID fill readiness through Vector self-acquire and scalar calc fence."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,TimebaseBundle,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

PARTICIPANTS=tuple(range(32))
OWNER='R9.valid_count_init_to_token_ids.local_self_acquire_fence_tail'
BOUNDARY='per PE valid_count VECTOR_WRITE end -> matching self acquire and scalar fence_calc completion before token-ID fill'
CALLSITE='router_gemm.hpp:465-489;ada_io.hpp:533-539,621-624;vector_fill.hpp:17-45'

class CountTokenHandoffWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    producer_stage:Literal['R9.valid_count_init']='R9.valid_count_init'
    consumer_stage:Literal['R9.token_ids_init']='R9.token_ids_init'
    operation:Literal['valid_count_token_ids_readiness']='valid_count_token_ids_readiness'
    V:Literal[64]=64
    ep_rank:Literal[0]=0
    pe_count:Literal[32]=32
    owned_pes:tuple[int,...]=PARTICIPANTS
    payload_bytes:Literal[128]=128
    input_elements:Literal[32]=32
    input_precision:Literal['int32']='int32'
    input_value:Literal[0]=0
    token_ids_elements:Literal[32]=32
    token_ids_bytes:Literal[128]=128
    expert_per_rank:Literal[96]=96
    valid_slots:Literal[8]=8
    buffer:Literal['moe.valid_expert_num->moe.topk_token_ids']='moe.valid_expert_num->moe.topk_token_ids'
    local_resource:Literal['PE_VECTOR->PE_VECTOR;PE_SCALAR.fence_calc']='PE_VECTOR->PE_VECTOR;PE_SCALAR.fence_calc'
    first_consumer:Literal['tl_vector_fill::fill_value<int32_t>(topk_token_ids,pe_id)']='tl_vector_fill::fill_value<int32_t>(topk_token_ids,pe_id)'
    terminal_kind:Literal['local_vector_self_acquire_and_fence_calc']='local_vector_self_acquire_and_fence_calc'
    source_callsite:Literal[CALLSITE]=CALLSITE
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    timing_ownership:Literal[OWNER]=OWNER
    boundary:Literal[BOUNDARY]=BOUNDARY
    @model_validator(mode='after')
    def exact(self):
        if self.owned_pes!=PARTICIPANTS:raise ValueError('count/token participants mismatch')
        return self

def extract_count_token_source(source,io_source,fill_source):
    compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
    required=('tl_vector_fill::fill_value<int32_t>(valid_expert_num,0);sync_vector_self();ada_sync_fence_calc();',
      'tl_vector_fill::fill_value<int32_t>(topk_token_ids,pe_id);','if(expert_id==expert_num_per_ep_rank){break;}')
    if any(x not in compact(source) for x in required) or 'sync_inner_release_acquire(PE_VECTOR, PE_VECTOR' not in io_source or 'vec_write(output, 0)' not in fill_source:
        raise ValueError('count/token source/callsite drift')
    return CountTokenHandoffWork()

class CountTokenObservedEvent(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    ep_rank:Literal[0]=0
    vector_write_end_ns:float=Field(ge=0)
    acquire_start_ns:float=Field(ge=0)
    acquire_end_ns:float=Field(ge=0)
    api_begin_ns:float=Field(ge=0)
    sync_api_return_ns:float=Field(ge=0)
    fence_begin_ns:float=Field(ge=0)
    fence_end_ns:float=Field(ge=0)
    consumer_start_ns:float=Field(ge=0)
    resource:Literal['PE_VECTOR->PE_VECTOR;PE_SCALAR.fence_calc']='PE_VECTOR->PE_VECTOR;PE_SCALAR.fence_calc'
    @model_validator(mode='after')
    def exact(self):
        values=(self.vector_write_end_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.sync_api_return_ns,self.fence_begin_ns,self.fence_end_ns,self.consumer_start_ns)
        if not all(math.isfinite(x) for x in values) or self.sync_api_return_ns<self.api_begin_ns or self.fence_begin_ns<self.sync_api_return_ns or self.acquire_end_ns<max(self.vector_write_end_ns,self.acquire_start_ns) or self.fence_end_ns<max(self.fence_begin_ns,self.acquire_end_ns) or self.consumer_start_ns<self.fence_end_ns:
            raise ValueError('count/token event identity/order mismatch')
        return self
    def terminal_ns(self):return self.fence_end_ns
    def tail_ns(self):return self.fence_end_ns-self.vector_write_end_ns

def validate_count_token_events(events,ep_rank=0):
    rows=tuple(CountTokenObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
    if ep_rank!=0 or len(rows)!=32 or {x.pe for x in rows}!=set(PARTICIPANTS):raise ValueError('count/token duplicate/missing/rank events')
    return tuple(sorted(rows,key=lambda x:x.pe))

class CountTokenPeTiming(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    local_tail_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        if not math.isfinite(self.local_tail_ns):raise ValueError('count/token nonfinite local tail')
        return self

class CountTokenHandoffFormula(FrozenRecord):
    revision:Literal['M07-fused-count-token-local-handoff-r1']='M07-fused-count-token-local-handoff-r1'
    source_unit:Literal['reported_ns']='reported_ns'
    ep_rank:int=Field(ge=0,lt=4)
    pe_timings:tuple[CountTokenPeTiming,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_timings)!=PARTICIPANTS or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:
            raise ValueError('count/token incomplete local formula')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('count/token formula hash drift')
        return self
    def parameter_paths(self):return tuple(f'sync.count_token.rank{self.ep_rank}.pe{x.pe}.vector_self_fence.local_tail_reported_ns' for x in self.pe_timings)
    def duration(self,pe,changes=None):
        changes=changes or {}
        if type(pe) is not int or pe not in PARTICIPANTS or not set(changes)<=set(self.parameter_paths()) or len(changes)>1:raise ValueError('count/token exact local parameter required')
        value=changes.get(self.parameter_paths()[pe],self.pe_timings[pe].local_tail_ns)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('count/token invalid local parameter')
        return float(value)
    def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in PARTICIPANTS)

class CountTokenHandoffDomain(FrozenRecord):
    work:CountTokenHandoffWork
    formula:CountTokenHandoffFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):
            raise ValueError('count/token timebase/work mismatch')
        if self.formula.ep_rank!=self.work.ep_rank:raise ValueError('count/token formula/rank mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('count/token domain hash drift')
        return self

class CountTokenHandoffBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str
    source_refs:tuple[str,...]
    binding_sha256:str
    @model_validator(mode='after')
    def exact(self):
        units={x['unit_id'] for x in self.binding.graph['execution_units']}
        if set(self.resources)!={f'PE{pe}.local_vector_ready' for pe in PARTICIPANTS} or len(set(self.resources.values()))!=32 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('count/token supplied local resources incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('count/token binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_count_token_formula(**kwargs):return _freeze(CountTokenHandoffFormula,'formula_sha256',dict(revision='M07-fused-count-token-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_count_token_domain(**kwargs):return _freeze(CountTokenHandoffDomain,'domain_sha256',kwargs)
def freeze_count_token_binding(**kwargs):return _freeze(CountTokenHandoffBinding,'binding_sha256',kwargs)

def resolve_count_token_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=CountTokenHandoffWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        d=CountTokenHandoffDomain.model_validate_json(raw.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError(f'count/token requires one exact local domain, found {len(matches)}')
    return matches[0]

def count_token_handoff_actions(domain,binding,*,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('count/token binding/domain mismatch')
    actions=[ComponentAction(action_id=f'COUNT_TOKEN.handoff.pe{pe}',stage='COUNT_TOKEN.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.local_vector_ready'],work=128,work_unit='count-buffer-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=(f'R9.rank0.pe{pe}.count_fill.write.complete',),participants=(f'PE{pe}',),status='calibrated') for pe in PARTICIPANTS]
    deps=tuple(x.action_id for x in actions)
    actions.append(ComponentAction(action_id='COUNT_TOKEN.handoff.complete',stage='COUNT_TOKEN.handoff',component='M07',resource_id=binding.resources['PE0.local_vector_ready'],work=32,work_unit='readiness-after-all',duration_cycles=0,duration_source='zero-cost max of 32 already-owned local tails',calibration_sha256=domain.domain_sha256,dependencies=deps,participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
    return tuple(actions)

def execute_count_token_handoff(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=CountTokenHandoffDomain.model_validate_json(domain.model_dump_json());binding=CountTokenHandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production count/token requires supplied binding')
    if any(x.stage=='COUNT_TOKEN.handoff' or x.action_id.startswith('COUNT_TOKEN.handoff') for x in extra_actions):raise ValueError('count/token duplicate timing owner')
    result=execute_actions(count_token_handoff_actions(domain,binding,changes=changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    result.receipt.update(timing_owner=OWNER,count_token_domain_sha256=domain.domain_sha256,profile='transformer-fused',V=64,pe_count=32,owned_pes=list(PARTICIPANTS),ep_rank=domain.work.ep_rank,parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['R9.valid_count_fill','R9.token_ids_fill','R9.scalar_count_scan','R10','R11','Matrix','DataNoC','GM','R9.filter_to_count_handoff'],physical_route_resolved=False,physical_publishable=False,model_cycle_mapping=False,simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)))
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'COUNT_TOKEN.handoff.complete')
    return result
def append_count_token_timebase(base,domain):
    if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate count/token timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-count-token-local-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
