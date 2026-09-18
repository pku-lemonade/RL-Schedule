"""Contracts and deterministic planning tests for the multicast child."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from pydantic import ValidationError

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.tests.test_torus import small_graph

EVIDENCE = {"status": "assumed", "description": "synthetic multicast contract"}


def graph_document(width: int = 3, height: int = 2) -> dict[str, Any]:
    document = small_graph(width, height)
    document["attachments"] = []
    document["resources"] = []
    enabled = []
    logical = []
    for tile in document["tiles"]:
        tile_id = tile["tile_id"]
        if tile_id == "t2_0":
            tile["role"] = "transit"
            continue
        enabled.append(tile_id)
        logical.append({"worker_index": len(logical), "logical_x": tile["x"], "logical_y": tile["y"], "tile_id": tile_id})
        resource_id = f"r-{tile_id}"
        document["resources"].append({
            "resource_id": resource_id,
            "kind": "local_sram",
            "owner_tile_id": tile_id,
            "capacity_bytes": 4096,
        })
        document["attachments"].append({
            "endpoint_id": f"ep-{tile_id}",
            "fabric_id": 0,
            "router_id": tile_id,
            "role": "compute",
            "enabled": True,
            "replay_enabled": True,
            "permissions_resolved": True,
            "inject_port": "local",
            "eject_port": "local",
            "resource_ids": [resource_id],
        })
    document["enabled_worker_ids"] = enabled
    document["logical_workers"] = logical
    return document


def memory_document(graph: dict[str, Any]) -> dict[str, Any]:
    resources = graph["resources"]
    endpoints = graph["attachments"]
    return {
        "source": {"kind": "canonical_graph", "graph_path": "graph.json"},
        "aci_clock_hz": 500_000_000.0,
        "fabrics": [0],
        "packet": {
            "physical_flit_bytes": 32,
            "data_capacity_bytes": 32,
            "header_flits": 1,
            "max_segment_payload_bytes": 64,
            "address_alignment_bytes": 32,
            "evidence": copy.deepcopy(EVIDENCE),
        },
        "issue_latency_aci_cycles": 1.0,
        "max_outstanding_segments": 2,
        "endpoint_queue_capacity_packets": 2,
        "endpoint_staging_capacity_flits": 4,
        "resources": [
            {
                "resource_id": item["resource_id"],
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
            }
            for item in resources
        ],
        "endpoints": [
            {
                "endpoint_id": item["endpoint_id"],
                "fabric_id": 0,
                "router_id": item["router_id"],
                "roles": ["initiator", "target", "response_sink"]
                if item["endpoint_id"] == "ep-t0_0"
                else ["target"],
                "resource_ids": item["resource_ids"],
                "evidence": copy.deepcopy(EVIDENCE),
            }
            for item in endpoints
        ],
        "buffers": [
            {
                "buffer_id": f"b-{item['endpoint_id']}",
                "resource_id": item["resource_ids"][0],
                "base_address": 0,
                "size_bytes": 1024,
                "writable": True,
                "readable": True,
                "initially_ready": item["endpoint_id"] == "ep-t0_0",
            }
            for item in endpoints
        ],
        "max_aci_cycles": 10_000.0,
    }


def workload_document(*, major_axis: str = "x", include_source: bool = True,
                      width: int = 3, height: int = 2) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = graph_document(width, height)
    destinations = [
        {
            "endpoint_id": item["endpoint_id"],
            "buffer_id": f"b-{item['endpoint_id']}",
            "offset_bytes": 128,
        }
        for item in graph["attachments"]
    ]
    if not include_source:
        destinations = [item for item in destinations if item["endpoint_id"] != "ep-t0_0"]
    return {
        "kind": "multicast_sync_workload",
        "schema_version": 1,
        "model_revision": "finite_multicast_sync_v1",
        "memory": memory_document(graph),
        "control": {
            "reservation_capacity": 2,
            "replication_capacity_flits": 4,
            "reservation_setup_aci_cycles": 1.0,
            "reservation_edge_aci_cycles": 1.0,
            "atomic_native_cycles": 5.0,
            "local_observation_aci_cycles": 1.0,
            "evidence": copy.deepcopy(EVIDENCE),
        },
        "writes": [
            {
                "operation_id": "broadcast",
                "source_endpoint_id": "ep-t0_0",
                "fabric_id": 0,
                "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 96},
                "target_offset_bytes": 128,
                "size_bytes": 96,
                "rectangle": {
                    "start": {"x": 0, "y": 0},
                    "end": {"x": width - 1, "y": height - 1},
                    "major_axis": major_axis,
                    "include_source": include_source,
                },
                "destinations": destinations,
                "completion": "write_acknowledged",
            }
        ],
    }, graph


class MulticastContractTests(unittest.TestCase):
    def test_versioned_schema_rejects_unknown_and_bool_values(self) -> None:
        document, _ = workload_document()
        parsed = MulticastSyncWorkload.model_validate(document)
        self.assertEqual(parsed, MulticastSyncWorkload.model_validate(parsed.model_dump(mode="json")))
        unknown = copy.deepcopy(document)
        unknown["unexpected"] = True
        with self.assertRaises(ValidationError):
            MulticastSyncWorkload.model_validate(unknown)
        boolean_version = copy.deepcopy(document)
        boolean_version["schema_version"] = True
        with self.assertRaises(ValidationError):
            MulticastSyncWorkload.model_validate(boolean_version)

    def test_x_major_plan_is_deterministic_and_admission_only(self) -> None:
        document, graph_document_value = workload_document()
        workload = MulticastSyncWorkload.model_validate(document)
        graph = CanonicalTopology.model_validate(graph_document_value)
        first = MulticastSyncPlan.compile(workload, graph)
        second = MulticastSyncPlan.compile(workload, graph)
        self.assertEqual(first.record, second.record)
        self.assertEqual(first.record.operation_order, ("broadcast",))
        self.assertEqual(first.record.writes[0].destination_useful_bytes, 5 * 96)
        self.assertEqual(first.record.writes[0].packet_physical_bytes, 160)
        self.assertEqual(len(first.record.writes[0].segments), 2)
        self.assertEqual(len(first.record.writes[0].tree.edges), 5)
        self.assertEqual(
            [(edge.src_router, edge.dst_router) for edge in first.record.writes[0].tree.edges],
            [("t0_0", "t0_1"), ("t0_0", "t1_0"), ("t1_0", "t2_0"), ("t0_1", "t1_1"), ("t1_1", "t2_1")],
        )
        self.assertEqual(first.planning_result().status, "planned")
        with self.assertRaisesRegex(RuntimeError, "admission-only"):
            first.require_executable()
        first.revalidate()

    def test_y_major_and_source_exclusion(self) -> None:
        document, graph_value = workload_document(major_axis="y", include_source=False)
        write = document["writes"][0]
        write["rectangle"]["start"] = {"x": 0, "y": 0}
        write["rectangle"]["end"] = {"x": 1, "y": 1}
        # The source remains at the corner but is intentionally excluded.
        document["writes"][0]["destinations"] = [
            item for item in document["writes"][0]["destinations"]
            if item["endpoint_id"] not in {"ep-t0_0", "ep-t2_1"}
        ]
        plan = MulticastSyncPlan.compile(
            MulticastSyncWorkload.model_validate(document),
            CanonicalTopology.model_validate(graph_value),
        )
        self.assertEqual(len(plan.record.writes[0].tree.recipients), 3)
        self.assertEqual([(edge.src_router, edge.dst_router) for edge in plan.record.writes[0].tree.edges],
                         [("t0_0", "t1_0"), ("t0_0", "t0_1"), ("t1_0", "t1_1")])

    def test_invalid_recipient_shape_and_incomplete_graph_fail(self) -> None:
        document, graph_value = workload_document()
        missing = copy.deepcopy(document)
        missing["writes"][0]["destinations"].pop()
        with self.assertRaises(ValueError):
            MulticastSyncPlan.compile(
                MulticastSyncWorkload.model_validate(missing), CanonicalTopology.model_validate(graph_value)
            )
        incomplete = copy.deepcopy(graph_value)
        incomplete["connectivity_state"] = "unresolved"
        with self.assertRaises(ValueError):
            MulticastSyncPlan.compile(
                MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(incomplete)
            )
        overlap = copy.deepcopy(document)
        overlap["writes"][0]["target_offset_bytes"] = 0
        overlap["writes"][0]["destinations"][0]["offset_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "overlap"):
            MulticastSyncPlan.compile(
                MulticastSyncWorkload.model_validate(overlap), CanonicalTopology.model_validate(graph_value)
            )

    def test_control_bounds_widths_and_overflow_are_admitted_or_rejected_explicitly(self) -> None:
        document, graph_value = workload_document()
        invalid_duration = copy.deepcopy(document)
        invalid_duration["control"]["reservation_edge_aci_cycles"] = -1.0
        with self.assertRaises(ValidationError):
            MulticastSyncWorkload.model_validate(invalid_duration)
        unsupported = copy.deepcopy(document)
        unsupported["model_revision"] = "legacy_multicast"
        with self.assertRaises(ValidationError):
            MulticastSyncWorkload.model_validate(unsupported)

        changed_geometry = copy.deepcopy(document)
        changed_geometry["memory"]["packet"].update({
            "physical_flit_bytes": 64,
            "data_capacity_bytes": 16,
            "max_segment_payload_bytes": 32,
            "address_alignment_bytes": 16,
        })
        for resource in changed_geometry["memory"]["resources"]:
            resource["service"].update(service_granule_bytes=16, chunk_bytes=16)
        changed = MulticastSyncPlan.compile(
            MulticastSyncWorkload.model_validate(changed_geometry),
            CanonicalTopology.model_validate(graph_value),
        )
        self.assertEqual(changed.record.writes[0].segments[0].flit_count, 3)

        overflow = copy.deepcopy(document)
        overflow["counters"] = [{
            "counter_id": "counter",
            "endpoint_id": "ep-t0_0",
            "buffer_id": "b-ep-t0_0",
            "offset_bytes": 256,
            "width_bytes": 1,
            "initial_value": 255,
        }]
        overflow["increments"] = [{
            "operation_id": "increment",
            "source_endpoint_id": "ep-t0_0",
            "fabric_id": 0,
            "counter_id": "counter",
            "completion": "atomic_posted",
        }]
        with self.assertRaisesRegex(ValueError, "overflows"):
            MulticastSyncPlan.compile(
                MulticastSyncWorkload.model_validate(overflow), CanonicalTopology.model_validate(graph_value)
            )


if __name__ == "__main__":
    unittest.main()
