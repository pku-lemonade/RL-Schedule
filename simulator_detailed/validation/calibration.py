"""Finite typed candidate search with fit-only selection and sealed evaluation."""

import itertools
import json
import math
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..configs.schemas.validation import (
    CalibrationCase,
    CalibrationMetric,
    CalibrationParameter,
    CalibrationPlan,
    CalibrationResult,
    CandidateResult,
    CanonicalJSON,
    CaseResult,
    CheckResult,
    CheckSelection,
    EvidenceReference,
    FrozenSelection,
    MemoryTarget,
    MetricPolicy,
    ParameterValue,
    ReferenceConditions,
    RunIdentity,
    SeedState,
    ValidationCase,
    ValidationReference,
)
from .adapters import Admission, admit
from .comparison import (
    IncompatibleReference,
    admit_conditions,
    compare,
    metric_error_summary,
    metric_pair,
    model_metadata,
    tolerance_pass,
)
from .data import obj, parse, require, rows
from .gates import ROOT
from .identity import (
    bytes_digest,
    collect_run_identity,
    content_digest,
    read_verified_artifact,
    resolve_asset,
)
from .outcomes import aggregate_status, tier_status
from .references import import_reference
from .runner import run_case


@dataclass(frozen=True)
class CalibrationInput:
    case: CalibrationCase
    admitted: Admission
    reference: ValidationReference | None
    evidence: EvidenceReference | None
    unavailable: str | None


@dataclass(frozen=True)
class CalibrationAdmission:
    plan: CalibrationPlan
    fit: tuple[CalibrationInput, ...]
    evaluation: tuple[CalibrationInput, ...]
    identity: RunIdentity
    plan_sha256: str


def semantic_fingerprint(admitted: Admission, conditions: ReferenceConditions | None) -> str:
    metadata = model_metadata(admitted)
    scope = {} if conditions is None else conditions.model_dump(mode="json", exclude={"capture_group", "measurement", "workload"})
    return content_digest({"model": {k: v.model_dump(mode="json") if isinstance(v, CanonicalJSON) else v for k, v in metadata.items()}, "conditions": scope})


def _case_metrics(
    plan: CalibrationPlan, case: CalibrationCase
) -> tuple[CalibrationMetric, ...]:
    selected = set(case.metric_ids)
    return tuple(
        metric
        for metric in plan.metrics
        if not selected or metric.metric_id in selected
    )


def admit_calibration(path: Path) -> CalibrationAdmission:
    plan = CalibrationPlan.model_validate_json(path.read_bytes())
    items: list[CalibrationInput] = []
    inputs: dict[str, Path] = {"plan.json": path.resolve()}
    if plan.source_campaign is not None:
        read_verified_artifact(path, plan.source_campaign)
        inputs["external/campaign.json"] = resolve_asset(
            path, plan.source_campaign.path
        )
    for index, case in enumerate((*plan.fit_cases, *plan.evaluation_cases)):
        admitted = admit(case.adapter, resolve_asset(path, case.input_path), horizon=case.budget.max_aci_cycles)
        require(semantic_fingerprint(admitted, case.conditions) == case.semantic_sha256, "declared semantic fingerprint disagrees with admitted workload/conditions")
        inputs.update({f"case/{index}/{name}": file for name, file in admitted.inputs.items()})
        reference = None
        evidence = None
        unavailable = None
        try:
            read_verified_artifact(path, case.reference.document)
            reference_path = resolve_asset(path, case.reference.document.path)
            reference = import_reference(reference_path)
            require(reference.reference_id == case.reference.reference_id, "calibration reference binding differs from document")
            require(reference.conditions.capture_group.state == "known" and reference.conditions.capture_group.value == case.capture_group,
                    "capture-group declaration differs from supplied capture")
            inputs[f"reference/{index}/document.json"] = reference_path
            inputs[f"reference/{index}/raw"] = resolve_asset(reference_path, reference.provenance.raw_artifact.path)
            evidence = EvidenceReference(reference_id=reference.reference_id, document_sha256=case.reference.document.sha256,
                                         classification=reference.provenance.classification)
        except FileNotFoundError as exc:
            unavailable = f"reference unavailable: {exc}"
        items.append(CalibrationInput(case, admitted, reference, evidence, unavailable))
    fit, evaluation = tuple(items[:len(plan.fit_cases)]), tuple(items[len(plan.fit_cases):])
    # Independently recompute split membership; the declarations alone are not evidence.
    fit_semantics = {semantic_fingerprint(i.admitted, i.case.conditions) for i in fit}
    require(not fit_semantics.intersection(semantic_fingerprint(i.admitted, i.case.conditions) for i in evaluation), "renamed fit/evaluation semantic duplicate")
    fit_raw = {i.reference.provenance.raw_artifact.sha256 for i in fit if i.reference is not None}
    require(not fit_raw.intersection(i.reference.provenance.raw_artifact.sha256 for i in evaluation if i.reference is not None), "fit/evaluation reuse the same raw capture")
    for item in items:
        if item.reference is not None and item.reference.observations is not None:
            for metric in _case_metrics(plan, item.case):
                expected = next((m for m in item.reference.observations.metrics if m.metric_id == metric.metric_id), None)
                if expected is not None:
                    tolerance_pass(Fraction(str(expected.value)), Fraction(str(expected.value)), metric)
    identity, _ = collect_run_identity(ROOT, inputs=inputs, effective_plan=plan, selection={"loss": plan.loss, "tie_break": plan.tie_break},
                                      seed=SeedState(mode="deterministic"))
    return CalibrationAdmission(plan, fit, evaluation, identity, bytes_digest(path.read_bytes()))


