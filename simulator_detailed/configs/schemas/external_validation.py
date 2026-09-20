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
    IdentifierMapping,
    IntervalSemanticScope,
    Metadata,
    MetricPolicy,
    Positive,
    ProfilerSelection,
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
CampaignStage = Literal[
    "planned", "collected", "imported", "functionally_checked", "timing_checked"
]
CampaignArtifactRole = Literal[
    "capture_bundle",
    "functional_reference",
    "profiler_reference",
    "model_observation",
    "comparison_result",
]
OutputRole = Literal["functional_record", "profiler_csv", "capture_manifest"]
ExternalStage = Literal[
    "collection", "import", "functional", "timing", "calibration", "evaluation"
]
PinnedRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
TTSimRecipe = Literal[
    "noc_ack_roundtrip_v1",
    "dram_read_return_v1",
    "compute_service_v1",
]
CaptureKitFileRole = Literal[
    "campaign",
    "case_input",
    "host_source",
    "device_source",
    "build_file",
    "runtime_config",
]
FunctionalEntityRole = Literal["endpoint", "resource", "worker", "job", "transfer"]
HexPayload = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{2})+$")]

TTSIM_SOURCE_URL = "https://github.com/tenstorrent/ttsim"
TTSIM_PINNED_REVISION = "40bb1a2ad6a755279c4628ddc65e30b10721fdef"
TTSIM_SOURCE_SNAPSHOT_SHA256 = (
    "92d33ef15728f17ed5488d28d24dac13550ef58d3b2da16c9fa8a63bd6bb69d0"
)


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
        unique(
            tuple(item.artifact_id for item in self.artifacts),
            "build artifact identity",
        )
        unique(
            tuple(item.logical_path for item in self.artifacts), "build artifact path"
        )
        _reject_executable_json(self.configuration, "build configuration")
        return self


class ProducerDefinition(ValidationRecord):
    producer_id: Identifier
    adapter: ProducerAdapter
    build_id: Identifier


class PinnedSourceIdentity(ValidationRecord):
    source_url: Text
    revision: PinnedRevision
    source_snapshot_sha256: Digest


class CaptureKitFile(ValidationRecord):
    artifact_id: Identifier
    logical_path: Text
    sha256: Digest
    size_bytes: Index
    role: CaptureKitFileRole

    @model_validator(mode="after")
    def portable(self) -> Self:
        _portable_path(self.logical_path, "capture-kit file")
        return self


class CaptureEnvironmentVariable(ValidationRecord):
    name: Literal[
        "TT_METAL_HOME",
        "TT_METAL_SIMULATOR",
        "TT_METAL_SLOW_DISPATCH_MODE",
        "TT_METAL_DISABLE_SFPLOADMACRO",
    ]
    value: Text

    @model_validator(mode="after")
    def fixed_value(self) -> Self:
        expected = {
            "TT_METAL_HOME": "vendor/tt-metal",
            "TT_METAL_SIMULATOR": "runtime/ttsim/libttsim_wh.so",
            "TT_METAL_SLOW_DISPATCH_MODE": "1",
            "TT_METAL_DISABLE_SFPLOADMACRO": "1",
        }
        if self.value != expected[self.name]:
            raise ValueError("capture-kit environment uses an unsupported value")
        return self


