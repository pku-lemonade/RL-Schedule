"""Rank1..3 source router local domains; each segment has independent evidence."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,freeze_timebase_bundle,convert_time_value
PES=tuple(range(32))
Segment=Literal['filter_count','count_token','token_fill']
SOURCE_COMMIT='094bcd25e985a4c836fd5521cccd61b14d911aac'
OPS={'filter_count':'filter_valid_count_readiness','count_token':'valid_count_token_ids_readiness','token_fill':'token_ids_fill'}
COMPONENTS={'filter_count':'M07','count_token':'M07','token_fill':'M06'}
STAGES={'filter_count':'R9.valid_count_init','count_token':'R9.token_ids_init','token_fill':'R9.token_ids_init'}
BOUNDARIES={'filter_count':'filter named VECTOR_WRITE completion -> third Vector self acquire/API return',
 'count_token':'count-fill named VECTOR_WRITE completion -> fourth self acquire and scalar calc fence return',
 'token_fill':'token-fill API issue -> fifth Vector CAL/WRITE visible completion;observer fence excluded'}
CALLS={'filter_count':'router_gemm.hpp:462-467;softmax_topk.hpp:251-294;ada_io.hpp:621-624',
 'count_token':'router_gemm.hpp:465-473;vector_fill.hpp:17-45;ada_io.hpp:621-624',
 'token_fill':'router_gemm.hpp:471-489;vector_fill.hpp:9-45'}
class RouterRankWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused'
 ep_rank:int=Field(ge=1,le=3)
 segment:Segment
 V:Literal[64]=64
 participants:tuple[int,...]=PES
 experts:Literal[384]=384
 experts_per_rank:Literal[96]=96
 top_k:Literal[8]=8
 valid_per_pe:Literal[2]=2
 payload_bytes:int
 operation:str
 source_callsite:str
 boundary:str
 source_commit:Literal[SOURCE_COMMIT]=SOURCE_COMMIT
 resource_kinds:tuple[str,...]
 buffer:str
 @model_validator(mode='after')
 def exact(self):
  kinds=('CONFIG','VECTOR_CAL','VECTOR_WRITE') if self.segment=='token_fill' else ('VECTOR_SELF_SYNC',) if self.segment=='filter_count' else ('VECTOR_SELF_SYNC_AND_SCALAR_FENCE',)
  buffer={'filter_count':'moe.topk_score+topk_idx->valid_expert_num','count_token':'moe.valid_expert_num->topk_token_ids','token_fill':'moe.topk_token_ids[0:32]'}[self.segment]
  if self.participants!=PES or self.payload_bytes!=(256 if self.segment=='filter_count' else 128) or self.operation!=OPS[self.segment]+f'.rank{self.ep_rank}' or self.source_callsite!=CALLS[self.segment] or self.boundary!=BOUNDARIES[self.segment] or self.resource_kinds!=kinds or self.buffer!=buffer:raise ValueError('rank source/work/resource/boundary mismatch')
  return self

def rank_work(rank,segment):
 kinds=('CONFIG','VECTOR_CAL','VECTOR_WRITE') if segment=='token_fill' else ('VECTOR_SELF_SYNC',) if segment=='filter_count' else ('VECTOR_SELF_SYNC_AND_SCALAR_FENCE',)
 return RouterRankWork(ep_rank=rank,segment=segment,payload_bytes=256 if segment=='filter_count' else 128,operation=OPS[segment]+f'.rank{rank}',source_callsite=CALLS[segment],boundary=BOUNDARIES[segment],resource_kinds=kinds,buffer={'filter_count':'moe.topk_score+topk_idx->valid_expert_num','count_token':'moe.valid_expert_num->topk_token_ids','token_fill':'moe.topk_token_ids[0:32]'}[segment])

def extract_rank_source(router,filter_source,fill_source,io_source):
 compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
 code=compact(router)
 required=('pe_node_process(intep_rank,intep_size)','constintexpert_num_per_ep_rank=EXPERT_NUM/ep_size;',
 'topk_stage_2<EXPERT_NUM>(topk_score,topk_idx,ep_rank*expert_num_per_ep_rank,expert_num_per_ep_rank);sync_vector_self();',
 'tl_vector_fill::fill_value<int32_t>(valid_expert_num,0);sync_vector_self();ada_sync_fence_calc();','tl_vector_fill::fill_value<int32_t>(topk_token_ids,pe_id);')
 if any(x not in code for x in required) or 'start_expert_idx + expert_num_per_ep_rank' not in filter_source or 'broadcast_value(output, value)' not in fill_source or 'sync_inner_release_acquire(PE_VECTOR, PE_VECTOR' not in io_source:raise ValueError('rank source/callsite drift')
 return tuple(rank_work(r,s) for r in (1,2,3) for s in OPS)

class RankPeParameters(FrozenRecord):
 pe:int=Field(ge=0,lt=32)
 local_tail_ns:float=Field(default=0,ge=0)
 config_startup_ns:float=Field(default=0,ge=0)
 cal_ns:float=Field(default=0,ge=0)
 write_issue_offset_ns:float=Field(default=0,ge=0)
 write_drain_ns:float=Field(default=0,ge=0)
 @model_validator(mode='after')
 def finite(self):
  if not all(math.isfinite(v) for k,v in self.model_dump().items()):raise ValueError('nonfinite rank parameter')
  return self
class RankSegmentFormula(FrozenRecord):
 revision:Literal['router-rank-local-events-r1']='router-rank-local-events-r1'
 ep_rank:int=Field(ge=1,le=3)
 segment:Segment
 pe_parameters:tuple[RankPeParameters,...]
 calibration_scene_ids:tuple[str,...]
 applicability:str
 formula_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if tuple(x.pe for x in self.pe_parameters)!=PES or len(self.calibration_scene_ids)!=2 or len(set(self.calibration_scene_ids))!=2 or any(not f'rank{self.ep_rank}-' in s for s in self.calibration_scene_ids):raise ValueError('rank formula inventory/calibration identity')
  for p in self.pe_parameters:
   if self.segment=='token_fill' and p.local_tail_ns!=0 or self.segment!='token_fill' and any(getattr(p,k)!=0 for k in ('config_startup_ns','cal_ns','write_issue_offset_ns','write_drain_ns')):raise ValueError('rank segment cost ownership mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('rank formula hash drift')
  return self
 def parameter_paths(self):
  names=('config_startup_ns','cal_ns','write_issue_offset_ns','write_drain_ns') if self.segment=='token_fill' else ('local_tail_ns',)
  return tuple(f'router.rank{self.ep_rank}.{self.segment}.pe{pe}.{name}' for pe in PES for name in names)
 def timing(self,pe,changes=None):
  changes=changes or {};paths=self.parameter_paths()
  if type(pe) is not int or pe not in PES or len(changes)>1 or not set(changes)<=set(paths):raise ValueError('rank local parameter path mismatch')
  q=self.pe_parameters[pe];names=('config_startup_ns','cal_ns','write_issue_offset_ns','write_drain_ns') if self.segment=='token_fill' else ('local_tail_ns',);values={}
  for name in names:
   v=changes.get(f'router.rank{self.ep_rank}.{self.segment}.pe{pe}.{name}',getattr(q,name))
   if isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('invalid rank local resource parameter')
   values[name]=float(v)
  if self.segment!='token_fill':return dict(terminal=values['local_tail_ns'],**values)
  s,c,o,d=(values[k] for k in names)
  return dict(terminal=s+max(c,o)+d,write_occupancy=max(0,c-o)+d,**values)
 def terminal(self,changes=None):return max(self.timing(p,changes)['terminal'] for p in PES)
class RouterRankDomain(FrozenRecord):
 work:RouterRankWork
 formula:RankSegmentFormula
 timebase:TimebaseDomain
 observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 counter_callsite:int=Field(ge=0)
 source_refs:tuple[str,...]
 domain_sha256:str
 @model_validator(mode='after')
 def exact(self):
  w,f,t=self.work,self.formula,self.timebase
  if (f.ep_rank,f.segment)!=(w.ep_rank,w.segment) or (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=((COMPONENTS[w.segment],),w.operation,w.profile,w.boundary,'reported_ns',(64,),'validated_simulation'):raise ValueError('rank domain component/formula/timebase mismatch')
  if t.calibration_scene_ids!=f.calibration_scene_ids or not all(f'rank{w.ep_rank}-' in s for s in t.validation_scene_ids):raise ValueError('cross-rank validation/timebase identity')
  if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('rank domain hash drift')
  return self
class RouterRankBinding(FrozenRecord):
 binding:SuppliedBinding
 resources:dict[str,str]
 domain_sha256:str
 source_refs:tuple[str,...]
 binding_sha256:str
 @model_validator(mode='after')
 def exact(self):
  units={u['unit_id']:u for u in self.binding.graph['execution_units']}
  if not self.resources or len(set(self.resources.values()))!=len(self.resources) or not set(self.resources.values())<=set(units) or not self.source_refs:raise ValueError('rank resource binding incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('rank binding hash drift')
  return self

def _freeze(cls,field,raw):
 raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_rank_formula(**kw):return _freeze(RankSegmentFormula,'formula_sha256',dict(revision='router-rank-local-events-r1',**kw))
def freeze_rank_domain(**kw):return _freeze(RouterRankDomain,'domain_sha256',kw)
def freeze_rank_binding(**kw):return _freeze(RouterRankBinding,'binding_sha256',kw)
def resolve_rank_domain(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
 w=RouterRankWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=RouterRankDomain.model_validate_json(raw.model_dump_json())
  if not physical and d.work==w and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
 if len(matches)!=1:raise ValueError('rank local resolver requires unique domain, found '+str(len(matches)))
 return matches[0]

def action_stem(rank,segment):return f'rank{rank}.{segment}'
def predecessor(rank,segment,pe):return {'filter_count':f'rank{rank}.filter_write.pe{pe}.complete','count_token':f'rank{rank}.count_fill.pe{pe}.write.complete','token_fill':f'rank{rank}.count_token.pe{pe}.complete'}[segment]
def required_resources(work):return tuple(f'rank{work.ep_rank}.PE{p}.{kind}' for p in PES for kind in work.resource_kinds)
def rank_actions(domain,binding,changes=None):
 w=domain.work;f=domain.formula;rank=w.ep_rank;stem=action_stem(rank,w.segment)
 if binding.domain_sha256!=domain.domain_sha256 or set(binding.resources)!=set(required_resources(w)):raise ValueError('rank/segment resource ownership mismatch')
 units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
 for logical,physical in binding.resources.items():
  if logical!=physical or units[physical]['node_id']!=logical.split('.')[0]+'.'+logical.split('.')[1]:raise ValueError('rank/PE/engine binding mismatch')
 out=[]
 def add(i,pe,kind,duration,deps,byte_count=0):out.append(ComponentAction(action_id=i,stage=stem,component=COMPONENTS[w.segment],resource_id=binding.resources[f'rank{rank}.PE{pe}.{kind}'],work=32,work_unit='source-exact-rank-router',duration_cycles=duration,duration_source=f'owned:{w.operation}:{w.boundary}',calibration_sha256=f.formula_sha256,dependencies=deps,participants=(f'rank{rank}.PE{pe}',),bytes=byte_count,status='calibrated'))
 for p in PES:
  t=f.timing(p,changes);local=stem+f'.pe{p}'
  if w.segment!='token_fill':add(local+'.complete',p,w.resource_kinds[0],t['terminal'],(predecessor(rank,w.segment,p),))
  else:
   add(local+'.config',p,'CONFIG',t['config_startup_ns'],(predecessor(rank,w.segment,p),))
   add(local+'.cal',p,'VECTOR_CAL',t['cal_ns'],(local+'.config',))
   add(local+'.write_issue',p,'CONFIG',t['write_issue_offset_ns'],(local+'.config',))
   add(local+'.write',p,'VECTOR_WRITE',t['write_occupancy'],(local+'.write_issue',),128)
   add(local+'.complete',p,'CONFIG',0,(local+'.cal',local+'.write'))
 add(stem+'.complete',0,w.resource_kinds[0],0,tuple(stem+f'.pe{p}.complete' for p in PES))
 return tuple(out)

def execute_rank_domain(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
 domain=RouterRankDomain.model_validate_json(domain.model_dump_json());binding=RouterRankBinding.model_validate_json(binding.model_dump_json());w=domain.work;stem=action_stem(w.ep_rank,w.segment)
 if hardware.clock_domain!='reported_ns':raise ValueError('rank domain simulation unit mismatch')
 if production and binding.binding.fixture_only:raise ValueError('rank production supplied binding required')
 if any(a.action_id.startswith(stem+'.') for a in extra_actions):raise ValueError('rank segment duplicate cost owner')
 run=execute_actions(rank_actions(domain,binding,changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 run.receipt.update(rank=w.ep_rank,segment=w.segment,domain_sha256=domain.domain_sha256,timing_owner=w.boundary,parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,dispatch_identity_closed=False,per_pe_timing=[dict(pe=p,**domain.formula.timing(p,changes)) for p in PES],cost_count_once=True)
 if run.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  run.receipt['event_resource_critical_path']=_critical_to_completion(run.receipt['causal_graph'],stem+'.complete')
 return run

def execute_rank_composition(domains,bindings,hardware,*,changes=None,extra_actions=(),production=True):
 # Native dependency execution over explicit component owners, no scheduler.
 if len(domains)!=len(bindings) or not domains:raise ValueError('rank composition binding inventory')
 ids=[(d.work.ep_rank,d.work.segment) for d in domains]
 if len(set(ids))!=len(ids):raise ValueError('rank composition duplicate owner')
 graph=bindings[0].binding
 if any(b.binding!=graph for b in bindings):raise ValueError('rank composition requires identical supplied native graph')
 changes=changes or {};allowed={p for d in domains for p in d.formula.parameter_paths()}
 if len(changes)>1 or not set(changes)<=allowed or hardware.clock_domain!='reported_ns':raise ValueError('rank composition local parameter/unit mismatch')
 actions=[]
 for d,b in zip(domains,bindings):actions+=rank_actions(d,b,{k:v for k,v in changes.items() if k in d.formula.parameter_paths()})
 if set(a.action_id for a in actions)&set(a.action_id for a in extra_actions):raise ValueError('rank composition repeated billing')
 if production and graph.fixture_only:raise ValueError('rank production binding required')
 run=execute_actions(tuple(actions)+tuple(extra_actions),graph,hardware,production=production)
 run.receipt.update(rank_domain_owners=[dict(rank=d.work.ep_rank,segment=d.work.segment,domain_sha256=d.domain_sha256) for d in domains],physical_publishable=False,simulation_only=True)
 return run

def append_rank_timebase(base,domain):
 if domain.timebase.domain_id in {t.domain_id for t in base.domains}:raise ValueError('duplicate rank timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-plus-router-ranks123',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
