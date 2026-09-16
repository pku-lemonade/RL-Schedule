"""Executable examples, pure profile admission and independent exported-byte audits."""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.memory_execution import (
    MemoryExecutionPlan,
    MemoryExecutionResult,
)
from simulator_detailed.replay_memory import load_plan, run_replay
from simulator_detailed.topology import content_digest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "simulator_detailed/configs/memory_replays"
GENERIC = EXAMPLES / "generic_ordered.json"
WORMHOLE = EXAMPLES / "wormhole_ordered.json"


def standalone(path):
    doc = json.loads(path.read_text())
    source = doc["source"]
    field = "graph_path" if source["kind"] == "canonical_graph" else "profile_path"
    source[field] = str((path.parent / source[field]).resolve())
    return doc


class MemoryCliTests(unittest.TestCase):
    def cli(self, path, output, *, cwd=ROOT):
        return subprocess.run([sys.executable, "-m", "simulator_detailed.replay_memory", "--replay", str(path),
                               "--output", str(output)], cwd=cwd, text=True, capture_output=True, check=False)

    def test_generic_cli_determinism_output_and_incomplete_status(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            complete = self.cli(GENERIC, output)
            self.assertEqual(complete.returncode, 0, complete.stderr)
            self.assertEqual(complete.stdout, output.read_text())
            self.assertEqual(complete.stdout, self.cli(GENERIC, output).stdout)
            result = json.loads(complete.stdout)
            self.assertEqual(result["elapsed_aci_cycles"], 387)
            self.assertEqual(result["logical_bytes"], 206)
            short = self.cli(EXAMPLES / "generic_incomplete.json", output)
            self.assertEqual(short.returncode, 2, short.stderr)
            self.assertEqual(short.stdout, output.read_text())
            result = json.loads(short.stdout)
            self.assertEqual((result["status"], result["reason"]), ("incomplete", "cycle_limit"))
            self.assertEqual((result["packet_bytes"], result["channel_bytes"], result["memory_service_bytes"]), (128, 576, 116))
            self.assertTrue(result["pending"])
            self.assertFalse(result["teardown_complete"])
            self.assertEqual(result["released_resources"], [])

    def test_wormhole_cli_profile_aliases_and_full_plan_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            completed = self.cli(WORMHOLE, output)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, output.read_text())
            result = json.loads(completed.stdout)
        self.assertEqual((result["elapsed_aci_cycles"], result["packet_bytes"], result["channel_bytes"]), (1382, 8544, 183936))
        self.assertEqual(result["silicon_timing"], "unvalidated")
        graph = json.loads(result["plan"]["source_json"])
        self.assertEqual((len(graph["routers"]), len(graph["links"]), len(graph["resources"])), (240, 480, 86))
        self.assertEqual(content_digest(graph), result["plan"]["source_sha256"])
        profile = json.loads(graph["origin"]["document_json"])
        self.assertEqual(content_digest(profile), graph["origin"]["content_hash"])
        self.assertEqual(len([r for r in result["memory_resources"] if r["resource_id"] == "dram0"]), 1)
        requests = {p["definition"]["packet"]["identity"]["operation_id"]: p["definition"]["route"]
                    for p in result["wire_packets"] if p["definition"]["packet"]["identity"]["purpose"] in {"write_request", "read_request"}}
        self.assertEqual(requests["ack"]["destination"], "tile_0_0_noc0")
        self.assertEqual(requests["read"]["destination"], "tile_0_1_noc1")
        self.assertEqual([s["logical_bytes"] for s in result["segments"] if s["operation_id"] == "ack"], [8192, 1])
        self.assert_accounting(result)

    def test_invalid_documents_fail_before_environment_and_preserve_output(self):
        valid = standalone(GENERIC)
        invalid = []
        for field, value in (("kind", "topology_replay"), ("schema_version", True), ("schema_version", "1"),
                             ("schema_version", 2), ("unexpected", 1)):
            invalid.append({**copy.deepcopy(valid), field: value})
        missing = copy.deepcopy(valid)
        del missing["runtime"]
        invalid.append(missing)
        mismatch = copy.deepcopy(valid)
        mismatch["runtime"]["transport"]["endpoint_staging_capacity_flits"] = 2
        invalid.append(mismatch)
        unsupported = copy.deepcopy(valid)
        unsupported["operations"][1]["kind"] = "atomic"
        invalid.append(unsupported)
        wrong_port = copy.deepcopy(valid)
        wrong_port["endpoints"][0]["inject_port"] = "unpermitted"
        invalid.append(wrong_port)
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "replay.json", Path(directory) / "output.json"
            output.write_text("keep this result\n")
            for doc in invalid:
                with self.subTest(doc=doc.get("schema_version")):
                    path.write_text(json.dumps(doc))
                    with (patch("simulator_detailed.memory_runtime.simpy.Environment", side_effect=AssertionError("runtime allocated")),
                          self.assertRaises((ValueError, TypeError))):
                        run_replay(path)
                    run = self.cli(path, output)
                    self.assertEqual(run.returncode, 1, run.stderr)
                    self.assertEqual(run.stdout, "")
                    self.assertTrue(run.stderr)
                    self.assertEqual(output.read_text(), "keep this result\n")
            for text in ("{", "[]"):
                path.write_text(text)
                self.assertEqual(self.cli(path, output).returncode, 1)
                self.assertEqual(output.read_text(), "keep this result\n")
            self.assertEqual(self.cli(Path(directory) / "missing.json", output).returncode, 1)
            self.assertEqual(output.read_text(), "keep this result\n")

    def test_source_paths_are_relative_and_runtime_settings_have_identity(self):
        original = load_plan(GENERIC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            path.write_text(json.dumps(standalone(GENERIC)))
            relocated = load_plan(path)
        self.assertEqual(original.plan_sha256, relocated.plan_sha256)
        self.assertEqual(original.memory.record.source_sha256, relocated.memory.record.source_sha256)
        self.assertEqual(original.memory.record.configuration_sha256, relocated.memory.record.configuration_sha256)
        changed = original.settings.model_copy(update={"request_control_aci_cycles": 2.0})
        reconfigured = MemoryExecutionPlan.compile(original.memory, changed)
        self.assertNotEqual(original.plan_sha256, reconfigured.plan_sha256)
        self.assertEqual(original.memory.plan_sha256, reconfigured.memory.plan_sha256)

    def test_profile_binding_rejects_unavailable_or_incomplete_permissions_before_runtime(self):
        valid = standalone(WORMHOLE)
        variants = []
        missing = copy.deepcopy(valid)
        missing["routing"] = []
        variants.append(missing)
        ports = copy.deepcopy(valid)
        for e in ports["endpoints"]:
            e.pop("inject_port")
            e.pop("eject_port")
        variants.append(ports)
        harvested = copy.deepcopy(valid)
        selected = next(e for e in harvested["endpoints"] if e["endpoint_id"] == "tile_1_1_noc0")
        selected.update(endpoint_id="tile_1_11_noc0", router_id="tile_1_11", resource_ids=["l1_tile_1_11"])
        for op in harvested["operations"]:
            if op["initiator_id"] == "tile_1_1_noc0":
                op["initiator_id"] = "tile_1_11_noc0"
        variants.append(harvested)
        alias = copy.deepcopy(valid)
        next(e for e in alias["endpoints"] if e["endpoint_id"] == "tile_0_0_noc0")["resource_ids"] = ["dram1"]
        variants.append(alias)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            for doc in variants:
                path.write_text(json.dumps(doc))
                with (patch("simulator_detailed.memory_runtime.simpy.Environment", side_effect=AssertionError("runtime allocated")),
                      self.assertRaises(ValueError)):
                    run_replay(path)
        plan = load_plan(WORMHOLE)
        plan.memory.graph = plan.memory.graph.model_copy(update={"links": ()})
        with self.assertRaisesRegex(ValueError, "admitted source"):
            MemoryExecutionPlan.compile(plan.memory, plan.settings)

    def assert_accounting(self, result):
        # Reconstruct from serialized raw events, without production accounting helpers.
        per_packet = defaultdict(lambda: {"useful_bytes": 0, "header_bytes": 0, "padding_bytes": 0, "packet_bytes": 0, "channel": 0})
        definitions = {}
        for record in result["wire_packets"]:
            identity = record["definition"]["packet"]["identity"]
            definitions[json.dumps([identity["operation_id"], identity["segment_index"], identity["purpose"]], separators=(",", ":"))] = record
        for event in result["transport"]["trace"]:
            if event["action"] != "link_launch":
                continue
            key = event["packet"]["transfer_id"]
            totals = per_packet[key]
            totals["channel"] += event["physical_bytes"]
            layout = definitions[key]["definition"]["packet"]["layout"]
            if event["channel"]["kind"] == "inject":
                totals["packet_bytes"] += event["physical_bytes"]
                totals["useful_bytes"] += event["payload_bytes"]
                header = event["flit_index"] < layout["header_flits"]
                totals["header_bytes"] += event["physical_bytes"] if header else 0
                totals["padding_bytes"] += 0 if header else event["physical_bytes"] - event["payload_bytes"]
        for key, record in definitions.items():
            layout = record["definition"]["packet"]["layout"]
            flits = (layout["useful_bytes"] + layout["data_capacity_bytes"] - 1) // layout["data_capacity_bytes"]
            expected = {"useful_bytes": layout["useful_bytes"], "header_bytes": layout["header_flits"] * layout["physical_flit_bytes"],
                        "padding_bytes": flits * layout["physical_flit_bytes"] - layout["useful_bytes"],
                        "packet_bytes": (flits + layout["header_flits"]) * layout["physical_flit_bytes"]}
            self.assertEqual(record["planned"], expected)
            self.assertEqual(record["planned_channel_bytes"], expected["packet_bytes"] * len(record["definition"]["route"]["hops"]))
            for field, value in record["injected"].items():
                self.assertEqual(value, per_packet[key][field])
            self.assertEqual(record["launched_channel_bytes"], per_packet[key]["channel"])
        accounting = result["accounting"]
        for kind, field in (("planned", "planned_wire"), ("injected", "injected_wire")):
            for unit, value in accounting[field].items():
                self.assertEqual(value, sum(p[kind][unit] for p in result["wire_packets"]))
        self.assertEqual(result["packet_bytes"], accounting["injected_wire"]["packet_bytes"])
        self.assertEqual(result["channel_bytes"], sum(p["channel"] for p in per_packet.values()))
        self.assertEqual(result["logical_bytes"], accounting["planned_network_logical_bytes"] + accounting["planned_local_logical_bytes"])
        config = json.loads(result["plan"]["configuration_json"])
        services = {r["resource_id"]: r["service"] for r in config["resources"]}
        for c in result["chunks"]:
            s = services[c["resource_id"]]
            rounded = math.ceil((c["address"] % s["service_granule_bytes"] + c["useful_bytes"]) / s["service_granule_bytes"]) * s["service_granule_bytes"]
            self.assertEqual(c["serviced_bytes"], rounded)
            native = s["fixed_latency_cycles"] + rounded / s["bytes_per_cycle"]
            self.assertEqual(c["native_cycles"], native)
            aci = native * config["aci_clock_hz"] / s["native_clock_hz"]
            self.assertAlmostEqual(c["service_aci_cycles"], aci)
            self.assertAlmostEqual(c["end_aci_cycles"] - c["start_aci_cycles"], aci)
        for direction in ("read", "write"):
            chunks = [c for c in result["chunks"] if c["direction"] == direction]
            self.assertEqual(accounting[f"completed_{direction}_useful_bytes"], sum(c["useful_bytes"] for c in chunks))
            self.assertEqual(accounting[f"completed_{direction}_service_bytes"], sum(c["serviced_bytes"] for c in chunks))
        self.assertEqual(result["memory_service_bytes"], sum(c["serviced_bytes"] for c in result["chunks"]))
        names = {"submission": "submission", "acceptance": "descriptor_acceptance", "source_read_complete": "source_read_completion",
                 "request_handoff": "final_request_handoff", "response_wire_receipt": "response_receipt", "destination_ready": "destination_ready", "complete": "completion"}
        for segment in result["segments"]:
            events = [e for e in result["lifecycle"] if e["operation_id"] == segment["operation_id"] and e["segment_index"] == segment["segment"]["segment_index"]]
            for action, field in names.items():
                times = [e["time_aci_cycles"] for e in events if e["action"] == action]
                self.assertEqual(times, [] if segment[field + "_aci_cycles"] is None else [segment[field + "_aci_cycles"]])

    def test_complete_and_incomplete_export_reconcile_and_roundtrip(self):
        for name in ("generic_ordered", "generic_incomplete"):
            result = run_replay(EXAMPLES / (name + ".json"))
            self.assert_accounting(json.loads(result.model_dump_json()))
            self.assertEqual(result, MemoryExecutionResult.model_validate_json(result.model_dump_json()))
            tampered = result.model_dump(mode="json")
            tampered["accounting"]["completed_read_service_bytes"] += 4
            with self.assertRaises(ValueError):
                MemoryExecutionResult.model_validate(tampered)
            if result.status == "incomplete":
                self.assertGreater(result.accounting.planned_wire.packet_bytes, result.packet_bytes)
                self.assertTrue(any(p.injected_flits == 0 for p in result.wire_packets))
                self.assertTrue(any(r.service.pending_service_ids for r in result.memory_resources))


if __name__ == "__main__":
    unittest.main()
