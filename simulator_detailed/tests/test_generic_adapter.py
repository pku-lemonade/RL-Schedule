"""Adapter boundary and privacy guard tests; synthetic formats only."""

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_graph import GenericSystemGraph
from simulator_detailed.configs.schemas.synthetic_world import SyntheticWorld
from simulator_detailed.generic_adapter import (
    GenericInputAdapter,
    run_generic_adapter,
    scan_forbidden_tokens,
)
from simulator_detailed.generic_graph import load_generic_system, topology_from_generic
from simulator_detailed.generic_runtime import run_generic_batch
from simulator_detailed.replay_generic import load_batch
from simulator_detailed.synthetic_adapter import SyntheticGridWorldAdapter
from simulator_detailed.topology_compatibility import (
    legacy_event_rows,
    require_legacy_topology,
)

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json"
ACCEPTANCE = ROOT / "configs" / "generic_transactions" / "acceptance_batch.json"
WORLD = ROOT / "configs" / "generic_adapters" / "synthetic_world_2d.json"

# The public generic surface. configs/schemas/generic_graph.py is excluded
# because it defines FORBIDDEN_DEVICE_TOKENS, which must name the tokens.
PUBLIC_GENERIC_FILES = (
    ROOT / "configs" / "schemas" / "generic_transactions.py",
    ROOT / "configs" / "schemas" / "synthetic_world.py",
    ROOT / "generic_graph.py",
    ROOT / "generic_runtime.py",
    ROOT / "replay_generic.py",
    ROOT / "generic_adapter.py",
    ROOT / "synthetic_adapter.py",
    ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json",
    ROOT / "configs" / "generic_transactions" / "acceptance_batch.json",
    ROOT / "configs" / "generic_adapters" / "synthetic_world_2d.json",
    ROOT / "docs" / "generic_simulation.md",
)


class TestSyntheticAdapter(unittest.TestCase):
    def test_graph_matches_file_fixture(self):
        adapter = SyntheticGridWorldAdapter.from_path(WORLD)
        converted = adapter.load_system_graph()
        reference = GenericSystemGraph.model_validate(json.loads(GRAPH.read_text()))
        self.assertEqual(converted.model_dump(mode="json"), reference.model_dump(mode="json"))
        self.assertEqual(
            topology_from_generic(converted).generic_sha256,
            load_generic_system(GRAPH).generic_sha256,
        )

    def test_end_to_end_matches_file_loaded(self):
        via_adapter = run_generic_adapter(SyntheticGridWorldAdapter.from_path(WORLD))
        system, batch = load_batch(ACCEPTANCE)
        via_files = run_generic_batch(system, batch)
        left = via_adapter.model_dump(mode="json")
        right = via_files.model_dump(mode="json")
        # batch identity differs by declaration; every behavior must agree.
        for key in ("batch_id", "batch_sha256", "plan_sha256"):
            left.pop(key)
            right.pop(key)
        self.assertEqual(left, right)

    def test_boundary_revalidates_bypassed_construction(self):
        adapter = SyntheticGridWorldAdapter.from_path(WORLD)

        class RogueGraph(GenericInputAdapter):
            def load_system_graph(self):
                return adapter.load_system_graph().model_copy(update={"nodes": ()})

            def load_transactions(self):
                return adapter.load_transactions()

        with self.assertRaises(ValidationError):
            run_generic_adapter(RogueGraph())

        class RogueBatch(GenericInputAdapter):
            def load_system_graph(self):
                return adapter.load_system_graph()

            def load_transactions(self):
                return adapter.load_transactions().model_copy(update={"transactions": ()})

        with self.assertRaises(ValidationError):
            run_generic_adapter(RogueBatch())

    def test_synthetic_schema_rejections(self):
        def expect_invalid(mutate):
            document = json.loads(WORLD.read_text())
            mutate(document)
            with self.assertRaises(ValidationError):
                SyntheticWorld.model_validate(document)

        expect_invalid(lambda document: document["sites"][0].update({"vendor": "x"}))
        expect_invalid(lambda document: document["jobs"][0].update({"act": "teleport"}))
        expect_invalid(lambda document: document["jobs"].append({
            "job": "w_9", "act": "await", "gauge": "ghost",
            "until": 1, "after": [], "at_cycle": 0.0,
        }))
        expect_invalid(lambda document: document["wires"][0].update({"from_socket": "dma0"}))
        expect_invalid(lambda document: document.update({"schema_version": 2}))


class TestPrivacyGuard(unittest.TestCase):
    def test_public_files_are_clean(self):
        self.assertEqual(scan_forbidden_tokens(PUBLIC_GENERIC_FILES), ())

    def test_seeded_violation_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leaked.json"
            path.write_text('{"node_id": "n00-wormhole"}')
            findings = scan_forbidden_tokens([path])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].token, "wormhole")
        self.assertTrue(findings[0].path.endswith("leaked.json"))

    def test_no_dynamic_discovery_or_network(self):
        for name in (
            "generic_adapter.py",
            "synthetic_adapter.py",
            "generic_runtime.py",
            "replay_generic.py",
        ):
            text = (ROOT / name).read_text()
            for token in (
                "importlib",
                "entry_points",
                "import subprocess",
                "import socket",
                "urllib",
                "requests",
            ):
                self.assertNotIn(token, text, f"{name} must not reference {token}")


if __name__ == "__main__":
    unittest.main()


class TestLegacyConsumerGuards(unittest.TestCase):
    def test_generic_kinds_rejected_by_legacy_consumers(self):
        for kind in (
            "generic_system_graph",
            "generic_system_inspection",
            "generic_transaction_batch",
            "generic_simulation_result",
            "synthetic_grid_world",
            "system_spec",
            "immutable_plan",
        ):
            for value in ({"kind": kind}, json.dumps({"kind": kind})):
                if isinstance(value, str):
                    value = json.loads(value)
                with self.assertRaises(TypeError, msg=kind):
                    legacy_event_rows(value, "communication")

    def test_generic_system_is_not_a_legacy_topology(self):
        system = load_generic_system(GRAPH)
        for consumer in ("detailed_predictor", "detailed_encoder"):
            with self.assertRaises(ValueError):
                require_legacy_topology(system, consumer)
