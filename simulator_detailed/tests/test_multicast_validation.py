"""Validation, oracle and CLI coverage for the finite multicast child."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from simulator_detailed.topology_compatibility import (
    legacy_event_rows,
    require_legacy_nocs,
)
from simulator_detailed.validation.adapters import admit
from simulator_detailed.validation.audits import UnsupportedAudit, audit
from simulator_detailed.validation.normalize import normalize
from simulator_detailed.validation.runner import run_suite

ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "simulator_detailed/configs/multicast_workloads/distribute_compute_collect.json"
PROFILE_WORKLOAD = ROOT / "simulator_detailed/configs/multicast_workloads/wormhole_b0_multicast_assumed.json"


class MulticastValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admission = admit("multicast_sync_v1", WORKLOAD, horizon=10000)
        cls.raw = cls.admission.execute(())[0]

    def test_adapter_normalization_and_scoped_audits(self):
        observation = normalize(self.admission, self.raw)
        self.assertEqual(observation.execution, "complete")
        self.assertFalse(observation.pending)
        self.assertEqual(len(observation.effects), 10)
        self.assertEqual({effect.offset_bytes for effect in observation.effects}, {128})
        self.assertFalse(observation.metrics)
        expected_events = len(self.raw["events"]) + len(self.raw["scalar"]["events"]) + sum(
            len(result["events"]) + len(result["transport"]["events"]) for result in self.raw["memory"])
        self.assertEqual(len(observation.events), expected_events)
        self.assertTrue(all(event.details is not None for event in observation.events))
        for check in ("routing", "multicast", "bounded_execution"):
            self.assertIn("passed", audit(check, self.admission, self.raw))
        for check in ("packet_accounting", "memory_service", "ownership", "synchronization", "compute_work", "drain"):
            with self.subTest(check=check), self.assertRaises(UnsupportedAudit):
                audit(check, self.admission, self.raw)
        with self.assertRaisesRegex(ValueError, "before its memory dependency"):
            audit("causality", self.admission, self.raw)
        with self.assertRaisesRegex(ValueError, "interruption/resume"):
            self.admission.execute((5.0,))

    def test_oracle_rejects_missing_or_duplicate_tree_flits(self):
        missing = copy.deepcopy(self.raw)
        missing["memory"][0]["transport"]["events"].remove(
            next(event for event in missing["memory"][0]["transport"]["events"] if event["action"] == "tree_forward")
        )
        with self.assertRaisesRegex(ValueError, "uncharged"):
            audit("multicast", self.admission, missing)
        duplicate = copy.deepcopy(self.raw)
        event = next(event for event in duplicate["memory"][0]["transport"]["events"] if event["action"] == "tree_forward")
        duplicate["memory"][0]["transport"]["events"].append(copy.deepcopy(event))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit("multicast", self.admission, duplicate)

    def test_oracle_rejects_duplicate_scalar_transition_and_leaked_reservation(self):
        scalar = copy.deepcopy(self.raw)
        scalar["scalar"]["increments"][0]["new_value"] = 2
        with self.assertRaisesRegex(ValueError, "exactly one"):
            audit("synchronization", self.admission, scalar)
        leaked = copy.deepcopy(self.raw)
        leaked["memory"][0]["transport"]["snapshot"]["active_reservation_ids"] = ["broadcast:reservation"]
        with self.assertRaisesRegex(ValueError, "retains tree reservations"):
            audit("ownership", self.admission, leaked)

    def test_oracle_rejects_consistent_totals_with_missing_segment_or_false_recipient(self):
        missing = copy.deepcopy(self.raw)
        transport = missing["memory"][0]["transport"]
        transport["events"] = [event for event in transport["events"] if event["segment_index"] != 1]
        transport["packet_physical_bytes"] -= 64
        transport["launched_channel_bytes"] -= 320
        with self.assertRaisesRegex(ValueError, "missing declared tree"):
            audit("multicast", self.admission, missing)
        wrong = copy.deepcopy(self.raw)
        event = next(event for event in wrong["memory"][0]["transport"]["events"] if event["action"] == "recipient_deliver")
        event["recipient_endpoint_id"] = "nonworker"
        with self.assertRaisesRegex(ValueError, "undeclared"):
            audit("multicast", self.admission, wrong)

    def test_current_causal_gap_cannot_become_a_passing_summary(self):
        early = copy.deepcopy(self.raw)
        early["scalar"]["events"].insert(0, {
            **next(event for event in early["scalar"]["events"] if event["action"] == "atomic_effect"),
            "action": "atomic_return", "time_aci_cycles": 0,
        })
        with self.assertRaisesRegex(ValueError, "precedes visible update"):
            audit("synchronization", self.admission, early)

    def test_legacy_predictor_and_encoder_guards_reject_new_documents(self):
        document = {"kind": "multicast_pipeline_result", "schema_version": 1}
        with self.assertRaisesRegex(TypeError, "finite replay"):
            legacy_event_rows(document, "compute")
        with self.assertRaisesRegex(TypeError, "finite replay"):
            require_legacy_nocs(document)

    def cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        return subprocess.run(
            [sys.executable, "-m", "simulator_detailed.replay_multicast_sync", *map(str, args)],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=60, check=False,
        )

    def test_cli_complete_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            result = self.cli("--workload", WORKLOAD, "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "complete")
            self.assertEqual(json.loads(output.read_text())["kind"], "multicast_pipeline_result")
            self.assertIn("multicast replay complete", result.stderr)

    def test_cli_incomplete_invalid_and_input_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            workload = directory_path / "workload.json"
            graph = directory_path / "graph.json"
            document = json.loads(WORKLOAD.read_text())["multicast"]
            document["memory"]["source"]["graph_path"] = "graph.json"
            document["memory"]["buffers"][0]["initially_ready"] = False
            workload.write_text(json.dumps(document))
            graph.write_text((ROOT / "simulator_detailed/configs/topologies/multicast_sync_fixture.json").read_text())
            incomplete = self.cli("--workload", workload)
            self.assertEqual(incomplete.returncode, 1, incomplete.stderr)
            self.assertEqual(json.loads(incomplete.stdout)["status"], "incomplete")
            previous = workload.read_bytes()
            protected = self.cli("--workload", workload, "--output", workload)
            self.assertEqual(protected.returncode, 2)
            self.assertEqual(workload.read_bytes(), previous)
            invalid = self.cli("--workload", PROFILE_WORKLOAD)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")

    def test_pipeline_horizon_overrun_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.json"
            output = Path(directory) / "output.json"
            document = json.loads(WORKLOAD.read_text())
            document["multicast"]["memory"]["source"]["graph_path"] = str(self.admission.inputs["source.json"])
            document["multicast"]["memory"]["max_aci_cycles"] = 1
            path.write_text(json.dumps(document))
            output.write_text("previous output")
            result = self.cli("--workload", path, "--output", output)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("horizon", result.stderr)
            self.assertEqual(output.read_text(), "previous output")
            self.assertEqual(result.stdout, "")

    def test_cli_input_hardlink_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.json"
            alias = Path(directory) / "alias.json"
            document = json.loads(WORKLOAD.read_text())
            document["multicast"]["memory"]["source"]["graph_path"] = str(self.admission.inputs["source.json"])
            path.write_text(json.dumps(document))
            os.link(path, alias)
            previous = path.read_bytes()
            result = self.cli("--workload", path, "--output", alias)
            self.assertEqual(result.returncode, 2)
            self.assertIn("declared input", result.stderr)
            self.assertEqual(alias.read_bytes(), previous)

    def test_validation_suite_worker_enforces_multicast_adapter_budget(self):
        suite = json.loads((ROOT / "simulator_detailed/configs/validation/offline.json").read_text())
        case = suite["cases"][0]
        case.update({
            "case_id": "multicast_pipeline",
            "adapter": "multicast_sync_v1",
            "input_path": str(WORKLOAD),
            "budget": {"wall_time_seconds": 30, "max_aci_cycles": 10000.0},
            "expected_execution": "complete",
            "resume_at_aci_cycles": [],
            "checks": [
                {"check_id": check, "check": check, "required": True, "tier": "model_invariant", "requirements": ["VA-D05"]}
                for check in ("expected_execution", "bounded_execution", "routing", "packet_accounting",
                              "memory_service", "ownership", "synchronization", "drain")
            ],
        })
        suite.update({"cases": [case], "gates": [], "references": []})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(suite))
            report = run_suite(path)
        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.cases[0].adapter, "multicast_sync_v1")
        checks = {check.check_id: check.outcome for check in report.cases[0].checks}
        self.assertEqual(checks["routing"], "pass")
        self.assertEqual(checks["bounded_execution"], "pass")
        self.assertEqual(checks["ownership"], "unsupported")
        self.assertIn("pending_multicast", {item.requirement_id for item in report.coverage})
        self.assertEqual(report.functional_reference, "unvalidated")
        self.assertEqual(report.silicon_timing, "unvalidated")


if __name__ == "__main__":
    unittest.main()
