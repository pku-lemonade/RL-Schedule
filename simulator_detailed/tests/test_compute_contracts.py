"""Independent cost arithmetic and pure finite compute admission regressions."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.compute_cost import checked_compute_finish, compute_cost
from simulator_detailed.compute_plan import ComputePlan
from simulator_detailed.compute_records import ComputeWorkloadResult, TensorFootprint
from simulator_detailed.configs.schemas.compute_workload import ComputeWorkload
from simulator_detailed.configs.schemas.hardware_profile import HardwareProfileConfig
from simulator_detailed.hardware_profile import (
    UnsupportedHardwareProfileError,
    require_executable_architecture,
)
from simulator_detailed.tests.test_memory_contracts import EVIDENCE
from simulator_detailed.tests.test_memory_packets import memory_documents


def addressed(buffer, offset, size):
    return {"buffer_id": buffer, "offset_bytes": offset, "size_bytes": size}


def compute_documents(*, jobs=1, slots=1, local=False):
    memory, graph = memory_documents(size=64)
    for key in ("kind", "schema_version", "model_revision", "operations"):
        del memory[key]
    memory["packet"]["address_alignment_bytes"] = 1
    for resource in memory["resources"]:
        resource["service"]["service_granule_bytes"] = 1
    for buffer in memory["buffers"]:
        buffer["initially_ready"] = True
    layout = {"kind": "dense_row_major"}
    operation = {"kind": "matmul", "shared_weights": True,
                 "a": {"shape": [2, 3, 5], "dtype": "bf16", "layout": layout},
                 "b": {"shape": [5, 7], "dtype": "bf16", "layout": layout},
                 "c": {"shape": [2, 3, 7], "dtype": "bf16", "layout": layout},
                 "accumulator_precision": "fp32", "fidelity": "assumed_high"}
    rate = {"rate_id": "matrix", "policy": "effective_matmul_v1",
            "key": {"operation": "matmul", "a_dtype": "bf16", "b_dtype": "bf16", "c_dtype": "bf16",
                    "accumulator_precision": "fp32", "layout": layout, "fidelity": "assumed_high"},
            "block": {"m": 4, "n": 8, "k": 4}, "work_per_native_cycle": 128.0,
            "setup_native_cycles": 2.0, "quantum_native_cycles": 0.5, "minimum_native_cycles": 1.0,
            "evidence": EVIDENCE}
    slot_records = [{"slot_id": f"slot{i}", "a": addressed("local", i * 512, 128),
                     "b": addressed("local", i * 512 + 128, 128),
                     "c": addressed("local", i * 512 + 256, 128)} for i in range(slots)]
    job_records = []
    for i in range(jobs):
        offset = (i % slots) * 512 if local else 0
        buffer = "local" if local else "remote"
        mode = {"mode": "local"} if local else {"mode": "remote", "fabric_id": 0}
        output = {"mode": "local"} if local else {"mode": "write_acknowledged", "fabric_id": 1}
        job_records.append({"job_id": f"job{i}", "operation": copy.deepcopy(operation),
                            "a": {**mode, "source": addressed(buffer, offset, 60), "version": {"kind": "initial"}},
                            "b": {**mode, "source": addressed(buffer, offset + 128, 70), "version": {"kind": "initial"}},
                            "output": {**output, "destination": addressed(buffer, offset + 256, 84)}})
    return {"kind": "compute_workload", "schema_version": 1, "policy": "finite_compute_dataflow_v1",
            "buffer_policy": "fifo_item_slots_v1", "memory": memory,
            "dtypes": [{"dtype_id": "bf16", "bytes_per_element": 2, "evidence": EVIDENCE}],
            "rates": [rate], "workers": [{"tile_id": "t0_0", "l1_resource_id": "l1-a", "endpoint_ids": ["src0", "src1"],
                                          "rate_ids": ["matrix"], "native_clock_hz": 1_000_000_000.0,
                                          "reader_capacity": 1, "compute_contexts": 1, "writer_capacity": 1, "evidence": EVIDENCE}],
            "streams": [{"stream_id": "stream", "worker_tile_id": "t0_0", "slots": slot_records, "jobs": job_records}]}, graph


def cost_only(document):
    config = ComputeWorkload.model_validate(document)
    return compute_cost(config.streams[0].jobs[0].operation, config.rates[0], {d.dtype_id: d for d in config.dtypes},
                        native_clock_hz=config.workers[0].native_clock_hz, aci_clock_hz=config.memory.aci_clock_hz)


def compile_document(document, graph):
    return ComputePlan.compile(ComputeWorkload.model_validate(document), graph)


class ComputeCostTests(unittest.TestCase):
    def test_independent_dense_arithmetic_and_clock_conversion(self):
        document, _ = compute_documents()
        cost = cost_only(document)
        self.assertEqual((cost.useful_work, cost.executed_work), (420, 1024))
        self.assertEqual((cost.a.useful_bytes, cost.b.useful_bytes, cost.c.useful_bytes), (60, 70, 84))
        self.assertEqual((cost.a.storage_bytes, cost.b.storage_bytes, cost.c.storage_bytes), (60, 70, 84))
        self.assertEqual((cost.service_native_cycles, cost.service_aci_cycles), (10, 5))

    def test_storage_tiles_are_independent_of_compute_blocks(self):
        document, _ = compute_documents()
        layout = {"kind": "tiled", "tile_rows": 2, "tile_columns": 4}
        for tensor in ("a", "b", "c"):
            document["streams"][0]["jobs"][0]["operation"][tensor]["layout"] = layout
        document["rates"][0]["key"]["layout"] = layout
        cost = cost_only(document)
        self.assertEqual((cost.a.storage_bytes, cost.b.storage_bytes, cost.c.storage_bytes), (128, 96, 128))
        self.assertEqual((cost.a.useful_bytes, cost.b.useful_bytes, cost.c.useful_bytes), (60, 70, 84))
        self.assertEqual((cost.useful_work, cost.executed_work, cost.service_aci_cycles), (420, 1024, 5))

    def test_second_geometry_synthetic_dtype_rate_and_clocks(self):
        document, _ = compute_documents()
        document["dtypes"][0].update(dtype_id="test_u8", bytes_per_element=1)
        for tensor in ("a", "b", "c"):
            document["streams"][0]["jobs"][0]["operation"][tensor]["dtype"] = "test_u8"
            document["rates"][0]["key"][f"{tensor}_dtype"] = "test_u8"
        document["rates"][0].update(block={"m": 1, "n": 1, "k": 1}, work_per_native_cycle=100.0,
                                     quantum_native_cycles=0.1, minimum_native_cycles=0.3, setup_native_cycles=0.2)
        document["memory"]["aci_clock_hz"] = 2_000_000_000.0
        cost = cost_only(document)
        self.assertEqual((cost.useful_work, cost.executed_work), (420, 420))
        self.assertEqual((cost.a.storage_bytes, cost.b.storage_bytes, cost.c.storage_bytes), (30, 35, 42))
        self.assertEqual((cost.service_native_cycles, cost.service_aci_cycles), (4.4, 8.8))

    def test_fc_and_batched_weights_equivalence(self):
        document, _ = compute_documents()
        expected = cost_only(document)
        document["streams"][0]["jobs"][0]["operation"].update(kind="fc", flattening="already_flattened_bmk_v1")
        document["rates"][0]["key"]["operation"] = "fc"
        self.assertEqual(cost_only(document), expected)
        op = document["streams"][0]["jobs"][0]["operation"]
        op["shared_weights"] = False
        op["b"]["shape"] = [2, 5, 7]
        batched = cost_only(document)
        self.assertEqual((batched.executed_work, batched.service_aci_cycles), (1024, 5))
        self.assertEqual(batched.b.storage_bytes, 140)

    def test_subquantum_cost_and_clock_advancement(self):
        document, _ = compute_documents()
        document["rates"][0].update(work_per_native_cycle=1e20, minimum_native_cycles=0.5, setup_native_cycles=0.0)
        cost = cost_only(document)
        self.assertEqual((cost.service_native_cycles, cost.service_aci_cycles), (0.5, 0.25))
        self.assertEqual(checked_compute_finish(1, cost.service_aci_cycles), 1.25)
        for start, duration in ((1e20, 0.25), (1e308, 1e308), (-1, 1), (0, 0), (0, float("nan"))):
            with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                checked_compute_finish(start, duration)

    def test_overflow_underflow_and_invalid_quantization_rejected(self):
        document, _ = compute_documents()
        for changes in ({"work_per_native_cycle": 5e-324}, {"minimum_native_cycles": 0.6}):
            invalid = copy.deepcopy(document)
            invalid["rates"][0].update(changes)
            with self.assertRaises(ValueError):
                cost_only(invalid)
        document["rates"][0].update(work_per_native_cycle=1e308, setup_native_cycles=0.0,
                                     quantum_native_cycles=1e-300, minimum_native_cycles=1e-300)
        document["memory"]["aci_clock_hz"] = 1e-300
        document["workers"][0]["native_clock_hz"] = 1e300
        with self.assertRaisesRegex(ValueError, "unrepresentable"):
            cost_only(document)

    def test_cost_records_reject_false_accounting(self):
        document, _ = compute_documents()
        cost = cost_only(document)
        with self.assertRaises(ValueError):
            type(cost).model_validate({**cost.model_dump(), "useful_work": 419})
        with self.assertRaises(ValueError):
            TensorFootprint(useful_bytes=2, storage_bytes=1)


class ComputeAdmissionTests(unittest.TestCase):
    def test_round_trip_pure_admission_and_honest_result(self):
        document, graph = compute_documents(jobs=5, slots=2)
        with patch("simpy.Environment", side_effect=AssertionError("runtime allocated")):
            plan = compile_document(document, graph)
            self.assertEqual(plan.config, ComputeWorkload.model_validate_json(plan.config.model_dump_json()))
            result = plan.planning_result()
            self.assertEqual(result, ComputeWorkloadResult.model_validate_json(result.model_dump_json()))
            self.assertFalse(result.execution_supported)
            self.assertEqual((result.status, result.completed_work, result.completed_job_ids), ("planned", 0, ()))
            self.assertEqual([j.slot_generation for j in plan.record.jobs], [0, 0, 1, 1, 2])
            self.assertEqual([j.slot_id for j in plan.record.jobs], ["slot0", "slot1", "slot0", "slot1", "slot0"])
            self.assertEqual(plan.record.jobs[1].predecessors, ())
            self.assertEqual(plan.record.jobs[1].fifo_predecessor_id, "job0")
            with self.assertRaises(NotImplementedError):
                plan.require_executable()
        for changes in ({"status": "complete"}, {"completed_work": 420}, {"execution_supported": True},
                        {"completed_job_ids": ["job0"]}, {"remaining_stages": []}, {"elapsed_aci_cycles": 5},
                        {"schema_version": True}, {"completed_work": False}, {"execution_supported": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ComputeWorkloadResult.model_validate({**result.model_dump(), **changes})

    def test_schema_rejects_extra_unsupported_nonfinite_and_noninteger_values(self):
        document, graph = compute_documents()
        changes = [(("schema_version",), True), (("schema_version",), "1"), (("kind",), "memory_replay"),
                   (("policy",), "dynamic"), (("buffer_policy",), "unbounded"), (("extra",), 1),
                   (("rates", 0, "work_per_native_cycle"), float("inf")),
                   (("workers", 0, "native_clock_hz"), float("nan")),
                   (("workers", 0, "compute_contexts"), 0), (("dtypes", 0, "bytes_per_element"), 1.5)]
        op = ("streams", 0, "jobs", 0, "operation")
        changes += [(op + ("kind",), "conv"), (op + ("split_k",), 2), (op + ("bias",), True),
                    (op + ("transpose_a",), True), (op + ("a", "shape", 1), 0),
                    (op + ("a", "shape", 1), True), (op + ("a", "layout", "kind"), "packed_bfp"),
                    (op + ("kind",), "fc"), (op + ("flattening",), "already_flattened_bmk_v1")]
        for path, value in changes:
            invalid = copy.deepcopy(document)
            target = invalid
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                compile_document(invalid, graph)

    def test_unique_ids_and_ambiguous_rates(self):
        document, graph = compute_documents()
        for field in ("dtypes", "rates", "workers", "streams"):
            invalid = copy.deepcopy(document)
            invalid[field].append(copy.deepcopy(invalid[field][0]))
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ComputeWorkload.model_validate(invalid)
        for field in ("slots", "jobs"):
            invalid = copy.deepcopy(document)
            invalid["streams"][0][field] *= 2
            with self.assertRaises(ValidationError):
                ComputeWorkload.model_validate(invalid)
        document["rates"].append({**copy.deepcopy(document["rates"][0]), "rate_id": "duplicate"})
        document["workers"][0]["rate_ids"].append("duplicate")
        with self.assertRaisesRegex(ValueError, "ambiguous effective rate"):
            compile_document(document, graph)

    def test_shape_layout_precision_and_fidelity_fail_without_exact_support(self):
        document, graph = compute_documents()
        for field, value in (("accumulator_precision", "fp64"), ("fidelity", "unknown"), ("shared_weights", False)):
            invalid = copy.deepcopy(document)
            invalid["streams"][0]["jobs"][0]["operation"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                compile_document(invalid, graph)
        for tensor, changes in (("a", {"shape": [3, 5]}), ("b", {"shape": [6, 7]}),
                                ("c", {"shape": [1, 3, 7]}), ("b", {"layout": {"kind": "tiled", "tile_rows": 2, "tile_columns": 4}})):
            invalid = copy.deepcopy(document)
            invalid["streams"][0]["jobs"][0]["operation"][tensor].update(changes)
            with self.subTest(tensor=tensor, changes=changes), self.assertRaises(ValueError):
                compile_document(invalid, graph)

    def test_disabled_router_only_legacy_and_wrong_l1_workers_fail(self):
        document, graph = compute_documents()
        for worker_id in ("t2_2", "0", "src0", "t1_0"):
            invalid = copy.deepcopy(document)
            invalid["workers"][0]["tile_id"] = worker_id
            invalid["streams"][0]["worker_tile_id"] = worker_id
            with self.subTest(worker_id=worker_id), self.assertRaises(ValueError):
                compile_document(invalid, graph)
        disabled = copy.deepcopy(graph)
        disabled["enabled_worker_ids"].remove("t0_0")
        disabled["logical_workers"] = [w for w in disabled["logical_workers"] if w["tile_id"] != "t0_0"]
        for endpoint in disabled["attachments"]:
            if endpoint["router_id"] == "t0_0":
                endpoint["role"] = "network"
        with self.assertRaisesRegex(ValueError, "enabled canonical physical tile"):
            compile_document(document, disabled)
        document["workers"][0]["endpoint_ids"] = ["ram0"]
        with self.assertRaises(ValueError):
            compile_document(document, graph)

    def test_two_fabrics_keep_one_worker_and_missing_interfaces_fail(self):
        document, graph = compute_documents()
        plan = compile_document(document, graph)
        self.assertEqual(len(plan.config.workers), 1)
        self.assertEqual(plan.record.jobs[0].worker_tile_id, "t0_0")
        document["workers"][0]["endpoint_ids"] = ["src0"]
        with self.assertRaisesRegex(ValueError, "one admitted initiator"):
            compile_document(document, graph)

    def test_full_storage_slot_bounds_and_alias_overlap(self):
        document, graph = compute_documents()
        invalids = []
        for access in ("a", "b", "c"):
            invalid = copy.deepcopy(document)
            invalid["streams"][0]["slots"][0][access]["size_bytes"] = 1
            invalids.append(invalid)
        overlap = copy.deepcopy(document)
        overlap["streams"][0]["slots"][0]["b"]["offset_bytes"] = 0
        invalids.append(overlap)
        exceeds = copy.deepcopy(document)
        exceeds["streams"][0]["slots"][0]["c"]["offset_bytes"] = 16384
        invalids.append(exceeds)
        capacity = copy.deepcopy(document)
        capacity["memory"]["resources"][0]["capacity_override_bytes"] = 8192
        invalids.append(capacity)
        alias = copy.deepcopy(document)
        alias["memory"]["buffers"].append({**alias["memory"]["buffers"][0], "buffer_id": "alias"})
        invalids.append(alias)
        for invalid in invalids:
            with self.assertRaises(ValueError):
                compile_document(invalid, graph)

    def test_range_byte_mismatch_in_place_and_initial_readiness(self):
        document, graph = compute_documents()
        for side, access in (("a", "source"), ("b", "source"), ("output", "destination")):
            invalid = copy.deepcopy(document)
            invalid["streams"][0]["jobs"][0][side][access]["size_bytes"] += 1
            with self.assertRaises(ValueError):
                compile_document(invalid, graph)
        document["streams"][0]["jobs"][0]["output"]["destination"]["offset_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "in-place"):
            compile_document(document, graph)
        document, graph = compute_documents()
        document["memory"]["buffers"][1]["initially_ready"] = False
        with self.assertRaisesRegex(ValueError, "initialized"):
            compile_document(document, graph)

    def test_local_paths_require_assigned_slot(self):
        document, graph = compute_documents(jobs=4, slots=2, local=True)
        self.assertEqual(len(compile_document(document, graph).record.jobs), 4)
        document["streams"][0]["jobs"][1]["a"]["source"]["offset_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "assigned slot"):
            compile_document(document, graph)

    def test_completion_dependencies_and_fifo_cycles(self):
        document, graph = compute_documents(jobs=3, slots=2)
        document["streams"][0]["jobs"][2]["depends_on"] = ["job0"]
        plan = compile_document(document, graph)
        self.assertEqual(plan.record.jobs[2].predecessors, ("job0",))
        for dependency in ("job2", "job0", "unknown"):
            invalid = copy.deepcopy(document)
            invalid["streams"][0]["jobs"][0]["depends_on"] = [dependency]
            with self.subTest(dependency=dependency), self.assertRaises(ValueError):
                compile_document(invalid, graph)
        # No recursion per job in dependency admission; buffers stay constant.
        document, graph = compute_documents(jobs=1100, slots=2)
        self.assertEqual(len(compile_document(document, graph).record.jobs), 1100)

    def test_job_versions_require_matching_output_and_supported_completion(self):
        document, graph = compute_documents(jobs=2, slots=2)
        job = document["streams"][0]["jobs"][1]
        job["a"]["version"] = {"kind": "job", "producer_job_id": "job0"}
        with self.assertRaisesRegex(ValueError, "range/shape"):
            compile_document(document, graph)
        document["streams"][0]["jobs"][0]["output"]["mode"] = "write_posted"
        with self.assertRaisesRegex(ValueError, "posted output"):
            compile_document(document, graph)

    def test_matching_job_version_and_independent_streams_on_one_worker(self):
        document, graph = compute_documents(jobs=2, slots=2)
        job = document["streams"][0]["jobs"][1]
        job["a"]["version"] = {"kind": "job", "producer_job_id": "job0"}
        job["a"]["source"] = addressed("remote", 256, 84)
        job["operation"]["a"]["shape"] = [2, 3, 7]
        job["operation"]["b"]["shape"] = [7, 7]
        job["b"]["source"]["size_bytes"] = 98
        job["output"]["destination"]["offset_bytes"] = 512
        plan = compile_document(document, graph)
        self.assertEqual(plan.record.jobs[1].predecessors, ("job0",))
        # Move the second job/slot to another stream on the same engine.
        first = document["streams"][0]
        document["streams"].append({"stream_id": "other", "worker_tile_id": "t0_0",
                                    "slots": [first["slots"].pop()], "jobs": [first["jobs"].pop()]})
        plan = compile_document(document, graph)
        self.assertEqual(len(plan.config.workers), 1)
        self.assertIsNone(plan.record.jobs[1].fifo_predecessor_id)
        self.assertEqual(plan.record.jobs[1].predecessors, ("job0",))

    def test_independent_workers_admit_but_cross_worker_completion_rejects(self):
        document, graph = compute_documents()
        graph["resources"].append({"resource_id": "l1-b", "kind": "local_sram", "owner_tile_id": "t1_0", "capacity_bytes": 65536})
        for fabric in (0, 1):
            attachment = next(a for a in graph["attachments"] if a["endpoint_id"] == f"src{fabric}")
            graph["attachments"].append({**copy.deepcopy(attachment), "endpoint_id": f"other{fabric}",
                                         "router_id": "t1_0", "resource_ids": ["l1-b"]})
            endpoint = next(e for e in document["memory"]["endpoints"] if e["endpoint_id"] == f"src{fabric}")
            document["memory"]["endpoints"].append({**copy.deepcopy(endpoint), "endpoint_id": f"other{fabric}",
                                                     "router_id": "t1_0", "resource_ids": ["l1-b"]})
        resource = next(r for r in document["memory"]["resources"] if r["resource_id"] == "l1-a")
        document["memory"]["resources"].append({**copy.deepcopy(resource), "resource_id": "l1-b"})
        document["memory"]["buffers"].append({"buffer_id": "other_local", "resource_id": "l1-b", "base_address": 0, "size_bytes": 4096})
        document["workers"].append({**copy.deepcopy(document["workers"][0]), "tile_id": "t1_0",
                                    "l1_resource_id": "l1-b", "endpoint_ids": ["other0", "other1"]})
        stream = copy.deepcopy(document["streams"][0])
        stream.update(stream_id="other_stream", worker_tile_id="t1_0")
        stream["slots"][0]["slot_id"] = "other_slot"
        for tensor in ("a", "b", "c"):
            stream["slots"][0][tensor]["buffer_id"] = "other_local"
        stream["jobs"][0]["job_id"] = "other_job"
        stream["jobs"][0]["output"]["destination"]["offset_bytes"] = 512
        document["streams"].append(stream)
        self.assertEqual(len(compile_document(document, graph).config.workers), 2)
        stream["jobs"][0]["depends_on"] = ["job0"]
        with self.assertRaisesRegex(ValueError, "cross-worker completion"):
            compile_document(document, graph)

    def test_wormhole_profile_binding_and_revalidation_are_still_planning_only(self):
        root = Path(__file__).resolve().parents[1]
        memory = json.loads((root / "configs/memory_replays/wormhole_ordered.json").read_text())
        profile = json.loads((root / "configs/profiles/wormhole_b0_n150_assumed.json").read_text())
        for key in ("kind", "schema_version", "model_revision", "operations", "runtime"):
            del memory[key]
        memory["buffers"][1]["initially_ready"] = True
        document, _ = compute_documents()
        document["memory"] = memory
        document["workers"][0].update(tile_id="tile_1_1", l1_resource_id="l1_tile_1_1",
                                        endpoint_ids=["tile_1_1_noc0", "tile_1_1_noc1"])
        document["streams"][0]["worker_tile_id"] = "tile_1_1"
        job = document["streams"][0]["jobs"][0]
        for name, shape in (("a", [1, 4, 4]), ("b", [4, 4]), ("c", [1, 4, 4])):
            job["operation"][name]["shape"] = shape
        job["a"]["source"]["size_bytes"] = 32
        job["b"]["source"]["size_bytes"] = 32
        job["output"]["destination"]["size_bytes"] = 32
        with patch("simpy.Environment", side_effect=AssertionError("runtime allocated")):
            plan = compile_document(document, profile)
            self.assertEqual(len(plan.graph.resources), 86)
            self.assertEqual(plan.record.jobs[0].worker_tile_id, "tile_1_1")
            self.assertEqual(plan.record.jobs[0].cost.useful_work, 128)
            self.assertFalse(plan.planning_result().execution_supported)

    def test_identity_relocation_and_configuration_changes(self):
        document, graph = compute_documents()
        original = compile_document(document, graph)
        document["memory"]["source"]["graph_path"] = "moved/graph.json"
        relocated = compile_document(document, graph)
        self.assertEqual(original.record.plan_sha256, relocated.record.plan_sha256)
        self.assertNotEqual(original.record.configuration_json, relocated.record.configuration_json)
        document["workers"][0]["native_clock_hz"] *= 2
        changed = compile_document(document, graph)
        self.assertNotEqual(changed.record.effective_sha256, original.record.effective_sha256)
        self.assertNotEqual(changed.record.plan_sha256, original.record.plan_sha256)
        self.assertEqual(changed.record.source_sha256, original.record.source_sha256)
        with self.assertRaisesRegex(ValueError, "differs"):
            replace(original, record=changed.record).revalidate()
        invalid = original.config.model_copy(update={"workers": ()})
        with self.assertRaises(ValueError):
            ComputePlan.compile(invalid, graph)

    def test_legacy_full_profile_gate_stays_closed(self):
        root = Path(__file__).resolve().parents[1]
        profile = HardwareProfileConfig.model_validate(json.loads((root / "configs/profiles/wormhole_b0_n150_assumed.json").read_text()))
        with self.assertRaises(UnsupportedHardwareProfileError):
            require_executable_architecture(profile)
        document, graph = compute_documents()
        with self.assertRaises(TypeError):
            require_executable_architecture(compile_document(document, graph).config)


if __name__ == "__main__":
    unittest.main()
