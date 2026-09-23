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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

import simpy
from simpy.events import Event, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .configs.schemas.generic_transactions import (
    GenericCounterState,
    GenericErrorRecord,
    GenericHopSpan,
    GenericLinkTiming,
    GenericMemoryServiceSpan,
    GenericResourceUsage,
    GenericSimulationResult,
    GenericTraceEvent,
    GenericTransactionSpan,
)
from .configs.schemas.system_spec import (
    ImmutablePlan,
    PlanCompute,
    PlanMemoryService,
    PlanSignal,
    PlanTransfer,
    PlanWait,
)

TraceAction = Literal[
    "credit_acquire", "credit_release", "serialize_start", "serialize_end",
    "hop_arrive", "unit_acquire", "unit_release", "counter_publish",
    "wait_resume", "cancel", "memory_command", "memory_service_start",
    "memory_service_end", "route_select",
]


@dataclass
class _ResourceEntry:
    """One physical resource: stable ID, built once, fully accounted."""

    resource_id: str
    kind: Literal[
        "link", "execution_unit", "memory_bank", "memory_port", "memory_channel"
    ]
    credits: Resource | None = None
    serializer: Resource | None = None
    service_count: int = 0
    busy_cycles: float = 0.0
    queue_wait_cycles: float = 0.0
    waiting: int = 0


def _immediately_available(resource: Resource) -> bool:
    """Free capacity and an empty queue, so a grant lands this time step."""
    queue = cast(list[object], resource.queue)  # pyright: ignore[reportUnknownMemberType]
    return len(resource.users) < resource.capacity and not queue


@dataclass(frozen=True)
class _DynamicRuntime:
    """Compiled routing view of one dynamic network."""

    routing: Literal["shortest_path", "adaptive"]
    adjacency: Mapping[str, tuple[tuple[str, str, GenericLinkTiming], ...]]
    distances: Mapping[str, Mapping[str, int]]