def apply_candidate(source: Admission, parameters: tuple[CalibrationParameter, ...], values: tuple[ParameterValue, ...], directory: Path) -> Admission:
    """Copy exact inputs and evidence, change only typed numeric targets, fully re-admit."""
    document = parse(source.path.read_text())
    memory = obj(document["memory"]) if source.adapter == "compute_workload_v1" else document
    for parameter, value in zip(parameters, values, strict=True):
        require(parameter.parameter_id == value.parameter_id, "candidate parameter order differs from plan")
        require(parameter.lower <= value.value <= parameter.upper and value.value in parameter.candidates, "candidate outside declared finite domain")
        target = parameter.target
        if isinstance(target, MemoryTarget):
            selected = [r for r in rows(memory["resources"]) if r["resource_id"] == target.resource_id]
            require(len(selected) == 1, "unknown/ambiguous physical memory target")
            obj(selected[0]["service"])[target.field] = value.value
        else:
            require(source.adapter == "compute_workload_v1", "compute rate target cannot modify a memory-only case")
            selected = [r for r in rows(document["rates"]) if r["rate_id"] == target.rate_id]
            require(len(selected) == 1, "unknown/ambiguous compute rate target")
            selected[0][target.field] = value.value
    directory.mkdir()
    binding = obj(memory["source"])
    source_field = "profile_path" if "profile_path" in binding else "graph_path"
    binding[source_field] = "source.json"
    (directory / "source.json").write_bytes(source.inputs["source.json"].read_bytes())
    candidate_path = directory / "case.json"
    candidate_path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return admit(source.adapter, candidate_path)


def _runtime_case(case: CalibrationCase) -> ValidationCase:
    names = ("expected_execution", "packet_accounting", "memory_service", "ownership", "drain")
    if case.adapter == "compute_workload_v1":
        names += ("compute_work",)
    checks = tuple(CheckSelection.model_validate({"check_id": name, "check": name, "required": True,
                                                  "tier": "model_invariant", "requirements": ("VA-D08",)}) for name in names)
    return ValidationCase(case_id=case.case_id, adapter=case.adapter, input_path=case.input_path, budget=case.budget,
                          expected_execution="complete", checks=checks, conditions=case.conditions)


def _policies(
    plan: CalibrationPlan, case: CalibrationCase
) -> tuple[MetricPolicy, ...]:
    return tuple(
        MetricPolicy.model_validate(metric.model_dump(exclude={"weight", "scale"}))
        for metric in _case_metrics(plan, case)
    )


