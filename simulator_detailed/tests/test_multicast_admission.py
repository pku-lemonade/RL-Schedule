"""Admission failures must precede allocation and identify actual hardware inputs."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.configs.schemas.multicast_sync import (
    MulticastSyncWorkload,
    ScalarCounter,
)
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.multicast_scalar import ScalarExecutor
from simulator_detailed.multicast_transport import TreeTransportPlan
from simulator_detailed.tests.test_multicast_sync import workload_document
from simulator_detailed.tests.test_multicast_tree_admission import compile_document

ROOT = Path(__file__).resolve().parents[1]


class MulticastAdmissionTests(unittest.TestCase):
    def test_source_locators_and_graph_order_do_not_change_effective_identity(self) -> None:
        document, graph = workload_document()
        first = compile_document(document, graph)
        document["memory"]["source"]["graph_path"] = "../relocated/graph.json"
        for field in ("tiles", "routers", "links", "attachments", "resources", "fabrics", "enabled_worker_ids", "logical_workers"):
            graph[field].reverse()
        second = compile_document(document, graph)
        self.assertEqual(first.record, second.record)
        document["memory"]["resources"][0]["service"]["native_clock_hz"] /= 2
        changed = compile_document(document, graph)
        self.assertNotEqual(first.record.plan_sha256, changed.record.plan_sha256)

    def test_shared_memory_geometry_capacity_and_bindings_are_checked_before_allocation(self) -> None:
        changes = {
            "overlapping_reservations": lambda d: d["memory"]["buffers"].append({
                **d["memory"]["buffers"][0], "buffer_id": "alias"}),
            "effective_capacity": lambda d: d["memory"]["resources"][0].update(capacity_override_bytes=64),
            "unaligned_reservation": lambda d: d["memory"]["buffers"][0].update(base_address=1),
            "unaligned_source": lambda d: d["writes"][0]["source"].update(offset_bytes=1),
            "missing_resource": lambda d: d["memory"]["resources"].pop(),
            "foreign_binding": lambda d: d["memory"]["endpoints"][0].update(resource_ids=["r-t1_0"]),
            "unrepresentable_service": lambda d: d["memory"]["resources"][0]["service"].update(bytes_per_cycle=5e-324),
            "incompatible_chunk": lambda d: d["memory"]["resources"][0]["service"].update(chunk_bytes=64),
            "unrepresentable_atomic": lambda d: d["control"].update(atomic_native_cycles=5e-324),
            "unrepresentable_controller": lambda d: d["control"].update(reservation_edge_aci_cycles=1e308),
        }
        for name, change in changes.items():
            with self.subTest(name=name), patch("simpy.Environment", side_effect=AssertionError("runtime allocated during admission")):
                document, graph = workload_document()
                change(document)
                with self.assertRaises(ValueError):
                    compile_document(document, graph)

    def test_nonfinite_and_boolean_control_values_are_rejected(self) -> None:
        for value in (True, float("inf"), float("nan"), -1, 0):
            for field in ("reservation_setup_aci_cycles", "reservation_edge_aci_cycles", "atomic_native_cycles"):
                with self.subTest(value=value, field=field):
                    document, _ = workload_document()
                    document["control"][field] = value
                    with self.assertRaises(ValidationError):
                        MulticastSyncWorkload.model_validate(document)
        counter = {"counter_id": "counter", "endpoint_id": "ep", "buffer_id": "buffer",
                   "offset_bytes": 0, "width_bytes": 1, "initial_value": 0}
        for value in (True, 1.0, "1"):
            with self.subTest(width=value), self.assertRaises(ValidationError):
                ScalarCounter.model_validate({**counter, "width_bytes": value})

    def test_changed_dimensions_clocks_widths_and_capacities_remain_configurable(self) -> None:
        document, graph = workload_document(width=4, height=3)
        document["memory"]["aci_clock_hz"] = 300_000_000.0
        document["memory"]["packet"].update(physical_flit_bytes=64, data_capacity_bytes=16,
                                               max_segment_payload_bytes=32, address_alignment_bytes=16)
        document["memory"].update(endpoint_queue_capacity_packets=1, endpoint_staging_capacity_flits=1)
        document["control"].update(reservation_capacity=1, replication_capacity_flits=1)
        for resource in document["memory"]["resources"]:
            resource["service"].update(native_clock_hz=600_000_000.0, service_granule_bytes=16, chunk_bytes=16)
        plan = compile_document(document, graph)
        write = plan.record.writes[0]
        self.assertEqual(len(write.tree.recipients), 11)
        self.assertEqual(len(write.tree.edges), 11)
        self.assertEqual([s.payload_bytes for s in write.segments], [32, 32, 32])
        self.assertEqual(write.packet_physical_bytes, 9 * 64)
        self.assertEqual(plan.workload.memory.aci_clock_hz, 300_000_000.0)

    def test_executor_admission_rejects_forged_plan_inventory_with_original_digest(self) -> None:
        document, graph = workload_document()
        plan = compile_document(document, graph)
        forged = replace(plan, record=plan.record.model_copy(update={"writes": ()}))
        for entry in (TreeTransportPlan.compile, ScalarExecutor.compile):
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "differs from its admitted"):
                entry(forged)

    def test_profile_binding_and_word_contract_are_checked_purely(self) -> None:
        memory = json.loads((ROOT / "configs/memory_replays/wormhole_ordered.json").read_text())
        for field in ("kind", "schema_version", "model_revision", "operations", "runtime"):
            memory.pop(field, None)
        document, _ = workload_document()
        document.update(memory=memory, writes=[], counters=[{
            "counter_id": "counter", "endpoint_id": "tile_1_1_noc0", "buffer_id": "local",
            "offset_bytes": 256, "width_bytes": 4, "initial_value": 0,
        }], increments=[{
            "operation_id": "increment", "source_endpoint_id": "tile_1_1_noc1", "fabric_id": 1,
            "counter_id": "counter", "completion": "atomic_posted",
        }])
        document["writes"] = [{
            "operation_id": "local_tree", "source_endpoint_id": "tile_1_1_noc0", "fabric_id": 0,
            "source": {"buffer_id": "local", "offset_bytes": 0, "size_bytes": 32},
            "target_offset_bytes": 512, "size_bytes": 32,
            "rectangle": {"start": {"x": 1, "y": 1}, "end": {"x": 1, "y": 1},
                          "major_axis": "x", "include_source": True},
            "destinations": [{"endpoint_id": "tile_1_1_noc0", "buffer_id": "local", "offset_bytes": 512}],
            "completion": "write_acknowledged",
        }]
        profile = json.loads((ROOT / "configs/profiles/wormhole_b0_n150_assumed.json").read_text())
        with patch("simpy.Environment", side_effect=AssertionError("runtime allocated during profile binding")):
            plan = MulticastSyncPlan.from_source(MulticastSyncWorkload.model_validate(document), profile)
        self.assertEqual(plan.record.counters[0].width_bytes, 4)
        self.assertEqual(plan.topology.origin.kind, "hardware_profile")
        self.assertEqual(plan.record.writes[0].tree.recipients[0].tile_id, "tile_1_1")
        plan.revalidate()
        wrong_width = copy.deepcopy(document)
        wrong_width["counters"][0]["width_bytes"] = 8
        with self.assertRaisesRegex(ValueError, "32-bit"):
            MulticastSyncPlan.compile(MulticastSyncWorkload.model_validate(wrong_width), plan.topology)
        wrong_packet = copy.deepcopy(document)
        wrong_packet["memory"]["packet"]["physical_flit_bytes"] = 64
        with self.assertRaisesRegex(ValueError, "packet geometry"):
            MulticastSyncPlan.compile(MulticastSyncWorkload.model_validate(wrong_packet), plan.topology)
        graph_value = plan.topology.model_dump(mode="json")
        graph_value["topology_id"] = "forged"
        with self.assertRaisesRegex(ValueError, "differs from its declared profile"):
            MulticastSyncPlan.compile(MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph_value))

    def test_profile_source_cannot_be_substituted_with_an_unrelated_synthetic_graph(self) -> None:
        document, graph = workload_document()
        document["memory"]["source"] = {"kind": "hardware_profile", "profile_path": "profile.json"}
        with self.assertRaisesRegex(ValueError, "original source"):
            compile_document(document, graph)
