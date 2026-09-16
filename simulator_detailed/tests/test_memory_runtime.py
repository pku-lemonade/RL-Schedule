"""Causal transaction, finite ownership and independent timing/byte oracles."""

from __future__ import annotations

import copy
import unittest
from collections import Counter
from itertools import pairwise
from unittest.mock import patch

from simulator_detailed.configs.schemas.memory_replay import MemoryReplay
from simulator_detailed.memory_execution import (
    MemoryExecutionPlan,
    MemoryExecutionResult,
    MemoryRuntimeConfig,
)
from simulator_detailed.memory_plan import MemoryPlan
from simulator_detailed.memory_records import MemoryOperationRecord
from simulator_detailed.memory_resources import MemoryAccess, MemoryVersion
from simulator_detailed.memory_runtime import MemoryRuntime
from simulator_detailed.memory_transport import MemoryTransportConfig
from simulator_detailed.tests.test_memory_packets import (
    EVIDENCE,
    FIXTURE,
    memory_documents,
)
from simulator_detailed.tests.test_packet_runtime import transport_config


def documents(kind="write_acknowledged", size=33, fabric=0):
    doc, graph = memory_documents(kind, size, fabric)
    doc["endpoint_queue_capacity_packets"] = doc["endpoint_staging_capacity_flits"] = 1
    if kind == "read":
        doc["buffers"][1]["initially_ready"] = True
    return doc, graph


def execution_plan(doc, graph, *, transport=None, responders=1, request_control=1, response_control=1,
                   local_capacity=1, local_control=0):
    return MemoryExecutionPlan.compile(MemoryPlan.compile(MemoryReplay.model_validate(doc), graph),
                                       MemoryRuntimeConfig(transport=transport or transport_config(),
                                                           responder_capacity_packets=responders,
                                                           request_control_aci_cycles=request_control,
                                                           response_control_aci_cycles=response_control,
                                                           local_capacity_operations=local_capacity,
                                                           local_control_aci_cycles=local_control, evidence=EVIDENCE))


