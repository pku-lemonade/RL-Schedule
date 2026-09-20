"""External campaign calibration planning and sealing tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.external_validation import (
    BoundaryMap,
    ExternalArtifactIdentity,
    ExternalValidationCampaign,
    ModelIntervalSelection,
    RepetitionAggregation,
)
from simulator_detailed.configs.schemas.validation import (
    ArtifactReference,
    CalibrationParameter,
    CalibrationPlan,
    CanonicalJSON,
    Metadata,
    MetricObservation,
    ReferenceConditions,
    ReferenceProvenance,
    ValidationReference,
)
from simulator_detailed.validation.adapters import Admission, admit
from simulator_detailed.validation.calibration import admit_calibration, calibrate
from simulator_detailed.validation.external import admit_external_campaign
from simulator_detailed.validation.external_calibration import (
    ExternalCalibrationBinding,
    generate_external_calibration_plan,
    publish_external_calibration_result,
)
from simulator_detailed.validation.external_campaign import (
    ReportArtifactInput,
    write_external_report,
)
from simulator_detailed.validation.identity import bytes_digest
from simulator_detailed.validation.normalize import normalize

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "simulator_detailed/configs/validation/calibration"


def _portable_memory_input(root: Path, name: str) -> tuple[Path, Admission]:
    document = json.loads((CALIBRATION / name).read_text())
    source = document["source"]
    field = "profile_path" if "profile_path" in source else "graph_path"
    source[field] = str((CALIBRATION / source[field]).resolve())
    path = root / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return path, admit("memory_replay_v1", path)


def _synthetic_interval_reference(
    root: Path,
    case_id: str,
    admitted: Admission,
    conditions: ReferenceConditions,
    metric_index: int,
) -> tuple[Path, ReferenceConditions, MetricObservation]:
    observation = normalize(admitted, admitted.execute(())[-1])
    service_metrics = tuple(
        metric
        for metric in observation.metrics
        if metric.window.boundary == "memory_service_begin_to_end"
        and metric.interval is not None
        and metric.interval.resource_id == 'resource:"l1-a"'
    )
    metric = service_metrics[metric_index]
    updated_conditions = conditions.model_copy(
        update={
            "measurement": conditions.measurement.model_copy(
                update={"value": metric.window}
            )
        }
    )
    selected = observation.model_copy(update={"metrics": (metric,)})
    raw_document = selected.model_dump(
        mode="json", exclude={"observation_id", "source_result_sha256"}
    )
    raw_data = json.dumps(
        raw_document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    reference_root = root / "references" / case_id
    reference_root.mkdir(parents=True)
    raw_path = reference_root / "raw.json"
    raw_path.write_bytes(raw_data)
    raw_sha256 = bytes_digest(raw_data)
    reference = ValidationReference(
        kind="validation_reference",
        schema_version=1,
        reference_id=f"synthetic-external:{case_id}",
        format="normalized_functional_v1",
        provenance=ReferenceProvenance(
            classification="synthetic",
            producer="synthetic_external_calibration_test",
            source_url=Metadata[str](
                state="unknown", reason="unit-test mechanics only"
            ),
            revision=Metadata[str](state="known", value="unit-test-v1"),
            snapshot_sha256=Metadata[str](state="known", value=raw_sha256),
            raw_artifact=ArtifactReference(path="raw.json", sha256=raw_sha256),
            extractor="wormhole_reference_import",
            extractor_version="1",
            original_units=("cycles",),
            normalized_units=("cycles",),
        ),
        conditions=updated_conditions,
        observations=None,
    )
    reference_path = reference_root / "reference.json"
    reference_path.write_text(reference.model_dump_json(indent=2) + "\n")
    return reference_path, updated_conditions, metric


def _campaign(root: Path):
    source_plan = CalibrationPlan.model_validate_json(
        (CALIBRATION / "memory_plan.json").read_bytes()
    )
    source_cases = (*source_plan.fit_cases, *source_plan.evaluation_cases)
    definitions = (
        ("memory-fit", "memory_fit.json", 0, source_cases[0].conditions),
        ("memory-heldout", "memory_heldout.json", 1, source_cases[1].conditions),
    )
    cases: list[dict[str, object]] = []
    references: dict[str, Path] = {}
    for case_id, filename, metric_index, conditions in definitions:
        if conditions is None:
            raise AssertionError("calibration fixture omitted conditions")
        input_path, admitted = _portable_memory_input(root, filename)
        reference_path, effective_conditions, metric = _synthetic_interval_reference(
            root, case_id, admitted, conditions, metric_index
        )
        references[case_id] = reference_path
        identity = metric.interval
        if identity is None:
            raise AssertionError("service metric omitted interval identity")
        sample = identity.samples[0]
        boundary = BoundaryMap(
            boundary_id=f"{case_id}-window",
            case_family="dram_read_return",
            producer_zone=f"{case_id.upper()}_ZONE",
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
            comparison={
                "metric_id": metric.metric_id,
                "unit": "cycles",
                "boundary": "memory_service_begin_to_end",
                "absolute_tolerance": 0,
                "relative_tolerance": 0,
                "rationale": "exact synthetic external calibration mechanics",
            },
        )
        input_data = input_path.read_bytes()
        cases.append(
            {
                "case_id": case_id,
                "family": "dram_read_return",
                "simulator_input": ExternalArtifactIdentity(
                    artifact_id=f"input:{case_id}",
                    logical_path=f"inputs/{filename}",
                    sha256=bytes_digest(input_data),
                    size_bytes=len(input_data),
                ).model_dump(mode="json"),
                "conditions": effective_conditions.model_dump(mode="json"),
                "boundary_maps": [boundary.model_dump(mode="json")],
                "producers": [
                    {
                        "producer_id": "ttsim-functional",
                        "outputs": [
                            {
                                "artifact_id": f"{case_id}:ttsim:functional",
                                "logical_path": f"{case_id}/ttsim-functional.json",
                                "role": "functional_record",
                            },
                            {
                                "artifact_id": f"{case_id}:ttsim:manifest",
                                "logical_path": f"{case_id}/ttsim-manifest.json",
                                "role": "capture_manifest",
                            },
                        ],
                    },
                    {
                        "producer_id": "wormhole-profiler",
                        "outputs": [
                            {
                                "artifact_id": f"{case_id}:wormhole:functional",
                                "logical_path": f"{case_id}/wormhole-functional.json",
                                "role": "functional_record",
                            },
                            {
                                "artifact_id": f"{case_id}:wormhole:profiler",
                                "logical_path": f"{case_id}/profile.csv",
                                "role": "profiler_csv",
                            },
                            {
                                "artifact_id": f"{case_id}:wormhole:manifest",
                                "logical_path": f"{case_id}/wormhole-manifest.json",
                                "role": "capture_manifest",
                            },
                        ],
                    },
                ],
                "budget": {
                    "repetitions": 1,
                    "warmup_repetitions": 0,
                    "timeout_seconds": 30,
                    "max_output_bytes": 131072,
                },
                "required_evidence": ["functional_reference", "silicon_timing"],
            }
        )
    campaign = ExternalValidationCampaign.model_validate_json(
        json.dumps(
            {
            "kind": "external_validation_campaign",
            "schema_version": 1,
            "campaign_id": "synthetic-external-calibration",
            "output_directory": "generated",
            "max_cases": 2,
            "max_invocations": 4,
            "max_total_output_bytes": 524288,
            "builds": [
                {
                    "build_id": "unit-build",
                    "source_url": "https://example.invalid/unit-source",
                    "revision": "1" * 40,
                    "source_snapshot_sha256": "2" * 64,
                    "configuration": CanonicalJSON(
                        text='{"purpose":"unit-test"}'
                    ).model_dump(mode="json"),
                    "artifacts": [
                        {
                            "artifact_id": "unit-binary",
                            "logical_path": "bin/unit",
                            "sha256": "3" * 64,
                            "size_bytes": 1,
                            "role": "host_binary",
                        }
                    ],
                }
            ],
            "producers": [
                {
                    "producer_id": "ttsim-functional",
                    "adapter": "ttsim_tt_metal_v1",
                    "build_id": "unit-build",
                },
                {
                    "producer_id": "wormhole-profiler",
                    "adapter": "wormhole_tt_metal_profiler_v1",
                    "build_id": "unit-build",
                },
            ],
                "cases": cases,
            }
        )
    )
    campaign_path = root / "campaign.json"
    campaign_path.write_text(campaign.model_dump_json(indent=2) + "\n")
    return admit_external_campaign(campaign_path), references, source_plan.parameters


class ExternalCalibrationTests(unittest.TestCase):
    def test_generated_plan_selects_case_metrics_and_runs_sealed_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, references, parameters = _campaign(root)
            bindings = (
                ExternalCalibrationBinding(
                    case_id="memory-fit",
                    boundary_id="memory-fit-window",
                    split="fit",
                    reference_path=references["memory-fit"],
                    wall_time_seconds=30,
                    max_aci_cycles=10000,
                    weight=1,
                    scale=1,
                ),
                ExternalCalibrationBinding(
                    case_id="memory-heldout",
                    boundary_id="memory-heldout-window",
                    split="evaluation",
                    reference_path=references["memory-heldout"],
                    wall_time_seconds=30,
                    max_aci_cycles=10000,
                    weight=1,
                    scale=1,
                ),
            )
            first = generate_external_calibration_plan(
                campaign=campaign,
                bindings=bindings,
                parameters=parameters,
                evidence_scope="synthetic_demonstration",
                output_directory=root / "generated-one",
            )
            second = generate_external_calibration_plan(
                campaign=campaign,
                bindings=bindings,
                parameters=parameters,
                evidence_scope="synthetic_demonstration",
                output_directory=root / "generated-two",
            )
            self.assertEqual(first.plan_path.read_bytes(), second.plan_path.read_bytes())
            plan = first.admission.plan
            self.assertIsNotNone(plan.source_campaign)
            self.assertNotEqual(
                plan.fit_cases[0].metric_ids,
                plan.evaluation_cases[0].metric_ids,
            )
            self.assertEqual(
                {item.metric_id for item in plan.metrics},
                {
                    *plan.fit_cases[0].metric_ids,
                    *plan.evaluation_cases[0].metric_ids,
                },
            )
            result = calibrate(first.plan_path)
            self.assertEqual(result.status, "pass", result.reason)
            self.assertEqual(result.evidence_scope, "synthetic_demonstration")
            selection = result.selection
            if selection is None:
                self.fail("bounded synthetic fit omitted its selection")
            self.assertEqual(selection.values[0].value, 2)
            self.assertEqual(len(result.candidates), 3)
            self.assertEqual(result.tied_candidate_ids, ())
            material = publish_external_calibration_result(
                admission=first.admission,
                result=result,
                result_artifact_id="calibration-result",
                derived_from=("calibration-plan",),
                output_path=root / "calibration-result.json",
            )
            self.assertEqual(
                tuple(item.stage for item in material.outcomes),
                ("calibration", "evaluation"),
            )
            self.assertTrue(
                all(item.outcome == "pass" for item in material.outcomes)
            )
            self.assertIn("candidate history", material.outcomes[0].reason)
            self.assertIn("fit sample statistics", material.outcomes[0].reason)
            self.assertIn("absolute_error", material.outcomes[1].reason)
            self.assertIn(
                "evaluation sample statistics", material.outcomes[1].reason
            )
            self.assertTrue(
                any(item.startswith("case conditions:") for item in material.claim_scope)
            )
            self.assertIn(
                "result is not measured hardware calibration",
                material.limitations,
            )
            written = write_external_report(
                campaign=campaign,
                bundle_paths=(),
                artifacts=(
                    ReportArtifactInput(
                        artifact_id="calibration-plan",
                        path=first.plan_path,
                        derived_from=("campaign",),
                        transform="external_calibration_plan",
                    ),
                    material.artifact,
                ),
                outcomes=material.outcomes,
                claim_scope=material.claim_scope,
                limitations=material.limitations,
                output_directory=root / "report",
            )
            self.assertEqual(written.document.status, "pass")
            result_artifact = next(
                item
                for item in written.document.artifacts
                if item.artifact_id == "calibration-result"
            )
            self.assertEqual(
                result_artifact.sha256,
                bytes_digest((root / "calibration-result.json").read_bytes()),
            )

    def test_campaign_raw_and_scope_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, references, parameters = _campaign(root)
            bindings = (
                ExternalCalibrationBinding(
                    case_id="memory-fit",
                    boundary_id="memory-fit-window",
                    split="fit",
                    reference_path=references["memory-fit"],
                    wall_time_seconds=30,
                    max_aci_cycles=10000,
                    weight=1,
                    scale=1,
                ),
                ExternalCalibrationBinding(
                    case_id="memory-heldout",
                    boundary_id="memory-heldout-window",
                    split="evaluation",
                    reference_path=references["memory-heldout"],
                    wall_time_seconds=30,
                    max_aci_cycles=10000,
                    weight=1,
                    scale=1,
                ),
            )
            with self.assertRaisesRegex(ValueError, "classification"):
                generate_external_calibration_plan(
                    campaign=campaign,
                    bindings=bindings,
                    parameters=parameters,
                    evidence_scope="measured_conditions",
                    output_directory=root / "measured",
                )
            self.assertFalse((root / "measured").exists())
            generated = generate_external_calibration_plan(
                campaign=campaign,
                bindings=bindings,
                parameters=parameters,
                evidence_scope="synthetic_demonstration",
                output_directory=root / "generated",
            )
            packaged_root = generated.plan_path.parent
            campaign_copy = packaged_root / "campaign.json"
            campaign_copy.write_bytes(campaign_copy.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "hash|digest|SHA"):
                admit_calibration(generated.plan_path)

            campaign_copy.write_bytes(campaign.document_path.read_bytes())
            raw = packaged_root / "references/0/raw.json"
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "hash|digest|SHA"):
                admit_calibration(generated.plan_path)

            compute_parameter = CalibrationParameter(
                parameter_id="compute-rate",
                target={
                    "kind": "compute",
                    "rate_id": "matrix",
                    "field": "work_per_native_cycle",
                },
                lower=1,
                upper=2,
                candidates=(1, 2),
            )
            with self.assertRaisesRegex(ValueError, "parameter kind"):
                generate_external_calibration_plan(
                    campaign=campaign,
                    bindings=bindings,
                    parameters=(compute_parameter,),
                    evidence_scope="synthetic_demonstration",
                    output_directory=root / "wrong-target",
                )


if __name__ == "__main__":
    unittest.main()
