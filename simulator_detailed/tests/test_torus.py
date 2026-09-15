"""Independent topology/routing checks for the version-two torus binder."""

import copy
import json
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.torus_replay import TorusReplay
from simulator_detailed.tests.test_torus_contract import document, profile_documents
from simulator_detailed.torus import BoundTorus, TorusPlan
from simulator_detailed.torus_dependencies import (
    DependencyResource,
    ResourceDependencies,
)

ROOT = Path(__file__).resolve().parents[1]


def profile_replay(endpoint0="tile_1_1_noc0", endpoint1="tile_2_1_noc0"):
    replay, profile = profile_documents()
    for index, endpoint in enumerate(replay["binding"]["endpoints"]):
        endpoint["endpoint_id"] = ("tile_1_1_noc0" if index == 0 else "tile_2_1_noc0"
                                     if index == 1 else "tile_1_1_noc1" if index == 2 else "tile_2_1_noc1")
    replay["traffic"][0]["source"], replay["traffic"][0]["destination"] = endpoint0, endpoint1
    replay["traffic"][1]["source"], replay["traffic"][1]["destination"] = "tile_1_1_noc1", "tile_2_1_noc1"
    return TorusReplay.model_validate(replay), profile


def small_graph(width=3, height=2):
    tiles = [{"tile_id": f"t{x}_{y}", "x": x, "y": y, "role": "worker"}
             for y in range(height) for x in range(width)]
    routers = [{"router_id": tile["tile_id"], "fabric_id": 0, "tile_id": tile["tile_id"], "enabled": True,
                "coordinate": {"x": tile["x"], "y": tile["y"]},
                "ports": [{"port_id": f"{axis}{sign}", "kind": "network"}
                          for axis in ("x", "y") for sign in ("+", "-")]
                         + [{"port_id": "local", "kind": "local"}]}
               for tile in tiles]
    links = []
    for y in range(height):
        for x in range(width):
            src = f"t{x}_{y}"
            for axis, dx, dy in (("x", 1, 0), ("y", 0, 1)):
                dst = f"t{(x + dx) % width}_{(y + dy) % height}"
                links.append({"link_id": f"{src}/{axis}+", "fabric_id": 0, "src_router": src,
                              "src_port": f"{axis}+", "dst_router": dst, "dst_port": f"{axis}-",
                              "enabled": True, "direction": "right" if axis == "x" else "down",
                              "wrap": (x == width - 1 if axis == "x" else y == height - 1)})
    attachments = [
        {"endpoint_id": "src", "fabric_id": 0, "router_id": "t0_0", "role": "compute", "enabled": True,
         "inject_port": "local", "eject_port": "local", "permissions_resolved": True},
        {"endpoint_id": "dst", "fabric_id": 0, "router_id": "t1_1", "role": "compute", "enabled": True,
         "inject_port": "local", "eject_port": "local", "permissions_resolved": True},
    ]
    return {"kind": "canonical_topology", "schema_version": 1, "topology_id": f"small-{width}x{height}",
            "asic_id": "synthetic", "origin": {"kind": "synthetic"}, "connectivity_state": "complete",
            "tiles": tiles, "fabrics": [{"fabric_id": 0, "extent": {"width": width, "height": height},
                                           "topology_policy": "torus_2d", "routing_policy": "dimension_order_xy"}],
            "routers": routers, "links": links, "attachments": attachments,
            "enabled_worker_ids": [tile["tile_id"] for tile in tiles],
            "logical_workers": [{"worker_index": i, "logical_x": tile["x"], "logical_y": tile["y"],
                                 "tile_id": tile["tile_id"]} for i, tile in enumerate(tiles)],
            "resources": []}


