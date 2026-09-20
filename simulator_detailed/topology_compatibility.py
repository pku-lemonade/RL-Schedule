"""Dependency-free admission for the existing detailed mesh model contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .configs.schemas.arch_config import NoCConfig
from .noc import NoC
from .topology import Topology, content_digest, topology_from_legacy
from .utils.definitions import NoCChannel

CONSUMERS = frozenset({"detailed_predictor", "detailed_encoder"})
VALIDATION_KINDS = frozenset({
    "validation_suite", "validation_reference", "validation_report", "calibration_plan", "calibration_result",
    "external_validation_campaign", "external_capture_bundle", "external_validation_report",
})
NONLEGACY_KINDS = frozenset({"multicast_sync_workload", "multicast_sync_result", "multicast_sync_plan",
                            "multicast_sync_execution_result", "multicast_pipeline_workload", "multicast_pipeline_result",
                            "multicast_memory_result", "multicast_scalar_result"})


def reject_validation_document(value: object) -> None:
    kind = cast(Mapping[str, object], value).get("kind") if isinstance(value, Mapping) else getattr(value, "kind", None)
    if isinstance(kind, str) and kind in VALIDATION_KINDS:
        raise TypeError(f"{kind} is validation evidence, not a legacy topology or event stream")
    if isinstance(kind, str) and kind in NONLEGACY_KINDS:
        raise TypeError(f"{kind} is a finite replay document, not a legacy topology or event stream")


def require_legacy_topology(topology: object, consumer: str,
                            event_format: str = "legacy_events") -> NoCConfig:
    if consumer not in CONSUMERS:
        raise ValueError(f"unknown topology consumer {consumer!r}")
    if event_format != "legacy_events":
        raise ValueError(f"{consumer} requires legacy_events; unsupported format {event_format!r}")
    if not isinstance(topology, Topology):
        raise TypeError("consumer admission requires a compiled canonical Topology")
    graph = Topology.compile(topology.graph).graph
    origin = graph.origin
    if origin.kind != "legacy_mesh" or origin.document_json is None:
        raise ValueError(f"{consumer} requires verified legacy mesh semantics; heterogeneous/profile topology is unsupported")
    config = NoCConfig.model_validate_json(origin.document_json)
    if config.type != "Mesh" or config.router.type != "XY":
        raise ValueError(f"{consumer} requires a legacy XY mesh")
    if origin.content_hash != content_digest(config.model_dump(mode="json")):
        raise ValueError("legacy topology source hash does not match")
    expected = topology_from_legacy(config)
    if (graph != expected.graph or topology.content_hash != expected.content_hash
            or topology.router_indices != expected.router_indices
            or topology.link_indices != expected.link_indices or topology.port_indices != expected.port_indices):
        raise ValueError(f"{consumer} topology differs from its declared legacy mesh contract")
    return config


def legacy_event_rows(document: object, stream: str) -> list[dict[str, object]]:
    """Accept existing trace wrappers/lists, reject versioned graph/replay events."""
    reject_validation_document(document)
    if stream not in {"compute", "communication"}:
        raise ValueError(f"unknown legacy event stream {stream!r}")
    if isinstance(document, Mapping):
        wrapper = cast(Mapping[str, object], document)
        if "kind" in wrapper or "schema_version" in wrapper:
            raise ValueError("versioned topology/replay documents are not legacy event streams")
        document = wrapper.get("trace", [])
    if not isinstance(document, list):
        raise TypeError("legacy events require a list or a trace wrapper")
    rows: list[dict[str, object]] = []
    required = {"start_time", "end_time"} | ({"pe_id"} if stream == "compute" else {"src_id", "dst_id"})
    for item in cast(list[object], document):
        if not isinstance(item, Mapping):
            raise TypeError("legacy event must be a record")
        row = cast(Mapping[str, object], item)
        if "kind" in row or "schema_version" in row or "time_aci_cycles" in row or not required <= row.keys():
            raise ValueError(f"unsupported {stream} event format; expected legacy_events")
        rows.append(dict(row))
    return rows


def legacy_coordinates(topology: Topology) -> dict[tuple[int, int], tuple[int, int]]:
    """Coordinate lookup after the consumer has verified its legacy contract."""
    coordinates: dict[tuple[int, int], tuple[int, int]] = {}
    for router in topology.graph.routers:
        if router.coordinate is None:
            raise ValueError("legacy coordinate is missing")
        coordinates[(router.fabric_id, topology.router_indices[router.key])] = (router.coordinate.x, router.coordinate.y)
    return coordinates


def require_legacy_nocs(value: object) -> tuple[dict[NoCChannel, NoC], Topology]:
    """Validate actual encoder runtime objects before optional tensor operations."""
    reject_validation_document(value)
    if not isinstance(value, Mapping) or not value:
        raise TypeError("detailed encoder requires a nonempty mapping of legacy NoC instances")
    nocs: dict[NoCChannel, NoC] = {}
    for fabric, noc in cast(Mapping[object, object], value).items():
        if not isinstance(fabric, NoCChannel) or not isinstance(noc, NoC):
            raise TypeError("detailed encoder requires legacy NoC instances; topology/replay documents are unsupported")
        if noc.fabric_id is not fabric:
            raise ValueError("encoder fabric key does not match its NoC")
        nocs[fabric] = noc
    topology = next(iter(nocs.values())).topology
    config = require_legacy_topology(topology, "detailed_encoder")
    if set(nocs) != set(config.fabric_ids):
        raise ValueError("hardware graph requires exactly the configured fabrics")
    for fabric, noc in nocs.items():
        require_legacy_topology(noc.topology, "detailed_encoder")
        if noc.topology.content_hash != topology.content_hash:
            raise ValueError("hardware graph fabrics must share one canonical topology")
        expected_routers = sorted(index for (f, _), index in topology.router_indices.items() if f == fabric)
        if [r.id for r in noc.routers] != expected_routers:
            raise ValueError("encoder runtime routers differ from canonical topology order")
        expected_links = [
            (topology.link_indices[e.key], topology.router_indices[(int(fabric), e.src_router)],
             topology.router_indices[(int(fabric), e.dst_router)])
            for e in sorted((e for e in topology.graph.links if e.fabric_id == fabric),
                            key=lambda e: topology.link_indices[e.key])
        ]
        if [(l.identity.link_id, l.identity.src_router, l.identity.dst_router) for l in noc.r2r_links] != expected_links:
            raise ValueError("encoder runtime links differ from canonical topology order")
    return nocs, topology
