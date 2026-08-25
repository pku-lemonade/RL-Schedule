import unittest

import simpy
from pydantic import ValidationError

from simulator_detailed.configs.schemas.arch_config import (
    DMAEngineConfig,
    DMAType,
    FlitConfig,
    LinkConfig,
    NMCConfig,
    NoCConfig,
)
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.noc import FlitAction, Link, NoC, NoCTracer
from simulator_detailed.utils.definitions import (
    PORT_DDR_RDMA,
    PORT_DDR_RDMA_LOC,
    PORT_DDR_WDMA,
    PORT_DDR_WDMA_CH0,
    PORT_DDR_WDMA_CH1,
    PORT_DDR_WDMA_LOC,
    PORT_GM_RDMA,
    PORT_GM_RDMA_LOC,
    PORT_GM_WDMA,
    PORT_GM_WDMA_CH0,
    PORT_GM_WDMA_CH1,
    PORT_GM_WDMA_LOC,
    PORT_PE,
    DimSlice,
    EndpointAddress,
    Flit,
    FlitType,
    Message,
    NoCChannel,
    NodeType,
    DMAAttachmentMode,
    TransType,
    compute_flit_count,
)


class MeshHarness:
    def __init__(self):
        self.env = simpy.Environment()
        self.config = NoCConfig()
        self.tracer = NoCTracer()
        self.noc = NoC(self.env, self.config, self.tracer).build_connection_mesh()
        self.endpoints = {}

    def attach(self, router_id):
        c2r = Link(
            self.env,
            self.config.c2r_link,
            self.config.router.flit.physical_flit_bytes,
            self.tracer,
            f"PE{router_id}->R{router_id}",
        )
        r2c = Link(
            self.env,
            self.config.c2r_link,
            self.config.router.flit.physical_flit_bytes,
            self.tracer,
            f"R{router_id}->PE{router_id}",
        )
        self.noc.routers[router_id].bind_link(PORT_PE, c2r, r2c)
        self.endpoints[router_id] = (c2r, r2c)

    @staticmethod
    def flit(flit_type, msg_id, src, dst, payload=512):
        return Flit(
            flit_type=flit_type,
            payload_bytes=payload,
            msg_id=msg_id,
            src_router=src,
            dst_router=dst,
        )

    def transfer(self, src, dst, flits, ack_delays=None):
        for router_id in (src, dst):
            if router_id not in self.endpoints:
                self.attach(router_id)
        c2r = self.endpoints[src][0]
        r2c = self.endpoints[dst][1]
        arrivals = []
        ack_delays = ack_delays or [0.0] * len(flits)

        def sender():
            for flit in flits:
                yield c2r.send_flit(flit)

        def receiver():
            for delay in ack_delays:
                flit = yield r2c.recv_flit()
                arrivals.append((flit, self.env.now))
                if delay:
                    yield self.env.timeout(delay)
                r2c.ack_credit()

        self.env.process(sender())
        receive_process = self.env.process(receiver())
        self.env.run(until=receive_process)
        return arrivals


