"""Configuration-based simulator regressions with synthetic data."""

import unittest
from pydantic import ValidationError
from profiling_sim.config import NodeAttachment, NodeType
from profiling_sim.nodes import NodeLayout
from profiling_sim.tests.helpers import device, device_config, noc_config


class NodeTests(unittest.TestCase):
    def test_mapping_comes_from_config(self):
        arch = device()
        layout = arch.noc.layout
        self.assertEqual(len(arch.nodes), len(arch.config.noc.attachments))
        for item in arch.config.noc.attachments:
            gid = layout.global_node_id(item.node_type, item.instance_id)
            self.assertEqual(layout.node_type(gid), item.node_type)
            self.assertEqual(layout.type_local_id(gid), item.instance_id)
            self.assertEqual(arch.nodes[gid].router_id, item.router_id)
            self.assertEqual(
                layout.address(item.node_type, item.instance_id),
                (item.router_id, item.ports[0]),
            )

    def test_layouts_do_not_share_state(self):
        first = NodeLayout(device_config().noc)
        second = NodeLayout(
            noc_config(
                x=2,
                y=2,
                attachments=[
                    NodeAttachment(
                        node_type=NodeType.GM_RDMA,
                        instance_id=77,
                        router_id=3,
                        ports=[55],
                    )
                ],
            )
        )
        self.assertEqual(second.global_node_id(NodeType.GM_RDMA, 77), 4)
        self.assertEqual(first.address(NodeType.GM_RDMA, 12), (1, 30))
        with self.assertRaises(KeyError):
            first.global_node_id(NodeType.GM_RDMA, 77)

    def test_invalid_attachments(self):
        item = NodeAttachment(
            node_type=NodeType.GM_RDMA, instance_id=4, router_id=1, ports=[60]
        )
        for attachments in ([item, item], [item.model_copy(update={"router_id": 100})]):
            with self.assertRaises(ValidationError):
                noc_config(attachments=attachments)
