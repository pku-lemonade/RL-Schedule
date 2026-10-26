"""Exact fused E2 Vector completion to E3 local-readiness handoff."""
from typing import Literal
import json,math
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseBundle,TimebaseDomain,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256
OWNER='E2.silu_score_quant_to_E3.broadcast.local_handoff_tail';BOUNDARY='global max root last named Vector completion -> max 32-PE local sync/acquire/API completion';PREDECESSOR='m06-source-bulk-transformer-fused-silu_score_quant-V64';ROOTS=(28,29,30,31)
def role_for_pe(pe):
 if type(pe) is not int or pe<0 or pe>=32:raise ValueError('invalid E2/E3 PE')
 return 'vector_to_upload1_and_matrix' if pe in ROOTS else 'upload1_to_download1_ready'
class E2E3HandoffWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused';producer_stage:Literal['E2']='E2';consumer_stage:Literal['E3']='E3';operation:Literal['silu_quant_broadcast_local_handoff']='silu_quant_broadcast_local_handoff';V:Literal[64]=64;pe_count:Literal[32]=32;vector_participants:tuple[int,...]=ROOTS;gate_up_elements:Literal[512]=512;valid_output_elements:Literal[256]=256;scale_slot_bytes:Literal[128]=128;payload_bytes_per_root:Literal[24576]=24576;total_payload_bytes:Literal[98304]=98304;channel:Literal[1]=1;root_dual_sync_count:Literal[4]=4;nonroot_ready_sync_count:Literal[28]=28;predecessor_entry_id:Literal[PREDECESSOR]=PREDECESSOR;boundary:Literal[BOUNDARY]=BOUNDARY;timing_ownership:Literal[OWNER]=OWNER;source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac';source_callsite:Literal['moe_gemm.hpp:194-217;utils.hpp:23-33,59-63']='moe_gemm.hpp:194-217;utils.hpp:23-33,59-63'
 @model_validator(mode='after')
 def exact(self):
  if self.vector_participants!=ROOTS or self.payload_bytes_per_root!=self.V*(self.valid_output_elements+self.scale_slot_bytes) or self.total_payload_bytes!=4*self.payload_bytes_per_root or self.root_dual_sync_count+self.nonroot_ready_sync_count!=32:raise ValueError('E2/E3 source work mismatch')
  return self
class E2E3PeTiming(FrozenRecord):
 pe:int=Field(ge=0,lt=32);role:Literal['vector_to_upload1_and_matrix','upload1_to_download1_ready'];predecessor_skew_ns:float=Field(le=0);local_tail_ns:float=Field(ge=0)
 @model_validator(mode='after')
 def exact(self):
  if self.role!=role_for_pe(self.pe) or not all(math.isfinite(x) for x in (self.predecessor_skew_ns,self.local_tail_ns)) or (self.pe not in ROOTS and self.predecessor_skew_ns!=0):raise ValueError('E2/E3 PE timing mismatch')
  return self
 def effective(self,tail=None):return max(0.0,self.predecessor_skew_ns+(self.local_tail_ns if tail is None else tail))
