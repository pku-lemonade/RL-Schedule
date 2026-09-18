"""Reservation, branch accounting and resumable transport checks."""

from __future__ import annotations

import unittest

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.multicast_transport import (
    AtomicTreeReservation,
    CompositeLaneIdentity,
    SharedPhysicalTransportRegistry,
    TreeForwardingEngine,
    TreeTransportPlan,
)
from simulator_detailed.tests.test_multicast_sync import workload_document


class MulticastTransportTests(unittest.TestCase):
    def _plan(self):
        document, graph = workload_document()
        return MulticastSyncPlan.compile(
            MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
        )

    def test_reservation_is_fifo_and_all_or_none(self) -> None:
        reservations = AtomicTreeReservation(capacity=1)
        first = reservations.submit(
            reservation_id="a", operation_id="a", lane_ids=("lane-a", "lane-b"),
            setup_aci_cycles=1, edge_aci_cycles=2,
        )
        self.assertIsNotNone(first)
        waiting = reservations.submit(
            reservation_id="b", operation_id="b", lane_ids=("lane-c", "lane-d"),
            setup_aci_cycles=1, edge_aci_cycles=2,
        )
        self.assertIsNone(waiting)
        self.assertEqual(reservations.active, ("a",))
        released = reservations.release("a", released_aci_cycles=10)
        self.assertEqual(released[0].released_aci_cycles, 10)
        self.assertEqual(released[1].reservation_id, "b")
        self.assertEqual(reservations.active, ("b",))
        self.assertIsNone(
            reservations.submit(
                reservation_id="c", operation_id="c", lane_ids=("lane-c", "lane-x"),
                setup_aci_cycles=1, edge_aci_cycles=1,
            )
        )

    def test_shared_prefix_launch_and_recipient_effect_accounting(self) -> None:
        transport = TreeTransportPlan.compile(TreeTransportPlanTests.plan(self))
        result = TreeForwardingEngine(transport, branch_capacity_flits=1).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.source_read_useful_bytes, 96)
        self.assertEqual(result.destination_write_useful_bytes, 5 * 96)
        self.assertEqual(result.packet_physical_bytes, 160)
        self.assertEqual(result.launched_channel_bytes, 5 * 5 * 32)
        self.assertEqual(sum(event.action == "recipient_deliver" for event in result.events), 5 * 5)
        self.assertEqual(sum(event.action == "reservation_acquire" for event in result.events), 1)
        self.assertEqual(sum(event.action == "reservation_release" for event in result.events), 1)
        self.assertEqual(result.pending_operations, ())

    def test_cycle_limit_is_incomplete_without_partial_release(self) -> None:
        transport = TreeTransportPlan.compile(TreeTransportPlanTests.plan(self))
        result = TreeForwardingEngine(transport, branch_capacity_flits=1).run(cycle_limit=1)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.reason, "cycle_limit")
        self.assertEqual(result.pending_operations, ("broadcast",))
        self.assertTrue(any(event.action == "reservation_acquire" for event in result.events))
        self.assertEqual(result.snapshot.active_reservation_ids, ("broadcast:reservation",))

    def test_composite_registry_shares_serializer_and_keeps_lane_classes(self) -> None:
        registry = SharedPhysicalTransportRegistry(physical_flit_bytes=32, capacity_flits=1)
        request = CompositeLaneIdentity(fabric_id=0, link_id="x", lane_kind="request", lane_index=0)
        response = CompositeLaneIdentity(fabric_id=0, link_id="x", lane_kind="response", lane_index=0)
        tree = CompositeLaneIdentity(fabric_id=1, link_id="x", lane_kind="multicast_tree", lane_index=0)
        registry.register(request)
        registry.register(response)
        registry.claim(tree, "tree-1")
        with self.assertRaises(ValueError):
            registry.claim(tree, "tree-2")
        self.assertEqual(len(registry.export()), 3)
        self.assertEqual({item.lane.lane_kind for item in registry.export()}, {"request", "response", "multicast_tree"})
        registry.release(tree, "tree-1")


class TreeTransportPlanTests:
    @staticmethod
    def plan(test: unittest.TestCase) -> MulticastSyncPlan:
        document, graph = workload_document()
        return MulticastSyncPlan.compile(
            MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
        )


if __name__ == "__main__":
    unittest.main()
