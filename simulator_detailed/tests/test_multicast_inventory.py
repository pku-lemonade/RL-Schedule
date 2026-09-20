"""Execution admission checked against explicit routes, extents and phase graphs."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.tests.test_multicast_sync import EVIDENCE, workload_document
from simulator_detailed.tests.test_packet_runtime import transport_config
from simulator_detailed.tests.test_torus import small_replay


def execution_document(**kwargs):
    document, graph = workload_document(**kwargs)
    document["memory"]["routing"] = [small_replay().binding.fabrics[0].model_dump(mode="json")]
    transport = transport_config(staging=4).model_dump(mode="json")
    transport["fabrics"] = transport["fabrics"][:1]
    transport["endpoint_queue_capacity_packets"] = 2
    document["runtime"] = {
        "transport": transport, "responder_capacity_packets": 1,
        "request_control_aci_cycles": 1, "response_control_aci_cycles": 1, "evidence": EVIDENCE,
    }
    return document, graph


def compile_document(document, graph):
    return MulticastSyncPlan.from_source(MulticastSyncWorkload.model_validate(document), graph)


def scalar_document():
    document, graph = execution_document()
    document["control"].update(atomic_granule_bytes=32, inline_control_bytes=8)
    document["memory"]["buffers"].extend([
        {"buffer_id": "counter", "resource_id": "r-t0_0", "base_address": 1024, "size_bytes": 32},
        {"buffer_id": "inbox", "resource_id": "r-t0_0", "base_address": 1056, "size_bytes": 32},
    ])
    document["counters"] = [{"counter_id": "count", "endpoint_id": "ep-t0_0", "buffer_id": "counter",
                             "offset_bytes": 0, "width_bytes": 4, "initial_value": 0}]
    document["increments"] = [{"operation_id": "increment", "source_endpoint_id": "ep-t0_0", "fabric_id": 0,
                               "counter_id": "count", "completion": "atomic_returning", "depends_on": ["broadcast"],
                               "return_inbox": {"buffer_id": "inbox", "offset_bytes": 0, "size_bytes": 4}}]
    document["waits"] = [{"wait_id": "collect", "endpoint_id": "ep-t0_0", "counter_id": "count",
                         "threshold": 1, "producer_operations": ["increment"]}]
    return document, graph


class MixedInventoryTests(unittest.TestCase):
    def test_exact_segment_and_ack_inventory_without_runtime_allocation(self):
        document, graph = execution_document()
        with patch("simpy.Environment", side_effect=AssertionError("pure admission allocated an environment")):
            plan = compile_document(document, graph)
        inventory = plan.record.inventory
        self.assertIsNotNone(inventory)
        # 1 injection, 5 directed edges, 5 worker ejections. Two packets: 96 and 64 bytes.
        self.assertEqual([t.planned_channel_bytes for t in inventory.tree_channels], [1056, 704])
        self.assertEqual(len(inventory.controls), 10)
        self.assertEqual(len(inventory.responders), 10)
        self.assertTrue(all(r.capacity_packets == 1 for r in inventory.responders))
        # Positive torus return paths independently count wrap distances on a 3x2 grid.
        hops = {"ep-t0_0": 2, "ep-t1_0": 4, "ep-t0_1": 3, "ep-t1_1": 5, "ep-t2_1": 4}
        self.assertEqual(sum(p.planned_channel_bytes for p in inventory.controls), 2 * sum(hops.values()) * 32)
        self.assertEqual(sum(a.size_bytes for a in inventory.accesses if a.direction == "read"), 96)
        self.assertEqual(sum(a.size_bytes for a in inventory.accesses if a.direction == "write"), 480)
        plan.revalidate()

    def test_missing_return_path_and_unbounded_or_mismatched_settings_rejected(self):
        for mutate in (
            lambda d, g: g["attachments"][1].update(inject_port=None),
            lambda d, g: d["runtime"].update(responder_capacity_packets=0),
            lambda d, g: d["runtime"]["transport"].update(endpoint_staging_capacity_flits=2),
            lambda d, g: d["runtime"]["transport"]["fabrics"][0]["local_link"].update(aci_clock_hz=250_000_000),
            lambda d, g: d["memory"].update(routing=[]),
        ):
            document, graph = execution_document()
            mutate(document, graph)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                compile_document(document, graph)

    def test_hardware_changes_have_effective_identity_and_path_is_only_locator(self):
        document, graph = execution_document(width=4, height=3)
        original = compile_document(document, graph)
        moved = copy.deepcopy(document)
        moved["memory"]["source"]["graph_path"] = "/elsewhere/graph.json"
        self.assertEqual(compile_document(moved, graph).record, original.record)
        changed = copy.deepcopy(document)
        changed["memory"]["aci_clock_hz"] = 250_000_000
        for name in ("network_link", "local_link"):
            changed["runtime"]["transport"]["fabrics"][0][name].update(aci_clock_hz=250_000_000)
        self.assertNotEqual(compile_document(changed, graph).record.plan_sha256, original.record.plan_sha256)
        forged = original.record.inventory.model_copy(update={"controls": ()})
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "differs"):
            replace(original, record=original.record.model_copy(update={"inventory": forged})).revalidate()

    def test_conflicting_operations_require_order_and_remote_diagnostics_are_not_gates(self):
        document, graph = execution_document()
        document["operations"] = [{"operation_id": "reuse", "initiator_id": "ep-t0_0", "kind": "local_write",
                                    "destination": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 32}}]
        with self.assertRaisesRegex(ValueError, "conflicting"):
            compile_document(document, graph)
        document["operations"][0]["depends_on"] = ["broadcast"]
        compile_document(document, graph)
        document["operations"][0].update(initiator_id="ep-t1_0", destination={"buffer_id": "b-ep-t1_0", "offset_bytes": 128, "size_bytes": 32})
        document["memory"]["endpoints"][1]["roles"].append("initiator")
        with self.assertRaisesRegex(ValueError, "remote diagnostic"):
            compile_document(document, graph)
        document["operations"][0]["depends_on"] = []
        document["operations"][0]["destination_ready_after"] = ["broadcast"]
        compile_document(document, graph)

    def test_ordinary_unicast_and_local_payload_share_one_inventory(self):
        document, graph = execution_document()
        document["operations"] = [{"operation_id": "unicast", "initiator_id": "ep-t0_0", "fabric_id": 0,
                                    "kind": "write_acknowledged",
                                    "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 32},
                                    "destination": {"buffer_id": "b-ep-t1_0", "offset_bytes": 256, "size_bytes": 32}}]
        inventory = compile_document(document, graph).record.inventory
        self.assertEqual(len(inventory.ordinary_routes), 1)
        self.assertEqual(inventory.ordinary_routes[0].response.destination, "ep-t0_0")
        self.assertEqual(len(inventory.responders), 11)

    def test_atomic_inline_packets_granules_and_return_inbox(self):
        document, graph = scalar_document()
        inventory = compile_document(document, graph).record.inventory
        controls = [p for p in inventory.controls if p.operation_id == "increment"]
        self.assertEqual([p.purpose for p in controls], ["atomic_request", "atomic_return"])
        self.assertEqual([(p.physical_bytes, p.inline_bytes) for p in controls], [(32, 4), (32, 4)])
        self.assertEqual([(a.direction, a.size_bytes) for a in inventory.accesses if a.operation_id == "increment"],
                         [("atomic", 32), ("write", 4)])
        self.assertEqual(inventory.operation_order, ("broadcast", "increment", "collect"))
        for mutate in (
            lambda d: d["increments"][0].pop("return_inbox"),
            lambda d: d["increments"][0]["return_inbox"].update(buffer_id="counter"),
            lambda d: d["control"].update(inline_control_bytes=2),
            lambda d: d["counters"][0].update(offset_bytes=30),
            lambda d: d["control"].update(atomic_granule_bytes=None),
        ):
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                compile_document(changed, graph)

    def test_wait_proofs_remote_peeks_cycles_and_phase_overtake(self):
        document, graph = scalar_document()
        for mutate in (
            lambda d: d["waits"][0].update(threshold=2),
            lambda d: d["waits"][0].update(endpoint_id="ep-t1_0"),
            lambda d: d["waits"][0].update(producer_operations=["broadcast"]),
            lambda d: d.update(gates=[{"operation_id": "increment", "after_waits": ["collect"]}]),
        ):
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                compile_document(changed, graph)
        second = copy.deepcopy(document["increments"][0])
        second.update(operation_id="next", completion="atomic_posted")
        second.pop("return_inbox")
        document["increments"].append(second)
        with self.assertRaisesRegex(ValueError, "overtake"):
            compile_document(document, graph)
        document["gates"] = [{"operation_id": "next", "after_waits": ["collect"]}]
        compile_document(document, graph)

    def test_local_data_requires_full_extent_and_exact_producer_version(self):
        document, graph = scalar_document()
        prerequisite = {"access": {"buffer_id": "b-ep-t0_0", "offset_bytes": 128, "size_bytes": 96},
                        "version": {"kind": "producer", "producer_id": "broadcast"}}
        document["waits"][0]["local_data"] = [prerequisite]
        compile_document(document, graph)
        for mutate in (
            lambda p: p["access"].update(size_bytes=97),
            lambda p: p["access"].update(buffer_id="b-ep-t1_0"),
            lambda p: p["version"].update(producer_id="increment"),
        ):
            changed = copy.deepcopy(document)
            mutate(changed["waits"][0]["local_data"][0])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                compile_document(changed, graph)


if __name__ == "__main__":
    unittest.main()
