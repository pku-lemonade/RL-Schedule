"""Offline consumer contracts; no Torch/PyG substitutes or model claims."""

import json
import unittest

from simulator_detailed.configs.schemas.arch_config import NoCConfig
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.predictor.topology import Mesh
from simulator_detailed.tests.test_topology import REFERENCE_PATH
from simulator_detailed.tests.test_topology_replay import EXAMPLE
from simulator_detailed.topology import Topology, topology_from_legacy
from simulator_detailed.topology_compatibility import (
    legacy_coordinates,
    legacy_event_rows,
    require_legacy_topology,
)
from simulator_detailed.transport import ReplayPlan, ReplayRuntime
from simulator_detailed.utils.definitions import NoCChannel


class TopologyConsumerTests(unittest.TestCase):
    def test_supported_legacy_contract_and_canonical_coordinates(self):
        for shape in ((1, 1), (3, 2), (4, 4)):
            config = NoCConfig(x=shape[0], y=shape[1], fabric_ids=(NoCChannel(7), NoCChannel.CH0), pe_local_port=9)
            topology = topology_from_legacy(config)
            for consumer in ("detailed_predictor", "detailed_encoder"):
                self.assertEqual(require_legacy_topology(topology, consumer), config)
            coordinates = legacy_coordinates(topology)
            for (fabric, router), xy in coordinates.items():
                self.assertIn(fabric, (0, 7))
                self.assertEqual(xy, (router % shape[0], router // shape[0]))
            mesh = Mesh(*shape, config.fabric_ids, topology=topology)
            self.assertIs(mesh.topology, topology)
            self.assertEqual(mesh.core_count, shape[0] * shape[1])

    def test_same_counts_different_roles_edges_and_spoofed_origins_rejected(self):
        topology = topology_from_legacy(NoCConfig(x=3, y=2))
        for consumer in ("detailed_predictor", "detailed_encoder"):
            role = topology.graph.model_dump(mode="json")
            role["tiles"][0]["role"] = "transit"
            role["enabled_worker_ids"].remove("R0")
            role["logical_workers"] = [{**w, "worker_index": i} for i, w in enumerate(role["logical_workers"][1:])]
            for attachment in role["attachments"]:
                if attachment["router_id"] == "R0":
                    attachment["role"] = "network"
                    attachment["legacy_binding"] = None
            edges = topology.graph.model_dump(mode="json")
            first, second = edges["links"][:2]
            first["dst_router"], second["dst_router"] = second["dst_router"], first["dst_router"]
            first["dst_port"], second["dst_port"] = second["dst_port"], first["dst_port"]
            origin = topology.graph.model_dump(mode="json")
            origin["origin"]["content_hash"] = "0" * 64
            endpoint = topology.graph.model_dump(mode="json")
            endpoint["attachments"].pop()
            for document in (role, edges, origin, endpoint):
                altered = Topology.compile(CanonicalTopology.model_validate(document))
                self.assertEqual(len(altered.graph.routers), len(topology.graph.routers))
                with self.assertRaisesRegex(ValueError, "contract|hash"):
                    require_legacy_topology(altered, consumer)
            with self.assertRaisesRegex(ValueError, "heterogeneous/profile"):
                require_legacy_topology(ReplayPlan.load(EXAMPLE).topology, consumer)
        with self.assertRaisesRegex(ValueError, "unknown topology consumer"):
            require_legacy_topology(topology, "unknown")
        with self.assertRaisesRegex(TypeError, "compiled canonical"):
            require_legacy_topology({"kind": "topology_replay_result"}, "detailed_predictor")
        with self.assertRaisesRegex(ValueError, "unsupported format"):
            require_legacy_topology(topology, "detailed_predictor", "topology_replay_result")
        document = topology.graph.model_dump(mode="json")
        document["compatible"] = True
        with self.assertRaises(ValueError):
            Topology.compile(CanonicalTopology.model_validate(document))

    def test_mesh_order_and_path_nodes_against_independent_baseline(self):
        edges = json.loads(REFERENCE_PATH.read_text())["mesh_3x2_ordered_edges"]
        mesh = Mesh(3, 2, (NoCChannel(7), NoCChannel.CH0))
        self.assertEqual(mesh.link_count, 28)
        self.assertEqual(list(mesh.link_to_core_pair.values()), [tuple(e) for e in edges] * 2)
        self.assertEqual(list(mesh.link_to_fabric.values()), [NoCChannel.CH0] * 14 + [NoCChannel(7)] * 14)
        for fabric, offset in ((NoCChannel.CH0, 0), (NoCChannel(7), 14)):
            self.assertEqual(mesh.manhattan_path_nodes(0, 5, fabric),
                             [("link", offset), ("core", 1), ("link", offset + 4), ("core", 2), ("link", offset + 8)])
            self.assertEqual(mesh.manhattan_path_nodes(5, 0, fabric),
                             [("link", offset + 13), ("core", 4), ("link", offset + 11), ("core", 3), ("link", offset + 3)])
            self.assertEqual(mesh.manhattan_path_nodes(2, 2, fabric), [])
        expected_core_links = [[core for pair in edges * 2 for core in pair], [i for i in range(28) for _ in range(2)]]
        self.assertEqual(mesh.core_link, expected_core_links)
        self.assertEqual(mesh.link_core, expected_core_links[::-1])
        with self.assertRaisesRegex(ValueError, "outside"):
            mesh.manhattan_path_nodes(0, 0, NoCChannel(2))
        with self.assertRaisesRegex(ValueError, "dimensions/fabrics"):
            Mesh(2, 3, topology=mesh.topology)

    def test_event_stream_rejection_before_features(self):
        compute = [{"start_time": 0, "end_time": 1, "pe_id": 0, "flops": 7}]
        communication = [{"start_time": 1, "end_time": 4, "src_id": 0, "dst_id": 5, "data_size": 16, "fabric_id": 7}]
        self.assertEqual(legacy_event_rows(compute, "compute"), compute)
        self.assertEqual(legacy_event_rows({"trace": communication}, "communication"), communication)
        result = ReplayRuntime(ReplayPlan.load(EXAMPLE)).run().model_dump(mode="json")
        for document in (result, result["trace"], {"trace": result["trace"]}, {"kind": "canonical_topology"}, [3], "text"):
            with self.assertRaises((TypeError, ValueError)):
                legacy_event_rows(document, "communication")
        with self.assertRaisesRegex(ValueError, "unknown legacy event stream"):
            legacy_event_rows([], "unknown")
