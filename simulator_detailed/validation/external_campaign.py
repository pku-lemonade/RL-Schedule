"""Resumable external campaign state, equivalence and functional timing gates."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from ..configs.schemas.external_validation import (
    ArtifactLineage,
    BoundaryMap,
    CampaignArtifactRole,
    CampaignStage,
    CampaignStateArtifact,
    CampaignTransition,
    EquivalenceCheck,
    EquivalenceValue,
    ExternalArtifactIdentity,
    ExternalCampaignState,
    ExternalCaseProgress,
    ExternalEquivalenceResult,
    ExternalOutcome,
    ExternalValidationCampaign,
    ExternalValidationReport,
    PairedFunctionalGate,
)
from ..configs.schemas.validation import (
    CheckSelection,
    EvidenceReference,
    NormalizedObservations,
    ReferenceConditions,
    ValidationReference,
)
from .adapters import Admission
from .comparison import compare
from .data import parse, require
from .external import (
    AdmittedExternalCampaign,
    AdmittedExternalCapture,
    admit_external_campaign,
    admit_external_capture,
)
from .external_capture import FunctionalReferenceConversion
from .identity import bytes_digest, canonical_record, content_digest, resolve_asset
from .matching import functional_match
from .outcomes import ReportStatus
from .references import import_reference

_STAGES: tuple[CampaignStage, ...] = (
    "planned",
    "collected",
    "imported",
    "functionally_checked",
    "timing_checked",
)


@dataclass(frozen=True)
class AdmittedCampaignState:
    document: ExternalCampaignState
    document_path: Path
    campaign_path: Path
    artifact_paths: tuple[Path, ...]
    document_sha256: str


@dataclass(frozen=True)
class CampaignStepOutput:
    role: CampaignArtifactRole
    path: Path


@dataclass(frozen=True)
class ReportArtifactInput:
    artifact_id: str
    path: Path
    derived_from: tuple[str, ...]
    transform: str


@dataclass(frozen=True)
class WrittenExternalReport:
    document: ExternalValidationReport
    document_path: Path
    document_sha256: str


def _state_id(document: ExternalCampaignState) -> str:
    payload = document.model_dump(mode="json", exclude={"state_id"})
    return "external-state:" + content_digest(payload)


def _resolve(root: Path, logical_path: str) -> Path:
    path = (root.resolve() / logical_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("campaign state artifact escapes its directory")
    return path


def _verified(path: Path, identity: ExternalArtifactIdentity) -> None:
    data = path.read_bytes()
    require(
        len(data) == identity.size_bytes and bytes_digest(data) == identity.sha256,
        f"campaign state artifact changed: {identity.logical_path}",
    )


def admit_campaign_state(path: Path) -> AdmittedCampaignState:
    """Verify state identity, transition inputs and every retained artifact byte."""
    data = path.read_bytes()
    document = ExternalCampaignState.model_validate_json(data)
    require(document.state_id == _state_id(document), "campaign state identity mismatch")
    root = path.resolve().parent
    campaign_path = _resolve(root, document.campaign.logical_path)
    _verified(campaign_path, document.campaign)
    campaign_document = ExternalValidationCampaign.model_validate_json(
        campaign_path.read_bytes()
    )
    require(
        campaign_document.campaign_id == document.campaign_id,
        "campaign state references a different campaign",
    )
    artifact_paths: list[Path] = []
    for progress in document.cases:
        prior_hashes = {document.campaign.sha256}
        for transition in progress.transitions:
            require(
                transition.input_sha256s == tuple(sorted(prior_hashes)),
                "campaign transition input hashes disagree with prior state",
            )
            for artifact in transition.artifacts:
                artifact_path = _resolve(root, artifact.logical_path)
                _verified(artifact_path, artifact)
                artifact_paths.append(artifact_path)
                prior_hashes.add(artifact.sha256)
    return AdmittedCampaignState(
        document=document,
        document_path=path.resolve(),
        campaign_path=campaign_path,
        artifact_paths=tuple(artifact_paths),
        document_sha256=bytes_digest(data),
    )


def initialize_campaign_state(
    admitted: AdmittedExternalCampaign, output_directory: Path
) -> AdmittedCampaignState:
    """Create one planned transition for every declared case/producer pair."""
    if output_directory.exists():
        raise ValueError("campaign state output directory already exists")
    campaign_data = admitted.document_path.read_bytes()
    campaign = ExternalArtifactIdentity(
        artifact_id="campaign",
        logical_path="campaign.json",
        sha256=bytes_digest(campaign_data),
        size_bytes=len(campaign_data),
    )
    cases = tuple(
        ExternalCaseProgress(
            case_id=case.case_id,
            producer_id=binding.producer_id,
            transitions=(
                CampaignTransition(
                    stage="planned",
                    input_sha256s=(campaign.sha256,),
                    artifacts=(),
                ),
            ),
        )
        for case in admitted.document.cases
        for binding in case.producers
    )
    initial = ExternalCampaignState(
        kind="external_campaign_state",
        schema_version=1,
        state_id="pending",
        campaign=campaign,
        campaign_id=admitted.document.campaign_id,
        cases=cases,
    )
    document = initial.model_copy(update={"state_id": _state_id(initial)})
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".external-state-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "state"
        staged.mkdir()
        (staged / "campaign.json").write_bytes(campaign_data)
        (staged / "state.json").write_bytes(
            (document.model_dump_json(indent=2) + "\n").encode()
        )
        staged.rename(output_directory)
    return admit_campaign_state(output_directory / "state.json")


def run_campaign_step(
    state_path: Path,
    case_id: str,
    producer_id: str,
    stage: CampaignStage,
    produce: Callable[[], tuple[CampaignStepOutput, ...]],
) -> AdmittedCampaignState:
    """Run and atomically record the next stage, or reuse its verified result."""
    admitted = admit_campaign_state(state_path)
    progress = next(
        (
            item
            for item in admitted.document.cases
            if (item.case_id, item.producer_id) == (case_id, producer_id)
        ),
        None,
    )
    if progress is None:
        raise ValueError("campaign state omits the requested case/producer")
    observed = tuple(item.stage for item in progress.transitions)
    if stage in observed:
        return admitted
    expected = _STAGES[len(observed)] if len(observed) < len(_STAGES) else None
    if stage != expected:
        raise ValueError(f"campaign stage must advance to {expected}")
    outputs = produce()
    require(bool(outputs), "completed campaign stage requires output artifacts")
    require(
        len({item.role for item in outputs}) == len(outputs),
        "campaign stage output roles must be unique",
    )
    allowed: dict[CampaignStage, set[CampaignArtifactRole]] = {
        "planned": set(),
        "collected": {"capture_bundle"},
        "imported": {"functional_reference", "profiler_reference"},
        "functionally_checked": {"comparison_result"},
        "timing_checked": {"model_observation", "comparison_result"},
    }
    require(
        {item.role for item in outputs} <= allowed[stage],
        "campaign stage produced an unsupported artifact role",
    )
    source_data = tuple((item, item.path.read_bytes()) for item in outputs)
    campaign = ExternalValidationCampaign.model_validate_json(
        admitted.campaign_path.read_bytes()
    )
    case = next(item for item in campaign.cases if item.case_id == case_id)
    require(
        sum(len(data) for _, data in source_data)
        <= case.budget.max_output_bytes * case.budget.repetitions,
        "campaign stage outputs exceed the case byte budget",
    )
    prior = {
        admitted.document.campaign.sha256,
        *(
            artifact.sha256
            for transition in progress.transitions
            for artifact in transition.artifacts
        ),
    }
    artifacts: list[CampaignStateArtifact] = []
    destinations: list[tuple[Path, bytes]] = []
    identity_prefix = content_digest(
        {"case_id": case_id, "producer_id": producer_id}
    )[:16]
    for output, data in source_data:
        digest = bytes_digest(data)
        suffix = output.path.suffix if output.path.suffix in (".json", ".csv") else ".bin"
        logical_path = (
            f"artifacts/{identity_prefix}/{stage}/{output.role}-{digest}{suffix}"
        )
        artifact = CampaignStateArtifact(
            artifact_id=f"{case_id}:{producer_id}:{stage}:{output.role}",
            logical_path=logical_path,
            sha256=digest,
            size_bytes=len(data),
            role=output.role,
        )
        artifacts.append(artifact)
        destinations.append((_resolve(state_path.parent, logical_path), data))
    transition = CampaignTransition(
        stage=stage,
        input_sha256s=tuple(sorted(prior)),
        artifacts=tuple(artifacts),
    )
    updated_progress = progress.model_copy(
        update={"transitions": (*progress.transitions, transition)}
    )
    updated_cases = tuple(
        updated_progress if item == progress else item
        for item in admitted.document.cases
    )
    pending = admitted.document.model_copy(
        update={"state_id": "pending", "cases": updated_cases}
    )
    updated = pending.model_copy(update={"state_id": _state_id(pending)})
    for destination, data in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            require(
                destination.read_bytes() == data,
                "hash-addressed campaign artifact path contains different bytes",
            )
        else:
            with tempfile.NamedTemporaryFile(
                prefix=".artifact-", dir=destination.parent, delete=False
            ) as handle:
                handle.write(data)
                temporary_path = Path(handle.name)
            temporary_path.replace(destination)
    with tempfile.NamedTemporaryFile(
        prefix=".state-", dir=state_path.parent, delete=False
    ) as handle:
        handle.write((updated.model_dump_json(indent=2) + "\n").encode())
        temporary_state = Path(handle.name)
    temporary_state.replace(state_path)
    return admit_campaign_state(state_path)


def _json_value(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(canonical_record(value).text))


def _flatten(prefix: str, value: JsonValue) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return {
            key: child
            for name, item in sorted(value.items())
            for key, child in _flatten(f"{prefix}.{name}" if prefix else name, item).items()
        }
    if isinstance(value, list):
        return {
            key: child
            for index, item in enumerate(value)
            for key, child in _flatten(f"{prefix}[{index}]", item).items()
        }
    return {prefix: value}


def _equivalence_fields(capture: AdmittedExternalCapture) -> dict[str, JsonValue]:
    document = capture.document
    conditions = document.conditions
    fields: dict[str, JsonValue] = {
        "source.url": document.build.source_url,
        "source.revision": document.build.revision,
        "source.snapshot_sha256": document.build.source_snapshot_sha256,
    }
    fields.update(
        _flatten(
            "build.configuration",
            cast(JsonValue, json.loads(document.build.configuration.text)),
        )
    )
    for artifact in document.build.artifacts:
        fields.update(
            _flatten(
                f"binary.{artifact.artifact_id}",
                _json_value(artifact.model_dump(mode="json")),
            )
        )
    for name in ("architecture", "profile_version"):
        metadata = getattr(conditions, name)
        fields[f"conditions.{name}"] = _json_value(metadata.model_dump(mode="json"))
    for name in ("enabled_layout", "workload", "mapping", "instrumentation"):
        metadata = getattr(conditions, name)
        fields[f"conditions.{name}.state"] = metadata.state
        if metadata.value is None:
            fields[f"conditions.{name}.reason"] = metadata.reason
        else:
            fields.update(
                _flatten(
                    f"conditions.{name}",
                    cast(JsonValue, json.loads(metadata.value.text)),
                )
            )
    fields.update(
        _flatten(
            "conditions.clocks",
            _json_value(conditions.clocks.model_dump(mode="json")),
        )
    )
    return fields


def compare_capture_equivalence(
    campaign: AdmittedExternalCampaign,
    ttsim: AdmittedExternalCapture,
    silicon: AdmittedExternalCapture,
) -> ExternalEquivalenceResult:
    """Compare material producer fields against one canonical campaign case."""
    require(ttsim.document.case_id == silicon.document.case_id, "paired captures use different cases")
    case = next(
        item
        for item in campaign.document.cases
        if item.case_id == ttsim.document.case_id
    )
    require(
        ttsim.document.adapter == "ttsim_tt_metal_v1"
        and silicon.document.adapter == "wormhole_tt_metal_profiler_v1",
        "paired captures require the named ttsim and Wormhole producers",
    )
    simulator = _equivalence_fields(ttsim)
    for name in ("architecture", "profile_version", "enabled_layout", "workload", "mapping", "instrumentation", "clocks"):
        metadata = getattr(case.conditions, name)
        base = f"conditions.{name}"
        for key in tuple(
            key
            for key in simulator
            if key == base or key.startswith((base + ".", base + "["))
        ):
            simulator.pop(key)
        if name in ("enabled_layout", "workload", "mapping", "instrumentation"):
            simulator[f"{base}.state"] = metadata.state
            if metadata.value is None:
                simulator[f"{base}.reason"] = metadata.reason
            else:
                simulator.update(
                    _flatten(
                        base,
                        cast(JsonValue, json.loads(metadata.value.text)),
                    )
                )
        elif name == "clocks":
            simulator.update(
                _flatten(base, _json_value(metadata.model_dump(mode="json")))
            )
        else:
            simulator[base] = _json_value(metadata.model_dump(mode="json"))
    ttsim_fields = _equivalence_fields(ttsim)
    silicon_fields = _equivalence_fields(silicon)
    keys = tuple(sorted(set(simulator) | set(ttsim_fields) | set(silicon_fields)))
    checks: list[EquivalenceCheck] = []
    missing = {"state": "unknown", "reason": "field absent"}
    for field in keys:
        values = (
            simulator.get(field, missing),
            ttsim_fields.get(field, missing),
            silicon_fields.get(field, missing),
        )
        passed = values[0] == values[1] == values[2]
        checks.append(
            EquivalenceCheck(
                field=field,
                outcome="pass" if passed else "blocked",
                reason=(
                    "canonical producer values match"
                    if passed
                    else f"material producer mismatch at {field}"
                ),
                values=tuple(
                    EquivalenceValue(producer=producer, value=canonical_record(value))
                    for producer, value in zip(
                        ("simulator", "ttsim", "silicon"), values, strict=True
                    )
                ),
            )
        )
    outcome = "pass" if all(item.outcome == "pass" for item in checks) else "blocked"
    identity = {
        "campaign": campaign.document_sha256,
        "case": case.case_id,
        "ttsim": ttsim.document_sha256,
        "silicon": silicon.document_sha256,
        "checks": [item.model_dump(mode="json") for item in checks],
    }
    return ExternalEquivalenceResult(
        kind="external_equivalence_result",
        schema_version=1,
        result_id="external-equivalence:" + content_digest(identity),
        campaign_sha256=campaign.document_sha256,
        case_id=case.case_id,
        ttsim_bundle_sha256=ttsim.document_sha256,
        silicon_bundle_sha256=silicon.document_sha256,
        outcome=outcome,
        checks=tuple(checks),
    )


def evaluate_functional_gate(
    case_id: str,
    actual: NormalizedObservations,
    ttsim: FunctionalReferenceConversion,
    silicon: FunctionalReferenceConversion,
    *,
    ttsim_artifact_id: str,
    silicon_artifact_id: str,
) -> PairedFunctionalGate:
    """Require both converted functional records to match before timing is eligible."""
    outcomes: list[ExternalOutcome] = []
    for producer_id, conversion, artifact_id in (
        ("ttsim-functional", ttsim, ttsim_artifact_id),
        ("wormhole-profiler", silicon, silicon_artifact_id),
    ):
        reference = conversion.reference.observations
        if reference is None:
            outcome, reason = "blocked", "converted functional observations are unavailable"
        else:
            try:
                functional_match(
                    actual,
                    reference,
                    {
                        item.reference: item.simulator
                        for item in conversion.mappings.entity_mappings
                    },
                    {
                        item.reference: item.simulator
                        for item in conversion.mappings.event_mappings
                    },
                )
                outcome, reason = "pass", "addressed effects and required causal order match"
            except (KeyError, ValueError) as exc:
                outcome, reason = "fail", str(exc) or type(exc).__name__
        outcomes.append(
            ExternalOutcome(
                stage="functional",
                required=True,
                outcome=outcome,
                reason=reason,
                executed=outcome in ("pass", "fail"),
                case_id=case_id,
                producer_id=producer_id,
                artifact_ids=(artifact_id,) if outcome == "pass" else (),
            )
        )
    eligible = all(item.outcome == "pass" for item in outcomes)
    return PairedFunctionalGate(
        kind="paired_functional_gate",
        schema_version=1,
        gate_id="functional-gate:"
        + content_digest(
            {
                "case": case_id,
                "actual": actual.observation_id,
                "references": [
                    ttsim.reference.reference_id,
                    silicon.reference.reference_id,
                ],
                "outcomes": [item.model_dump(mode="json") for item in outcomes],
            }
        ),
        case_id=case_id,
        outcomes=tuple(outcomes),
        timing_eligible=eligible,
    )


def gate_timing_outcome(
    functional: PairedFunctionalGate, timing: ExternalOutcome
) -> ExternalOutcome:
    """Discard timing evidence when either producer's functional gate did not pass."""
    if timing.stage != "timing" or timing.case_id != functional.case_id:
        raise ValueError("timing outcome disagrees with its functional gate")
    if functional.timing_eligible:
        return timing
    failed = ", ".join(
        item.producer_id or "unknown"
        for item in functional.outcomes
        if item.outcome != "pass"
    )
    return ExternalOutcome(
        stage="timing",
        required=timing.required,
        outcome="blocked",
        reason=f"timing evidence rejected because functional validation failed: {failed}",
        executed=False,
        case_id=timing.case_id,
        producer_id=timing.producer_id,
        boundary_id=timing.boundary_id,
        artifact_ids=(),
    )


