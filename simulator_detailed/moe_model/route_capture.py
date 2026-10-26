"""Strict import/capture at the post-top-k, pre/post EP-filter boundary."""
from typing import Literal
import math
from pydantic import Field,model_validator
from .workload import FrozenRecord,RouteTraceIdentity,RouteRecord,PROFILES,freeze_workload,FrozenMoeWorkload,stable_sha256

class TopKCapture(FrozenRecord):
    schema_version: int=1
    identity: RouteTraceIdentity
    expert_ids: tuple[tuple[int,...],...]
    scores: tuple[tuple[float,...],...]
    source_refs: tuple[str,...]
    fixture_only: bool
    capture_sha256: str
    @model_validator(mode='after')
    def shape(self):
        profile=PROFILES[self.identity.profile]
        if len(self.expert_ids)!=profile.token_count or len(self.scores)!=profile.token_count:raise ValueError('capture token count mismatch')
        if any(len(row)!=profile.top_k for row in self.expert_ids+self.scores):raise ValueError('capture top-k shape mismatch')
        if any(type(x) is not int or x<0 or x>=profile.global_experts for row in self.expert_ids for x in row):raise ValueError('capture expert id invalid')
        if any(not math.isfinite(x) or x<0 or x>1 for row in self.scores for x in row):raise ValueError('capture score invalid')
        if any(len(set(row))!=len(row) for row in self.expert_ids):raise ValueError('top-k experts must be unique per token')
        if not self.source_refs:raise ValueError('capture source refs required')
        if stable_sha256(self.model_dump(mode='json',exclude={'capture_sha256'}))!=self.capture_sha256:raise ValueError('capture hash mismatch')
        return self

def freeze_topk_capture(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(schema_version=1,**kwargs);frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return TopKCapture(**raw,capture_sha256=stable_sha256(frozen))

def capture_post_topk_tensors(identity,expert_ids,scores,*,source_refs,fixture_only):
    """Harness hook for tensors immediately after top-k and before EP filtering.

    Torch/NumPy inputs are converted with ``tolist``; no runtime is imported.
    """
    def rows(value):
        if hasattr(value,'detach'):value=value.detach().cpu()
        if hasattr(value,'tolist'):value=value.tolist()
        return tuple(tuple(x for x in row) for row in value)
    return freeze_topk_capture(identity=identity,expert_ids=rows(expert_ids),scores=rows(scores),source_refs=tuple(source_refs),fixture_only=fixture_only)

def capture_to_workload(capture,*,real_run):
    capture=TopKCapture.model_validate_json(capture.model_dump_json())
    if real_run and capture.fixture_only:raise ValueError('fixture capture cannot become real input')
    if not real_run and not capture.fixture_only:raise ValueError('real capture cannot be relabeled fixture')
    profile=PROFILES[capture.identity.profile];records=[];sequence=0
    for rank in range(profile.ep_size):
        first=rank*profile.experts_per_rank;last=first+profile.experts_per_rank
        for token in range(profile.token_count):
            for slot,(expert,score) in enumerate(zip(capture.expert_ids[token],capture.scores[token])):
                valid=first<=expert<last
                records.append(RouteRecord(sequence_index=sequence,token_id=token,topk_slot=slot,global_expert_id=expert,ep_rank=rank,
                  local_expert_id=expert-first if valid else None,score=score if valid else -1.0,status='valid' if valid else 'sentinel'));sequence+=1
    return freeze_workload(capture.identity,records,trace_kind='real_decode' if real_run else 'synthetic_fixture',trace_scope='ep_group',source_refs=capture.source_refs)

def import_frozen_route(document,*,require_real=True):
    route=FrozenMoeWorkload.model_validate_json(document if isinstance(document,str) else __import__('json').dumps(document))
    if require_real and route.trace_kind!='real_decode':raise ValueError('production route import requires real_decode')
    return route
