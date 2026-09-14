"""Configuration-based simulator regressions with synthetic data."""

import unittest
import simpy
from profiling_sim.config import ClockConfig, NodeType
from profiling_sim.memory import Memory
from profiling_sim.tests.helpers import device, device_config


class MemoryTests(unittest.TestCase):
    def test_capacity_and_atomics(self):
        memory = Memory(simpy.Environment(), "test", 17, 3)
        memory.allocate(16)
        with self.assertRaises(MemoryError):
            memory.allocate(2)
        memory.write(3, 7)
        memory.write(3, 4, 1)
        memory.write(3, 9, 2)
        self.assertEqual(memory.read(3), 11)

    def test_configured_clock_and_engine_width(self):
        for ratio in (1.0, 2.5):
            arch = device(
                device_config(clock=ClockConfig(aci_mhz=2, ddr_mhz=2 * ratio))
            )
            gid = arch.noc.layout.global_node_id(NodeType.DDR_WDMA, 9)
            node = arch.nodes[gid]
            self.assertEqual(node.engine_width, 5 * ratio)
            tasks = [arch.env.process(node.transfer(23)) for _ in range(4)]
            arch.env.run(until=arch.env.all_of(tasks))
            self.assertEqual(arch.ddr.used, 92)
            self.assertEqual(len(node.channel_store.items), 3)
