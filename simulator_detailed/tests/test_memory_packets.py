"""Independent byte tables, addressed route checks and wire admission failures."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.configs.schemas.memory_replay import (
    MemoryOperation,
    MemoryPacketConfig,
    MemoryReplay,
)
from simulator_detailed.configs.schemas.torus_replay import TransportEnvelope
from simulator_detailed.memory_packets import (
    MemoryPacket,
    MemoryPacketIdentity,
    MemoryPacketLayout,
    MemoryWireEnvelope,
    MemoryWirePlan,
    packetize,
)
from simulator_detailed.memory_plan import MemoryPlan
from simulator_detailed.tests.test_memory_contracts import EVIDENCE, replay_document
from simulator_detailed.tests.test_torus import small_graph, small_replay

FIXTURE = json.loads((Path(__file__).parent / "fixtures/wormhole_memory_packets.json").read_text())


def operation(kind="write_posted", size=33, operation_id="transfer"):
    return MemoryOperation(operation_id=operation_id, initiator_id="src0", fabric_id=0, kind=kind,
                           source={"buffer_id": "local", "offset_bytes": 0, "size_bytes": size},
                           destination={"buffer_id": "remote", "offset_bytes": 0, "size_bytes": size})


def packet_config(**changes):
    return MemoryPacketConfig.model_validate({**FIXTURE["packet_config"], "evidence": EVIDENCE, **changes})


def memory_documents(kind="write_acknowledged", size=8193, fabric=0):
    graph = small_graph(3, 3)
    graph["tiles"][-1]["role"] = "memory"
    graph["enabled_worker_ids"].remove("t2_2")
    graph["logical_workers"] = [w for w in graph["logical_workers"] if w["tile_id"] != "t2_2"]
    graph["fabrics"].append({**copy.deepcopy(graph["fabrics"][0]), "fabric_id": 1,
                              "routing_policy": "dimension_order_yx"})
    for router in copy.deepcopy(graph["routers"]):
        router["fabric_id"] = 1
        router["coordinate"] = {axis: 2 - router["coordinate"][axis] for axis in ("x", "y")}
        graph["routers"].append(router)
    for y in range(3):
        for x in range(3):
            for axis, dx, dy in (("x", -1, 0), ("y", 0, -1)):
                graph["links"].append({"link_id": f"t{x}_{y}/{axis}+", "fabric_id": 1,
                                       "src_router": f"t{x}_{y}", "src_port": f"{axis}+",
                                       "dst_router": f"t{(x + dx) % 3}_{(y + dy) % 3}", "dst_port": f"{axis}-",
                                       "enabled": True, "direction": "left" if axis == "x" else "up",
                                       "wrap": (x == 0 if axis == "x" else y == 0)})
    graph["resources"] = [{"resource_id": "l1-a", "kind": "local_sram", "owner_tile_id": "t0_0", "capacity_bytes": 65536},
                           {"resource_id": "dram", "kind": "dram", "capacity_bytes": 65536}]
    graph["attachments"] = [{"endpoint_id": f"{name}{f}", "fabric_id": f, "router_id": router,
                              "role": role, "enabled": True, "replay_enabled": True,
                              "permissions_resolved": True, "inject_port": "local", "eject_port": "local",
                              "resource_ids": [resource]}
                             for f in (0, 1)
                             for name, router, role, resource in (("src", "t0_0", "compute", "l1-a"),
                                                                  ("ram", "t2_2", "network", "dram"))]
    config = replay_document()
    config["fabrics"] = [0, 1]
    routing = small_replay().binding.fabrics[0].model_dump(mode="json")
    config["routing"] = [copy.deepcopy(routing), {**copy.deepcopy(routing), "fabric_id": 1,
                                                "routing_policy": "dimension_order_yx"}]
    config["endpoints"] = [{"endpoint_id": a["endpoint_id"], "fabric_id": a["fabric_id"], "router_id": a["router_id"],
                            "roles": ["initiator", "target", "response_sink"] if a["role"] == "compute" else ["target"],
                            "resource_ids": a["resource_ids"], "evidence": EVIDENCE}
                           for a in graph["attachments"]]
    for buffer in config["buffers"]:
        buffer["size_bytes"] = 16384
    config["operations"] = [operation(kind, size).model_dump(mode="json")]
    config["operations"][0]["fabric_id"] = fabric
    if kind == "read":
        config["operations"][0]["source"]["buffer_id"] = "remote"
        config["operations"][0]["destination"]["buffer_id"] = "local"
    return config, graph


def wire_plan(config, graph):
    return MemoryWirePlan.compile(MemoryPlan.compile(MemoryReplay.model_validate(config), graph))


class PacketLayoutTests(unittest.TestCase):
    def test_pinned_reference_boundaries_and_separate_byte_units(self):
        for case in FIXTURE["cases"]:
            for kind in ("write_posted", "write_acknowledged", "read"):
                with self.subTest(size=case["logical"], kind=kind):
                    packets = packetize(operation(kind, case["logical"]), packet_config(),
                                        source_base_address=0, destination_base_address=0)
                    self.assertEqual(sum(p.layout.useful_bytes for p in packets), case["logical"])
                    self.assertEqual(len({p.segment.segment_index for p in packets}), case["segments"])
                    self.assertEqual(sum(p.layout.data_flits for p in packets), case["data_flits"])
                    self.assertEqual(sum(p.layout.padding_bytes for p in packets), case["padding"])
                    prefix = "posted" if kind == "write_posted" else "paired"
                    self.assertEqual(sum(p.layout.header_bytes for p in packets), case[f"{prefix}_headers"])
                    self.assertEqual(sum(p.layout.packet_bytes for p in packets), case[f"{prefix}_packet"])
                    for packet in packets:
                        layout = packet.layout
                        lengths = [layout.flit_useful_bytes(i) for i in range(layout.flit_count)]
                        self.assertEqual(sum(lengths), layout.useful_bytes)
                        self.assertEqual(lengths[:layout.header_flits], [0] * layout.header_flits)
                        self.assertEqual(layout.header_bytes + layout.padding_bytes + layout.useful_bytes, layout.packet_bytes)
        for source in FIXTURE["sources"].values():
            self.assertIn(source["revision"], source["url"])
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")

    def test_generic_width_capacity_multiple_headers_and_segmentation(self):
        config = packet_config(physical_flit_bytes=16, data_capacity_bytes=8, header_flits=2,
                               max_segment_payload_bytes=24, address_alignment_bytes=4)
        # size -> segments, padding bytes, posted packet bytes, paired bytes.
        for size, segments, padding, posted, paired in (
            (1, 1, 15, 48, 80), (7, 1, 9, 48, 80), (8, 1, 8, 48, 80),
            (9, 1, 23, 64, 96), (23, 1, 25, 80, 112), (24, 1, 24, 80, 112),
            (25, 2, 39, 128, 192), (49, 3, 63, 208, 304),
        ):
            for kind, expected in (("write_posted", posted), ("write_acknowledged", paired), ("read", paired)):
                with self.subTest(size=size, kind=kind):
                    packets = packetize(operation(kind, size), config, source_base_address=4, destination_base_address=12)
                    self.assertEqual(sum(p.layout.packet_bytes for p in packets), expected)
                    self.assertEqual(sum(p.layout.padding_bytes for p in packets), padding)
                    self.assertEqual(len({p.segment.segment_index for p in packets}), segments)
                    self.assertTrue(all(p.segment.source_address % 4 == p.segment.destination_address % 4 == 0 for p in packets))

    def test_segment_address_offsets_and_strict_subset(self):
        case = FIXTURE["address_case"]
        document = operation(size=case["size"]).model_dump(mode="json")
        document["source"]["offset_bytes"] = case["source_offset"]
        document["destination"]["offset_bytes"] = case["destination_offset"]
        packets = packetize(MemoryOperation.model_validate(document), packet_config(),
                            source_base_address=case["source_base"], destination_base_address=case["destination_base"])
        self.assertEqual([p.segment.source_address for p in packets], case["segment_sources"])
        self.assertEqual([p.segment.destination_address for p in packets], case["segment_destinations"])
        self.assertEqual([p.segment.logical_bytes for p in packets], case["segment_lengths"])
        for base in (1, -32, True, 0.0):
            with self.assertRaises(ValueError):
                packetize(operation(), packet_config(), source_base_address=base, destination_base_address=0)
        for mutate in (lambda d: d["source"].update(offset_bytes=1),
                       lambda d: d["destination"].update(size_bytes=32)):
            doc = operation().model_dump(mode="json")
            mutate(doc)
            with self.assertRaises(ValueError):
                packetize(MemoryOperation.model_validate(doc), packet_config(), source_base_address=0, destination_base_address=0)

    def test_structural_ids_are_collision_free_and_records_immutable(self):
        identities = [MemoryPacketIdentity(operation_id=name, segment_index=index, purpose=purpose)
                      for name in ('a', 'a/1', 'a:response', '["a",1,"write_ack"]', '数据')
                      for index in (0, 1) for purpose in ("read_request", "read_response", "write_request", "write_ack")]
        self.assertEqual(len({identity.transport_identity for identity in identities}), len(identities))
        for identity in identities:
            self.assertEqual(json.loads(identity.transport_identity.transfer_id),
                             [identity.operation_id, identity.segment_index, identity.purpose])
        packet = packetize(operation(), packet_config(), source_base_address=0, destination_base_address=0)[0]
        self.assertEqual(packet, MemoryPacket.model_validate_json(packet.model_dump_json()))
        with self.assertRaises(ValidationError):
            packet.layout.useful_bytes = 10
        for mutate in (lambda d: d["identity"].update(segment_index=1),
                       lambda d: d["identity"].update(purpose="read_request"),
                       lambda d: d["layout"].update(useful_bytes=0)):
            doc = packet.model_dump(mode="json")
            mutate(doc)
            with self.assertRaises(ValidationError):
                MemoryPacket.model_validate(doc)
        with self.assertRaises(ValidationError):
            MemoryPacketLayout(physical_flit_bytes=8, data_capacity_bytes=16, header_flits=1, useful_bytes=1)


class MemoryWireTests(unittest.TestCase):
    def test_dual_fabric_aliases_wraps_and_independent_channel_totals(self):
        expected_requests = {0: ["t1_0", "t2_0", "t2_1", "t2_2"], 1: ["t0_2", "t2_2"]}
        expected_responses = {0: ["t0_2", "t0_0"], 1: ["t2_1", "t2_0", "t1_0", "t0_0"]}
        for case in FIXTURE["channel_cases"]:
            for kind in ("write_posted", "write_acknowledged", "read"):
                config, graph = memory_documents(kind, case["size"], case["fabric"])
                with self.subTest(kind=kind, fabric=case["fabric"]), patch("simpy.Environment", side_effect=AssertionError("runtime constructed")):
                    plan = wire_plan(config, graph)
                    route = plan.routes.operations[0]
                    self.assertEqual(route.initiator_resource_id, "l1-a")
                    self.assertEqual(route.target_resource_id, "dram")
                    self.assertEqual(route.request.source, f"src{case['fabric']}")
                    self.assertEqual([h.dst_router for h in route.request.hops[1:-1]], expected_requests[case["fabric"]])
                    if route.response is not None:
                        self.assertEqual([h.dst_router for h in route.response.hops[1:-1]], expected_responses[case["fabric"]])
                    self.assertEqual(sum(p.planned_channel_bytes for p in plan.packets), case[kind])
                    self.assertTrue(plan.routes.dependencies.export()["acyclic"])
                    self.assertFalse(hasattr(plan, "env"))

    def test_same_router_routes_still_charge_local_channels(self):
        config, graph = memory_documents(size=1)
        config["operations"][0]["destination"]["buffer_id"] = "local"
        config["operations"][0]["destination"]["offset_bytes"] = 32
        plan = wire_plan(config, graph)
        for packet in plan.packets:
            self.assertEqual([h.lane.channel.kind for h in packet.route.hops], ["inject", "eject"])
        self.assertEqual(sum(p.planned_channel_bytes for p in plan.packets), 192)

    def test_unavailable_wrong_roles_foreign_ranges_and_missing_routes_fail(self):
        mutations = [
            lambda c, g: c.update(routing=[]),
            lambda c, g: c["endpoints"][0].update(roles=["target", "response_sink"]),
            lambda c, g: c["endpoints"][0].update(roles=["initiator"]),
            lambda c, g: c["endpoints"][1].update(enabled=False),
            lambda c, g: c["operations"][0].update(initiator_id="ram0"),
            lambda c, g: (c["endpoints"][1].update(roles=["initiator", "target"]), c["operations"][0].update(initiator_id="ram0")),
            lambda c, g: c["operations"][0]["source"].update(buffer_id="remote"),
            lambda c, g: c["operations"][0]["source"].update(offset_bytes=16384),
            lambda c, g: c["operations"][0]["source"].update(offset_bytes=1),
            lambda c, g: c["operations"][0]["source"].update(size_bytes=1),
            lambda c, g: c["buffers"][0].update(readable=False),
            lambda c, g: c["buffers"][1].update(writable=False),
            lambda c, g: c["resources"][0].update(capacity_override_bytes=32),
            lambda c, g: g["links"][0].update(enabled=False),
            lambda c, g: g["links"].pop(),
            lambda c, g: c["routing"][0]["dateline"].update(x=3),
            lambda c, g: c["operations"].append(copy.deepcopy(c["operations"][0])),
            lambda c, g: c["endpoints"][1].update(resource_ids=["l1-a"]),
        ]
        for mutate in mutations:
            config, graph = memory_documents()
            mutate(config, graph)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                wire_plan(config, graph)

    def test_ambiguous_target_aliases_are_not_silently_selected(self):
        config, graph = memory_documents()
        graph["routers"][8]["ports"].append({"port_id": "alternate", "kind": "local"})
        alias = {**copy.deepcopy(graph["attachments"][1]), "endpoint_id": "ram-alias",
                 "inject_port": "alternate", "eject_port": "alternate"}
        graph["attachments"].append(alias)
        config["endpoints"].append({**copy.deepcopy(config["endpoints"][1]), "endpoint_id": "ram-alias"})
        with self.assertRaisesRegex(ValueError, "exactly one enabled target"):
            wire_plan(config, graph)
        config["endpoints"][1]["enabled"] = False
        admitted = wire_plan(config, graph)
        self.assertEqual(admitted.routes.operations[0].request.destination, "ram-alias")
        self.assertEqual(admitted.routes.operations[0].target_resource_id, "dram")

    def test_envelope_headers_tails_and_admitted_path(self):
        plan = wire_plan(*memory_documents("read", 33))
        for item in plan.packets:
            for hop in range(len(item.route.hops)):
                for index in range(item.packet.layout.flit_count):
                    envelope = plan.envelope(item.packet.identity, index, hop)
                    plan.validate_envelope(envelope)
                    self.assertEqual(envelope.is_head, index == 0)
                    self.assertEqual(envelope.is_tail, index + 1 == item.packet.layout.flit_count)
                    self.assertEqual(envelope.useful_bytes, 0 if index == 0 else 32 if index == 1 else 1)
        head = plan.envelope(plan.packets[0].packet.identity, 0, 0)
        self.assertTrue(head.is_head and head.is_tail)
        self.assertEqual(head.useful_bytes, 0)
        self.assertEqual(head.physical_bytes, 32)

    def test_mutated_envelopes_rejected_before_runtime_and_v2_stays_positive(self):
        config, graph = memory_documents("read", 33)
        plan = wire_plan(config, graph)
        original = plan.envelope(plan.packets[0].packet.identity, 0, 1)
        mutations = [lambda d: d.update(plan_sha256="0" * 64),
                     lambda d: d.update(useful_bytes=1), lambda d: d.update(physical_bytes=16),
                     lambda d: d.update(flit_index=1), lambda d: d.update(hop_index=99),
                     lambda d: d["packet"].update(operation_id="foreign"),
                     lambda d: d["packet"].update(purpose="write_ack"),
                     lambda d: d["lane"].update(traffic_class="response"),
                     lambda d: d["lane"]["channel"].update(fabric_id=1),
                     lambda d: d["lane"]["channel"].update(identity="other/x+"),
                     lambda d: d["lane"].update(dateline_phase=0 if original.lane.dateline_phase else 1),
                     lambda d: d["layout"].update(header_flits=2)]
        for mutate in mutations:
            doc = original.model_dump(mode="json")
            mutate(doc)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                plan.validate_envelope(MemoryWireEnvelope.model_validate(doc))
        config["packet"]["header_flits"] = 2
        other = wire_plan(config, graph)
        with self.assertRaisesRegex(ValueError, "admitted"):
            other.validate_envelope(original)
        header = other.packets[0].packet.identity
        self.assertFalse(other.envelope(header, 0, 0).is_tail)
        self.assertFalse(other.envelope(header, 1, 0).is_head)
        self.assertTrue(other.envelope(header, 1, 0).is_tail)
        with self.assertRaises(ValidationError):
            TransportEnvelope(plan_sha256=plan.plan_sha256, packet=original.packet.transport_identity,
                              fabric_id=0, source="src0", destination="ram0", hop_index=1, lane=original.lane,
                              flit_index=0, flit_count=1, payload_bytes=0, physical_bytes=32, burst_quantum_flits=1)

    def test_plan_identity_is_path_independent_and_settings_sensitive(self):
        config, graph = memory_documents()
        first = wire_plan(config, graph)
        config["source"]["graph_path"] = "different/path.json"
        second = wire_plan(config, graph)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        config["routing"][0]["dateline"]["x"] = 1
        shifted = wire_plan(config, graph)
        self.assertNotEqual(first.plan_sha256, shifted.plan_sha256)


if __name__ == "__main__":
    unittest.main()
