"""Strict contracts for the opt-in finite multicast/synchronization child.

These records describe an admitted workload.  They do not construct SimPy
objects and they do not make the legacy ``MULTICAST`` or ``sync_mode`` fields
executable.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, field_validator, model_validator

from ...memory_execution import MemoryRuntimeConfig
from .compute_workload import (
    ComputeOperation,
    ComputeOutput,
    ComputeRate,
    ComputeSlot,
    ComputeWorker,
    StorageDtype,
)
from .memory_replay import (
    MemoryOperation,
    MemoryRange,
    MemorySystemConfig,
    MemoryVersion,
)
from .topology import Coordinate, GraphRecord, Identifier, Index, PositiveInt, unique
from .torus_replay import TransportEvidence

Address = Annotated[int, Field(strict=True, ge=0)]
NonNegative = Annotated[float, Field(strict=True, ge=0)]
Positive = Annotated[float, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class Rectangle(GraphRecord):
    """Inclusive fabric coordinates for the supported broadcast subset."""

    start: Coordinate
    end: Coordinate
    major_axis: Literal["x", "y"]
    include_source: StrictBool = False

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start.x > self.end.x or self.start.y > self.end.y:
            raise ValueError("rectangle bounds must be ordered and non-wrapping")
        return self


class DestinationBinding(GraphRecord):
    """One exact recipient buffer binding for a multicast write."""

    endpoint_id: Identifier
    buffer_id: Identifier
    offset_bytes: NonNegativeInt


class MulticastWrite(GraphRecord):
    operation_id: Identifier
    source_endpoint_id: Identifier
    fabric_id: Index
    source: MemoryRange
    target_offset_bytes: NonNegativeInt = Field(
        description="Common byte address within each target L1; binding offsets are buffer-relative."
    )
    size_bytes: PositiveInt
    rectangle: Rectangle
    destinations: tuple[DestinationBinding, ...] = Field(min_length=1)
    completion: Literal["write_posted", "write_acknowledged"]
    depends_on: tuple[Identifier, ...] = ()
    source_version: MemoryVersion | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.source.size_bytes != self.size_bytes:
            raise ValueError("source range size must equal multicast size")
        if len({item.endpoint_id for item in self.destinations}) != len(self.destinations):
            raise ValueError("multicast destinations must be unique")
        if len({item.buffer_id for item in self.destinations}) != len(self.destinations):
            raise ValueError("multicast destination buffers must be unique")
        unique(self.depends_on, "multicast dependency")
        return self


class ScalarCounter(GraphRecord):
    counter_id: Identifier
    endpoint_id: Identifier
    buffer_id: Identifier
    offset_bytes: NonNegativeInt
    width_bytes: Literal[1, 2, 4, 8]
    initial_value: NonNegativeInt

    @field_validator("width_bytes", mode="before")
    @classmethod
    def integer_width(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("scalar width must be an integer, not a boolean or coerced number")
        return value


class ScalarIncrement(GraphRecord):
    operation_id: Identifier
    source_endpoint_id: Identifier
    fabric_id: Index
    counter_id: Identifier
    completion: Literal["atomic_posted", "atomic_returning"]
    depends_on: tuple[Identifier, ...] = ()
    return_inbox: MemoryRange | None = None

    @model_validator(mode="after")
    def dependencies(self) -> Self:
        unique(self.depends_on, "atomic dependency")
        if self.completion == "atomic_posted" and self.return_inbox is not None:
            raise ValueError("posted increments cannot declare a return inbox")
        return self


class LocalDataPrerequisite(GraphRecord):
    """An extent and version observed only at the declaring operation's L1."""

    access: MemoryRange
    version: MemoryVersion


class SyncGate(GraphRecord):
    operation_id: Identifier
    after_waits: tuple[Identifier, ...] = ()
    local_data: tuple[LocalDataPrerequisite, ...] = ()

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(self.after_waits, "local wait dependency")
        return self


class ScalarWait(GraphRecord):
    wait_id: Identifier
    endpoint_id: Identifier
    counter_id: Identifier
    threshold: NonNegativeInt
    producer_operations: tuple[Identifier, ...] = Field(min_length=1)
    data_ready_after: tuple[Identifier, ...] = ()
    local_data: tuple[LocalDataPrerequisite, ...] = ()

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(self.producer_operations, "wait producer")
        unique(self.data_ready_after, "wait data dependency")
        return self


class SyncControlConfig(GraphRecord):
    tree_policy: Literal["corner_rectangle_tree_v1"] = "corner_rectangle_tree_v1"
    reservation_policy: Literal["atomic_tree_reservation_v1"] = "atomic_tree_reservation_v1"
    scalar_policy: Literal["monotonic_l1_counter_v1"] = "monotonic_l1_counter_v1"
    wait_policy: Literal["local_threshold_wait_v1"] = "local_threshold_wait_v1"
    reservation_capacity: PositiveInt
    replication_capacity_flits: PositiveInt
    reservation_setup_aci_cycles: Positive
    reservation_edge_aci_cycles: Positive
    atomic_native_cycles: Positive
    local_observation_aci_cycles: NonNegative
    atomic_granule_bytes: PositiveInt | None = None
    inline_control_bytes: PositiveInt | None = None
    evidence: TransportEvidence


