"""E3 native endpoint visibility to E4 local Matrix readiness, simulation only."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,TimebaseBundle,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

ROOTS=(28,29,30,31)
NONROOTS=tuple(range(28))
OWNER='E3.endpoint_visible_to_E4.matrix_readiness.local_handoff_tail'
BOUNDARY='PE0..27 last named source-exact E3 DOWNLOAD1 ALL FINISH/PERF end -> DOWNLOAD1-to-MATRIX acquire/API completion'
CALLSITE='moe_gemm.hpp:213-244;colwise_pe_allreduce.hpp:47-72;utils.hpp:53-57'

class E3E4HandoffWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    producer_stage:Literal['E3']='E3'
    consumer_stage:Literal['E4']='E4'
    operation:Literal['activation_multicast_matrix_readiness']='activation_multicast_matrix_readiness'
    V:Literal[64]=64
    pe_count:Literal[32]=32
    roots:tuple[int,...]=ROOTS
    owned_pes:tuple[int,...]=NONROOTS
    payload_bytes:Literal[24576]=24576
    endpoint_fragments:Literal[384]=384
    channel:Literal[1]=1
    shape:tuple[int,int]=(64,384)
    quant_elements:Literal[256]=256
    scale_slot_bytes:Literal[128]=128
    root_masks:tuple[int,...]=tuple(0x1111111<<c for c in range(4))
    buffer:Literal['moe.down_activation.quant_scale.inplace']='moe.down_activation.quant_scale.inplace'
    local_resource:Literal['PE_DOWNLOAD_1->PE_MATRIX']='PE_DOWNLOAD_1->PE_MATRIX'
    first_consumer:Literal['compute_down_gemm.gathered_W4A8_M1_N256_K256']='compute_down_gemm.gathered_W4A8_M1_N256_K256'
    terminal_kind:Literal['local_matrix_acquire']='local_matrix_acquire'
    source_callsite:Literal[CALLSITE]=CALLSITE
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    timing_ownership:Literal[OWNER]=OWNER
    boundary:Literal[BOUNDARY]=BOUNDARY
    root_readiness_owner:Literal['m07-source-fused-E2-E3-local-handoff-V64']='m07-source-fused-E2-E3-local-handoff-V64'
    @model_validator(mode='after')
    def exact(self):
        if self.roots!=ROOTS or self.owned_pes!=NONROOTS or self.shape!=(64,384) or self.root_masks!=tuple(0x1111111<<c for c in range(4)) or self.payload_bytes!=self.V*(self.quant_elements+self.scale_slot_bytes):
            raise ValueError('E3/E4 exact source work/root/mask mismatch')
        return self

def extract_e3_e4_source(source,colwise):
    compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
    code=compact(source);helper=compact(colwise)
    required=('pe_colwise_broadcast<1>(down_activation_valid);','if(node_id()<=27){sync_download_to_matrix();}',
              'compute_down_gemm(valid_num,down_res,down_activation,down_activation_scale,down_weight,mlp1_weight_table);')
    if any(x not in code for x in required) or 'native_receive<channel>(view,NodeType::PE,dst_node_id)' not in helper or '0x1111111<<(node_id()-28)' not in helper:
        raise ValueError('E3/E4 source/callsite drift')
    return E3E4HandoffWork()

class E3E4ObservedEvent(FrozenRecord):
    pe:int=Field(ge=0,lt=28)
    root:int=Field(ge=28,lt=32)
    endpoint_visible_ns:float=Field(ge=0)
    release_ns:float=Field(ge=0)
    acquire_start_ns:float=Field(ge=0)
    acquire_end_ns:float=Field(ge=0)
    api_begin_ns:float=Field(ge=0)
    api_return_ns:float=Field(ge=0)
    fragments:Literal[384]=384
    channel:Literal[1]=1
    resource:Literal['PE_DOWNLOAD_1->PE_MATRIX']='PE_DOWNLOAD_1->PE_MATRIX'
    @model_validator(mode='after')
    def exact(self):
        values=(self.endpoint_visible_ns,self.release_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.api_return_ns)
        if self.root!=28+self.pe%4 or not all(math.isfinite(x) for x in values) or self.api_return_ns<self.api_begin_ns or self.acquire_end_ns<max(self.endpoint_visible_ns,self.acquire_start_ns) or self.release_ns<self.endpoint_visible_ns:
            raise ValueError('E3/E4 event identity/order mismatch')
        return self
    def terminal_ns(self):return max(self.acquire_end_ns,self.api_return_ns)
    def tail_ns(self):return self.terminal_ns()-self.endpoint_visible_ns

def validate_e3_e4_events(events):
    rows=tuple(E3E4ObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(rows)!=28 or {x.pe for x in rows}!=set(NONROOTS):raise ValueError('E3/E4 duplicate/missing endpoint events')
    return tuple(sorted(rows,key=lambda x:x.pe))

class E3E4PeTiming(FrozenRecord):
    pe:int=Field(ge=0,lt=28)
    local_tail_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        if not math.isfinite(self.local_tail_ns):raise ValueError('E3/E4 nonfinite local tail')
        return self

class E3E4HandoffFormula(FrozenRecord):
    revision:Literal['M07-fused-E3-E4-local-handoff-r2']='M07-fused-E3-E4-local-handoff-r2'
    source_unit:Literal['reported_ns']='reported_ns'
    pe_timings:tuple[E3E4PeTiming,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_timings)!=NONROOTS or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:
            raise ValueError('E3/E4 incomplete local formula')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('E3/E4 formula hash drift')
        return self
    def parameter_paths(self):return tuple(f'sync.e3_e4.pe{x.pe}.download1_to_matrix.local_tail_reported_ns' for x in self.pe_timings)
    def duration(self,pe,changes=None):
        changes=changes or {}
        if type(pe) is not int or pe not in NONROOTS or not set(changes)<=set(self.parameter_paths()) or len(changes)>1:raise ValueError('E3/E4 exact local parameter required')
        value=changes.get(self.parameter_paths()[pe],self.pe_timings[pe].local_tail_ns)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('E3/E4 invalid local parameter')
        return float(value)
    def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in NONROOTS)

class E3E4HandoffDomain(FrozenRecord):
    work:E3E4HandoffWork
    formula:E3E4HandoffFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):
            raise ValueError('E3/E4 timebase/work mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('E3/E4 domain hash drift')
        return self

class E3E4HandoffBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str
    source_refs:tuple[str,...]
    binding_sha256:str
    @model_validator(mode='after')
    def exact(self):
        units={x['unit_id'] for x in self.binding.graph['execution_units']}
        if set(self.resources)!={f'PE{pe}.download1_to_matrix' for pe in NONROOTS} or len(set(self.resources.values()))!=28 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('E3/E4 supplied local resources incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('E3/E4 binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_e3_e4_formula(**kwargs):return _freeze(E3E4HandoffFormula,'formula_sha256',dict(revision='M07-fused-E3-E4-local-handoff-r2',source_unit='reported_ns',**kwargs))
def freeze_e3_e4_domain(**kwargs):return _freeze(E3E4HandoffDomain,'domain_sha256',kwargs)
def freeze_e3_e4_binding(**kwargs):return _freeze(E3E4HandoffBinding,'binding_sha256',kwargs)

def resolve_e3_e4_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=E3E4HandoffWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        d=E3E4HandoffDomain.model_validate_json(raw.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError(f'E3/E4 requires one exact local domain, found {len(matches)}')
    return matches[0]

def e3_e4_handoff_actions(domain,binding,*,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('E3/E4 binding/domain mismatch')
    actions=[ComponentAction(action_id=f'E3_E4.handoff.pe{pe}',stage='E3_E4.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.download1_to_matrix'],work=24576,work_unit='endpoint-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=(f'E3.pe{pe}.visible',),participants=(f'PE{pe}',),status='calibrated') for pe in NONROOTS]
    deps=tuple(x.action_id for x in actions)+tuple(f'E2_E3.root{pe}.matrix.ready' for pe in ROOTS)
    actions.append(ComponentAction(action_id='E3_E4.handoff.complete',stage='E3_E4.handoff',component='M07',resource_id=binding.resources['PE0.download1_to_matrix'],work=32,work_unit='readiness-after-all',duration_cycles=0,duration_source='zero-cost max; roots owned by E2/E3',calibration_sha256=domain.domain_sha256,dependencies=deps,participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
    return tuple(actions)

def execute_e3_e4_handoff(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=E3E4HandoffDomain.model_validate_json(domain.model_dump_json());binding=E3E4HandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production E3/E4 requires supplied binding')
    if any(x.stage=='E3_E4.handoff' or x.action_id.startswith('E3_E4.handoff') for x in extra_actions):raise ValueError('E3/E4 duplicate timing owner')
    result=execute_actions(e3_e4_handoff_actions(domain,binding,changes=changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    result.receipt.update(timing_owner=OWNER,e3_e4_domain_sha256=domain.domain_sha256,profile='transformer-fused',V=64,pe_count=32,owned_pes=list(NONROOTS),root_readiness_owner=domain.work.root_readiness_owner,parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['E3.multicast','E4.Matrix','E4.WeightSRAM.gather','E2_E3.root.VectorToMatrix','DataNoC','GM'],physical_route_resolved=False,physical_publishable=False,model_cycle_mapping=False,simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)))
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'E3_E4.handoff.complete')
    return result
def append_e3_e4_timebase(base,domain):
    if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate E3/E4 timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-E3-E4-local-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
