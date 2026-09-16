"""Pure admission and records for explicitly ordered addressed memory execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from .configs.schemas.memory_replay import MemoryOperation, MemoryReplay
from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
)
from .configs.schemas.torus_replay import Cycles, TransportEvidence
from .memory_ordering import MemoryOperationOrder, MemoryOrderingPlan
from .memory_packets import MemorySegment, MemoryWirePlan, RoutedMemoryPacket
from .memory_plan import MemoryPlan
from .memory_records import (
    MemoryByteAccounting,
    MemoryOperationRecord,
    MemoryPacketBytes,
    MemoryReplayResult,
)
from .memory_resources import (
    MemoryOwnershipEvent,
    MemoryResourcePlan,
    MemoryResourceState,
)
from .memory_service import MemoryChunkRecord, MemoryServiceEvent
from .memory_transport import MemoryTransportConfig, MemoryTransportPlan
from .packet_runtime import PacketTransportResult
from .topology import content_digest

SegmentKey = tuple[str, int]


class MemoryRuntimeConfig(GraphRecord):
    """Explicit transport, descriptor and control settings for memory execution."""

    transport: MemoryTransportConfig
    responder_capacity_packets: PositiveInt
    request_control_aci_cycles: Cycles
    response_control_aci_cycles: Cycles
    local_capacity_operations: PositiveInt = 1
    local_control_aci_cycles: Cycles = 0
    evidence: TransportEvidence


class MemoryExecutionReplay(MemoryReplay):
    """Executable file contract: admission configuration plus required runtime settings."""

    runtime: MemoryRuntimeConfig


@dataclass(frozen=True)
class MemorySegmentDefinition:
    operation: MemoryOperation
    request: RoutedMemoryPacket
    response: RoutedMemoryPacket | None
    initiator_resource_id: str

    @property
    def segment(self) -> MemorySegment:
        return self.request.packet.segment

    @property
    def key(self) -> SegmentKey:
        return self.segment.operation_id, self.segment.segment_index


@dataclass(frozen=True)
class MemoryExecutionPlan:
    memory: MemoryPlan
    settings: MemoryRuntimeConfig
    resources: MemoryResourcePlan
    transport: MemoryTransportPlan
    ordering: MemoryOrderingPlan
    segments: tuple[MemorySegmentDefinition, ...]
    plan_sha256: str

    @classmethod
    def compile(cls, memory: MemoryPlan, settings: MemoryRuntimeConfig) -> MemoryExecutionPlan:
        settings = MemoryRuntimeConfig.model_validate(settings.model_dump(mode="python"))
        memory = memory.revalidate()
        config = memory.config
        if (config.endpoint_queue_capacity_packets, config.endpoint_staging_capacity_flits) != (
                settings.transport.endpoint_queue_capacity_packets, settings.transport.endpoint_staging_capacity_flits):
            raise ValueError("memory and transport endpoint capacities must agree")
        for operation in config.operations:
            control = (settings.local_control_aci_cycles if operation.kind in {"local_read", "local_write"}
                       else 0 if operation.kind == "fence" else config.issue_latency_aci_cycles)
            if not math.isfinite(operation.start_aci_cycles + control):
                raise ValueError("unrepresentable memory admission/control time")
        resources = MemoryResourcePlan.compile(memory)
        wire = MemoryWirePlan.compile(memory)
        ordering = MemoryOrderingPlan.compile(memory, wire.routes)
        transport = MemoryTransportPlan.compile(wire, settings.transport, physical_flit_bytes=config.packet.physical_flit_bytes)
        operations = {o.operation_id: o for o in config.operations}
        routes = {r.operation_id: r for r in wire.routes.operations}
        responses = {(p.packet.identity.operation_id, p.packet.identity.segment_index): p
                     for p in wire.packets if p.packet.identity.traffic_class == "response"}
        segments = tuple(MemorySegmentDefinition(
            operations[p.packet.identity.operation_id], p,
            responses.get((p.packet.identity.operation_id, p.packet.identity.segment_index)),
            routes[p.packet.identity.operation_id].initiator_resource_id)
            for p in wire.packets if p.packet.identity.traffic_class == "request")
        digest = content_digest({"memory_plan_sha256": memory.plan_sha256,
                                 "settings": settings.model_dump(mode="json"), "execution": "addressed_memory_ordered_v1"})
        return cls(memory, settings, resources, transport, ordering, segments, digest)


class MemorySegmentRecord(MemoryOperationRecord):
    segment: MemorySegment
    descriptor_retirement_aci_cycles: Cycles | None

    @model_validator(mode="after")
    def retirement(self) -> Self:
        if self.segment.operation_id != self.operation_id or self.segment.logical_bytes != self.logical_bytes:
            raise ValueError("segment lifecycle and identity disagree")
        if self.descriptor_retirement_aci_cycles != self.completion_aci_cycles:
            raise ValueError("segment descriptor must retire exactly at observable completion")
        return self


class MemoryLifecycleEvent(GraphRecord):
    time_aci_cycles: Cycles
    operation_id: Identifier
    segment_index: Index | None
    action: Literal["submission", "acceptance", "source_read_complete", "request_handoff",
                    "response_wire_receipt", "destination_ready", "complete"]


class MemoryDescriptorState(GraphRecord):
    kind: Literal["issue", "responder", "local_client"]
    owner_id: Identifier
    capacity: PositiveInt
    occupied: Index
    peak_occupied: Index
    owners: tuple[tuple[Identifier, Index | None], ...]

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.occupied != len(self.owners) or not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("memory descriptor occupancy is not conserved")
        return self


class MemoryDescriptorEvent(GraphRecord):
    time_aci_cycles: Cycles
    kind: Literal["issue", "responder", "local_client"]
    owner_id: Identifier
    action: Literal["acquire", "release"]
    operation_id: Identifier
    segment_index: Index | None
    occupied: Index


class MemoryPacketExecutionRecord(GraphRecord):
    definition: RoutedMemoryPacket
    planned: MemoryPacketBytes
    injected: MemoryPacketBytes
    injected_flits: Index
    planned_channel_bytes: Index
    launched_channel_bytes: Index

    @model_validator(mode="after")
    def packet_progress(self) -> Self:
        layout = self.definition.packet.layout
        expected = MemoryPacketBytes(useful_bytes=layout.useful_bytes, header_bytes=layout.header_bytes,
                                     padding_bytes=layout.padding_bytes, packet_bytes=layout.packet_bytes)
        header = min(self.injected_flits, layout.header_flits) * layout.physical_flit_bytes
        useful = min(max(0, self.injected_flits - layout.header_flits) * layout.data_capacity_bytes, layout.useful_bytes)
        if (self.planned != expected or self.injected_flits > layout.flit_count
                or self.injected.header_bytes != header or self.injected.useful_bytes != useful
                or self.injected.packet_bytes != self.injected_flits * layout.physical_flit_bytes
                or self.planned_channel_bytes != self.definition.planned_channel_bytes
                or not self.injected.packet_bytes <= self.launched_channel_bytes <= self.planned_channel_bytes):
            raise ValueError("memory packet accounting disagrees with layout or progress")
        return self


def memory_byte_accounting(operations: tuple[MemoryOperationRecord, ...],
                           packets: tuple[MemoryPacketExecutionRecord, ...],
                           chunks: tuple[MemoryChunkRecord, ...]) -> MemoryByteAccounting:
    def total(records: tuple[MemoryPacketBytes, ...]) -> MemoryPacketBytes:
        return MemoryPacketBytes(useful_bytes=sum(r.useful_bytes for r in records),
                                 header_bytes=sum(r.header_bytes for r in records),
                                 padding_bytes=sum(r.padding_bytes for r in records),
                                 packet_bytes=sum(r.packet_bytes for r in records))

    return MemoryByteAccounting(
        planned_network_logical_bytes=sum(o.logical_bytes for o in operations if o.kind in {"read", "write_posted", "write_acknowledged"}),
        planned_local_logical_bytes=sum(o.logical_bytes for o in operations if o.kind in {"local_read", "local_write"}),
        planned_wire=total(tuple(p.planned for p in packets)), injected_wire=total(tuple(p.injected for p in packets)),
        planned_channel_bytes=sum(p.planned_channel_bytes for p in packets),
        launched_channel_bytes=sum(p.launched_channel_bytes for p in packets),
        completed_read_useful_bytes=sum(c.useful_bytes for c in chunks if c.direction == "read"),
        completed_write_useful_bytes=sum(c.useful_bytes for c in chunks if c.direction == "write"),
        completed_read_service_bytes=sum(c.serviced_bytes for c in chunks if c.direction == "read"),
        completed_write_service_bytes=sum(c.serviced_bytes for c in chunks if c.direction == "write"))


class MemoryExecutionResult(MemoryReplayResult):
    execution_plan_sha256: Digest
    settings: MemoryRuntimeConfig
    ordering: tuple[MemoryOperationOrder, ...]
    segments: tuple[MemorySegmentRecord, ...]
    transport: PacketTransportResult
    memory_resources: tuple[MemoryResourceState, ...]
    released_resources: tuple[MemoryResourceState, ...]
    descriptors: tuple[MemoryDescriptorState, ...]
    lifecycle: tuple[MemoryLifecycleEvent, ...]
    descriptor_trace: tuple[MemoryDescriptorEvent, ...]
    ownership_trace: tuple[MemoryOwnershipEvent, ...]
    service_trace: tuple[MemoryServiceEvent, ...]
    chunks: tuple[MemoryChunkRecord, ...]
    wire_packets: tuple[MemoryPacketExecutionRecord, ...]
    accounting: MemoryByteAccounting
    teardown_complete: bool

    @model_validator(mode="after")
    def execution_state(self) -> Self:
        if (self.accounting != memory_byte_accounting(self.operations, self.wire_packets, self.chunks)
                or self.packet_bytes != self.accounting.injected_wire.packet_bytes
                or self.channel_bytes != self.accounting.launched_channel_bytes
                or self.accounting.planned_wire.useful_bytes != self.accounting.planned_network_logical_bytes):
            raise ValueError("memory accounting disagrees with packet, operation or service records")
        if self.memory_service_bytes != sum(c.serviced_bytes for c in self.chunks):
            raise ValueError("memory byte total must count executed service")
        if self.channel_bytes != self.transport.transmitted_channel_bytes:
            raise ValueError("channel byte total must count actual launches")
        if self.status == "complete" and (not self.teardown_complete or self.transport.status != "complete"
                                         or self.accounting.planned_wire != self.accounting.injected_wire
                                         or self.channel_bytes != self.accounting.planned_channel_bytes
                                         or any(d.occupied for d in self.descriptors)
                                         or any(r.reserved_bytes for r in self.released_resources)
                                         or any(o.completion_aci_cycles is None for o in self.operations)
                                         or any(s.completion_aci_cycles is None or s.destination_ready_aci_cycles is None for s in self.segments)):
            raise ValueError("memory execution cannot complete with pending effects or resources")
        return self
