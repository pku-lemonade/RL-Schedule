"""Strict contracts for portable external-validation campaigns and evidence.

These records describe work and evidence. They never execute producer input,
probe hardware, or infer missing device parameters.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self, cast

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    TypeAdapter,
    model_validator,
)

from ...validation.outcomes import CheckOutcome, ReportStatus
from .topology import Digest, Identifier, Index, PositiveInt, unique
from .validation import (
    CanonicalJSON,
    IntervalSemanticScope,
    Metadata,
    Positive,
    ReferenceConditions,
    Text,
    ValidationRecord,
    Version,
)

ProducerAdapter = Literal["ttsim_tt_metal_v1", "wormhole_tt_metal_profiler_v1"]
CaseFamily = Literal["noc_ack_roundtrip", "dram_read_return", "compute_service"]
ExternalIntervalBoundary = Literal[
    "operation_submission_to_acknowledged_completion",
    "memory_service_begin_to_end",
    "compute_resource_acquire_to_release",
]
EvidenceClassification = Literal["functional_capture", "hardware_capture"]
EvidenceGate = Literal["functional_reference", "silicon_timing"]
OutputRole = Literal["functional_record", "profiler_csv", "capture_manifest"]
ExternalStage = Literal["collection", "import", "functional", "timing", "calibration", "evaluation"]
PinnedRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]


def _portable_path(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or "\\" in value
        or ":" in value
        or str(path) == "."
    ):
        raise ValueError(f"{label} must be a normalized relative POSIX path")


def _reject_executable_json(record: CanonicalJSON, label: str) -> None:
    blocked = {"shell", "command", "command_line", "script", "executable"}

    def visit(value: JsonValue) -> None:
        if isinstance(value, dict):
            keys = {str(key).casefold() for key in value}
            if keys & blocked:
                raise ValueError(f"{label} cannot contain executable command fields")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(cast(JsonValue, json.loads(record.text)))


class ExternalArtifactIdentity(ValidationRecord):
    artifact_id: Identifier
    logical_path: Text
    sha256: Digest
    size_bytes: Index

    @model_validator(mode="after")
    def portable(self) -> Self:
        _portable_path(self.logical_path, "artifact path")
        return self


class OutputDeclaration(ValidationRecord):
    artifact_id: Identifier
    logical_path: Text
    role: OutputRole

    @model_validator(mode="after")
    def portable(self) -> Self:
        _portable_path(self.logical_path, "output path")
        return self


class BuildArtifactIdentity(ValidationRecord):
    artifact_id: Identifier
    logical_path: Text
    sha256: Digest
    size_bytes: Index
    role: Literal["host_binary", "device_binary", "source_bundle"]

    @model_validator(mode="after")
    def portable(self) -> Self:
        _portable_path(self.logical_path, "build artifact path")
        return self


class SourceBuildIdentity(ValidationRecord):
    build_id: Identifier
    source_url: Text
    revision: PinnedRevision
    source_snapshot_sha256: Digest
    configuration: CanonicalJSON
    artifacts: tuple[BuildArtifactIdentity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def immutable_build(self) -> Self:
        unique(tuple(item.artifact_id for item in self.artifacts), "build artifact identity")
        unique(tuple(item.logical_path for item in self.artifacts), "build artifact path")
        _reject_executable_json(self.configuration, "build configuration")
        return self


class ProducerDefinition(ValidationRecord):
    producer_id: Identifier
    adapter: ProducerAdapter
    build_id: Identifier


class ExternalCaseBudget(ValidationRecord):
    repetitions: PositiveInt
    warmup_repetitions: Index
    timeout_seconds: Positive
    max_output_bytes: PositiveInt

    @model_validator(mode="after")
    def finite_samples(self) -> Self:
        if self.warmup_repetitions >= self.repetitions:
            raise ValueError("warm-up repetitions must leave at least one retained run")
        return self


class RepetitionAggregation(ValidationRecord):
    repetition_ids: tuple[Identifier, ...] = Field(min_length=1)
    warmup_repetition_ids: tuple[Identifier, ...] = ()
    aggregation: Literal["none", "mean", "median"]

    @model_validator(mode="after")
    def finite_samples(self) -> Self:
        unique(self.repetition_ids, "external repetition")
        unique(self.warmup_repetition_ids, "external warm-up repetition")
        if not set(self.warmup_repetition_ids) < set(self.repetition_ids):
            raise ValueError("warm-ups must be a proper subset of campaign repetitions")
        retained = len(self.repetition_ids) - len(self.warmup_repetition_ids)
        if self.aggregation == "none" and retained != 1:
            raise ValueError("unaggregated campaign metric requires one retained run")
        return self


class ModelIntervalSelection(ValidationRecord):
    metric_id: Identifier
    boundary: ExternalIntervalBoundary
    subject_id: Identifier
    resource_id: Identifier | None
    start_event_id: Identifier
    end_event_id: Identifier
    semantic_scope: IntervalSemanticScope

    @model_validator(mode="after")
    def supported_identity(self) -> Self:
        expected: dict[ExternalIntervalBoundary, tuple[IntervalSemanticScope, bool]] = {
            "operation_submission_to_acknowledged_completion": ("acknowledged_operation", False),
            "memory_service_begin_to_end": ("memory_service", True),
            "compute_resource_acquire_to_release": ("compute_service", True),
        }
        scope, resource_required = expected[self.boundary]
        if self.semantic_scope != scope:
            raise ValueError("model interval semantic scope disagrees with its boundary")
        if resource_required != (self.resource_id is not None):
            raise ValueError("model interval resource identity disagrees with its boundary")
        if self.start_event_id == self.end_event_id:
            raise ValueError("model interval requires distinct start/end events")
        return self


class BoundaryMap(ValidationRecord):
    boundary_id: Identifier
    case_family: CaseFamily
    producer_zone: Identifier
    simulator_boundary: ExternalIntervalBoundary
    clock_domain: Identifier
    completion_scope: IntervalSemanticScope
    simulator_interval: ModelIntervalSelection
    samples: RepetitionAggregation

    @model_validator(mode="after")
    def consistent_mapping(self) -> Self:
        expected: dict[CaseFamily, tuple[ExternalIntervalBoundary, IntervalSemanticScope]] = {
            "noc_ack_roundtrip": (
                "operation_submission_to_acknowledged_completion",
                "acknowledged_operation",
            ),
            "dram_read_return": ("memory_service_begin_to_end", "memory_service"),
            "compute_service": ("compute_resource_acquire_to_release", "compute_service"),
        }
        if (self.simulator_boundary, self.completion_scope) != expected[self.case_family]:
            raise ValueError("case family uses an unsupported boundary/completion scope")
        if (
            self.simulator_interval.boundary != self.simulator_boundary
            or self.simulator_interval.semantic_scope != self.completion_scope
        ):
            raise ValueError("model interval selection disagrees with its boundary map")
        return self


class ProducerBinding(ValidationRecord):
    producer_id: Identifier
    outputs: tuple[OutputDeclaration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_outputs(self) -> Self:
        unique(tuple(item.artifact_id for item in self.outputs), "producer output identity")
        unique(tuple(item.logical_path for item in self.outputs), "producer output path")
        unique(tuple(item.role for item in self.outputs), "producer output role")
        return self


class ExternalValidationCase(ValidationRecord):
    case_id: Identifier
    family: CaseFamily
    simulator_input: ExternalArtifactIdentity
    conditions: ReferenceConditions
    boundary_maps: tuple[BoundaryMap, ...] = Field(min_length=1)
    producers: tuple[ProducerBinding, ...] = Field(min_length=1)
    budget: ExternalCaseBudget
    required_evidence: tuple[EvidenceGate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def declared_case(self) -> Self:
        unique(tuple(item.boundary_id for item in self.boundary_maps), "boundary map")
        unique(tuple(item.producer_id for item in self.producers), "case producer")
        unique(self.required_evidence, "required evidence gate")
        if any(item.case_family != self.family for item in self.boundary_maps):
            raise ValueError("boundary map case family disagrees with its case")
        for item in self.boundary_maps:
            if (
                len(item.samples.repetition_ids) != self.budget.repetitions
                or len(item.samples.warmup_repetition_ids) != self.budget.warmup_repetitions
            ):
                raise ValueError("boundary sample policy disagrees with the case budget")
        canonical = (
            self.conditions.enabled_layout,
            self.conditions.workload,
            self.conditions.mapping,
            self.conditions.instrumentation,
        )
        for name, metadata in zip(("layout", "workload", "mapping", "instrumentation"), canonical, strict=True):
            if metadata.value is not None:
                _reject_executable_json(metadata.value, f"case {name}")
        return self


class ExternalValidationCampaign(ValidationRecord):
    kind: Literal["external_validation_campaign"]
    schema_version: Version
    campaign_id: Identifier
    output_directory: Text
    max_cases: PositiveInt
    max_invocations: PositiveInt
    max_total_output_bytes: PositiveInt
    builds: tuple[SourceBuildIdentity, ...] = Field(min_length=1)
    producers: tuple[ProducerDefinition, ...] = Field(min_length=1)
    cases: tuple[ExternalValidationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bounded_campaign(self) -> Self:
        _portable_path(self.output_directory, "output directory")
        unique(tuple(item.build_id for item in self.builds), "build identity")
        unique(tuple(item.producer_id for item in self.producers), "producer identity")
        unique(tuple(item.adapter for item in self.producers), "producer adapter")
        unique(tuple(item.case_id for item in self.cases), "external case")
        unique(tuple(item.simulator_input.artifact_id for item in self.cases), "simulator input identity")
        unique(tuple(item.simulator_input.logical_path for item in self.cases), "simulator input path")
        if len(self.cases) > self.max_cases:
            raise ValueError("case count exceeds the declared campaign budget")
        builds = {item.build_id for item in self.builds}
        if any(item.build_id not in builds for item in self.producers):
            raise ValueError("producer references an unknown source/build identity")
        producers = {item.producer_id: item for item in self.producers}
        output_ids: list[str] = []
        output_paths: list[str] = []
        invocations = 0
        output_bytes = 0
        for case in self.cases:
            for binding in case.producers:
                producer = producers.get(binding.producer_id)
                if producer is None:
                    raise ValueError("case references an unknown named producer")
                roles = {item.role for item in binding.outputs}
                required = {"functional_record", "capture_manifest"}
                if producer.adapter == "wormhole_tt_metal_profiler_v1":
                    required.add("profiler_csv")
                if not required <= roles:
                    raise ValueError("producer output contract is incomplete")
                output_ids.extend(item.artifact_id for item in binding.outputs)
                output_paths.extend(item.logical_path for item in binding.outputs)
                invocations += case.budget.repetitions
                output_bytes += case.budget.max_output_bytes * case.budget.repetitions
        unique(tuple(output_ids), "campaign output identity")
        unique(tuple(output_paths), "campaign output path")
        if invocations > self.max_invocations:
            raise ValueError("producer invocation count exceeds the declared campaign budget")
        if output_bytes > self.max_total_output_bytes:
            raise ValueError("producer output budget exceeds the declared campaign budget")
        return self


class CaptureCounter(ValidationRecord):
    name: Identifier
    value: Annotated[StrictInt, Field(ge=0)]
    unit: Identifier


class CaptureEnvironment(ValidationRecord):
    host: Metadata[CanonicalJSON]
    device: Metadata[Text]
    software: Metadata[Text]
    firmware: Metadata[Text]
    clocks: Metadata[tuple[CanonicalJSON, ...]]
    enabled_layout: Metadata[CanonicalJSON]


class ExternalOutcome(ValidationRecord):
    stage: ExternalStage
    required: StrictBool
    outcome: CheckOutcome
    reason: Text
    executed: StrictBool
    case_id: Identifier | None = None
    producer_id: Identifier | None = None
    boundary_id: Identifier | None = None
    artifact_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def honest_outcome(self) -> Self:
        unique(self.artifact_ids, "outcome artifact identity")
        if self.outcome in ("pass", "fail") and not self.executed:
            raise ValueError("pass/fail external outcomes require executed work")
        if self.outcome == "not_run" and self.executed:
            raise ValueError("not-run external outcomes cannot be executed")
        if self.outcome == "pass" and not self.artifact_ids:
            raise ValueError("passing external outcomes require artifact evidence")
        return self


class ArtifactLineage(ValidationRecord):
    artifact_id: Identifier
    derived_from: tuple[Identifier, ...]
    transform: Identifier

    @model_validator(mode="after")
    def acyclic_edge(self) -> Self:
        unique(self.derived_from, "lineage parent")
        if self.artifact_id in self.derived_from:
            raise ValueError("artifact lineage cannot reference itself")
        return self


class ExternalCaptureBundle(ValidationRecord):
    kind: Literal["external_capture_bundle"]
    schema_version: Version
    bundle_id: Identifier
    campaign: ExternalArtifactIdentity
    case_id: Identifier
    producer_id: Identifier
    adapter: ProducerAdapter
    build: SourceBuildIdentity
    intended_classification: EvidenceClassification
    environment: CaptureEnvironment
    conditions: ReferenceConditions
    outcome: ExternalOutcome
    raw_artifacts: tuple[ExternalArtifactIdentity, ...]
    counters: tuple[CaptureCounter, ...]
    lineage: tuple[ArtifactLineage, ...]
    diagnostics: tuple[Text, ...]
    origin_authentication: Literal["not_authenticated"] = "not_authenticated"

    @model_validator(mode="after")
    def captured_evidence(self) -> Self:
        unique(tuple(item.artifact_id for item in self.raw_artifacts), "raw artifact identity")
        unique(tuple(item.logical_path for item in self.raw_artifacts), "raw artifact path")
        unique(tuple(item.name for item in self.counters), "capture counter")
        unique(tuple(item.artifact_id for item in self.lineage), "lineage artifact")
        artifacts = {self.campaign.artifact_id, *(item.artifact_id for item in self.raw_artifacts)}
        if any(parent not in artifacts for item in self.lineage for parent in item.derived_from):
            raise ValueError("lineage references an unknown artifact")
        if any(item.artifact_id not in artifacts for item in self.lineage):
            raise ValueError("lineage describes an unknown artifact")
        if any(item not in artifacts for item in self.outcome.artifact_ids):
            raise ValueError("capture outcome references an unknown artifact")
        if (self.adapter == "ttsim_tt_metal_v1") != (
            self.intended_classification == "functional_capture"
        ):
            raise ValueError("ttsim is functional evidence; Wormhole profiler is hardware evidence")
        if self.outcome.case_id != self.case_id or self.outcome.producer_id != self.producer_id:
            raise ValueError("capture outcome identity disagrees with the bundle")
        if self.outcome.outcome == "pass" and not self.raw_artifacts:
            raise ValueError("successful collection requires raw artifacts")
        if self.outcome.outcome != "pass" and self.intended_classification == "hardware_capture" and self.raw_artifacts:
            raise ValueError("failed or blocked hardware collection cannot carry admitted raw evidence")
        return self


class ExternalValidationReport(ValidationRecord):
    kind: Literal["external_validation_report"]
    schema_version: Version
    report_id: Identifier
    campaign: ExternalArtifactIdentity
    bundles: tuple[ExternalArtifactIdentity, ...]
    boundaries: tuple[BoundaryMap, ...]
    outcomes: tuple[ExternalOutcome, ...] = Field(min_length=1)
    lineage: tuple[ArtifactLineage, ...] = Field(min_length=1)
    status: ReportStatus
    claim_scope: tuple[Text, ...]
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def scoped_report(self) -> Self:
        unique(tuple(item.artifact_id for item in self.bundles), "report bundle identity")
        unique(tuple(item.logical_path for item in self.bundles), "report bundle path")
        unique(tuple(item.boundary_id for item in self.boundaries), "report boundary")
        unique(tuple(item.artifact_id for item in self.lineage), "report lineage artifact")
        artifacts = {self.campaign.artifact_id, *(item.artifact_id for item in self.bundles)}
        if any(item.artifact_id not in artifacts for item in self.lineage):
            raise ValueError("report lineage describes an unknown artifact")
        if any(parent not in artifacts for item in self.lineage for parent in item.derived_from):
            raise ValueError("report lineage references an unknown artifact")
        if any(item not in artifacts for outcome in self.outcomes for item in outcome.artifact_ids):
            raise ValueError("report outcome references an unknown artifact")
        expected: ReportStatus
        if any(item.outcome == "fail" for item in self.outcomes):
            expected = "fail"
        elif not any(item.required for item in self.outcomes) or any(
            item.required and item.outcome != "pass" for item in self.outcomes
        ):
            expected = "incomplete"
        else:
            expected = "pass"
        if self.status != expected:
            raise ValueError("external report status disagrees with its outcomes")
        return self


ExternalValidationDocument = Annotated[
    ExternalValidationCampaign | ExternalCaptureBundle | ExternalValidationReport,
    Field(discriminator="kind"),
]
ExternalValidationDocumentAdapter: TypeAdapter[ExternalValidationDocument] = TypeAdapter(ExternalValidationDocument)
