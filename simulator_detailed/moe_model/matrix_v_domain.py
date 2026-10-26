"""Calibration-anchor Matrix timing for bounded high-V domains."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256


class MatrixVAnchorSlice(FrozenRecord):
    engine: Literal['MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE']
    start_cycles: tuple[float,...]
    duration_cycles: tuple[float,...]
    @model_validator(mode='after')
    def complete(self):
        if not self.start_cycles or len(self.start_cycles)!=len(self.duration_cycles) or any(value<0 for value in (*self.start_cycles,*self.duration_cycles)):
            raise ValueError('Matrix V anchor slice invalid')
        return self


class MatrixVAnchor(FrozenRecord):
    V: int = Field(gt=0)
    slices: tuple[MatrixVAnchorSlice,...]
    @model_validator(mode='after')
    def complete(self):
        expected={'MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE'}
        if {row.engine for row in self.slices}!=expected or any(len(row.start_cycles)!=self.V for row in self.slices):raise ValueError('Matrix V anchor engines/count invalid')
        return self


class MatrixVExtrapolationSlice(FrozenRecord):
    engine: Literal['MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE']
    prefix_start_slope_per_v: tuple[float,...]
    prefix_duration_slope_per_v: tuple[float,...]
    steady_interval_cycles: float = Field(gt=0)
    steady_duration_cycles: float = Field(ge=0)
    @model_validator(mode='after')
    def complete(self):
        if not self.prefix_start_slope_per_v or len(self.prefix_start_slope_per_v)!=len(self.prefix_duration_slope_per_v):raise ValueError('Matrix V extrapolation prefix invalid')
        return self


class MatrixVDomainFormula(FrozenRecord):
    component: Literal['M05']='M05'
    profile: Literal['standalone','transformer-fused']
    operation: Literal['router','gate_up','down']
    model_revision: Literal['M05-engine-v-anchor-steady-r4']='M05-engine-v-anchor-steady-r4'
    anchors: tuple[MatrixVAnchor,...]
    extrapolation: tuple[MatrixVExtrapolationSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    formula_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        values=tuple(row.V for row in self.anchors)
        expected={'MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE'}
        if values!=tuple(sorted(values)) or len(values)<2 or len(set(values))!=len(values):raise ValueError('Matrix V anchors must be unique/increasing')
        if {row.engine for row in self.extrapolation}!=expected:raise ValueError('Matrix V extrapolation engines incomplete')
        if any(len(row.prefix_start_slope_per_v)!=values[-1] for row in self.extrapolation):raise ValueError('Matrix V extrapolation prefix length mismatch')
        if not self.source_refs or not self.applicability:raise ValueError('Matrix V evidence/applicability required')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('Matrix V formula hash mismatch')
        return self

    def timing(self,V,item,engine):
        if type(V) is not int or type(item) is not int or V<=0 or item<0 or item>=V:raise ValueError('Matrix V timing request invalid')
        anchors={row.V:row for row in self.anchors}
        if V in anchors:
            row=next(value for value in anchors[V].slices if value.engine==engine);return row.start_cycles[item],row.duration_cycles[item]
        maximum=self.anchors[-1]
        if V<maximum.V:raise ValueError('unregistered interpolation V is outside accepted formula')
        base=next(value for value in maximum.slices if value.engine==engine);extra=V-maximum.V;model=next(value for value in self.extrapolation if value.engine==engine)
        if item<maximum.V:return (base.start_cycles[item]+extra*model.prefix_start_slope_per_v[item],base.duration_cycles[item]+extra*model.prefix_duration_slope_per_v[item])
        start=base.start_cycles[-1]+extra*model.prefix_start_slope_per_v[-1]+(item-maximum.V+1)*model.steady_interval_cycles
        return start,model.steady_duration_cycles


def freeze_matrix_v_formula(**kwargs):
    from pydantic import TypeAdapter
    values=dict(component='M05',model_revision='M05-engine-v-anchor-steady-r4',**kwargs);raw=TypeAdapter(dict).dump_python(values,mode='json')
    return MatrixVDomainFormula(**values,formula_sha256=stable_sha256(raw))


__all__=['MatrixVAnchor','MatrixVAnchorSlice','MatrixVDomainFormula','MatrixVExtrapolationSlice','freeze_matrix_v_formula']