def compare_external_timing(
    *,
    case_id: str,
    boundary: BoundaryMap,
    admitted: Admission,
    conditions: ReferenceConditions,
    actual: NormalizedObservations,
    reference: ValidationReference,
    evidence: EvidenceReference,
    artifact_id: str,
) -> ExternalOutcome:
    """Compare one predeclared model interval with one hardware reference."""
    if reference.reference_id != evidence.reference_id:
        raise ValueError("timing evidence identity disagrees with its reference")
    selection = CheckSelection(
        check_id=f"{case_id}:{boundary.boundary_id}:silicon-timing",
        check="silicon_timing",
        required=True,
        tier="silicon_timing",
        requirements=("EV-05",),
        reference_id=reference.reference_id,
        metrics=(boundary.comparison,),
        entity_mappings=boundary.entity_mappings,
        clock_mappings=boundary.clock_mappings,
    )
    result = compare(selection, admitted, conditions, actual, reference, evidence)
    reason = result.reason
    if reference.sample_statistics is not None:
        statistics = reference.sample_statistics
        reason += (
            f"; sample_count={statistics.sample_count}; "
            "mean_absolute_deviation_cycles="
            f"{statistics.mean_absolute_deviation_cycles}"
        )
    return ExternalOutcome(
        stage="timing",
        required=True,
        outcome=result.outcome,
        reason=reason,
        executed=result.executed,
        case_id=case_id,
        producer_id="wormhole-profiler",
        boundary_id=boundary.boundary_id,
        artifact_ids=(artifact_id,),
    )


