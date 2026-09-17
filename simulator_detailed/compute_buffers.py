"""Finite FIFO slot generations over the memory session's existing reservations.

This ledger owns logical lifetimes only. MemoryResources remains the sole byte
allocator, access/version owner and service provider. No arithmetic is run here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator
from simpy.events import Event

from .compute_memory import ComputeMemoryPlan
from .configs.schemas.compute_workload import ComputeSlot
from .configs.schemas.memory_replay import MemoryRange, MemoryVersion
from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .configs.schemas.torus_replay import Cycles
from .memory_runtime import MemorySession

SlotStage = Literal["free", "reserved", "inputs_published", "consuming", "output_published", "draining"]
SlotAction = Literal["wait", "reserve", "publish_inputs", "consume", "publish_output", "drain", "writer_complete", "release"]


class ComputeSlotState(GraphRecord):
    slot_id: Identifier
    generation: Index
    job_id: Identifier | None
    stage: SlotStage

    @model_validator(mode="after")
    def ownership(self) -> Self:
        if (self.stage == "free") != (self.job_id is None):
            raise ValueError("slot stage disagrees with its job ownership")
        return self


class ComputeSlotPoolState(GraphRecord):
    policy: Literal["fifo_item_slots_v1"] = "fifo_item_slots_v1"
    stream_id: Identifier
    worker_tile_id: Identifier
    resource_id: Identifier
    capacity: PositiveInt
    free: Index
    occupied: Index
    peak_occupied: Index
    footprint_bytes: PositiveInt
    next_job_id: Identifier | None
    waiting_job_id: Identifier | None
    slots: tuple[ComputeSlotState, ...]

    @model_validator(mode="after")
    def conserved(self) -> Self:
        if (self.free + self.occupied != self.capacity or len(self.slots) != self.capacity
                or self.occupied != sum(slot.job_id is not None for slot in self.slots)
                or not self.occupied <= self.peak_occupied <= self.capacity
                or (self.waiting_job_id is not None and self.waiting_job_id != self.next_job_id)):
            raise ValueError("compute slot capacity or FIFO wait is not conserved")
        return self


class ComputeSlotEvent(GraphRecord):
    time_aci_cycles: Cycles
    stream_id: Identifier
    worker_tile_id: Identifier
    slot_id: Identifier
    job_id: Identifier
    generation: Index
    action: SlotAction
    capacity: PositiveInt
    free: Index
    occupied: Index

    @model_validator(mode="after")
    def conserved(self) -> Self:
        if self.free + self.occupied != self.capacity:
            raise ValueError("compute slot event loses capacity")
        return self


@dataclass(frozen=True, eq=False)
class ComputeSlotToken:
    stream_id: str
    slot_id: str
    job_id: str
    generation: int


@dataclass
class _Slot:
    definition: ComputeSlot
    generation: int = 0
    stage: SlotStage = "free"
    token: ComputeSlotToken | None = None


class ComputeBuffers:
    """One whole-bundle ledger per session, with at most one waiting producer per stream.

    try_reserve admits only the next FIFO item. A failed attempt owns no slot;
    callers retry after a relevant lifecycle fact or `changed` notification.
    Slot release never releases the persistent physical reservation.
    """

    def __init__(self, plan: ComputeMemoryPlan, session: MemorySession):
        plan = plan.revalidate()
        if session.binding is None or session.binding.plan_sha256 != plan.session.plan_sha256:
            raise ValueError("compute buffers require the matching admitted memory session")
        self.plan, self.session, self.env = plan, session, session.env
        self._streams = {s.stream_id: s for s in plan.workload.config.streams}
        self._jobs = {j.job_id: j for j in plan.workload.record.jobs}
        self._memory_jobs = {j.job_id: j for j in plan.jobs}
        self._operations = {o.operation_id: o for o in plan.session.execution.memory.config.operations}
        self._gates = {g.gate_id: g for g in plan.session.gates}
        self._slots = {s.slot_id: _Slot(s) for stream in self._streams.values() for s in stream.slots}
        self._next = dict.fromkeys(self._streams, 0)
        self._waiting: set[str] = set()
        self._peaks = dict.fromkeys(self._streams, 0)
        self._released = 0
        self._events: list[ComputeSlotEvent] = []
        self._changed = self.env.event()
        self._check_backing()
        self._component = session.register_owner_component(session.owner, "fifo_item_slots_v1")

    @property
    def changed(self) -> Event:
        return self._changed

    @property
    def events(self) -> tuple[ComputeSlotEvent, ...]:
        return tuple(self._events)

    @property
    def is_drained(self) -> bool:
        return self._released == len(self._jobs)

    def _check_backing(self) -> None:
        for slot in self._slots.values():
            for extent in (slot.definition.a, slot.definition.b, slot.definition.c):
                handle = self.session.memory.handles[extent.buffer_id]
                owner = self.session.memory.resources[handle.buffer.resource_id]
                if not owner.owns(handle):
                    raise ValueError("slot backing reservation was released or replaced")

    def _idle(self, slot: _Slot) -> bool:
        return all(self.session.memory.resources[self.session.memory.handles[r.buffer_id].buffer.resource_id].range_is_idle(
            self.session.memory.handles[r.buffer_id], offset_bytes=r.offset_bytes, size_bytes=r.size_bytes)
            for r in (slot.definition.a, slot.definition.b, slot.definition.c))

    def _ready(self, extent: MemoryRange, version: MemoryVersion) -> bool:
        handle = self.session.memory.handles[extent.buffer_id]
        return self.session.memory.resources[handle.buffer.resource_id].is_ready(
            handle, offset_bytes=extent.offset_bytes, size_bytes=extent.size_bytes, version=version)

    def _gate_ready(self, gate_id: str) -> bool:
        gate = self._gates[gate_id]
        return (all(self.session.gate_time(parent) is not None for parent in gate.after_gates)
                and all(self.session.lifecycle_time(w.operation_id, w.event) is not None for w in gate.waits))

    def _record(self, token: ComputeSlotToken, action: SlotAction) -> None:
        stream = self._streams[token.stream_id]
        occupied = sum(self._slots[s.slot_id].token is not None for s in stream.slots)
        self._peaks[stream.stream_id] = max(self._peaks[stream.stream_id], occupied)
        self._events.append(ComputeSlotEvent(time_aci_cycles=self.env.now, stream_id=stream.stream_id,
                                             worker_tile_id=stream.worker_tile_id, slot_id=token.slot_id,
                                             job_id=token.job_id, generation=token.generation, action=action,
                                             capacity=len(stream.slots), free=len(stream.slots) - occupied, occupied=occupied))
        if not self._changed.triggered:
            self._changed.succeed()
        self._changed = self.env.event()

    def _live(self, token: object, expected: SlotStage) -> tuple[ComputeSlotToken, _Slot]:
        self._check_backing()
        if not isinstance(token, ComputeSlotToken):
            raise TypeError("foreign compute slot token")
        slot = self._slots.get(token.slot_id)
        if (slot is None or slot.token is not token or slot.generation != token.generation
                or slot.stage != expected):
            raise ValueError(f"foreign, stale or wrong-stage compute slot token; expected {expected}")
        return token, slot

    def try_reserve(self, job_id: str, *, activate_reader: bool = True) -> ComputeSlotToken | None:
        self._check_backing()
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("slot reservation names an unadmitted job")
        stream = self._streams[job.stream_id]
        index = self._next[stream.stream_id]
        if index >= len(stream.jobs) or stream.jobs[index].job_id != job_id:
            raise ValueError("only the next unreserved FIFO job may request a slot")
        slot = self._slots[job.slot_id]
        token = ComputeSlotToken(job.stream_id, job.slot_id, job_id, job.slot_generation)
        reader = self._memory_jobs[job_id].gate_ids[0]
        if slot.token is not None or not self._idle(slot) or not self._gate_ready(reader):
            if job_id not in self._waiting:
                self._waiting.add(job_id)
                self._record(token, "wait")
            return None
        if slot.generation != job.slot_generation:
            raise ValueError("FIFO slot generation differs from admitted assignment")
        if activate_reader:
            self.session.activate(self.session.gate(reader))
        slot.token, slot.stage = token, "reserved"
        self._next[stream.stream_id] += 1
        self._waiting.discard(job_id)
        self._record(token, "reserve")
        return token

    def start_reader(self, token: ComputeSlotToken) -> None:
        """Open a reserved reader gate after its bounded reader context is owned."""
        token, _ = self._live(token, "reserved")
        self.session.activate(self.session.gate(self._memory_jobs[token.job_id].gate_ids[0]))

    def publish_inputs(self, token: ComputeSlotToken) -> None:
        token, slot = self._live(token, "reserved")
        job = self._memory_jobs[token.job_id]
        for operation_id in job.operand_operations:
            operation = self._operations[operation_id]
            version = self.plan.session.execution.ordering.operations[operation_id].source_version
            if operation.source is None or version is None or not self._ready(operation.source, version):
                raise ValueError("slot inputs require the admitted ready memory versions")
        self.session.activate(self.session.gate(job.gate_ids[1]))
        slot.stage = "inputs_published"
        self._record(token, "publish_inputs")

    def consume(self, token: ComputeSlotToken) -> None:
        token, slot = self._live(token, "inputs_published")
        self.session.activate(self.session.gate(self._memory_jobs[token.job_id].gate_ids[2]))
        slot.stage = "consuming"
        self._record(token, "consume")

    def publish_output(self, token: ComputeSlotToken) -> None:
        token, slot = self._live(token, "consuming")
        job = self._memory_jobs[token.job_id]
        result = self._operations[job.result_operation]
        version = MemoryVersion(kind="producer", producer_id=job.result_operation)
        if result.destination is None or not self._ready(result.destination, version):
            raise ValueError("slot output requires completed result service for this generation")
        self.session.activate(self.session.gate(job.gate_ids[4]))
        slot.stage = "output_published"
        self._record(token, "publish_output")

    def begin_drain(self, token: ComputeSlotToken) -> None:
        token, slot = self._live(token, "output_published")
        self.session.activate(self.session.gate(self._memory_jobs[token.job_id].gate_ids[5]))
        slot.stage = "draining"
        self._record(token, "drain")

    def finish_writer(self, token: ComputeSlotToken) -> None:
        """Publish job completion independently of consumers retaining its local result."""
        token, _ = self._live(token, "draining")
        self.session.activate(self.session.gate(self._memory_jobs[token.job_id].gate_ids[6]))
        self._record(token, "writer_complete")

    def release_ready(self, token: ComputeSlotToken) -> bool:
        """Observe release eligibility without changing ownership or readiness."""
        token, slot = self._live(token, "draining")
        job = self._memory_jobs[token.job_id]
        return (self.session.gate_time(job.gate_ids[6]) is not None and self._idle(slot)
                and all(self.session.lifecycle_time(op, "complete") is not None for op in job.consumer_operations))

    def release(self, token: ComputeSlotToken) -> None:
        token, slot = self._live(token, "draining")
        if not self.release_ready(token):
            raise ValueError("slot still has an unfinished writer, consumer or memory lease")
        slot.token, slot.stage = None, "free"
        slot.generation += 1
        self._released += 1
        self._record(token, "release")
        if self.is_drained:
            self.session.complete_component(self._component)

    def snapshot(self) -> tuple[ComputeSlotPoolState, ...]:
        workers = {w.tile_id: w for w in self.plan.workload.config.workers}
        pools: list[ComputeSlotPoolState] = []
        for stream_id, stream in self._streams.items():
            states = tuple(self._slots[s.slot_id] for s in stream.slots)
            slots = tuple(ComputeSlotState(slot_id=s.definition.slot_id, generation=s.generation,
                                           job_id=None if s.token is None else s.token.job_id, stage=s.stage) for s in states)
            occupied = sum(s.job_id is not None for s in slots)
            index = self._next[stream_id]
            next_job = stream.jobs[index].job_id if index < len(stream.jobs) else None
            pools.append(ComputeSlotPoolState(stream_id=stream_id, worker_tile_id=stream.worker_tile_id,
                                              resource_id=workers[stream.worker_tile_id].l1_resource_id,
                                              capacity=len(slots), free=len(slots) - occupied, occupied=occupied,
                                              peak_occupied=self._peaks[stream_id],
                                              footprint_bytes=sum(r.size_bytes for s in stream.slots for r in (s.a, s.b, s.c)),
                                              next_job_id=next_job, waiting_job_id=next_job if next_job in self._waiting else None,
                                              slots=slots))
        return tuple(pools)