class Phase2NoCTests(unittest.TestCase):
    def test_config_and_mesh_shape(self):
        harness = MeshHarness()
        flit_config = harness.config.router.flit
        link_config = harness.config.link
        nmc_config = NMCConfig()

        self.assertEqual((harness.noc.x, harness.noc.y), (4, 8))
        self.assertEqual(len(harness.noc.routers), 32)
        self.assertEqual(len(harness.noc.r2r_links), 104)
        self.assertEqual(harness.config.clock_mhz, 1125.0)
        self.assertEqual(harness.config.router.vc, 1)
        self.assertEqual(flit_config.physical_flit_bytes, 512)
        self.assertEqual(flit_config.payload_capacity_bytes, 512)
        self.assertEqual(link_config.phit_bytes, 128)
        self.assertEqual(link_config.serialization_cycles(512), 4.0)
        self.assertAlmostEqual(link_config.launch_interval_cycles, 512.0 / 120.0)
        self.assertEqual(link_config.wire_delay_cycles, 0.5)
        self.assertEqual(link_config.input_buffer_depth_flits, 1)
        self.assertEqual(link_config.flow_control_window_flits, 1)
        self.assertIsNot(nmc_config.ch0, nmc_config.ch1)
        for channel in NoCChannel:
            channel_config = nmc_config.channel_config(channel)
            self.assertEqual(channel_config.tx_bytes_per_cycle, 120.0)
            self.assertEqual(channel_config.rx_bytes_per_cycle, 120.0)
            self.assertEqual(channel_config.descriptor_issue_cycles, 57.0)
            self.assertEqual(channel_config.max_outstanding_descriptors, 24)

    def test_hardware_enum_values_and_invalid_channel(self):
        self.assertEqual(
            {member.name: member.value for member in TransType},
            {
                "SINGLECAST": 0,
                "FIXPATH": 1,
                "MULTICAST": 2,
                "BROADCAST": 3,
            },
        )
        self.assertEqual(
            {member.name: member.value for member in NoCChannel},
            {"CH0": 0, "CH1": 1},
        )
        with self.assertRaises(ValueError):
            NoCChannel(2)

    def test_ambiguous_legacy_transport_config_is_rejected(self):
        with self.assertRaises(ValidationError):
            FlitConfig.model_validate({"flit_size": 512})
        with self.assertRaises(ValidationError):
            FlitConfig(physical_flit_bytes=256, payload_capacity_bytes=512)
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"phit_width": 128})
        with self.assertRaises(ValidationError):
            NMCConfig.model_validate({"channels": 2, "sram_port_bw": 106.0})

    def test_single_flit_properties(self):
        single = MeshHarness.flit(FlitType.SINGLE, 1, 0, 1)
        head = MeshHarness.flit(FlitType.HEAD, 2, 0, 1)
        body = MeshHarness.flit(FlitType.BODY, 2, 0, 1)
        tail = MeshHarness.flit(FlitType.TAIL, 2, 0, 1)
        self.assertTrue(single.is_head)
        self.assertTrue(single.is_tail)
        self.assertTrue(head.is_head)
        self.assertFalse(head.is_tail)
        self.assertFalse(body.is_head)
        self.assertFalse(body.is_tail)
        self.assertFalse(tail.is_head)
        self.assertTrue(tail.is_tail)

    def test_flit_count_uses_measured_logical_payload_capacity(self):
        expected_counts = {
            0: 1,
            1: 1,
            512: 1,
            513: 2,
            1024: 2,
            1025: 3,
            2048: 4,
        }
        for payload_bytes, expected in expected_counts.items():
            with self.subTest(payload_bytes=payload_bytes):
                self.assertEqual(compute_flit_count(payload_bytes), expected)

        message = Message(
            src=EndpointAddress(
                node_type=NodeType.PE,
                node_id=0,
                fabric_id=NoCChannel.CH0,
                router_id=0,
                local_port=PORT_PE,
            ),
            dst=EndpointAddress(
                node_type=NodeType.PE,
                node_id=1,
                fabric_id=NoCChannel.CH0,
                router_id=1,
                local_port=PORT_PE,
            ),
            index=1,
            data=[DimSlice(start=0, end=512)],
            header_bytes=64,
        )
        self.assertEqual(message.payload_bytes(), 512)
        self.assertEqual(message.flit_count(), 1)

        with self.assertRaises(ValueError):
            compute_flit_count(-1)
        with self.assertRaises(ValueError):
            compute_flit_count(1, payload_capacity_bytes=0)

    def test_message_packetize_uses_config_and_preserves_metadata(self) -> None:
        pe_registry = EndpointRegistry(NoCConfig())
        boundary_cases = {
            0: ([FlitType.SINGLE], [0]),
            1: ([FlitType.SINGLE], [1]),
            512: ([FlitType.SINGLE], [512]),
            513: ([FlitType.HEAD, FlitType.TAIL], [512, 1]),
            1024: ([FlitType.HEAD, FlitType.TAIL], [512, 512]),
            1025: ([FlitType.HEAD, FlitType.BODY, FlitType.TAIL], [512, 512, 1]),
            2048: (
                [FlitType.HEAD, FlitType.BODY, FlitType.BODY, FlitType.TAIL],
                [512, 512, 512, 512],
            ),
        }

        for payload_bytes, (expected_types, expected_payloads) in boundary_cases.items():
            with self.subTest(payload_bytes=payload_bytes):
                message = Message(
                    src=pe_registry.resolve(
                        NodeType.PE, 28, fabric_id=NoCChannel.CH0
                    ),
                    dst=pe_registry.resolve(
                        NodeType.PE, 31, fabric_id=NoCChannel.CH0
                    ),
                    index=100 + payload_bytes,
                    data=[DimSlice(start=0, end=payload_bytes)],
                    trans_type=TransType.SINGLECAST,
                )

                flits = message.packetize(FlitConfig(payload_capacity_bytes=512))

                self.assertEqual([flit.flit_type for flit in flits], expected_types)
                self.assertEqual([flit.payload_bytes for flit in flits], expected_payloads)
                self.assertEqual(sum(flit.payload_bytes for flit in flits), payload_bytes)
                for flit in flits:
                    self.assertEqual(flit.msg_id, message.index)
                    self.assertEqual(flit.src_router, 28)
                    self.assertEqual(flit.dst_router, 31)
                    self.assertEqual(flit.src_local_port, message.src.local_port)
                    self.assertEqual(flit.dst_local_port, message.dst.local_port)
                    self.assertFalse(flit.is_broadcast)
                    self.assertEqual(flit.broadcast_dst_mask, 0)
                    self.assertEqual(flit.reduce_op, -1)
                    self.assertEqual(flit.sync_mode, 0)

        dma_registry = EndpointRegistry(
            NoCConfig(
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
                        instance_id=3,
                        router_id=31,
                        channels=2,
                        local_ports=[PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                    ),
                ]
            )
        )
        configured_message = Message(
            src=dma_registry.resolve(
                NodeType.GM_RDMA,
                0,
                fabric_id=NoCChannel.CH1,
                attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
            ),
            dst=dma_registry.resolve(
                NodeType.GM_WDMA,
                3,
                fabric_id=NoCChannel.CH1,
                attachment_mode=DMAAttachmentMode.DUAL_SIDE,
            ),
            index=7,
            data=[DimSlice(start=0, end=513)],
        )
        configured_flits = configured_message.packetize(
            FlitConfig(payload_capacity_bytes=256)
        )
        self.assertEqual(
            [flit.flit_type for flit in configured_flits],
            [FlitType.HEAD, FlitType.BODY, FlitType.TAIL],
        )
        self.assertEqual(
            [flit.payload_bytes for flit in configured_flits],
            [256, 256, 1],
        )
        for flit in configured_flits:
            self.assertEqual(flit.src_router, 28)
            self.assertEqual(flit.src_local_port, PORT_GM_RDMA)
            self.assertEqual(flit.dst_router, 31)
            self.assertEqual(flit.dst_local_port, PORT_GM_WDMA_CH1)

        with self.assertRaisesRegex(ValueError, "GM_WDMA cannot inject"):
            Message(
                src=dma_registry.resolve(
                    NodeType.GM_WDMA,
                    3,
                    fabric_id=NoCChannel.CH1,
                    attachment_mode=DMAAttachmentMode.DUAL_SIDE,
                ),
                dst=pe_registry.resolve(
                    NodeType.PE, 0, fabric_id=NoCChannel.CH1
                ),
                index=8,
                data=[DimSlice(start=0, end=1)],
            )
        with self.assertRaisesRegex(ValueError, "GM_RDMA cannot consume"):
            Message(
                src=pe_registry.resolve(
                    NodeType.PE, 0, fabric_id=NoCChannel.CH1
                ),
                dst=dma_registry.resolve(
                    NodeType.GM_RDMA,
                    0,
                    fabric_id=NoCChannel.CH1,
                    attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
                ),
                index=9,
                data=[DimSlice(start=0, end=1)],
            )

    def test_unsupported_transfer_types_stop_at_packetization_boundary(self):
        registry = EndpointRegistry(NoCConfig())
        for trans_type in (
            TransType.FIXPATH,
            TransType.MULTICAST,
            TransType.BROADCAST,
        ):
            with self.subTest(trans_type=trans_type):
                message = Message(
                    src=registry.resolve(
                        NodeType.PE, 0, fabric_id=NoCChannel.CH0
                    ),
                    dst=registry.resolve(
                        NodeType.PE, 1, fabric_id=NoCChannel.CH0
                    ),
                    index=trans_type.value,
                    data=[DimSlice(start=0, end=512)],
                    trans_type=trans_type,
                )
                self.assertEqual(message.trans_type, trans_type)
                with self.assertRaisesRegex(
                    NotImplementedError,
                    f"does not implement {trans_type.name}",
                ):
                    message.packetize(FlitConfig())

    def test_endpoint_registry_requires_explicit_fabric_and_path(self) -> None:
        registry = EndpointRegistry(
            NoCConfig(
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
        )

        pe_ch0 = registry.resolve(
            NodeType.PE, 17, fabric_id=NoCChannel.CH0
        )
        pe_ch1 = registry.resolve(
            NodeType.PE, 17, fabric_id=NoCChannel.CH1
        )
        self.assertEqual(
            (pe_ch0.fabric_id, pe_ch0.router_id, pe_ch0.local_port),
            (NoCChannel.CH0, 17, PORT_PE),
        )
        self.assertEqual(
            (pe_ch1.fabric_id, pe_ch1.router_id, pe_ch1.local_port),
            (NoCChannel.CH1, 17, PORT_PE),
        )
        self.assertNotEqual(pe_ch0, pe_ch1)

        dual_ch0 = registry.resolve(
            NodeType.GM_WDMA,
            0,
            fabric_id=NoCChannel.CH0,
            attachment_mode=DMAAttachmentMode.DUAL_SIDE,
        )
        dual_ch1 = registry.resolve(
            NodeType.GM_WDMA,
            0,
            fabric_id=NoCChannel.CH1,
            attachment_mode=DMAAttachmentMode.DUAL_SIDE,
        )
        self.assertEqual(dual_ch0.local_port, PORT_GM_WDMA_CH0)
        self.assertEqual(dual_ch1.local_port, PORT_GM_WDMA_CH1)

        with self.assertRaisesRegex(ValueError, "requires an attachment mode"):
            registry.resolve(
                NodeType.GM_WDMA, 0, fabric_id=NoCChannel.CH0
            )
        with self.assertRaisesRegex(ValueError, "no SINGLE_SIDE attachment"):
            registry.resolve(
                NodeType.GM_WDMA,
                0,
                fabric_id=NoCChannel.CH0,
                attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
            )
        with self.assertRaisesRegex(ValueError, "does not use a DMA attachment mode"):
            registry.resolve(
                NodeType.PE,
                0,
                fabric_id=NoCChannel.CH0,
                attachment_mode=DMAAttachmentMode.DUAL_SIDE,
            )
        with self.assertRaisesRegex(KeyError, "is not configured"):
            registry.resolve(
                NodeType.DDR_RDMA,
                0,
                fabric_id=NoCChannel.CH0,
                attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
            )

        with self.assertRaisesRegex(ValueError, "same NoC fabric"):
            Message(
                src=registry.resolve(
                    NodeType.PE, 0, fabric_id=NoCChannel.CH0
                ),
                dst=registry.resolve(
                    NodeType.PE, 1, fabric_id=NoCChannel.CH1
                ),
                index=10,
                data=[DimSlice(start=0, end=1)],
            )

    def test_endpoint_registry_maps_hardware_attachment_modes(self) -> None:
        mode_cases = (
            (
                DMAType.GM_RDMA,
                NodeType.GM_RDMA,
                28,
                [PORT_GM_RDMA],
                DMAAttachmentMode.SINGLE_SIDE,
                PORT_GM_RDMA,
                PORT_GM_RDMA,
            ),
            (
                DMAType.GM_RDMA,
                NodeType.GM_RDMA,
                28,
                [PORT_GM_RDMA_LOC],
                DMAAttachmentMode.AIU_LOCAL,
                PORT_GM_RDMA_LOC,
                PORT_GM_RDMA_LOC,
            ),
            (
                DMAType.GM_WDMA,
                NodeType.GM_WDMA,
                28,
                [PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1],
                DMAAttachmentMode.DUAL_SIDE,
                PORT_GM_WDMA_CH0,
                PORT_GM_WDMA_CH1,
            ),
            (
                DMAType.GM_WDMA,
                NodeType.GM_WDMA,
                28,
                [PORT_GM_WDMA],
                DMAAttachmentMode.SINGLE_SIDE,
                PORT_GM_WDMA,
                PORT_GM_WDMA,
            ),
            (
                DMAType.GM_WDMA,
                NodeType.GM_WDMA,
                28,
                [PORT_GM_WDMA_LOC],
                DMAAttachmentMode.AIU_LOCAL,
                PORT_GM_WDMA_LOC,
                PORT_GM_WDMA_LOC,
            ),
            (
                DMAType.DDR_RDMA,
                NodeType.DDR_RDMA,
                0,
                [PORT_DDR_RDMA],
                DMAAttachmentMode.SINGLE_SIDE,
                PORT_DDR_RDMA,
                PORT_DDR_RDMA,
            ),
            (
                DMAType.DDR_RDMA,
                NodeType.DDR_RDMA,
                0,
                [PORT_DDR_RDMA_LOC],
                DMAAttachmentMode.AIU_LOCAL,
                PORT_DDR_RDMA_LOC,
                PORT_DDR_RDMA_LOC,
            ),
            (
                DMAType.DDR_WDMA,
                NodeType.DDR_WDMA,
                0,
                [PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1],
                DMAAttachmentMode.DUAL_SIDE,
                PORT_DDR_WDMA_CH0,
                PORT_DDR_WDMA_CH1,
            ),
            (
                DMAType.DDR_WDMA,
                NodeType.DDR_WDMA,
                0,
                [PORT_DDR_WDMA],
                DMAAttachmentMode.SINGLE_SIDE,
                PORT_DDR_WDMA,
                PORT_DDR_WDMA,
            ),
            (
                DMAType.DDR_WDMA,
                NodeType.DDR_WDMA,
                0,
                [PORT_DDR_WDMA_LOC],
                DMAAttachmentMode.AIU_LOCAL,
                PORT_DDR_WDMA_LOC,
                PORT_DDR_WDMA_LOC,
            ),
        )

        for (
            dma_type,
            node_type,
            router_id,
            local_ports,
            attachment_mode,
            ch0_port,
            ch1_port,
        ) in mode_cases:
            with self.subTest(node_type=node_type, attachment_mode=attachment_mode):
                registry = EndpointRegistry(
                    NoCConfig(
                        dma_engines=[
                            DMAEngineConfig(
                                dma_type=dma_type,
                                instance_id=0,
                                router_id=router_id,
                                channels=len(local_ports),
                                local_ports=local_ports,
                            )
                        ]
                    )
                )
                ch0_address = registry.resolve(
                    node_type,
                    0,
                    fabric_id=NoCChannel.CH0,
                    attachment_mode=attachment_mode,
                )
                ch1_address = registry.resolve(
                    node_type,
                    0,
                    fabric_id=NoCChannel.CH1,
                    attachment_mode=attachment_mode,
                )
                self.assertEqual(ch0_address.local_port, ch0_port)
                self.assertEqual(ch1_address.local_port, ch1_port)
                self.assertEqual(ch0_address.attachment_mode, attachment_mode)
                self.assertEqual(ch1_address.attachment_mode, attachment_mode)

    def test_aiu_local_address_is_representable_but_not_executable(self) -> None:
        registry = EndpointRegistry(
            NoCConfig(
                dma_engines=[
                    DMAEngineConfig(
                        dma_type=DMAType.GM_RDMA,
                        instance_id=0,
                        router_id=28,
                        channels=1,
                        local_ports=[PORT_GM_RDMA_LOC],
                    )
                ]
            )
        )
        message = Message(
            src=registry.resolve(
                NodeType.GM_RDMA,
                0,
                fabric_id=NoCChannel.CH0,
                attachment_mode=DMAAttachmentMode.AIU_LOCAL,
            ),
            dst=registry.resolve(
                NodeType.PE, 0, fabric_id=NoCChannel.CH0
            ),
            index=11,
            data=[DimSlice(start=0, end=512)],
        )
        with self.assertRaisesRegex(NotImplementedError, "AIU-local"):
            message.packetize(FlitConfig())

    def test_endpoint_registry_rejects_invalid_mappings(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a 4x8 NoC"):
            EndpointRegistry(NoCConfig(x=8, y=4))
        with self.assertRaisesRegex(ValueError, "must attach to router 28"):
            EndpointAddress(
                node_type=NodeType.GM_RDMA,
                node_id=0,
                fabric_id=NoCChannel.CH0,
                router_id=29,
                local_port=PORT_GM_RDMA,
            )
        with self.assertRaisesRegex(ValueError, "cannot use local port 7"):
            EndpointAddress(
                node_type=NodeType.GM_RDMA,
                node_id=0,
                fabric_id=NoCChannel.CH0,
                router_id=28,
                local_port=7,
            )
        with self.assertRaisesRegex(ValueError, "cannot use local port 11 on CH0"):
            EndpointAddress(
                node_type=NodeType.GM_WDMA,
                node_id=0,
                fabric_id=NoCChannel.CH0,
                router_id=28,
                local_port=PORT_GM_WDMA_CH1,
            )

        invalid_configs = (
            (
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=29,
                    channels=1,
                    local_ports=[14],
                ),
                "must attach to router 28",
            ),
            (
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[14],
                ),
                "2 channels but 1 local ports",
            ),
            (
                DMAEngineConfig(
                    dma_type=DMAType.GM_RDMA,
                    instance_id=0,
                    router_id=28,
                    channels=1,
                    local_ports=[7],
                ),
                "invalid local ports",
            ),
            (
                DMAEngineConfig(
                    dma_type=DMAType.GM_WDMA,
                    instance_id=0,
                    router_id=28,
                    channels=2,
                    local_ports=[PORT_GM_WDMA_CH0, 15],
                ),
                "unsupported local-port layout",
            ),
        )
        for dma_config, expected_error in invalid_configs:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    EndpointRegistry(NoCConfig(dma_engines=[dma_config]))

        with self.assertRaisesRegex(ValueError, "is configured twice"):
            EndpointRegistry(
                NoCConfig(
                    dma_engines=[
                        DMAEngineConfig(
                            dma_type=DMAType.GM_RDMA,
                            instance_id=0,
                            router_id=28,
                            channels=1,
                            local_ports=[14],
                        ),
                        DMAEngineConfig(
                            dma_type=DMAType.GM_RDMA,
                            instance_id=0,
                            router_id=28,
                            channels=1,
                            local_ports=[14],
                        ),
                    ]
                )
            )

    def test_direct_link_latency_and_steady_gap(self):
        env = simpy.Environment()
        tracer = NoCTracer()
        link = Link(env, LinkConfig(), 512, tracer, "probe")
        flits = [
            self._standalone_flit(FlitType.HEAD, 10),
            self._standalone_flit(FlitType.BODY, 10),
            self._standalone_flit(FlitType.TAIL, 10),
        ]
        arrivals = []

        def sender():
            for flit in flits:
                yield link.send_flit(flit)

        def receiver():
            for _ in flits:
                yield link.recv_flit()
                arrivals.append(env.now)
                link.ack_credit()

        env.process(sender())
        done = env.process(receiver())
        env.run(until=done)
        self.assertAlmostEqual(arrivals[0], 4.5)
        self.assertAlmostEqual(arrivals[1] - arrivals[0], 4.5)
        self.assertAlmostEqual(arrivals[2] - arrivals[1], 4.5)

    def test_single_flit_latency_for_one_to_ten_hops(self):
        for hops in range(1, 11):
            x = min(3, hops)
            y = hops - x
            dst = y * 4 + x
            harness = MeshHarness()
            flit = harness.flit(FlitType.SINGLE, hops, 0, dst)
            arrivals = harness.transfer(0, dst, [flit])
            self.assertAlmostEqual(
                arrivals[0][1],
                8.5 * hops + 13.0,
                msg=f"unexpected latency for {hops} hops",
            )
            self.assertFalse(
                any(e.action == FlitAction.STALL_SA for e in harness.tracer.events)
            )

    def test_x_y_symmetry(self):
        times = []
        for dst in (1, 4):
            harness = MeshHarness()
            flit = harness.flit(FlitType.SINGLE, dst, 0, dst)
            times.append(harness.transfer(0, dst, [flit])[0][1])
        self.assertEqual(times, [21.5, 21.5])

    def test_three_flit_wormhole_pipeline(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.HEAD, 20, 0, 3, 512),
            harness.flit(FlitType.BODY, 20, 0, 3, 512),
            harness.flit(FlitType.TAIL, 20, 0, 3, 476),
        ]
        arrivals = harness.transfer(0, 3, flits)
        times = [time for _, time in arrivals]
        for actual, expected in zip(times, (38.5, 43.0, 47.5)):
            self.assertAlmostEqual(actual, expected)
        self.assertFalse(harness.noc.routers[0].reservation)
        self.assertFalse(harness.noc.routers[3].reservation)

    def test_receiver_backpressure_logs_credit_stall(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.HEAD, 30, 0, 1),
            harness.flit(FlitType.BODY, 30, 0, 1),
            harness.flit(FlitType.TAIL, 30, 0, 1),
        ]
        arrivals = harness.transfer(0, 1, flits, ack_delays=[50, 0, 0])
        self.assertEqual(len(arrivals), 3)
        self.assertGreater(arrivals[1][1] - arrivals[0][1], 50)
        self.assertTrue(
            any(e.action == FlitAction.STALL_CREDIT for e in harness.tracer.events)
        )

    def test_output_contention_logs_sa_stall(self):
        harness = MeshHarness()
        for router_id in (0, 4, 3):
            harness.attach(router_id)
        flow_a = [
            harness.flit(FlitType.HEAD, 40, 0, 3),
            harness.flit(FlitType.BODY, 40, 0, 3),
            harness.flit(FlitType.TAIL, 40, 0, 3),
        ]
        flow_b = [harness.flit(FlitType.SINGLE, 41, 4, 3)]
        received = []

        def sender(src, flits):
            for flit in flits:
                yield harness.endpoints[src][0].send_flit(flit)

        def receiver():
            r2c = harness.endpoints[3][1]
            for _ in range(4):
                flit = yield r2c.recv_flit()
                received.append(flit.msg_id)
                r2c.ack_credit()

        harness.env.process(sender(0, flow_a))
        harness.env.process(sender(4, flow_b))
        done = harness.env.process(receiver())
        harness.env.run(until=done)
        self.assertCountEqual(received, [40, 40, 40, 41])
        self.assertTrue(
            any(e.action == FlitAction.STALL_SA for e in harness.tracer.events)
        )

    def test_fail_slow_scales_incident_links(self):
        harness = MeshHarness()
        harness.attach(0)
        harness.attach(1)
        harness.noc.routers[0].scale_link_delay(2.0)
        flit = harness.flit(FlitType.SINGLE, 50, 0, 1)
        arrivals = harness.transfer(0, 1, [flit])
        self.assertAlmostEqual(arrivals[0][1], 30.5)

    def test_single_packets_release_switch_allocation(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.SINGLE, 60, 0, 1),
            harness.flit(FlitType.SINGLE, 61, 0, 1),
        ]
        arrivals = harness.transfer(0, 1, flits)
        self.assertEqual([flit.msg_id for flit, _ in arrivals], [60, 61])
        for router in (harness.noc.routers[0], harness.noc.routers[1]):
            self.assertFalse(router.reservation)
            self.assertFalse(router._sa_reqs)

    @staticmethod
    def _standalone_flit(flit_type, msg_id):
        return Flit(
            flit_type=flit_type,
            payload_bytes=512,
            msg_id=msg_id,
            src_router=0,
            dst_router=1,
        )


if __name__ == "__main__":
    unittest.main()
