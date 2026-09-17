"""Independent pipeline oracles and bounded shared-resource execution checks."""

from __future__ import annotations

import copy
import unittest
from collections import defaultdict
from itertools import pairwise

import simpy
from pydantic import ValidationError

from simulator_detailed.compute_memory import compute_memory_id
from simulator_detailed.compute_pipeline import BoundedPipeline, StagePool
from simulator_detailed.compute_runtime import (
    ComputeExecutionResult,
    ComputeOverlapRuntime,
    ComputeRuntime,
)
from simulator_detailed.tests import test_compute_runtime
from simulator_detailed.tests.test_compute_buffers import lower
from simulator_detailed.tests.test_compute_contracts import addressed, compute_documents
from simulator_detailed.tests.test_compute_runtime import tiny_documents, tiny_lower


def streaming(*, jobs=3, slots=2, remote=False, writer="local", far=False):
    document, graph = tiny_documents(remote=remote, writer=writer, far=far)
    stream = document["streams"][0]
    original_slot, original_job = stream["slots"][0], stream["jobs"][0]
    stream["slots"], stream["jobs"] = [], []
    for i in range(slots):
        slot = copy.deepcopy(original_slot)
        slot["slot_id"] = f"slot{i}"
        for extent in (slot["a"], slot["b"], slot["c"]):
            extent["offset_bytes"] += 512 * i
        stream["slots"].append(slot)
    for i in range(jobs):
        job = copy.deepcopy(original_job)
        job["job_id"] = f"job{i}"
        for name in ("a", "b"):
            job[name]["source"]["offset_bytes"] = (8192 if job[name]["mode"] == "remote" else
                                                    stream["slots"][i % slots][name]["offset_bytes"])
        job["output"]["destination"]["offset_bytes"] = (stream["slots"][i % slots]["c"]["offset_bytes"]
                                                          if writer == "local" else 12288 + i)
        stream["jobs"].append(job)
    return document, graph


def two_streams(*, independent=False, remote=False, separate_dram=False):
    document, graph = streaming(jobs=1, slots=1, remote=remote, far=remote)
    stream = copy.deepcopy(document["streams"][0])
    stream["stream_id"] = "other_stream"
    stream["slots"][0]["slot_id"] = "other_slot"
    job = stream["jobs"][0]
    job["job_id"] = "other_job"
    for extent in (stream["slots"][0]["a"], stream["slots"][0]["b"], stream["slots"][0]["c"],
                   job["b"]["source"], job["output"]["destination"]):
        extent["offset_bytes"] += 4096
    if remote:
        job["a"]["fabric_id"] = 1
    else:
        job["a"]["source"]["offset_bytes"] += 4096
    document["streams"].append(stream)
    if independent:
        graph["resources"].append({"resource_id": "l1-b", "kind": "local_sram", "owner_tile_id": "t1_0",
                                   "capacity_bytes": 65536})
        for fabric in (0, 1):
            for records in (graph["attachments"], document["memory"]["endpoints"]):
                endpoint = copy.deepcopy(next(e for e in records if e["endpoint_id"] == f"src{fabric}"))
                endpoint.update(endpoint_id=f"other{fabric}", router_id="t1_0", resource_ids=["l1-b"])
                records.append(endpoint)
        resource = copy.deepcopy(next(r for r in document["memory"]["resources"] if r["resource_id"] == "l1-a"))
        resource["resource_id"] = "l1-b"
        document["memory"]["resources"].append(resource)
        document["memory"]["buffers"].append({"buffer_id": "other_local", "resource_id": "l1-b", "base_address": 0,
                                                 "size_bytes": 16384, "initially_ready": True})
        document["workers"].append({**copy.deepcopy(document["workers"][0]), "tile_id": "t1_0",
                                    "l1_resource_id": "l1-b", "endpoint_ids": ["other0", "other1"]})
        stream["worker_tile_id"] = "t1_0"
        for extent in (stream["slots"][0]["a"], stream["slots"][0]["b"], stream["slots"][0]["c"],
                       job["a"]["source"], job["b"]["source"], job["output"]["destination"]):
            if extent["buffer_id"] == "local":
                extent["buffer_id"] = "other_local"
    if separate_dram:
        graph["resources"].append({"resource_id": "dram-b", "kind": "dram", "capacity_bytes": 65536})
        for records in (graph["attachments"], document["memory"]["endpoints"]):
            for endpoint in records:
                if endpoint["endpoint_id"].startswith("ram"):
                    endpoint["resource_ids"] = [*endpoint["resource_ids"], "dram-b"]
        resource = copy.deepcopy(next(r for r in document["memory"]["resources"] if r["resource_id"] == "dram"))
        resource["resource_id"] = "dram-b"
        document["memory"]["resources"].append(resource)
        document["memory"]["buffers"].append({"buffer_id": "other_remote", "resource_id": "dram-b", "base_address": 0,
                                                 "size_bytes": 16384, "initially_ready": True})
        job["a"]["source"]["buffer_id"] = "other_remote"
    return document, graph


