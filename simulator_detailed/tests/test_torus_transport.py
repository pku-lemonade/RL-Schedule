"""Cut-through torus transport checks; causal responses and slowdown are later parts."""

import unittest

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
from simulator_detailed.torus import TorusPlan
from simulator_detailed.torus_transport import RouterPipeline, TorusTransport


class TorusTransportTests(unittest.TestCase):
    def plan(self, *, same_router=False, size=120):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        data["traffic"][0].update(payload_bytes=size, burst_quantum_flits=2)
        if same_router:
            graph = small_graph()
            graph["routers"][0]["ports"].append({"port_id": "local2", "kind": "local"})
            graph["attachments"][1]["router_id"] = graph["attachments"][0]["router_id"]
            graph["attachments"][1]["inject_port"] = "local2"
            graph["attachments"][1]["eject_port"] = "local2"
            data["binding"]["endpoints"][1]["eject_port"] = "local2"
        else:
            graph = small_graph()
        return TorusPlan.compile(TorusReplay.model_validate(data), graph)

    def test_local_same_router_delivery_and_counts(self):
        result = TorusTransport(self.plan(same_router=True, size=9)).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.reason, "drained")
        packet = result.packets[0]
        self.assertEqual((packet.expected_payload_bytes, packet.received_payload_bytes), (9, 9))
        self.assertEqual((packet.expected_flits, packet.received_flits), (1, 1))
        self.assertLessEqual(packet.first_injection_aci_cycles, packet.first_ejection_aci_cycles)
        self.assertEqual(result.transmitted_channel_bytes, packet.physical_bytes * 2)
        self.assertTrue(all(resource.is_drained for resource in result.resources))

    def test_cut_through_wrap_and_long_packet_with_small_buffers(self):
        result = TorusTransport(self.plan(size=240)).run()
        self.assertEqual(result.status, "complete")
        packet = result.packets[0]
        self.assertEqual((packet.expected_flits, packet.received_flits), (10, 10))
        self.assertEqual(packet.received_payload_bytes, 240)
        launches = [event for event in result.trace if event.action == "link_launch"]
        self.assertEqual(len(launches), 10 * 4)
        first_eject = packet.first_ejection_aci_cycles
        source_tail = max(event.time_aci_cycles for event in launches
                          if event.channel is not None and event.channel.kind == "inject")
        self.assertLess(first_eject, source_tail)
        self.assertEqual(result.transmitted_channel_bytes, 10 * 4 * 32)
        self.assertTrue(all(resource.is_drained for resource in result.resources))

    def test_two_packets_share_route_and_completion_is_resource_complete(self):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        data["traffic"] = [
            {**data["traffic"][0], "transfer_id": "first", "payload_bytes": 49},
            {**data["traffic"][0], "transfer_id": "second", "payload_bytes": 49, "start_aci_cycles": 0},
        ]
        result = TorusTransport(TorusPlan.compile(TorusReplay.model_validate(data), small_graph())).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.packets), 2)
        self.assertEqual(result.received_payload_bytes, 98)
        self.assertEqual(result.transmitted_channel_bytes, 2 * 3 * 4 * 32)

    def test_cycle_limit_reports_pending_resources(self):
        result = TorusTransport(self.plan(size=240)).run(max_aci_cycles=1)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.reason, "cycle_limit")
        self.assertTrue(any(not resource.is_drained for resource in result.resources))
        self.assertLess(result.received_payload_bytes, result.expected_payload_bytes)

    def test_two_fabric_profile_routes_drain_without_cross_fabric_sharing(self):
        config, profile = profile_replay()
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        for traffic in data["traffic"]:
            traffic["kind"] = "one_way"
            traffic.pop("response_payload_bytes", None)
            traffic.pop("response_burst_quantum_flits", None)
            traffic["payload_bytes"] = 24
        plan = TorusPlan.compile(TorusReplay.model_validate(data), profile)
        result = TorusTransport(plan).run(max_aci_cycles=10_000)
        self.assertEqual(result.status, "complete")
        self.assertEqual({packet.fabric_id for packet in result.packets}, {0, 1})
        self.assertEqual(result.received_payload_bytes, 48)
        self.assertTrue(all(resource.is_drained for resource in result.resources))

    def test_payload_can_arrive_before_delayed_credits_drain(self):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        for link in (data["fabrics"][0]["network_link"], data["fabrics"][0]["local_link"]):
            link["credit_return_noc_cycles"] = 100
        plan = TorusPlan.compile(TorusReplay.model_validate(data), small_graph())
        result = TorusTransport(plan).run(max_aci_cycles=50)
        self.assertEqual(result.status, "incomplete")
        self.assertIn(result.reason, {"cycle_limit", "idle_with_pending"})
        self.assertEqual(result.received_payload_bytes, result.expected_payload_bytes)
        self.assertTrue(any(resource.pending_returns for resource in result.resources))

    def test_response_and_slowdown_modes_are_not_silently_reinterpreted(self):
        response_data = small_replay((2, 1)).model_dump(mode="json")
        response_data["slowdowns"] = []
        response_data["traffic"][0]["kind"] = "request_response"
        response_data["traffic"][0]["response_payload_bytes"] = 9
        response_data["traffic"][0]["response_burst_quantum_flits"] = 1
        with self.assertRaisesRegex(ValueError, "response"):
            TorusTransport(TorusPlan.compile(TorusReplay.model_validate(response_data), small_graph()))
        slowdown_data = small_replay((2, 1)).model_dump(mode="json")
        slowdown_data["slowdowns"] = [{"failure_id": "f", "fabric_id": 0, "link_id": "t0_0/x+",
                                        "start_aci_cycles": 0, "end_aci_cycles": 3, "factor": 2}]
        with self.assertRaisesRegex(ValueError, "slowdown"):
            TorusTransport(TorusPlan.compile(TorusReplay.model_validate(slowdown_data), small_graph()))

    def test_router_pipeline_overlaps_independent_outputs_with_finite_capacity(self):
        import simpy

        env = simpy.Environment()
        stage = RouterPipeline(env, fabric_id=0, router_id="r", transfer_aci_cycles=4,
                               initiation_aci_cycles=1, capacity=2)
        outputs = [ChannelIdentity(fabric_id=0, kind="network", identity=name)
                   for name in ("r/x+", "r/y+")]
        packet_a = PacketIdentity(transfer_id="a", traffic_class="request")
        packet_b = PacketIdentity(transfer_id="b", traffic_class="request")
        starts = []

        def transfer(output, packet):
            while not stage.try_start(output, packet):
                yield stage.changed
            starts.append((output.identity, env.now))
            yield env.timeout(stage.transfer_aci_cycles)
            stage.finish(output, packet)

        env.process(transfer(outputs[0], packet_a))
        env.process(transfer(outputs[1], packet_b))
        env.run()
        self.assertEqual(starts, [("r/x+", 0), ("r/y+", 1)])
        self.assertEqual(stage.peak, 2)
        self.assertTrue(stage.is_drained)


if __name__ == "__main__":
    unittest.main()
