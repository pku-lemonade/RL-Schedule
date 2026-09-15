"""Canonical next-hop policies and finite-channel admission for explicit unicast."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType
from typing import Protocol

from .configs.schemas.topology import ExplicitRoute
from .topology import Topology, canonical_json
from .utils.definitions import Flit, FlitConfig, FlitTrafficType

PathKey = tuple[int, int, int, int, int]


def channel_id(kind: str, fabric: int, identity: str) -> str:
    return canonical_json([kind, fabric, identity])


class RoutingPolicy(Protocol):
    def next_port(self, router_id: int, flit: Flit) -> int: ...
    def validate_router(self, flit: Flit, router_id: int, input_port: int) -> None: ...
    def coordinate(self, router_id: int) -> tuple[int, int]: ...


class LegacyXYRouting:
    def __init__(self, topology: Topology, fabric: int):
        self.topology = topology
        self.fabric = fabric
        self.coordinates: dict[int, tuple[int, int]] = {}
        self.outputs: dict[tuple[int, str], int] = {}
        for router in topology.graph.routers:
            if router.fabric_id != fabric:
                continue
            if router.coordinate is None:
                raise ValueError("legacy XY requires fabric coordinates")
            index = topology.router_indices[router.key]
            self.coordinates[index] = (router.coordinate.x, router.coordinate.y)
        for link in topology.graph.links:
            if link.fabric_id == fabric and link.enabled is True and link.direction is not None:
                self.outputs[(topology.router_indices[(fabric, link.src_router)], link.direction)] = (
                    topology.port_indices[(fabric, link.src_router, link.src_port)]
                )

    def coordinate(self, router_id: int) -> tuple[int, int]:
        try:
            return self.coordinates[router_id]
        except KeyError as exc:
            raise ValueError(f"router {router_id} is out of range") from exc

    def validate_router(self, flit: Flit, router_id: int, input_port: int) -> None:
        if flit.transport_id is not None:
            raise ValueError("graph-context flit cannot enter legacy routing")
        self.coordinate(router_id)
        self.coordinate(flit.dst_router)

    def next_port(self, router_id: int, flit: Flit) -> int:
        rx, ry = self.coordinate(router_id)
        dx, dy = self.coordinate(flit.dst_router)
        if (rx, ry) == (dx, dy):
            return flit.dst_local_port
        direction = ("EAST" if dx > rx else "WEST") if dx != rx else ("NORTH" if dy > ry else "SOUTH")
        try:
            return self.outputs[(router_id, direction)]
        except KeyError as exc:
            raise ValueError(f"router {router_id}: unavailable XY output {direction}") from exc


@dataclass(frozen=True)
class ResolvedReplayEndpoint:
    endpoint_id: str
    fabric_id: int
    router_id: int
    inject_port: int | None
    eject_port: int | None
    transport_id: str


@dataclass(frozen=True)
class CompiledRoute:
    definition: ExplicitRoute
    key: PathKey
    # router, input, output; immutable and permits no repeated router.
    hops: tuple[tuple[int, int, int], ...]
    channels: tuple[str, ...]


@dataclass(frozen=True)
class ExplicitRouting:
    topology: Topology
    plan_id: str
    formats: Mapping[int, FlitConfig]
    paths: Mapping[PathKey, CompiledRoute]

    def endpoint(self, endpoint_id: str) -> ResolvedReplayEndpoint:
        endpoint = next(a for a in self.topology.graph.attachments if a.endpoint_id == endpoint_id)
        if not endpoint.replay_enabled:
            raise ValueError("attachment is not a replay terminal")
        prefix = (endpoint.fabric_id, endpoint.router_id)
        return ResolvedReplayEndpoint(
            endpoint_id, endpoint.fabric_id, self.topology.router_indices[prefix],
            None if endpoint.inject_port is None else self.topology.port_indices[(*prefix, endpoint.inject_port)],
            None if endpoint.eject_port is None else self.topology.port_indices[(*prefix, endpoint.eject_port)],
            self.plan_id,
        )

    @classmethod
    def compile(cls, topology: Topology, routes: tuple[ExplicitRoute, ...],
                formats: Mapping[int, FlitConfig], plan_id: str) -> ExplicitRouting:
        graph = topology.graph
        if graph.connectivity_state != "complete" or graph.origin.kind == "hardware_profile":
            raise ValueError("unresolved/profile topology cannot execute explicit replay")
        routers = {r.key: r for r in graph.routers}
        edges = {l.key: l for l in graph.links}
        endpoints = {a.endpoint_id: a for a in graph.attachments}
        fabrics = {f.fabric_id: f for f in graph.fabrics}
        paths: dict[PathKey, CompiledRoute] = {}
        dependencies: dict[str, set[str]] = {}
        for route in routes:
            route = ExplicitRoute.model_validate(route.model_dump(mode="json"))
            fabric = route.fabric_id
            if fabric not in formats or fabric not in fabrics:
                raise ValueError(f"route uses unconfigured fabric {fabric}")
            metadata = fabrics[fabric]
            if metadata.topology_policy not in {"explicit", "mesh"} or metadata.routing_policy not in {
                "explicit_unicast", "dimension_order_xy"
            }:
                raise NotImplementedError(f"unsupported replay fabric policy {metadata.topology_policy}/{metadata.routing_policy}")
            source, destination = endpoints.get(route.source), endpoints.get(route.destination)
            if source is None or destination is None:
                raise ValueError("route has unknown source/destination attachment")
            for endpoint in (source, destination):
                if (endpoint.fabric_id != fabric or endpoint.enabled is not True
                        or not endpoint.permissions_resolved or not endpoint.replay_enabled):
                    raise ValueError(f"route endpoint {endpoint.endpoint_id} is unavailable on fabric {fabric}")
                if routers[(fabric, endpoint.router_id)].enabled is not True:
                    raise ValueError("route endpoint router is unavailable")
            if source.inject_port is None or destination.eject_port is None:
                raise ValueError("route requires injection and ejection permissions")
            src = topology.router_indices[(fabric, source.router_id)]
            dst = topology.router_indices[(fabric, destination.router_id)]
            inject = topology.port_indices[(fabric, source.router_id, source.inject_port)]
            eject = topology.port_indices[(fabric, destination.router_id, destination.eject_port)]
            key: PathKey = (fabric, src, inject, dst, eject)
            if key in paths:
                raise ValueError("duplicate route endpoint pair")
            current = source.router_id
            in_port = inject
            visited = {current}
            hops: list[tuple[int, int, int]] = []
            channels = [channel_id("inject", fabric, source.endpoint_id)]
            for link_id in route.link_ids:
                link = edges.get((fabric, link_id))
                if link is None or link.enabled is not True or link.src_router != current:
                    raise ValueError(f"route has missing, disabled or noncontiguous edge {fabric}:{link_id}")
                if link.dst_router in visited or routers[(fabric, link.dst_router)].enabled is not True:
                    raise ValueError("route repeats or visits an unavailable router")
                out_port = topology.port_indices[(fabric, current, link.src_port)]
                hops.append((topology.router_indices[(fabric, current)], in_port, out_port))
                channels.append(channel_id("network", fabric, link_id))
                current = link.dst_router
                visited.add(current)
                in_port = topology.port_indices[(fabric, current, link.dst_port)]
            if current != destination.router_id:
                raise ValueError("route does not reach destination")
            hops.append((dst, in_port, eject))
            channels.append(channel_id("eject", fabric, destination.endpoint_id))
            paths[key] = CompiledRoute(route, key, tuple(hops), tuple(channels))
            for channel in channels:
                dependencies.setdefault(channel, set())
            for before, after in pairwise(channels):
                dependencies[before].add(after)
        _require_acyclic(dependencies)
        return cls(topology, plan_id, MappingProxyType(dict(formats)), MappingProxyType(paths))

    def route_for(self, flit: Flit) -> CompiledRoute:
        flit.validate_transport()
        if flit.transport_id != self.plan_id:
            raise ValueError("flit transport plan does not match")
        if self.formats.get(int(flit.fabric_id)) != flit.format:
            raise ValueError("flit format does not match transport plan")
        if (flit.traffic_type is not FlitTrafficType.PAYLOAD or flit.dma_header_bytes
                or flit.is_broadcast or flit.broadcast_dst_mask or flit.reduce_op != -1 or flit.sync_mode):
            raise NotImplementedError("explicit replay supports ordinary unicast payload only")
        key = (int(flit.fabric_id), flit.src_router, flit.src_local_port, flit.dst_router, flit.dst_local_port)
        try:
            return self.paths[key]
        except KeyError as exc:
            raise ValueError("flit endpoint pair has no admitted route") from exc

    def validate_channel(self, flit: Flit, channel: str) -> None:
        if channel not in self.route_for(flit).channels:
            raise ValueError("flit cannot enter a channel outside its admitted route")

    def validate_router(self, flit: Flit, router_id: int, input_port: int) -> None:
        if not any(r == router_id and incoming == input_port for r, incoming, _ in self.route_for(flit).hops):
            raise ValueError("flit cannot enter this router/input on its admitted route")

    def next_port(self, router_id: int, flit: Flit) -> int:
        for router, _, outgoing in self.route_for(flit).hops:
            if router == router_id:
                return outgoing
        raise ValueError("router is outside admitted route")

    def coordinate(self, router_id: int) -> tuple[int, int]:
        raise ValueError("explicit routing coordinates require a fabric-qualified graph lookup")


def _require_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    incoming = {channel: 0 for channel in dependencies}
    for targets in dependencies.values():
        for target in targets:
            incoming[target] += 1
    ready = deque(sorted(channel for channel, count in incoming.items() if count == 0))
    while ready:
        channel = ready.popleft()
        for target in sorted(dependencies[channel]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    remaining = sorted(channel for channel, count in incoming.items() if count)
    if remaining:
        raise ValueError("channel dependency cycle involving " + ", ".join(remaining))
