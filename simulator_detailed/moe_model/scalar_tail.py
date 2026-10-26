"""Source scalar cumulative tail stores, combined Scalar/DLCM primitive only."""
from typing import Literal
import math,json,re
from pydantic import Field,TypeAdapter,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,freeze_timebase_bundle,convert_time_value
PES=tuple(range(32));CALLSITE='router_gemm.hpp:97-103,478-489';BOUNDARY='PE counter before init_valid_expert_num -> counter after final source scalar DLCM tail store'
class ScalarTailWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused'
 ep_rank:int=Field(ge=0,le=3)
 V:Literal[64]=64
 valid_per_token:tuple[int,...]=(2,)*32
 participants:tuple[int,...]=PES
 elements:Literal[32]=32
 dtype:Literal['int32']='int32'
 count_buffer:Literal['moe.valid_expert_num']='moe.valid_expert_num'
 sram_address:Literal[0x1c8000]=0x1c8000
 dlcm_address:Literal[0x201c8000]=0x201c8000
 store_counts:tuple[int,...]=tuple(range(32,0,-1))
 bytes_per_store:Literal[4]=4
 source_callsite:Literal[CALLSITE]=CALLSITE
 source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
 boundary:Literal[BOUNDARY]=BOUNDARY
 operation:str
 @model_validator(mode='after')
 def exact(self):
  if self.valid_per_token!=(2,)*32 or self.participants!=PES or self.store_counts!=tuple(range(32,0,-1)) or self.operation!=f'valid_count_scalar_tail.rank{self.ep_rank}':raise ValueError('scalar tail rank/work/buffer mismatch')
  return self

def tail_work(rank):return ScalarTailWork(ep_rank=rank,operation=f'valid_count_scalar_tail.rank{rank}')
def extract_tail_source(router,define):
 compact=lambda x:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',x,flags=re.S).split())
 if '__device__inlinevoidinit_valid_expert_num(intpe_id,int32_tvalid_expert){for(inti=pe_id;i<PE_NUM;++i){dlcm_tl_sram->moe.valid_expert_num[i]=valid_expert;}}' not in compact(router) or 'alignas(128)int32_tvalid_expert_num[moe::PE_NUM];' not in compact(define):raise ValueError('scalar tail source/callsite drift')
 return tuple(tail_work(r) for r in range(4))
class ScalarTailFormula(FrozenRecord):
 revision:Literal['M09-scalar-dlcm-source-loop-r1']='M09-scalar-dlcm-source-loop-r1'
 rank:int=Field(ge=0,le=3)
 startup_ns:float=Field(ge=0)
 per_store_ns:float=Field(ge=0)
 calibration_scene_ids:tuple[str,...]
 formula_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if not math.isfinite(self.startup_ns) or not math.isfinite(self.per_store_ns) or len(self.calibration_scene_ids)!=2 or len(set(self.calibration_scene_ids))!=2 or not all(f'rank{self.rank}-' in s for s in self.calibration_scene_ids):raise ValueError('scalar tail formula identity/parameters')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('scalar tail formula hash drift')
  return self
 def parameter_paths(self):return tuple(f'scalar.tail.rank{self.rank}.pe{p}.{k}' for p in PES for k in ('startup_ns','per_store_ns'))
 def duration(self,pe,changes=None):
  changes=changes or {}
  if type(pe) is not int or pe not in PES or len(changes)>1 or not set(changes)<=set(self.parameter_paths()):raise ValueError('scalar tail exact local parameter path required')
  a=changes.get(f'scalar.tail.rank{self.rank}.pe{pe}.startup_ns',self.startup_ns);b=changes.get(f'scalar.tail.rank{self.rank}.pe{pe}.per_store_ns',self.per_store_ns)
  if any(isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (a,b)):raise ValueError('scalar tail invalid local parameter')
  return float(a+(32-pe)*b)
 def terminal(self,changes=None):return max(self.duration(p,changes) for p in PES)
