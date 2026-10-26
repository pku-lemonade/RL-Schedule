"""Evidence contract for composing accepted R10 component domains.

This contract never introduces a whole-R10 cost. A failed full-scene check
blocks publication but leaves every accepted component formula unchanged.
"""
from typing import Literal
import json
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .r10_stage import R10StageDomain

class R10FullCompositionEvidence(FrozenRecord):
    schema_version:Literal[1]=1
    status:Literal['VALIDATED_COMPONENT_COMPOSITION','FAILED_COMPONENT_COMPOSITION_GATE']
    profile:Literal['transformer-fused']='transformer-fused'
    stage:Literal['R10']='R10'
    work:dict
    component_domain_sha256:tuple[str,str,str]
    r10_stage_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    component_prediction_counter:tuple[int,int,int]
    actual_component_counter:tuple[int,int,int]
    component_error_counter:tuple[int,int,int]
    component_error_thresholds:tuple[int,int,int]
    predicted_total_counter:int=Field(gt=0)
    actual_total_counter:int=Field(gt=0)
    total_error_counter:int=Field(ge=0)
    total_error_threshold:int=Field(ge=0)
    composition_rule:Literal['zero + write_sum + post_tail_nonoverlapping_ownership']='zero + write_sum + post_tail_nonoverlapping_ownership'
    rdma_wait_overlaps_write:bool
    validation_scene_id:str|None
    validation_executed:bool
    black_box_cost_added:Literal[False]=False
    component_formula_changed:Literal[False]=False
    physical_publishable:Literal[False]=False
    source_refs:tuple[str,...]
    evidence_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if sum(self.component_prediction_counter)!=self.predicted_total_counter or self.total_error_counter!=abs(self.actual_total_counter-self.predicted_total_counter):raise ValueError('R10 full composition arithmetic mismatch')
        if self.component_error_counter!=tuple(abs(a-p) for a,p in zip(self.actual_component_counter,self.component_prediction_counter)):raise ValueError('R10 component error mismatch')
        passed=all(e<=t for e,t in zip(self.component_error_counter,self.component_error_thresholds)) and self.total_error_counter<=self.total_error_threshold
        if (self.status=='VALIDATED_COMPONENT_COMPOSITION')!=(passed and self.validation_executed):raise ValueError('R10 composition status/gate mismatch')
        if self.status=='FAILED_COMPONENT_COMPOSITION_GATE' and self.validation_executed:raise ValueError('failed calibration must not run validation')
        if not self.rdma_wait_overlaps_write or not self.source_refs:raise ValueError('R10 source overlap/evidence missing')
        if stable_sha256(self.model_dump(mode='json',exclude={'evidence_sha256'}))!=self.evidence_sha256:raise ValueError('R10 composition evidence hash drift')
        return self

def freeze_r10_full_composition(**kwargs):
    raw=dict(schema_version=1,profile='transformer-fused',stage='R10',composition_rule='zero + write_sum + post_tail_nonoverlapping_ownership',black_box_cost_added=False,component_formula_changed=False,physical_publishable=False,**kwargs);raw['evidence_sha256']=stable_sha256(raw);return R10FullCompositionEvidence.model_validate_json(json.dumps(raw))

def require_validated_r10_full_composition(evidence,stage):
    evidence=R10FullCompositionEvidence.model_validate_json(evidence.model_dump_json());stage=R10StageDomain.model_validate_json(stage.model_dump_json())
    hashes=tuple(d.domain_sha256 for d in (stage.zero,stage.write_sum,stage.post))
    if evidence.r10_stage_sha256!=stage.stage_sha256 or evidence.component_domain_sha256!=hashes:raise ValueError('R10 full composition component identity drift')
    if evidence.status!='VALIDATED_COMPONENT_COMPOSITION':raise ValueError('R10 full composition is not validated: '+evidence.status)
    return evidence

def annotate_r10_receipt(receipt,evidence,stage):
    evidence=R10FullCompositionEvidence.model_validate_json(evidence.model_dump_json());stage=R10StageDomain.model_validate_json(stage.model_dump_json());result=dict(receipt)
    actions={r['action_id']:r for r in receipt['actions']}
    names=('R10.zero_init.combined','R10.write_sum.combined','R10.reduce_done_broadcast_consumer.combined')
    durations=tuple(int(actions[n]['duration_cycles']) for n in names)
    if durations!=evidence.component_prediction_counter or len([r for r in receipt['actions'] if r['stage']=='R10'])!=4:raise ValueError('R10 receipt ownership or segment duration mismatch')
    if receipt.get('combined_internal_costs_separable') is not False:raise ValueError('R10 receipt must not add internal chain/sync/NoC costs')
    result['full_r10_composition_evidence_sha256']=evidence.evidence_sha256
    result['full_r10_composition_status']=evidence.status
    result['full_r10_composition_validated']=evidence.status=='VALIDATED_COMPONENT_COMPOSITION'
    result['whole_r10_black_box_cost']=None
    result['component_prediction_counter']=list(durations)
    result['component_prediction_total_counter']=sum(durations)
    return result
