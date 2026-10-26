"""Supplied physical binding for M01/M02; never derive routes from coordinates."""
from __future__ import annotations
from typing import Literal
import math
from pydantic import Field, model_validator
from .workload import FrozenRecord, stable_sha256
from .hardware import HardwareParameterSet

class EndpointBinding(FrozenRecord):
    role_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    kind: Literal['pe', 'gm_rdma', 'gm_wdma', 'gsram', 'external']

class ParameterBinding(FrozenRecord):
    parameter_path: str
    network_id: str
    link_ids: tuple[str, ...]
    effect: Literal['bandwidth'] = 'bandwidth'
    allowed_component: Literal['M01', 'M02']

class SuppliedBinding(FrozenRecord):
    binding_id: str
    fixture_only: bool
    graph: dict
    timing: tuple[dict, ...]
    endpoints: tuple[EndpointBinding, ...]
    parameters: tuple[ParameterBinding, ...]
    source_refs: tuple[str, ...]
    calibration_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def integrity(self):
        from ..configs.schemas.generic_graph import GenericSystemGraph
        # Generic validation supplies topology/ports/path continuity checks.
        import json
        graph = GenericSystemGraph.model_validate_json(json.dumps(self.graph))
        roles = [e.role_id for e in self.endpoints]
        if len(roles) != len(set(roles)) or not roles or not self.source_refs:
            raise ValueError('binding roles must be unique and sources explicit')
        physical = {x.endpoint_id for x in graph.dma_endpoints} | {x.unit_id for x in graph.execution_units}
        physical |= {x.endpoint_id for x in graph.memory_resources if x.endpoint_id}
        if any(e.endpoint_id not in physical for e in self.endpoints):
            raise ValueError('binding endpoint absent from supplied graph')
        links = {(x.network_id,x.link_id) for x in graph.links}
        paths = [p.parameter_path for p in self.parameters]
        if len(paths) != len(set(paths)):
            raise ValueError('duplicate parameter path binding')
        for p in self.parameters:
            if not p.link_ids or any((p.network_id,k) not in links for k in p.link_ids):
                raise ValueError('parameter targets unknown physical link')
            if not p.parameter_path.startswith(('data_noc.', 'gm.', 'gsram.')):
                raise ValueError('Phase 1 permits only NoC and ordinary GM bandwidth paths')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'})) != self.binding_sha256:
            raise ValueError('binding hash mismatch')
        return self

    def resolve(self, role):
        matches=[e.endpoint_id for e in self.endpoints if e.role_id==role]
        if len(matches)!=1:raise ValueError('unknown logical endpoint role: '+role)
        return matches[0]

    def effective_timing(self, hardware: HardwareParameterSet):
        import copy
        result=copy.deepcopy(list(self.timing))
        hp=hardware.by_path()
        for target in self.parameters:
            if target.parameter_path not in hp:raise ValueError('missing hardware parameter: '+target.parameter_path)
            q=hp[target.parameter_path].quantity
            if q.value is None:raise ValueError('unavailable bandwidth: '+target.parameter_path)
            if q.calibration_status!='calibrated' and not self.fixture_only:
                raise ValueError('production execution requires calibrated bandwidth')
            if q.unit!='byte/cycle' or q.value<=0:raise ValueError('bandwidth must be positive byte/cycle')
            network=next((n for n in result if n['network_id']==target.network_id),None)
            if network is None:raise ValueError('binding has no network timing')
            overrides={o['link_id']:o['timing'] for o in network.get('overrides',[])}
            for lid in target.link_ids:
                t=copy.deepcopy(overrides.get(lid,network['link']))
                if t.get('mode')!='latency_fifo':raise ValueError('M01/M02 require latency_fifo timing')
                # Existing runtime has an integer issue quantum. Record exact
                # requested/effective bandwidth in receipt; never alter payload.
                t['issue_cycles']=max(1,math.ceil(t['packet_bytes']/q.value))
                overrides[lid]=t
            network['overrides']=[{'link_id':k,'timing':v} for k,v in sorted(overrides.items())]
        return result

class LogicalFlow(FrozenRecord):
    flow_id: str
    source_role: str
    destination_roles: tuple[str,...]
    payload_bytes: int = Field(gt=0)
    primitive: Literal['point_to_point','masked_multicast']
    depends_on: tuple[str,...] = ()
    start_cycles: float = Field(default=0.0,ge=0)
    @model_validator(mode='after')
    def semantics(self):
        if not self.destination_roles or len(set(self.destination_roles))!=len(self.destination_roles):
            raise ValueError('destinations must be nonempty and unique')
        if self.primitive=='point_to_point' and len(self.destination_roles)!=1:
            raise ValueError('point-to-point requires one destination')
        if not math.isfinite(self.start_cycles):raise ValueError('nonfinite start')
        return self

class NetworkWorkload(FrozenRecord):
    workload_id: str
    fixture_only: bool
    flows: tuple[LogicalFlow,...]
    source_refs: tuple[str,...]
    workload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        known=set()
        for f in self.flows:
            if f.flow_id in known or not set(f.depends_on)<=known:
                raise ValueError('logical flow duplicate or noncausal dependency')
            known.add(f.flow_id)
        if not self.flows or not self.source_refs:raise ValueError('workload and sources required')
        if stable_sha256(self.model_dump(mode='json',exclude={'workload_sha256'}))!=self.workload_sha256:
            raise ValueError('network workload hash mismatch')
        return self

def freeze_binding(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(kwargs,mode='json')
    return SuppliedBinding(**kwargs,binding_sha256=stable_sha256(raw))

def freeze_network_workload(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(kwargs,mode='json')
    return NetworkWorkload(**kwargs,workload_sha256=stable_sha256(raw))
