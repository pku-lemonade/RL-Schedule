"""Source-shaped single router task; route V never multiplies router GEMMs.

Only observed task startup/active/drain boundaries are calibrated. Independent
per-tile throughput and Weight-SRAM locality are unavailable without separate
observations; task parameters support explicit CPU sensitivity studies.
"""
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES,stable_sha256
from .component_service import ComponentAction

class RouterWork(FrozenRecord):
 profile:Literal['standalone','transformer-fused']
 route_V:int=Field(ge=0,le=256)
 router_invocations:int=Field(ge=1)
 feature_layout:Literal['source-hidden-stride-pe-row-shard']
 weight_locality:Literal['resident-source-packed-current-address']
 shape:tuple[int,int,int]
 @model_validator(mode='after')
 def valid(self):
  expected=(8,384,PROFILES[self.profile].hidden_gating//8)
  if self.shape!=expected:raise ValueError('router shape differs from source profile')
  return self
 def issued_flops_per_pe(self):return math.prod(self.shape)*2*self.router_invocations
 def source_production(self):
  if self.router_invocations!=1:raise ValueError('production router executes once per PE; repeated calls are diagnostic')
  return self

class RouterEngineBoundary(FrozenRecord):
 engine:Literal['MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE']
 startup_offset:float=Field(ge=0)
 active_duration:float=Field(gt=0)
 tail_duration:float=Field(ge=0)
 observed_loop_count:int|None=Field(default=None,gt=0)
 parameter_prefix:str
 @model_validator(mode='after')
 def valid(self):
  if not all(math.isfinite(v) for v in (self.startup_offset,self.active_duration,self.tail_duration)) or not self.parameter_prefix:raise ValueError('router boundary invalid')
  return self

class RouterMatrixFormula(FrozenRecord):
 revision:Literal['M05-router-source-task-boundaries-r2']='M05-router-source-task-boundaries-r2'
 profile:Literal['standalone','transformer-fused']
 shape:tuple[int,int,int]
 feature_layout:Literal['source-hidden-stride-pe-row-shard']='source-hidden-stride-pe-row-shard'
 weight_locality:Literal['resident-source-packed-current-address']='resident-source-packed-current-address'
 boundaries:tuple[RouterEngineBoundary,...]
 calibration_scene_ids:tuple[str,...]
 source_refs:tuple[str,...]
 unit:Literal['reported_ns']='reported_ns'
 formula_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if self.shape!=(8,384,PROFILES[self.profile].hidden_gating//8) or {b.engine for b in self.boundaries}!={'MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE'} or len(self.boundaries)!=4 or not self.source_refs or not self.calibration_scene_ids:raise ValueError('router formula incomplete/source shape mismatch')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('router formula hash drift')
  return self
 def actions(self,work,pe_id,*,resource_ids,changes=None):
  work=RouterWork.model_validate_json(work.model_dump_json());work.source_production()
  if (work.profile,work.shape,work.weight_locality,work.feature_layout)!=(self.profile,self.shape,self.weight_locality,self.feature_layout):raise ValueError('router formula work/locality mismatch')
  if not 0<=pe_id<32 or set(resource_ids)!={b.engine for b in self.boundaries}:raise ValueError('router 32PE engine binding incomplete')
  changes=changes or {};allowed={b.parameter_prefix+'.'+p for b in self.boundaries for p in ('startup','active_service','tail')}
  if not set(changes)<=allowed:raise ValueError('router parameter changes must target exact declared engine paths')
  out=[]
  for b in self.boundaries:
   start=changes.get(b.parameter_prefix+'.startup',b.startup_offset);active=changes.get(b.parameter_prefix+'.active_service',b.active_duration);tail=changes.get(b.parameter_prefix+'.tail',b.tail_duration)
   if any(isinstance(v,bool) or not math.isfinite(v) or v<0 for v in (start,active,tail)) or active<=0:raise ValueError('invalid router boundary parameter')
   prefix=f'router.pe{pe_id}.{b.engine}'
   out.append(ComponentAction(action_id=prefix+'.active',stage='R6.'+b.engine+'.active',component='M05',resource_id=resource_ids[b.engine],work=float(b.observed_loop_count or 1),work_unit='observed-loop-count' if b.observed_loop_count else 'source-shape-task',duration_cycles=active,duration_source='router task active boundary; per-tile service uncalibrated',calibration_sha256=self.formula_sha256,dependencies=(),earliest_start=start,participants=(f'pe{pe_id}',),status='calibrated'))
   out.append(ComponentAction(action_id=prefix+'.tail',stage='R6.'+b.engine+'.tail',component='M05',resource_id=resource_ids[b.engine],work=1.0,work_unit='task-drain',duration_cycles=tail,duration_source='router observed ALL FINISH -> PERF end',calibration_sha256=self.formula_sha256,dependencies=(prefix+'.active',),earliest_start=0.0,participants=(f'pe{pe_id}',),status='calibrated'))
  return tuple(out)

def freeze_router_formula(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(revision='M05-router-source-task-boundaries-r2',feature_layout='source-hidden-stride-pe-row-shard',weight_locality='resident-source-packed-current-address',unit='reported_ns',**kwargs);raw=TypeAdapter(dict).dump_python(raw,mode='json');raw['formula_sha256']=stable_sha256(raw)
 return RouterMatrixFormula.model_validate_json(json.dumps(raw))
