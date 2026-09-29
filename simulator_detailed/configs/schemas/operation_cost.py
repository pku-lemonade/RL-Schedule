"""Architecture-neutral whole-operation cost and explicit cycle conversion."""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, ConfigDict, Field, model_validator

from .generic_graph import NeutralId
from .topology import GraphRecord


def _number(value: object) -> int | float:
    # Check integers before any float conversion; bool is not a cost value.
    if type(value) not in (int, float):
        raise ValueError("cost value must be an int or float, not bool")
    if not 0 < value <= 2**53 or (type(value) is float and not math.isfinite(value)):
        raise ValueError("cost value must be finite and in (0, 2**53]")
    return value


CostValue = Annotated[int | float, BeforeValidator(_number)]
CycleCount = Annotated[int, Field(strict=True, gt=0, le=2**53)]


class OperationRecord(GraphRecord):
    model_config = ConfigDict(revalidate_instances="always")


class OperationDomain(OperationRecord):
    """Exact workload contract identity, supplied by the caller, not inferred.

    domain_id identifies the complete workload/configuration revision relevant
    to this quote. This minimal API does not accept untyped model parameters.
    """

    operation_kind: NeutralId
    domain_id: NeutralId


class OperationCostRequest(OperationRecord):
    operation_id: NeutralId
    domain: OperationDomain


class OperationCostUnit(OperationRecord):
    kind: Literal["cycle", "time"]
    name: NeutralId
    clock_domain: NeutralId


RUNTIME_CYCLE_UNIT = OperationCostUnit(
    kind="cycle", name="cycle", clock_domain="generic_runtime",
)


class OperationProvenance(OperationRecord):
    provider_ref: NeutralId
    revision: NeutralId
    evidence_ref: NeutralId


class OperationCostContract(OperationRecord):
    """Registered service semantics; only isolated durations are supported."""

    quantity: Literal["service_duration"]
    unit: OperationCostUnit
    domain: OperationDomain
    provenance: OperationProvenance
    service_semantics: Literal["isolated_service_duration"]


class OperationCostQuote(OperationCostContract):
    scope: Literal["whole_operation"]
    operation_id: NeutralId
    value: CostValue


class OperationCycleConversion(OperationRecord):
    """Explicit rational conversion, rounded upward to whole runtime cycles.

    Even an identity conversion requires a reference and evidence. A clock or
    time-unit name alone never supplies a scale factor.
    """

    conversion_ref: NeutralId
    source: OperationCostUnit
    target: OperationCostUnit
    numerator: CycleCount
    denominator: CycleCount
    evidence_ref: NeutralId
    rounding: Literal["ceil"]

    @model_validator(mode="after")
    def runtime_target(self) -> Self:
        if self.target != RUNTIME_CYCLE_UNIT:
            raise ValueError("conversion target must be the generic runtime cycle domain")
        return self

    def cycles(self, value: int | float) -> int:
        scaled = Fraction(value) * Fraction(self.numerator, self.denominator)
        cycles = -(-scaled.numerator // scaled.denominator)
        if not 0 < cycles <= 2**53:
            raise ValueError("converted duration is outside the runtime cycle range")
        return cycles


class CompiledOperationCost(OperationRecord):
    """Serializable admission evidence; contains no provider object/callback."""

    provider_ref: NeutralId
    quote: OperationCostQuote
    conversion: OperationCycleConversion
    duration_cycles: CycleCount
    semantics: Literal["isolated_completion"]

    @model_validator(mode="after")
    def compatible(self) -> Self:
        if self.provider_ref != self.quote.provenance.provider_ref:
            raise ValueError("operation cost provenance does not match provider reference")
        if self.quote.unit != self.conversion.source:
            raise ValueError("operation quote unit does not match conversion source")
        if self.duration_cycles != self.conversion.cycles(self.quote.value):
            raise ValueError("operation duration does not match explicit conversion")
        return self
