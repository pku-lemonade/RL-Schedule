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
    chunk: ServiceChunk
    entered: float
    serviced_bytes: int
    native_cycles: float
    aci_cycles: float
    done: Event


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

    def try_submit(self, chunk: ServiceChunk) -> Event | None:
        # Python mode preserves malformed bool/int values from model_copy;
        # JSON serialization can normalize them before strict validation.
        chunk = ServiceChunk.model_validate(chunk.model_dump(mode="python"))
        cost, native, aci = self.timing.cost(chunk.address, chunk.useful_bytes)
        if chunk.service_id in self._seen:
            raise ValueError("duplicate memory service identity")
        if self._active is not None and len(self._queue) == self.timing.config.queue_capacity:
            return None
        start = float(self.env.now) if self._active is None else self._scheduled_until
        end = start + aci
        if not math.isfinite(end) or end <= start:
            raise ValueError("memory duration cannot advance the scheduled environment clock")
        job = _Job(chunk, float(self.env.now), cost, native, aci, self.env.event())
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
        record = MemoryChunkRecord(
            resource_id=self.resource_id, **job.chunk.model_dump(), serviced_bytes=job.serviced_bytes,
            queue_enter_aci_cycles=job.entered, start_aci_cycles=start, end_aci_cycles=self.env.now,
            native_clock_hz=self.timing.config.native_clock_hz, aci_clock_hz=self.timing.aci_clock_hz,
            native_cycles=job.native_cycles, service_aci_cycles=job.aci_cycles)
        self._records.append(record)
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
                                  completed_read_bytes=sum(r.serviced_bytes for r in self._records if r.direction == "read"),
                                  completed_write_bytes=sum(r.serviced_bytes for r in self._records if r.direction == "write"))
