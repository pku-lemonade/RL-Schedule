"""Finite link-kernel checks against independent service/credit timelines."""

import copy
import unittest

import simpy

from simulator_detailed.configs.schemas.torus_replay import (
    ChannelIdentity,
    PacketIdentity,
    TorusReplay,
)
from simulator_detailed.tests.test_torus import (
    profile_replay,
    small_graph,
    small_replay,
)
from simulator_detailed.tests.test_torus_contract import endpoint, literal
from simulator_detailed.torus import TorusPlan
from simulator_detailed.virtual_channel import (
    CreditToken,
    LinkContract,
    VirtualChannelLink,
)


def fixture(*, size=120, capacity=3, staging=4, quantum=2, width=128,
            noc_clock=1_000_000_000, aci_clock=500_000_000, spacing=2,
            propagation=3, credit=2):
    data = small_replay((2, 1)).model_dump(mode="json")
    data["slowdowns"] = []
    graph = small_graph()
    graph["attachments"].append({**graph["attachments"][0], "endpoint_id": "west", "router_id": "t2_0"})
    data["binding"]["endpoints"] = []
    for name in ("src", "dst", "west"):
        record = endpoint(0, name, ["request_source", "request_sink", "responder", "response_sink"])
        record["endpoint_id"] = name
        data["binding"]["endpoints"].append(record)
    data["traffic"] = [{
        "kind": "request_response", "transfer_id": name, "fabric_id": 0,
        "source": source, "destination": dest, "payload_bytes": size,
        "start_aci_cycles": 0, "burst_quantum_flits": quantum,
        "response_payload_bytes": size, "response_burst_quantum_flits": quantum,
    } for name, source, dest in (("a", "src", "dst"), ("a2", "src", "dst"),
                                ("b", "west", "dst"), ("c", "dst", "src"), ("d", "dst", "west"))]
    data["aci_clock"] = literal(aci_clock, "Hz")
    data["fabrics"][0]["noc_clock"] = literal(noc_clock, "Hz")
    for link in (data["fabrics"][0]["network_link"], data["fabrics"][0]["local_link"]):
        link.update(wire_bits_per_noc_cycle=literal(width, "bits_per_cycle"),
                    payload_bits_per_noc_cycle=literal(width, "bits_per_cycle"),
                    launch_interval_noc_cycles=spacing, propagation_noc_cycles=propagation,
                    credit_return_noc_cycles=credit, lane_capacity_flits=capacity,
                    staging_capacity_flits=staging, arbitration_quantum_flits=quantum)
    plan = TorusPlan.compile(TorusReplay.model_validate(data), graph)
    channel = ChannelIdentity(fabric_id=0, kind="network", identity="t0_0/x+")
    return plan, LinkContract.from_plan(plan, channel)


def packet(name):
    return PacketIdentity(transfer_id=name, traffic_class="response" if name in ("c", "d") else "request")


def send(env, link, identity, *, first_delay=0, gap=0):
    if first_delay:
        yield env.timeout(first_delay)
    count = link.contract.envelope(identity, 0).flit_count
    for index in range(count):
        flit = link.contract.envelope(identity, index)
        token = link.try_reserve(flit)
        while token is None:
            yield link.changed
            token = link.try_reserve(flit)
        link.make_ready(token)
        if gap:
            yield env.timeout(gap)


def consume(env, link, lane, count, *, service=0, start=0, received=None):
    if start:
        yield env.timeout(start)
    for _ in range(count):
        token = link.take(lane)
        while token is None:
            yield link.changed
            token = link.take(lane)
        if received is not None:
            received.append((env.now, token.envelope.packet.transfer_id, token.envelope.flit_index))
        yield env.timeout(service)
        link.release(token)


