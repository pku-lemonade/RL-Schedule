"""Pure admission and records for the initial disjoint memory execution subset."""

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
from .memory_packets import MemorySegment, MemoryWirePlan, RoutedMemoryPacket
from .memory_plan import MemoryPlan
from .memory_records import MemoryOperationRecord, MemoryReplayResult
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
    """Explicit composition settings; the CLI/input adapter is a later part."""

    transport: MemoryTransportConfig
    responder_capacity_packets: PositiveInt
    request_control_aci_cycles: Cycles
    response_control_aci_cycles: Cycles
    evidence: TransportEvidence


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
    segments: tuple[MemorySegmentDefinition, ...]
    plan_sha256: str

    @classmethod
    def compile(cls, memory: MemoryPlan, settings: MemoryRuntimeConfig) -> MemoryExecutionPlan:
        settings = MemoryRuntimeConfig.model_validate(settings.model_dump(mode="python"))
        memory = MemoryPlan.compile(MemoryReplay.model_validate(memory.config.model_dump(mode="python")),
                                    memory.graph.model_dump(mode="python"))
        config = memory.config
        if (config.endpoint_queue_capacity_packets, config.endpoint_staging_capacity_flits) != (
                settings.transport.endpoint_queue_capacity_packets, settings.transport.endpoint_staging_capacity_flits):
            raise ValueError("memory and transport endpoint capacities must agree")
        buffers = {b.buffer_id: b for b in config.buffers}
        accesses: list[tuple[str, int, int, bool]] = []
        for operation in config.operations:
            if operation.kind not in {"read", "write_posted", "write_acknowledged"}:
                raise ValueError("initial memory execution does not support local operations or fences")
            if operation.depends_on or operation.fence_operations:
                raise ValueError("initial memory execution does not support dependencies")
            if operation.source is None or operation.destination is None:
                raise ValueError("memory execution needs source and destination")
            if not buffers[operation.source.buffer_id].initially_ready:
                raise ValueError("initial memory execution requires an initialized source")
            if not math.isfinite(operation.start_aci_cycles + config.issue_latency_aci_cycles):
                raise ValueError("unrepresentable memory issue time")
            for access, write in ((operation.source, False), (operation.destination, True)):
                buffer = buffers[access.buffer_id]
                start = buffer.base_address + access.offset_bytes
                end = start + access.size_bytes
                for resource, a, b, writes in accesses:
                    if resource == buffer.resource_id and start < b and a < end and (write or writes):
                        raise ValueError("initial memory execution rejects overlapping conflicting accesses")
                accesses.append((buffer.resource_id, start, end, write))
        if any(b.producer_operation_id is not None for b in config.buffers):
            raise ValueError("producer-dependent buffer declarations require ordering support")
        resources = MemoryResourcePlan.compile(memory)
        wire = MemoryWirePlan.compile(memory)
        transport = MemoryTransportPlan.compile(wire, settings.transport)
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
                                 "settings": settings.model_dump(mode="json"), "execution": "addressed_memory_disjoint_v1"})
        return cls(memory, settings, resources, transport, segments, digest)


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
    segment_index: Index
    action: Literal["submission", "acceptance", "source_read_complete", "request_handoff",
                    "response_wire_receipt", "destination_ready", "complete"]


class MemoryDescriptorState(GraphRecord):
    kind: Literal["issue", "responder"]
    owner_id: Identifier
    capacity: PositiveInt
    occupied: Index
    peak_occupied: Index
    owners: tuple[tuple[Identifier, Index], ...]

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.occupied != len(self.owners) or not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("memory descriptor occupancy is not conserved")
        return self


class MemoryDescriptorEvent(GraphRecord):
    time_aci_cycles: Cycles
    kind: Literal["issue", "responder"]
    owner_id: Identifier
    action: Literal["acquire", "release"]
    operation_id: Identifier
    segment_index: Index
    occupied: Index


class MemoryExecutionResult(MemoryReplayResult):
    execution_plan_sha256: Digest
    settings: MemoryRuntimeConfig
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
    teardown_complete: bool

    @model_validator(mode="after")
    def execution_state(self) -> Self:
        if self.memory_service_bytes != sum(c.serviced_bytes for c in self.chunks):
            raise ValueError("memory byte total must count executed service")
        if self.channel_bytes != self.transport.transmitted_channel_bytes:
            raise ValueError("channel byte total must count actual launches")
        if self.status == "complete" and (not self.teardown_complete or self.transport.status != "complete"
                                         or any(d.occupied for d in self.descriptors)
                                         or any(r.reserved_bytes for r in self.released_resources)
                                         or any(s.completion_aci_cycles is None or s.destination_ready_aci_cycles is None for s in self.segments)):
            raise ValueError("memory execution cannot complete with pending effects or resources")
        return self
