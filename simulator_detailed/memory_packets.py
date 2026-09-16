"""Pure software segmentation, wire layouts and plan-bound flit metadata.

Headers consume physical bytes but carry zero useful data. These are scheduling
records, not byte values, NIU register encodings, automatic splits or VC_LINKED.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from .configs.schemas.memory_replay import MemoryOperation, MemoryPacketConfig
from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
    unique,
)
from .configs.schemas.torus_replay import (
    LaneIdentity,
    PacketIdentity,
    RouteRecord,
    TrafficClass,
)
from .memory_plan import MemoryPlan
from .memory_routes import MemoryRoutes
from .topology import content_digest

Purpose = Literal["read_request", "read_response", "write_request", "write_ack"]


class MemoryPacketIdentity(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    purpose: Purpose

    @property
    def traffic_class(self) -> TrafficClass:
        return "request" if self.purpose in {"read_request", "write_request"} else "response"

    @property
    def transport_identity(self) -> PacketIdentity:
        # A canonical tuple encoding is injective even when user IDs contain
        # punctuation or look like a generated segment/response suffix.
        transfer_id = json.dumps([self.operation_id, self.segment_index, self.purpose],
                                 separators=(",", ":"), ensure_ascii=True)
        return PacketIdentity(transfer_id=transfer_id, traffic_class=self.traffic_class)


class MemoryPacketLayout(GraphRecord):
    physical_flit_bytes: PositiveInt
    data_capacity_bytes: PositiveInt
    header_flits: PositiveInt
    useful_bytes: Index

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if self.data_capacity_bytes > self.physical_flit_bytes:
            raise ValueError("data capacity exceeds physical flit width")
        return self

    @property
    def data_flits(self) -> int:
        return (self.useful_bytes + self.data_capacity_bytes - 1) // self.data_capacity_bytes

    @property
    def flit_count(self) -> int:
        return self.header_flits + self.data_flits

    @property
    def header_bytes(self) -> int:
        return self.header_flits * self.physical_flit_bytes

    @property
    def padding_bytes(self) -> int:
        return self.data_flits * self.physical_flit_bytes - self.useful_bytes

    @property
    def packet_bytes(self) -> int:
        return self.flit_count * self.physical_flit_bytes

    def flit_useful_bytes(self, index: int) -> int:
        if type(index) is not int or not 0 <= index < self.flit_count:
            raise ValueError("flit index outside packet")
        if index < self.header_flits:
            return 0
        return min(self.data_capacity_bytes, self.useful_bytes - (index - self.header_flits) * self.data_capacity_bytes)


class MemorySegment(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    offset_bytes: Index
    source_address: Index
    destination_address: Index
    logical_bytes: PositiveInt


class MemoryPacket(GraphRecord):
    identity: MemoryPacketIdentity
    segment: MemorySegment
    layout: MemoryPacketLayout

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.identity.operation_id, self.identity.segment_index) != (
                self.segment.operation_id, self.segment.segment_index):
            raise ValueError("packet identity differs from segment")
        data = self.identity.purpose in {"write_request", "read_response"}
        if self.layout.useful_bytes != (self.segment.logical_bytes if data else 0):
            raise ValueError("packet purpose disagrees with useful data layout")
        return self


def packetize(operation: MemoryOperation, config: MemoryPacketConfig, *,
              source_base_address: int, destination_base_address: int) -> tuple[MemoryPacket, ...]:
    """Segment admitted contiguous ranges into independent request/response pairs."""
    if operation.kind not in {"read", "write_posted", "write_acknowledged"}:
        raise ValueError("packetization requires a network memory operation")
    if operation.source is None or operation.destination is None:
        raise ValueError("packetization requires both memory ranges")
    for base in (source_base_address, destination_base_address):
        if type(base) is not int or base < 0:
            raise ValueError("buffer base must be a nonnegative integer")
    source = source_base_address + operation.source.offset_bytes
    destination = destination_base_address + operation.destination.offset_bytes
    if source % config.address_alignment_bytes or destination % config.address_alignment_bytes:
        raise ValueError("packet addresses must be aligned")
    size = operation.source.size_bytes
    if size != operation.destination.size_bytes:
        raise ValueError("source/destination lengths must agree")
    purposes: tuple[Purpose, ...] = (("read_request", "read_response") if operation.kind == "read" else
                                   ("write_request", "write_ack") if operation.kind == "write_acknowledged" else
                                   ("write_request",))
    packets: list[MemoryPacket] = []
    for index, offset in enumerate(range(0, size, config.max_segment_payload_bytes)):
        length = min(config.max_segment_payload_bytes, size - offset)
        segment = MemorySegment(operation_id=operation.operation_id, segment_index=index, offset_bytes=offset,
                                source_address=source + offset, destination_address=destination + offset,
                                logical_bytes=length)
        for purpose in purposes:
            packets.append(MemoryPacket(
                identity=MemoryPacketIdentity(operation_id=operation.operation_id, segment_index=index, purpose=purpose),
                segment=segment,
                layout=MemoryPacketLayout(physical_flit_bytes=config.physical_flit_bytes,
                                          data_capacity_bytes=config.data_capacity_bytes,
                                          header_flits=config.header_flits,
                                          useful_bytes=length if purpose in {"write_request", "read_response"} else 0),
            ))
    return tuple(packets)


class MemoryWireEnvelope(GraphRecord):
    plan_sha256: Digest
    packet: MemoryPacketIdentity
    layout: MemoryPacketLayout
    hop_index: Index
    lane: LaneIdentity
    flit_index: Index
    useful_bytes: Index
    physical_bytes: PositiveInt

    @model_validator(mode="after")
    def flit_shape(self) -> Self:
        if self.useful_bytes != self.layout.flit_useful_bytes(self.flit_index):
            raise ValueError("envelope useful bytes differ from layout")
        if self.physical_bytes != self.layout.physical_flit_bytes:
            raise ValueError("envelope physical bytes differ from layout")
        if self.lane.traffic_class != self.packet.traffic_class:
            raise ValueError("envelope packet and lane classes differ")
        data = self.packet.purpose in {"write_request", "read_response"}
        if data != (self.layout.useful_bytes > 0):
            raise ValueError("envelope purpose differs from layout")
        return self

    @property
    def is_head(self) -> bool:
        return self.flit_index == 0

    @property
    def is_tail(self) -> bool:
        return self.flit_index + 1 == self.layout.flit_count


class RoutedMemoryPacket(GraphRecord):
    packet: MemoryPacket
    route: RouteRecord

    @model_validator(mode="after")
    def route_class(self) -> Self:
        if self.packet.identity.traffic_class != self.route.traffic_class:
            raise ValueError("packet and route classes differ")
        return self

    @property
    def planned_channel_bytes(self) -> int:
        # Potential launches, not measured runtime traffic.
        return self.packet.layout.packet_bytes * len(self.route.hops)


@dataclass(frozen=True)
class MemoryWirePlan:
    plan_sha256: str
    memory_plan_sha256: str
    aci_clock_hz: float
    packets: tuple[RoutedMemoryPacket, ...]
    routes: MemoryRoutes

    @classmethod
    def compile(cls, plan: MemoryPlan) -> MemoryWirePlan:
        routes = MemoryRoutes.compile(plan)
        by_operation = {item.operation_id: item for item in routes.operations}
        buffers = {item.buffer_id: item for item in plan.config.buffers}
        packets: list[RoutedMemoryPacket] = []
        for operation in plan.config.operations:
            if operation.operation_id not in by_operation:
                continue
            if operation.source is None or operation.destination is None:
                raise ValueError("network operation requires ranges")
            selected = by_operation[operation.operation_id]
            for packet in packetize(operation, plan.config.packet,
                                    source_base_address=buffers[operation.source.buffer_id].base_address,
                                    destination_base_address=buffers[operation.destination.buffer_id].base_address):
                route = selected.request if packet.identity.traffic_class == "request" else selected.response
                if route is None:
                    raise ValueError("response packet has no route")
                packets.append(RoutedMemoryPacket(packet=packet, route=route))
        unique(tuple(item.packet.identity for item in packets), "memory packet identity")
        digest = content_digest({"memory_plan_sha256": plan.plan_sha256,
                                 "packets": [item.model_dump(mode="json") for item in packets]})
        return cls(digest, plan.plan_sha256, plan.config.aci_clock_hz, tuple(packets), routes)

    def envelope(self, identity: MemoryPacketIdentity, flit_index: int, hop_index: int) -> MemoryWireEnvelope:
        matches = tuple(item for item in self.packets if item.packet.identity == identity)
        if len(matches) != 1:
            raise ValueError("packet identity is not in this memory wire plan")
        item = matches[0]
        if type(hop_index) is not int or not 0 <= hop_index < len(item.route.hops):
            raise ValueError("hop index outside admitted route")
        layout = item.packet.layout
        return MemoryWireEnvelope(plan_sha256=self.plan_sha256, packet=identity, layout=layout,
                                  hop_index=hop_index, lane=item.route.hops[hop_index].lane,
                                  flit_index=flit_index, useful_bytes=layout.flit_useful_bytes(flit_index),
                                  physical_bytes=layout.physical_flit_bytes)

    def validate_envelope(self, envelope: MemoryWireEnvelope) -> None:
        envelope = MemoryWireEnvelope.model_validate(envelope.model_dump(mode="json"))
        if envelope != self.envelope(envelope.packet, envelope.flit_index, envelope.hop_index):
            raise ValueError("memory envelope differs from admitted plan/layout/path")
