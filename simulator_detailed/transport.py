"""Admitted, finite synthetic byte replay over shared directed Router/Link kernels."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

import simpy
from pydantic import JsonValue
from simpy.events import ProcessGenerator

from .configs.schemas.arch_config import LinkConfig, RouterConfig, RouterPipelineConfig
from .configs.schemas.topology import (
    CanonicalTopology,
    ReplayFabricSettings,
    ReplayLinkSettings,
    ReplayResult,
    ReplayTraceEvent,
    ReplayTraffic,
    ReplayTransferResult,
    TopologyReplay,
)
from .noc import FlitAction, Link, NoCTracer, Router
from .routing import CompiledRoute, ExplicitRouting, channel_id
from .topology import Topology, canonical_json, content_digest
from .utils.definitions import (
    BurstLenMode,
    Flit,
    FlitConfig,
    FlitType,
    NoCChannel,
    compute_flit_count,
)

ChannelKind = Literal["network", "inject", "eject"]


def json_object(value: object) -> dict[str, JsonValue]:
    """Detach a JSON export from mutable runtime objects."""
    return cast(dict[str, JsonValue], json.loads(canonical_json(value)))


@dataclass(frozen=True)
class ChannelPlan:
    channel_id: str
    kind: ChannelKind
    fabric_id: int
    identity: str
    settings: ReplayLinkSettings


@dataclass(frozen=True)
class ReplayPlan:
    topology: Topology
    config: TopologyReplay
    routing: ExplicitRouting
    channels: Mapping[str, ChannelPlan]
    fabrics: Mapping[int, ReplayFabricSettings]
    traffic_routes: Mapping[str, CompiledRoute]
    effective_json: str

    @classmethod
    def compile(cls, topology: Topology, config: TopologyReplay) -> ReplayPlan:
        # Revalidate before creating an environment, process, queue or output file.
        topology = Topology.compile(topology.graph)
        config = TopologyReplay.model_validate(config.model_dump(mode="json"))
        config = config.model_copy(update={
            "fabrics": tuple(sorted(config.fabrics, key=lambda f: f.fabric_id)),
            "routes": tuple(sorted(config.routes, key=lambda r: (r.fabric_id, r.source, r.destination))),
            "traffic": tuple(sorted(config.traffic, key=lambda t: t.transfer_id)),
            "network_overrides": tuple(sorted(config.network_overrides, key=lambda o: (o.fabric_id, o.link_id))),
            "local_overrides": tuple(sorted(config.local_overrides, key=lambda o: (o.endpoint_id, o.direction))),
        })
        graph = topology.graph
        if graph.connectivity_state != "complete" or graph.origin.kind == "hardware_profile":
            raise ValueError("hardware profiles/unresolved connectivity support inventory only")
        fabrics = {f.fabric_id: f for f in config.fabrics}
        if set(fabrics) != {f.fabric_id for f in graph.fabrics}:
            raise ValueError("replay must configure exactly the graph fabrics")
        for fabric in graph.fabrics:
            if fabric.topology_policy not in {"explicit", "mesh"} or fabric.routing_policy not in {
                "explicit_unicast", "dimension_order_xy"
            }:
                raise NotImplementedError("replay does not implement this topology/routing policy")
        routers = {r.key: r for r in graph.routers}
        channels: dict[str, ChannelPlan] = {}
        network_overrides = {(o.fabric_id, o.link_id): o.settings for o in config.network_overrides}
        local_overrides = {(o.endpoint_id, o.direction): o.settings for o in config.local_overrides}
        for edge in graph.links:
            if edge.enabled is not True:
                continue
            if any(routers[(edge.fabric_id, r)].enabled is not True for r in (edge.src_router, edge.dst_router)):
                raise ValueError("enabled edge requires enabled routers")
            settings = network_overrides.pop(edge.key, fabrics[edge.fabric_id].network_link)
            channel = channel_id("network", edge.fabric_id, edge.link_id)
            channels[channel] = ChannelPlan(channel, "network", edge.fabric_id, edge.link_id, settings)
        for endpoint in graph.attachments:
            if not endpoint.replay_enabled:
                continue
            for kind, port in (("inject", endpoint.inject_port), ("eject", endpoint.eject_port)):
                if port is None:
                    continue
                kind = cast(ChannelKind, kind)
                settings = local_overrides.pop((endpoint.endpoint_id, kind), fabrics[endpoint.fabric_id].local_link)
                channel = channel_id(kind, endpoint.fabric_id, endpoint.endpoint_id)
                channels[channel] = ChannelPlan(channel, kind, endpoint.fabric_id, endpoint.endpoint_id, settings)
        if network_overrides or local_overrides:
            raise ValueError("override refers to an unknown or uninstantiated channel")
        formats = {f: FlitConfig.model_validate(s.flit.model_dump()) for f, s in fabrics.items()}
        # Validate defaults too: unresolved/unused parameters must not hide invalid units.
        for fabric in config.fabrics:
            for settings in (fabric.network_link, fabric.local_link):
                _validate_link(settings, fabric, config.aci_clock_mhz, formats[fabric.fabric_id])
        for channel in channels.values():
            _validate_link(channel.settings, fabrics[channel.fabric_id], config.aci_clock_mhz, formats[channel.fabric_id])
        effective = config.model_dump(mode="json", exclude={"graph_path"})
        effective["graph_sha256"] = topology.content_hash
        effective["channels"] = [
            {"channel_id": c.channel_id, "kind": c.kind, "fabric_id": c.fabric_id,
             "identity": c.identity, "settings": c.settings.model_dump(mode="json")}
            for c in sorted(channels.values(), key=lambda c: c.channel_id)
        ]
        plan_id = content_digest(effective)
        routing = ExplicitRouting.compile(topology, config.routes, formats, plan_id)
        by_pair = {(p.definition.fabric_id, p.definition.source, p.definition.destination): p
                   for p in routing.paths.values()}
        traffic_routes: dict[str, CompiledRoute] = {}
        for traffic in config.traffic:
            key = (traffic.fabric_id, traffic.source, traffic.destination)
            if key not in by_pair:
                raise ValueError(f"transfer {traffic.transfer_id} has no admitted route")
            traffic_routes[traffic.transfer_id] = by_pair[key]
        return cls(topology, config, routing, MappingProxyType(channels), MappingProxyType(fabrics),
                   MappingProxyType(traffic_routes), canonical_json(effective))

    @classmethod
    def load(cls, path: str | Path) -> ReplayPlan:
        path = Path(path)
        config = TopologyReplay.model_validate_json(path.read_text())
        graph = CanonicalTopology.model_validate_json((path.parent / config.graph_path).read_text())
        return cls.compile(Topology.compile(graph), config)


def _validate_link(settings: ReplayLinkSettings, fabric: ReplayFabricSettings,
                   aci_clock: float, flit: FlitConfig) -> None:
    config = LinkConfig.model_validate(settings.model_dump())
    ratio = fabric.noc_clock_mhz / aci_clock
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("NoC/ACI clock ratio must be finite and positive")
    if config.launch_interval_aci_cycles < config.serialization_aci_cycles(ratio, flit):
        raise ValueError("launch interval cannot be shorter than serialization")
    if config.effective_in_flight_window_flits < config.required_in_flight_window_flits(ratio, flit):
        raise ValueError("in-flight window is too small for configured link timing")


class ReplayRuntime:
    """One-use runtime. Inventory records are not compute or memory services."""

    def __init__(self, plan: ReplayPlan):
        self.plan = plan
        self.env = simpy.Environment()
        self.tracers = {f: NoCTracer(NoCChannel(f)) for f in plan.fabrics}
        self.routers: dict[tuple[int, str], Router] = {}
        self.links: dict[str, Link] = {}
        self._trace_channels: dict[tuple[int, str], ChannelPlan] = {}
        self._injection_locks: dict[str, simpy.Resource] = {}
        self._received: dict[str, list[Flit]] = {t.transfer_id: [] for t in plan.config.traffic}
        self._completion: dict[str, float] = {}
        self._sent: set[str] = set()
        self._started = False
        self._traffic = {i: t for i, t in enumerate(plan.config.traffic)}
        graph = plan.topology.graph
        for record in sorted(graph.routers, key=lambda r: (r.fabric_id, plan.topology.router_indices[r.key])):
            if record.enabled is not True:
                continue
            settings = plan.fabrics[record.fabric_id]
            config = RouterConfig(type="explicit_unicast", vc=1, arbitration="round_robin",
                                  default_burst_len_mode=BurstLenMode(settings.burst_quantum_flits),
                                  flit=plan.routing.formats[record.fabric_id], pipeline=RouterPipelineConfig(
                                      effective_rc_aci_cycles=settings.effective_rc_aci_cycles,
                                      effective_sa_aci_cycles=settings.effective_sa_aci_cycles,
                                      effective_st_aci_cycles=settings.effective_st_aci_cycles))
            self.routers[record.key] = Router(
                self.env, config, plan.topology.router_indices[record.key], None, None,
                NoCChannel(record.fabric_id), self.tracers[record.fabric_id],
                routing=plan.routing, transport_context=plan.routing)
        edges = {e.key: e for e in graph.links}
        attachments = {a.endpoint_id: a for a in graph.attachments}
        for channel in sorted(plan.channels.values(), key=lambda c: c.channel_id):
            fabric = channel.fabric_id
            edge = edges[(fabric, channel.identity)] if channel.kind == "network" else None
            link = Link(
                self.env, LinkConfig.model_validate(channel.settings.model_dump()), NoCChannel(fabric),
                self.tracers[fabric], channel.channel_id,
                noc_cycles_per_aci_cycle=plan.fabrics[fabric].noc_clock_mhz / plan.config.aci_clock_mhz,
                flit_format=plan.routing.formats[fabric],
                link_id=None if edge is None else plan.topology.link_indices[edge.key],
                src_router=None if edge is None else plan.topology.router_indices[(fabric, edge.src_router)],
                dst_router=None if edge is None else plan.topology.router_indices[(fabric, edge.dst_router)],
                transport_context=plan.routing, graph_channel_id=channel.channel_id)
            self.links[channel.channel_id] = link
            self._trace_channels[(fabric, link.link_name)] = channel
            if edge is not None:
                self.routers[(fabric, edge.src_router)].bind_output(
                    plan.topology.port_indices[(fabric, edge.src_router, edge.src_port)], link)
                self.routers[(fabric, edge.dst_router)].bind_input(
                    plan.topology.port_indices[(fabric, edge.dst_router, edge.dst_port)], link)
            else:
                endpoint = attachments[channel.identity]
                resolved = plan.routing.endpoint(endpoint.endpoint_id)
                router = self.routers[(fabric, endpoint.router_id)]
                if channel.kind == "inject":
                    assert resolved.inject_port is not None
                    router.bind_input(resolved.inject_port, link)
                    self._injection_locks[endpoint.endpoint_id] = simpy.Resource(self.env, capacity=1)
                else:
                    assert resolved.eject_port is not None
                    router.bind_output(resolved.eject_port, link)
                    self.env.process(self._sink(endpoint.endpoint_id, link))

    def packet(self, msg_id: int, traffic: ReplayTraffic) -> tuple[Flit, ...]:
        source = self.plan.routing.endpoint(traffic.source)
        destination = self.plan.routing.endpoint(traffic.destination)
        fmt = self.plan.routing.formats[traffic.fabric_id]
        count = compute_flit_count(traffic.payload_bytes, fmt.payload_capacity_bytes)
        assert source.inject_port is not None and destination.eject_port is not None
        return tuple(Flit(
            format=fmt, mesh_x=None, mesh_y=None, transport_id=self.plan.routing.plan_id,
            flit_type=_flit_type(index, count),
            payload_bytes=min(fmt.payload_capacity_bytes, traffic.payload_bytes - index * fmt.payload_capacity_bytes),
            msg_id=msg_id, fabric_id=NoCChannel(traffic.fabric_id), src_router=source.router_id,
            src_local_port=source.inject_port, dst_router=destination.router_id, dst_local_port=destination.eject_port,
            burst_len_mode=BurstLenMode(traffic.burst_quantum_flits),
        ) for index in range(count))

    def _source(self, msg_id: int, traffic: ReplayTraffic) -> ProcessGenerator:
        yield self.env.timeout(traffic.start_aci_cycles)
        request = self._injection_locks[traffic.source].request()
        with request:
            yield request
            link = self.links[channel_id("inject", traffic.fabric_id, traffic.source)]
            for flit in self.packet(msg_id, traffic):
                yield link.send_flit(flit)
            self._sent.add(traffic.transfer_id)

    def _sink(self, endpoint_id: str, link: Link) -> ProcessGenerator:
        while True:
            flit = cast(Flit, (yield link.recv_flit()))
            traffic = self._traffic[flit.msg_id]
            if traffic.destination != endpoint_id:
                raise RuntimeError("flit delivered to the wrong terminal")
            received = self._received[traffic.transfer_id]
            fmt = self.plan.routing.formats[traffic.fabric_id]
            count = compute_flit_count(traffic.payload_bytes, fmt.payload_capacity_bytes)
            index = len(received)
            expected_bytes = min(fmt.payload_capacity_bytes, traffic.payload_bytes - index * fmt.payload_capacity_bytes)
            if index >= count or flit.flit_type != _flit_type(index, count) or flit.payload_bytes != expected_bytes:
                raise RuntimeError("terminal received invalid packet order or byte count")
            yield self.env.timeout(self.plan.config.sink_service_aci_cycles_per_flit)
            received.append(flit)
            link.ack_credit()
            if len(received) == count:
                self._completion[traffic.transfer_id] = float(self.env.now)

    def pending(self) -> tuple[str, ...]:
        pending = [f"transfer:{t.transfer_id}" for t in self.plan.config.traffic
                   if t.transfer_id not in self._completion or t.transfer_id not in self._sent]
        for identity, link in self.links.items():
            if not link.is_drained:
                pending.append(f"channel:{identity}")
        for (fabric, identity), router in self.routers.items():
            if not router.is_drained:
                pending.append(f"router:{fabric}:{identity}")
        return tuple(sorted(pending))

    def run(self) -> ReplayResult:
        if self._started:
            raise RuntimeError("replay runtimes may run only once")
        self._started = True
        for msg_id, traffic in self._traffic.items():
            self.env.process(self._source(msg_id, traffic))
        reason: Literal["drained", "cycle_limit", "idle_with_pending"] = "drained"
        # No periodic watchdog event: an empty queue with pending state is visible.
        while True:
            next_time = self.env.peek()
            if next_time == float("inf"):
                if self.pending():
                    reason = "idle_with_pending"
                break
            if next_time > self.plan.config.max_aci_cycles:
                if self.pending():
                    if self.env.now < self.plan.config.max_aci_cycles:
                        self.env.run(until=self.plan.config.max_aci_cycles)
                    reason = "cycle_limit"
                break
            self.env.step()
        return self._result(reason)

    def _result(self, reason: Literal["drained", "cycle_limit", "idle_with_pending"]) -> ReplayResult:
        topology = self.plan.topology
        router_ids = {(f, i): r for (f, r), i in topology.router_indices.items()}
        port_ids = {(f, topology.router_indices[(f, r)], i): p for (f, r, p), i in topology.port_indices.items()}
        events: list[ReplayTraceEvent] = []
        for fabric, tracer in self.tracers.items():
            for event in tracer.events:
                channel = self._trace_channels[(fabric, event.link_name)] if event.link_name else None
                traffic = self._traffic.get(event.msg_id)
                router = router_ids.get((fabric, event.router_id))
                events.append(ReplayTraceEvent(
                    time_aci_cycles=event.time, action=event.action.name, fabric_id=fabric, plane=event.plane.name,
                    transfer_id=None if traffic is None else traffic.transfer_id, router_id=router,
                    port_id=port_ids.get((fabric, event.router_id, event.port)),
                    out_port_id=port_ids.get((fabric, event.router_id, event.out_port)) if event.action in {
                        FlitAction.ROUTER_RC, FlitAction.ROUTER_ST, FlitAction.ROUTER_SA_GRANT,
                        FlitAction.ROUTER_SA_RELEASE, FlitAction.STALL_SA} else None,
                    channel_id=None if channel is None else channel.channel_id,
                    channel_kind=None if channel is None else channel.kind,
                    link_id=channel.identity if channel is not None and channel.kind == "network" else None,
                    endpoint_id=channel.identity if channel is not None and channel.kind != "network" else None,
                    payload_bytes=event.payload_bytes,
                    physical_bytes=self.plan.routing.formats[fabric].physical_flit_bytes if traffic else 0,
                    is_tail=event.is_tail, grant_flits=event.grant_flits))
        transfers: list[ReplayTransferResult] = []
        for traffic in self.plan.config.traffic:
            received = self._received[traffic.transfer_id]
            fmt = self.plan.routing.formats[traffic.fabric_id]
            count = compute_flit_count(traffic.payload_bytes, fmt.payload_capacity_bytes)
            transfers.append(ReplayTransferResult(
                transfer_id=traffic.transfer_id, fabric_id=traffic.fabric_id, source=traffic.source,
                destination=traffic.destination, expected_payload_bytes=traffic.payload_bytes,
                received_payload_bytes=sum(f.payload_bytes for f in received), expected_flits=count,
                received_flits=len(received), physical_bytes=count * fmt.physical_flit_bytes,
                completion_aci_cycles=self._completion.get(traffic.transfer_id)))
        return ReplayResult(
            status="complete" if reason == "drained" else "incomplete", reason=reason,
            graph_sha256=topology.content_hash, plan_sha256=self.plan.routing.plan_id,
            elapsed_aci_cycles=float(self.env.now), expected_payload_bytes=sum(t.expected_payload_bytes for t in transfers),
            received_payload_bytes=sum(t.received_payload_bytes for t in transfers),
            packet_physical_bytes=sum(t.physical_bytes for t in transfers),
            transmitted_channel_bytes=sum(e.physical_bytes for e in events if e.action == "LINK_SEND"),
            graph=json_object(topology.export()), effective_plan=cast(dict[str, JsonValue], json.loads(self.plan.effective_json)),
            instantiated=json_object({
                "routers": [{"fabric_id": f, "router_id": r, "runtime_index": runtime.id}
                            for (f, r), runtime in self.routers.items()],
                "channels": list(self.links), "terminals": [a.endpoint_id for a in topology.graph.attachments if a.replay_enabled],
                "compute_services": [], "memory_services": [],
            }),
            pending=self.pending(), transfers=tuple(transfers),
            trace=tuple(sorted(events, key=lambda e: (e.time_aci_cycles, e.fabric_id))))


def _flit_type(index: int, count: int) -> FlitType:
    if count == 1:
        return FlitType.SINGLE
    if index == 0:
        return FlitType.HEAD
    return FlitType.TAIL if index == count - 1 else FlitType.BODY
