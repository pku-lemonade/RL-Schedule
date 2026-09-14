"""Configuration-based simulator regressions with synthetic data."""

import unittest
from pydantic import ValidationError
from profiling_sim.config import NodeAttachment, NodeType
from profiling_sim.tests.helpers import network, noc_config, message


class PortTests(unittest.TestCase):
    def test_arbitrary_port_and_mesh(self):
        for x, y, port in ((2, 3, 43), (4, 2, 61), (1, 1, 9)):
            env, noc, pes = network(noc_config(x=x, y=y, pe_local_port=port))
            msg = message(dst=x * y - 1, src_local_port=port, dst_local_port=port)
            pes[0].send(port, msg)
            env.run()
            self.assertEqual(pes[-1].received[-1][1], msg)

    def test_duplicate_and_negative_ports_rejected(self):
        env, noc, _ = network()
        with self.assertRaises(ValueError):
            noc.attach_local(0, 7)
        with self.assertRaises(ValueError):
            noc.attach_local(0, -1)
        before = dict(noc.node_router)
        for router, port in ((0, 7), (0, -1), (-1, 60), (100, 60)):
            with self.assertRaises(ValueError):
                noc.attach_local(router, port, node_id=1000)
            self.assertEqual(noc.node_router, before)
        with self.assertRaises(ValidationError):
            noc_config(
                attachments=[
                    NodeAttachment(
                        node_type=NodeType.GM_RDMA,
                        instance_id=0,
                        router_id=0,
                        ports=[7],
                    )
                ]
            )
