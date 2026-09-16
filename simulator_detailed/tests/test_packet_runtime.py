"""Shared-kernel compatibility and independent bounded memory-wire checks."""

from __future__ import annotations

import copy
import unittest
from collections import Counter
from itertools import pairwise
from unittest.mock import patch

import simpy

from simulator_detailed.configs.schemas.torus_replay import PacketIdentity
from simulator_detailed.memory_transport import (
    MemoryLinkContract,
    MemoryTransportConfig,
    MemoryTransportPlan,
)
from simulator_detailed.packet_runtime import PacketTransport, PacketTransportResult
from simulator_detailed.tests import test_torus_transport
from simulator_detailed.tests.test_memory_packets import (
    EVIDENCE,
    FIXTURE,
    memory_documents,
    wire_plan,
)
from simulator_detailed.topology import content_digest
from simulator_detailed.torus_transport import TorusTransport


def transport_config(*, physical=32, noc_clock=500_000_000.0, width=256, interval=1,
                     credit=1, propagation=0, capacity=1, staging=1):
    link = {"aci_clock_hz": 500_000_000.0, "noc_clock_hz": noc_clock, "physical_flit_bytes": physical,
            "wire_bits_per_noc_cycle": width, "payload_bits_per_noc_cycle": width,
            "launch_interval_noc_cycles": interval, "propagation_noc_cycles": propagation,
            "credit_return_noc_cycles": credit, "lane_capacity_flits": capacity,
            "staging_capacity_flits": capacity, "arbitration_quantum_flits": 1}
    return MemoryTransportConfig.model_validate({
        "fabrics": [{"fabric_id": f, "network_link": link, "local_link": link,
                     "router": {"transfer_aci_cycles": 1, "initiation_aci_cycles": 1, "capacity_flits": 1}}
                    for f in (0, 1)],
        "endpoint_queue_capacity_packets": 1, "endpoint_staging_capacity_flits": staging,
        "burst_quantum_flits": 1, "evidence": EVIDENCE})


class Hooks:
    def __init__(self, env, *, produce=0, consume=1, producer_gate=None, consumer_gate=None):
        self.env = env
        self.produce_delay, self.consume_delay = produce, consume
        self.producer_gate, self.consumer_gate = producer_gate, consumer_gate
        self.produced = []
        self.consumed = []

    def produce(self, flit):
        if self.producer_gate is not None:
            yield self.producer_gate
        yield self.env.timeout(self.produce_delay)
        self.produced.append((flit.packet, flit.flit_index, flit.payload_bytes, self.env.now))

    def consume(self, flit):
        if self.consumer_gate is not None:
            yield self.consumer_gate
        yield self.env.timeout(self.consume_delay)
        self.consumed.append((flit.packet, flit.flit_index, flit.payload_bytes, self.env.now))


def start_inventory(runtime, *, reverse_requests=False, response_delay=0):
    def submit(definition):
        if definition.after_packet is not None:
            yield runtime.receipt(definition.after_packet)
            yield runtime.env.timeout(response_delay)
        while not runtime.try_submit(definition.packet):
            yield runtime.changed

    definitions = list(runtime.plan.packets.values())
    if reverse_requests:
        definitions.reverse()
    for definition in definitions:
        runtime.env.process(submit(definition))


def execute(wire, config=None, *, produce=0, consume=1, response_delay=0, reverse=False):
    plan = MemoryTransportPlan.compile(wire, config or transport_config())
    env = simpy.Environment()
    hooks = Hooks(env, produce=produce, consume=consume)
    runtime = PacketTransport(env, plan.network, hooks)
    start_inventory(runtime, reverse_requests=reverse, response_delay=response_delay)
    return runtime, hooks, runtime.run(max_aci_cycles=100_000)


class SharedTransportCompatibilityTests(unittest.TestCase):
    def test_exact_v2_results_match_pre_extraction_fingerprints(self):
        fixture = test_torus_transport.TorusTransportTests()
        # Complete serialized results from commit 4c5f416: includes every trace
        # action, token identity, byte count, timestamp and resource snapshot.
        cases = (
            (fixture.plan(same_router=True, size=9), "f0ea85ad0d2296a7f1355aa6fbae146ff56208e585969052e55f76ef90581482"),
            (fixture.plan(size=240), "6b0d7e49c68f6987447ac13324a84fb6604f9100aaf230c91f82b3d24fc1643c"),
            (fixture.response_plan(requests=2, response_service=5, sink_service=7),
             "6c6304a42de8b98106fa71a70c6867448151b41d2cd94879eb7fae9c20db3e81"),
        )
        for plan, expected in cases:
            self.assertEqual(content_digest(TorusTransport(plan).run(max_aci_cycles=10_000).model_dump(mode="json")), expected)


