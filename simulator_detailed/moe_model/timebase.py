"""C09 explicit time identities and exact simulation-only conversions.

A clock label or nominal frequency never binds model_cycle to seconds. Domains
require actual active-unit observations and independently passed validation;
physical publication additionally requires an attested physical clock source.
"""
from typing import Literal
import math,json
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256

class TimebaseBoundaryObservation(FrozenRecord):
 scene_id:str
 component_id:Literal['M01','M06']
 operation:Literal['point_to_point','rmsnorm']
 profile:Literal['standalone']
 work:int=Field(gt=0)
 work_unit:Literal['byte','rmsnorm_launch']
 boundary_id:str
 model_library_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 systemc_library_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 actual_counter_callsite:int=Field(ge=0)
 active_default_unit_seconds:float=Field(gt=0)
 active_resolution_seconds:float=Field(gt=0)
 counter_begin:int=Field(ge=0)
 counter_end:int=Field(gt=0)
 begin_ticks_before:int=Field(ge=0)
 begin_ticks_after:int=Field(ge=0)
 end_ticks_before:int=Field(ge=0)
 end_ticks_after:int=Field(ge=0)
 begin_seconds_before:float=Field(ge=0)
 begin_seconds_after:float=Field(ge=0)
 end_seconds_before:float=Field(ge=0)
 end_seconds_after:float=Field(ge=0)
 host_start_monotonic_ns:int=Field(ge=0)
 host_completion_monotonic_ns:int=Field(gt=0)
 function_pass:bool
 guard_pass:bool
 cleanup_pass:bool
 source_refs:tuple[str,...]
 @model_validator(mode='after')
 def valid(self):
  values=(self.active_default_unit_seconds,self.active_resolution_seconds,self.begin_seconds_before,self.begin_seconds_after,self.end_seconds_before,self.end_seconds_after)
  if not all(math.isfinite(v) for v in values) or not self.boundary_id or not self.scene_id or not self.source_refs:raise ValueError('timebase observation invalid')
  if self.counter_end<=self.counter_begin or self.end_ticks_before<self.begin_ticks_after or self.host_completion_monotonic_ns<self.host_start_monotonic_ns:raise ValueError('timebase boundary order')
  for prefix in ('begin','end'):
   before=getattr(self,prefix+'_ticks_before');after=getattr(self,prefix+'_ticks_after')
   if after<before:raise ValueError('timebase bracket inverted')
   for suffix in ('before','after'):
    if not math.isclose(getattr(self,prefix+'_seconds_'+suffix),getattr(self,prefix+'_ticks_'+suffix)*self.active_resolution_seconds,rel_tol=1e-12,abs_tol=1e-18):raise ValueError('active tick/second mismatch')
  if self.active_default_unit_seconds<self.active_resolution_seconds:raise ValueError('unit smaller than resolution')
  return self
 def interval(self):
  return dict(counter_delta=self.counter_end-self.counter_begin,seconds_lower=self.end_seconds_before-self.begin_seconds_after,seconds_upper=self.end_seconds_after-self.begin_seconds_before,host_elapsed_ns=self.host_completion_monotonic_ns-self.host_start_monotonic_ns)

class TimebaseDomain(FrozenRecord):
 domain_id:str
 component_ids:tuple[str,...]
 operation:str
 profile:str
 boundary_id:str
 source_unit:Literal['tsim_counter_default_unit','reported_ns','reported_cycle','model_cycle','protocol_event']
 work_unit:str
 accepted_work_values:tuple[int,...]
 seconds_per_source_unit:float|None
 status:Literal['validated_simulation','unavailable','fixture_only','validated_physical']
 active_default_unit_seconds:float|None
 active_resolution_seconds:float|None
 model_library_sha256:str
 systemc_library_sha256:str
 calibration_scene_ids:tuple[str,...]
 validation_scene_ids:tuple[str,...]
 source_refs:tuple[str,...]
 applicability:str
 physical_clock_source_ref:str|None=None
 @model_validator(mode='after')
 def valid(self):
  if not self.domain_id or not self.component_ids or not self.boundary_id or not self.source_refs or not self.applicability:raise ValueError('timebase domain incomplete')
  if len(set(self.accepted_work_values))!=len(self.accepted_work_values) or any(v<=0 for v in self.accepted_work_values):raise ValueError('timebase work identity invalid')
  if set(self.calibration_scene_ids)&set(self.validation_scene_ids):raise ValueError('timebase calibration/validation overlap')
  validated=self.status in ('validated_simulation','validated_physical')
  if validated and (not self.calibration_scene_ids or not self.validation_scene_ids or self.seconds_per_source_unit is None or not self.accepted_work_values):raise ValueError('validated timebase needs disjoint evidence and exact work domain')
  if self.status=='unavailable' and self.seconds_per_source_unit is not None:raise ValueError('unavailable timebase cannot have conversion')
  for value in (self.seconds_per_source_unit,self.active_default_unit_seconds,self.active_resolution_seconds):
   if value is not None and (not math.isfinite(value) or value<=0):raise ValueError('timebase quantity invalid')
  if validated and (self.active_default_unit_seconds is None or self.active_resolution_seconds is None):raise ValueError('active unit evidence missing')
  if self.source_unit=='protocol_event' and self.seconds_per_source_unit is not None:raise ValueError('protocol event has no time conversion')
  if self.status=='validated_physical' and not self.physical_clock_source_ref:raise ValueError('physical timebase needs independently attested physical clock source')
  return self

