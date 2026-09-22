"""Negative-path and CLI tests for versioned generic simulation results."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_transactions import (
    GenericSimulationResult,
    GenericTransactionBatch,
)
from simulator_detailed.generic_graph import load_generic_system
from simulator_detailed.generic_runtime import run_generic_batch
from simulator_detailed.tests.test_generic_runtime import batch, transfer

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
GRAPH = ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json"
ACCEPTANCE = ROOT / "configs" / "generic_transactions" / "acceptance_batch.json"


def run_batch(document):
    parsed = GenericTransactionBatch.model_validate(document)
    return run_generic_batch(load_generic_system(GRAPH), parsed)


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestNegativePaths(unittest.TestCase):
    def test_unreachable_route_is_incomplete_not_raised(self):
        result = run_batch(batch([transfer("t_bad", "eu_00", "dma_b")]))
        self.assertEqual(result.status, "incomplete")
        span = spans_by_id(result)["t_bad"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "route_unreachable")
        self.assertIsNone(span.start_cycles)
        self.assertIsNone(span.end_cycles)

    def test_dependent_of_unreachable_is_dependency_unsatisfied(self):
        result = run_batch(batch([
            transfer("t_bad", "eu_00", "dma_b"),
            transfer("t_next", "eu_00", "mem_b_ep", depends_on=["t_bad"]),
        ]))
        spans = spans_by_id(result)
        self.assertEqual(spans["t_bad"].reason, "route_unreachable")
        self.assertEqual(spans["t_next"].reason, "dependency_unsatisfied")

    def test_capacity_exceeded_is_incomplete(self):
        # mem_b declares 2,097,152 bytes; one byte over must not execute.
        result = run_batch(batch([
            transfer("t_big", "eu_00", "mem_b_ep", payload=2097153),
        ]))
        span = spans_by_id(result)["t_big"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "capacity_exceeded")

    def test_unknown_endpoint_fails_before_simulation(self):
        with self.assertRaises(ValueError):
            run_batch(batch([transfer("t_ghost", "eu_00", "no_such_ep")]))

    def test_all_terminal_batch_has_no_completion(self):
        result = run_batch(batch([transfer("t_bad", "eu_00", "dma_b")]))
        self.assertIsNone(result.completion_cycles)
        self.assertEqual(result.reason, "transactions_incomplete")


class TestResultIntegrity(unittest.TestCase):
    def result_dump(self):
        result = run_batch(batch([
            transfer("t_a", "eu_00", "mem_b_ep"),
            transfer("t_bad", "eu_00", "dma_b"),
        ]))
        return result.model_dump(mode="json")

    def test_valid_result_revalidates(self):
        GenericSimulationResult.model_validate(self.result_dump())

    def test_flipped_status_rejected(self):
        dumped = self.result_dump()
        dumped["status"] = "complete"
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(dumped)

    def test_completed_span_with_missing_end_rejected(self):
        dumped = self.result_dump()
        span = next(s for s in dumped["transactions"] if s["status"] == "complete")
        span["end_cycles"] = None
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(dumped)

    def test_incomplete_span_with_times_rejected(self):
        dumped = self.result_dump()
        span = next(s for s in dumped["transactions"] if s["status"] == "incomplete")
        span["end_cycles"] = 5.0
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(dumped)

    def test_wrong_completion_rejected(self):
        dumped = self.result_dump()
        dumped["completion_cycles"] = 1.0
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(dumped)

    def test_empty_transactions_cannot_report_success(self):
        dumped = self.result_dump()
        dumped["transactions"] = []
        dumped["status"] = "complete"
        dumped["reason"] = "drained"
        dumped["completion_cycles"] = None
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(dumped)

    def test_hops_only_on_transfers(self):
        compute_dump = run_batch(batch([{
            "transaction_id": "c_00", "kind": "compute", "unit_id": "eu_00",
            "duration_cycles": 5.0, "depends_on": [], "start_cycles": 0.0,
        }])).model_dump(mode="json")
        span = compute_dump["transactions"][0]
        span["hops"] = [{
            "network_id": "net_alpha", "link_id": "al_00e10",
            "queued_cycles": 0.0, "serialization_start_cycles": 0.0,
            "serialization_end_cycles": 1.0, "arrival_cycles": 2.0,
        }]
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(compute_dump)


class TestGenericCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "simulator_detailed.replay_generic", *args],
            cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
        )

    def test_acceptance_batch_exit_0(self):
        process = self.run_cli("--batch", str(ACCEPTANCE))
        self.assertEqual(process.returncode, 0, process.stderr)
        result = GenericSimulationResult.model_validate(json.loads(process.stdout))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.batch_id, "acceptance-2d")
        spans = spans_by_id(result)
        self.assertEqual(spans["t_a"].end_cycles, 33.0)
        self.assertEqual(spans["w_1"].end_cycles, 10.0)

    def test_incomplete_batch_exit_1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.json"
            document = batch(
                [{
                    "transaction_id": "w_9", "kind": "wait", "counter_id": "barrier",
                    "threshold": 5, "depends_on": [], "start_cycles": 0.0,
                }],
                counters=[{"counter_id": "barrier", "initial_value": 0}],
                max_cycles=50.0,
            )
            document["graph_path"] = str(GRAPH)
            path.write_text(json.dumps(document))
            process = self.run_cli("--batch", str(path))
        self.assertEqual(process.returncode, 1, process.stderr)
        result = GenericSimulationResult.model_validate(json.loads(process.stdout))
        self.assertEqual(result.status, "incomplete")

    def test_invalid_inputs_exit_2(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong_kind = Path(directory) / "wrong.json"
            wrong_kind.write_text(json.dumps({"kind": "topology_replay"}))
            self.assertEqual(self.run_cli("--batch", str(wrong_kind)).returncode, 2)
            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{not json")
            self.assertEqual(self.run_cli("--batch", str(malformed)).returncode, 2)
            missing = Path(directory) / "missing.json"
            self.assertEqual(self.run_cli("--batch", str(missing)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
