"""Generic system-graph contract tests; synthetic fixtures only, no runtime claims."""

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_graph import (
    FORBIDDEN_DEVICE_TOKENS,
    GenericSystemGraph,
)
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.generic_graph import (
    GenericSystem,
    generic_digest,
    topology_from_generic,
)
from simulator_detailed.replay_topology import inspect_topology
from simulator_detailed.topology import Topology

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json"


def fixture_doc():
    return json.loads(FIXTURE.read_text())


def compile_fixture():
    return topology_from_generic(GenericSystemGraph.model_validate(fixture_doc()))


def expect_invalid(mutate):
    document = fixture_doc()
    mutate(document)
    with unittest.TestCase().assertRaises(ValidationError):
        GenericSystemGraph.model_validate(document)


class TestGenericGraphContracts(unittest.TestCase):
    def test_fixture_loads_and_compiles(self):
        system = compile_fixture()
        self.assertIsInstance(system, GenericSystem)
        self.assertEqual(system.document.system_id, "synthetic-grid-2d")
        self.assertEqual(system.graph.connectivity_state, "complete")
        self.assertEqual(system.graph.origin.kind, "synthetic")
        self.assertEqual(dict(system.network_fabrics), {"net_alpha": 0, "net_beta": 1})

    def test_acceptance_connectivity(self):
        system = compile_fixture()
        self.assertEqual(
            set(system.network_links("net_alpha")),
            {
                "al_00e10", "al_10w00", "al_10e20", "al_20w10",
                "al_01e11", "al_11w01", "al_11e21", "al_21w11",
                "al_00s01", "al_01n00", "al_10s11", "al_11n10",
                "al_20s21", "al_21n20",
            },
        )
        self.assertEqual(
            set(system.network_links("net_beta")),
            {"be_00e10", "be_10w00", "be_10e20", "be_20w10", "be_20s21", "be_21n20"},
        )
        self.assertEqual(
            set(system.network_nodes("net_alpha")),
            {"n00", "n10", "n20", "n01", "n11", "n21"},
        )
        self.assertEqual(set(system.network_nodes("net_beta")), {"n00", "n10", "n20", "n21"})
        adjacency: dict[str, dict[str, set[str]]] = {"net_alpha": {}, "net_beta": {}}
        for link in system.document.links:
            adjacency[link.network_id].setdefault(link.src_node, set()).add(link.dst_node)
        self.assertEqual(adjacency["net_alpha"]["n00"], {"n10", "n01"})
        self.assertEqual(adjacency["net_alpha"]["n11"], {"n01", "n21", "n10"})
        self.assertEqual(adjacency["net_beta"]["n20"], {"n10", "n21"})
        self.assertNotIn("n01", adjacency["net_beta"])

    def test_unified_system_object(self):
        system = compile_fixture()
        export = system.export()
        self.assertEqual(export["kind"], "generic_system_inspection")
        self.assertEqual(export["generic_sha256"], generic_digest(system.document))
        endpoints = export["endpoints"]
        self.assertEqual(endpoints["dma"], ["dma_a", "dma_b"])
        self.assertEqual(endpoints["execution_units"], ["eu_00", "eu_10", "eu_11"])
        self.assertEqual(endpoints["memory_services"], ["mem_a_ep", "mem_b_ep"])
        self.assertEqual(len(export["static_routes"]), 4)
        networks = {entry["network_id"]: entry for entry in export["networks"]}
        self.assertEqual(networks["net_alpha"]["fabric_id"], 0)
        self.assertEqual(networks["net_beta"]["fabric_id"], 1)
        self.assertEqual(len(networks["net_alpha"]["links"]), 14)
        self.assertEqual(len(networks["net_beta"]["links"]), 6)
        resources = {entry["resource_id"]: entry for entry in export["memory_resources"]}
        self.assertEqual(resources["mem_a"]["capacity_bytes"], 1048576)
        self.assertEqual(resources["mem_b"]["owner_node"], "n21")
        routes = system.routes_for("net_beta")
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["link_ids"], ["be_21n20"])

    def test_unknown_fields_rejected(self):
        def mutate(document):
            document["nodes"][0]["vendor"] = "unknown"
        expect_invalid(mutate)

        def mutate_top(document):
            document["device_name"] = "mystery"
        expect_invalid(mutate_top)

    def test_kind_and_version_rejected(self):
        def bad_kind(document):
            document["kind"] = "canonical_topology"
        expect_invalid(bad_kind)

        def bad_version(document):
            document["schema_version"] = 2
        expect_invalid(bad_version)

    def test_duplicate_identities_rejected(self):
        def dup_node(document):
            document["nodes"].append(copy.deepcopy(document["nodes"][0]))
        expect_invalid(dup_node)

        def dup_link(document):
            document["links"].append(copy.deepcopy(document["links"][0]))
        expect_invalid(dup_link)

        def dup_endpoint(document):
            document["dma_endpoints"].append({
                "endpoint_id": "eu_00", "node_id": "n10",
                "network_id": "net_alpha", "port_id": "dma9",
            })
        expect_invalid(dup_endpoint)

    def test_dangling_references_rejected(self):
        def bad_link_node(document):
            document["links"][0]["src_node"] = "n99"
        expect_invalid(bad_link_node)

        def bad_link_port(document):
            document["links"][0]["src_port"] = "q9"
        expect_invalid(bad_link_port)

        def bad_endpoint_network(document):
            document["dma_endpoints"][0]["network_id"] = "net_gamma"
        expect_invalid(bad_endpoint_network)

        def bad_owner(document):
            document["memory_resources"][0]["owner_node"] = "n99"
        expect_invalid(bad_owner)

    def test_empty_network_rejected(self):
        def mutate(document):
            document["networks"].append({"network_id": "net_gamma"})
        expect_invalid(mutate)

    def test_device_vocabulary_rejected(self):
        for token in FORBIDDEN_DEVICE_TOKENS:
            def mutate(document, token=token):
                document["nodes"][0]["node_id"] = f"n00-{token}"
            expect_invalid(mutate)

    def test_port_half_occupancy_rejected(self):
        def mutate(document):
            document["links"].append({
                "link_id": "al_dup", "network_id": "net_alpha",
                "src_node": "n00", "src_port": "ea",
                "dst_node": "n10", "dst_port": "we",
            })
        expect_invalid(mutate)

    def test_route_validation(self):
        def discontiguous(document):
            document["static_routes"][0]["link_ids"] = ["al_00e10", "al_11e21"]
        expect_invalid(discontiguous)

        def wrong_network(document):
            document["static_routes"][0]["link_ids"] = ["be_21n20"]
        expect_invalid(wrong_network)

        def revisits(document):
            document["static_routes"].append({
                "network_id": "net_alpha", "source": "eu_00", "destination": "eu_10",
                "link_ids": ["al_00e10", "al_10s11", "al_11n10"],
            })
        expect_invalid(revisits)

        def wrong_destination(document):
            document["static_routes"][0]["destination"] = "eu_11"
        expect_invalid(wrong_destination)

    def test_execution_unit_requires_compute_node(self):
        def mutate(document):
            document["execution_units"].append({
                "unit_id": "eu_bad", "node_id": "n01",
                "network_id": "net_alpha", "port_id": "eu0",
            })
            document["ports"].append({
                "port_id": "eu0", "node_id": "n01",
                "network_id": "net_alpha", "kind": "local",
            })
        expect_invalid(mutate)

    def test_round_trip_and_order_independent_digest(self):
        document = GenericSystemGraph.model_validate(fixture_doc())
        dumped = document.model_dump(mode="json")
        reloaded = GenericSystemGraph.model_validate(json.loads(json.dumps(dumped)))
        self.assertEqual(generic_digest(reloaded), generic_digest(document))

        shuffled = fixture_doc()
        for key in (
            "nodes", "networks", "ports", "links",
            "dma_endpoints", "execution_units", "memory_resources", "static_routes",
        ):
            shuffled[key] = list(reversed(shuffled[key]))
        base = compile_fixture()
        reordered = topology_from_generic(GenericSystemGraph.model_validate(shuffled))
        self.assertEqual(reordered.content_hash, base.content_hash)
        self.assertEqual(reordered.generic_sha256, base.generic_sha256)
        self.assertEqual(reordered.export(), base.export())

    def test_inspect_entrypoint_dispatches_generic(self):
        system = inspect_topology(FIXTURE)
        self.assertIsInstance(system, GenericSystem)
        self.assertEqual(system.export()["kind"], "generic_system_inspection")

    def test_canonical_revalidation_is_stable(self):
        system = compile_fixture()
        again = Topology.compile(
            CanonicalTopology.model_validate(system.graph.model_dump(mode="json"))
        )
        self.assertEqual(again.content_hash, system.content_hash)
        self.assertEqual(again.export()["counts"], Topology.export(system)["counts"])


if __name__ == "__main__":
    unittest.main()
