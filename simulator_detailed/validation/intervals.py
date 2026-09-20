"""Supported timing boundaries and additive interval metric construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..configs.schemas.validation import (
    Identifier,
    IntervalSampleIdentity,
    IntervalSemanticScope,
    MeasurementWindow,
    MetricIntervalIdentity,
    MetricObservation,
    Number,
    TimePoint,
)

CompletionScope = Literal["complete_run", "interval"]


@dataclass(frozen=True)
class BoundaryRule:
    completion_scope: CompletionScope
    semantic_scope: IntervalSemanticScope | None
    resource_required: bool


SUPPORTED_BOUNDARIES: dict[str, BoundaryRule] = {
    "simulation_start_to_snapshot": BoundaryRule("complete_run", None, False),
    "operation_submission_to_acknowledged_completion": BoundaryRule(
        "interval", "acknowledged_operation", False
    ),
    "memory_service_begin_to_end": BoundaryRule("interval", "memory_service", True),
    "compute_resource_acquire_to_release": BoundaryRule("interval", "compute_service", True),
}


def boundary_rule(boundary: str) -> BoundaryRule:
    try:
        return SUPPORTED_BOUNDARIES[boundary]
    except KeyError as exc:
        raise ValueError(
            f"unsupported measurement boundary {boundary!r}; host, full-kernel, "
            "cross-core and overhead-corrected intervals are not admitted"
        ) from exc


def interval_metric(
    *,
    metric_id: Identifier,
    boundary: Identifier,
    semantic_scope: IntervalSemanticScope,
    subject_id: Identifier,
    resource_id: Identifier | None,
    start_event_id: Identifier,
    end_event_id: Identifier,
    start: TimePoint,
    end: TimePoint,
    value: Number,
    repetition_id: Identifier = "0",
) -> MetricObservation:
    """Build one exact, source-local interval metric from normalized endpoints."""
    rule = boundary_rule(boundary)
    if rule.semantic_scope != semantic_scope or rule.completion_scope != "interval":
        raise ValueError("interval semantic scope disagrees with its supported boundary")
    if rule.resource_required != (resource_id is not None):
        raise ValueError("interval resource identity disagrees with its supported boundary")
    return MetricObservation(
        metric_id=metric_id,
        value=value,
        unit=start.unit,
        clock_domain=start.clock_domain,
        numerator=f"{end_event_id} minus {start_event_id}",
        denominator="one retained run",
        window=MeasurementWindow(
            boundary=boundary,
            start=start,
            end=end,
            excluded_warmups=(),
            repetitions=1,
            aggregation="none",
        ),
        completion_scope="interval",
        interval=MetricIntervalIdentity(
            semantic_scope=semantic_scope,
            subject_id=subject_id,
            resource_id=resource_id,
            samples=(
                IntervalSampleIdentity(
                    repetition_id=repetition_id,
                    start_event_id=start_event_id,
                    end_event_id=end_event_id,
                ),
            ),
        ),
    )
