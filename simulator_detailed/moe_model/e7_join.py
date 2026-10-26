"""Exact fused E7 PE_UPLOAD1 -> fullmesh ADALINK counted join."""
from typing import Literal
import json,math
from pydantic import Field,TypeAdapter,model_validator
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseBundle,TimebaseDomain,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

FULLMESH_PORTS=tuple(range(4,18));E7_OWNER='E7.pe_upload1_commid16_counted_join';E7_BOUNDARY='32 PE_UPLOAD1 comm-id16 internal release issues -> max ADALINK4..17 ADL_FUNC internal acquire(decr=32) completion'
class E7JoinWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused';consumer_stage:Literal['E7']='E7';operation:Literal['output_completion_join']='output_completion_join';producer_count:Literal[32]=32;waiter_count:Literal[14]=14;comm_id:Literal[16]=16;increment:Literal[1]=1;decrement:Literal[32]=32;counter_kind:Literal['COMM_PRODUCE_CNT']='COMM_PRODUCE_CNT';source_entry:Literal['PE_UPLOAD1']='PE_UPLOAD1';waiter_entry:Literal['ADL_FUNC']='ADL_FUNC';adalink_mask:Literal[0x3FFF0]=0x3FFF0;waiter_ports:tuple[int,...]=FULLMESH_PORTS;boundary:Literal[E7_BOUNDARY]=E7_BOUNDARY;timing_ownership:Literal[E7_OWNER]=E7_OWNER;source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac';source_callsite:Literal['moe_gemm.hpp:130-139,269-287']='moe_gemm.hpp:130-139,269-287'
 @model_validator(mode='after')
 def valid(self):
  if self.waiter_ports!=FULLMESH_PORTS or self.adalink_mask!=sum(1<<port for port in FULLMESH_PORTS) or self.decrement!=self.producer_count*self.increment:raise ValueError('E7 source count/mask identity mismatch')
  return self
