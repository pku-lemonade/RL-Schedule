"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.config import NodeType, MemoryConfig
from profiling_sim.definitions import DimSlice, OperatorType
from profiling_sim.dfg import DFG
from profiling_sim.task import Task
from profiling_sim.tests.helpers import device, device_config, message


class IntegrationTests(unittest.TestCase):
    def test_send_task_uses_configured_pe_port(self):
        arch = device()
        dfg = DFG()
        shape = [DimSlice(start=0, end=13)]
        source = dfg.add_node(0, OperatorType.SEND, 0, output_size=shape)
        dfg.add_node(1, OperatorType.RECV, 5, input_size=shape)
        dfg.add_edge(0, 1)
        arch.mapper.dfg = dfg

        def send():
            yield arch.env.process(arch.cores[0].spm.allocate(13, 0))
            yield arch.env.process(Task(source).execute(arch.cores[0]))

        sent = arch.env.process(send())
        received = arch.cores[5].data_in.get()
        arch.execute()
        self.assertTrue(sent.triggered)
        self.assertTrue(received.triggered)
        self.assertEqual(received.value.byte_size(), 13)
        self.assertEqual(received.value.src_local_port, arch.config.noc.pe_local_port)
        self.assertEqual(received.value.dst_local_port, arch.config.noc.pe_local_port)
        self.assertEqual(arch.cores[0].spm.container.level, arch.config.core.spm.size)

    def test_configured_memory_endpoint_receives_payload(self):
        arch = device()
        gid = arch.noc.layout.global_node_id(NodeType.GM_WDMA, 12)
        source = arch.noc.routers[0].local_ports[7]["in"]
        source.put(message(dst=gid, dst_local_port=32, size=23))
        arch.execute()
        self.assertEqual(arch.gm.used, 23)
        self.assertEqual(arch.nodes[gid].received[0][1].byte_size(), 23)

    def test_local_memory_alignment_and_sync_destination(self):
        cfg = device_config(
            memory=MemoryConfig(
                local_memory_size=100,
                local_memory_alignment=3,
                local_memory_sync_target=4,
                local_memory_sync_bytes=2,
            )
        )
        arch = device(cfg)
        gid = arch.noc.layout.global_node_id(NodeType.GM_RDMA, 12)
        arch.noc.routers[0].local_ports[7]["in"].put(
            message(dst=gid, dst_local_port=31, size=12, is_local_memory=True)
        )
        arch.execute()
        self.assertEqual(arch.nodes[gid].local_memory_used, 12)
        self.assertTrue(arch.nodes[gid].local_memory_events)
