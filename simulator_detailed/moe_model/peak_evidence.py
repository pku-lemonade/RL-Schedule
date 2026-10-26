"""Bounded peak evidence over the existing MFU PeakConvention schema."""
from typing import Literal
from pydantic import model_validator
import json,math
from .workload import FrozenRecord,stable_sha256
from .mfu import PeakConvention
class PeakEvidence(FrozenRecord):
 peak:PeakConvention
 source_commit:str
 source_path:str
 source_sha256:str
 precision:Literal['BF16','INT8','FP8','VectorFP32','VectorBF16','W4A8']
 flop_convention:Literal['useful_model','issued_padded','unbound']
 device_scope:Literal['per_device','unbound']
 profile_scope:tuple[str,...]
 status:Literal['source_candidate_unbound','authorized_selected','missing']
 authority_ref:str|None=None
 evidence_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if not self.source_path or not self.source_commit or not self.profile_scope:raise ValueError('peak evidence identity incomplete')
  if self.status=='authorized_selected' and (not self.authority_ref or self.flop_convention=='unbound' or self.device_scope=='unbound' or self.peak.peak_flops_per_second_per_device is None):raise ValueError('selected peak needs authority/value/FLOP/device scope')
  if self.status=='missing' and self.peak.peak_flops_per_second_per_device is not None:raise ValueError('missing peak cannot have value')
  if self.precision=='W4A8' and self.status=='authorized_selected' and self.peak.precision!='W4A8':raise ValueError('INT8 peak cannot stand in for W4A8')
  if stable_sha256(self.model_dump(mode='json',exclude={'evidence_sha256'}))!=self.evidence_sha256:raise ValueError('peak evidence hash drift')
  return self

def freeze_peak_evidence(**kwargs):
 from pydantic import TypeAdapter
 raw=TypeAdapter(dict).dump_python({'authority_ref':None,**kwargs},mode='json');raw['evidence_sha256']=stable_sha256(raw)
 return PeakEvidence.model_validate_json(json.dumps(raw))

def require_selected_peak(evidence,*,profile,precision,flop_convention):
 evidence=PeakEvidence.model_validate_json(evidence.model_dump_json())
 if evidence.status!='authorized_selected' or profile not in evidence.profile_scope or precision!=evidence.precision or flop_convention!=evidence.flop_convention:raise ValueError('MFU peak convention unavailable or outside scope')
 return evidence.peak
