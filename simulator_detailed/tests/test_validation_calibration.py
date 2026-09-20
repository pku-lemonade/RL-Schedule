"""Independent finite-search arithmetic and fit/held-out isolation tests."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.configs.schemas.validation import (
    CalibrationResult,
    CaseResult,
    CheckResult,
    ParameterValue,
)
from simulator_detailed.validation.calibration import (
    admit_calibration,
    apply_candidate,
    calibrate,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "configs/validation/calibration"


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.memory = calibrate(EXAMPLES / "memory_plan.json")
        cls.compute = calibrate(EXAMPLES / "compute_plan.json")

    def copied(self, directory, mode="memory"):
        target = Path(directory) / "calibration"
        shutil.copytree(EXAMPLES, target)
        # Preserve explicit source resolution after relocating the fixture tree.
        for path in target.glob("*.json"):
            document = json.loads(path.read_text())
            if document.get("kind") in ("memory_replay", "compute_workload"):
                source = document.get("memory", document)["source"]
                source["graph_path"] = str((EXAMPLES / source["graph_path"]).resolve())
                path.write_text(json.dumps(document))
        return target / (mode + "_plan.json")

    def edit(self, path, change):
        document = json.loads(path.read_text())
        change(document)
        path.write_text(json.dumps(document))

    def changed_reference(self, plan_path, split, change):
        plan = json.loads(plan_path.read_text())
        case = plan[split][0]
        ref_path = plan_path.parent / case["reference"]["document"]["path"]
        reference = json.loads(ref_path.read_text())
        raw_path = ref_path.parent / reference["provenance"]["raw_artifact"]["path"]
        raw = json.loads(raw_path.read_text())
        change(raw)
        raw_path.write_text(json.dumps(raw))
        reference["provenance"]["raw_artifact"]["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        ref_path.write_text(json.dumps(reference))
        case["reference"]["document"]["sha256"] = hashlib.sha256(ref_path.read_bytes()).hexdigest()
        plan_path.write_text(json.dumps(plan))

    def test_memory_search_matches_independent_arithmetic(self):
        # Nine granules: cycles = 9 * (0.5 + 4/rate) * 2.
        self.assertEqual([c.fit_loss for c in self.memory.candidates], [18, 0, 9])
        self.assertEqual(self.memory.selection.values[0].value, 4)
        self.assertEqual(self.memory.status, "pass")
        self.assertEqual(self.memory.evaluation_runs[0].observations[-1].metrics[0].value, 51)

    def test_compute_quantization_ties_use_declared_order(self):
        # Reader 9 + operand 2 + (setup 1 + ceil(2/rate)) + result 1.
        self.assertEqual([c.fit_loss for c in self.compute.candidates], [1, 0, 0])
        self.assertEqual(self.compute.selection.values[0].value, 2)
        self.assertEqual(self.compute.tied_candidate_ids, ("candidate:1", "candidate:2"))
        self.assertEqual(self.compute.evaluation_runs[0].observations[-1].metrics[0].value, 17)

    def test_synthetic_fitting_never_claims_measured_calibration(self):
        for result in (self.memory, self.compute):
            self.assertEqual(result.evidence_scope, "synthetic_demonstration")
            self.assertTrue(all(r.classification == "synthetic" for r in result.references))
            self.assertTrue(result.identity.source.bundle_sha256)

    def test_source_files_and_evidence_unchanged(self):
        admitted = admit_calibration(EXAMPLES / "memory_plan.json")
        before = admitted.fit[0].admitted.path.read_bytes()
        original = json.loads(before)
        with tempfile.TemporaryDirectory() as directory:
            changed = apply_candidate(admitted.fit[0].admitted, admitted.plan.parameters,
                                      (ParameterValue(parameter_id="rate", value=8.0),), Path(directory) / "candidate")
            copied = json.loads(changed.path.read_text())
        self.assertEqual(admitted.fit[0].admitted.path.read_bytes(), before)
        for expected, actual in zip(original["resources"], copied["resources"], strict=True):
            if expected["resource_id"] == "l1-a":
                expected["service"]["bytes_per_cycle"] = 8
            self.assertEqual(actual, expected)
        self.assertEqual(original["runtime"], copied["runtime"])
        self.assertEqual(original["buffers"], copied["buffers"])

    def test_perfect_fit_can_fail_heldout_without_refitting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.changed_reference(path, "evaluation_cases", lambda raw: raw["metrics"][0].update(value=999))
            result = calibrate(path)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.selection.values, self.memory.selection.values)
        self.assertEqual([c.fit_loss for c in result.candidates], [18, 0, 9])
        self.assertEqual(result.selection.configuration_sha256, self.memory.selection.configuration_sha256)
        self.assertEqual(result.selection.fit_evidence_sha256, self.memory.selection.fit_evidence_sha256)

    def test_seal_exists_before_heldout_execution(self):
        from simulator_detailed.validation import calibration
        original = calibration._evaluate
        frozen_seen = []
        def evaluation(admission, frozen, directory):
            self.assertTrue(frozen.selection_sha256)
            self.assertTrue(frozen.fit_evidence_sha256)
            frozen_seen.append(frozen)
            return original(admission, frozen, directory)
        with patch.object(calibration, "_evaluate", side_effect=evaluation):
            result = calibrate(EXAMPLES / "memory_plan.json")
        self.assertEqual(result.selection, frozen_seen[0])

    def test_threshold_change_changes_identity_and_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["metrics"][0].update(absolute_tolerance=2))
            result = calibrate(path)
        self.assertEqual(result.selection.values, self.memory.selection.values)
        self.assertNotEqual(result.plan_sha256, self.memory.plan_sha256)
        self.assertNotEqual(result.selection.selection_sha256, self.memory.selection.selection_sha256)

    def test_declared_candidate_order_is_part_of_the_seal_and_tie_break(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory, mode="compute")
            self.edit(
                path,
                lambda document: document["parameters"][0].update(
                    candidates=[4, 2, 1]
                ),
            )
            result = calibrate(path)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.selection.values[0].value, 4)
        self.assertEqual(result.tied_candidate_ids, ("candidate:0", "candidate:1"))
        self.assertEqual(result.evaluation_checks[0].outcome, "fail")
        self.assertNotEqual(result.plan_sha256, self.compute.plan_sha256)
        self.assertNotEqual(
            result.selection.selection_sha256,
            self.compute.selection.selection_sha256,
        )

    def test_all_rejected_candidates_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["parameters"][0]["target"].update(resource_id="absent"))
            result = calibrate(path)
        self.assertEqual(len(result.candidates), 3)
        self.assertTrue(all(c.outcome == "rejected" for c in result.candidates))
        self.assertIsNone(result.selection)
        self.assertEqual(result.status, "fail")
        self.assertFalse(result.evaluation_runs)

    def test_missing_evidence_has_no_default_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["fit_cases"][0]["reference"]["document"].update(path="missing.json"))
            result = calibrate(path)
        self.assertEqual(result.status, "incomplete")
        self.assertIsNone(result.selection)
        self.assertTrue(all(c.outcome == "blocked" for c in result.candidates))

    def test_all_executed_candidates_fail_without_winner(self):
        def failed(case, _):
            return CaseResult(case_id=case.case_id, adapter=case.adapter, execution="unavailable", reason="injected runtime assertion",
                              identity=None, observations=(), checks=(CheckResult(check_id="runtime", required=True, tier="model_invariant", outcome="fail", executed=True, reason="injected assertion"),))
        with patch("simulator_detailed.validation.calibration.run_case", side_effect=failed):
            result = calibrate(EXAMPLES / "memory_plan.json")
        self.assertIsNone(result.selection)
        self.assertEqual(result.status, "fail")
        self.assertTrue(all(c.outcome == "failed" for c in result.candidates))

    def test_exported_selection_seal_detects_tampering(self):
        document = self.memory.model_dump(mode="json")
        document["selection"]["selection_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "frozen selection digest"):
            CalibrationResult.model_validate_json(json.dumps(document))
        document = self.memory.model_dump(mode="json")
        document["candidates"][0]["fit_checks"][0]["reason"] = "tampered evidence"
        with self.assertRaisesRegex(ValueError, "fit evidence digest"):
            CalibrationResult.model_validate_json(json.dumps(document))

    def test_renamed_duplicate_workload_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["evaluation_cases"][0].update(input_path=d["fit_cases"][0]["input_path"]))
            with patch("simulator_detailed.validation.calibration.run_case", side_effect=AssertionError("executed")), self.assertRaisesRegex(ValueError, "semantic"):
                calibrate(path)

    def test_reused_capture_group_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["evaluation_cases"][0].update(capture_group=d["fit_cases"][0]["capture_group"]))
            with self.assertRaisesRegex(ValueError, "capture groups overlap"):
                admit_calibration(path)

    def test_forged_capture_group_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d["evaluation_cases"][0].update(capture_group="forged"))
            with self.assertRaisesRegex(ValueError, "capture-group"):
                admit_calibration(path)

    def test_budgets_and_structural_targets_rejected(self):
        for mutation in (lambda d: d.update(max_candidates=2), lambda d: d.update(max_case_runs=3),
                         lambda d: d["parameters"][0]["target"].update(field="native_clock_hz")):
            with tempfile.TemporaryDirectory() as directory:
                path = self.copied(directory)
                self.edit(path, mutation)
                with self.assertRaises(ValueError):
                    admit_calibration(path)

    def test_declared_measured_plan_blocks_synthetic_references(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.edit(path, lambda d: d.update(evidence_scope="measured_conditions"))
            result = calibrate(path)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.evidence_scope, "unvalidated")
        self.assertIsNone(result.selection)

    def test_invalid_zero_reference_tolerance_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            self.changed_reference(path, "fit_cases", lambda raw: raw["metrics"][0].update(value=0))
            self.edit(path, lambda d: d["metrics"][0].update(relative_tolerance=0.1))
            with self.assertRaisesRegex(ValueError, "zero reference"):
                admit_calibration(path)

    def test_weighted_scaled_loss_has_independent_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.copied(directory)
            for split, count in (("fit_cases", 37), ("evaluation_cases", 68)):
                def add_metric(raw, value=count):
                    metric = copy.deepcopy(raw["metrics"][0])
                    metric.update(metric_id="memory_service_bytes", value=value, unit="bytes", clock_domain=None)
                    raw["metrics"].append(metric)
                self.changed_reference(path, split, add_metric)
            def policies(d):
                d["metrics"][0].update(weight=2, scale=3)
                second = copy.deepcopy(d["metrics"][0])
                second.update(metric_id="memory_service_bytes", unit="bytes", weight=1, scale=1)
                d["metrics"].append(second)
            self.edit(path, policies)
            result = calibrate(path)
        for actual, expected in zip([c.fit_loss for c in result.candidates], [13 / 3, 1 / 3, 7 / 3], strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(result.status, "pass")


if __name__ == "__main__":
    unittest.main()
