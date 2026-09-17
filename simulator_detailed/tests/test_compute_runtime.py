"""Independent single-job service/route oracles and resumable ownership checks."""

from __future__ import annotations

import copy
import unittest
from collections import Counter
from dataclasses import replace
from unittest.mock import patch

from simulator_detailed.compute_memory import ComputeMemoryPlan
from simulator_detailed.compute_runtime import ComputeExecutionResult, ComputeRuntime
from simulator_detailed.memory_execution import MemoryRuntimeConfig
from simulator_detailed.memory_resources import MemoryAccess, MemoryVersion
from simulator_detailed.tests import test_memory_runtime
from simulator_detailed.tests.test_compute_buffers import lower
from simulator_detailed.tests.test_compute_contracts import (
    addressed,
    compile_document,
    compute_documents,
)
from simulator_detailed.tests.test_memory_contracts import EVIDENCE
from simulator_detailed.tests.test_packet_runtime import transport_config


def tiny_documents(*, remote=False, writer="local", far=False):
    document, graph = compute_documents(local=True)
    document["memory"].update(issue_latency_aci_cycles=0, endpoint_queue_capacity_packets=1,
                               endpoint_staging_capacity_flits=1)
    for resource in document["memory"]["resources"]:
        resource["service"].update(native_clock_hz=500_000_000, bytes_per_cycle=1,
                                    fixed_latency_cycles=0, service_granule_bytes=1)
    document["dtypes"][0].update(dtype_id="test_u8", bytes_per_element=1)
    document["workers"][0]["native_clock_hz"] = 500_000_000
    job = document["streams"][0]["jobs"][0]
    for name, shape in (("a", [1, 1, 1]), ("b", [1, 1]), ("c", [1, 1, 1])):
        job["operation"][name].update(shape=shape, dtype="test_u8")
        document["rates"][0]["key"][f"{name}_dtype"] = "test_u8"
    for operand in (job["a"], job["b"]):
        operand["source"]["size_bytes"] = 1
    job["output"]["destination"]["size_bytes"] = 1
    document["rates"][0].update(block={"m": 1, "n": 1, "k": 1}, work_per_native_cycle=1,
                                 setup_native_cycles=1, quantum_native_cycles=1, minimum_native_cycles=1)
    if remote:
        job["a"].update(mode="remote", fabric_id=0, source=addressed("remote" if far else "local", 1024, 1))
    if writer != "local":
        job["output"].update(mode=writer, fabric_id=1, destination=addressed("local", 2048, 1))
    return document, graph


def tiny_plan(*, credit=0, **kwargs):
    document, graph = tiny_documents(**kwargs)
    return tiny_lower(document, graph, credit=credit)


def tiny_lower(document, graph, *, credit=0):
    settings = MemoryRuntimeConfig(transport=transport_config(credit=credit), responder_capacity_packets=1,
                                   request_control_aci_cycles=0, response_control_aci_cycles=0, evidence=EVIDENCE)
    return ComputeMemoryPlan.compile(compile_document(document, graph), settings)


def times(result):
    return {event.action: event.time_aci_cycles for event in result.stages}


