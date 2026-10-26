"""Source-exact sentinel count scan: compiled integer Scalar/DLCM path service."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,freeze_timebase_bundle,convert_time_value
PES=tuple(range(32));PATH_COUNTS=(8,9,13,16,20,23,27,31,31)
CALLSITE='router_gemm.hpp:478-487';BOUNDARY='PE counter before source sentinel count scan -> counter after valid_expert result before tail stores'
class CountScanWork(FrozenRecord):
 profile:Literal['transformer-fused']='transformer-fused'
 ep_rank:int=Field(ge=0,le=3)
 V:Literal[64]=64
 valid_per_token:tuple[int,...]=(2,)*32
 participants:tuple[int,...]=PES
 topk:Literal[8]=8
 expert_per_rank:Literal[96]=96
 sentinel:Literal[96]=96
 sram_address:Literal[0x1c8080]=0x1c8080
 dlcm_address:Literal[0x201c8080]=0x201c8080
 loads_per_pe:Literal[3]=3
 payload_bytes_per_pe:Literal[12]=12
 input_bytes_per_pe:Literal[32]=32
 instruction_count_per_pe:Literal[13]=13
 resource_kind:Literal['SCALAR_DLCM']='SCALAR_DLCM'
 source_callsite:Literal[CALLSITE]=CALLSITE
 source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
 operation:str
 boundary:Literal[BOUNDARY]=BOUNDARY
 @model_validator(mode='after')
 def exact(self):
  if self.valid_per_token!=(2,)*32 or self.participants!=PES or self.operation!=f'valid_count_scan.rank{self.ep_rank}':raise ValueError('count scan rank/distribution/work mismatch')
  return self

def scan_work(rank):return CountScanWork(ep_rank=rank,operation=f'valid_count_scan.rank{rank}')
def extract_scan_source(router,define,paths):
 compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
 required=('Tensor dlcm_topk_idx = cute::make_tensor(&dlcm_tl_sram->moe.topk_idx[0], typename Kernel_traits::SmemLayoutTopkIdx{});','int32_t valid_expert = 0;','#pragma unroll','for (int32_t i = 0; i < TOPK; i++) { int32_t expert_id = dlcm_topk_idx(i); if (expert_id == expert_num_per_ep_rank) { break; } valid_expert = valid_expert + 1; }','init_valid_expert_num(pe_id, valid_expert);')
 if any(compact(x) not in compact(router) for x in required) or 'typedefMoeParams<2048,1024,4,4>DefaultMoeParams;' not in compact(define) or tuple(paths['instruction_counts'])!=PATH_COUNTS:raise ValueError('count scan source/profile/compiled paths drift')
 return tuple(scan_work(r) for r in range(4))
class CountScanFormula(FrozenRecord):
 revision:Literal['M07-source-scalar-count-instruction-path-r1']='M07-source-scalar-count-instruction-path-r1'
 rank:int=Field(ge=0,le=3)
 counter_envelope_ns:float=Field(ge=0)
 instruction_service_ns:float=Field(ge=0)
 calibration_scene_ids:tuple[str,...]
 formula_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if not all(math.isfinite(x) for x in (self.counter_envelope_ns,self.instruction_service_ns)) or len(set(self.calibration_scene_ids))!=2 or not all(f'rank{self.rank}-' in x for x in self.calibration_scene_ids):raise ValueError('count scan formula calibration/rank')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('count scan formula hash drift')
  return self
 def parameter_paths(self):return tuple(f'scalar.scan.rank{self.rank}.pe{p}.{k}' for p in PES for k in ('counter_envelope_ns','instruction_service_ns'))
 def duration(self,pe,changes=None,valid_count=2):
  changes=changes or {}
  if type(pe) is not int or pe not in PES or type(valid_count) is not int or valid_count not in range(9) or len(changes)>1 or not set(changes)<=set(self.parameter_paths()):raise ValueError('count scan local path/work mismatch')
  a=changes.get(f'scalar.scan.rank{self.rank}.pe{pe}.counter_envelope_ns',self.counter_envelope_ns);b=changes.get(f'scalar.scan.rank{self.rank}.pe{pe}.instruction_service_ns',self.instruction_service_ns)
  if any(isinstance(x,bool) or type(x) not in (float,int) or not math.isfinite(x) or x<0 for x in (a,b)):raise ValueError('count scan invalid coefficient')
  return float(a+PATH_COUNTS[valid_count]*b)
 def terminal(self,changes=None):return max(self.duration(p,changes) for p in PES)
class CountScanDomain(FrozenRecord):
 work:CountScanWork
 formula:CountScanFormula
 timebase:TimebaseDomain
 observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 counter_callsite:int=Field(ge=0)
 compiled_path_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 source_refs:tuple[str,...]
 domain_sha256:str
 @model_validator(mode='after')
 def exact(self):
  w,f,t=self.work,self.formula,self.timebase
  if f.rank!=w.ep_rank or (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values)!=(('M07',),w.operation,w.profile,w.boundary,'reported_ns',(64,)) or t.calibration_scene_ids!=f.calibration_scene_ids:raise ValueError('count scan rank/timebase mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256 or not self.source_refs:raise ValueError('count scan domain drift')
  return self
class CountScanBinding(FrozenRecord):
 binding:SuppliedBinding
 resources:dict[str,str]
 domain_sha256:str
 source_refs:tuple[str,...]
 binding_sha256:str
 @model_validator(mode='after')
 def exact(self):
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256 or not self.source_refs:raise ValueError('count scan binding drift')
  return self

def _freeze(cls,key,data):
 data=TypeAdapter(dict).dump_python(data,mode='json');data[key]=stable_sha256(data);return cls.model_validate_json(json.dumps(data))
def freeze_scan_formula(**kw):return _freeze(CountScanFormula,'formula_sha256',dict(revision='M07-source-scalar-count-instruction-path-r1',**kw))
def freeze_scan_domain(**kw):return _freeze(CountScanDomain,'domain_sha256',kw)
def freeze_scan_binding(**kw):return _freeze(CountScanBinding,'binding_sha256',kw)
def resolve_count_scan(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,compiled_path_sha256,physical=False):
 w=CountScanWork.model_validate_json(work.model_dump_json());matches=[]
 for raw in domains:
  d=CountScanDomain.model_validate_json(raw.model_dump_json())
  if not physical and d.work==w and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite,d.compiled_path_sha256)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,compiled_path_sha256):matches.append(d)
 if len(matches)!=1:raise ValueError('count scan unique exact domain required, found '+str(len(matches)))
 return matches[0]
def scan_resource(rank,pe):return f'rank{rank}.PE{pe}.SCALAR_DLCM'
def scan_pred(rank,pe):return f'rank{rank}.count_scan_input.pe{pe}.ready'
def scan_end(rank,pe):return f'rank{rank}.scalar_count_scan.pe{pe}.complete'
def scan_actions(domain,binding,changes=None):
 r=domain.work.ep_rank;units={u['unit_id']:u for u in binding.binding.graph['execution_units']};needed={scan_resource(r,p) for p in PES}
 if binding.domain_sha256!=domain.domain_sha256 or set(binding.resources)!=needed:raise ValueError('count scan domain/resource mapping')
 for k,v in binding.resources.items():
  if k!=v or v not in units or units[v]['node_id']!='.'.join(k.split('.')[:2]):raise ValueError('count scan rankPEresource identity')
 out=tuple(ComponentAction(action_id=scan_end(r,p),stage=f'rank{r}.scalar_count_scan',component='M07',resource_id=binding.resources[scan_resource(r,p)],work=13,work_unit='compiled-source-instruction-path',duration_cycles=domain.formula.duration(p,changes),duration_source=BOUNDARY,calibration_sha256=domain.formula.formula_sha256,dependencies=(scan_pred(r,p),),participants=(f'rank{r}.PE{p}',),bytes=12,status='calibrated') for p in PES)
 return out+(ComponentAction(action_id=f'rank{r}.scalar_count_scan.complete',stage=f'rank{r}.scalar_count_scan',component='M07',resource_id=binding.resources[scan_resource(r,0)],work=32,work_unit='after-all',duration_cycles=0,duration_source='zero-cost32waymax',calibration_sha256=domain.domain_sha256,dependencies=tuple(x.action_id for x in out),participants=tuple(f'rank{r}.PE{p}' for p in PES),status='calibrated'),)
def execute_count_scan(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
 domain=CountScanDomain.model_validate_json(domain.model_dump_json());binding=CountScanBinding.model_validate_json(binding.model_dump_json());r=domain.work.ep_rank
 if hardware.clock_domain!='reported_ns' or production and binding.binding.fixture_only:raise ValueError('count scan explicit production simulation binding/unit required')
 owned=scan_actions(domain,binding,changes)
 if {a.action_id for a in owned}&{a.action_id for a in extra_actions}:raise ValueError('count scan duplicate timing ownership')
 if not {scan_pred(r,p) for p in PES}<={a.action_id for a in extra_actions}:raise ValueError('count scan exact source-input readiness missing')
 run=execute_actions(owned+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
 run.receipt.update(rank=r,count_scan_domain_sha256=domain.domain_sha256,timing_owner=BOUNDARY,instruction_counts=[13]*32,dlcm_load_counts=[3]*32,parameter_paths=list(domain.formula.parameter_paths()),formula='counter_envelope+integer_compiled_path_count*combined_Scalar_DLCM_instruction_service',simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,independent_dlcm_load_cost_verified=False,excluded_cost_owners=['count_fill','count_token_readiness','token_fill','scalar_preparation','tail_store','Vector','Matrix','GM','DataNoC'],cost_count_once=True)
 if run.result.status=='complete':
  from .adalink_execute import _critical_to_completion
  run.receipt['event_resource_critical_path']=_critical_to_completion(run.receipt['causal_graph'],f'rank{r}.scalar_count_scan.complete')
 return run
def append_scan_timebase(base,domain):
 if domain.timebase.domain_id in {d.domain_id for d in base.domains}:raise ValueError('duplicate count scan timebase')
 return freeze_timebase_bundle(bundle_id='T81-C09-count-scan-ranks',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
