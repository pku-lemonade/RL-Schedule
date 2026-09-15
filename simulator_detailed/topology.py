"""Normalization, immutable index maps, and inventory export for directed graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .configs.schemas.arch_config import NoCConfig
from .configs.schemas.hardware_profile import HardwareProfileConfig
from .configs.schemas.topology import (
    CanonicalTopology,
    Coordinate,
    Extent,
    LegacyEndpointBinding,
    TopologyAttachment,
    TopologyFabric,
    TopologyLink,
    TopologyOrigin,
    TopologyPort,
    TopologyResource,
    TopologyRouter,
    TopologyTile,
    TopologyWorker,
)
from .hardware_profile import inspect_profile
from .utils.definitions import Direction, direction_to_port

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


def topology_from_profile(profile: HardwareProfileConfig) -> Topology:
    """Project inventory; do not infer local ports, permissions, or torus links."""
    report = inspect_profile(profile)
    profile = report.profile
    graph = CanonicalTopology(
        kind="canonical_topology", schema_version=1,
        topology_id=profile.profile_id, asic_id=profile.asic_id,
        origin=TopologyOrigin(kind="hardware_profile", content_hash=report.profile_sha256,
                              document_json=canonical_json(profile.model_dump(mode="json"))),
        connectivity_state="unresolved",
        tiles=tuple(TopologyTile(tile_id=t.tile_id, x=t.x, y=t.y, role=t.role)
                    for t in profile.layout.tiles),
        fabrics=tuple(TopologyFabric(fabric_id=f.fabric_id,
                                     extent=Extent(width=f.extent.width, height=f.extent.height),
                                     topology_policy=f.topology_policy, routing_policy=f.routing_policy)
                      for f in profile.fabrics),
        routers=tuple(TopologyRouter(router_id=t.tile_id, tile_id=t.tile_id,
                                      fabric_id=f.fabric_id, enabled=None,
                                      coordinate=Coordinate(x=f.coordinates[t.tile_id].x,
                                                            y=f.coordinates[t.tile_id].y))
                      for f in profile.fabrics for t in profile.layout.tiles),
        links=(),
        attachments=tuple(TopologyAttachment(endpoint_id=a.endpoint_id, fabric_id=a.fabric_id,
                                              router_id=a.tile_id, role="network", enabled=None,
                                              resource_ids=tuple(a.resource_ids))
                          for a in profile.attachments.endpoints),
        enabled_worker_ids=tuple(profile.worker_selection.enabled_worker_ids),
        logical_workers=tuple(TopologyWorker.model_validate(w.model_dump())
                              for w in profile.worker_selection.logical_workers),
        resources=tuple(TopologyResource(resource_id=r.resource_id, kind=r.kind,
                                          capacity_bytes=report.memory.resources[r.resource_id].capacity_bytes,
                                          owner_tile_id=r.owner_tile_id,
                                          source_parameter=r.capacity_parameter,
                                          evidence_path=f"/memory/resources/{i}")
                        for i, r in enumerate(profile.memory.resources)),
    )
    return Topology.compile(graph)


def topology_from_legacy(config: NoCConfig) -> Topology:
    """The single mesh generator; compatibility indices preserve previous order."""
    config = NoCConfig.model_validate(config.model_dump(mode="json"))
    if config.type != "Mesh" or config.router.type != "XY":
        raise ValueError("legacy topology adapter supports Mesh with XY routing only")
    tiles = tuple(TopologyTile(tile_id=f"R{i}", x=i % config.x, y=i // config.x, role="worker")
                  for i in range(config.x * config.y))
    routers: list[TopologyRouter] = []
    links: list[TopologyLink] = []
    attachments: list[TopologyAttachment] = []
    for fabric in config.fabric_ids:
        ports_by_router = {i: {config.pe_local_port} for i in range(len(tiles))}
        for dma in config.dma_engines:
            if fabric in dma.fabric_ids:
                index = dma.fabric_ids.index(fabric)
                ports_by_router[dma.router_id].add(dma.local_ports[0 if len(dma.local_ports) == 1 else index])
        for i, tile in enumerate(tiles):
            routers.append(TopologyRouter(
                router_id=tile.tile_id, tile_id=tile.tile_id, fabric_id=int(fabric),
                coordinate=Coordinate(x=tile.x, y=tile.y), enabled=True, runtime_index=i,
                ports=tuple(TopologyPort(port_id=d.name, kind="network", runtime_index=direction_to_port(d))
                            for d in Direction) + tuple(
                    TopologyPort(port_id=f"P{p}", kind="local", runtime_index=p)
                    for p in sorted(ports_by_router[i])
                ),
            ))
            attachments.append(TopologyAttachment(
                endpoint_id=f"{int(fabric)}:PE:{i}", fabric_id=int(fabric), router_id=tile.tile_id,
                role="compute", enabled=True, permissions_resolved=True,
                inject_port=f"P{config.pe_local_port}", eject_port=f"P{config.pe_local_port}",
                legacy_binding=LegacyEndpointBinding(node_type="PE", node_id=i),
            ))
        link_index = 0
        for y in range(config.y):
            for x in range(config.x):
                src = y * config.x + x
                neighbors: list[tuple[int, Direction, Direction]] = []
                if x + 1 < config.x:
                    neighbors.append((src + 1, Direction.EAST, Direction.WEST))
                if y + 1 < config.y:
                    neighbors.append((src + config.x, Direction.NORTH, Direction.SOUTH))
                for dst, direction, opposite in neighbors:
                    for a, b, out, incoming in ((src, dst, direction, opposite), (dst, src, opposite, direction)):
                        links.append(TopologyLink(
                            link_id=f"R{a}_{out.name}->R{b}_{incoming.name}", fabric_id=int(fabric),
                            src_router=f"R{a}", src_port=out.name, dst_router=f"R{b}", dst_port=incoming.name,
                            enabled=True, direction=out.name, runtime_index=link_index,
                        ))
                        link_index += 1
        for dma in config.dma_engines:
            if fabric not in dma.fabric_ids:
                continue
            index = dma.fabric_ids.index(fabric)
            port = dma.local_ports[0 if len(dma.local_ports) == 1 else index]
            attachments.append(TopologyAttachment(
                endpoint_id=f"{int(fabric)}:{dma.dma_type.name}:{dma.instance_id}",
                fabric_id=int(fabric), router_id=f"R{dma.router_id}", role="dma",
                enabled=True, permissions_resolved=True, inject_port=f"P{port}", eject_port=f"P{port}",
                legacy_binding=LegacyEndpointBinding.model_validate({
                    "node_type": dma.dma_type.name, "node_id": dma.instance_id,
                    "attachment_mode": dma.attachment_mode.value,
                }),
            ))
    document = config.model_dump(mode="json")
    return Topology.compile(CanonicalTopology(
        kind="canonical_topology", schema_version=1,
        topology_id="legacy-mesh", asic_id="synthetic-0",
        origin=TopologyOrigin(kind="legacy_mesh", content_hash=content_digest(document),
                              document_json=canonical_json(document)),
        connectivity_state="complete", tiles=tiles,
        fabrics=tuple(TopologyFabric(fabric_id=int(f), extent=Extent(width=config.x, height=config.y),
                                     topology_policy="mesh", routing_policy="dimension_order_xy")
                      for f in config.fabric_ids),
        routers=tuple(routers), links=tuple(links), attachments=tuple(attachments),
        enabled_worker_ids=tuple(t.tile_id for t in tiles),
        logical_workers=tuple(TopologyWorker(tile_id=t.tile_id, worker_index=i, logical_x=t.x, logical_y=t.y)
                              for i, t in enumerate(tiles)), resources=(),
    ))
