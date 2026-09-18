"""Isolation, admission, real subprocess timeout and named regression outcomes."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.configs.schemas.validation import RegressionGate
from simulator_detailed.validation.gates import gate_command, run_gate
from simulator_detailed.validation.runner import admit_suite, run_case, run_suite

CATALOG = Path(__file__).resolve().parents[1] / "configs/validation/offline.json"


class RunnerTests(unittest.TestCase):
    def test_finite_catalog(self):
        report = run_suite(CATALOG)
        self.assertEqual(report.status, "pass", [(c.case_id, [(s.check_id, s.reason) for s in c.checks if s.outcome != "pass"]) for c in report.cases])
        self.assertEqual(len(report.cases), 19)
        self.assertEqual(report.silicon_timing, "unvalidated")
        by_id = {c.case_id: c for c in report.cases}
        self.assertEqual(by_id["memory_incomplete"].execution, "incomplete")
        self.assertEqual(len(by_id["memory_resume"].observations), 3)
        self.assertEqual(len(by_id["compute_resume"].observations), 3)
        for case in report.cases:
            self.assertTrue(case.identity.inputs)
            for metric in case.observations[-1].metrics:
                self.assertTrue(metric.numerator and metric.denominator and metric.window.boundary)

    def test_cases_are_deterministic_and_isolated(self):
        admitted = admit_suite(CATALOG)
        first = run_case(admitted.document.cases[0], admitted.cases[0])
        second = run_case(admitted.document.cases[0], admitted.cases[0])
        self.assertEqual(first, second)

    def test_all_admission_precedes_any_execution(self):
        document = json.loads(CATALOG.read_text())
        for case in document["cases"]:
            case["input_path"] = str((CATALOG.parent / case["input_path"]).resolve())
        document["cases"][-1]["input_path"] = "/missing/workload.json"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(document))
            with patch("simulator_detailed.validation.runner.isolated_run", side_effect=AssertionError("allocated before admission")), self.assertRaises(FileNotFoundError):
                run_suite(path)

    def test_real_wall_timeout_is_a_failure_not_unavailable_pass(self):
        admitted = admit_suite(CATALOG)
        case = admitted.document.cases[0]
        case = case.model_copy(update={"budget": case.budget.model_copy(update={"wall_time_seconds": 0.000001})})
        result = run_case(case, admitted.cases[0])
        self.assertEqual(result.execution, "unavailable")
        self.assertFalse(result.observations)
        self.assertEqual(result.checks[-1].outcome, "fail")
        self.assertIn("wall-time", result.checks[-1].reason)

    def test_worker_runtime_error_is_failure(self):
        admitted = admit_suite(CATALOG)
        with patch("simulator_detailed.validation.runner.isolated_run", side_effect=RuntimeError("assertion failed")):
            result = run_case(admitted.document.cases[0], admitted.cases[0])
        self.assertEqual(result.checks[-1].outcome, "fail")

    def test_invalid_resume_is_rejected(self):
        document = json.loads(CATALOG.read_text())
        document["cases"][0]["resume_at_aci_cycles"] = [1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                admit_suite(path)

    def gate(self, name="compute_unittest"):
        return RegressionGate(gate_id="gate", gate=name, required=True, wall_time_seconds=5.0, requirements=("VA-D10",))

    def test_gate_missing_tool_is_blocked(self):
        with patch("simulator_detailed.validation.gates.importlib.util.find_spec", return_value=None):
            result = run_gate(self.gate("strict_pyright"))
        self.assertEqual(result.outcome, "blocked")
        self.assertFalse(result.executed)

    def test_gate_executed_nonzero_is_failure(self):
        process = subprocess.CompletedProcess([], 1, "type or assertion error", "")
        with patch("simulator_detailed.validation.gates.subprocess.run", return_value=process):
            result = run_gate(self.gate("strict_pyright"))
        self.assertEqual(result.outcome, "fail")
        self.assertTrue(result.executed)

    def test_unittest_skips_and_assertions_remain_distinct(self):
        counts = {"passed": 3, "failed": 0, "skipped": [["test", "optional dependency"]], "expected_failures": 0}
        for failures, outcome in ((0, "blocked"), (1, "fail")):
            counts["failed"] = failures
            process = subprocess.CompletedProcess([], 0, json.dumps(counts), "")
            with patch("simulator_detailed.validation.gates.subprocess.run", return_value=process):
                result = run_gate(self.gate())
            self.assertEqual(result.outcome, outcome)
            self.assertIn("optional dependency", result.reason)

    def test_empty_gate_does_not_pass(self):
        counts = {"passed": 0, "failed": 0, "skipped": [], "expected_failures": 0}
        with patch("simulator_detailed.validation.gates.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")):
            self.assertEqual(run_gate(self.gate()).outcome, "not_run")

    def test_gate_timeout_is_failure(self):
        with patch("simulator_detailed.validation.gates.subprocess.run", side_effect=subprocess.TimeoutExpired("fixed", 1)):
            self.assertEqual(run_gate(self.gate()).outcome, "fail")

    def test_registry_does_not_accept_shell_programs(self):
        command = gate_command("strict_pyright")
        self.assertIsInstance(command, list)
        for value in ("echo hacked", "compute_unittest; touch /tmp/hacked"):
            with self.assertRaises(ValueError):
                self.gate(value)
        document = copy.deepcopy(json.loads(CATALOG.read_text()))
        document["cases"][0]["adapter"] = "eval"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                admit_suite(path)


if __name__ == "__main__":
    unittest.main()
