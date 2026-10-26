"""Source-exact router reduce/broadcast completion to local softmax readiness."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,TimebaseBundle,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

ROOTS=(28,29,30,31)
NONROOTS=tuple(range(28))
PARTICIPANTS=tuple(range(32))
OWNER='R7.router_reduce_broadcast_to_R8.softmax.local_handoff_tail'
BOUNDARY='per PE R7 broadcast DOWNLOAD1 or root UPLOAD1 completion -> corresponding local VECTOR acquire/API completion'
CALLSITE='router_gemm.hpp:430-454;colwise_pe_allreduce.hpp:7-72;utils.hpp:11-21'

class R7R8HandoffWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    producer_stage:Literal['R7']='R7'
    consumer_stage:Literal['R8']='R8'
    operation:Literal['router_broadcast_softmax_readiness']='router_broadcast_softmax_readiness'
    V:Literal[64]=64
    pe_count:Literal[32]=32
    roots:tuple[int,...]=ROOTS
    owned_pes:tuple[int,...]=PARTICIPANTS
    payload_bytes:Literal[12288]=12288
    endpoint_fragments:Literal[192]=192
    channel:Literal[1]=1
    shape:tuple[int,int]=(8,384)
    softmax_elements_per_pe:Literal[384]=384
    input_precision:Literal['FP32']='FP32'
    root_masks:tuple[int,...]=tuple(0x1111111<<c for c in range(4))
    buffer:Literal['moe.router_res.inplace']='moe.router_res.inplace'
    local_resource:Literal['PE_UPLOAD_1_or_DOWNLOAD_1->PE_VECTOR']='PE_UPLOAD_1_or_DOWNLOAD_1->PE_VECTOR'
    first_consumer:Literal['softmax_func<384>(router_res(pe/4,_),same)']='softmax_func<384>(router_res(pe/4,_),same)'
    terminal_kind:Literal['local_vector_acquire']='local_vector_acquire'
    source_callsite:Literal[CALLSITE]=CALLSITE
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    timing_ownership:Literal[OWNER]=OWNER
    boundary:Literal[BOUNDARY]=BOUNDARY
    predecessor_entry_id:Literal['m03-c03-transformer-fused-R7-controlled_overlap-B12288']='m03-c03-transformer-fused-R7-controlled_overlap-B12288'
    @model_validator(mode='after')
    def exact(self):
        if self.roots!=ROOTS or self.owned_pes!=PARTICIPANTS or self.shape!=(8,384) or self.root_masks!=tuple(0x1111111<<c for c in range(4)) or self.payload_bytes!=8*384*4:
            raise ValueError('R7/R8 exact source work/root/mask mismatch')
        return self

def extract_r7_r8_source(source,colwise):
    compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
    code=compact(source);helper=compact(colwise)
    required=('pe_colwise_reduce<0,1>(router_res,router_res);','pe_colwise_broadcast<1>(router_res);',
              'if(pe_id>27){sync_upload_to_vector();}else{sync_download_to_vector();}',
              'Tensorsoftmax_res=router_res(pe_id/COL_NUM,_);','softmax_func<EXPERT_NUM>(softmax_res,softmax_res);')
    if any(x not in code for x in required) or 'native_receive<channel>(view,NodeType::PE,dst_node_id)' not in helper:
        raise ValueError('R7/R8 source/callsite drift')
    return R7R8HandoffWork()

class R7R8ObservedEvent(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    root:int=Field(ge=28,lt=32)
    endpoint_visible_ns:float=Field(ge=0)
    release_ns:float=Field(ge=0)
    acquire_start_ns:float=Field(ge=0)
    acquire_end_ns:float=Field(ge=0)
    api_begin_ns:float=Field(ge=0)
    api_return_ns:float=Field(ge=0)
    fragments:Literal[192]=192
    channel:Literal[1]=1
    resource:Literal['PE_DOWNLOAD_1->PE_VECTOR','PE_UPLOAD_1->PE_VECTOR']
    @model_validator(mode='after')
    def exact(self):
        values=(self.endpoint_visible_ns,self.release_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.api_return_ns)
        if self.root!=28+self.pe%4 or self.resource!=('PE_UPLOAD_1->PE_VECTOR' if self.pe in ROOTS else 'PE_DOWNLOAD_1->PE_VECTOR') or not all(math.isfinite(x) for x in values) or self.api_return_ns<self.api_begin_ns or self.acquire_end_ns<max(self.endpoint_visible_ns,self.acquire_start_ns) or self.release_ns<self.endpoint_visible_ns:
            raise ValueError('R7/R8 event identity/order mismatch')
        return self
    def terminal_ns(self):return max(self.acquire_end_ns,self.api_return_ns)
    def tail_ns(self):return self.terminal_ns()-self.endpoint_visible_ns

def validate_r7_r8_events(events):
    rows=tuple(R7R8ObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(rows)!=32 or {x.pe for x in rows}!=set(PARTICIPANTS):raise ValueError('R7/R8 duplicate/missing endpoint events')
    return tuple(sorted(rows,key=lambda x:x.pe))

class R7R8PeTiming(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    local_tail_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        if not math.isfinite(self.local_tail_ns):raise ValueError('R7/R8 nonfinite local tail')
        return self

class R7R8HandoffFormula(FrozenRecord):
    revision:Literal['M07-fused-R7-R8-local-handoff-r1']='M07-fused-R7-R8-local-handoff-r1'
    source_unit:Literal['reported_ns']='reported_ns'
    pe_timings:tuple[R7R8PeTiming,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_timings)!=PARTICIPANTS or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:
            raise ValueError('R7/R8 incomplete local formula')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('R7/R8 formula hash drift')
        return self
    def parameter_paths(self):return tuple(f'sync.r7_r8.pe{x.pe}.upload_or_download1_to_vector.local_tail_reported_ns' for x in self.pe_timings)
    def duration(self,pe,changes=None):
        changes=changes or {}
        if type(pe) is not int or pe not in PARTICIPANTS or not set(changes)<=set(self.parameter_paths()) or len(changes)>1:raise ValueError('R7/R8 exact local parameter required')
        value=changes.get(self.parameter_paths()[pe],self.pe_timings[pe].local_tail_ns)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('R7/R8 invalid local parameter')
        return float(value)
    def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in PARTICIPANTS)

class R7R8HandoffDomain(FrozenRecord):
    work:R7R8HandoffWork
    formula:R7R8HandoffFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):
            raise ValueError('R7/R8 timebase/work mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('R7/R8 domain hash drift')
        return self

class R7R8HandoffBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str
    source_refs:tuple[str,...]
    binding_sha256:str
    @model_validator(mode='after')
    def exact(self):
        units={x['unit_id'] for x in self.binding.graph['execution_units']}
        if set(self.resources)!={f'PE{pe}.local_vector_ready' for pe in PARTICIPANTS} or len(set(self.resources.values()))!=32 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('R7/R8 supplied local resources incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('R7/R8 binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_r7_r8_formula(**kwargs):return _freeze(R7R8HandoffFormula,'formula_sha256',dict(revision='M07-fused-R7-R8-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_r7_r8_domain(**kwargs):return _freeze(R7R8HandoffDomain,'domain_sha256',kwargs)
def freeze_r7_r8_binding(**kwargs):return _freeze(R7R8HandoffBinding,'binding_sha256',kwargs)

def resolve_r7_r8_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=R7R8HandoffWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        d=R7R8HandoffDomain.model_validate_json(raw.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError(f'R7/R8 requires one exact local domain, found {len(matches)}')
    return matches[0]

def r7_r8_handoff_actions(domain,binding,*,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('R7/R8 binding/domain mismatch')
    actions=[ComponentAction(action_id=f'R7_R8.handoff.pe{pe}',stage='R7_R8.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.local_vector_ready'],work=12288,work_unit='router-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=(f'R7.pe{pe}.broadcast.complete',),participants=(f'PE{pe}',),status='calibrated') for pe in PARTICIPANTS]
    deps=tuple(x.action_id for x in actions)
    actions.append(ComponentAction(action_id='R7_R8.handoff.complete',stage='R7_R8.handoff',component='M07',resource_id=binding.resources['PE0.local_vector_ready'],work=32,work_unit='readiness-after-all',duration_cycles=0,duration_source='zero-cost max of 32 already-owned local tails',calibration_sha256=domain.domain_sha256,dependencies=deps,participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
    return tuple(actions)

def execute_r7_r8_handoff(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=R7R8HandoffDomain.model_validate_json(domain.model_dump_json());binding=R7R8HandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production R7/R8 requires supplied binding')
    if any(x.stage=='R7_R8.handoff' or x.action_id.startswith('R7_R8.handoff') for x in extra_actions):raise ValueError('R7/R8 duplicate timing owner')
    result=execute_actions(r7_r8_handoff_actions(domain,binding,changes=changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    result.receipt.update(timing_owner=OWNER,r7_r8_domain_sha256=domain.domain_sha256,profile='transformer-fused',V=64,pe_count=32,owned_pes=list(PARTICIPANTS),predecessor_entry_id=domain.work.predecessor_entry_id,parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['R7.chain_reduce_broadcast','R8.Vector_softmax','R9.topk_filter','Matrix','DataNoC','GM'],physical_route_resolved=False,physical_publishable=False,model_cycle_mapping=False,simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)))
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'R7_R8.handoff.complete')
    return result
def append_r7_r8_timebase(base,domain):
    if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate R7/R8 timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-R7-R8-local-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
