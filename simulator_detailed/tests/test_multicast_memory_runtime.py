"""Addressed multicast lifetime and canonical memory conservation checks."""

from __future__ import annotations

import unittest
from collections import Counter
from itertools import pairwise

from simulator_detailed.configs.schemas.memory_replay import MemoryVersion
from simulator_detailed.memory_resources import MemoryAccess
from simulator_detailed.multicast_memory_runtime import (
    MulticastExecutionResult,
    MulticastMemoryRuntime,
)
from simulator_detailed.tests.test_multicast_inventory import (
    compile_document,
    execution_document,
)


def make_runtime(*, completion="write_acknowledged", size=96, slow=0, **kwargs):
    document, graph = execution_document(**kwargs)
    document["writes"][0].update(completion=completion, size_bytes=size)
    document["writes"][0]["source"]["size_bytes"] = size
    if slow:
        document["memory"]["resources"][-1]["service"]["fixed_latency_cycles"] = slow
    return MulticastMemoryRuntime(compile_document(document, graph))


def time_of(result, operation, action, *, segment=None):
    return next(e.time_aci_cycles for e in result.lifecycle if e.operation_id == operation and e.action == action and e.segment_index == segment)


class MulticastMemoryRuntimeTests(unittest.TestCase):
    def assert_conserved(self, result, complete=True):
        self.assertEqual(result.status, "complete" if complete else "incomplete")
        self.assertEqual(result.teardown_complete, complete)
        self.assertEqual(result.source_useful_bytes, sum(c.useful_bytes for c in result.chunks if c.direction == "read"))
        self.assertEqual(result.destination_useful_bytes, sum(c.useful_bytes for c in result.chunks if c.direction == "write"))
        for resource in result.memory_resources:
            self.assertEqual(resource.available_bytes + resource.reserved_bytes, resource.capacity_bytes)
            self.assertLessEqual(resource.service.queue_peak, resource.service.queue_capacity)
        for descriptor in result.descriptors:
            self.assertLessEqual(descriptor.peak_occupied, descriptor.capacity)
            if complete:
                self.assertEqual(descriptor.occupied, 0)
        # The canonical server never runs simultaneous read/write chunks.
        for resource in result.memory_resources:
            chunks = sorted((c for c in result.chunks if c.resource_id == resource.resource_id), key=lambda c: c.start_aci_cycles)
            for first, second in pairwise(chunks):
                self.assertLessEqual(first.end_aci_cycles, second.start_aci_cycles)
        active = {}
        for event in result.descriptor_trace:
            key = event.kind, event.resource_id
            owners = active.setdefault(key, set())
            if event.action == "acquire":
                self.assertNotIn(event.owner, owners)
                owners.add(event.owner)
            else:
                self.assertIn(event.owner, owners)
                owners.remove(event.owner)
            self.assertEqual(len(owners), event.occupied)
        if complete:
            self.assertTrue(all(not v for v in active.values()))
            self.assertTrue(all(r.reserved_bytes == 0 for r in result.released_resources))
            self.assertTrue(all(not r.service.active and not r.service.queued for r in result.released_resources))
        self.assertEqual(result, MulticastExecutionResult.model_validate_json(result.model_dump_json()))

    def test_segment_boundaries_charge_source_once_and_each_destination_service(self):
        for size in (1, 31, 32, 33, 63, 64, 65, 96):
            for completion in ("write_posted", "write_acknowledged"):
                with self.subTest(size=size, completion=completion):
                    runtime = make_runtime(size=size, completion=completion)
                    result = runtime.run()
                    self.assert_conserved(result)
                    count = (size + 63) // 64
                    flits = count + (size + 31) // 32
                    self.assertEqual((result.source_useful_bytes, result.destination_useful_bytes), (size, 5 * size))
                    self.assertEqual(result.physical_channel_bytes, flits * 32 * 11 + (count * 576 if completion == "write_acknowledged" else 0))
                    self.assertEqual(sum(c.serviced_bytes for c in result.chunks), 6 * ((size + 31) // 32) * 32)
                    self.assertEqual(len([e for e in result.lifecycle if e.action == "acknowledgement"]), count * 5 if completion == "write_acknowledged" else 0)
                    self.assertIs(runtime.run(), result)
                    self.assertIs(runtime.finalize(), result)

    def test_posted_handoff_source_reuse_and_slow_target_tail(self):
        document, graph = execution_document()
        document["writes"][0]["completion"] = "write_posted"
        document["memory"]["resources"][-1]["service"]["fixed_latency_cycles"] = 100
        document["operations"] = [{"operation_id": "reuse", "initiator_id": "ep-t0_0", "kind": "local_write", "depends_on": ["broadcast"],
                                    "destination": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 96}}]
        plan = compile_document(document, graph)
        complete = MulticastMemoryRuntime(plan).run()
        self.assert_conserved(complete)
        handoff = time_of(complete, "broadcast", "handoff")
        self.assertLessEqual(time_of(complete, "broadcast", "source_read_complete"), handoff)
        self.assertLess(time_of(complete, "reuse", "complete"), time_of(complete, "broadcast", "all_effects"))
        runtime = MulticastMemoryRuntime(plan)
        early = runtime.run(max_aci_cycles=handoff)
        self.assert_conserved(early, complete=False)
        self.assertIn("broadcast", early.pending_operations)
        self.assertTrue(any(r.reserved_bytes for r in early.memory_resources))
        with self.assertRaisesRegex(ValueError, "drained"):
            runtime.finalize()
        self.assertEqual(runtime.run(), complete)
        released = Counter((e.resource_id, e.buffer_id) for e in runtime.snapshot().ownership_trace if e.action == "release")
        self.assertTrue(all(count == 1 for count in released.values()))

    def test_ack_launches_after_own_service_and_final_completion_waits_for_last_return(self):
        runtime = make_runtime(slow=50)
        result = runtime.run()
        self.assert_conserved(result)
        controls = {c.packet_id: c for c in result.plan.inventory.controls}
        for launch in result.tree_transport.trace:
            if launch.action != "link_launch" or launch.lane.channel.kind != "inject" or launch.packet.traffic_class != "response":
                continue
            control = controls[launch.packet.transfer_id]
            resource = next(e.resource_ids[0] for e in result.configuration.memory.endpoints if e.endpoint_id == control.route.source)
            effect = next(e for e in result.lifecycle if e.action == "recipient_effect" and e.resource_id == resource
                          and e.segment_index == control.segment_index)
            self.assertGreaterEqual(launch.time_aci_cycles, effect.time_aci_cycles)
        self.assertGreaterEqual(time_of(result, "broadcast", "complete"), max(e.time_aci_cycles for e in result.lifecycle if e.action == "acknowledgement"))
        self.assertLess(time_of(result, "broadcast", "handoff"), time_of(result, "broadcast", "complete"))

    def test_bundle_failure_preserves_readiness_leases_and_descriptors(self):
        runtime = make_runtime(completion="write_posted")
        handle = runtime.memory.handles["b-ep-t0_0"]
        source = runtime.memory.resources[handle.buffer.resource_id]
        lease = source.try_acquire(handle, MemoryAccess(client_id="external", direction="read", offset_bytes=128, size_bytes=32,
                                                        version=MemoryVersion(kind="initial")))
        self.assertIsNotNone(lease)
        before = source.snapshot()
        target = runtime.memory.handles["b-ep-t1_0"]
        requests = ((target.buffer.buffer_id, MemoryAccess(client_id="bundle", direction="write", offset_bytes=128, size_bytes=32,
                        version=MemoryVersion(kind="producer", producer_id="new"))),
                    (handle.buffer.buffer_id, MemoryAccess(client_id="bundle", direction="write", offset_bytes=128, size_bytes=32,
                        version=MemoryVersion(kind="producer", producer_id="new"))))
        target_before = runtime.memory.resources[target.buffer.resource_id].snapshot()
        self.assertIsNone(runtime.memory.try_acquire_bundle(requests))
        self.assertEqual(source.snapshot(), before)
        self.assertEqual(runtime.memory.resources[target.buffer.resource_id].snapshot(), target_before)
        source.release_access(lease)
        self.assert_conserved(runtime.run())

    def test_shared_unicast_read_write_local_service_and_full_extent_versions(self):
        document, graph = execution_document()
        document["operations"] = [
            {"operation_id": "ordinary", "initiator_id": "ep-t0_0", "fabric_id": 0, "kind": "write_acknowledged",
             "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 65},
             "destination": {"buffer_id": "b-ep-t1_0", "offset_bytes": 256, "size_bytes": 65}},
            {"operation_id": "readback", "initiator_id": "ep-t0_0", "fabric_id": 0, "kind": "read", "depends_on": ["ordinary"],
             "source": {"buffer_id": "b-ep-t1_0", "offset_bytes": 256, "size_bytes": 65},
             "source_version": {"kind": "producer", "producer_id": "ordinary"},
             "destination": {"buffer_id": "b-ep-t0_0", "offset_bytes": 256, "size_bytes": 65}},
            {"operation_id": "local", "initiator_id": "ep-t0_0", "kind": "local_read", "depends_on": ["broadcast"],
             "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 128, "size_bytes": 96},
             "source_version": {"kind": "producer", "producer_id": "broadcast"}},
        ]
        plan = compile_document(document, graph)
        uninterrupted = MulticastMemoryRuntime(plan).run()
        self.assert_conserved(uninterrupted)
        self.assertEqual((uninterrupted.source_useful_bytes, uninterrupted.destination_useful_bytes), (96 + 130 + 96, 480 + 130))
        runtime = MulticastMemoryRuntime(plan)
        partial = runtime.run(max_aci_cycles=15)
        self.assert_conserved(partial, complete=False)
        target = runtime.memory.handles["b-ep-t1_0"]
        self.assertFalse(runtime.memory.resources[target.buffer.resource_id].is_ready(target, offset_bytes=128, size_bytes=96,
                                                                                     version=MemoryVersion(kind="producer", producer_id='["broadcast","r-t1_0"]')))
        self.assertEqual(runtime.run(), uninterrupted)

    def test_aliases_on_both_fabrics_share_canonical_l1_service(self):
        import copy

        from simulator_detailed.tests.test_multicast_tree_admission import (
            dual_fabric_document,
        )
        from simulator_detailed.tests.test_packet_runtime import transport_config
        document, graph = dual_fabric_document()
        settings, _ = execution_document()
        document["runtime"] = settings["runtime"]
        document["runtime"]["transport"] = transport_config(staging=4).model_dump(mode="json")
        document["runtime"]["transport"]["endpoint_queue_capacity_packets"] = 2
        routing = settings["memory"]["routing"][0]
        document["memory"]["routing"] = [routing, {**copy.deepcopy(routing), "fabric_id": 1}]
        second = copy.deepcopy(document["writes"][0])
        second.update(operation_id="opposite", fabric_id=1, source_endpoint_id="ep-t2_1-f1", target_offset_bytes=256)
        second["source"]["buffer_id"] = "b-ep-t2_1"
        for destination in second["destinations"]:
            destination["endpoint_id"] += "-f1"
            destination["offset_bytes"] = 256
        document["writes"].append(second)
        next(b for b in document["memory"]["buffers"] if b["buffer_id"] == "b-ep-t2_1")["initially_ready"] = True
        runtime = MulticastMemoryRuntime(compile_document(document, graph))
        self.assertIs(runtime.memory.via("ep-t1_0", "r-t1_0"), runtime.memory.via("ep-t1_0-f1", "r-t1_0"))
        result = runtime.run()
        self.assert_conserved(result)
        self.assertEqual(len(result.memory_resources), 5)
        self.assertEqual((result.source_useful_bytes, result.destination_useful_bytes), (192, 960))
        self.assertEqual({e.fabric_id for e in result.tree_transport.trace if e.action == "link_launch"}, {0, 1})

    def test_configured_headers_and_delayed_credit_drain_are_charged(self):
        document, graph = execution_document()
        document["memory"]["packet"]["header_flits"] = 3
        for name in ("network_link", "local_link"):
            document["runtime"]["transport"]["fabrics"][0][name]["credit_return_noc_cycles"] = 20
        runtime = MulticastMemoryRuntime(compile_document(document, graph))
        result = runtime.run()
        self.assert_conserved(result)
        # Two source segments carry 3 headers each, plus 3 payload flits;
        # 10 return packets each carry 3 headers on independently counted paths.
        self.assertEqual(result.physical_channel_bytes, 9 * 32 * 11 + 3 * 1152)
        self.assertEqual(len([e for e in result.lifecycle if e.action == "acknowledgement"]), 10)
        complete = time_of(result, "broadcast", "complete")
        self.assertLess(complete, result.elapsed_aci_cycles)
        second = MulticastMemoryRuntime(compile_document(document, graph))
        early = second.run(max_aci_cycles=complete)
        self.assert_conserved(early, complete=False)
        self.assertTrue(any(r.pending_returns for r in early.tree_transport.resources))
        self.assertEqual(second.run(), result)

    def test_responder_backpressure_is_reserved_before_tree_injection(self):
        runtime = make_runtime(slow=40)
        partial = runtime.run(max_aci_cycles=15)
        self.assert_conserved(partial, complete=False)
        self.assertTrue(any(d.kind == "responder" and d.occupied for d in partial.descriptors))
        # One descriptor at each recipient admits only one multicast segment.
        accepted = {e.segment_index for e in partial.lifecycle if e.action == "acceptance"}
        self.assertEqual(accepted, {0})
        self.assertEqual(len(runtime.tree.submitted), 1)
        self.assert_conserved(runtime.run())


if __name__ == "__main__":
    unittest.main()
