"""Neutral generic transaction batches and simulation results.

Exactly four transaction kinds exist: transfer, compute, wait and signal.
Timing fields use neutral `cycles` names with explicit per-network rates; no
device-era units or defaults appear. Wait/signal dependencies are counters:
signals add deltas, waits complete when the counter reaches a threshold.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .generic_graph import NeutralId
from .topology import (
    Cycles,
    Digest,
    GraphRecord,
    Index,
    PositiveInt,
    PositiveTime,
    unique,
)


class GenericLinkTiming(GraphRecord):
    """Explicit per-link timing; every value is a configured input."""

    bytes_per_cycle: PositiveInt
    hop_cycles: Cycles
    credit_return_cycles: Cycles
    buffer_slots: PositiveInt


class GenericLinkOverride(GraphRecord):
    link_id: NeutralId
    timing: GenericLinkTiming


class GenericNetworkTiming(GraphRecord):
    network_id: NeutralId
    link: GenericLinkTiming
    overrides: tuple[GenericLinkOverride, ...] = ()

    @model_validator(mode="after")
    def unique_overrides(self) -> Self:
        unique(tuple(o.link_id for o in self.overrides), "link timing override")
        return self


class GenericCounter(GraphRecord):
    counter_id: NeutralId
    initial_value: Index


class GenericTransactionBase(GraphRecord):
    """Shared envelope: identity, explicit dependencies and earliest start."""

    transaction_id: NeutralId
    depends_on: tuple[NeutralId, ...] = ()
    start_cycles: Cycles = 0.0


class GenericTransfer(GenericTransactionBase):
    """Endpoint-to-endpoint byte transfer along a declared static route.

    An optional `address` turns the memory-side endpoint into an addressed
    access: memory destination is a write, memory source is a read.
    """

    kind: Literal["transfer"]
    network_id: NeutralId
    source: NeutralId
    destination: NeutralId
    payload_bytes: PositiveInt
    address: Index | None = None


class GenericCompute(GenericTransactionBase):
    """Occupies one execution unit for a configured duration."""

    kind: Literal["compute"]
    unit_id: NeutralId
    duration_cycles: PositiveTime


class GenericWait(GenericTransactionBase):
    """Completes when the named counter first reaches the threshold."""

    kind: Literal["wait"]
    counter_id: NeutralId
    threshold: PositiveInt


class GenericSignal(GenericTransactionBase):
    """Adds a declared delta to the named counter."""

    kind: Literal["signal"]
    counter_id: NeutralId
    delta: PositiveInt


GenericTransaction = Annotated[
    GenericTransfer | GenericCompute | GenericWait | GenericSignal,
    Field(discriminator="kind"),
]


class GenericTransactionBatch(GraphRecord):
    """A finite, strictly admitted batch executed on one generic system graph."""

    kind: Literal["generic_transaction_batch"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    batch_id: NeutralId
    graph_path: NeutralId | None = None
    timing: tuple[GenericNetworkTiming, ...] = Field(min_length=1)
    counters: tuple[GenericCounter, ...] = ()
    transactions: tuple[GenericTransaction, ...] = Field(min_length=1)
    max_cycles: PositiveTime

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(t.network_id for t in self.timing), "timing network")
        unique(tuple(c.counter_id for c in self.counters), "counter identity")
        counter_ids = {c.counter_id for c in self.counters}
        timing_networks = {t.network_id for t in self.timing}
        identities: list[str] = []
        for transaction in self.transactions:
            identities.append(transaction.transaction_id)
            if isinstance(transaction, GenericTransfer):
                if transaction.network_id not in timing_networks:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: "
                        f"no timing declared for network {transaction.network_id}"
                    )
            else:
                counter_id = (
                    transaction.counter_id
                    if isinstance(transaction, (GenericWait, GenericSignal))
                    else None
                )
                if counter_id is not None and counter_id not in counter_ids:
                    raise ValueError(
                        f"transaction {transaction.transaction_id}: "
                        f"undeclared counter {counter_id}"
                    )
        unique(tuple(identities), "transaction identity")
        declared = set(identities)
        for transaction in self.transactions:
            for dependency in transaction.depends_on:
                if dependency not in declared:
                    raise ValueError(
                        f"transaction {transaction.transaction_id}: "
                        f"unknown dependency {dependency}"
                    )
                if dependency == transaction.transaction_id:
                    raise ValueError(
                        f"transaction {transaction.transaction_id} depends on itself"
                    )
        # Dependency cycles make the batch unschedulable; reject before simulation.
        incoming = {t.transaction_id: set(t.depends_on) for t in self.transactions}
        resolved: set[str] = set()
        pending = dict(incoming)
        while pending:
            ready = sorted(k for k, deps in pending.items() if deps <= resolved)
            if not ready:
                raise ValueError(
                    "dependency cycle involving " + ", ".join(sorted(pending))
                )
            resolved.update(ready)
            for key in ready:
                del pending[key]
        return self


class GenericHopSpan(GraphRecord):
    """One traversed link: queue entry, serialization window and arrival."""

    network_id: NeutralId
    link_id: NeutralId
    queued_cycles: Cycles
    serialization_start_cycles: Cycles
    serialization_end_cycles: Cycles
    arrival_cycles: Cycles


class GenericMemoryServiceSpan(GraphRecord):
    """The memory subsystem service window of one addressed transfer."""

    resource_id: NeutralId
    direction: Literal["read", "write"]
    bank_id: NeutralId
    port_id: NeutralId
    channel_id: NeutralId
    command_start_cycles: Cycles
    command_end_cycles: Cycles
    service_start_cycles: Cycles
    service_end_cycles: Cycles


class GenericTransactionSpan(GraphRecord):
    transaction_id: NeutralId
    kind: Literal["transfer", "compute", "wait", "signal"]
    status: Literal["complete", "incomplete"]
    reason: Literal[
        "completed",
        "cycle_limit",
        "dependency_unsatisfied",
        "route_unreachable",
        "capacity_exceeded",
    ]
    start_cycles: Cycles | None
    end_cycles: Cycles | None
    wait_cycles: Cycles | None = None
    hops: tuple[GenericHopSpan, ...] = ()
    service: GenericMemoryServiceSpan | None = None

    @model_validator(mode="after")
    def consistency(self) -> Self:
        if self.status == "complete":
            if self.reason != "completed":
                raise ValueError("a complete span must carry reason completed")
            if self.start_cycles is None or self.end_cycles is None:
                raise ValueError("a complete span requires start and end cycles")
        else:
            if self.reason == "completed":
                raise ValueError("an incomplete span cannot claim completion")
            if self.start_cycles is not None or self.end_cycles is not None:
                raise ValueError("an incomplete span cannot carry start or end cycles")
            if self.wait_cycles is not None:
                raise ValueError("an incomplete span cannot carry wait cycles")
            if self.service is not None:
                raise ValueError("an incomplete span cannot carry a memory service")
        if self.hops and self.kind != "transfer":
            raise ValueError("only transfer spans record hops")
        if self.service is not None and self.kind != "transfer":
            raise ValueError("only transfer spans record memory service")
        return self


class GenericResourceUsage(GraphRecord):
    resource_id: NeutralId
    kind: Literal[
        "link",
        "execution_unit",
        "memory_bank",
        "memory_port",
        "memory_channel",
    ]
    service_count: Index
    busy_cycles: Cycles
    queue_wait_cycles: Cycles = 0.0
    utilization: Cycles


class GenericCounterState(GraphRecord):
    counter_id: NeutralId
    value: Index
    updates: Index


class GenericErrorRecord(GraphRecord):
    """One machine-readable error or incomplete-transaction explanation."""

    transaction_id: NeutralId
    code: NeutralId
    message: Annotated[str, Field(min_length=1)]


class GenericTraceEvent(GraphRecord):
    """One deterministic trace row; sequence orders same-time events."""

    sequence: Index
    time_cycles: Cycles
    action: Literal[
        "credit_acquire",
        "credit_release",
        "serialize_start",
        "serialize_end",
        "hop_arrive",
        "unit_acquire",
        "unit_release",
        "counter_publish",
        "wait_resume",
        "cancel",
        "memory_command",
        "memory_service_start",
        "memory_service_end",
        "route_select",
    ]
    transaction_id: NeutralId | None = None
    resource_id: NeutralId | None = None
    detail: NeutralId | None = None


class GenericSimulationResult(GraphRecord):
    """Version-one honest result: no partial output can read as complete."""

    kind: Literal["generic_simulation_result"] = "generic_simulation_result"
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    batch_id: NeutralId
    system_id: NeutralId
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "transactions_incomplete"]
    completion_cycles: Cycles | None
    graph_sha256: Digest
    batch_sha256: Digest
    transactions: tuple[GenericTransactionSpan, ...]
    resources: tuple[GenericResourceUsage, ...]
    counters: tuple[GenericCounterState, ...]
    errors: tuple[GenericErrorRecord, ...] = ()
    trace: tuple[GenericTraceEvent, ...] = ()
    plan_sha256: Digest | None = None
    execution: Literal["generic_packet_transport"] = "generic_packet_transport"
    silicon_timing: Literal["unvalidated"] = "unvalidated"

    @model_validator(mode="after")
    def consistency(self) -> Self:
        """Corrupted or partial outputs must fail revalidation."""
        if not self.transactions:
            raise ValueError("an empty transaction set cannot report a result")
        complete = tuple(s.status == "complete" for s in self.transactions)
        if self.status == "complete":
            if not all(complete):
                raise ValueError("status complete requires every span complete")
            if self.reason != "drained":
                raise ValueError("a drained run carries reason drained")
        else:
            if all(complete):
                raise ValueError("status incomplete requires an incomplete span")
            if self.reason != "transactions_incomplete":
                raise ValueError("an incomplete run carries reason transactions_incomplete")
        ends = [s.end_cycles for s in self.transactions if s.end_cycles is not None]
        expected = max(ends) if ends else None
        if self.completion_cycles != expected:
            raise ValueError("completion_cycles disagrees with the transaction spans")
        sequences = [event.sequence for event in self.trace]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise ValueError("trace sequences must be unique and ordered")
        return self