def small_replay(dateline=(0, 0)):
    replay = document((0,))
    replay["source"] = {"kind": "canonical_graph", "graph_path": "graph.json"}
    replay["binding"]["fabrics"][0]["dateline"] = {"x": dateline[0], "y": dateline[1]}
    replay["binding"]["endpoints"] = [
        {"endpoint_id": "src", "fabric_id": 0, "roles": ["request_source", "request_sink"],
         "inject_port": "local", "eject_port": "local", "injection_queue_capacity_packets": 1,
         "sink_service": {"cycles": 1, "timebase": "noc", "evidence": copy.deepcopy({"status": "assumed", "description": "test"})},
         "response_queue_capacity_packets": None, "response_service": None,
         "evidence": copy.deepcopy({"status": "assumed", "description": "test"})},
        {"endpoint_id": "dst", "fabric_id": 0, "roles": ["request_sink"],
         "inject_port": None, "eject_port": "local", "injection_queue_capacity_packets": None,
         "sink_service": {"cycles": 1, "timebase": "noc", "evidence": copy.deepcopy({"status": "assumed", "description": "test"})},
         "response_queue_capacity_packets": None, "response_service": None,
         "evidence": copy.deepcopy({"status": "assumed", "description": "test"})},
    ]
    replay["traffic"] = [{"kind": "one_way", "transfer_id": "small", "fabric_id": 0,
                          "source": "src", "destination": "dst", "payload_bytes": 9,
                          "start_aci_cycles": 0, "burst_quantum_flits": 1}]
    return TorusReplay.model_validate(replay)


class TorusBindingTests(unittest.TestCase):
    def test_profile_inventory_and_endpoint_admission(self):
        config, profile = profile_replay()
        bound = BoundTorus.bind(config, profile)
        self.assertEqual(len(bound.topology.graph.tiles), 120)
        self.assertEqual(len(bound.topology.graph.routers), 240)
        self.assertEqual(len(bound.topology.graph.links), 480)
        self.assertEqual(len(bound.topology.graph.attachments), 240)
        self.assertEqual(set(bound.topology.graph.enabled_worker_ids), {
            tile for tile in json.loads((ROOT / "configs/profiles/wormhole_b0_n150_assumed.json").read_text())[
                "worker_selection"]["enabled_worker_ids"]})
        self.assertEqual(len(bound.traffic_routes()), 4)
        bad = copy.deepcopy(config.model_dump(mode="json"))
        harvested = "tile_1_11_noc0"
        bad["binding"]["endpoints"][0]["endpoint_id"] = harvested
        bad["traffic"][0]["source"] = harvested
        with self.assertRaises(ValueError):
            BoundTorus.bind(TorusReplay.model_validate(bad), profile)
        bad = copy.deepcopy(config.model_dump(mode="json"))
        bad["binding"]["router_overrides"] = [{"fabric_id": 0, "router_id": "tile_1_1",
            "enabled": False, "evidence": {"status": "assumed", "description": "test"}}]
        with self.assertRaises(ValueError):
            BoundTorus.bind(TorusReplay.model_validate(bad), profile)

    def test_all_pairs_match_independent_physical_coordinate_oracle(self):
        config, profile = profile_replay()
        bound = BoundTorus.bind(config, profile)
        physical = {t["tile_id"]: (t["x"], t["y"]) for t in profile["layout"]["tiles"]}
        checked = 0
        for fabric_id in (0, 1):
            for source, src in physical.items():
                for destination, dst in physical.items():
                    # NoC0 physically right/down; NoC1 physically up/left.
                    current = list(src)
                    expected = []
                    for axis in ((0, 1) if fabric_id == 0 else (1, 0)):
                        while current[axis] != dst[axis]:
                            current[axis] = (current[axis] + (1 if fabric_id == 0 else -1)) % (10 if axis == 0 else 12)
                            expected.append(tuple(current))
                    path = bound.network_path(fabric_id, source, destination)
                    actual = [physical[bound.routers[(fabric_id, hop.dst_router)].tile_id] for hop in path]
                    self.assertEqual(actual, expected)
                    checked += 1
        self.assertEqual(checked, 28_800)
        local = bound.route(0, "tile_1_1_noc0", "tile_1_1_noc0", "request")
        self.assertEqual([hop.lane.channel.kind for hop in local.hops], ["inject", "eject"])

    def test_small_torus_shifted_dateline_and_disabled_edge(self):
        graph = small_graph()
        for dateline in ((0, 0), (1, 1)):
            config = small_replay(dateline)
            bound = BoundTorus.bind(config, graph)
            plan = TorusPlan.compile(config, graph)
            self.assertTrue(plan.dependencies.export()["acyclic"])
            path = bound.network_path(0, "t0_0", "t1_1")
            self.assertEqual(len(path), 2)
            for first in path:
                self.assertEqual(first.lane.channel.kind, "network")
            self.assertEqual(bound.route(0, "src", "dst", "request").hops[-1].lane.channel.kind, "eject")
            route = bound.route(0, "src", "dst", "request")
            wrong_phase = route.model_dump(mode="json")
            original_phase = wrong_phase["hops"][1]["lane"]["dateline_phase"]
            wrong_phase["hops"][1]["lane"]["dateline_phase"] = 1 - original_phase
            with self.assertRaises(ValueError):
                bound.validate_route(type(route).model_validate(wrong_phase))
            wrong_rank = route.model_dump(mode="json")
            wrong_rank["hops"][2]["rank"] = wrong_rank["hops"][1]["rank"]
            with self.assertRaises(ValueError):
                bound.validate_route(type(route).model_validate(wrong_rank))
        broken = copy.deepcopy(graph)
        broken["links"] = [link for link in broken["links"] if link["link_id"] != "t0_0/x+"]
        with self.assertRaises(ValueError):
            BoundTorus.bind(small_replay(), broken)

    def test_transcribed_paths_and_physical_wraps(self):
        config, profile = profile_replay()
        bound = BoundTorus.bind(config, profile)
        fixture = json.loads((ROOT / "tests/fixtures/wormhole_torus_routes.json").read_text())
        physical = {t["tile_id"]: [t["x"], t["y"]] for t in profile["layout"]["tiles"]}
        for case in fixture["cases"]:
            source = f"tile_{case['source'][0]}_{case['source'][1]}"
            destination = f"tile_{case['destination'][0]}_{case['destination'][1]}"
            path = bound.network_path(case["fabric_id"], source, destination)
            self.assertEqual([physical[hop.dst_router] for hop in path], case["physical_destinations"])
        edges = {(e.fabric_id, e.link_id): e for e in bound.topology.graph.links}
        self.assertTrue(edges[(0, "tile_9_11/x+")].wrap)
        self.assertEqual(edges[(0, "tile_9_11/x+")].direction, "right")
        self.assertTrue(edges[(1, "tile_0_0/y+")].wrap)
        self.assertEqual(edges[(1, "tile_0_0/y+")].direction, "up")
        for source in fixture["sources"].values():
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(source["revision"], source["url"])


