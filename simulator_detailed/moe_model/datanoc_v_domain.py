"""Frozen C01/C02 packet-boundary formulas for exact logical route domains."""
from typing import Literal
from pydantic import model_validator
from .workload import FrozenRecord,stable_sha256
class DataNocBoundaryAffine(FrozenRecord):
    boundary_id:str
    intercept_cycles:float
    per_byte_cycles:float
    parameter_path:str
class DataNocDomainFormula(FrozenRecord):
    component:Literal['M01/M02']='M01/M02'
    family:str
    primitive:Literal['point_to_point','masked_multicast']
    direction:str
    model_revision:Literal['M01-M02-production-packet-r2']='M01-M02-production-packet-r2'
    calibration_payloads:tuple[int,...]
    boundaries:tuple[DataNocBoundaryAffine,...]
    source_refs:tuple[str,...]
    applicability:str
    formula_sha256:str
    @model_validator(mode='after')
    def integrity(self):
        if self.calibration_payloads!=tuple(sorted(set(self.calibration_payloads))) or len(self.calibration_payloads)<2:raise ValueError('NoC calibration payloads invalid')
        if not self.boundaries or len({b.boundary_id for b in self.boundaries})!=len(self.boundaries) or not self.source_refs:raise ValueError('NoC formula incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('NoC formula hash mismatch')
        return self
    def timing(self,payload_bytes,boundary_id):
        import math
        if type(payload_bytes) is not int or payload_bytes<=0:raise ValueError('NoC payload invalid')
        b=next((x for x in self.boundaries if x.boundary_id==boundary_id),None)
        if b is None:raise ValueError('NoC boundary absent')
        value=b.intercept_cycles+b.per_byte_cycles*payload_bytes
        if value<0 or not math.isfinite(value):raise ValueError('NoC formula outside domain')
        return value
def freeze_datanoc_formula(**kwargs):
    values=dict(component='M01/M02',model_revision='M01-M02-production-packet-r2',**kwargs);shape=DataNocDomainFormula.model_construct(**values,formula_sha256='0'*64).model_dump(mode='json',exclude={'formula_sha256'})
    return DataNocDomainFormula(**values,formula_sha256=stable_sha256(shape))
__all__=['DataNocBoundaryAffine','DataNocDomainFormula','freeze_datanoc_formula']
