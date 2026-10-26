"""Count zero-fill body strictly between frozen readiness and WRITE terminal."""
from typing import Literal
import json,re,math
from pydantic import Field,TypeAdapter,model_validator
from .workload import FrozenRecord,stable_sha256
from .token_id_fill import TokenIdPeFormula
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,freeze_timebase_bundle,convert_time_value
PES=tuple(range(32));SOURCE='094bcd25e985a4c836fd5521cccd61b14d911aac'
CALLSITE='router_gemm.hpp:463-470;vector_fill.hpp:9-45;ada_io.hpp:621-624'
BOUNDARY='third VECTOR self-acquire/API readiness terminal -> fourth count-fill CAL/WRITE visible end'
class CountFillWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused'
 ep_rank:int=Field(ge=0,le=3)
 V:Literal[64]=64
 per_token_valid_counts:tuple[int,...]=(2,)*32
 elements:Literal[32]=32
 bytes:Literal[128]=128
 dtype:Literal['int32']='int32'
 fill_value:Literal[0]=0
 participants:tuple[int,...]=PES
 buffer:Literal['moe.valid_expert_num[0:32]']='moe.valid_expert_num[0:32]'
 source_callsite:Literal[CALLSITE]=CALLSITE
 source_commit:Literal[SOURCE]=SOURCE
 boundary:Literal[BOUNDARY]=BOUNDARY
 operation:str
 @model_validator(mode='after')
 def exact(self):
  if self.participants!=PES or self.per_token_valid_counts!=(2,)*32 or self.operation!=f'count_zero_fill.rank{self.ep_rank}':raise ValueError('count fill rank/work/source identity mismatch')
  return self

def count_work(rank):return CountFillWork(ep_rank=rank,operation=f'count_zero_fill.rank{rank}')
def extract_count_fill(router,fill,io):
 compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
 code=compact(router)
 required=('topk_stage_2<EXPERT_NUM>(topk_score,topk_idx,ep_rank*expert_num_per_ep_rank,expert_num_per_ep_rank);sync_vector_self();','tl_vector_fill::fill_value<int32_t>(valid_expert_num,0);sync_vector_self();ada_sync_fence_calc();')
 if any(x not in code for x in required) or 'broadcast_value(output, value)' not in fill or 'cute::ada::vec_write(output, 0)' not in fill or 'sync_inner_release_acquire(PE_VECTOR, PE_VECTOR' not in io:raise ValueError('count fill source drift')
 return tuple(count_work(r) for r in range(4))
class CountFillFormula(FrozenRecord):
 revision:Literal['M06-count-fill-ready-engine-r1']='M06-count-fill-ready-engine-r1'
 rank:int=Field(ge=0,le=3)
 pe_formulas:tuple[TokenIdPeFormula,...]
 calibration_scene_ids:tuple[str,...]
 formula_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if tuple(x.pe for x in self.pe_formulas)!=PES or len(self.calibration_scene_ids)!=2 or len(set(self.calibration_scene_ids))!=2 or not all(f'rank{self.rank}-' in x for x in self.calibration_scene_ids):raise ValueError('count formula rank/calibration inventory')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('count formula hash drift')
  return self
 def parameter_paths(self):return tuple(f'vector.count_fill.rank{self.rank}.pe{p}.{k}' for p in PES for k in ('ready_startup_ns','cal_ns','write_issue_offset_ns','write_drain_ns'))
 def timing(self,pe,changes=None):
  changes=changes or {};keys=self.parameter_paths()
  if type(pe) is not int or pe not in PES or len(changes)>1 or not set(changes)<=set(keys):raise ValueError('count fill exact local parameter required')
  q=self.pe_formulas[pe];vals=[q.config_startup_ns,q.cal_ns,q.write_issue_offset_ns,q.write_drain_ns]
  for i in range(4):
   v=changes.get(keys[4*pe+i],vals[i])
   if isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('invalid count fill local parameter')
   vals[i]=float(v)
  s,c,o,d=vals
  return dict(ready_startup_ns=s,cal_ns=c,write_issue_offset_ns=o,write_drain_ns=d,write_occupancy_ns=max(0.,c-o)+d,terminal_ns=s+max(c,o)+d)
 def terminal(self,changes=None):return max(self.timing(p,changes)['terminal_ns'] for p in PES)
class CountFillDomain(FrozenRecord):
 work:CountFillWork
 formula:CountFillFormula
 timebase:TimebaseDomain
 observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 counter_callsite:int=Field(ge=0)
 source_refs:tuple[str,...]
 domain_sha256:str
 @model_validator(mode='after')
 def exact(self):
  w,f,t=self.work,self.formula,self.timebase
  if f.rank!=w.ep_rank or (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M06',),w.operation,w.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation') or t.calibration_scene_ids!=f.calibration_scene_ids or not all(f'rank{w.ep_rank}-' in x for x in t.validation_scene_ids):raise ValueError('count fill domain/timebase/rank mismatch')
  if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('count fill domain hash drift')
  return self
