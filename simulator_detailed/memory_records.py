"""Immutable admission and lifecycle records for addressed memory."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
    unique,
)

Cycles = float


class MemoryQuantity(GraphRecord):
    field_path: Identifier
    value: float
    unit: Literal["Hz", "bytes", "bytes_per_cycle", "aci_cycles", "native_cycles"]
    source: Literal["configured", "profile", "assumed"]


class MemoryBufferRecord(GraphRecord):
    buffer_id: Identifier
    resource_id: Identifier
    base_address: int = Field(strict=True, ge=0)
    size_bytes: PositiveInt
    reserved_bytes: PositiveInt
    ready: bool


class MemoryPacketBytes(GraphRecord):
    """Physical byte decomposition; padding includes unused data-flit capacity."""

    useful_bytes: Index
    header_bytes: Index
    padding_bytes: Index
    packet_bytes: Index

    @model_validator(mode="after")
    def conserved(self) -> MemoryPacketBytes:
        if self.packet_bytes != self.useful_bytes + self.header_bytes + self.padding_bytes:
            raise ValueError("packet bytes must equal useful, header and padding bytes")
        return self


class MemoryByteAccounting(GraphRecord):
    """Planned logical work, actual launches and completed service stay distinct."""

    planned_network_logical_bytes: Index
    planned_local_logical_bytes: Index
    planned_wire: MemoryPacketBytes
    injected_wire: MemoryPacketBytes
    planned_channel_bytes: Index
    launched_channel_bytes: Index
    completed_read_useful_bytes: Index
    completed_write_useful_bytes: Index
    completed_read_service_bytes: Index
    completed_write_service_bytes: Index


class MemoryOperationRecord(GraphRecord):
    operation_id: Identifier
    kind: Literal["read", "write_posted", "write_acknowledged", "local_read", "local_write", "fence"]
    status: Literal["accepted", "complete", "incomplete", "rejected"]
    submission_aci_cycles: Cycles | None
    descriptor_acceptance_aci_cycles: Cycles | None
    source_read_completion_aci_cycles: Cycles | None
    final_request_handoff_aci_cycles: Cycles | None
    response_receipt_aci_cycles: Cycles | None
    destination_ready_aci_cycles: Cycles | None
    completion_aci_cycles: Cycles | None
    logical_bytes: Index
    packet_bytes: Index
    channel_bytes: Index
    memory_service_bytes: Index

    @model_validator(mode="after")
    def ordered_times(self) -> MemoryOperationRecord:
        submission, acceptance = self.submission_aci_cycles, self.descriptor_acceptance_aci_cycles
        source, handoff = self.source_read_completion_aci_cycles, self.final_request_handoff_aci_cycles
        response, ready, complete = self.response_receipt_aci_cycles, self.destination_ready_aci_cycles, self.completion_aci_cycles
        values = (submission, acceptance, source, handoff, response, ready, complete)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("memory lifecycle time cannot be negative")
        pairs = [(submission, acceptance), (submission, complete), *((acceptance, value) for value in (source, handoff, response, ready, complete)),
                 (source, ready)]
        # Lifecycle facts form a partial order. In a read, request handoff
        # precedes the remote source read; posted completion precedes visibility.
        if self.kind == "read":
            pairs.extend(((handoff, source), (source, response), (response, ready), (ready, complete)))
        elif self.kind in {"write_posted", "write_acknowledged"}:
            pairs.extend(((source, handoff), (handoff, ready), (handoff, complete)))
            if self.kind == "write_acknowledged":
                pairs.extend(((ready, response), (response, complete)))
            elif response is not None:
                raise ValueError("posted writes have no response receipt")
        elif self.kind in {"local_read", "local_write"}:
            pairs.append((source if self.kind == "local_read" else ready, complete))
            if handoff is not None or response is not None:
                raise ValueError("local memory clients have no network lifecycle")
            if (self.kind == "local_read" and ready is not None) or (self.kind == "local_write" and source is not None):
                raise ValueError("local memory lifecycle must match its access direction")
        elif self.kind == "fence" and any(value is not None for value in (acceptance, source, handoff, response, ready)):
            raise ValueError("fences have no descriptor, memory or wire work")
        if any(a is not None and b is not None and a > b for a, b in pairs):
            raise ValueError("memory lifecycle violates its operation's causal order")
        boundary = (handoff if self.kind == "write_posted" else ready if self.kind in {"read", "local_write"}
                    else source if self.kind == "local_read" else None)
        if complete is not None and boundary is not None and complete != boundary:
            raise ValueError("memory completion differs from its observable boundary")
        return self


class MemoryServiceRecord(GraphRecord):
    service_id: Identifier
    resource_id: Identifier
    client_id: Identifier
    direction: Literal["read", "write"]
    address: int = Field(strict=True, ge=0)
    useful_bytes: Index
    serviced_bytes: Index
    queue_enter_aci_cycles: Cycles
    start_aci_cycles: Cycles
    end_aci_cycles: Cycles

    @model_validator(mode="after")
    def service_interval(self) -> MemoryServiceRecord:
        if self.end_aci_cycles < self.start_aci_cycles or self.start_aci_cycles < self.queue_enter_aci_cycles:
            raise ValueError("memory service interval is not ordered")
        if self.serviced_bytes < self.useful_bytes:
            raise ValueError("serviced bytes cannot be smaller than useful bytes")
        return self


class MemoryPlanRecord(GraphRecord):
    kind: Literal["memory_plan"] = "memory_plan"
    schema_version: Literal[1] = 1
    source_sha256: Digest
    configuration_sha256: Digest
    plan_sha256: Digest
    source_kind: Literal["canonical_graph", "hardware_profile"]
    source_json: str
    configuration_json: str
    quantities: tuple[MemoryQuantity, ...]
    resource_ids: tuple[Identifier, ...]
    endpoint_ids: tuple[Identifier, ...]
    buffer_ids: tuple[Identifier, ...]
    operation_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def unique_identity(self) -> MemoryPlanRecord:
        unique(tuple(q.field_path for q in self.quantities), "memory quantity")
        unique(self.resource_ids, "plan resource")
        unique(self.endpoint_ids, "plan endpoint")
        unique(self.buffer_ids, "plan buffer")
        unique(self.operation_ids, "plan operation")
        return self


class MemoryReplayResult(GraphRecord):
    kind: Literal["memory_replay_result"] = "memory_replay_result"
    schema_version: Literal[1] = 1
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending"]
    plan: MemoryPlanRecord
    elapsed_aci_cycles: Cycles
    operations: tuple[MemoryOperationRecord, ...]
    buffers: tuple[MemoryBufferRecord, ...]
    service: tuple[MemoryServiceRecord, ...]
    pending: tuple[Identifier, ...] = ()
    logical_bytes: Index
    packet_bytes: Index
    channel_bytes: Index
    memory_service_bytes: Index
    execution: Literal["addressed_memory_unimplemented", "addressed_memory_disjoint_v1", "addressed_memory_ordered_v1"] = "addressed_memory_unimplemented"
    silicon_timing: Literal["unvalidated"] = "unvalidated"

    @model_validator(mode="after")
    def result_state(self) -> MemoryReplayResult:
        if (self.status == "complete") != (self.reason == "drained"):
            raise ValueError("only a drained replay can be complete")
        if self.status == "complete" and self.pending:
            raise ValueError("complete replay cannot contain pending work")
        if self.logical_bytes != sum(item.logical_bytes for item in self.operations):
            raise ValueError("logical byte total disagrees with operations")
        if self.packet_bytes != sum(item.packet_bytes for item in self.operations):
            raise ValueError("packet byte total disagrees with operations")
        if self.channel_bytes != sum(item.channel_bytes for item in self.operations):
            raise ValueError("channel byte total disagrees with operations")
        if self.memory_service_bytes != sum(item.memory_service_bytes for item in self.operations):
            raise ValueError("memory service total disagrees with operations")
        unique(tuple(item.operation_id for item in self.operations), "result operation")
        return self
