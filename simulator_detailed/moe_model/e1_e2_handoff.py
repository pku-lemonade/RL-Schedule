"""Source-exact E1 root materialization to E2 DOWNLOAD1/Vector readiness."""
from typing import Literal
import json
import math
from pydantic import Field, TypeAdapter, model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction, execute_actions
from .timebase import TimebaseDomain, TimebaseBundle, convert_time_value, freeze_timebase_bundle
from .workload import FrozenRecord, stable_sha256

ROOTS = (28, 29, 30, 31)
OWNER = 'E1.column_reduce_to_E2.silu_score_quant.local_handoff_tail'
BOUNDARY = 'per-root last C03 capacity19 selector1 materialization -> DOWNLOAD1-to-VECTOR acquire/API completion'
PREDECESSOR = 'm03-c03-transformer-fused-E1-controlled_overlap-B131072'
CALLSITE = 'moe_gemm.hpp:188-206;colwise_pe_allreduce.hpp:17-30;utils.hpp:11-15;silu.hpp:75-88'

class E1E2HandoffWork(FrozenRecord):
    profile: Literal['transformer-fused'] = 'transformer-fused'
    producer_stage: Literal['E1'] = 'E1'
    consumer_stage: Literal['E2'] = 'E2'
    operation: Literal['column_reduce_vector_readiness'] = 'column_reduce_vector_readiness'
    V: Literal[64] = 64
    pe_count: Literal[32] = 32
    roots: tuple[int, ...] = ROOTS
    column_count: Literal[4] = 4
    contributors_per_root: Literal[8] = 8
    gate_up_elements: Literal[512] = 512
    payload_bytes_per_root: Literal[131072] = 131072
    root_fragment_count: Literal[2048] = 2048
    channel: Literal[1] = 1
    scenario: Literal['controlled_overlap'] = 'controlled_overlap'
    rounds: Literal[1] = 1
    input_buffer: Literal['gate_up_res.inplace'] = 'gate_up_res.inplace'
    first_consumer: Literal['silu_score_quant<512,256>.VECTOR_READ'] = 'silu_score_quant<512,256>.VECTOR_READ'
    local_resource: Literal['PE_DOWNLOAD_1->PE_VECTOR'] = 'PE_DOWNLOAD_1->PE_VECTOR'
    predecessor_entry_id: Literal[PREDECESSOR] = PREDECESSOR
    boundary: Literal[BOUNDARY] = BOUNDARY
    timing_ownership: Literal[OWNER] = OWNER
    source_commit: Literal['094bcd25e985a4c836fd5521cccd61b14d911aac'] = '094bcd25e985a4c836fd5521cccd61b14d911aac'
    source_callsite: Literal[CALLSITE] = CALLSITE

    @model_validator(mode='after')
    def exact(self):
        if self.roots != ROOTS or self.payload_bytes_per_root != self.V*self.gate_up_elements*4:
            raise ValueError('E1/E2 source work mismatch')
        return self

class E1E2ObservedEvent(FrozenRecord):
    pe: int
    root_materialized_ns: float
    release_ns: float
    acquire_start_ns: float
    acquire_end_ns: float
    api_begin_ns: float
    api_return_ns: float
    vector_read_start_ns: float
    resource: Literal['PE_DOWNLOAD_1->PE_VECTOR'] = 'PE_DOWNLOAD_1->PE_VECTOR'
    fragments: Literal[2048] = 2048
    channel: Literal[1] = 1

    @model_validator(mode='after')
    def exact(self):
        values = (self.root_materialized_ns,self.release_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.api_return_ns,self.vector_read_start_ns)
        if self.pe not in ROOTS or not all(math.isfinite(x) and x >= 0 for x in values):
            raise ValueError('E1/E2 root/event identity')
        if self.acquire_end_ns < max(self.acquire_start_ns,self.root_materialized_ns) or self.api_return_ns < self.api_begin_ns:
            raise ValueError('E1/E2 event order')
        if self.vector_read_start_ns < self.terminal_ns():
            raise ValueError('E1/E2 Vector starts before readiness')
        return self

    def terminal_ns(self):
        return max(self.acquire_end_ns,self.api_return_ns)

    def tail_ns(self):
        return self.terminal_ns()-self.root_materialized_ns