class ComputeRuntimeTests(unittest.TestCase):
    def assert_conserved(self, result, *, complete=True):
        self.assertEqual(result.status, "complete" if complete else "incomplete")
        test_memory_runtime.MemoryRuntimeTests().assert_conserved(result.memory_session, complete=complete)
        live, peaks = {}, Counter()
        for event in result.resource_events:
            key = event.worker_tile_id, event.kind
            owners = live.setdefault(key, {})
            if event.action == "acquire":
                self.assertNotIn(event.engine_index, owners)
                owners[event.engine_index] = event.job_id
            else:
                self.assertEqual(owners.pop(event.engine_index), event.job_id)
            self.assertEqual(len(owners), event.occupied)
            self.assertLessEqual(event.occupied, event.capacity)
            peaks[key] = max(peaks[key], event.occupied)
        for resource in result.resources:
            key = resource.worker_tile_id, resource.kind
            self.assertEqual({o.engine_index: o.job_id for o in resource.owners}, live.get(key, {}))
            self.assertEqual(resource.peak_occupied, peaks[key])
        self.assertEqual(result, ComputeExecutionResult.model_validate_json(result.model_dump_json()))

    def test_local_fractional_oracle_and_no_legacy_charges(self):
        document, graph = tiny_documents()
        for resource in document["memory"]["resources"]:
            resource["service"].update(bytes_per_cycle=4, fixed_latency_cycles=1)
        document["rates"][0].update(work_per_native_cycle=4, quantum_native_cycles=0.25,
                                     minimum_native_cycles=0.25, setup_native_cycles=0.25)
        document["workers"][0].update(reader_capacity=2, compute_contexts=3, writer_capacity=4)
        with patch("simulator_detailed.core.TPU.occupy", side_effect=AssertionError("legacy math")), \
                patch("simulator_detailed.core.LSU.occupy", side_effect=AssertionError("duplicate LSU")), \
                patch("simulator_detailed.core.ScratchpadMemory.allocate", side_effect=AssertionError("duplicate capacity")):
            runtime = ComputeRuntime(tiny_lower(document, graph))
            result = runtime.run()
        self.assert_conserved(result)
        # Each of three local bytes costs 1 + 1/4 = 1.25 cycles, serially.
        # Two multiply-add operations at rate 4 plus setup .25 cost .75.
        stage = times(result)
        self.assertEqual((stage["inputs_ready"], stage["operand_start"], stage["operand_end"],
                          stage["math_start"], stage["math_end"], stage["result_start"],
                          stage["output_ready"], stage["writer_complete"]), (0, 0, 2.5, 2.5, 3.25, 3.25, 4.5, 4.5))
        self.assertEqual(result.elapsed_aci_cycles, 4.5)
        self.assertEqual((result.work.completed_useful_work, result.work.completed_executed_work,
                          result.work.math_busy_aci_cycles, result.work.context_occupied_aci_cycles), (2, 2, 0.75, 4.5))
        memory = result.memory_session
        self.assertEqual([(c.start_aci_cycles, c.end_aci_cycles) for c in memory.chunks], [(0, 1.25), (1.25, 2.5), (3.25, 4.5)])
        self.assertEqual((memory.packet_bytes, memory.channel_bytes, memory.memory_service_bytes), (0, 0, 3))
        self.assertEqual([(r.kind, r.capacity, r.peak_occupied) for r in result.resources],
                         [("reader", 2, 1), ("compute", 3, 1), ("writer", 4, 1)])
        self.assertIs(runtime.env, runtime.memory.env)
        self.assertIs(runtime.env, runtime.buffers.env)
        self.assertIs(runtime.env, runtime.memory.transport.env)
        self.assertTrue(all(r.env is runtime.env for r in runtime.memory.memory.resources.values()))

    def test_same_router_transaction_oracle_and_local_posted_boundary(self):
        # Unit channels/router service, one-byte memory service, zero control.
        # Read: request 0..3; response header 3..6; data 5..8; L1 write 8..9.
        # Operand reads 9..11, math 11..14, result 14..15. Writer: source
        # 15..16, handoff 17, tail arrival 20, target write 20..21, ack 21..24.
        for mode, complete, end, packets, channels in (("write_posted", 17, 21, 160, 320),
                                                       ("write_acknowledged", 24, 24, 192, 384)):
            with self.subTest(mode=mode):
                result = ComputeRuntime(tiny_plan(remote=True, writer=mode)).run()
                self.assert_conserved(result)
                stage = times(result)
                self.assertEqual((stage["inputs_ready"], stage["math_start"], stage["math_end"],
                                  stage["output_ready"], stage["writer_complete"], result.elapsed_aci_cycles),
                                 (9, 11, 14, 15, complete, end))
                memory = result.memory_session
                self.assertEqual((memory.packet_bytes, memory.channel_bytes, memory.memory_service_bytes), (packets, channels, 7))
                read = next(o for o in memory.operations if o.kind == "read")
                writer = next(o for o in memory.operations if o.kind == mode)
                self.assertEqual((read.source_read_completion_aci_cycles, read.response_receipt_aci_cycles,
                                  read.destination_ready_aci_cycles), (4, 8, 9))
                self.assertEqual((writer.source_read_completion_aci_cycles, writer.final_request_handoff_aci_cycles,
                                  writer.destination_ready_aci_cycles, writer.completion_aci_cycles), (16, 17, 21, complete))
                self.assertEqual({e.worker_tile_id for e in result.resource_events}, {"t0_0"})
                self.assertEqual({p.definition.route.fabric_id for p in memory.wire_packets}, {0, 1})

    def test_longer_route_delays_compute_by_independently_counted_hops(self):
        near = ComputeRuntime(tiny_plan(remote=True)).run()
        far = ComputeRuntime(tiny_plan(remote=True, far=True)).run()
        # Positive torus: request t0_0->t2_2 crosses four links; return crosses
        # two. Six added links cost 12 channel/router cycles. The first
        # response network credit is held until the header's next transfer
        # ends at t=15; data arrives at the injection buffer at 14 and waits
        # one extra cycle. Tail arrives at 21; destination service ends at 22.
        self.assertEqual(times(near)["inputs_ready"], 9)
        self.assertEqual(times(far)["inputs_ready"], 9 + 6 * 2 + 1)
        self.assertEqual(times(far)["math_start"] - times(near)["math_start"], 13)
        blocked_transfer = [e for e in far.memory_session.transport.trace if e.action == "transfer_start"
                            and e.packet.traffic_class == "response" and e.flit_index == 1
                            and e.channel.identity == "t2_2/x+"]
        self.assertEqual([e.time_aci_cycles for e in blocked_transfer], [15])
        self.assertEqual(near.work, far.work)
        # A one-header request uses six channels; header+data return uses four.
        self.assertEqual(far.memory_session.channel_bytes, 32 * 6 + 64 * 4)
        self.assert_conserved(far)

    def test_full_matrix_bytes_both_fabrics_and_shared_compute_identity(self):
        result = ComputeRuntime(lower(*compute_documents())).run()
        self.assert_conserved(result)
        self.assertEqual((result.work.completed_useful_work, result.work.completed_executed_work), (420, 1024))
        self.assertEqual((result.work.math_busy_aci_cycles, result.work.context_occupied_aci_cycles), (5, 12.34375))
        memory = result.memory_session
        # A/B/C = 60/70/84 bytes. Six packet headers, eight 32-byte data
        # flits. Read-request routes have 6 channels, responses 4; writer
        # data has 4 and ack 6. Local operand/result service adds 214 bytes.
        self.assertEqual((memory.packet_bytes, memory.channel_bytes, memory.memory_service_bytes), (448, 1984, 642))
        accounting = memory.accounting
        self.assertEqual((accounting.planned_network_logical_bytes, accounting.planned_local_logical_bytes), (214, 214))
        self.assertEqual((accounting.injected_wire.useful_bytes, accounting.injected_wire.header_bytes,
                          accounting.injected_wire.padding_bytes), (214, 192, 42))
        self.assertEqual((accounting.completed_read_useful_bytes, accounting.completed_write_useful_bytes), (344, 298))
        self.assertEqual({r.worker_tile_id for r in result.resources}, {"t0_0"})
        self.assertEqual(sum(r.kind == "compute" for r in result.resources), 1)

    def test_fc_uses_the_same_admitted_math_and_memory_stages(self):
        document, graph = compute_documents()
        matmul = ComputeRuntime(lower(document, graph)).run()
        document["streams"][0]["jobs"][0]["operation"].update(kind="fc", flattening="already_flattened_bmk_v1")
        document["rates"][0]["key"]["operation"] = "fc"
        fc = ComputeRuntime(lower(document, graph)).run()
        self.assertEqual(fc.work, matmul.work)
        self.assertEqual(fc.stages, matmul.stages)
        self.assertEqual(fc.memory_session.chunks, matmul.memory_session.chunks)
        self.assertNotEqual(fc.execution_plan_sha256, matmul.execution_plan_sha256)

    def test_storage_padding_and_service_rounding_have_separate_byte_totals(self):
        document, graph = compute_documents()
        document["memory"]["packet"]["address_alignment_bytes"] = 32
        for resource in document["memory"]["resources"]:
            resource["service"]["service_granule_bytes"] = 32
        rounded = ComputeRuntime(lower(document, graph)).run(max_aci_cycles=1000)
        # Dense useful/storage A/B/C remain 60/70/84. Each access is rounded
        # to 64/96/96 by the memory server, independently of packet padding.
        accounting = rounded.memory_session.accounting
        self.assertEqual((accounting.completed_read_useful_bytes, accounting.completed_read_service_bytes), (344, 416))
        self.assertEqual((accounting.completed_write_useful_bytes, accounting.completed_write_service_bytes), (298, 352))
        self.assertEqual(accounting.injected_wire.padding_bytes, 42)
        layout = {"kind": "tiled", "tile_rows": 2, "tile_columns": 4}
        job = document["streams"][0]["jobs"][0]
        for name in ("a", "b", "c"):
            job["operation"][name]["layout"] = layout
        document["rates"][0]["key"]["layout"] = layout
        job["a"]["source"]["size_bytes"] = 128
        job["b"]["source"]["size_bytes"] = 96
        job["output"]["destination"]["size_bytes"] = 128
        tiled = ComputeRuntime(lower(document, graph)).run(max_aci_cycles=1000)
        self.assert_conserved(tiled)
        cost = tiled.plan.jobs[0].cost
        self.assertEqual([(t.useful_bytes, t.storage_bytes) for t in (cost.a, cost.b, cost.c)], [(60, 128), (70, 96), (84, 128)])
        # Storage is now 352 bytes: eleven data flits + six headers, no packet
        # padding. Three accesses to each tensor sum to 1056 service bytes.
        self.assertEqual((tiled.memory_session.packet_bytes, tiled.memory_session.memory_service_bytes), (544, 1056))
        self.assertEqual(tiled.memory_session.accounting.injected_wire.padding_bytes, 0)
        self.assertEqual((tiled.work.completed_executed_work, tiled.work.math_busy_aci_cycles), (1024, 5))

    def test_short_runs_preserve_context_versions_and_resume_exactly(self):
        plan = tiny_plan()
        runtime = ComputeRuntime(plan)
        initial = runtime.snapshot()
        self.assertEqual(initial.work.completed_executed_work, 0)
        self.assertFalse(initial.stages)
        # Two unit operand reads, three math cycles, one result write => 6.
        for horizon, completed, busy in ((1.5, 0, 0), (2.5, 0, 0.5), (5.5, 2, 3)):
            result = runtime.run(max_aci_cycles=horizon)
            self.assert_conserved(result, complete=False)
            self.assertEqual(result.elapsed_aci_cycles, horizon)
            self.assertEqual(result.work.completed_executed_work, completed)
            self.assertEqual(result.work.math_busy_aci_cycles, busy)
            self.assertEqual(result.work.context_occupied_aci_cycles, horizon)
            self.assertEqual(next(r for r in result.resources if r.kind == "compute").occupied, 1)
            self.assertEqual(result.slots[0].occupied, 1)
            owner = runtime.memory.memory.resources["l1-a"]
            self.assertFalse(owner.is_ready(runtime.memory.memory.handles["local"], offset_bytes=256, size_bytes=1,
                                            version=MemoryVersion(kind="producer", producer_id=plan.jobs[0].result_operation)))
            with self.assertRaisesRegex(ValueError, "drain"):
                runtime.finalize()
        final = runtime.run(max_aci_cycles=6)
        self.assert_conserved(final)
        self.assertEqual(final, ComputeRuntime(plan).run())
        self.assertEqual(Counter(e.action for e in final.stages)["math_end"], 1)

    def test_remote_resumption_preserves_all_traces_and_exactly_once_teardown(self):
        plan = lower(*compute_documents())
        reference = ComputeRuntime(plan).run()
        runtime = ComputeRuntime(plan)
        for horizon in (1.5, 20.125, 50.5, 56.5, 80.25, 90.875):
            partial = runtime.run(max_aci_cycles=horizon)
            self.assert_conserved(partial, complete=False)
        result = runtime.run(max_aci_cycles=1000)
        self.assertEqual(result, reference)
        before = tuple(r.events for r in runtime.memory.memory.resources.values())
        self.assertIs(runtime.snapshot(), result)
        self.assertIs(runtime.finalize(), result)
        self.assertIs(runtime.run(), result)
        self.assertEqual(before, tuple(r.events for r in runtime.memory.memory.resources.values()))

    def test_posted_effects_and_delayed_credits_prevent_early_success(self):
        plan = tiny_plan(remote=True, writer="write_posted", credit=30)
        reference_runtime = ComputeRuntime(plan)
        reference = reference_runtime.run(max_aci_cycles=10000)
        runtime = ComputeRuntime(plan)
        partial = runtime.run(max_aci_cycles=times(reference)["writer_complete"])
        self.assert_conserved(partial, complete=False)
        self.assertEqual(partial.completed_job_ids, ("job0",))
        self.assertEqual(partial.slots[0].occupied, 0)
        self.assertTrue(all(r.occupied == 0 for r in partial.resources))
        self.assertTrue(any(r.pending_returns for r in partial.memory_session.transport.resources))
        self.assertTrue(all(r.reserved_bytes for r in partial.memory_session.memory_resources))
        with self.assertRaises(ValueError):
            runtime.finalize()
        self.assertEqual(runtime.run(max_aci_cycles=10000), reference)

    def test_advance_separates_drain_from_finalization(self):
        runtime = ComputeRuntime(tiny_plan())
        result = runtime.advance(max_aci_cycles=100)
        self.assertEqual((result.status, result.reason, result.elapsed_aci_cycles), ("incomplete", "awaiting_finalization", 6))
        self.assertTrue(runtime.is_drained)
        self.assertFalse(result.teardown_complete)
        self.assertTrue(all(r.reserved_bytes for r in result.memory_session.memory_resources))
        final = runtime.finalize()
        self.assertEqual(final.elapsed_aci_cycles, 6)
        self.assert_conserved(final)

    def test_idle_pending_external_lease_is_not_success(self):
        runtime = ComputeRuntime(tiny_plan())
        owner = runtime.memory.memory.resources["l1-a"]
        lease = owner.try_acquire(runtime.memory.memory.handles["local"], MemoryAccess(
            client_id="external", direction="read", offset_bytes=4096, size_bytes=1, version=MemoryVersion(kind="initial")))
        result = runtime.run()
        self.assertEqual((result.status, result.reason), ("incomplete", "idle_with_pending"))
        self.assertEqual(result.completed_job_ids, ("job0",))
        self.assertTrue(any("access" in p for p in result.pending))
        with self.assertRaises(ValueError):
            runtime.finalize()
        owner.release_access(lease)
        self.assert_conserved(runtime.run())

    def test_subquantum_math_stays_positive_and_failed_math_cannot_publish(self):
        document, graph = tiny_documents()
        document["rates"][0].update(work_per_native_cycle=1e20, quantum_native_cycles=0.125,
                                     minimum_native_cycles=0.125, setup_native_cycles=0)
        plan = tiny_lower(document, graph)
        result = ComputeRuntime(plan).run()
        self.assertEqual((times(result)["math_end"] - times(result)["math_start"], result.elapsed_aci_cycles), (0.125, 3.125))
        runtime = ComputeRuntime(plan)
        with patch("simulator_detailed.compute_runtime.checked_compute_finish", side_effect=ValueError("lost clock advancement")), \
                self.assertRaisesRegex(ValueError, "clock advancement"):
            runtime.run()
        failed = runtime.snapshot()
        self.assertEqual(failed.work.completed_executed_work, 0)
        self.assertNotIn("output_ready", times(failed))
        self.assertEqual(failed.slots[0].occupied, 1)
        with self.assertRaises(ValueError):
            runtime.finalize()

    def test_runtime_admission_horizons_and_result_integrity(self):
        multiple = lower(*compute_documents(jobs=2, slots=2, local=True))
        single = tiny_plan()
        with patch("simpy.Environment", side_effect=AssertionError("allocated")):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                ComputeRuntime(multiple)
            with self.assertRaises(ValueError):
                ComputeRuntime(replace(single, plan_sha256="0" * 64))
        runtime = ComputeRuntime(single)
        before = runtime.snapshot()
        for horizon in (0, -1, True, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                runtime.advance(max_aci_cycles=horizon)
            self.assertEqual(runtime.snapshot(), before)
        result = runtime.run()
        document = result.model_dump(mode="json")
        for key, value in (("completed_job_ids", []), ("status", "incomplete"), ("pending", ["unretired"]),
                           ("execution", "kernel"), ("silicon_timing", "validated"), ("teardown_complete", 1)):
            with self.assertRaises(ValueError):
                ComputeExecutionResult.model_validate({**document, key: value})
        for index, field, value in ((6, "action", "math_end"), (0, "time_aci_cycles", 10),
                                    (0, "worker_tile_id", "t1_0"), (0, "generation", 1)):
            invalid = copy.deepcopy(document)
            invalid["stages"][index][field] = value
            with self.assertRaises(ValueError):
                ComputeExecutionResult.model_validate(invalid)
        false_work = copy.deepcopy(document)
        false_work["work"]["completed_executed_work"] = 0
        with self.assertRaises(ValueError):
            ComputeExecutionResult.model_validate(false_work)


if __name__ == "__main__":
    unittest.main()
