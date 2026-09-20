"""Independent byte, coverage, condition and lineage audit for external reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..configs.schemas.external_validation import (
    ExternalArtifactIdentity,
    ExternalValidationCase,
    ExternalValidationReport,
)
from ..configs.schemas.validation import (
    CalibrationPlan,
    CalibrationResult,
    ValidationReference,
)
from .data import parse, require
from .external import (
    AdmittedExternalCampaign,
    AdmittedExternalCapture,
    admit_external_campaign,
    admit_external_capture,
)
from .identity import bytes_digest, content_digest
from .references import import_reference


@dataclass(frozen=True)
class ExternalReportAudit:
    document: ExternalValidationReport
    document_path: Path
    document_sha256: str
    campaign: AdmittedExternalCampaign
    captures: tuple[AdmittedExternalCapture, ...]
    reference_ids: tuple[str, ...]
    model_artifact_ids: tuple[str, ...]
    comparison_artifact_ids: tuple[str, ...]
    covered_case_producers: tuple[tuple[str, str], ...]


def _resolve(root: Path, logical_path: str) -> Path:
    path = (root / logical_path).resolve()
    require(path.is_relative_to(root.resolve()), "report artifact escapes its directory")
    return path


def _verified(root: Path, artifact: ExternalArtifactIdentity) -> Path:
    path = _resolve(root, artifact.logical_path)
    data = path.read_bytes()
    require(
        len(data) == artifact.size_bytes and bytes_digest(data) == artifact.sha256,
        f"report artifact identity mismatch: {artifact.artifact_id}",
    )
    return path


def _report_id(document: ExternalValidationReport) -> str:
    identity = {
        "campaign": document.campaign.model_dump(mode="json"),
        "bundles": [item.model_dump(mode="json") for item in document.bundles],
        "artifacts": [item.model_dump(mode="json") for item in document.artifacts],
        "boundaries": [item.model_dump(mode="json") for item in document.boundaries],
        "outcomes": [item.model_dump(mode="json") for item in document.outcomes],
        "claim_scope": document.claim_scope,
        "limitations": document.limitations,
    }
    return "external-report:" + content_digest(identity)


def _matching_case(
    campaign: AdmittedExternalCampaign, capture: AdmittedExternalCapture
) -> ExternalValidationCase:
    return next(
        item
        for item in campaign.document.cases
        if item.case_id == capture.document.case_id
    )


def _audit_capture_conditions(
    campaign: AdmittedExternalCampaign, capture: AdmittedExternalCapture
) -> None:
    case = _matching_case(campaign, capture)
    for name in (
        "architecture",
        "device_scope",
        "profile_version",
        "enabled_layout",
        "clocks",
        "workload",
        "mapping",
        "software",
        "instrumentation",
        "capture_group",
    ):
        expected = getattr(case.conditions, name)
        actual = getattr(capture.document.conditions, name)
        if expected.state == "known":
            require(
                actual == expected,
                f"capture condition disagrees with campaign: {name}",
            )
    conditions = capture.document.conditions
    environment = capture.document.environment
    for name in ("device", "software", "firmware", "enabled_layout"):
        declared = getattr(conditions, name)
        observed = getattr(environment, name)
        if declared.state == "known":
            require(
                observed == declared,
                f"capture environment disagrees with conditions: {name}",
            )
    if conditions.clocks.state == "known" and environment.clocks.state == "known":
        declared_clocks = tuple(
            {
                "domain_id": item.domain_id,
                "hz": item.hz.value,
            }
            for item in conditions.clocks.value or ()
        )
        observed_clocks = tuple(
            json.loads(item.text) for item in environment.clocks.value or ()
        )
        require(
            observed_clocks == declared_clocks,
            "capture environment disagrees with declared clocks",
        )


def _ancestors(
    artifact_id: str,
    parents: dict[str, tuple[str, ...]],
    active: frozenset[str] = frozenset(),
) -> frozenset[str]:
    require(artifact_id not in active, "report lineage contains a cycle")
    direct = parents[artifact_id]
    result = set(direct)
    for parent in direct:
        result.update(_ancestors(parent, parents, active | {artifact_id}))
    return frozenset(result)


def audit_external_report(path: Path) -> ExternalReportAudit:
    """Recompute an external report from bytes without trusting summary fields."""
    report_data = path.read_bytes()
    document = ExternalValidationReport.model_validate_json(report_data)
    require(document.report_id == _report_id(document), "external report identity mismatch")
    root = path.resolve().parent
    campaign_path = _verified(root, document.campaign)
    campaign = admit_external_campaign(campaign_path)
    require(
        campaign.document_sha256 == document.campaign.sha256,
        "report campaign identity changed during admission",
    )
    expected_boundaries = tuple(
        boundary
        for case in campaign.document.cases
        for boundary in case.boundary_maps
    )
    require(
        document.boundaries == expected_boundaries,
        "report boundaries disagree with the admitted campaign",
    )

    identities = (document.campaign, *document.bundles, *document.artifacts)
    paths = {
        item.artifact_id: _verified(root, item)
        for item in identities
    }
    lineage = {item.artifact_id: item for item in document.lineage}
    require(
        set(lineage) == set(paths),
        "report lineage must describe every artifact exactly once",
    )
    require(
        lineage[document.campaign.artifact_id].derived_from == (),
        "report campaign must be the lineage root",
    )
    parents = {
        artifact_id: item.derived_from for artifact_id, item in lineage.items()
    }
    for artifact_id in parents:
        ancestors = _ancestors(artifact_id, parents)
        if artifact_id != document.campaign.artifact_id:
            require(
                document.campaign.artifact_id in ancestors,
                f"report artifact is disconnected from campaign: {artifact_id}",
            )

    captures = tuple(
        admit_external_capture(paths[item.artifact_id]) for item in document.bundles
    )
    capture_by_artifact: dict[str, AdmittedExternalCapture] = {}
    pairs: list[tuple[str, str]] = []
    for identity, capture in zip(document.bundles, captures, strict=True):
        require(
            identity.artifact_id == f"bundle:{capture.document.bundle_id}",
            "report bundle identity disagrees with the admitted bundle",
        )
        require(
            capture.document.campaign.sha256 == campaign.document_sha256,
            "report bundle references a different campaign",
        )
        require(
            parents[identity.artifact_id] == (document.campaign.artifact_id,),
            "capture bundle must derive directly from the campaign",
        )
        _audit_capture_conditions(campaign, capture)
        case = _matching_case(campaign, capture)
        binding = next(
            item
            for item in case.producers
            if item.producer_id == capture.document.producer_id
        )
        declarations = {item.artifact_id: item for item in binding.outputs}
        require(
            all(item.artifact_id in declarations for item in capture.document.raw_artifacts),
            "capture contains an undeclared raw artifact",
        )
        if capture.document.outcome.outcome == "pass":
            require(
                {declarations[item.artifact_id].role for item in capture.document.raw_artifacts}
                == {item.role for item in binding.outputs},
                "successful capture omits a declared output role",
            )
        pair = (capture.document.case_id, capture.document.producer_id)
        require(pair not in pairs, "report contains duplicate case/producer captures")
        pairs.append(pair)
        capture_by_artifact[identity.artifact_id] = capture
    expected_pairs = {
        (case.case_id, binding.producer_id)
        for case in campaign.document.cases
        for binding in case.producers
    }
    require(
        set(pairs) == expected_pairs,
        "report case/producer capture coverage is incomplete",
    )

    reference_by_artifact: dict[str, ValidationReference] = {}
    plans: dict[str, CalibrationPlan] = {}
    results: dict[str, CalibrationResult] = {}
    model_ids = {
        artifact_id
        for artifact_id, item in lineage.items()
        if item.transform == "model_execution"
    }
    comparison_ids = {
        artifact_id
        for artifact_id, item in lineage.items()
        if item.transform == "interval_comparison"
    }
    for artifact in document.artifacts:
        artifact_path = paths[artifact.artifact_id]
        try:
            raw_document = parse(artifact_path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            continue
        kind = raw_document.get("kind")
        if kind == "validation_reference":
            reference = import_reference(artifact_path)
            direct_captures = tuple(
                capture_by_artifact[parent]
                for parent in parents[artifact.artifact_id]
                if parent in capture_by_artifact
            )
            require(
                bool(direct_captures),
                "validation reference must derive directly from a capture bundle",
            )
            matched = tuple(
                capture
                for capture in direct_captures
                if capture.document.intended_classification
                == reference.provenance.classification
                and capture.document.build.source_url
                == reference.provenance.source_url.value
                and capture.document.build.revision
                == reference.provenance.revision.value
                and capture.document.build.source_snapshot_sha256
                == reference.provenance.snapshot_sha256.value
            )
            require(
                len(matched) == 1,
                "reference provenance does not identify one parent capture/build",
            )
            for name in (
                "architecture",
                "device_scope",
                "profile_version",
                "enabled_layout",
                "clocks",
                "workload",
                "mapping",
                "software",
                "firmware",
                "instrumentation",
                "capture_group",
            ):
                require(
                    getattr(reference.conditions, name)
                    == getattr(matched[0].document.conditions, name),
                    f"reference conditions disagree with parent capture: {name}",
                )
            reference_by_artifact[artifact.artifact_id] = reference
        elif kind == "calibration_plan":
            plan = CalibrationPlan.model_validate_json(artifact_path.read_bytes())
            require(
                plan.source_campaign is not None
                and plan.source_campaign.sha256 == campaign.document_sha256,
                "external calibration plan does not seal the report campaign",
            )
            require(
                document.campaign.artifact_id in parents[artifact.artifact_id],
                "external calibration plan must derive from the campaign",
            )
            plans[artifact.artifact_id] = plan
        elif kind == "calibration_result":
            results[artifact.artifact_id] = CalibrationResult.model_validate_json(
                artifact_path.read_bytes()
            )

    for artifact_id in comparison_ids:
        direct = set(parents[artifact_id])
        require(
            bool(direct & model_ids) and bool(direct & set(reference_by_artifact)),
            "comparison must derive directly from model and reference artifacts",
        )
    for artifact_id, result in results.items():
        parent_plans = [
            parent
            for parent in parents[artifact_id]
            if parent in plans
            and bytes_digest(paths[parent].read_bytes()) == result.plan_sha256
        ]
        require(
            len(parent_plans) == 1,
            "calibration result does not identify one parent plan",
        )

    for outcome in document.outcomes:
        if outcome.outcome != "pass":
            continue
        outcome_ancestors = {
            ancestor
            for artifact_id in outcome.artifact_ids
            for ancestor in ({artifact_id} | set(_ancestors(artifact_id, parents)))
        }
        if outcome.stage in ("functional", "timing"):
            require(
                bool(outcome_ancestors & comparison_ids)
                and bool(outcome_ancestors & model_ids)
                and bool(outcome_ancestors & set(reference_by_artifact)),
                "passing external comparison lacks raw/reference/model lineage",
            )
            if outcome.stage == "timing":
                require(
                    any(
                        reference.provenance.classification == "hardware_capture"
                        for artifact_id, reference in reference_by_artifact.items()
                        if artifact_id in outcome_ancestors
                    ),
                    "passing timing comparison lacks hardware reference lineage",
                )
        elif outcome.stage in ("calibration", "evaluation"):
            require(
                bool(outcome_ancestors & set(results))
                and bool(outcome_ancestors & set(plans)),
                "passing calibration/evaluation lacks plan/result lineage",
            )

    return ExternalReportAudit(
        document=document,
        document_path=path.resolve(),
        document_sha256=bytes_digest(report_data),
        campaign=campaign,
        captures=captures,
        reference_ids=tuple(
            reference.reference_id for reference in reference_by_artifact.values()
        ),
        model_artifact_ids=tuple(sorted(model_ids)),
        comparison_artifact_ids=tuple(sorted(comparison_ids)),
        covered_case_producers=tuple(sorted(pairs)),
    )
