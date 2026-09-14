"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.config import NodeType
from profiling_sim.tests.helpers import device, network, noc_config, message


class SyncTests(unittest.TestCase):
    def test_configurable_counter_count(self):
        arch = device()
        node = arch.nodes[arch.noc.layout.global_node_id(NodeType.INTERCHIP, 20)]
        self.assertEqual(len(node.commids["CREDIT"]), 5)
        waiter = arch.env.process(node.wait_comm_id(7, expected=2))
        node.release_comm_id(7, "CREDIT")
        arch.env.run(until=1)
        self.assertFalse(waiter.triggered)
        node.release_comm_id(7, "CREDIT")
        arch.env.run(until=waiter)
        self.assertEqual(node.commids["CREDIT"][2], 2)

    def test_fifo_bounds_are_configured(self):
        env, noc, pes = network(noc_config(fifo_hardware_count=5, fifo_logical_count=7))
        fifo = noc.register_fifo(4, 6, 2)
        for pair in ((5, 0), (0, 7), (-1, 0)):
            with self.assertRaises(ValueError):
                noc.register_fifo(*pair, 1)
        pes[0].send(7, message(fifo_hw_id=4, fifo_logic_id=6, fifo_check_type=0))
        env.run()
        self.assertEqual(fifo["credits"].level, 2)
        self.assertTrue(pes[5].received)

    def test_sync_uses_selected_local_ports(self):
        env, noc, pes = network()
        pes[0].send(7, message(sync=True))
        env.run()
        self.assertTrue(any(not msg.is_control for _, msg in pes[5].received))