class DependencyTests(unittest.TestCase):
    def test_independent_reference_fixture_is_pinned(self):
        fixture = json.loads((ROOT / "tests/fixtures/wormhole_b0_profile_reference.json").read_text())
        self.assertEqual(fixture["extraction"].startswith("Independent transcription"), True)
        self.assertEqual(fixture["extent"], [10, 12])
        self.assertEqual(fixture["endpoint_count"], 240)
        for source in fixture["sources"].values():
            self.assertTrue(source["url"].startswith("https://"))
            self.assertRegex(source["revision"], r"^[0-9a-f]{40,64}$")
            if source["sha256"] is not None:
                self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(fixture["routing_policies"], ["dimension_order_xy", "dimension_order_yx"])

    def test_dependency_mutations_are_rejected_before_runtime(self):
        config, profile = profile_replay()
        plan = TorusPlan.compile(config, profile)
        self.assertGreater(len(plan.dependencies.edges), 0)
        resources = list(plan.dependencies.resources.values())
        with self.assertRaises(ValueError):
            ResourceDependencies.validate(resources, plan.dependencies.edges + (("missing", "also-missing"),))
        a, b = resources[0], resources[1]
        cycle = ((a.identity, b.identity), (b.identity, a.identity))
        with self.assertRaises(ValueError):
            ResourceDependencies.validate(resources, cycle)
        with self.assertRaises(ValueError):
            DependencyResource.model_validate({"kind": "response_descriptors", "fabric_id": 0,
                "traffic_class": "response", "lane": None, "endpoint_id": "dst", "rank": 1})
        changed = copy.deepcopy(config.model_dump(mode="json"))
        changed["binding"]["fabrics"][0]["dateline"]["x"] = 3
        shifted = TorusPlan.compile(TorusReplay.model_validate(changed), profile)
        self.assertNotEqual(plan.record.plan_sha256, shifted.record.plan_sha256)


if __name__ == "__main__":
    unittest.main()
