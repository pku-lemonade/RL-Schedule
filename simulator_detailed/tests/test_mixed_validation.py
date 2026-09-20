"""Public mixed replay, independent corruption audits and bounded harness evidence."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.validation.adapters import admit
from simulator_detailed.validation.audits import audit
from simulator_detailed.validation.normalize import normalize
from simulator_detailed.validation.runner import run_suite

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "simulator_detailed/configs"
WORKLOAD = CONFIG / "multicast_workloads/mixed_two_rounds.json"
CHECKS = (
    "routing",
    "multicast",
    "packet_accounting",
    "memory_service",
    "ownership",
    "synchronization",
    "causality",
    "compute_work",
    "drain",
    "bounded_execution",
)


class MixedValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admitted = admit("multicast_sync_v1", WORKLOAD, horizon=10000)
        cls.raw = cls.admitted.execute(())[0]
        cls.analytical = admit(
            "multicast_sync_v1",
            CONFIG / "multicast_workloads/mixed_analytical.json",
            horizon=10000,
        )
        cls.atomic = cls.analytical.execute(())[0]

    def test_result_identity_and_strict_execution_flags(self):
        from simulator_detailed.multicast_memory_runtime import MulticastExecutionResult

        raw = copy.deepcopy(self.raw)
        raw["plan"]["plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            audit("drain", self.admitted, raw)
        for field in ("teardown_complete", "execution_supported"):
            raw = copy.deepcopy(self.raw)
            raw[field] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                MulticastExecutionResult.model_validate(raw)

    def test_transient_credit_overallocation_cannot_hide_in_drained_snapshot(self):
        raw = copy.deepcopy(self.raw)
        trace = raw["tree_transport"]["trace"]
        first = next(e for e in trace if e["action"] == "credit_reserve")
        copies = [
            copy.deepcopy(e)
            for e in trace
            if e.get("token_id") == first["token_id"]
            and e["action"] in {"credit_reserve", "credit_release", "credit_return"}
        ]
        self.assertEqual(len(copies), 3)
        for event in copies:
            event["token_id"] = "unaccounted-extra-credit"
        trace.extend(copies)
        with self.assertRaisesRegex(ValueError, "capacity"):
            audit("ownership", self.admitted, raw)

    def test_oracles_use_inputs_not_production_helpers_and_normalization_is_lossless(
        self,
    ):
        with (
            patch(
                "simulator_detailed.multicast_tree.compile_rectangle_tree",
                side_effect=AssertionError("production tree"),
            ),
            patch(
                "simulator_detailed.compute_cost.compute_cost",
                side_effect=AssertionError("production cost"),
            ),
            patch(
                "simulator_detailed.memory_service.ServiceTiming.cost",
                side_effect=AssertionError("production cost"),
            ),
        ):
            for admitted, raw in (
                (self.admitted, self.raw),
                (self.analytical, self.atomic),
            ):
                for check in CHECKS:
                    self.assertIn("passed", audit(check, admitted, raw))
        observations = normalize(self.admitted, self.raw)
        self.assertTrue(all(e.details is not None for e in observations.events))
        self.assertFalse(observations.pending)
        chunks = [
            json.loads(e.details.text)
            for e in observations.events
            if e.event_id.startswith("chunks:")
        ]
        self.assertEqual(chunks, self.raw["chunks"])
        scalars = [
            json.loads(e.details.text)
            for e in observations.events
            if e.event_id.startswith("scalar_service:")
        ]
        self.assertEqual(scalars, self.raw["scalar_service"])
        snapshot = json.loads(
            next(
                e.details.text for e in observations.events if e.event_id == "snapshot"
            )
        )
        self.assertEqual(
            snapshot["deliveries"], self.raw["tree_transport"]["deliveries"]
        )
        self.assertEqual(snapshot["compute"]["slots"], self.raw["compute"]["slots"])

    def test_tree_and_control_corruption_with_plausible_totals(self):
        for damage in (
            "missing",
            "duplicate",
            "false_source",
            "missing_recipient",
            "control",
        ):
            raw = copy.deepcopy(self.raw)
            trace = raw["tree_transport"]["trace"]
            launches = [e for e in trace if e["action"] == "link_launch"]
            event = next(
                e
                for e in launches
                if e["packet"]["traffic_class"]
                == ("request" if damage == "control" else "multicast")
                and e["channel"]["kind"] == "network"
            )
            if damage in {"missing", "control"}:
                trace.remove(event)
                raw["physical_channel_bytes"] -= 32
                raw["tree_transport"]["physical_channel_bytes"] -= 32
            elif damage == "duplicate":
                trace.append(copy.deepcopy(event))
                raw["physical_channel_bytes"] += 32
                raw["tree_transport"]["physical_channel_bytes"] += 32
            elif damage == "false_source":
                raw["tree_transport"]["deliveries"][0]["endpoint_id"] = "ep-t0_0"
            else:
                raw["tree_transport"]["deliveries"].pop()
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                audit("packet_accounting", self.admitted, raw)

    def test_scalar_causality_generation_and_ownership_corruption(self):
        for damage in (
            "duplicate_update",
            "early_wait",
            "early_phase",
            "stale_generation",
            "leaked_grant",
            "early_publication",
            "hidden_lease",
        ):
            raw = copy.deepcopy(self.raw)
            check = "synchronization"
            if damage == "duplicate_update":
                raw["scalar_service"].append(
                    copy.deepcopy(
                        next(
                            s
                            for s in raw["scalar_service"]
                            if s["direction"] == "atomic"
                        )
                    )
                )
            if damage == "early_wait":
                next(e for e in raw["lifecycle"] if e["action"] == "wait_release")[
                    "time_aci_cycles"
                ] = 0
            if damage == "early_phase":
                next(
                    e
                    for e in raw["lifecycle"]
                    if e["operation_id"] == "distribute1"
                    and e["action"] == "acceptance"
                )["time_aci_cycles"] = 0
            if damage == "stale_generation":
                raw["compute"]["slot_events"][0]["generation"] = 1
                check = "compute_work"
            if damage == "leaked_grant":
                raw["tree_transport"]["events"].remove(
                    next(
                        e
                        for e in raw["tree_transport"]["events"]
                        if e["action"] == "reservation_release"
                    )
                )
                check = "ownership"
            if damage == "early_publication":
                next(e for e in raw["ownership_trace"] if e["action"] == "publish")[
                    "time_aci_cycles"
                ] = 0
                check = "memory_service"
            if damage == "hidden_lease":
                raw["ownership_trace"].remove(
                    next(
                        e
                        for e in raw["ownership_trace"]
                        if e["action"] == "access_release"
                    )
                )
                check = "ownership"
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                audit(check, self.admitted, raw)
        for damage in ("early_return", "wrong_previous"):
            raw = copy.deepcopy(self.atomic)
            if damage == "early_return":
                next(
                    e
                    for e in raw["lifecycle"]
                    if e["operation_id"] == "increment"
                    and e["action"] == "acknowledgement"
                )["time_aci_cycles"] = 0
            else:
                raw["inbox_values"][0][1] = 99
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                audit("synchronization", self.analytical, raw)

    def test_budget_before_allocation_and_partial_owner_audits(self):
        with (
            patch("simpy.Environment", side_effect=AssertionError("allocated")),
            self.assertRaisesRegex(ValueError, "budget"),
        ):
            admit("multicast_sync_v1", WORKLOAD, horizon=1)
        snapshots = self.admitted.execute((1, 15, 35, 60))
        for raw in snapshots[:-1]:
            self.assertEqual(raw["status"], "incomplete")
            audit("ownership", self.admitted, raw)
            normalize(self.admitted, raw)
        self.assertEqual(snapshots[-1], self.raw)

    def cli(self, path, *args):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "simulator_detailed.replay_multicast_sync",
                "--workload",
                str(path),
                *map(str, args),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_real_cli_profile_partial_and_invalid_output_preservation(self):
        for name in ("mixed_two_rounds.json", "wormhole_b0_mixed_assumed.json"):
            result = self.cli(CONFIG / "multicast_workloads" / name)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["kind"], "multicast_sync_result")
            self.assertIn("shared finite runtime", result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.json"
            output = Path(directory) / "result.json"
            doc = json.loads(WORKLOAD.read_text())
            doc["memory"]["source"]["graph_path"] = str(
                CONFIG / "topologies/multicast_sync_fixture.json"
            )
            doc["memory"]["max_aci_cycles"] = 15
            path.write_text(json.dumps(doc))
            result = self.cli(path, "--output", output)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(output.read_text()), json.loads(result.stdout))
            self.assertFalse(json.loads(result.stdout)["teardown_complete"])
            before = output.read_bytes()
            doc["compute"]["workers"][0]["compute_contexts"] = True
            path.write_text(json.dumps(doc))
            result = self.cli(path, "--output", output)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(result.stdout)
            self.assertEqual(output.read_bytes(), before)

    def test_published_analytical_pipeline_and_profile_suites(self):
        for name in (
            "multicast_analytical",
            "multicast_pipeline",
            "multicast_wormhole",
        ):
            with self.subTest(suite=name):
                report = run_suite(CONFIG / "validation" / (name + ".json"))
                self.assertEqual(
                    report.status,
                    "pass",
                    [
                        (
                            c.case_id,
                            [
                                (k.check_id, k.outcome, k.reason)
                                for k in c.checks
                                if k.outcome != "pass"
                            ],
                        )
                        for c in report.cases
                    ],
                )
                self.assertEqual(
                    (report.functional_reference, report.silicon_timing),
                    ("unvalidated", "unvalidated"),
                )
                self.assertTrue(
                    all(
                        "shared_physical_transport" in c.enabled_mechanisms
                        for c in report.case_capabilities
                    )
                )
                self.assertTrue(all(c.identity is not None for c in report.cases))
                self.assertNotIn(
                    "pending_multicast", {r.requirement_id for r in report.coverage}
                )


if __name__ == "__main__":
    unittest.main()
