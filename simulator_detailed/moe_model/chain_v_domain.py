"""M03 chain payload/backpressure timing over frozen per-boundary affines."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256

ChainVariant=Literal['column_reduce_broadcast','column_reduce_only','row_reduce_broadcast']
ChainScenario=Literal['isolated','controlled_overlap','sustained_backpressure']


class ChainBoundaryAffine(FrozenRecord):
    boundary_id: str
    intercept_cycles: float
    per_packet_cycles: float
    parameter_path: str
    @model_validator(mode='after')
    def finite(self):
        import math
        if not self.boundary_id or not self.parameter_path or not all(math.isfinite(x) for x in (self.intercept_cycles,self.per_packet_cycles)):
            raise ValueError('chain boundary affine invalid')
        return self


class ChainScenarioFormula(FrozenRecord):
    scenario_class: ChainScenario
    rounds: int = Field(gt=0)
    active_groups: int = Field(gt=0)
    observed_fifo_capacities: tuple[int,...]
    boundaries: tuple[ChainBoundaryAffine,...]
    @model_validator(mode='after')
    def complete(self):
        ids=[row.boundary_id for row in self.boundaries]
        if not self.observed_fifo_capacities or len(ids)!=len(set(ids)) or not ids:raise ValueError('chain scenario formula incomplete')
        return self


class ChainPayloadFormula(FrozenRecord):
    component: Literal['M03']='M03'
    variant: ChainVariant
    axis: Literal['column','row']
    model_revision: Literal['M03-chain-payload-backpressure-r2']='M03-chain-payload-backpressure-r2'
    packet_bytes: Literal[128]=128
    observed_fragment_bytes: Literal[64]=64
    calibration_payloads: tuple[int,...]
    scenarios: tuple[ChainScenarioFormula,...]
    source_refs: tuple[str,...]
    applicability: str
    formula_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if tuple(sorted(set(self.calibration_payloads)))!=self.calibration_payloads or len(self.calibration_payloads)<2 or any(x<=0 or x%128 for x in self.calibration_payloads):raise ValueError('chain calibration payloads invalid')
        if {row.scenario_class for row in self.scenarios}!={'isolated','controlled_overlap','sustained_backpressure'}:raise ValueError('chain scenario coverage incomplete')
        expected_axis='row' if self.variant.startswith('row_') else 'column'
        if self.axis!=expected_axis or not self.source_refs or not self.applicability:raise ValueError('chain identity/evidence invalid')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('chain formula hash mismatch')
        return self
    def timing(self,payload_bytes,scenario_class,boundary_id):
        import math
        if type(payload_bytes) is not int or payload_bytes<=0 or payload_bytes%128:raise ValueError('chain payload request invalid')
        scenario=next((row for row in self.scenarios if row.scenario_class==scenario_class),None)
        if scenario is None:raise ValueError('chain scenario outside formula')
        boundary=next((row for row in scenario.boundaries if row.boundary_id==boundary_id),None)
        if boundary is None:raise ValueError('chain boundary outside formula')
        packets=math.ceil(payload_bytes/self.packet_bytes);value=boundary.intercept_cycles+packets*boundary.per_packet_cycles
        if value<0 or not math.isfinite(value):raise ValueError('chain formula outside nonnegative domain')
        return value


def freeze_chain_payload_formula(**kwargs):
    values=dict(component='M03',model_revision='M03-chain-payload-backpressure-r2',**kwargs)
    shape=ChainPayloadFormula.model_construct(**values,formula_sha256='0'*64).model_dump(mode='json',exclude={'formula_sha256'})
    return ChainPayloadFormula(**values,formula_sha256=stable_sha256(shape))


__all__=['ChainBoundaryAffine','ChainPayloadFormula','ChainScenarioFormula','freeze_chain_payload_formula']
