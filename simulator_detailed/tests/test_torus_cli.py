"""Public version dispatch, preconstruction rejection and output-file boundaries."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.replay_topology import run_replay
from simulator_detailed.torus_records import TorusReplayResult

ROOT = Path(__file__).resolve().parents[1]
GENERIC = ROOT / "configs/replays/torus_small_v2.json"
WORMHOLE = ROOT / "configs/replays/wormhole_transport_v2.json"


class TorusCLITests(unittest.TestCase):
    def test_published_examples_emit_versioned_json_and_matching_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            for path, payload, channel_bytes in ((GENERIC, 241, 1760), (WORMHOLE, 148, 4160)):
                # Run outside the config directory: sources resolve relative to the replay.
                run = subprocess.run(
                    [sys.executable, "-m", "simulator_detailed.replay_topology", "--replay", str(path),
                     "--output", str(output)], capture_output=True, text=True, check=False,
                )
                with self.subTest(example=path.name):
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(run.stderr, "")
                    self.assertEqual(json.loads(run.stdout), json.loads(output.read_text()))
                    result = TorusReplayResult.model_validate_json(run.stdout)
                    self.assertEqual(result.status, "complete")
                    self.assertEqual(result.received_payload_bytes, payload)
                    self.assertEqual(result.transmitted_channel_bytes, channel_bytes)
                    self.assertTrue(all(resource.is_drained for resource in result.resources))
                    self.assertEqual(result.memory_service, "unsupported")
                    if path == WORMHOLE:
                        self.assertEqual(len(result.plan.graph.routers), 240)
                        self.assertEqual(len(result.plan.graph.links), 480)
                        self.assertEqual({packet.fabric_id for packet in result.packets}, {0, 1})

    def test_invalid_headers_and_settings_fail_before_environment(self):
        original = json.loads(GENERIC.read_text())
        original["source"]["graph_path"] = str(ROOT / "configs/topologies/torus_small_v2.json")
        cases = [[], {"kind": "topology_replay"},
                 *[{**original, "schema_version": value} for value in (True, 2.0, "2", 0, 3)],
                 {**original, "kind": "topology_replay_result"},
                 {**original, "hardware_vc_priority": 1}]
        missing_link = copy.deepcopy(original)
        missing_link["slowdowns"][0]["link_id"] = "missing"
        cases.append(missing_link)
        unavailable_route = copy.deepcopy(original)
        unavailable_route["traffic"][0]["destination"] = "unbound"
        cases.append(unavailable_route)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for document in cases:
                path.write_text(json.dumps(document))
                with self.subTest(document=document), patch("simulator_detailed.torus_transport.simpy.Environment") as env:
                    with self.assertRaises((ValueError, TypeError)):
                        run_replay(path)
                    env.assert_not_called()

    def test_invalid_and_incomplete_cli_output_contract(self):
        document = json.loads(GENERIC.read_text())
        document["source"]["graph_path"] = str(ROOT / "configs/topologies/torus_small_v2.json")
        with tempfile.TemporaryDirectory() as directory:
            replay, output = Path(directory) / "replay.json", Path(directory) / "output.json"
            args = [sys.executable, "-m", "simulator_detailed.replay_topology", "--replay", str(replay),
                    "--output", str(output)]
            document["slowdowns"][0]["link_id"] = "missing"
            replay.write_text(json.dumps(document))
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertEqual(run.stdout, "")
            self.assertIn("slowdown", run.stderr)
            self.assertFalse(output.exists())
            output.write_text("existing artifact\n")
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(output.read_text(), "existing artifact\n")
            document["slowdowns"] = []
            document["max_aci_cycles"] = 1
            replay.write_text(json.dumps(document))
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(run.stdout), json.loads(output.read_text()))
            result = TorusReplayResult.model_validate_json(run.stdout)
            self.assertEqual(result.reason, "cycle_limit")
            self.assertTrue(any(not resource.is_drained for resource in result.resources))
