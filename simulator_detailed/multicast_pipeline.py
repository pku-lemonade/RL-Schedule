"""Finite distribute/compute/collect composition over one admitted session."""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import Field, model_validator

from .configs.schemas.multicast_sync import MulticastSyncWorkload
from .configs.schemas.topology import (
    CanonicalTopology,
    GraphRecord,
    Identifier,
    PositiveInt,
    unique,
)
from .multicast_memory import (
    MulticastBufferState,
    MulticastMemoryExecutor,
    MulticastMemoryResult,
)
from .multicast_plan import MulticastSyncPlan
from .multicast_scalar import ScalarExecutionResult, ScalarExecutor


class PipelineStage(GraphRecord):
    stage_id: Identifier
    endpoint_id: Identifier
    input_buffer_ids: tuple[Identifier, ...] = Field(min_length=1)
    output_buffer_id: Identifier
    wait_ids: tuple[Identifier, ...] = Field(min_length=1)
    service_aci_cycles: PositiveInt
    slot_generation: PositiveInt

    @model_validator(mode="after")
    def distinct_buffers(self):
        unique(self.input_buffer_ids, "pipeline input buffer")
        if self.output_buffer_id in self.input_buffer_ids:
            raise ValueError("pipeline output cannot alias an input slot")
        unique(self.wait_ids, "pipeline wait")
        return self


class PipelineRound(GraphRecord):
    round_id: Identifier
    distribute_operation_ids: tuple[Identifier, ...] = Field(min_length=1)
    stage_ids: tuple[Identifier, ...] = Field(min_length=1)
    wait_ids: tuple[Identifier, ...] = Field(min_length=1)
    threshold: PositiveInt

    @model_validator(mode="after")
    def unique_members(self):
        unique(self.distribute_operation_ids, "round distribution operation")
        unique(self.stage_ids, "round stage")
        unique(self.wait_ids, "round wait")
        return self


