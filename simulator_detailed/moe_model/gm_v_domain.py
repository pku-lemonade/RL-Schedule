"""Frozen M04 controller-boundary affines for exact accepted workload domains."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
class GMBoundaryAffine(FrozenRecord):
    boundary_id:str
    intercept_cycles:float
    per_unit_cycles:float
    parameter_path:str
class GMServiceDomainFormula(FrozenRecord):
    component:Literal['M04']='M04'
    family:str
    independent_variable:Literal['payload_bytes','contributors','V']
    model_revision:Literal['M04-gm-controller-domain-r2']='M04-gm-controller-domain-r2'
    calibration_values:tuple[int,...]
    boundaries:tuple[GMBoundaryAffine,...]
    source_refs:tuple[str,...]
    applicability:str
    formula_sha256:str
    @model_validator(mode='after')
    def integrity(self):
        if len(self.calibration_values)<2 or self.calibration_values!=tuple(sorted(set(self.calibration_values))):raise ValueError('GM calibration values invalid')
        if not self.boundaries or len({b.boundary_id for b in self.boundaries})!=len(self.boundaries) or not self.source_refs or not self.applicability:raise ValueError('GM formula incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('GM formula hash mismatch')
        return self
    def timing(self,value,boundary_id):
        import math
        if type(value) is not int or value<=0:raise ValueError('GM formula input invalid')
        b=next((x for x in self.boundaries if x.boundary_id==boundary_id),None)
        if b is None:raise ValueError('GM boundary absent')
        result=b.intercept_cycles+b.per_unit_cycles*value
        if result<0 or not math.isfinite(result):raise ValueError('GM formula outside nonnegative domain')
        return result
def freeze_gm_service_formula(**kwargs):
    values=dict(component='M04',model_revision='M04-gm-controller-domain-r2',**kwargs);shape=GMServiceDomainFormula.model_construct(**values,formula_sha256='0'*64).model_dump(mode='json',exclude={'formula_sha256'})
    return GMServiceDomainFormula(**values,formula_sha256=stable_sha256(shape))
__all__=['GMBoundaryAffine','GMServiceDomainFormula','freeze_gm_service_formula']
