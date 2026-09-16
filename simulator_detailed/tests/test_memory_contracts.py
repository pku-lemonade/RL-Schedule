"""Admission-only tests for the addressed-memory child."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.memory_replay import MemoryReplay
from simulator_detailed.memory_plan import MemoryPlan

ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads((ROOT / "configs/topologies/heterogeneous_example.json").read_text())
EVIDENCE = {"status": "assumed", "description": "contract test assumption"}


def replay_document() -> dict[str, object]:
    return {
        "kind": "memory_replay",
        "schema_version": 1,
        "model_revision": "addressed_memory_v1",
        "source": {"kind": "canonical_graph", "graph_path": "graph.json"},
        "aci_clock_hz": 500_000_000.0,
        "fabrics": [0],
        "packet": {
            "physical_flit_bytes": 32,
            "data_capacity_bytes": 32,
            "header_flits": 1,
            "max_segment_payload_bytes": 8192,
            "address_alignment_bytes": 32,
            "evidence": copy.deepcopy(EVIDENCE),
        },
        "issue_latency_aci_cycles": 1.0,
        "max_outstanding_segments": 2,
        "endpoint_queue_capacity_packets": 2,
        "endpoint_staging_capacity_flits": 2,
        "resources": [
            {
                "resource_id": "dram",
                "service": {
                    "policy": "aggregate_shared_rw_v1",
                    "native_clock_hz": 1_000_000_000.0,
                    "service_granule_bytes": 32,
                    "chunk_bytes": 32,
                    "bytes_per_cycle": 32.0,
                    "fixed_latency_cycles": 1.0,
                    "queue_capacity": 4,
                    "evidence": copy.deepcopy(EVIDENCE),
                },
                "evidence": copy.deepcopy(EVIDENCE),
            },
            {
                "resource_id": "l1-a",
                "service": {
                    "policy": "aggregate_shared_rw_v1",
                    "native_clock_hz": 1_000_000_000.0,
                    "service_granule_bytes": 32,
                    "chunk_bytes": 32,
                    "bytes_per_cycle": 32.0,
                    "fixed_latency_cycles": 1.0,
                    "queue_capacity": 4,
                    "evidence": copy.deepcopy(EVIDENCE),
                },
                "evidence": copy.deepcopy(EVIDENCE),
            },
        ],
        "endpoints": [
            {
                "endpoint_id": "source",
                "fabric_id": 0,
                "router_id": "a",
                "roles": ["initiator"],
                "resource_ids": ["l1-a"],
                "evidence": copy.deepcopy(EVIDENCE),
            },
            {
                "endpoint_id": "ram-noc0",
                "fabric_id": 0,
                "router_id": "ram",
                "roles": ["target"],
                "resource_ids": ["dram"],
                "enabled": False,
                "evidence": copy.deepcopy(EVIDENCE),
            },
        ],
        "buffers": [
            {
                "buffer_id": "local",
                "resource_id": "l1-a",
                "base_address": 0,
                "size_bytes": 64,
                "initially_ready": True,
            },
            {
                "buffer_id": "remote",
                "resource_id": "dram",
                "base_address": 0,
                "size_bytes": 64,
            },
        ],
        "operations": [
            {
                "operation_id": "write",
                "initiator_id": "source",
                "fabric_id": 0,
                "kind": "write_acknowledged",
                "source": {"buffer_id": "local", "offset_bytes": 0, "size_bytes": 32},
                "destination": {"buffer_id": "remote", "offset_bytes": 0, "size_bytes": 32},
            }
        ],
        "max_aci_cycles": 100.0,
    }


class MemoryContractTests(unittest.TestCase):
    def test_round_trip_is_immutable_and_compiles_without_runtime(self) -> None:
        document = replay_document()
        config = MemoryReplay.model_validate(document)
        self.assertEqual(config, MemoryReplay.model_validate_json(config.model_dump_json()))
        document["operations"] = []
        self.assertEqual(config.operations[0].operation_id, "write")
        plan = MemoryPlan.compile(config, GRAPH)
        self.assertEqual(plan.record.source_kind, "canonical_graph")
        self.assertEqual(plan.graph.connectivity_state, "complete")
        self.assertEqual(plan.record.resource_ids, ("dram", "l1-a"))
        self.assertFalse(hasattr(plan, "env"))

    def test_source_path_does_not_change_plan_identity(self) -> None:
        first = MemoryReplay.model_validate(replay_document())
        second_document = replay_document()
        second_document["source"] = {"kind": "canonical_graph", "graph_path": "../other/graph.json"}
        second = MemoryReplay.model_validate(second_document)
        self.assertEqual(MemoryPlan.compile(first, GRAPH).plan_sha256, MemoryPlan.compile(second, GRAPH).plan_sha256)

    def test_invalid_modes_and_ranges_fail_before_plan(self) -> None:
        mutations = [
            lambda value: value.update(schema_version=True),
            lambda value: value["packet"].update(data_capacity_bytes=33),
            lambda value: value["packet"].update(max_segment_payload_bytes=8001),
            lambda value: value["operations"][0].update(kind="write_posted", fence_mode="remote_completion"),
            lambda value: value["operations"][0]["source"].update(buffer_id="missing"),
            lambda value: value["buffers"][0].update(base_address=33),
            lambda value: value["endpoints"][0].update(fabric_id=7),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = replay_document()
                mutate(value)
                with self.assertRaises((ValidationError, ValueError)):
                    config = MemoryReplay.model_validate(value)
                    MemoryPlan.compile(config, GRAPH)

    def test_dependency_cycle_and_posted_fence_are_rejected(self) -> None:
        value = replay_document()
        value["operations"].append(
            {
                "operation_id": "fence",
                "initiator_id": "source",
                "kind": "fence",
                "fence_mode": "remote_completion",
                "fence_operations": ["write"],
            }
        )
        value["operations"][0]["kind"] = "write_posted"
        with self.assertRaises(ValidationError):
            MemoryReplay.model_validate(value)

        value = replay_document()
        value["operations"][0]["depends_on"] = ["second"]
        value["operations"].append(
            {
                "operation_id": "second",
                "initiator_id": "source",
                "fabric_id": 0,
                "kind": "write_acknowledged",
                "source": {"buffer_id": "local", "offset_bytes": 0, "size_bytes": 32},
                "destination": {"buffer_id": "remote", "offset_bytes": 0, "size_bytes": 32},
                "depends_on": ["write"],
            }
        )
        with self.assertRaises(ValidationError):
            MemoryReplay.model_validate(value)

    def test_profile_source_is_inventory_only_until_connectivity_is_resolved(self) -> None:
        value = replay_document()
        value["source"] = {"kind": "hardware_profile", "profile_path": "profile.json"}
        config = MemoryReplay.model_validate(value)
        profile = json.loads((ROOT / "configs/profiles/wormhole_b0_n150_assumed.json").read_text())
        with self.assertRaisesRegex(ValueError, "complete topology"):
            MemoryPlan.compile(config, profile)


if __name__ == "__main__":
    unittest.main()