def _fit_candidate(plan: CalibrationPlan, fit: tuple[CalibrationInput, ...], values: tuple[ParameterValue, ...], index: int, directory: Path) -> CandidateResult:
    identifier = f"candidate:{index}"
    checks: list[CheckResult] = []
    runs: list[CaseResult] = []
    effective: list[str] = []
    errors, weights = Fraction(0), Fraction(0)
    measured_accepted = True
    failure = "rejected"
    try:
        # Re-admit every fitting configuration before running this candidate.
        admitted = tuple(apply_candidate(item.admitted, plan.parameters, values, directory / f"candidate-{index}-fit-{n}") for n, item in enumerate(fit))
        effective = [a.effective.text for a in admitted]
        for item, candidate in zip(fit, admitted, strict=True):
            failure = "blocked"
            if item.reference is None or item.reference.observations is None or item.evidence is None:
                raise IncompatibleReference(item.unavailable or "reference observations unavailable")
            if plan.evidence_scope == "measured_conditions" and item.reference.provenance.classification != "hardware_capture":
                raise IncompatibleReference("measured fitting requires hardware captures")
            admit_conditions(candidate, item.case.conditions, item.reference.conditions, timing=True)
            failure = "failed"
            result = run_case(_runtime_case(item.case), candidate)
            runs.append(result)
            if aggregate_status(result.checks) != "pass" or not result.observations:
                raise ValueError("candidate runtime/audit did not complete successfully")
            observation = result.observations[-1]
            acceptance = compare(CheckSelection(check_id="fit_acceptance", check="metrics", required=True,
                                                  tier="silicon_timing" if plan.evidence_scope == "measured_conditions" else "model_invariant",
                                                  requirements=("VA-D08",), reference_id=item.reference.reference_id, metrics=_policies(plan, item.case),
                                                  entity_mappings=item.case.entity_mappings,
                                                  clock_mappings=item.case.clock_mappings),
                                 candidate, item.case.conditions, observation, item.reference, item.evidence)
            if acceptance.outcome == "blocked":
                raise IncompatibleReference(acceptance.reason)
            measured_accepted = measured_accepted and acceptance.outcome == "pass"
            for metric in _case_metrics(plan, item.case):
                failure = "blocked"
                actual, expected = metric_pair(
                    observation,
                    item.reference.observations,
                    metric,
                    {
                        mapping.reference: mapping.simulator
                        for mapping in item.case.clock_mappings
                    },
                    {
                        mapping.reference: mapping.simulator
                        for mapping in item.case.entity_mappings
                    },
                )
                error = abs(actual - expected) / Fraction(str(metric.scale))
                weight = Fraction(str(metric.weight))
                errors += weight * error
                weights += weight
                checks.append(CheckResult(check_id=f"{item.case.case_id}:{metric.metric_id}:fit_loss", required=True, tier="model_invariant", outcome="pass",
                                           executed=True, reason=f"fit observation admitted; {metric_error_summary(actual, expected)}; scaled absolute error={error}",
                                           observation_ids=(observation.observation_id,), evidence=(item.evidence,), comparison_admitted=True))
        loss = float(errors / weights)
        require(math.isfinite(loss), "unrepresentable fit loss")
        if plan.evidence_scope == "measured_conditions" and measured_accepted:
            checks.append(CheckResult(check_id="measured_fit_acceptance", required=False, tier="silicon_timing", outcome="pass", executed=True,
                                       reason="all admitted measured fit cases/metrics satisfy original tolerances",
                                       observation_ids=tuple(r.observations[-1].observation_id for r in runs),
                                       evidence=tuple({i.evidence.reference_id: i.evidence for i in fit if i.evidence is not None}.values()), comparison_admitted=True))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        outcome = "blocked" if isinstance(exc, IncompatibleReference) else failure
        checks.append(CheckResult(check_id="candidate_failure", required=True, tier="model_invariant",
                                   outcome="blocked" if outcome == "blocked" else "fail", executed=outcome != "blocked", reason=str(exc) or type(exc).__name__))
        return CandidateResult.model_validate({"candidate_id": identifier, "values": values, "outcome": outcome,
                                               "reason": str(exc) or type(exc).__name__, "fit_loss": None,
                                               "configuration_sha256": content_digest(effective) if effective else None,
                                               "fit_checks": tuple(checks), "fit_runs": tuple(runs)})
    return CandidateResult(candidate_id=identifier, values=values, outcome="valid", reason="fit-only weighted scaled absolute error computed",
                            fit_loss=loss, configuration_sha256=content_digest({"values": [v.model_dump(mode="json") for v in values], "plans": effective}),
                            fit_checks=tuple(checks), fit_runs=tuple(runs))