class VirtualChannelTests(unittest.TestCase):
    def assert_accounted(self, link):
        snapshot = link.inspect()
        stages = snapshot["tokens"]
        self.assertEqual(snapshot["staging_occupied_flits"],
                         sum(t["stage"] in ("serializing", "propagating") for t in stages))
        self.assertLessEqual(snapshot["staging_peak_flits"], snapshot["staging_capacity_flits"])
        self.assertLessEqual(sum(t["stage"] == "serializing" for t in stages), 1)
        for state in link.resources():
            self.assertEqual(state.capacity, state.available + state.occupied + state.pending_returns)
            self.assertEqual(state.occupied + state.pending_returns,
                             sum(t["lane"] == state.lane.model_dump(mode="json") for t in stages))
            self.assertLessEqual(state.peak_occupied, state.capacity)

    def run_checked(self, env, link):
        # Check every SimPy boundary, including internal staging/credit callbacks.
        steps = 0
        while env.peek() != float("inf"):
            env.step()
            self.assert_accounted(link)
            steps += 1
            self.assertLess(steps, 10000, "kernel failed to settle")

    def test_token_lifecycle_counts_receiver_and_held_storage(self):
        _, contract = fixture(size=24, capacity=1, staging=1, propagation=4, credit=6)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        flit = contract.envelope(packet("a"), 0)
        token = link.try_reserve(flit)
        self.assertIsNotNone(token)
        self.assertFalse(link.is_drained)
        link.make_ready(token)
        env.run()
        # 2 native serialization + 4 propagation at ratio 2 = arrival at 3 ACI.
        self.assertEqual(env.now, 3)
        self.assertEqual(link.inspect()["tokens"][0]["stage"], "received")
        self.assertIs(link.take(flit.lane), token)
        self.assertEqual(link.resources()[0].available, 0)
        self.assertEqual(link.inspect()["tokens"][0]["stage"], "held")
        before = copy.deepcopy(link.inspect()), link.events
        self.assertIsNone(link.try_reserve(contract.envelope(packet("a2"), 0)))
        self.assertEqual((link.inspect(), link.events), before)
        link.release(token)
        state = link.resources()[0]
        self.assertEqual((state.available, state.occupied, state.pending_returns), (0, 0, 1))
        self.assertFalse(link.is_drained)
        with self.assertRaises(ValueError):
            link.release(token)
        env.run()
        self.assertEqual(env.now, 6)
        self.assertTrue(link.is_drained)
        self.assertEqual(link.resources()[0].available, 1)
        actions = [e.action for e in link.events]
        self.assertEqual(actions, ["owner_acquire", "credit_reserve", "link_ready", "link_launch",
                                   "owner_release", "serialization_end", "propagation_start", "link_arrive",
                                   "link_take", "credit_release", "credit_return"])
        self.assertEqual({e.token_id for e in link.events}, {token.token_id})
        self.assert_accounted(link)

    def test_invalid_admission_has_no_side_effects(self):
        _, contract = fixture()
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        head = contract.envelope(packet("a"), 0)
        other = fixture(credit=3)[1]
        mutations = [other.envelope(packet("a"), 0),
                     head.model_copy(update={"plan_sha256": "f" * 64}),
                     head.model_copy(update={"hop_index": head.hop_index + 1}),
                     head.model_copy(update={"payload_bytes": 1}),
                     head.model_copy(update={"physical_bytes": 33}),
                     head.model_copy(update={"source": "west"}),
                     head.model_copy(update={"flit_count": 6}),
                     head.model_copy(update={"burst_quantum_flits": 1}),
                     head.model_copy(update={"lane": head.lane.model_copy(update={"dateline_phase": 1})}),
                     contract.envelope(packet("a"), 1)]
        for bad in mutations:
            with self.subTest(bad=bad):
                before = copy.deepcopy(link.inspect()), link.events, env.peek()
                with self.assertRaises(ValueError):
                    link.try_reserve(bad)
                self.assertEqual((link.inspect(), link.events, env.peek()), before)
        token = link.try_reserve(head)
        for action in (lambda: link.try_reserve(head), lambda: link.release(token),
                       lambda: link.make_ready(CreditToken(token.token_id, token.envelope))):
            before = copy.deepcopy(link.inspect()), link.events
            with self.assertRaises(ValueError):
                action()
            self.assertEqual((link.inspect(), link.events), before)
        link.make_ready(token)
        with self.assertRaises(ValueError):
            link.make_ready(token)

    def test_staging_overflow_and_order_preserve_reserved_budget(self):
        _, contract = fixture(capacity=3, staging=1)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        tokens = [link.try_reserve(contract.envelope(packet("a"), i)) for i in range(3)]
        with self.assertRaises(ValueError):
            link.make_ready(tokens[1])
        link.make_ready(tokens[0])
        link.make_ready(tokens[1])
        before = copy.deepcopy(link.inspect()), link.events
        self.assertIsNone(link.try_reserve(contract.envelope(packet("a"), 3)))
        self.assertEqual((link.inspect(), link.events), before)
        self.assert_accounted(link)
        env.run()
        self.assertEqual([t["stage"] for t in link.inspect()["tokens"]], ["received", "received", "reserved"])
        self.assertFalse(link.is_drained)

    def test_credit_blocked_lane_does_not_block_ready_lane(self):
        _, contract = fixture(size=72, capacity=1, staging=1)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        blocked = contract.envelope(packet("a"), 0).lane
        ready = contract.envelope(packet("c"), 0).lane
        env.process(send(env, link, packet("a")))
        env.process(send(env, link, packet("c"), first_delay=4))
        env.process(consume(env, link, blocked, 3, start=30))
        env.process(consume(env, link, ready, 3))
        self.run_checked(env, link)
        launches = [(e.time_aci_cycles, e.packet.transfer_id) for e in link.events if e.action == "link_launch"]
        self.assertEqual([p for t, p in launches if t < 30], ["a", "c", "c", "c"])
        self.assertTrue(link.is_drained)

    def test_idle_owner_releases_unused_physical_quantum(self):
        _, contract = fixture(size=72, capacity=3, staging=6, quantum=10, credit=0)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        env.process(send(env, link, packet("a"), gap=10))
        env.process(send(env, link, packet("c"), first_delay=2))
        for name in ("a", "c"):
            env.process(consume(env, link, contract.envelope(packet(name), 0).lane, 3))
        self.run_checked(env, link)
        launches = [(e.time_aci_cycles, e.packet.transfer_id) for e in link.events if e.action == "link_launch"]
        self.assertEqual(launches[:4], [(0, "a"), (2, "c"), (3, "c"), (4, "c")])
        self.assertTrue(link.is_drained)

    def test_idle_with_only_packet_owner_is_not_drained(self):
        _, contract = fixture(size=72, capacity=1, staging=1)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        token = link.try_reserve(contract.envelope(packet("a"), 0))
        link.make_ready(token)
        env.process(consume(env, link, token.envelope.lane, 1))
        self.run_checked(env, link)
        self.assertEqual(env.peek(), float("inf"))
        state = link.resources()[0]
        self.assertEqual((state.available, state.occupied, state.pending_returns), (1, 0, 0))
        self.assertEqual(state.owners, (packet("a"),))
        self.assertFalse(link.is_drained)

    def test_same_lane_packets_remain_fifo_and_cut_through(self):
        _, contract = fixture(size=240, capacity=2, staging=1, quantum=3)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        received = []
        for name in ("a", "a2"):
            env.process(send(env, link, packet(name)))
        lane = contract.envelope(packet("a"), 0).lane
        env.process(consume(env, link, lane, 20, service=2, received=received))
        self.run_checked(env, link)
        self.assertEqual([(p, i) for _, p, i in received], [(p, i) for p in ("a", "a2") for i in range(10)])
        a_tail = next(e.time_aci_cycles for e in link.events
                      if e.action == "link_launch" and e.packet.transfer_id == "a" and e.flit_index == 9)
        self.assertLess(received[0][0], a_tail)
        self.assertTrue(link.is_drained)

    def test_four_lanes_share_bandwidth_with_bounded_quantum(self):
        _, contract = fixture(size=120, capacity=5, staging=20, quantum=2, credit=0)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        for name in ("a", "b", "c", "d"):
            env.process(send(env, link, packet(name)))
            env.process(consume(env, link, contract.envelope(packet(name), 0).lane, 5))
        self.run_checked(env, link)
        launches = [e for e in link.events if e.action == "link_launch"]
        self.assertEqual([e.time_aci_cycles for e in launches], list(range(20)))
        self.assertEqual([e.packet.transfer_id for e in launches[:8]], list("aabbccdd"))
        self.assertEqual(link.inspect()["transmitted_channel_bytes"], 20 * 32)
        self.assertTrue(link.is_drained)

    def test_one_shared_stage_is_fair_with_unequal_packet_quanta(self):
        plan, base = fixture(size=120, capacity=5, staging=1, quantum=2, credit=0)
        data = plan.binding.contract.config.model_dump(mode="json")
        for traffic in data["traffic"]:
            quantum = {"a": 9, "a2": 1, "b": 1, "c": 3, "d": 1}[traffic["transfer_id"]]
            traffic.update(burst_quantum_flits=quantum, response_burst_quantum_flits=quantum)
        plan = TorusPlan.compile(TorusReplay.model_validate(data), plan.record.graph.model_dump(mode="json"))
        contract = LinkContract.from_plan(plan, base.channel)
        env = simpy.Environment()
        link = VirtualChannelLink(env, contract)
        for name in ("a", "b", "c", "d"):
            env.process(send(env, link, packet(name)))
            env.process(consume(env, link, contract.envelope(packet(name), 0).lane, 5))
        self.run_checked(env, link)
        launches = [e for e in link.events if e.action == "link_launch"]
        self.assertEqual([e.packet.transfer_id for e in launches[:6]], list("aabccd"))
        # One shared slot covers 1 ACI serialization + 1.5 propagation.
        self.assertEqual([e.time_aci_cycles for e in launches], [i * 2.5 for i in range(20)])
        self.assertEqual(link.inspect()["staging_peak_flits"], 1)
        self.assertTrue(link.is_drained)

    def test_tokens_cannot_cross_runtime_instances_or_return_twice(self):
        _, contract = fixture(size=24, capacity=1, staging=1, credit=0)
        env = simpy.Environment()
        left, right = VirtualChannelLink(env, contract), VirtualChannelLink(env, contract)
        token = left.try_reserve(contract.envelope(packet("a"), 0))
        other = right.try_reserve(contract.envelope(packet("a"), 0))
        self.assertEqual(token.token_id, other.token_id)
        before = copy.deepcopy(right.inspect()), right.events
        with self.assertRaises(ValueError):
            right.make_ready(token)
        self.assertEqual((right.inspect(), right.events), before)
        left.make_ready(token)
        env.run()
        self.assertIs(left.take(token.envelope.lane), token)
        left.release(token)
        env.run()
        before = copy.deepcopy(left.inspect()), left.events
        with self.assertRaises(ValueError):
            left.release(token)
        self.assertEqual((left.inspect(), left.events), before)

    def test_native_clock_width_timelines_and_delayed_final_drain(self):
        # Hand-derived native serialization: ceil(256/96)=3 and ceil(256/256)=1.
        for width, noc, spacing, expected_launch, expected_arrive, expected_drain in (
            (96, 1_000_000_000, 4, [0, 2, 4], [3, 5, 7], 9),
            (256, 250_000_000, 2, [0, 4, 8], [8, 12, 16], 21),
        ):
            with self.subTest(width=width):
                _, contract = fixture(size=49, capacity=3, staging=3, width=width,
                                      noc_clock=noc, spacing=spacing, propagation=3, credit=2)
                env = simpy.Environment()
                link = VirtualChannelLink(env, contract)
                received = []
                env.process(send(env, link, packet("a")))
                env.process(consume(env, link, contract.envelope(packet("a"), 0).lane, 3,
                                    service=1, received=received))
                self.run_checked(env, link)
                self.assertEqual([e.time_aci_cycles for e in link.events if e.action == "link_launch"], expected_launch)
                self.assertEqual([t for t, _, _ in received], expected_arrive)
                self.assertEqual(env.now, expected_drain)
                self.assertEqual(sum(e.payload_bytes for e in link.events if e.action == "link_launch"), 49)
                self.assertEqual(link.inspect()["transmitted_channel_bytes"], 96)
                self.assertTrue(link.is_drained)

    def test_small_capacity_reduces_throughput_without_enlargement(self):
        launches_by_capacity = {}
        for capacity in (1, 3):
            _, contract = fixture(size=72, capacity=capacity, staging=3, propagation=6, credit=2)
            env = simpy.Environment()
            link = VirtualChannelLink(env, contract)
            env.process(send(env, link, packet("a")))
            env.process(consume(env, link, contract.envelope(packet("a"), 0).lane, 3, service=1))
            self.run_checked(env, link)
            launches_by_capacity[capacity] = [e.time_aci_cycles for e in link.events if e.action == "link_launch"]
            self.assertEqual(link.resources()[0].capacity, capacity)
            self.assertTrue(link.is_drained)
        self.assertEqual(launches_by_capacity, {1: [0, 6, 12], 3: [0, 1, 2]})

    def test_local_and_network_overrides_and_profile_quantities(self):
        plan, base = fixture()
        data = plan.binding.contract.config.model_dump(mode="json")
        settings = copy.deepcopy(data["fabrics"][0]["network_link"])
        settings.update(lane_capacity_flits=7, payload_bits_per_noc_cycle=literal(64, "bits_per_cycle"),
                        launch_interval_noc_cycles=4)
        data["network_overrides"] = [{"fabric_id": 0, "link_id": base.channel.identity, "settings": settings}]
        data["local_overrides"] = [{"endpoint_id": "src", "direction": "inject", "settings": settings}]
        override = TorusPlan.compile(TorusReplay.model_validate(data), plan.record.graph.model_dump(mode="json"))
        for channel in (base.channel, ChannelIdentity(fabric_id=0, kind="inject", identity="src")):
            contract = LinkContract.from_plan(override, channel)
            self.assertEqual(contract.config.lane_capacity_flits, 7)
            self.assertEqual(contract.config.serialization_noc_cycles, 4)
            self.assertEqual(len(contract.lanes), 4 if channel.kind == "network" else 2)
        config, profile = profile_replay()
        config = config.model_copy(update={"slowdowns": ()})
        profile_plan = TorusPlan.compile(config, profile)
        channel = profile_plan.record.routes[0].hops[0].lane.channel
        contract = LinkContract.from_plan(profile_plan, channel)
        quantities = {q.field_path: q.value for q in profile_plan.record.quantities}
        self.assertEqual(contract.config.noc_clock_hz, quantities[f"fabrics.{channel.fabric_id}.noc_clock"])
        self.assertEqual(contract.config.physical_flit_bytes, 32)
        for channel in (ChannelIdentity(fabric_id=7, kind="network", identity="t0_0/x+"),
                        ChannelIdentity(fabric_id=0, kind="network", identity="missing"),
                        ChannelIdentity(fabric_id=0, kind="inject", identity="missing")):
            with self.assertRaises(ValueError):
                LinkContract.from_plan(plan, channel)

    def test_slowdown_schedule_is_bound_to_the_network_contract(self):
        plan, contract = fixture()
        data = plan.binding.contract.config.model_dump(mode="json")
        data["slowdowns"] = [{"failure_id": "slow", "fabric_id": 0, "link_id": contract.channel.identity,
                              "start_aci_cycles": 0, "end_aci_cycles": 5, "factor": 2}]
        plan = TorusPlan.compile(TorusReplay.model_validate(data), plan.record.graph.model_dump(mode="json"))
        slowed = LinkContract.from_plan(plan, contract.channel)
        self.assertEqual(slowed.config.slowdowns, (("slow", 0.0, 5.0, 2.0),))


if __name__ == "__main__":
    unittest.main()
