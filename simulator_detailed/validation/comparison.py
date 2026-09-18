"""Fail-closed condition admission, explicit mappings and declared tolerances."""

import json
from fractions import Fraction

from pydantic import JsonValue

from ..configs.schemas.validation import (
    CheckResult,
    CheckSelection,
    EvidenceReference,
    MetricObservation,
    MetricPolicy,
    NormalizedObservations,
    ReferenceConditions,
    TimePoint,
    ValidationReference,
)
from .adapters import Admission
from .data import Data, obj, require
from .identity import canonical_record
from .matching import functional_match
from .normalize import converted_seconds


class IncompatibleReference(ValueError):
    """Missing or incompatible conditions; not an observed model discrepancy."""


def semantic_workload(admitted: Admission) -> Data:
    """Work identity excludes paths/evidence and the four allowed fitted knobs."""
    def clean(value: JsonValue) -> JsonValue:
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {
                "evidence", "graph_path", "profile_path", "max_aci_cycles", "bytes_per_cycle",
                "fixed_latency_cycles", "work_per_native_cycle", "setup_native_cycles"}}
        return value
    return obj(clean(admitted.configuration))


def model_metadata(admitted: Admission) -> dict[str, object]:
    source = obj(json.loads(admitted.inputs.get("source.json", admitted.path).read_bytes()))
    profile = source.get("kind") == "hardware_profile"
    return {
        "architecture": source.get("architecture", "synthetic"),
        "profile_version": str(source.get("profile_revision")) if profile else "canonical_topology_v1",
        "enabled_layout": canonical_record({"enabled_worker_ids": admitted.graph.get("enabled_worker_ids", [])}),
        "workload": canonical_record(semantic_workload(admitted)),
        "mapping": canonical_record({"attachments": admitted.graph.get("attachments", []), "resources": admitted.graph.get("resources", [])}),
    }


def admit_conditions(admitted: Admission, actual: ReferenceConditions | None, reference: ReferenceConditions,
                     *, timing: bool) -> None:
    if actual is None:
        raise IncompatibleReference("case has no declared comparison conditions")
    model = model_metadata(admitted)
    required = tuple(ReferenceConditions.model_fields) if timing else ("architecture", "profile_version", "enabled_layout", "workload", "mapping")
    for field in required:
        left, right = getattr(actual, field), getattr(reference, field)
        if left.state != "known" or right.state != "known":
            raise IncompatibleReference(f"unknown comparison metadata: {field}")
        if field in model and left.value != model[field]:
            raise IncompatibleReference(f"case {field} contradicts admitted simulator input")
        if field not in ("capture_group", "measurement", "clocks") and left != right:
            raise IncompatibleReference(f"incompatible comparison metadata: {field}")
    if timing:
        left_window, right_window = actual.measurement.value, reference.measurement.value
        if left_window is None or right_window is None:
            raise IncompatibleReference("measurement metadata unavailable")
        if (left_window.boundary, left_window.repetitions, left_window.aggregation, left_window.excluded_warmups) != (
                right_window.boundary, right_window.repetitions, right_window.aggregation, right_window.excluded_warmups):
            raise IncompatibleReference("incompatible measurement window/repetitions/aggregation")
        if left_window.boundary != "simulation_start_to_snapshot":
            raise IncompatibleReference("kernel/host/unmodeled interval has no supported simulator boundary")


def metric_pair(actual: NormalizedObservations, reference: NormalizedObservations, policy: MetricPolicy,
                clock_mapping: dict[str, str]) -> tuple[Fraction, Fraction]:
    left = next((m for m in actual.metrics if m.metric_id == policy.metric_id), None)
    right = next((m for m in reference.metrics if m.metric_id == policy.metric_id), None)
    if left is None or right is None:
        raise IncompatibleReference(f"missing observable metric: {policy.metric_id}")
    for metric in (left, right):
        if metric.completion_scope != "complete_run" or metric.window.boundary != policy.boundary:
            raise IncompatibleReference("incomplete or mismatched measurement boundary")
    if (left.window.repetitions, left.window.aggregation, left.window.excluded_warmups) != (
            right.window.repetitions, right.window.aggregation, right.window.excluded_warmups):
        raise IncompatibleReference("metric repetitions/warmups/aggregation differ")
    if left.clock_domain != right.clock_domain and clock_mapping.get(right.clock_domain or "") != left.clock_domain:
        raise IncompatibleReference("missing explicit clock-domain mapping")

    def value(metric: MetricObservation, observation: NormalizedObservations) -> Fraction:
        if policy.unit == "seconds" and metric.unit in ("cycles", "seconds", "nanoseconds") and metric.clock_domain is not None:
            return converted_seconds(TimePoint(value=metric.value, unit=metric.unit, clock_domain=metric.clock_domain), observation.clocks)
        if metric.unit != policy.unit:
            raise IncompatibleReference("incompatible metric units")
        return Fraction(str(metric.value))

    if policy.unit == "cycles":
        left_clock = next((c for c in actual.clocks if c.domain_id == left.clock_domain), None)
        right_clock = next((c for c in reference.clocks if c.domain_id == right.clock_domain), None)
        if left_clock is None or right_clock is None or left_clock.hz.state != "known" or right_clock.hz.state != "known" or left_clock.hz.value != right_clock.hz.value:
            raise IncompatibleReference("cycle comparison requires equal known frequencies; explicitly select seconds for conversion")
    return value(left, actual), value(right, reference)


