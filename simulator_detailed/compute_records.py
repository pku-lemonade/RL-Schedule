"""Planned compute costs and identities, kept distinct from execution evidence."""

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
    remaining_stages: tuple[Literal["memory_composition"], Literal["buffer_lifecycle"], Literal["compute_runtime"]] = (
        "memory_composition", "buffer_lifecycle", "compute_runtime",
    )
    plan: ComputePlanRecord

    @field_validator("execution_supported", mode="before")
    @classmethod
    def strict_execution_flag(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("execution support must be a boolean")
        return value
