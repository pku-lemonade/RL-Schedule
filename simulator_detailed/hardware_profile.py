"""Inspect hardware data without converting it into a synthetic architecture."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal, cast

from .configs.schemas.arch_config import ArchConfig
from .configs.schemas.hardware_profile import (
    Evidence,
    EvidenceStatus,
    HardwareProfileConfig,
    LiteralQuantity,
    OverrideRecord,
    ProfileModel,
    Unit,
    resolve_parameters,
)

SupportState = Literal["executable", "abstract", "represented_only", "unsupported"]
MANIFEST_VERSION = "hardware-profile-4"

# Implementation-owned: neither profile declarations nor policy names enable code.
_MANIFEST: dict[str, tuple[SupportState, str, str]] = {
    "profile_inspection": (
        "executable",
        "profile data",
        "Schema validation and inspection execute.",
    ),
    "physical_layout": (
        "represented_only",
        "profile data",
        "Tile identities and roles are descriptive.",
    ),
    "coordinate_mapping": (
        "represented_only",
        "profile data",
        "Raw and logical coordinate tables are descriptive.",
    ),
    "memory_inventory": (
        "represented_only",
        "profile data",
        "Unique capacities are physical inventory, not allocation budgets.",
    ),
    "profile_runtime_adapter": (
        "unsupported",
        "profile execution",
        "No hardware-profile-to-full-workload adapter exists; transport and memory replay are separate opt-in scopes.",
    ),
    "heterogeneous_topology": (
        "unsupported",
        "profile execution",
        "Heterogeneous full-workload construction is unsupported; transport constructs routers without compute services.",
    ),
    "worker_selection": (
        "unsupported",
        "profile execution",
        "Profile masks do not yet control the scheduler.",
    ),
    "multiple_fabrics": (
        "unsupported",
        "profile execution",
        "Profile fabrics require explicit transport or memory replay bindings; full-workload execution remains unsupported.",
    ),
    "memory_service": (
        "unsupported",
        "profile execution",
        "Full-workload memory integration is unsupported; opt-in memory replay has explicit aggregate service.",
    ),
    "shared_memory_service": (
        "unsupported",
        "profile execution",
        "Full-workload alias integration is unsupported; admitted memory replay shares capacity and service across aliases.",
    ),
    "compute_dataflow": (
        "unsupported",
        "profile execution",
        "Legacy full-profile compute remains unsupported; finite abstract compute requires separate workload admission.",
    ),
    "hardware_timing": (
        "unsupported",
        "profile execution",
        "Reference quantities are not calibrated runtime timing.",
    ),
    "topology:torus_2d": (
        "unsupported",
        "profile execution",
        "Full-workload torus execution is unsupported; transport and memory replays require validated bindings.",
    ),
    "routing:dimension_order_xy": (
        "unsupported",
        "profile execution",
        "Profile inspection is descriptive; XY execution requires an admitted transport or memory replay.",
    ),
    "routing:dimension_order_yx": (
        "unsupported",
        "profile execution",
        "Profile inspection is descriptive; YX execution requires an admitted transport or memory replay.",
    ),
    "torus_transport_binding": (
        "executable",
        "opt-in version-2 transport",
        "Compiler available; this inspection does not establish graph, endpoint, route or runtime admission.",
    ),
    "torus_unicast_transport": (
        "abstract",
        "opt-in version-2 transport",
        "Admitted replay executes bounded class/dateline lanes over shared links with explicit assumed timing.",
    ),
    "causal_response_fixtures": (
        "abstract",
        "opt-in version-2 transport",
        "Finite byte requests produce bounded causal responses; these are not NIU or memory transactions.",
    ),
    "directed_link_slowdown": (
        "abstract",
        "opt-in version-2 transport",
        "Admitted directed-link schedules scale launch-time serialization, spacing and propagation.",
    ),
    "memory_replay_binding": (
        "executable",
        "opt-in memory replay",
        "Strict graph/profile compiler and CLI available; profile inspection alone does not establish runtime admission.",
    ),
    "addressed_memory_transactions": (
        "abstract",
        "opt-in memory replay",
        "Admitted reads, posted/acknowledged writes, software segmentation and real headers execute on shared transport.",
    ),
    "aggregate_memory_service": (
        "abstract",
        "opt-in memory replay",
        "Aliases and local clients share bounded capacity/readiness and combined read/write service; no bank/channel fidelity.",
    ),
    "explicit_memory_ordering": (
        "abstract",
        "opt-in memory replay",
        "Producer dependencies and scoped fences execute with explicit event costs; no DFG, dynamic circular buffers or scalar executor.",
    ),
    "niu_transactions": (
        "unsupported",
        "profile execution",
        "Exact NIU commands/registers/counters, linked packets, atomics and hardware ordering are unsupported; memory replay uses explicit abstract transactions.",
    ),
    "abstract_compute_workload_v1": (
        "executable",
        "opt-in finite compute workload",
        ("CLI/runtime available only for admitted finite FC/matmul, explicit effective rates, addressed memory and bundled slots; "
         "inspection is not admission. No tensor values, kernels or calibrated silicon timing execute."),
    ),
}


class FeatureSupport(ProfileModel):
    feature_id: str
    state: SupportState
    scope: str
    reason: str
    required_for_execution: bool


class ResolvedParameter(ProfileModel):
    value: int | float
    unit: Unit
    clock_parameter: str | None
    evidence: Evidence
    dependency_statuses: dict[str, EvidenceStatus]
    overrides: list[OverrideRecord]
    usage: Literal["reference_only"] = "reference_only"


class InventoryCounts(ProfileModel):
    physical_tiles: int
    roles: dict[str, int]
    enabled_workers: int
    disabled_workers: int
    fabrics: int
    endpoints: int


class ResourceInventory(ProfileModel):
    capacity_bytes: int
    attachment_ids: list[str]


class MemoryInventory(ProfileModel):
    resources: dict[str, ResourceInventory]
    unique_dram_bytes: int
    physical_worker_l1_bytes: int
    enabled_worker_l1_bytes: int
    interpretation: Literal["physical_inventory_not_usable_or_pooled_memory"] = (
        "physical_inventory_not_usable_or_pooled_memory"
    )


class InspectionReport(ProfileModel):
    report_schema_version: Literal[1] = 1
    manifest_version: str = MANIFEST_VERSION
    profile_sha256: str
    profile: HardwareProfileConfig
    counts: InventoryCounts
    memory: MemoryInventory
    resolved_parameters: dict[str, ResolvedParameter]
    features: list[FeatureSupport]
    blockers: list[FeatureSupport]
    can_execute: Literal[False] = False
    silicon_timing: Literal["unvalidated"] = "unvalidated"
    evidence_verification: Literal[
        "metadata_only_no_source_fetch_or_measurement_reproduction"
    ] = "metadata_only_no_source_fetch_or_measurement_reproduction"


def normalize_profile(profile: HardwareProfileConfig) -> HardwareProfileConfig:
    """Revalidate and deep-copy nested data; normalize tables, not recipe/history order."""
    result = HardwareProfileConfig.model_validate(profile.model_dump(mode="json"))
    result.sources = dict(sorted(result.sources.items()))
    result.parameters = dict(sorted(result.parameters.items()))
    result.layout.tiles.sort(key=lambda tile: tile.tile_id)
    result.worker_selection.enabled_worker_ids.sort()
    result.worker_selection.logical_workers.sort(key=lambda worker: worker.worker_index)
    result.fabrics.sort(key=lambda fabric: fabric.fabric_id)
    for fabric in result.fabrics:
        fabric.coordinates = dict(sorted(fabric.coordinates.items()))
    result.memory.resources.sort(key=lambda resource: resource.resource_id)
    result.attachments.endpoints.sort(key=lambda endpoint: endpoint.endpoint_id)
    for endpoint in result.attachments.endpoints:
        endpoint.resource_ids.sort()
    result.requested_features.sort()
    records = [
        result.layout.evidence,
        result.worker_selection.evidence,
        result.memory.evidence,
        result.attachments.evidence,
    ]
    records.extend(fabric.evidence for fabric in result.fabrics)
    records.extend(
        tile.evidence for tile in result.layout.tiles if tile.evidence is not None
    )
    records.extend(
        resource.evidence
        for resource in result.memory.resources
        if resource.evidence is not None
    )
    records.extend(
        endpoint.evidence
        for endpoint in result.attachments.endpoints
        if endpoint.evidence is not None
    )
    for parameter in result.parameters.values():
        records.append(parameter.evidence)
        if isinstance(parameter, LiteralQuantity):
            records.extend(
                override.previous_evidence for override in parameter.overrides
            )
    for record in records:
        record.source_ids.sort()
    return result


def override_parameter(
    profile: HardwareProfileConfig,
    parameter_id: str,
    value: float,
    reason: str,
) -> HardwareProfileConfig:
    result = normalize_profile(profile)
    if parameter_id not in result.parameters:
        raise ValueError(f"unknown parameter: {parameter_id}")
    parameter = result.parameters[parameter_id]
    if not isinstance(parameter, LiteralQuantity):
        raise TypeError(
            f"cannot override derived parameter {parameter_id}; override its operands"
        )
    previous = OverrideRecord(
        previous_value=parameter.value,
        previous_evidence=parameter.evidence.model_copy(deep=True),
        reason=reason,
    )
    replacement = parameter.model_dump(mode="json")
    replacement["value"] = value
    replacement["evidence"] = Evidence(
        status="assumed",
        locator=f"override:{parameter_id}",
        conditions="User-supplied value; not calibrated by the inspector.",
        rationale=reason,
    ).model_dump(mode="json")
    replacement["overrides"] = [
        item.model_dump(mode="json") for item in [*parameter.overrides, previous]
    ]
    result.parameters[parameter_id] = LiteralQuantity.model_validate(replacement)
    return normalize_profile(result)


def _feature_support(profile: HardwareProfileConfig) -> list[FeatureSupport]:
    required = {
        "profile_runtime_adapter",
        "hardware_timing",
        *profile.requested_features,
    }
    if len({tile.role for tile in profile.layout.tiles}) > 1:
        required.add("heterogeneous_topology")
    workers = {tile.tile_id for tile in profile.layout.tiles if tile.role == "worker"}
    if workers != set(profile.worker_selection.enabled_worker_ids):
        required.add("worker_selection")
    if profile.worker_selection.enabled_worker_ids:
        required.add("compute_dataflow")
    if len(profile.fabrics) > 1:
        required.add("multiple_fabrics")
    if profile.memory.resources:
        required.add("memory_service")
    resource_tiles: dict[str, set[str]] = {}
    for endpoint in profile.attachments.endpoints:
        for resource_id in endpoint.resource_ids:
            resource_tiles.setdefault(resource_id, set()).add(endpoint.tile_id)
    if any(len(tiles) > 1 for tiles in resource_tiles.values()):
        required.add("shared_memory_service")
    for fabric in profile.fabrics:
        required.add(f"topology:{fabric.topology_policy}")
        required.add(f"routing:{fabric.routing_policy}")
    features: list[FeatureSupport] = []
    for feature_id in sorted(set(_MANIFEST) | required):
        state, scope, reason = _MANIFEST.get(
            feature_id,
            (
                "unsupported",
                "profile execution",
                "Unknown feature/policy; no executor is registered.",
            ),
        )
        features.append(
            FeatureSupport(
                feature_id=feature_id,
                state=state,
                scope=scope,
                reason=reason,
                required_for_execution=feature_id in required,
            )
        )
    return features


def inspect_profile(profile: HardwareProfileConfig) -> InspectionReport:
    effective = normalize_profile(profile)
    canonical = json.dumps(
        effective.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    values = resolve_parameters(effective.parameters)
    resolved: dict[str, ResolvedParameter] = {}
    dependency_cache: dict[str, dict[str, EvidenceStatus]] = {}

    def dependencies(key: str) -> dict[str, EvidenceStatus]:
        if key in dependency_cache:
            return dependency_cache[key]
        parameter = effective.parameters[key]
        refs = (
            [] if isinstance(parameter, LiteralQuantity) else list(parameter.operands)
        )
        if parameter.clock_parameter is not None:
            refs.append(parameter.clock_parameter)
        statuses: dict[str, EvidenceStatus] = {}
        for ref in refs:
            statuses[ref] = effective.parameters[ref].evidence.status
            statuses.update(dependencies(ref))
        dependency_cache[key] = dict(sorted(statuses.items()))
        return dependency_cache[key]

    for key, parameter in effective.parameters.items():
        resolved[key] = ResolvedParameter(
            value=values[key],
            unit=parameter.unit,
            clock_parameter=parameter.clock_parameter,
            evidence=parameter.evidence.model_copy(deep=True),
            dependency_statuses=dependencies(key),
            overrides=(
                [record.model_copy(deep=True) for record in parameter.overrides]
                if isinstance(parameter, LiteralQuantity)
                else []
            ),
        )
    resources: dict[str, ResourceInventory] = {}
    dram_bytes = physical_l1 = enabled_l1 = 0
    worker_ids = {
        tile.tile_id for tile in effective.layout.tiles if tile.role == "worker"
    }
    enabled_ids = set(effective.worker_selection.enabled_worker_ids)
    for resource in effective.memory.resources:
        capacity = int(values[resource.capacity_parameter])  # Validated integral bytes.
        resources[resource.resource_id] = ResourceInventory(
            capacity_bytes=capacity,
            attachment_ids=[
                endpoint.endpoint_id
                for endpoint in effective.attachments.endpoints
                if resource.resource_id in endpoint.resource_ids
            ],
        )
        if resource.kind == "dram":
            dram_bytes += capacity
        elif resource.owner_tile_id in worker_ids:
            physical_l1 += capacity
            if resource.owner_tile_id in enabled_ids:
                enabled_l1 += capacity
    features = _feature_support(effective)
    return InspectionReport(
        profile_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        profile=effective,
        counts=InventoryCounts(
            physical_tiles=len(effective.layout.tiles),
            roles=dict(
                sorted(Counter(tile.role for tile in effective.layout.tiles).items())
            ),
            enabled_workers=len(enabled_ids),
            disabled_workers=len(worker_ids - enabled_ids),
            fabrics=len(effective.fabrics),
            endpoints=len(effective.attachments.endpoints),
        ),
        memory=MemoryInventory(
            resources=resources,
            unique_dram_bytes=dram_bytes,
            physical_worker_l1_bytes=physical_l1,
            enabled_worker_l1_bytes=enabled_l1,
        ),
        resolved_parameters=resolved,
        features=features,
        blockers=[
            feature.model_copy(deep=True)
            for feature in features
            if feature.required_for_execution
            and feature.state not in {"executable", "abstract"}
        ],
    )


def load_architecture_document(path: str | Path) -> ArchConfig | HardwareProfileConfig:
    data: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        keys = cast(dict[str, object], data)
        if keys.get("kind") in {"canonical_topology", "topology_replay", "topology_replay_result"}:
            raise ValueError("topology documents require the topology inspection/replay entry point")
        if "kind" in keys or "schema_version" in keys:
            return HardwareProfileConfig.model_validate(data)
    return ArchConfig.model_validate(data)


class UnsupportedHardwareProfileError(ValueError):
    def __init__(self, profile_id: str, blockers: list[FeatureSupport]):
        self.profile_id = profile_id
        self.blockers = [blocker.model_copy(deep=True) for blocker in blockers]
        super().__init__(
            f"Hardware profile {profile_id!r} cannot execute: "
            + "; ".join(
                f"{blocker.feature_id}: {blocker.reason}" for blocker in blockers
            )
        )


def require_executable_architecture(document: object) -> ArchConfig:
    if isinstance(document, HardwareProfileConfig):
        profile = normalize_profile(document)
        blockers = [
            feature
            for feature in _feature_support(profile)
            if feature.required_for_execution
            and feature.state not in {"executable", "abstract"}
        ]
        raise UnsupportedHardwareProfileError(profile.profile_id, blockers)
    if not isinstance(document, ArchConfig):
        raise TypeError("expected ArchConfig or HardwareProfileConfig; topology replay uses a separate entry point")
    return document
