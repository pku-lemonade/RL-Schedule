"""Bounded FIFO stage scheduling, independent of memory and arithmetic service.

Only a stream's next item waits for reservation. Downstream coroutines exist
only for reserved items, and stage queues retain those counted item slots.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

import simpy
from simpy.events import ProcessGenerator

from .compute_records import (
    ComputeResourceEvent,
    ComputeResourceKind,
    ComputeResourceOwner,
    ComputeResourceState,
)


class StagePool:
    """FIFO contexts for one physical worker and stage; no L1 capacity charge."""

    def __init__(self, env: simpy.Environment, worker: str, kind: ComputeResourceKind,
                 capacity: int, events: list[ComputeResourceEvent]):
        self.env, self.worker, self.capacity = env, worker, capacity
        self.kind: ComputeResourceKind = kind
        self.events = events
        self.owners: dict[str, ComputeResourceOwner] = {}
        self.peak = 0
        self._queue: deque[str] = deque()
        self._changed = env.event()

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def request(self, job_id: str) -> ProcessGenerator:
        if job_id in self.owners or job_id in self._queue:
            raise ValueError("duplicate stage context request")
        self._queue.append(job_id)
        while self._queue[0] != job_id or len(self.owners) == self.capacity:
            yield self._changed
        self._queue.popleft()
        self.acquire(job_id)
        self._notify()

    def acquire(self, job_id: str) -> None:
        if job_id in self.owners or len(self.owners) >= self.capacity:
            raise ValueError("duplicate or exhausted compute stage context")
        occupied = {owner.engine_index for owner in self.owners.values()}
        index = next(i for i in range(self.capacity) if i not in occupied)
        self.owners[job_id] = ComputeResourceOwner(engine_index=index, job_id=job_id, acquired_aci_cycles=self.env.now)
        self.peak = max(self.peak, len(self.owners))
        self._record(job_id, index, "acquire")

    def release(self, job_id: str) -> None:
        if job_id not in self.owners:
            raise ValueError("foreign or already released compute stage context")
        owner = self.owners.pop(job_id)
        self._record(job_id, owner.engine_index, "release")
        self._notify()

    def _record(self, job_id: str, index: int, action: Literal["acquire", "release"]) -> None:
        self.events.append(ComputeResourceEvent(time_aci_cycles=self.env.now, worker_tile_id=self.worker,
                                                kind=self.kind, action=action, engine_index=index,
                                                job_id=job_id, capacity=self.capacity, occupied=len(self.owners)))

    def snapshot(self) -> ComputeResourceState:
        return ComputeResourceState(worker_tile_id=self.worker, kind=self.kind, capacity=self.capacity,
                                    occupied=len(self.owners), peak_occupied=self.peak, owners=tuple(self.owners.values()))


class PipelineStages(Protocol):
    """Service hooks: reservation is whole-item; release includes all consumers."""

    def reserve(self, job_id: str) -> ProcessGenerator: ...
    def eligible(self, job_id: str, kind: ComputeResourceKind) -> ProcessGenerator: ...
    def serve(self, job_id: str, kind: ComputeResourceKind) -> ProcessGenerator: ...
    def release(self, job_id: str) -> ProcessGenerator: ...


class BoundedPipeline:
    """Independent stages with FIFO admission across eligible streams.

    The service adapter waits for dependency eligibility before joining a stage
    queue. No stage grant is held while waiting for a slot or earlier FIFO stage.
    """

    def __init__(self, env: simpy.Environment, streams: Sequence[Sequence[str]], workers: Mapping[str, str],
                 pools: Mapping[tuple[str, ComputeResourceKind], StagePool], stages: PipelineStages):
        self.env, self.workers, self.pools, self.stages = env, workers, pools, stages
        self.done = env.event()
        self._remaining = sum(len(stream) for stream in streams)
        for stream in streams:
            env.process(self._feed(stream))

    def _stage(self, job_id: str, kind: ComputeResourceKind) -> ProcessGenerator:
        yield from self.stages.eligible(job_id, kind)
        pool = self.pools[self.workers[job_id], kind]
        yield from pool.request(job_id)
        yield from self.stages.serve(job_id, kind)
        pool.release(job_id)

    def _feed(self, jobs: Sequence[str]) -> ProcessGenerator:
        for job_id in jobs:
            yield from self.stages.reserve(job_id)
            yield from self._stage(job_id, "reader")
            self.env.process(self._finish(job_id))

    def _finish(self, job_id: str) -> ProcessGenerator:
        yield from self._stage(job_id, "compute")
        yield from self._stage(job_id, "writer")
        yield from self.stages.release(job_id)
        self._remaining -= 1
        if self._remaining == 0:
            self.done.succeed()
