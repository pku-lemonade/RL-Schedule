"""Opt-in single-fabric/multi-fabric torus byte transport.

The runtime is deliberately bounded and plan-bound. It supports one-way packets
and finite causal request/response fixtures; directed slowdowns and CLI version
dispatch remain later parts of the change.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import simpy
from simpy.events import Event, ProcessGenerator

from .configs.schemas.torus_replay import (
    ChannelIdentity,
    PacketIdentity,
    PacketTraffic,
    RequestResponseTraffic,
    ResourceHop,
    RouteRecord,
    TrafficClass,
)
from .torus import TorusPlan
from .torus_records import (
    PacketResult,
    ResourceState,
    TorusReplayResult,
    TransportTraceEvent,
)
from .utils.definitions import compute_flit_count
from .virtual_channel import LinkContract, VirtualChannelLink

ChannelKey = tuple[int, str, str]


def channel_key(channel: ChannelIdentity) -> ChannelKey:
    return channel.fabric_id, channel.kind, channel.identity


@dataclass
class _PacketState:
    traffic: PacketTraffic
    packet: PacketIdentity
    expected_flits: int
    expected_payload: int
    physical_bytes: int
    received_flits: int = 0
    received_payload: int = 0
    first_injection: float | None = None
    first_ejection: float | None = None
    completion: float | None = None


class _ResponseDescriptors:
    """Finite active-response registers owned by one responder endpoint."""

    def __init__(self, env: simpy.Environment, *, fabric_id: int, endpoint_id: str,
                 capacity: int):
        self.env = env
        self.fabric_id = fabric_id
        self.endpoint_id = endpoint_id
        self.capacity = capacity
        self._owners: dict[str, PacketIdentity] = {}
        self.peak = 0
        self._changed = env.event()

    @property
    def changed(self) -> Event:
        return self._changed

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def try_acquire(self, request: PacketIdentity) -> bool:
        if len(self._owners) >= self.capacity:
            return False
        if request in self._owners.values():
            raise ValueError("request already owns a response descriptor")
        key = f"{request.model_dump_json()}:{len(self._owners)}:{self.env.now}"
        self._owners[key] = request
        self.peak = max(self.peak, len(self._owners))
        self._notify()
        return True

    def release(self, request: PacketIdentity) -> None:
        for key, owner in tuple(self._owners.items()):
            if owner == request:
                del self._owners[key]
                self._notify()
                return
        raise ValueError("response descriptor release does not match an active request")

    def resources(self) -> ResourceState:
        return ResourceState(
            resource_id=f"response-descriptors:{self.endpoint_id}", kind="response_descriptors",
            fabric_id=self.fabric_id, unit="packets", lane=None, capacity=self.capacity,
            available=self.capacity - len(self._owners), occupied=len(self._owners),
            pending_returns=0, peak_occupied=self.peak,
            owners=tuple(dict.fromkeys(self._owners.values())),
        )

    @property
    def is_drained(self) -> bool:
        return not self._owners


class RouterPipeline:
    """Finite transfer-stage capacity shared by all outputs of one router.

    A caller reserves the downstream lane before asking this stage for service.
    The stage has no wait on a physical serializer or receiver while occupied.
    Waiting output identities are bounded by the finite route/channel inventory.
    """

    def __init__(self, env: simpy.Environment, *, fabric_id: int, router_id: str,
                 transfer_aci_cycles: float, initiation_aci_cycles: float,
                 capacity: int):
        self.env = env
        self.fabric_id = fabric_id
        self.router_id = router_id
        self.transfer_aci_cycles = transfer_aci_cycles
        self.initiation_aci_cycles = initiation_aci_cycles
        self.capacity = capacity
        self.active = 0
        self.peak = 0
        self._owners: dict[str, PacketIdentity] = {}
        self._waiting: deque[ChannelIdentity] = deque()
        self._waiting_set: set[str] = set()
        self._next_start = float(env.now)
        self._changed = env.event()

    @property
    def changed(self) -> Event:
        return self._changed

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def try_start(self, output: ChannelIdentity, packet: PacketIdentity) -> bool:
        output_key = output.model_dump_json()
        if output_key not in self._waiting_set:
            self._waiting.append(output)
            self._waiting_set.add(output_key)
        if self.active >= self.capacity or self._waiting[0] != output:
            return False
        if self.env.now < self._next_start:
            return False
        self._waiting.popleft()
        self._waiting_set.remove(output_key)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self._owners[f"{output.model_dump_json()}:{self.active}:{self.env.now}"] = packet
        self._next_start = self.env.now + self.initiation_aci_cycles
        if self.initiation_aci_cycles:
            self.env.process(self._wake_after(self.initiation_aci_cycles))
        self._notify()
        return True

    def _wake_after(self, delay: float) -> ProcessGenerator:
        yield self.env.timeout(delay)
        self._notify()

    def finish(self, output: ChannelIdentity, packet: PacketIdentity) -> None:
        for key, owner in tuple(self._owners.items()):
            if owner == packet and key.startswith(f"{output.model_dump_json()}:"):
                del self._owners[key]
                break
        else:
            raise ValueError("router transfer completion does not match an active owner")
        self.active -= 1
        self._notify()

    def resources(self) -> ResourceState:
        owners = tuple(dict.fromkeys(self._owners.values()))
        return ResourceState(
            resource_id=f"router-pipeline:{self.router_id}", kind="router_pipeline",
            fabric_id=self.fabric_id, unit="flits", lane=None, capacity=self.capacity,
            available=self.capacity - self.active, occupied=self.active, pending_returns=0,
            peak_occupied=self.peak, owners=owners,
        )

    @property
    def is_drained(self) -> bool:
        return self.active == 0 and not self._waiting


class TorusTransport:
    """Finite transport runtime for an already compiled :class:`TorusPlan`."""

    def __init__(self, plan: TorusPlan):
        if plan.binding.contract.config.slowdowns:
            raise ValueError("directed slowdown execution belongs to part 6")
        self.plan = plan
        self.env = simpy.Environment()
        traffic = plan.binding.contract.config.traffic
        self.routes: dict[tuple[int, str, str, str], RouteRecord] = {}
        for item in traffic:
            request_key = (item.fabric_id, item.source, item.destination, "request")
            self.routes[request_key] = next(route for route in plan.record.routes
                                           if (route.fabric_id, route.source, route.destination,
                                               route.traffic_class) == request_key)
            if isinstance(item, RequestResponseTraffic):
                response_key = (item.fabric_id, item.destination, item.source, "response")
                self.routes[response_key] = next(route for route in plan.record.routes
                                                if (route.fabric_id, route.source, route.destination,
                                                    route.traffic_class) == response_key)
        if not self.routes:
            raise ValueError("transport requires at least one admitted packet")
        channel_map = {
            channel_key(hop.lane.channel): hop.lane.channel
            for route in self.routes.values()
            for hop in route.hops
        }
        channels = sorted(channel_map.values(), key=lambda c: (c.fabric_id, c.kind, c.identity))
        self.links = {channel_key(channel): VirtualChannelLink(self.env, LinkContract.from_plan(plan, channel))
                      for channel in channels}
        self.pipelines = self._build_pipelines()
        self._states: dict[PacketIdentity, _PacketState] = {}
        self._request_states: list[_PacketState] = []
        self._response_states: dict[PacketIdentity, _PacketState] = {}
        for item in traffic:
            request = self._packet_state(item, "request")
            self._states[request.packet] = request
            self._request_states.append(request)
            if isinstance(item, RequestResponseTraffic):
                response = self._packet_state(item, "response")
                self._states[response.packet] = response
                self._response_states[response.packet] = response
        self._injection_slots = {
            (endpoint.endpoint_id, traffic_class): simpy.Container(
                self.env, init=endpoint.injection_queue_capacity_packets or 0,
                capacity=endpoint.injection_queue_capacity_packets or 0,
            )
            for endpoint in plan.binding.contract.config.binding.endpoints
            if endpoint.injection_queue_capacity_packets is not None
            for traffic_class in ("request", "response")
            if (traffic_class == "request" and (
                "request_source" in endpoint.roles or "responder" in endpoint.roles
            )) or (traffic_class == "response" and "responder" in endpoint.roles)
        }
        self._descriptors = {
            endpoint.endpoint_id: _ResponseDescriptors(
                self.env, fabric_id=endpoint.fabric_id, endpoint_id=endpoint.endpoint_id,
                capacity=endpoint.response_queue_capacity_packets,
            )
            for endpoint in plan.binding.contract.config.binding.endpoints
            if "responder" in endpoint.roles and endpoint.response_queue_capacity_packets is not None
        }

    def _quantities(self) -> dict[str, int | float]:
        return {item.field_path: item.value for item in self.plan.record.quantities}

    def _packet_state(self, item: PacketTraffic, traffic_class: TrafficClass) -> _PacketState:
        fabric = next(f for f in self.plan.binding.contract.config.fabrics if f.fabric_id == item.fabric_id)
        physical = int(self._quantities()[f"fabrics.{item.fabric_id}.flit.physical_flit_bytes"])
        payload = (item.response_payload_bytes if traffic_class == "response"
                   and isinstance(item, RequestResponseTraffic) else item.payload_bytes)
        count = compute_flit_count(payload, fabric.flit.payload_capacity_bytes)
        return _PacketState(item, PacketIdentity(transfer_id=item.transfer_id, traffic_class=traffic_class),
                            count, payload, count * physical)

    def _clock_ratio(self, fabric_id: int) -> float:
        quantities = self._quantities()
        return float(quantities[f"fabrics.{fabric_id}.noc_clock"]) / float(quantities["aci_clock"])

    def _build_pipelines(self) -> dict[tuple[int, str], RouterPipeline]:
        quantities = self._quantities()
        pipelines: dict[tuple[int, str], RouterPipeline] = {}
        for route in self.routes.values():
            for hop in route.hops[:-1]:
                router = hop.dst_router
                if router is None:
                    raise ValueError("route transition has no receiving router")
                key = (route.fabric_id, router)
                if key in pipelines:
                    continue
                settings = next(f.router for f in self.plan.binding.contract.config.fabrics
                                if f.fabric_id == route.fabric_id)
                ratio = float(quantities[f"fabrics.{route.fabric_id}.noc_clock"]) / float(quantities["aci_clock"])
                pipelines[key] = RouterPipeline(
                    self.env, fabric_id=route.fabric_id, router_id=router,
                    transfer_aci_cycles=settings.transfer_noc_cycles / ratio,
                    initiation_aci_cycles=settings.transfer_initiation_interval_noc_cycles / ratio,
                    capacity=settings.transfer_capacity_flits,
                )
        return pipelines

    def _service_aci(self, endpoint_id: str) -> float:
        endpoint = next(e for e in self.plan.binding.contract.config.binding.endpoints
                        if e.endpoint_id == endpoint_id)
        assert endpoint.sink_service is not None
        if endpoint.sink_service.timebase == "aci":
            return float(endpoint.sink_service.cycles)
        return float(endpoint.sink_service.cycles) / self._clock_ratio(endpoint.fabric_id)

    def _response_service_aci(self, endpoint_id: str) -> float:
        endpoint = next(e for e in self.plan.binding.contract.config.binding.endpoints
                        if e.endpoint_id == endpoint_id)
        if endpoint.response_service is None:
            raise ValueError("responder has no admitted response service")
        if endpoint.response_service.timebase == "aci":
            return float(endpoint.response_service.cycles)
        return float(endpoint.response_service.cycles) / self._clock_ratio(endpoint.fabric_id)

    def _link_for(self, hop: ResourceHop) -> VirtualChannelLink:
        return self.links[channel_key(hop.lane.channel)]

    def _wait_reserve(self, link: VirtualChannelLink, packet: PacketIdentity, index: int) -> ProcessGenerator:
        envelope = link.contract.envelope(packet, index)
        token = link.try_reserve(envelope)
        while token is None:
            yield link.changed
            token = link.try_reserve(envelope)
        link.make_ready(token)
        return token

    def _inject(self, state: _PacketState, route: RouteRecord,
                descriptor: _ResponseDescriptors | None = None,
                request_packet: PacketIdentity | None = None) -> ProcessGenerator:
        item = state.traffic
        if state.packet.traffic_class == "request" and item.start_aci_cycles:
            yield self.env.timeout(item.start_aci_cycles)
        slot = self._injection_slots[(item.destination if state.packet.traffic_class == "response"
                                      else item.source, state.packet.traffic_class)]
        yield slot.get(1)
        link = self._link_for(route.hops[0])
        first_token = None
        for index in range(state.expected_flits):
            token = yield from self._wait_reserve(link, state.packet, index)
            if first_token is None:
                first_token = token
        if descriptor is not None:
            if request_packet is None or first_token is None:
                raise ValueError("response injection is missing its causal descriptor")
            link.log_event("response_ready", first_token, duration=self._response_service_aci(item.destination))
            descriptor.release(request_packet)
        yield slot.put(1)

    def _forward(self, state: _PacketState, route: RouteRecord, hop_index: int) -> ProcessGenerator:
        incoming = self._link_for(route.hops[hop_index - 1])
        outgoing = self._link_for(route.hops[hop_index])
        in_lane = route.hops[hop_index - 1].lane
        router_id = route.hops[hop_index - 1].dst_router
        if router_id is None:
            raise ValueError("forwarding hop has no router transfer stage")
        pipeline = self.pipelines[(route.fabric_id, router_id)]
        output_channel = route.hops[hop_index].lane.channel
        for index in range(state.expected_flits):
            token = incoming.take(in_lane)
            while token is None:
                yield incoming.changed
                token = incoming.take(in_lane)
            out_token = outgoing.try_reserve(outgoing.contract.envelope(state.packet, index))
            while out_token is None:
                yield outgoing.changed
                out_token = outgoing.try_reserve(outgoing.contract.envelope(state.packet, index))
            while not pipeline.try_start(output_channel, state.packet):
                yield pipeline.changed
            outgoing.log_event("transfer_start", out_token,
                               duration=pipeline.transfer_aci_cycles,
                               router_id=route.hops[hop_index - 1].dst_router)
            yield self.env.timeout(pipeline.transfer_aci_cycles)
            outgoing.log_event("transfer_end", out_token,
                               duration=pipeline.transfer_aci_cycles,
                               router_id=route.hops[hop_index - 1].dst_router)
            outgoing.make_ready(out_token)
            pipeline.finish(output_channel, state.packet)
            incoming.release(token)

    def _sink(self, state: _PacketState, route: RouteRecord) -> ProcessGenerator:
        link = self._link_for(route.hops[-1])
        lane = route.hops[-1].lane
        service = self._service_aci(state.traffic.destination)
        for _ in range(state.expected_flits):
            token = link.take(lane)
            while token is None:
                yield link.changed
                token = link.take(lane)
            state.received_flits += 1
            state.received_payload += token.envelope.payload_bytes
            if state.first_ejection is None:
                state.first_ejection = self.env.now
            if service:
                yield self.env.timeout(service)
            state.completion = self.env.now
            link.log_event("sink_complete", token, duration=service)
            link.release(token)
        state.completion = self.env.now
        if state.packet.traffic_class == "request" and isinstance(state.traffic, RequestResponseTraffic):
            yield from self._generate_response(state)

    def _generate_response(self, request: _PacketState) -> ProcessGenerator:
        item = request.traffic
        if not isinstance(item, RequestResponseTraffic):
            raise TypeError("only request/response traffic can generate a response")
        descriptor = self._descriptors[item.destination]
        while not descriptor.try_acquire(request.packet):
            yield descriptor.changed
        service = self._response_service_aci(item.destination)
        if service:
            yield self.env.timeout(service)
        response = self._response_states[PacketIdentity(transfer_id=item.transfer_id, traffic_class="response")]
        route = self.routes[(item.fabric_id, item.destination, item.source, "response")]
        self.env.process(self._inject(response, route, descriptor, request.packet))

    def _start(self) -> None:
        for state in self._request_states:
            route = self.routes[(state.traffic.fabric_id, state.traffic.source,
                                 state.traffic.destination, "request")]
            self.env.process(self._inject(state, route))
            for hop_index in range(1, len(route.hops)):
                self.env.process(self._forward(state, route, hop_index))
            self.env.process(self._sink(state, route))
        for state in self._response_states.values():
            route = self.routes[(state.traffic.fabric_id, state.traffic.destination,
                                 state.traffic.source, "response")]
            for hop_index in range(1, len(route.hops)):
                self.env.process(self._forward(state, route, hop_index))
            self.env.process(self._sink(state, route))

    def _trace(self) -> tuple[TransportTraceEvent, ...]:
        return tuple(sorted((event for link in self.links.values() for event in link.events),
                            key=lambda event: (event.time_aci_cycles, event.action,
                                               event.fabric_id, event.token_id or "")))

    def _resources(self) -> tuple[ResourceState, ...]:
        link_resources = [resource for link in self.links.values() for resource in link.resources()]
        pipeline_resources = [pipeline.resources() for pipeline in self.pipelines.values()]
        descriptor_resources = [descriptor.resources() for descriptor in self._descriptors.values()]
        return tuple(sorted((*link_resources, *pipeline_resources, *descriptor_resources),
                            key=lambda resource: (resource.fabric_id, resource.resource_id)))

    def run(self, *, max_aci_cycles: float | None = None) -> TorusReplayResult:
        self._start()
        limit = max_aci_cycles
        if limit is None:
            limit = float(self.plan.binding.contract.config.max_aci_cycles)
        while self.env.peek() != float("inf") and self.env.peek() <= limit:
            self.env.step()
        trace = self._trace()
        resources = self._resources()
        complete_packets = all(state.completion is not None for state in self._states.values())
        drained = (
            all(resource.is_drained for resource in resources)
            and all(link.is_drained for link in self.links.values())
            and all(pipeline.is_drained for pipeline in self.pipelines.values())
            and all(descriptor.is_drained for descriptor in self._descriptors.values())
        )
        complete = complete_packets and drained and self.env.peek() == float("inf")
        reason = "drained" if complete else ("cycle_limit" if self.env.peek() != float("inf") else "idle_with_pending")
        packet_results = tuple(PacketResult(
            packet=state.packet, fabric_id=state.traffic.fabric_id,
            source=(state.traffic.destination if state.packet.traffic_class == "response"
                    else state.traffic.source),
            destination=(state.traffic.source if state.packet.traffic_class == "response"
                         else state.traffic.destination),
            expected_payload_bytes=state.expected_payload, received_payload_bytes=state.received_payload,
            expected_flits=state.expected_flits, received_flits=state.received_flits,
            physical_bytes=state.physical_bytes, first_injection_aci_cycles=next(
                (e.time_aci_cycles for e in trace if e.action == "link_launch" and e.packet == state.packet
                 and e.channel is not None and e.channel.kind == "inject"), None),
            first_ejection_aci_cycles=state.first_ejection, completion_aci_cycles=state.completion,
        ) for state in sorted(self._states.values(), key=lambda item: (item.packet.transfer_id,
                                                                        item.packet.traffic_class)))
        elapsed = float(self.env.now)
        return TorusReplayResult(
            kind="topology_replay_result", schema_version=2, plan=self.plan.record,
            plan_sha256=self.plan.record.plan_sha256, runtime_indices=(),
            status="complete" if complete else "incomplete", reason=reason,
            elapsed_aci_cycles=elapsed,
            expected_payload_bytes=sum(state.expected_payload for state in self._states.values()),
            received_payload_bytes=sum(state.received_payload for state in self._states.values()),
            packet_physical_bytes=sum(state.physical_bytes for state in self._states.values()),
            transmitted_channel_bytes=sum(event.physical_bytes for event in trace if event.action == "link_launch"),
            packets=packet_results, resources=resources, trace=trace,
            execution="torus_unicast_byte_transport", niu_transactions="unsupported",
            memory_service="unsupported", compute_execution="unsupported", silicon_timing="unvalidated",
        )
