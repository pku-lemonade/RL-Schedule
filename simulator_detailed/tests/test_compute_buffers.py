"""Generation, capacity and memory-effect boundaries without a compute scheduler."""

from __future__ import annotations

import copy
import unittest
from collections import Counter, deque
from dataclasses import replace
from unittest.mock import patch

import simpy

from simulator_detailed.compute_buffers import (
    ComputeBuffers,
    ComputeSlotEvent,
    ComputeSlotPoolState,
)
from simulator_detailed.compute_memory import ComputeMemoryPlan
from simulator_detailed.memory_execution import MemoryRuntimeConfig
from simulator_detailed.memory_resources import MemoryAccess, MemoryVersion
from simulator_detailed.memory_runtime import MemorySession
from simulator_detailed.memory_session import MemoryOwnerComponentToken
from simulator_detailed.tests.test_compute_contracts import (
    addressed,
    compile_document,
    compute_documents,
)
from simulator_detailed.tests.test_memory_contracts import EVIDENCE
from simulator_detailed.tests.test_packet_runtime import transport_config


def lower(document, graph, *, credit=1):
    transport = transport_config(credit=credit, staging=2).model_copy(update={"endpoint_queue_capacity_packets": 2})
    settings = MemoryRuntimeConfig(transport=transport, responder_capacity_packets=1,
                                   request_control_aci_cycles=1, response_control_aci_cycles=1, evidence=EVIDENCE)
    return ComputeMemoryPlan.compile(compile_document(document, graph), settings)


def setup(*, jobs=1, slots=1, local=True, document=None, graph=None, credit=1):
    if document is None:
        document, graph = compute_documents(jobs=jobs, slots=slots, local=local)
    plan = lower(document, graph, credit=credit)
    session = MemorySession(simpy.Environment(), plan.session)
    return plan, session, ComputeBuffers(plan, session)


def prepare_inputs(plan, session, buffers, token):
    job = next(j for j in plan.jobs if j.job_id == token.job_id)
    for operation in job.reader_operations:
        yield session.lifecycle_event(operation, "complete")
    buffers.publish_inputs(token)


def finish_job(plan, session, buffers, token):
    """Test controller: explicit synthetic math delay, not a production executor."""
    job = next(j for j in plan.jobs if j.job_id == token.job_id)
    buffers.consume(token)
    for operation in job.operand_operations:
        yield session.lifecycle_event(operation, "complete")
    yield session.env.timeout(5)
    session.activate(session.gate(job.gate_ids[3]))
    yield session.lifecycle_event(job.result_operation, "complete")
    buffers.publish_output(token)
    buffers.begin_drain(token)
    if job.writer_operation is not None:
        yield session.lifecycle_event(job.writer_operation, "complete")
    buffers.finish_writer(token)


