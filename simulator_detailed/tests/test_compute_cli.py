"""Executable examples, independent exported accounting and capability boundaries."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.compute_memory import ComputeMemoryPlan
from simulator_detailed.compute_runtime import (
    ComputeExecutionResult,
    ComputeOverlapRuntime,
)
from simulator_detailed.configs.schemas.hardware_profile import HardwareProfileConfig
from simulator_detailed.hardware_profile import (
    UnsupportedHardwareProfileError,
    require_executable_architecture,
)
from simulator_detailed.replay_compute import load_plan, run_workload
from simulator_detailed.tests import test_compute_runtime, test_memory_cli
from simulator_detailed.topology import content_digest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "simulator_detailed/configs/compute_workloads"
GENERIC = EXAMPLES / "generic_matmul.json"
WORMHOLE = EXAMPLES / "wormhole_bf16_matmul.json"


def standalone(path):
    doc = json.loads(path.read_text())
    source = doc["memory"]["source"]
    field = "graph_path" if source["kind"] == "canonical_graph" else "profile_path"
    source[field] = str((path.parent / source[field]).resolve())
    return doc


class ComputeCliTests(unittest.TestCase):
    def cli(self, path, output, *, cwd=ROOT):
        args = [sys.executable, "-m", "simulator_detailed.replay_compute", "--workload", str(path)]
        if output is not None:
            args.extend(["--output", str(output)])
        return subprocess.run(args, cwd=cwd, env={**os.environ, "PYTHONPATH": str(ROOT)},
                              text=True, capture_output=True, check=False)

    def assert_accounting(self, result):
        """Reconstruct work from shapes, and time from exported intervals."""
        doc = result.model_dump(mode="json")
        config = json.loads(doc["plan"]["configuration_json"])
        jobs = {j["job_id"]: j for s in config["streams"] for j in s["jobs"]}
        rates = {r["rate_id"]: r for r in config["rates"]}
        dtypes = {d["dtype_id"]: d["bytes_per_element"] for d in config["dtypes"]}
        stages = defaultdict(dict)
        for event in doc["stages"]:
            stages[event["job_id"]][event["action"]] = event["time_aci_cycles"]
        planned_useful = planned_padded = completed_useful = completed_padded = 0
        for plan in doc["plan"]["jobs"]:
            op, cost = jobs[plan["job_id"]]["operation"], plan["cost"]
            batch, m, k = op["a"]["shape"]
            n = op["c"]["shape"][-1]
            block = rates[cost["rate_id"]]["block"]
            useful = 2 * batch * m * n * k
            padded = 2 * batch * math.prod(math.ceil(v / block[a]) * block[a] for a, v in (("m", m), ("n", n), ("k", k)))
            self.assertEqual((cost["useful_work"], cost["executed_work"]), (useful, padded))
            for operand in ("a", "b", "c"):
                tensor = op[operand]
                useful_bytes = math.prod(tensor["shape"]) * dtypes[tensor["dtype"]]
                self.assertEqual(cost[operand]["useful_bytes"], useful_bytes)
                # Shipped examples declare dense storage, with no conversion.
                self.assertEqual(tensor["layout"]["kind"], "dense_row_major")
                self.assertEqual(cost[operand]["storage_bytes"], useful_bytes)
            planned_useful += useful
            planned_padded += padded
            if "math_end" in stages[plan["job_id"]]:
                completed_useful += useful
                completed_padded += padded
        work = doc["work"]
        self.assertEqual((work["planned_useful_work"], work["planned_executed_work"],
                          work["completed_useful_work"], work["completed_executed_work"]),
                         (planned_useful, planned_padded, completed_useful, completed_padded))
        math_busy = sum(s.get("math_end", doc["elapsed_aci_cycles"]) - s["math_start"] for s in stages.values() if "math_start" in s)
        owners, intervals = {}, []
        for event in doc["resource_events"]:
            if event["kind"] != "compute":
                continue
            key = event["worker_tile_id"], event["engine_index"]
            if event["action"] == "acquire":
                self.assertNotIn(key, owners)
                owners[key] = event["time_aci_cycles"]
            else:
                intervals.append(event["time_aci_cycles"] - owners.pop(key))
        context_time = sum(intervals) + sum(doc["elapsed_aci_cycles"] - t for t in owners.values())
        self.assertAlmostEqual(math_busy, work["math_busy_aci_cycles"])
        self.assertAlmostEqual(context_time, work["context_occupied_aci_cycles"])
        test_memory_cli.MemoryCliTests().assert_accounting(doc["memory_session"])
        # A drained session before teardown still retains reserved capacity.
        test_compute_runtime.ComputeRuntimeTests().assert_conserved(result, complete=result.status == "complete")

    def test_all_examples_cli_determinism_relative_paths_and_output(self):
        # Generic byte oracle is also derived in test_compute_runtime. Wormhole:
        # two read requests each 32 bytes * 22 hops, two responses 64 * 4,
        # one write 64 * 3 and acknowledgement 32 * 11 = 2464 channel bytes.
        expected = {"generic_matmul": (420, 1024, 448, 1984, 642),
                    "streaming_depth1": (6, 6, 288, 576, 15),
                    "streaming_depth2": (6, 6, 288, 576, 15),
                    "wormhole_bf16_matmul": (128, 256, 288, 2464, 288)}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            for path in sorted(EXAMPLES.glob("*.json")):
                with self.subTest(example=path.name):
                    run = self.cli(path, output, cwd=directory)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(run.stdout, output.read_text())
                    self.assertEqual(run.stdout, self.cli(path, output).stdout)
                    result = ComputeExecutionResult.model_validate_json(run.stdout)
                    self.assertTrue(result.execution_supported)
                    self.assertEqual(result.capability, "abstract_compute_workload_v1")
                    self.assertEqual(result.numerical_execution, "unsupported")
                    self.assertEqual(result.silicon_timing, "unvalidated")
                    self.assertEqual((result.work.completed_useful_work,
                                      result.work.completed_executed_work, result.memory_session.packet_bytes,
                                      result.memory_session.channel_bytes, result.memory_session.memory_service_bytes), expected[path.stem])
                    self.assert_accounting(result)
            stdout_only = self.cli(GENERIC, None)
            self.assertEqual(stdout_only.returncode, 0, stdout_only.stderr)
            self.assertEqual(json.loads(stdout_only.stdout)["status"], "complete")

    def test_streaming_timelines_follow_integrated_service_oracle(self):
        for depth, starts in ((1, (0, 15, 30)), (2, (0, 9, 18))):
            result = run_workload(EXAMPLES / f"streaming_depth{depth}.json")
            for job, start in zip(result.plan.jobs, starts, strict=True):
                t = {e.action: e.time_aci_cycles for e in result.stages if e.job_id == job.job_id}
                # A read takes nine cycles, local A/B two, math three, result one.
                self.assertEqual((t["reader_start"], t["inputs_ready"], t["math_start"], t["math_end"], t["writer_complete"]),
                                 (start, start + 9, start + 11, start + 14, start + 15))

    def test_incomplete_cli_status_and_independent_partial_work(self):
        doc = standalone(EXAMPLES / "streaming_depth2.json")
        doc["memory"]["max_aci_cycles"] = 12
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "short.json", Path(directory) / "result.json"
            path.write_text(json.dumps(doc))
            run = self.cli(path, output)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertEqual(run.stdout, output.read_text())
            result = ComputeExecutionResult.model_validate_json(run.stdout)
        self.assert_accounting(result)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.completed_job_ids, ())
        self.assertEqual(result.work.completed_useful_work, 0)
        self.assertEqual(result.work.math_busy_aci_cycles, 1)
        self.assertTrue(result.pending)
        self.assertTrue(any(r.occupied for r in result.resources))
        self.assertFalse(result.teardown_complete)
        self.assertEqual(result.memory_session.released_resources, ())

    def test_invalid_input_preserves_output_and_allocates_no_runtime(self):
        valid = standalone(GENERIC)
        variants = []
        for field, value in (("kind", "memory_replay"), ("schema_version", True), ("schema_version", 2), ("extra", 1)):
            variants.append({**copy.deepcopy(valid), field: value})
        missing = copy.deepcopy(valid)
        del missing["runtime"]
        variants.append(missing)
        unsupported = copy.deepcopy(valid)
        unsupported["streams"][0]["jobs"][0]["operation"]["kind"] = "conv"
        variants.append(unsupported)
        rate = copy.deepcopy(valid)
        rate["rates"][0]["work_per_native_cycle"] = 0
        variants.append(rate)
        capacity = copy.deepcopy(valid)
        capacity["runtime"]["transport"]["endpoint_staging_capacity_flits"] = 99
        variants.append(capacity)
        worker = standalone(WORMHOLE)
        worker["workers"][0]["tile_id"] = "tile_1_11"
        worker["streams"][0]["worker_tile_id"] = "tile_1_11"
        variants.append(worker)
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "invalid.json", Path(directory) / "result.json"
            output.write_text("preserve existing evidence\n")
            for doc in variants:
                path.write_text(json.dumps(doc))
                with patch("simpy.Environment", side_effect=AssertionError("runtime allocated")), self.assertRaises(ValueError):
                    run_workload(path)
                run = self.cli(path, output)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertEqual(run.stdout, "")
                self.assertTrue(run.stderr)
                self.assertEqual(output.read_text(), "preserve existing evidence\n")
            for malformed in ("{", "[]"):
                path.write_text(malformed)
                self.assertEqual(self.cli(path, output).returncode, 2)
                self.assertEqual(output.read_text(), "preserve existing evidence\n")
            self.assertEqual(self.cli(Path(directory) / "missing.json", output).returncode, 2)

    def test_source_identities_relocation_and_scoped_profile_admission(self):
        plan = load_plan(WORMHOLE)
        record = plan.workload.record
        graph = json.loads(record.source_json)
        self.assertEqual(content_digest(graph), record.source_sha256)
        self.assertEqual((len(graph["routers"]), len(graph["links"]), len(graph["resources"])), (240, 480, 86))
        profile = json.loads(graph["origin"]["document_json"])
        self.assertEqual(content_digest(profile), graph["origin"]["content_hash"])
        with self.assertRaises(UnsupportedHardwareProfileError):
            require_executable_architecture(HardwareProfileConfig.model_validate(profile))
        self.assertFalse(plan.workload.planning_result().execution_supported)
        with self.assertRaises(NotImplementedError):
            plan.workload.require_executable()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relocated.json"
            path.write_text(json.dumps(standalone(WORMHOLE)))
            relocated = load_plan(path)
        self.assertEqual(plan.plan_sha256, relocated.plan_sha256)
        self.assertEqual(record.plan_sha256, relocated.workload.record.plan_sha256)
        settings = plan.session.execution.settings.model_copy(update={"request_control_aci_cycles": 2.0})
        changed = ComputeMemoryPlan.compile(plan.workload, settings)
        self.assertEqual(changed.workload.record, record)
        self.assertNotEqual(changed.plan_sha256, plan.plan_sha256)

    def test_snapshots_reconcile_resumption_and_exactly_once_finalization(self):
        plan = load_plan(EXAMPLES / "streaming_depth2.json")
        runtime = ComputeOverlapRuntime(plan)
        for horizon in (3, 12, 20, 32):
            result = runtime.advance(max_aci_cycles=horizon)
            self.assert_accounting(result)
        drained = runtime.advance(max_aci_cycles=100)
        self.assertEqual(drained.reason, "awaiting_finalization")
        self.assertTrue(runtime.memory.is_drained)
        self.assertEqual(drained.memory_session.reason, "idle_with_pending")
        self.assertEqual(drained.memory_session.pending, ('["teardown"]',))
        self.assert_accounting(drained)
        self.assertFalse(drained.teardown_complete)
        self.assertTrue(any(r.reserved_bytes for r in drained.memory_session.memory_resources))
        final = runtime.finalize()
        self.assert_accounting(final)
        self.assertEqual(final, ComputeOverlapRuntime(plan).run())
        self.assertIs(runtime.finalize(), final)
        self.assertEqual(len(final.memory_session.released_resources), len(plan.workload.config.memory.buffers))

    def test_reject_fabricated_time_and_work_in_execution_evidence(self):
        result = run_workload(GENERIC)
        for flag in (False, 1, "true"):
            with self.assertRaises(ValueError):
                ComputeExecutionResult.model_validate({**result.model_dump(mode="json"), "execution_supported": flag})
        for field in ("math_busy_aci_cycles", "context_occupied_aci_cycles", "completed_useful_work", "completed_executed_work"):
            doc = result.model_dump(mode="json")
            doc["work"][field] += 1
            with self.assertRaises(ValueError):
                ComputeExecutionResult.model_validate(doc)
        doc = result.model_dump(mode="json")
        next(e for e in doc["stages"] if e["action"] == "math_end")["time_aci_cycles"] -= 1
        with self.assertRaisesRegex(ValueError, "math interval"):
            ComputeExecutionResult.model_validate(doc)
        doc = result.model_dump(mode="json")
        doc["resource_events"].pop(0)
        with self.assertRaisesRegex(ValueError, "matching acquisition"):
            ComputeExecutionResult.model_validate(doc)

    def test_small_representable_math_after_large_clock_is_valid(self):
        document, graph = test_compute_runtime.tiny_documents()
        document["memory"]["max_aci_cycles"] = 1_000_000_000
        for resource in document["memory"]["resources"]:
            resource["service"]["fixed_latency_cycles"] = 100_000_000
        document["rates"][0].update(work_per_native_cycle=2_000_000, setup_native_cycles=0,
                                     quantum_native_cycles=0.000001, minimum_native_cycles=0.000001)
        result = ComputeOverlapRuntime(test_compute_runtime.tiny_lower(document, graph)).run()
        self.assertEqual(result.status, "complete")
        self.assertGreater(result.work.math_busy_aci_cycles, 0)
        self.assert_accounting(result)


if __name__ == "__main__":
    unittest.main()