def _report_status(outcomes: tuple[ExternalOutcome, ...]) -> ReportStatus:
    if any(item.outcome == "fail" for item in outcomes):
        return "fail"
    if not any(item.required for item in outcomes) or any(
        item.required and item.outcome != "pass" for item in outcomes
    ):
        return "incomplete"
    return "pass"


def write_external_report(
    *,
    campaign: AdmittedExternalCampaign,
    bundle_paths: tuple[Path, ...],
    artifacts: tuple[ReportArtifactInput, ...],
    outcomes: tuple[ExternalOutcome, ...],
    claim_scope: tuple[str, ...],
    limitations: tuple[str, ...],
    output_directory: Path,
) -> WrittenExternalReport:
    """Publish a portable, hash-verified report tree with explicit lineage."""
    if output_directory.exists():
        raise ValueError("external report output directory already exists")
    admitted_bundles = tuple(admit_external_capture(path) for path in bundle_paths)
    if any(
        item.document.campaign.sha256 != campaign.document_sha256
        for item in admitted_bundles
    ):
        raise ValueError("report bundle references a different campaign")
    artifact_data = tuple((item, item.path.read_bytes()) for item in artifacts)
    campaign_data = campaign.document_path.read_bytes()
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".external-report-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "report"
        staged.mkdir()
        campaign_path = staged / "campaign.json"
        campaign_path.write_bytes(campaign_data)
        for case, source in zip(
            campaign.document.cases, campaign.input_paths, strict=True
        ):
            destination = staged / case.simulator_input.logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        admit_external_campaign(campaign_path)
        campaign_identity = ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.json",
            sha256=bytes_digest(campaign_data),
            size_bytes=len(campaign_data),
        )
        bundle_identities: list[ExternalArtifactIdentity] = []
        lineage: list[ArtifactLineage] = [
            ArtifactLineage(
                artifact_id="campaign",
                derived_from=(),
                transform="campaign_authoring",
            )
        ]
        for index, admitted_bundle in enumerate(admitted_bundles):
            bundle_root = staged / "bundles" / str(index)
            bundle_root.mkdir(parents=True)
            (bundle_root / "campaign.json").write_bytes(campaign_data)
            for raw, source in zip(
                admitted_bundle.document.raw_artifacts,
                admitted_bundle.artifact_paths[1:],
                strict=True,
            ):
                destination = bundle_root / raw.logical_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            portable_bundle = admitted_bundle.document.model_copy(
                update={"campaign": campaign_identity}
            )
            bundle_data = (portable_bundle.model_dump_json(indent=2) + "\n").encode()
            bundle_path = bundle_root / "bundle.json"
            bundle_path.write_bytes(bundle_data)
            admit_external_capture(bundle_path)
            artifact_id = f"bundle:{portable_bundle.bundle_id}"
            logical_path = f"bundles/{index}/bundle.json"
            bundle_identities.append(
                ExternalArtifactIdentity(
                    artifact_id=artifact_id,
                    logical_path=logical_path,
                    sha256=bytes_digest(bundle_data),
                    size_bytes=len(bundle_data),
                )
            )
            lineage.append(
                ArtifactLineage(
                    artifact_id=artifact_id,
                    derived_from=("campaign",),
                    transform="capture_bundle_packaging",
                )
            )
        artifact_identities: list[ExternalArtifactIdentity] = []
        for index, (artifact, data) in enumerate(artifact_data):
            suffix = artifact.path.suffix if artifact.path.suffix else ".bin"
            logical_path = f"artifacts/{index}/{bytes_digest(data)}{suffix}"
            destination = staged / logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            try:
                document = parse(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                document = None
            if document is not None and document.get("kind") == "validation_reference":
                reference = ValidationReference.model_validate_json(data)
                raw_source = resolve_asset(
                    artifact.path, reference.provenance.raw_artifact.path
                )
                raw_destination = (
                    destination.parent / reference.provenance.raw_artifact.path
                ).resolve()
                if not raw_destination.is_relative_to(destination.parent.resolve()):
                    raise ValueError("reference raw artifact escapes its report package")
                if raw_destination == destination.resolve():
                    raise ValueError("reference raw artifact aliases its document")
                raw_data = raw_source.read_bytes()
                if bytes_digest(raw_data) != reference.provenance.raw_artifact.sha256:
                    raise ValueError("reference raw artifact changed before report packaging")
                raw_destination.parent.mkdir(parents=True, exist_ok=True)
                raw_destination.write_bytes(raw_data)
                import_reference(destination)
            artifact_identities.append(
                ExternalArtifactIdentity(
                    artifact_id=artifact.artifact_id,
                    logical_path=logical_path,
                    sha256=bytes_digest(data),
                    size_bytes=len(data),
                )
            )
            lineage.append(
                ArtifactLineage(
                    artifact_id=artifact.artifact_id,
                    derived_from=artifact.derived_from,
                    transform=artifact.transform,
                )
            )
        boundaries = tuple(
            boundary
            for case in campaign.document.cases
            for boundary in case.boundary_maps
        )
        identity = {
            "campaign": campaign_identity.model_dump(mode="json"),
            "bundles": [item.model_dump(mode="json") for item in bundle_identities],
            "artifacts": [item.model_dump(mode="json") for item in artifact_identities],
            "boundaries": [item.model_dump(mode="json") for item in boundaries],
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
            "claim_scope": claim_scope,
            "limitations": limitations,
        }
        report = ExternalValidationReport(
            kind="external_validation_report",
            schema_version=1,
            report_id="external-report:" + content_digest(identity),
            campaign=campaign_identity,
            bundles=tuple(bundle_identities),
            artifacts=tuple(artifact_identities),
            boundaries=boundaries,
            outcomes=outcomes,
            lineage=tuple(lineage),
            status=_report_status(outcomes),
            claim_scope=claim_scope,
            limitations=limitations,
        )
        report_path = staged / "report.json"
        report_data = (report.model_dump_json(indent=2) + "\n").encode()
        report_path.write_bytes(report_data)
        ExternalValidationReport.model_validate_json(report_data)
        staged.rename(output_directory)
    final_path = output_directory / "report.json"
    final_data = final_path.read_bytes()
    return WrittenExternalReport(
        document=ExternalValidationReport.model_validate_json(final_data),
        document_path=final_path.resolve(),
        document_sha256=bytes_digest(final_data),
    )
