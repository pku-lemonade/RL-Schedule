import unittest
from unittest.mock import Mock

import simpy

from simulator_detailed.architecture import Arch
from simulator_detailed.benchmark_references import GM_WDMA_REFERENCE
from simulator_detailed.configs.schemas.arch_config import (
    CoreConfig,
    DMAEngineConfig,
    DMAType,
    NoCConfig,
)
from simulator_detailed.core import Core
from simulator_detailed.dma_endpoint import DMAReceiveResult, DMATransmitResult
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.utils.definitions import (
    PORT_DDR_RDMA,
    PORT_GM_RDMA,
    PORT_GM_WDMA_CH0,
    PORT_GM_WDMA_CH1,
    DimSlice,
    DMAAttachmentMode,
    Message,
    NMCShapeMode,
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

    @classmethod
    def _build_executable_runtime(
        cls,
        noc_config: NoCConfig,
    ) -> tuple[simpy.Environment, Arch, list[Core]]:
        env, arch = cls._build_runtime(noc_config)
        arch.x_size = noc_config.x
        arch.y_size = noc_config.y
        cores = arch.build_cores(
            env=env,
            config=CoreConfig(),
            noc_config=noc_config,
            mapper=Mock(),
        )
        return env, arch, cores

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

    def test_gm_commands_execute_in_both_directions_and_fabrics(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                    port_bw=64.0,
                    descriptor_issue_cycles=3.0,
                ),
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                    descriptor_issue_cycles=3.0,
                ),
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]

        pe_to_gm = Message(
            src=cores[0].binding_for(NoCChannel.CH0).address,
            dst=gm_wdma.binding_for(NoCChannel.CH0).address,
            index=1000,
            data=[DimSlice(start=0, end=1025)],
        )
        gm_to_pe = Message(
            src=gm_rdma.binding_for(NoCChannel.CH1).address,
            dst=cores[1].binding_for(NoCChannel.CH1).address,
            index=1001,
            data=[DimSlice(start=0, end=513)],
        )

        gm_receive = gm_wdma.recv_message(pe_to_gm)
        pe_send = cores[0].nmc_channel_for(NoCChannel.CH0).send(pe_to_gm)
        pe_receive = cores[1].nmc_channel_for(NoCChannel.CH1).recv_message(
            gm_to_pe,
            NMCShapeMode.STATIC,
        )
        gm_send = gm_rdma.send(gm_to_pe)
        env.run(until=env.all_of((gm_receive, pe_send, pe_receive, gm_send)))

        self.assertIsInstance(gm_receive.value, DMAReceiveResult)
        self.assertIsInstance(gm_send.value, DMATransmitResult)
        self.assertEqual(
            [flit.payload_bytes for flit in gm_receive.value.flits],
            [512, 512, 1],
        )
        self.assertEqual(
            [flit.payload_bytes for flit in gm_send.value.flits],
            [512, 1],
        )
        self.assertEqual(
            gm_receive.value.descriptor_acceptance_time_aci_cycles,
            3.0,
        )
        self.assertEqual(
            gm_send.value.descriptor_acceptance_time_aci_cycles,
            3.0,
        )
        self.assertEqual(
            gm_receive.value.operation_completion_time_aci_cycles,
            GM_WDMA_REFERENCE.operation_latency_aci_cycles(7),
        )
        self.assertLess(
            gm_receive.value.tail_service_completion_time_aci_cycles,
            gm_receive.value.operation_completion_time_aci_cycles,
        )

    def test_dual_fabric_wdma_commands_share_completion_cadence(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                    port_bw=64.0,
                    descriptor_issue_cycles=0.0,
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        source = cores[28]
        messages = tuple(
            Message(
                src=source.binding_for(fabric_id).address,
                dst=gm_wdma.binding_for(fabric_id).address,
                index=1100 + int(fabric_id),
                data=[DimSlice(start=0, end=512)],
            )
            for fabric_id in NoCChannel
        )

        receive_processes = tuple(
            gm_wdma.recv_message(message) for message in messages
        )
        send_processes = tuple(
            source.nmc_channel_for(message.src.fabric_id).send(message)
            for message in messages
        )
        env.run(until=env.all_of((*receive_processes, *send_processes)))

        completion_times = sorted(
            process.value.operation_completion_time_aci_cycles
            for process in receive_processes
        )
        self.assertAlmostEqual(
            completion_times[1] - completion_times[0],
            GM_WDMA_REFERENCE.small_message_completion_interval_aci_cycles,
        )

    def test_wdma_single_source_latency_and_bulk_rate_match_reference(self) -> None:
        for pe_id, hops in ((28, 0), (0, 7)):
            with self.subTest(pe_id=pe_id, hops=hops):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.GM_WDMA,
                            instance_id=0,
                            router_id=28,
                            channels=2,
                            local_ports=[
                                PORT_GM_WDMA_CH0,
                                PORT_GM_WDMA_CH1,
                            ],
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
                message = Message(
                    src=cores[pe_id].binding_for(NoCChannel.CH0).address,
                    dst=gm_wdma.binding_for(NoCChannel.CH0).address,
                    index=1500 + pe_id,
                    data=[DimSlice(start=0, end=512)],
                )

                receive = gm_wdma.recv_message(message)
                send = cores[pe_id].nmc_channel_for(NoCChannel.CH0).send(
                    message
                )
                env.run(until=env.all_of((receive, send)))

                self.assertEqual(
                    receive.value.operation_latency_aci_cycles,
                    GM_WDMA_REFERENCE.operation_latency_aci_cycles(hops),
                )

        bulk_bytes = 512 * 1024
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        bulk_message = Message(
            src=cores[28].binding_for(NoCChannel.CH0).address,
            dst=gm_wdma.binding_for(NoCChannel.CH0).address,
            index=1600,
            data=[DimSlice(start=0, end=bulk_bytes)],
        )

        bulk_receive = gm_wdma.recv_message(bulk_message)
        bulk_send = cores[28].nmc_channel_for(NoCChannel.CH0).send(
            bulk_message
        )
        env.run(until=env.all_of((bulk_receive, bulk_send)))
        effective_rate = (
            bulk_bytes / bulk_receive.value.operation_latency_aci_cycles
        )

        self.assertEqual(
            gm_wdma.service_bytes_per_aci_cycle,
            GM_WDMA_REFERENCE.bulk_bytes_per_aci_cycle,
        )
        self.assertGreaterEqual(
            effective_rate,
            GM_WDMA_REFERENCE.bulk_bytes_per_aci_cycle * 0.96,
        )
        self.assertLessEqual(
            effective_rate,
            GM_WDMA_REFERENCE.bulk_bytes_per_aci_cycle,
        )

    def test_large_n_way_incast_uses_one_wdma_service_engine(self) -> None:
        message_bytes = 128 * 1024
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        source_ids = (28, 24, 20, 16, 12, 8, 4, 0)
        messages = tuple(
            Message(
                src=cores[source_id]
                .binding_for(NoCChannel.CH0)
                .address,
                dst=gm_wdma.binding_for(NoCChannel.CH0).address,
                index=1700 + source_id,
                data=[DimSlice(start=0, end=message_bytes)],
            )
            for source_id in source_ids
        )

        receive_processes = tuple(
            gm_wdma.recv_message(message) for message in messages
        )
        send_processes = tuple(
            cores[source_id]
            .nmc_channel_for(NoCChannel.CH0)
            .send(message)
            for source_id, message in zip(source_ids, messages, strict=True)
        )
        env.run(until=env.all_of((*receive_processes, *send_processes)))

        final_completion = max(
            process.value.operation_completion_time_aci_cycles
            for process in receive_processes
        )
        aggregate_rate = (
            message_bytes * len(messages) / final_completion
        )
        minimum_rate, maximum_rate = (
            GM_WDMA_REFERENCE.incast_bytes_per_aci_cycle_range
        )
        self.assertGreaterEqual(aggregate_rate, minimum_rate)
        self.assertLessEqual(aggregate_rate, maximum_rate)
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH0),
            0,
        )

    def test_wdma_descriptor_capacity_is_independent_per_fabric(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                    port_bw=1.0,
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        source = cores[28]
        ch0_messages = tuple(
            Message(
                src=source.binding_for(NoCChannel.CH0).address,
                dst=gm_wdma.binding_for(NoCChannel.CH0).address,
                index=1300 + message_index,
                data=[DimSlice(start=0, end=512)],
            )
            for message_index in range(5)
        )
        ch1_message = Message(
            src=source.binding_for(NoCChannel.CH1).address,
            dst=gm_wdma.binding_for(NoCChannel.CH1).address,
            index=1400,
            data=[DimSlice(start=0, end=512)],
        )
        messages = (*ch0_messages, ch1_message)

        receive_processes = tuple(
            gm_wdma.recv_message(message) for message in messages
        )
        send_processes = tuple(
            source.nmc_channel_for(message.src.fabric_id).send(message)
            for message in messages
        )
        env.run(until=201.0)

        self.assertEqual(gm_wdma.descriptor_issue_cycles, 40.0)
        self.assertEqual(
            gm_wdma.max_outstanding_descriptors_per_channel,
            4,
        )
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH0),
            4,
        )
        self.assertEqual(
            len(gm_wdma.descriptor_slots[NoCChannel.CH0].queue),
            1,
        )
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH1),
            1,
        )
        self.assertFalse(gm_wdma.descriptor_slots[NoCChannel.CH1].queue)

        env.run(until=env.all_of((*receive_processes, *send_processes)))
        ch0_acceptance_times = [
            process.value.descriptor_acceptance_time_aci_cycles
            for process in receive_processes[:5]
        ]
        self.assertEqual(ch0_acceptance_times[:4], [40.0, 80.0, 120.0, 160.0])
        self.assertGreaterEqual(
            ch0_acceptance_times[4],
            receive_processes[0].value.operation_completion_time_aci_cycles
            + gm_wdma.descriptor_issue_cycles,
        )
        self.assertEqual(
            receive_processes[5].value.descriptor_acceptance_time_aci_cycles,
            200.0,
        )
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH0),
            0,
        )
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH1),
            0,
        )

    def test_same_fabric_rdma_commands_preserve_packet_order(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                    port_bw=64.0,
                    descriptor_issue_cycles=3.0,
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        messages = tuple(
            Message(
                src=gm_rdma.binding_for(NoCChannel.CH0).address,
                dst=cores[pe_id].binding_for(NoCChannel.CH0).address,
                index=1200 + pe_id,
                data=[DimSlice(start=0, end=1024)],
            )
            for pe_id in (0, 1)
        )

        receive_processes = tuple(
            cores[pe_id].nmc_channel_for(NoCChannel.CH0).recv_message(
                message,
                NMCShapeMode.STATIC,
            )
            for pe_id, message in enumerate(messages)
        )
        send_processes = tuple(gm_rdma.send(message) for message in messages)
        env.run(until=env.all_of((*receive_processes, *send_processes)))

        self.assertEqual(
            [process.value.descriptor_acceptance_time_aci_cycles for process in send_processes],
            [3.0, 6.0],
        )
        self.assertLess(
            send_processes[0].value.operation_completion_time_aci_cycles,
            send_processes[1].value.operation_completion_time_aci_cycles,
        )


if __name__ == "__main__":
    unittest.main()
