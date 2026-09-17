"""Finite compute planning contracts. Parsing does not enable execution."""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .memory_replay import MemoryRange, MemorySystemConfig, NonNegative, Positive
from .topology import GraphRecord, Identifier, Index, PositiveInt, unique
from .torus_replay import TransportEvidence


class DenseLayout(GraphRecord):
    kind: Literal["dense_row_major"]


class TiledLayout(GraphRecord):
    kind: Literal["tiled"]
    tile_rows: PositiveInt
    tile_columns: PositiveInt


TensorLayout = Annotated[DenseLayout | TiledLayout, Field(discriminator="kind")]


class StorageDtype(GraphRecord):
    dtype_id: Identifier
    bytes_per_element: PositiveInt
    evidence: TransportEvidence


class ComputeTensor(GraphRecord):
    # FC callers explicitly flatten before admission; there is no legacy-axis inference.
    shape: tuple[PositiveInt, ...] = Field(min_length=2, max_length=3)
    dtype: Identifier
    layout: TensorLayout


class ComputeOperation(GraphRecord):
    kind: Literal["matmul", "fc"]
    a: ComputeTensor
    b: ComputeTensor
    c: ComputeTensor
    shared_weights: StrictBool
    accumulator_precision: Identifier
    fidelity: Identifier
    flattening: Literal["already_flattened_bmk_v1"] | None = None

    @model_validator(mode="after")
    def explicit_fc(self) -> Self:
        if (self.kind == "fc") != (self.flattening is not None):
            raise ValueError("only FC requires explicit already-flattened B/M/K dimensions")
        return self


class ComputeRateKey(GraphRecord):
    operation: Literal["matmul", "fc"]
    a_dtype: Identifier
    b_dtype: Identifier
    c_dtype: Identifier
    accumulator_precision: Identifier
    layout: TensorLayout
    fidelity: Identifier


class ComputeBlock(GraphRecord):
    m: PositiveInt
    n: PositiveInt
    k: PositiveInt


class ComputeRate(GraphRecord):
    rate_id: Identifier
    policy: Literal["effective_matmul_v1"]
    key: ComputeRateKey
    block: ComputeBlock
    work_per_native_cycle: Positive
    setup_native_cycles: NonNegative
    quantum_native_cycles: Positive
    minimum_native_cycles: Positive
    evidence: TransportEvidence

    @model_validator(mode="after")
    def quantum_multiple(self) -> Self:
        # Decimal JSON settings retain their intended quantum ratios (e.g. .3/.1).
        ratio = Fraction(str(self.minimum_native_cycles)) / Fraction(str(self.quantum_native_cycles))
        if ratio.denominator != 1:
            raise ValueError("minimum compute service must be a positive quantum multiple")
        return self


class ComputeWorker(GraphRecord):
    tile_id: Identifier
    l1_resource_id: Identifier
    endpoint_ids: tuple[Identifier, ...] = Field(min_length=1)
    rate_ids: tuple[Identifier, ...] = Field(min_length=1)
    native_clock_hz: Positive
    reader_capacity: PositiveInt
    compute_contexts: PositiveInt
    writer_capacity: PositiveInt
    evidence: TransportEvidence

    @model_validator(mode="after")
    def unique_bindings(self) -> Self:
        unique(self.endpoint_ids, "worker endpoint")
        unique(self.rate_ids, "worker rate")
        return self


class ComputeVersion(GraphRecord):
    kind: Literal["initial", "job"]
    producer_job_id: Identifier | None = None

    @model_validator(mode="after")
    def tagged(self) -> Self:
        if (self.kind == "job") != (self.producer_job_id is not None):
            raise ValueError("only a job version names a producer job")
        return self


class ComputeInput(GraphRecord):
    mode: Literal["local", "remote"]
    source: MemoryRange
    version: ComputeVersion
    fabric_id: Index | None = None

    @model_validator(mode="after")
    def fabric(self) -> Self:
        if (self.mode == "remote") != (self.fabric_id is not None):
            raise ValueError("only a remote input requires a fabric")
        return self


class ComputeOutput(GraphRecord):
    mode: Literal["local", "write_posted", "write_acknowledged"]
    destination: MemoryRange
    fabric_id: Index | None = None

    @model_validator(mode="after")
    def fabric(self) -> Self:
        if (self.mode != "local") != (self.fabric_id is not None):
            raise ValueError("only a remote output requires a fabric")
        return self


class ComputeJob(GraphRecord):
    job_id: Identifier
    operation: ComputeOperation
    a: ComputeInput
    b: ComputeInput
    output: ComputeOutput
    depends_on: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def dependencies(self) -> Self:
        unique(self.depends_on, "compute dependency")
        return self


class ComputeSlot(GraphRecord):
    slot_id: Identifier
    a: MemoryRange
    b: MemoryRange
    c: MemoryRange


class ComputeStream(GraphRecord):
    stream_id: Identifier
    worker_tile_id: Identifier
    slots: tuple[ComputeSlot, ...] = Field(min_length=1)
    jobs: tuple[ComputeJob, ...] = Field(min_length=1)


class ComputeWorkload(GraphRecord):
    kind: Literal["compute_workload"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    policy: Literal["finite_compute_dataflow_v1"]
    buffer_policy: Literal["fifo_item_slots_v1"]
    memory: MemorySystemConfig
    dtypes: tuple[StorageDtype, ...] = Field(min_length=1)
    rates: tuple[ComputeRate, ...] = Field(min_length=1)
    workers: tuple[ComputeWorker, ...] = Field(min_length=1)
    streams: tuple[ComputeStream, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self) -> Self:
        unique(tuple(d.dtype_id for d in self.dtypes), "storage dtype")
        unique(tuple(r.rate_id for r in self.rates), "compute rate")
        unique(tuple(w.tile_id for w in self.workers), "physical compute worker")
        unique(tuple(s.stream_id for s in self.streams), "compute stream")
        unique(tuple(j.job_id for s in self.streams for j in s.jobs), "compute job")
        unique(tuple(slot.slot_id for s in self.streams for slot in s.slots), "compute slot")
        if any(b.producer_operation_id is not None for b in self.memory.buffers):
            raise ValueError("compute buffers use job versions, not undeclared memory-operation producers")
        return self