class MemoryPacketRuntimeTests(unittest.TestCase):
    def assert_conserved(self, result, *, complete=True):
        self.assertEqual(result.status, "complete" if complete else "incomplete")
        for resource in result.resources:
            self.assertEqual(resource.available + resource.occupied + resource.pending_returns, resource.capacity)
            self.assertLessEqual(resource.peak_occupied, resource.capacity)
            if complete:
                self.assertTrue(resource.is_drained)
        for buffer in result.endpoint_buffers:
            self.assertEqual(buffer.available + buffer.occupied, buffer.capacity)
            self.assertLessEqual(buffer.peak_occupied, buffer.capacity)
            if complete:
                self.assertEqual(buffer.occupied, 0)
        # Independently replay all lease transitions, catching uncharged storage,
        # double release and false high-water marks, including incomplete runs.
        owners, peaks = {}, Counter()
        for event in result.endpoint_trace:
            key = event.endpoint_id, event.traffic_class, event.kind
            active = owners.setdefault(key, set())
            if event.action == "acquire":
                self.assertNotIn(event.lease_id, active)
                active.add(event.lease_id)
            else:
                self.assertIn(event.lease_id, active)
                active.remove(event.lease_id)
            self.assertEqual(len(active), event.occupied)
            peaks[key] = max(peaks[key], event.occupied)
        for buffer in result.endpoint_buffers:
            key = buffer.endpoint_id, buffer.traffic_class, buffer.kind
            self.assertEqual(buffer.occupied, len(owners.get(key, set())))
            self.assertEqual(buffer.peak_occupied, peaks[key])

    def test_one_byte_read_write_headers_consume_real_channels_and_router_work(self):
        for kind, flits, channel_bytes in (("write_posted", 2, 384), ("write_acknowledged", 3, 512), ("read", 3, 448)):
            with self.subTest(kind=kind):
                runtime, hooks, result = execute(wire_plan(*memory_documents(kind, 1)))
                self.assert_conserved(result)
                self.assertEqual(result.transmitted_channel_bytes, channel_bytes)
                self.assertEqual(result.received_useful_bytes, 1)
                self.assertEqual(len(hooks.consumed), flits)
                launches = [e for e in result.trace if e.action == "link_launch"]
                headers = [e for e in launches if e.flit_index == 0]
                self.assertTrue(headers)
                self.assertTrue(all(e.payload_bytes == 0 and e.physical_bytes == 32 and e.duration_aci_cycles == 1 for e in headers))
                self.assertEqual(sum(e.physical_bytes for e in launches), channel_bytes)
                self.assertEqual(sum(e.action == "transfer_start" for e in result.trace), len(launches) - flits)
                self.assertEqual(len({(p, i) for p, i, _, _ in hooks.consumed}), flits)
                self.assertEqual(result.memory_service, "unsupported")
                self.assertEqual(result.execution, "wire_packets_only")
                self.assertTrue(all(link.env is runtime.env for link in runtime.links.values()))
                self.assertEqual(result, PacketTransportResult.model_validate_json(result.model_dump_json()))

    def test_large_segmented_packets_match_literal_channel_oracles(self):
        for case in FIXTURE["channel_cases"]:
            for kind in ("write_posted", "write_acknowledged", "read"):
                with self.subTest(fabric=case["fabric"], kind=kind):
                    wire = wire_plan(*memory_documents(kind, case["size"], case["fabric"]))
                    _, hooks, result = execute(wire, consume=3, reverse=True)
                    self.assert_conserved(result)
                    self.assertEqual(result.transmitted_channel_bytes, case[kind])
                    self.assertEqual(result.received_useful_bytes, 8193)
                    self.assertEqual(sum(n for _, _, n, _ in hooks.consumed), 8193)
                    self.assertEqual(len(hooks.consumed), 259 if kind == "write_posted" else 261)
                    self.assertEqual(len({(p, i) for p, i, _, _ in hooks.consumed}), len(hooks.consumed))
                    # Reverse submission demonstrates actual token routing, not
                    # a per-packet forwarder consuming a different packet's flit.
                    injection = [e for e in result.trace if e.action == "link_launch" and e.channel.kind == "inject"]
                    self.assertIn(',1,', injection[0].packet.transfer_id)

    def test_generic_multiple_headers_native_clock_and_partial_data(self):
        doc, graph = memory_documents("read", 25)
        doc["packet"].update(physical_flit_bytes=16, data_capacity_bytes=8, header_flits=2,
                             max_segment_payload_bytes=24, address_alignment_bytes=4)
        config = transport_config(physical=16, noc_clock=1_000_000_000.0, width=64, interval=2,
                                  propagation=2, credit=2, staging=2)
        _, hooks, result = execute(wire_plan(doc, graph), config)
        self.assert_conserved(result)
        # Two read headers of 2 flits, responses of 5 and 3 flits.
        self.assertEqual(result.transmitted_channel_bytes, 4 * 16 * 6 + 8 * 16 * 4)
        self.assertEqual(result.received_useful_bytes, 25)
        self.assertEqual(Counter(n for _, _, n, _ in hooks.consumed), {0: 8, 8: 3, 1: 1})
        self.assertTrue(all(e.duration_aci_cycles == 1 for e in result.trace if e.action == "link_launch"))
        self.assertTrue(any(b.peak_occupied == 2 for b in result.endpoint_buffers))

    def test_same_router_analytical_timeline(self):
        for kind, completion in (("write_posted", 7), ("write_acknowledged", 11), ("read", 11)):
            doc, graph = memory_documents(kind, 1)
            doc["operations"][0]["source"]["buffer_id"] = "local"
            doc["operations"][0]["destination"]["buffer_id"] = "local"
            doc["operations"][0]["destination"]["offset_bytes"] = 32
            _, _, result = execute(wire_plan(doc, graph), transport_config(credit=0))
            self.assert_conserved(result)
            self.assertEqual(result.elapsed_aci_cycles, completion)

    def test_response_is_causal_and_duplicate_foreign_submission_is_rejected(self):
        wire = wire_plan(*memory_documents("read", 33))
        plan = MemoryTransportPlan.compile(wire, transport_config())
        env = simpy.Environment()
        runtime = PacketTransport(env, plan.network, Hooks(env, consume=4))
        request, response = list(plan.network.packets)
        before = runtime.snapshot().model_dump()
        self.assertFalse(runtime.try_submit(response))
        self.assertEqual(runtime.snapshot().model_dump(), before)
        with self.assertRaises(ValueError):
            runtime.try_submit(PacketIdentity(transfer_id="foreign", traffic_class="request"))
        self.assertTrue(runtime.try_submit(request))
        with self.assertRaises(ValueError):
            runtime.try_submit(request)

        def response_driver():
            yield runtime.receipt(request)
            yield env.timeout(5)
            self.assertTrue(runtime.try_submit(response))
        env.process(response_driver())
        result = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(result)
        launches = [e for e in result.trace if e.action == "link_launch" and e.packet == response]
        completed_request = next(p for p in result.packets if p.packet == request)
        self.assertGreaterEqual(launches[0].time_aci_cycles, completed_request.completion_aci_cycles + 5)
        self.assertEqual(sum(e.channel.kind == "inject" and e.flit_index == 0 for e in launches), 1)

    def test_blocked_producer_and_consumer_keep_charged_storage(self):
        for side in ("producer", "consumer"):
            with self.subTest(side=side):
                plan = MemoryTransportPlan.compile(wire_plan(*memory_documents("write_posted", 513)), transport_config())
                env = simpy.Environment()
                gate = env.event()
                hooks = Hooks(env, **{f"{side}_gate": gate})
                runtime = PacketTransport(env, plan.network, hooks)
                start_inventory(runtime)
                blocked = runtime.run(max_aci_cycles=1000)
                self.assert_conserved(blocked, complete=False)
                self.assertEqual(blocked.reason, "idle_with_pending")
                kind = "tx_staging" if side == "producer" else "rx_staging"
                self.assertTrue(any(b.kind == kind and b.occupied == 1 for b in blocked.endpoint_buffers))
                self.assertTrue(blocked.pending_packets)
                if side == "producer":
                    self.assertEqual(blocked.transmitted_channel_bytes, 0)
                else:
                    self.assertTrue(any(r.occupied for r in blocked.resources))
                    self.assertEqual(len(hooks.consumed), 0)
                gate.succeed()
                complete = runtime.run(max_aci_cycles=10_000)
                self.assert_conserved(complete)
                self.assertEqual(complete.received_useful_bytes, 513)

    def test_credit_drain_horizon_and_resume(self):
        wire = wire_plan(*memory_documents("write_posted", 1))
        plan = MemoryTransportPlan.compile(wire, transport_config(credit=100, capacity=2))
        env = simpy.Environment()
        runtime = PacketTransport(env, plan.network, Hooks(env))
        start_inventory(runtime)
        early = runtime.run(max_aci_cycles=50)
        self.assert_conserved(early, complete=False)
        self.assertEqual(early.reason, "cycle_limit")
        self.assertEqual(early.received_useful_bytes, 1)
        self.assertFalse(early.pending_packets)
        self.assertTrue(any(r.pending_returns for r in early.resources))
        late = runtime.run(max_aci_cycles=1000)
        self.assert_conserved(late)
        self.assertEqual(early.transmitted_channel_bytes, late.transmitted_channel_bytes)

    def test_memory_envelope_mutations_fail_before_credit_or_event_changes(self):
        wire = wire_plan(*memory_documents("read", 33))
        plan = MemoryTransportPlan.compile(wire, transport_config())
        env = simpy.Environment()
        runtime = PacketTransport(env, plan.network, Hooks(env))
        identity = wire.packets[0].packet.identity
        memory = wire.envelope(identity, 0, 0)
        key = memory.lane.channel.model_dump_json()
        contract = plan.network.links[key]
        self.assertIsInstance(contract, MemoryLinkContract)
        original = contract.admit_memory(memory)
        link = runtime.links[key]
        before = link.inspect()
        mutations = [{"payload_bytes": 1}, {"physical_bytes": 16}, {"plan_sha256": "0" * 64},
                     {"flit_count": 2}, {"hop_index": 1}, {"burst_quantum_flits": 9}, {"flit_index": 1}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                link.try_reserve(original.model_copy(update=mutation))
            self.assertEqual(link.inspect(), before)
            self.assertEqual(link.events, ())
        with self.assertRaises(ValueError):
            contract.admit_memory(memory.model_copy(update={"useful_bytes": 1}))
        token = link.try_reserve(original)
        self.assertIsNotNone(token)
        self.assertEqual(token.envelope.payload_bytes, 0)
        with self.assertRaises(ValueError):
            link.try_reserve(original)

    def test_invalid_transport_service_fails_during_pure_compilation(self):
        wire = wire_plan(*memory_documents())
        mutations = [
            lambda c: c["fabrics"][0]["local_link"].update(aci_clock_hz=1),
            lambda c: c["fabrics"][0]["network_link"].update(physical_flit_bytes=16),
            lambda c: c["fabrics"][0]["network_link"].update(payload_bits_per_noc_cycle=0),
            lambda c: c.update(endpoint_staging_capacity_flits=0),
            lambda c: c.update(endpoint_queue_capacity_packets=True),
            lambda c: c["fabrics"].pop(),
            lambda c: c["fabrics"][0]["network_link"].update(slowdowns=[["f", 0, 10, 2]]),
        ]
        for mutate in mutations:
            doc = transport_config().model_dump(mode="json")
            mutate(doc)
            with self.subTest(mutate=mutate), patch("simpy.Environment", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                MemoryTransportPlan.compile(wire, MemoryTransportConfig.model_validate(doc))

    def test_mixed_dual_fabric_slowdown_and_one_slot_liveness(self):
        doc, graph = memory_documents("write_acknowledged", 513)
        base = doc["operations"][0]
        doc["operations"] = []
        for index in range(8):
            op = copy.deepcopy(base)
            op.update(operation_id=f"mixed-{index}", fabric_id=index % 2,
                      kind=("read", "write_posted", "write_acknowledged")[index % 3])
            if op["kind"] == "read":
                op["source"]["buffer_id"], op["destination"]["buffer_id"] = "remote", "local"
            doc["operations"].append(op)
        wire = wire_plan(doc, graph)
        config = transport_config(propagation=2, credit=3).model_dump(mode="json")
        slow = copy.deepcopy(config["fabrics"][0]["network_link"])
        slow["slowdowns"] = [["directed", 0, 70, 3]]
        config["overrides"] = [{"channel": {"fabric_id": 0, "kind": "network", "identity": "t0_0/x+"}, "settings": slow}]
        runtime, hooks, result = execute(wire, MemoryTransportConfig.model_validate(config), produce=0.5, consume=7,
                                         response_delay=11, reverse=True)
        self.assert_conserved(result)
        self.assertEqual(result.received_useful_bytes, 8 * 513)
        self.assertEqual(result.transmitted_channel_bytes, sum(p.planned_channel_bytes for p in wire.packets))
        self.assertEqual(len({(p, i) for p, i, _, _ in hooks.consumed}), len(hooks.consumed))
        self.assertTrue(all(p.completion_aci_cycles is not None for p in result.packets))
        affected = [e for e in result.trace if e.action == "link_launch" and e.channel.fabric_id == 0 and e.channel.identity == "t0_0/x+"]
        self.assertEqual({e.launch_factor for e in affected}, {1, 3})
        self.assertTrue(all(e.launch_factor == 1 for e in result.trace if e.action == "link_launch" and e.channel.fabric_id == 1))
        for link in runtime.links.values():
            self.assertLessEqual(link.inspect()["staging_peak_flits"], 1)
        # Every physical serializer is shared across its classes/lanes.
        by_channel = {}
        for event in result.trace:
            if event.action == "link_launch":
                by_channel.setdefault(event.channel, []).append(event)
        for launches in by_channel.values():
            for first, second in pairwise(launches):
                self.assertGreaterEqual(second.time_aci_cycles, first.time_aci_cycles + first.duration_aci_cycles)

    def test_competing_initiators_merge_routes_without_packet_mixup_or_starvation(self):
        doc, graph = memory_documents("write_acknowledged", 513)
        graph["resources"].append({"resource_id": "l1-b", "kind": "local_sram",
                                   "owner_tile_id": "t1_0", "capacity_bytes": 65536})
        doc["resources"].append({**copy.deepcopy(doc["resources"][1]), "resource_id": "l1-b"})
        doc["buffers"].append({**copy.deepcopy(doc["buffers"][0]), "buffer_id": "local-b", "resource_id": "l1-b"})
        for fabric in (0, 1):
            attachment = {**copy.deepcopy(graph["attachments"][2 * fabric]), "endpoint_id": f"peer{fabric}",
                          "router_id": "t1_0", "resource_ids": ["l1-b"]}
            graph["attachments"].append(attachment)
            doc["endpoints"].append({**copy.deepcopy(doc["endpoints"][2 * fabric]),
                                     "endpoint_id": f"peer{fabric}", "router_id": "t1_0", "resource_ids": ["l1-b"]})
        base = doc["operations"][0]
        doc["operations"] = []
        for index in range(12):
            op = copy.deepcopy(base)
            peer = index % 2 == 1
            op.update(operation_id=f"compete-{index}", initiator_id="peer0" if peer else "src0",
                      fabric_id=(index // 2) % 2)
            if peer:
                op["source"]["buffer_id"] = "local-b"
            op["destination"]["offset_bytes"] = index * 544
            doc["operations"].append(op)
        wire = wire_plan(doc, graph)
        _, hooks, result = execute(wire, produce=0.5, consume=3, response_delay=7, reverse=True)
        self.assert_conserved(result)
        self.assertEqual(result.received_useful_bytes, 12 * 513)
        self.assertEqual(result.transmitted_channel_bytes, sum(p.planned_channel_bytes for p in wire.packets))
        self.assertEqual(len({(p, i) for p, i, _, _ in hooks.consumed}), len(hooks.consumed))
        # Two independent input lanes compete for the same directed output.
        shared = [e for e in result.trace if e.action == "link_launch" and e.channel.fabric_id == 0
                  and e.channel.kind == "network" and e.channel.identity == "t1_0/x+"
                  and e.packet.traffic_class == "request"]
        self.assertEqual(len({e.packet for e in shared}), 6)


if __name__ == "__main__":
    unittest.main()
