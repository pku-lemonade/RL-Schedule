"""Exact fused E0 gate-up to E1 column-reduce local handoff."""
from typing import Literal
import json, math
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseBundle,TimebaseDomain,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

E0_E1_OWNER='E0.gate_up_to_E1.column_reduce.local_handoff_tail'
E0_E1_BOUNDARY='max PE last MATRIX_WRITE completion -> max PE MATRIX-to-UPLOAD1/DOWNLOAD1 acquire/API completion'
E0_E1_PREDECESSOR_ENTRY='m05-c05-transformer-fused-gate_up-V64'
def role_for_pe(pe):
 if type(pe) is not int or pe<0 or pe>=32:raise ValueError('invalid E0/E1 PE')
 return 'matrix_to_download1' if pe>27 else 'matrix_to_upload1'

class E0E1HandoffWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused';producer_stage:Literal['E0']='E0';consumer_stage:Literal['E1']='E1';operation:Literal['gate_up_gemm_column_reduce_handoff']='gate_up_gemm_column_reduce_handoff';V:Literal[64]=64;pe_count:Literal[32]=32;pe_rows:Literal[8]=8;pe_columns:Literal[4]=4;gate_up_shape_per_selection:tuple[Literal[1],Literal[512],Literal[256]]=(1,512,256);precision:Literal['W4A8-group256']='W4A8-group256';output_elements_per_pe:Literal[32768]=32768;output_bytes_per_pe:Literal[131072]=131072;channel:Literal[1]=1;upload_waiters:Literal[28]=28;download_waiters:Literal[4]=4;predecessor_entry_id:Literal[E0_E1_PREDECESSOR_ENTRY]=E0_E1_PREDECESSOR_ENTRY;boundary:Literal[E0_E1_BOUNDARY]=E0_E1_BOUNDARY;timing_ownership:Literal[E0_E1_OWNER]=E0_E1_OWNER;source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac';source_callsite:Literal['moe_gemm.hpp:160-192;utils.hpp:41-51']='moe_gemm.hpp:160-192;utils.hpp:41-51'
 @model_validator(mode='after')
 def exact(self):
  if self.upload_waiters+self.download_waiters!=32 or self.output_elements_per_pe*4!=self.output_bytes_per_pe:raise ValueError('E0/E1 source work mismatch')
  return self
class E0E1PeTiming(FrozenRecord):
 pe:int=Field(ge=0,lt=32);role:Literal['matrix_to_upload1','matrix_to_download1'];post_matrix_tail_ns:float=Field(ge=0)
 @model_validator(mode='after')
 def exact(self):
  if self.role!=role_for_pe(self.pe) or not math.isfinite(self.post_matrix_tail_ns):raise ValueError('E0/E1 PE timing mismatch')
  return self
class E0E1HandoffFormula(FrozenRecord):
 revision:Literal['M07-fused-E0-E1-local-handoff-r1']='M07-fused-E0-E1-local-handoff-r1';source_unit:Literal['reported_ns']='reported_ns';pe_timings:tuple[E0E1PeTiming,...];calibration_scene_ids:tuple[str,...];applicability:str;formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def complete(self):
  if tuple(x.pe for x in self.pe_timings)!=tuple(range(32)) or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:raise ValueError('E0/E1 formula incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('E0/E1 formula hash drift')
  return self
 def parameter_paths(self):return tuple(f'sync.e0_e1.pe{x.pe}.{x.role}.post_matrix_tail_reported_ns' for x in self.pe_timings)
 def duration(self,pe,changes=None):
  changes=changes or {};known=set(self.parameter_paths())
  if not set(changes)<=known or len(changes)>1:raise ValueError('E0/E1 accepts one exact local parameter')
  row=self.pe_timings[pe];path=f'sync.e0_e1.pe{pe}.{row.role}.post_matrix_tail_reported_ns';value=changes.get(path,row.post_matrix_tail_ns)
  if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('E0/E1 parameter must be finite and nonnegative')
  return float(value)
 def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in range(32))
