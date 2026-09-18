"""Atomic counter and local wait behavior for the multicast child."""

from __future__ import annotations

import copy
import unittest

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.multicast_scalar import ScalarExecutor
from simulator_detailed.tests.test_multicast_sync import workload_document


def scalar_executor(*, completion: str = "atomic_returning", threshold: int = 1,
                    data_ready_after: tuple[str, ...] = ()) -> ScalarExecutor:
    document, graph = workload_document()
    document["counters"] = [{
        "counter_id": "counter",
        "endpoint_id": "ep-t0_0",
        "buffer_id": "b-ep-t0_0",
        "offset_bytes": 256,
        "width_bytes": 1,
        "initial_value": 0,
    }]
    document["increments"] = [{
        "operation_id": "increment",
        "source_endpoint_id": "ep-t0_0",
        "fabric_id": 0,
        "counter_id": "counter",
        "completion": completion,
    }]
    document["waits"] = [{
        "wait_id": "wait",
        "endpoint_id": "ep-t0_0",
        "counter_id": "counter",
        "threshold": threshold,
        "producer_operations": ["increment"],
        "data_ready_after": list(data_ready_after),
    }]
    return ScalarExecutor.compile(
        MulticastSyncPlan.compile(
            MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
        )
    )


class MulticastScalarTests(unittest.TestCase):
    def test_returning_increment_is_indivisible_and_returns_previous_value(self) -> None:
        result = scalar_executor().run()
        self.assertEqual(result.status, "complete")
        increment = result.increments[0]
        self.assertEqual((increment.old_value, increment.new_value), (0, 1))
        self.assertEqual(increment.request_physical_bytes, 32)
        self.assertEqual(increment.response_physical_bytes, 32)
        self.assertEqual(result.counters[0].value, 1)
        self.assertEqual(result.waits[0].reason, "threshold_reached")
        self.assertEqual(sum(event.action == "atomic_effect" for event in result.events), 1)

    def test_posted_increment_has_no_return_packet_and_wait_observes_locally(self) -> None:
        result = scalar_executor(completion="atomic_posted").run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.increments[0].response_physical_bytes, 0)
        self.assertEqual(result.waits[0].observation_count, 1)
        self.assertEqual(sum(event.action == "atomic_return" for event in result.events), 0)

    def test_wait_rejects_unreachable_threshold_and_data_prerequisite(self) -> None:
        unreachable = scalar_executor(threshold=2).run()
        self.assertEqual(unreachable.status, "incomplete")
        self.assertEqual(unreachable.waits[0].reason, "threshold_unreachable")
        data_pending = scalar_executor(data_ready_after=("broadcast",)).run()
        self.assertEqual(data_pending.status, "incomplete")
        self.assertEqual(data_pending.waits[0].reason, "data_not_ready")

    def test_counter_overlap_and_width_overflow_fail_before_execution(self) -> None:
        document, graph = workload_document()
        document["counters"] = [
            {
                "counter_id": "first", "endpoint_id": "ep-t0_0", "buffer_id": "b-ep-t0_0",
                "offset_bytes": 256, "width_bytes": 4, "initial_value": 0,
            },
            {
                "counter_id": "second", "endpoint_id": "ep-t0_0", "buffer_id": "b-ep-t0_0",
                "offset_bytes": 256, "width_bytes": 4, "initial_value": 0,
            },
        ]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ScalarExecutor.compile(
                MulticastSyncPlan.compile(
                    MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
                )
            )
        invalid = copy.deepcopy(document)
        invalid["counters"] = [{
            "counter_id": "counter", "endpoint_id": "ep-t0_0", "buffer_id": "b-ep-t0_0",
            "offset_bytes": 256, "width_bytes": 1, "initial_value": 255,
        }]
        invalid["increments"] = [{
            "operation_id": "increment", "source_endpoint_id": "ep-t0_0", "fabric_id": 0,
            "counter_id": "counter", "completion": "atomic_posted",
        }]
        with self.assertRaisesRegex(ValueError, "overflows"):
            ScalarExecutor.compile(
                MulticastSyncPlan.compile(
                    MulticastSyncWorkload.model_validate(invalid), CanonicalTopology.model_validate(graph)
                )
            )


if __name__ == "__main__":
    unittest.main()
