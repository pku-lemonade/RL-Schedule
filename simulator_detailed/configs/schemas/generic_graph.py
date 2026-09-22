"""Neutral generic system-graph documents; no device-era vocabulary.

Every record is strict (unknown fields fail) and frozen. Identifiers are
synthetic names only: any real device or vendor token is rejected. No field
carries a device-derived default; structural facts are explicit inputs.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from .topology import Cycles, GraphRecord, Index, PositiveInt, PositiveTime, unique

# Real device/vendor vocabulary observed in this repository's history. Generic
# documents must never mention them; the check is a case-insensitive substring
# match over every identifier so renamed or embedded mentions still fail.
FORBIDDEN_DEVICE_TOKENS: tuple[str, ...] = (
    "wormhole",
    "blackhole",
    "grayskull",
    "tenstorrent",
    "ttsim",
    "ttmetal",
    "tt_metal",
    "tt-metal",
    "n150",
    "n300",
    "nebula",
    "quasar",
    "galaxy",
)


def _neutral(value: str) -> str:
    lowered = value.lower()
    for token in FORBIDDEN_DEVICE_TOKENS:
        if token in lowered:
            raise ValueError(f"identifier {value!r} carries device-era vocabulary {token!r}")
    return value


NeutralId = Annotated[
    str,
    Field(min_length=1, pattern=r"^\S+$"),
    AfterValidator(_neutral),
]


class GenericNode(GraphRecord):
    """One neutral compute/memory/transit position in a two-dimensional grid."""

    node_id: NeutralId
    x: Index
    y: Index
    role: Literal["compute", "memory", "transit"]


class GenericNetwork(GraphRecord):
    """One independent network; membership is derived from ports and links.

    `routing` selects the path model: `static_table` (default) requires
    declared per-pair routes; `shortest_path` and `adaptive` compute paths
    dynamically and must not declare static routes.
    """

    network_id: NeutralId
    routing: Literal["static_table", "shortest_path", "adaptive"] = "static_table"


class GenericPort(GraphRecord):
    """One port on one node in one network; local ports attach endpoints."""

    port_id: NeutralId
    node_id: NeutralId
    network_id: NeutralId
    kind: Literal["network", "local"]


class GenericLink(GraphRecord):
    """One directed link between network ports of two nodes in one network."""

    link_id: NeutralId
    network_id: NeutralId
    src_node: NeutralId
    src_port: NeutralId
    dst_node: NeutralId
    dst_port: NeutralId


class GenericDmaEndpoint(GraphRecord):
    """One DMA agent attached to a local port; may transfer to any endpoint."""

    endpoint_id: NeutralId
    node_id: NeutralId
    network_id: NeutralId
    port_id: NeutralId


class GenericExecutionUnit(GraphRecord):
    """One execution resource on a compute node, reachable via a local port."""

    unit_id: NeutralId
    node_id: NeutralId
    network_id: NeutralId
    port_id: NeutralId


class GenericMemoryChannel(GraphRecord):
    """One independent data path with its own byte rate."""

    channel_id: NeutralId
    bytes_per_cycle: PositiveInt


class GenericMemoryPort(GraphRecord):
    """One command issue point bound to a declared channel."""

    port_id: NeutralId
    channel_id: NeutralId
    command_cycles: PositiveTime


class GenericMemoryHierarchy(GraphRecord):
    """Optional internal structure of one memory resource."""

    banks: PositiveInt
    stripe_bytes: PositiveInt
    latency_cycles: Cycles
    ports: tuple[GenericMemoryPort, ...] = Field(min_length=1)
    channels: tuple[GenericMemoryChannel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(p.port_id for p in self.ports), "memory port identity")
        unique(tuple(c.channel_id for c in self.channels), "memory channel identity")
        channel_ids = {c.channel_id for c in self.channels}
        for port in self.ports:
            if port.channel_id not in channel_ids:
                raise ValueError(
                    f"memory port {port.port_id}: unknown channel {port.channel_id}"
                )
        return self


class GenericMemoryResource(GraphRecord):
    """One memory resource; an optional service endpoint makes it reachable."""

    resource_id: NeutralId
    owner_node: NeutralId | None
    capacity_bytes: PositiveInt
    endpoint_id: NeutralId | None = None
    network_id: NeutralId | None = None
    port_id: NeutralId | None = None
    hierarchy: GenericMemoryHierarchy | None = None


class GenericStaticRoute(GraphRecord):
    """One explicit ordered hop path between two endpoints in one network."""

    network_id: NeutralId
    source: NeutralId
    destination: NeutralId
    link_ids: tuple[NeutralId, ...] = Field(min_length=1)


class GenericSystemGraph(GraphRecord):
    """Version-one neutral system description compiled into the canonical graph."""

    kind: Literal["generic_system_graph"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    system_id: NeutralId
    nodes: tuple[GenericNode, ...] = Field(min_length=1)
    networks: tuple[GenericNetwork, ...] = Field(min_length=1)
    ports: tuple[GenericPort, ...] = ()
    links: tuple[GenericLink, ...] = ()
    dma_endpoints: tuple[GenericDmaEndpoint, ...] = ()
    execution_units: tuple[GenericExecutionUnit, ...] = ()
    memory_resources: tuple[GenericMemoryResource, ...] = ()
    static_routes: tuple[GenericStaticRoute, ...] = ()

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(n.node_id for n in self.nodes), "node identity")
        unique(tuple((n.x, n.y) for n in self.nodes), "node position")
        unique(tuple(n.network_id for n in self.networks), "network identity")
        unique(
            tuple((p.node_id, p.network_id, p.port_id) for p in self.ports),
            "port identity",
        )
        unique(
            tuple((link.network_id, link.link_id) for link in self.links),
            "link identity",
        )
        unique(
            tuple(r.resource_id for r in self.memory_resources),
            "memory resource identity",
        )
        unique(
            tuple((r.network_id, r.source, r.destination) for r in self.static_routes),
            "static route",
        )
        endpoint_ids = tuple(
            [e.endpoint_id for e in self.dma_endpoints]
            + [e.unit_id for e in self.execution_units]
            + [r.endpoint_id for r in self.memory_resources if r.endpoint_id is not None]
        )
        unique(endpoint_ids, "endpoint identity")

        nodes = {n.node_id: n for n in self.nodes}
        networks = {n.network_id for n in self.networks}
        dynamic_networks = {
            n.network_id for n in self.networks if n.routing != "static_table"
        }
        ports = {(p.node_id, p.network_id, p.port_id): p for p in self.ports}
        for port in self.ports:
            if port.node_id not in nodes:
                raise ValueError(f"port {port.port_id}: unknown node {port.node_id}")
            if port.network_id not in networks:
                raise ValueError(f"port {port.port_id}: unknown network {port.network_id}")

        # A network port carries at most one outgoing and one incoming link.
        out_used: set[tuple[str, str, str]] = set()
        in_used: set[tuple[str, str, str]] = set()
        for link in self.links:
            if link.network_id not in networks:
                raise ValueError(f"link {link.link_id}: unknown network {link.network_id}")
            for node_id, port_id, used, half in (
                (link.src_node, link.src_port, out_used, "out"),
                (link.dst_node, link.dst_port, in_used, "in"),
            ):
                if node_id not in nodes:
                    raise ValueError(f"link {link.link_id}: unknown {half} node {node_id}")
                port = ports.get((node_id, link.network_id, port_id))
                if port is None or port.kind != "network":
                    raise ValueError(
                        f"link {link.link_id}: invalid network port {node_id}:{port_id}"
                    )
                key = (node_id, link.network_id, port_id)
                if key in used:
                    raise ValueError(
                        f"link {link.link_id}: network port {half} half already occupied"
                    )
                used.add(key)

        local_used: set[tuple[str, str, str]] = set()

        def bind_local(owner: str, label: str, node_id: str, network_id: str, port_id: str) -> None:
            if node_id not in nodes:
                raise ValueError(f"{label} {owner}: unknown node {node_id}")
            if network_id not in networks:
                raise ValueError(f"{label} {owner}: unknown network {network_id}")
            port = ports.get((node_id, network_id, port_id))
            if port is None or port.kind != "local":
                raise ValueError(f"{label} {owner}: invalid local port {node_id}:{port_id}")
            key = (node_id, network_id, port_id)
            if key in local_used:
                raise ValueError(f"{label} {owner}: local port {node_id}:{port_id} already bound")
            local_used.add(key)

        for endpoint in self.dma_endpoints:
            bind_local(
                endpoint.endpoint_id,
                "dma endpoint",
                endpoint.node_id,
                endpoint.network_id,
                endpoint.port_id,
            )
        endpoint_nodes: dict[str, str] = {e.endpoint_id: e.node_id for e in self.dma_endpoints}
        for unit in self.execution_units:
            bind_local(unit.unit_id, "execution unit", unit.node_id, unit.network_id, unit.port_id)
            if nodes[unit.node_id].role != "compute":
                raise ValueError(
                    f"execution unit {unit.unit_id}: node {unit.node_id} is not a compute node"
                )
            endpoint_nodes[unit.unit_id] = unit.node_id
        for resource in self.memory_resources:
            if resource.owner_node is not None and resource.owner_node not in nodes:
                raise ValueError(
                    f"memory resource {resource.resource_id}: unknown owner {resource.owner_node}"
                )
            declared = (
                resource.endpoint_id is not None,
                resource.network_id is not None,
                resource.port_id is not None,
            )
            if any(declared) and not all(declared):
                raise ValueError(
                    f"memory resource {resource.resource_id}: service endpoint is all-or-nothing"
                )
            if (
                resource.endpoint_id is not None
                and resource.network_id is not None
                and resource.port_id is not None
            ):
                if resource.owner_node is None:
                    raise ValueError(
                        f"memory resource {resource.resource_id}: endpoint requires an owner node"
                    )
                bind_local(
                    resource.endpoint_id,
                    "memory service endpoint",
                    resource.owner_node,
                    resource.network_id,
                    resource.port_id,
                )
                endpoint_nodes[resource.endpoint_id] = resource.owner_node

        links_by_network: dict[str, dict[str, GenericLink]] = {}
        for link in self.links:
            links_by_network.setdefault(link.network_id, {})[link.link_id] = link
        for route in self.static_routes:
            if route.network_id not in networks:
                raise ValueError(f"route {route.source}->{route.destination}: unknown network")
            if route.network_id in dynamic_networks:
                raise ValueError(
                    f"route {route.source}->{route.destination}: network "
                    f"{route.network_id} uses a dynamic routing policy and "
                    "cannot declare static routes"
                )
            source_node = endpoint_nodes.get(route.source)
            destination_node = endpoint_nodes.get(route.destination)
            if source_node is None:
                raise ValueError(f"route: unknown source endpoint {route.source}")
            if destination_node is None:
                raise ValueError(f"route: unknown destination endpoint {route.destination}")
            if route.source == route.destination:
                raise ValueError(f"route {route.source}: source equals destination")
            current = source_node
            visited = {current}
            for link_id in route.link_ids:
                link = links_by_network.get(route.network_id, {}).get(link_id)
                if link is None:
                    raise ValueError(
                        f"route {route.source}->{route.destination}: "
                        f"unknown link {link_id} in network {route.network_id}"
                    )
                if link.src_node != current:
                    raise ValueError(
                        f"route {route.source}->{route.destination}: link {link_id} "
                        "breaks contiguity"
                    )
                if link.dst_node in visited:
                    raise ValueError(
                        f"route {route.source}->{route.destination}: revisits node {link.dst_node}"
                    )
                visited.add(link.dst_node)
                current = link.dst_node
            if current != destination_node:
                raise ValueError(
                    f"route {route.source}->{route.destination}: "
                    f"path ends at {current}, not {destination_node}"
                )

        nonempty = {p.network_id for p in self.ports} | {link.network_id for link in self.links}
        for network_id in networks:
            if network_id not in nonempty:
                raise ValueError(f"network {network_id} is empty: no ports or links")
        return self