class ScalarTailDomain(FrozenRecord):
 work:ScalarTailWork
 formula:ScalarTailFormula
 timebase:TimebaseDomain
 observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 counter_callsite:int=Field(ge=0)
 source_refs:tuple[str,...]
 domain_sha256:str
 @model_validator(mode='after')
 def exact(self):
  w,f,t=self.work,self.formula,self.timebase
  if f.rank!=w.ep_rank or (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M07',),w.operation,w.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation') or t.calibration_scene_ids!=f.calibration_scene_ids or not all(f'rank{w.ep_rank}-' in s for s in t.validation_scene_ids):raise ValueError('scalar tail domain rank/timebase mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256 or not self.source_refs:raise ValueError('scalar tail domain hash drift')
  return self
class ScalarTailBinding(FrozenRecord):
 binding:SuppliedBinding
 resources:dict[str,str]
 domain_sha256:str
 source_refs:tuple[str,...]
 binding_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('scalar tail binding hash drift')
  return self

def _freeze(cls,k,d):
 d=TypeAdapter(dict).dump_python(d,mode='json');d[k]=stable_sha256(d);return cls.model_validate_json(json.dumps(d))
def freeze_tail_formula(**kw):return _freeze(ScalarTailFormula,'formula_sha256',dict(revision='M09-scalar-dlcm-source-loop-r1',**kw))
def freeze_tail_domain(**kw):return _freeze(ScalarTailDomain,'domain_sha256',kw)
def freeze_tail_binding(**kw):return _freeze(ScalarTailBinding,'binding_sha256',kw)
def resolve_scalar_tail(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 w=ScalarTailWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=ScalarTailDomain.model_validate_json(raw.model_dump_json())
  if not physical and d.work==w and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError('scalar tail unique exact domain required, found '+str(len(matches)))
 return matches[0]
def tail_resource(rank,pe):return f'rank{rank}.PE{pe}.SCALAR_DLCM'
def tail_pred(rank,pe):return f'rank{rank}.scalar_count_scan.pe{pe}.complete'
def tail_actions(domain,binding,changes=None):
 r=domain.work.ep_rank;required={tail_resource(r,p) for p in PES};units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
 if binding.domain_sha256!=domain.domain_sha256 or set(binding.resources)!=required:raise ValueError('scalar tail binding/domain/resource mismatch')
 for k,v in binding.resources.items():
  if k!=v or v not in units or units[v]['node_id']!='.'.join(k.split('.')[:2]):raise ValueError('scalar tail rank/PE resource identity')
 out=[]
 for p in PES:out.append(ComponentAction(action_id=f'rank{r}.scalar_tail.pe{p}.complete',stage=f'rank{r}.scalar_tail',component='M07',resource_id=binding.resources[tail_resource(r,p)],work=32-p,work_unit='source-int32-tail-stores',duration_cycles=domain.formula.duration(p,changes),duration_source=BOUNDARY,calibration_sha256=domain.formula.formula_sha256,dependencies=(tail_pred(r,p),),participants=(f'rank{r}.PE{p}',),bytes=4*(32-p),status='calibrated'))
 out.append(ComponentAction(action_id=f'rank{r}.scalar_tail.complete',stage=f'rank{r}.scalar_tail',component='M07',resource_id=binding.resources[tail_resource(r,0)],work=32,work_unit='scalar-tail-after-all',duration_cycles=0,duration_source='zero-cost32way max',calibration_sha256=domain.domain_sha256,dependencies=tuple(a.action_id for a in out),participants=tuple(f'rank{r}.PE{p}' for p in PES),status='calibrated'))
 return tuple(out)
def execute_scalar_tail(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
 domain=ScalarTailDomain.model_validate_json(domain.model_dump_json());binding=ScalarTailBinding.model_validate_json(binding.model_dump_json());r=domain.work.ep_rank
 if hardware.clock_domain!='reported_ns':raise ValueError('scalar tail simulation unit mismatch')
 if production and binding.binding.fixture_only:raise ValueError('scalar tail production binding required')
 owned=tail_actions(domain,binding,changes)
 if set(a.action_id for a in owned)&set(a.action_id for a in extra_actions):raise ValueError('scalar tail duplicate timing ownership')
 result=execute_actions(owned+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 result.receipt.update(rank=r,scalar_tail_domain_sha256=domain.domain_sha256,timing_owner=BOUNDARY,source_store_counts=list(domain.work.store_counts),formula='startup+integer_store_count*combined_scalar_dlcm_store_service',parameter_paths=list(domain.formula.parameter_paths()),excluded_cost_owners=['scalar_preparation_blocked','count_scan','Vector','R10','R11','GM','DataNoC'],simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,independent_DLCM_store_cost_verified=False,dispatch_identity_closed=False)
 if result.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],f'rank{r}.scalar_tail.complete')
 return result
def append_tail_timebase(base,domain):
 if domain.timebase.domain_id in {d.domain_id for d in base.domains}:raise ValueError('duplicate scalar tail timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-scalar-tail-ranks',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
