"""Profile inventory preservation, mesh parity, and explicit endpoint construction."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simulator_detailed.architecture import Arch
from simulator_detailed.configs.schemas.arch_config import ArchConfig, NoCConfig
from simulator_detailed.configs.schemas.failure_configs import FailSlow
from simulator_detailed.configs.schemas.hardware_profile import HardwareProfileConfig
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.hardware_profile import (
    inspect_profile,
    load_architecture_document,
)
from simulator_detailed.tests.test_topology import REFERENCE_PATH, reference_graph
from simulator_detailed.topology import (
    Topology,
    topology_from_legacy,
    topology_from_profile,
)
from simulator_detailed.utils.definitions import DMAAttachmentMode, NoCChannel, NodeType

EXAMPLE = Path(__file__).parents[1] / "configs/profiles/wormhole_b0_n150_assumed.json"


class TopologyAdapterTests(unittest.TestCase):
    def test_profile_inventory_preserves_identity_and_unknowns(self):
        profile = HardwareProfileConfig.model_validate_json(EXAMPLE.read_text())
        report = inspect_profile(profile)
        topology = topology_from_profile(profile)
        counts = topology.export()["counts"]
        self.assertEqual([counts[k] for k in ("physical_tiles", "fabric_routers", "attachments",
                                               "enabled_workers", "resources")], [120, 240, 240, 72, 86])
        self.assertIsNone(counts["directed_links"])
        self.assertEqual(topology.graph.origin.content_hash, report.profile_sha256)
        self.assertEqual(json.loads(topology.graph.origin.document_json), report.profile.model_dump(mode="json"))
        self.assertEqual({r.resource_id: r.capacity_bytes for r in topology.graph.resources},
                         {key: value.capacity_bytes for key, value in report.memory.resources.items()})
        self.assertFalse(report.can_execute)
        for endpoint in topology.graph.attachments:
            self.assertIsNone(endpoint.enabled)
            self.assertIsNone(endpoint.inject_port)
            self.assertIsNone(endpoint.eject_port)
            self.assertIsNone(endpoint.legacy_binding)
            self.assertFalse(endpoint.permissions_resolved)
            self.assertFalse(endpoint.replay_enabled)
        for router in topology.graph.routers:
            self.assertIsNone(router.enabled)
            coordinate = next(f for f in profile.fabrics if f.fabric_id == router.fabric_id).coordinates[router.tile_id]
            self.assertEqual(router.coordinate.model_dump(), coordinate.model_dump())
        reverse = profile.model_copy(deep=True)
        reverse.layout.tiles.reverse()
        self.assertEqual(topology.content_hash, topology_from_profile(reverse).content_hash)

    def test_mesh_edge_indices_match_independent_reference(self):
        reference = json.loads(REFERENCE_PATH.read_text())
        topology = topology_from_legacy(NoCConfig(x=3, y=2, fabric_ids=(NoCChannel(7), NoCChannel(0))))
        for fabric in (0, 7):
            links = sorted((l for l in topology.graph.links if l.fabric_id == fabric),
                           key=lambda l: topology.link_indices[l.key])
            self.assertEqual([[topology.router_indices[(fabric, l.src_router)],
                               topology.router_indices[(fabric, l.dst_router)]] for l in links],
                             reference["mesh_3x2_ordered_edges"])
        for x, y, per_fabric in ((1, 1, 0), (4, 4, 48)):
            graph = topology_from_legacy(NoCConfig(x=x, y=y))
            self.assertEqual(len(graph.graph.links), 2 * per_fabric)
            self.assertEqual(len(graph.graph.enabled_worker_ids), x * y)

    def test_registry_uses_only_explicit_legacy_bindings(self):
        topology = Topology.compile(CanonicalTopology.model_validate(reference_graph()))
        registry = EndpointRegistry(NoCConfig(), topology)
        with self.assertRaises(KeyError):
            registry.resolve(NodeType.PE, 0, fabric_id=NoCChannel.CH0)
        config = ArchConfig.model_validate_json((EXAMPLE.parents[1] / "instances/mesh_example.json").read_text())
        registry = EndpointRegistry(config.noc)
        self.assertEqual(registry.resolve(NodeType.PE, 0, fabric_id=NoCChannel(2)).local_port, 9)
        self.assertEqual(registry.resolve(NodeType.GM_WDMA, 12, fabric_id=NoCChannel(2),
                                         attachment_mode=DMAAttachmentMode.DUAL_SIDE).local_port, 42)

    def test_architecture_shares_one_graph_and_early_gates(self):
        mapper = Mock()
        mapper.zero_degree.return_value = []
        mapper.all_tasks_completed.return_value = True
        arch = Arch(ArchConfig(), mapper, FailSlow(router=[], link=[], lsu=[], tpu=[]))
        self.assertIs(arch.topology, arch.endpoint_registry.topology)
        self.assertTrue(all(noc.topology is arch.topology for noc in arch.nocs.values()))
        self.assertEqual([c.id for c in arch.cores], list(range(6)))
        graph = CanonicalTopology.model_validate(reference_graph())
        with patch("simulator_detailed.architecture.simpy.Environment", side_effect=AssertionError), self.assertRaises(TypeError):
            Arch(graph, mapper, object())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for kind in ("canonical_topology", "topology_replay", "topology_replay_result"):
                path.write_text(json.dumps({"kind": kind, "schema_version": 1}))
                with self.assertRaisesRegex(ValueError, "topology inspection/replay"):
                    load_architecture_document(path)