def job_times(result):
    times = defaultdict(dict)
    for event in result.stages:
        times[event.job_id][event.action] = event.time_aci_cycles
    return times


class ConstantStages:
    """Synthetic service only: production scheduler, independently known durations."""

    def __init__(self, env, depth):
        self.env = env
        self.slots = simpy.Resource(env, capacity=depth)
        self.requests = {}
        self.events = []
        self.peak = 0

    def reserve(self, job):
        request = self.slots.request()
        yield request
        self.requests[job] = request
        self.peak = max(self.peak, len(self.requests))

    def eligible(self, job, kind):
        yield self.env.timeout(0)

    def serve(self, job, kind):
        start = self.env.now
        yield self.env.timeout({"reader": 2, "compute": 3, "writer": 2}[kind])
        self.events.append((job, kind, start, self.env.now))

    def release(self, job):
        yield self.slots.release(self.requests.pop(job))


class ComputeOverlapTests(unittest.TestCase):
    def assert_conserved(self, result, *, complete=True):
        test_compute_runtime.ComputeRuntimeTests().assert_conserved(result, complete=complete)
        active, generations = {}, defaultdict(int)
        for event in result.slot_events:
            key = event.stream_id, event.slot_id
            if event.action == "wait":
                continue
            if event.action == "reserve":
                self.assertNotIn(key, active)
                self.assertEqual(event.generation, generations[key])
                active[key] = event.job_id
            else:
                self.assertEqual(active[key], event.job_id)
                if event.action == "release":
                    del active[key]
                    generations[key] += 1
            self.assertEqual(event.free + event.occupied, event.capacity)
            self.assertEqual(event.occupied, sum(stream == event.stream_id for stream, _ in active))
        self.assertEqual(len(active), sum(pool.occupied for pool in result.slots))
        times = job_times(result)
        kinds = {"reader": ("reader_start", "inputs_ready"), "compute": ("operand_start", "output_ready"),
                 "writer": ("writer_start", "writer_complete")}
        for event in result.resource_events:
            action = kinds[event.kind][event.action == "release"]
            self.assertEqual(event.time_aci_cycles, times[event.job_id][action])
        operation_stages = {}
        for job in result.plan.jobs:
            operation_stages.update({compute_memory_id(job.job_id, name): (job.job_id, action)
                                     for name, action in (("read_a", "reader_start"), ("read_b", "reader_start"),
                                                          ("operand_a", "operand_start"), ("operand_b", "operand_start"),
                                                          ("result", "math_end"), ("write", "writer_start"))})
        for event in result.memory_session.lifecycle:
            if event.action == "submission":
                job, action = operation_stages[event.operation_id]
                self.assertGreaterEqual(event.time_aci_cycles, times[job][action])
        by_resource = defaultdict(list)
        for chunk in result.memory_session.chunks:
            by_resource[chunk.resource_id].append(chunk)
        for chunks in by_resource.values():
            for first, second in pairwise(sorted(chunks, key=lambda c: c.start_aci_cycles)):
                self.assertLessEqual(first.end_aci_cycles, second.start_aci_cycles)

    def test_independent_constant_service_depth_oracle(self):
        expected = {
            1: [(0, 2, 2, 5, 5, 7), (7, 9, 9, 12, 12, 14), (14, 16, 16, 19, 19, 21)],
            2: [(0, 2, 2, 5, 5, 7), (2, 4, 5, 8, 8, 10), (7, 9, 9, 12, 12, 14)],
        }
        for depth, timeline in expected.items():
            env, events = simpy.Environment(), []
            stages = ConstantStages(env, depth)
            pools = {("worker", kind): StagePool(env, "worker", kind, 1, events)
                     for kind in ("reader", "compute", "writer")}
            pipeline = BoundedPipeline(env, (("a", "b", "c"),), dict.fromkeys(("a", "b", "c"), "worker"), pools, stages)
            env.run()
            self.assertTrue(pipeline.done.triggered)
            self.assertEqual(env.now, 21 if depth == 1 else 14)
            for job, expected_times in zip(("a", "b", "c"), timeline, strict=True):
                self.assertEqual(tuple(t for item, _, start, end in stages.events if item == job for t in (start, end)),
                                 expected_times)
            self.assertEqual(stages.peak, depth)
            self.assertFalse(stages.requests)
            self.assertTrue(all(not pool.owners and pool.peak == 1 for pool in pools.values()))

    def test_integrated_same_router_read_and_local_compute_oracle(self):
        # Read: request 0..3, source 3..4, header 3..6, data 5..8,
        # destination 8..9. Compute: operands 9..11, math 11..14,
        # result 14..15. With two slots, next request starts at 9;
        # its source 12..13 overlaps math, and destination 17..18
        # follows the first result. R=9, C=6, W=0; no L1 collision.
        for depth, starts, end in ((1, (0, 15, 30), 45), (2, (0, 9, 18), 33)):
            result = ComputeOverlapRuntime(tiny_lower(*streaming(slots=depth, remote=True))).run()
            self.assert_conserved(result)
            self.assertEqual(result.elapsed_aci_cycles, end)
            for job, start in zip(("job0", "job1", "job2"), starts, strict=True):
                t = job_times(result)[job]
                self.assertEqual((t["reader_start"], t["inputs_ready"], t["operand_end"], t["math_end"],
                                  t["output_ready"], t["writer_complete"]),
                                 (start, start + 9, start + 11, start + 14, start + 15, start + 15))
            self.assertEqual(result.memory_session.memory_service_bytes, 15)
            self.assertEqual((result.work.math_busy_aci_cycles, result.work.context_occupied_aci_cycles), (9, 18))
            self.assertEqual(result.work.completed_executed_work, 6)

    def test_single_job_path_retains_full_execution_evidence(self):
        for remote, writer in ((False, "local"), (True, "write_posted"), (True, "write_acknowledged")):
            plan = tiny_lower(*streaming(jobs=1, slots=1, remote=remote, writer=writer))
            serial, overlap = ComputeRuntime(plan).run(), ComputeOverlapRuntime(plan).run()
            self.assertEqual(serial.model_dump(exclude={"execution"}), overlap.model_dump(exclude={"execution"}))

    def test_shared_engine_serialization_and_independent_workers(self):
        for independent, expected in ((False, 12), (True, 6)):
            result = ComputeOverlapRuntime(tiny_lower(*two_streams(independent=independent))).run()
            self.assert_conserved(result)
            self.assertEqual(result.elapsed_aci_cycles, expected)
            times = job_times(result)
            self.assertEqual(times["other_job"]["operand_start"], 0 if independent else 6)
            self.assertEqual(result.work.math_busy_aci_cycles, 6)
            self.assertEqual(result.work.context_occupied_aci_cycles, 12)
        # More software streams did not change the one declared engine's rate.
        self.assertEqual(result.work.planned_executed_work, 4)

    def test_two_declared_engines_still_share_one_l1_server(self):
        document, graph = two_streams()
        document["workers"][0]["compute_contexts"] = 2
        result = ComputeOverlapRuntime(tiny_lower(document, graph)).run()
        self.assert_conserved(result)
        # Both contexts start at zero, but four operand bytes serialize on L1.
        # Each compute gate admits both operands: A0,B0,A1,B1 at 0..4;
        # math at 2..5 and 4..7, then results at 5..6 and 7..8.
        # Two independent L1s instead take 6.
        self.assertEqual(result.elapsed_aci_cycles, 8)
        self.assertEqual(sorted(t["math_start"] for t in job_times(result).values()), [2, 4])
        self.assertEqual(next(r.peak_occupied for r in result.resources if r.kind == "compute"), 2)
        self.assertEqual((result.work.math_busy_aci_cycles, result.work.context_occupied_aci_cycles), (6, 14))
        self.assertEqual(result.memory_session.memory_service_bytes, 6)

    def test_dram_aliases_across_fabrics_share_service_until_explicitly_separated(self):
        results = []
        for separate in (False, True):
            document, graph = two_streams(independent=True, remote=True, separate_dram=separate)
            for resource in document["memory"]["resources"]:
                if resource["resource_id"].startswith("dram"):
                    resource["service"]["bytes_per_cycle"] = 1 / 32
            result = ComputeOverlapRuntime(tiny_lower(document, graph)).run(max_aci_cycles=1000)
            self.assert_conserved(result)
            chunks = [c for c in result.memory_session.chunks if c.resource_id.startswith("dram")]
            self.assertEqual(sum(c.end_aci_cycles - c.start_aci_cycles for c in chunks), 64)
            self.assertEqual({c.resource_id for c in chunks}, {"dram", "dram-b"} if separate else {"dram"})
            first, second = sorted(chunks, key=lambda c: c.start_aci_cycles)
            self.assertEqual(second.start_aci_cycles < first.end_aci_cycles, separate)
            self.assertEqual({p.definition.route.fabric_id for p in result.memory_session.wire_packets}, {0, 1})
            results.append(result)
        self.assertLess(results[1].elapsed_aci_cycles, results[0].elapsed_aci_cycles)

    def test_shared_worker_contexts_are_not_duplicated_by_fabric(self):
        document, graph = two_streams(remote=True)
        document["rates"][0]["setup_native_cycles"] = 100
        result = ComputeOverlapRuntime(tiny_lower(document, graph)).run(max_aci_cycles=1000)
        self.assert_conserved(result)
        contexts = [r for r in result.resources if r.kind == "compute"]
        self.assertEqual([(r.capacity, r.peak_occupied) for r in contexts], [(1, 1)])
        spans = sorted((t["math_start"], t["math_end"]) for t in job_times(result).values())
        self.assertLessEqual(spans[0][1], spans[1][0])
        self.assertEqual(result.work.math_busy_aci_cycles, 204)

    def test_more_jobs_than_slots_minimum_capacities_and_slow_stages(self):
        for writer in ("write_posted", "write_acknowledged"):
            for slow_stage in ("reader", "writer"):
                for depth in (1, 2):
                    with self.subTest(writer=writer, slow_stage=slow_stage, depth=depth):
                        document, graph = streaming(jobs=12, slots=depth, remote=slow_stage == "reader",
                                                    writer=writer, far=True)
                        document["memory"]["max_outstanding_segments"] = 1
                        for resource in document["memory"]["resources"]:
                            resource["service"]["queue_capacity"] = 1
                            if resource["resource_id"] == "dram":
                                resource["service"]["bytes_per_cycle"] = 0.125
                        if slow_stage == "writer":
                            for job in document["streams"][0]["jobs"]:
                                job["output"]["destination"]["buffer_id"] = "remote"
                        runtime = ComputeOverlapRuntime(tiny_lower(document, graph, credit=3))
                        partial = runtime.advance(max_aci_cycles=17)
                        self.assert_conserved(partial, complete=False)
                        self.assertFalse(partial.teardown_complete)
                        result = runtime.run(max_aci_cycles=10000)
                        self.assert_conserved(result)
                        self.assertEqual(len(result.completed_job_ids), 12)
                        self.assertLessEqual(result.slots[0].peak_occupied, depth)
                        self.assertEqual(sum(s.generation for s in result.slots[0].slots), 12)
                        self.assertEqual(result.work.completed_executed_work, 24)
                        self.assertIs(runtime.finalize(), result)

    def test_split_horizons_preserve_all_traces_and_partial_work(self):
        for writer in ("local", "write_posted", "write_acknowledged"):
            plan = tiny_lower(*streaming(jobs=5, remote=True, writer=writer), credit=2)
            expected = ComputeOverlapRuntime(plan).run(max_aci_cycles=1000)
            runtime = ComputeOverlapRuntime(plan)
            for horizon in (1, 10, 14, 20, 40):
                partial = runtime.advance(max_aci_cycles=horizon)
                self.assert_conserved(partial, complete=False)
            self.assertEqual(runtime.run(max_aci_cycles=1000), expected)
        runtime = ComputeOverlapRuntime(tiny_lower(*two_streams(independent=True)))
        partial = runtime.advance(max_aci_cycles=3)
        self.assertEqual((partial.work.completed_executed_work, partial.work.math_busy_aci_cycles,
                          partial.work.context_occupied_aci_cycles), (0, 2, 6))

    def test_local_result_consumer_releases_producer_after_writer_grant(self):
        document, graph = compute_documents(jobs=3, slots=2)
        first, consumer, _ = document["streams"][0]["jobs"]
        first["output"] = {"mode": "local", "destination": addressed("local", 256, 84)}
        consumer["a"].update(source=addressed("local", 256, 84), version={"kind": "job", "producer_job_id": "job0"})
        consumer["operation"]["a"]["shape"] = [2, 3, 7]
        consumer["operation"]["b"]["shape"] = [7, 7]
        consumer["b"]["source"]["size_bytes"] = 98
        consumer["output"]["destination"]["offset_bytes"] = 512
        runtime = ComputeOverlapRuntime(lower(document, graph))
        result = runtime.run(max_aci_cycles=10000)
        self.assert_conserved(result)
        times = job_times(result)
        consumed = runtime.memory.lifecycle_time(runtime.plan.jobs[1].reader_operations[0], "complete")
        self.assertEqual(times["job0"]["slot_release"], consumed)
        self.assertLess(times["job0"]["writer_complete"], times["job0"]["slot_release"])
        self.assertGreaterEqual(times["job2"]["reader_start"], times["job0"]["slot_release"])

    def test_earlier_stream_waits_for_later_stream_without_holding_a_context(self):
        document, graph = two_streams()
        document["streams"][0]["jobs"][0]["depends_on"] = ["other_job"]
        result = ComputeOverlapRuntime(tiny_lower(document, graph)).run()
        self.assert_conserved(result)
        self.assertEqual(result.completed_job_ids, ("other_job", "job0"))
        self.assertEqual(job_times(result)["job0"]["reader_start"], 6)
        self.assertEqual(result.resource_events[0].job_id, "other_job")

    def test_overlap_results_reject_false_completion_and_noncausal_stages(self):
        result = ComputeOverlapRuntime(tiny_lower(*streaming())).run()
        for field, value in (("completed_job_ids", ["job0"]), ("stages", result.model_dump()["stages"][1:]),
                             ("execution", "single_job_v1")):
            invalid = result.model_dump()
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ComputeExecutionResult.model_validate(invalid)

    def test_reader_grants_bound_activation_and_writer_capacity_is_configurable(self):
        for capacity in (1, 2):
            document, graph = two_streams(remote=True)
            document["workers"][0]["reader_capacity"] = capacity
            runtime = ComputeOverlapRuntime(tiny_lower(document, graph))
            partial = runtime.advance(max_aci_cycles=1)
            self.assert_conserved(partial, complete=False)
            self.assertEqual(sum(r.occupied for r in partial.resources if r.kind == "reader"), capacity)
            submitted = [o for o in partial.memory_session.operations if o.kind == "read"
                         and o.submission_aci_cycles is not None]
            self.assertEqual(len(submitted), capacity)
            if capacity == 1:
                self.assertIsNone(runtime.memory.gate_time(runtime.plan.jobs[1].gate_ids[0]))
                self.assertEqual(job_times(partial)["other_job"], {"slot_wait": 0})
            self.assert_conserved(runtime.run(max_aci_cycles=1000))

            document, graph = two_streams()
            document["workers"][0].update(compute_contexts=2, writer_capacity=capacity)
            for index, stream in enumerate(document["streams"]):
                stream["jobs"][0]["output"].update(mode="write_acknowledged", fabric_id=index,
                                                      destination=addressed("remote", 1024 + index, 1))
            result = ComputeOverlapRuntime(tiny_lower(document, graph)).run(max_aci_cycles=1000)
            self.assert_conserved(result)
            self.assertEqual(next(r.peak_occupied for r in result.resources if r.kind == "writer"), capacity)

    def test_posted_jobs_and_slots_complete_before_all_effects_drain(self):
        plan = tiny_lower(*streaming(writer="write_posted", remote=True))
        final = ComputeOverlapRuntime(plan).run(max_aci_cycles=1000)
        completion = max(t["writer_complete"] for t in job_times(final).values())
        self.assertLess(completion, final.elapsed_aci_cycles)
        runtime = ComputeOverlapRuntime(plan)
        partial = runtime.run(max_aci_cycles=completion)
        self.assert_conserved(partial, complete=False)
        self.assertEqual(len(partial.completed_job_ids), 3)
        self.assertTrue(runtime.buffers.is_drained)
        self.assertTrue(all(r.occupied == 0 for r in partial.resources))
        self.assertTrue(partial.pending)
        self.assertFalse(any("job_completion" in pending or "slot_release" in pending for pending in partial.pending))
        self.assertTrue(any(op.kind == "write_posted" and op.destination_ready_aci_cycles is None
                            for op in partial.memory_session.operations))
        with self.assertRaisesRegex(ValueError, "drain"):
            runtime.finalize()
        self.assertEqual(runtime.run(max_aci_cycles=1000), final)
