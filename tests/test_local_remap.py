import json
import multiprocessing as mp
import os
import tempfile
import unittest
from pathlib import Path

from configs.schemas.arch_config import ArchConfig
from configs.schemas.failure_configs import FailSlow
from simulator.architecture import Arch
from utils.definitions import TimeSlice, Trace, TraceItem
from utils.mapper import NetworkMapper, parse_mapping


WORKLOAD_PATH = "workloads/darknet19-4-4.json"
ARCH_PATH = "configs/instances/gemini4_4.json"
FAIL_PATH = "configs/instances/normal.json"


def build_trace(overrides=None):
    overrides = overrides or {}
    cores = []
    for core_id in range(16):
        slow, util, op_num = overrides.get(core_id, (0.1, 0.1, 10))
        cores.append(TraceItem(id=core_id, slow=slow, ultilization=util, op_num=op_num))
    return Trace(time_slices=[TimeSlice(cores=cores, links=[])])


def sequence_step(action_type, layer_id, src_core, dst_core, trace_overrides=None):
    return {
        "action_type": action_type,
        "layer_id": layer_id,
        "src_core": src_core,
        "dst_core": dst_core,
        "trace_overrides": trace_overrides or {},
    }


def _execute_sequence(sequence, fail_path, result_path):
    try:
        with open(ARCH_PATH, "r") as file:
            arch_cfg = ArchConfig.model_validate(json.load(file))
        with open(fail_path, "r") as file:
            fail_cfg = FailSlow.model_validate(json.load(file))

        mapper = NetworkMapper(parse_mapping(WORKLOAD_PATH))
        applied = []

        for index, step in enumerate(sequence):
            trace = build_trace(step.get("trace_overrides"))
            report = mapper.apply_local_remap(
                layer_id=step["layer_id"],
                action_type=step["action_type"],
                src_core=step["src_core"],
                dst_core=step["dst_core"],
                trace=trace,
            )
            applied.append({
                "index": index,
                "step": step,
                "accepted": report.accepted,
                "reason": report.reason,
            })
            if not report.accepted:
                with open(result_path, "w", encoding="utf-8") as file:
                    json.dump({
                    "status": "rejected",
                    "step_index": index,
                    "reason": report.reason,
                    "applied": applied,
                    }, file)
                return

        if mapper.node_counter == 0:
            mapper.gen_dfg()
        arch = Arch(arch_cfg, mapper, fail_cfg)
        arch.execute()
        with open(result_path, "w", encoding="utf-8") as file:
            json.dump({
            "status": "drained",
            "cycles": arch.env.now,
            "applied": applied,
            }, file)
    except Exception as exc:  # pragma: no cover - diagnostic path
        with open(result_path, "w", encoding="utf-8") as file:
            json.dump({
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            }, file)


def run_sequence(sequence, fail_path=FAIL_PATH, timeout_seconds=40):
    ctx = mp.get_context("fork")
    with tempfile.NamedTemporaryFile(prefix="local-remap-", suffix=".json", delete=False) as handle:
        result_path = handle.name
    process = ctx.Process(target=_execute_sequence, args=(sequence, fail_path, result_path))
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join()
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass
        return {"status": "timeout", "timeout_seconds": timeout_seconds}

    try:
        with open(result_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {"status": "missing-result"}
    finally:
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass


BASELINE_SEQUENCE = []
SAFE_LAYER5_SEQUENCE = [
    sequence_step("split", 5, 1, 0),
    sequence_step("replace", 5, 0, 6),
]
CHANNEL_LAYER10_SEQUENCE = [
    sequence_step("split", 10, 4, 0),
    sequence_step("replace", 10, 0, 6),
]
LAYER18_BAD_SEQUENCE = [
    sequence_step("remove", 18, 2, 0),
    sequence_step("shift", 18, 14, 7, trace_overrides={
        14: (0.9, 0.9, 10),
        7: (0.1, 0.1, 10),
    }),
]
SMALL_LAYER19_SEQUENCE = [
    sequence_step("split", 19, 0, 2),
    sequence_step("replace", 19, 2, 1),
]
MIXED_LAYER5_LAYER19_SEQUENCE = [
    sequence_step("split", 5, 1, 0),
    sequence_step("split", 19, 0, 2),
]
INVALID_LAYER0_SEQUENCE = [
    sequence_step("replace", 0, 0, 4),
    sequence_step("shift", 0, 0, 4, trace_overrides={
        0: (0.9, 0.9, 10),
        4: (0.1, 0.1, 10),
    }),
]


class LocalRemapTests(unittest.TestCase):
    def make_mapper(self):
        return NetworkMapper(parse_mapping(WORKLOAD_PATH))

    def test_internal_view_layer5(self):
        mapper = self.make_mapper()
        layout = mapper.normalize_layer(5)
        self.assertEqual(layout.active_cores, [1, 6])
        self.assertEqual(layout.input_fetch.dims, [1, 1, 1, 1])
        self.assertEqual(layout.input_source, "core")
        self.assertEqual(layout.input_source_layer_id, 4)
        self.assertEqual(layout.output_dest, "dram")
        self.assertEqual(layout.output_next, [6])
        self.assertEqual(layout.weight_source, "dram")
        self.assertEqual(len(layout.bindings), 2)

    def test_internal_view_matches_reference_dfg(self):
        mapper = self.make_mapper()
        ok, detail = mapper._compare_reference()
        self.assertTrue(ok, detail)

    def test_replace_is_transactional_against_input_network(self):
        source_network = parse_mapping(WORKLOAD_PATH)
        mapper = NetworkMapper(source_network)
        report = mapper.apply_local_remap(5, "replace", 1, 0)
        self.assertIn(report.accepted, {True, False})
        self.assertIsInstance(report.reason, str)

        original_cores = []
        for block in source_network.layers[5].output_feature[0].blocks:
            original_cores.extend((core.x * 4) + core.y for core in block.cores)
        self.assertEqual(sorted(set(original_cores)), [1, 6])

    def test_baseline_mapping_drains(self):
        result = run_sequence(BASELINE_SEQUENCE, timeout_seconds=240)
        self.assertEqual(result["status"], "drained", result)
        self.assertGreaterEqual(result["cycles"], 0)

    def test_safe_layer5_sequence_drains(self):
        result = run_sequence(SAFE_LAYER5_SEQUENCE, timeout_seconds=180)
        self.assertEqual(result["status"], "drained", result)

    def test_channel_layer10_sequence_drains(self):
        result = run_sequence(CHANNEL_LAYER10_SEQUENCE, timeout_seconds=240)
        self.assertEqual(result["status"], "drained", result)

    def test_bad_layer18_sequence_does_not_hang(self):
        result = run_sequence(LAYER18_BAD_SEQUENCE, timeout_seconds=300)
        self.assertEqual(result["status"], "drained", result)

    def test_small_layer19_sequence_drains(self):
        result = run_sequence(SMALL_LAYER19_SEQUENCE, timeout_seconds=240)
        self.assertEqual(result["status"], "drained", result)

    def test_mixed_layer5_layer19_sequence_drains(self):
        result = run_sequence(MIXED_LAYER5_LAYER19_SEQUENCE, timeout_seconds=240)
        self.assertEqual(result["status"], "drained", result)

    def test_invalid_layer0_sequence_is_rejected(self):
        result = run_sequence(INVALID_LAYER0_SEQUENCE, timeout_seconds=120)
        self.assertEqual(result["status"], "rejected", result)
        self.assertIn("empty slice", result["reason"].lower(), result)


if __name__ == "__main__":
    unittest.main()
