"""Typed shared link/router service boundary, independent of replay semantics."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol, Self

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
    ChannelIdentity,
    LaneIdentity,
    PacketIdentity,
    TransportEnvelope,
)
from .torus_records import ResourceState
from .tree_wire import KernelLane, KernelPacket, SharedResourceState, TreeFlit

if TYPE_CHECKING:
    from .virtual_channel import CreditToken, ResolvedLinkConfig, VirtualChannelLink


class PacketFlit(GraphRecord):
    """Internal admitted wire metadata; zero useful payload is legal for headers.

    Public v2 envelopes remain positive. Memory callers must validate their own
    wire layout and route before constructing this internal representation.
    """

    plan_sha256: Digest
    packet: PacketIdentity
    fabric_id: Index
    source: Identifier
    destination: Identifier
    hop_index: Index
    lane: LaneIdentity
    flit_index: Index
    flit_count: PositiveInt
    payload_bytes: Index
    physical_bytes: PositiveInt
    burst_quantum_flits: PositiveInt

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.flit_index >= self.flit_count or self.payload_bytes > self.physical_bytes:
            raise ValueError("invalid internal flit bounds")
        if self.lane.channel.fabric_id != self.fabric_id or self.lane.traffic_class != self.packet.traffic_class:
            raise ValueError("internal flit and lane disagree")
        return self

    @property
    def is_head(self) -> bool:
        return self.flit_index == 0

    @property
    def is_tail(self) -> bool:
        return self.flit_index + 1 == self.flit_count


WireFlit = TransportEnvelope | PacketFlit | TreeFlit


class LinkService(Protocol):
    @property
    def plan_sha256(self) -> str: ...
    @property
    def channel(self) -> ChannelIdentity: ...
    @property
    def config(self) -> ResolvedLinkConfig: ...
    @property
    def lanes(self) -> tuple[KernelLane, ...]: ...
    def envelope(self, packet: PacketIdentity, flit_index: int) -> WireFlit: ...
    def validate(self, envelope: WireFlit) -> WireFlit: ...


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
        self._owners: dict[str, KernelPacket] = {}
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

    def try_start(self, output: ChannelIdentity, packet: KernelPacket) -> bool:
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

    def finish(self, output: ChannelIdentity, packet: KernelPacket) -> None:
        for key, owner in tuple(self._owners.items()):
            if owner == packet and key.startswith(f"{output.model_dump_json()}:"):
                del self._owners[key]
                break
        else:
            raise ValueError("router transfer completion does not match an active owner")
        self.active -= 1
        self._notify()

    def resources(self) -> ResourceState:
        owners = tuple(dict.fromkeys(p for p in self._owners.values() if isinstance(p, PacketIdentity)))
        return ResourceState(
            resource_id=f"router-pipeline:{self.router_id}", kind="router_pipeline",
            fabric_id=self.fabric_id, unit="flits", lane=None, capacity=self.capacity,
            available=self.capacity - self.active, occupied=self.active, pending_returns=0,
            peak_occupied=self.peak, owners=owners,
        )

    def shared_resources(self) -> SharedResourceState:
        return SharedResourceState(resource_id=f"router-pipeline:{self.router_id}", kind="router_pipeline",
                                   fabric_id=self.fabric_id, capacity=self.capacity,
                                   available=self.capacity - self.active, occupied=self.active, peak_occupied=self.peak,
                                   owners=tuple(dict.fromkeys(self._owners.values())))

    @property
    def is_drained(self) -> bool:
        return self.active == 0 and not self._waiting


def forward_flit(env: simpy.Environment, incoming: VirtualChannelLink,
                 token: CreditToken, outgoing: VirtualChannelLink, envelope: WireFlit,
                 pipeline: RouterPipeline) -> ProcessGenerator:
    """Retain input credit while waiting; no router grant waits on the wire."""
    out_token = outgoing.try_reserve(envelope)
    while out_token is None:
        yield outgoing.changed
        out_token = outgoing.try_reserve(envelope)
    channel = outgoing.contract.channel
    while not pipeline.try_start(channel, envelope.packet):
        yield pipeline.changed
    outgoing.log_event("transfer_start", out_token, duration=pipeline.transfer_aci_cycles,
                       router_id=pipeline.router_id)
    yield env.timeout(pipeline.transfer_aci_cycles)
    outgoing.log_event("transfer_end", out_token, duration=pipeline.transfer_aci_cycles,
                       router_id=pipeline.router_id)
    outgoing.make_ready(out_token)
    pipeline.finish(channel, envelope.packet)
    incoming.release(token)
