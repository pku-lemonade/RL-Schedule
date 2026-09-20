"""Attach finite compute stages to an existing mixed session via local capabilities.

No environment, memory allocator, link or independent hardware capacity is
constructed here. The original standalone wrappers retain their v1 records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from simpy.events import ProcessGenerator

from .compute_buffers import (
    ComputeSlotEvent,
    ComputeSlotPoolState,
    ComputeSlotState,
    SlotAction,
    SlotStage,
)
from .compute_cost import checked_compute_finish
from .compute_pipeline import BoundedPipeline, StagePool
from .compute_records import (
    ComputeResourceEvent,
    ComputeResourceKind,
    ComputeResourceState,
    ComputeStage,
    ComputeStageEvent,
)
from .configs.schemas.topology import GraphRecord, Identifier
from .multicast_compute_plan import MixedComputePlan

if TYPE_CHECKING:
    from .multicast_memory_runtime import MulticastMemoryRuntime


@dataclass(frozen=True, eq=False)
class LocalOperationCapability:
    operation_id: str
    resource_id: str


class MixedComputeSnapshot(GraphRecord):
    stages: tuple[ComputeStageEvent, ...]
    resources: tuple[ComputeResourceState, ...]
    resource_events: tuple[ComputeResourceEvent, ...]
    slots: tuple[ComputeSlotPoolState, ...]
    slot_events: tuple[ComputeSlotEvent, ...]
    pending: tuple[Identifier, ...]


class MixedComputeComponent:
    def __init__(self, session: MulticastMemoryRuntime, plan: MixedComputePlan):
        config = session.plan.workload.compute
        if config is None or session.plan.record.compute != plan:
            raise ValueError("compute attachment requires its exact admitted mixed plan")
        self.session, self.plan, self.env = session, plan, session.env
        self.capabilities = session.attach_compute(plan)
        self.jobs = {j.job_id: j for j in plan.jobs}
        self.streams = {s.stream_id: s for s in config.streams}
        self.events: list[ComputeResourceEvent] = []
        self.stages: list[ComputeStageEvent] = []
        self.slot_events: list[ComputeSlotEvent] = []
        self._states = {s.slot_id: ComputeSlotState(slot_id=s.slot_id, generation=0, job_id=None, stage="free")
                        for stream in config.streams for s in stream.slots}
        self._released: set[str] = set()
        self._next = dict.fromkeys(self.streams, 0)
        self._peak = dict.fromkeys(self.streams, 0)
        self._changed = self.env.event()
        self.pools: dict[tuple[str, ComputeResourceKind], StagePool] = {}
        for worker in config.workers:
            definitions: tuple[tuple[ComputeResourceKind, int], ...] = (("reader", worker.reader_capacity), ("compute", worker.compute_contexts), ("writer", worker.writer_capacity))
            for kind, capacity in definitions:
                self.pools[worker.tile_id, kind] = StagePool(self.env, worker.tile_id, kind, capacity, self.events)
        self.pipeline = BoundedPipeline(self.env, tuple(tuple(j.job_id for j in st.jobs) for st in config.streams),
                                       {j.job_id: j.worker_tile_id for j in plan.jobs}, self.pools, self)

    @property
    def is_drained(self) -> bool:
        return len(self._released) == len(self.jobs) and all(not p.owners for p in self.pools.values())

    def _stage(self, job_id: str, action: ComputeStage) -> None:
        job = self.jobs[job_id]
        self.stages.append(ComputeStageEvent(time_aci_cycles=self.env.now, job_id=job_id, worker_tile_id=job.worker_tile_id,
                                            stream_id=job.stream_id, slot_id=job.slot_id, generation=job.generation, action=action))

    def _slot(self, job_id: str, action: SlotAction, stage: SlotStage) -> None:
        job = self.jobs[job_id]
        current = self._states[job.slot_id]
        if current.generation != job.generation or (action != "reserve" and current.job_id != job_id):
            raise ValueError("stale compute slot generation")
        self._states[job.slot_id] = current.model_copy(update={"stage": stage, "job_id": None if stage == "free" else job_id,
                                                             "generation": job.generation + (stage == "free")})
        stream = self.streams[job.stream_id]
        occupied = sum(self._states[s.slot_id].job_id is not None for s in stream.slots)
        self._peak[job.stream_id] = max(self._peak[job.stream_id], occupied)
        self.slot_events.append(ComputeSlotEvent(time_aci_cycles=self.env.now, stream_id=job.stream_id, worker_tile_id=job.worker_tile_id,
            slot_id=job.slot_id, job_id=job_id, generation=job.generation, action=action, capacity=len(stream.slots),
            free=len(stream.slots)-occupied, occupied=occupied))
        self._changed.succeed()
        self._changed = self.env.event()

    def reserve(self, job_id: str) -> ProcessGenerator:
        job = self.jobs[job_id]
        self._stage(job_id, "slot_wait")
        for operation in job.operands:
            yield from self.session.wait_local_prerequisites(self.capabilities[operation])
        while self._states[job.slot_id].job_id is not None:
            yield self._changed
        self._slot(job_id, "reserve", "reserved")
        self._next[job.stream_id] += 1

    def eligible(self, job_id: str, kind: ComputeResourceKind) -> ProcessGenerator:
        job = self.jobs[job_id]
        previous = job.compute_after if kind == "compute" else job.writer_after if kind == "writer" else None
        if kind != "reader":
            self._stage(job_id, "compute_wait" if kind == "compute" else "writer_wait")
        if previous is not None:
            yield self.session.local_completion(self.capabilities[previous])

    def serve(self, job_id: str, kind: ComputeResourceKind) -> ProcessGenerator:
        job = self.jobs[job_id]
        if kind == "reader":
            # Imported operands already occupy their admitted A/B slots; no reader packet is emitted.
            self._stage(job_id, "reader_start")
            self._slot(job_id, "publish_inputs", "inputs_published")
            self._stage(job_id, "inputs_ready")
        elif kind == "compute":
            self._slot(job_id, "consume", "consuming")
            self._stage(job_id, "operand_start")
            for operation in job.operands:
                self.session.activate_local(self.capabilities[operation])
                yield self.session.local_completion(self.capabilities[operation])
            self._stage(job_id, "operand_end")
            checked_compute_finish(float(self.env.now), job.cost.service_aci_cycles)
            self._stage(job_id, "math_start")
            yield self.env.timeout(job.cost.service_aci_cycles)
            self._stage(job_id, "math_end")
            self._stage(job_id, "result_start")
            self.session.activate_local(self.capabilities[job.result_operation])
            yield self.session.local_completion(self.capabilities[job.result_operation])
            self._slot(job_id, "publish_output", "output_published")
            self._stage(job_id, "output_ready")
        else:
            self._slot(job_id, "drain", "draining")
            self._stage(job_id, "writer_start")
            if job.writer_operation is not None:
                self.session.activate_local(self.capabilities[job.writer_operation])
                yield self.session.local_completion(self.capabilities[job.writer_operation])
            self.session.activate_local(self.capabilities[job_id])
            yield self.session.local_completion(self.capabilities[job_id])
            self._slot(job_id, "writer_complete", "draining")
            self._stage(job_id, "writer_complete")

    def release(self, job_id: str) -> ProcessGenerator:
        job = self.jobs[job_id]
        for consumer in job.consumers:
            yield self.session.local_completion(self.session.local_operation(job.resource_id, consumer))
        self._slot(job_id, "release", "free")
        self._released.add(job_id)
        self._stage(job_id, "slot_release")

    def snapshot(self) -> MixedComputeSnapshot:
        states: list[ComputeSlotPoolState] = []
        for stream in self.streams.values():
            slots = tuple(self._states[s.slot_id] for s in stream.slots)
            occupied = sum(s.job_id is not None for s in slots)
            index = self._next[stream.stream_id]
            next_job = stream.jobs[index].job_id if index < len(stream.jobs) else None
            states.append(ComputeSlotPoolState(stream_id=stream.stream_id, worker_tile_id=stream.worker_tile_id,
                resource_id=self.jobs[stream.jobs[0].job_id].resource_id, capacity=len(slots), free=len(slots)-occupied,
                occupied=occupied, peak_occupied=self._peak[stream.stream_id], next_job_id=next_job,
                waiting_job_id=next_job, slots=slots, footprint_bytes=sum(r.size_bytes for s in stream.slots for r in (s.a,s.b,s.c))))
        return MixedComputeSnapshot(stages=tuple(self.stages), resources=tuple(p.snapshot() for p in self.pools.values()),
            resource_events=tuple(self.events), slots=tuple(states), slot_events=tuple(self.slot_events),
            pending=tuple(j for j in self.jobs if j not in self._released))