class ComputeBufferTests(unittest.TestCase):
    def test_binding_reuses_reservations_and_excludes_duplicate_ledgers(self):
        document, graph = compute_documents(jobs=3, slots=2)
        plan = lower(document, graph)
        session = MemorySession(simpy.Environment(), plan.session)
        before = {key: (r.snapshot(), r.events) for key, r in session.memory.resources.items()}
        with patch("simpy.Container", side_effect=AssertionError("shadow capacity")), \
                patch("simpy.Resource", side_effect=AssertionError("shadow server")):
            buffers = ComputeBuffers(plan, session)
        self.assertEqual({key: (r.snapshot(), r.events) for key, r in session.memory.resources.items()}, before)
        self.assertEqual(buffers.snapshot()[0].footprint_bytes, 768)
        self.assertEqual(buffers.snapshot()[0].free, 2)
        with self.assertRaisesRegex(ValueError, "unique"):
            ComputeBuffers(plan, session)
        altered = copy.deepcopy(document)
        altered["rates"][0]["setup_native_cycles"] += 1
        with self.assertRaisesRegex(ValueError, "matching"):
            ComputeBuffers(lower(altered, graph), session)
        self.assertEqual(Counter(e.action for r in session.memory.resources.values() for e in r.events), {"reserve": 2})
        self.assertFalse(buffers.is_drained)

    def test_strict_tokens_stages_and_owner_completion(self):
        plan, session, buffers = setup()
        env = session.env
        with self.assertRaises(ValueError):
            buffers.try_reserve("missing")
        token = buffers.try_reserve("job0")
        with self.assertRaisesRegex(ValueError, "unreserved FIFO"):
            buffers.try_reserve("job0")
        before = buffers.snapshot(), buffers.events
        for action in (lambda: buffers.publish_inputs(replace(token)), lambda: buffers.consume(token),
                       lambda: buffers.publish_output(token), lambda: buffers.begin_drain(token), lambda: buffers.release(token)):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual((buffers.snapshot(), buffers.events), before)
        buffers.publish_inputs(token)
        with self.assertRaises(ValueError):
            buffers.publish_inputs(token)
        env.run(until=env.process(finish_job(plan, session, buffers, token)))
        self.assertTrue(session.is_drained)
        with self.assertRaisesRegex(ValueError, "pending components"):
            session.complete_owner(session.owner)
        self.assertTrue(any("owner_component" in pending for pending in session.snapshot().pending))
        with self.assertRaises(ValueError):
            session.finalize(session.owner)
        with self.assertRaises(ValueError):
            session.complete_component(MemoryOwnerComponentToken("fifo_item_slots_v1"))
        buffers.release(token)
        self.assertTrue(buffers.is_drained)
        with self.assertRaisesRegex(ValueError, "stale"):
            buffers.release(token)
        session.complete_owner(session.owner)
        self.assertTrue(session.finalize(session.owner).teardown_complete)

    def test_bounded_fifo_backpressure_and_many_generations(self):
        plan, session, buffers = setup(jobs=24, slots=2)
        env = session.env
        before = {key: r.available_bytes for key, r in session.memory.resources.items()}
        with self.assertRaisesRegex(ValueError, "FIFO"):
            buffers.try_reserve("job1")
        tokens = deque()
        first = buffers.try_reserve("job0")
        buffers.publish_inputs(first)
        tokens.append(first)
        second = buffers.try_reserve("job1")
        buffers.publish_inputs(second)
        tokens.append(second)
        for index in range(2, 24):
            self.assertIsNone(buffers.try_reserve(f"job{index}"))
            state = buffers.snapshot()[0]
            self.assertEqual((state.free, state.occupied, state.waiting_job_id), (0, 2, f"job{index}"))
            events = buffers.events
            self.assertIsNone(buffers.try_reserve(f"job{index}"))
            self.assertEqual(buffers.events, events)  # Retry creates no hidden waiter or payload.
            changed = buffers.changed
            token = tokens.popleft()
            env.run(until=env.process(finish_job(plan, session, buffers, token)))
            buffers.release(token)
            self.assertTrue(changed.triggered)
            new = buffers.try_reserve(f"job{index}")
            buffers.publish_inputs(new)
            tokens.append(new)
            self.assertEqual({key: r.available_bytes for key, r in session.memory.resources.items()}, before)
        for token in tokens:
            env.run(until=env.process(finish_job(plan, session, buffers, token)))
            buffers.release(token)
        state = buffers.snapshot()[0]
        self.assertEqual((state.free, state.occupied, state.peak_occupied), (2, 0, 2))
        self.assertEqual([s.generation for s in state.slots], [12, 12])
        self.assertEqual(state, ComputeSlotPoolState.model_validate_json(state.model_dump_json()))
        live = set()
        for event in buffers.events:
            key = event.slot_id, event.generation, event.job_id
            if event.action == "reserve":
                self.assertNotIn(key, live)
                live.add(key)
            elif event.action == "release":
                live.remove(key)
            self.assertEqual(len(live), event.occupied)
            self.assertEqual(event.free + event.occupied, 2)
            self.assertEqual(event, ComputeSlotEvent.model_validate_json(event.model_dump_json()))
        self.assertFalse(live)
        self.assertEqual(sum(e.action == "release" for e in buffers.events), 24)
        self.assertEqual(sum(e.action == "reserve" for r in session.memory.resources.values() for e in r.events), 2)

    def test_remote_versions_invalidate_on_reuse_and_stale_output_cannot_publish(self):
        plan, session, buffers = setup(jobs=2, slots=1, local=False)
        env = session.env
        first = buffers.try_reserve("job0")
        with self.assertRaisesRegex(ValueError, "ready memory versions"):
            buffers.publish_inputs(first)
        env.run(until=env.process(prepare_inputs(plan, session, buffers, first)))
        env.run(until=env.process(finish_job(plan, session, buffers, first)))
        buffers.release(first)
        second = buffers.try_reserve("job1")
        env.run(until=env.process(prepare_inputs(plan, session, buffers, second)))
        local = session.memory.resources["l1-a"]
        handle = session.memory.handles["local"]
        self.assertFalse(local.is_ready(handle, offset_bytes=0, size_bytes=60,
                                       version=MemoryVersion(kind="producer", producer_id=plan.jobs[0].reader_operations[0])))
        self.assertTrue(local.is_ready(handle, offset_bytes=0, size_bytes=60,
                                      version=MemoryVersion(kind="producer", producer_id=plan.jobs[1].reader_operations[0])))
        buffers.consume(second)
        with self.assertRaisesRegex(ValueError, "this generation"):
            buffers.publish_output(second)
        self.assertFalse(buffers.is_drained)
        self.assertFalse(session.snapshot().teardown_complete)
        self.assertEqual(buffers.snapshot()[0].occupied, 1)
        with self.assertRaisesRegex(ValueError, "stale"):
            buffers.consume(first)

    def test_posted_slot_release_precedes_remote_effect_and_full_session_drain(self):
        document, graph = compute_documents(jobs=2, slots=1, local=True)
        for index, job in enumerate(document["streams"][0]["jobs"]):
            job["output"] = {"mode": "write_posted", "fabric_id": 1, "destination": addressed("remote", 256 + index * 256, 84)}
        for resource in document["memory"]["resources"]:
            if resource["resource_id"] == "dram":
                resource["service"]["fixed_latency_cycles"] = 200
        plan, session, buffers = setup(document=document, graph=graph, credit=100)
        env = session.env
        for index in range(2):
            token = buffers.try_reserve(f"job{index}")
            buffers.publish_inputs(token)
            env.run(until=env.process(finish_job(plan, session, buffers, token)))
            buffers.release(token)
            writer = plan.jobs[index].writer_operation
            self.assertIsNotNone(session.lifecycle_time(writer, "complete"))
            self.assertIsNone(session.lifecycle_time(writer, "destination_ready"))
            self.assertFalse(session.is_drained)
        self.assertTrue(buffers.is_drained)
        session.complete_owner(session.owner)
        with self.assertRaises(ValueError):
            session.finalize(session.owner)
        self.assertTrue(all(r.reserved_bytes for r in session.snapshot().memory_resources))
        session.advance(max_aci_cycles=10000)
        final = session.finalize(session.owner)
        self.assertEqual(final.status, "complete")
        self.assertTrue(all(r.available_bytes == r.capacity_bytes for r in final.released_resources))

    def test_local_output_consumers_delay_release_without_blocking_job_completion(self):
        document, graph = compute_documents(jobs=3, slots=2)
        first, consumer, _ = document["streams"][0]["jobs"]
        first["output"] = {"mode": "local", "destination": addressed("local", 256, 84)}
        consumer["a"].update(source=addressed("local", 256, 84), version={"kind": "job", "producer_job_id": "job0"})
        consumer["operation"]["a"]["shape"] = [2, 3, 7]
        consumer["operation"]["b"]["shape"] = [7, 7]
        consumer["b"]["source"]["size_bytes"] = 98
        consumer["output"]["destination"]["offset_bytes"] = 512
        plan, session, buffers = setup(document=document, graph=graph)
        env = session.env
        first_token = buffers.try_reserve("job0")
        env.run(until=env.process(prepare_inputs(plan, session, buffers, first_token)))
        env.run(until=env.process(finish_job(plan, session, buffers, first_token)))
        self.assertIsNotNone(session.gate_time(plan.jobs[0].gate_ids[-1]))
        with self.assertRaisesRegex(ValueError, "consumer"):
            buffers.release(first_token)
        consumer_token = buffers.try_reserve("job1")
        self.assertIsNone(buffers.try_reserve("job2"))
        env.run(until=env.process(prepare_inputs(plan, session, buffers, consumer_token)))
        self.assertEqual(plan.jobs[0].consumer_operations, (plan.jobs[1].reader_operations[0],))
        buffers.release(first_token)
        self.assertIsNotNone(buffers.try_reserve("job2"))
        # One slot would require overwriting the result before its own reader.
        invalid = copy.deepcopy(document)
        invalid["streams"][0]["slots"] = invalid["streams"][0]["slots"][:1]
        with patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
            lower(invalid, graph)

    def test_live_range_leases_pin_only_the_occupied_slot(self):
        plan, session, buffers = setup(jobs=1, slots=2)
        token = buffers.try_reserve("job0")
        buffers.publish_inputs(token)
        session.env.run(until=session.env.process(finish_job(plan, session, buffers, token)))
        local = session.memory.resources["l1-a"]
        handle = session.memory.handles["local"]
        lease = local.try_acquire(handle, MemoryAccess(client_id="external", direction="read", offset_bytes=256,
                                                       size_bytes=84, version=MemoryVersion(kind="producer", producer_id=plan.jobs[0].result_operation)))
        with self.assertRaisesRegex(ValueError, "lease"):
            buffers.release(token)
        local.release_access(lease)
        disjoint = local.try_acquire(handle, MemoryAccess(client_id="disjoint", direction="read", offset_bytes=512,
                                                         size_bytes=60, version=MemoryVersion(kind="initial")))
        buffers.release(token)
        self.assertTrue(buffers.is_drained)
        session.complete_owner(session.owner)
        with self.assertRaises(ValueError):
            session.finalize(session.owner)
        local.release_access(disjoint)
        self.assertTrue(session.finalize(session.owner).teardown_complete)

    def test_released_backing_and_invalid_idle_queries_fail(self):
        _, session, buffers = setup()
        local = session.memory.resources["l1-a"]
        handle = session.memory.handles["local"]
        for offset, size in ((-1, 1), (0, 0), (True, 1), (0, False), (16384, 1)):
            with self.assertRaises(ValueError):
                local.range_is_idle(handle, offset_bytes=offset, size_bytes=size)
        local.release(handle)
        local.reserve(handle.buffer)
        with self.assertRaisesRegex(ValueError, "released or replaced"):
            buffers.try_reserve("job0")


if __name__ == "__main__":
    unittest.main()