class MixedComputeJob(GraphRecord):
    job_id: Identifier
    operation: ComputeOperation
    a: LocalDataPrerequisite
    b: LocalDataPrerequisite
    output: ComputeOutput
    depends_on: tuple[Identifier, ...] = ()


class MixedComputeStream(GraphRecord):
    stream_id: Identifier
    worker_tile_id: Identifier
    slots: tuple[ComputeSlot, ...] = Field(min_length=1)
    jobs: tuple[MixedComputeJob, ...] = Field(min_length=1)


class MixedComputeConfig(GraphRecord):
    policy: Literal["finite_compute_dataflow_v1"] = "finite_compute_dataflow_v1"
    dtypes: tuple[StorageDtype, ...] = Field(min_length=1)
    rates: tuple[ComputeRate, ...] = Field(min_length=1)
    workers: tuple[ComputeWorker, ...] = Field(min_length=1)
    streams: tuple[MixedComputeStream, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self) -> Self:
        unique(tuple(d.dtype_id for d in self.dtypes), "storage dtype")
        unique(tuple(r.rate_id for r in self.rates), "compute rate")
        unique(tuple(w.tile_id for w in self.workers), "physical compute worker")
        unique(tuple(w.l1_resource_id for w in self.workers), "physical compute L1")
        unique(tuple(s.stream_id for s in self.streams), "compute stream")
        unique(tuple(j.job_id for s in self.streams for j in s.jobs), "compute job")
        unique(tuple(slot.slot_id for s in self.streams for slot in s.slots), "compute slot")
        return self


class MulticastSyncWorkload(GraphRecord):
    """Versioned input for the pure multicast/synchronization compiler."""

    kind: Literal["multicast_sync_workload"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    model_revision: Literal["finite_multicast_sync_v1"]
    memory: MemorySystemConfig
    runtime: MemoryRuntimeConfig | None = None
    control: SyncControlConfig
    compute: MixedComputeConfig | None = None
    operations: tuple[MemoryOperation, ...] = ()
    gates: tuple[SyncGate, ...] = ()
    writes: tuple[MulticastWrite, ...] = ()
    counters: tuple[ScalarCounter, ...] = ()
    increments: tuple[ScalarIncrement, ...] = ()
    waits: tuple[ScalarWait, ...] = ()

    @model_validator(mode="after")
    def identities(self) -> Self:
        if not self.writes and not self.increments and not self.waits and not self.operations and self.compute is None:
            raise ValueError("multicast/synchronization workload requires an operation")
        unique(tuple(w.operation_id for w in self.writes), "multicast operation")
        unique(tuple(i.operation_id for i in self.increments), "atomic operation")
        unique(tuple(w.wait_id for w in self.waits), "scalar wait")
        unique(tuple(c.counter_id for c in self.counters), "scalar counter")
        job_ids = tuple(j.job_id for st in self.compute.streams for j in st.jobs) if self.compute else ()
        all_operations = (tuple(w.operation_id for w in self.writes) + tuple(i.operation_id for i in self.increments)
                          + tuple(o.operation_id for o in self.operations) + job_ids)
        unique(all_operations + tuple(w.wait_id for w in self.waits), "operation/wait")
        operation_ids = set(all_operations)
        counter_ids = {c.counter_id for c in self.counters}
        wait_ids = {w.wait_id for w in self.waits}
        unique(tuple(g.operation_id for g in self.gates), "operation gate")
        for gate in self.gates:
            if gate.operation_id not in operation_ids or not set(gate.after_waits) <= wait_ids:
                raise ValueError("gate refers to an unknown operation/wait")
        for operation in self.operations:
            if not set(operation.depends_on + operation.destination_ready_after + operation.fence_operations) <= operation_ids:
                raise ValueError(f"memory {operation.operation_id}: unknown dependency")
        for write in self.writes:
            if any(dep not in operation_ids for dep in write.depends_on):
                raise ValueError(f"multicast {write.operation_id}: unknown dependency")
        for increment in self.increments:
            if increment.counter_id not in counter_ids or any(dep not in operation_ids for dep in increment.depends_on):
                raise ValueError(f"atomic {increment.operation_id}: unknown counter/dependency")
        for wait in self.waits:
            if wait.counter_id not in counter_ids:
                raise ValueError(f"wait {wait.wait_id}: unknown counter")
            if any(dep not in operation_ids for dep in wait.producer_operations + wait.data_ready_after):
                raise ValueError(f"wait {wait.wait_id}: unknown producer/dependency")
        return self


class MulticastSyncResult(GraphRecord):
    """Planning/result envelope; execution is added by later child parts."""

    kind: Literal["multicast_sync_result"] = "multicast_sync_result"
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    status: Literal["planned"] = "planned"
    execution_supported: Literal[False] = False
    plan_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    pending_operations: tuple[Identifier, ...]
