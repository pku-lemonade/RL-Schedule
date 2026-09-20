"""Counter linearization, shared service, real control traffic and local waits."""

from __future__ import annotations

import copy
import unittest

import simpy

from simulator_detailed.memory_service import MemoryService, ServiceChunk, ServiceTiming
from simulator_detailed.multicast_memory_runtime import MulticastMemoryRuntime
from simulator_detailed.scalar_service import (
    AtomicChunk,
    CounterDefinition,
    CounterHandle,
)
from simulator_detailed.tests.test_multicast_inventory import (
    compile_document,
    scalar_document,
)
from simulator_detailed.tests.test_multicast_memory_runtime import time_of


def scalar_only(*, completion="atomic_returning", width=4):
    document, graph = scalar_document()
    document["writes"] = []
    document["increments"][0].update(depends_on=[], completion=completion)
    document["counters"][0]["width_bytes"] = width
    if completion == "atomic_returning":
        document["increments"][0]["return_inbox"]["size_bytes"] = width
    else:
        document["increments"][0].pop("return_inbox")
    return document, graph


class SharedScalarTests(unittest.TestCase):
    def test_atomic_job_is_one_final_service_on_the_read_write_fifo(self):
        document, graph = scalar_only()
        plan = compile_document(document, graph)
        env = simpy.Environment()
        service = MemoryService(env, "r-t0_0", ServiceTiming(config=plan.workload.memory.resources[0].service,
                                                            aci_clock_hz=plan.workload.memory.aci_clock_hz))
        counter = service.register_counter(CounterDefinition(counter_id="c", address=1024, width_bytes=4, granule_bytes=32, initial_value=7))
        service.try_submit(ServiceChunk(service_id="read", client_id="payload", direction="read", address=0, useful_bytes=32))
        service.try_scalar(counter, AtomicChunk(service_id="inc", client_id="producer", counter_id="c", native_cycles=6))
        service.try_submit(ServiceChunk(service_id="write", client_id="payload", direction="write", address=128, useful_bytes=32))
        env.run()
        self.assertEqual([(r.start_aci_cycles, r.end_aci_cycles) for r in service.records], [(0, 1), (4, 5)])
        atomic = service.scalar_records[0]
        self.assertEqual((atomic.start_aci_cycles, atomic.end_aci_cycles, atomic.old_value, atomic.new_value), (1, 4, 7, 8))
        self.assertEqual((atomic.read_service_bytes, atomic.write_service_bytes, atomic.native_cycles, atomic.admission_sequence), (32, 32, 6, 1))
        self.assertEqual((service.snapshot().completed_read_bytes, service.snapshot().completed_write_bytes), (64, 64))
        self.assertEqual(service.counter_state(counter).value, 8)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            service.try_scalar(counter, AtomicChunk(service_id="inc", client_id="producer", counter_id="c", native_cycles=6))
        with self.assertRaisesRegex(ValueError, "foreign"):
            service.counter_state(CounterHandle(counter.definition))

    def test_generic_word_widths_and_returned_integer_values_are_exact(self):
        for width in (1, 2, 4, 8):
            with self.subTest(width=width):
                document, graph = scalar_only(width=width)
                initial = (1 << (8 * width)) - 2
                document["counters"][0]["initial_value"] = initial
                document["waits"][0]["threshold"] = initial + 1
                runtime = MulticastMemoryRuntime(compile_document(document, graph))
                result = runtime.run()
                self.assertEqual(result.status, "complete")
                self.assertEqual((result.counters[0].value, result.counters[0].updates), (initial + 1, 1))
                self.assertEqual(result.inbox_values, (("increment", initial),))
                self.assertEqual(result.source_useful_bytes, 0)
                self.assertEqual(result.destination_useful_bytes, width)
                self.assertEqual(result.physical_channel_bytes, 4 * 32)
                atomic = next(r for r in result.scalar_service if r.direction == "atomic")
                self.assertEqual(atomic.service_aci_cycles, 2.5)
                self.assertEqual(len([c for c in result.chunks if c.direction == "write"]), 1)
                self.assertLessEqual(atomic.end_aci_cycles, result.chunks[0].start_aci_cycles)
                self.assertIs(runtime.run(), result)

    def test_posted_handoff_precedes_update_and_resume_does_not_repeat_it(self):
        document, graph = scalar_only(completion="atomic_posted")
        document["control"]["atomic_native_cycles"] = 100
        plan = compile_document(document, graph)
        complete = MulticastMemoryRuntime(plan).run()
        handoff = time_of(complete, "increment", "handoff")
        update = next(r for r in complete.scalar_service if r.direction == "atomic")
        self.assertLess(handoff, update.end_aci_cycles)
        runtime = MulticastMemoryRuntime(plan)
        early = runtime.run(max_aci_cycles=handoff)
        self.assertEqual(early.status, "incomplete")
        self.assertEqual(early.counters[0].value, 0)
        self.assertEqual(time_of(early, "increment", "complete"), handoff)
        self.assertEqual(complete.physical_channel_bytes, 64)
        self.assertEqual(complete.destination_useful_bytes, 0)
        self.assertEqual(runtime.run(), complete)
        self.assertEqual(len([r for r in runtime.snapshot().scalar_service if r.direction == "atomic"]), 1)

    def test_observation_update_race_and_data_not_ready_do_not_release_early(self):
        document, graph = scalar_document()
        document["increments"][0]["depends_on"] = []
        document["waits"][0]["local_data"] = [{"access": {"buffer_id": "b-ep-t0_0", "offset_bytes": 128, "size_bytes": 96},
                                               "version": {"kind": "producer", "producer_id": "broadcast"}}]
        document["control"]["local_observation_aci_cycles"] = 12
        runtime = MulticastMemoryRuntime(compile_document(document, graph))
        result = runtime.run()
        self.assertEqual(result.status, "complete")
        observations = [r for r in result.scalar_service if r.direction == "observe"]
        self.assertEqual([r.new_value for r in observations], [0, 1])
        release = time_of(result, "collect", "wait_release")
        local_effect = max(e.time_aci_cycles for e in result.lifecycle if e.action == "recipient_effect" and e.operation_id == "broadcast" and e.resource_id == "r-t0_0")
        self.assertGreaterEqual(release, local_effect)
        self.assertGreaterEqual(release, observations[-1].end_aci_cycles)
        self.assertTrue(all(r.service_aci_cycles == 13 for r in observations))

    def test_independent_l1_atomic_service_can_overlap(self):
        document, graph = scalar_only(completion="atomic_posted")
        document["waits"] = []
        document["control"]["atomic_native_cycles"] = 100
        document["memory"]["buffers"].append({"buffer_id": "counter-other", "resource_id": "r-t1_0", "base_address": 1024, "size_bytes": 32})
        document["counters"].append({"counter_id": "other", "endpoint_id": "ep-t1_0", "buffer_id": "counter-other", "offset_bytes": 0, "width_bytes": 4, "initial_value": 0})
        document["increments"].append({"operation_id": "remote", "source_endpoint_id": "ep-t0_0", "fabric_id": 0, "counter_id": "other", "completion": "atomic_posted"})
        result = MulticastMemoryRuntime(compile_document(document, graph)).run()
        self.assertEqual(result.status, "complete")
        first, second = sorted(result.scalar_service, key=lambda r: r.start_aci_cycles)
        self.assertNotEqual(first.resource_id, second.resource_id)
        self.assertLess(second.start_aci_cycles, first.end_aci_cycles)
        self.assertEqual([c.value for c in result.counters], [1, 1])

    def test_return_reordering_preserves_each_linearized_previous_value(self):
        document, graph = scalar_only()
        document["waits"] = []
        document["counters"][0]["endpoint_id"] = "ep-t1_0"
        next(b for b in document["memory"]["buffers"] if b["buffer_id"] == "counter")["resource_id"] = "r-t1_0"
        document["memory"]["buffers"].append({"buffer_id": "inbox-other", "resource_id": "r-t2_1", "base_address": 1024, "size_bytes": 32})
        next(e for e in document["memory"]["endpoints"] if e["endpoint_id"] == "ep-t2_1")["roles"] = ["initiator", "target", "response_sink"]
        second = copy.deepcopy(document["increments"][0])
        second.update(operation_id="second", source_endpoint_id="ep-t2_1", return_inbox={"buffer_id": "inbox-other", "offset_bytes": 0, "size_bytes": 4})
        document["increments"].append(second)
        delayed = next(e for e in graph["links"] if e["src_router"] == "t2_0" and e["dst_router"] == "t0_0")
        settings = copy.deepcopy(document["runtime"]["transport"]["fabrics"][0]["network_link"])
        settings["slowdowns"] = [["return-delay", 0, 100, 30]]
        document["runtime"]["transport"]["overrides"] = [{"channel": {"fabric_id": 0, "kind": "network", "identity": delayed["link_id"]}, "settings": settings}]
        result = MulticastMemoryRuntime(compile_document(document, graph)).run()
        self.assertEqual(result.status, "complete")
        updates = [r for r in result.scalar_service if r.direction == "atomic"]
        self.assertEqual([(r.client_id, r.old_value, r.new_value) for r in updates], [("increment", 0, 1), ("second", 1, 2)])
        self.assertEqual(result.inbox_values, (("second", 1), ("increment", 0)))
        self.assertLess(time_of(result, "second", "complete"), time_of(result, "increment", "complete"))

    def test_opposite_fabric_updates_and_local_clients_share_the_same_fifo(self):
        from simulator_detailed.tests.test_multicast_tree_admission import (
            dual_fabric_document,
        )
        from simulator_detailed.tests.test_packet_runtime import transport_config
        document, _ = scalar_only(completion="atomic_posted")
        dual, graph = dual_fabric_document()
        document["memory"]["fabrics"] = [0, 1]
        document["memory"]["endpoints"] = dual["memory"]["endpoints"]
        document["memory"]["routing"].append({**copy.deepcopy(document["memory"]["routing"][0]), "fabric_id": 1})
        document["runtime"]["transport"] = transport_config(staging=4).model_dump(mode="json")
        document["runtime"]["transport"]["endpoint_queue_capacity_packets"] = 2
        document["waits"] = []
        document["control"]["atomic_native_cycles"] = 200
        document["increments"].append({"operation_id": "opposite", "source_endpoint_id": "ep-t2_1-f1", "fabric_id": 1,
                                       "counter_id": "count", "completion": "atomic_posted"})
        document["operations"] = [{"operation_id": "local", "initiator_id": "ep-t0_0", "kind": "local_read",
                                    "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 32}}]
        result = MulticastMemoryRuntime(compile_document(document, graph)).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual((result.counters[0].value, result.counters[0].updates), (2, 2))
        updates = result.scalar_service
        self.assertEqual([r.resource_id for r in updates], ["r-t0_0", "r-t0_0"])
        self.assertEqual([r.linearization_sequence for r in updates], [1, 2])
        self.assertLess(updates[0].admission_sequence, updates[1].admission_sequence)
        self.assertLessEqual(updates[0].end_aci_cycles, updates[1].start_aci_cycles)
        self.assertLessEqual(result.chunks[0].end_aci_cycles, updates[0].start_aci_cycles)
        self.assertEqual({e.fabric_id for e in result.tree_transport.trace if e.action == "link_launch"}, {0, 1})

    def test_observation_duration_overflow_rejected_before_environment_allocation(self):
        from unittest.mock import patch
        document, graph = scalar_only()
        document["control"]["local_observation_aci_cycles"] = 1e308
        with patch("simpy.Environment", side_effect=AssertionError("allocated before admission")), self.assertRaisesRegex(ValueError, "observation duration"):
            MulticastMemoryRuntime(compile_document(document, graph))

    def test_second_phase_cannot_start_before_local_collector_release(self):
        document, graph = scalar_only(completion="atomic_posted")
        second = copy.deepcopy(document["increments"][0])
        second["operation_id"] = "second"
        document["increments"].append(second)
        document["gates"] = [{"operation_id": "second", "after_waits": ["collect"]}]
        document["waits"].append({"wait_id": "collect2", "endpoint_id": "ep-t0_0", "counter_id": "count", "threshold": 2,
                                 "producer_operations": ["increment", "second"]})
        result = MulticastMemoryRuntime(compile_document(document, graph)).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.counters[0].value, 2)
        self.assertGreaterEqual(time_of(result, "second", "acceptance", segment=0), time_of(result, "collect", "wait_release"))
        self.assertGreater(time_of(result, "collect2", "wait_release"), time_of(result, "collect", "wait_release"))


if __name__ == "__main__":
    unittest.main()
