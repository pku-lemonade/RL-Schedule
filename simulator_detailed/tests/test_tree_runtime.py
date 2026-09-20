"""Independent wire and ownership checks for the live shared tree runtime."""

from __future__ import annotations

import copy
import unittest
from collections import Counter, defaultdict
from itertools import pairwise

import simpy

from simulator_detailed.multicast_network import MulticastNetworkPlan
from simulator_detailed.packet_runtime import PacketTransport, PhysicalTransportRegistry
from simulator_detailed.tests.test_multicast_inventory import (
    compile_document,
    execution_document,
)
from simulator_detailed.tests.test_packet_runtime import Hooks, start_inventory
from simulator_detailed.tree_runtime import TreeTransport, TreeTransportSnapshot
from simulator_detailed.tree_wire import TreeFlit, TreeLaneIdentity, TreeTraceEvent


class TreeMemoryHooks:
    def __init__(self, env, *, produce=0, consume=0, slow_endpoint=None):
        self.env, self.produce_cost, self.consume_cost, self.slow_endpoint = env, produce, consume, slow_endpoint
        self.reads, self.writes = [], []

    def produce(self, flit):
        if flit.payload_bytes:
            yield self.env.timeout(self.produce_cost)
            self.reads.append((flit.packet, flit.flit_index, flit.payload_bytes, self.env.now))

    def consume(self, flit, endpoint_id):
        if flit.payload_bytes:
            yield self.env.timeout(self.consume_cost if self.slow_endpoint is None or endpoint_id == self.slow_endpoint else 0)
            self.writes.append((flit.packet, flit.flit_index, endpoint_id, flit.payload_bytes, self.env.now))


def runtime(document=None, graph=None, **hooks_kwargs):
    if document is None:
        document, graph = execution_document()
        document["writes"][0]["completion"] = "write_posted"
    plan = MulticastNetworkPlan.compile(compile_document(document, graph))
    env = simpy.Environment()
    registry = PhysicalTransportRegistry(env, plan.network)
    hooks = TreeMemoryHooks(env, **hooks_kwargs)
    tree = TreeTransport(env, plan, registry, hooks)
    return tree, hooks


def submit_all(tree):
    def submit(packet):
        while not tree.try_submit(packet):
            yield tree.changed
    for packet in tree.plan.trees:
        tree.env.process(submit(packet))


