"""Shared-clock gates, scoped drain and preserved standalone replay evidence."""

from __future__ import annotations

import itertools
import unittest
from collections import Counter
from dataclasses import replace
from unittest.mock import patch

import simpy

from simulator_detailed.memory_runtime import MemoryRuntime, MemorySession
from simulator_detailed.memory_session import (
    MemoryGateDefinition,
    MemoryGateToken,
    MemoryOwnerToken,
    MemorySessionPlan,
)
from simulator_detailed.tests.test_memory_ordering import add_worker, local
from simulator_detailed.tests.test_memory_runtime import documents, execution_plan
from simulator_detailed.tests.test_packet_runtime import transport_config
from simulator_detailed.topology import content_digest

# Whole-result hashes captured on fa02ace before the session refactor. They cover
# identities, all lifecycle/packet/service/ownership traces and final capacity.
STANDALONE = {
    "read": ("633318556e8ab4026a064849a1e4c88b2b5215868495ababced170071aeb89e2", "bf40c6babb66ccd4dac9ce0d09a5062ff5405805262775d68a6161ab80721faa", 52),
    "write_posted": ("39487af542ee0213a23bd755f1f72575dff29745f3e20c36e7f9d5fe3e82e027", "a54848952ce0f003501cd2d866729cf377bbaab3660ca927bfc85c900f6603ff", 34),
    "write_acknowledged": ("3800e74d6ad57b0b2d768254fa7dcadec99396d26f8035f677fa7a0149810e59", "2d0782e23b5ccbb0a0fd3933500fa19b6c73a792ba16e55319fc8948f58068f0", 52),
    "local_chain": ("073aae903c4b8aae35c69fb356df5867a6dd04fda0dcd53b1428b31042291ff7", "bce9e96e10ab6e155ac49bf8ec3f10308f1f821c3542762ebe22bbba999abae8", 9),
}


def binding(plan, gates=None):
    if gates is None:
        owners = sorted({o.initiator_resource_id for o in plan.ordering.operations.values()})
        gates = tuple(MemoryGateDefinition(gate_id=owner, resource_id=owner,
                                           operation_ids=tuple(o.operation_id for o in plan.ordering.operations.values()
                                                               if o.initiator_resource_id == owner)) for owner in owners)
    return MemorySessionPlan.compile(plan, owner_id="test-workload", gates=gates)