def _evaluate(admission: CalibrationAdmission, frozen: FrozenSelection, directory: Path) -> tuple[tuple[CaseResult, ...], tuple[CheckResult, ...]]:
    runs: list[CaseResult] = []
    checks: list[CheckResult] = []
    for index, item in enumerate(admission.evaluation):
        try:
            candidate = apply_candidate(item.admitted, admission.plan.parameters, frozen.values, directory / f"held-out-{index}")
            if item.reference is None or item.reference.observations is None or item.evidence is None:
                raise IncompatibleReference(item.unavailable or "held-out reference unavailable")
            result = run_case(_runtime_case(item.case), candidate)
            runs.append(result)
            if aggregate_status(result.checks) != "pass" or not result.observations:
                raise ValueError("held-out runtime/audit did not complete successfully")
            selection = CheckSelection(check_id=item.case.case_id + ":evaluation", check="metrics", required=True,
                                        tier="silicon_timing" if admission.plan.evidence_scope == "measured_conditions" else "model_invariant",
                                        requirements=("VA-D08",), reference_id=item.reference.reference_id,
                                        metrics=_policies(admission.plan, item.case),
                                        entity_mappings=item.case.entity_mappings,
                                        clock_mappings=item.case.clock_mappings)
            checks.append(compare(selection, candidate, item.case.conditions, result.observations[-1], item.reference, item.evidence))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            checks.append(CheckResult(check_id=item.case.case_id + ":evaluation", required=True, tier="model_invariant",
                                       outcome="blocked" if isinstance(exc, IncompatibleReference) else "fail", executed=not isinstance(exc, IncompatibleReference), reason=str(exc)))
    return tuple(runs), tuple(checks)


def calibrate(path: Path) -> CalibrationResult:
    admitted = admit_calibration(path)
    plan = admitted.plan
    candidates: list[CandidateResult] = []
    references = {i.evidence.reference_id: i.evidence for i in (*admitted.fit, *admitted.evaluation) if i.evidence is not None}
    with tempfile.TemporaryDirectory(prefix="wormhole-calibration-") as temporary:
        directory = Path(temporary)
        for index, vector in enumerate(itertools.product(*(p.candidates for p in plan.parameters))):
            values = tuple(ParameterValue(parameter_id=p.parameter_id, value=v) for p, v in zip(plan.parameters, vector, strict=True))
            candidates.append(_fit_candidate(plan, admitted.fit, values, index, directory))
        valid = [c for c in candidates if c.outcome == "valid"]
        if not valid:
            return CalibrationResult(kind="calibration_result", schema_version=1, plan_sha256=admitted.plan_sha256,
                                      status="fail" if any(c.outcome in ("failed", "rejected") for c in candidates) else "incomplete",
                                      reason="no candidate has complete admitted fitting evidence; no default winner", candidates=tuple(candidates), selection=None,
                                      tied_candidate_ids=(), evaluation_checks=(), evidence_scope="unvalidated", references=tuple(references.values()), identity=admitted.identity)
        winner = min(valid, key=lambda c: c.fit_loss if c.fit_loss is not None else math.inf)
        require(winner.configuration_sha256 is not None, "valid candidate lacks configuration identity")
        fit_digest = content_digest([c.model_dump(mode="json") for c in candidates])
        seal = {"candidate_id": winner.candidate_id, "values": [v.model_dump(mode="json") for v in winner.values],
                "configuration_sha256": winner.configuration_sha256, "fit_evidence_sha256": fit_digest,
                "policy": [m.model_dump(mode="json") for m in plan.metrics]}
        frozen = FrozenSelection(candidate_id=winner.candidate_id, values=winner.values, configuration_sha256=winner.configuration_sha256 or "",
                                 fit_evidence_sha256=fit_digest, selection_sha256=content_digest(seal))
        # This is the only held-out execution call, after the immutable seal exists.
        runs, checks = _evaluate(admitted, frozen, directory)
        status = aggregate_status(checks)
        scope = "synthetic_demonstration"
        if plan.evidence_scope == "measured_conditions":
            measured_fit = tier_status(winner.fit_checks, "silicon_timing") == "validated"
            scope = "measured_conditions" if status == "pass" and measured_fit and all(r.classification == "hardware_capture" for r in references.values()) else "unvalidated"
        ties = tuple(c.candidate_id for c in valid if c.fit_loss == winner.fit_loss)
        return CalibrationResult.model_validate({"kind": "calibration_result", "schema_version": 1, "plan_sha256": admitted.plan_sha256,
                                                  "status": status, "reason": "held-out checks use the frozen fit-only winner and original tolerances",
                                                  "candidates": tuple(candidates), "selection": frozen, "tied_candidate_ids": ties if len(ties) > 1 else (),
                                                  "evaluation_checks": checks, "evaluation_runs": runs, "evidence_scope": scope,
                                                  "references": tuple(references.values()), "identity": admitted.identity})
