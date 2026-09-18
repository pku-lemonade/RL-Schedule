"""Strict v1 validation contracts. Records alone do not execute or verify evidence.

Runtime admission, observation extraction and reference comparison are separate
boundaries. No device dimensions, clock rates or acceptance thresholds default
to Wormhole example values here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

from ...validation.outcomes import (
    CheckOutcome,
    EvidenceStatus,
    EvidenceTier,
    ReportStatus,
    aggregate_status,
    tier_status,
)
from .topology import Digest, GraphRecord, Identifier, Index, PositiveInt, unique

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Version = Annotated[int, Field(strict=True, ge=1, le=1)]
Positive = Annotated[float, Field(strict=True, gt=0)]
NonNegative = Annotated[float, Field(strict=True, ge=0)]
# Keep integer counters exact, including values above 2**53.
Number = StrictInt | StrictFloat
TimeValue = Annotated[Number, Field(ge=0)]
AdapterName = Literal[
    "profile_inspection_v1", "topology_inspection_v1", "topology_replay_v1",
    "torus_replay_v2", "memory_replay_v1", "compute_workload_v1",
]
CheckName = Literal[
    "admission", "architecture", "routing", "packet_accounting", "memory_service",
    "ownership", "compute_work", "causality", "drain", "expected_execution",
    "metrics", "functional_reference", "silicon_timing", "bounded_execution",
    "tensor_values", "multicast", "synchronization",
]
GateName = Literal[
    "detailed_unittest", "compute_unittest", "memory_unittest", "torus_unittest",
    "strict_pyright", "scoped_ruff", "root_darknet19_smoke", "optional_ml",
]
ExecutionState = Literal["inspected", "complete", "incomplete", "rejected", "unavailable"]
SourceClass = Literal["synthetic", "architecture_document", "functional_capture", "hardware_capture"]
T = TypeVar("T")


class ValidationRecord(GraphRecord):
    model_config = ConfigDict(strict=True, revalidate_instances="always")


class Metadata(ValidationRecord, Generic[T]):
    """Unknown fields carry reasons, never values inferred from simulator defaults."""

    state: Literal["known", "unknown"]
    value: T | None = None
    reason: Text | None = None

    @model_validator(mode="after")
    def known_or_unknown(self) -> Self:
        if self.state == "known":
            if self.value is None or self.reason is not None:
                raise ValueError("known metadata requires a value and no unknown reason")
        elif self.value is not None or self.reason is None:
            raise ValueError("unknown metadata requires a reason and no value")
        return self


class CanonicalJSON(ValidationRecord):
    """Immutable effective configuration/conditions, never executable expressions."""

    text: Text

    @model_validator(mode="after")
    def canonical(self) -> Self:
        value: object = json.loads(self.text)
        if json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) != self.text:
            raise ValueError("configuration must be finite canonical JSON")
        return self


class ArtifactReference(ValidationRecord):
    path: Text
    sha256: Digest


class ClockDomain(ValidationRecord):
    domain_id: Identifier
    hz: Metadata[Positive]


class TimePoint(ValidationRecord):
    value: TimeValue
    unit: Literal["cycles", "seconds", "nanoseconds"]
    clock_domain: Identifier


class MeasurementWindow(ValidationRecord):
    boundary: Identifier
    start: TimePoint
    end: TimePoint
    excluded_warmups: tuple[Identifier, ...]
    repetitions: PositiveInt
    aggregation: Literal["none", "mean", "median"]

    @model_validator(mode="after")
    def interval(self) -> Self:
        if (self.start.unit, self.start.clock_domain) != (self.end.unit, self.end.clock_domain):
            raise ValueError("measurement window must use one unit and clock domain")
        if self.end.value < self.start.value:
            raise ValueError("measurement window ends before it starts")
        unique(self.excluded_warmups, "warm-up repetition")
        if self.aggregation == "none" and self.repetitions != 1:
            raise ValueError("multiple repetitions require an aggregation")
        return self


class ReferenceConditions(ValidationRecord):
    architecture: Metadata[Text]
    device: Metadata[Text]
    device_scope: Metadata[Text]
    profile_version: Metadata[Text]
    enabled_layout: Metadata[CanonicalJSON]
    clocks: Metadata[tuple[ClockDomain, ...]]
    workload: Metadata[CanonicalJSON]
    mapping: Metadata[CanonicalJSON]
    software: Metadata[Text]
    firmware: Metadata[Text]
    instrumentation: Metadata[CanonicalJSON]
    measurement: Metadata[MeasurementWindow]
    capture_group: Metadata[Identifier]

    @model_validator(mode="after")
    def clock_identities(self) -> Self:
        if self.clocks.value is not None:
            if not self.clocks.value:
                raise ValueError("known clocks cannot be empty")
            unique(tuple(c.domain_id for c in self.clocks.value), "clock domain")
            if (self.measurement.value is not None
                    and self.measurement.value.start.clock_domain not in
                    {c.domain_id for c in self.clocks.value}):
                raise ValueError("measurement references an unknown clock domain")
        return self


class ObservationEntity(ValidationRecord):
    entity_id: Identifier
    role: Literal["endpoint", "resource", "worker", "router", "link", "job", "transfer", "slot"]
    physical_owner: Identifier | None = None
    fabric_id: Index | None = None


class ObservationCounter(ValidationRecord):
    name: Identifier
    value: Index
    unit: Literal["bytes", "work", "count"]
    scope: Literal["planned", "observed"]


class ObservationEvent(ValidationRecord):
    event_id: Identifier
    action: Identifier
    subject_id: Identifier
    time: TimePoint | None
    counters: tuple[ObservationCounter, ...] = ()
    generation: Index | None = None

    @model_validator(mode="after")
    def counters_unique(self) -> Self:
        unique(tuple((c.name, c.scope) for c in self.counters), "event counter")
        return self


class AddressedEffect(ValidationRecord):
    effect_id: Identifier
    destination_id: Identifier
    resource_id: Identifier
    offset_bytes: Index
    size_bytes: PositiveInt
    count: PositiveInt
    visibility_event: Identifier | None


class CausalEdge(ValidationRecord):
    before: Identifier
    after: Identifier


class RouteObservation(ValidationRecord):
    transfer_id: Identifier
    source_id: Identifier
    destination_id: Identifier
    fabric_id: Index
    link_ids: tuple[Identifier, ...]


class OccupancyInterval(ValidationRecord):
    interval_id: Identifier
    resource_id: Identifier
    owner_id: Identifier
    start: TimePoint
    end: TimePoint | None
    generation: Index | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end is not None and (
            (self.start.unit, self.start.clock_domain) != (self.end.unit, self.end.clock_domain)
            or self.end.value < self.start.value
        ):
            raise ValueError("occupancy interval must be ordered in one domain/unit")
        return self


class MetricObservation(ValidationRecord):
    metric_id: Identifier
    value: Number
    unit: Identifier
    clock_domain: Identifier | None
    numerator: Text
    denominator: Text
    window: MeasurementWindow
    completion_scope: Literal["complete_run", "interval", "partial"]


class MissingObservation(ValidationRecord):
    name: Identifier
    outcome: Literal["blocked", "unsupported", "not_run"]
    reason: Text


class NormalizedObservations(ValidationRecord):
    schema_version: Version = 1
    observation_id: Identifier
    source_result_sha256: Digest
    execution: ExecutionState
    clocks: tuple[ClockDomain, ...]
    entities: tuple[ObservationEntity, ...]
    events: tuple[ObservationEvent, ...]
    effects: tuple[AddressedEffect, ...]
    causal_edges: tuple[CausalEdge, ...]
    routes: tuple[RouteObservation, ...]
    intervals: tuple[OccupancyInterval, ...]
    metrics: tuple[MetricObservation, ...]
    pending: tuple[Identifier, ...]
    missing: tuple[MissingObservation, ...]

    @model_validator(mode="after")
    def references(self) -> Self:
        for label, ids in (
            ("clock", tuple(c.domain_id for c in self.clocks)),
            ("entity", tuple(e.entity_id for e in self.entities)),
            ("event", tuple(e.event_id for e in self.events)),
            ("effect", tuple(e.effect_id for e in self.effects)),
            ("route", tuple(r.transfer_id for r in self.routes)),
            ("interval", tuple(i.interval_id for i in self.intervals)),
            ("metric", tuple(m.metric_id for m in self.metrics)),
            ("missing observable", tuple(m.name for m in self.missing)),
            ("pending identity", self.pending),
            ("causal edge", tuple((e.before, e.after) for e in self.causal_edges)),
        ):
            unique(ids, label)
        entities = {e.entity_id: e for e in self.entities}
        events = {e.event_id for e in self.events}
        clocks = {c.domain_id for c in self.clocks}
        for entity in self.entities:
            if entity.physical_owner is not None and entity.physical_owner not in entities:
                raise ValueError("unknown physical owner")
        times: list[TimePoint] = []
        for event in self.events:
            if event.subject_id not in entities:
                raise ValueError("event references an unknown subject")
            if event.time is not None:
                times.append(event.time)
        for effect in self.effects:
            if effect.destination_id not in entities or effect.resource_id not in entities:
                raise ValueError("effect references an unknown destination/resource")
            if entities[effect.resource_id].role != "resource":
                raise ValueError("addressed effect requires a physical resource")
            if effect.visibility_event is not None and effect.visibility_event not in events:
                raise ValueError("effect references an unknown visibility event")
        for route in self.routes:
            if any(i not in entities for i in
                   (route.transfer_id, route.source_id, route.destination_id, *route.link_ids)):
                raise ValueError("route references an unknown entity")
        for interval in self.intervals:
            if interval.resource_id not in entities or interval.owner_id not in entities:
                raise ValueError("interval references an unknown resource/owner")
            times.append(interval.start)
            if interval.end is not None:
                times.append(interval.end)
        for metric in self.metrics:
            times.extend((metric.window.start, metric.window.end))
            if metric.clock_domain is not None and metric.clock_domain not in clocks:
                raise ValueError("metric references an unknown clock domain")
            if self.execution != "complete" and metric.completion_scope == "complete_run":
                raise ValueError("incomplete/inspection evidence cannot carry complete-run metrics")
        if any(t.clock_domain not in clocks for t in times):
            raise ValueError("observation references an unknown clock domain")
        if self.execution == "complete" and (self.pending or any(i.end is None for i in self.intervals)):
            raise ValueError("complete observations cannot retain pending work/open intervals")
        # Check only declared partial-order edges, never incidental event-list order.
        successors: dict[str, list[str]] = {event: [] for event in events}
        indegree = dict.fromkeys(events, 0)
        for edge in self.causal_edges:
            if edge.before not in events or edge.after not in events:
                raise ValueError("causal edge references an unknown event")
            successors[edge.before].append(edge.after)
            indegree[edge.after] += 1
        ready = [event for event, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            visited += 1
            for successor in successors[ready.pop()]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        if visited != len(events):
            raise ValueError("causal edges contain a cycle")
        return self


class ReferenceProvenance(ValidationRecord):
    classification: SourceClass
    producer: Identifier
    source_url: Metadata[Text]
    revision: Metadata[Text]
    snapshot_sha256: Metadata[Digest]
    raw_artifact: ArtifactReference
    extractor: Identifier
    extractor_version: Identifier
    original_units: tuple[Identifier, ...] = Field(min_length=1)
    normalized_units: tuple[Identifier, ...] = Field(min_length=1)
    # Integrity is checkable; this harness does not authenticate a supplied origin.
    origin_authentication: Literal["not_authenticated"] = "not_authenticated"

    @model_validator(mode="after")
    def source_identity(self) -> Self:
        if self.revision.state != "known" and self.snapshot_sha256.state != "known":
            raise ValueError("reference requires an immutable revision or snapshot identity")
        if "ttsim" in self.producer.casefold() and self.classification == "hardware_capture":
            raise ValueError("ttsim is not a silicon timing source")
        if "synthetic" in self.producer.casefold() and self.classification != "synthetic":
            raise ValueError("synthetic producer cannot claim external evidence")
        unique(self.original_units, "original unit")
        unique(self.normalized_units, "normalized unit")
        return self


class IdentifierMapping(ValidationRecord):
    reference: Identifier
    simulator: Identifier


class ProfilerSelection(ValidationRecord):
    device: Text
    core_x: Index
    core_y: Index
    risc: Identifier
    zone: Identifier
    source_file: Text
    source_line: Index
    clock_domain: Identifier
    metric_id: Identifier
    boundary: Identifier
    run_ids: tuple[Index, ...] = Field(min_length=1)
    warmup_run_ids: tuple[Index, ...] = ()
    aggregation: Literal["none", "mean", "median"]

    @model_validator(mode="after")
    def finite_samples(self) -> Self:
        unique(self.run_ids, "profiler run")
        unique(self.warmup_run_ids, "profiler warmup")
        if not set(self.warmup_run_ids) < set(self.run_ids):
            raise ValueError("warmups must be a proper subset of selected runs")
        if self.aggregation == "none" and len(self.run_ids) - len(self.warmup_run_ids) != 1:
            raise ValueError("unaggregated profiler metric requires one retained run")
        return self


class SampleStatistics(ValidationRecord):
    sample_count: PositiveInt
    durations_cycles: tuple[Index, ...] = Field(min_length=1)
    minimum_cycles: Index
    maximum_cycles: Index
    mean_absolute_deviation_cycles: NonNegative


class ValidationReference(ValidationRecord):
    kind: Literal["validation_reference"]
    schema_version: Version
    reference_id: Identifier
    format: Literal["normalized_functional_v1", "tt_metal_device_profiler_csv_v1"]
    provenance: ReferenceProvenance
    conditions: ReferenceConditions
    observations: NormalizedObservations | None
    profiler: ProfilerSelection | None = None
    sample_statistics: SampleStatistics | None = None

    @model_validator(mode="after")
    def raw_identity(self) -> Self:
        if (self.observations is not None and self.observations.source_result_sha256
                != self.provenance.raw_artifact.sha256):
            raise ValueError("reference observations must identify their raw artifact")
        if self.format == "normalized_functional_v1" and (self.profiler is not None or self.sample_statistics is not None):
            raise ValueError("functional reference cannot carry CSV extraction options")
        return self


class CaseBudget(ValidationRecord):
    wall_time_seconds: Positive
    max_aci_cycles: Positive | None


class MetricPolicy(ValidationRecord):
    metric_id: Identifier
    unit: Identifier
    boundary: Identifier
    absolute_tolerance: NonNegative
    relative_tolerance: NonNegative
    rationale: Text


class CheckSelection(ValidationRecord):
    check_id: Identifier
    check: CheckName
    required: StrictBool
    tier: EvidenceTier
    requirements: tuple[Identifier, ...] = Field(min_length=1)
    reference_id: Identifier | None = None
    metrics: tuple[MetricPolicy, ...] = ()
    entity_mappings: tuple[IdentifierMapping, ...] = ()
    event_mappings: tuple[IdentifierMapping, ...] = ()
    clock_mappings: tuple[IdentifierMapping, ...] = ()

    @model_validator(mode="after")
    def policy(self) -> Self:
        unique(self.requirements, "requirement binding")
        unique(tuple(m.metric_id for m in self.metrics), "metric policy")
        for mapping in (self.entity_mappings, self.event_mappings, self.clock_mappings):
            unique(tuple(m.reference for m in mapping), "reference mapping")
            unique(tuple(m.simulator for m in mapping), "simulator mapping")
        if self.check in ("functional_reference", "silicon_timing") and self.reference_id is None:
            raise ValueError("reference comparisons require an explicit reference binding")
        if self.check in ("metrics", "silicon_timing") and not self.metrics:
            raise ValueError("metric checks require predeclared tolerances")
        return self


class ReferenceBinding(ValidationRecord):
    reference_id: Identifier
    document: ArtifactReference


class ValidationCase(ValidationRecord):
    case_id: Identifier
    adapter: AdapterName
    input_path: Text
    budget: CaseBudget
    expected_execution: ExecutionState
    checks: tuple[CheckSelection, ...] = Field(min_length=1)
    resume_at_aci_cycles: tuple[Positive, ...] = ()
    conditions: ReferenceConditions | None = None

    @model_validator(mode="after")
    def finite_execution(self) -> Self:
        unique(tuple(c.check_id for c in self.checks), "case check")
        if self.resume_at_aci_cycles:
            if self.adapter not in ("memory_replay_v1", "compute_workload_v1"):
                raise ValueError("only memory/compute adapters support interruption/resume")
            if tuple(sorted(set(self.resume_at_aci_cycles))) != self.resume_at_aci_cycles:
                raise ValueError("resume horizons must be unique and increasing")
            if self.budget.max_aci_cycles is None or self.resume_at_aci_cycles[-1] >= self.budget.max_aci_cycles:
                raise ValueError("resume horizons must precede the final budget")
        if (self.adapter not in ("profile_inspection_v1", "topology_inspection_v1")
                and self.budget.max_aci_cycles is None):
            raise ValueError("runtime adapter requires a finite simulation horizon")
        return self


class RegressionGate(ValidationRecord):
    gate_id: Identifier
    gate: GateName
    required: StrictBool
    wall_time_seconds: Positive
    requirements: tuple[Identifier, ...] = Field(min_length=1)


class ValidationSuite(ValidationRecord):
    kind: Literal["validation_suite"]
    schema_version: Version
    suite_id: Identifier
    max_cases: PositiveInt
    cases: tuple[ValidationCase, ...] = Field(min_length=1)
    references: tuple[ReferenceBinding, ...] = ()
    gates: tuple[RegressionGate, ...] = ()

    @model_validator(mode="after")
    def bindings(self) -> Self:
        unique(tuple(c.case_id for c in self.cases), "case")
        unique(tuple(r.reference_id for r in self.references), "reference")
        unique(tuple(g.gate_id for g in self.gates), "gate")
        if len(self.cases) > self.max_cases:
            raise ValueError("case count exceeds declared budget")
        checks = tuple(check for case in self.cases for check in case.checks)
        if not any(check.required for check in checks):
            raise ValueError("suite requires at least one required case check")
        references = {r.reference_id for r in self.references}
        if any(c.reference_id is not None and c.reference_id not in references for c in checks):
            raise ValueError("check references an unknown reference binding")
        return self


class FileIdentity(ValidationRecord):
    logical_path: Text
    sha256: Digest | None
    size_bytes: Index | None

    @model_validator(mode="after")
    def portable(self) -> Self:
        path = PurePosixPath(self.logical_path)
        if (path.is_absolute() or ".." in path.parts or str(path) != self.logical_path
                or "\\" in self.logical_path or ":" in self.logical_path or str(path) == "."):
            raise ValueError("identity path must be a normalized relative POSIX path")
        if (self.sha256 is None) != (self.size_bytes is None):
            raise ValueError("deleted source requires both hash and size to be absent")
        return self


class SourceIdentity(ValidationRecord):
    revision: Metadata[Text]
    dirty: Metadata[StrictBool]
    files: tuple[FileIdentity, ...] = Field(min_length=1)
    bundle_sha256: Digest

    @model_validator(mode="after")
    def membership(self) -> Self:
        paths = tuple(f.logical_path for f in self.files)
        unique(paths, "source file")
        if paths != tuple(sorted(paths)):
            raise ValueError("source bundle membership must be sorted")
        manifest = json.dumps([f.model_dump(mode="json") for f in self.files],
                              sort_keys=True, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(manifest.encode("utf-8")).hexdigest() != self.bundle_sha256:
            raise ValueError("source bundle hash does not match its membership")
        return self


class DependencyVersion(ValidationRecord):
    name: Identifier
    version: Metadata[Text]


class EnvironmentIdentity(ValidationRecord):
    python_version: Text
    python_implementation: Text
    system: Text
    release: Text
    machine: Text
    dependencies: tuple[DependencyVersion, ...]

    @model_validator(mode="after")
    def dependencies_unique(self) -> Self:
        names = tuple(d.name for d in self.dependencies)
        unique(names, "dependency")
        if names != tuple(sorted(names)):
            raise ValueError("dependency membership must be sorted")
        return self


class SeedState(ValidationRecord):
    mode: Literal["deterministic", "seeded", "not_applicable"]
    seed: StrictInt | None = None

    @model_validator(mode="after")
    def explicit_seed(self) -> Self:
        if (self.mode == "seeded") != (self.seed is not None):
            raise ValueError("only seeded execution requires a seed")
        return self


class RunIdentity(ValidationRecord):
    source: SourceIdentity
    inputs: tuple[FileIdentity, ...] = Field(min_length=1)
    effective_plan: CanonicalJSON
    effective_plan_sha256: Digest
    selection: CanonicalJSON
    selection_sha256: Digest
    environment: EnvironmentIdentity
    seed: SeedState

    @model_validator(mode="after")
    def input_membership(self) -> Self:
        names = tuple(i.logical_path for i in self.inputs)
        unique(names, "input identity")
        if names != tuple(sorted(names)) or any(i.sha256 is None for i in self.inputs):
            raise ValueError("input identities must be present and sorted")
        for record, digest in ((self.effective_plan, self.effective_plan_sha256),
                               (self.selection, self.selection_sha256)):
            if hashlib.sha256(record.text.encode("utf-8")).hexdigest() != digest:
                raise ValueError("effective plan/selection hash does not match its content")
        return self


class DiagnosticPath(ValidationRecord):
    logical_path: Text
    local_path: Text


class RunDiagnostics(ValidationRecord):
    """Excluded from portable identity; paths/timestamps are only diagnostics."""

    recorded_at: Text | None = None
    checkout_path: Text | None = None
    paths: tuple[DiagnosticPath, ...] = ()


class EvidenceReference(ValidationRecord):
    reference_id: Identifier
    document_sha256: Digest
    classification: SourceClass


class CheckResult(ValidationRecord):
    check_id: Identifier
    required: StrictBool
    outcome: CheckOutcome
    tier: EvidenceTier
    reason: Text
    executed: StrictBool
    observation_ids: tuple[Identifier, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    comparison_admitted: StrictBool = False

    @model_validator(mode="after")
    def evidence_strength(self) -> Self:
        unique(self.observation_ids, "check observation")
        unique(tuple(e.reference_id for e in self.evidence), "check reference")
        if self.outcome in ("pass", "fail") and not self.executed:
            raise ValueError("pass/fail requires an executed check")
        if self.outcome == "not_run" and self.executed:
            raise ValueError("not_run cannot describe an executed check")
        if self.outcome in ("pass", "fail"):
            if self.tier == "architecture_protocol" and any(
                e.classification == "synthetic" for e in self.evidence
            ):
                raise ValueError("synthetic evidence supports only model/adapter checks")
            if self.tier in ("functional_reference", "silicon_timing"):
                allowed = {"hardware_capture"} if self.tier == "silicon_timing" else {
                    "functional_capture", "hardware_capture"
                }
                if (not self.comparison_admitted or not self.evidence or not self.observation_ids
                        or any(e.classification not in allowed for e in self.evidence)):
                    raise ValueError("external pass/fail requires admitted observations and appropriate evidence")
        return self


class CaseResult(ValidationRecord):
    case_id: Identifier
    adapter: AdapterName
    execution: ExecutionState
    reason: Text
    identity: RunIdentity | None
    observations: tuple[NormalizedObservations, ...]
    checks: tuple[CheckResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def checked_observations(self) -> Self:
        unique(tuple(o.observation_id for o in self.observations), "case observation")
        unique(tuple(c.check_id for c in self.checks), "case check")
        ids = {o.observation_id for o in self.observations}
        if any(i not in ids for c in self.checks for i in c.observation_ids):
            raise ValueError("check references an unknown observation")
        if self.execution in ("inspected", "complete", "incomplete") and self.identity is None:
            raise ValueError("observed case requires a reproducible run identity")
        if self.observations and self.observations[-1].execution != self.execution:
            raise ValueError("last observation and case execution disagree")
        return self


class CoverageCheck(ValidationRecord):
    # None selects a report-level regression gate; IDs remain scoped.
    case_id: Identifier | None
    check_id: Identifier


class RequirementCoverage(ValidationRecord):
    requirement_id: Identifier
    status: Literal["implemented_and_checked", "partial", "blocked", "unsupported", "pending"]
    checks: tuple[CoverageCheck, ...]
    child_commits: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")], ...]
    reason: Text

    @model_validator(mode="after")
    def evidence_present(self) -> Self:
        unique(tuple((c.case_id, c.check_id) for c in self.checks), "coverage check")
        unique(self.child_commits, "child commit")
        if self.status == "implemented_and_checked" and (not self.checks or not self.child_commits):
            raise ValueError("delivered coverage requires exact checks and implementing commits")
        return self


class ValidationReport(ValidationRecord):
    kind: Literal["validation_report"]
    schema_version: Version
    suite_sha256: Digest
    status: ReportStatus
    cases: tuple[CaseResult, ...] = Field(min_length=1)
    gate_checks: tuple[CheckResult, ...] = ()
    references: tuple[EvidenceReference, ...] = ()
    coverage: tuple[RequirementCoverage, ...]
    capabilities: tuple[Identifier, ...]
    assumptions: tuple[Text, ...]
    limitations: tuple[Text, ...]
    functional_reference: EvidenceStatus
    silicon_timing: EvidenceStatus
    diagnostics: RunDiagnostics = Field(default_factory=RunDiagnostics)

    @model_validator(mode="after")
    def honest_report(self) -> Self:
        unique(tuple(c.case_id for c in self.cases), "report case")
        unique(tuple(c.check_id for c in self.gate_checks), "gate check")
        unique(tuple(r.reference_id for r in self.references), "report reference")
        unique(tuple(c.requirement_id for c in self.coverage), "coverage requirement")
        checks = tuple(c for case in self.cases for c in case.checks) + self.gate_checks
        if not any(c.required for c in checks):
            raise ValueError("report requires required checks")
        if self.status != aggregate_status(checks):
            raise ValueError("report status disagrees with actual checks")
        if (self.functional_reference != tier_status(checks, "functional_reference")
                or self.silicon_timing != tier_status(checks, "silicon_timing")):
            raise ValueError("report claims disagree with actual evidence tiers")
        references = {r.reference_id: r for r in self.references}
        for check in checks:
            if any(references.get(e.reference_id) != e for e in check.evidence):
                raise ValueError("check evidence disagrees with report reference identity/classification")
        lookup = {(case.case_id, c.check_id): c for case in self.cases for c in case.checks}
        gate_lookup = {c.check_id: c for c in self.gate_checks}
        for coverage in self.coverage:
            selected = [lookup.get((c.case_id, c.check_id)) if c.case_id is not None
                        else gate_lookup.get(c.check_id) for c in coverage.checks]
            if any(c is None for c in selected):
                raise ValueError("coverage references an unknown check")
            if coverage.status == "implemented_and_checked" and any(
                c is None or c.outcome != "pass" for c in selected
            ):
                raise ValueError("delivered coverage requires passed checks")
        return self


class MemoryTarget(ValidationRecord):
    kind: Literal["memory"]
    resource_id: Identifier
    field: Literal["bytes_per_cycle", "fixed_latency_cycles"]


class ComputeTarget(ValidationRecord):
    kind: Literal["compute"]
    rate_id: Identifier
    field: Literal["work_per_native_cycle", "setup_native_cycles"]


CalibrationTarget = Annotated[MemoryTarget | ComputeTarget, Field(discriminator="kind")]


class CalibrationParameter(ValidationRecord):
    parameter_id: Identifier
    target: CalibrationTarget
    lower: NonNegative
    upper: NonNegative
    candidates: tuple[NonNegative, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bounded_candidates(self) -> Self:
        unique(self.candidates, "candidate value")
        if self.lower > self.upper or any(not self.lower <= c <= self.upper for c in self.candidates):
            raise ValueError("candidate values must lie within ordered declared bounds")
        if self.target.field in ("bytes_per_cycle", "work_per_native_cycle") and self.lower <= 0:
            raise ValueError("effective rate bounds must be positive")
        return self


class CalibrationMetric(MetricPolicy):
    weight: Positive
    scale: Positive


class CalibrationCase(ValidationRecord):
    case_id: Identifier
    adapter: Literal["memory_replay_v1", "compute_workload_v1"]
    input_path: Text
    reference: ReferenceBinding
    budget: CaseBudget
    # Declarations are checked again against admitted input/capture content in
    # the calibration executor; names alone never define the held-out split.
    semantic_sha256: Digest
    capture_group: Identifier
    conditions: ReferenceConditions | None = None

    @model_validator(mode="after")
    def horizon(self) -> Self:
        if self.budget.max_aci_cycles is None:
            raise ValueError("calibration case requires a finite simulation horizon")
        return self


class CalibrationPlan(ValidationRecord):
    kind: Literal["calibration_plan"]
    schema_version: Version
    plan_id: Identifier
    parameters: tuple[CalibrationParameter, ...] = Field(min_length=1)
    max_candidates: PositiveInt
    max_case_runs: PositiveInt
    fit_cases: tuple[CalibrationCase, ...] = Field(min_length=1)
    evaluation_cases: tuple[CalibrationCase, ...] = Field(min_length=1)
    metrics: tuple[CalibrationMetric, ...] = Field(min_length=1)
    loss: Literal["weighted_mean_absolute_scaled_error_v1"]
    tie_break: Literal["declared_candidate_order"]
    evidence_scope: Literal["synthetic_demonstration", "measured_conditions"]

    @model_validator(mode="after")
    def bounded_split(self) -> Self:
        unique(tuple(p.parameter_id for p in self.parameters), "parameter ID")
        targets = tuple((p.target.kind,
                         p.target.resource_id if isinstance(p.target, MemoryTarget) else p.target.rate_id,
                         p.target.field) for p in self.parameters)
        unique(targets, "parameter target")
        unique(tuple(m.metric_id for m in self.metrics), "calibration metric")
        cases = self.fit_cases + self.evaluation_cases
        unique(tuple(c.case_id for c in cases), "fit/evaluation case")
        bindings: dict[str, ArtifactReference] = {}
        for case in cases:
            previous = bindings.setdefault(case.reference.reference_id, case.reference.document)
            if previous != case.reference.document:
                raise ValueError("ambiguous calibration reference identity")
        if {c.semantic_sha256 for c in self.fit_cases} & {c.semantic_sha256 for c in self.evaluation_cases}:
            raise ValueError("fit/evaluation workloads overlap semantically")
        if {c.capture_group for c in self.fit_cases} & {c.capture_group for c in self.evaluation_cases}:
            raise ValueError("fit/evaluation capture groups overlap")
        if {c.reference.reference_id for c in self.fit_cases} & {
            c.reference.reference_id for c in self.evaluation_cases
        }:
            raise ValueError("fit/evaluation reference identities overlap")
        count = 1
        for parameter in self.parameters:
            count *= len(parameter.candidates)
            if count > self.max_candidates:
                raise ValueError("Cartesian candidate count exceeds declared budget")
        if count * len(self.fit_cases) + len(self.evaluation_cases) > self.max_case_runs:
            raise ValueError("calibration execution count exceeds declared budget")
        return self


class ParameterValue(ValidationRecord):
    parameter_id: Identifier
    value: NonNegative


class CandidateResult(ValidationRecord):
    candidate_id: Identifier
    values: tuple[ParameterValue, ...] = Field(min_length=1)
    outcome: Literal["valid", "rejected", "failed", "blocked"]
    reason: Text
    fit_loss: NonNegative | None
    configuration_sha256: Digest | None
    fit_checks: tuple[CheckResult, ...]
    fit_runs: tuple[CaseResult, ...] = ()

    @model_validator(mode="after")
    def fitted(self) -> Self:
        unique(tuple(p.parameter_id for p in self.values), "candidate parameter")
        unique(tuple(c.check_id for c in self.fit_checks), "fit check")
        if self.outcome == "valid":
            if (self.fit_loss is None or self.configuration_sha256 is None
                    or aggregate_status(self.fit_checks) != "pass"):
                raise ValueError("valid candidate requires an actual passing fit and loss")
        elif self.fit_loss is not None:
            raise ValueError("invalid candidate cannot carry a selectable fit loss")
        if self.fit_runs:
            observations = {o.observation_id for run in self.fit_runs for o in run.observations}
            if any(i not in observations for c in self.fit_checks for i in c.observation_ids):
                raise ValueError("fit check refers to absent run observations")
            if self.outcome == "valid" and any(aggregate_status(run.checks) != "pass" for run in self.fit_runs):
                raise ValueError("valid fitting candidate requires passing runtime audits")
        return self


class FrozenSelection(ValidationRecord):
    candidate_id: Identifier
    values: tuple[ParameterValue, ...] = Field(min_length=1)
    configuration_sha256: Digest
    fit_evidence_sha256: Digest
    selection_sha256: Digest

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        unique(tuple(p.parameter_id for p in self.values), "frozen parameter")
        return self


class CalibrationResult(ValidationRecord):
    kind: Literal["calibration_result"]
    schema_version: Version
    plan_sha256: Digest
    status: ReportStatus
    reason: Text
    candidates: tuple[CandidateResult, ...]
    selection: FrozenSelection | None
    tied_candidate_ids: tuple[Identifier, ...]
    evaluation_checks: tuple[CheckResult, ...]
    evidence_scope: Literal["synthetic_demonstration", "measured_conditions", "unvalidated"]
    references: tuple[EvidenceReference, ...]
    diagnostics: RunDiagnostics = Field(default_factory=RunDiagnostics)
    identity: RunIdentity | None = None
    evaluation_runs: tuple[CaseResult, ...] = ()

    @model_validator(mode="after")
    def sealed_evaluation(self) -> Self:
        unique(tuple(c.candidate_id for c in self.candidates), "candidate result")
        unique(self.tied_candidate_ids, "tied candidate")
        unique(tuple(c.check_id for c in self.evaluation_checks), "evaluation check")
        unique(tuple(r.reference_id for r in self.references), "calibration reference")
        if self.candidates:
            parameter_ids = tuple(p.parameter_id for p in self.candidates[0].values)
            if any(tuple(p.parameter_id for p in c.values) != parameter_ids for c in self.candidates):
                raise ValueError("candidate vectors require the same ordered parameter IDs")
        valid = [c for c in self.candidates if c.outcome == "valid"]
        if self.selection is None:
            if valid or self.evaluation_checks or self.tied_candidate_ids or self.status == "pass":
                raise ValueError("no selection means no successful fit/evaluation claim")
            if self.evidence_scope != "unvalidated":
                raise ValueError("calibration without a winner is unvalidated")
        else:
            if not valid:
                raise ValueError("selection requires a valid fitted candidate")
            winner = min(valid, key=lambda c: c.fit_loss if c.fit_loss is not None else float("inf"))
            if (winner.candidate_id != self.selection.candidate_id
                    or winner.values != self.selection.values
                    or winner.configuration_sha256 != self.selection.configuration_sha256):
                raise ValueError("frozen selection must match the first minimum-loss fitted candidate")
            tied = tuple(c.candidate_id for c in valid if c.fit_loss == winner.fit_loss)
            if self.tied_candidate_ids != (tied if len(tied) > 1 else ()):
                raise ValueError("all tied minima must be reported in candidate order")
            if self.status != aggregate_status(self.evaluation_checks):
                raise ValueError("held-out outcomes determine status without refitting")
            if self.identity is not None:
                plan = CalibrationPlan.model_validate_json(self.identity.effective_plan.text)
                fit_text = json.dumps([c.model_dump(mode="json") for c in self.candidates], sort_keys=True, separators=(",", ":"), allow_nan=False)
                fit_digest = hashlib.sha256(fit_text.encode()).hexdigest()
                if fit_digest != self.selection.fit_evidence_sha256:
                    raise ValueError("frozen fit evidence digest disagrees with retained candidates")
                seal = {"candidate_id": self.selection.candidate_id, "values": [v.model_dump(mode="json") for v in self.selection.values],
                        "configuration_sha256": self.selection.configuration_sha256, "fit_evidence_sha256": fit_digest,
                        "policy": [m.model_dump(mode="json") for m in plan.metrics]}
                seal_text = json.dumps(seal, sort_keys=True, separators=(",", ":"), allow_nan=False)
                if hashlib.sha256(seal_text.encode()).hexdigest() != self.selection.selection_sha256:
                    raise ValueError("frozen selection digest disagrees with vector/evidence/policy")
        checks = tuple(c for candidate in self.candidates for c in candidate.fit_checks) + self.evaluation_checks
        references = {r.reference_id: r for r in self.references}
        if any(references.get(e.reference_id) != e for c in checks for e in c.evidence):
            raise ValueError("calibration check references inconsistent evidence")
        if self.evidence_scope == "measured_conditions" and (
            self.status != "pass" or not self.references
            or any(r.classification != "hardware_capture" for r in self.references)
            or tier_status(self.evaluation_checks, "silicon_timing") != "validated"
            or self.selection is None
            or not any(c.candidate_id == self.selection.candidate_id
                       and tier_status(c.fit_checks, "silicon_timing") == "validated"
                       for c in valid)
        ):
            raise ValueError("measured calibration requires passing admitted measured evaluation")
        return self


ValidationDocument = Annotated[
    ValidationSuite | ValidationReference | ValidationReport | CalibrationPlan | CalibrationResult,
    Field(discriminator="kind"),
]
