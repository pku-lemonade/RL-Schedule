"""Pure, fail-closed admission for external-validation documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..configs.schemas.external_validation import (
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalValidationCampaign,
    ExternalValidationDocument,
    ExternalValidationDocumentAdapter,
)
from .identity import bytes_digest


@dataclass(frozen=True)
class AdmittedExternalCampaign:
    document: ExternalValidationCampaign
    document_path: Path
    input_paths: tuple[Path, ...]
    output_paths: tuple[Path, ...]
    document_sha256: str


@dataclass(frozen=True)
class AdmittedExternalCapture:
    document: ExternalCaptureBundle
    document_path: Path
    artifact_paths: tuple[Path, ...]
    document_sha256: str


def load_external_document(path: Path) -> ExternalValidationDocument:
    """Parse external contracts without launching processes or probing devices."""
    return ExternalValidationDocumentAdapter.validate_json(path.read_bytes())


def _resolve_within(root: Path, logical_path: str, label: str) -> Path:
    resolved_root = root.resolve()
    resolved = (resolved_root / logical_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{label} escapes its declaring directory")
    return resolved


def _verify_artifact(root: Path, artifact: ExternalArtifactIdentity) -> Path:
    path = _resolve_within(root, artifact.logical_path, f"artifact {artifact.artifact_id}")
    data = path.read_bytes()
    if len(data) != artifact.size_bytes:
        raise ValueError(f"artifact size mismatch: {artifact.logical_path}")
    if bytes_digest(data) != artifact.sha256:
        raise ValueError(f"artifact hash mismatch: {artifact.logical_path}")
    return path


def admit_external_campaign(path: Path) -> AdmittedExternalCampaign:
    """Resolve and verify an entire finite campaign before any work can run."""
    data = path.read_bytes()
    document = ExternalValidationCampaign.model_validate_json(data)
    root = path.resolve().parent
    inputs = tuple(_verify_artifact(root, case.simulator_input) for case in document.cases)
    output_root = _resolve_within(root, document.output_directory, "output directory")
    outputs = tuple(
        _resolve_within(output_root, output.logical_path, f"output {output.artifact_id}")
        for case in document.cases
        for producer in case.producers
        for output in producer.outputs
    )
    protected = {path.resolve(), *inputs}
    if any(output in protected for output in outputs):
        raise ValueError("campaign output cannot replace its document or a declared input")
    if len(set(outputs)) != len(outputs):
        raise ValueError("campaign outputs resolve to duplicate paths")
    return AdmittedExternalCampaign(
        document=document,
        document_path=path.resolve(),
        input_paths=inputs,
        output_paths=outputs,
        document_sha256=bytes_digest(data),
    )


def admit_external_capture(path: Path) -> AdmittedExternalCapture:
    """Verify bundle lineage bytes without interpreting or executing raw data."""
    data = path.read_bytes()
    document = ExternalCaptureBundle.model_validate_json(data)
    root = path.resolve().parent
    campaign_path = _verify_artifact(root, document.campaign)
    campaign = ExternalValidationCampaign.model_validate_json(campaign_path.read_bytes())
    case = next((item for item in campaign.cases if item.case_id == document.case_id), None)
    producer = next((item for item in campaign.producers if item.producer_id == document.producer_id), None)
    if case is None or not any(item.producer_id == document.producer_id for item in case.producers):
        raise ValueError("capture case/producer is absent from the admitted campaign")
    if producer is None or producer.adapter != document.adapter:
        raise ValueError("capture producer adapter disagrees with the admitted campaign")
    build = next((item for item in campaign.builds if item.build_id == producer.build_id), None)
    if build is None or build != document.build:
        raise ValueError("capture source/build identity disagrees with the admitted campaign")
    paths = (campaign_path, *(
        _verify_artifact(root, artifact) for artifact in document.raw_artifacts
    ))
    if len(set(paths)) != len(paths):
        raise ValueError("capture artifacts resolve to duplicate paths")
    return AdmittedExternalCapture(
        document=document,
        document_path=path.resolve(),
        artifact_paths=paths,
        document_sha256=bytes_digest(data),
    )