class TTSimCaptureInvocation(ValidationRecord):
    invocation_id: Identifier
    recipe: TTSimRecipe
    case_id: Identifier
    argv: tuple[Text, ...] = Field(min_length=17, max_length=17)
    environment: tuple[CaptureEnvironmentVariable, ...] = Field(
        min_length=4, max_length=4
    )
    repetition_ids: tuple[Identifier, ...] = Field(min_length=1)
    warmup_repetition_ids: tuple[Identifier, ...] = ()
    timeout_seconds: Positive
    max_output_bytes: PositiveInt
    outputs: tuple[OutputDeclaration, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def fixed_arguments(self) -> Self:
        unique(self.repetition_ids, "capture-kit repetition")
        unique(self.warmup_repetition_ids, "capture-kit warm-up repetition")
        if not set(self.warmup_repetition_ids) < set(self.repetition_ids):
            raise ValueError(
                "capture-kit warm-ups must be a proper subset of repetitions"
            )
        expected_flags = (
            "--recipe",
            "--input",
            "--functional-output",
            "--manifest-output",
            "--repetitions",
            "--warmup-repetitions",
            "--timeout-seconds",
            "--max-output-bytes",
        )
        if (
            self.argv[0] != "bin/wormhole_external_validation"
            or self.argv[1::2] != expected_flags
        ):
            raise ValueError("capture-kit argv does not use the fixed named interface")
        if self.argv[2] != self.recipe:
            raise ValueError("capture-kit argv recipe disagrees with its typed recipe")
        for index in (4, 6, 8):
            _portable_path(self.argv[index], "capture-kit argv path")
        if self.argv[10] != str(len(self.repetition_ids)):
            raise ValueError(
                "capture-kit repetition argv disagrees with its finite budget"
            )
        if self.argv[12] != str(len(self.warmup_repetition_ids)):
            raise ValueError(
                "capture-kit warm-up argv disagrees with its finite budget"
            )
        if self.argv[14] != format(self.timeout_seconds, "g"):
            raise ValueError(
                "capture-kit timeout argv disagrees with its finite budget"
            )
        if self.argv[16] != str(self.max_output_bytes):
            raise ValueError("capture-kit output argv disagrees with its finite budget")
        if any(
            any(
                token in value
                for token in ("\n", "\r", "`", "$(", ";", "&&", "||", "|")
            )
            for value in self.argv
        ):
            raise ValueError("capture-kit argv cannot contain evaluated shell text")
        variables = {item.name for item in self.environment}
        if variables != {
            "TT_METAL_HOME",
            "TT_METAL_SIMULATOR",
            "TT_METAL_SLOW_DISPATCH_MODE",
            "TT_METAL_DISABLE_SFPLOADMACRO",
        }:
            raise ValueError("capture-kit environment is incomplete")
        if {item.role for item in self.outputs} != {
            "functional_record",
            "capture_manifest",
        }:
            raise ValueError("ttsim capture-kit output contract is incomplete")
        paths = {item.logical_path for item in self.outputs}
        if paths != {self.argv[6], self.argv[8]}:
            raise ValueError("capture-kit argv disagrees with its output contract")
        return self


class TTSimCaptureKitManifest(ValidationRecord):
    kind: Literal["ttsim_capture_kit"]
    schema_version: Version
    kit_id: Identifier
    campaign: ExternalArtifactIdentity
    campaign_id: Identifier
    producer: ProducerDefinition
    build: SourceBuildIdentity
    binary_manifest: tuple[BuildArtifactIdentity, ...] = Field(min_length=1)
    ttsim_source: PinnedSourceIdentity
    files: tuple[CaptureKitFile, ...] = Field(min_length=1)
    invocations: tuple[TTSimCaptureInvocation, ...] = Field(min_length=1)
    max_invocations: PositiveInt
    max_total_output_bytes: PositiveInt

    @model_validator(mode="after")
    def portable_bounded_kit(self) -> Self:
        if self.producer.adapter != "ttsim_tt_metal_v1":
            raise ValueError("capture kit requires the named ttsim adapter")
        if (
            self.producer.build_id != self.build.build_id
            or self.binary_manifest != self.build.artifacts
        ):
            raise ValueError("capture-kit source/build/binary manifests disagree")
        if self.ttsim_source != PinnedSourceIdentity(
            source_url=TTSIM_SOURCE_URL,
            revision=TTSIM_PINNED_REVISION,
            source_snapshot_sha256=TTSIM_SOURCE_SNAPSHOT_SHA256,
        ):
            raise ValueError("capture kit requires the pinned ttsim source identity")
        unique(
            tuple(item.artifact_id for item in self.files), "capture-kit file identity"
        )
        unique(tuple(item.logical_path for item in self.files), "capture-kit file path")
        unique(
            tuple(item.invocation_id for item in self.invocations),
            "capture-kit invocation",
        )
        unique(tuple(item.case_id for item in self.invocations), "capture-kit case")
        if len(self.invocations) > self.max_invocations:
            raise ValueError("capture-kit invocation count exceeds its budget")
        output_budget = sum(
            item.max_output_bytes * len(item.repetition_ids)
            for item in self.invocations
        )
        if output_budget > self.max_total_output_bytes:
            raise ValueError("capture-kit output bytes exceed its budget")
        return self


class WormholeDeviceSelection(ValidationRecord):
    worker_id: Identifier
    device_index: Index
    pcie_slot: Text
    architecture: Literal["wormhole_b0"]


class WormholeProfilerEnvironmentVariable(ValidationRecord):
    name: Literal["TT_METAL_DEVICE_PROFILER", "TT_METAL_SLOW_DISPATCH_MODE"]
    value: Literal["1"]


class WormholeCollectionPlan(ValidationRecord):
    kind: Literal["wormhole_collection_plan"]
    schema_version: Version
    plan_id: Identifier
    campaign: ExternalArtifactIdentity
    campaign_id: Identifier
    case_id: Identifier
    producer: ProducerDefinition
    build: SourceBuildIdentity
    binary_manifest: tuple[BuildArtifactIdentity, ...] = Field(min_length=1)
    selection: WormholeDeviceSelection
    conditions: ReferenceConditions
    argv: tuple[Text, ...] = Field(min_length=23, max_length=23)
    environment: tuple[WormholeProfilerEnvironmentVariable, ...] = Field(
        min_length=2, max_length=2
    )
    repetition_ids: tuple[Identifier, ...] = Field(min_length=1)
    warmup_repetition_ids: tuple[Identifier, ...] = ()
    timeout_seconds: Positive
    max_output_bytes: PositiveInt
    outputs: tuple[OutputDeclaration, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def fixed_collection(self) -> Self:
        if self.producer.adapter != "wormhole_tt_metal_profiler_v1":
            raise ValueError("collection plan requires the named Wormhole profiler adapter")
        if self.producer.build_id != self.build.build_id or self.binary_manifest != self.build.artifacts:
            raise ValueError("collection-plan source/build/binary manifests disagree")
        if (
            self.conditions.architecture.value is not None
            and self.conditions.architecture.value != self.selection.architecture
        ):
            raise ValueError("collection-plan architecture disagrees with device selection")
        unique(self.repetition_ids, "Wormhole collection repetition")
        unique(self.warmup_repetition_ids, "Wormhole collection warm-up")
        if not set(self.warmup_repetition_ids) < set(self.repetition_ids):
            raise ValueError("Wormhole collection warm-ups must be a proper subset of repetitions")
        expected_flags = (
            "--recipe", "--input", "--functional-output", "--profiler-output", "--manifest-output",
            "--repetitions", "--warmup-repetitions", "--timeout-seconds", "--max-output-bytes",
            "--device-index", "--pcie-slot",
        )
        host = next((item for item in self.binary_manifest if item.role == "host_binary"), None)
        if host is None or self.argv[0] != host.logical_path or self.argv[1::2] != expected_flags:
            raise ValueError("Wormhole collector argv does not use the fixed named interface")
        for index in (0, 4, 6, 8, 10):
            _portable_path(self.argv[index], "Wormhole collector argv path")
        if self.argv[12] != str(len(self.repetition_ids)):
            raise ValueError("Wormhole collection repetition argv disagrees with its budget")
        if self.argv[14] != str(len(self.warmup_repetition_ids)):
            raise ValueError("Wormhole collection warm-up argv disagrees with its budget")
        if self.argv[16] != format(self.timeout_seconds, "g") or self.argv[18] != str(self.max_output_bytes):
            raise ValueError("Wormhole collection argv disagrees with its finite budget")
        if self.argv[20] != str(self.selection.device_index) or self.argv[22] != self.selection.pcie_slot:
            raise ValueError("Wormhole collection argv disagrees with explicit device selection")
        if {item.name for item in self.environment} != {
            "TT_METAL_DEVICE_PROFILER", "TT_METAL_SLOW_DISPATCH_MODE",
        }:
            raise ValueError("Wormhole profiler environment is incomplete")
        if {item.role for item in self.outputs} != {
            "functional_record", "profiler_csv", "capture_manifest",
        }:
            raise ValueError("Wormhole collection output contract is incomplete")
        if {item.logical_path for item in self.outputs} != {self.argv[6], self.argv[8], self.argv[10]}:
            raise ValueError("Wormhole collection argv disagrees with its output contract")
        return self


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
            "operation_submission_to_acknowledged_completion": (
                "acknowledged_operation",
                False,
            ),
            "memory_service_begin_to_end": ("memory_service", True),
            "compute_resource_acquire_to_release": ("compute_service", True),
        }
        scope, resource_required = expected[self.boundary]
        if self.semantic_scope != scope:
            raise ValueError(
                "model interval semantic scope disagrees with its boundary"
            )
        if resource_required != (self.resource_id is not None):
            raise ValueError(
                "model interval resource identity disagrees with its boundary"
            )
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
    comparison: MetricPolicy
    clock_mappings: tuple[IdentifierMapping, ...] = ()
    entity_mappings: tuple[IdentifierMapping, ...] = ()

    @model_validator(mode="after")
    def consistent_mapping(self) -> Self:
        expected: dict[
            CaseFamily, tuple[ExternalIntervalBoundary, IntervalSemanticScope]
        ] = {
            "noc_ack_roundtrip": (
                "operation_submission_to_acknowledged_completion",
                "acknowledged_operation",
            ),
            "dram_read_return": ("memory_service_begin_to_end", "memory_service"),
            "compute_service": (
                "compute_resource_acquire_to_release",
                "compute_service",
            ),
        }
        if (self.simulator_boundary, self.completion_scope) != expected[
            self.case_family
        ]:
            raise ValueError(
                "case family uses an unsupported boundary/completion scope"
            )
        if (
            self.simulator_interval.boundary != self.simulator_boundary
            or self.simulator_interval.semantic_scope != self.completion_scope
        ):
            raise ValueError("model interval selection disagrees with its boundary map")
        if (
            self.comparison.metric_id != self.simulator_interval.metric_id
            or self.comparison.boundary != self.simulator_boundary
        ):
            raise ValueError("comparison policy disagrees with its boundary map")
        for mappings in (self.clock_mappings, self.entity_mappings):
            unique(tuple(item.reference for item in mappings), "reference mapping")
            unique(tuple(item.simulator for item in mappings), "simulator mapping")
        return self


class ProducerBinding(ValidationRecord):
    producer_id: Identifier
    outputs: tuple[OutputDeclaration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_outputs(self) -> Self:
        unique(
            tuple(item.artifact_id for item in self.outputs), "producer output identity"
        )
        unique(
            tuple(item.logical_path for item in self.outputs), "producer output path"
        )
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
                or len(item.samples.warmup_repetition_ids)
                != self.budget.warmup_repetitions
            ):
                raise ValueError(
                    "boundary sample policy disagrees with the case budget"
                )
        canonical = (
            self.conditions.enabled_layout,
            self.conditions.workload,
            self.conditions.mapping,
            self.conditions.instrumentation,
        )
        for name, metadata in zip(
            ("layout", "workload", "mapping", "instrumentation"), canonical, strict=True
        ):
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
        unique(
            tuple(item.simulator_input.artifact_id for item in self.cases),
            "simulator input identity",
        )
        unique(
            tuple(item.simulator_input.logical_path for item in self.cases),
            "simulator input path",
        )
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
            raise ValueError(
                "producer invocation count exceeds the declared campaign budget"
            )
        if output_bytes > self.max_total_output_bytes:
            raise ValueError(
                "producer output budget exceeds the declared campaign budget"
            )
        return self


class ProducerFunctionalEntity(ValidationRecord):
    entity_id: Identifier
    role: FunctionalEntityRole
    simulator_id: Identifier
    physical_owner: Identifier | None = None
    fabric_id: Index | None = None


class ProducerFunctionalCounter(ValidationRecord):
    name: Identifier
    value: Index
    unit: Literal["bytes", "work", "count"]
    scope: Literal["planned", "observed"]


class ProducerFunctionalEvent(ValidationRecord):
    event_id: Identifier
    action: Identifier
    subject_id: Identifier
    sequence: Index
    simulator_event_id: Identifier
    counters: tuple[ProducerFunctionalCounter, ...] = ()

    @model_validator(mode="after")
    def unique_counters(self) -> Self:
        unique(
            tuple((item.name, item.scope) for item in self.counters),
            "producer event counter",
        )
        return self


class ProducerFunctionalEffect(ValidationRecord):
    effect_id: Identifier
    destination_id: Identifier
    resource_id: Identifier
    offset_bytes: Index
    size_bytes: PositiveInt
    count: PositiveInt
    visibility_event: Identifier
    simulator_effect_id: Identifier


class ProducerFunctionalRepetition(ValidationRecord):
    repetition_id: Identifier
    status: Literal["pass", "fail"]
    completion_marker: Literal["WORMHOLE_EXTERNAL_COMPLETE_V1"]
    sentinel_algorithm: Literal["sha256"]
    sentinel_payload_hex: HexPayload
    sentinel_sha256: Digest
    events: tuple[ProducerFunctionalEvent, ...] = Field(min_length=2)
    effects: tuple[ProducerFunctionalEffect, ...]

    @model_validator(mode="after")
    def ordered_record(self) -> Self:
        unique(tuple(item.event_id for item in self.events), "producer event")
        unique(tuple(item.sequence for item in self.events), "producer event sequence")
        unique(tuple(item.effect_id for item in self.effects), "producer effect")
        if tuple(item.sequence for item in self.events) != tuple(
            sorted(item.sequence for item in self.events)
        ):
            raise ValueError(
                "producer events must be serialized in causal sequence order"
            )
        event_ids = {item.event_id for item in self.events}
        if any(item.visibility_event not in event_ids for item in self.effects):
            raise ValueError("producer effect references an unknown visibility event")
        return self


class ProducerFunctionalRecord(ValidationRecord):
    kind: Literal["tt_metal_functional_record"]
    schema_version: Version
    case_id: Identifier
    case_family: CaseFamily
    producer_id: Identifier
    adapter: ProducerAdapter
    build_id: Identifier
    input_artifact: ExternalArtifactIdentity
    binary_artifacts: tuple[BuildArtifactIdentity, ...] = Field(min_length=1)
    workload: CanonicalJSON
    mapping: CanonicalJSON
    enabled_layout: CanonicalJSON
    instrumentation: CanonicalJSON
    entities: tuple[ProducerFunctionalEntity, ...] = Field(min_length=1)
    repetitions: tuple[ProducerFunctionalRepetition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_functional_record(self) -> Self:
        unique(tuple(item.entity_id for item in self.entities), "producer entity")
        unique(
            tuple(item.simulator_id for item in self.entities),
            "producer entity mapping",
        )
        unique(
            tuple(item.repetition_id for item in self.repetitions),
            "producer repetition",
        )
        entities = {item.entity_id for item in self.entities}
        event_ids: list[str] = []
        effect_ids: list[str] = []
        for repetition in self.repetitions:
            event_ids.extend(item.event_id for item in repetition.events)
            effect_ids.extend(item.effect_id for item in repetition.effects)
            if any(item.subject_id not in entities for item in repetition.events):
                raise ValueError("producer event references an unknown entity")
            if any(
                item.destination_id not in entities or item.resource_id not in entities
                for item in repetition.effects
            ):
                raise ValueError("producer effect references an unknown entity")
        unique(tuple(event_ids), "producer event across repetitions")
        unique(tuple(effect_ids), "producer effect across repetitions")
        for label, record in (
            ("producer workload", self.workload),
            ("producer mapping", self.mapping),
            ("producer enabled layout", self.enabled_layout),
            ("producer instrumentation", self.instrumentation),
        ):
            _reject_executable_json(record, label)
        return self


class FunctionalMappingManifest(ValidationRecord):
    kind: Literal["functional_identifier_mappings"]
    schema_version: Version
    reference_id: Identifier
    source_artifact_sha256: Digest
    entity_mappings: tuple[IdentifierMapping, ...] = Field(min_length=1)
    event_mappings: tuple[IdentifierMapping, ...] = Field(min_length=1)
    effect_mappings: tuple[IdentifierMapping, ...]

    @model_validator(mode="after")
    def unique_reference_ids(self) -> Self:
        for label, items in (
            ("entity mapping", self.entity_mappings),
            ("event mapping", self.event_mappings),
            ("effect mapping", self.effect_mappings),
        ):
            unique(tuple(item.reference for item in items), label)
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


class WormholeWorkerResult(ValidationRecord):
    kind: Literal["wormhole_worker_result"]
    schema_version: Version
    plan_id: Identifier
    campaign_id: Identifier
    case_id: Identifier
    producer_id: Identifier
    build_id: Identifier
    selection: WormholeDeviceSelection
    environment: CaptureEnvironment
    conditions: ReferenceConditions
    profiler_selections: tuple[ProfilerSelection, ...] = Field(min_length=1)
    counters: tuple[CaptureCounter, ...]
    diagnostics: tuple[Text, ...]

    @model_validator(mode="after")
    def explicit_worker_identity(self) -> Self:
        unique(tuple(item.name for item in self.counters), "worker-result counter")
        unique(
            tuple(
                (
                    item.device,
                    item.core_x,
                    item.core_y,
                    item.risc,
                    item.zone,
                    item.source_file,
                    item.source_line,
                )
                for item in self.profiler_selections
            ),
            "worker-result profiler source",
        )
        if (
            self.conditions.architecture.value is not None
            and self.conditions.architecture.value != self.selection.architecture
        ):
            raise ValueError("worker architecture disagrees with explicit device selection")
        # The pinned TT-Metal CSV labels this field "PCIe slot" but emits the
        # numeric chip_id. Accept the earlier BDF representation as a v1
        # compatibility form while binding either value to the selected device.
        profiler_devices = {
            str(self.selection.device_index),
            self.selection.pcie_slot,
        }
        if any(item.device not in profiler_devices for item in self.profiler_selections):
            raise ValueError("profiler device disagrees with explicit device selection")
        return self


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
    profiler_selections: tuple[ProfilerSelection, ...] = ()
    counters: tuple[CaptureCounter, ...]
    lineage: tuple[ArtifactLineage, ...]
    diagnostics: tuple[Text, ...]
    origin_authentication: Literal["not_authenticated"] = "not_authenticated"

    @model_validator(mode="after")
    def captured_evidence(self) -> Self:
        unique(
            tuple(item.artifact_id for item in self.raw_artifacts),
            "raw artifact identity",
        )
        unique(
            tuple(item.logical_path for item in self.raw_artifacts), "raw artifact path"
        )
        unique(tuple(item.name for item in self.counters), "capture counter")
        unique(
            tuple(
                (
                    item.device,
                    item.core_x,
                    item.core_y,
                    item.risc,
                    item.zone,
                    item.source_file,
                    item.source_line,
                )
                for item in self.profiler_selections
            ),
            "profiler source selection",
        )
        unique(tuple(item.artifact_id for item in self.lineage), "lineage artifact")
        artifacts = {
            self.campaign.artifact_id,
            *(item.artifact_id for item in self.raw_artifacts),
        }
        if any(
            parent not in artifacts
            for item in self.lineage
            for parent in item.derived_from
        ):
            raise ValueError("lineage references an unknown artifact")
        if any(item.artifact_id not in artifacts for item in self.lineage):
            raise ValueError("lineage describes an unknown artifact")
        if any(item not in artifacts for item in self.outcome.artifact_ids):
            raise ValueError("capture outcome references an unknown artifact")
        if (self.adapter == "ttsim_tt_metal_v1") != (
            self.intended_classification == "functional_capture"
        ):
            raise ValueError(
                "ttsim is functional evidence; Wormhole profiler is hardware evidence"
            )
        if self.profiler_selections and self.adapter != "wormhole_tt_metal_profiler_v1":
            raise ValueError("only the Wormhole profiler adapter can select profiler rows")
        if (
            self.outcome.case_id != self.case_id
            or self.outcome.producer_id != self.producer_id
        ):
            raise ValueError("capture outcome identity disagrees with the bundle")
        if self.outcome.outcome == "pass" and not self.raw_artifacts:
            raise ValueError("successful collection requires raw artifacts")
        if (
            self.outcome.outcome != "pass"
            and self.intended_classification == "hardware_capture"
            and self.raw_artifacts
        ):
            raise ValueError(
                "failed or blocked hardware collection cannot carry admitted raw evidence"
            )
        return self


class CampaignStateArtifact(ExternalArtifactIdentity):
    role: CampaignArtifactRole


class CampaignTransition(ValidationRecord):
    stage: CampaignStage
    input_sha256s: tuple[Digest, ...] = Field(min_length=1)
    artifacts: tuple[CampaignStateArtifact, ...]

    @model_validator(mode="after")
    def hash_addressed(self) -> Self:
        unique(self.input_sha256s, "campaign transition input hash")
        unique(
            tuple(item.artifact_id for item in self.artifacts),
            "campaign transition artifact",
        )
        unique(
            tuple(item.logical_path for item in self.artifacts),
            "campaign transition artifact path",
        )
        if (self.stage == "planned") != (not self.artifacts):
            raise ValueError("only the planned transition has no produced artifacts")
        return self


class ExternalCaseProgress(ValidationRecord):
    case_id: Identifier
    producer_id: Identifier
    transitions: tuple[CampaignTransition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_prefix(self) -> Self:
        order: tuple[CampaignStage, ...] = (
            "planned",
            "collected",
            "imported",
            "functionally_checked",
            "timing_checked",
        )
        observed = tuple(item.stage for item in self.transitions)
        if observed != order[: len(observed)]:
            raise ValueError("campaign transitions must be one ordered stage prefix")
        return self


class ExternalCampaignState(ValidationRecord):
    kind: Literal["external_campaign_state"]
    schema_version: Version
    state_id: Identifier
    campaign: ExternalArtifactIdentity
    campaign_id: Identifier
    cases: tuple[ExternalCaseProgress, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_progress(self) -> Self:
        unique(
            tuple((item.case_id, item.producer_id) for item in self.cases),
            "campaign case/producer progress",
        )
        artifacts = tuple(
            artifact
            for case in self.cases
            for transition in case.transitions
            for artifact in transition.artifacts
        )
        unique(tuple(item.artifact_id for item in artifacts), "campaign state artifact")
        unique(tuple(item.logical_path for item in artifacts), "campaign state path")
        return self


class EquivalenceValue(ValidationRecord):
    producer: Literal["simulator", "ttsim", "silicon"]
    value: CanonicalJSON


class EquivalenceCheck(ValidationRecord):
    field: Text
    outcome: Literal["pass", "blocked"]
    reason: Text
    values: tuple[EquivalenceValue, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def all_producers(self) -> Self:
        if {item.producer for item in self.values} != {
            "simulator",
            "ttsim",
            "silicon",
        }:
            raise ValueError("equivalence check requires all three producer values")
        return self


class ExternalEquivalenceResult(ValidationRecord):
    kind: Literal["external_equivalence_result"]
    schema_version: Version
    result_id: Identifier
    campaign_sha256: Digest
    case_id: Identifier
    ttsim_bundle_sha256: Digest
    silicon_bundle_sha256: Digest
    outcome: Literal["pass", "blocked"]
    checks: tuple[EquivalenceCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def aggregate(self) -> Self:
        expected = (
            "pass" if all(item.outcome == "pass" for item in self.checks) else "blocked"
        )
        if self.outcome != expected:
            raise ValueError("equivalence result disagrees with field checks")
        return self


class PairedFunctionalGate(ValidationRecord):
    kind: Literal["paired_functional_gate"]
    schema_version: Version
    gate_id: Identifier
    case_id: Identifier
    outcomes: tuple[ExternalOutcome, ...] = Field(min_length=2, max_length=2)
    timing_eligible: StrictBool

    @model_validator(mode="after")
    def paired(self) -> Self:
        if any(
            item.stage != "functional" or item.case_id != self.case_id
            for item in self.outcomes
        ):
            raise ValueError("functional gate outcomes disagree with the case/stage")
        unique(
            tuple(item.producer_id for item in self.outcomes),
            "functional gate producer",
        )
        if self.timing_eligible != all(
            item.outcome == "pass" for item in self.outcomes
        ):
            raise ValueError("timing eligibility disagrees with functional outcomes")
        return self


class ExternalValidationReport(ValidationRecord):
    kind: Literal["external_validation_report"]
    schema_version: Version
    report_id: Identifier
    campaign: ExternalArtifactIdentity
    bundles: tuple[ExternalArtifactIdentity, ...]
    artifacts: tuple[ExternalArtifactIdentity, ...] = ()
    boundaries: tuple[BoundaryMap, ...]
    outcomes: tuple[ExternalOutcome, ...] = Field(min_length=1)
    lineage: tuple[ArtifactLineage, ...] = Field(min_length=1)
    status: ReportStatus
    claim_scope: tuple[Text, ...]
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def scoped_report(self) -> Self:
        report_artifacts = (*self.bundles, *self.artifacts)
        unique(
            tuple(item.artifact_id for item in report_artifacts),
            "report artifact identity",
        )
        unique(
            tuple(item.logical_path for item in report_artifacts),
            "report artifact path",
        )
        unique(tuple(item.boundary_id for item in self.boundaries), "report boundary")
        unique(
            tuple(item.artifact_id for item in self.lineage), "report lineage artifact"
        )
        artifacts = {
            self.campaign.artifact_id,
            *(item.artifact_id for item in self.bundles),
            *(item.artifact_id for item in self.artifacts),
        }
        if any(item.artifact_id not in artifacts for item in self.lineage):
            raise ValueError("report lineage describes an unknown artifact")
        if any(
            parent not in artifacts
            for item in self.lineage
            for parent in item.derived_from
        ):
            raise ValueError("report lineage references an unknown artifact")
        if any(
            item not in artifacts
            for outcome in self.outcomes
            for item in outcome.artifact_ids
        ):
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
ExternalValidationDocumentAdapter: TypeAdapter[ExternalValidationDocument] = (
    TypeAdapter(ExternalValidationDocument)
)
