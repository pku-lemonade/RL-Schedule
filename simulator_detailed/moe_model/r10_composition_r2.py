"""Mechanistic R10 composition from accepted component and named-event slices."""
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .r10_stage import R10StageDomain

class R10CompositionRuleR2(FrozenRecord):
    revision:Literal['R10-context-overlap-r2']='R10-context-overlap-r2'
    source_unit:Literal['tsim_counter_default_unit']='tsim_counter_default_unit'
    zero_handoff_offset:int=Field(ge=0)
    zero_component_duration:int=Field(gt=0)
    write_engine_ready_offset:int=Field(ge=0)
    write_channel_service_span:int=Field(gt=0)
    write_visibility_tail:int=Field(ge=0)
    write_component_duration:int=Field(gt=0)
    isolated_post_prebroadcast:int=Field(ge=0)
    isolated_broadcast_consumer_tail:int=Field(ge=0)
    persistent_wait_wakeup:int=Field(ge=0)
    context_broadcast_consumer_tail:int=Field(ge=0)
    post_component_duration:int=Field(gt=0)
    source_calls:tuple[str,...]
    component_domain_sha256:tuple[str,str,str]
    provider_identity:dict
    source_refs:tuple[str,...]
    rule_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if self.zero_handoff_offset>self.zero_component_duration:raise ValueError('zero handoff after terminal')
        if self.write_engine_ready_offset+self.write_channel_service_span+self.write_visibility_tail!=self.write_component_duration:raise ValueError('write event decomposition differs from frozen duration')
        if self.isolated_post_prebroadcast+self.isolated_broadcast_consumer_tail!=self.post_component_duration:raise ValueError('post event decomposition differs from frozen duration')
        if not self.source_calls or not self.source_refs or not self.provider_identity:raise ValueError('R10 R2 identity incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'rule_sha256'}))!=self.rule_sha256:raise ValueError('R10 R2 rule hash drift')
        return self
    def schedule(self,zero_duration=None,write_duration=None,post_duration=None):
        z=self.zero_component_duration if zero_duration is None else zero_duration;w=self.write_component_duration if write_duration is None else write_duration;p=self.post_component_duration if post_duration is None else post_duration
        if any(isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (z,w,p)):raise ValueError('component durations must be finite positive')
        z_delta=z-self.zero_component_duration;w_delta=w-self.write_component_duration;p_delta=p-self.post_component_duration
        zero_handoff=max(0,self.zero_handoff_offset+z_delta);zero_terminal=z
        first_write_service=max(zero_handoff,self.write_engine_ready_offset)
        write_service_end=first_write_service+self.write_channel_service_span+w_delta
        write_visibility_end=write_service_end+self.write_visibility_tail
        if write_service_end<first_write_service or write_visibility_end<write_service_end:raise ValueError('component mutation invalidates R10 ordering')
        post_context=max(0,self.persistent_wait_wakeup+self.context_broadcast_consumer_tail+p_delta)
        post_terminal=write_service_end+post_context
        terminal=max(zero_terminal,write_visibility_end,post_terminal)
        return dict(stage_entry=0,zero_handoff=zero_handoff,zero_terminal=zero_terminal,write_engine_ready=self.write_engine_ready_offset,write_first_service=first_write_service,write_service_end=write_service_end,write_visibility_end=write_visibility_end,persistent_wait_wakeup=self.persistent_wait_wakeup,context_broadcast_consumer_tail=self.context_broadcast_consumer_tail,post_terminal=post_terminal,full_terminal=terminal,overlaps=dict(zero_visibility_with_write=first_write_service<zero_terminal,rdma_wait_with_zero_write=True,write_visibility_tail_with_post=write_visibility_end>write_service_end),component_durations=dict(zero=z,write_sum=w,post=p),black_box_cost=None)

class R10CompositionR2Evidence(FrozenRecord):
    rule:R10CompositionRuleR2
    status:Literal['CALIBRATION_PASS_VALIDATION_PENDING','VALIDATED','CALIBRATION_FAILED','VALIDATION_FAILED']
    calibration_scene_id:str
    validation_scene_id:str
    predicted_terminal:int=Field(gt=0)
    observed_terminal:int|None=Field(default=None,gt=0)
    error_counter:int|None=Field(default=None,ge=0)
    threshold_counter:int=Field(ge=0)
    validation_executed:bool
    black_box_cost_added:Literal[False]=False
    component_formula_changed:Literal[False]=False
    source_refs:tuple[str,...]
    evidence_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if self.predicted_terminal!=self.rule.schedule()['full_terminal']:raise ValueError('R10 R2 prediction differs from rule')
        if (self.observed_terminal is None)!=(self.error_counter is None):raise ValueError('R10 R2 observation/error pairing')
        if self.observed_terminal is not None and self.error_counter!=abs(self.observed_terminal-self.predicted_terminal):raise ValueError('R10 R2 error mismatch')
        if self.status=='VALIDATED' and (not self.validation_executed or self.error_counter is None or self.error_counter>self.threshold_counter):raise ValueError('R10 R2 validation status invalid')
        if self.status=='CALIBRATION_PASS_VALIDATION_PENDING' and self.validation_executed:raise ValueError('R10 R2 pending status invalid')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'evidence_sha256'}))!=self.evidence_sha256:raise ValueError('R10 R2 evidence hash drift')
        return self

def _freeze(cls,field,raw):raw=dict(raw);raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_r10_composition_rule_r2(**kwargs):return _freeze(R10CompositionRuleR2,'rule_sha256',dict(revision='R10-context-overlap-r2',source_unit='tsim_counter_default_unit',**kwargs))
def freeze_r10_composition_evidence_r2(**kwargs):return _freeze(R10CompositionR2Evidence,'evidence_sha256',kwargs)
def require_validated_r10_r2(evidence,stage):
    evidence=R10CompositionR2Evidence.model_validate_json(evidence.model_dump_json());stage=R10StageDomain.model_validate_json(stage.model_dump_json())
    if evidence.rule.component_domain_sha256!=tuple(d.domain_sha256 for d in (stage.zero,stage.write_sum,stage.post)):raise ValueError('R10 R2 component identity drift')
    if evidence.status!='VALIDATED':raise ValueError('R10 R2 composition not validated: '+evidence.status)
    return evidence
