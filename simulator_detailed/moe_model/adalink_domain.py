"""Exact C08 ACI WQE boundaries, never a full-allreduce cost."""
from typing import Literal
from pydantic import Field,model_validator
import math,json
from .workload import FrozenRecord,stable_sha256
class AdalinkWqeFormula(FrozenRecord):
 revision:Literal['M08-aci-wqe-local-domain-r1']='M08-aci-wqe-local-domain-r1'
 rank_count:int=Field(ge=2)
 rank:int=Field(ge=0)
 port_node:int=Field(ge=0)
 operation:Literal['write','write_sum']
 payload_anchors:tuple[int,...]
 startup:float=Field(ge=0)
 service_per_byte:float=Field(ge=0)
 unit:Literal['reported_ns']='reported_ns'
 source_refs:tuple[str,...]
 formula_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if self.rank>=self.rank_count or not self.source_refs or len(self.payload_anchors)<2 or self.payload_anchors!=tuple(sorted(set(self.payload_anchors))):raise ValueError('ADALINK WQE formula domain invalid')
  if not math.isfinite(self.startup) or not math.isfinite(self.service_per_byte):raise ValueError('ADALINK formula nonfinite')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('ADALINK formula hash mismatch')
  return self
 def duration(self,byte_count):
  if type(byte_count) is not int or byte_count<=0:raise ValueError('WQE payload invalid')
  return self.startup+self.service_per_byte*byte_count

def freeze_adalink_wqe_formula(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(revision='M08-aci-wqe-local-domain-r1',unit='reported_ns',**kwargs);raw=TypeAdapter(dict).dump_python(raw,mode='json');raw['formula_sha256']=stable_sha256(raw)
 return AdalinkWqeFormula.model_validate_json(json.dumps(raw))
