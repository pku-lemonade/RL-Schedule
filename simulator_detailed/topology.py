"""Normalization, immutable index maps, and inventory export for directed graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .configs.schemas.topology import CanonicalTopology

RouterKey = tuple[int, str]
LinkKey = tuple[int, str]
PortKey = tuple[int, str, str]


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def normalize_topology(graph: CanonicalTopology) -> CanonicalTopology:
    """Revalidate even model_copy/construct callers, then order semantic records."""
    graph = CanonicalTopology.model_validate(graph.model_dump(mode="json"))
    return graph.model_copy(update={
        "tiles": tuple(sorted(graph.tiles, key=lambda t: t.tile_id)),
        "fabrics": tuple(sorted(graph.fabrics, key=lambda f: f.fabric_id)),
        "routers": tuple(r.model_copy(update={
            "ports": tuple(sorted(r.ports, key=lambda p: p.port_id))
        }) for r in sorted(graph.routers, key=lambda r: r.key)),
        "links": tuple(sorted(graph.links, key=lambda l: l.key)),
        "attachments": tuple(a.model_copy(update={"resource_ids": tuple(sorted(a.resource_ids))})
                             for a in sorted(graph.attachments, key=lambda a: a.endpoint_id)),
        "enabled_worker_ids": tuple(sorted(graph.enabled_worker_ids)),
        "logical_workers": tuple(sorted(graph.logical_workers, key=lambda w: w.worker_index)),
        "resources": tuple(sorted(graph.resources, key=lambda r: r.resource_id)),
    })


@dataclass(frozen=True)
class Topology:
    graph: CanonicalTopology
    content_hash: str
    router_indices: Mapping[RouterKey, int]
    link_indices: Mapping[LinkKey, int]
    port_indices: Mapping[PortKey, int]

    @classmethod
    def compile(cls, graph: CanonicalTopology) -> Topology:
        graph = normalize_topology(graph)
        routers: dict[RouterKey, int] = {}
        links: dict[LinkKey, int] = {}
        ports: dict[PortKey, int] = {}
        for fabric in graph.fabrics:
            for index, router in enumerate(r for r in graph.routers if r.fabric_id == fabric.fabric_id):
                routers[router.key] = index if router.runtime_index is None else router.runtime_index
                used = {p.runtime_index for p in router.ports if p.runtime_index is not None}
                for port in router.ports:
                    value = port.runtime_index
                    if value is None:
                        value = -1 if port.kind == "network" else 0
                        while value in used:
                            value += -1 if port.kind == "network" else 1
                    used.add(value)
                    ports[(*router.key, port.port_id)] = value
            for index, link in enumerate(l for l in graph.links if l.fabric_id == fabric.fabric_id):
                links[link.key] = index if link.runtime_index is None else link.runtime_index
        return cls(graph, content_digest(graph.model_dump(mode="json")),
                   MappingProxyType(routers), MappingProxyType(links), MappingProxyType(ports))

    def export(self) -> dict[str, object]:
        """Inventory counts do not assert runtime instantiation or service support."""
        return {
            "kind": "topology_inspection", "schema_version": 1,
            "graph_hash": self.content_hash,
            "graph": self.graph.model_dump(mode="json"),
            "counts": {
                "physical_tiles": len(self.graph.tiles), "fabric_routers": len(self.graph.routers),
                "directed_links": len(self.graph.links) if self.graph.connectivity_state == "complete" else None,
                "declared_links": len(self.graph.links), "attachments": len(self.graph.attachments),
                "enabled_workers": len(self.graph.enabled_worker_ids), "resources": len(self.graph.resources),
            },
            "unique_memory_bytes": sum(r.capacity_bytes for r in self.graph.resources),
            "runtime_indices": {
                "routers": [{"fabric_id": f, "router_id": r, "index": i}
                            for (f, r), i in self.router_indices.items()],
                "links": [{"fabric_id": f, "link_id": l, "index": i}
                          for (f, l), i in self.link_indices.items()],
                "ports": [{"fabric_id": f, "router_id": r, "port_id": p, "index": i}
                          for (f, r, p), i in self.port_indices.items()],
            },
            "execution": "inventory_only", "silicon_timing": "unvalidated",
        }
