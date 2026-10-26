"""Synthetic regressions for opt-in MoE extensions on the current core."""
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from simpy.resources.resource import PriorityResource, Resource

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.quantity_resources import QuantityClaim
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system
from simulator_detailed.tests.test_unified_runtime import ring_graph, ring_batch, transfer


def specification(*, fifo=False, envelopes=(), max_cycles=100.0, transactions=None):
    transactions = transactions or [transfer("move", "eu0", "eu2", payload=24)]
    batch = ring_batch(transactions, max_cycles=max_cycles)
    if fifo:
        def timing(identity, stage):
            return dict(
                mode="latency_fifo", stage=stage, packet_bytes=8, issue_cycles=2,
                input_latency_cycles=1.0, channel_latency_cycles=1.0,
                fifo_slots=2, channel_slots=2, input_resource_id=identity + "-input",
                output_resource_id=identity + "-output", channel_resource_id=identity + "-channel",
                flow_serialized=True,
            )
        batch["timing"][0]["link"] = timing("first", "ingress")
        batch["timing"][0]["overrides"] = [dict(link_id="l12", timing=timing("last", "egress"))]
    batch["operation_envelopes"] = list(envelopes)
    return SystemSpec.model_validate(dict(
        kind="system_spec", schema_version=1, spec_id="synthetic-moe-integration",
        graph=ring_graph(), batch=batch,
    ))


def envelope(duration=20.0):
    return dict(
        operation_id="operation", member_transactions=["move"], boundary_ref="synthetic-boundary",
        phases=[dict(phase_id="tail", duration_cycles=duration, depends_on=["move"], evidence_ref="synthetic-phase")],
    )


class MoeRuntimeIntegrationTests(unittest.TestCase):
    def test_legacy_plan_and_results_omit_extensions(self):
        spec = specification()
        context = RuntimeContext(compile_system(spec))
        self.assertFalse(context._moe_extensions_enabled())
        self.assertTrue(all(type(entry.serializer) is Resource for entry in context.registry.entries()))
        for document in (spec.batch.model_dump(mode="json"), context.plan.content.model_dump(mode="json")):
            self.assertNotIn("operation_envelopes", document)
            for transaction in document["transactions"]:
                for name in ("multicast_group", "flow_priority", "flow_release_delay_cycles"):
                    self.assertNotIn(name, transaction)
        with patch.object(context, "_run_moe_extensions", side_effect=AssertionError("legacy dispatch changed")):
            result = context.run()
        self.assertEqual(result.status, "complete")
        self.assertNotIn("operation_envelopes", result.model_dump(mode="json"))
        self.assertNotIn("contention", result.model_dump(mode="json"))

    def test_fifo_uses_same_registry_and_drains_deterministically(self):
        plan = compile_system(specification(fifo=True))
        results = []
        for repeat in range(2):
            context = RuntimeContext(plan)
            self.assertIsInstance(context.registry.get("last-output").serializer, PriorityResource)
            self.assertIs(type(context.registry.get("first-channel").serializer), Resource)
            result = context.run()
            self.assertEqual(result.status, "complete")
            self.assertTrue(context.registry.all_released())
            self.assertTrue(any(event.action == "fifo_event" for event in result.trace))
            results.append(result.model_dump_json())
        self.assertEqual(*results)

    def test_envelope_completion_and_dependent_compute(self):
        transactions = [transfer("move", "eu0", "eu2", payload=24), dict(
            transaction_id="consumer", kind="compute", unit_id="eu1",
            duration_cycles=3.0, depends_on=["operation"],
        )]
        context = RuntimeContext(compile_system(specification(
            fifo=True, envelopes=[envelope()], transactions=transactions,
        )))
        result = context.run()
        self.assertEqual(result.status, "complete")
        spans = {span.transaction_id: span for span in result.transactions}
        operation = result.operation_envelopes[0]
        self.assertEqual(operation["end_cycles"], spans["move"].end_cycles + 20.0)
        self.assertEqual(spans["consumer"].start_cycles, operation["end_cycles"])
        self.assertTrue(context.registry.all_released())

    def test_envelope_timeout_drains_children(self):
        context = RuntimeContext(compile_system(specification(envelopes=[envelope(100.0)], max_cycles=20.0)))
        result = context.run()
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.operation_envelopes[0]["status"], "incomplete")
        self.assertTrue(context.registry.all_released())

    def test_fifo_timeout_releases_packet_resources(self):
        context = RuntimeContext(compile_system(specification(fifo=True, max_cycles=3.0)))
        self.assertEqual(context.run().status, "incomplete")
        self.assertTrue(context.registry.all_released())

    def test_quantity_cleanup_preserves_ownership(self):
        context = RuntimeContext(compile_system(specification()))
        context.registry.register_quantity("scratch", 16, "bytes")
        context.registry.try_acquire_quantities([QuantityClaim("scratch", 8, "bytes")], "synthetic-owner")
        self.assertTrue(context._moe_extensions_enabled())
        self.assertFalse(context.registry.all_released())
        self.assertEqual(context.cleanup(), ())
        self.assertTrue(context.registry.all_released())
        self.assertEqual(context.cleanup_status, "complete")

    def test_fifo_failure_aborts_and_cleans_up(self):
        context = RuntimeContext(compile_system(specification(fifo=True)))
        original = context._emit
        def emit(action, *args, **kwargs):
            if action == "fifo_event":
                raise ValueError("synthetic-event-failure")
            return original(action, *args, **kwargs)
        with patch.object(context, "_emit", side_effect=emit):
            with self.assertRaisesRegex(RuntimeError, "synthetic-event-failure"):
                context.run()
        self.assertTrue(context.registry.all_released())
        self.assertEqual(context.cleanup_status, "complete")

    def test_envelope_dependency_cycle_is_rejected(self):
        with self.assertRaises(ValidationError):
            specification(envelopes=[envelope()], transactions=[transfer("move", "eu0", "eu2", depends_on=["operation"])])


if __name__ == "__main__":
    unittest.main()