class MemoryRuntimeTests(unittest.TestCase):
    def assert_conserved(self, result, *, complete=True):
        self.assertEqual(result.status, "complete" if complete else "incomplete")
        self.assertEqual(result.teardown_complete, complete)
        self.assertEqual(result.channel_bytes, sum(e.physical_bytes for e in result.transport.trace if e.action == "link_launch"))
        self.assertEqual(result.packet_bytes, sum(e.physical_bytes for e in result.transport.trace
                                                if e.action == "link_launch" and e.channel.kind == "inject"))
        self.assertEqual(result.memory_service_bytes, sum(c.serviced_bytes for c in result.chunks))
        self.assertEqual(result.execution, "addressed_memory_ordered_v1")
        self.assertEqual(result.transport.memory_service, "external_hooks")
        self.assertEqual(result.silicon_timing, "unvalidated")
        owners, peaks = {}, Counter()
        for event in result.descriptor_trace:
            key = event.kind, event.owner_id
            live = owners.setdefault(key, set())
            segment = event.operation_id, event.segment_index
            if event.action == "acquire":
                self.assertNotIn(segment, live)
                live.add(segment)
            else:
                self.assertIn(segment, live)
                live.remove(segment)
            self.assertEqual(len(live), event.occupied)
            peaks[key] = max(peaks[key], event.occupied)
        for descriptor in result.descriptors:
            key = descriptor.kind, descriptor.owner_id
            self.assertEqual(set(descriptor.owners), owners.get(key, set()))
            self.assertEqual(descriptor.peak_occupied, peaks[key])
            self.assertLessEqual(descriptor.peak_occupied, descriptor.capacity)
            if complete:
                self.assertEqual(descriptor.occupied, 0)
        for buffer in result.transport.endpoint_buffers:
            self.assertEqual(buffer.available + buffer.occupied, buffer.capacity)
            self.assertLessEqual(buffer.peak_occupied, buffer.capacity)
            if complete:
                self.assertEqual(buffer.occupied, 0)
        for resource in result.transport.resources:
            self.assertEqual(resource.available + resource.occupied + resource.pending_returns, resource.capacity)
            if complete:
                self.assertTrue(resource.is_drained)
        for resource in result.memory_resources:
            self.assertEqual(resource.available_bytes + resource.reserved_bytes, resource.capacity_bytes)
            self.assertLessEqual(resource.service.queue_peak, resource.service.queue_capacity)
            if complete:
                self.assertEqual(resource.service.pending_service_ids, ())
                self.assertFalse(any(b.access_ids for b in resource.buffers))
            for first, second in pairwise(c for c in result.chunks if c.resource_id == resource.resource_id):
                self.assertLessEqual(first.end_aci_cycles, second.start_aci_cycles)
        if complete:
            self.assertFalse(result.pending)
            self.assertTrue(all(r.available_bytes == r.capacity_bytes and not r.buffers for r in result.released_resources))
            self.assertEqual(Counter(e.buffer_id for e in result.ownership_trace if e.action == "reserve"),
                             Counter(e.buffer_id for e in result.ownership_trace if e.action == "release"))

    def test_same_router_independent_one_byte_timeline(self):
        # One-cycle source/destination memory chunks. Two one-cycle channels
        # and one one-cycle router transfer per route; zero control/credit cost.
        # Header travels at t=0..3. Its input credit is retained until router
        # transfer ends at 2, so data travels at 2..5, then memory at 5..6.
        for kind, source, handoff, receipt, ready, completion, drain, packet, channel in (
            ("write_posted", 1, 2, None, 6, 2, 6, 64, 128),
            ("write_acknowledged", 1, 2, 9, 6, 9, 9, 96, 192),
            ("read", 4, 0, 8, 9, 9, 9, 96, 192),
        ):
            with self.subTest(kind=kind):
                doc, graph = documents(kind, 1)
                doc["issue_latency_aci_cycles"] = 0
                op = doc["operations"][0]
                op["source"]["buffer_id"] = op["destination"]["buffer_id"] = "local"
                op["destination"]["offset_bytes"] = 32
                runtime = MemoryRuntime(execution_plan(doc, graph, transport=transport_config(credit=0),
                                                       request_control=0, response_control=0))
                result = runtime.run(max_aci_cycles=100)
                self.assert_conserved(result)
                record = result.operations[0]
                self.assertEqual((record.source_read_completion_aci_cycles, record.final_request_handoff_aci_cycles,
                                  record.response_receipt_aci_cycles, record.destination_ready_aci_cycles,
                                  record.completion_aci_cycles, result.elapsed_aci_cycles),
                                 (source, handoff, receipt, ready, completion, drain))
                self.assertEqual((result.logical_bytes, result.packet_bytes, result.channel_bytes, result.memory_service_bytes),
                                 (1, packet, channel, 64))
                self.assertEqual([(c.direction, c.useful_bytes, c.serviced_bytes) for c in result.chunks],
                                 [("read", 1, 32), ("write", 1, 32)])
                self.assertIs(runtime.run(), result)
                self.assertEqual(result, MemoryExecutionResult.model_validate_json(result.model_dump_json()))

    def test_segmented_transactions_both_fabrics_literal_byte_oracles(self):
        for case in FIXTURE["channel_cases"]:
            for kind in ("read", "write_posted", "write_acknowledged"):
                with self.subTest(kind=kind, fabric=case["fabric"]):
                    result = MemoryRuntime(execution_plan(*documents(kind, 8193, case["fabric"]))).run(max_aci_cycles=100_000)
                    self.assert_conserved(result)
                    self.assertEqual((result.logical_bytes, result.packet_bytes, result.channel_bytes, result.memory_service_bytes),
                                     (8193, 8288 if kind == "write_posted" else 8352, case[kind], 16448))
                    self.assertEqual([s.logical_bytes for s in result.segments], [8192, 1])
                    self.assertEqual(sum(c.useful_bytes for c in result.chunks), 2 * 8193)
                    self.assertEqual(len({c.service_id for c in result.chunks}), 514)
                    self.assertEqual(result.operations[0].completion_aci_cycles, max(s.completion_aci_cycles for s in result.segments))
                    self.assertEqual(result.operations[0].destination_ready_aci_cycles,
                                     max(s.destination_ready_aci_cycles for s in result.segments))
                    responses = [e for e in result.transport.trace if e.action == "link_launch" and e.channel.kind == "inject"
                                 and e.packet.traffic_class == "response" and e.flit_index == 0]
                    self.assertEqual(len(responses), 0 if kind == "write_posted" else 2)
                    self.assertEqual(len({e.packet for e in responses}), len(responses))

    def test_posted_completion_keeps_pending_target_effects_and_capacity(self):
        doc, graph = documents("write_posted", 33)
        for resource in doc["resources"]:
            if resource["resource_id"] == "dram":
                resource["service"]["fixed_latency_cycles"] = 100
        plan = execution_plan(doc, graph)
        complete = MemoryRuntime(plan).run(max_aci_cycles=1000)
        handoff = complete.operations[0].completion_aci_cycles
        runtime = MemoryRuntime(plan)
        early = runtime.run(max_aci_cycles=handoff)
        self.assert_conserved(early, complete=False)
        self.assertEqual(early.operations[0].completion_aci_cycles, handoff)
        self.assertIsNone(early.operations[0].destination_ready_aci_cycles)
        self.assertEqual(early.reason, "cycle_limit")
        self.assertTrue(any("destination_ready" in p for p in early.pending))
        self.assertTrue(any(r.reserved_bytes for r in early.memory_resources))
        self.assertEqual(next(d for d in early.descriptors if d.kind == "issue").occupied, 0)
        self.assertLess(early.channel_bytes, complete.channel_bytes)
        self.assertEqual(runtime.run(max_aci_cycles=1000), complete)

    def test_read_wire_arrival_precedes_local_write_and_descriptor_retirement(self):
        doc, graph = documents("read", 33)
        for resource in doc["resources"]:
            if resource["resource_id"] == "l1-a":
                resource["service"]["fixed_latency_cycles"] = 80
        plan = execution_plan(doc, graph)
        complete = MemoryRuntime(plan).run(max_aci_cycles=1000)
        arrival = complete.operations[0].response_receipt_aci_cycles
        self.assertLess(arrival, complete.operations[0].completion_aci_cycles)
        runtime = MemoryRuntime(plan)
        early = runtime.run(max_aci_cycles=arrival)
        self.assert_conserved(early, complete=False)
        self.assertEqual(early.operations[0].response_receipt_aci_cycles, arrival)
        self.assertIsNone(early.operations[0].completion_aci_cycles)
        self.assertIsNone(early.operations[0].destination_ready_aci_cycles)
        self.assertEqual(next(d for d in early.descriptors if d.kind == "issue").occupied, 1)
        self.assertTrue(any(b.kind == "rx_staging" and b.occupied for b in early.transport.endpoint_buffers))
        final = runtime.run(max_aci_cycles=1000)
        self.assertEqual(final, complete)

    def test_ack_requires_target_service_and_only_finite_control_at_sink(self):
        doc, graph = documents("write_acknowledged", 65)
        result = MemoryRuntime(execution_plan(doc, graph, response_control=9)).run(max_aci_cycles=1000)
        self.assert_conserved(result)
        op = result.operations[0]
        first_ack = min(e.time_aci_cycles for e in result.transport.trace if e.action == "link_launch"
                        and e.packet.traffic_class == "response")
        self.assertGreaterEqual(first_ack, op.destination_ready_aci_cycles)
        self.assertEqual(op.completion_aci_cycles, op.response_receipt_aci_cycles + 9)
        self.assertEqual(Counter((c.resource_id, c.direction) for c in result.chunks), {("l1-a", "read"): 3, ("dram", "write"): 3})

    def test_source_and_destination_service_keep_counted_staging(self):
        for side, kind in (("l1-a", "tx_staging"), ("dram", "rx_staging")):
            doc, graph = documents("write_posted", 65)
            for resource in doc["resources"]:
                if resource["resource_id"] == side:
                    resource["service"]["fixed_latency_cycles"] = 100
            runtime = MemoryRuntime(execution_plan(doc, graph))
            early = runtime.run(max_aci_cycles=20)
            self.assert_conserved(early, complete=False)
            self.assertTrue(next(r for r in early.memory_resources if r.resource_id == side).service.active)
            self.assertTrue(any(b.kind == kind and b.occupied == 1 for b in early.transport.endpoint_buffers))
            self.assert_conserved(runtime.run(max_aci_cycles=1000))

    def test_delayed_credits_prevent_early_teardown(self):
        runtime = MemoryRuntime(execution_plan(*documents("write_posted", 1), transport=transport_config(credit=100, capacity=2)))
        early = runtime.run(max_aci_cycles=50)
        self.assert_conserved(early, complete=False)
        self.assertTrue(all(s.completion_aci_cycles is not None and s.destination_ready_aci_cycles is not None for s in early.segments))
        self.assertFalse(early.transport.pending_packets)
        self.assertTrue(any(r.pending_returns for r in early.transport.resources))
        self.assertTrue(any('"network"' in p for p in early.pending))
        final = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(final)
        self.assertEqual(early.memory_service_bytes, final.memory_service_bytes)

    def test_idle_blocked_access_is_diagnosed_without_partial_lease_or_descriptor(self):
        doc, graph = documents("write_posted", 33)
        doc["buffers"][1]["initially_ready"] = True
        runtime = MemoryRuntime(execution_plan(doc, graph))
        owner = runtime.memory.resources["dram"]
        lease = owner.try_acquire(runtime.memory.handles["remote"], MemoryAccess(
            client_id="held", direction="read", offset_bytes=0, size_bytes=33, version=MemoryVersion(kind="initial")))
        early = runtime.run(max_aci_cycles=100)
        self.assert_conserved(early, complete=False)
        self.assertEqual(early.reason, "idle_with_pending")
        self.assertEqual(early.packet_bytes, 0)
        self.assertTrue(all(d.occupied == 0 for d in early.descriptors))
        self.assertTrue(runtime.memory.resources["l1-a"].is_drained)
        self.assertTrue(any('"access"' in p for p in early.pending))
        owner.release_access(lease)
        self.assert_conserved(runtime.run(max_aci_cycles=1000))

    def test_unready_source_waits_before_issue_and_start_time_holds_no_descriptor(self):
        doc, graph = documents("write_posted", 1)
        doc["operations"][0]["start_aci_cycles"] = 50
        runtime = MemoryRuntime(execution_plan(doc, graph))
        early = runtime.run(max_aci_cycles=10)
        self.assert_conserved(early, complete=False)
        self.assertIsNone(early.operations[0].submission_aci_cycles)
        source = runtime.memory.resources["l1-a"]
        source.try_acquire(runtime.memory.handles["local"], MemoryAccess(
            client_id="invalidate", direction="write", offset_bytes=0, size_bytes=1,
            version=MemoryVersion(kind="producer", producer_id="invalidate")))
        blocked = runtime.run(max_aci_cycles=100)
        self.assert_conserved(blocked, complete=False)
        self.assertEqual(blocked.reason, "idle_with_pending")
        self.assertEqual(blocked.operations[0].submission_aci_cycles, 50)
        self.assertIsNone(blocked.operations[0].descriptor_acceptance_aci_cycles)
        self.assertTrue(all(d.occupied == 0 for d in blocked.descriptors))
        self.assertEqual(blocked.channel_bytes, 0)

    def test_mixed_dual_fabric_slow_memory_one_slot_endpoints_share_owners(self):
        doc, graph = documents(size=513)
        doc["buffers"][1]["initially_ready"] = True
        doc["max_outstanding_segments"] = 4
        for resource in doc["resources"]:
            resource["service"].update(queue_capacity=1, fixed_latency_cycles=7)
        base = doc["operations"][0]
        doc["operations"] = []
        for index in range(12):
            op = copy.deepcopy(base)
            op.update(operation_id=f"mixed-{index}", kind=("read", "write_posted", "write_acknowledged")[index % 3],
                      fabric_id=index % 2, initiator_id=f"src{index % 2}")
            if op["kind"] == "read":
                op["source"]["buffer_id"], op["destination"]["buffer_id"] = "remote", "local"
            op["source"]["offset_bytes"] = op["destination"]["offset_bytes"] = index * 544
            doc["operations"].append(op)
        config = transport_config(propagation=2, credit=3).model_dump(mode="json")
        slow = copy.deepcopy(config["fabrics"][0]["network_link"])
        slow["slowdowns"] = [["directed", 0, 70, 3]]
        config["overrides"] = [{"channel": {"fabric_id": 0, "kind": "network", "identity": "t0_0/x+"}, "settings": slow}]
        runtime = MemoryRuntime(execution_plan(doc, graph, transport=MemoryTransportConfig.model_validate(config)))
        self.assertIs(runtime.memory.via("ram0", "dram"), runtime.memory.via("ram1", "dram"))
        result = runtime.run(max_aci_cycles=100_000)
        self.assert_conserved(result)
        self.assertEqual(result.logical_bytes, 12 * 513)
        self.assertEqual(result.memory_service_bytes, 12 * 2 * 544)
        self.assertEqual([(d.owner_id, d.capacity, d.peak_occupied) for d in result.descriptors if d.kind == "issue"], [("l1-a", 4, 4)])
        self.assertEqual(len([p for p in result.transport.packets if p.packet.traffic_class == "response"]), 8)
        self.assertTrue(all(link.env is runtime.env for link in runtime.transport.links.values()))
        self.assertTrue(all(owner.env is runtime.env for owner in runtime.memory.resources.values()))
        self.assertTrue(any(e.launch_factor == 3 for e in result.transport.trace))
        self.assertTrue(all(e.launch_factor == 1 for e in result.transport.trace if e.action == "link_launch" and e.fabric_id == 1))

    def test_both_initiator_aliases_share_one_issue_budget_and_allow_common_read_source(self):
        for capacity in (1, 2):
            doc, graph = documents("write_posted", 33)
            doc["max_outstanding_segments"] = capacity
            second = copy.deepcopy(doc["operations"][0])
            second.update(operation_id="second", initiator_id="src1", fabric_id=1)
            second["destination"]["offset_bytes"] = 64
            doc["operations"].append(second)
            result = MemoryRuntime(execution_plan(doc, graph)).run(max_aci_cycles=1000)
            self.assert_conserved(result)
            first, second = result.operations
            issue = [d for d in result.descriptors if d.kind == "issue"]
            self.assertEqual(len(issue), 1)
            self.assertEqual(issue[0].peak_occupied, capacity)
            self.assertEqual(second.descriptor_acceptance_aci_cycles, first.completion_aci_cycles if capacity == 1 else 0)
            self.assertEqual(result.memory_service_bytes, 256)
            self.assertEqual(sum(e.action == "read_acquire" for e in result.ownership_trace), 2)

    def test_competing_initiators_reserve_responders_only_after_target_writes(self):
        doc, graph = documents(size=513)
        graph["resources"].append({"resource_id": "l1-b", "kind": "local_sram", "owner_tile_id": "t1_0", "capacity_bytes": 65536})
        doc["resources"].append({**copy.deepcopy(doc["resources"][1]), "resource_id": "l1-b"})
        doc["buffers"].append({**copy.deepcopy(doc["buffers"][0]), "buffer_id": "local-b", "resource_id": "l1-b"})
        for fabric in (0, 1):
            graph["attachments"].append({**copy.deepcopy(graph["attachments"][2 * fabric]), "endpoint_id": f"peer{fabric}",
                                         "router_id": "t1_0", "resource_ids": ["l1-b"]})
            doc["endpoints"].append({**copy.deepcopy(doc["endpoints"][2 * fabric]), "endpoint_id": f"peer{fabric}",
                                     "router_id": "t1_0", "resource_ids": ["l1-b"]})
        base = doc["operations"][0]
        doc["operations"] = []
        for index in range(6):
            op = copy.deepcopy(base)
            op.update(operation_id=f"compete-{index}", initiator_id="peer0" if index % 2 else "src0")
            if index % 2:
                op["source"]["buffer_id"] = "local-b"
            op["destination"]["offset_bytes"] = index * 544
            doc["operations"].append(op)
        result = MemoryRuntime(execution_plan(doc, graph, response_control=11)).run(max_aci_cycles=100_000)
        self.assert_conserved(result)
        self.assertEqual(result.logical_bytes, 6 * 513)
        arrivals = [e for e in result.transport.trace if e.action == "link_arrive" and e.channel.kind == "eject"
                    and e.packet.traffic_class == "request"]
        first_tail = next(i for i, e in enumerate(arrivals) if e.flit_index == 17)
        # Existing per-lane packet ownership serializes these competing packets
        # at the shared ejection lane; do not claim this is flit interleaving.
        self.assertEqual(sum(e.flit_index == 0 for e in arrivals[:first_tail]), 1)
        self.assertEqual(next(d for d in result.descriptors if d.kind == "responder").peak_occupied, 1)
        self.assertEqual(sum(e.action == "acquire" and e.kind == "responder" for e in result.descriptor_trace), 6)
        records = {s.operation_id: s for s in result.segments}
        for event in result.descriptor_trace:
            if event.kind == "responder" and event.action == "acquire":
                self.assertGreaterEqual(event.time_aci_cycles, records[event.operation_id].destination_ready_aci_cycles)

    def test_full_responder_and_request_rx_drain_through_independent_response_class(self):
        doc, graph = documents("read", 513)
        doc["packet"]["max_segment_payload_bytes"] = 256
        doc["max_outstanding_segments"] = 3
        for resource in doc["resources"]:
            if resource["resource_id"] == "dram":
                resource["service"]["fixed_latency_cycles"] = 50
        runtime = MemoryRuntime(execution_plan(doc, graph))
        early = runtime.run(max_aci_cycles=50)
        self.assert_conserved(early, complete=False)
        self.assertEqual(next(d for d in early.descriptors if d.kind == "responder").occupied, 1)
        self.assertTrue(any(b.traffic_class == "request" and b.kind == "rx_staging" and b.occupied == 1
                            for b in early.transport.endpoint_buffers))
        self.assertTrue(any(b.traffic_class == "response" and b.kind == "tx_staging" and b.occupied == 1
                            for b in early.transport.endpoint_buffers))
        final = runtime.run(max_aci_cycles=10_000)
        self.assert_conserved(final)
        self.assertEqual(sum(e.kind == "responder" and e.action == "release" for e in final.descriptor_trace), 3)

    def test_out_of_order_segment_completion_aggregates_all_facts(self):
        class PerturbedIssueRuntime(MemoryRuntime):
            def _issue(self, state):
                # Exercise an alternative legal scheduler ordering without
                # changing transport, memory service or aggregation behavior.
                if state.definition.segment.segment_index == 0:
                    yield self.env.timeout(100)
                yield from super()._issue(state)

        doc, graph = documents("read", 65)
        doc["packet"]["max_segment_payload_bytes"] = 32
        doc["max_outstanding_segments"] = 2
        runtime = PerturbedIssueRuntime(execution_plan(doc, graph))
        early = runtime.run(max_aci_cycles=80)
        self.assert_conserved(early, complete=False)
        self.assertIsNone(early.operations[0].completion_aci_cycles)
        self.assertTrue(all(s.completion_aci_cycles is not None for s in early.segments[1:]))
        result = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(result)
        order = [e.segment_index for e in result.lifecycle if e.action == "complete"]
        self.assertEqual(order, [1, 2, 0])
        op = result.operations[0]
        self.assertEqual(op.descriptor_acceptance_aci_cycles, 0)
        self.assertEqual(op.completion_aci_cycles, result.segments[0].completion_aci_cycles)
        self.assertEqual(op.destination_ready_aci_cycles, result.segments[0].destination_ready_aci_cycles)

    def test_generic_multiple_headers_chunks_and_native_clock_conversion(self):
        doc, graph = documents("read", 25)
        doc["packet"].update(physical_flit_bytes=16, data_capacity_bytes=8, header_flits=2,
                             max_segment_payload_bytes=24, address_alignment_bytes=4)
        for resource in doc["resources"]:
            resource["service"].update(service_granule_bytes=4, chunk_bytes=4, native_clock_hz=250_000_000.0,
                                       bytes_per_cycle=4, fixed_latency_cycles=0.5)
        result = MemoryRuntime(execution_plan(doc, graph, transport=transport_config(physical=16, width=64, interval=2))).run(max_aci_cycles=1000)
        self.assert_conserved(result)
        self.assertEqual((result.logical_bytes, result.packet_bytes, result.channel_bytes, result.memory_service_bytes),
                         (25, 192, 896, 56))
        self.assertEqual(len(result.chunks), 14)
        self.assertTrue(all(c.native_cycles == 1.5 and c.service_aci_cycles == 3 for c in result.chunks))
        self.assertEqual(Counter(c.useful_bytes for c in result.chunks), {4: 12, 1: 2})

    def test_invalid_execution_rejected_before_environment_allocation(self):
        for mutate in (
            lambda d: d["buffers"][0].update(initially_ready=False),
            lambda d: d["operations"][0].update(kind="local_write", fabric_id=None),
            lambda d: d["operations"].append({**copy.deepcopy(d["operations"][0]), "operation_id": "conflict", "fabric_id": 1}),
            lambda d: d["operations"][0]["destination"].update(buffer_id="local"),
        ):
            doc, graph = documents()
            mutate(doc)
            with self.subTest(mutate=mutate), patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                execution_plan(doc, graph)
        plan = execution_plan(*documents())
        for settings in (plan.settings.model_copy(update={"responder_capacity_packets": True}),
                         plan.settings.model_copy(update={"local_capacity_operations": True}),
                         plan.settings.model_copy(update={"local_control_aci_cycles": -1}),
                         plan.settings.model_copy(update={"transport": transport_config(staging=2)})):
            with patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                MemoryExecutionPlan.compile(plan.memory, settings)

    def test_lifecycle_records_accept_partial_order_and_reject_false_boundaries(self):
        posted = MemoryRuntime(execution_plan(*documents("write_posted", 1))).run(max_aci_cycles=1000).operations[0]
        self.assertLess(posted.completion_aci_cycles, posted.destination_ready_aci_cycles)
        with self.assertRaises(ValueError):
            MemoryOperationRecord.model_validate({**posted.model_dump(), "completion_aci_cycles": posted.destination_ready_aci_cycles})
        read = MemoryRuntime(execution_plan(*documents("read", 1))).run(max_aci_cycles=1000).operations[0]
        self.assertLess(read.final_request_handoff_aci_cycles, read.source_read_completion_aci_cycles)
        with self.assertRaises(ValueError):
            MemoryOperationRecord.model_validate({**read.model_dump(), "completion_aci_cycles": read.response_receipt_aci_cycles})


if __name__ == "__main__":
    unittest.main()
