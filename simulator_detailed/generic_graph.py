"""Compile neutral generic system-graph documents into the canonical machinery.

The adapter emits a `CanonicalTopology` and reuses `Topology.compile`, so the
generic layer inherits canonical validation, digests and index maps instead of
forking them. `GenericSystem` adds the neutral views (networks, endpoints,
memory resources, static route tables) on top of the canonical inventory.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from .configs.schemas.generic_graph import GenericSystemGraph
from .configs.schemas.topology import (
    CanonicalTopology,
    Coordinate,
    TileRole,
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
from .topology import Topology, canonical_json, content_digest

_TILE_ROLES: dict[str, TileRole] = {"compute": "worker", "memory": "memory", "transit": "transit"}


def normalize_generic(document: GenericSystemGraph) -> GenericSystemGraph:
    """Revalidate and order records so digests are input-order independent."""
    document = GenericSystemGraph.model_validate(document.model_dump(mode="json"))
    return document.model_copy(update={
        "nodes": tuple(sorted(document.nodes, key=lambda n: n.node_id)),
        "networks": tuple(sorted(document.networks, key=lambda n: n.network_id)),
        "ports": tuple(sorted(document.ports, key=lambda p: (p.node_id, p.network_id, p.port_id))),
        "links": tuple(sorted(document.links, key=lambda link: (link.network_id, link.link_id))),
        "dma_endpoints": tuple(sorted(document.dma_endpoints, key=lambda e: e.endpoint_id)),
        "execution_units": tuple(sorted(document.execution_units, key=lambda e: e.unit_id)),
        "memory_resources": tuple(sorted(document.memory_resources, key=lambda r: r.resource_id)),
        "static_routes": tuple(sorted(
            document.static_routes, key=lambda r: (r.network_id, r.source, r.destination)
        )),
    })


def generic_digest(document: GenericSystemGraph) -> str:
    return content_digest(normalize_generic(document).model_dump(mode="json"))


def _canonical_from_generic(document: GenericSystemGraph) -> CanonicalTopology:
    network_fabrics = {
        network.network_id: index
        for index, network in enumerate(sorted(document.networks, key=lambda n: n.network_id))
    }
    nodes = {node.node_id: node for node in document.nodes}

    tiles = tuple(
        TopologyTile(tile_id=node.node_id, x=node.x, y=node.y, role=_TILE_ROLES[node.role])
        for node in document.nodes
    )
    fabrics = tuple(
        TopologyFabric(
            fabric_id=index,
            extent=None,
            topology_policy="generic",
            routing_policy="static_table",
        )
        for index in range(len(network_fabrics))
    )

    routers: list[TopologyRouter] = []
    for network_id, fabric_id in network_fabrics.items():
        member_node_ids = sorted({p.node_id for p in document.ports if p.network_id == network_id})
        for node_id in member_node_ids:
            node = nodes[node_id]
            ports = tuple(
                TopologyPort(port_id=port.port_id, kind=port.kind)
                for port in document.ports
                if port.node_id == node_id and port.network_id == network_id
            )
            routers.append(TopologyRouter(
                router_id=node_id,
                fabric_id=fabric_id,
                tile_id=node_id,
                enabled=True,
                coordinate=Coordinate(x=node.x, y=node.y),
                ports=ports,
            ))

    links = tuple(
        TopologyLink(
            link_id=link.link_id,
            fabric_id=network_fabrics[link.network_id],
            src_router=link.src_node,
            src_port=link.src_port,
            dst_router=link.dst_node,
            dst_port=link.dst_port,
            enabled=True,
        )
        for link in document.links
    )

    attachments: list[TopologyAttachment] = []
    for endpoint in document.dma_endpoints:
        attachments.append(TopologyAttachment(
            endpoint_id=endpoint.endpoint_id,
            fabric_id=network_fabrics[endpoint.network_id],
            router_id=endpoint.node_id,
            role="dma",
            enabled=True,
            inject_port=endpoint.port_id,
            eject_port=endpoint.port_id,
            permissions_resolved=True,
        ))
    for unit in document.execution_units:
        attachments.append(TopologyAttachment(
            endpoint_id=unit.unit_id,
            fabric_id=network_fabrics[unit.network_id],
            router_id=unit.node_id,
            role="compute",
            enabled=True,
            inject_port=unit.port_id,
            eject_port=unit.port_id,
            permissions_resolved=True,
        ))
    for resource in document.memory_resources:
        if (
            resource.endpoint_id is not None
            and resource.network_id is not None
            and resource.port_id is not None
            and resource.owner_node is not None
        ):
            attachments.append(TopologyAttachment(
                endpoint_id=resource.endpoint_id,
                fabric_id=network_fabrics[resource.network_id],
                router_id=resource.owner_node,
                role="network",
                enabled=True,
                inject_port=resource.port_id,
                eject_port=resource.port_id,
                permissions_resolved=True,
                resource_ids=(resource.resource_id,),
            ))

    compute_node_ids = sorted(n.node_id for n in document.nodes if n.role == "compute")
    document_json = canonical_json(document.model_dump(mode="json"))
    return CanonicalTopology(
        kind="canonical_topology",
        schema_version=1,
        topology_id=document.system_id,
        asic_id=document.system_id,
        origin=TopologyOrigin(
            kind="synthetic",
            content_hash=content_digest(document.model_dump(mode="json")),
            document_json=document_json,
        ),
        connectivity_state="complete",
        tiles=tiles,
        fabrics=fabrics,
        routers=tuple(routers),
        links=links,
        attachments=tuple(attachments),
        enabled_worker_ids=tuple(compute_node_ids),
        logical_workers=tuple(
            TopologyWorker(
                worker_index=index,
                logical_x=nodes[node_id].x,
                logical_y=nodes[node_id].y,
                tile_id=node_id,
            )
            for index, node_id in enumerate(compute_node_ids)
        ),
        resources=tuple(
            TopologyResource(
                resource_id=resource.resource_id,
                kind="local_sram" if resource.owner_node is not None else "dram",
                capacity_bytes=resource.capacity_bytes,
                owner_tile_id=resource.owner_node,
            )
            for resource in document.memory_resources
        ),
    )


@dataclass(frozen=True)
class GenericSystem(Topology):
    """Compiled generic system: canonical inventory plus neutral views."""

    document: GenericSystemGraph
    network_fabrics: Mapping[str, int]
    endpoint_nodes: Mapping[str, str]

    @classmethod
    def compile_generic(cls, document: GenericSystemGraph) -> GenericSystem:
        document = normalize_generic(document)
        base = Topology.compile(_canonical_from_generic(document))
        network_fabrics = {
            network.network_id: index
            for index, network in enumerate(sorted(document.networks, key=lambda n: n.network_id))
        }
        endpoint_nodes: dict[str, str] = {e.endpoint_id: e.node_id for e in document.dma_endpoints}
        endpoint_nodes.update({u.unit_id: u.node_id for u in document.execution_units})
        for resource in document.memory_resources:
            if resource.endpoint_id is not None and resource.owner_node is not None:
                endpoint_nodes[resource.endpoint_id] = resource.owner_node
        return cls(
            graph=base.graph,
            content_hash=base.content_hash,
            router_indices=base.router_indices,
            link_indices=base.link_indices,
            port_indices=base.port_indices,
            document=document,
            network_fabrics=MappingProxyType(network_fabrics),
            endpoint_nodes=MappingProxyType(endpoint_nodes),
        )

    @property
    def generic_sha256(self) -> str:
        return generic_digest(self.document)

    def network_links(self, network_id: str) -> tuple[str, ...]:
        return tuple(
            link.link_id for link in self.document.links if link.network_id == network_id
        )

    def network_nodes(self, network_id: str) -> tuple[str, ...]:
        return tuple(sorted({
            p.node_id for p in self.document.ports if p.network_id == network_id
        }))

    def routes_for(self, network_id: str) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "source": route.source,
                "destination": route.destination,
                "link_ids": list(route.link_ids),
            }
            for route in self.document.static_routes
            if route.network_id == network_id
        )

    def export(self) -> dict[str, object]:
        """Unified inventory; counts do not assert runtime instantiation."""
        return {
            "kind": "generic_system_inspection",
            "schema_version": 1,
            "system_id": self.document.system_id,
            "generic_sha256": self.generic_sha256,
            "canonical": Topology.export(self),
            "networks": [
                {
                    "network_id": network_id,
                    "fabric_id": fabric_id,
                    "nodes": list(self.network_nodes(network_id)),
                    "links": list(self.network_links(network_id)),
                }
                for network_id, fabric_id in sorted(
                    self.network_fabrics.items(), key=lambda item: item[1]
                )
            ],
            "endpoints": {
                "dma": sorted(e.endpoint_id for e in self.document.dma_endpoints),
                "execution_units": sorted(u.unit_id for u in self.document.execution_units),
                "memory_services": sorted(
                    r.endpoint_id
                    for r in self.document.memory_resources
                    if r.endpoint_id is not None
                ),
            },
            "memory_resources": [
                {
                    "resource_id": resource.resource_id,
                    "owner_node": resource.owner_node,
                    "capacity_bytes": resource.capacity_bytes,
                    "endpoint_id": resource.endpoint_id,
                }
                for resource in self.document.memory_resources
            ],
            "static_routes": [
                {
                    "network_id": route.network_id,
                    "source": route.source,
                    "destination": route.destination,
                    "link_ids": list(route.link_ids),
                }
                for route in self.document.static_routes
            ],
            "execution": "inventory_only",
            "silicon_timing": "unvalidated",
        }


def topology_from_generic(document: GenericSystemGraph) -> GenericSystem:
    return GenericSystem.compile_generic(document)


def load_generic_system(path: str | Path) -> GenericSystem:
    """Load and compile a `generic_system_graph` JSON document."""
    raw: object = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or cast(dict[str, object], raw).get("kind") != "generic_system_graph":
        raise ValueError("expected a generic_system_graph document")
    return topology_from_generic(GenericSystemGraph.model_validate(raw))
