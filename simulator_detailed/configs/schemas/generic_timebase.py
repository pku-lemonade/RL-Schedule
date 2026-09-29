"""Optional, explicitly declared SI-nanosecond timebase for generic cycles.

A declaration labels the model timeline; it does not establish a hardware
frequency. Evidence references must identify the applicable unit contract.
"""
from __future__ import annotations
from fractions import Fraction
from typing import Annotated, Literal
from pydantic import Field
from .generic_graph import NeutralId
from .operation_cost import (
    CycleCount, OperationCycleConversion, OperationCostUnit, OperationRecord,
    RUNTIME_CYCLE_UNIT,
)


VersionOne = Annotated[int, Field(strict=True, ge=1, le=1)]


class GenericClockDomain(OperationRecord):
    """One fixed period for the entire generic runtime timeline, in SI ns."""

    schema_version: VersionOne
    domain_id: Literal["generic_runtime"]
    timebase_ref: NeutralId
    period_num_ns: CycleCount
    period_den: CycleCount
    evidence_ref: NeutralId

    @property
    def period_ns(self) -> Fraction:
        return Fraction(self.period_num_ns, self.period_den)


def validate_clock_conversion(
    clock: GenericClockDomain, conversion: OperationCycleConversion,
) -> None:
    clock = GenericClockDomain.model_validate(clock)
    conversion = OperationCycleConversion.model_validate(conversion)
    ratio = Fraction(conversion.numerator, conversion.denominator)
    if conversion.source == RUNTIME_CYCLE_UNIT:
        if ratio != 1:
            raise ValueError("runtime cycle identity must have ratio one")
    elif conversion.source.kind == "time" and conversion.source.name == "ns":
        if ratio != 1 / clock.period_ns:
            raise ValueError("conversion disagrees with the declared runtime period")
    else:
        raise ValueError("mapped mode accepts only explicit ns or runtime-cycle identity")


def make_ns_conversion(
    clock: GenericClockDomain, source: OperationCostUnit, *,
    conversion_ref: str, evidence_ref: str,
) -> OperationCycleConversion:
    clock = GenericClockDomain.model_validate(clock)
    source = OperationCostUnit.model_validate(source)
    if (source.kind, source.name) != ("time", "ns"):
        raise ValueError("this constructor requires an explicitly declared SI ns source")
    ratio = 1 / clock.period_ns
    conversion = OperationCycleConversion(
        conversion_ref=conversion_ref, source=source, target=RUNTIME_CYCLE_UNIT,
        numerator=ratio.numerator, denominator=ratio.denominator,
        evidence_ref=evidence_ref, rounding="ceil",
    )
    validate_clock_conversion(clock, conversion)
    return conversion