class FinitePipelineWorkload(GraphRecord):
    kind: Literal["multicast_pipeline_workload"]
    schema_version: Literal[1]
    multicast: MulticastSyncWorkload
    stages: tuple[PipelineStage, ...] = Field(min_length=1)
    rounds: tuple[PipelineRound, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identity(self):
        unique(tuple(stage.stage_id for stage in self.stages), "pipeline stage")
        unique(tuple(round_.round_id for round_ in self.rounds), "pipeline round")
        thresholds = tuple(round_.threshold for round_ in self.rounds)
        if thresholds != tuple(sorted(thresholds)) or len(set(thresholds)) != len(thresholds):
            raise ValueError("pipeline round thresholds must be increasing")
        return self


class PipelineStageRecord(GraphRecord):
    stage_id: Identifier
    endpoint_id: Identifier
    status: Literal["complete", "incomplete"]
    input_buffer_ids: tuple[Identifier, ...]
    output_buffer_id: Identifier
    slot_generation: PositiveInt
    start_aci_cycles: float | None
    completion_aci_cycles: float | None


class PipelineEvent(GraphRecord):
    sequence: int
    time_aci_cycles: float
    action: Literal["stage_wait", "stage_start", "stage_complete"]
    stage_id: Identifier
    round_id: Identifier


class FinitePipelineResult(GraphRecord):
    kind: Literal["multicast_pipeline_result"] = "multicast_pipeline_result"
    schema_version: Literal[1] = 1
    status: Literal["complete", "incomplete"]
    elapsed_aci_cycles: float
    memory: tuple[MulticastMemoryResult, ...]
    scalar: ScalarExecutionResult
    stages: tuple[PipelineStageRecord, ...]
    events: tuple[PipelineEvent, ...]
    buffers: tuple[MulticastBufferState, ...]


@dataclass(frozen=True)
class FinitePipelinePlan:
    workload: FinitePipelineWorkload
    multicast: MulticastSyncPlan
    stage_by_id: dict[str, PipelineStage]
    plan_sha256: str

    @classmethod
    def compile(cls, workload: FinitePipelineWorkload, topology: CanonicalTopology) -> FinitePipelinePlan:
        from .topology import content_digest

        workload = FinitePipelineWorkload.model_validate(workload.model_dump(mode="json"))
        multicast = MulticastSyncPlan.compile(workload.multicast, CanonicalTopology.model_validate(topology.model_dump(mode="json")))
        endpoint_ids = {endpoint.endpoint_id for endpoint in multicast.workload.memory.endpoints}
        buffer_ids = {buffer.buffer_id for buffer in multicast.workload.memory.buffers}
        wait_ids = {wait.wait_id for wait in multicast.workload.waits}
        operation_ids = {operation for operation in multicast.record.operation_order}
        for stage in workload.stages:
            if stage.endpoint_id not in endpoint_ids:
                raise ValueError(f"stage {stage.stage_id}: endpoint is unknown")
            if any(buffer not in buffer_ids for buffer in (*stage.input_buffer_ids, stage.output_buffer_id)):
                raise ValueError(f"stage {stage.stage_id}: buffer is unknown")
            if any(wait not in wait_ids for wait in stage.wait_ids):
                raise ValueError(f"stage {stage.stage_id}: wait is unknown")
        stage_ids = {stage.stage_id for stage in workload.stages}
        used_stages: set[str] = set()
        used_operations: set[str] = set()
        used_waits: set[str] = set()
        edges: dict[str, set[str]] = {node: set() for node in (*operation_ids, *wait_ids, *stage_ids)}
        for write in multicast.workload.writes:
            edges[write.operation_id].update(write.depends_on)
        for increment in multicast.workload.increments:
            edges[increment.operation_id].update(increment.depends_on)
        for wait in multicast.workload.waits:
            edges[wait.wait_id].update(wait.producer_operations + wait.data_ready_after)
        for round_ in workload.rounds:
            if any(operation not in operation_ids for operation in round_.distribute_operation_ids):
                raise ValueError(f"round {round_.round_id}: unknown distribution operation")
            if any(stage not in stage_ids for stage in round_.stage_ids):
                raise ValueError(f"round {round_.round_id}: unknown stage")
            if any(wait not in wait_ids for wait in round_.wait_ids):
                raise ValueError(f"round {round_.round_id}: unknown wait")
            if used_stages & set(round_.stage_ids):
                raise ValueError(f"round {round_.round_id}: stage is reused without a new slot generation")
            used_stages.update(round_.stage_ids)
            used_operations.update(round_.distribute_operation_ids)
            used_waits.update(round_.wait_ids)
            for stage_id in round_.stage_ids:
                edges[stage_id].update(round_.wait_ids + round_.distribute_operation_ids)
        write_operation_ids = {write.operation_id for write in multicast.workload.writes}
        if used_operations != write_operation_ids or used_stages != stage_ids or used_waits != wait_ids:
            raise ValueError("pipeline rounds must account for every operation, stage and wait")
        try:
            tuple(TopologicalSorter(edges).static_order())
        except CycleError as error:
            raise ValueError("pipeline dependency graph contains a cycle") from error
        digest = content_digest({
            "multicast": multicast.record.plan_sha256,
            "stages": [stage.model_dump(mode="json") for stage in workload.stages],
            "rounds": [round_.model_dump(mode="json") for round_ in workload.rounds],
        })
        return cls(workload, multicast, {stage.stage_id: stage for stage in workload.stages}, digest)


@dataclass(frozen=True)
class FinitePipelineExecutor:
    plan: FinitePipelinePlan

    def run(self) -> FinitePipelineResult:
        memory_executor = MulticastMemoryExecutor.compile(self.plan.multicast)
        memory_results: list[MulticastMemoryResult] = []
        memory_result = memory_executor.run()
        memory_results.append(memory_result)
        while memory_result.status == "complete" and memory_result.snapshot.pending_operation_ids:
            previous_completed = memory_result.snapshot.completed_operation_ids
            memory_result = memory_executor.resume(memory_result)
            memory_results.append(memory_result)
            if memory_result.snapshot.completed_operation_ids == previous_completed:
                break
        external_completed = tuple(
            operation.operation_id
            for result in memory_results
            for operation in result.operations
            if operation.status == "complete"
        )
        scalar = ScalarExecutor.compile(self.plan.multicast).run(external_completed=external_completed)
        buffers = {buffer.buffer_id: buffer for buffer in memory_results[-1].buffers}
        elapsed = max(memory_results[-1].elapsed_aci_cycles, scalar.elapsed_aci_cycles)
        stages: list[PipelineStageRecord] = []
        events: list[PipelineEvent] = []
        completed_waits = {wait.wait_id for wait in scalar.waits if wait.status == "complete"}
        for round_ in self.plan.workload.rounds:
            for stage_id in round_.stage_ids:
                stage = self.plan.stage_by_id[stage_id]
                events.append(PipelineEvent(sequence=len(events), time_aci_cycles=elapsed,
                                            action="stage_wait", stage_id=stage_id, round_id=round_.round_id))
                if not set(stage.wait_ids) <= completed_waits:
                    stages.append(PipelineStageRecord(stage_id=stage_id, endpoint_id=stage.endpoint_id,
                                                      status="incomplete", input_buffer_ids=stage.input_buffer_ids,
                                                      output_buffer_id=stage.output_buffer_id,
                                                      slot_generation=stage.slot_generation,
                                                      start_aci_cycles=None, completion_aci_cycles=None))
                    continue
                if not all(buffers[buffer_id].ready for buffer_id in stage.input_buffer_ids):
                    stages.append(PipelineStageRecord(stage_id=stage_id, endpoint_id=stage.endpoint_id,
                                                      status="incomplete", input_buffer_ids=stage.input_buffer_ids,
                                                      output_buffer_id=stage.output_buffer_id,
                                                      slot_generation=stage.slot_generation,
                                                      start_aci_cycles=None, completion_aci_cycles=None))
                    continue
                start = elapsed
                events.append(PipelineEvent(sequence=len(events), time_aci_cycles=start,
                                            action="stage_start", stage_id=stage_id, round_id=round_.round_id))
                elapsed += stage.service_aci_cycles
                buffers[stage.output_buffer_id] = buffers[stage.output_buffer_id].model_copy(update={
                    "ready": True,
                    "version": buffers[stage.output_buffer_id].version + 1,
                    "producer_operation_id": stage.stage_id,
                })
                events.append(PipelineEvent(sequence=len(events), time_aci_cycles=elapsed,
                                            action="stage_complete", stage_id=stage_id, round_id=round_.round_id))
                stages.append(PipelineStageRecord(stage_id=stage_id, endpoint_id=stage.endpoint_id,
                                                  status="complete", input_buffer_ids=stage.input_buffer_ids,
                                                  output_buffer_id=stage.output_buffer_id,
                                                  slot_generation=stage.slot_generation,
                                                  start_aci_cycles=start, completion_aci_cycles=elapsed))
        status = "complete" if (memory_results[-1].status == "complete"
                                and not memory_results[-1].snapshot.pending_operation_ids
                                and scalar.status == "complete"
                                and all(stage.status == "complete" for stage in stages)) else "incomplete"
        return FinitePipelineResult(status=status, elapsed_aci_cycles=elapsed, memory=tuple(memory_results),
                                    scalar=scalar, stages=tuple(stages), events=tuple(events),
                                    buffers=tuple(buffers.values()))
