"""Explicit effect ordering, producer epochs and bounded local-client oracles."""

from __future__ import annotations

import copy
import unittest
from collections import Counter
from unittest.mock import patch

from simulator_detailed.memory_execution import MemoryExecutionResult
from simulator_detailed.memory_records import MemoryOperationRecord
from simulator_detailed.memory_runtime import MemoryRuntime
from simulator_detailed.tests import test_memory_runtime as runtime_tests

documents = runtime_tests.documents
execution_plan = runtime_tests.execution_plan


def extent(buffer="local", size=33, offset=0):
    return {"buffer_id": buffer, "size_bytes": size, "offset_bytes": offset}


def local(operation_id, *, write=False, buffer="local", size=33, offset=0, initiator="src0", **fields):
    return {"operation_id": operation_id, "kind": "local_write" if write else "local_read", "initiator_id": initiator,
            "destination" if write else "source": extent(buffer, size, offset), **fields}


def producer(operation_id):
    return {"kind": "producer", "producer_id": operation_id}


def fence(*operations, mode="remote_completion", **fields):
    return {"operation_id": "barrier", "kind": "fence", "initiator_id": "src0", "fence_mode": mode,
            "fence_operations": list(operations), **fields}


def add_worker(doc, graph):
    graph["resources"].append({"resource_id": "l1-b", "kind": "local_sram", "owner_tile_id": "t1_0", "capacity_bytes": 65536})
    doc["resources"].append({**copy.deepcopy(doc["resources"][1]), "resource_id": "l1-b"})
    doc["buffers"].append({**copy.deepcopy(doc["buffers"][0]), "buffer_id": "local-b", "resource_id": "l1-b"})
    for fabric in (0, 1):
        for items, key in ((graph["attachments"], "attachments"), (doc["endpoints"], "endpoints")):
            original = (graph if key == "attachments" else doc)[key][2 * fabric]
            items.append({**copy.deepcopy(original), "endpoint_id": f"peer{fabric}", "router_id": "t1_0", "resource_ids": ["l1-b"]})


