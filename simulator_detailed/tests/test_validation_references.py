"""Independent synthetic import fixtures and conservative compatibility failures."""

import copy
import hashlib
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from simulator_detailed.configs.schemas.validation import (
    CheckSelection,
    EvidenceReference,
    MetricPolicy,
    ValidationReference,
)
from simulator_detailed.validation.adapters import admit
from simulator_detailed.validation.comparison import (
    IncompatibleReference,
    compare,
    metric_pair,
    tolerance_pass,
)
from simulator_detailed.validation.normalize import normalize
from simulator_detailed.validation.references import import_reference
from simulator_detailed.validation.runner import admit_suite, run_suite

ROOT = Path(__file__).resolve().parents[1] / "configs/validation"
REFS = ROOT / "references"


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admitted = admit("memory_replay_v1", ROOT / "memory_local.json")
        cls.actual = normalize(cls.admitted, cls.admitted.execute(())[0])
        cls.suite = admit_suite(ROOT / "synthetic_reference_suite.json")
        cls.functional = import_reference(REFS / "synthetic_functional.json")

    def csv_variant(self, *, raw_transform=lambda value: value, document_transform=lambda value: None):
        document = json.loads((REFS / "synthetic_profiler.json").read_text())
        raw = raw_transform((REFS / "synthetic_profiler.csv").read_text())
        document["provenance"]["raw_artifact"] = {"path": "raw.csv", "sha256": hashlib.sha256(raw.encode()).hexdigest()}
        document_transform(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sidecar.json"
            path.write_text(json.dumps(document))
            path.with_name("raw.csv").write_text(raw)
            return import_reference(path)

    def test_functional_suite_is_model_evidence_only(self):
        report = run_suite(ROOT / "synthetic_reference_suite.json")
        self.assertEqual(report.status, "pass", report.cases[0].checks)
        self.assertEqual(report.functional_reference, "unvalidated")
        self.assertEqual(report.silicon_timing, "unvalidated")
        self.assertEqual(report.references[0].classification, "synthetic")

    def comparison(self, reference=None, **updates):
        case = self.suite.document.cases[0]
        selection = case.checks[0].model_copy(update=updates)
        reference = reference or self.functional
        evidence = EvidenceReference(reference_id=reference.reference_id, document_sha256="a" * 64, classification=reference.provenance.classification)
        return compare(selection, self.admitted, case.conditions, self.actual, reference, evidence)

    def test_global_event_order_does_not_define_functionality(self):
        observation = self.functional.observations
        reference = self.functional.model_copy(update={"observations": observation.model_copy(update={"events": tuple(reversed(observation.events))})})
        self.assertEqual(self.comparison(reference).outcome, "pass")

    def test_mismatched_addressed_effect_fails(self):
        observation = self.functional.observations
        effect = observation.effects[0].model_copy(update={"size_bytes": 32})
        reference = self.functional.model_copy(update={"observations": observation.model_copy(update={"effects": (effect,)})})
        self.assertEqual(self.comparison(reference).outcome, "fail")

    def test_synthetic_cannot_authorize_external_tier(self):
        self.assertEqual(self.comparison(tier="functional_reference").outcome, "blocked")

    def test_embedded_observations_are_verified_against_raw(self):
        document = self.functional.model_dump(mode="json")
        document["observations"]["effects"][0]["size_bytes"] = 2
        document["provenance"]["raw_artifact"]["path"] = str(REFS / "synthetic_functional_raw.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "disagree"):
                import_reference(path)

    def test_raw_hash_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "hash|digest|SHA"):
            self.csv_variant(document_transform=lambda d: d["provenance"]["raw_artifact"].update(sha256="0" * 64))

    def test_large_integer_subtraction_warmup_mean_dispersion(self):
        reference = import_reference(REFS / "synthetic_profiler.json")
        self.assertEqual(reference.sample_statistics.durations_cycles, (25, 27, 29))
        self.assertEqual(reference.sample_statistics.sample_count, 3)
        self.assertAlmostEqual(reference.sample_statistics.mean_absolute_deviation_cycles, 4 / 3)
        self.assertEqual(reference.observations.metrics[0].value, 27)
        self.assertEqual(reference.observations.events[0].time.value, 2**60 + 3)
        self.assertEqual(reference.observations.metrics[0].window.excluded_warmups, ("0",))

    def test_median_is_declared_and_deterministic(self):
        def change(d):
            d["profiler"]["aggregation"] = "median"
            d["conditions"]["measurement"]["value"]["aggregation"] = "median"
        self.assertEqual(self.csv_variant(document_transform=change).observations.metrics[0].value, 27)

    def test_supported_profiler_interval_retains_exact_sample_identities(self):
        def change(document):
            document["profiler"]["boundary"] = "memory_service_begin_to_end"
            document["conditions"]["measurement"]["value"]["boundary"] = "memory_service_begin_to_end"

        metric = self.csv_variant(document_transform=change).observations.metrics[0]
        self.assertEqual(metric.completion_scope, "interval")
        self.assertEqual(metric.interval.semantic_scope, "memory_service")
        self.assertEqual(metric.interval.resource_id, "profiler_resource")
        self.assertEqual(
            tuple((sample.repetition_id, sample.start_event_id, sample.end_event_id) for sample in metric.interval.samples),
            (("1", "run:1:begin", "run:1:end"), ("2", "run:2:begin", "run:2:end"), ("3", "run:3:begin", "run:3:end")),
        )

    def test_ambiguous_and_missing_pairs_rejected(self):
        raw = (REFS / "synthetic_profiler.csv").read_text()
        for changed in (raw + raw.splitlines()[2] + "\n", "\n".join(raw.splitlines()[:-1])):
            with self.assertRaisesRegex(ValueError, "ambiguous|missing"):
                self.csv_variant(raw_transform=lambda _, value=changed: value)

    def test_cross_core_pair_not_subtracted(self):
        def change(raw):
            lines = raw.splitlines()
            lines[-1] = lines[-1].replace("0,1,1,BRISC", "0,2,1,BRISC")
            return "\n".join(lines)
        with self.assertRaisesRegex(ValueError, "same-device/core"):
            self.csv_variant(raw_transform=change)

    def test_unsupported_header_phase_or_fractional_counter_rejected(self):
        for old, new in (("timer_id", "timer"), (",begin,", ",middle,"), (str(2**60 + 3), "3.5")):
            with self.assertRaises(ValueError):
                self.csv_variant(raw_transform=lambda raw, a=old, b=new: raw.replace(a, b))

    def test_architecture_and_frequency_contradictions_rejected(self):
        for old, new in (("ARCH: synthetic", "ARCH: grayskull"), ("MHz]: 500", "MHz]: 501")):
            with self.assertRaisesRegex(ValueError, "contradict"):
                self.csv_variant(raw_transform=lambda raw, a=old, b=new: raw.replace(a, b))

    def test_unknown_metadata_retained_but_blocks_comparison(self):
        reference = self.functional.model_copy(update={"conditions": self.functional.conditions.model_copy(update={"architecture": self.functional.conditions.architecture.model_copy(update={"state": "unknown", "value": None, "reason": "not supplied"})})})
        self.assertEqual(self.comparison(reference).outcome, "blocked")

    def test_changed_layout_workload_and_mapping_block(self):
        for field in ("enabled_layout", "workload", "mapping"):
            metadata = getattr(self.functional.conditions, field)
            changed = metadata.model_copy(update={"value": metadata.value.model_copy(update={"text": "{}"})})
            reference = self.functional.model_copy(update={"conditions": self.functional.conditions.model_copy(update={field: changed})})
            self.assertEqual(self.comparison(reference).outcome, "blocked")

    def test_ttsim_and_synthetic_origin_cannot_claim_hardware(self):
        original = json.loads((REFS / "synthetic_functional.json").read_text())
        for producer in ("ttsim", "ttsim-v2", "handwritten_synthetic_fixture"):
            document = copy.deepcopy(original)
            document["provenance"].update(producer=producer, classification="hardware_capture")
            with self.assertRaises(ValueError):
                ValidationReference.model_validate_json(json.dumps(document))

    def policy(self):
        return MetricPolicy(metric_id="elapsed", unit="cycles", boundary="simulation_start_to_snapshot", absolute_tolerance=0.0, relative_tolerance=0.0, rationale="exact synthetic arithmetic")

    def test_zero_reference_requires_explicit_absolute_or_exact_policy(self):
        self.assertTrue(tolerance_pass(Fraction(0), Fraction(0), self.policy()))
        with self.assertRaises(IncompatibleReference):
            tolerance_pass(Fraction(0), Fraction(0), self.policy().model_copy(update={"relative_tolerance": 0.1}))

    def test_aggregation_and_domain_mismatch_block(self):
        reference = import_reference(REFS / "synthetic_profiler.json")
        with self.assertRaisesRegex(IncompatibleReference, "aggregation"):
            metric_pair(self.actual, reference.observations, self.policy(), {})

    def single_csv(self):
        def change(document):
            document["profiler"].update(run_ids=[2], warmup_run_ids=[], aggregation="none")
            document["conditions"]["measurement"]["value"].update(repetitions=1, excluded_warmups=[], aggregation="none")
        return self.csv_variant(document_transform=change)

    def test_single_run_metric_comparison_and_missing_timing_metadata(self):
        reference = self.single_csv()
        selection = CheckSelection(check_id="elapsed", check="metrics", required=True, tier="model_invariant", requirements=("VA-D07",),
                                   reference_id=reference.reference_id, metrics=(self.policy(),))
        evidence = EvidenceReference(reference_id=reference.reference_id, document_sha256="b" * 64, classification="synthetic")
        conditions = self.suite.document.cases[0].conditions
        result = compare(selection, self.admitted, conditions, self.actual, reference, evidence)
        self.assertEqual(result.outcome, "pass", result.reason)
        for field in ("software", "firmware", "instrumentation", "device_scope"):
            original = getattr(reference.conditions, field)
            missing = original.model_copy(update={"state": "unknown", "value": None, "reason": "not supplied"})
            changed = reference.model_copy(update={"conditions": reference.conditions.model_copy(update={field: missing})})
            result = compare(selection, self.admitted, conditions, self.actual, changed, evidence)
            self.assertEqual(result.outcome, "blocked", field)

    def test_unmapped_clock_and_unmatched_kernel_window_block(self):
        reference = self.single_csv().observations
        metric = reference.metrics[0]
        bad_metric = metric.model_copy(update={"clock_domain": "different_core"})
        with self.assertRaisesRegex(IncompatibleReference, "clock-domain"):
            metric_pair(self.actual, reference.model_copy(update={"metrics": (bad_metric,)}), self.policy(), {})
        bad_window = metric.window.model_copy(update={"boundary": "host_dispatch_plus_kernel"})
        with self.assertRaisesRegex(IncompatibleReference, "boundary"):
            metric_pair(self.actual, reference.model_copy(update={"metrics": (metric.model_copy(update={"window": bad_window}),)}), self.policy(), {})

    def test_checked_in_negative_fixtures(self):
        for name in ("negative_hash", "negative_grayskull"):
            with self.assertRaises(ValueError):
                import_reference(REFS / (name + ".json"))
        reference = import_reference(REFS / "negative_unknown_metadata.json")
        self.assertEqual(self.comparison(reference).outcome, "blocked")

    def test_missing_external_optional_and_required(self):
        document = json.loads((ROOT / "synthetic_reference_suite.json").read_text())
        document["cases"][0]["input_path"] = str(ROOT / "memory_local.json")
        document["references"][0]["document"]["path"] = "missing.json"
        document["cases"][0]["checks"].append({"check_id": "offline", "check": "drain", "required": True, "tier": "model_invariant", "requirements": ["VA-D05"]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            for required, status in ((True, "incomplete"), (False, "pass")):
                document["cases"][0]["checks"][0]["required"] = required
                path.write_text(json.dumps(document))
                result = run_suite(path)
                self.assertEqual(result.status, status)
                self.assertEqual(result.cases[0].checks[0].outcome, "blocked")

    def test_tensor_values_remain_unsupported(self):
        from simulator_detailed.validation.runner import local_check
        case = self.suite.document.cases[0]
        selection = CheckSelection(check_id="tensor", check="tensor_values", required=True, tier="model_invariant", requirements=("VA-D04",))
        self.assertEqual(local_check(selection, case, self.admitted, self.admitted.execute(())[0], self.actual.observation_id).outcome, "unsupported")


if __name__ == "__main__":
    unittest.main()
