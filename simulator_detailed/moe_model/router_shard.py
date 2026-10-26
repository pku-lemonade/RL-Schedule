"""M05 single-router timing on source-declared feature shards.

Calibration binds task startup, active service and drain to each PE//4 feature
shard. This is a current-buffer task model, not independent weight-locality or
intra-task tile throughput calibration. Route V is metadata, not a call count.
"""
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES,stable_sha256
from .router_matrix import RouterWork,RouterEngineBoundary
from .component_service import ComponentAction

class RouterSourceShard(FrozenRecord):
 shard:int=Field(ge=0,lt=8)
 boundaries:tuple[RouterEngineBoundary,...]
 @model_validator(mode='after')
 def valid(self):
  if len(self.boundaries)!=4 or {b.engine for b in self.boundaries}!={'MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE'}:raise ValueError('router source shard needs four engines')
  return self

class RouterShardFormula(FrozenRecord):
 revision:Literal['M05-router-source-shard-boundaries-r3']='M05-router-source-shard-boundaries-r3'
 profile:Literal['standalone','transformer-fused']
 shape:tuple[int,int,int]
 feature_stride:int=Field(gt=0)
 feature_layout:Literal['source-hidden-stride-pe-row-shard']='source-hidden-stride-pe-row-shard'
 weight_locality:Literal['resident-source-packed-current-address']='resident-source-packed-current-address'
 shards:tuple[RouterSourceShard,...]
 calibration_scene_ids:tuple[str,...]
 source_refs:tuple[str,...]
 unit:Literal['reported_ns']='reported_ns'
 formula_sha256:str
 @model_validator(mode='after')
 def valid(self):
  p=PROFILES[self.profile]
  if self.shape!=(8,384,p.hidden_gating//8) or self.feature_stride!=p.hidden_gating or tuple(s.shard for s in self.shards)!=tuple(range(8)) or not self.calibration_scene_ids or not self.source_refs:raise ValueError('source-shard router formula incomplete')
  paths=[b.parameter_prefix for s in self.shards for b in s.boundaries]
  if len(paths)!=len(set(paths)):raise ValueError('router resource parameter prefixes must be unique')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('router shard formula hash drift')
  return self
 def timing(self,pe_id,engine):
  if type(pe_id) is not int or not 0<=pe_id<32:raise ValueError('router PE outside 32PE mapping')
  values=[b for b in self.shards[pe_id//4].boundaries if b.engine==engine]
  if len(values)!=1:raise ValueError('router engine not declared')
  b=values[0];return dict(start_ns=b.startup_offset,active_finish_ns=b.startup_offset+b.active_duration,end_ns=b.startup_offset+b.active_duration+b.tail_duration)
 def parameter_paths(self):return tuple(b.parameter_prefix+'.'+term for s in self.shards for b in s.boundaries for term in ('startup','active_service','tail'))
 def actions(self,work,pe_id,*,resource_ids,changes=None):
  work=RouterWork.model_validate_json(work.model_dump_json());work.source_production()
  if (work.profile,work.shape,work.feature_layout,work.weight_locality)!=(self.profile,self.shape,self.feature_layout,self.weight_locality):raise ValueError('router work shape/profile/layout/locality mismatch')
  if type(pe_id) is not int or not 0<=pe_id<32 or set(resource_ids)!={b.engine for b in self.shards[pe_id//4].boundaries}:raise ValueError('supplied router PE/engine binding incomplete')
  changes=changes or {}
  if not set(changes)<=set(self.parameter_paths()):raise ValueError('router mutation needs exact declared resource parameter')
  result=[]
  for b in self.shards[pe_id//4].boundaries:
   start=changes.get(b.parameter_prefix+'.startup',b.startup_offset);active=changes.get(b.parameter_prefix+'.active_service',b.active_duration);tail=changes.get(b.parameter_prefix+'.tail',b.tail_duration)
   if any(isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (start,active,tail)) or active<=0:raise ValueError('router startup/service/tail invalid')
   prefix=f'router.pe{pe_id}.{b.engine}'
   result.append(ComponentAction(action_id=prefix+'.active',stage='R6.'+b.engine+'.active',component='M05',resource_id=resource_ids[b.engine],work=float(b.observed_loop_count or 1),work_unit='observed-loop-count' if b.observed_loop_count else 'source-shaped-task',duration_cycles=float(active),duration_source='source-shard calibrated active task boundary; independent tile throughput unavailable',calibration_sha256=self.formula_sha256,dependencies=(),earliest_start=float(start),participants=(f'pe{pe_id}',),status='calibrated'))
   result.append(ComponentAction(action_id=prefix+'.tail',stage='R6.'+b.engine+'.tail',component='M05',resource_id=resource_ids[b.engine],work=1.0,work_unit='engine-drain',duration_cycles=float(tail),duration_source='source-shard ALL FINISH -> PERF end',calibration_sha256=self.formula_sha256,dependencies=(prefix+'.active',),earliest_start=0.0,participants=(f'pe{pe_id}',),status='calibrated'))
  return tuple(result)

def freeze_router_shard_formula(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(revision='M05-router-source-shard-boundaries-r3',feature_layout='source-hidden-stride-pe-row-shard',weight_locality='resident-source-packed-current-address',unit='reported_ns',**kwargs)
 raw=TypeAdapter(dict).dump_python(raw,mode='json');raw['formula_sha256']=stable_sha256(raw)
 return RouterShardFormula.model_validate_json(json.dumps(raw))
