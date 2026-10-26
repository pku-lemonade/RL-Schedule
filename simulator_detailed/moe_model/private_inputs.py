"""Strict production input importers. Missing physical values stay missing."""
from __future__ import annotations
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,FrozenMoeWorkload,stable_sha256
from .mfu import DecodeBaselineManifest
from ..configs.schemas.system_spec import SystemSpec

class BoundRole(FrozenRecord):
    logical_role: str
    kind: Literal['pe','gm_rdma','gm_wdma','gsram']
    physical_id: str | None
    resource_id: str | None
    source_ref: str
    @model_validator(mode='after')
    def identity(self):
        if not self.logical_role or not self.source_ref:raise ValueError('role identity/source required')
        if (self.physical_id is None)!=(self.resource_id is None):raise ValueError('physical and resource identity bind together')
        return self

class ImportedMulticastTree(FrozenRecord):
    tree_id: str
    network_id: str
    source_role: str
    destination_roles: tuple[str,...]
    physical_link_ids: tuple[str,...]
    replication_nodes: tuple[str,...]
    source_ref: str
    @model_validator(mode='after')
    def tree(self):
        if not self.destination_roles or len(set(self.destination_roles))!=len(self.destination_roles):raise ValueError('multicast destinations invalid')
        if not self.tree_id or not self.network_id or not self.source_ref:raise ValueError('tree identity/source required')
        return self

class PhysicalBindingArtifact(FrozenRecord):
    schema_version: int=1
    binding_id: str
    origin: Literal['real_current_input','synthetic_fixture','partial_static_extraction']
    authorized: bool
    system_spec: dict | None
    roles: tuple[BoundRole,...]
    multicast_trees: tuple[ImportedMulticastTree,...]
    complete: bool
    missing_fields: tuple[str,...]
    source_refs: tuple[str,...]
    artifact_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if not self.binding_id or not self.source_refs:raise ValueError('binding identity/source required')
        logical=[x.logical_role for x in self.roles]
        if len(logical)!=len(set(logical)):raise ValueError('duplicate logical role')
        spec=None
        if self.system_spec is not None:spec=SystemSpec.model_validate_json(json.dumps(self.system_spec))
        if self.complete:
            if self.origin!='real_current_input' or not self.authorized or self.missing_fields or spec is None:raise ValueError('complete binding must be authorized real input')
            expected={*(f'PE{i}' for i in range(32)),*(f'GM_RDMA{i}' for i in range(4)),*(f'GM_WDMA{i}' for i in range(4)),'GSRAM'}
            if set(logical)!=expected or any(x.physical_id is None for x in self.roles):raise ValueError('complete binding role inventory mismatch')
            graph=spec.graph
            expected_kinds={**{f'PE{i}':'pe' for i in range(32)},**{f'GM_RDMA{i}':'gm_rdma' for i in range(4)},**{f'GM_WDMA{i}':'gm_wdma' for i in range(4)},'GSRAM':'gsram'}
            if any(x.kind!=expected_kinds[x.logical_role] for x in self.roles):raise ValueError('complete binding role kind mismatch')
            resources={x.endpoint_id for x in graph.dma_endpoints}|{x.unit_id for x in graph.execution_units}|{x.resource_id for x in graph.memory_resources}
            if any(x.resource_id not in resources for x in self.roles):raise ValueError('bound role resource absent from SystemSpec')
            if not graph.links or not graph.static_routes or not self.multicast_trees:raise ValueError('complete binding requires links, routes and multicast trees')
            if len(spec.batch.timing)!=len(graph.networks):raise ValueError('complete timing must cover networks')
            if not graph.memory_resources or any(x.capacity_bytes is None or x.hierarchy is None for x in graph.memory_resources):raise ValueError('complete GSRAM services require capacity and timing hierarchy')
            links={x.link_id for x in graph.links};roles=set(logical)
            for tree in self.multicast_trees:
                if tree.source_role not in roles or not set(tree.destination_roles)<=roles or not tree.physical_link_ids or not set(tree.physical_link_ids)<=links:
                    raise ValueError('multicast tree is not physically bound')
        elif not self.missing_fields:raise ValueError('incomplete binding must enumerate missing fields')
        if stable_sha256(self.model_dump(mode='json',exclude={'artifact_sha256'}))!=self.artifact_sha256:raise ValueError('binding artifact hash mismatch')
        return self
    @property
    def production_ready(self):return self.complete and self.origin=='real_current_input' and self.authorized

