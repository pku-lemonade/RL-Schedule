"""Addressed memory lifecycle checks for posted and acknowledged multicast."""

from __future__ import annotations

import unittest

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_memory import MulticastMemoryExecutor
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.tests.test_multicast_sync import workload_document


class MulticastMemoryTests(unittest.TestCase):
    def _executor(self, completion: str = "write_posted") -> MulticastMemoryExecutor:
        document, graph = workload_document()
        document["writes"][0]["completion"] = completion
        workload = MulticastSyncWorkload.model_validate(document)
        return MulticastMemoryExecutor.compile(
            MulticastSyncPlan.compile(workload, CanonicalTopology.model_validate(graph))
        )

    def test_posted_effects_are_per_recipient_and_full_extent_ready(self) -> None:
        result = self._executor().run()
        self.assertEqual(result.status, "complete")
        operation = result.operations[0]
        self.assertEqual(operation.source_read_useful_bytes, 96)
        self.assertEqual(operation.destination_write_useful_bytes, 5 * 96)
        self.assertEqual(operation.acknowledgement_physical_bytes, 0)
        self.assertEqual(operation.source_reusable_aci_cycles is not None, True)
        self.assertEqual(operation.destination_ready_aci_cycles, operation.completion_aci_cycles)
        self.assertEqual(sum(state.ready for state in result.buffers), 5)
        self.assertEqual(sum(event.action == "buffer_ready" for event in result.events), 5)
        source_release = next(index for index, event in enumerate(result.events) if event.action == "source_release")
        first_destination = next(
            index for index, event in enumerate(result.events) if event.action == "destination_write_start"
        )
        self.assertLess(source_release, first_destination)

    def test_acknowledged_completion_has_bounded_return_packets(self) -> None:
        result = self._executor("write_acknowledged").run()
        self.assertEqual(result.status, "complete")
        operation = result.operations[0]
        self.assertEqual(operation.acknowledgement_physical_bytes, 5 * 2 * 32)
        self.assertEqual(sum(event.action == "ack_emit" for event in result.events), 5)
        self.assertEqual(sum(event.action == "ack_receive" for event in result.events), 5)
        assert operation.completion_aci_cycles is not None
        assert operation.destination_ready_aci_cycles is not None
        self.assertGreater(operation.completion_aci_cycles, operation.destination_ready_aci_cycles)

    def test_source_not_ready_and_resume_do_not_mutate_effects(self) -> None:
        document, graph = workload_document()
        next(buffer for buffer in document["memory"]["buffers"] if buffer["buffer_id"] == "b-ep-t0_0")[
            "initially_ready"
        ] = False
        executor = MulticastMemoryExecutor.compile(
            MulticastSyncPlan.compile(
                MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
            )
        )
        pending = executor.run()
        self.assertEqual(pending.status, "incomplete")
        self.assertEqual(pending.operations[0].reason, "source_not_ready")
        self.assertEqual(sum(state.useful_bytes for state in pending.buffers), 0)

        complete = self._executor().run()
        resumed = self._executor().resume(complete)
        self.assertEqual(resumed.status, "complete")
        self.assertEqual(resumed.operations, ())
        self.assertEqual(resumed.buffers, complete.buffers)

    def test_cycle_limit_preserves_pre_effect_buffer_versions(self) -> None:
        result = self._executor().run(cycle_limit=1)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(sum(state.version for state in result.buffers), 0)
        self.assertEqual(result.operations[0].reason, "cycle_limit")

    def test_acknowledged_write_requires_the_existing_response_sink_role(self) -> None:
        document, graph = workload_document()
        document["writes"][0]["completion"] = "write_acknowledged"
        source = next(item for item in document["memory"]["endpoints"] if item["endpoint_id"] == "ep-t0_0")
        source["roles"] = ["initiator", "target"]
        with self.assertRaisesRegex(ValueError, "response sink"):
            MulticastMemoryExecutor.compile(
                MulticastSyncPlan.compile(
                    MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
                )
            )


if __name__ == "__main__":
    unittest.main()
