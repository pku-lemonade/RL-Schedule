import unittest

import simpy

from simulator_detailed.architecture import Arch
from simulator_detailed.configs.schemas.arch_config import (
    DMAEngineConfig,
    DMAType,
    NoCConfig,
)
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.utils.definitions import (
    PORT_DDR_RDMA,
    PORT_GM_RDMA,
    PORT_GM_WDMA_CH0,
    PORT_GM_WDMA_CH1,
    DMAAttachmentMode,
    NoCChannel,
    NodeType,
)


class Phase3DMAEndpointTests(unittest.TestCase):
    @staticmethod
    def _build_runtime(
        noc_config: NoCConfig,
    ) -> tuple[simpy.Environment, Arch]:
        env = simpy.Environment()
        arch = object.__new__(Arch)
        arch.env = env
        arch.endpoint_registry = EndpointRegistry(noc_config)
        arch.nocs = Arch.build_nocs(env, noc_config)
        arch.dma_endpoints = arch.build_dma_endpoints(env, noc_config)
        return env, arch

    def test_arch_binds_gm_endpoints_to_both_data_fabrics(self) -> None:
        gm_rdma_config = DMAEngineConfig(
            dma_type=DMAType.GM_RDMA,
            instance_id=0,
            router_id=28,
            channels=1,
            local_ports=[PORT_GM_RDMA],
        )
        gm_wdma_config = DMAEngineConfig(
            dma_type=DMAType.GM_WDMA,
            instance_id=0,
            router_id=28,
            channels=2,
            local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
        )
        env, arch = self._build_runtime(
            NoCConfig(dma_engines=[gm_rdma_config, gm_wdma_config])
        )

        self.assertEqual(
            set(arch.dma_endpoints),
            {(NodeType.GM_RDMA, 0), (NodeType.GM_WDMA, 0)},
        )
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        self.assertIs(gm_rdma.env, env)
        self.assertIs(gm_rdma.config, gm_rdma_config)
        self.assertIs(gm_wdma.config, gm_wdma_config)
        self.assertIsNot(gm_rdma.internal_datapath, gm_wdma.internal_datapath)

        endpoint_links = []
        for endpoint, attachment_mode in (
            (gm_rdma, DMAAttachmentMode.SINGLE_SIDE),
            (gm_wdma, DMAAttachmentMode.DUAL_SIDE),
        ):
            self.assertEqual(set(endpoint.bindings), set(NoCChannel))
            for fabric_id in NoCChannel:
                binding = endpoint.binding_for(fabric_id)
                expected_address = arch.endpoint_registry.resolve(
                    endpoint.node_type,
                    endpoint.instance_id,
                    fabric_id=fabric_id,
                    attachment_mode=attachment_mode,
                )
                self.assertEqual(binding.address, expected_address)
                self.assertIs(
                    binding.router,
                    arch.nocs[fabric_id].routers[expected_address.router_id],
                )
                self.assertIs(
                    binding.router.port_in[expected_address.local_port],
                    binding.tx_link,
                )
                self.assertIs(
                    binding.router.port_out[expected_address.local_port],
                    binding.rx_link,
                )
                self.assertIs(binding.tx_link.fabric_id, fabric_id)
                self.assertIs(binding.rx_link.fabric_id, fabric_id)
                self.assertIs(binding.tx_link.tracer, binding.router.tracer)
                self.assertIs(binding.rx_link.tracer, binding.router.tracer)
                endpoint_links.extend((binding.tx_link, binding.rx_link))

        self.assertEqual(len({id(link) for link in endpoint_links}), 8)
        self.assertEqual(
            gm_wdma.binding_for(NoCChannel.CH0).address.local_port,
            PORT_GM_WDMA_CH0,
        )
        self.assertEqual(
            gm_wdma.binding_for(NoCChannel.CH1).address.local_port,
            PORT_GM_WDMA_CH1,
        )

        first_request = gm_wdma.internal_datapath.request()
        second_request = gm_wdma.internal_datapath.request()
        env.run()
        self.assertTrue(first_request.triggered)
        self.assertFalse(second_request.triggered)
        gm_wdma.internal_datapath.release(first_request)
        env.run()
        self.assertTrue(second_request.triggered)

    def test_arch_rejects_ddr_runtime_before_phase3_ddr_support(self) -> None:
        ddr_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.DDR_RDMA,
                    instance_id=0,
                    router_id=0,
                    channels=1,
                    local_ports=[PORT_DDR_RDMA],
                )
            ]
        )
        with self.assertRaisesRegex(NotImplementedError, "GM DMA endpoints only"):
            self._build_runtime(ddr_config)


if __name__ == "__main__":
    unittest.main()
