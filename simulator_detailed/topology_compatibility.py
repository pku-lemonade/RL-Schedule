"""Dependency-free admission for the existing detailed mesh model contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .configs.schemas.arch_config import NoCConfig
from .topology import Topology, content_digest, topology_from_legacy

CONSUMERS = frozenset({"detailed_predictor", "detailed_encoder"})


def require_legacy_topology(topology: Topology, consumer: str,
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
