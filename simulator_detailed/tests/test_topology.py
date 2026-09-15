"""Canonical graph validation and independent inventory/identity expectations."""

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.topology import Topology

REFERENCE_PATH = Path(__file__).parent / "fixtures/topology_reference.json"


def reference_graph():
    return json.loads(REFERENCE_PATH.read_text())["graph"]


class TopologyContractTests(unittest.TestCase):
    def test_inventory_counts_roles_and_shared_capacity(self):
        reference = json.loads(REFERENCE_PATH.read_text())
        topology = Topology.compile(CanonicalTopology.model_validate(reference["graph"]))
        report = topology.export()
        self.assertEqual(report["counts"], reference["expected_counts"])
        self.assertEqual(report["unique_memory_bytes"], reference["expected_memory_bytes"])
        self.assertEqual(topology.graph.enabled_worker_ids, ("a", "z"))
        off = next(r for r in topology.graph.routers if r.router_id == "off")
        self.assertTrue(off.enabled)
        self.assertFalse(any(a.router_id == "off" for a in topology.graph.attachments))
        self.assertEqual(topology.router_indices[(0, "z")], 4)
        self.assertEqual(topology.router_indices[(7, "alias")], 0)

    def test_reordering_round_trip_and_immutable_identity(self):
        document = reference_graph()
        first = Topology.compile(CanonicalTopology.model_validate(document))
        for value in document.values():
            if isinstance(value, list):
                value.reverse()
        for router in document["routers"]:
            router["ports"].reverse()
        second = Topology.compile(CanonicalTopology.model_validate_json(json.dumps(document)))
        self.assertEqual(first.export(), second.export())
        with self.assertRaises(TypeError):
            first.router_indices[(0, "a")] = 4
        with self.assertRaises(ValidationError):
            first.graph.tiles[0].role = "memory"
        dumped = first.export()
        dumped["graph"]["tiles"][0]["role"] = "memory"
        self.assertEqual(first.graph.tiles[0].role, "worker")

    def test_unknown_connectivity_is_not_zero(self):
        document = reference_graph()
        document["connectivity_state"] = "unresolved"
        document["links"] = []
        topology = Topology.compile(CanonicalTopology.model_validate(document))
        self.assertIsNone(topology.export()["counts"]["directed_links"])
        document["connectivity_state"] = "complete"
        self.assertEqual(Topology.compile(CanonicalTopology.model_validate(document))
                         .export()["counts"]["directed_links"], 0)

    def test_invalid_records_and_references_are_rejected(self):
        cases = [
            lambda d: d.update(schema_version=True),
            lambda d: d.update(schema_version=2),
            lambda d: d.update(can_execute=True),
            lambda d: d["fabrics"][0].update(fabric_id=True),
            lambda d: d["tiles"][0].update(role="unknown"),
            lambda d: d["tiles"].append(d["tiles"][0]),
            lambda d: d["routers"][0].update(tile_id="absent"),
            lambda d: d["links"][0].update(fabric_id=8),
            lambda d: d["links"][0].update(src_port="terminal"),
            lambda d: d["links"].append({**d["links"][0], "link_id": "parallel-collision"}),
            lambda d: d["attachments"][0].update(router_id="off"),
            lambda d: d["attachments"][0].update(enabled=False),
            lambda d: d["attachments"][0].update(permissions_resolved=False),
            lambda d: d["attachments"][2].update(resource_ids=["missing"]),
            lambda d: d["attachments"][2].update(resource_ids=["l1-a"]),
            lambda d: d["enabled_worker_ids"].append("ram"),
            lambda d: d["logical_workers"].pop(),
            lambda d: d["logical_workers"][0].update(worker_index=1),
            lambda d: d["resources"][0].update(capacity_bytes=float("inf")),
            lambda d: d["routers"][0].update(runtime_index=0),
            lambda d: d["routers"][0]["ports"][0].update(runtime_index=0),
        ]
        for i, change in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValidationError):
                document = reference_graph()
                change(document)
                CanonicalTopology.model_validate(document)

    def test_parallel_links_and_disabled_wrap_metadata(self):
        document = reference_graph()
        for index in (0, 2):
            document["routers"][index]["ports"].append({"port_id": "parallel", "kind": "network"})
        document["links"].append({**document["links"][0], "link_id": "parallel",
                                  "src_port": "parallel", "dst_port": "parallel",
                                  "enabled": False, "wrap": True, "direction": "east"})
        topology = Topology.compile(CanonicalTopology.model_validate(document))
        edge = next(l for l in topology.graph.links if l.link_id == "parallel")
        self.assertTrue(edge.wrap)
        self.assertFalse(edge.enabled)
        self.assertNotEqual(topology.link_indices[(0, "parallel")],
                            topology.link_indices[(0, "a-ram")])
        self.assertFalse(any(l.src_router == "ram" and l.dst_router == "a"
                             for l in topology.graph.links))

    def test_compatibility_indices_are_complete_and_stable(self):
        document = reference_graph()
        for index, router in enumerate(document["routers"][:5]):
            router["runtime_index"] = index
        topology = Topology.compile(CanonicalTopology.model_validate(document))
        self.assertEqual(topology.router_indices[(0, "z")], 1)
        altered = topology.graph.model_copy(update={"enabled_worker_ids": ("ram",)})
        with self.assertRaises(ValidationError):
            Topology.compile(altered)
        document["routers"][0]["runtime_index"] = 1
        with self.assertRaises(ValidationError):
            CanonicalTopology.model_validate(document)

    def test_component_state_changes_content_hash(self):
        document = reference_graph()
        changed = copy.deepcopy(document)
        changed["links"][0]["enabled"] = False
        first = Topology.compile(CanonicalTopology.model_validate(document))
        second = Topology.compile(CanonicalTopology.model_validate(changed))
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.link_indices, second.link_indices)