class E2E3HandoffFormula(FrozenRecord):
 revision:Literal['M07-fused-E2-E3-local-handoff-r1']='M07-fused-E2-E3-local-handoff-r1';source_unit:Literal['reported_ns']='reported_ns';pe_timings:tuple[E2E3PeTiming,...];calibration_scene_ids:tuple[str,...];applicability:str;formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def complete(self):
  if tuple(x.pe for x in self.pe_timings)!=tuple(range(32)) or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.applicability:raise ValueError('E2/E3 formula incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('E2/E3 formula hash drift')
  return self
 def parameter_paths(self):return tuple(f'sync.e2_e3.pe{x.pe}.{x.role}.local_tail_reported_ns' for x in self.pe_timings)
 def duration(self,pe,changes=None):
  changes=changes or {};known=set(self.parameter_paths())
  if not set(changes)<=known or len(changes)>1:raise ValueError('E2/E3 accepts one exact local parameter')
  row=self.pe_timings[pe];path=f'sync.e2_e3.pe{pe}.{row.role}.local_tail_reported_ns';value=changes.get(path,row.local_tail_ns)
  if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('E2/E3 parameter invalid')
  return row.effective(float(value))
 def terminal(self,changes=None):return max(self.duration(pe,changes) for pe in range(32))
class E2E3ObservedEvent(FrozenRecord):
 pe:int=Field(ge=0,lt=32);role:Literal['vector_to_upload1_and_matrix','upload1_to_download1_ready'];global_vector_end_ns:float=Field(ge=0);own_vector_end_ns:float|None=None;release_count:int=Field(gt=0);acquire_count:int=Field(gt=0);last_acquire_end_ns:float=Field(ge=0);api_begin_ns:float=Field(ge=0);api_end_ns:float=Field(ge=0)
 @model_validator(mode='after')
 def exact(self):
  if self.role!=role_for_pe(self.pe) or not all(math.isfinite(x) for x in (self.global_vector_end_ns,self.last_acquire_end_ns,self.api_begin_ns,self.api_end_ns)) or self.api_end_ns<self.api_begin_ns:raise ValueError('E2/E3 observed identity/order')
  if self.pe in ROOTS:
   if self.own_vector_end_ns is None or self.release_count!=2 or self.acquire_count!=2 or self.own_vector_end_ns>self.global_vector_end_ns:raise ValueError('E2/E3 root event identity')
  elif self.own_vector_end_ns is not None or self.release_count!=1 or self.acquire_count!=1:raise ValueError('E2/E3 nonroot event identity')
  return self
 def local_terminal_ns(self):return max(self.last_acquire_end_ns,self.api_end_ns,self.own_vector_end_ns or 0)
 def predecessor_skew_ns(self):return 0.0 if self.own_vector_end_ns is None else self.own_vector_end_ns-self.global_vector_end_ns
 def local_tail_ns(self):return max(0.0,self.local_terminal_ns()-(self.own_vector_end_ns if self.own_vector_end_ns is not None else self.global_vector_end_ns))
 def effective_ns(self):return max(0.0,self.local_terminal_ns()-self.global_vector_end_ns)
def validate_e2_e3_event_inventory(events):
 events=tuple(E2E3ObservedEvent.model_validate_json(x.model_dump_json()) for x in events)
 if len(events)!=32 or {x.pe for x in events}!=set(range(32)) or len({x.pe for x in events})!=32:raise ValueError('E2/E3 duplicate/missing inventory')
 if sum(x.pe in ROOTS for x in events)!=4:raise ValueError('E2/E3 root inventory')
 return tuple(sorted(events,key=lambda x:x.pe))
class E2E3HandoffDomain(FrozenRecord):
 work:E2E3HandoffWork;formula:E2E3HandoffFormula;timebase:TimebaseDomain;observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');counter_callsite:int=Field(ge=0);source_refs:tuple[str,...];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def exact(self):
  t=self.timebase
  if t.component_ids!=('M07',) or t.operation!=self.work.operation or t.profile!=self.work.profile or t.boundary_id!=self.work.boundary or t.source_unit!=self.formula.source_unit or t.accepted_work_values!=(64,) or t.status!='validated_simulation' or not self.source_refs:raise ValueError('E2/E3 domain mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('E2/E3 domain hash drift')
  return self
class E2E3HandoffBinding(FrozenRecord):
 binding:SuppliedBinding;resources:dict[str,str];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');timing_owner:Literal[OWNER]=OWNER;source_refs:tuple[str,...];binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def exact(self):
  expected={f'PE{pe}.{role_for_pe(pe)}' for pe in range(32)};units={x['unit_id'] for x in self.binding.graph['execution_units']}
  if set(self.resources)!=expected or len(set(self.resources.values()))!=32 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('E2/E3 binding incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('E2/E3 binding hash drift')
  return self
def _freeze(cls,field,raw):raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_e2_e3_formula(**kwargs):return _freeze(E2E3HandoffFormula,'formula_sha256',dict(revision='M07-fused-E2-E3-local-handoff-r1',source_unit='reported_ns',**kwargs))
def freeze_e2_e3_domain(**kwargs):return _freeze(E2E3HandoffDomain,'domain_sha256',kwargs)
def freeze_e2_e3_binding(**kwargs):return _freeze(E2E3HandoffBinding,'binding_sha256',dict(timing_owner=OWNER,**kwargs))
def resolve_e2_e3_handoff(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 work=E2E3HandoffWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=E2E3HandoffDomain.model_validate_json(raw.model_dump_json());identity=(d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)
  if not physical and d.work==work and identity==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError(f'E2/E3 handoff requires one exact domain, found {len(matches)}')
 return matches[0]
def e2_e3_handoff_actions(domain,binding,*,before=('E2.vector.complete',),changes=None):
 domain=E2E3HandoffDomain.model_validate_json(domain.model_dump_json());binding=E2E3HandoffBinding.model_validate_json(binding.model_dump_json())
 if binding.domain_sha256!=domain.domain_sha256:raise ValueError('E2/E3 binding/domain mismatch')
 actions=[]
 for pe in range(32):
  role=role_for_pe(pe);actions.append(ComponentAction(action_id=f'E2_E3.handoff.pe{pe}.{role}',stage='E2_E3.handoff',component='M07',resource_id=binding.resources[f'PE{pe}.{role}'],work=24576 if pe in ROOTS else 0,work_unit='down-activation-byte-ready',duration_cycles=domain.formula.duration(pe,changes),duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=tuple(before),participants=(f'PE{pe}',),status='calibrated'))
 actions.append(ComponentAction(action_id='E2_E3.handoff.complete',stage='E2_E3.handoff',component='M07',resource_id=binding.resources['PE0.upload1_to_download1_ready'],work=32,work_unit='after-all-local-readiness',duration_cycles=0,duration_source='zero-cost max; local tails already owned',calibration_sha256=domain.domain_sha256,dependencies=tuple(x.action_id for x in actions),participants=tuple(f'PE{pe}' for pe in range(32)),status='calibrated'))
 return tuple(actions)
def execute_e2_e3_handoff(domain,binding,hardware,*,before=('E2.vector.complete',),changes=None,extra_actions=(),production=True,cancel=None):
 domain=E2E3HandoffDomain.model_validate_json(domain.model_dump_json());binding=E2E3HandoffBinding.model_validate_json(binding.model_dump_json())
 if production and binding.binding.fixture_only:raise ValueError('production E2/E3 handoff requires supplied binding')
 if any(x.stage=='E2_E3.handoff' or x.action_id.startswith('E2_E3.handoff') for x in extra_actions):raise ValueError('E2/E3 duplicate timing owner')
 actions=e2_e3_handoff_actions(domain,binding,before=before,changes=changes);result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 result.receipt.update(e2_e3_domain_sha256=domain.domain_sha256,e2_e3_binding_sha256=binding.binding_sha256,timing_owner=OWNER,predecessor_entry_id=PREDECESSOR,profile='transformer-fused',V=64,pe_count=32,vector_participants=list(ROOTS),payload_bytes_per_root=24576,total_payload_bytes=98304,channel=1,parameter_paths=list(domain.formula.parameter_paths()),timing_rows=[x.model_dump(mode='json') for x in domain.formula.pe_timings],simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),excluded_cost_owners=['E2.Vector','E3.DataNoC.multicast','E4.Matrix','GM','physical_route'],physical_publishable=False,model_cycle_mapping=False,e3_physical_route_resolved=False)
 if result.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'E2_E3.handoff.complete')
 return result
def append_e2_e3_timebase(base,domain):
 base=TimebaseBundle.model_validate_json(base.model_dump_json());domain=E2E3HandoffDomain.model_validate_json(domain.model_dump_json())
 if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate E2/E3 timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-E2-E3-handoff',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
