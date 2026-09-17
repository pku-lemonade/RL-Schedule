"""Single-job abstract execution on one shared memory/NoC clock.

Arithmetic advances time using the admitted effective cost; no tensor values or
instructions are evaluated. Multi-item scheduling is a separate admission step.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

import simpy
from pydantic import Field, StrictBool, model_validator
from simpy.events import ProcessGenerator

from .compute_buffers import ComputeBuffers, ComputeSlotEvent, ComputeSlotPoolState
from .compute_cost import checked_compute_finish
from .compute_memory import ComputeMemoryPlan, compute_memory_id
from .compute_records import (
    ComputePlanRecord,
    ComputeResourceEvent,
    ComputeResourceKind,
    ComputeResourceOwner,
    ComputeResourceState,
    ComputeStage,
    ComputeStageEvent,
    ComputeWorkAccounting,
)
from .configs.schemas.topology import Digest, GraphRecord, Identifier
from .configs.schemas.torus_replay import Cycles
from .memory_execution import MemoryExecutionResult
from .memory_runtime import MemorySession


class ComputeExecutionResult(GraphRecord):
    """Executed evidence; the nested memory status describes the memory session."""

    kind: Literal["compute_workload_result"] = "compute_workload_result"
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    policy: Literal["finite_compute_dataflow_v1"] = "finite_compute_dataflow_v1"
    execution: Literal["single_job_v1"] = "single_job_v1"
    buffer_policy: Literal["fifo_item_slots_v1"] = "fifo_item_slots_v1"
    numerical_execution: Literal["unsupported"] = "unsupported"
    silicon_timing: Literal["unvalidated"] = "unvalidated"
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending", "awaiting_finalization"]
    plan: ComputePlanRecord
    execution_plan_sha256: Digest
    elapsed_aci_cycles: Cycles
    completed_job_ids: tuple[Identifier, ...]
    work: ComputeWorkAccounting
    stages: tuple[ComputeStageEvent, ...]
    resources: tuple[ComputeResourceState, ...]
    resource_events: tuple[ComputeResourceEvent, ...]
    slots: tuple[ComputeSlotPoolState, ...]
    slot_events: tuple[ComputeSlotEvent, ...]
    memory_session: MemoryExecutionResult
    pending: tuple[Identifier, ...]
    teardown_complete: StrictBool

    @model_validator(mode="after")
    def result_state(self) -> Self:
        jobs = {job.job_id: job for job in self.plan.jobs}
        if len(jobs) != 1:
            raise ValueError("single-job execution requires exactly one admitted job")
        sequence = ("slot_wait", "reader_start", "inputs_ready", "compute_wait", "operand_start", "operand_end",
                    "math_start", "math_end", "result_start", "output_ready", "writer_wait", "writer_start",
                    "writer_complete", "slot_release")
        if tuple(event.action for event in self.stages) != sequence[:len(self.stages)]:
            raise ValueError("execution stages are not a causal single-job prefix")
        previous = 0.0
        for event in self.stages:
            job = jobs.get(event.job_id)
            if (job is None or (event.stream_id, event.worker_tile_id, event.slot_id, event.generation) !=
                    (job.stream_id, job.worker_tile_id, job.slot_id, job.slot_generation)
                    or not previous <= event.time_aci_cycles <= self.elapsed_aci_cycles):
                raise ValueError("execution event differs from its admitted identity or clock")
            previous = event.time_aci_cycles
        completed = tuple(event.job_id for event in self.stages if event.action == "writer_complete")
        math_jobs = [jobs[event.job_id] for event in self.stages if event.action == "math_end"]
        if (self.completed_job_ids != completed or len(set(completed)) != len(completed)
                or len({job.job_id for job in math_jobs}) != len(math_jobs)
                or self.work.planned_useful_work != sum(job.cost.useful_work for job in jobs.values())
                or self.work.planned_executed_work != sum(job.cost.executed_work for job in jobs.values())
                or self.work.completed_useful_work != sum(job.cost.useful_work for job in math_jobs)
                or self.work.completed_executed_work != sum(job.cost.executed_work for job in math_jobs)):
            raise ValueError("compute accounting disagrees with executed events or admitted costs")
        if (self.status == "complete") != (self.reason == "drained") or self.teardown_complete != (self.status == "complete"):
            raise ValueError("only finalized full drain can report workload completion")
        if self.status == "complete" and (
                self.pending or len(self.stages) != len(sequence) or set(completed) != set(jobs) or len(math_jobs) != len(jobs)
                or any(resource.occupied for resource in self.resources) or any(pool.occupied for pool in self.slots)
                or self.memory_session.status != "complete" or not self.memory_session.teardown_complete):
            raise ValueError("completed workload still has pending ownership or work")
        return self


class _StagePool:
    """Finite physical worker contexts, distinct from already reserved L1 bytes."""

    def __init__(self, env: simpy.Environment, worker: str, kind: ComputeResourceKind,
                 capacity: int, events: list[ComputeResourceEvent]):
        self.env, self.worker, self.capacity = env, worker, capacity
        self.kind: ComputeResourceKind = kind
        self.events = events
        self.owners: dict[str, ComputeResourceOwner] = {}
        self.peak = 0

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

    def _record(self, job_id: str, index: int, action: Literal["acquire", "release"]) -> None:
        self.events.append(ComputeResourceEvent(time_aci_cycles=self.env.now, worker_tile_id=self.worker,
                                                kind=self.kind, action=action, engine_index=index,
                                                job_id=job_id, capacity=self.capacity, occupied=len(self.owners)))

    def snapshot(self) -> ComputeResourceState:
        return ComputeResourceState(worker_tile_id=self.worker, kind=self.kind, capacity=self.capacity,
                                    occupied=len(self.owners), peak_occupied=self.peak, owners=tuple(self.owners.values()))


class ComputeRuntime:
    """Own one admitted job's clock, stage contexts and exactly-once finalization.

    Construction rejects multi-job plans before runtime allocation. advance()
    observes without teardown; run() additionally finalizes a fully drained run.
    Both accept an absolute horizon and resume the same processes and owners.
    """

    def __init__(self, plan: ComputeMemoryPlan):
        plan = plan.revalidate()
        if len(plan.jobs) != 1:
            raise ValueError("single_job_v1 requires exactly one job; multi-item scheduling is not implemented")
        self.plan = plan
        self.env = simpy.Environment()
        self.memory = MemorySession(self.env, plan.session)
        self.buffers = ComputeBuffers(plan, self.memory)
        self._component = self.memory.register_owner_component(self.memory.owner, "compute_runtime_v1")
        self._stages: list[ComputeStageEvent] = []
        self._resource_events: list[ComputeResourceEvent] = []
        self._pools: dict[tuple[str, ComputeResourceKind], _StagePool] = {}
        for worker in plan.workload.config.workers:
            capacities: tuple[tuple[ComputeResourceKind, int], ...] = (
                ("reader", worker.reader_capacity), ("compute", worker.compute_contexts), ("writer", worker.writer_capacity))
            for kind, capacity in capacities:
                self._pools[worker.tile_id, kind] = _StagePool(self.env, worker.tile_id, kind, capacity, self._resource_events)
        self._owner_complete = False
        self._final: ComputeExecutionResult | None = None
        self.env.process(self._execute())

    def _stage(self, action: ComputeStage) -> None:
        job = self.plan.workload.record.jobs[0]
        self._stages.append(ComputeStageEvent(time_aci_cycles=self.env.now, job_id=job.job_id,
                                              stream_id=job.stream_id, worker_tile_id=job.worker_tile_id,
                                              slot_id=job.slot_id, generation=job.slot_generation, action=action))

    def _execute(self) -> ProcessGenerator:
        job, memory_job = self.plan.workload.record.jobs[0], self.plan.jobs[0]
        reader = self._pools[job.worker_tile_id, "reader"]
        compute = self._pools[job.worker_tile_id, "compute"]
        writer = self._pools[job.worker_tile_id, "writer"]
        self._stage("slot_wait")
        token = self.buffers.try_reserve(job.job_id)
        if token is None:
            raise ValueError("single admitted job cannot reserve its idle slot")
        # With one admitted job the stage context is immediately available;
        # no memory coroutine runs between slot activation and reader ownership.
        reader.acquire(job.job_id)
        self._stage("reader_start")
        for operation in memory_job.reader_operations:
            yield self.memory.lifecycle_event(operation, "complete")
        self.buffers.publish_inputs(token)
        self._stage("inputs_ready")
        reader.release(job.job_id)

        self._stage("compute_wait")
        compute.acquire(job.job_id)
        self.buffers.consume(token)
        self._stage("operand_start")
        for operation in memory_job.operand_operations:
            yield self.memory.lifecycle_event(operation, "complete")
        self._stage("operand_end")
        checked_compute_finish(float(self.env.now), job.cost.service_aci_cycles)
        self._stage("math_start")
        yield self.env.timeout(job.cost.service_aci_cycles)
        self._stage("math_end")
        self.memory.activate(self.memory.gate(memory_job.gate_ids[3]))
        self._stage("result_start")
        yield self.memory.lifecycle_event(memory_job.result_operation, "complete")
        self.buffers.publish_output(token)
        self._stage("output_ready")
        compute.release(job.job_id)

        self._stage("writer_wait")
        writer.acquire(job.job_id)
        self.buffers.begin_drain(token)
        self._stage("writer_start")
        if memory_job.writer_operation is not None:
            yield self.memory.lifecycle_event(memory_job.writer_operation, "complete")
        self.buffers.finish_writer(token)
        self._stage("writer_complete")
        writer.release(job.job_id)
        self.buffers.release(token)
        self._stage("slot_release")
        self.memory.complete_component(self._component)
        self.memory.complete_owner(self.memory.owner)
        self._owner_complete = True

    @property
    def is_drained(self) -> bool:
        return (self._owner_complete and self.buffers.is_drained and not any(p.owners for p in self._pools.values())
                and self.memory.is_drained)

    def advance(self, *, max_aci_cycles: float | None = None) -> ComputeExecutionResult:
        if self._final is not None:
            return self._final
        horizon = self.plan.workload.config.memory.max_aci_cycles if max_aci_cycles is None else max_aci_cycles
        if isinstance(horizon, bool) or not math.isfinite(horizon) or horizon <= self.env.now:
            raise ValueError("compute horizon must be finite and later than current time")
        while self.env.peek() != float("inf") and self.env.peek() <= horizon:
            self.env.step()
        # Observe partial arithmetic/context occupancy at the requested horizon,
        # without moving an already idle or drained run beyond its last event.
        if self.env.peek() != float("inf") and self.env.now < horizon:
            self.env.run(until=horizon)
        return self.snapshot()

    def run(self, *, max_aci_cycles: float | None = None) -> ComputeExecutionResult:
        result = self.advance(max_aci_cycles=max_aci_cycles)
        return self.finalize() if self.is_drained else result

    def finalize(self) -> ComputeExecutionResult:
        if self._final is not None:
            return self._final
        if not self.is_drained:
            raise ValueError("compute finalization requires all stages, slots and memory effects to drain")
        self.memory.finalize(self.memory.owner)
        self._final = self.snapshot()
        return self._final

    def snapshot(self) -> ComputeExecutionResult:
        if self._final is not None:
            return self._final
        jobs = self.plan.workload.record.jobs
        math_jobs = {event.job_id for event in self._stages if event.action == "math_end"}
        completed = tuple(event.job_id for event in self._stages if event.action == "writer_complete")
        stage_times = {event.action: event.time_aci_cycles for event in self._stages}
        math_busy = (stage_times.get("math_end", float(self.env.now)) - stage_times["math_start"]
                     if "math_start" in stage_times else 0.0)
        active_contexts: dict[str, float] = {}
        context_time = 0.0
        for event in self._resource_events:
            if event.kind == "compute":
                if event.action == "acquire":
                    active_contexts[event.job_id] = event.time_aci_cycles
                else:
                    context_time += event.time_aci_cycles - active_contexts.pop(event.job_id)
        context_time += sum(float(self.env.now) - start for start in active_contexts.values())
        memory = self.memory.snapshot()
        finalized = memory.teardown_complete and self.is_drained
        pending: list[str] = []
        if not finalized:
            pending.extend(compute_memory_id(job.job_id, "job_completion") for job in jobs if job.job_id not in completed)
            pending.extend(compute_memory_id(job.job_id, "slot_release") for job in jobs if not self.buffers.is_drained)
            pending.extend(memory.pending)
        return ComputeExecutionResult(
            status="complete" if finalized else "incomplete",
            reason="drained" if finalized else "awaiting_finalization" if self.is_drained else
                   "idle_with_pending" if self.env.peek() == float("inf") else "cycle_limit",
            plan=self.plan.workload.record, execution_plan_sha256=self.plan.plan_sha256,
            elapsed_aci_cycles=self.env.now, completed_job_ids=completed,
            work=ComputeWorkAccounting(
                planned_useful_work=sum(job.cost.useful_work for job in jobs),
                planned_executed_work=sum(job.cost.executed_work for job in jobs),
                completed_useful_work=sum(job.cost.useful_work for job in jobs if job.job_id in math_jobs),
                completed_executed_work=sum(job.cost.executed_work for job in jobs if job.job_id in math_jobs),
                math_busy_aci_cycles=math_busy, context_occupied_aci_cycles=context_time),
            stages=tuple(self._stages), resources=tuple(pool.snapshot() for pool in self._pools.values()),
            resource_events=tuple(self._resource_events), slots=self.buffers.snapshot(), slot_events=self.buffers.events,
            memory_session=memory, pending=tuple(pending), teardown_complete=finalized)
