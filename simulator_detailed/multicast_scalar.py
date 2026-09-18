"""Finite monotonic counter and local-threshold execution records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .configs.schemas.multicast_sync import ScalarIncrement, ScalarWait
from .configs.schemas.topology import GraphRecord, Identifier, Index
from .multicast_plan import MulticastSyncPlan

ScalarAction = Literal["atomic_submit", "atomic_linearize", "atomic_effect", "atomic_return", "wait_observe", "wait_complete"]
IncrementStatus = Literal["complete", "incomplete", "rejected"]
IncrementReason = Literal["drained", "cycle_limit", "overflow", "invalid"]
WaitStatus = Literal["complete", "incomplete", "rejected"]
WaitReason = Literal["threshold_reached", "threshold_unreachable", "cycle_limit", "data_not_ready", "invalid"]


class ScalarCounterState(GraphRecord):
    counter_id: Identifier
    endpoint_id: Identifier
    buffer_id: Identifier
    offset_bytes: Index
    width_bytes: Literal[1, 2, 4, 8]
    value: Index
    version: Index


class ScalarEvent(GraphRecord):
    sequence: Index
    time_aci_cycles: float
    action: ScalarAction
    operation_id: Identifier | None
    wait_id: Identifier | None
    counter_id: Identifier
    old_value: Index | None
    new_value: Index | None
    physical_bytes: Index


class ScalarIncrementRecord(GraphRecord):
    operation_id: Identifier
    counter_id: Identifier
    completion: Literal["atomic_posted", "atomic_returning"]
    status: IncrementStatus
    old_value: Index
    new_value: Index
    request_physical_bytes: Index
    response_physical_bytes: Index
    service_bytes: Index
    linearization_aci_cycles: float
    completion_aci_cycles: float
    reason: IncrementReason


class ScalarWaitRecord(GraphRecord):
    wait_id: Identifier
    counter_id: Identifier
    threshold: Index
    status: WaitStatus
    observed_value: Index
    observation_count: Index
    data_ready: bool
    completion_aci_cycles: float | None
    reason: WaitReason


class ScalarSnapshot(GraphRecord):
    next_operation_index: Index
    next_wait_index: Index
    elapsed_aci_cycles: float
    counters: tuple[ScalarCounterState, ...]
    completed_operations: tuple[Identifier, ...]
    completed_waits: tuple[Identifier, ...]
    pending_waits: tuple[Identifier, ...]


class ScalarExecutionResult(GraphRecord):
    kind: Literal["multicast_scalar_result"] = "multicast_scalar_result"
    schema_version: Literal[1] = 1
    status: Literal["complete", "incomplete"]
    elapsed_aci_cycles: float
    counters: tuple[ScalarCounterState, ...]
    increments: tuple[ScalarIncrementRecord, ...]
    waits: tuple[ScalarWaitRecord, ...]
    events: tuple[ScalarEvent, ...]
    snapshot: ScalarSnapshot


@dataclass(frozen=True)
class ScalarExecutor:
    plan: MulticastSyncPlan

    @classmethod
    def compile(cls, plan: MulticastSyncPlan) -> ScalarExecutor:
        cls._validate(plan)
        return cls(plan=plan)

    @staticmethod
    def _validate(plan: MulticastSyncPlan) -> None:
        workload = plan.workload
        counters = {counter.counter_id: counter for counter in workload.counters}
        ranges: dict[str, tuple[int, int]] = {}
        for counter in workload.counters:
            maximum = (1 << (8 * counter.width_bytes)) - 1
            if counter.initial_value > maximum:
                raise ValueError(f"counter {counter.counter_id}: initial value overflows width")
            extent = (counter.offset_bytes, counter.offset_bytes + counter.width_bytes)
            for other_id, other_extent in ranges.items():
                if extent[0] < other_extent[1] and other_extent[0] < extent[1]:
                    raise ValueError(f"counter {counter.counter_id}: overlaps counter {other_id}")
            ranges[counter.counter_id] = extent
        for increment in workload.increments:
            if increment.counter_id not in counters:
                raise ValueError(f"atomic {increment.operation_id}: unknown counter")
        operation_ids = {
            *(increment.operation_id for increment in workload.increments),
            *(write.operation_id for write in workload.writes),
        }
        for wait in workload.waits:
            if wait.threshold < counters[wait.counter_id].initial_value:
                raise ValueError(f"wait {wait.wait_id}: threshold is below the initial value")
            if any(operation not in operation_ids for operation in wait.producer_operations + wait.data_ready_after):
                raise ValueError(f"wait {wait.wait_id}: unknown producer")

    def run(self, *, cycle_limit: float | None = None, snapshot: ScalarSnapshot | None = None) -> ScalarExecutionResult:
        if cycle_limit is not None and cycle_limit < 0:
            raise ValueError("cycle limit must be non-negative")
        workload = self.plan.workload
        self._validate(self.plan)
        counters = {
            state.counter_id: state
            for state in snapshot.counters
        } if snapshot is not None else {
            counter.counter_id: ScalarCounterState(
                counter_id=counter.counter_id,
                endpoint_id=counter.endpoint_id,
                buffer_id=counter.buffer_id,
                offset_bytes=counter.offset_bytes,
                width_bytes=counter.width_bytes,
                value=counter.initial_value,
                version=0,
            )
            for counter in workload.counters
        }
        completed_operations: set[str] = set(snapshot.completed_operations) if snapshot else set()
        completed_waits: set[str] = set(snapshot.completed_waits) if snapshot else set()
        pending_waits = set(snapshot.pending_waits) if snapshot else {
            wait.wait_id for wait in workload.waits
        }
        elapsed = snapshot.elapsed_aci_cycles if snapshot else 0.0
        events: list[ScalarEvent] = []
        increment_records: list[ScalarIncrementRecord] = []
        wait_records: list[ScalarWaitRecord] = []
        for increment in workload.increments:
            if increment.operation_id in completed_operations:
                continue
            state = counters[increment.counter_id]
            maximum = (1 << (8 * state.width_bytes)) - 1
            if state.value >= maximum:
                increment_records.append(
                    self._increment_record(
                        increment, state, "rejected", state.value, state.value,
                        0, 0, 0, 0, "overflow"
                    )
                )
                continue
            request_bytes = workload.memory.packet.physical_flit_bytes
            response_bytes = request_bytes if increment.completion == "atomic_returning" else 0
            events.append(self._event(len(events), elapsed, "atomic_submit", increment.operation_id, None,
                                      state, state.value, None, request_bytes))
            elapsed += workload.control.atomic_native_cycles
            old = state.value
            new = old + 1
            counters[state.counter_id] = state.model_copy(update={"value": new, "version": state.version + 1})
            events.append(self._event(len(events), elapsed, "atomic_linearize", increment.operation_id, None,
                                      state, old, new, 0))
            events.append(self._event(len(events), elapsed, "atomic_effect", increment.operation_id, None,
                                      state, old, new, 0))
            if response_bytes:
                elapsed += workload.control.local_observation_aci_cycles
                events.append(self._event(len(events), elapsed, "atomic_return", increment.operation_id, None,
                                          state, old, new, response_bytes))
            completed_operations.add(increment.operation_id)
            increment_records.append(self._increment_record(
                increment, state, "complete", old, new, request_bytes, response_bytes,
                workload.control.atomic_native_cycles, elapsed, "drained"
            ))
            if cycle_limit is not None and elapsed >= cycle_limit:
                break

        for wait in workload.waits:
            if wait.wait_id in completed_waits:
                continue
            state = counters[wait.counter_id]
            observations = 1
            events.append(self._event(len(events), elapsed, "wait_observe", None, wait, state, state.value, None, 0))
            threshold_reached = state.value >= wait.threshold
            producer_ready = all(operation in completed_operations for operation in wait.producer_operations)
            data_ready = all(operation in completed_operations for operation in wait.data_ready_after)
            if threshold_reached and producer_ready and data_ready:
                elapsed += workload.control.local_observation_aci_cycles
                events.append(self._event(len(events), elapsed, "wait_complete", None, wait, state, state.value, state.value, 0))
                completed_waits.add(wait.wait_id)
                pending_waits.discard(wait.wait_id)
                wait_records.append(ScalarWaitRecord(
                    wait_id=wait.wait_id, counter_id=wait.counter_id, threshold=wait.threshold,
                    status="complete", observed_value=state.value, observation_count=observations,
                    data_ready=True, completion_aci_cycles=elapsed, reason="threshold_reached",
                ))
            elif not any(item.counter_id == wait.counter_id for item in workload.counters):
                wait_records.append(self._wait_record(wait, state, observations, "rejected", False, None, "invalid"))
            else:
                max_possible = state.value + sum(
                    increment.operation_id not in completed_operations
                    for increment in workload.increments if increment.counter_id == wait.counter_id
                )
                reason = "data_not_ready" if threshold_reached and not data_ready else (
                    "threshold_unreachable" if max_possible < wait.threshold else "cycle_limit"
                )
                wait_records.append(self._wait_record(wait, state, observations, "incomplete", data_ready, None, reason))

        complete = len(completed_operations) == len(workload.increments) and not pending_waits - completed_waits
        pending = tuple(sorted(pending_waits - completed_waits))
        return ScalarExecutionResult(
            status="complete" if complete else "incomplete",
            elapsed_aci_cycles=elapsed,
            counters=tuple(counters.values()),
            increments=tuple(increment_records),
            waits=tuple(wait_records),
            events=tuple(events),
            snapshot=ScalarSnapshot(
                next_operation_index=len(completed_operations),
                next_wait_index=len(completed_waits),
                elapsed_aci_cycles=elapsed,
                counters=tuple(counters.values()),
                completed_operations=tuple(sorted(completed_operations)),
                completed_waits=tuple(sorted(completed_waits)),
                pending_waits=pending,
            ),
        )

    def resume(self, result: ScalarExecutionResult) -> ScalarExecutionResult:
        return self.run(snapshot=result.snapshot)

    @staticmethod
    def _event(sequence: int, elapsed: float, action: ScalarAction, operation_id: str | None,
               wait: ScalarWait | None, state: ScalarCounterState, old: int | None,
               new: int | None, physical: int) -> ScalarEvent:
        return ScalarEvent(
            sequence=sequence, time_aci_cycles=elapsed, action=action,
            operation_id=operation_id, wait_id=wait.wait_id if wait else None,
            counter_id=state.counter_id, old_value=old, new_value=new, physical_bytes=physical,
        )

    @staticmethod
    def _increment_record(increment: ScalarIncrement, state: ScalarCounterState, status: IncrementStatus, old: int, new: int,
                          request: int, response: int, service: float, completion: float = 0,
                          reason: IncrementReason = "drained") -> ScalarIncrementRecord:
        return ScalarIncrementRecord(
            operation_id=increment.operation_id, counter_id=increment.counter_id,
            completion=increment.completion, status=status, old_value=old, new_value=new,
            request_physical_bytes=request, response_physical_bytes=response,
            service_bytes=state.width_bytes, linearization_aci_cycles=completion - service,
            completion_aci_cycles=completion, reason=reason,
        )

    @staticmethod
    def _wait_record(wait: ScalarWait, state: ScalarCounterState, observations: int, status: WaitStatus,
                     data_ready: bool, completion: float | None, reason: WaitReason) -> ScalarWaitRecord:
        return ScalarWaitRecord(
            wait_id=wait.wait_id, counter_id=wait.counter_id, threshold=wait.threshold,
            status=status, observed_value=state.value, observation_count=observations,
            data_ready=data_ready, completion_aci_cycles=completion, reason=reason,
        )
