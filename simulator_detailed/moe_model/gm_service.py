"""M04 address/controller contract and explicit atomic service work."""
from collections import defaultdict
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord
from .component_service import ComponentAction

class GMTransaction(FrozenRecord):
    transaction_id: str
    source_pe: int = Field(ge=0,lt=32)
    token_id: int = Field(ge=0)
    output_shard: int = Field(ge=0,lt=8)
    address: int = Field(ge=0)
    payload_bytes: int = Field(gt=0)
    operation: Literal['read','write','write_sum']
    controller_id: str
    dependencies: tuple[str,...]=()
    @model_validator(mode='after')
    def shape(self):
        if self.address%4 or self.payload_bytes%4:raise ValueError('GM fp32 transaction alignment')
        if not self.controller_id:raise ValueError('GM controller must be bound explicitly')
        return self

class GMFormula(FrozenRecord):
    formula_id: str
    startup_cycles: float | None
    cycles_per_byte: float | None
    rmw_cycles_per_contribution: float | None
    status: Literal['calibrated','unavailable']
    calibration_sha256: str
    applicability: str
    source_refs: tuple[str,...]
    @model_validator(mode='after')
    def contract(self):
        import math
        values=(self.startup_cycles,self.cycles_per_byte,self.rmw_cycles_per_contribution)
        if self.status=='unavailable' and any(v is not None for v in values):raise ValueError('unavailable GM formula cannot assign cost')
        if self.status=='calibrated' and any(v is None or v<0 or not math.isfinite(v) for v in values):raise ValueError('invalid GM coefficients')
        if not self.source_refs:raise ValueError('GM formula source required')
        return self

    def service(self,transaction):
        if self.status!='calibrated':raise ValueError('GM component unavailable')
        return self.startup_cycles+self.cycles_per_byte*transaction.payload_bytes+(self.rmw_cycles_per_contribution if transaction.operation=='write_sum' else 0.0)

def conflict_groups(transactions):
    groups=defaultdict(list)
    for t in transactions:groups[(t.controller_id,t.address,t.payload_bytes)].append(t.transaction_id)
    return [dict(controller_id=k[0],address=k[1],payload_bytes=k[2],contributors=v,conflict_multiplicity=len(v)) for k,v in sorted(groups.items())]

def gm_actions(transactions,formula):
    """Controller resource FIFO covers actual arrivals; no histogram power law."""
    result=[]
    for t in transactions:
        result.append(ComponentAction(action_id=t.transaction_id,stage='gm-'+t.operation,component='M04',resource_id=t.controller_id,
            work=float(t.payload_bytes),work_unit='byte',duration_cycles=formula.service(t),duration_source=formula.formula_id,
            calibration_sha256=formula.calibration_sha256,dependencies=t.dependencies,bytes=t.payload_bytes,
            participants=(f'pe{t.source_pe}',t.controller_id),status='calibrated'))
    return tuple(result)
