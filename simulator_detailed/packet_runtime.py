"""Bounded streaming endpoints around the shared link/router service kernel.

The finite packet inventory is control metadata. Each live producer/consumer
flit occupies an explicit staging lease, including while its hook is waiting.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, Self

import simpy
from pydantic import model_validator
from simpy.events import Event, ProcessGenerator

from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
)
from .configs.schemas.torus_replay import (
    Cycles,
    LaneIdentity,
    PacketIdentity,
    RouteRecord,
    TrafficClass,
)
from .packet_transport import LinkService, PacketFlit, RouterPipeline, forward_flit
from .torus_records import ResourceState, TransportTraceEvent
from .virtual_channel import CreditToken, VirtualChannelLink

EndpointKey = tuple[str, TrafficClass]


class RouterServiceConfig(GraphRecord):
    transfer_aci_cycles: Cycles
    initiation_aci_cycles: Cycles
    capacity_flits: PositiveInt


class PacketDefinition(GraphRecord):
    packet: PacketIdentity
    route: RouteRecord
    flit_count: PositiveInt
    useful_bytes: Index
    physical_bytes: PositiveInt
    after_packet: PacketIdentity | None = None


@dataclass(frozen=True)
class PacketNetworkPlan:
    plan_sha256: str
    packets: Mapping[PacketIdentity, PacketDefinition]
    links: Mapping[str, LinkService]
    routers: Mapping[tuple[int, str], RouterServiceConfig]
    endpoint_queue_capacity_packets: int
    endpoint_staging_capacity_flits: int


class PacketHooks(Protocol):
    """Trusted simulator hooks; JSON cannot inject callbacks or retain payloads.

    Both hooks run under a counted one-flit staging lease. A future memory hook
    must finish/release memory service before returning to network admission.
    """

    @property
    def env(self) -> simpy.Environment: ...
    def produce(self, flit: PacketFlit) -> ProcessGenerator: ...
    def consume(self, flit: PacketFlit) -> ProcessGenerator: ...


class PacketBufferState(GraphRecord):
    endpoint_id: Identifier
    traffic_class: TrafficClass
    kind: Literal["tx_descriptors", "tx_staging", "rx_staging"]
    capacity: PositiveInt
    available: Index
    occupied: Index
    peak_occupied: Index
    owners: tuple[PacketIdentity, ...]

    @model_validator(mode="after")
    def conservation(self) -> Self:
        if self.available + self.occupied != self.capacity or not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("endpoint staging/descriptor capacity is not conserved")
        return self


class PacketEndpointEvent(GraphRecord):
    time_aci_cycles: Cycles
    endpoint_id: Identifier
    traffic_class: TrafficClass
    kind: Literal["tx_descriptors", "tx_staging", "rx_staging"]
    action: Literal["acquire", "release"]
    packet: PacketIdentity
    lease_id: Index
    occupied: Index


class PacketDelivery(GraphRecord):
    packet: PacketIdentity
    submitted: bool
    received_flits: Index
    received_useful_bytes: Index
    handoff_aci_cycles: Cycles | None
    completion_aci_cycles: Cycles | None


class PacketTransportResult(GraphRecord):
    kind: Literal["packet_transport_result"] = "packet_transport_result"
    plan_sha256: Digest
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending"]
    elapsed_aci_cycles: Cycles
    packets: tuple[PacketDelivery, ...]
    resources: tuple[ResourceState, ...]
    endpoint_buffers: tuple[PacketBufferState, ...]
    trace: tuple[TransportTraceEvent, ...]
    endpoint_trace: tuple[PacketEndpointEvent, ...]
    transmitted_channel_bytes: Index
    received_useful_bytes: Index
    pending_packets: tuple[PacketIdentity, ...]
    execution: Literal["wire_packets_only"] = "wire_packets_only"
    memory_service: Literal["unsupported", "external_hooks"] = "unsupported"

    @model_validator(mode="after")
    def totals(self) -> Self:
        if self.transmitted_channel_bytes != sum(e.physical_bytes for e in self.trace if e.action == "link_launch"):
            raise ValueError("channel total must count actual launches")
        if self.received_useful_bytes != sum(p.received_useful_bytes for p in self.packets):
            raise ValueError("useful receipt total disagrees with packets")
        if (self.status == "complete") != (self.reason == "drained"):
            raise ValueError("only drained transport can be complete")
        if self.status == "complete" and (self.pending_packets or any(not r.is_drained for r in self.resources)
                                         or any(b.occupied for b in self.endpoint_buffers)
                                         or any(p.completion_aci_cycles is None for p in self.packets)):
            raise ValueError("completed transport has pending work")
        return self


@dataclass(frozen=True, eq=False)
class _Lease:
    sequence: int
    packet: PacketIdentity


class _Budget:
    def __init__(self, runtime: PacketTransport, endpoint: EndpointKey,
                 kind: Literal["tx_descriptors", "tx_staging", "rx_staging"], capacity: int):
        self.runtime, self.endpoint, self.capacity = runtime, endpoint, capacity
        self.kind: Literal["tx_descriptors", "tx_staging", "rx_staging"] = kind
        self.owners: dict[int, _Lease] = {}
        self.peak = 0
        self.sequence = 0

    @property
    def full(self) -> bool:
        return len(self.owners) == self.capacity

    def acquire(self, packet: PacketIdentity) -> _Lease:
        if self.full:
            raise ValueError("endpoint budget exhausted")
        lease = _Lease(self.sequence, packet)
        self.sequence += 1
        self.owners[lease.sequence] = lease
        self.peak = max(self.peak, len(self.owners))
        self._log("acquire", lease)
        return lease

    def release(self, lease: _Lease) -> None:
        if self.owners.get(lease.sequence) is not lease:
            raise ValueError("foreign or duplicate staging lease")
        del self.owners[lease.sequence]
        self._log("release", lease)

    def _log(self, action: Literal["acquire", "release"], lease: _Lease) -> None:
        self.runtime.endpoint_events.append(PacketEndpointEvent(
            time_aci_cycles=self.runtime.env.now, endpoint_id=self.endpoint[0], traffic_class=self.endpoint[1],
            kind=self.kind, action=action, packet=lease.packet, lease_id=lease.sequence, occupied=len(self.owners)))
        self.runtime.notify_changed()

    def snapshot(self) -> PacketBufferState:
        return PacketBufferState(endpoint_id=self.endpoint[0], traffic_class=self.endpoint[1], kind=self.kind,
                                 capacity=self.capacity, available=self.capacity - len(self.owners),
                                 occupied=len(self.owners), peak_occupied=self.peak,
                                 owners=tuple(dict.fromkeys(v.packet for v in self.owners.values())))


@dataclass
class _Transmit:
    descriptors: _Budget
    staging: _Budget
    pending: deque[PacketIdentity] = field(default_factory=lambda: deque[PacketIdentity]())
    ready: deque[tuple[_Lease, PacketFlit]] = field(default_factory=lambda: deque[tuple[_Lease, PacketFlit]]())
    accepted: dict[PacketIdentity, _Lease] = field(default_factory=lambda: dict[PacketIdentity, _Lease]())


@dataclass
class _Receive:
    staging: _Budget
    ready: deque[tuple[_Lease, CreditToken]] = field(default_factory=lambda: deque[tuple[_Lease, CreditToken]]())


class PhysicalTransportRegistry:
    """Own the one physical link/router allocation for an admitted mixed plan."""

    def __init__(self, env: simpy.Environment, plan: PacketNetworkPlan):
        self.env, self.plan = env, plan
        self.links = {key: VirtualChannelLink(env, contract) for key, contract in plan.links.items()}
        self.pipelines = {key: RouterPipeline(env, fabric_id=key[0], router_id=key[1],
                                             transfer_aci_cycles=config.transfer_aci_cycles,
                                             initiation_aci_cycles=config.initiation_aci_cycles,
                                             capacity=config.capacity_flits) for key, config in plan.routers.items()}
        self._attachments: set[str] = set()

    def attach(self, component: str, env: simpy.Environment, plan: PacketNetworkPlan) -> None:
        if env is not self.env or plan is not self.plan:
            raise ValueError("component must attach to its admitted environment and physical plan")
        if component in self._attachments:
            raise ValueError("physical registry already has this component; duplicate capacity is forbidden")
        self._attachments.add(component)

    @property
    def is_drained(self) -> bool:
        return all(link.is_drained for link in self.links.values()) and all(p.is_drained for p in self.pipelines.values())


class PacketTransport:
    """One shared environment/network and finite, nonblocking packet admission.

    Callers wait for changed after a failed try_submit, retaining only their
    own control metadata. No SimPy put waiter is allowed to hide queued data.
    """

    def __init__(self, env: simpy.Environment, plan: PacketNetworkPlan, hooks: PacketHooks,
                 *, registry: PhysicalTransportRegistry | None = None):
        if hooks.env is not env:
            raise ValueError("packet hooks must use the shared environment")
        self.env, self.plan, self.hooks = env, plan, hooks
        self._changed = env.event()
        self.endpoint_events: list[PacketEndpointEvent] = []
        physical = registry if registry is not None else PhysicalTransportRegistry(env, plan)
        physical.attach("unicast", env, plan)
        self.links, self.pipelines = physical.links, physical.pipelines
        self._receipts = {packet: env.event() for packet in plan.packets}
        self._handoffs = {packet: env.event() for packet in plan.packets}
        self._states = {packet: PacketDelivery(packet=packet, submitted=False, received_flits=0,
                                               received_useful_bytes=0, handoff_aci_cycles=None,
                                               completion_aci_cycles=None) for packet in plan.packets}
        tx_set: set[EndpointKey] = {(d.route.source, d.packet.traffic_class) for d in plan.packets.values()}
        rx_set: set[EndpointKey] = {(d.route.destination, d.packet.traffic_class) for d in plan.packets.values()}
        tx_keys, rx_keys = sorted(tx_set), sorted(rx_set)
        self._tx = {key: _Transmit(_Budget(self, key, "tx_descriptors", plan.endpoint_queue_capacity_packets),
                                  _Budget(self, key, "tx_staging", plan.endpoint_staging_capacity_flits)) for key in tx_keys}
        self._rx = {key: _Receive(_Budget(self, key, "rx_staging", plan.endpoint_staging_capacity_flits)) for key in rx_keys}
        lanes = {hop.lane.model_dump_json(): hop.lane for d in plan.packets.values() for hop in d.route.hops[:-1]}
        for _, lane in sorted(lanes.items()):
            env.process(self._forward(lane))
        for key in tx_keys:
            env.process(self._produce(key))
            env.process(self._inject(key))
        for key in rx_keys:
            definition = next(d for d in plan.packets.values() if (d.route.destination, d.packet.traffic_class) == key)
            env.process(self._receive(key, definition.route.hops[-1].lane))
            env.process(self._consume(key))

    @property
    def changed(self) -> Event:
        return self._changed

    def notify_changed(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def receipt(self, packet: PacketIdentity) -> Event:
        return self._receipts[packet]

    def handoff(self, packet: PacketIdentity) -> Event:
        return self._handoffs[packet]

    def try_submit(self, packet: PacketIdentity) -> bool:
        definition = self.plan.packets.get(packet)
        if definition is None:
            raise ValueError("packet is not in the admitted inventory")
        if self._states[packet].submitted:
            raise ValueError("packet was already submitted")
        if definition.after_packet is not None and not self.receipt(definition.after_packet).triggered:
            return False
        tx = self._tx[(definition.route.source, packet.traffic_class)]
        if tx.descriptors.full:
            return False
        tx.accepted[packet] = tx.descriptors.acquire(packet)
        tx.pending.append(packet)
        self._states[packet] = self._states[packet].model_copy(update={"submitted": True})
        self.notify_changed()
        return True

    def _produce(self, key: EndpointKey) -> ProcessGenerator:
        tx = self._tx[key]
        while True:
            while not tx.pending:
                yield self.changed
            packet = tx.pending.popleft()
            definition = self.plan.packets[packet]
            link = self.links[definition.route.hops[0].lane.channel.model_dump_json()]
            for index in range(definition.flit_count):
                while tx.staging.full:
                    yield self.changed
                lease = tx.staging.acquire(packet)
                envelope = link.contract.envelope(packet, index)
                if not isinstance(envelope, PacketFlit):
                    raise TypeError("streaming endpoint requires an internal packet flit")
                yield from self.hooks.produce(envelope)
                tx.ready.append((lease, envelope))
                self.notify_changed()
                del envelope, lease

    def _inject(self, key: EndpointKey) -> ProcessGenerator:
        tx = self._tx[key]
        while True:
            while not tx.ready:
                yield self.changed
            lease, envelope = tx.ready[0]
            link = self.links[envelope.lane.channel.model_dump_json()]
            token = link.try_reserve(envelope)
            while token is None:
                yield link.changed
                token = link.try_reserve(envelope)
            link.make_ready(token)
            tx.ready.popleft()
            tx.staging.release(lease)
            if envelope.is_tail:
                tx.descriptors.release(tx.accepted.pop(envelope.packet))
                self._states[envelope.packet] = self._states[envelope.packet].model_copy(
                    update={"handoff_aci_cycles": float(self.env.now)})
                self.handoff(envelope.packet).succeed()
            self.notify_changed()
            del token, envelope, lease

    def _forward(self, lane: LaneIdentity) -> ProcessGenerator:
        incoming = self.links[lane.channel.model_dump_json()]
        while True:
            token = incoming.take(lane)
            while token is None:
                yield incoming.changed
                token = incoming.take(lane)
            flit = token.envelope
            if not isinstance(flit, PacketFlit):
                raise TypeError("unicast forwarding requires a packet flit")
            definition = self.plan.packets[flit.packet]
            hop = definition.route.hops[flit.hop_index + 1]
            outgoing = self.links[hop.lane.channel.model_dump_json()]
            if hop.src_router is None:
                raise ValueError("forwarding hop has no router")
            pipeline = self.pipelines[(definition.route.fabric_id, hop.src_router)]
            yield from forward_flit(self.env, incoming, token, outgoing,
                                    outgoing.contract.envelope(flit.packet, flit.flit_index), pipeline)
            del flit, token

    def _receive(self, key: EndpointKey, lane: LaneIdentity) -> ProcessGenerator:
        rx = self._rx[key]
        link = self.links[lane.channel.model_dump_json()]
        while True:
            while rx.staging.full:
                yield self.changed
            token = link.take(lane)
            if token is None:
                yield link.changed
                continue
            if not isinstance(token.envelope, PacketFlit):
                raise TypeError("unicast ejection requires a packet flit")
            lease = rx.staging.acquire(token.envelope.packet)
            rx.ready.append((lease, token))
            self.notify_changed()
            del token, lease

    def _consume(self, key: EndpointKey) -> ProcessGenerator:
        rx = self._rx[key]
        while True:
            while not rx.ready:
                yield self.changed
            lease, token = rx.ready.popleft()
            flit = token.envelope
            if not isinstance(flit, PacketFlit):
                raise TypeError("streaming consumer requires an internal packet flit")
            state = self._states[flit.packet]
            if flit.flit_index != state.received_flits:
                raise ValueError("duplicate or out-of-order packet delivery")
            yield from self.hooks.consume(flit)
            # Injection can finish while the consumer hook waits. Preserve its
            # handoff fact instead of writing back the earlier state snapshot.
            state = self._states[flit.packet]
            link = self.links[flit.lane.channel.model_dump_json()]
            link.log_event("sink_complete", token)
            link.release(token)
            rx.staging.release(lease)
            self._states[flit.packet] = state.model_copy(update={
                "received_flits": state.received_flits + 1,
                "received_useful_bytes": state.received_useful_bytes + flit.payload_bytes,
                "completion_aci_cycles": float(self.env.now) if flit.is_tail else None,
            })
            if flit.is_tail:
                self.receipt(flit.packet).succeed()
            self.notify_changed()
            del flit, token, lease

    def snapshot(self, *, memory_service: Literal["unsupported", "external_hooks"] = "unsupported",
                 require_idle_environment: bool = True) -> PacketTransportResult:
        trace = tuple(sorted((event for link in self.links.values() for event in link.events),
                             key=lambda e: (e.time_aci_cycles, e.action, e.fabric_id, e.token_id or "")))
        resources = tuple(resource for link in self.links.values() for resource in link.resources()) + tuple(
            pipeline.resources() for pipeline in self.pipelines.values())
        buffers = tuple(b.snapshot() for tx in self._tx.values() for b in (tx.descriptors, tx.staging)) + tuple(
            rx.staging.snapshot() for rx in self._rx.values())
        pending = tuple(p for p in self.plan.packets if not self.receipt(p).triggered)
        drained = (not pending and all(link.is_drained for link in self.links.values())
                   and all(p.is_drained for p in self.pipelines.values()) and not any(b.occupied for b in buffers))
        # An attached memory session shares the clock with unrelated workload
        # processes; only standalone transport owns the whole event queue.
        complete = drained and (not require_idle_environment or self.env.peek() == float("inf"))
        return PacketTransportResult(
            plan_sha256=self.plan.plan_sha256, status="complete" if complete else "incomplete",
            reason="drained" if complete else "idle_with_pending" if self.env.peek() == float("inf") else "cycle_limit",
            elapsed_aci_cycles=self.env.now, packets=tuple(self._states.values()), resources=resources,
            endpoint_buffers=buffers, trace=trace, endpoint_trace=tuple(self.endpoint_events),
            transmitted_channel_bytes=sum(e.physical_bytes for e in trace if e.action == "link_launch"),
            received_useful_bytes=sum(s.received_useful_bytes for s in self._states.values()), pending_packets=pending,
            memory_service=memory_service)

    def run(self, *, max_aci_cycles: float) -> PacketTransportResult:
        if not math.isfinite(max_aci_cycles) or max_aci_cycles <= self.env.now:
            raise ValueError("transport horizon must be finite and later than current time")
        while self.env.peek() != float("inf") and self.env.peek() <= max_aci_cycles:
            self.env.step()
        return self.snapshot()
