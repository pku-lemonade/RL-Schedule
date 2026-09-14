"""Configuration-based simulator regressions with synthetic data."""

import unittest
from unittest.mock import Mock
import simpy
from profiling_sim.definitions import InterChipOp
from profiling_sim.fabric import InterChipRelocateTable, MultiChipFabric
from profiling_sim.tests.helpers import device_config, message


class FabricTests(unittest.TestCase):
    def test_relocation_uses_configured_logical_ids(self):
        table = InterChipRelocateTable([20, 77])
        table.set(77, 3, 123)
        self.assertEqual(table.lookup(77), (3, 123))
        with self.assertRaises(ValueError):
            table.set(77, 4, 5)
        with self.assertRaises(ValueError):
            table.set(1, 4, 5)

    def test_two_chips_exchange_payload_and_return_credit(self):
        env = simpy.Environment()
        configs = {2: device_config(), 5: device_config()}
        mappers = {}
        for rank in configs:
            mapper = Mock()
            mapper.zero_degree.return_value = []
            mapper.all_tasks_completed.return_value = True
            mappers[rank] = mapper
        fabric = MultiChipFabric(
            env,
            configs,
            mappers,
            [(2, 20, 5, 20)],
            link_latency=3,
            credits=1,
            atomic_latency=2,
            deterministic=True,
        )
        first = fabric.chips[2]
        source = first.noc.routers[0].local_ports[7]["in"]
        source.put(
            message(
                dst=5, dst_rank=5, interchip_op=InterChipOp.WRITE_SUM, immediate_bytes=3
            )
        )
        fabric.execute()
        self.assertTrue(fabric.interchip_events)
        self.assertEqual(len(fabric.atomic_events), 1)
        self.assertTrue(all(link.credits.level == 1 for link in fabric.links))
        self.assertEqual(fabric.relocate[2].lookup(20), (5, 20))

    def test_operation_labels_do_not_pack_wire_registers(self):
        self.assertTrue(InterChipOp.WRITE_SUM.is_atomic)
        self.assertFalse(InterChipOp.WRITE.is_atomic)
        self.assertTrue(InterChipOp.WRITE_WITH_IMMEDIATE.has_immediate)
