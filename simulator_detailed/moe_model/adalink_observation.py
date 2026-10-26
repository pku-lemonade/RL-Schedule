"""C08 link/receive accumulation identification contract.

A paired opcode difference is diagnostic only. The accepted timing contract
requires independently observed, causally ordered service boundaries tied to
one chunk/port/transaction. No duration is inferred from residuals.
"""
from typing import Literal
import math,json
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256

class AdalinkServiceObservation(FrozenRecord):
 run_id:str=Field(min_length=1)
 primitive:Literal['write','write_sum']
 rank_count:int=Field(ge=2)
 sender_rank:int=Field(ge=0)
 receiver_rank:int=Field(ge=0)
 source_port:int=Field(ge=0)
 destination_port:int|None=Field(default=None,ge=0)
 receiver_port_inventory:tuple[int,...]=()
 receiver_timestamp_inventory:tuple[float,...]=()
 shared_timebase_sha256:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
 payload_bytes:int=Field(gt=0)
 chunk_id:str=Field(min_length=1)
 schedule_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 unit:Literal['reported_ns','fixture_cycle']
 function_pass:bool
 guard_pass:bool
 cleanup_pass:bool
 wqe_start:float=Field(ge=0)
 wqe_ack:float=Field(ge=0)
 receiver_count_update:float|None=Field(default=None,ge=0)
 link_start:float|None=None
 link_complete:float|None=None
 accumulation_start:float|None=None
 accumulation_complete:float|None=None
 local_reduce_work:int|None=Field(default=None,ge=0)
 service_correlation_id:str|None=None
 source_refs:tuple[str,...]
 @model_validator(mode='after')
 def valid(self):
  if self.sender_rank==self.receiver_rank or max(self.sender_rank,self.receiver_rank)>=self.rank_count:raise ValueError('observation rank map invalid')
  for value in (self.wqe_start,self.wqe_ack,self.receiver_count_update,self.link_start,self.link_complete,self.accumulation_start,self.accumulation_complete):
   if value is not None and (not math.isfinite(value) or value<0):raise ValueError('observation time must be finite/nonnegative')
  if self.wqe_ack<self.wqe_start:raise ValueError('observed completion precedes issue')
  if len(self.receiver_port_inventory)!=len(self.receiver_timestamp_inventory):raise ValueError('receiver inventory mismatch')
  if any(not math.isfinite(v) or v<0 for v in self.receiver_timestamp_inventory):raise ValueError('receiver timestamp invalid')
  if self.shared_timebase_sha256 and self.receiver_count_update is not None and self.receiver_count_update<self.wqe_start:raise ValueError('common-timebase receive precedes issue')
  if not self.source_refs:raise ValueError('observation source identity required')
  return self

class AdalinkMatchedPair(FrozenRecord):
 revision:Literal['C08-accumulation-identifiability-r2']='C08-accumulation-identifiability-r2'
 write:AdalinkServiceObservation
 write_sum:AdalinkServiceObservation
 trace_kind:Literal['tsim_calibration','fixture_only']
 pair_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 @model_validator(mode='after')
 def valid(self):
  if self.write.primitive!='write' or self.write_sum.primitive!='write_sum' or self.write.run_id==self.write_sum.run_id:raise ValueError('pair must contain distinct WRITE and WRITE_SUM runs')
  for key in ('rank_count','sender_rank','receiver_rank','source_port','destination_port','payload_bytes','chunk_id','schedule_sha256','binding_sha256','unit','receiver_port_inventory'):
   if getattr(self.write,key)!=getattr(self.write_sum,key):raise ValueError('matched pair identity differs: '+key)
  if stable_sha256(self.model_dump(mode='json',exclude={'pair_sha256'}))!=self.pair_sha256:raise ValueError('matched pair hash mismatch')
  return self
 def identification(self):
  missing=[]
  for name,row in (('write',self.write),('write_sum',self.write_sum)):
   if not all((row.function_pass,row.guard_pass,row.cleanup_pass)):missing.append(name+'.function_guard_cleanup')
   for key in ('link_start','link_complete','service_correlation_id','destination_port','receiver_count_update','shared_timebase_sha256'):
    if getattr(row,key) is None:missing.append(name+'.'+key)
  for key in ('accumulation_start','accumulation_complete','local_reduce_work'):
   if getattr(self.write_sum,key) is None:missing.append('write_sum.'+key)
  if missing:return dict(status='BLOCKED_SEPARATE_SERVICE_OBSERVATION',ready=False,missing_fields=missing,link_duration=None,accumulation_duration=None,physical_publishable=False)
  for row in (self.write,self.write_sum):
   if row.link_complete<row.link_start or row.link_start<row.wqe_start or row.link_complete>row.receiver_count_update:raise ValueError('link boundary order invalid')
  row=self.write_sum
  if row.accumulation_start<row.link_complete or row.accumulation_complete<row.accumulation_start or row.accumulation_complete>row.receiver_count_update:raise ValueError('accumulation boundary order invalid')
  return dict(status='SEPARATE_SERVICE_OBSERVED' if self.trace_kind=='tsim_calibration' else 'FIXTURE_ONLY_SERVICE_OBSERVATION',ready=self.trace_kind=='tsim_calibration',missing_fields=[],link_duration=self.write.link_complete-self.write.link_start,accumulation_duration=row.accumulation_complete-row.accumulation_start,physical_publishable=False)
 def require_identified(self):
  gate=self.identification()
  if not gate['ready']:raise ValueError('C08 accumulation not independently identified: '+','.join(gate['missing_fields']))
  return gate

def freeze_adalink_matched_pair(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(revision='C08-accumulation-identifiability-r2',**kwargs);raw=TypeAdapter(dict).dump_python(raw,mode='json');raw['pair_sha256']=stable_sha256(raw)
 return AdalinkMatchedPair.model_validate_json(json.dumps(raw))
