"""Opt-in bounded lane/credit kernel; no router, endpoint service or replay driver.

Nonblocking admission never parks a flit in an unaccounted SimPy put/get waiter.
Callers retain their own charged storage until a reservation/staging attempt succeeds.
All lanes share one serializer; its loop waits only for finite physical service.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Self

import simpy
from pydantic import model_validator
from simpy.events import Event, ProcessGenerator

from .configs.schemas.topology import GraphRecord, PositiveInt
from .configs.schemas.torus_replay import (
    ChannelIdentity,
    Cycles,
    LaneIdentity,
    PacketIdentity,
    PositiveTime,
    RequestResponseTraffic,
    TransportEnvelope,
)
from .torus import TorusPlan
from .torus_records import ResourceState, TransportTraceEvent
from .utils.definitions import compute_flit_count


class ResolvedLinkConfig(GraphRecord):
    """Effective values only; the admitted plan retains evidence and overrides."""

    aci_clock_hz: PositiveTime
    noc_clock_hz: PositiveTime
    physical_flit_bytes: PositiveInt
    wire_bits_per_noc_cycle: PositiveInt
    payload_bits_per_noc_cycle: PositiveInt
    launch_interval_noc_cycles: PositiveTime
    propagation_noc_cycles: Cycles
    credit_return_noc_cycles: Cycles
    lane_capacity_flits: PositiveInt
    staging_capacity_flits: PositiveInt
    arbitration_quantum_flits: PositiveInt

    @model_validator(mode="after")
    def timing_bounds(self) -> Self:
        if self.payload_bits_per_noc_cycle > self.wire_bits_per_noc_cycle:
            raise ValueError("usable width exceeds wire width")
        if self.launch_interval_noc_cycles < self.serialization_noc_cycles:
            raise ValueError("launch interval is shorter than serialization")
        ratio = self.noc_clock_hz / self.aci_clock_hz
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("invalid native/ACI clock ratio")
        for native in (self.serialization_noc_cycles, self.launch_interval_noc_cycles,
                       self.propagation_noc_cycles, self.credit_return_noc_cycles):
            converted = native / ratio
            if not math.isfinite(converted) or (native > 0 and converted <= 0):
                raise ValueError("unrepresentable native/ACI duration")
        return self

    @property
    def serialization_noc_cycles(self) -> int:
        bits = 8 * self.physical_flit_bytes
        return (bits + self.payload_bits_per_noc_cycle - 1) // self.payload_bits_per_noc_cycle

    def aci(self, native_cycles: float) -> float:
        return native_cycles / (self.noc_clock_hz / self.aci_clock_hz)

    def export(self) -> dict[str, object]:
        return {
            **self.model_dump(mode="json"),
            "serialization_noc_cycles": self.serialization_noc_cycles,
            "serialization_aci_cycles": self.aci(self.serialization_noc_cycles),
            "launch_interval_aci_cycles": self.aci(self.launch_interval_noc_cycles),
            "propagation_aci_cycles": self.aci(self.propagation_noc_cycles),
            "credit_return_aci_cycles": self.aci(self.credit_return_noc_cycles),
        }


@dataclass(frozen=True)
class LinkContract:
    """Pure per-channel projection of a compiled plan, before SimPy allocation."""

    plan_sha256: str
    channel: ChannelIdentity
    config: ResolvedLinkConfig
    _templates: Mapping[PacketIdentity, TransportEnvelope]
    _payloads: Mapping[PacketIdentity, tuple[int, int]]

    @classmethod
    def from_plan(cls, plan: TorusPlan, channel: ChannelIdentity) -> LinkContract:
        channel = ChannelIdentity.model_validate(channel.model_dump(mode="json"))
        prepared = plan.binding.contract
        config = prepared.config
        if config.slowdowns:
            raise ValueError("directed slowdown execution is not implemented by this kernel")
        fabric = next((f for f in config.fabrics if f.fabric_id == channel.fabric_id), None)
        if fabric is None:
            raise ValueError("unknown channel fabric")
        prefix = f"fabrics.{fabric.fabric_id}"
        if channel.kind == "network":
            edge = next((e for e in plan.record.graph.links
                         if (e.fabric_id, e.link_id) == (channel.fabric_id, channel.identity)), None)
            if edge is None or not edge.enabled:
                raise ValueError("missing or disabled network channel")
            override = next((o for o in config.network_overrides
                             if (o.fabric_id, o.link_id) == (channel.fabric_id, channel.identity)), None)
            settings = fabric.network_link if override is None else override.settings
            label = f"{prefix}.network_link" if override is None else f"network_overrides.{fabric.fabric_id}.{channel.identity}"
        else:
            endpoint = next((e for e in config.binding.endpoints if e.endpoint_id == channel.identity), None)
            if endpoint is None or endpoint.fabric_id != channel.fabric_id or (
                endpoint.inject_port if channel.kind == "inject" else endpoint.eject_port
            ) is None:
                raise ValueError("unbound local channel")
            local = next((o for o in config.local_overrides
                          if (o.endpoint_id, o.direction) == (channel.identity, channel.kind)), None)
            settings = fabric.local_link if local is None else local.settings
            label = f"{prefix}.local_link" if local is None else f"local_overrides.{channel.identity}.{channel.kind}"
        quantities = {q.field_path: q.value for q in prepared.quantities}
        physical = int(quantities[f"{prefix}.flit.physical_flit_bytes"])
        resolved = ResolvedLinkConfig(
            aci_clock_hz=quantities["aci_clock"], noc_clock_hz=quantities[f"{prefix}.noc_clock"],
            physical_flit_bytes=physical,
            wire_bits_per_noc_cycle=int(quantities[f"{label}.wire_bits_per_noc_cycle"]),
            payload_bits_per_noc_cycle=int(quantities[f"{label}.payload_bits_per_noc_cycle"]),
            launch_interval_noc_cycles=settings.launch_interval_noc_cycles,
            propagation_noc_cycles=settings.propagation_noc_cycles,
            credit_return_noc_cycles=settings.credit_return_noc_cycles,
            lane_capacity_flits=settings.lane_capacity_flits,
            staging_capacity_flits=settings.staging_capacity_flits,
            arbitration_quantum_flits=settings.arbitration_quantum_flits,
        )
        templates: dict[PacketIdentity, TransportEnvelope] = {}
        payloads: dict[PacketIdentity, tuple[int, int]] = {}
        plan_hash = plan.record.plan_sha256
        routes = {(r.source, r.destination, r.traffic_class): r
                  for r in plan.record.routes if r.fabric_id == channel.fabric_id}
        for traffic in config.traffic:
            if traffic.fabric_id != channel.fabric_id:
                continue
            for traffic_class in ("request", "response"):
                if traffic_class == "response":
                    if not isinstance(traffic, RequestResponseTraffic):
                        continue
                    source, destination = traffic.destination, traffic.source
                    size, quantum = traffic.response_payload_bytes, traffic.response_burst_quantum_flits
                else:
                    source, destination = traffic.source, traffic.destination
                    size, quantum = traffic.payload_bytes, traffic.burst_quantum_flits
                route = routes[(source, destination, traffic_class)]
                for index, hop in enumerate(route.hops):
                    if hop.lane.channel != channel:
                        continue
                    packet = PacketIdentity(transfer_id=traffic.transfer_id, traffic_class=traffic_class)
                    capacity = fabric.flit.payload_capacity_bytes
                    templates[packet] = TransportEnvelope(
                        plan_sha256=plan_hash, packet=packet, fabric_id=channel.fabric_id,
                        source=source, destination=destination, hop_index=index, lane=hop.lane,
                        flit_index=0, flit_count=compute_flit_count(size, capacity),
                        payload_bytes=min(size, capacity), physical_bytes=physical, burst_quantum_flits=quantum,
                    )
                    payloads[packet] = size, capacity
        return cls(plan_hash, channel, resolved, MappingProxyType(templates), MappingProxyType(payloads))

    @property
    def lanes(self) -> tuple[LaneIdentity, ...]:
        phases = (0, 1) if self.channel.kind == "network" else (None,)
        return tuple(LaneIdentity(channel=self.channel, traffic_class=kind, dateline_phase=phase)
                     for kind in ("request", "response") for phase in phases)

    def envelope(self, packet: PacketIdentity, flit_index: int) -> TransportEnvelope:
        template = self._templates.get(packet)
        if template is None:
            raise ValueError("packet has no admitted path on this channel")
        if type(flit_index) is not int or not 0 <= flit_index < template.flit_count:
            raise ValueError("invalid flit index")
        size, capacity = self._payloads[packet]
        return template.model_copy(update={"flit_index": flit_index,
                                           "payload_bytes": min(capacity, size - flit_index * capacity)})

    def validate(self, envelope: TransportEnvelope) -> TransportEnvelope:
        # Revalidate model_copy/model_construct inputs before any event or state change.
        checked = TransportEnvelope.model_validate(envelope.model_dump(mode="json"))
        if checked.plan_sha256 != self.plan_sha256:
            raise ValueError("cross-plan envelope")
        if checked != self.envelope(checked.packet, checked.flit_index):
            raise ValueError("envelope disagrees with admitted packet, hop or format")
        return checked


@dataclass(frozen=True, eq=False)
class CreditToken:
    """Opaque instance capability; matching text IDs cannot forge another token."""

    token_id: str
    envelope: TransportEnvelope


Stage = Literal["reserved", "ready", "serializing", "propagating", "received", "held", "returning"]


@dataclass
class _Slot:
    token: CreditToken
    stage: Stage


@dataclass
class _Lane:
    identity: LaneIdentity
    slots: dict[str, _Slot] = field(default_factory=lambda: dict[str, _Slot]())
    ready: deque[CreditToken] = field(default_factory=lambda: deque[CreditToken]())
    received: deque[CreditToken] = field(default_factory=lambda: deque[CreditToken]())
    owner: PacketIdentity | None = None
    peak: int = 0


class VirtualChannelLink:
    """One serializer with independently owned lanes and an exact finite budget.

    A reservation charges B until release plus delayed credit return. Staging is
    an additional shared bound, charged from launch through propagation/arrival.
    A received token remains charged after take(), including external forwarder
    registers, until release(). None means backpressure, never queued work.
    """

    def __init__(self, env: simpy.Environment, contract: LinkContract):
        self.env = env
        self.contract = contract
        self.config = contract.config
        self._lanes = {lane: _Lane(lane) for lane in contract.lanes}
        self._order = tuple(self._lanes)
        self._next_reserve: dict[PacketIdentity, int] = {}
        self._next_stage: dict[PacketIdentity, int] = {}
        self._sequence = 0
        self._staging = 0
        self._staging_peak = 0
        self._cursor = 0
        # Round-robin preference only; it holds no physical grant while waiting.
        self._turn_lane: LaneIdentity | None = None
        self._quantum_left = 0
        self._busy_until = float(env.now)
        self._changed = env.event()
        self._events: list[TransportTraceEvent] = []
        env.process(self._serialize())

    @property
    def changed(self) -> Event:
        """Wait after a failed try operation, with the caller's storage still charged."""
        return self._changed

    @property
    def events(self) -> tuple[TransportTraceEvent, ...]:
        return tuple(self._events)

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def _log(self, action: str, token: CreditToken, *, duration: float | None = None) -> None:
        flit = token.envelope
        launched = action == "link_launch"
        self._events.append(TransportTraceEvent.model_validate({
            "action": action, "time_aci_cycles": self.env.now, "fabric_id": flit.fabric_id,
            "router_id": None, "port_id": None, "channel": flit.lane.channel, "lane": flit.lane,
            "packet": flit.packet, "flit_index": flit.flit_index, "token_id": token.token_id,
            "failure_id": None, "physical_bytes": flit.physical_bytes if launched else 0,
            "payload_bytes": flit.payload_bytes if launched else 0,
            "duration_aci_cycles": duration, "launch_factor": 1.0 if launched else None,
        }))

    def try_reserve(self, envelope: TransportEnvelope) -> CreditToken | None:
        flit = self.contract.validate(envelope)
        lane = self._lanes[flit.lane]
        expected = self._next_reserve.get(flit.packet, 0)
        if flit.flit_index != expected:
            raise ValueError("duplicate or out-of-order HEAD/BODY/TAIL")
        if lane.owner is not None and lane.owner != flit.packet:
            return None
        if len(lane.slots) == self.config.lane_capacity_flits:
            return None
        self._sequence += 1
        token = CreditToken(f"{self.contract.plan_sha256}:{self.contract.channel.model_dump_json()}:{self._sequence}", flit)
        lane.slots[token.token_id] = _Slot(token, "reserved")
        self._next_reserve[flit.packet] = expected + 1
        if lane.owner is None:
            lane.owner = flit.packet
            self._log("owner_acquire", token)
        lane.peak = max(lane.peak, sum(s.stage != "returning" for s in lane.slots.values()))
        self._log("credit_reserve", token)
        self._notify()
        return token

    def _slot(self, token: CreditToken, expected: Stage) -> tuple[_Lane, _Slot]:
        lane = self._lanes.get(token.envelope.lane)
        slot = None if lane is None else lane.slots.get(token.token_id)
        if lane is None or slot is None or slot.token is not token or slot.stage != expected:
            raise ValueError(f"foreign, duplicate or wrong-stage token; expected {expected}")
        return lane, slot

    def make_ready(self, token: CreditToken) -> None:
        """Publish an already charged reservation to its lane FIFO, without waiting.

        Ready storage is covered by B. Only the fair physical arbiter can acquire
        a shared staging slot, so producer callback order cannot monopolize it.
        """
        lane, slot = self._slot(token, "reserved")
        flit = token.envelope
        if self._next_stage.get(flit.packet, 0) != flit.flit_index:
            raise ValueError("out-of-order staging on a lane")
        self._next_stage[flit.packet] = flit.flit_index + 1
        slot.stage = "ready"
        lane.ready.append(token)
        self._log("link_ready", token)
        self._notify()

    def take(self, identity: LaneIdentity) -> CreditToken | None:
        lane = self._lanes[identity]
        if not lane.received:
            return None
        token = lane.received.popleft()
        lane.slots[token.token_id].stage = "held"
        self._log("link_take", token)
        self._notify()
        return token

    def release(self, token: CreditToken) -> None:
        _, slot = self._slot(token, "held")
        slot.stage = "returning"
        self._log("credit_release", token, duration=self.config.aci(self.config.credit_return_noc_cycles))
        self.env.process(self._return_credit(token))
        self._notify()

    def _return_credit(self, token: CreditToken) -> ProcessGenerator:
        yield self.env.timeout(self.config.aci(self.config.credit_return_noc_cycles))
        lane, _ = self._slot(token, "returning")
        del lane.slots[token.token_id]
        self._log("credit_return", token)
        self._notify()

    def _select(self) -> _Lane | None:
        if self._turn_lane is not None and self._quantum_left and self._lanes[self._turn_lane].ready:
            return self._lanes[self._turn_lane]
        self._turn_lane = None
        self._quantum_left = 0
        for offset in range(len(self._order)):
            index = (self._cursor + offset) % len(self._order)
            lane = self._lanes[self._order[index]]
            if lane.ready:
                self._cursor = (index + 1) % len(self._order)
                self._turn_lane = lane.identity
                self._quantum_left = min(self.config.arbitration_quantum_flits,
                                         lane.ready[0].envelope.burst_quantum_flits)
                return lane
        return None

    def _serialize(self) -> ProcessGenerator:
        while True:
            if self._staging == self.config.staging_capacity_flits:
                yield self.changed
                continue
            lane = self._select()
            if lane is None:
                yield self.changed
                continue
            token = lane.ready.popleft()
            slot = lane.slots[token.token_id]
            slot.stage = "serializing"
            self._staging += 1
            self._staging_peak = max(self._staging_peak, self._staging)
            self._quantum_left -= 1
            duration = self.config.aci(self.config.serialization_noc_cycles)
            self._busy_until = float(self.env.now) + self.config.aci(self.config.launch_interval_noc_cycles)
            self._log("link_launch", token, duration=duration)
            if token.envelope.is_tail:
                lane.owner = None
                # A new packet must receive a fresh quantum even on the same lane.
                self._quantum_left = 0
                self._log("owner_release", token)
            self._notify()
            # No downstream, ownership, credit or producer waits under this grant.
            yield self.env.timeout(duration)
            slot.stage = "propagating"
            self._log("serialization_end", token)
            self._log("propagation_start", token, duration=self.config.aci(self.config.propagation_noc_cycles))
            self.env.process(self._deliver(token))
            # Propagation owns the charged token now; the wire retains no flit.
            del token, slot
            self._notify()
            gap = self.config.aci(self.config.launch_interval_noc_cycles) - duration
            if gap:
                yield self.env.timeout(gap)
                self._notify()

    def _deliver(self, token: CreditToken) -> ProcessGenerator:
        yield self.env.timeout(self.config.aci(self.config.propagation_noc_cycles))
        lane, slot = self._slot(token, "propagating")
        self._staging -= 1
        slot.stage = "received"
        lane.received.append(token)
        self._log("link_arrive", token)
        self._notify()

    def resources(self) -> tuple[ResourceState, ...]:
        return tuple(ResourceState(
            resource_id=identity.model_dump_json(), kind="lane", fabric_id=identity.channel.fabric_id,
            unit="flits", lane=identity, capacity=self.config.lane_capacity_flits,
            available=self.config.lane_capacity_flits - len(lane.slots),
            occupied=sum(s.stage != "returning" for s in lane.slots.values()),
            pending_returns=sum(s.stage == "returning" for s in lane.slots.values()),
            peak_occupied=lane.peak, owners=() if lane.owner is None else (lane.owner,),
        ) for identity, lane in self._lanes.items())

    @property
    def is_drained(self) -> bool:
        return (all(r.is_drained for r in self.resources()) and not self._staging
                and self.env.now >= self._busy_until)

    def inspect(self) -> dict[str, object]:
        return {
            "plan_sha256": self.contract.plan_sha256, "channel": self.contract.channel.model_dump(mode="json"),
            "timing": self.config.export(), "resources": [r.model_dump(mode="json") for r in self.resources()],
            "staging_capacity_flits": self.config.staging_capacity_flits,
            "staging_occupied_flits": self._staging, "staging_peak_flits": self._staging_peak,
            "serializer_busy": self.env.now < self._busy_until,
            "tokens": [{"token_id": slot.token.token_id, "stage": slot.stage,
                        "lane": lane.identity.model_dump(mode="json"),
                        "packet": slot.token.envelope.packet.model_dump(mode="json"),
                        "flit_index": slot.token.envelope.flit_index}
                       for lane in self._lanes.values() for slot in lane.slots.values()],
            "transmitted_channel_bytes": sum(e.physical_bytes for e in self._events if e.action == "link_launch"),
            "is_drained": self.is_drained,
        }
