"""Resumable campaign, equivalence and functional-gate tests."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.external_validation import (
    ArtifactLineage,
    BoundaryMap,
    CaptureCounter,
    CaptureEnvironment,
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalOutcome,
    FunctionalMappingManifest,
    ModelIntervalSelection,
    RepetitionAggregation,
)
from simulator_detailed.configs.schemas.validation import (
    AddressedEffect,
    ArtifactReference,
    CanonicalJSON,
    CausalEdge,
    EvidenceReference,
    IdentifierMapping,
    Metadata,
    MetricPolicy,
    NormalizedObservations,
    ObservationEntity,
    ObservationEvent,
    ProfilerSelection,
    ReferenceProvenance,
    SampleStatistics,
    ValidationReference,
)
from simulator_detailed.validation.adapters import admit
from simulator_detailed.validation.external import (
    AdmittedExternalCampaign,
    AdmittedExternalCapture,
    admit_external_campaign,
    admit_external_capture,
)
from simulator_detailed.validation.external_campaign import (
    CampaignStepOutput,
    ReportArtifactInput,
    compare_capture_equivalence,
    compare_external_timing,
    evaluate_functional_gate,
    gate_timing_outcome,
    initialize_campaign_state,
    run_campaign_step,
    write_external_report,
)
from simulator_detailed.validation.external_capture import FunctionalReferenceConversion
from simulator_detailed.validation.identity import bytes_digest, canonical_record
from simulator_detailed.validation.normalize import normalize
from simulator_detailed.validation.runner import admit_suite

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "simulator_detailed/configs/validation/external"


def _environment(case: object, device: str) -> CaptureEnvironment:
    return CaptureEnvironment(
        host=Metadata[CanonicalJSON](
            state="known", value=canonical_record({"worker_id": "unit-test"})
        ),
        device=Metadata[str](state="known", value=device),
        software=Metadata[str](state="known", value="test-build"),
        firmware=Metadata[str](state="unknown", reason="unit-test fixture"),
        clocks=Metadata[tuple[CanonicalJSON, ...]](
            state="known",
            value=(canonical_record({"domain_id": "tensix", "hz": 1_000_000_000}),),
        ),
        enabled_layout=case.conditions.enabled_layout,
    )


def _capture_pair(
    root: Path,
) -> tuple[AdmittedExternalCampaign, AdmittedExternalCapture, AdmittedExternalCapture]:
    shutil.copytree(EXTERNAL, root, dirs_exist_ok=True)
    campaign_path = root / "campaign.valid.json"
    campaign = admit_external_campaign(campaign_path)
    case = next(item for item in campaign.document.cases if item.case_id == "noc-64b")
    build = campaign.document.builds[0]
    campaign_data = campaign_path.read_bytes()
    campaign_identity = ExternalArtifactIdentity(
        artifact_id="campaign",
        logical_path="campaign.valid.json",
        sha256=bytes_digest(campaign_data),
        size_bytes=len(campaign_data),
    )

    ttsim_data = b'{"unit_test":"ttsim functional bytes"}\n'
    ttsim_raw = root / "raw/ttsim-functional.json"
    ttsim_raw.parent.mkdir(exist_ok=True)
    ttsim_raw.write_bytes(ttsim_data)
    ttsim_artifact = ExternalArtifactIdentity(
        artifact_id="unit-ttsim-functional",
        logical_path="raw/ttsim-functional.json",
        sha256=bytes_digest(ttsim_data),
        size_bytes=len(ttsim_data),
    )
    ttsim_document = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="unit-ttsim-bundle",
        campaign=campaign_identity,
        case_id=case.case_id,
        producer_id="ttsim-functional",
        adapter="ttsim_tt_metal_v1",
        build=build,
        intended_classification="functional_capture",
        environment=_environment(case, "ttsim-wormhole"),
        conditions=case.conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="unit-test capture",
            executed=True,
            case_id=case.case_id,
            producer_id="ttsim-functional",
            artifact_ids=(ttsim_artifact.artifact_id,),
        ),
        raw_artifacts=(ttsim_artifact,),
        counters=(CaptureCounter(name="repetitions", value=2, unit="count"),),
        lineage=(
            ArtifactLineage(
                artifact_id=ttsim_artifact.artifact_id,
                derived_from=("campaign",),
                transform="unit_test_capture",
            ),
        ),
        diagnostics=("unit-test data; not external evidence",),
    )
    ttsim_path = root / "ttsim-bundle.json"
    ttsim_path.write_text(ttsim_document.model_dump_json(indent=2) + "\n")

    binding = next(
        item for item in case.producers if item.producer_id == "wormhole-profiler"
    )
    payloads = {
        "functional_record": b'{"unit_test":"silicon functional bytes"}\n',
        "profiler_csv": b"unit-test profiler bytes\n",
        "capture_manifest": b'{"unit_test":"worker manifest"}\n',
    }
    silicon_artifacts: list[ExternalArtifactIdentity] = []
    for declaration in binding.outputs:
        data = payloads[declaration.role]
        artifact_path = root / declaration.logical_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(data)
        silicon_artifacts.append(
            ExternalArtifactIdentity(
                artifact_id=declaration.artifact_id,
                logical_path=declaration.logical_path,
                sha256=bytes_digest(data),
                size_bytes=len(data),
            )
        )
    boundary = case.boundary_maps[0]
    profiler = ProfilerSelection(
        device="0000:01:00.0",
        core_x=0,
        core_y=0,
        risc="BRISC",
        zone=boundary.producer_zone,
        source_file="kernels/noc_ack_roundtrip.cpp",
        source_line=22,
        clock_domain=boundary.clock_domain,
        metric_id=boundary.simulator_interval.metric_id,
        boundary=boundary.simulator_boundary,
        run_ids=(0, 1),
        warmup_run_ids=(0,),
        aggregation="none",
    )
    silicon_document = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="unit-silicon-bundle",
        campaign=campaign_identity,
        case_id=case.case_id,
        producer_id="wormhole-profiler",
        adapter="wormhole_tt_metal_profiler_v1",
        build=build,
        intended_classification="hardware_capture",
        environment=_environment(case, profiler.device),
        conditions=case.conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="unit-test capture",
            executed=True,
            case_id=case.case_id,
            producer_id="wormhole-profiler",
            artifact_ids=tuple(item.artifact_id for item in silicon_artifacts),
        ),
        raw_artifacts=tuple(silicon_artifacts),
        profiler_selections=(profiler,),
        counters=(CaptureCounter(name="repetitions", value=2, unit="count"),),
        lineage=tuple(
            ArtifactLineage(
                artifact_id=item.artifact_id,
                derived_from=("campaign",),
                transform="unit_test_capture",
            )
            for item in silicon_artifacts
        ),
        diagnostics=("unit-test data; not external evidence",),
    )
    silicon_path = root / "silicon-bundle.json"
    silicon_path.write_text(silicon_document.model_dump_json(indent=2) + "\n")
    return (
        campaign,
        admit_external_capture(ttsim_path),
        admit_external_capture(silicon_path),
    )


def _functional_observations(effect_size: int = 64) -> NormalizedObservations:
    return NormalizedObservations(
        observation_id=f"functional-observation-{effect_size}",
        source_result_sha256=("a" if effect_size == 64 else "b") * 64,
        execution="complete",
        clocks=(),
        entities=(
            ObservationEntity(entity_id="operation", role="transfer"),
            ObservationEntity(entity_id="destination", role="endpoint"),
            ObservationEntity(entity_id="destination-l1", role="resource"),
        ),
        events=(
            ObservationEvent(
                event_id="start", action="submit", subject_id="operation", time=None
            ),
            ObservationEvent(
                event_id="done", action="complete", subject_id="operation", time=None
            ),
        ),
        effects=(
            AddressedEffect(
                effect_id="effect",
                destination_id="destination",
                resource_id="destination-l1",
                offset_bytes=8192,
                size_bytes=effect_size,
                count=1,
                visibility_event="done",
            ),
        ),
        causal_edges=(CausalEdge(before="start", after="done"),),
        routes=(),
        intervals=(),
        metrics=(),
        pending=(),
        missing=(),
    )


def _conversion(
    classification: str, producer: str, observations: NormalizedObservations
) -> FunctionalReferenceConversion:
    reference = ValidationReference(
        kind="validation_reference",
        schema_version=1,
        reference_id=f"{producer}-reference",
        format="normalized_functional_v1",
        provenance=ReferenceProvenance(
            classification=classification,
            producer=producer,
            source_url=Metadata[str](state="unknown", reason="unit-test fixture"),
            revision=Metadata[str](state="known", value="unit-test-v1"),
            snapshot_sha256=Metadata[str](
                state="known", value=observations.source_result_sha256
            ),
            raw_artifact=ArtifactReference(
                path="unit-test.json", sha256=observations.source_result_sha256
            ),
            extractor="wormhole_reference_import",
            extractor_version="1",
            original_units=("bytes",),
            normalized_units=("bytes",),
        ),
        conditions=admit_external_campaign(EXTERNAL / "campaign.valid.json")
        .document.cases[0]
        .conditions,
        observations=observations,
    )
    mappings = FunctionalMappingManifest(
        kind="functional_identifier_mappings",
        schema_version=1,
        reference_id=reference.reference_id,
        source_artifact_sha256=observations.source_result_sha256,
        entity_mappings=tuple(
            IdentifierMapping(reference=item.entity_id, simulator=item.entity_id)
            for item in observations.entities
        ),
        event_mappings=tuple(
            IdentifierMapping(reference=item.event_id, simulator=item.event_id)
            for item in observations.events
        ),
        effect_mappings=(IdentifierMapping(reference="effect", simulator="effect"),),
    )
    return FunctionalReferenceConversion(
        reference=reference,
        mappings=mappings,
        reference_path=Path("unit-test-reference.json"),
        observations_path=Path("unit-test-observations.json"),
        mappings_path=Path("unit-test-mappings.json"),
    )


class ExternalCampaignStateTests(unittest.TestCase):
    def test_restart_reuses_completed_stage_and_rejects_stale_artifacts(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = initialize_campaign_state(campaign, root / "state")
            output = root / "bundle.json"
            output.write_text('{"bundle":"one"}\n')
            calls = 0

            def produce() -> tuple[CampaignStepOutput, ...]:
                nonlocal calls
                calls += 1
                return (CampaignStepOutput(role="capture_bundle", path=output),)

            state = run_campaign_step(
                state.document_path,
                "noc-64b",
                "ttsim-functional",
                "collected",
                produce,
            )
            state = run_campaign_step(
                state.document_path,
                "noc-64b",
                "ttsim-functional",
                "collected",
                produce,
            )
            self.assertEqual(calls, 1)
            state.artifact_paths[0].write_text('{"bundle":"tampered"}\n')
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                run_campaign_step(
                    state.document_path,
                    "noc-64b",
                    "ttsim-functional",
                    "collected",
                    produce,
                )

    def test_failed_or_out_of_order_step_does_not_change_state(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with tempfile.TemporaryDirectory() as directory:
            state = initialize_campaign_state(campaign, Path(directory) / "state")
            before = state.document_path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                run_campaign_step(
                    state.document_path,
                    "noc-64b",
                    "ttsim-functional",
                    "collected",
                    lambda: (_ for _ in ()).throw(RuntimeError("interrupted")),
                )
            self.assertEqual(state.document_path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "advance to collected"):
                run_campaign_step(
                    state.document_path,
                    "noc-64b",
                    "ttsim-functional",
                    "imported",
                    lambda: (),
                )


class ExternalEquivalenceAndGateTests(unittest.TestCase):
    def test_equivalence_passes_and_reports_material_field_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign, ttsim, silicon = _capture_pair(Path(directory))
            result = compare_capture_equivalence(campaign, ttsim, silicon)
            self.assertEqual(result.outcome, "pass")
            document = silicon.document.model_dump(mode="json")
            workload = json.loads(document["conditions"]["workload"]["value"]["text"])
            workload["bytes"] = 128
            document["conditions"]["workload"]["value"] = canonical_record(
                workload
            ).model_dump(mode="json")
            changed_path = Path(directory) / "silicon-changed.json"
            changed_path.write_text(json.dumps(document))
            changed = admit_external_capture(changed_path)
            mismatch = compare_capture_equivalence(campaign, ttsim, changed)
            self.assertEqual(mismatch.outcome, "blocked")
            failed = [item for item in mismatch.checks if item.outcome == "blocked"]
            self.assertEqual(tuple(item.field for item in failed), ("conditions.workload.bytes",))
            self.assertIn("conditions.workload.bytes", failed[0].reason)

    def test_corrupted_effect_blocks_plausible_timing(self):
        actual = _functional_observations()
        ttsim = _conversion("functional_capture", "ttsim-producer", actual)
        silicon = _conversion("hardware_capture", "wormhole-producer", actual)
        passing = evaluate_functional_gate(
            "noc-64b",
            actual,
            ttsim,
            silicon,
            ttsim_artifact_id="ttsim-reference",
            silicon_artifact_id="silicon-reference",
        )
        self.assertTrue(passing.timing_eligible)
        timing = ExternalOutcome(
            stage="timing",
            required=True,
            outcome="pass",
            reason="plausible duration",
            executed=True,
            case_id="noc-64b",
            producer_id="wormhole-profiler",
            boundary_id="noc-ack-window",
            artifact_ids=("timing-reference",),
        )
        self.assertEqual(gate_timing_outcome(passing, timing), timing)

        corrupted = _conversion(
            "hardware_capture", "wormhole-producer", _functional_observations(63)
        )
        failing = evaluate_functional_gate(
            "noc-64b",
            actual,
            ttsim,
            corrupted,
            ttsim_artifact_id="ttsim-reference",
            silicon_artifact_id="silicon-reference",
        )
        self.assertFalse(failing.timing_eligible)
        gated = gate_timing_outcome(failing, timing)
        self.assertEqual(gated.outcome, "blocked")
        self.assertEqual(gated.artifact_ids, ())
        self.assertNotIn("sentinel", actual.model_dump_json())

    def test_predeclared_timing_comparison_reports_errors_and_dispersion(self):
        validation_root = ROOT / "simulator_detailed/configs/validation"
        admitted = admit("memory_replay_v1", validation_root / "memory_local.json")
        actual = normalize(admitted, admitted.execute(())[-1])
        metric = next(
            item
            for item in actual.metrics
            if item.window.boundary == "memory_service_begin_to_end"
        )
        identity = metric.interval
        if identity is None:
            self.fail("memory service metric omitted its interval identity")
        sample = identity.samples[0]
        conditions = admit_suite(
            validation_root / "synthetic_reference_suite.json"
        ).document.cases[0].conditions
        if conditions is None:
            self.fail("synthetic reference case omitted conditions")
        conditions = conditions.model_copy(
            update={
                "measurement": conditions.measurement.model_copy(
                    update={"value": metric.window}
                )
            }
        )
        policy = MetricPolicy(
            metric_id=metric.metric_id,
            unit="cycles",
            boundary="memory_service_begin_to_end",
            absolute_tolerance=0,
            relative_tolerance=0,
            rationale="exact unit-test interval",
        )
        boundary = BoundaryMap(
            boundary_id="unit-memory-window",
            case_family="dram_read_return",
            producer_zone="UNIT_MEMORY",
            simulator_boundary="memory_service_begin_to_end",
            clock_domain="aci",
            completion_scope="memory_service",
            simulator_interval=ModelIntervalSelection(
                metric_id=metric.metric_id,
                boundary="memory_service_begin_to_end",
                subject_id=identity.subject_id,
                resource_id=identity.resource_id,
                start_event_id=sample.start_event_id,
                end_event_id=sample.end_event_id,
                semantic_scope="memory_service",
            ),
            samples=RepetitionAggregation(
                repetition_ids=("0",), aggregation="none"
            ),
            comparison=policy,
        )
        provenance = ReferenceProvenance(
            classification="hardware_capture",
            producer="wormhole-unit-device",
            source_url=Metadata[str](state="known", value="unit-test-source"),
            revision=Metadata[str](state="known", value="unit-test-revision"),
            snapshot_sha256=Metadata[str](
                state="known", value=actual.source_result_sha256
            ),
            raw_artifact=ArtifactReference(
                path="raw.csv", sha256=actual.source_result_sha256
            ),
            extractor="unit_test_profiler",
            extractor_version="1",
            original_units=("cycles",),
            normalized_units=("cycles",),
        )
        reference = ValidationReference(
            kind="validation_reference",
            schema_version=1,
            reference_id="unit-hardware-reference",
            format="tt_metal_device_profiler_csv_v1",
            provenance=provenance,
            conditions=conditions,
            observations=actual,
            sample_statistics=SampleStatistics(
                sample_count=1,
                durations_cycles=(int(metric.value),),
                minimum_cycles=int(metric.value),
                maximum_cycles=int(metric.value),
                mean_absolute_deviation_cycles=0,
            ),
        )
        evidence = EvidenceReference(
            reference_id=reference.reference_id,
            document_sha256="c" * 64,
            classification="hardware_capture",
        )
        passing = compare_external_timing(
            case_id="unit-memory",
            boundary=boundary,
            admitted=admitted,
            conditions=conditions,
            actual=actual,
            reference=reference,
            evidence=evidence,
            artifact_id="unit-reference",
        )
        self.assertEqual(passing.outcome, "pass", passing.reason)
        self.assertIn("absolute_error=0", passing.reason)
        self.assertIn("sample_count=1", passing.reason)

        changed_metric = metric.model_copy(update={"value": metric.value + 1})
        changed_observations = actual.model_copy(
            update={
                "metrics": tuple(
                    changed_metric if item == metric else item
                    for item in actual.metrics
                )
            }
        )
        failing = compare_external_timing(
            case_id="unit-memory",
            boundary=boundary,
            admitted=admitted,
            conditions=conditions,
            actual=actual,
            reference=reference.model_copy(
                update={"observations": changed_observations}
            ),
            evidence=evidence,
            artifact_id="unit-reference",
        )
        self.assertEqual(failing.outcome, "fail")
        self.assertIn("signed_error=-1", failing.reason)

    def test_report_preserves_mixed_outcomes_and_portable_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, ttsim, silicon = _capture_pair(root / "captures")
            comparison = root / "comparison.json"
            comparison.write_text('{"comparison":"unit"}\n')
            model = root / "model.json"
            model.write_text('{"observation":"unit"}\n')
            outcomes = (
                ExternalOutcome(
                    stage="functional",
                    required=True,
                    outcome="pass",
                    reason="unit functional agreement",
                    executed=True,
                    case_id="noc-64b",
                    producer_id="ttsim-functional",
                    artifact_ids=("comparison:noc",),
                ),
                ExternalOutcome(
                    stage="timing",
                    required=True,
                    outcome="fail",
                    reason="actual=11; reference=10; signed_error=1",
                    executed=True,
                    case_id="dram-read-256b",
                    producer_id="wormhole-profiler",
                    boundary_id="dram-service-window",
                    artifact_ids=("comparison:noc",),
                ),
                ExternalOutcome(
                    stage="timing",
                    required=True,
                    outcome="blocked",
                    reason="firmware metadata unavailable",
                    executed=False,
                    case_id="compute-bf16-32",
                    producer_id="wormhole-profiler",
                    boundary_id="compute-service-window",
                    artifact_ids=(),
                ),
            )
            written = write_external_report(
                campaign=campaign,
                bundle_paths=(ttsim.document_path, silicon.document_path),
                artifacts=(
                    ReportArtifactInput(
                        artifact_id="model:noc",
                        path=model,
                        derived_from=("campaign",),
                        transform="model_execution",
                    ),
                    ReportArtifactInput(
                        artifact_id="comparison:noc",
                        path=comparison,
                        derived_from=(
                            "model:noc",
                            "bundle:unit-silicon-bundle",
                        ),
                        transform="interval_comparison",
                    ),
                ),
                outcomes=outcomes,
                claim_scope=("unit-test mixed campaign",),
                limitations=("synthetic fixtures are not external evidence",),
                output_directory=root / "report",
            )
            self.assertEqual(written.document.status, "fail")
            self.assertEqual(
                tuple(item.outcome for item in written.document.outcomes),
                ("pass", "fail", "blocked"),
            )
            self.assertEqual(len(written.document.boundaries), 3)
            for artifact in (
                written.document.campaign,
                *written.document.bundles,
                *written.document.artifacts,
            ):
                data = (written.document_path.parent / artifact.logical_path).read_bytes()
                self.assertEqual(bytes_digest(data), artifact.sha256)
                self.assertEqual(len(data), artifact.size_bytes)
            for bundle in written.document.bundles:
                admit_external_capture(
                    written.document_path.parent / bundle.logical_path
                )
            with self.assertRaisesRegex(ValueError, "already exists"):
                write_external_report(
                    campaign=campaign,
                    bundle_paths=(),
                    artifacts=(),
                    outcomes=outcomes,
                    claim_scope=("unit-test",),
                    limitations=("unit-test",),
                    output_directory=root / "report",
                )


if __name__ == "__main__":
    unittest.main()
