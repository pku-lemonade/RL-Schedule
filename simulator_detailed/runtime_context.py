"""Unified RuntimeContext: one context executes transfer/compute/wait/signal.

The context owns the SimPy environment and time, one ResourceRegistry, one
EventBus, transaction states, a deterministic trace and metrics, and the
error/incomplete accounting. Mechanics reproduce the phase-1 numeric
semantics exactly; ownership is unified so no feature grows its own runtime.
Deterministic ordering comes from construction order and a global sequence,
never from dict iteration order or object addresses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast

import simpy
from simpy.events import Event, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .configs.schemas.generic_transactions import (
    GenericCounterState,
    GenericErrorRecord,
    GenericHopSpan,
    GenericResourceUsage,
    GenericSimulationResult,
    GenericTraceEvent,
    GenericTransactionSpan,
)
from .configs.schemas.system_spec import (
    ImmutablePlan,
    PlanCompute,
    PlanSignal,
    PlanTransfer,
    PlanWait,
)

TraceAction = Literal[
    "credit_acquire", "credit_release", "serialize_start", "serialize_end",
    "hop_arrive", "unit_acquire", "unit_release", "counter_publish",
    "wait_resume", "cancel",
]


def _release_held(granted: list[tuple[Resource, Request]]) -> None:
    """Release granted requests and cancel pending ones after interruption."""
    for resource, request in granted:
        if request.triggered:
            if request in resource.users:
                resource.release(request)
        else:
            request.cancel()


@dataclass
class _ResourceEntry:
    """One physical resource: stable ID, built once, fully accounted."""

    resource_id: str
    kind: Literal["link", "execution_unit"]
    credits: Resource | None = None
    serializer: Resource | None = None
    service_count: int = 0
    busy_cycles: float = 0.0
    queue_wait_cycles: float = 0.0


class ResourceRegistry:
    """Stable-ID resources; one construction per physical resource per plan."""

    def __init__(self, env: simpy.Environment, plan: ImmutablePlan):
        self._entries: dict[str, _ResourceEntry] = {}
        for resource in plan.content.resources:
            if resource.resource_id in self._entries:
                raise ValueError(f"duplicate physical resource {resource.resource_id}")
            if resource.kind == "link":
                self._entries[resource.resource_id] = _ResourceEntry(
                    resource_id=resource.resource_id,
                    kind="link",
                    credits=Resource(env, capacity=resource.capacity),
                    serializer=Resource(env, capacity=1),
                )
            else:
                self._entries[resource.resource_id] = _ResourceEntry(
                    resource_id=resource.resource_id,
                    kind="execution_unit",
                    serializer=Resource(env, capacity=1),
                )

    def get(self, resource_id: str) -> _ResourceEntry:
        try:
            return self._entries[resource_id]
        except KeyError as exc:
            raise ValueError(f"unknown runtime resource {resource_id}") from exc

    def entries(self) -> tuple[_ResourceEntry, ...]:
        return tuple(self._entries[rid] for rid in sorted(self._entries))

    def all_released(self) -> bool:
        """True when no resource holds or awaits any ownership."""
        for entry in self._entries.values():
            for resource in (entry.credits, entry.serializer):
                if resource is None:
                    continue
                queue = cast(list[object], resource.queue)  # pyright: ignore[reportUnknownMemberType]
                if resource.users or queue:
                    return False
        return True

    def release_all(self, granted: list[tuple[Resource, Request]]) -> None:
        for resource, request in granted:
            if request.triggered and request in resource.users:
                resource.release(request)

    def acquire_all(self, resource_ids: tuple[str, ...]) -> ProcessGenerator:
        """Sorted-order multi-resource acquisition with interrupt safety.

        Callers receive the granted requests in sorted ID order. Because every
        caller acquires in the same total order, circular waits cannot form.
        An interrupted attempt cancels its pending request and releases the
        grants it already holds, so partial ownership never survives.
        """
        granted: list[tuple[Resource, Request]] = []
        try:
            for resource_id in sorted(resource_ids):
                entry = self.get(resource_id)
                resource = entry.serializer
                if resource is None:
                    raise ValueError(f"resource {resource_id} is not acquirable")
                request = resource.request()
                granted.append((resource, request))
                yield request
            return list(granted)
        except simpy.Interrupt:
            _release_held(granted)
            raise


class EventBus:
    """Named events, counted events and wait conditions, deterministically ordered."""

    def __init__(self, env: simpy.Environment, plan: ImmutablePlan):
        self.env = env
        self._counters: dict[str, _CounterRuntime] = {
            counter.counter_id: _CounterRuntime(env, counter.initial_value, counter.upper_bound)
            for counter in plan.content.counters
        }
        self._events: dict[str, Event] = {}

    def counter(self, counter_id: str) -> _CounterRuntime:
        try:
            return self._counters[counter_id]
        except KeyError as exc:
            raise ValueError(f"unknown counter {counter_id}") from exc

    def event(self, name: str) -> Event:
        if name not in self._events:
            self._events[name] = self.env.event()
        return self._events[name]

    def publish(self, name: str) -> None:
        event = self._events.pop(name, None)
        if event is not None and not event.triggered:
            event.succeed()

    def counters(self) -> tuple[GenericCounterState, ...]:
        return tuple(
            GenericCounterState(
                counter_id=counter_id,
                value=counter.value,
                updates=counter.updates,
            )
            for counter_id, counter in sorted(self._counters.items())
        )


class _CounterRuntime:
    def __init__(self, env: simpy.Environment, initial_value: int, upper_bound: int):
        self.env = env
        self.value = initial_value
        self.upper_bound = upper_bound
        self.updates = 0
        self.changed = env.event()

    def add(self, delta: int) -> None:
        self.value += delta
        self.updates += 1
        self.changed.succeed()
        self.changed = self.env.event()


class RuntimeContext:
    """One environment, one registry, one bus, all four transaction kinds."""

    def __init__(self, plan: ImmutablePlan):
        self.plan = ImmutablePlan.model_validate(plan.model_dump(mode="json"))
        self.env = simpy.Environment()
        self.registry = ResourceRegistry(self.env, self.plan)
        self.bus = EventBus(self.env, self.plan)
        self._spans: dict[str, GenericTransactionSpan] = {}
        self._done: dict[str, Event] = {}
        self._trace: list[GenericTraceEvent] = []
        self._sequence = 0
        self._terminal: dict[str, Literal["route_unreachable", "capacity_exceeded"]] = {
            tx.transaction_id: tx.terminal
            for tx in self.plan.content.transactions
            if isinstance(tx, PlanTransfer) and tx.terminal is not None
        }

    def _emit(
        self,
        action: TraceAction,
        transaction_id: str | None = None,
        resource_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._sequence += 1
        self._trace.append(GenericTraceEvent(
            sequence=self._sequence,
            time_cycles=float(self.env.now),
            action=action,
            transaction_id=transaction_id,
            resource_id=resource_id,
            detail=detail,
        ))

    def _start(self, depends_on: tuple[str, ...], start_cycles: float) -> ProcessGenerator:
        if start_cycles > self.env.now:
            yield self.env.timeout(start_cycles - self.env.now)
        for dependency in depends_on:
            yield self._done[dependency]

    def _finish(
        self,
        transaction_id: str,
        kind: Literal["transfer", "compute", "wait", "signal"],
        start: float,
        wait_cycles: float,
        hops: tuple[GenericHopSpan, ...] = (),
    ) -> None:
        self._spans[transaction_id] = GenericTransactionSpan(
            transaction_id=transaction_id,
            kind=kind,
            status="complete",
            reason="completed",
            start_cycles=start,
            end_cycles=float(self.env.now),
            wait_cycles=wait_cycles,
            hops=hops,
        )
        self._done[transaction_id].succeed()

    def _transfer(self, transaction: PlanTransfer) -> ProcessGenerator:
        granted: list[tuple[Resource, Request]] = []
        try:
            yield from self._start(transaction.depends_on, transaction.start_cycles)
            queued = float(self.env.now)
            wait_cycles = 0.0
            hops: list[GenericHopSpan] = []
            for hop in transaction.hops:
                entry = self.registry.get(f"{hop.network_id}/{hop.link_id}")
                if entry.credits is None or entry.serializer is None:
                    raise ValueError("link resource is not acquirable")
                requested = float(self.env.now)
                credit = entry.credits.request()
                granted.append((entry.credits, credit))
                yield credit
                credit_wait = float(self.env.now) - requested
                requested = float(self.env.now)
                grant = entry.serializer.request()
                granted.append((entry.serializer, grant))
                yield grant
                serialize_wait = float(self.env.now) - requested
                entry.queue_wait_cycles += credit_wait + serialize_wait
                wait_cycles += credit_wait + serialize_wait
                self._emit("credit_acquire", transaction.transaction_id, entry.resource_id)
                serialization_start = float(self.env.now)
                serialization_cycles = float(
                    math.ceil(transaction.payload_bytes / hop.timing.bytes_per_cycle)
                )
                self._emit("serialize_start", transaction.transaction_id, entry.resource_id)
                yield self.env.timeout(serialization_cycles)
                serialization_end = float(self.env.now)
                entry.busy_cycles += serialization_end - serialization_start
                entry.service_count += 1
                self._emit("serialize_end", transaction.transaction_id, entry.resource_id)
                entry.serializer.release(grant)
                granted.remove((entry.serializer, grant))
                yield self.env.timeout(hop.timing.hop_cycles)
                arrival = float(self.env.now)
                self._emit("hop_arrive", transaction.transaction_id, entry.resource_id)
                granted.remove((entry.credits, credit))
                self.env.process(self._return_credit(entry, credit, hop.timing.credit_return_cycles))
                hops.append(GenericHopSpan(
                    network_id=hop.network_id,
                    link_id=hop.link_id,
                    queued_cycles=queued,
                    serialization_start_cycles=serialization_start,
                    serialization_end_cycles=serialization_end,
                    arrival_cycles=arrival,
                ))
            self._finish(transaction.transaction_id, "transfer", queued, wait_cycles, tuple(hops))
        except simpy.Interrupt:
            pass
        finally:
            _release_held(granted)

    def _return_credit(
        self, entry: _ResourceEntry, credit: Request, delay: float
    ) -> ProcessGenerator:
        yield self.env.timeout(delay)
        if entry.credits is not None and credit.triggered and credit in entry.credits.users:
            entry.credits.release(credit)
        self._emit("credit_release", resource_id=entry.resource_id)

    def _compute(self, transaction: PlanCompute) -> ProcessGenerator:
        granted: list[tuple[Resource, Request]] = []
        try:
            yield from self._start(transaction.depends_on, transaction.start_cycles)
            entry = self.registry.get(transaction.unit_id)
            if entry.serializer is None:
                raise ValueError("execution unit is not acquirable")
            requested = float(self.env.now)
            grant = entry.serializer.request()
            granted.append((entry.serializer, grant))
            yield grant
            wait_cycles = float(self.env.now) - requested
            entry.queue_wait_cycles += wait_cycles
            self._emit("unit_acquire", transaction.transaction_id, entry.resource_id)
            start = float(self.env.now)
            yield self.env.timeout(transaction.duration_cycles)
            end = float(self.env.now)
            entry.busy_cycles += end - start
            entry.service_count += 1
            entry.serializer.release(grant)
            granted.remove((entry.serializer, grant))
            self._emit("unit_release", transaction.transaction_id, entry.resource_id)
            self._finish(transaction.transaction_id, "compute", start, wait_cycles)
        except simpy.Interrupt:
            pass
        finally:
            _release_held(granted)

    def _wait(self, transaction: PlanWait) -> ProcessGenerator:
        try:
            yield from self._start(transaction.depends_on, transaction.start_cycles)
            start = float(self.env.now)
            counter = self.bus.counter(transaction.counter_id)
            while counter.value < transaction.threshold:
                yield counter.changed
            self._emit("wait_resume", transaction.transaction_id, transaction.counter_id)
            self._finish(
                transaction.transaction_id,
                "wait",
                start,
                float(self.env.now) - start,
            )
        except simpy.Interrupt:
            pass

    def _signal(self, transaction: PlanSignal) -> ProcessGenerator:
        try:
            yield from self._start(transaction.depends_on, transaction.start_cycles)
            start = float(self.env.now)
            self.bus.counter(transaction.counter_id).add(transaction.delta)
            self._emit(
                "counter_publish",
                transaction.transaction_id,
                transaction.counter_id,
                f"+{transaction.delta}",
            )
            self._finish(transaction.transaction_id, "signal", start, 0.0)
        except simpy.Interrupt:
            pass

    def run(self) -> GenericSimulationResult:
        processes: dict[str, simpy.Process] = {}
        for transaction in self.plan.content.transactions:
            self._done[transaction.transaction_id] = self.env.event()
            if transaction.transaction_id in self._terminal:
                continue
            if isinstance(transaction, PlanTransfer):
                processes[transaction.transaction_id] = self.env.process(self._transfer(transaction))
            elif isinstance(transaction, PlanCompute):
                processes[transaction.transaction_id] = self.env.process(self._compute(transaction))
            elif isinstance(transaction, PlanWait):
                processes[transaction.transaction_id] = self.env.process(self._wait(transaction))
            else:
                processes[transaction.transaction_id] = self.env.process(self._signal(transaction))
        pending = tuple(
            event
            for transaction_id, event in self._done.items()
            if transaction_id not in self._terminal
        )
        drained = self.env.all_of(pending)
        self.env.run(
            until=simpy.AnyOf(self.env, (drained, self.env.timeout(self.plan.content.max_cycles)))
        )
        if not drained.triggered:
            for transaction_id, process in processes.items():
                if not process.triggered:
                    self._emit("cancel", transaction_id)
                    process.interrupt()
            self.env.run()

        spans: list[GenericTransactionSpan] = []
        errors: list[GenericErrorRecord] = []
        for transaction in self.plan.content.transactions:
            terminal_reason = self._terminal.get(transaction.transaction_id)
            if terminal_reason is not None and isinstance(transaction, PlanTransfer):
                spans.append(GenericTransactionSpan(
                    transaction_id=transaction.transaction_id,
                    kind=transaction.kind,
                    status="incomplete",
                    reason=terminal_reason,
                    start_cycles=None,
                    end_cycles=None,
                ))
                errors.append(GenericErrorRecord(
                    transaction_id=transaction.transaction_id,
                    code=terminal_reason,
                    message=self._terminal_message(transaction, terminal_reason),
                ))
                continue
            span = self._spans.get(transaction.transaction_id)
            if span is not None:
                spans.append(span)
                continue
            unsatisfied = any(
                dependency in self._terminal or self._spans.get(dependency) is None
                for dependency in transaction.depends_on
            )
            reason: Literal["cycle_limit", "dependency_unsatisfied"] = (
                "dependency_unsatisfied" if unsatisfied else "cycle_limit"
            )
            spans.append(GenericTransactionSpan(
                transaction_id=transaction.transaction_id,
                kind=transaction.kind,
                status="incomplete",
                reason=reason,
                start_cycles=None,
                end_cycles=None,
            ))
            errors.append(GenericErrorRecord(
                transaction_id=transaction.transaction_id,
                code=reason,
                message=self._incomplete_message(transaction, reason),
            ))
        completed = [span.end_cycles for span in spans if span.end_cycles is not None]
        completion = max(completed) if completed else None
        complete = all(span.status == "complete" for span in spans)

        resources = tuple(
            GenericResourceUsage(
                resource_id=entry.resource_id,
                kind=entry.kind,
                service_count=entry.service_count,
                busy_cycles=entry.busy_cycles,
                queue_wait_cycles=entry.queue_wait_cycles,
                utilization=(
                    entry.busy_cycles / completion
                    if completion is not None and completion > 0
                    else 0.0
                ),
            )
            for entry in self.registry.entries()
        )
        return GenericSimulationResult(
            batch_id=self.plan.content.spec_id,
            system_id=self.plan.content.system_id,
            status="complete" if complete else "incomplete",
            reason="drained" if complete else "transactions_incomplete",
            completion_cycles=completion,
            graph_sha256=self.plan.content.graph_sha256,
            batch_sha256=self.plan.content.batch_sha256,
            transactions=tuple(spans),
            resources=resources,
            counters=self.bus.counters(),
            errors=tuple(errors),
            trace=tuple(self._trace),
            plan_sha256=self.plan.plan_sha256,
        )

    def _terminal_message(
        self,
        transaction: PlanTransfer,
        reason: Literal["route_unreachable", "capacity_exceeded"],
    ) -> str:
        if reason == "route_unreachable":
            return (
                f"no static route in network {transaction.network_id} from "
                f"{transaction.source} to {transaction.destination}"
            )
        return (
            f"payload {transaction.payload_bytes} bytes exceeds the capacity of "
            f"{transaction.destination}"
        )

    def _incomplete_message(
        self,
        transaction: PlanTransfer | PlanCompute | PlanWait | PlanSignal,
        reason: Literal["cycle_limit", "dependency_unsatisfied"],
    ) -> str:
        if reason == "dependency_unsatisfied":
            return "one or more declared dependencies did not complete"
        if isinstance(transaction, PlanWait) and transaction.threshold > transaction.reachable:
            return (
                f"wait threshold {transaction.threshold} exceeds the maximum reachable "
                f"counter value {transaction.reachable}; it can never be satisfied"
            )
        return f"transaction did not finish within {self.plan.content.max_cycles} cycles"