def tolerance_pass(actual: Fraction, reference: Fraction, policy: MetricPolicy) -> bool:
    if reference == 0 and policy.absolute_tolerance == 0 and policy.relative_tolerance > 0:
        raise IncompatibleReference("zero reference requires positive absolute tolerance or an explicit exact policy")
    tolerance = Fraction(str(policy.absolute_tolerance)) + Fraction(str(policy.relative_tolerance)) * abs(reference)
    return abs(actual - reference) <= tolerance


def compare(selection: CheckSelection, admitted: Admission, conditions: ReferenceConditions | None,
            actual: NormalizedObservations, reference: ValidationReference, evidence: EvidenceReference) -> CheckResult:
    tier = selection.tier
    timing = selection.check in ("metrics", "silicon_timing")
    try:
        classification = reference.provenance.classification
        if tier == "silicon_timing" and classification != "hardware_capture":
            raise IncompatibleReference("silicon timing requires hardware_capture, not synthetic/functional evidence")
        if tier == "functional_reference" and classification not in ("functional_capture", "hardware_capture"):
            raise IncompatibleReference("external functional tier requires an actual functional or hardware capture")
        if tier == "architecture_protocol":
            raise IncompatibleReference("architecture facts require a separately named fact oracle")
        expected = reference.observations
        if expected is None:
            raise IncompatibleReference("reference observations unavailable")
        admit_conditions(admitted, conditions, reference.conditions, timing=timing)
        if timing:
            if not selection.metrics:
                raise IncompatibleReference("metric tolerances were not predeclared")
            for policy in selection.metrics:
                for declared, observation in ((conditions, actual), (reference.conditions, expected)):
                    if declared is None or declared.clocks.value is None:
                        raise IncompatibleReference("declared clock metadata unavailable")
                    clock_ids = {c.domain_id: c for c in declared.clocks.value}
                    for clock in observation.clocks:
                        declared_clock = clock_ids.get(clock.domain_id)
                        if declared_clock is None or declared_clock.hz.state != "known" or declared_clock.hz != clock.hz:
                            raise IncompatibleReference("declared clocks contradict observed clock domains/frequencies")
                left, right = metric_pair(actual, expected, policy, {m.reference: m.simulator for m in selection.clock_mappings})
                require(tolerance_pass(left, right, policy), f"{policy.metric_id} outside declared tolerance: actual={left}, reference={right}")
        else:
            functional_match(actual, expected, {m.reference: m.simulator for m in selection.entity_mappings},
                             {m.reference: m.simulator for m in selection.event_mappings})
    except IncompatibleReference as exc:
        return CheckResult(check_id=selection.check_id, required=selection.required, tier=tier, outcome="blocked", executed=False,
                           reason=str(exc), observation_ids=(actual.observation_id,), evidence=(evidence,))
    except (ValueError, KeyError) as exc:
        return CheckResult(check_id=selection.check_id, required=selection.required, tier=tier, outcome="fail", executed=True,
                           reason=str(exc), observation_ids=(actual.observation_id,), evidence=(evidence,), comparison_admitted=True)
    return CheckResult(check_id=selection.check_id, required=selection.required, tier=tier, outcome="pass", executed=True,
                       reason="admitted observable comparison passed; supplied origin is not authenticated", observation_ids=(actual.observation_id,),
                       evidence=(evidence,), comparison_admitted=True)
