"""Unified generic transaction runtime: transfer/compute/wait/signal on SimPy.

The link kernel follows the same accounting discipline as
`virtual_channel.py` (bounded credits, charged storage, explicit release),
but uses neutral identity types: the existing kernel's identities are
torus-bound record families and cannot appear in generic documents or traces.
Structure comes from the canonical graph via `GenericSystem`; nothing here
infers routes from geometry or fabricates timing defaults.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import simpy
from simpy.events import Event, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .configs.schemas.generic_transactions import (
    GenericCompute,
    GenericCounterState,
    GenericHopSpan,
    GenericLinkTiming,
    GenericResourceUsage,
    GenericSignal,
    GenericSimulationResult,
    GenericTransactionBase,
    GenericTransactionBatch,
    GenericTransactionSpan,
    GenericTransfer,
    GenericWait,
)
from .generic_graph import GenericSystem
from .topology import content_digest


@dataclass
class _LinkRuntime:
    """One directed link: FIFO credit slots plus a single serializer."""

    env: simpy.Environment
    network_id: str
    link_id: str
    timing: GenericLinkTiming
    credits: Resource
    serializer: Resource
    service_count: int = 0
    busy_cycles: float = 0.0

    @classmethod
    def create(
        cls,
        env: simpy.Environment,
        network_id: str,
        link_id: str,
        timing: GenericLinkTiming,
    ) -> _LinkRuntime:
        return cls(
            env=env,
            network_id=network_id,
            link_id=link_id,
            timing=timing,
            credits=Resource(env, capacity=timing.buffer_slots),
            serializer=Resource(env, capacity=1),
        )

    def cross(self, payload_bytes: int) -> ProcessGenerator:
        queued = float(self.env.now)
        credit = self.credits.request()
        yield credit
        grant = self.serializer.request()
        yield grant
        serialization_start = float(self.env.now)
        serialization_cycles = float(
            math.ceil(payload_bytes / self.timing.bytes_per_cycle)
        )
        yield self.env.timeout(serialization_cycles)
        serialization_end = float(self.env.now)
        self.busy_cycles += serialization_end - serialization_start
        self.service_count += 1
        self.serializer.release(grant)
        yield self.env.timeout(self.timing.hop_cycles)
        arrival = float(self.env.now)
        self.env.process(self._return_credit(credit))
        return GenericHopSpan(
            network_id=self.network_id,
            link_id=self.link_id,
            queued_cycles=queued,
            serialization_start_cycles=serialization_start,
            serialization_end_cycles=serialization_end,
            arrival_cycles=arrival,
        )

    def _return_credit(self, credit: Request) -> ProcessGenerator:
        yield self.env.timeout(self.timing.credit_return_cycles)
        self.credits.release(credit)


@dataclass
class _UnitRuntime:
    """One execution unit held exclusively for a compute duration."""

    resource: Resource
    service_count: int = 0
    busy_cycles: float = 0.0


class _CounterRuntime:
    def __init__(self, env: simpy.Environment, initial_value: int):
        self.env = env
        self.value = initial_value
        self.updates = 0
        self.changed = env.event()

    def add(self, delta: int) -> None:
        self.value += delta
        self.updates += 1
        self.changed.succeed()
        self.changed = self.env.event()


class GenericRuntime:
    """Compile one admitted batch against one compiled generic system."""

    def __init__(self, system: GenericSystem, batch: GenericTransactionBatch):
        self.system = system
        self.batch = GenericTransactionBatch.model_validate(batch.model_dump(mode="json"))
        self._links: dict[tuple[str, str], _LinkRuntime] = {}
        self._units: dict[str, _UnitRuntime] = {}
        self._counters: dict[str, _CounterRuntime] = {}
        self._spans: dict[str, GenericTransactionSpan] = {}
        self._done: dict[str, Event] = {}
        self._terminal: dict[str, Literal["route_unreachable", "capacity_exceeded"]] = {}
        self.env = simpy.Environment()
        self._timing = self._resolve_timing()
        self._routes = self._resolve_routes()
        self._validate_references()
        self._precheck_transfers()

    def _resolve_timing(self) -> dict[tuple[str, str], GenericLinkTiming]:
        timing: dict[tuple[str, str], GenericLinkTiming] = {}
        links = self.system.document.links
        for network in self.batch.timing:
            if network.network_id not in self.system.network_fabrics:
                raise ValueError(f"timing declared for unknown network {network.network_id}")
            member_links = {link.link_id for link in links if link.network_id == network.network_id}
            for link_id in member_links:
                timing[(network.network_id, link_id)] = network.link
            for override in network.overrides:
                if override.link_id not in member_links:
                    raise ValueError(
                        f"timing override for unknown link {override.link_id} "
                        f"in network {network.network_id}"
                    )
                timing[(network.network_id, override.link_id)] = override.timing
        return timing

    def _resolve_routes(self) -> dict[str, tuple[str, ...]]:
        routes: dict[str, tuple[str, ...]] = {}
        for transaction in self.batch.transactions:
            if not isinstance(transaction, GenericTransfer):
                continue
            route = next(
                (
                    route
                    for route in self.system.document.static_routes
                    if route.network_id == transaction.network_id
                    and route.source == transaction.source
                    and route.destination == transaction.destination
                ),
                None,
            )
            if route is not None:
                routes[transaction.transaction_id] = tuple(route.link_ids)
        return routes

    def _precheck_transfers(self) -> None:
        """Structural reference errors raise; viability failures become results.

        Unknown endpoints are malformed input and fail before simulation. A
        missing static route or an oversized memory payload leaves the
        transaction terminally incomplete with an explicit reason code.
        """
        endpoints = self.system.endpoint_nodes
        capacities = {
            resource.endpoint_id: resource.capacity_bytes
            for resource in self.system.document.memory_resources
            if resource.endpoint_id is not None
        }
        for transaction in self.batch.transactions:
            if not isinstance(transaction, GenericTransfer):
                continue
            for endpoint_id in (transaction.source, transaction.destination):
                if endpoint_id not in endpoints:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: "
                        f"unknown endpoint {endpoint_id}"
                    )
            if transaction.transaction_id not in self._routes:
                self._terminal[transaction.transaction_id] = "route_unreachable"
                continue
            capacity = capacities.get(transaction.destination)
            if capacity is not None and transaction.payload_bytes > capacity:
                self._terminal[transaction.transaction_id] = "capacity_exceeded"

    def _validate_references(self) -> None:
        units = {unit.unit_id for unit in self.system.document.execution_units}
        for transaction in self.batch.transactions:
            if isinstance(transaction, GenericCompute) and transaction.unit_id not in units:
                raise ValueError(
                    f"compute {transaction.transaction_id}: unknown execution unit "
                    f"{transaction.unit_id}"
                )
        for counter in self.batch.counters:
            self._counters[counter.counter_id] = _CounterRuntime(self.env, counter.initial_value)
        for unit_id in sorted(units):
            self._units[unit_id] = _UnitRuntime(Resource(self.env, capacity=1))
        for (network_id, link_id), timing in sorted(self._timing.items()):
            self._links[(network_id, link_id)] = _LinkRuntime.create(
                self.env, network_id, link_id, timing
            )

    def _start(self, transaction: GenericTransactionBase) -> ProcessGenerator:
        if transaction.start_cycles > self.env.now:
            yield self.env.timeout(transaction.start_cycles - self.env.now)
        for dependency in transaction.depends_on:
            yield self._done[dependency]

    def _finish(
        self,
        transaction_id: str,
        kind: Literal["transfer", "compute", "wait", "signal"],
        start: float,
        hops: tuple[GenericHopSpan, ...] = (),
    ) -> None:
        self._spans[transaction_id] = GenericTransactionSpan(
            transaction_id=transaction_id,
            kind=kind,
            status="complete",
            reason="completed",
            start_cycles=start,
            end_cycles=float(self.env.now),
            hops=hops,
        )
        self._done[transaction_id].succeed()

    def _transfer(self, transaction: GenericTransfer) -> ProcessGenerator:
        yield self.env.process(self._start(transaction))
        queued = float(self.env.now)
        hops: list[GenericHopSpan] = []
        for link_id in self._routes[transaction.transaction_id]:
            link = self._links[(transaction.network_id, link_id)]
            hop: GenericHopSpan = yield self.env.process(
                link.cross(transaction.payload_bytes)
            )
            hops.append(hop)
        self._finish(transaction.transaction_id, "transfer", queued, tuple(hops))

    def _compute(self, transaction: GenericCompute) -> ProcessGenerator:
        yield self.env.process(self._start(transaction))
        unit = self._units[transaction.unit_id]
        grant = unit.resource.request()
        yield grant
        start = float(self.env.now)
        yield self.env.timeout(transaction.duration_cycles)
        end = float(self.env.now)
        unit.busy_cycles += end - start
        unit.service_count += 1
        unit.resource.release(grant)
        self._finish(transaction.transaction_id, "compute", start)

    def _wait(self, transaction: GenericWait) -> ProcessGenerator:
        yield self.env.process(self._start(transaction))
        start = float(self.env.now)
        counter = self._counters[transaction.counter_id]
        while counter.value < transaction.threshold:
            yield counter.changed
        self._finish(transaction.transaction_id, "wait", start)

    def _signal(self, transaction: GenericSignal) -> ProcessGenerator:
        yield self.env.process(self._start(transaction))
        start = float(self.env.now)
        self._counters[transaction.counter_id].add(transaction.delta)
        self._finish(transaction.transaction_id, "signal", start)

    def run(self) -> GenericSimulationResult:
        for transaction in self.batch.transactions:
            self._done[transaction.transaction_id] = self.env.event()
            if transaction.transaction_id in self._terminal:
                continue
            if isinstance(transaction, GenericTransfer):
                self.env.process(self._transfer(transaction))
            elif isinstance(transaction, GenericCompute):
                self.env.process(self._compute(transaction))
            elif isinstance(transaction, GenericWait):
                self.env.process(self._wait(transaction))
            else:
                self.env.process(self._signal(transaction))
        pending = tuple(
            event
            for transaction_id, event in self._done.items()
            if transaction_id not in self._terminal
        )
        drained = self.env.all_of(pending)
        self.env.run(
            until=simpy.AnyOf(self.env, (drained, self.env.timeout(self.batch.max_cycles)))
        )

        spans: list[GenericTransactionSpan] = []
        for transaction in self.batch.transactions:
            terminal_reason = self._terminal.get(transaction.transaction_id)
            if terminal_reason is not None:
                spans.append(GenericTransactionSpan(
                    transaction_id=transaction.transaction_id,
                    kind=transaction.kind,
                    status="incomplete",
                    reason=terminal_reason,
                    start_cycles=None,
                    end_cycles=None,
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
            spans.append(GenericTransactionSpan(
                transaction_id=transaction.transaction_id,
                kind=transaction.kind,
                status="incomplete",
                reason="dependency_unsatisfied" if unsatisfied else "cycle_limit",
                start_cycles=None,
                end_cycles=None,
            ))
        completed = [span.end_cycles for span in spans if span.end_cycles is not None]
        completion = max(completed) if completed else None
        complete = all(span.status == "complete" for span in spans)

        resources: list[GenericResourceUsage] = []
        for (network_id, link_id), link in sorted(self._links.items()):
            resources.append(GenericResourceUsage(
                resource_id=f"{network_id}/{link_id}",
                kind="link",
                service_count=link.service_count,
                busy_cycles=link.busy_cycles,
                utilization=(
                    link.busy_cycles / completion
                    if completion is not None and completion > 0
                    else 0.0
                ),
            ))
        for unit_id, unit in sorted(self._units.items()):
            resources.append(GenericResourceUsage(
                resource_id=unit_id,
                kind="execution_unit",
                service_count=unit.service_count,
                busy_cycles=unit.busy_cycles,
                utilization=(
                    unit.busy_cycles / completion
                    if completion is not None and completion > 0
                    else 0.0
                ),
            ))
        return GenericSimulationResult(
            batch_id=self.batch.batch_id,
            system_id=self.system.document.system_id,
            status="complete" if complete else "incomplete",
            reason="drained" if complete else "transactions_incomplete",
            completion_cycles=completion,
            graph_sha256=self.system.generic_sha256,
            batch_sha256=content_digest(self.batch.model_dump(mode="json")),
            transactions=tuple(spans),
            resources=tuple(resources),
            counters=tuple(
                GenericCounterState(
                    counter_id=counter_id,
                    value=counter.value,
                    updates=counter.updates,
                )
                for counter_id, counter in sorted(self._counters.items())
            ),
        )


def run_generic_batch(system: GenericSystem, batch: GenericTransactionBatch) -> GenericSimulationResult:
    """Compile and execute one admitted batch; errors precede simulation."""
    return GenericRuntime(system, batch).run()
