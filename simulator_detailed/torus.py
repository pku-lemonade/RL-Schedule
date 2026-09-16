"""Pure torus binding and deterministic resource paths; no transport runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType
from typing import Literal

from .configs.schemas.hardware_profile import HardwareProfileConfig
from .configs.schemas.topology import (
    CanonicalTopology,
    Coordinate,
    TopologyAttachment,
    TopologyFabric,
    TopologyLink,
    TopologyPort,
    TopologyRouter,
)
from .configs.schemas.torus_replay import (
    AvailabilitySetting,
    ChannelIdentity,
    LaneIdentity,
    LinkAvailability,
    ProfileSource,
    RequestResponseTraffic,
    ResourceHop,
    RouterAvailability,
    RouteRecord,
    TorusBinding,
    TorusEndpointBinding,
    TorusFabricBinding,
    TorusReplay,
    TrafficClass,
)
from .topology import RouterKey, Topology, normalize_topology, topology_from_profile
from .torus_contract import PreparedTorusContract
from .torus_dependencies import ResourceDependencies
from .torus_records import EffectivePlanRecord

Axis = Literal["x", "y"]
RouteKey = tuple[int, str, str, TrafficClass]
AXES: tuple[Axis, Axis] = ("x", "y")


def _position(coordinate: Coordinate, axis: Axis) -> int:
    return coordinate.x if axis == "x" else coordinate.y


def _extent(fabric: TopologyFabric, axis: Axis) -> int:
    if fabric.extent is None:
        raise ValueError("torus requires an explicit extent")
    return fabric.extent.width if axis == "x" else fabric.extent.height


def _coordinate(router: TopologyRouter) -> Coordinate:
    if router.coordinate is None:
        raise ValueError(f"torus router {router.key} requires raw coordinates")
    return router.coordinate


def _order(binding: TorusFabricBinding) -> tuple[Axis, Axis]:
    return ("x", "y") if binding.routing_policy == "dimension_order_xy" else ("y", "x")


def _availability(original: bool | None, default: AvailabilitySetting,
                  override: AvailabilitySetting | None, label: str) -> bool:
    # Defaults fill unknowns; they never resurrect known-disabled resources.
    if override is not None:
        if original is False and override.enabled:
            raise ValueError(f"{label}: binding cannot enable a source-disabled resource")
        return override.enabled
    return default.enabled if original is None else original


def _raw_maps(graph: CanonicalTopology, fabric_bindings: tuple[TorusFabricBinding, ...]) -> dict[tuple[int, int, int], TopologyRouter]:
    if {f.fabric_id for f in graph.fabrics} != {f.fabric_id for f in fabric_bindings}:
        raise ValueError("binding must select exactly the source fabrics")
    bindings = {f.fabric_id: f for f in fabric_bindings}
    coordinates: dict[tuple[int, int, int], TopologyRouter] = {}
    for fabric in graph.fabrics:
        selected = bindings[fabric.fabric_id]
        if fabric.topology_policy not in {"torus_2d", "torus_2d_positive"} or (
            fabric.routing_policy != selected.routing_policy
        ):
            raise ValueError(f"fabric {fabric.fabric_id}: source and torus binding policies disagree")
        width, height = _extent(fabric, "x"), _extent(fabric, "y")
        if min(width, height) < 2:
            raise ValueError("initial torus policy requires both dimensions >= 2")
        if selected.dateline.x >= width or selected.dateline.y >= height:
            raise ValueError(f"fabric {fabric.fabric_id}: dateline outside raw extent")
        members = tuple(r for r in graph.routers if r.fabric_id == fabric.fabric_id)
        if len(members) != width * height:
            raise ValueError(f"fabric {fabric.fabric_id}: incomplete torus router grid")
        for router in members:
            raw = _coordinate(router)
            coordinates[(fabric.fabric_id, raw.x, raw.y)] = router
    return coordinates


def _physical_directions(graph: CanonicalTopology, coordinates: Mapping[tuple[int, int, int], TopologyRouter]
                         ) -> dict[tuple[int, Axis], tuple[int, int] | None]:
    """Recognize physical unit directions; arbitrary embeddings can remain unknown.

    Two-coordinate rings have identical positive/negative neighbors. Use the
    non-wrapping raw step to choose an orientation in that otherwise ambiguous case.
    """
    tiles = {t.tile_id: t for t in graph.tiles}
    width, height = max(t.x for t in graph.tiles) + 1, max(t.y for t in graph.tiles) + 1
    directions: dict[tuple[int, Axis], tuple[int, int] | None] = {}
    for fabric in graph.fabrics:
        for axis in AXES:
            members = tuple(r for r in graph.routers if r.fabric_id == fabric.fabric_id)
            candidates: list[tuple[int, tuple[int, int]]] = []
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                score = 0
                for router in members:
                    raw = _coordinate(router)
                    x = (raw.x + 1) % _extent(fabric, "x") if axis == "x" else raw.x
                    y = (raw.y + 1) % _extent(fabric, "y") if axis == "y" else raw.y
                    src, dst = tiles[router.tile_id], tiles[coordinates[(fabric.fabric_id, x, y)].tile_id]
                    if ((src.x + dx) % width, (src.y + dy) % height) != (dst.x, dst.y):
                        break
                    if _position(raw, axis) < _extent(fabric, axis) - 1:
                        score += (dst.x - src.x, dst.y - src.y) == (dx, dy)
                else:
                    candidates.append((score, (dx, dy)))
            directions[(fabric.fabric_id, axis)] = max(candidates, key=lambda v: v[0])[1] if candidates else None
    return directions


@dataclass(frozen=True)
class TorusEndpointPorts:
    """Topology permissions without transaction timing or traffic templates."""

    endpoint_id: str
    fabric_id: int
    inject_port: str | None
    eject_port: str | None
    initiates_requests: bool


def bind_torus_graph(graph: CanonicalTopology, fabrics: tuple[TorusFabricBinding, ...],
                     endpoints: tuple[TorusEndpointPorts, ...], *, generate_links: bool,
                     router_availability: tuple[RouterAvailability, ...] = (),
                     link_availability: tuple[LinkAvailability, ...] = ()) -> CanonicalTopology:
    """Resolve a torus inventory for independently admitted transport or memory clients."""
    graph = normalize_topology(graph)
    coordinates = _raw_maps(graph, fabrics)
    bindings = {f.fabric_id: f for f in fabrics}
    router_overrides = {(r.fabric_id, r.router_id): r for r in router_availability}
    link_overrides = {(e.fabric_id, e.link_id): e for e in link_availability}
    if set(router_overrides) - {r.key for r in graph.routers}:
        raise ValueError("availability override refers to an unknown router")
    endpoint_bindings = {e.endpoint_id: e for e in endpoints}
    if set(endpoint_bindings) - {a.endpoint_id for a in graph.attachments}:
        raise ValueError("binding refers to an unknown source endpoint")
    if generate_links and (graph.connectivity_state != "unresolved" or graph.links):
        raise ValueError("torus link generation requires unresolved, edge-free inventory")
    if not generate_links and graph.connectivity_state != "complete":
        raise ValueError("graph binding requires complete explicit edges")

    routers: dict[RouterKey, TopologyRouter] = {}
    for router in graph.routers:
        enabled = _availability(router.enabled, bindings[router.fabric_id].router_default,
                                router_overrides.get(router.key), f"router {router.key}")
        ports = router.ports
        if generate_links:
            if ports:
                raise ValueError("profile inventory must not invent runtime ports")
            ports = tuple(TopologyPort(port_id=f"{axis}{sign}", kind="network") for axis in AXES for sign in ("+", "-"))
        routers[router.key] = router.model_copy(update={"enabled": enabled, "ports": ports})

    links = list(graph.links)
    if generate_links:
        tiles = {t.tile_id: t for t in graph.tiles}
        physical_width, physical_height = max(t.x for t in graph.tiles) + 1, max(t.y for t in graph.tiles) + 1
        directions = _physical_directions(graph, coordinates)
        names = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}
        for fabric in graph.fabrics:
            for router in (r for r in graph.routers if r.fabric_id == fabric.fabric_id):
                raw = _coordinate(router)
                for axis in AXES:
                    x = (raw.x + 1) % _extent(fabric, "x") if axis == "x" else raw.x
                    y = (raw.y + 1) % _extent(fabric, "y") if axis == "y" else raw.y
                    destination = coordinates[(fabric.fabric_id, x, y)]
                    direction = directions[(fabric.fabric_id, axis)]
                    src = tiles[router.tile_id]
                    wrap = direction is not None and not (
                        0 <= src.x + direction[0] < physical_width and 0 <= src.y + direction[1] < physical_height
                    )
                    links.append(TopologyLink(
                        link_id=f"{router.router_id}/{axis}+", fabric_id=fabric.fabric_id,
                        src_router=router.router_id, src_port=f"{axis}+", dst_router=destination.router_id,
                        dst_port=f"{axis}-", enabled=None,
                        direction=None if direction is None else names[direction], wrap=wrap,
                    ))
    if set(link_overrides) - {e.key for e in links}:
        raise ValueError("availability override refers to an unknown directed link")
    links = [e.model_copy(update={"enabled": _availability(
        e.enabled, bindings[e.fabric_id].link_default, link_overrides.get(e.key), f"link {e.key}"
    )}) for e in links]

    attachments: list[TopologyAttachment] = []
    tiles = {t.tile_id: t for t in graph.tiles}
    for endpoint in graph.attachments:
        selected = endpoint_bindings.get(endpoint.endpoint_id)
        if selected is None:
            attachments.append(endpoint.model_copy(update={"replay_enabled": False}))
            continue
        if selected.fabric_id != endpoint.fabric_id or endpoint.enabled is False:
            raise ValueError(f"endpoint {endpoint.endpoint_id}: wrong fabric or source-disabled attachment")
        router = routers[(endpoint.fabric_id, endpoint.router_id)]
        if router.enabled is not True:
            raise ValueError(f"endpoint {endpoint.endpoint_id}: router is unavailable")
        tile = tiles[router.tile_id]
        if selected.inject_port is not None and tile.role == "worker" and tile.tile_id not in graph.enabled_worker_ids:
            raise ValueError(f"endpoint {endpoint.endpoint_id}: harvested worker cannot initiate traffic")
        if tile.role == "memory" and selected.initiates_requests:
            raise ValueError("memory fixtures cannot initiate independent requests")
        ports = {p.port_id: p for p in router.ports}
        for direction, port_id in (("inject", selected.inject_port), ("eject", selected.eject_port)):
            if port_id is None:
                continue
            allowed = endpoint.inject_port if direction == "inject" else endpoint.eject_port
            if endpoint.permissions_resolved and allowed != port_id:
                raise ValueError(f"endpoint {endpoint.endpoint_id}: binding exceeds source {direction} permission")
            if port_id not in ports and generate_links:
                ports[port_id] = TopologyPort(port_id=port_id, kind="local")
            if port_id not in ports or ports[port_id].kind != "local":
                raise ValueError(f"endpoint {endpoint.endpoint_id}: missing or nonlocal port {port_id}")
        routers[router.key] = router.model_copy(update={"ports": tuple(ports.values())})
        attachments.append(endpoint.model_copy(update={
            "enabled": True, "permissions_resolved": True, "replay_enabled": True,
            "inject_port": selected.inject_port, "eject_port": selected.eject_port,
        }))
    return normalize_topology(graph.model_copy(update={
        "connectivity_state": "complete", "routers": tuple(routers.values()),
        "links": tuple(links), "attachments": tuple(attachments),
    }))


def _bind_graph(graph: CanonicalTopology, binding: TorusBinding, *, generate_links: bool) -> CanonicalTopology:
    return bind_torus_graph(graph, binding.fabrics,
                            tuple(TorusEndpointPorts(e.endpoint_id, e.fabric_id, e.inject_port, e.eject_port,
                                                     "request_source" in e.roles) for e in binding.endpoints),
                            generate_links=generate_links, router_availability=binding.router_overrides,
                            link_availability=binding.link_overrides)


@dataclass(frozen=True)
class TorusRouting:
    """Pure graph/path compiler. Callers separately admit endpoint roles."""

    topology: Topology
    fabrics: Mapping[int, TopologyFabric]
    bindings: Mapping[int, TorusFabricBinding]
    routers: Mapping[RouterKey, TopologyRouter]
    axis_links: Mapping[tuple[int, str, Axis], TopologyLink]
    endpoints: Mapping[str, TopologyAttachment]

    @classmethod
    def from_graph(cls, graph: CanonicalTopology,
                   fabric_bindings: tuple[TorusFabricBinding, ...]) -> TorusRouting:
        graph = normalize_topology(graph)
        _raw_maps(graph, fabric_bindings)
        defaults = {f.fabric_id: f for f in fabric_bindings}
        graph = graph.model_copy(update={
            "routers": tuple(r.model_copy(update={"enabled": _availability(
                r.enabled, defaults[r.fabric_id].router_default, None, f"router {r.key}"
            )}) for r in graph.routers),
            "links": tuple(e.model_copy(update={"enabled": _availability(
                e.enabled, defaults[e.fabric_id].link_default, None, f"link {e.key}"
            )}) for e in graph.links),
        })
        topology = Topology.compile(graph)
        fabrics = {f.fabric_id: f for f in graph.fabrics}
        bindings = {f.fabric_id: f for f in fabric_bindings}
        routers = {r.key: r for r in graph.routers}
        axis_links: dict[tuple[int, str, Axis], TopologyLink] = {}
        for link in graph.links:
            fabric = fabrics[link.fabric_id]
            src, dst = _coordinate(routers[(link.fabric_id, link.src_router)]), _coordinate(routers[(link.fabric_id, link.dst_router)])
            axes: list[Axis] = [axis for axis in AXES if (
                ((src.x + 1) % _extent(fabric, "x"), src.y) if axis == "x"
                else (src.x, (src.y + 1) % _extent(fabric, "y"))
            ) == (dst.x, dst.y)]
            if len(axes) != 1:
                raise ValueError(f"link {link.key}: not a positive unit torus edge")
            axis_name: Axis = axes[0]
            key = (link.fabric_id, link.src_router, axis_name)
            if key in axis_links:
                raise ValueError(f"parallel torus edge at {key}")
            axis_links[key] = link
        if len(axis_links) != 2 * len(routers):
            raise ValueError("torus requires exactly one positive edge per router and axis")
        return TorusRouting(topology, MappingProxyType(fabrics), MappingProxyType(bindings),
                            MappingProxyType(routers), MappingProxyType(axis_links),
                            MappingProxyType({e.endpoint_id: e for e in graph.attachments}))

    def class_base(self, fabric_id: int, traffic_class: TrafficClass) -> int:
        if traffic_class not in {"request", "response"}:
            raise ValueError("unsupported traffic class")
        fabric = self.fabrics[fabric_id]
        return 0 if traffic_class == "request" else 2 * (_extent(fabric, "x") + _extent(fabric, "y")) + 3

    def ejection_rank(self, fabric_id: int, traffic_class: TrafficClass) -> int:
        fabric = self.fabrics[fabric_id]
        return self.class_base(fabric_id, traffic_class) + 2 * (_extent(fabric, "x") + _extent(fabric, "y")) + 1

    def network_path(self, fabric_id: int, source_router: str, destination_router: str,
                     traffic_class: TrafficClass = "request") -> tuple[ResourceHop, ...]:
        """Inspect router paths without granting local endpoint permissions."""
        if (fabric_id, source_router) not in self.routers or (fabric_id, destination_router) not in self.routers:
            raise ValueError("unknown fabric-qualified route router")
        if any(self.routers[(fabric_id, r)].enabled is not True for r in (source_router, destination_router)):
            raise ValueError("route endpoint router is unavailable")
        fabric, binding = self.fabrics[fabric_id], self.bindings[fabric_id]
        first, second = _order(binding)
        current = self.routers[(fabric_id, source_router)]
        destination = _coordinate(self.routers[(fabric_id, destination_router)])
        hops: list[ResourceHop] = []
        base = self.class_base(fabric_id, traffic_class) + 1
        for axis in (first, second):
            size, dateline = _extent(fabric, axis), _position(binding.dateline, axis)
            count = (_position(destination, axis) - _position(_coordinate(current), axis)) % size
            phase = 0
            for _ in range(count):
                source_position = _position(_coordinate(current), axis)
                if source_position == dateline:
                    phase = 1
                q = (source_position - dateline - 1) % size
                rank = base + (q if phase == 0 else size + (q + 1) % size)
                link = self.axis_links[(fabric_id, current.router_id, axis)]
                following = self.routers[(fabric_id, link.dst_router)]
                if link.enabled is not True or following.enabled is not True:
                    raise ValueError(f"deterministic route requires unavailable edge/router at {link.key}")
                hops.append(ResourceHop(
                    lane=LaneIdentity(channel=ChannelIdentity(fabric_id=fabric_id, kind="network", identity=link.link_id),
                                      traffic_class=traffic_class, dateline_phase=phase),
                    src_router=link.src_router, dst_router=link.dst_router,
                    src_port=link.src_port, dst_port=link.dst_port, axis=axis, rank=rank,
                ))
                current = following
            base += 2 * size
        if current.router_id != destination_router or any(a.rank >= b.rank for a, b in pairwise(hops)):
            raise ValueError("compiled torus route violates destination/resource order")
        return tuple(hops)

    def endpoint_path(self, fabric_id: int, source: str, destination: str,
                      traffic_class: TrafficClass) -> RouteRecord:
        """Build a permitted physical path; this does not authorize initiation."""
        if source not in self.endpoints or destination not in self.endpoints:
            raise ValueError("unknown route endpoint")
        src, dst = self.endpoints[source], self.endpoints[destination]
        if any(e.enabled is not True or not e.replay_enabled or not e.permissions_resolved for e in (src, dst)):
            raise ValueError("route endpoint is unavailable or unresolved")
        if src.fabric_id != fabric_id or dst.fabric_id != fabric_id or src.inject_port is None or dst.eject_port is None:
            raise ValueError("route requires same-fabric injection/ejection permissions")

        def local(kind: Literal["inject", "eject"], endpoint: TopologyAttachment, port: str, rank: int) -> ResourceHop:
            return ResourceHop(
                lane=LaneIdentity(channel=ChannelIdentity(fabric_id=fabric_id, kind=kind, identity=endpoint.endpoint_id),
                                  traffic_class=traffic_class, dateline_phase=None),
                src_router=None if kind == "inject" else endpoint.router_id,
                dst_router=endpoint.router_id if kind == "inject" else None,
                src_port=port, dst_port=port, axis=None, rank=rank,
            )

        return RouteRecord(fabric_id=fabric_id, source=source, destination=destination, traffic_class=traffic_class, hops=(
            local("inject", src, src.inject_port, self.class_base(fabric_id, traffic_class)),
            *self.network_path(fabric_id, src.router_id, dst.router_id, traffic_class),
            local("eject", dst, dst.eject_port, self.ejection_rank(fabric_id, traffic_class)),
        ))


@dataclass(frozen=True)
class BoundTorus(TorusRouting):
    contract: PreparedTorusContract
    endpoint_roles: Mapping[str, TorusEndpointBinding]

    @classmethod
    def bind(cls, config: TorusReplay, source_document: object) -> BoundTorus:
        prepared = PreparedTorusContract.prepare(config, source_document)
        profile_source = isinstance(prepared.config.source, ProfileSource)
        inventory = (
            topology_from_profile(HardwareProfileConfig.model_validate_json(prepared.source_json)).graph
            if profile_source else CanonicalTopology.model_validate_json(prepared.source_json)
        )
        graph = _bind_graph(inventory, prepared.config.binding, generate_links=profile_source)
        routing = TorusRouting.from_graph(graph, prepared.config.binding.fabrics)
        for override in prepared.config.network_overrides:
            if (override.fabric_id, override.link_id) not in routing.topology.link_indices:
                raise ValueError("network settings override has no canonical torus link")
        return cls(routing.topology, routing.fabrics, routing.bindings, routing.routers,
                   routing.axis_links, routing.endpoints, prepared,
                   MappingProxyType({e.endpoint_id: e for e in prepared.config.binding.endpoints}))

    def route(self, fabric_id: int, source: str, destination: str, traffic_class: TrafficClass) -> RouteRecord:
        if source not in self.endpoint_roles or destination not in self.endpoint_roles:
            raise ValueError("route endpoint is not in the transport allowlist")
        src, dst = self.endpoints[source], self.endpoints[destination]
        src_roles, dst_roles = self.endpoint_roles[source].roles, self.endpoint_roles[destination].roles
        required_source = "request_source" if traffic_class == "request" else "responder"
        required_sinks = {"request_sink", "responder"} if traffic_class == "request" else {"response_sink"}
        if required_source not in src_roles or not required_sinks.intersection(dst_roles):
            raise ValueError("route endpoint roles do not admit this class")
        if src.fabric_id != fabric_id or dst.fabric_id != fabric_id or src.inject_port is None or dst.eject_port is None:
            raise ValueError("route requires same-fabric injection/ejection permissions")

        return self.endpoint_path(fabric_id, source, destination, traffic_class)

    def validate_route(self, route: RouteRecord) -> None:
        route = RouteRecord.model_validate(route.model_dump(mode="json"))
        if route != self.route(route.fabric_id, route.source, route.destination, route.traffic_class):
            raise ValueError("route differs from the admitted torus/dateline policy")

    def traffic_routes(self) -> tuple[RouteRecord, ...]:
        paths: dict[RouteKey, RouteRecord] = {}
        for traffic in self.contract.config.traffic:
            key: RouteKey = (traffic.fabric_id, traffic.source, traffic.destination, "request")
            paths[key] = self.route(*key)
            if isinstance(traffic, RequestResponseTraffic):
                key = (traffic.fabric_id, traffic.destination, traffic.source, "response")
                paths[key] = self.route(*key)
        return tuple(paths[key] for key in sorted(paths))

    def export(self) -> dict[str, object]:
        return {
            "kind": "torus_binding", "schema_version": 1, "can_execute": False,
            "validation_stage": "topology_binding", "contract_sha256": self.contract.contract_sha256,
            "binding": self.contract.config.binding.model_dump(mode="json"),
            "topology": self.topology.export(),
            "source": json.loads(self.contract.source_json),
            "pending_validation": ["traffic_routes", "route_dependencies", "runtime_admission"],
            "edge_geometry": [{
                "fabric_id": fabric, "link_id": edge.link_id, "raw_axis": axis,
                "source_raw": _coordinate(self.routers[(fabric, source)]).model_dump(mode="json"),
                "destination_raw": _coordinate(self.routers[(fabric, edge.dst_router)]).model_dump(mode="json"),
                "raw_wrap": _position(_coordinate(self.routers[(fabric, source)]), axis) == _extent(self.fabrics[fabric], axis) - 1,
                "dateline": _position(_coordinate(self.routers[(fabric, source)]), axis) == _position(self.bindings[fabric].dateline, axis),
            } for (fabric, source, axis), edge in sorted(self.axis_links.items())],
        }


@dataclass(frozen=True)
class TorusPlan:
    binding: BoundTorus
    record: EffectivePlanRecord
    dependencies: ResourceDependencies

    @classmethod
    def compile(cls, config: TorusReplay, source_document: object) -> TorusPlan:
        binding = BoundTorus.bind(config, source_document)
        routes = binding.traffic_routes()
        for route in routes:
            binding.validate_route(route)
        lookup = {(r.fabric_id, r.source, r.destination, r.traffic_class): r for r in routes}
        pairs = tuple((lookup[(t.fabric_id, t.source, t.destination, "request")],
                       lookup[(t.fabric_id, t.destination, t.source, "response")])
                      for t in binding.contract.config.traffic if isinstance(t, RequestResponseTraffic))
        dependencies = ResourceDependencies.from_paths((r.hops for r in routes), pairs)
        prepared = binding.contract
        record = EffectivePlanRecord(
            policy=prepared.config.policy, source_sha256=prepared.source_sha256,
            contract_sha256=prepared.contract_sha256, source_json=prepared.source_json,
            configuration_json=prepared.configuration_json, quantities=prepared.quantities,
            graph=binding.topology.graph, routes=routes,
        )
        return cls(binding, record, dependencies)

    def export(self) -> dict[str, object]:
        return {
            "kind": "torus_compiled_plan", "schema_version": 1,
            "plan_sha256": self.record.plan_sha256, "graph_sha256": self.binding.topology.content_hash,
            "plan": self.record.model_dump(mode="json"), "binding": self.binding.export(),
            "dependencies": self.dependencies.export(), "can_execute": False,
            "validation_stage": "topology_and_routes",
            "pending_validation": ["slowdown_targets", "runtime_admission"],
            "niu_transactions": "unsupported", "memory_service": "unsupported",
            "compute_execution": "unsupported", "silicon_timing": "unvalidated",
        }