class MemoryOrderingTests(unittest.TestCase):
    assert_conserved = runtime_tests.MemoryRuntimeTests.assert_conserved

    def run_case(self, doc, graph, **settings):
        result = MemoryRuntime(execution_plan(doc, graph, **settings)).run(max_aci_cycles=100_000)
        self.assert_conserved(result)
        return result

    def assert_rejected(self, doc, graph):
        with patch("simpy.Environment", side_effect=AssertionError("allocated before admission")), self.assertRaises(ValueError):
            execution_plan(doc, graph)

    def test_local_only_literal_timeline_and_no_wire_inventory(self):
        # 33 useful bytes split into 32+1. Each rounded chunk takes
        # (1 native latency + 32/32 service) * 500MHz/1GHz = 1 ACI.
        for write in (False, True):
            doc, graph = documents()
            doc["operations"] = [local("local", write=write, start_aci_cycles=3)]
            result = self.run_case(doc, graph, local_control=2)
            op = result.operations[0]
            self.assertEqual((op.submission_aci_cycles, op.descriptor_acceptance_aci_cycles, op.completion_aci_cycles), (3, 3, 7))
            self.assertEqual((result.logical_bytes, result.memory_service_bytes, result.packet_bytes, result.channel_bytes), (33, 64, 0, 0))
            self.assertEqual([(c.start_aci_cycles, c.end_aci_cycles, c.useful_bytes) for c in result.chunks], [(5, 6, 32), (6, 7, 1)])
            self.assertFalse(result.transport.packets or result.transport.trace or result.segments)
            self.assertTrue(all(event.segment_index is None for event in result.lifecycle))
            self.assertTrue(all(event.segment_index is None for event in result.descriptor_trace))
            self.assertIsNone(op.final_request_handoff_aci_cycles)
            self.assertIsNone(op.response_receipt_aci_cycles)
            self.assertIsNone(op.source_read_completion_aci_cycles if write else op.destination_ready_aci_cycles)
            self.assertEqual(result, MemoryExecutionResult.model_validate_json(result.model_dump_json()))

    def test_local_producer_partial_readiness_and_alias_dependency(self):
        doc, graph = documents()
        doc["buffers"][0].update(initially_ready=False, producer_operation_id="produce")
        doc["operations"] = [local("produce", write=True, size=65),
                             local("consume", size=33, offset=32, initiator="src1", depends_on=["produce"])]
        result = self.run_case(doc, graph)
        produce, consume = result.operations
        self.assertEqual((produce.completion_aci_cycles, consume.descriptor_acceptance_aci_cycles, consume.completion_aci_cycles), (3, 3, 5))
        resource = next(r for r in result.memory_resources if r.resource_id == "l1-a")
        self.assertEqual([(r.address, r.size_bytes, r.version.producer_id) for r in resource.buffers[0].ready_ranges], [(0, 65, "produce")])
        self.assertFalse(next(b for b in result.buffers if b.buffer_id == "local").ready)
        self.assertEqual(result.ordering[1].source_version.producer_id, "produce")
        self.assertEqual(result.memory_service_bytes, 160)
        doc["operations"][1]["source"]["size_bytes"] = 34
        self.assert_rejected(doc, graph)

    def test_local_native_clock_and_bounded_slots_share_one_server(self):
        for capacity, acceptance in ((1, (0, 7)), (2, (0, 0))):
            doc, graph = documents()
            doc["packet"].update(address_alignment_bytes=4)
            for resource in doc["resources"]:
                resource["service"].update(service_granule_bytes=4, chunk_bytes=4, bytes_per_cycle=4,
                                           native_clock_hz=250_000_000.0, fixed_latency_cycles=0.5)
            doc["operations"] = [local("first", size=5), local("second", write=True, offset=32, size=5, initiator="src1")]
            result = self.run_case(doc, graph, local_capacity=capacity, local_control=1)
            self.assertEqual(tuple(o.descriptor_acceptance_aci_cycles for o in result.operations), acceptance)
            self.assertEqual(result.memory_service_bytes, 16)
            self.assertEqual(len(result.chunks), 4)
            self.assertTrue(all(c.native_cycles == 1.5 and c.service_aci_cycles == 3 for c in result.chunks))
            self.assertEqual(result.descriptors[0].peak_occupied, capacity)
            self.assertEqual(result.elapsed_aci_cycles, 14 if capacity == 1 else 13)

    def test_independent_local_resources_overlap(self):
        doc, graph = documents()
        add_worker(doc, graph)
        doc["operations"] = [local("a", write=True, size=65), local("b", write=True, size=65, buffer="local-b", initiator="peer1")]
        result = self.run_case(doc, graph)
        self.assertEqual([o.completion_aci_cycles for o in result.operations], [3, 3])
        self.assertEqual(result.elapsed_aci_cycles, 3)
        self.assertEqual({c.resource_id for c in result.chunks}, {"l1-a", "l1-b"})

    def test_local_and_both_fabrics_contend_for_one_l1_service(self):
        doc, graph = documents("write_acknowledged", 257)
        doc["max_outstanding_segments"] = 4
        second = copy.deepcopy(doc["operations"][0])
        second.update(operation_id="read", kind="read", initiator_id="src1", fabric_id=1,
                      source=extent("remote", 257, 1024), destination=extent("local", 257, 1024))
        doc["buffers"][1]["initially_ready"] = True
        doc["resources"][1]["service"]["fixed_latency_cycles"] = 10
        doc["operations"] += [second, local("local-read", size=257), local("local-write", write=True, size=257, offset=2048)]
        result = self.run_case(doc, graph, local_capacity=2)
        self.assertEqual(Counter(c.client_id for c in result.chunks if c.resource_id == "l1-a"),
                         {"transfer": 9, "read": 9, "local-read": 9, "local-write": 9})
        self.assertEqual(sum(c.service_aci_cycles for c in result.chunks if c.resource_id == "l1-a"), 36 * 5.5)
        self.assertEqual({e.fabric_id for e in result.transport.trace if e.action == "link_launch"}, {0, 1})
        self.assertTrue(all(o.packet_bytes == o.channel_bytes == 0 for o in result.operations[2:]))
        self.assertEqual(len([r for r in result.memory_resources if r.resource_id == "l1-a"]), 1)

    def test_cross_fabric_ack_fence_then_read_producer_version(self):
        doc, graph = documents(size=65)
        doc["packet"]["max_segment_payload_bytes"] = 32
        doc["max_outstanding_segments"] = 1
        doc["operations"] += [fence("transfer", fence_fabrics=[0, 1]),
                              {"operation_id": "readback", "kind": "read", "initiator_id": "src1", "fabric_id": 1,
                               "source": extent("remote", 65), "destination": extent("local", 65, 128),
                               "source_version": producer("transfer"), "depends_on": ["barrier"]}]
        result = self.run_case(doc, graph)
        write, barrier, read = result.operations
        self.assertEqual(barrier.completion_aci_cycles, write.completion_aci_cycles)
        self.assertEqual(read.descriptor_acceptance_aci_cycles, barrier.completion_aci_cycles)
        self.assertEqual((barrier.logical_bytes, barrier.memory_service_bytes, barrier.packet_bytes, barrier.channel_bytes), (0, 0, 0, 0))
        self.assertIsNone(barrier.descriptor_acceptance_aci_cycles)
        self.assertEqual(result.ordering[1].fence_fabrics, (0, 1))
        self.assertEqual(result.ordering[1].waits[0].event, "complete")
        self.assertEqual(result.packet_bytes, 576)
        self.assertEqual(result.memory_service_bytes, 384)

    def test_local_handoff_fence_does_not_gate_unrelated_later_issue(self):
        doc, graph = documents("write_posted", 33)
        doc["resources"][0]["service"]["fixed_latency_cycles"] = 100
        doc["operations"] += [fence("transfer", mode="local_handoff"),
                              local("dependent", depends_on=["barrier"]), local("independent", offset=64)]
        result = self.run_case(doc, graph, local_capacity=2)
        write, barrier, dependent, independent = result.operations
        self.assertEqual(barrier.completion_aci_cycles, write.final_request_handoff_aci_cycles)
        self.assertLess(barrier.completion_aci_cycles, write.destination_ready_aci_cycles)
        self.assertEqual(dependent.descriptor_acceptance_aci_cycles, barrier.completion_aci_cycles)
        self.assertEqual(independent.descriptor_acceptance_aci_cycles, 0)
        self.assertEqual(result.ordering[1].fence_fabrics, (0,))
        self.assertEqual([w.operation_id for w in result.ordering[1].waits], ["transfer"])

    def test_posted_source_reuse_releases_access_but_retains_buffer_until_drain(self):
        doc, graph = documents("write_posted", 33)
        doc["resources"][0]["service"]["fixed_latency_cycles"] = 100
        doc["operations"] += [local("overwrite", write=True, depends_on=["transfer"])]
        plan = execution_plan(doc, graph)
        complete = MemoryRuntime(plan).run(max_aci_cycles=1000)
        write, overwrite = complete.operations
        self.assertLess(overwrite.completion_aci_cycles, write.destination_ready_aci_cycles)
        self.assertLessEqual(write.source_read_completion_aci_cycles, overwrite.descriptor_acceptance_aci_cycles)
        acquired = next(e for e in complete.ownership_trace if e.action == "read_acquire")
        released = next(e for e in complete.ownership_trace if e.action == "access_release"
                        and (e.resource_id, e.access_id) == (acquired.resource_id, acquired.access_id))
        self.assertEqual(released.time_aci_cycles, write.source_read_completion_aci_cycles)
        runtime = MemoryRuntime(plan)
        early = runtime.run(max_aci_cycles=overwrite.completion_aci_cycles)
        self.assert_conserved(early, complete=False)
        self.assertFalse(any(e.action == "release" for e in early.ownership_trace))
        self.assertTrue(all(r.reserved_bytes for r in early.memory_resources))
        self.assertEqual(runtime.run(max_aci_cycles=1000), complete)

    def test_fence_freezes_both_fabrics_without_absorbing_later_work(self):
        doc, graph = documents(size=65)
        read = {"operation_id": "read", "kind": "read", "initiator_id": "src1", "fabric_id": 1,
                "source": extent("remote", 65, 256), "destination": extent("local", 65, 256)}
        doc["buffers"][1]["initially_ready"] = True
        doc["max_outstanding_segments"] = 2
        later = copy.deepcopy(doc["operations"][0])
        later.update(operation_id="later", start_aci_cycles=500, destination=extent("remote", 65, 1024))
        doc["operations"] += [read, fence("transfer", "read"), later, local("after", depends_on=["barrier"])]
        result = self.run_case(doc, graph)
        write, read, barrier, later, after = result.operations
        self.assertEqual(barrier.completion_aci_cycles, max(write.completion_aci_cycles, read.completion_aci_cycles))
        self.assertEqual(after.descriptor_acceptance_aci_cycles, barrier.completion_aci_cycles)
        self.assertLess(barrier.completion_aci_cycles, later.submission_aci_cycles)
        self.assertEqual(result.ordering[2].fence_fabrics, (0, 1))
        self.assertEqual({w.operation_id for w in result.ordering[2].waits}, {"transfer", "read"})

    def test_local_producer_enables_network_source_without_early_issue(self):
        doc, graph = documents(size=65)
        doc["buffers"][0].update(initially_ready=False, producer_operation_id="produce")
        doc["operations"][0]["depends_on"] = ["produce"]
        doc["operations"].insert(0, local("produce", write=True, size=65, initiator="src1"))
        runtime = MemoryRuntime(execution_plan(doc, graph))
        early = runtime.run(max_aci_cycles=2)
        self.assert_conserved(early, complete=False)
        self.assertEqual(next(d for d in early.descriptors if d.kind == "issue").occupied, 0)
        self.assertEqual(early.packet_bytes, 0)
        result = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(result)
        self.assertEqual(result.operations[1].descriptor_acceptance_aci_cycles, result.operations[0].destination_ready_aci_cycles)

    def test_destination_local_readiness_is_not_remote_sender_notification(self):
        doc, graph = documents("write_posted", 65)
        add_worker(doc, graph)
        doc["buffers"][2]["initially_ready"] = False
        doc["operations"][0]["destination"]["buffer_id"] = "local-b"
        doc["resources"][2]["service"]["fixed_latency_cycles"] = 40
        doc["packet"]["max_segment_payload_bytes"] = 32
        doc["operations"].append(local("consume", buffer="local-b", size=65, initiator="peer1",
                                       source_version=producer("transfer"), destination_ready_after=["transfer"]))
        result = self.run_case(doc, graph)
        write, consume = result.operations
        self.assertGreater(consume.descriptor_acceptance_aci_cycles, write.completion_aci_cycles)
        self.assertEqual(consume.descriptor_acceptance_aci_cycles, write.destination_ready_aci_cycles)
        self.assertEqual(consume.packet_bytes, 0)
        doc["operations"][1].update(destination_ready_after=[], depends_on=["transfer"])
        self.assert_rejected(doc, graph)
        doc["operations"][1].update(depends_on=[], destination_ready_after=["transfer"], initiator_id="src1", source=extent("local", 65))
        self.assert_rejected(doc, graph)

    def test_dependencies_wait_for_all_segments_even_with_out_of_order_issue(self):
        class DelayedFirstSegment(MemoryRuntime):
            def _issue(self, state):
                if state.definition.segment.segment_index == 0:
                    yield self.env.timeout(100)
                yield from super()._issue(state)

        doc, graph = documents("read", 65)
        doc["packet"]["max_segment_payload_bytes"] = 32
        doc["max_outstanding_segments"] = 2
        doc["operations"] += [local("consume", size=65, source_version=producer("transfer"), destination_ready_after=["transfer"]),
                              fence("transfer")]
        runtime = DelayedFirstSegment(execution_plan(doc, graph))
        early = runtime.run(max_aci_cycles=80)
        self.assert_conserved(early, complete=False)
        self.assertTrue(all(s.completion_aci_cycles is not None for s in early.segments[1:]))
        self.assertIsNone(early.operations[1].descriptor_acceptance_aci_cycles)
        self.assertIsNone(early.operations[2].completion_aci_cycles)
        self.assertTrue(any('"dependency","consume","transfer","destination_ready"' in p for p in early.pending))
        self.assertEqual(next(d for d in early.descriptors if d.kind == "local_client").occupied, 0)
        result = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(result)
        read, consume, barrier = result.operations
        self.assertEqual(consume.descriptor_acceptance_aci_cycles, read.destination_ready_aci_cycles)
        self.assertEqual(barrier.completion_aci_cycles, read.completion_aci_cycles)

    def test_version_epochs_reject_stale_initial_and_intervening_partial_writer(self):
        doc, graph = documents()
        doc["operations"] = [local("p", write=True, size=65), local("q", write=True, size=1, offset=32, depends_on=["p"]),
                             local("read", size=65, depends_on=["q"], source_version=producer("p"))]
        self.assert_rejected(doc, graph)
        doc["operations"][2]["source_version"] = {"kind": "initial"}
        self.assert_rejected(doc, graph)
        doc["operations"][2].update(source=extent(size=1, offset=32), source_version=producer("q"))
        result = self.run_case(doc, graph)
        self.assertEqual(result.ordering[2].source_version.producer_id, "q")
        doc["operations"][1]["depends_on"] = ["read"]
        doc["operations"][2].update(depends_on=["p"], source=extent(size=65), source_version=producer("p"))
        result = self.run_case(doc, graph)
        self.assertLessEqual(result.operations[2].completion_aci_cycles, result.operations[1].descriptor_acceptance_aci_cycles)

    def test_producer_can_supersede_older_ordered_writer(self):
        doc, graph = documents()
        doc["operations"] = [local("old", write=True), local("new", write=True, depends_on=["old"]),
                             local("read", depends_on=["new"], source_version=producer("new"))]
        result = self.run_case(doc, graph)
        self.assertEqual([o.completion_aci_cycles for o in result.operations], [2, 4, 6])

    def test_physical_alias_conflicts_require_effects_not_list_order_or_start_times(self):
        for dependency in ([], ["transfer"], ["barrier"]):
            doc, graph = documents("write_posted", 33)
            second = copy.deepcopy(doc["operations"][0])
            second.update(operation_id="overwrite", fabric_id=1, initiator_id="src1", start_aci_cycles=1000, depends_on=dependency)
            doc["operations"] += [fence("transfer", mode="local_handoff"), second]
            self.assert_rejected(doc, graph)
        doc["operations"][0]["kind"] = "write_acknowledged"
        doc["operations"][1]["fence_mode"] = "remote_completion"
        result = self.run_case(doc, graph)
        self.assertEqual(result.operations[2].descriptor_acceptance_aci_cycles, 1000)

    def test_scope_cycles_and_fence_references_rejected_before_allocation(self):
        cases = (
            lambda d: d["operations"][1].update(fence_fabrics=[1]),
            lambda d: d["operations"][1].update(fence_fabrics=[9]),
            lambda d: d["operations"].reverse(),
            lambda d: d["operations"][0].update(depends_on=["barrier"]),
            lambda d: d["operations"][1].update(depends_on=["missing"]),
            lambda d: d["operations"][0].update(kind="write_posted"),
            lambda d: d["operations"][1].update(initiator_id="peer0"),
            lambda d: d["operations"][1].update(fence_operations=["local"]),
            lambda d: d["operations"][1].update(destination_ready_after=["transfer"]),
        )
        for mutate in cases:
            doc, graph = documents()
            add_worker(doc, graph)
            doc["operations"] += [fence("transfer"), local("local")]
            mutate(doc)
            with self.subTest(mutate=mutate):
                self.assert_rejected(doc, graph)
        doc, graph = documents()
        doc["operations"] = [local("a", write=True, destination_ready_after=["b"]),
                             local("b", write=True, destination_ready_after=["a"])]
        self.assert_rejected(doc, graph)

    def test_local_access_and_producer_admission(self):
        cases = (
            lambda d: d["operations"][0]["source"].update(buffer_id="remote"),
            lambda d: d["operations"][0]["source"].update(offset_bytes=16384),
            lambda d: d["operations"][0]["source"].update(offset_bytes=1),
            lambda d: d["buffers"][0].update(readable=False),
            lambda d: d["endpoints"][0].update(enabled=False),
            lambda d: d["endpoints"][0].update(roles=["target"]),
            lambda d: d["operations"][0].update(source_version=producer("absent")),
            lambda d: d["operations"][0].update(source_version=producer("read")),
            lambda d: d["operations"][0].update(fabric_id=0),
            lambda d: d["operations"][0].update(destination=extent(offset=64)),
        )
        for mutate in cases:
            doc, graph = documents()
            doc["operations"] = [local("read")]
            mutate(doc)
            with self.subTest(mutate=mutate):
                self.assert_rejected(doc, graph)
        doc, graph = documents()
        doc["buffers"][0].update(initially_ready=False, producer_operation_id="p")
        doc["operations"] = [local("p"), local("consumer", depends_on=["p"])]
        self.assert_rejected(doc, graph)
        doc, graph = documents()
        doc["operations"] = [local("huge-control", start_aci_cycles=1e308)]
        with patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
            execution_plan(doc, graph, local_control=1e308)

    def test_local_lifecycle_rejects_fabricated_wire_or_completion_facts(self):
        doc, graph = documents()
        doc["operations"] = [local("read")]
        record = self.run_case(doc, graph).operations[0]
        for mutation in ({"final_request_handoff_aci_cycles": 0}, {"response_receipt_aci_cycles": 0},
                         {"destination_ready_aci_cycles": 2}, {"completion_aci_cycles": 1}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                MemoryOperationRecord.model_validate({**record.model_dump(), **mutation})


if __name__ == "__main__":
    unittest.main()
