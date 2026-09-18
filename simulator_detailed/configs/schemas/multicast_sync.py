"""Strict contracts for the opt-in finite multicast/synchronization child.

These records describe an admitted workload.  They do not construct SimPy
objects and they do not make the legacy ``MULTICAST`` or ``sync_mode`` fields
executable.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .memory_replay import MemoryRange, MemorySystemConfig
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
    target_offset_bytes: NonNegativeInt
    size_bytes: PositiveInt
    rectangle: Rectangle
    destinations: tuple[DestinationBinding, ...] = Field(min_length=1)
    completion: Literal["write_posted", "write_acknowledged"]
    depends_on: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.source.size_bytes != self.size_bytes:
            raise ValueError("source range size must equal multicast size")
        if len({item.endpoint_id for item in self.destinations}) != len(self.destinations):
            raise ValueError("multicast destinations must be unique")
        if len({item.buffer_id for item in self.destinations}) != len(self.destinations):
            raise ValueError("multicast destination buffers must be unique")
        unique(self.depends_on, "multicast dependency")
        if self.source.buffer_id in {item.buffer_id for item in self.destinations} and any(
            item.offset_bytes == self.source.offset_bytes for item in self.destinations
        ):
            # Same-buffer source/target overlap is checked with extents by the
            # compiler; this early check only rejects an ambiguous target offset.
            raise ValueError("multicast source and target cannot start at one buffer offset")
        return self


class ScalarCounter(GraphRecord):
    counter_id: Identifier
    endpoint_id: Identifier
    buffer_id: Identifier
    offset_bytes: NonNegativeInt
    width_bytes: Literal[1, 2, 4, 8]
    initial_value: NonNegativeInt


class ScalarIncrement(GraphRecord):
    operation_id: Identifier
    source_endpoint_id: Identifier
    fabric_id: Index
    counter_id: Identifier
    completion: Literal["atomic_posted", "atomic_returning"]
    depends_on: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def dependencies(self) -> Self:
        unique(self.depends_on, "atomic dependency")
        return self


class ScalarWait(GraphRecord):
    wait_id: Identifier
    endpoint_id: Identifier
    counter_id: Identifier
    threshold: NonNegativeInt
    producer_operations: tuple[Identifier, ...] = Field(min_length=1)
    data_ready_after: tuple[Identifier, ...] = ()

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
    reservation_setup_aci_cycles: NonNegative
    reservation_edge_aci_cycles: NonNegative
    atomic_native_cycles: Positive
    local_observation_aci_cycles: NonNegative
    evidence: TransportEvidence


class MulticastSyncWorkload(GraphRecord):
    """Versioned input for the pure multicast/synchronization compiler."""

    kind: Literal["multicast_sync_workload"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    model_revision: Literal["finite_multicast_sync_v1"]
    memory: MemorySystemConfig
    control: SyncControlConfig
    writes: tuple[MulticastWrite, ...] = ()
    counters: tuple[ScalarCounter, ...] = ()
    increments: tuple[ScalarIncrement, ...] = ()
    waits: tuple[ScalarWait, ...] = ()

    @model_validator(mode="after")
    def identities(self) -> Self:
        if not self.writes and not self.increments and not self.waits:
            raise ValueError("multicast/synchronization workload requires an operation")
        unique(tuple(w.operation_id for w in self.writes), "multicast operation")
        unique(tuple(i.operation_id for i in self.increments), "atomic operation")
        unique(tuple(w.wait_id for w in self.waits), "scalar wait")
        unique(tuple(c.counter_id for c in self.counters), "scalar counter")
        all_operations = tuple(w.operation_id for w in self.writes) + tuple(i.operation_id for i in self.increments)
        unique(all_operations, "operation")
        operation_ids = set(all_operations)
        counter_ids = {c.counter_id for c in self.counters}
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