def freeze_physical_binding(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(schema_version=1,**kwargs);frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return PhysicalBindingArtifact(**raw,artifact_sha256=stable_sha256(frozen))

def merge_static_binding(static_facts,external=None):
    static_roles={x['logical_role']:x['kind'] for x in static_facts['roles']}
    if external is None:
        roles=tuple(BoundRole(logical_role=k,kind=v,physical_id=None,resource_id=None,source_ref='static:'+static_facts['static_facts_sha256']) for k,v in sorted(static_roles.items()))
        return freeze_physical_binding(binding_id='T81-static-partial',origin='partial_static_extraction',authorized=False,system_spec=None,roles=roles,multicast_trees=(),complete=False,
          missing_fields=('authorized real SystemSpec','physical endpoint/resource ids','physical links/routes','masked multicast trees','network timing','GSRAM capacity/hierarchy'),
          source_refs=tuple(x['path'] for x in static_facts['source_files']))
    artifact=PhysicalBindingArtifact.model_validate_json(json.dumps(external) if not isinstance(external,str) else external)
    for role in artifact.roles:
        if role.logical_role in static_roles and role.kind!=static_roles[role.logical_role]:raise ValueError('external role conflicts with fixed source role')
    if not set(static_roles)<=set(x.logical_role for x in artifact.roles):raise ValueError('external binding omits fixed source roles')
    return artifact

class AddressRangeRule(FrozenRecord):
    start: int = Field(ge=0)
    length: int = Field(gt=0)
    controller_id: str
    source_ref: str

class WriteSumController(FrozenRecord):
    controller_id: str
    address_ranges: tuple[AddressRangeRule,...]
    atomic_lane_count: int | None = Field(default=None,gt=0)
    ordinary_write_service_ref: str | None
    write_sum_service_ref: str | None
    same_address_arbitration: str | None
    completion_semantics: str | None
    source_refs: tuple[str,...]

class WriteSumMappingArtifact(FrozenRecord):
    schema_version: int=1
    mapping_id: str
    origin: Literal['real_current_input','synthetic_fixture','partial_static_extraction']
    controllers: tuple[WriteSumController,...]
    complete: bool
    missing_fields: tuple[str,...]
    source_refs: tuple[str,...]
    artifact_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        ids=[x.controller_id for x in self.controllers]
        if len(ids)!=len(set(ids)) or not self.source_refs:raise ValueError('write-sum identity/source invalid')
        if self.complete:
            if self.origin!='real_current_input' or set(ids)!={f'GM_WDMA{i}' for i in range(4)} or self.missing_fields:raise ValueError('complete write-sum requires four real controllers')
            for c in self.controllers:
                if not c.address_ranges or None in (c.atomic_lane_count,c.ordinary_write_service_ref,c.write_sum_service_ref,c.same_address_arbitration,c.completion_semantics):raise ValueError('write-sum controller incomplete')
                if any(r.controller_id!=c.controller_id for r in c.address_ranges):raise ValueError('address rule controller mismatch')
            ranges=sorted((r.start,r.start+r.length,r.controller_id) for c in self.controllers for r in c.address_ranges)
            if any(a[1]>b[0] for a,b in zip(ranges,ranges[1:])):raise ValueError('write-sum address rules overlap')
        elif not self.missing_fields:raise ValueError('incomplete write-sum mapping must enumerate missing fields')
        if stable_sha256(self.model_dump(mode='json',exclude={'artifact_sha256'}))!=self.artifact_sha256:raise ValueError('write-sum artifact hash mismatch')
        return self

def freeze_write_sum_mapping(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(schema_version=1,**kwargs);frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return WriteSumMappingArtifact(**raw,artifact_sha256=stable_sha256(frozen))

class AdalinkRank(FrozenRecord):
    rank: int = Field(ge=0)
    device_id: str
    adalink_node_ids: tuple[str,...]
    source_ref: str

class AdalinkLink(FrozenRecord):
    link_id: str
    source_rank: int = Field(ge=0)
    destination_rank: int = Field(ge=0)
    source_port: str
    destination_port: str
    bandwidth_value: float | None = Field(default=None,gt=0)
    bandwidth_unit: str | None
    latency_value: float | None = Field(default=None,ge=0)
    latency_unit: str | None
    source_ref: str

class AdalinkChunk(FrozenRecord):
    chunk_id: str
    phase: Literal['reduce_scatter','barrier','all_gather']
    source_rank: int = Field(ge=0)
    destination_rank: int = Field(ge=0)
    link_ids: tuple[str,...]
    offset_bytes: int = Field(ge=0)
    length_bytes: int = Field(gt=0)

class AdalinkBindingArtifact(FrozenRecord):
    schema_version: int=1
    binding_id: str
    origin: Literal['real_current_input','synthetic_fixture','partial_static_extraction']
    profile: Literal['standalone','transformer-fused']
    ccl_config_source: str | None
    ranks: tuple[AdalinkRank,...]
    links: tuple[AdalinkLink,...]
    chunks: tuple[AdalinkChunk,...]
    complete: bool
    missing_fields: tuple[str,...]
    source_refs: tuple[str,...]
    artifact_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if not self.source_refs:raise ValueError('ADALINK sources required')
        expected=8 if self.profile=='standalone' else 4;rank_ids=[x.rank for x in self.ranks];link_ids={x.link_id for x in self.links}
        if len(rank_ids)!=len(set(rank_ids)):raise ValueError('duplicate ADALINK rank')
        if self.complete:
            if self.origin!='real_current_input' or rank_ids!=list(range(expected)) or not self.links or not self.chunks or not self.ccl_config_source or self.missing_fields:raise ValueError('complete ADALINK binding invalid')
            if any(None in (x.bandwidth_value,x.bandwidth_unit,x.latency_value,x.latency_unit) for x in self.links):raise ValueError('ADALINK link parameters missing')
            if any(x.source_rank not in rank_ids or x.destination_rank not in rank_ids or x.source_rank==x.destination_rank for x in self.links):raise ValueError('ADALINK link rank invalid')
            if any(not x.link_ids or not set(x.link_ids)<=link_ids for x in self.chunks):raise ValueError('ADALINK chunk route invalid')
            if {x.phase for x in self.chunks}!={'reduce_scatter','barrier','all_gather'}:raise ValueError('ADALINK collective phases incomplete')
        elif not self.missing_fields:raise ValueError('incomplete ADALINK binding must enumerate missing fields')
        if stable_sha256(self.model_dump(mode='json',exclude={'artifact_sha256'}))!=self.artifact_sha256:raise ValueError('ADALINK artifact hash mismatch')
        return self

def freeze_adalink_binding(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(schema_version=1,**kwargs);frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return AdalinkBindingArtifact(**raw,artifact_sha256=stable_sha256(frozen))

def import_decode_baseline(document,*,require_complete=True):
    baseline=DecodeBaselineManifest.model_validate_json(document if isinstance(document,str) else json.dumps(document))
    if require_complete and baseline.completeness!='complete':raise ValueError('full decode baseline incomplete')
    return baseline

def phase3_production_gate(route,binding,write_sum,baseline,adalink=None,*,include_adalink=False):
    missing=[]
    if route is None or route.trace_kind!='real_decode':missing.append('real_decode_route_trace')
    if binding is None or not binding.production_ready:missing.append('complete_physical_binding')
    if write_sum is None or not write_sum.complete or write_sum.origin!='real_current_input':missing.append('production_write_sum_mapping')
    if baseline is None or baseline.completeness!='complete':missing.append('full_decode_baseline_and_peak')
    if include_adalink and (adalink is None or not adalink.complete or adalink.origin!='real_current_input'):missing.append('adalink_binding')
    return dict(status='READY_PHASE3_REAL_CLOSURE' if not missing else 'BLOCKED_PHASE3_REAL_INPUTS',ready=not missing,missing_fields=missing,
      workload_sha256=None if route is None else route.workload_sha256,binding_sha256=None if binding is None else binding.artifact_sha256)
