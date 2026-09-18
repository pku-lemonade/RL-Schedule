"""Sequential isolated finite suites; admission is completed before execution."""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..configs.schemas.validation import (
    CaseResult,
    CheckResult,
    CheckSelection,
    SeedState,
    ValidationCase,
    ValidationReport,
    ValidationSuite,
)
from .adapters import Admission, admit
from .audits import UnsupportedAudit, audit
from .data import Data, number, obj, rows
from .gates import ROOT, run_gate
from .identity import bytes_digest, collect_run_identity, resolve_asset
from .normalize import execution, normalize
from .outcomes import aggregate_status, tier_status


@dataclass(frozen=True)
class AdmittedSuite:
    path: Path
    document: ValidationSuite
    cases: tuple[Admission, ...]


def admit_suite(path: Path) -> AdmittedSuite:
    suite = ValidationSuite.model_validate_json(path.read_bytes())
    admitted = tuple(admit(case.adapter, resolve_asset(path, case.input_path), horizon=case.budget.max_aci_cycles) for case in suite.cases)
    for case, admission in zip(suite.cases, admitted, strict=True):
        if case.resume_at_aci_cycles:
            config = admission.configuration
            if "memory" in config:
                config = obj(config["memory"])
            if case.resume_at_aci_cycles[-1] >= number(config["max_aci_cycles"]):
                raise ValueError("resume horizon must precede input simulation horizon")
    return AdmittedSuite(path.resolve(), suite, admitted)


def isolated_run(case: ValidationCase, admitted: Admission) -> tuple[Data, ...]:
    absolute = case.model_copy(update={"input_path": str(admitted.path)})
    process = subprocess.run([sys.executable, "-m", "simulator_detailed.validation.worker"], cwd=ROOT,
                             input=absolute.model_dump_json(), text=True, capture_output=True,
                             timeout=case.budget.wall_time_seconds, check=False)
    if process.returncode:
        raise RuntimeError(f"adapter exit={process.returncode}: {process.stderr[-6000:]}")
    return tuple(rows(json.loads(process.stdout)))


def local_check(selection: CheckSelection, case: ValidationCase, admitted: Admission, raw: Data, observation_id: str) -> CheckResult:
    reason = ""
    outcome = "pass"
    executed = True
    if selection.tier != "model_invariant":
        return CheckResult(check_id=selection.check_id, required=selection.required, tier=selection.tier, outcome="blocked", executed=False,
                           reason="external/source comparison requires an admitted reference", observation_ids=(observation_id,))
    try:
        if selection.check == "expected_execution":
            if execution(raw) != case.expected_execution:
                raise ValueError(f"expected {case.expected_execution}, observed {execution(raw)}")
            reason = f"observed expected execution state {case.expected_execution}"
        elif selection.check == "admission":
            reason = "all declared simulator assets admitted before runtime allocation"
        elif selection.check == "bounded_execution":
            reason = "isolated worker returned within the enforced wall-time and simulation budgets"
        else:
            reason = audit(selection.check, admitted, raw)
    except UnsupportedAudit as exc:
        outcome, executed, reason = "unsupported", False, str(exc)
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        outcome, reason = "fail", str(exc) or type(exc).__name__
    return CheckResult(check_id=selection.check_id, required=selection.required, tier=selection.tier, outcome=outcome, executed=executed,
                       reason=reason, observation_ids=(observation_id,))


def run_case(case: ValidationCase, admitted: Admission) -> CaseResult:
    before = {name: bytes_digest(path.read_bytes()) for name, path in admitted.inputs.items()}
    identity, _ = collect_run_identity(ROOT, inputs=admitted.inputs, effective_plan=json.loads(admitted.effective.text),
                                      selection=case.model_dump(mode="json", exclude={"input_path"}), seed=SeedState(mode="deterministic"))
    try:
        raw_results = isolated_run(case, admitted)
        for name, path in admitted.inputs.items():
            if bytes_digest(path.read_bytes()) != before[name]:
                raise ValueError("input changed during case execution")
        observations = tuple(normalize(admitted, raw) for raw in raw_results)
    except (subprocess.TimeoutExpired, RuntimeError, ValueError, TypeError, KeyError) as exc:
        reason = "enforced wall-time budget exceeded; no snapshot available" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        checks = [CheckResult(check_id=s.check_id, required=s.required, tier=s.tier, outcome="not_run", executed=False, reason=reason) for s in case.checks]
        identifier = "harness_bounded_execution"
        while identifier in {c.check_id for c in checks}:
            identifier += "_"
        checks.append(CheckResult(check_id=identifier, required=True, tier="model_invariant", outcome="fail", executed=True, reason=reason))
        return CaseResult(case_id=case.case_id, adapter=case.adapter, execution="unavailable", reason=reason, identity=identity, observations=(), checks=tuple(checks))
    checks = tuple(local_check(s, case, admitted, raw_results[-1], observations[-1].observation_id) for s in case.checks)
    if case.resume_at_aci_cycles:
        # Check the retained charges at every interrupted snapshot as well as the final result.
        outcome, reason = "pass", "every interrupted snapshot retains conserved resource charges"
        try:
            for raw in raw_results[:-1]:
                audit("ownership", admitted, raw)
        except (ValueError, TypeError, KeyError) as exc:
            outcome, reason = "fail", str(exc)
        identifier = "harness_resume"
        while identifier in {c.check_id for c in checks}:
            identifier += "_"
        checks += (CheckResult(check_id=identifier, required=True, tier="model_invariant", outcome=outcome, executed=True,
                               reason=reason, observation_ids=tuple(o.observation_id for o in observations)),)
    return CaseResult(case_id=case.case_id, adapter=case.adapter, execution=observations[-1].execution,
                      reason="public adapter result; interruption snapshots retained" if case.resume_at_aci_cycles else "public adapter result",
                      identity=identity, observations=observations, checks=checks)


def run_suite(path: Path) -> ValidationReport:
    admitted = admit_suite(path)
    cases = tuple(run_case(case, source) for case, source in zip(admitted.document.cases, admitted.cases, strict=True))
    gates = tuple(run_gate(gate) for gate in admitted.document.gates)
    checks = tuple(check for case in cases for check in case.checks) + gates
    return ValidationReport(kind="validation_report", schema_version=1, suite_sha256=bytes_digest(path.read_bytes()),
                            status=aggregate_status(checks), cases=cases, gate_checks=gates, coverage=(),
                            capabilities=("finite_offline_validation_v1",),
                            assumptions=("Configured rates, clocks and capacities are explicit model inputs.",),
                            limitations=("No tensor values, multicast or synchronization validation.", "No authenticated device origin or measured timing without compatible supplied captures.",
                                         "Historical root remap tests retain stale LayerView.active_cores and layer/core assumptions."),
                            functional_reference=tier_status(checks, "functional_reference"), silicon_timing=tier_status(checks, "silicon_timing"))
