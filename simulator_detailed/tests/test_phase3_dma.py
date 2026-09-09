import unittest
from unittest.mock import Mock

import simpy
from pydantic import ValidationError

from simulator_detailed.architecture import Arch
from simulator_detailed.benchmark_references import (
    DDR_DMA_REFERENCE,
    GM_RDMA_REFERENCE,
    GM_WDMA_REFERENCE,
)
from simulator_detailed.configs.schemas.arch_config import (
    CoreConfig,
    DMAEngineConfig,
    DMAType,
    NoCConfig,
)
from simulator_detailed.configs.schemas.failure_configs import DMAFail, FailSlow
from simulator_detailed.core import Core
from simulator_detailed.dma_endpoint import (
    DMAReceiveResult,
    DMAServiceEvent,
    DMATransmitResult,
)
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.noc import FlitAction
from simulator_detailed.run import _collect_simulation_events
from simulator_detailed.tracing import collect_dma_service_events, process_events
from simulator_detailed.utils.definitions import (
    PORT_DDR_RDMA,
    PORT_DDR_WDMA,
    PORT_DDR_WDMA_CH0,
    PORT_DDR_WDMA_CH1,
    PORT_GM_RDMA,
    PORT_GM_WDMA,
    PORT_GM_WDMA_CH0,
    PORT_GM_WDMA_CH1,
    BurstLenMode,
    DimSlice,
    DMAAttachmentMode,
    DMACommandMode,
    FlitTrafficType,
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

    @staticmethod
    def _all_ddr_controllers_config(
        wdma_modes: tuple[DMACommandMode, ...],
    ) -> NoCConfig:
        engines = []
        # Hardware controller order from NOC_ARCHITECTURE.md section 9.10.
        for instance_id, (router_id, mode) in enumerate(
            zip((0, 28, 3, 31), wdma_modes, strict=True)
        ):
            paired = mode is DMACommandMode.DUAL_SIDE
            engines.extend(
                (
                    DMAEngineConfig(
                        dma_type=DMAType.DDR_RDMA,
                        instance_id=instance_id,
                        router_id=router_id,
                        channels=1,
                        local_ports=[PORT_DDR_RDMA],
                        descriptor_issue_cycles=3.0,
                    ),
                    DMAEngineConfig(
                        dma_type=DMAType.DDR_WDMA,
                        instance_id=instance_id,
                        router_id=router_id,
                        channels=2 if paired else 1,
                        local_ports=(
                            [PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1]
                            if paired else [PORT_DDR_WDMA]
                        ),
                        descriptor_issue_cycles=3.0 if paired else None,
                        max_outstanding_descriptors_per_channel=(
                            1 if paired else None
                        ),
                    ),
                )
            )
        return NoCConfig(dma_engines=engines)

    def _assert_dma_runtime_drained(self, arch: Arch, cores: list[Core]) -> None:
        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        for endpoint in arch.dma_endpoints.values():
            self.assertEqual(endpoint.rx_data_queue.items, [])
            self.assertEqual(endpoint._receive_ready, {})
            for resource in (
                endpoint.internal_datapath,
                *endpoint.descriptor_issuers.values(),
                *endpoint.descriptor_slots.values(),
            ):
                self.assertEqual(resource.users, [])
                self.assertEqual(resource.queue, [])
            for arbiter in endpoint.tx_burst_arbiters.values():
                self.assertIsNone(arbiter.owner)
                self.assertEqual(arbiter.pending_ports, ())
        for core in cores:
            for fabric_id in NoCChannel:
                nmc = core.nmc_channel_for(fabric_id)
                self.assertEqual(nmc.outstanding_descriptor_count, 0)
                self.assertEqual(nmc.descriptor_slots.queue, [])
                self.assertEqual(nmc.tx_data_queue.items, [])
                self.assertEqual(nmc.rx_data_queue.items, [])
                self.assertIsNone(nmc.tx_burst_arbiter.owner)
                self.assertEqual(nmc.tx_burst_arbiter.pending_ports, ())
        for noc in arch.nocs.values():
            for router in noc.routers:
                self.assertEqual(router.reservation, {})
                for arbiter in router.output_arbiters.values():
                    self.assertIsNone(arbiter.owner)
                    self.assertEqual(arbiter.pending_ports, ())
                for link in (*router.port_in.values(), *router.port_out.values()):
                    if link is not None:
                        self.assertEqual(link.in_flight_flits, 0, link.link_name)
                        self.assertEqual(link.flit_buffer.items, [], link.link_name)

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

    def test_arch_binds_ddr_resources_and_defaults_service_rates(self) -> None:
        ddr_rdma_config = DMAEngineConfig(
            dma_type=DMAType.DDR_RDMA,
            instance_id=0,
            router_id=0,
            channels=1,
            local_ports=[PORT_DDR_RDMA],
        )
        ddr_wdma_config = DMAEngineConfig(
            dma_type=DMAType.DDR_WDMA,
            instance_id=0,
            router_id=0,
            channels=2,
            local_ports=[PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
        )
        env, arch = self._build_runtime(
            NoCConfig(dma_engines=[ddr_rdma_config, ddr_wdma_config])
        )
        ddr_rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, 0)]
        ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]

        self.assertIs(ddr_rdma.config, ddr_rdma_config)
        self.assertIs(ddr_wdma.config, ddr_wdma_config)
        self.assertEqual(set(ddr_rdma.bindings), set(NoCChannel))
        self.assertEqual(set(ddr_wdma.bindings), set(NoCChannel))
        self.assertIsNot(ddr_rdma.internal_datapath, ddr_wdma.internal_datapath)
        self.assertIsNot(
            ddr_rdma.binding_for(NoCChannel.CH0).router,
            ddr_rdma.binding_for(NoCChannel.CH1).router,
        )
        self.assertEqual(
            ddr_rdma.binding_for(NoCChannel.CH0).router.id,
            ddr_rdma.binding_for(NoCChannel.CH1).router.id,
        )
        self.assertEqual(
            ddr_wdma.binding_for(NoCChannel.CH0).address.local_port,
            PORT_DDR_WDMA_CH0,
        )
        self.assertEqual(
            ddr_wdma.binding_for(NoCChannel.CH1).address.local_port,
            PORT_DDR_WDMA_CH1,
        )
        self.assertIs(
            ddr_rdma.descriptor_issuers[NoCChannel.CH0],
            ddr_rdma.descriptor_issuers[NoCChannel.CH1],
        )
        self.assertIsNot(
            ddr_wdma.descriptor_issuers[NoCChannel.CH0],
            ddr_wdma.descriptor_issuers[NoCChannel.CH1],
        )

        self.assertEqual(ddr_rdma.clock_domain.endpoint_clock_mhz, 1200.0)
        self.assertEqual(ddr_rdma.clock_domain.aci_clock_mhz, 1125.0)
        self.assertAlmostEqual(
            ddr_rdma.clock_domain.endpoint_cycles_per_aci_cycle,
            1200.0 / 1125.0,
        )
        self.assertAlmostEqual(
            ddr_rdma.clock_domain.endpoint_cycles_to_aci_cycles(16.0),
            15.0,
        )
        self.assertAlmostEqual(
            ddr_rdma.clock_domain.aci_cycles_to_endpoint_cycles(15.0),
            16.0,
        )
        self.assertEqual(
            ddr_wdma.service_bytes_per_aci_cycle,
            DDR_DMA_REFERENCE.wdma_service_bytes_per_aci_cycle,
        )
        self.assertEqual(
            ddr_rdma.service_bytes_per_aci_cycle,
            DDR_DMA_REFERENCE.rdma_service_bytes_per_aci_cycle,
        )

        first_request = ddr_wdma.internal_datapath.request()
        second_request = ddr_wdma.internal_datapath.request()
        env.run()
        self.assertTrue(first_request.triggered)
        self.assertFalse(second_request.triggered)
        ddr_wdma.internal_datapath.release(first_request)
        env.run()
        self.assertTrue(second_request.triggered)

        upload = Message(
            src=arch.endpoint_registry.resolve(
                NodeType.PE,
                0,
                fabric_id=NoCChannel.CH0,
            ),
            dst=ddr_wdma.binding_for(NoCChannel.CH0).address,
            index=900,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        download = Message(
            src=ddr_rdma.binding_for(NoCChannel.CH1).address,
            dst=arch.endpoint_registry.resolve(
                NodeType.PE,
                1,
                fabric_id=NoCChannel.CH1,
            ),
            index=901,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "descriptor issue timing is uncalibrated",
        ):
            ddr_wdma.recv_message(upload)
        with self.assertRaisesRegex(
            RuntimeError,
            "descriptor issue timing is uncalibrated",
        ):
            ddr_rdma.send(download)

    def test_ddr_wdma_latency_reference_covers_each_size_regime(self) -> None:
        reference = DDR_DMA_REFERENCE
        for payload_bytes, expected_latency in reference.wdma_min_latency_samples:
            with self.subTest(payload_bytes=payload_bytes):
                self.assertEqual(
                    reference.wdma_min_operation_latency_aci_cycles(payload_bytes),
                    expected_latency,
                )

        lower_size, lower_latency = reference.wdma_min_latency_samples[1]
        upper_size, upper_latency = reference.wdma_min_latency_samples[2]
        midpoint_size = (lower_size + upper_size) // 2
        self.assertEqual(
            reference.wdma_min_operation_latency_aci_cycles(midpoint_size),
            (lower_latency + upper_latency) / 2,
        )
        threshold = reference.wdma_large_min_threshold_bytes
        self.assertEqual(
            reference.wdma_min_operation_latency_aci_cycles(threshold),
            reference.wdma_large_min_intercept_aci_cycles
            + threshold / reference.wdma_service_bytes_per_aci_cycle,
        )
        self.assertEqual(
            reference.wdma_min_operation_latency_aci_cycles(2 * threshold),
            reference.wdma_large_min_intercept_aci_cycles
            + 2 * threshold / reference.wdma_service_bytes_per_aci_cycle,
        )
        self.assertEqual(
            reference.wdma_min_operation_latency_aci_cycles(1),
            reference.wdma_min_latency_samples[0][1],
        )
        with self.assertRaisesRegex(ValueError, "payload size must be positive"):
            reference.wdma_min_operation_latency_aci_cycles(0)

    def test_paired_ddr_wdma_completion_uses_size_aware_floor(self) -> None:
        for payload_bytes in (512, 4 * 1024, 10 * 1024, 64 * 1024):
            with self.subTest(payload_bytes=payload_bytes):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.DDR_WDMA,
                            instance_id=0,
                            router_id=0,
                            channels=2,
                            local_ports=[
                                PORT_DDR_WDMA_CH0,
                                PORT_DDR_WDMA_CH1,
                            ],
                            descriptor_issue_cycles=0.0,
                            max_outstanding_descriptors_per_channel=2,
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
                message = Message(
                    src=cores[0].binding_for(NoCChannel.CH0).address,
                    dst=ddr_wdma.binding_for(NoCChannel.CH0).address,
                    index=909,
                    data=[DimSlice(start=0, end=payload_bytes)],
                    nmc_shape_mode=NMCShapeMode.STATIC,
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
                )

                receive = ddr_wdma.recv_message(message)
                send = cores[0].nmc_channel_for(NoCChannel.CH0).send(message)
                env.run(until=env.all_of((receive, send)))

                self.assertEqual(
                    receive.value.operation_latency_aci_cycles,
                    DDR_DMA_REFERENCE.wdma_min_operation_latency_aci_cycles(
                        payload_bytes
                    ),
                )

    def test_ddr_rdma_latency_reference_covers_size_and_hop_regimes(self) -> None:
        reference = DDR_DMA_REFERENCE
        for payload_bytes, expected_latency in reference.rdma_min_latency_samples:
            with self.subTest(payload_bytes=payload_bytes):
                self.assertEqual(
                    reference.rdma_min_operation_latency_aci_cycles(
                        payload_bytes,
                        0,
                    ),
                    expected_latency,
                )

        lower_size, lower_latency = reference.rdma_min_latency_samples[1]
        upper_size, upper_latency = reference.rdma_min_latency_samples[2]
        midpoint_size = (lower_size + upper_size) // 2
        self.assertEqual(
            reference.rdma_min_operation_latency_aci_cycles(midpoint_size, 0),
            (lower_latency + upper_latency) / 2,
        )
        threshold = reference.rdma_large_min_threshold_bytes
        hops = 3
        hop_latency = hops * reference.rdma_hop_slope_aci_cycles
        self.assertEqual(
            reference.rdma_min_operation_latency_aci_cycles(threshold, hops),
            reference.rdma_large_min_intercept_aci_cycles
            + threshold / reference.rdma_service_bytes_per_aci_cycle
            + hop_latency,
        )
        self.assertEqual(
            reference.rdma_min_operation_latency_aci_cycles(
                2 * threshold,
                hops,
            ),
            reference.rdma_large_min_intercept_aci_cycles
            + 2 * threshold / reference.rdma_service_bytes_per_aci_cycle
            + hop_latency,
        )
        self.assertEqual(
            reference.rdma_min_operation_latency_aci_cycles(1, 2),
            reference.rdma_min_latency_samples[0][1]
            + 2 * reference.rdma_hop_slope_aci_cycles,
        )
        with self.assertRaisesRegex(ValueError, "payload size must be positive"):
            reference.rdma_min_operation_latency_aci_cycles(0, 0)
        with self.assertRaisesRegex(ValueError, "hop count cannot be negative"):
            reference.rdma_min_operation_latency_aci_cycles(512, -1)

    def test_paired_ddr_rdma_completion_uses_size_and_hop_floor(self) -> None:
        cases = (
            (512, 0),
            (4 * 1024, 0),
            (10 * 1024, 0),
            (64 * 1024, 0),
            (64 * 1024, 7),
        )
        for payload_bytes, hops in cases:
            with self.subTest(payload_bytes=payload_bytes, hops=hops):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.DDR_RDMA,
                            instance_id=0,
                            router_id=0,
                            channels=1,
                            local_ports=[PORT_DDR_RDMA],
                            descriptor_issue_cycles=0.0,
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                ddr_rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, 0)]
                destination_id = hops * noc_config.x
                message = Message(
                    src=ddr_rdma.binding_for(NoCChannel.CH0).address,
                    dst=cores[destination_id]
                    .binding_for(NoCChannel.CH0)
                    .address,
                    index=910 + destination_id,
                    data=[DimSlice(start=0, end=payload_bytes)],
                    nmc_shape_mode=NMCShapeMode.STATIC,
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
                )

                receive = cores[destination_id].nmc_channel_for(
                    NoCChannel.CH0
                ).recv_message(message, NMCShapeMode.STATIC)
                send = ddr_rdma.send(message)
                env.run(until=env.all_of((receive, send)))

                self.assertEqual(
                    receive.value.operation_latency_aci_cycles,
                    DDR_DMA_REFERENCE.rdma_min_operation_latency_aci_cycles(
                        payload_bytes,
                        hops,
                    ),
                )
                self.assertLess(
                    send.value.operation_completion_time_aci_cycles,
                    receive.value.operation_completion_time_aci_cycles,
                )

    def test_paired_ddr_requires_explicit_uncalibrated_parameters(self) -> None:
        for config, expected_error in (
            (
                DMAEngineConfig(
                    dma_type=DMAType.DDR_RDMA,
                    instance_id=0,
                    router_id=0,
                    channels=1,
                    local_ports=[PORT_DDR_RDMA],
                    port_bw=64.0,
                ),
                "descriptor issue timing is uncalibrated",
            ),
            (
                DMAEngineConfig(
                    dma_type=DMAType.DDR_WDMA,
                    instance_id=0,
                    router_id=0,
                    channels=2,
                    local_ports=[PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
                    port_bw=64.0,
                    descriptor_issue_cycles=0.0,
                ),
                "descriptor capacity is not characterized",
            ),
        ):
            with self.subTest(dma_type=config.dma_type.name):
                _, arch = self._build_runtime(NoCConfig(dma_engines=[config]))
                node_type = (
                    NodeType.DDR_RDMA
                    if config.dma_type is DMAType.DDR_RDMA
                    else NodeType.DDR_WDMA
                )
                endpoint = arch.dma_endpoints[(node_type, 0)]
                pe_address = arch.endpoint_registry.resolve(
                    NodeType.PE,
                    0,
                    fabric_id=NoCChannel.CH0,
                )
                message = Message(
                    src=(
                        endpoint.binding_for(NoCChannel.CH0).address
                        if node_type is NodeType.DDR_RDMA
                        else pe_address
                    ),
                    dst=(
                        pe_address
                        if node_type is NodeType.DDR_RDMA
                        else endpoint.binding_for(NoCChannel.CH0).address
                    ),
                    index=908,
                    data=[DimSlice(start=0, end=512)],
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
                )

                with self.assertRaisesRegex(RuntimeError, expected_error):
                    if node_type is NodeType.DDR_RDMA:
                        endpoint.send(message)
                    else:
                        endpoint.recv_message(message)

    def test_dma_endpoint_clock_conversion_uses_configured_aci_clock(self) -> None:
        ddr_config = NoCConfig(
            aci_clock_mhz=1000.0,
            noc_clock_mhz=2000.0,
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.DDR_RDMA,
                    instance_id=0,
                    router_id=0,
                    channels=1,
                    local_ports=[PORT_DDR_RDMA],
                )
            ],
        )
        _, arch = self._build_runtime(ddr_config)
        clock_domain = arch.dma_endpoints[
            (NodeType.DDR_RDMA, 0)
        ].clock_domain

        self.assertEqual(clock_domain.endpoint_clock_mhz, 1200.0)
        self.assertEqual(clock_domain.aci_clock_mhz, 1000.0)
        self.assertAlmostEqual(
            clock_domain.endpoint_cycles_to_aci_cycles(6.0),
            5.0,
        )

    def test_paired_ddr_commands_execute_in_both_directions(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.DDR_RDMA,
                    instance_id=0,
                    router_id=0,
                    channels=1,
                    local_ports=[PORT_DDR_RDMA],
                    descriptor_issue_cycles=3.0,
                ),
                DMAEngineConfig(
                    dma_type=DMAType.DDR_WDMA,
                    instance_id=0,
                    router_id=0,
                    channels=2,
                    local_ports=[PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
                    descriptor_issue_cycles=3.0,
                    max_outstanding_descriptors_per_channel=2,
                ),
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        ddr_rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, 0)]
        ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
        self.assertEqual(
            ddr_wdma.service_bytes_per_aci_cycle,
            DDR_DMA_REFERENCE.wdma_service_bytes_per_aci_cycle,
        )
        self.assertEqual(
            ddr_rdma.service_bytes_per_aci_cycle,
            DDR_DMA_REFERENCE.rdma_service_bytes_per_aci_cycle,
        )

        upload = Message(
            src=cores[0].binding_for(NoCChannel.CH0).address,
            dst=ddr_wdma.binding_for(NoCChannel.CH0).address,
            index=902,
            data=[DimSlice(start=0, end=1025)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        download = Message(
            src=ddr_rdma.binding_for(NoCChannel.CH1).address,
            dst=cores[1].binding_for(NoCChannel.CH1).address,
            index=903,
            data=[DimSlice(start=0, end=513)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )

        ddr_receive = ddr_wdma.recv_message(upload)
        pe_send = cores[0].nmc_channel_for(NoCChannel.CH0).send(upload)
        pe_receive = cores[1].nmc_channel_for(NoCChannel.CH1).recv_message(
            download,
            NMCShapeMode.DYNAMIC,
        )
        ddr_send = ddr_rdma.send(download)
        env.run(until=env.all_of((ddr_receive, pe_send, pe_receive, ddr_send)))

        self.assertIsInstance(ddr_receive.value, DMAReceiveResult)
        self.assertIsInstance(ddr_send.value, DMATransmitResult)
        self.assertEqual(
            [flit.payload_bytes for flit in ddr_receive.value.flits],
            [512, 512, 1],
        )
        self.assertEqual(
            [flit.payload_bytes for flit in ddr_send.value.flits],
            [512, 1],
        )
        self.assertEqual(
            ddr_receive.value.descriptor_acceptance_time_aci_cycles,
            3.0,
        )
        self.assertEqual(
            ddr_send.value.descriptor_acceptance_time_aci_cycles,
            3.0,
        )
        self.assertEqual(
            ddr_wdma.outstanding_descriptor_count(NoCChannel.CH0),
            0,
        )
        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
        traffic_types = {
            event.traffic_type
            for noc in arch.nocs.values()
            for event in noc.tracer.events
            if event.msg_id in (upload.index, download.index)
        }
        self.assertEqual(traffic_types, {FlitTrafficType.PAYLOAD})

    def test_paired_ddr_channels_share_each_endpoint_datapath(self) -> None:
        for dma_type, local_ports, node_type in (
            (
                DMAType.DDR_RDMA,
                [PORT_DDR_RDMA],
                NodeType.DDR_RDMA,
            ),
            (
                DMAType.DDR_WDMA,
                [PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
                NodeType.DDR_WDMA,
            ),
        ):
            with self.subTest(dma_type=dma_type.name):
                is_wdma = dma_type is DMAType.DDR_WDMA
                config = DMAEngineConfig(
                    dma_type=dma_type,
                    instance_id=0,
                    router_id=0,
                    channels=2 if is_wdma else 1,
                    local_ports=local_ports,
                    descriptor_issue_cycles=10.0 if is_wdma else 0.0,
                    max_outstanding_descriptors_per_channel=(
                        2 if is_wdma else None
                    ),
                )
                env, arch, cores = self._build_executable_runtime(
                    NoCConfig(dma_engines=[config])
                )
                endpoint = arch.dma_endpoints[(node_type, 0)]
                expected_service_rate = (
                    DDR_DMA_REFERENCE.wdma_service_bytes_per_aci_cycle
                    if is_wdma
                    else DDR_DMA_REFERENCE.rdma_service_bytes_per_aci_cycle
                )
                self.assertEqual(
                    endpoint.service_bytes_per_aci_cycle,
                    expected_service_rate,
                )
                messages = tuple(
                    Message(
                        src=(
                            cores[0].binding_for(fabric_id).address
                            if is_wdma
                            else endpoint.binding_for(fabric_id).address
                        ),
                        dst=(
                            endpoint.binding_for(fabric_id).address
                            if is_wdma
                            else cores[0].binding_for(fabric_id).address
                        ),
                        index=904 + int(fabric_id),
                        data=[DimSlice(start=0, end=512)],
                        dma_command_mode=DMACommandMode.DUAL_SIDE,
                    )
                    for fabric_id in NoCChannel
                )

                if is_wdma:
                    dma_processes = tuple(
                        endpoint.recv_message(message) for message in messages
                    )
                    pe_processes = tuple(
                        cores[0]
                        .nmc_channel_for(message.src.fabric_id)
                        .send(message)
                        for message in messages
                    )
                else:
                    dma_processes = tuple(
                        endpoint.send(message) for message in messages
                    )
                    pe_processes = tuple(
                        cores[0]
                        .nmc_channel_for(message.dst.fabric_id)
                        .recv_message(message, NMCShapeMode.DYNAMIC)
                        for message in messages
                    )
                env.run(until=env.all_of((*dma_processes, *pe_processes)))

                if is_wdma:
                    self.assertEqual(
                        [
                            process.value.descriptor_acceptance_time_aci_cycles
                            for process in dma_processes
                        ],
                        [10.0, 10.0],
                    )
                    self.assertEqual(
                        [
                            process.value.operation_latency_aci_cycles
                            for process in dma_processes
                        ],
                        [
                            DDR_DMA_REFERENCE.wdma_min_operation_latency_aci_cycles(
                                512
                            )
                        ]
                        * 2,
                    )
                    service_boundaries = sorted(
                        process.value.tail_service_completion_time_aci_cycles
                        for process in dma_processes
                    )
                else:
                    service_boundaries = sorted(
                        process.value.final_local_handoff_time_aci_cycles
                        for process in dma_processes
                    )
                self.assertAlmostEqual(
                    service_boundaries[1] - service_boundaries[0],
                    512.0 / expected_service_rate,
                )
                self.assertEqual(
                    arch.dma_commands.pending_dual_side_commands,
                    0,
                )

    def test_single_side_ddr_upload_waits_for_response_and_retires_state(
        self,
    ) -> None:
        for fabric_id, source_id, payload_bytes in (
            (NoCChannel.CH0, 0, 1),
            (NoCChannel.CH0, 0, 512),
            (NoCChannel.CH1, 28, 513),
            (NoCChannel.CH1, 31, 8 * 512 + 1),
        ):
            with self.subTest(
                fabric_id=fabric_id.name,
                source_id=source_id,
                payload_bytes=payload_bytes,
            ):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.DDR_WDMA,
                            instance_id=0,
                            router_id=0,
                            channels=1,
                            local_ports=[PORT_DDR_WDMA],
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
                nmc = cores[source_id].nmc_channel_for(fabric_id)
                upload = Message(
                    src=cores[source_id].binding_for(fabric_id).address,
                    dst=ddr_wdma.binding_for(fabric_id).address,
                    index=906,
                    data=[DimSlice(start=0, end=payload_bytes)],
                    dma_command_mode=DMACommandMode.SINGLE_SIDE,
                )
                with self.assertRaisesRegex(ValueError, "initiated by NMCChannel"):
                    ddr_wdma.recv_message(upload)

                # Stall response consumption after allowing all upload service.
                response_blocker = nmc.rx_datapath.request()
                send = nmc.send(upload)
                env.run()
                self.assertFalse(send.triggered)
                self.assertEqual(nmc.outstanding_descriptor_count, 1)
                self.assertEqual(arch.dma_commands.pending_single_side_commands, 1)
                self.assertEqual(ddr_wdma.descriptor_slots, {})

                tracer = arch.nocs[fabric_id].tracer
                injections = [
                    event
                    for event in tracer.events
                    if event.action is FlitAction.INJECT
                    and event.msg_id == upload.index
                ]
                payload = [
                    event
                    for event in injections
                    if event.traffic_type is FlitTrafficType.PAYLOAD
                ]
                responses = [
                    event
                    for event in injections
                    if event.traffic_type is FlitTrafficType.DMA_RESPONSE
                ]
                self.assertEqual(len(injections), upload.flit_count() + 1)
                self.assertEqual(len(payload), upload.flit_count())
                self.assertEqual(
                    sum(event.payload_bytes for event in payload), payload_bytes
                )
                self.assertEqual(
                    [event.dma_header_bytes for event in payload],
                    [upload.header_bytes] + [0] * (upload.flit_count() - 1),
                )
                self.assertEqual(len(responses), 1)
                response = responses[0]
                self.assertEqual(
                    (response.src_router, response.dst_router), (0, source_id)
                )
                self.assertEqual(response.dma_header_bytes, upload.header_bytes)
                self.assertEqual(response.payload_bytes, 0)
                payload_tail = next(
                    event
                    for event in tracer.events
                    if event.action is FlitAction.EJECT
                    and event.msg_id == upload.index
                    and event.traffic_type is FlitTrafficType.PAYLOAD
                    and event.is_tail
                )
                self.assertGreaterEqual(
                    response.time - payload_tail.time,
                    ddr_wdma.service_interval_aci_cycles,
                )

                nmc.rx_datapath.release(response_blocker)
                env.run(until=send)
                self.assertLess(
                    send.value.final_local_handoff_time_aci_cycles,
                    send.value.operation_completion_time_aci_cycles,
                )
                self.assertEqual(nmc.outstanding_descriptor_count, 0)
                self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
                self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
                self.assertEqual(
                    tracer.message_fabric_timings()[
                        (fabric_id, upload.index)
                    ].final_ejection_time_aci_cycles,
                    payload_tail.time,
                )

    def test_single_side_ddr_uploads_share_service_across_fabrics(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.DDR_WDMA,
                    instance_id=0,
                    router_id=0,
                    channels=1,
                    local_ports=[PORT_DDR_WDMA],
                    # Target-side paired descriptor settings must not gate
                    # these PE-initiated single-side commands.
                    descriptor_issue_cycles=10_000.0,
                    max_outstanding_descriptors_per_channel=1,
                ),
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
        sends = []
        for fabric_id in NoCChannel:
            upload = Message(
                src=cores[0].binding_for(fabric_id).address,
                dst=ddr_wdma.binding_for(fabric_id).address,
                index=906,
                data=[DimSlice(start=0, end=512)],
                dma_command_mode=DMACommandMode.SINGLE_SIDE,
                nmc_shape_mode=NMCShapeMode.STATIC,
            )
            sends.append(cores[0].nmc_channel_for(fabric_id).send(upload))
        env.run(until=env.all_of(sends))

        response_times = []
        for fabric_id in NoCChannel:
            responses = [
                event
                for event in arch.nocs[fabric_id].tracer.events
                if event.action is FlitAction.INJECT
                and event.msg_id == 906
                and event.traffic_type is FlitTrafficType.DMA_RESPONSE
            ]
            self.assertEqual(len(responses), 1)
            response_times.append(responses[0].time)
            self.assertEqual(ddr_wdma.outstanding_descriptor_count(fabric_id), 0)
            self.assertEqual(
                cores[0].nmc_channel_for(fabric_id).outstanding_descriptor_count, 0
            )
        response_times.sort()
        self.assertAlmostEqual(response_times[1] - response_times[0], 512.0 / 117.0)
        completion_times = sorted(
            send.value.operation_completion_time_aci_cycles for send in sends
        )
        self.assertAlmostEqual(
            completion_times[1] - completion_times[0], 512.0 / 117.0
        )
        self.assertLess(completion_times[-1], 10_000.0)
        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)

    def test_single_side_ddr_upload_rejects_invalid_header_and_response(self) -> None:
        _, arch, cores = self._build_executable_runtime(
            NoCConfig(
                dma_engines=[
                    DMAEngineConfig(
                        dma_type=DMAType.DDR_WDMA,
                        instance_id=0,
                        router_id=0,
                        channels=1,
                        local_ports=[PORT_DDR_WDMA],
                    )
                ]
            )
        )
        ddr_wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
        upload = Message(
            src=cores[28].binding_for(NoCChannel.CH0).address,
            dst=ddr_wdma.binding_for(NoCChannel.CH0).address,
            index=906,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.SINGLE_SIDE,
        )
        commands = arch.dma_commands
        completion = commands.post_single_side_upload(upload)
        head = upload.packetize()[0]
        with self.assertRaisesRegex(RuntimeError, "no accepted single-side upload"):
            commands.create_single_side_upload_response(upload)
        with self.assertRaisesRegex(RuntimeError, "wrong single-side target"):
            commands.accept_single_side_upload_header(
                head, ddr_wdma.binding_for(NoCChannel.CH1).address
            )
        with self.assertRaisesRegex(RuntimeError, "invalid flit"):
            commands.accept_single_side_upload_header(
                head.model_copy(update={"dma_header_bytes": 0}), upload.dst
            )
        commands.accept_single_side_upload_header(head, upload.dst)
        with self.assertRaisesRegex(RuntimeError, "accepted twice"):
            commands.accept_single_side_upload_header(head, upload.dst)
        response = commands.create_single_side_upload_response(upload)
        self.assertEqual(response.src_local_port, PORT_DDR_WDMA)
        with self.assertRaisesRegex(RuntimeError, "created twice"):
            commands.create_single_side_upload_response(upload)
        with self.assertRaisesRegex(RuntimeError, "invalid single-side response"):
            commands.accept_single_side_upload_response(
                response, cores[0].binding_for(NoCChannel.CH0).address
            )
        with self.assertRaisesRegex(RuntimeError, "invalid single-side response"):
            commands.accept_single_side_upload_response(
                response.model_copy(update={"dma_header_bytes": 0}), upload.src
            )
        self.assertFalse(completion.triggered)
        self.assertEqual(commands.pending_single_side_commands, 1)
        commands.accept_single_side_upload_response(response, upload.src)
        self.assertTrue(completion.triggered)
        self.assertEqual(commands.pending_single_side_commands, 0)
        with self.assertRaisesRegex(RuntimeError, "no pending single-side upload"):
            commands.accept_single_side_upload_response(response, upload.src)

    def test_single_side_ddr_download_is_pe_initiated_on_both_fabrics(
        self,
    ) -> None:
        for fabric_id, destination_id, hops in (
            (NoCChannel.CH0, 0, 0),
            (NoCChannel.CH1, 28, 7),
        ):
            with self.subTest(fabric_id=fabric_id.name, hops=hops):
                noc_config = NoCConfig(
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
                env, arch, cores = self._build_executable_runtime(noc_config)
                ddr_rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, 0)]
                message = Message(
                    src=ddr_rdma.binding_for(fabric_id).address,
                    dst=cores[destination_id].binding_for(fabric_id).address,
                    index=907 + int(fabric_id),
                    data=[DimSlice(start=0, end=513)],
                    dma_command_mode=DMACommandMode.SINGLE_SIDE,
                )

                with self.assertRaisesRegex(ValueError, "initiated by NMCChannel"):
                    ddr_rdma.send(message)
                receive = cores[destination_id].nmc_channel_for(
                    fabric_id
                ).recv_message(message, NMCShapeMode.DYNAMIC)
                env.run(until=receive)

                self.assertEqual(
                    receive.value.operation_latency_aci_cycles,
                    DDR_DMA_REFERENCE.rdma_min_operation_latency_aci_cycles(
                        513,
                        hops,
                    ),
                )
                self.assertEqual(
                    [flit.dma_header_bytes for flit in receive.value.flits],
                    [message.header_bytes, 0],
                )
                injections = [
                    event
                    for event in arch.nocs[fabric_id].tracer.events
                    if event.action is FlitAction.INJECT
                    and event.msg_id == message.index
                ]
                self.assertEqual(
                    {event.traffic_type for event in injections},
                    {FlitTrafficType.DMA_REQUEST, FlitTrafficType.PAYLOAD},
                )
                request = next(
                    event
                    for event in injections
                    if event.traffic_type is FlitTrafficType.DMA_REQUEST
                )
                self.assertEqual(
                    (request.src_router, request.dst_router),
                    (destination_id, 0),
                )
                self.assertEqual(request.dma_header_bytes, message.header_bytes)
                self.assertEqual(
                    arch.dma_commands.pending_single_side_commands,
                    0,
                )
                self.assertIn(
                    (fabric_id, message.index),
                    arch.nocs[fabric_id].tracer.message_fabric_timings(),
                )

    def test_ddr_mixed_commands_across_all_controllers_and_fabrics(self) -> None:
        # One WDMA attachment per controller is configurable at a time. Swap
        # layouts so every controller executes both upload protocols.
        for first_mode in DMACommandMode:
            with self.subTest(first_mode=first_mode.name):
                other_mode = (
                    DMACommandMode.SINGLE_SIDE
                    if first_mode is DMACommandMode.DUAL_SIDE
                    else DMACommandMode.DUAL_SIDE
                )
                wdma_modes = (first_mode, other_mode, first_mode, other_mode)
                env, arch, cores = self._build_executable_runtime(
                    self._all_ddr_controllers_config(wdma_modes)
                )
                for batch, (shape_mode, payload_bytes) in enumerate(
                    (
                        (NMCShapeMode.STATIC, 513),
                        (NMCShapeMode.DYNAMIC, 8 * 512 + 1),
                    )
                ):
                    commands = []
                    processes = []
                    for controller, corner in enumerate((0, 28, 3, 31)):
                        rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, controller)]
                        wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, controller)]
                        for fabric_id in NoCChannel:
                            # Cross-corner traffic contends on shared mesh links.
                            pe = cores[31 - corner]
                            nmc = pe.nmc_channel_for(fabric_id)
                            pe_address = pe.binding_for(fabric_id).address
                            task_id = 2000 + batch * 100 + controller * 10
                            upload = Message(
                                src=pe_address,
                                dst=wdma.binding_for(fabric_id).address,
                                index=task_id,
                                data=[DimSlice(start=0, end=payload_bytes)],
                                nmc_shape_mode=shape_mode,
                                dma_command_mode=wdma_modes[controller],
                            )
                            send = nmc.send(upload)
                            processes.append(send)
                            completion = send
                            if (
                                upload.dma_command_mode
                                is DMACommandMode.DUAL_SIDE
                            ):
                                completion = wdma.recv_message(upload)
                                processes.append(completion)
                            commands.append((upload, completion))

                            # RDMA port 7 supports both command modes in the
                            # same runtime, including on the same fabric.
                            for offset, mode in enumerate(
                                DMACommandMode, start=1
                            ):
                                download = Message(
                                    src=rdma.binding_for(fabric_id).address,
                                    dst=pe_address,
                                    index=task_id + offset,
                                    data=[DimSlice(start=0, end=payload_bytes)],
                                    dma_command_mode=mode,
                                )
                                receive = nmc.recv_message(download, shape_mode)
                                processes.append(receive)
                                if mode is DMACommandMode.DUAL_SIDE:
                                    processes.append(rdma.send(download))
                                commands.append((download, receive))

                    env.run(until=env.all_of(processes))
                    # Drain delayed credit returns before the next batch.
                    env.run()
                    self._assert_dma_runtime_drained(arch, cores)
                    for message, completion in commands:
                        with self.subTest(
                            batch=batch,
                            fabric=message.src.fabric_id.name,
                            task_id=message.index,
                        ):
                            self.assertEqual(
                                completion.value.flits, tuple(message.packetize())
                            )
                            tracer = arch.nocs[message.src.fabric_id].tracer
                            injections = [
                                event for event in tracer.events
                                if event.action is FlitAction.INJECT
                                and event.msg_id == message.index
                            ]
                            is_download = (
                                message.src.node_type is NodeType.DDR_RDMA
                            )
                            single = (
                                message.dma_command_mode
                                is DMACommandMode.SINGLE_SIDE
                            )
                            control_type = (
                                FlitTrafficType.DMA_REQUEST
                                if is_download else FlitTrafficType.DMA_RESPONSE
                            )
                            control = [
                                event for event in injections
                                if event.traffic_type is not FlitTrafficType.PAYLOAD
                            ]
                            self.assertEqual(len(control), int(single))
                            if single:
                                self.assertIs(control[0].traffic_type, control_type)
                                self.assertEqual(
                                    (control[0].src_router, control[0].dst_router),
                                    (message.dst.router_id, message.src.router_id),
                                )
                            self.assertEqual(
                                len(injections), message.flit_count() + int(single)
                            )
                            timing = tracer.message_fabric_timings()[
                                (message.src.fabric_id, message.index)
                            ]
                            self.assertGreaterEqual(
                                completion.value.operation_completion_time_aci_cycles,
                                timing.final_ejection_time_aci_cycles,
                            )
                            if is_download:
                                self.assertGreaterEqual(
                                    completion.value.operation_latency_aci_cycles,
                                    DDR_DMA_REFERENCE.rdma_min_operation_latency_aci_cycles(
                                        payload_bytes, 10
                                    ),
                                )
                            elif not single:
                                self.assertGreaterEqual(
                                    completion.value.operation_latency_aci_cycles,
                                    DDR_DMA_REFERENCE.wdma_min_operation_latency_aci_cycles(
                                        payload_bytes
                                    ),
                                )

    def test_blocked_ddr_direction_does_not_block_other_engines(self) -> None:
        for blocked_type in (NodeType.DDR_RDMA, NodeType.DDR_WDMA):
            with self.subTest(blocked_type=blocked_type.name):
                env, arch, cores = self._build_executable_runtime(
                    self._all_ddr_controllers_config(
                        (DMACommandMode.SINGLE_SIDE,) * 4
                    )
                )
                blocked_endpoint = arch.dma_endpoints[(blocked_type, 0)]
                blocker = blocked_endpoint.internal_datapath.request()
                blocked_commands = []
                independent_commands = []
                for controller, corner in enumerate((0, 28, 3, 31)):
                    rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, controller)]
                    wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, controller)]
                    for fabric_id in NoCChannel:
                        # Local routes keep the independence check free of
                        # shared inter-router links and unrelated congestion.
                        pe = cores[corner]
                        nmc = pe.nmc_channel_for(fabric_id)
                        download = Message(
                            src=rdma.binding_for(fabric_id).address,
                            dst=pe.binding_for(fabric_id).address,
                            index=3000 + controller * 10,
                            data=[DimSlice(start=0, end=8 * 512 + 1)],
                            dma_command_mode=DMACommandMode.SINGLE_SIDE,
                        )
                        upload = Message(
                            src=pe.binding_for(fabric_id).address,
                            dst=wdma.binding_for(fabric_id).address,
                            index=download.index + 1,
                            data=download.data,
                            dma_command_mode=DMACommandMode.SINGLE_SIDE,
                        )
                        receive = nmc.recv_message(download, NMCShapeMode.STATIC)
                        send = nmc.send(upload)
                        for node_type, command in (
                            (NodeType.DDR_RDMA, receive),
                            (NodeType.DDR_WDMA, send),
                        ):
                            if controller == 0 and node_type is blocked_type:
                                blocked_commands.append(command)
                            else:
                                independent_commands.append(command)

                env.run(until=env.all_of(independent_commands))
                env.run()
                self.assertTrue(
                    all(command.triggered for command in independent_commands)
                )
                self.assertTrue(
                    all(not command.triggered for command in blocked_commands)
                )
                self.assertEqual(arch.dma_commands.pending_single_side_commands, 2)
                self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
                for fabric_id in NoCChannel:
                    self.assertEqual(
                        cores[0].nmc_channel_for(
                            fabric_id
                        ).outstanding_descriptor_count,
                        1,
                    )

                blocked_endpoint.internal_datapath.release(blocker)
                env.run(until=env.all_of(blocked_commands))
                env.run()
                self._assert_dma_runtime_drained(arch, cores)

    def test_download_requests_enter_pe_upload_stream_only_at_burst_boundaries(
        self,
    ) -> None:
        for mode, quantum in (
            (BurstLenMode.BURST_LEN_0, 1),
            (BurstLenMode.BURST_LEN_1, 2),
            (BurstLenMode.BURST_LEN_3, 4),
            (BurstLenMode.BURST_LEN_7, 8),
            (BurstLenMode.BURST_LEN_DEFAULT, 8),
        ):
            with self.subTest(mode=mode.name):
                env, arch, cores = self._build_executable_runtime(
                    self._all_ddr_controllers_config((DMACommandMode.SINGLE_SIDE,) * 4)
                )
                rdma = arch.dma_endpoints[(NodeType.DDR_RDMA, 0)]
                wdma = arch.dma_endpoints[(NodeType.DDR_WDMA, 0)]
                nmc = cores[0].nmc_channel_for(NoCChannel.CH0)
                upload = Message(
                    src=cores[0].binding_for(NoCChannel.CH0).address,
                    dst=wdma.binding_for(NoCChannel.CH0).address,
                    index=4500,
                    data=[DimSlice(start=0, end=64 * 512 + 1)],
                    dma_command_mode=DMACommandMode.SINGLE_SIDE,
                    burst_len_mode=mode,
                )
                download = Message(
                    src=rdma.binding_for(NoCChannel.CH0).address,
                    dst=upload.src,
                    index=4501,
                    data=[DimSlice(start=0, end=513)],
                    dma_command_mode=DMACommandMode.SINGLE_SIDE,
                )
                send = nmc.send(upload)
                receive = nmc.recv_message(download, NMCShapeMode.STATIC)
                env.run(until=env.all_of((send, receive)))
                env.run()
                injections = [
                    event
                    for event in arch.nocs[NoCChannel.CH0].tracer.events
                    if event.action is FlitAction.INJECT
                    and (
                        (
                            event.msg_id == upload.index
                            and event.traffic_type is FlitTrafficType.PAYLOAD
                        )
                        or event.traffic_type is FlitTrafficType.DMA_REQUEST
                    )
                ]
                requests = [
                    index
                    for index, event in enumerate(injections)
                    if event.traffic_type is FlitTrafficType.DMA_REQUEST
                ]
                self.assertEqual(len(requests), 1)
                payload_before_request = requests[0]
                self.assertGreater(payload_before_request, 0)
                self.assertLess(payload_before_request, upload.flit_count())
                self.assertEqual(payload_before_request % quantum, 0)
                self.assertEqual(len(injections), upload.flit_count() + 1)
                self.assertEqual(receive.value.flits, tuple(download.packetize()))
                self._assert_dma_runtime_drained(arch, cores)

    def test_dma_failure_validation_and_unconfigured_target(self) -> None:
        legacy = {"router": [], "link": [], "lsu": [], "tpu": []}
        self.assertEqual(FailSlow.model_validate(legacy).dma, [])
        valid = {
            "node_type": NodeType.DDR_WDMA,
            "instance_id": 0,
            "start_time": 0.5,
            "end_time": 10.5,
            "times": 2.5,
        }
        for change in (
            {"node_type": NodeType.PE},
            {"instance_id": 4},
            {"start_time": -1},
            {"end_time": 0.5},
            {"end_time": float("inf")},
            {"times": float("nan")},
            {"times": 0.5},
            {"fabric_id": NoCChannel.CH0},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                DMAFail.model_validate(valid | change)
        failure = DMAFail.model_validate(valid)
        self.assertEqual(
            DMAFail.model_validate_json(failure.model_dump_json()), failure
        )
        env, arch = self._build_runtime(NoCConfig())
        arch.fail_slow = FailSlow.model_validate(legacy | {"dma": [failure]})
        with self.assertRaisesRegex(ValueError, "DDR_WDMA\\[0\\] is not configured"):
            arch.run_fail_slow()
        env.run()
        self.assertEqual(env.now, 0)
        arch.preprocess_fail(times=3)
        self.assertEqual(failure.times, 3)

    def test_dma_faults_affect_both_fabrics_and_recover_for_all_command_paths(
        self,
    ) -> None:
        layouts = {
            NodeType.GM_RDMA: (DMAType.GM_RDMA, (28, 29), [PORT_GM_RDMA]),
            NodeType.GM_WDMA: (
                DMAType.GM_WDMA,
                (28, 29),
                [PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
            ),
            NodeType.DDR_RDMA: (DMAType.DDR_RDMA, (0, 28), [PORT_DDR_RDMA]),
            NodeType.DDR_WDMA: (
                DMAType.DDR_WDMA,
                (0, 28),
                [PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
            ),
        }
        for fault_type in layouts:
            for mode in DMACommandMode:
                with self.subTest(fault_type=fault_type.name, mode=mode.name):
                    opposite = {
                        NodeType.GM_RDMA: NodeType.GM_WDMA,
                        NodeType.GM_WDMA: NodeType.GM_RDMA,
                        NodeType.DDR_RDMA: NodeType.DDR_WDMA,
                        NodeType.DDR_WDMA: NodeType.DDR_RDMA,
                    }[fault_type]
                    engines = []
                    for node_type, instance_id in (
                        (fault_type, 0),
                        (opposite, 0),
                        (fault_type, 1),
                    ):
                        dma_type, routers, ports = layouts[node_type]
                        if len(ports) == 2 and mode is DMACommandMode.SINGLE_SIDE:
                            ports = [
                                PORT_GM_WDMA
                                if node_type is NodeType.GM_WDMA
                                else PORT_DDR_WDMA
                            ]
                        engines.append(
                            DMAEngineConfig(
                                dma_type=dma_type,
                                instance_id=instance_id,
                                router_id=routers[instance_id],
                                channels=len(ports),
                                local_ports=ports,
                                descriptor_issue_cycles=0.0,
                                max_outstanding_descriptors_per_channel=1,
                            )
                        )
                    env, arch, cores = self._build_executable_runtime(
                        NoCConfig(dma_engines=engines)
                    )
                    arch.cores = cores
                    arch.fail_slow = FailSlow(
                        router=[],
                        link=[],
                        lsu=[],
                        tpu=[],
                        dma=[
                            DMAFail(
                                node_type=fault_type,
                                instance_id=0,
                                start_time=0,
                                end_time=10_000,
                                times=3,
                            )
                        ],
                    )
                    arch.run_fail_slow()
                    for batch in range(2):
                        commands = []
                        for offset, endpoint in enumerate(arch.dma_endpoints.values()):
                            for fabric_id in NoCChannel:
                                address = endpoint.binding_for(fabric_id).address
                                pe = cores[address.router_id]
                                pe_address = pe.binding_for(fabric_id).address
                                is_download = endpoint.node_type in (
                                    NodeType.GM_RDMA,
                                    NodeType.DDR_RDMA,
                                )
                                message = Message(
                                    src=address if is_download else pe_address,
                                    dst=pe_address if is_download else address,
                                    index=4000 + batch * 10 + offset,
                                    data=[DimSlice(start=0, end=4097)],
                                    dma_command_mode=mode,
                                )
                                nmc = pe.nmc_channel_for(fabric_id)
                                if is_download:
                                    commands.append(
                                        nmc.recv_message(message, NMCShapeMode.STATIC)
                                    )
                                    if mode is DMACommandMode.DUAL_SIDE:
                                        commands.append(endpoint.send(message))
                                else:
                                    commands.append(nmc.send(message))
                                    if mode is DMACommandMode.DUAL_SIDE:
                                        commands.append(endpoint.recv_message(message))
                        env.run(until=env.all_of(commands))
                        streams = collect_dma_service_events(arch.dma_endpoints)
                        self.assertEqual(list(streams), sorted(arch.dma_endpoints))
                        for key, events in streams.items():
                            recent = [
                                event
                                for event in events
                                if event.message_id >= 4000 + batch * 10
                            ]
                            self.assertEqual(len(recent), 18)
                            self.assertEqual(
                                sum(event.payload_bytes for event in recent), 2 * 4097
                            )
                            factor = 3 if batch == 0 and key == (fault_type, 0) else 1
                            endpoint = arch.dma_endpoints[key]
                            self.assertIsNot(events, endpoint.service_events)
                            for event in recent:
                                self.assertEqual(
                                    (event.node_type, event.instance_id), key
                                )
                                self.assertEqual(event.delay_factor, factor)
                                self.assertAlmostEqual(
                                    event.end_time - event.start_time,
                                    512 / endpoint.service_bytes_per_aci_cycle * factor,
                                )
                            self.assertEqual(
                                {event.fabric_id for event in recent}, set(NoCChannel)
                            )
                        if batch == 0:
                            self.assertEqual(
                                arch.dma_endpoints[
                                    (fault_type, 0)
                                ].service_delay_factor,
                                3,
                            )
                        env.run()  # Recovery and delayed credit returns.
                        self.assertEqual(
                            arch.dma_endpoints[(fault_type, 0)].service_delay_factor, 1
                        )
                        self._assert_dma_runtime_drained(arch, cores)

                    # Local DMA traffic has no inter-router or Core task events:
                    # endpoint service must still contribute to the trace horizon.
                    horizon, _, _, _, core_json, link_json, streams = (
                        _collect_simulation_events(arch)
                    )
                    self.assertEqual((core_json, link_json), ([], []))
                    self.assertEqual(
                        horizon,
                        max(
                            event.end_time
                            for events in streams.values()
                            for event in events
                        ),
                    )
                    trace = process_events(horizon, 5, [], [], dma_events=streams)
                    self.assertTrue(
                        all(len(item.dmas) == 3 for item in trace.time_slices)
                    )
                    self.assertTrue(
                        all(
                            0 <= dma.ultilization <= 1
                            for item in trace.time_slices
                            for dma in item.dmas
                        )
                    )

    def test_overlapping_dma_faults_preserve_in_flight_flit_timing(self) -> None:
        env, arch, cores = self._build_executable_runtime(
            NoCConfig(
                dma_engines=[
                    DMAEngineConfig(
                        dma_type=DMAType.GM_RDMA,
                        instance_id=0,
                        router_id=28,
                        channels=1,
                        local_ports=[PORT_GM_RDMA],
                        descriptor_issue_cycles=0,
                    )
                ]
            )
        )
        endpoint = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        arch.fail_slow = FailSlow(
            router=[],
            link=[],
            lsu=[],
            tpu=[],
            dma=[
                DMAFail(
                    node_type=NodeType.GM_RDMA,
                    instance_id=0,
                    start_time=0,
                    end_time=100.75,
                    times=2,
                ),
                DMAFail(
                    node_type=NodeType.GM_RDMA,
                    instance_id=0,
                    start_time=80.25,
                    end_time=160.5,
                    times=3,
                ),
            ],
        )
        arch.run_fail_slow()
        message = Message(
            src=endpoint.binding_for(NoCChannel.CH0).address,
            dst=cores[28].binding_for(NoCChannel.CH0).address,
            index=4100,
            data=[DimSlice(start=0, end=32 * 512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        send = endpoint.send(message)
        receive = (
            cores[28]
            .nmc_channel_for(NoCChannel.CH0)
            .recv_message(message, NMCShapeMode.STATIC)
        )
        env.run(until=env.all_of((send, receive)))
        env.run()
        events = endpoint.service_events
        self.assertEqual(len(events), 32)
        self.assertEqual({event.delay_factor for event in events}, {1, 2, 3, 6})
        for event in events:
            expected = (2 if event.start_time < 100.75 else 1) * (
                3 if 80.25 <= event.start_time < 160.5 else 1
            )
            self.assertEqual(event.delay_factor, expected)
            self.assertAlmostEqual(
                event.end_time - event.start_time, 512 / 110 * expected
            )
        for boundary in (80.25, 100.75, 160.5):
            self.assertTrue(
                any(event.start_time < boundary < event.end_time for event in events)
            )
        self.assertEqual(endpoint.service_delay_factor, 1)
        self._assert_dma_runtime_drained(arch, cores)

    def test_dma_trace_utilization_uses_half_open_fractional_service_intervals(
        self,
    ) -> None:
        events = {
            (NodeType.GM_RDMA, 0): [
                DMAServiceEvent(
                    NodeType.GM_RDMA, 0, NoCChannel.CH0, 1, 512, 0.25, 1.75, 1
                ),
                DMAServiceEvent(
                    NodeType.GM_RDMA, 0, NoCChannel.CH1, 2, 1, 1.75, 3.25, 2
                ),
            ],
            (NodeType.DDR_WDMA, 0): [
                DMAServiceEvent(NodeType.DDR_WDMA, 0, NoCChannel.CH0, 3, 512, 2, 4, 1),
            ],
            (NodeType.GM_WDMA, 1): [],
        }
        trace = process_events(3, 2, [], [], dma_events=events)
        expected = (
            ((0.875, 2), (0, 0), (0, 0)),
            ((0.625, 1), (0, 0), (1, 1)),
        )
        for item, values in zip(trace.time_slices, expected, strict=True):
            self.assertEqual(
                [(dma.node_type, dma.id) for dma in item.dmas], sorted(events)
            )
            self.assertEqual(
                [(dma.ultilization, dma.op_num) for dma in item.dmas], list(values)
            )
            self.assertTrue(all(dma.fabric_id is None for dma in item.dmas))
        self.assertEqual(
            trace.model_dump(mode="json")["time_slices"][1]["dmas"][2]["node_type"],
            int(NodeType.DDR_WDMA),
        )
        self.assertEqual(process_events(3, 2, [], []).time_slices[0].dmas, [])

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
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        gm_to_pe = Message(
            src=gm_rdma.binding_for(NoCChannel.CH1).address,
            dst=cores[1].binding_for(NoCChannel.CH1).address,
            index=1001,
            data=[DimSlice(start=0, end=513)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
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

    def test_dual_side_rdma_waits_for_destination_descriptor(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                    descriptor_issue_cycles=0.0,
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        message = Message(
            src=gm_rdma.binding_for(NoCChannel.CH0).address,
            dst=cores[0].binding_for(NoCChannel.CH0).address,
            index=1002,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )

        send = gm_rdma.send(message)
        env.run(until=20.0)

        self.assertFalse(send.triggered)
        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 1)
        self.assertFalse(
            any(
                event.action is FlitAction.INJECT
                and event.msg_id == message.index
                for event in arch.nocs[NoCChannel.CH0].tracer.events
            )
        )

        receive = cores[0].nmc_channel_for(NoCChannel.CH0).recv_message(
            message,
            NMCShapeMode.DYNAMIC,
        )
        env.run(until=send)

        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 1)
        self.assertFalse(receive.triggered)
        env.run(until=receive)

        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
        self.assertTrue(
            any(
                event.action is FlitAction.INJECT
                and event.msg_id == message.index
                for event in arch.nocs[NoCChannel.CH0].tracer.events
            )
        )

    def test_single_side_download_is_pe_initiated_request_response(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        message = Message(
            src=gm_rdma.binding_for(NoCChannel.CH0).address,
            dst=cores[0].binding_for(NoCChannel.CH0).address,
            index=1003,
            data=[DimSlice(start=0, end=513)],
            dma_command_mode=DMACommandMode.SINGLE_SIDE,
        )

        with self.assertRaisesRegex(ValueError, "initiated by NMCChannel"):
            gm_rdma.send(message)
        receive = cores[0].nmc_channel_for(NoCChannel.CH0).recv_message(
            message,
            NMCShapeMode.DYNAMIC,
        )
        env.run(until=receive)

        self.assertEqual(
            receive.value.operation_latency_aci_cycles,
            GM_RDMA_REFERENCE.operation_latency_aci_cycles(7),
        )
        self.assertEqual(
            [flit.dma_header_bytes for flit in receive.value.flits],
            [message.header_bytes, 0],
        )
        injections = [
            event
            for event in arch.nocs[NoCChannel.CH0].tracer.events
            if event.action is FlitAction.INJECT and event.msg_id == message.index
        ]
        self.assertEqual(
            {event.traffic_type for event in injections},
            {FlitTrafficType.DMA_REQUEST, FlitTrafficType.PAYLOAD},
        )
        request = next(
            event
            for event in injections
            if event.traffic_type is FlitTrafficType.DMA_REQUEST
        )
        self.assertEqual((request.src_router, request.dst_router), (0, 28))
        self.assertEqual(request.dma_header_bytes, message.header_bytes)
        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        self.assertIn(
            (NoCChannel.CH0, message.index),
            arch.nocs[NoCChannel.CH0].tracer.message_fabric_timings(),
        )

    def test_single_side_upload_auto_receives_and_returns_response(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_WDMA],
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        message = Message(
            src=cores[0].binding_for(NoCChannel.CH1).address,
            dst=gm_wdma.binding_for(NoCChannel.CH1).address,
            index=1004,
            data=[DimSlice(start=0, end=513)],
            dma_command_mode=DMACommandMode.SINGLE_SIDE,
        )

        with self.assertRaisesRegex(ValueError, "initiated by NMCChannel"):
            gm_wdma.recv_message(message)
        send = cores[0].nmc_channel_for(NoCChannel.CH1).send(message)
        env.run(until=send)

        self.assertLess(
            send.value.final_local_handoff_time_aci_cycles,
            send.value.operation_completion_time_aci_cycles,
        )
        injections = [
            event
            for event in arch.nocs[NoCChannel.CH1].tracer.events
            if event.action is FlitAction.INJECT and event.msg_id == message.index
        ]
        self.assertEqual(
            {event.traffic_type for event in injections},
            {FlitTrafficType.PAYLOAD, FlitTrafficType.DMA_RESPONSE},
        )
        response = next(
            event
            for event in injections
            if event.traffic_type is FlitTrafficType.DMA_RESPONSE
        )
        self.assertEqual((response.src_router, response.dst_router), (28, 0))
        self.assertEqual(response.dma_header_bytes, message.header_bytes)
        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        self.assertEqual(
            gm_wdma.outstanding_descriptor_count(NoCChannel.CH1),
            0,
        )

    def test_protocol_state_rejects_duplicate_command_posts(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_WDMA],
                )
            ]
        )
        _, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        message = Message(
            src=cores[0].binding_for(NoCChannel.CH0).address,
            dst=gm_wdma.binding_for(NoCChannel.CH0).address,
            index=1005,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.SINGLE_SIDE,
        )

        arch.dma_commands.post_single_side_upload(message)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            arch.dma_commands.post_single_side_upload(message)

    def test_dma_command_mode_is_explicit_and_not_attachment_mode(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                ),
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                ),
            ]
        )
        _, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]

        with self.assertRaisesRegex(ValueError, "require dma_command_mode"):
            Message(
                src=cores[0].binding_for(NoCChannel.CH0).address,
                dst=gm_wdma.binding_for(NoCChannel.CH0).address,
                index=1006,
                data=[DimSlice(start=0, end=512)],
            )
        with self.assertRaisesRegex(ValueError, "single-side DMA attachment"):
            Message(
                src=cores[0].binding_for(NoCChannel.CH0).address,
                dst=gm_wdma.binding_for(NoCChannel.CH0).address,
                index=1007,
                data=[DimSlice(start=0, end=512)],
                dma_command_mode=DMACommandMode.SINGLE_SIDE,
            )
        with self.assertRaisesRegex(ValueError, "nonzero header"):
            Message(
                src=gm_rdma.binding_for(NoCChannel.CH0).address,
                dst=cores[0].binding_for(NoCChannel.CH0).address,
                index=1008,
                data=[DimSlice(start=0, end=512)],
                dma_command_mode=DMACommandMode.SINGLE_SIDE,
                header_bytes=0,
            )

        dual_rdma = Message(
            src=gm_rdma.binding_for(NoCChannel.CH0).address,
            dst=cores[0].binding_for(NoCChannel.CH0).address,
            index=1009,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        self.assertIs(
            dual_rdma.src.attachment_mode,
            DMAAttachmentMode.SINGLE_SIDE,
        )
        self.assertEqual(dual_rdma.packetize()[0].dma_header_bytes, 0)

    def test_single_side_task_ids_are_isolated_by_fabric(self) -> None:
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_WDMA],
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_wdma = arch.dma_endpoints[(NodeType.GM_WDMA, 0)]
        messages = tuple(
            Message(
                src=cores[28].binding_for(fabric_id).address,
                dst=gm_wdma.binding_for(fabric_id).address,
                index=1010,
                data=[DimSlice(start=0, end=512)],
                dma_command_mode=DMACommandMode.SINGLE_SIDE,
            )
            for fabric_id in NoCChannel
        )

        sends = tuple(
            cores[28].nmc_channel_for(message.src.fabric_id).send(message)
            for message in messages
        )
        env.run(until=env.all_of(sends))

        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        for fabric_id in NoCChannel:
            response_injections = [
                event
                for event in arch.nocs[fabric_id].tracer.events
                if event.action is FlitAction.INJECT
                and event.msg_id == 1010
                and event.traffic_type is FlitTrafficType.DMA_RESPONSE
            ]
            self.assertEqual(len(response_injections), 1)

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
                dma_command_mode=DMACommandMode.DUAL_SIDE,
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
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
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
            dma_command_mode=DMACommandMode.DUAL_SIDE,
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
                dma_command_mode=DMACommandMode.DUAL_SIDE,
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

    def test_rdma_latency_and_bulk_rate_match_reference(self) -> None:
        for pe_id, hops in ((28, 0), (0, 7)):
            with self.subTest(pe_id=pe_id, hops=hops):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.GM_RDMA,
                            instance_id=0,
                            router_id=28,
                            channels=1,
                            local_ports=[PORT_GM_RDMA],
                            descriptor_issue_cycles=0.0,
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
                message = Message(
                    src=gm_rdma.binding_for(NoCChannel.CH0).address,
                    dst=cores[pe_id].binding_for(NoCChannel.CH0).address,
                    index=1800 + pe_id,
                    data=[DimSlice(start=0, end=512)],
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
                )

                receive = cores[pe_id].nmc_channel_for(
                    NoCChannel.CH0
                ).recv_message(message, NMCShapeMode.DYNAMIC)
                send = gm_rdma.send(message)
                env.run(until=env.all_of((receive, send)))

                self.assertEqual(
                    receive.value.operation_latency_aci_cycles,
                    GM_RDMA_REFERENCE.operation_latency_aci_cycles(hops),
                )
                self.assertLess(
                    send.value.operation_completion_time_aci_cycles,
                    receive.value.operation_completion_time_aci_cycles,
                )

        bulk_bytes = 256 * 1024
        bulk_targets = (
            (28, GM_RDMA_REFERENCE.zero_hop_bulk_gbps),
            (0, GM_RDMA_REFERENCE.seven_hop_bulk_gbps),
        )
        for pe_id, target_gbps in bulk_targets:
            with self.subTest(pe_id=pe_id, target_gbps=target_gbps):
                noc_config = NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.GM_RDMA,
                            instance_id=0,
                            router_id=28,
                            channels=1,
                            local_ports=[PORT_GM_RDMA],
                            descriptor_issue_cycles=0.0,
                        )
                    ]
                )
                env, arch, cores = self._build_executable_runtime(noc_config)
                gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
                message = Message(
                    src=gm_rdma.binding_for(NoCChannel.CH0).address,
                    dst=cores[pe_id].binding_for(NoCChannel.CH0).address,
                    index=1900 + pe_id,
                    data=[DimSlice(start=0, end=bulk_bytes)],
                    dma_command_mode=DMACommandMode.DUAL_SIDE,
                )

                receive = cores[pe_id].nmc_channel_for(
                    NoCChannel.CH0
                ).recv_message(message, NMCShapeMode.DYNAMIC)
                send = gm_rdma.send(message)
                env.run(until=env.all_of((receive, send)))
                effective_rate = (
                    bulk_bytes / receive.value.operation_latency_aci_cycles
                )
                effective_gbps = (
                    GM_RDMA_REFERENCE.bytes_per_aci_cycle_to_gbps(
                        effective_rate
                    )
                )

                self.assertEqual(
                    gm_rdma.service_bytes_per_aci_cycle,
                    GM_RDMA_REFERENCE.service_bytes_per_aci_cycle,
                )
                self.assertLessEqual(
                    effective_rate,
                    GM_RDMA_REFERENCE.service_bytes_per_aci_cycle,
                )
                self.assertAlmostEqual(
                    effective_gbps,
                    target_gbps,
                    delta=target_gbps * 0.1,
                )

    def test_explicit_rdma_service_rate_overrides_calibrated_default(self) -> None:
        config = DMAEngineConfig(
            dma_type=DMAType.GM_RDMA,
            instance_id=0,
            router_id=28,
            channels=1,
            local_ports=[PORT_GM_RDMA],
            port_bw=64.0,
        )
        _, arch = self._build_runtime(NoCConfig(dma_engines=[config]))
        endpoint = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]

        self.assertEqual(endpoint.service_bytes_per_aci_cycle, 64.0)

    def test_large_n_way_outcast_fairly_shares_one_rdma_engine(self) -> None:
        message_bytes = 128 * 1024
        noc_config = NoCConfig(
            dma_engines=[
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[PORT_GM_RDMA],
                    descriptor_issue_cycles=0.0,
                )
            ]
        )
        env, arch, cores = self._build_executable_runtime(noc_config)
        gm_rdma = arch.dma_endpoints[(NodeType.GM_RDMA, 0)]
        destination_ids = (28, 24, 20, 16, 12, 8, 4, 0)
        messages = tuple(
            Message(
                src=gm_rdma.binding_for(NoCChannel.CH0).address,
                dst=cores[destination_id]
                .binding_for(NoCChannel.CH0)
                .address,
                index=2000 + destination_id,
                data=[DimSlice(start=0, end=message_bytes)],
                dma_command_mode=DMACommandMode.DUAL_SIDE,
            )
            for destination_id in destination_ids
        )

        receive_processes = tuple(
            cores[destination_id]
            .nmc_channel_for(NoCChannel.CH0)
            .recv_message(message, NMCShapeMode.DYNAMIC)
            for destination_id, message in zip(
                destination_ids,
                messages,
                strict=True,
            )
        )
        send_processes = tuple(gm_rdma.send(message) for message in messages)
        env.run(until=env.all_of((*receive_processes, *send_processes)))

        completion_times = tuple(
            process.value.operation_completion_time_aci_cycles
            for process in receive_processes
        )
        final_completion = max(completion_times)
        aggregate_rate = message_bytes * len(messages) / final_completion
        aggregate_gbps = GM_RDMA_REFERENCE.bytes_per_aci_cycle_to_gbps(
            aggregate_rate
        )
        minimum_gbps, maximum_gbps = GM_RDMA_REFERENCE.outcast_gbps_range

        self.assertGreaterEqual(aggregate_gbps, minimum_gbps)
        self.assertLessEqual(aggregate_gbps, maximum_gbps)
        self.assertLessEqual(
            max(completion_times) - min(completion_times),
            final_completion * 0.1,
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
                dma_command_mode=DMACommandMode.DUAL_SIDE,
            )
            for message_index in range(5)
        )
        ch1_message = Message(
            src=source.binding_for(NoCChannel.CH1).address,
            dst=gm_wdma.binding_for(NoCChannel.CH1).address,
            index=1400,
            data=[DimSlice(start=0, end=512)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
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
                dma_command_mode=DMACommandMode.DUAL_SIDE,
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