class CountFillBinding(FrozenRecord):
 binding:SuppliedBinding
 resources:dict[str,str]
 domain_sha256:str
 source_refs:tuple[str,...]
 binding_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('count fill binding hash drift')
  return self

def _freeze(cls,key,raw):
 raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[key]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_count_formula(**kw):return _freeze(CountFillFormula,'formula_sha256',dict(revision='M06-count-fill-ready-engine-r1',**kw))
def freeze_count_domain(**kw):return _freeze(CountFillDomain,'domain_sha256',kw)
def freeze_count_binding(**kw):return _freeze(CountFillBinding,'binding_sha256',kw)
def resolve_count_fill(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 w=CountFillWork.model_validate_json(work.model_dump_json());matches=[]
 for d0 in domains:
  d=CountFillDomain.model_validate_json(d0.model_dump_json())
  if not physical and d.work==w and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError('count fill exact domain required, found '+str(len(matches)))
 return matches[0]
def count_resources(rank):return tuple(f'rank{rank}.PE{p}.{k}' for p in PES for k in ('CONFIG','VECTOR_CAL','VECTOR_WRITE'))
def count_start(rank,pe):return f'R9_COUNT.handoff.pe{pe}' if rank==0 else f'rank{rank}.filter_count.pe{pe}.complete'
def count_end(rank,pe):return f'R9.rank0.pe{pe}.count_fill.write.complete' if rank==0 else f'rank{rank}.count_fill.pe{pe}.write.complete'
def count_actions(domain,binding,changes=None):
 r=domain.work.ep_rank;stem=f'rank{r}.count_body';units={x['unit_id']:x for x in binding.binding.graph['execution_units']}
 if binding.domain_sha256!=domain.domain_sha256 or set(binding.resources)!=set(count_resources(r)):raise ValueError('count fill binding/domain/resource mismatch')
 for logical,physical in binding.resources.items():
  if logical!=physical or physical not in units or units[physical]['node_id']!='.'.join(logical.split('.')[:2]):raise ValueError('count fill rank/PE/engine mismatch')
 out=[]
 def add(i,p,k,n,deps,byte_count=0):out.append(ComponentAction(action_id=i,stage=stem,component='M06',resource_id=binding.resources[f'rank{r}.PE{p}.{k}'],work=32,work_unit='source-count-fill-elements',duration_cycles=n,duration_source=domain.work.boundary,calibration_sha256=domain.formula.formula_sha256,dependencies=deps,participants=(f'rank{r}.PE{p}',),bytes=byte_count,status='calibrated'))
 for p in PES:
  t=domain.formula.timing(p,changes);i=stem+f'.pe{p}'
  add(i+'.config',p,'CONFIG',t['ready_startup_ns'],(count_start(r,p),))
  add(i+'.cal',p,'VECTOR_CAL',t['cal_ns'],(i+'.config',))
  add(i+'.write_issue',p,'CONFIG',t['write_issue_offset_ns'],(i+'.config',))
  add(i+'.write',p,'VECTOR_WRITE',t['write_occupancy_ns'],(i+'.write_issue',),128)
  add(count_end(r,p),p,'CONFIG',0.,(i+'.cal',i+'.write'))
 add(stem+'.complete',0,'CONFIG',0.,tuple(count_end(r,p) for p in PES))
 return tuple(out)
def execute_count_fill(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
 domain=CountFillDomain.model_validate_json(domain.model_dump_json());binding=CountFillBinding.model_validate_json(binding.model_dump_json());r=domain.work.ep_rank
 if hardware.clock_domain!='reported_ns':raise ValueError('count fill simulation unit required')
 if production and binding.binding.fixture_only:raise ValueError('count fill production binding required')
 actions=count_actions(domain,binding,changes)
 if set(a.action_id for a in actions)&set(a.action_id for a in extra_actions):raise ValueError('count fill duplicate cost owner')
 run=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 run.receipt.update(rank=r,domain_sha256=domain.domain_sha256,timing_owner=domain.work.boundary,excluded_cost_owners=['filter_count_handoff','count_token_handoff','configuration_before_readiness','token_fill','scalar_scan','observer_fence','R10','R11','GM','DataNoC','Matrix'],parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,cost_count_once=True)
 if run.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  run.receipt['event_resource_critical_path']=_critical_to_completion(run.receipt['causal_graph'],f'rank{r}.count_body.complete')
 return run
def append_count_timebase(base,domain):
 if domain.timebase.domain_id in {x.domain_id for x in base.domains}:raise ValueError('duplicate count fill timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-plus-count-fill-ranks',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
