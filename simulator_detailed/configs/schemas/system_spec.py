"""SystemSpec documents and immutable execution plans.

A `system_spec` composes one `generic_system_graph` with one
`generic_transaction_batch`. `compile_system` turns it into an
`immutable_plan`: stable resources, resolved transfers with effective hop
timings and terminal classifications, computes, waits with reachability
bounds, signals, counters and deterministic digests. Plan records carry no
SimPy objects and no runtime state.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ...topology import content_digest
from .generic_graph import GenericSystemGraph, NeutralId
from .generic_transactions import GenericLinkTiming, GenericTransactionBatch
from .topology import (
    Cycles,
    Digest,
    GraphRecord,
    Index,
    PositiveInt,
    PositiveTime,
    unique,
)


class SystemSpec(GraphRecord):
    """The single compile input: one graph plus one transaction batch."""

    kind: Literal["system_spec"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    spec_id: NeutralId
    graph: GenericSystemGraph
    batch: GenericTransactionBatch


class PlanHop(GraphRecord):
    """One resolved hop with its effective link timing."""

    network_id: NeutralId
    link_id: NeutralId
    timing: GenericLinkTiming


class PlanTransfer(GraphRecord):
    transaction_id: NeutralId
    kind: Literal["transfer"]
    network_id: NeutralId
    source: NeutralId
    destination: NeutralId
    payload_bytes: PositiveInt
    depends_on: tuple[NeutralId, ...]
    start_cycles: Cycles
    hops: tuple[PlanHop, ...] = ()
    terminal: Literal["route_unreachable", "capacity_exceeded"] | None = None


class PlanCompute(GraphRecord):
    transaction_id: NeutralId
    kind: Literal["compute"]
    unit_id: NeutralId
    duration_cycles: PositiveTime
    depends_on: tuple[NeutralId, ...]
    start_cycles: Cycles


class PlanWait(GraphRecord):
    transaction_id: NeutralId
    kind: Literal["wait"]
    counter_id: NeutralId
    threshold: PositiveInt
    depends_on: tuple[NeutralId, ...]
    start_cycles: Cycles
    reachable: Index


class PlanSignal(GraphRecord):
    transaction_id: NeutralId
    kind: Literal["signal"]
    counter_id: NeutralId
    delta: PositiveInt
    depends_on: tuple[NeutralId, ...]
    start_cycles: Cycles


PlanTransaction = Annotated[
    PlanTransfer | PlanCompute | PlanWait | PlanSignal,
    Field(discriminator="kind"),
]


class PlanResource(GraphRecord):
    """One physical resource constructible at most once per plan."""

    resource_id: NeutralId
    kind: Literal["link", "execution_unit"]
    capacity: PositiveInt


class PlanCounter(GraphRecord):
    counter_id: NeutralId
    initial_value: Index
    upper_bound: Index

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.upper_bound < self.initial_value:
            raise ValueError("counter upper bound is below its initial value")
        return self


class PlanContent(GraphRecord):
    """Everything the runtime needs, in deterministic order."""

    spec_id: NeutralId
    system_id: NeutralId
    graph_sha256: Digest
    batch_sha256: Digest
    resources: tuple[PlanResource, ...]
    counters: tuple[PlanCounter, ...]
    transactions: tuple[PlanTransaction, ...]
    max_cycles: PositiveTime

    @model_validator(mode="after")
    def identities(self) -> Self:
        unique(tuple(r.resource_id for r in self.resources), "plan resource")
        unique(tuple(c.counter_id for c in self.counters), "plan counter")
        unique(tuple(t.transaction_id for t in self.transactions), "plan transaction")
        return self


class ImmutablePlan(GraphRecord):
    """A validated, frozen, content-addressed execution plan."""

    kind: Literal["immutable_plan"] = "immutable_plan"
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    spec_sha256: Digest
    content: PlanContent
    plan_sha256: Digest

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.plan_sha256 != content_digest(self.content.model_dump(mode="json")):
            raise ValueError("plan digest does not match plan content")
        return self
