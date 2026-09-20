"""One bounded FIFO aggregate read/write server per physical memory resource.

Admission is nonblocking. Jobs contain only addressed control metadata; callers
retain any data in their own counted staging until completion. The server's only
wait is a finite local service timeout, never a network or client callback.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal, Self

import simpy
from pydantic import Field, model_validator
from simpy.events import Event, ProcessGenerator

from .configs.schemas.memory_replay import MemoryServiceConfig, Positive
from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .configs.schemas.torus_replay import Cycles
from .memory_records import MemoryServiceRecord
from .scalar_service import (
    AtomicChunk,
    CounterDefinition,
    CounterHandle,
    CounterState,
    ObservationChunk,
    ScalarServiceRecord,
)


class ServiceTiming(GraphRecord):
    config: MemoryServiceConfig
    aci_clock_hz: Positive

    @model_validator(mode="after")
    def representable(self) -> Self:
        # Include the extra touched granule possible for an unaligned client.
        g = self.config.service_granule_bytes
        for serviced in (g, self.config.chunk_bytes + g):
            self.duration(serviced)
        return self

    def duration(self, serviced_bytes: int) -> tuple[float, float]:
        try:
            native = self.config.fixed_latency_cycles + serviced_bytes / self.config.bytes_per_cycle
            ratio = self.config.native_clock_hz / self.aci_clock_hz
            aci = native / ratio
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("unrepresentable memory service duration") from exc
        if not all(math.isfinite(v) and v > 0 for v in (native, ratio, aci)):
            raise ValueError("unrepresentable memory service duration")
        return native, aci

    def cost(self, address: int, useful_bytes: int) -> tuple[int, float, float]:
        if type(address) is not int or address < 0 or type(useful_bytes) is not int or not 0 < useful_bytes <= self.config.chunk_bytes:
            raise ValueError("service requires a positive bounded addressed chunk")
        g = self.config.service_granule_bytes
        serviced = ((address % g + useful_bytes + g - 1) // g) * g
        native, aci = self.duration(serviced)
        return serviced, native, aci

    def observation_cost(self, address: int, width: int, control_aci_cycles: float) -> tuple[int, float, float]:
        """One scalar word observation plus its configured local control cost."""
        g = self.config.service_granule_bytes
        granule = ((address % g + width + g - 1) // g) * g
        base_native, base_aci = self.duration(granule)
        ratio = self.config.native_clock_hz / self.aci_clock_hz
        aci, native = base_aci + control_aci_cycles, base_native + control_aci_cycles * ratio
        if not all(math.isfinite(v) and v > 0 for v in (native, aci)):
            raise ValueError("unrepresentable scalar observation duration")
        return granule, native, aci


class ServiceChunk(GraphRecord):
    service_id: Identifier
    client_id: Identifier
    direction: Literal["read", "write"]
    address: Index
    useful_bytes: PositiveInt


class MemoryChunkRecord(MemoryServiceRecord):
    policy: Literal["aggregate_shared_rw_v1"] = "aggregate_shared_rw_v1"
    native_clock_hz: Positive
    aci_clock_hz: Positive
    native_cycles: Positive
    service_aci_cycles: Positive


class MemoryServiceEvent(GraphRecord):
    time_aci_cycles: Cycles
    resource_id: Identifier
    service_id: Identifier
    action: Literal["admit", "start", "finish"]
    queued: Index
    active: int = Field(strict=True, ge=0, le=1)


class MemoryServiceState(GraphRecord):
    resource_id: Identifier
    policy: Literal["aggregate_shared_rw_v1"] = "aggregate_shared_rw_v1"
    queue_capacity: PositiveInt
    queued: Index
    queue_peak: Index
    active: int = Field(strict=True, ge=0, le=1)
    pending_service_ids: tuple[Identifier, ...]
    completed_read_bytes: Index
    completed_write_bytes: Index

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not self.queued <= self.queue_peak <= self.queue_capacity:
            raise ValueError("memory service queue exceeds its bound")
        if len(self.pending_service_ids) != self.queued + self.active:
            raise ValueError("memory service owners disagree with occupancy")
        return self


@dataclass(frozen=True)
class _Job:
    chunk: ServiceChunk | AtomicChunk | ObservationChunk
    entered: float
    serviced_bytes: int
    native_cycles: float
    aci_cycles: float
    done: Event
    admission_sequence: int


class MemoryService:
    def __init__(self, env: simpy.Environment, resource_id: str, timing: ServiceTiming):
        self.timing = ServiceTiming.model_validate(timing.model_dump(mode="python"))
        self.env, self.resource_id = env, resource_id
        self._queue: deque[_Job] = deque()
        self._active: _Job | None = None
        self._seen: set[str] = set()
        self._peak = 0
        self._scheduled_until = float(env.now)
        self._changed = env.event()
        self._records: list[MemoryChunkRecord] = []
        self._events: list[MemoryServiceEvent] = []
        self._counters: dict[str, CounterHandle] = {}
        self._counter_values: dict[str, int] = {}
        self._counter_updates: dict[str, int] = {}
        self._counter_changed: dict[str, Event] = {}
        self._scalar_records: list[ScalarServiceRecord] = []
        self._admissions = 0
        self._linearizations = 0

    @property
    def changed(self) -> Event:
        return self._changed

    @property
    def records(self) -> tuple[MemoryChunkRecord, ...]:
        return tuple(self._records)

    @property
    def events(self) -> tuple[MemoryServiceEvent, ...]:
        return tuple(self._events)

    @property
    def is_drained(self) -> bool:
        return self._active is None and not self._queue

    def register_counter(self, definition: CounterDefinition) -> CounterHandle:
        definition = CounterDefinition.model_validate(definition.model_dump(mode="python"))
        if (definition.counter_id in self._counters or definition.width_bytes not in {1, 2, 4, 8}
                or definition.granule_bytes % definition.width_bytes or definition.address % definition.width_bytes
                or definition.address % definition.granule_bytes + definition.width_bytes > definition.granule_bytes
                or definition.initial_value >= 1 << (8 * definition.width_bytes)):
            raise ValueError("invalid or duplicate addressed scalar definition")
        start = definition.address - definition.address % definition.granule_bytes
        for handle in self._counters.values():
            other = handle.definition
            other_start = other.address - other.address % other.granule_bytes
            if start < other_start + other.granule_bytes and other_start < start + definition.granule_bytes:
                raise ValueError("atomic granules overlap")
        handle = CounterHandle(definition)
        self._counters[definition.counter_id] = handle
        self._counter_values[definition.counter_id] = definition.initial_value
        self._counter_updates[definition.counter_id] = 0
        self._counter_changed[definition.counter_id] = self.env.event()
        return handle

    def _counter(self, handle: CounterHandle) -> CounterDefinition:
        if self._counters.get(handle.definition.counter_id) is not handle:
            raise ValueError("foreign scalar handle")
        return handle.definition

    def counter_state(self, handle: CounterHandle) -> CounterState:
        definition = self._counter(handle)
        return CounterState(definition=definition, value=self._counter_values[definition.counter_id],
                            updates=self._counter_updates[definition.counter_id])

    def counter_changed(self, handle: CounterHandle) -> Event:
        return self._counter_changed[self._counter(handle).counter_id]

    @property
    def scalar_records(self) -> tuple[ScalarServiceRecord, ...]:
        return tuple(self._scalar_records)

    def try_scalar(self, handle: CounterHandle, chunk: AtomicChunk | ObservationChunk) -> Event | None:
        definition = self._counter(handle)
        chunk = type(chunk).model_validate(chunk.model_dump(mode="python"))
        if chunk.counter_id != definition.counter_id:
            raise ValueError("scalar job refers to a different counter")
        ratio = self.timing.config.native_clock_hz / self.timing.aci_clock_hz
        if isinstance(chunk, AtomicChunk):
            native, aci = chunk.native_cycles, chunk.native_cycles / ratio
            granule = definition.granule_bytes
        else:
            granule, native, aci = self.timing.observation_cost(definition.address, definition.width_bytes, chunk.control_aci_cycles)
        if not all(math.isfinite(v) and v > 0 for v in (native, aci)):
            raise ValueError("unrepresentable scalar service duration")
        return self._submit(chunk, granule, native, aci)

    def try_submit(self, chunk: ServiceChunk) -> Event | None:
        # Python mode preserves malformed bool/int values from model_copy;
        # JSON serialization can normalize them before strict validation.
        chunk = ServiceChunk.model_validate(chunk.model_dump(mode="python"))
        cost, native, aci = self.timing.cost(chunk.address, chunk.useful_bytes)
        return self._submit(chunk, cost, native, aci)

    def _submit(self, chunk: ServiceChunk | AtomicChunk | ObservationChunk, cost: int, native: float, aci: float) -> Event | None:
        if chunk.service_id in self._seen:
            raise ValueError("duplicate memory service identity")
        if self._active is not None and len(self._queue) == self.timing.config.queue_capacity:
            return None
        start = float(self.env.now) if self._active is None else self._scheduled_until
        end = start + aci
        if not math.isfinite(end) or end <= start:
            raise ValueError("memory duration cannot advance the scheduled environment clock")
        job = _Job(chunk, float(self.env.now), cost, native, aci, self.env.event(), self._admissions)
        self._admissions += 1
        self._seen.add(chunk.service_id)
        self._scheduled_until = end
        if self._active is None:
            self._active = job
            self._log("admit", job)
            self.env.process(self._run(job))
        else:
            self._queue.append(job)
            self._peak = max(self._peak, len(self._queue))
            self._log("admit", job)
        self._notify()
        return job.done

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def _log(self, action: Literal["admit", "start", "finish"], job: _Job) -> None:
        self._events.append(MemoryServiceEvent(time_aci_cycles=self.env.now, resource_id=self.resource_id,
                                               service_id=job.chunk.service_id, action=action,
                                               queued=len(self._queue), active=int(self._active is not None)))

    def _run(self, job: _Job) -> ProcessGenerator:
        start = float(self.env.now)
        self._log("start", job)
        yield self.env.timeout(job.aci_cycles)
        record: MemoryChunkRecord | ScalarServiceRecord
        if isinstance(job.chunk, ServiceChunk):
            record = MemoryChunkRecord(
                resource_id=self.resource_id, **job.chunk.model_dump(), serviced_bytes=job.serviced_bytes,
                queue_enter_aci_cycles=job.entered, start_aci_cycles=start, end_aci_cycles=self.env.now,
                native_clock_hz=self.timing.config.native_clock_hz, aci_clock_hz=self.timing.aci_clock_hz,
                native_cycles=job.native_cycles, service_aci_cycles=job.aci_cycles)
            self._records.append(record)
        else:
            scalar = self._counters[job.chunk.counter_id].definition
            old = self._counter_values[scalar.counter_id]
            atomic = isinstance(job.chunk, AtomicChunk)
            new = old + 1 if atomic else old
            if new >= 1 << (8 * scalar.width_bytes):
                raise ValueError("scalar update would overflow its admitted width")
            if atomic:
                self._counter_values[scalar.counter_id] = new
                self._counter_updates[scalar.counter_id] += 1
                self._linearizations += 1
            record = ScalarServiceRecord(resource_id=self.resource_id, service_id=job.chunk.service_id,
                client_id=job.chunk.client_id, counter_id=scalar.counter_id, direction=job.chunk.direction,
                address=scalar.address, width_bytes=scalar.width_bytes, read_service_bytes=job.serviced_bytes,
                write_service_bytes=job.serviced_bytes if atomic else 0, old_value=old, new_value=new,
                admission_sequence=job.admission_sequence, linearization_sequence=self._linearizations if atomic else None,
                queue_enter_aci_cycles=job.entered, start_aci_cycles=start, end_aci_cycles=self.env.now,
                native_cycles=job.native_cycles, service_aci_cycles=job.aci_cycles)
            self._scalar_records.append(record)
            if atomic:
                self._counter_changed[scalar.counter_id].succeed()
                self._counter_changed[scalar.counter_id] = self.env.event()
        self._active = None
        self._log("finish", job)
        # Release the grant and promote FIFO work before waking the client.
        if self._queue:
            self._active = self._queue.popleft()
            self.env.process(self._run(self._active))
        self._notify()
        job.done.succeed(record)

    def snapshot(self) -> MemoryServiceState:
        pending = (() if self._active is None else (self._active.chunk.service_id,)) + tuple(j.chunk.service_id for j in self._queue)
        return MemoryServiceState(resource_id=self.resource_id, queue_capacity=self.timing.config.queue_capacity,
                                  queued=len(self._queue), queue_peak=self._peak, active=int(self._active is not None),
                                  pending_service_ids=pending,
                                  completed_read_bytes=sum(r.serviced_bytes for r in self._records if r.direction == "read")
                                      + sum(r.read_service_bytes for r in self._scalar_records),
                                  completed_write_bytes=sum(r.serviced_bytes for r in self._records if r.direction == "write")
                                       + sum(r.write_service_bytes for r in self._scalar_records))