class ResourceRegistry:
    """Stable-ID resources; one construction per physical resource per plan."""

    def __init__(self, env: simpy.Environment, plan: ImmutablePlan):
        self.env = env
        self._released = env.event()
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
                    kind=resource.kind,
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

    def _notify_release(self) -> None:
        """Wake every atomic-acquisition waiter; events are single-shot."""
        self._released.succeed()
        self._released = self.env.event()

    def release(self, resource: Resource, request: Request) -> None:
        """Release one granted request on the normal path; notify waiters."""
        if request.triggered and request in resource.users:
            resource.release(request)
            self._notify_release()

    def release_all(self, granted: list[tuple[Resource, Request]]) -> None:
        """Release granted requests and cancel pending ones; notify waiters."""
        if not granted:
            return
        for resource, request in granted:
            if request.triggered:
                if request in resource.users:
                    resource.release(request)
            else:
                request.cancel()
        self._notify_release()

    def acquire_all(self, resource_ids: tuple[str, ...]) -> ProcessGenerator:
        """Atomic all-or-nothing acquisition with upfront validation.

        The complete identity set is validated before any request exists:
        empty, duplicate or unknown identities raise immediately, so an
        invalid set can never strand earlier grants. Grants then land at
        one time step or not at all — a waiter holds no member of the set
        while waiting, so partial ownership can never block a
        single-resource acquirer. An interrupted attempt releases or
        cancels everything it created. Wait time is attributed to every
        member entry, matching the per-entry accounting of `_acquire`.
        """
        if not resource_ids:
            raise ValueError("resource set is empty")
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError(
                f"duplicate resource identity in {sorted(resource_ids)}"
            )
        entries = [self.get(resource_id) for resource_id in sorted(resource_ids)]
        resources: list[Resource] = []
        for entry in entries:
            if entry.serializer is None:
                raise ValueError(f"resource {entry.resource_id} is not acquirable")
            resources.append(entry.serializer)
        granted: list[tuple[Resource, Request]] = []
        requested = float(self.env.now)
        try:
            while True:
                if all(_immediately_available(resource) for resource in resources):
                    requests = [resource.request() for resource in resources]
                    granted.extend(zip(resources, requests))
                    yield self.env.all_of(tuple(requests))
                    wait = float(self.env.now) - requested
                    for entry in entries:
                        entry.queue_wait_cycles += wait
                    return list(granted)
                yield self._released
        except simpy.Interrupt:
            self.release_all(granted)
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
        self._background: list[simpy.Process] = []
        self._terminal: dict[str, Literal["route_unreachable", "capacity_exceeded"]] = {
            tx.transaction_id: tx.terminal
            for tx in self.plan.content.transactions
            if isinstance(tx, PlanTransfer) and tx.terminal is not None
        }
        self._dynamic: dict[str, _DynamicRuntime] = {}
        for network in self.plan.content.dynamic_networks:
            adjacency: dict[str, list[tuple[str, str, GenericLinkTiming]]] = {}
            for link in network.links:
                adjacency.setdefault(link.src_node, []).append(
                    (link.link_id, link.dst_node, link.timing)
                )
            self._dynamic[network.network_id] = _DynamicRuntime(
                routing=network.routing,
                adjacency={
                    node: tuple(out_links) for node, out_links in adjacency.items()
                },
                distances={
                    table.destination_node: {
                        entry.node: entry.distance for entry in table.distances
                    }
                    for table in network.distances
                },
            )

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
        service: GenericMemoryServiceSpan | None = None,
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
            service=service,
        )
        self._done[transaction_id].succeed()

    def _acquire(
        self,
        entry: _ResourceEntry,
        transaction_id: str,
        granted: list[tuple[Resource, Request]],
    ) -> ProcessGenerator:
        """Grant one serializer with queue accounting; (resource, request, wait).

        The request is registered in `granted` at creation, so an interrupt
        while waiting still cancels the queued request.
        """
        resource = entry.serializer
        if resource is None:
            raise ValueError(f"resource {entry.resource_id} is not acquirable")
        requested = float(self.env.now)
        grant = resource.request()
        granted.append((resource, grant))
        yield grant
        wait = float(self.env.now) - requested
        entry.queue_wait_cycles += wait
        return resource, grant, wait

    def _memory_service(
        self, transaction: PlanTransfer, service: PlanMemoryService
    ) -> ProcessGenerator:
        """Command issue on the mapped port, then bank+channel data service.

        Interrupts propagate to the transaction layer: a cancelled or
        timed-out access never starts a later stage and never reports
        completion. Bank and channel are acquired atomically through the
        registry, and every request is released or cancelled on every exit.
        """
        granted: list[tuple[Resource, Request]] = []
        try:
            port_resource, port_grant, port_wait = yield from self._acquire(
                self.registry.get(service.port_id), transaction.transaction_id, granted
            )
            self._emit("memory_command", transaction.transaction_id, service.port_id)
            command_start = float(self.env.now)
            yield self.env.timeout(service.command_cycles)
            command_end = float(self.env.now)
            port = self.registry.get(service.port_id)
            port.busy_cycles += command_end - command_start
            port.service_count += 1
            granted.remove((port_resource, port_grant))
            self.registry.release(port_resource, port_grant)

            data_ids = tuple(sorted((service.bank_id, service.channel_id)))
            data_requested = float(self.env.now)
            data_granted = yield from self.registry.acquire_all(data_ids)
            granted.extend(data_granted)
            data_wait = float(self.env.now) - data_requested
            self._emit("memory_service_start", transaction.transaction_id, service.bank_id)
            service_start = float(self.env.now)
            data_cycles = service.latency_cycles + float(
                math.ceil(transaction.payload_bytes / service.channel_bytes_per_cycle)
            )
            yield self.env.timeout(data_cycles)
            service_end = float(self.env.now)
            for resource_id in data_ids:
                entry = self.registry.get(resource_id)
                entry.busy_cycles += service_end - service_start
                entry.service_count += 1
            for resource, request in data_granted:
                granted.remove((resource, request))
                self.registry.release(resource, request)
            self._emit("memory_service_end", transaction.transaction_id, service.bank_id)
            return (
                GenericMemoryServiceSpan(
                    resource_id=service.resource_id,
                    direction=service.direction,
                    bank_id=service.bank_id,
                    port_id=service.port_id,
                    channel_id=service.channel_id,
                    command_start_cycles=command_start,
                    command_end_cycles=command_end,
                    service_start_cycles=service_start,
                    service_end_cycles=service_end,
                ),
                port_wait + data_wait,
            )
        finally:
            self.registry.release_all(granted)

    def _pressure(self, network_id: str, link_id: str) -> int:
        """Granted credit users plus pending requests on one link."""
        entry = self.registry.get(f"{network_id}/{link_id}")
        users = len(entry.credits.users) if entry.credits is not None else 0
        return users + entry.waiting

    def _cross_link(
        self,
        transaction: PlanTransfer,
        network_id: str,
        link_id: str,
        timing: GenericLinkTiming,
        granted: list[tuple[Resource, Request]],
    ) -> ProcessGenerator:
        """Cross one link with credit, serialization and hop accounting."""
        entry = self.registry.get(f"{network_id}/{link_id}")
        if entry.credits is None or entry.serializer is None:
            raise ValueError("link resource is not acquirable")
        requested = float(self.env.now)
        credit = entry.credits.request()
        granted.append((entry.credits, credit))
        entry.waiting += 1
        try:
            yield credit
        finally:
            entry.waiting -= 1
        credit_wait = float(self.env.now) - requested
        hop_queued = float(self.env.now)
        requested = float(self.env.now)
        grant = entry.serializer.request()
        granted.append((entry.serializer, grant))
        entry.waiting += 1
        try:
            yield grant
        finally:
            entry.waiting -= 1
        serialize_wait = float(self.env.now) - requested
        entry.queue_wait_cycles += credit_wait + serialize_wait
        self._emit("credit_acquire", transaction.transaction_id, entry.resource_id)
        serialization_start = float(self.env.now)
        serialization_cycles = float(
            math.ceil(transaction.payload_bytes / timing.bytes_per_cycle)
        )
        self._emit("serialize_start", transaction.transaction_id, entry.resource_id)
        yield self.env.timeout(serialization_cycles)
        serialization_end = float(self.env.now)
        entry.busy_cycles += serialization_end - serialization_start
        entry.service_count += 1
        self._emit("serialize_end", transaction.transaction_id, entry.resource_id)
        granted.remove((entry.serializer, grant))
        self.registry.release(entry.serializer, grant)
        yield self.env.timeout(timing.hop_cycles)
        arrival = float(self.env.now)
        self._emit("hop_arrive", transaction.transaction_id, entry.resource_id)
        granted.remove((entry.credits, credit))
        self._background.append(
            self.env.process(self._return_credit(entry, credit, timing.credit_return_cycles))
        )
        return (
            GenericHopSpan(
                network_id=network_id,
                link_id=link_id,
                queued_cycles=hop_queued,
                serialization_start_cycles=serialization_start,
                serialization_end_cycles=serialization_end,
                arrival_cycles=arrival,
            ),
            credit_wait + serialize_wait,
        )

    def _select_next_link(
        self, transaction: PlanTransfer, current: str
    ) -> tuple[str, str, GenericLinkTiming]:
        """Distance-reducing next hop; deterministic for both policies."""
        table = self._dynamic[transaction.network_id]
        destination = transaction.destination_node
        if destination is None:
            raise ValueError("dynamic transfer lacks a destination node")
        distances = table.distances[destination]
        here = distances[current]
        candidates = [
            (link_id, dst, timing)
            for (link_id, dst, timing) in table.adjacency.get(current, ())
            if distances.get(dst) == here - 1
        ]
        if not candidates:
            raise ValueError(
                f"no distance-reducing link at node {current} toward {destination}"
            )
        if table.routing == "shortest_path":
            link_id, dst, timing = min(candidates, key=lambda candidate: candidate[0])
        else:
            link_id, dst, timing = min(
                candidates,
                key=lambda candidate: (
                    self._pressure(transaction.network_id, candidate[0]),
                    candidate[0],
                ),
            )
        self._emit(
            "route_select",
            transaction.transaction_id,
            f"{transaction.network_id}/{link_id}",
            current,
        )
        return link_id, dst, timing

    def _transfer(self, transaction: PlanTransfer) -> ProcessGenerator:
        granted: list[tuple[Resource, Request]] = []
        try:
            yield from self._start(transaction.depends_on, transaction.start_cycles)
            start = float(self.env.now)
            wait_cycles = 0.0
            hops: list[GenericHopSpan] = []
            service_span: GenericMemoryServiceSpan | None = None
            if transaction.memory_service is not None and (
                transaction.memory_service.direction == "read"
            ):
                service_span, service_wait = yield from self._memory_service(
                    transaction, transaction.memory_service
                )
                wait_cycles += service_wait
            if transaction.routing == "static":
                for hop in transaction.hops:
                    hop_span, hop_wait = yield from self._cross_link(
                        transaction, hop.network_id, hop.link_id, hop.timing, granted
                    )
                    hops.append(hop_span)
                    wait_cycles += hop_wait
            else:
                current = transaction.source_node
                while current is not None and current != transaction.destination_node:
                    link_id, dst_node, timing = self._select_next_link(transaction, current)
                    hop_span, hop_wait = yield from self._cross_link(
                        transaction, transaction.network_id, link_id, timing, granted
                    )
                    hops.append(hop_span)
                    wait_cycles += hop_wait
                    current = dst_node
            if transaction.memory_service is not None and (
                transaction.memory_service.direction == "write"
            ):
                service_span, service_wait = yield from self._memory_service(
                    transaction, transaction.memory_service
                )
                wait_cycles += service_wait
            self._finish(
                transaction.transaction_id,
                "transfer",
                start,
                wait_cycles,
                tuple(hops),
                service_span,
            )
        except simpy.Interrupt:
            pass
        finally:
            self.registry.release_all(granted)

    def _return_credit(
        self, entry: _ResourceEntry, credit: Request, delay: float
    ) -> ProcessGenerator:
        yield self.env.timeout(delay)
        if entry.credits is not None:
            self.registry.release(entry.credits, credit)
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
            granted.remove((entry.serializer, grant))
            self.registry.release(entry.serializer, grant)
            self._emit("unit_release", transaction.transaction_id, entry.resource_id)
            self._finish(transaction.transaction_id, "compute", start, wait_cycles)
        except simpy.Interrupt:
            pass
        finally:
            self.registry.release_all(granted)

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

    def _cancel_processes(self, processes: dict[str, simpy.Process]) -> None:
        """Interrupt every unfinished business process in plan order."""
        for transaction_id, process in processes.items():
            if not process.triggered:
                self._emit("cancel", transaction_id)
                process.interrupt()

    def _await_processes(self, processes: dict[str, simpy.Process]) -> None:
        """Bounded wait until every business process has ended.

        Interrupts propagate through every stage, so unwinding finishes at
        the interruption time step; the backstop only guards against a
        future stage that mis-handles interrupts, and a surviving process
        is caught by the all-released invariant afterwards.
        """
        pending = tuple(
            process for process in processes.values() if not process.triggered
        )
        if not pending:
            return
        backstop = self.plan.content.max_cycles * 2 + 100.0
        self.env.run(
            until=simpy.AnyOf(
                self.env, (self.env.all_of(pending), self.env.timeout(backstop))
            )
        )

    def _drain_background(self) -> None:
        """Run until delayed credit returns and other background work end."""
        if self._background:
            self.env.run(until=self.env.all_of(tuple(self._background)))

    def _abort(self, processes: dict[str, simpy.Process], exc: Exception) -> None:
        """Cancel in-flight work, run bounded cleanup, then fail honestly."""
        try:
            self._cancel_processes(processes)
            self._await_processes(processes)
            self._drain_background()
        except Exception as cleanup_error:  # noqa: BLE001 - cleanup must never mask the root cause
            exc.add_note(f"cleanup after abort raised: {cleanup_error!r}")
        raise RuntimeError(
            f"simulation aborted at {float(self.env.now)} cycles: {exc}"
        ) from exc

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
        try:
            self.env.run(
                until=simpy.AnyOf(
                    self.env, (drained, self.env.timeout(self.plan.content.max_cycles))
                )
            )
            if not drained.triggered:
                self._cancel_processes(processes)
                self._await_processes(processes)
            self._drain_background()
        except Exception as exc:  # noqa: BLE001 - any business failure aborts the run
            self._abort(processes, exc)
        if not self.registry.all_released():
            raise RuntimeError(
                "resources still held or queued after cleanup and drain; "
                "refusing to report a result"
            )

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
