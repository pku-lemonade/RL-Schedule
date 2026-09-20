"""Portable calibration plans derived from admitted external campaigns."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..configs.schemas.external_validation import BoundaryMap, ExternalOutcome
from ..configs.schemas.validation import (
    ArtifactReference,
    CalibrationCase,
    CalibrationMetric,
    CalibrationParameter,
    CalibrationPlan,
    CalibrationResult,
    CaseBudget,
    ComputeTarget,
    MemoryTarget,
    ReferenceBinding,
    ReferenceConditions,
    ValidationReference,
)
from .adapters import Admission, admit
from .calibration import CalibrationAdmission, admit_calibration, semantic_fingerprint
from .comparison import admit_conditions
from .data import obj, require, rows
from .external import AdmittedExternalCampaign
from .external_campaign import ReportArtifactInput
from .identity import bytes_digest, content_digest, resolve_asset
from .outcomes import CheckOutcome
from .references import import_reference

CalibrationSplit = Literal["fit", "evaluation"]


@dataclass(frozen=True)
class ExternalCalibrationBinding:
    case_id: str
    boundary_id: str
    split: CalibrationSplit
    reference_path: Path
    wall_time_seconds: float
    max_aci_cycles: float
    weight: float
    scale: float


@dataclass(frozen=True)
class GeneratedExternalCalibrationPlan:
    admission: CalibrationAdmission
    plan_path: Path
    plan_sha256: str


@dataclass(frozen=True)
class ExternalCalibrationReportMaterial:
    artifact: ReportArtifactInput
    outcomes: tuple[ExternalOutcome, ...]
    claim_scope: tuple[str, ...]
    limitations: tuple[str, ...]


def _adapter(family: str) -> Literal["memory_replay_v1", "compute_workload_v1"]:
    if family == "dram_read_return":
        return "memory_replay_v1"
    if family == "compute_service":
        return "compute_workload_v1"
    raise ValueError("NoC transport cases are comparison-only calibration inputs")


def _require_target(admitted: Admission, parameter: CalibrationParameter) -> None:
    target = parameter.target
    configuration = admitted.configuration
    if isinstance(target, MemoryTarget):
        memory = obj(configuration["memory"]) if "memory" in configuration else configuration
        resources = [
            item
            for item in rows(memory["resources"])
            if item["resource_id"] == target.resource_id
        ]
        require(len(resources) == 1, "calibration target selects one memory resource")
        service = obj(resources[0]["service"])
        require(target.field in service, "calibration memory field is absent")
        return
    rates = [
        item
        for item in rows(configuration["rates"])
        if item["rate_id"] == target.rate_id
    ]
    require(len(rates) == 1, "calibration target selects one compute rate")
    require(target.field in rates[0], "calibration compute field is absent")


def _portable_model(admitted: Admission, directory: Path) -> None:
    document = obj(json.loads(admitted.path.read_text()))
    configuration = (
        obj(document["memory"])
        if admitted.adapter == "compute_workload_v1"
        else document
    )
    source = obj(configuration["source"])
    source_field = "profile_path" if "profile_path" in source else "graph_path"
    require(source_field in source, "calibration input requires an explicit source")
    source[source_field] = "source.json"
    source_path = admitted.inputs.get("source.json")
    require(source_path is not None, "calibration input source was not admitted")
    if source_path is None:
        raise ValueError("calibration input source was not admitted")
    directory.mkdir(parents=True)
    (directory / "source.json").write_bytes(source_path.read_bytes())
    (directory / "case.json").write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def _canonical_conditions_match(left: object, right: object) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in (
            "architecture",
            "profile_version",
            "enabled_layout",
            "workload",
            "mapping",
            "instrumentation",
            "clocks",
        )
    )


def _model_conditions(
    reference: ReferenceConditions, boundary: BoundaryMap
) -> ReferenceConditions:
    domain_map = {
        mapping.reference: mapping.simulator for mapping in boundary.clock_mappings
    }
    clocks = reference.clocks
    if clocks.value is not None:
        clocks = clocks.model_copy(
            update={
                "value": tuple(
                    clock.model_copy(
                        update={
                            "domain_id": domain_map.get(
                                clock.domain_id, clock.domain_id
                            )
                        }
                    )
                    for clock in clocks.value
                )
            }
        )
    measurement = reference.measurement
    if measurement.value is not None:
        window = measurement.value
        measurement = measurement.model_copy(
            update={
                "value": window.model_copy(
                    update={
                        "start": window.start.model_copy(
                            update={
                                "clock_domain": domain_map.get(
                                    window.start.clock_domain,
                                    window.start.clock_domain,
                                )
                            }
                        ),
                        "end": window.end.model_copy(
                            update={
                                "clock_domain": domain_map.get(
                                    window.end.clock_domain,
                                    window.end.clock_domain,
                                )
                            }
                        ),
                    }
                )
            }
        )
    return reference.model_copy(
        update={"clocks": clocks, "measurement": measurement}
    )


def generate_external_calibration_plan(
    *,
    campaign: AdmittedExternalCampaign,
    bindings: tuple[ExternalCalibrationBinding, ...],
    parameters: tuple[CalibrationParameter, ...],
    evidence_scope: Literal["synthetic_demonstration", "measured_conditions"],
    output_directory: Path,
) -> GeneratedExternalCalibrationPlan:
    """Seal one family of fit/held-out cases into the existing v1 plan format."""
    if output_directory.exists():
        raise ValueError("external calibration output directory already exists")
    require(bool(bindings), "external calibration requires case bindings")
    require(bool(parameters), "external calibration requires a finite parameter grid")
    require(
        {item.split for item in bindings} == {"fit", "evaluation"},
        "external calibration requires fit and held-out bindings",
    )
    require(
        len({item.case_id for item in bindings}) == len(bindings),
        "external calibration case bindings must be unique",
    )
    campaign_cases = {item.case_id: item for item in campaign.document.cases}
    campaign_inputs = {
        case.case_id: path
        for case, path in zip(
            campaign.document.cases, campaign.input_paths, strict=True
        )
    }
    require(
        all(item.case_id in campaign_cases for item in bindings),
        "calibration binding references an unknown campaign case",
    )
    families = {campaign_cases[item.case_id].family for item in bindings}
    require(
        len(families) == 1,
        "one calibration plan must use one supported case family",
    )
    family = next(iter(families))
    adapter = _adapter(family)
    if family == "dram_read_return":
        require(
            all(isinstance(item.target, MemoryTarget) for item in parameters),
            "calibration parameter kind disagrees with the campaign family",
        )
    else:
        require(
            all(isinstance(item.target, ComputeTarget) for item in parameters),
            "calibration parameter kind disagrees with the campaign family",
        )

    prepared: list[
        tuple[
            ExternalCalibrationBinding,
            Admission,
            ValidationReference,
            BoundaryMap,
            ReferenceConditions,
        ]
    ] = []
    for binding in bindings:
        case = campaign_cases.get(binding.case_id)
        require(case is not None, "calibration binding references an unknown campaign case")
        if case is None:
            raise ValueError("unknown campaign case")
        boundary = next(
            (
                item
                for item in case.boundary_maps
                if item.boundary_id == binding.boundary_id
            ),
            None,
        )
        require(boundary is not None, "calibration binding references an unknown boundary")
        if boundary is None:
            raise ValueError("unknown campaign boundary")
        admitted_model = admit(adapter, campaign_inputs[case.case_id])
        for parameter in parameters:
            _require_target(admitted_model, parameter)
        reference = import_reference(binding.reference_path)
        expected_classification = (
            "hardware_capture"
            if evidence_scope == "measured_conditions"
            else "synthetic"
        )
        require(
            reference.provenance.classification == expected_classification,
            "calibration reference classification disagrees with evidence scope",
        )
        if evidence_scope == "measured_conditions":
            require(
                reference.format == "tt_metal_device_profiler_csv_v1"
                and reference.sample_statistics is not None,
                "measured calibration requires profiler samples and statistics",
            )
        require(
            _canonical_conditions_match(case.conditions, reference.conditions),
            "calibration reference conditions disagree with the campaign",
        )
        model_conditions = _model_conditions(reference.conditions, boundary)
        admit_conditions(
            admitted_model,
            model_conditions,
            reference.conditions,
            timing=True,
        )
        observations = reference.observations
        require(observations is not None, "calibration reference observations unavailable")
        if observations is None:
            raise ValueError("calibration reference observations unavailable")
        metric = next(
            (
                item
                for item in observations.metrics
                if item.metric_id == boundary.comparison.metric_id
            ),
            None,
        )
        require(metric is not None, "calibration reference omits its boundary metric")
        if metric is None:
            raise ValueError("calibration reference omits its boundary metric")
        require(
            metric.window.boundary == boundary.simulator_boundary,
            "calibration reference metric uses a different boundary",
        )
        prepared.append(
            (binding, admitted_model, reference, boundary, model_conditions)
        )

    campaign_data = campaign.document_path.read_bytes()
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".external-calibration-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "calibration"
        staged.mkdir()
        (staged / "campaign.json").write_bytes(campaign_data)
        cases: list[tuple[CalibrationSplit, CalibrationCase]] = []
        metrics: dict[str, CalibrationMetric] = {}
        for index, (
            binding,
            admitted_model,
            reference_value,
            boundary_value,
            model_conditions,
        ) in enumerate(prepared):
            reference = reference_value
            boundary = boundary_value
            case_root = staged / "cases" / str(index)
            _portable_model(admitted_model, case_root)
            reference_root = staged / "references" / str(index)
            reference_root.mkdir(parents=True)
            raw_source = resolve_asset(
                binding.reference_path, reference.provenance.raw_artifact.path
            )
            raw_data = raw_source.read_bytes()
            raw_suffix = raw_source.suffix if raw_source.suffix else ".bin"
            raw_name = "raw" + raw_suffix
            (reference_root / raw_name).write_bytes(raw_data)
            provenance = reference.provenance.model_copy(
                update={
                    "raw_artifact": ArtifactReference(
                        path=raw_name,
                        sha256=bytes_digest(raw_data),
                    )
                }
            )
            portable_reference = reference.model_copy(
                update={"provenance": provenance}
            )
            reference_data = (
                portable_reference.model_dump_json(indent=2) + "\n"
            ).encode()
            reference_path = reference_root / "reference.json"
            reference_path.write_bytes(reference_data)
            import_reference(reference_path)
            metric = CalibrationMetric.model_validate(
                {
                    **boundary.comparison.model_dump(mode="json"),
                    "weight": binding.weight,
                    "scale": binding.scale,
                }
            )
            previous = metrics.setdefault(metric.metric_id, metric)
            require(
                previous == metric,
                "shared calibration metric has inconsistent policy",
            )
            capture_group = reference.conditions.capture_group.value
            require(
                reference.conditions.capture_group.state == "known"
                and capture_group is not None,
                "calibration capture group is unavailable",
            )
            if capture_group is None:
                raise ValueError("calibration capture group is unavailable")
            calibration_case = CalibrationCase(
                case_id=binding.case_id,
                adapter=adapter,
                input_path=f"cases/{index}/case.json",
                reference=ReferenceBinding(
                    reference_id=reference.reference_id,
                    document=ArtifactReference(
                        path=f"references/{index}/reference.json",
                        sha256=bytes_digest(reference_data),
                    ),
                ),
                budget=CaseBudget(
                    wall_time_seconds=binding.wall_time_seconds,
                    max_aci_cycles=binding.max_aci_cycles,
                ),
                semantic_sha256=semantic_fingerprint(
                    admitted_model, model_conditions
                ),
                capture_group=capture_group,
                conditions=model_conditions,
                metric_ids=(metric.metric_id,),
                entity_mappings=boundary.entity_mappings,
                clock_mappings=boundary.clock_mappings,
            )
            cases.append((binding.split, calibration_case))
        fit_cases = tuple(case for split, case in cases if split == "fit")
        evaluation_cases = tuple(
            case for split, case in cases if split == "evaluation"
        )
        candidate_count = 1
        for parameter in parameters:
            candidate_count *= len(parameter.candidates)
        plan_identity = {
            "campaign_sha256": campaign.document_sha256,
            "parameters": [item.model_dump(mode="json") for item in parameters],
            "bindings": [
                {
                    "case_id": item.case_id,
                    "boundary_id": item.boundary_id,
                    "split": item.split,
                }
                for item in bindings
            ],
            "metrics": [item.model_dump(mode="json") for item in metrics.values()],
            "evidence_scope": evidence_scope,
        }
        plan = CalibrationPlan(
            kind="calibration_plan",
            schema_version=1,
            plan_id="external-calibration:" + content_digest(plan_identity),
            parameters=parameters,
            max_candidates=candidate_count,
            max_case_runs=candidate_count * len(fit_cases) + len(evaluation_cases),
            fit_cases=fit_cases,
            evaluation_cases=evaluation_cases,
            metrics=tuple(metrics.values()),
            loss="weighted_mean_absolute_scaled_error_v1",
            tie_break="declared_candidate_order",
            evidence_scope=evidence_scope,
            source_campaign=ArtifactReference(
                path="campaign.json", sha256=campaign.document_sha256
            ),
        )
        plan_path = staged / "plan.json"
        plan_path.write_bytes((plan.model_dump_json(indent=2) + "\n").encode())
        admit_calibration(plan_path)
        staged.rename(output_directory)
    final_path = output_directory / "plan.json"
    final_admission = admit_calibration(final_path)
    return GeneratedExternalCalibrationPlan(
        admission=final_admission,
        plan_path=final_path.resolve(),
        plan_sha256=bytes_digest(final_path.read_bytes()),
    )


def publish_external_calibration_result(
    *,
    admission: CalibrationAdmission,
    result: CalibrationResult,
    result_artifact_id: str,
    derived_from: tuple[str, ...],
    output_path: Path,
) -> ExternalCalibrationReportMaterial:
    """Materialize a calibration result and its distinct fit/evaluation outcomes."""
    if output_path.exists():
        raise ValueError("calibration result output already exists")
    require(bool(derived_from), "calibration result requires lineage parents")
    require(
        result.plan_sha256 == admission.plan_sha256,
        "calibration result was produced from a different admitted plan",
    )
    plan = admission.plan
    split_inputs = (
        *(('fit', item) for item in admission.fit),
        *(('evaluation', item) for item in admission.evaluation),
    )
    statistics: dict[str, list[str]] = {"fit": [], "evaluation": []}
    case_scopes: list[str] = []
    for split, item in split_inputs:
        reference = item.reference
        if reference is None:
            statistics[split].append(
                f"{item.case.case_id}:unavailable:{item.unavailable or 'unknown reason'}"
            )
            conditions = item.case.conditions
        else:
            sample_statistics = reference.sample_statistics
            statistics[split].append(
                item.case.case_id
                + ":"
                + (
                    json.dumps(
                        sample_statistics.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    if sample_statistics is not None
                    else "not_applicable"
                )
            )
            conditions = reference.conditions
        case_scopes.append(
            "case conditions:"
            + json.dumps(
                {
                    "case_id": item.case.case_id,
                    "split": split,
                    "conditions": (
                        conditions.model_dump(mode="json")
                        if conditions is not None
                        else None
                    ),
                    "metric_ids": item.case.metric_ids,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    candidate_summary = "; ".join(
        f"{item.candidate_id}:{item.outcome}:fit_loss={item.fit_loss}"
        for item in result.candidates
    ) or "no candidates executed"
    if result.selection is not None:
        calibration_outcome: CheckOutcome = "pass"
        calibration_executed = True
        selected = ",".join(
            f"{item.parameter_id}={item.value}"
            for item in result.selection.values
        )
        calibration_reason = (
            f"selected {result.selection.candidate_id} ({selected}); "
            f"candidate history: {candidate_summary}; "
            f"ties={result.tied_candidate_ids or 'none'}; "
            f"configuration_sha256={result.selection.configuration_sha256}; "
            f"fit_evidence_sha256={result.selection.fit_evidence_sha256}; "
            "fit sample statistics: " + ",".join(statistics["fit"])
        )
    elif any(
        item.outcome in ("failed", "rejected") for item in result.candidates
    ):
        calibration_outcome = "fail"
        calibration_executed = True
        calibration_reason = "no fitted selection; candidate history: " + candidate_summary
    elif result.candidates:
        calibration_outcome = "blocked"
        calibration_executed = False
        calibration_reason = "fitting evidence unavailable; " + candidate_summary
    else:
        calibration_outcome = "not_run"
        calibration_executed = False
        calibration_reason = "no calibration candidates were executed"
    evaluation_outcome: CheckOutcome
    if result.status == "pass":
        evaluation_outcome = "pass"
    elif result.status == "fail":
        evaluation_outcome = "fail"
    else:
        evaluation_outcome = "blocked"
    evaluation_reason = "; ".join(
        f"{item.check_id}:{item.outcome}:{item.reason}"
        for item in result.evaluation_checks
    ) or result.reason
    evaluation_reason += "; evaluation sample statistics: " + ",".join(
        statistics["evaluation"]
    )
    data = (result.model_dump_json(indent=2) + "\n").encode()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".calibration-result-", dir=output_path.parent, delete=False
    ) as handle:
        handle.write(data)
        temporary_path = Path(handle.name)
    temporary_path.replace(output_path)
    CalibrationResult.model_validate_json(output_path.read_bytes())
    artifact_ids = (result_artifact_id,)
    outcomes = (
        ExternalOutcome(
            stage="calibration",
            required=True,
            outcome=calibration_outcome,
            reason=calibration_reason,
            executed=calibration_executed,
            artifact_ids=artifact_ids,
        ),
        ExternalOutcome(
            stage="evaluation",
            required=True,
            outcome=evaluation_outcome,
            reason=evaluation_reason,
            executed=bool(result.evaluation_checks),
            artifact_ids=artifact_ids,
        ),
    )
    targets = tuple(
        (
            f"memory:{item.target.resource_id}:{item.target.field}"
            if isinstance(item.target, MemoryTarget)
            else f"compute:{item.target.rate_id}:{item.target.field}"
        )
        for item in plan.parameters
    )
    cases = tuple(
        item.case_id for item in (*plan.fit_cases, *plan.evaluation_cases)
    )
    limitations = [
        "calibration is scoped to the named cases, captures, device conditions, intervals and candidate grid",
        "a selected effective parameter vector does not uniquely identify physical hardware parameters",
        "claims do not extend to untested workloads or mappings",
        "the result does not establish full-device timing accuracy or conformance",
    ]
    if result.tied_candidate_ids:
        limitations.append(
            "multiple declared candidates are indistinguishable at the captured resolution"
        )
    if result.evidence_scope != "measured_conditions":
        limitations.append("result is not measured hardware calibration")
    return ExternalCalibrationReportMaterial(
        artifact=ReportArtifactInput(
            artifact_id=result_artifact_id,
            path=output_path,
            derived_from=derived_from,
            transform="bounded_calibration",
        ),
        outcomes=outcomes,
        claim_scope=(
            f"calibration evidence scope: {result.evidence_scope}",
            "cases: " + ",".join(cases),
            "targets: " + ",".join(targets),
            *case_scopes,
        ),
        limitations=tuple(limitations),
    )
