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
        values = (
            self.submission_aci_cycles,
            self.descriptor_acceptance_aci_cycles,
            self.source_read_completion_aci_cycles,
            self.final_request_handoff_aci_cycles,
            self.response_receipt_aci_cycles,
            self.destination_ready_aci_cycles,
            self.completion_aci_cycles,
        )
        previous: float | None = None
        for value in values:
            if value is not None and (previous is not None and value < previous):
                raise ValueError("memory operation lifecycle timestamps must be ordered")
            if value is not None:
                previous = value
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
    execution: Literal["addressed_memory_unimplemented"] = "addressed_memory_unimplemented"
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
