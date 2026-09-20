"""Internal multicast identities; public v2 unicast envelopes stay unchanged."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
    unique,
)
from .configs.schemas.torus_replay import (
    ChannelIdentity,
    Cycles,
    LaneIdentity,
    PacketIdentity,
)


class TreePacketIdentity(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    traffic_class: Literal["multicast"] = "multicast"


class TreeLaneIdentity(GraphRecord):
    channel: ChannelIdentity
    traffic_class: Literal["multicast"] = "multicast"


class TreeFlit(GraphRecord):
    plan_sha256: Digest
    packet: TreePacketIdentity
    fabric_id: Index
    source: Identifier
    branch_id: Identifier
    lane: TreeLaneIdentity
    flit_index: Index
    flit_count: PositiveInt
    payload_bytes: Index
    physical_bytes: PositiveInt
    burst_quantum_flits: PositiveInt

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.flit_index >= self.flit_count or self.payload_bytes > self.physical_bytes
                or self.fabric_id != self.lane.channel.fabric_id):
            raise ValueError("invalid tree flit bounds or fabric")
        return self

    @property
    def is_head(self) -> bool:
        return self.flit_index == 0

    @property
    def is_tail(self) -> bool:
        return self.flit_index + 1 == self.flit_count


KernelPacket = PacketIdentity | TreePacketIdentity
KernelLane = LaneIdentity | TreeLaneIdentity


class SharedResourceState(GraphRecord):
    resource_id: Identifier
    kind: Literal["lane", "router_pipeline", "replication", "tree_reservations", "tx_descriptors", "tx_staging", "rx_staging"]
    fabric_id: Index
    lane: KernelLane | None = None
    capacity: PositiveInt
    available: Index
    occupied: Index
    pending_returns: Index = 0
    peak_occupied: Index
    owners: tuple[KernelPacket, ...]

    @model_validator(mode="after")
    def conservation(self) -> Self:
        unique(self.owners, "shared resource owner")
        if self.available + self.occupied + self.pending_returns != self.capacity:
            raise ValueError("shared resource capacity is not conserved")
        if not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("shared resource exceeds its capacity/peak")
        return self

    @property
    def is_drained(self) -> bool:
        return not (self.occupied or self.pending_returns or self.owners)


class TreeTraceEvent(GraphRecord):
    action: Literal[
        "inject", "eject", "router_arrive", "route", "grant", "release",
        "transfer_start", "transfer_end", "link_launch", "serialization_end",
        "link_arrive", "credit_wait", "credit_return", "arbitration_wait",
        "arrival_order_wait", "sink_complete", "response_ready",
        "owner_acquire", "owner_release", "credit_reserve", "link_ready", "link_take",
        "credit_release", "propagation_start",
    ]
    time_aci_cycles: Cycles
    fabric_id: Index
    router_id: Identifier | None
    port_id: Identifier | None
    channel: ChannelIdentity
    lane: TreeLaneIdentity
    packet: TreePacketIdentity
    flit_index: Index
    token_id: Identifier
    failure_id: Identifier | None
    physical_bytes: Index
    payload_bytes: Index
    duration_aci_cycles: Cycles | None
    launch_factor: float | None = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.lane.channel != self.channel or self.channel.fabric_id != self.fabric_id:
            raise ValueError("tree trace identity mismatch")
        if self.payload_bytes > self.physical_bytes:
            raise ValueError("tree payload exceeds charged physical bytes")
        if self.action == "link_launch" and (self.launch_factor is None or not self.physical_bytes):
            raise ValueError("tree launch requires physical service")
        return self
