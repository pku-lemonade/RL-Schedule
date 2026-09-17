"""Compute costs, identities and execution events with explicit accounting units."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .configs.schemas.memory_replay import Positive
from .configs.schemas.topology import (
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
)
from .configs.schemas.torus_replay import Cycles


class MatrixDimensions(GraphRecord):
    batch: PositiveInt
    m: PositiveInt
    n: PositiveInt
    k: PositiveInt


class TensorFootprint(GraphRecord):
    useful_bytes: PositiveInt
    storage_bytes: PositiveInt

    @model_validator(mode="after")
    def storage_covers_useful(self) -> Self:
        if self.storage_bytes < self.useful_bytes:
            raise ValueError("tensor storage cannot be smaller than useful bytes")
        return self


class ComputeCost(GraphRecord):
    policy: Literal["effective_matmul_v1"] = "effective_matmul_v1"
    rate_id: Identifier
    dimensions: MatrixDimensions
    useful_work: PositiveInt
    executed_work: PositiveInt
    a: TensorFootprint
    b: TensorFootprint
    c: TensorFootprint
    service_native_cycles: Positive
    service_aci_cycles: Positive

    @model_validator(mode="after")
    def work_covers_useful(self) -> Self:
        d = self.dimensions
        if self.useful_work != 2 * d.batch * d.m * d.n * d.k or self.executed_work < self.useful_work:
            raise ValueError("planned work disagrees with dimensions or padding")
        return self


class ComputeJobPlan(GraphRecord):
    job_id: Identifier
    stream_id: Identifier
    worker_tile_id: Identifier
    slot_id: Identifier
    slot_generation: Index
    predecessors: tuple[Identifier, ...]
    # FIFO stage order permits overlap; it is not a predecessor-completion wait.
    fifo_predecessor_id: Identifier | None
    cost: ComputeCost


class ComputePlanRecord(GraphRecord):
    source_sha256: Digest
    configuration_sha256: Digest
    effective_sha256: Digest
    plan_sha256: Digest
    source_json: str
    configuration_json: str
    jobs: tuple[ComputeJobPlan, ...] = Field(min_length=1)


class ComputeWorkloadResult(GraphRecord):
    """A planning result cannot claim work, timestamps, or runtime completion."""

    kind: Literal["compute_workload_result"] = "compute_workload_result"
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    status: Literal["planned"] = "planned"
    execution_supported: Literal[False] = False
    completed_job_ids: tuple[()] = ()
    completed_work: Annotated[int, Field(strict=True, ge=0, le=0)] = 0
    remaining_stages: tuple[Literal["runtime_admission"]] = ("runtime_admission",)
    plan: ComputePlanRecord

    @field_validator("execution_supported", mode="before")
    @classmethod
    def strict_execution_flag(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("execution support must be a boolean")
        return value


ComputeStage = Literal["slot_wait", "reader_start", "inputs_ready", "compute_wait", "operand_start",
                       "operand_end", "math_start", "math_end", "result_start", "output_ready",
                       "writer_wait", "writer_start", "writer_complete", "slot_release"]
ComputeResourceKind = Literal["reader", "compute", "writer"]


class ComputeStageEvent(GraphRecord):
    time_aci_cycles: Cycles
    job_id: Identifier
    stream_id: Identifier
    worker_tile_id: Identifier
    slot_id: Identifier
    generation: Index
    action: ComputeStage


class ComputeResourceOwner(GraphRecord):
    engine_index: Index
    job_id: Identifier
    acquired_aci_cycles: Cycles


class ComputeResourceState(GraphRecord):
    worker_tile_id: Identifier
    kind: ComputeResourceKind
    capacity: PositiveInt
    occupied: Index
    peak_occupied: Index
    owners: tuple[ComputeResourceOwner, ...]

    @model_validator(mode="after")
    def conserved(self) -> Self:
        if (self.occupied != len(self.owners) or not self.occupied <= self.peak_occupied <= self.capacity
                or len({owner.engine_index for owner in self.owners}) != self.occupied
                or len({owner.job_id for owner in self.owners}) != self.occupied
                or any(owner.engine_index >= self.capacity for owner in self.owners)):
            raise ValueError("compute resource occupancy is not conserved")
        return self


class ComputeResourceEvent(GraphRecord):
    time_aci_cycles: Cycles
    worker_tile_id: Identifier
    kind: ComputeResourceKind
    action: Literal["acquire", "release"]
    engine_index: Index
    job_id: Identifier
    capacity: PositiveInt
    occupied: Index

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.occupied > self.capacity or self.engine_index >= self.capacity:
            raise ValueError("compute resource event exceeds configured capacity")
        return self


class ComputeWorkAccounting(GraphRecord):
    planned_useful_work: Index
    planned_executed_work: Index
    completed_useful_work: Index
    completed_executed_work: Index
    math_busy_aci_cycles: Cycles
    context_occupied_aci_cycles: Cycles

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (not self.completed_useful_work <= self.completed_executed_work <= self.planned_executed_work
                or not self.completed_useful_work <= self.planned_useful_work <= self.planned_executed_work
                or self.math_busy_aci_cycles > self.context_occupied_aci_cycles):
            raise ValueError("completed compute work or math time exceeds its admitted ownership")
        return self
