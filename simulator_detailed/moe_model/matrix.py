"""M05 named Matrix work, precision and Weight-SRAM gather contracts."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES
from .component_service import ComponentAction
class MatrixWork(FrozenRecord):
    work_id: str
    profile: Literal['standalone','transformer-fused']
    operation: Literal['router','gate_up','down']
    V: int = Field(ge=0,le=256)
    pe_id: int = Field(ge=0,lt=32)
    expert_sequence: tuple[int,...]=()
    @model_validator(mode='after')
    def routes(self):
        if self.operation!='router' and len(self.expert_sequence)!=self.V:raise ValueError('Matrix expert order must cover V')
        if any(e<0 or e>=PROFILES[self.profile].experts_per_rank for e in self.expert_sequence):raise ValueError('expert id outside rank')
        return self
    def shape(self):
        p=PROFILES[self.profile]
        if self.operation=='router':return (8,384,p.hidden_gating//8)
        return p.gate_up_per_pe if self.operation=='gate_up' else p.down_per_pe
    def issued_flops(self):
        m,n,k=self.shape();return 2*m*n*k*(1 if self.operation=='router' else self.V)
    def precision(self):return 'BF16' if self.operation=='router' else 'W4A8-group256'
    def weight_gather(self):return dict(medium='Weight SRAM',expert_sequence=self.expert_sequence,group_size=256,data_noc=False)