class TreeRuntimeTests(unittest.TestCase):
    def assert_conserved(self, snapshot, complete=True):
        self.assertEqual(snapshot.status, "complete" if complete else "incomplete")
        for resource in snapshot.resources:
            self.assertEqual(resource.available + resource.occupied + resource.pending_returns, resource.capacity)
            self.assertLessEqual(resource.peak_occupied, resource.capacity)
            if complete:
                self.assertTrue(resource.is_drained, resource)
        launches = [e for e in snapshot.trace if e.action == "link_launch"]
        by_channel = defaultdict(list)
        for event in launches:
            by_channel[event.channel].append(event)
        for events in by_channel.values():
            for previous, following in pairwise(events):
                self.assertGreaterEqual(following.time_aci_cycles, previous.time_aci_cycles + previous.duration_aci_cycles)
        # Replay per-token conservation independently of producer summary totals.
        tokens = {}
        for event in sorted(snapshot.trace, key=lambda e: e.time_aci_cycles):
            if event.action == "credit_reserve":
                self.assertNotIn(event.token_id, tokens)
                tokens[event.token_id] = event.lane
            elif event.action == "credit_return":
                self.assertIn(event.token_id, tokens)
                del tokens[event.token_id]
        if complete:
            self.assertFalse(tokens)
        self.assertEqual(snapshot.physical_channel_bytes, sum(e.physical_bytes for e in launches))

    def test_capacity_one_exact_prefix_delivery_and_terminal_discard(self):
        tree, hooks = runtime(produce=2, consume=3)
        submit_all(tree)
        result = tree.run(max_aci_cycles=10_000)
        self.assert_conserved(result)
        self.assertEqual(result.physical_channel_bytes, 1760)
        self.assertEqual(sum(r[2] for r in hooks.reads), 96)
        self.assertEqual(sum(w[3] for w in hooks.writes), 480)
        self.assertEqual(len(result.deliveries), 10)
        self.assertTrue(all(d.received_flits in (2, 3) for d in result.deliveries))
        launches = [e for e in result.trace if e.action == "link_launch"]
        self.assertEqual(len(launches), 55)
        self.assertEqual(len({(e.packet, e.lane.channel, e.flit_index) for e in launches}), 55)
        self.assertTrue(all(isinstance(e, TreeTraceEvent) for e in launches))
        self.assertEqual(sum(e.action == "terminal" for e in result.events), 5)
        self.assertFalse(any(d.endpoint_id == "ep-t2_0" for d in result.deliveries))
        self.assertEqual(TreeTransportSnapshot.model_validate_json(result.model_dump_json()), result)

    def test_live_interruption_retains_grants_credits_and_resume_is_identical(self):
        first, _ = runtime(consume=20, slow_endpoint="ep-t2_1")
        submit_all(first)
        complete = first.run(max_aci_cycles=10_000)
        second, _ = runtime(consume=20, slow_endpoint="ep-t2_1")
        submit_all(second)
        partial = second.run(max_aci_cycles=20)
        self.assert_conserved(partial, complete=False)
        self.assertTrue(second.reservations.active)
        self.assertTrue(any(r.occupied for r in partial.resources if r.kind == "lane"))
        with self.assertRaisesRegex(ValueError, "drain"):
            second.reservations.release(next(iter(second.reservations.active)))
        self.assertEqual(second.run(max_aci_cycles=10_000), complete)
        self.assertEqual(second.snapshot(), complete)

    def test_fifo_conflict_has_no_partial_grants_and_releases_after_credit_returns(self):
        document, graph = execution_document()
        document["writes"][0]["completion"] = "write_posted"
        document["control"].update(reservation_capacity=1, replication_capacity_flits=1)
        for kind in ("network_link", "local_link"):
            document["runtime"]["transport"]["fabrics"][0][kind]["credit_return_noc_cycles"] = 9
        tree, _ = runtime(document, graph, consume=3)
        submit_all(tree)
        early = tree.run(max_aci_cycles=10)
        self.assertEqual(len(tree.reservations.active), 1)
        self.assertEqual(len(tree.reservations.pending), 0)
        self.assertEqual(len(tree.submitted), 1)
        self.assert_conserved(early, complete=False)
        result = tree.run(max_aci_cycles=10_000)
        self.assert_conserved(result)
        released = {e.packet: e.time_aci_cycles for e in result.events if e.action == "reservation_release"}
        for packet, end in released.items():
            self.assertGreaterEqual(end, max(e.time_aci_cycles for e in result.trace if e.packet == packet and e.action == "credit_return"))
        acquisitions = [e for e in result.events if e.action == "reservation_acquire"]
        self.assertGreaterEqual(acquisitions[1].time_aci_cycles, released[acquisitions[0].packet])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            tree.reservations.release(acquisitions[0].packet)

    def test_real_unicast_response_shares_links_and_router_stages(self):
        document, graph = execution_document()
        document["writes"][0]["completion"] = "write_posted"
        document["operations"] = [{"operation_id": "ordinary", "initiator_id": "ep-t0_0", "fabric_id": 0,
                                  "kind": "write_acknowledged", "source": {"buffer_id": "b-ep-t0_0", "offset_bytes": 0, "size_bytes": 64},
                                  "destination": {"buffer_id": "b-ep-t1_1", "offset_bytes": 256, "size_bytes": 64}}]
        tree, _ = runtime(document, graph, consume=2)
        unicast = PacketTransport(tree.env, tree.plan.network, Hooks(tree.env, consume=2), registry=tree.registry)
        self.assertIs(unicast.links, tree.registry.links)
        self.assertIs(unicast.pipelines, tree.registry.pipelines)
        submit_all(tree)
        start_inventory(unicast)
        result = tree.run(max_aci_cycles=10_000)
        self.assert_conserved(result)
        self.assertEqual(unicast.snapshot().status, "complete")
        self.assertEqual(result.physical_channel_bytes, 1760 + 3 * 32 * 4 + 32 * 5)
        network = [link for link in tree.registry.links.values() if link.contract.channel.kind == "network"]
        self.assertTrue(any({e.packet.traffic_class for e in link.all_events if e.action == "link_launch"} >= {"request", "multicast"} for link in network))
        with self.assertRaisesRegex(ValueError, "duplicate capacity"):
            PacketTransport(tree.env, tree.plan.network, Hooks(tree.env), registry=tree.registry)
        with self.assertRaisesRegex(ValueError, "duplicate capacity"):
            TreeTransport(tree.env, tree.plan, tree.registry, TreeMemoryHooks(tree.env))

    def test_slowdown_mixed_clock_and_generic_wire_width(self):
        document, graph = execution_document()
        document["writes"][0]["completion"] = "write_posted"
        for kind in ("network_link", "local_link"):
            document["runtime"]["transport"]["fabrics"][0][kind].update(noc_clock_hz=1_000_000_000,
                wire_bits_per_noc_cycle=128, payload_bits_per_noc_cycle=128, launch_interval_noc_cycles=2)
        base = copy.deepcopy(document["runtime"]["transport"]["fabrics"][0]["network_link"])
        base["slowdowns"] = [["slow", 0, 40, 4]]
        link_id = next(e["link_id"] for e in graph["links"] if e["src_router"] == "t0_0" and e["dst_router"] == "t1_0")
        document["runtime"]["transport"]["overrides"] = [{"channel": {"fabric_id": 0, "kind": "network", "identity": link_id}, "settings": base}]
        tree, _ = runtime(document, graph)
        submit_all(tree)
        result = tree.run(max_aci_cycles=10_000)
        self.assert_conserved(result)
        launches = [e for e in result.trace if e.action == "link_launch" and e.channel.identity == link_id]
        self.assertTrue(any(e.launch_factor == 4 for e in launches))
        self.assertEqual(result.physical_channel_bytes, 1760)

    def test_disjoint_local_trees_hold_simultaneous_grants(self):
        document, graph = execution_document()
        first = document["writes"][0]
        first.update(completion="write_posted", size_bytes=32)
        first["source"]["size_bytes"] = 32
        first["rectangle"].update(end={"x": 0, "y": 0})
        first["destinations"] = first["destinations"][:1]
        second = copy.deepcopy(first)
        second.update(operation_id="local-other", source_endpoint_id="ep-t1_0")
        second["source"]["buffer_id"] = "b-ep-t1_0"
        second["rectangle"].update(start={"x": 1, "y": 0}, end={"x": 1, "y": 0})
        second["destinations"][0].update(endpoint_id="ep-t1_0", buffer_id="b-ep-t1_0")
        document["writes"].append(second)
        document["memory"]["buffers"][1]["initially_ready"] = True
        document["memory"]["endpoints"][1]["roles"].append("initiator")
        tree, _ = runtime(document, graph, consume=100)
        submit_all(tree)
        partial = tree.run(max_aci_cycles=15)
        self.assert_conserved(partial, complete=False)
        self.assertEqual(len(tree.reservations.active), 2)
        self.assertEqual(len(tree.reservations.owners), 4)
        self.assert_conserved(tree.run(max_aci_cycles=1000))

    def test_both_fabrics_execute_with_opposite_physical_coordinates(self):
        from simulator_detailed.tests.test_multicast_tree_admission import (
            dual_fabric_document,
        )
        from simulator_detailed.tests.test_packet_runtime import transport_config
        document, graph = dual_fabric_document()
        graph["fabrics"][1]["routing_policy"] = "dimension_order_yx"
        settings, _ = execution_document()
        document["runtime"] = settings["runtime"]
        document["runtime"]["transport"] = transport_config(staging=4).model_dump(mode="json")
        document["runtime"]["transport"]["endpoint_queue_capacity_packets"] = 2
        routing = settings["memory"]["routing"][0]
        document["memory"]["routing"] = [routing, {**copy.deepcopy(routing), "fabric_id": 1, "routing_policy": "dimension_order_yx"}]
        first = document["writes"][0]
        first["completion"] = "write_posted"
        second = copy.deepcopy(first)
        second.update(operation_id="opposite", fabric_id=1, source_endpoint_id="ep-t2_1-f1", target_offset_bytes=256)
        second["source"]["buffer_id"] = "b-ep-t2_1"
        for destination in second["destinations"]:
            destination["endpoint_id"] += "-f1"
            destination["offset_bytes"] = 256
        document["writes"].append(second)
        next(b for b in document["memory"]["buffers"] if b["buffer_id"] == "b-ep-t2_1")["initially_ready"] = True
        tree, _ = runtime(document, graph)
        submit_all(tree)
        result = tree.run(max_aci_cycles=10_000)
        self.assert_conserved(result)
        self.assertEqual(result.physical_channel_bytes, 3520)
        self.assertEqual({e.fabric_id for e in result.trace if e.action == "link_launch"}, {0, 1})
        self.assertEqual(len(result.deliveries), 20)

    def test_public_envelopes_cannot_masquerade_as_tree_packets(self):
        tree, _ = runtime()
        packet = next(iter(tree.plan.trees))
        channel = tree.plan.trees[packet].channels[0]
        flit = tree.envelope(channel, packet, 0)
        self.assertIsInstance(flit, TreeFlit)
        self.assertFalse(hasattr(flit, "destination"))
        forged = flit.model_copy(update={"branch_id": "wrong"})
        with self.assertRaisesRegex(ValueError, "branch"):
            tree.link(channel).try_reserve(forged)
        self.assertTrue(tree.link(channel).lane_drained(TreeLaneIdentity(channel=channel)))
        self.assertEqual(Counter(e.action for e in tree.link(channel).all_events), {})


if __name__ == "__main__":
    unittest.main()