class MemorySessionTests(unittest.TestCase):
    def test_standalone_complete_and_incomplete_results_match_pre_refactor(self):
        for kind, (early_hash, final_hash, elapsed) in STANDALONE.items():
            with self.subTest(kind=kind):
                doc, graph = documents(kind if kind != "local_chain" else "write_acknowledged", 65)
                if kind == "local_chain":
                    doc["operations"] = [local("produce", write=True, size=65, start_aci_cycles=3),
                                         local("consume", size=65, depends_on=["produce"],
                                               source_version={"kind": "producer", "producer_id": "produce"})]
                else:
                    doc["packet"]["max_segment_payload_bytes"] = 32
                runtime = MemoryRuntime(execution_plan(doc, graph))
                self.assertEqual(content_digest(runtime.run(max_aci_cycles=4).model_dump(mode="json")), early_hash)
                final = runtime.run(max_aci_cycles=1000)
                self.assertEqual(content_digest(final.model_dump(mode="json")), final_hash)
                self.assertEqual(final.elapsed_aci_cycles, elapsed)
                self.assertIs(runtime.run(), final)

    def test_closed_gate_prevents_submission_and_uses_absolute_start_time(self):
        for opens, start, expected in ((20, 5, 20), (5, 20, 20)):
            with self.subTest(opens=opens, start=start):
                doc, graph = documents(size=65)
                doc["packet"]["max_segment_payload_bytes"] = 32
                doc["operations"][0]["start_aci_cycles"] = start
                plan = binding(execution_plan(doc, graph))
                env = simpy.Environment()
                session = MemorySession(env, plan)
                gate = session.gate("l1-a")
                for invalid in (MemoryGateToken("l1-a"), env.event(), MemorySession(simpy.Environment(), plan).gate("l1-a")):
                    with self.assertRaisesRegex(ValueError, "foreign or unadmitted"):
                        session.activate(invalid)
                with self.assertRaises(ValueError):
                    session.gate("missing")
                early = session.advance(max_aci_cycles=1)
                self.assertIsNone(early.operations[0].submission_aci_cycles)
                self.assertFalse(early.chunks or early.descriptor_trace)
                self.assertTrue(any('"activation"' in pending for pending in early.pending))

                def controller(controller_env=env, controller_opens=opens,
                               controller_session=session, controller_gate=gate):
                    yield controller_env.timeout(controller_opens)
                    controller_session.activate(controller_gate)

                env.process(controller())
                complete = session.advance(max_aci_cycles=1000)
                self.assertTrue(session.is_drained)
                self.assertEqual(complete.operations[0].submission_aci_cycles, expected)
                self.assertEqual({s.submission_aci_cycles for s in complete.segments}, {expected})
                self.assertEqual(session.gate_time("l1-a"), opens)
                with self.assertRaisesRegex(ValueError, "twice"):
                    session.activate(gate)

    def test_waiting_for_gate_prerequisites_cannot_publish_parent_or_fact(self):
        doc, graph = documents()
        doc["operations"] = [local("operand", size=32), local("result", write=True, size=32, offset=64,
                                                            depends_on=["operand"])]
        gates = (
            MemoryGateDefinition(gate_id="input", resource_id="l1-a", operation_ids=("operand",)),
            MemoryGateDefinition(gate_id="math_done", resource_id="l1-a", operation_ids=("result",),
                                 after_gates=("input",), waits=({"operation_id": "operand", "event": "complete"},)),
        )
        env = simpy.Environment()
        session = MemorySession(env, binding(execution_plan(doc, graph), gates))
        observer = session.wait_gate_prerequisites("math_done")
        # A yielded observation is not the internal gate event. Even forcing
        # the caller's generator onward cannot authorize an early activation.
        next(observer).succeed()
        next(observer).succeed()
        with self.assertRaises(StopIteration):
            next(observer)
        self.assertIsNone(session.gate_time("input"))
        self.assertIsNone(session.lifecycle_time("operand", "complete"))
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            session.activate(session.gate("math_done"))
        wait = env.process(session.wait_gate_prerequisites("math_done"))
        session.advance(max_aci_cycles=1)
        self.assertFalse(wait.triggered)
        session.activate(session.gate("input"))
        session.advance(max_aci_cycles=100)
        self.assertTrue(wait.triggered)
        self.assertIsNotNone(session.lifecycle_time("operand", "complete"))
        self.assertIsNone(session.gate_time("math_done"))
        session.activate(session.gate("math_done"))

    def test_delayed_math_gate_and_external_owner_outlive_memory(self):
        doc, graph = documents()
        doc["operations"] = [local("operand", size=32), local("result", write=True, size=32, offset=64, depends_on=["operand"])]
        gates = (
            MemoryGateDefinition(gate_id="input", resource_id="l1-a", operation_ids=("operand",)),
            MemoryGateDefinition(gate_id="math_done", resource_id="l1-a", operation_ids=("result",),
                                 after_gates=("input",), waits=({"operation_id": "operand", "event": "complete"},)),
        )
        env = simpy.Environment()
        session = MemorySession(env, binding(execution_plan(doc, graph), gates))
        session.activate(session.gate("input"))
        # Spoofing an observer cannot alter the event used by admission gates.
        session.lifecycle_event("operand", "complete").succeed()
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            session.activate(session.gate("math_done"))
        for operation, event in (("operand", "request_handoff"), ("result", "source_read_complete"),
                                 ("missing", "complete"), ("operand", "response_wire_receipt")):
            with self.assertRaises(ValueError):
                session.lifecycle_event(operation, event)

        def controller():
            yield session.lifecycle_event("operand", "complete")
            yield env.timeout(7)
            session.activate(session.gate("math_done"))
            yield session.lifecycle_event("result", "complete")
            yield env.timeout(11)
            session.complete_owner(session.owner)

        env.process(controller())
        early = session.advance(max_aci_cycles=5)
        self.assertEqual(early.operations[0].completion_aci_cycles, 1)
        self.assertIsNone(early.operations[1].submission_aci_cycles)
        ready = session.advance(max_aci_cycles=15)
        self.assertEqual((ready.operations[1].submission_aci_cycles, ready.operations[1].completion_aci_cycles), (8, 9))
        self.assertTrue(session.is_drained)  # The unrelated owner timeout is still queued.
        self.assertEqual(ready.status, "incomplete")
        self.assertFalse(ready.teardown_complete)
        self.assertTrue(all(r.reserved_bytes for r in ready.memory_resources))
        with self.assertRaises(ValueError):
            session.finalize(session.owner)
        session.advance(max_aci_cycles=100)
        self.assertEqual(env.now, 20)
        self.assertEqual(session.finalize(session.owner).status, "complete")

    def test_finalization_is_owned_idempotent_and_does_not_step_other_work(self):
        doc, graph = documents()
        doc["operations"] = [local("operand", size=32)]
        env = simpy.Environment(initial_time=10)
        session = MemorySession(env, binding(execution_plan(doc, graph)))
        with self.assertRaises(ValueError):
            session.complete_owner(session.owner)
        session.activate(session.gate("l1-a"))
        before = session.advance(max_aci_cycles=100)
        self.assertEqual(before.operations[0].submission_aci_cycles, 10)
        with self.assertRaises(ValueError):
            session.complete_owner(MemoryOwnerToken("test-workload"))
        session.complete_owner(session.owner)
        with self.assertRaises(ValueError):
            session.complete_owner(session.owner)
        marker = []

        def other_work():
            yield env.timeout(0)
            marker.append(env.now)

        env.process(other_work())
        with self.assertRaises(ValueError):
            session.finalize()
        final = session.finalize(session.owner)
        self.assertFalse(marker)
        self.assertTrue(final.teardown_complete)
        self.assertTrue(all(r.available_bytes == r.capacity_bytes for r in final.released_resources))
        self.assertIs(session.finalize(session.owner), final)
        self.assertEqual(Counter(e.buffer_id for e in final.ownership_trace if e.action == "release"), {"local": 1, "remote": 1})
        env.run()
        self.assertEqual(marker, [11])

    def test_posted_effects_and_delayed_credits_still_prevent_finalization(self):
        session = MemorySession(simpy.Environment(), binding(execution_plan(*documents("write_posted", 1),
                                                                           transport=transport_config(credit=100, capacity=2))))
        session.activate(session.gate("l1-a"))
        early = session.advance(max_aci_cycles=50)
        self.assertIsNotNone(early.operations[0].completion_aci_cycles)
        session.complete_owner(session.owner)
        with self.assertRaises(ValueError):
            session.finalize(session.owner)
        self.assertTrue(any(r.pending_returns for r in early.transport.resources))
        session.advance(max_aci_cycles=1000)
        self.assertEqual(session.finalize(session.owner).status, "complete")

    def test_invalid_gate_sets_and_cycles_fail_before_resources(self):
        plan = execution_plan(*documents())
        good = MemoryGateDefinition(gate_id="stage", resource_id="l1-a", operation_ids=("transfer",))
        cases = [(), (good, good), (good.model_copy(update={"operation_ids": ("unknown",)}),),
                 (good.model_copy(update={"resource_id": "dram"}),),
                 (good.model_copy(update={"after_gates": ("stage",)}),),
                 (MemoryGateDefinition(gate_id="stage", resource_id="l1-a", operation_ids=("transfer",),
                                       waits=({"operation_id": "transfer", "event": "complete"},)),),
                 (MemoryGateDefinition(gate_id="stage", resource_id="l1-a", operation_ids=("transfer",),
                                       waits=({"operation_id": "transfer", "event": "destination_ready"},)),)]
        for gates in cases:
            with patch("simpy.Resource", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                binding(plan, gates)
        valid = binding(plan)
        with self.assertRaisesRegex(ValueError, "differs"):
            replace(valid, owner_id="changed").revalidate()

    def test_shared_and_independent_local_network_service(self):
        results = []
        for shared in (True, False):
            doc, graph = documents("read", 257)
            for resource in doc["resources"]:
                if resource["resource_id"] == "l1-a":
                    resource["service"]["fixed_latency_cycles"] = 20
            if not shared:
                add_worker(doc, graph)
            doc["operations"].append(local("local-client", size=257, offset=1024,
                                            buffer="local" if shared else "local-b", initiator="src0" if shared else "peer0"))
            plan = binding(execution_plan(doc, graph, local_capacity=1))
            session = MemorySession(simpy.Environment(), plan)
            for gate in plan.gates:
                session.activate(session.gate(gate.gate_id))
            session.advance(max_aci_cycles=10000)
            session.complete_owner(session.owner)
            result = session.finalize(session.owner)
            results.append(result)
            self.assertEqual(len([r for r in result.memory_resources if r.resource_id == "l1-a"]), 1)
            self.assertEqual(result.operations[1].packet_bytes, 0)
            self.assertEqual(sum(c.useful_bytes for c in result.chunks if c.client_id == "local-client"), 257)
        shared, independent = results
        self.assertLess(independent.operations[0].completion_aci_cycles, shared.operations[0].completion_aci_cycles)
        shared_chunks = [c for c in shared.chunks if c.resource_id == "l1-a"]
        self.assertTrue(all(a.end_aci_cycles <= b.start_aci_cycles for a, b in itertools.pairwise(shared_chunks)))
        network = [c for c in independent.chunks if c.resource_id == "l1-a"]
        local_chunks = [c for c in independent.chunks if c.resource_id == "l1-b"]
        self.assertTrue(any(a.start_aci_cycles < b.end_aci_cycles and b.start_aci_cycles < a.end_aci_cycles
                            for a in network for b in local_chunks))
        self.assertEqual(sum(c.service_aci_cycles for c in shared_chunks), 189)

    def test_cross_worker_gate_dependency_requires_real_notification(self):
        doc, graph = documents()
        add_worker(doc, graph)
        doc["operations"].append(local("peer-read", buffer="local-b", initiator="peer0"))
        plan = execution_plan(doc, graph)
        original = binding(plan)
        changed = list(original.gates)
        changed[1] = changed[1].model_copy(update={"after_gates": (changed[0].gate_id,)})
        with self.assertRaisesRegex(ValueError, "remote notification"):
            binding(plan, tuple(changed))
        changed = list(original.gates)
        fields = changed[1].model_dump()
        fields["waits"] = [{"operation_id": "transfer", "event": "complete"}]
        changed[1] = MemoryGateDefinition.model_validate(fields)
        with self.assertRaisesRegex(ValueError, "same canonical initiator"):
            binding(plan, tuple(changed))


if __name__ == "__main__":
    unittest.main()