class E7JoinFormula(FrozenRecord):
 revision:Literal['M07-fused-E7-commid-join-r4']='M07-fused-E7-commid-join-r4';source_unit:Literal['reported_ns']='reported_ns';token_issue_ns:tuple[tuple[float,...],...];waiter_consume_ns:tuple[float,...];calibration_scene_ids:tuple[str,...];applicability:str;formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def valid(self):
  values=[v for row in self.token_issue_ns for v in row]+list(self.waiter_consume_ns)
  if len(self.token_issue_ns)!=14 or any(len(row)!=32 for row in self.token_issue_ns) or len(self.waiter_consume_ns)!=14 or any(not math.isfinite(v) or v<0 for v in values) or len(self.calibration_scene_ids)<2 or not self.applicability:raise ValueError('E7 formula identity/value incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('E7 formula hash drift')
  return self
 def parameter_paths(self):return tuple([f'sync.e7.adalink{port}.counted_ready_ns' for port in FULLMESH_PORTS]+[f'sync.e7.adalink{port}.consume_ns' for port in FULLMESH_PORTS])
 def ready_duration(self,port,changes=None):
  changes=changes or {};path=f'sync.e7.adalink{port}.counted_ready_ns';value=changes.get(path,max(self.token_issue_ns[port-4]));self._check(changes,value);return value
 def waiter_duration(self,port,changes=None):
  changes=changes or {};path=f'sync.e7.adalink{port}.consume_ns';value=changes.get(path,self.waiter_consume_ns[port-4]);self._check(changes,value);return value
 def _check(self,changes,value):
  if not set(changes)<=set(self.parameter_paths()) or len(changes)>1 or isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('E7 requires one exact finite local parameter')
 def terminal(self,changes=None):
  return max(self.ready_duration(port,changes)+self.waiter_duration(port,changes) for port in FULLMESH_PORTS)
class E7ObservedEvent(FrozenRecord):
 role:Literal['producer','waiter'];participant_id:int=Field(ge=0);begin_ns:float=Field(ge=0);end_ns:float=Field(ge=0);comm_id:Literal[16]=16;count:int=Field(gt=0)
 @model_validator(mode='after')
 def valid(self):
  if self.end_ns<self.begin_ns or (self.role=='producer' and (self.participant_id>=32 or self.count!=1)) or (self.role=='waiter' and (self.participant_id not in FULLMESH_PORTS or not 1<=self.count<=32)):raise ValueError('E7 observed event identity/order mismatch')
  return self
def validate_e7_event_inventory(events):
 events=tuple(E7ObservedEvent.model_validate_json(event.model_dump_json()) for event in events);producers=[e for e in events if e.role=='producer'];waiters=[e for e in events if e.role=='waiter']
 if len(producers)!=32 or {e.participant_id for e in producers}!=set(range(32)) or len(waiters)!=14 or {e.participant_id for e in waiters}!=set(FULLMESH_PORTS) or len({(e.role,e.participant_id) for e in events})!=46:raise ValueError('E7 duplicate/missing event inventory')
 return events
class E7JoinDomain(FrozenRecord):
 work:E7JoinWork;formula:E7JoinFormula;timebase:TimebaseDomain;observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');counter_callsite:int=Field(ge=0);source_refs:tuple[str,...];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def valid(self):
  t=self.timebase
  if t.component_ids!=('M07',) or t.operation!=self.work.operation or t.profile!=self.work.profile or t.boundary_id!=self.work.boundary or t.source_unit!=self.formula.source_unit or t.accepted_work_values!=(32,) or t.status!='validated_simulation' or not self.source_refs:raise ValueError('E7 exact domain mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('E7 domain hash drift')
  return self
class E7JoinBinding(FrozenRecord):
 binding:SuppliedBinding;producer_resources:dict[str,str];waiter_resources:dict[str,str];domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$');timing_owner:Literal[E7_OWNER]=E7_OWNER;source_refs:tuple[str,...];binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def valid(self):
  units={row['unit_id'] for row in self.binding.graph['execution_units']};expected_p={f'PE{pe}.UPLOAD1' for pe in range(32)};expected_w={f'ADALINK{port}.ADL_FUNC' for port in FULLMESH_PORTS};values=set(self.producer_resources.values())|set(self.waiter_resources.values())
  if set(self.producer_resources)!=expected_p or set(self.waiter_resources)!=expected_w or len(values)!=46 or not values<=units or not self.source_refs:raise ValueError('E7 supplied resource binding incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('E7 binding hash drift')
  return self
def _freeze(cls,field,raw):raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_e7_formula(**kwargs):return _freeze(E7JoinFormula,'formula_sha256',dict(revision='M07-fused-E7-commid-join-r4',source_unit='reported_ns',**kwargs))
def freeze_e7_domain(**kwargs):return _freeze(E7JoinDomain,'domain_sha256',kwargs)
def freeze_e7_binding(**kwargs):return _freeze(E7JoinBinding,'binding_sha256',dict(timing_owner=E7_OWNER,**kwargs))
def resolve_e7(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 work=E7JoinWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=E7JoinDomain.model_validate_json(raw.model_dump_json())
  if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError(f'E7 requires one exact domain, found {len(matches)}')
 return matches[0]
def e7_actions(domain,binding,*,before=('E6.scatter.complete',),changes=None):
 domain=E7JoinDomain.model_validate_json(domain.model_dump_json());binding=E7JoinBinding.model_validate_json(binding.model_dump_json())
 if binding.domain_sha256!=domain.domain_sha256:raise ValueError('E7 binding/domain mismatch')
 actions=[]
 for port in FULLMESH_PORTS:actions.append(ComponentAction(action_id=f'E7.acquire.adalink{port}',stage='E7',component='M07',resource_id=binding.waiter_resources[f'ADALINK{port}.ADL_FUNC'],work=32,work_unit='commid-token-issues-and-decrement',duration_cycles=domain.formula.ready_duration(port,changes)+domain.formula.waiter_duration(port,changes),duration_source=E7_OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=tuple(before),participants=tuple(f'PE{pe}.UPLOAD1' for pe in range(32))+(f'ADALINK{port}.ADL_FUNC',),status='calibrated'))
 actions.append(ComponentAction(action_id='E7.complete',stage='E7',component='M07',resource_id=binding.waiter_resources['ADALINK4.ADL_FUNC'],work=14,work_unit='after-all-waiters',duration_cycles=0,duration_source='zero-cost max; 32x14 counted edges already owned',calibration_sha256=domain.domain_sha256,dependencies=tuple(f'E7.acquire.adalink{port}' for port in FULLMESH_PORTS),participants=tuple(f'ADALINK{port}' for port in FULLMESH_PORTS),status='calibrated'))
 return tuple(actions)
def execute_e7(domain,binding,hardware,*,before=('E6.scatter.complete',),changes=None,extra_actions=(),production=True,cancel=None):
 domain=E7JoinDomain.model_validate_json(domain.model_dump_json());binding=E7JoinBinding.model_validate_json(binding.model_dump_json())
 if production and binding.binding.fixture_only:raise ValueError('production E7 requires supplied binding')
 if any(action.stage=='E7' or action.action_id.startswith('E7.') for action in extra_actions):raise ValueError('E7 duplicate timing owner')
 actions=e7_actions(domain,binding,before=before,changes=changes);result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 result.receipt.update(e7_domain_sha256=domain.domain_sha256,e7_binding_sha256=binding.binding_sha256,timing_owner=E7_OWNER,comm_id=16,counter_kind='COMM_PRODUCE_CNT',producer_count=32,waiter_count=14,release_edges=448,decrement_per_waiter=32,token_issue_ns=[list(row) for row in domain.formula.token_issue_ns],parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),excluded_cost_owners=['E6.scatter_write_sum','E8.allreduce','DataNoC','Matrix','Vector'],physical_publishable=False,model_cycle_mapping=False)
 if result.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'E7.complete')
 return result
def append_e7_timebase(base,domain):
 base=TimebaseBundle.model_validate_json(base.model_dump_json());domain=E7JoinDomain.model_validate_json(domain.model_dump_json())
 if domain.timebase.domain_id in {d.domain_id for d in base.domains}:raise ValueError('duplicate E7 timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-E7-join',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
