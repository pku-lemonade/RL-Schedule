"""Pure, fail-closed admission for external-validation documents."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..configs.schemas.external_validation import (
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalValidationCampaign,
    ExternalValidationDocument,
    ExternalValidationDocumentAdapter,
    ModelIntervalSelection,
    RepetitionAggregation,
)
from ..configs.schemas.validation import (
    IntervalSampleIdentity,
    MetricObservation,
    NormalizedObservations,
    Number,
)
from .adapters import Admission
from .identity import bytes_digest, content_digest
from .normalize import normalize


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


@dataclass(frozen=True)
class ModelRepetitionResult:
    observation: NormalizedObservations
    repetitions: tuple[NormalizedObservations, ...]
    sample_values: tuple[Number, ...]
    mean_absolute_deviation: float


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
    binding = next(item for item in case.producers if item.producer_id == document.producer_id)
    declarations = {item.artifact_id: item for item in binding.outputs}
    if document.intended_classification == "hardware_capture" and document.outcome.outcome == "pass":
        if any(item.artifact_id not in declarations for item in document.raw_artifacts):
            raise ValueError("hardware capture contains an undeclared producer artifact")
        required_roles = {"functional_record", "profiler_csv", "capture_manifest"}
        captured_roles = {
            declarations[item.artifact_id].role for item in document.raw_artifacts
        }
        if captured_roles != required_roles:
            raise ValueError("successful hardware capture requires every declared output role")
        if len(document.profiler_selections) != len(case.boundary_maps):
            raise ValueError("hardware capture requires one profiler selection per boundary")
        for boundary in case.boundary_maps:
            selection = next(
                (
                    item
                    for item in document.profiler_selections
                    if item.zone == boundary.producer_zone
                ),
                None,
            )
            if selection is None:
                raise ValueError("hardware capture omits a declared profiler zone")
            try:
                run_ids = tuple(int(item) for item in boundary.samples.repetition_ids)
                warmups = tuple(
                    int(item) for item in boundary.samples.warmup_repetition_ids
                )
            except ValueError as exc:
                raise ValueError("profiler repetition identities must be decimal integers") from exc
            if (
                selection.boundary != boundary.simulator_boundary
                or selection.clock_domain != boundary.clock_domain
                or selection.metric_id != boundary.simulator_interval.metric_id
                or selection.run_ids != run_ids
                or selection.warmup_run_ids != warmups
                or selection.aggregation != boundary.samples.aggregation
            ):
                raise ValueError("profiler selection disagrees with its campaign boundary")
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


def _selected_interval(
    observations: NormalizedObservations,
    selection: ModelIntervalSelection,
) -> MetricObservation:
    metric = next((item for item in observations.metrics if item.metric_id == selection.metric_id), None)
    if metric is None:
        raise ValueError(f"selected model interval is absent: {selection.metric_id}")
    identity = metric.interval
    if (
        metric.window.boundary != selection.boundary
        or metric.completion_scope != "interval"
        or identity is None
        or identity.semantic_scope != selection.semantic_scope
        or identity.subject_id != selection.subject_id
        or identity.resource_id != selection.resource_id
        or len(identity.samples) != 1
        or identity.samples[0].start_event_id != selection.start_event_id
        or identity.samples[0].end_event_id != selection.end_event_id
    ):
        raise ValueError("selected model interval identity does not match normalized observations")
    return metric


def execute_model_repetitions(
    admitted: Admission,
    selection: ModelIntervalSelection,
    samples: RepetitionAggregation,
) -> ModelRepetitionResult:
    """Execute every declared model run and aggregate only retained interval samples."""
    observations: list[NormalizedObservations] = []
    metrics: list[MetricObservation] = []
    for _repetition_id in samples.repetition_ids:
        raw_results = admitted.execute(())
        if not raw_results:
            raise ValueError("model repetition returned no result")
        observation = normalize(admitted, raw_results[-1])
        if observation.execution != "complete":
            raise ValueError("model repetition did not complete")
        observations.append(observation)
        metrics.append(_selected_interval(observation, selection))
    warmups = set(samples.warmup_repetition_ids)
    retained = [
        (repetition_id, observation, metric)
        for repetition_id, observation, metric in zip(
            samples.repetition_ids, observations, metrics, strict=True
        )
        if repetition_id not in warmups
    ]
    values = tuple(Fraction(str(metric.value)) for _, _, metric in retained)
    ordered = sorted(values)
    if samples.aggregation == "mean":
        aggregate = sum(values, Fraction()) / len(values)
    elif samples.aggregation == "median":
        middle = len(ordered) // 2
        aggregate = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    else:
        aggregate = values[0]
    numeric: Number = aggregate.numerator if aggregate.denominator == 1 else float(aggregate)
    mean = sum(values, Fraction()) / len(values)
    dispersion = float(sum((abs(value - mean) for value in values), Fraction()) / len(values))
    base_observation = retained[0][1]
    base = retained[0][2]
    if base.interval is None:
        raise ValueError("selected interval lost its semantic identity")
    aggregate_events = list(base_observation.events)
    aggregate_samples: list[IntervalSampleIdentity] = []
    for repetition_id, observation, metric in retained:
        if metric.interval is None:
            raise ValueError("selected interval lost its semantic identity")
        sample = metric.interval.samples[0]
        if len(retained) == 1:
            aggregate_samples.append(sample.model_copy(update={"repetition_id": repetition_id}))
            continue
        event_records = {event.event_id: event for event in observation.events}
        start_id = f"repetition:{repetition_id}:{sample.start_event_id}"
        end_id = f"repetition:{repetition_id}:{sample.end_event_id}"
        aggregate_events.extend((
            event_records[sample.start_event_id].model_copy(update={"event_id": start_id}),
            event_records[sample.end_event_id].model_copy(update={"event_id": end_id}),
        ))
        aggregate_samples.append(sample.model_copy(update={
            "repetition_id": repetition_id,
            "start_event_id": start_id,
            "end_event_id": end_id,
        }))
    aggregate_metric = base.model_copy(update={
        "value": numeric,
        "denominator": "one retained run" if samples.aggregation == "none" else f"{samples.aggregation} of retained runs",
        "window": base.window.model_copy(update={
            "excluded_warmups": samples.warmup_repetition_ids,
            "repetitions": len(retained),
            "aggregation": samples.aggregation,
        }),
        "interval": base.interval.model_copy(update={
            "samples": tuple(aggregate_samples),
        }),
    })
    source_digest = content_digest({
        "repetition_ids": samples.repetition_ids,
        "source_result_sha256": tuple(item.source_result_sha256 for item in observations),
    })
    aggregate_observation = base_observation.model_copy(update={
        "observation_id": "aggregate:" + source_digest,
        "source_result_sha256": source_digest,
        "events": tuple(aggregate_events),
        "metrics": (aggregate_metric,),
    })
    return ModelRepetitionResult(
        observation=aggregate_observation,
        repetitions=tuple(observations),
        sample_values=tuple(
            value.numerator if value.denominator == 1 else float(value) for value in values
        ),
        mean_absolute_deviation=dispersion,
    )
