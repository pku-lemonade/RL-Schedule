"""Cut-through torus transport and bounded causal response checks."""

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

    def response_plan(self, *, requests=1, response_service=3, sink_service=1):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        data["binding"]["endpoints"][0].update(
            roles=["request_source", "response_sink"],
            injection_queue_capacity_packets=1,
        )
        data["binding"]["endpoints"][0]["sink_service"]["cycles"] = sink_service
        data["binding"]["endpoints"][1].update(
            roles=["responder"], inject_port="local", injection_queue_capacity_packets=1,
            response_queue_capacity_packets=1,
            response_service={"cycles": response_service, "timebase": "noc",
                              "evidence": {"status": "assumed", "description": "test"}},
        )
        traffic = []
        for index in range(requests):
            traffic.append({"kind": "request_response", "transfer_id": f"read-{index}",
                            "fabric_id": 0, "source": "src", "destination": "dst",
                            "payload_bytes": 9, "start_aci_cycles": 0,
                            "burst_quantum_flits": 1, "response_payload_bytes": 25,
                            "response_burst_quantum_flits": 1})
        data["traffic"] = traffic
        return TorusPlan.compile(TorusReplay.model_validate(data), small_graph())

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
        result = TorusTransport(self.response_plan()).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual({packet.packet.traffic_class for packet in result.packets}, {"request", "response"})
        slowdown_data = small_replay((2, 1)).model_dump(mode="json")
        slowdown_data["slowdowns"] = [{"failure_id": "f", "fabric_id": 0, "link_id": "t0_0/x+",
                                        "start_aci_cycles": 0, "end_aci_cycles": 3, "factor": 2}]
        result = TorusTransport(TorusPlan.compile(TorusReplay.model_validate(slowdown_data), small_graph())).run()
        self.assertEqual(result.status, "complete")
        failure_events = [event for event in result.trace if event.action.startswith("failure_")]
        self.assertEqual([event.action for event in failure_events], ["failure_start", "failure_end"])
        self.assertEqual(failure_events[0].failure_id, "f")

    def test_slowdown_half_open_snapshot_and_recovery(self):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["slowdowns"] = [{"failure_id": "slow", "fabric_id": 0, "link_id": "t0_0/x+",
                              "start_aci_cycles": 0, "end_aci_cycles": 3, "factor": 3}]
        data["traffic"][0]["payload_bytes"] = 49
        result = TorusTransport(TorusPlan.compile(TorusReplay.model_validate(data), small_graph())).run()
        self.assertEqual(result.status, "complete")
        launches = [event for event in result.trace if event.action == "link_launch"
                    and event.channel is not None and event.channel.identity == "t0_0/x+"]
        self.assertGreaterEqual(len(launches), 2)
        self.assertEqual(launches[0].launch_factor, 3)
        self.assertEqual(launches[-1].launch_factor, 1)
        self.assertEqual(launches[0].failure_id, "slow")
        self.assertIsNone(launches[-1].failure_id)

    def test_recovery_preserves_lane_arrival_order(self):
        config = small_replay((2, 1))
        data = config.model_dump(mode="json")
        data["fabrics"][0]["network_link"]["propagation_noc_cycles"] = 20
        data["fabrics"][0]["network_link"]["lane_capacity_flits"] = 3
        data["fabrics"][0]["network_link"]["staging_capacity_flits"] = 3
        data["slowdowns"] = [{"failure_id": "slow", "fabric_id": 0, "link_id": "t0_0/x+",
                              "start_aci_cycles": 0, "end_aci_cycles": 3, "factor": 3}]
        data["traffic"][0]["payload_bytes"] = 49
        result = TorusTransport(TorusPlan.compile(TorusReplay.model_validate(data), small_graph())).run()
        self.assertEqual(result.status, "complete")
        arrivals = [event.flit_index for event in result.trace
                     if event.action == "link_arrive" and event.channel is not None
                     and event.channel.identity == "t0_0/x+"]
        waits = [event for event in result.trace if event.action == "arrival_order_wait"
                 and event.channel is not None and event.channel.identity == "t0_0/x+"]
        self.assertEqual(arrivals, [0, 1, 2])
        self.assertTrue(waits)

    def test_slowdown_target_must_be_enabled_directed_link(self):
        config = small_replay((2, 1)).model_dump(mode="json")
        config["slowdowns"] = [{"failure_id": "missing", "fabric_id": 0, "link_id": "local",
                                "start_aci_cycles": 0, "end_aci_cycles": 3, "factor": 2}]
        with self.assertRaisesRegex(ValueError, "inter-router link"):
            TorusTransport(TorusPlan.compile(TorusReplay.model_validate(config), small_graph()))

    def test_causal_response_is_generated_once_after_request_service(self):
        result = TorusTransport(self.response_plan()).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.packets), 2)
        self.assertEqual(result.expected_payload_bytes, 34)
        self.assertEqual(result.received_payload_bytes, 34)
        self.assertEqual({packet.packet.traffic_class for packet in result.packets}, {"request", "response"})
        ready = [event for event in result.trace if event.action == "response_ready"]
        self.assertEqual(len(ready), 1)
        descriptors = [resource for resource in result.resources if resource.kind == "response_descriptors"]
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0].peak_occupied, 1)
        self.assertTrue(descriptors[0].is_drained)

    def test_descriptor_capacity_one_serializes_multiple_responses(self):
        result = TorusTransport(self.response_plan(requests=2, response_service=5, sink_service=7)).run(max_aci_cycles=10_000)
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.packets), 4)
        responses = [packet for packet in result.packets if packet.packet.traffic_class == "response"]
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(packet.received_payload_bytes == 25 for packet in responses))
        self.assertEqual(len([event for event in result.trace if event.action == "response_ready"]), 2)
        descriptor = next(resource for resource in result.resources if resource.kind == "response_descriptors")
        self.assertEqual(descriptor.peak_occupied, 1)
        self.assertTrue(descriptor.is_drained)

    def test_response_service_pending_is_incomplete_with_descriptor_owner(self):
        result = TorusTransport(self.response_plan(response_service=10_000)).run(max_aci_cycles=20)
        self.assertEqual(result.status, "incomplete")
        self.assertIn(result.reason, {"cycle_limit", "idle_with_pending"})
        request = next(packet for packet in result.packets if packet.packet.traffic_class == "request")
        response = next(packet for packet in result.packets if packet.packet.traffic_class == "response")
        self.assertEqual(request.received_payload_bytes, 9)
        self.assertIsNone(response.first_injection_aci_cycles)
        descriptor = next(resource for resource in result.resources if resource.kind == "response_descriptors")
        self.assertEqual(descriptor.occupied, 1)
        self.assertFalse(descriptor.is_drained)

    def test_request_response_drains_on_both_assumed_fabrics(self):
        config, profile = profile_replay()
        data = config.model_dump(mode="json")
        data["slowdowns"] = []
        plan = TorusPlan.compile(TorusReplay.model_validate(data), profile)
        result = TorusTransport(plan).run(max_aci_cycles=100_000)
        self.assertEqual(result.status, "complete")
        self.assertEqual({packet.packet.traffic_class for packet in result.packets}, {"request", "response"})
        self.assertEqual(result.received_payload_bytes, result.expected_payload_bytes)
        self.assertEqual(len([event for event in result.trace if event.action == "response_ready"]), 2)
        self.assertEqual({resource.fabric_id for resource in result.resources
                          if resource.kind == "response_descriptors"}, {0, 1})

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