class E0E1ObservedEvent(FrozenRecord):
 pe:int=Field(ge=0,lt=32);role:Literal['matrix_to_upload1','matrix_to_download1'];matrix_write_end_ns:float=Field(ge=0);release_issue_ns:float=Field(ge=0);acquire_start_ns:float=Field(ge=0);acquire_end_ns:float=Field(ge=0);api_begin_ns:float=Field(ge=0);api_end_ns:float=Field(ge=0)
 @model_validator(mode='after')
 def ordered(self):
  values=(self.matrix_write_end_ns,self.release_issue_ns,self.acquire_start_ns,self.acquire_end_ns,self.api_begin_ns,self.api_end_ns)
  if not all(math.isfinite(x) for x in values) or self.role!=role_for_pe(self.pe) or self.acquire_end_ns<self.acquire_start_ns or self.api_end_ns<self.api_begin_ns or self.release_issue_ns>self.acquire_end_ns:raise ValueError('E0/E1 observed identity/order invalid')
  return self
 def terminal_ns(self):return max(self.matrix_write_end_ns,self.acquire_end_ns,self.api_end_ns)
 def post_matrix_tail_ns(self):return self.terminal_ns()-self.matrix_write_end_ns
def validate_e0_e1_event_inventory(events):
 events=tuple(E0E1ObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
 if len(events)!=32 or {x.pe for x in events}!=set(range(32)) or len({x.pe for x in events})!=32:raise ValueError('E0/E1 duplicate/missing inventory')
 if sum(x.role=='matrix_to_upload1' for x in events)!=28 or sum(x.role=='matrix_to_download1' for x in events)!=4:raise ValueError('E0/E1 role inventory')
 return tuple(sorted(events,key=lambda x:x.pe))
class E0E1HandoffDomain(FrozenRecord):
 work:E0E1HandoffWork;formula:E0E1HandoffFormula;timebase:TimebaseDomain;observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');counter_callsite:int=Field(ge=0);source_refs:tuple[str,...];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def exact(self):
  t=self.timebase
  if t.component_ids!=('M07',) or t.operation!=self.work.operation or t.profile!=self.work.profile or t.boundary_id!=self.work.boundary or t.source_unit!=self.formula.source_unit or t.accepted_work_values!=(64,) or t.status!='validated_simulation' or not self.source_refs:raise ValueError('E0/E1 domain mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('E0/E1 domain hash drift')
  return self
class E0E1HandoffBinding(FrozenRecord):
 binding:SuppliedBinding;resources:dict[str,str];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');timing_owner:Literal[E0_E1_OWNER]=E0_E1_OWNER;source_refs:tuple[str,...];binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def exact(self):
  expected={f'PE{pe}.{role_for_pe(pe)}' for pe in range(32)};units={x['unit_id'] for x in self.binding.graph['execution_units']}
  if set(self.resources)!=expected or len(set(self.resources.values()))!=32 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('E0/E1 binding incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('E0/E1 binding hash drift')
  return self
def _freeze(cls,field,raw):raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_e0_e1_formula(**kwargs):return _freeze(E0E1HandoffFormula,'formula_sha256',dict(revision='M07-fused-E0-E1-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_e0_e1_domain(**kwargs):return _freeze(E0E1HandoffDomain,'domain_sha256',kwargs)
def freeze_e0_e1_binding(**kwargs):return _freeze(E0E1HandoffBinding,'binding_sha256',dict(timing_owner=E0_E1_OWNER,**kwargs))
def resolve_e0_e1_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 work=E0E1HandoffWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=E0E1HandoffDomain.model_validate_json(raw.model_dump_json());identity=(d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)
  if not physical and d.work==work and identity==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError(f'E0/E1 handoff requires one exact domain, found {len(matches)}')
 return matches[0]
def e0_e1_handoff_actions(domain,binding,*,before=None,changes=None):
 domain=E0E1HandoffDomain.model_validate_json(domain.model_dump_json());binding=E0E1HandoffBinding.model_validate_json(binding.model_dump_json())
 if binding.domain_sha256!=domain.domain_sha256:raise ValueError('E0/E1 binding/domain mismatch')
 if before is None:before={pe:(f'rank0.E0.pe{pe}.call{domain.work.V-1}.MATRIX_WRITE.end',) for pe in range(32)}
 if not isinstance(before,dict) or set(before)!=set(range(32)) or any(not isinstance(deps,tuple) or len(deps)!=1 or 'E0.gate_up.complete' in deps for deps in before.values()):raise ValueError('E0/E1 requires per-PE writer boundaries, not a global barrier')
 if len({deps[0] for deps in before.values()})!=32:raise ValueError('E0/E1 requires distinct per-PE writer boundaries')
 actions=[]
 for pe in range(32):
  role=role_for_pe(pe);actions.append(ComponentAction(action_id=f'E0_E1.handoff.pe{pe}.{role}',stage='E0_E1.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.{role}'],work=131072,work_unit='gate-up-result-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=E0_E1_OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=before[pe],participants=(f'PE{pe}.MATRIX',f"PE{pe}.{'DOWNLOAD1' if pe>27 else 'UPLOAD1'}"),status='calibrated'))
 actions.append(ComponentAction(action_id='E0_E1.handoff.complete',stage='E0_E1.handoff',component='M07',resource_id=binding.resources['PE0.matrix_to_upload1'],work=32,work_unit='after-all-local-handoffs',duration_cycles=0,duration_source='zero-cost max; local tails already owned',calibration_sha256=domain.domain_sha256,dependencies=tuple(x.action_id for x in actions),participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
 return tuple(actions)
def execute_e0_e1_handoff(domain,binding,hardware,*,before=None,changes=None,extra_actions=(),production=True,cancel=None):
 domain=E0E1HandoffDomain.model_validate_json(domain.model_dump_json());binding=E0E1HandoffBinding.model_validate_json(binding.model_dump_json())
 if production and binding.binding.fixture_only:raise ValueError('production E0/E1 handoff requires supplied binding')
 if any(x.stage=='E0_E1.handoff' or x.action_id.startswith('E0_E1.handoff') for x in extra_actions):raise ValueError('E0/E1 duplicate timing owner')
 actions=e0_e1_handoff_actions(domain,binding,before=before,changes=changes);result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 result.receipt.update(e0_e1_domain_sha256=domain.domain_sha256,e0_e1_binding_sha256=binding.binding_sha256,timing_owner=E0_E1_OWNER,predecessor_entry_id=E0_E1_PREDECESSOR_ENTRY,profile='transformer-fused',V=64,pe_count=32,output_bytes_per_pe=131072,channel=1,upload_waiters=28,download_waiters=4,parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=None,excluded_cost_owners=['E0.gate_up.Matrix','E1.column_chain','E2.Vector','DataNoC','GM'],physical_publishable=False,model_cycle_mapping=False)
 if result.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  spans={span.transaction_id:span for span in result.result.transactions}
  boundary_start=max(spans[action.dependencies[0]].end_cycles for action in actions[:-1])
  boundary_end=spans['E0_E1.handoff.complete'].end_cycles
  result.receipt['simulation_seconds']=convert_time_value(domain.timebase,boundary_end-boundary_start)
  result.receipt['simulation_boundary_reported_ns']=[boundary_start,boundary_end]
  result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'E0_E1.handoff.complete')
 return result
def append_e0_e1_timebase(base,domain):
 base=TimebaseBundle.model_validate_json(base.model_dump_json());domain=E0E1HandoffDomain.model_validate_json(domain.model_dump_json())
 if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate E0/E1 timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-E0-E1-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