def validate_e1_e2_events(events):
    events = tuple(E1E2ObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(events)!=4 or {x.pe for x in events}!=set(ROOTS):
        raise ValueError('E1/E2 duplicate/missing events')
    return tuple(sorted(events,key=lambda x:x.pe))

class E1E2PeTiming(FrozenRecord):
    pe: int
    local_tail_ns: float = Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        if self.pe not in ROOTS or not math.isfinite(self.local_tail_ns):
            raise ValueError('E1/E2 local timing identity')
        return self

class E1E2HandoffFormula(FrozenRecord):
    revision: Literal['M07-fused-E1-E2-local-handoff-r1'] = 'M07-fused-E1-E2-local-handoff-r1'
    source_unit: Literal['reported_ns'] = 'reported_ns'
    pe_timings: tuple[E1E2PeTiming,...]
    calibration_scene_ids: tuple[str,...]
    applicability: str
    formula_sha256: str
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_timings)!=ROOTS or len(set(self.calibration_scene_ids))<2 or not self.applicability:
            raise ValueError('E1/E2 incomplete formula')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:
            raise ValueError('E1/E2 formula hash drift')
        return self
    def parameter_paths(self):
        return tuple(f'sync.e1_e2.pe{x.pe}.download1_to_vector.local_tail_reported_ns' for x in self.pe_timings)
    def duration(self,pe,changes=None):
        changes=changes or {}
        if pe not in ROOTS or not set(changes)<=set(self.parameter_paths()) or len(changes)>1:
            raise ValueError('E1/E2 exact local parameter required')
        row=self.pe_timings[pe-28];value=changes.get(self.parameter_paths()[pe-28],row.local_tail_ns)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError('E1/E2 invalid parameter')
        return float(value)
    def terminal(self,changes=None):
        return max(self.duration(pe,changes) for pe in ROOTS)

class E1E2HandoffDomain(FrozenRecord):
    work: E1E2HandoffWork
    formula: E1E2HandoffFormula
    timebase: TimebaseDomain
    observer_sha256: str
    counter_callsite: int
    predecessor_entry_sha256: str
    source_refs: tuple[str,...]
    domain_sha256: str
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):
            raise ValueError('E1/E2 timebase mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:
            raise ValueError('E1/E2 domain hash drift')
        return self

class E1E2HandoffBinding(FrozenRecord):
    binding: SuppliedBinding
    resources: dict[str,str]
    domain_sha256: str
    timing_owner: Literal[OWNER] = OWNER
    source_refs: tuple[str,...]
    binding_sha256: str
    @model_validator(mode='after')
    def exact(self):
        units={x['unit_id'] for x in self.binding.graph['execution_units']}
        if set(self.resources)!={f'PE{pe}.download1_to_vector' for pe in ROOTS} or len(set(self.resources.values()))!=4 or not set(self.resources.values())<=units:
            raise ValueError('E1/E2 binding resource identity')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:
            raise ValueError('E1/E2 binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_e1_e2_formula(**kwargs):
    return _freeze(E1E2HandoffFormula,'formula_sha256',dict(revision='M07-fused-E1-E2-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_e1_e2_domain(**kwargs):
    return _freeze(E1E2HandoffDomain,'domain_sha256',kwargs)
def freeze_e1_e2_binding(**kwargs):
    return _freeze(E1E2HandoffBinding,'binding_sha256',dict(timing_owner=OWNER,**kwargs))
def resolve_e1_e2_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=E1E2HandoffWork.model_validate_json(work.model_dump_json())
    matches=[d for d in domains if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite)]
    if len(matches)!=1:raise ValueError(f'E1/E2 requires one exact domain, found {len(matches)}')
    return matches[0]
def e1_e2_handoff_actions(domain,binding,*,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('E1/E2 domain/binding mismatch')
    actions=[ComponentAction(action_id=f'E1_E2.handoff.pe{pe}',stage='E1_E2.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.download1_to_vector'],work=131072,work_unit='root-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=(f'E1.root{pe}.materialized',),participants=(f'PE{pe}',),status='calibrated') for pe in ROOTS]
    actions.append(ComponentAction(action_id='E1_E2.handoff.complete',stage='E1_E2.handoff',component='M07',resource_id=binding.resources['PE28.download1_to_vector'],work=4,work_unit='root-after-all',duration_cycles=0,duration_source='zero-cost max of already owned tails',calibration_sha256=domain.domain_sha256,dependencies=tuple(x.action_id for x in actions),participants=tuple(f'PE{pe}' for pe in ROOTS),status='calibrated'))
    return tuple(actions)
def execute_e1_e2_handoff(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=E1E2HandoffDomain.model_validate_json(domain.model_dump_json());binding=E1E2HandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production E1/E2 requires supplied binding')
    if any(x.stage=='E1_E2.handoff' or x.action_id.startswith('E1_E2.handoff') for x in extra_actions):raise ValueError('E1/E2 duplicate timing ownership')
    actions=e1_e2_handoff_actions(domain,binding,changes=changes)
    result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    result.receipt.update(timing_owner=OWNER,e1_e2_domain_sha256=domain.domain_sha256,predecessor_entry_id=PREDECESSOR,roots=list(ROOTS),pe_count=32,profile='transformer-fused',V=64,payload_bytes_per_root=131072,parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['E1.chain','E2.Vector','Matrix','DataNoC','GM','E3.multicast'],simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,model_cycle_mapping=False)
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'E1_E2.handoff.complete')
    return result
def append_e1_e2_timebase(base,domain):
    if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate E1/E2 timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-E1-E2-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
