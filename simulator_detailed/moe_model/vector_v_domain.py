"""Calibration-anchor Vector timing for exact validated V subdomains."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256

VectorEngine=Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']


class VectorVAnchorSlice(FrozenRecord):
    engine: VectorEngine
    start_cycles: tuple[float,...]
    duration_cycles: tuple[float,...]
    @model_validator(mode='after')
    def valid(self):
        if not self.start_cycles or len(self.start_cycles)!=len(self.duration_cycles) or any(x<0 for x in (*self.start_cycles,*self.duration_cycles)):raise ValueError('Vector V anchor slice invalid')
        return self


class VectorVAnchor(FrozenRecord):
    V: int = Field(gt=0)
    slices: tuple[VectorVAnchorSlice,...]
    @model_validator(mode='after')
    def valid(self):
        if {x.engine for x in self.slices}!={'VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'}:raise ValueError('Vector V anchor engines incomplete')
        return self


class VectorVDomainFormula(FrozenRecord):
    component: Literal['M06']='M06'
    profile: Literal['standalone','transformer-fused']
    operation: Literal['rmsnorm','quant','softmax_topk','table','silu_score_quant']
    model_revision: Literal['M06-vector-v-anchor-r5']='M06-vector-v-anchor-r5'
    events_per_item: int = Field(ge=0)
    anchors: tuple[VectorVAnchor,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    formula_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        values=tuple(x.V for x in self.anchors)
        if len(values)<2 or values!=tuple(sorted(values)) or len(set(values))!=len(values):raise ValueError('Vector V anchors invalid')
        expected=lambda v:1 if self.events_per_item==0 else v*self.events_per_item
        if any(any(len(s.start_cycles)!=expected(a.V) for s in a.slices) for a in self.anchors):raise ValueError('Vector V anchor event count mismatch')
        if not self.source_refs or not self.applicability:raise ValueError('Vector V evidence/applicability required')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('Vector V formula hash mismatch')
        return self
    def timing(self,V,event_index,engine):
        if type(V) is not int or type(event_index) is not int or V<=0 or event_index<0:raise ValueError('Vector V timing request invalid')
        exact=next((a for a in self.anchors if a.V==V),None)
        if exact is not None:
            row=next(s for s in exact.slices if s.engine==engine)
            if event_index>=len(row.start_cycles):raise ValueError('Vector event index outside anchor')
            return row.start_cycles[event_index],row.duration_cycles[event_index]
        lower=[a for a in self.anchors if a.V<V];upper=[a for a in self.anchors if a.V>V]
        pair=(max(lower,key=lambda a:a.V),min(upper,key=lambda a:a.V)) if lower and upper else tuple(self.anchors[:2] if not lower else self.anchors[-2:])
        left,right=pair;ls=next(s for s in left.slices if s.engine==engine);rs=next(s for s in right.slices if s.engine==engine)
        if event_index>=len(rs.start_cycles) and event_index>=len(ls.start_cycles):raise ValueError('Vector event index outside interpolation anchors')
        if event_index>=len(ls.start_cycles):return rs.start_cycles[event_index],rs.duration_cycles[event_index]
        if event_index>=len(rs.start_cycles):return ls.start_cycles[event_index],ls.duration_cycles[event_index]
        ratio=(V-left.V)/(right.V-left.V);start=ls.start_cycles[event_index]+ratio*(rs.start_cycles[event_index]-ls.start_cycles[event_index]);duration=ls.duration_cycles[event_index]+ratio*(rs.duration_cycles[event_index]-ls.duration_cycles[event_index])
        return start,duration


def freeze_vector_v_formula(**kwargs):
    from pydantic import TypeAdapter
    values=dict(component='M06',model_revision='M06-vector-v-anchor-r5',**kwargs);shape=TypeAdapter(dict).dump_python(values,mode='json')
    return VectorVDomainFormula(**values,formula_sha256=stable_sha256(shape))


__all__=['VectorVAnchor','VectorVAnchorSlice','VectorVDomainFormula','freeze_vector_v_formula']