class TimebaseBundle(FrozenRecord):
 schema_version:Literal[1]=1
 bundle_id:str
 fixture_only:bool
 domains:tuple[TimebaseDomain,...]
 source_refs:tuple[str,...]
 bundle_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if not self.bundle_id or not self.domains or not self.source_refs or len({d.domain_id for d in self.domains})!=len(self.domains):raise ValueError('timebase bundle incomplete/duplicate')
  if stable_sha256(self.model_dump(mode='json',exclude={'bundle_sha256'}))!=self.bundle_sha256:raise ValueError('timebase bundle hash mismatch')
  return self

def freeze_timebase_bundle(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(schema_version=1,**kwargs);raw=TypeAdapter(dict).dump_python(raw,mode='json');raw['bundle_sha256']=stable_sha256(raw)
 return TimebaseBundle.model_validate_json(json.dumps(raw))

def resolve_timebase(bundle,*,component_id,operation,profile,boundary_id,source_unit,work,model_library_sha256,systemc_library_sha256,physical=False):
 bundle=TimebaseBundle.model_validate_json(bundle.model_dump_json())
 matches=[d for d in bundle.domains if component_id in d.component_ids and d.operation==operation and d.profile==profile and d.boundary_id==boundary_id and d.source_unit==source_unit and work in d.accepted_work_values and d.model_library_sha256==model_library_sha256 and d.systemc_library_sha256==systemc_library_sha256 and d.status in ('validated_simulation','validated_physical') and (not physical or d.status=='validated_physical')]
 if physical and bundle.fixture_only:matches=[]
 if len(matches)!=1:raise ValueError('timebase requires one exact validated domain, found '+str(len(matches)))
 return matches[0]

def convert_time_value(domain,value,*,physical=False):
 domain=TimebaseDomain.model_validate_json(domain.model_dump_json())
 if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('time value must be finite and nonnegative')
 if domain.status not in ('validated_simulation','validated_physical') or domain.seconds_per_source_unit is None:raise ValueError('timebase conversion unavailable')
 if physical and domain.status!='validated_physical':raise ValueError('simulation mapping cannot publish physical time')
 return dict(raw_value=value,raw_unit=domain.source_unit,converted_seconds=value*domain.seconds_per_source_unit,status='simulation_seconds' if domain.status=='validated_simulation' else 'physical_seconds',domain_id=domain.domain_id,domain_sha256=stable_sha256(domain.model_dump(mode='json')),physical_publishable=domain.status=='validated_physical')

def model_timebase_gate(bundle,components,*,operation,profile,boundary_id,physical=True):
 missing=[]
 if bundle is None:return dict(status='unavailable',seconds_per_model_cycle=None,missing_fields=['validated active-unit timebase bundle and per-component model_cycle mapping'])
 for component in components:
  rows=[d for d in bundle.domains if component in d.component_ids and d.source_unit=='model_cycle' and d.operation==operation and d.profile==profile and d.boundary_id==boundary_id and d.status==('validated_physical' if physical else 'validated_simulation')]
  if len(rows)!=1:missing.append(component+'.model_cycle_timebase')
 if missing:return dict(status='unavailable',seconds_per_model_cycle=None,missing_fields=missing)
 rates={d.seconds_per_source_unit for d in bundle.domains if d.source_unit=='model_cycle' and d.component_ids and set(d.component_ids)&set(components)}
 if len(rates)!=1:return dict(status='unavailable',seconds_per_model_cycle=None,missing_fields=['cross-component model-cycle conflict'])
 return dict(status='physical' if physical else 'simulation_only',seconds_per_model_cycle=next(iter(rates)),missing_fields=[])
