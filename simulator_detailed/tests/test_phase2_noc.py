import unittest
from pathlib import Path
from unittest.mock import Mock

import simpy
from pydantic import ValidationError

from simulator_detailed.architecture import Arch
from simulator_detailed.configs.schemas.arch_config import (
    DMAEngineConfig,
    DMAType,
    CoreConfig,
    FlitConfig,
    LinkConfig,
    NMCConfig,
    NoCConfig,
    RouterPipelineConfig,
)
from simulator_detailed.configs.schemas.failure_configs import LinkFail, RouterFail
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.noc import FlitAction, Link, NoC, NoCTracer
from simulator_detailed.pe_channel import PEChannelBinding
from simulator_detailed.run import DEFAULT_ARCH_PATH, arch_analyzer
from simulator_detailed.tracing import collect_noc_link_events, process_events
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
    FLIT_BYTES,
    Direction,
    DimSlice,
    EndpointAddress,
    BurstLenMode,
    Flit,
    FlitType,
    Message,
    NoCChannel,
    NoCPlane,
    NodeType,
    DMAAttachmentMode,
    TransType,
    compute_flit_count,
)


class MeshHarness:
    def __init__(self, fabric_id=NoCChannel.CH0):
        self.env = simpy.Environment()
        self.config = NoCConfig()
        self.fabric_id = fabric_id
        self.tracer = NoCTracer(fabric_id)
        self.noc = NoC(
            self.env,
            self.config,
            fabric_id,
            self.tracer,
        ).build_connection_mesh()
        self.endpoints = {}

    def attach(self, router_id):
        c2r = Link(
            self.env,
            self.config.c2r_link,
            self.fabric_id,
            self.tracer,
            f"PE{router_id}->R{router_id}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
        )
        r2c = Link(
            self.env,
            self.config.c2r_link,
            self.fabric_id,
            self.tracer,
            f"R{router_id}->PE{router_id}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
        )
        self.noc.routers[router_id].bind_link(PORT_PE, c2r, r2c)
        self.endpoints[router_id] = (c2r, r2c)

    def flit(self, flit_type, msg_id, src, dst, payload=512):
        return Flit(
            flit_type=flit_type,
            payload_bytes=payload,
            msg_id=msg_id,
            fabric_id=self.fabric_id,
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
    def test_arch_builds_two_independent_data_meshes(self):
        env = simpy.Environment()
        nocs = Arch.build_nocs(env, NoCConfig())

        self.assertEqual(set(nocs), set(NoCChannel))
        self.assertEqual(sum(len(noc.routers) for noc in nocs.values()), 64)
        self.assertEqual(sum(len(noc.r2r_links) for noc in nocs.values()), 208)
        self.assertEqual(len({id(noc.tracer) for noc in nocs.values()}), 2)

        routers = [router for noc in nocs.values() for router in noc.routers]
        links = [link for noc in nocs.values() for link in noc.r2r_links]
        self.assertEqual(len({id(router) for router in routers}), 64)
        self.assertEqual(len({id(router.reservation) for router in routers}), 64)
        self.assertEqual(len({id(link) for link in links}), 208)
        self.assertEqual(len({id(link.flit_buffer) for link in links}), 208)
        self.assertEqual(len({id(link.in_flight_credits) for link in links}), 208)
        self.assertEqual(len({id(link._out_queue) for link in links}), 208)

        arbiters = [
            arbiter
            for router in routers
            for arbiter in router.out_channels.values()
        ]
        self.assertEqual(len({id(arbiter) for arbiter in arbiters}), len(arbiters))
        for fabric_id, noc in nocs.items():
            self.assertTrue(
                all(router.fabric_id is fabric_id for router in noc.routers)
            )
            self.assertEqual(
                [link.identity.link_id for link in noc.r2r_links],
                list(range(104)),
            )

    def test_every_pe_is_bound_independently_to_both_fabrics(self):
        env = simpy.Environment()
        noc_config = NoCConfig()
        arch = object.__new__(Arch)
        arch.env = env
        arch.x_size = noc_config.x
        arch.y_size = noc_config.y
        arch.endpoint_registry = EndpointRegistry(noc_config)
        arch.nocs = Arch.build_nocs(env, noc_config)

        cores = arch.build_cores(
            env=env,
            config=CoreConfig(),
            noc_config=noc_config,
            mapper=Mock(),
        )

        self.assertEqual(len(cores), 32)
        endpoint_links = []
        for core in cores:
            self.assertEqual(set(core.channel_bindings), set(NoCChannel))
            self.assertFalse(hasattr(core, "data_in"))
            self.assertFalse(hasattr(core, "data_out"))
            self.assertFalse(hasattr(core, "router"))

            for fabric_id in NoCChannel:
                binding = core.binding_for(fabric_id)
                expected_address = arch.endpoint_registry.resolve(
                    NodeType.PE,
                    core.id,
                    fabric_id=fabric_id,
                )
                self.assertEqual(binding.address, expected_address)
                self.assertIs(binding.router, arch.nocs[fabric_id].routers[core.id])
                self.assertIs(
                    binding.router.port_in[PORT_PE],
                    binding.tx_link,
                )
                self.assertIs(
                    binding.router.port_out[PORT_PE],
                    binding.rx_link,
                )
                self.assertIs(binding.tx_link.fabric_id, fabric_id)
                self.assertIs(binding.rx_link.fabric_id, fabric_id)
                self.assertIsNot(binding.tx_link, binding.rx_link)
                endpoint_links.extend((binding.tx_link, binding.rx_link))

            ch0 = core.binding_for(NoCChannel.CH0)
            ch1 = core.binding_for(NoCChannel.CH1)
            self.assertIsNot(ch0.router, ch1.router)
            self.assertEqual(ch0.router.id, ch1.router.id)
            self.assertEqual(
                len(
                    {
                        id(ch0.tx_link),
                        id(ch0.rx_link),
                        id(ch1.tx_link),
                        id(ch1.rx_link),
                    }
                ),
                4,
            )

        self.assertEqual(len(endpoint_links), 128)
        self.assertEqual(len({id(link) for link in endpoint_links}), 128)

        core0_ch0 = cores[0].binding_for(NoCChannel.CH0)
        core0_ch1 = cores[0].binding_for(NoCChannel.CH1)
        with self.assertRaisesRegex(ValueError, "cannot bind CH1:R0"):
            PEChannelBinding(
                address=core0_ch0.address,
                tx_link=core0_ch1.tx_link,
                rx_link=core0_ch1.rx_link,
                router=core0_ch1.router,
            )
        with self.assertRaisesRegex(ValueError, "already has a CH0 binding"):
            cores[0].bind_channel(core0_ch0)

    def test_router_failure_is_isolated_to_its_fabric(self):
        env = simpy.Environment()
        nocs = Arch.build_nocs(env, NoCConfig())
        arch = object.__new__(Arch)
        arch.env = env
        arch.nocs = nocs
        failure = RouterFail(
            start_time=1,
            end_time=3,
            fabric_id=NoCChannel.CH0,
            router_id=0,
            times=2,
        )

        env.process(arch.router_fail(failure))
        env.run(until=1.5)
        self.assertEqual(nocs[NoCChannel.CH0].r2r_links[0].delay_factor, 2.0)
        self.assertEqual(nocs[NoCChannel.CH1].r2r_links[0].delay_factor, 1.0)

        env.run(until=3.5)
        self.assertEqual(nocs[NoCChannel.CH0].r2r_links[0].delay_factor, 1.0)
        self.assertEqual(nocs[NoCChannel.CH1].r2r_links[0].delay_factor, 1.0)

    def test_dual_fabric_trace_collection_has_stable_link_identity(self):
        harness = MeshHarness(NoCChannel.CH0)
        ch1_noc = NoC(
            harness.env,
            harness.config,
            NoCChannel.CH1,
            NoCTracer(NoCChannel.CH1),
        ).build_connection_mesh()
        nocs = {
            NoCChannel.CH0: harness.noc,
            NoCChannel.CH1: ch1_noc,
        }
        harness.transfer(
            0,
            1,
            [harness.flit(FlitType.SINGLE, 81, 0, 1)],
        )

        link_events, identities = collect_noc_link_events(nocs)
        qualified_ids = {
            (identity.fabric_id, identity.link_id) for identity in identities
        }
        self.assertEqual(len(link_events), 208)
        self.assertEqual(len(identities), 208)
        self.assertEqual(len(qualified_ids), 208)
        self.assertEqual(len(link_events[0]), 1)
        self.assertEqual(link_events[104], [])
        self.assertIs(link_events[0][0].fabric_id, NoCChannel.CH0)
        self.assertEqual(
            (identities[0].src_router, identities[0].dst_router),
            (0, 1),
        )

        trace = process_events(
            harness.env.now,
            1,
            [],
            link_events,
            identities,
        )
        trace_links = trace.time_slices[0].links
        self.assertEqual(len(trace_links), 208)
        self.assertEqual(
            {(item.fabric_id, item.id) for item in trace_links},
            qualified_ids,
        )

    def test_predictor_topology_matches_both_noc_fabrics(self):
        from simulator_detailed.predictor.topology import Mesh

        env = simpy.Environment()
        nocs = Arch.build_nocs(env, NoCConfig())
        mesh = Mesh(4, 8)
        expected_links = [
            (
                link.identity.fabric_id,
                link.identity.src_router,
                link.identity.dst_router,
            )
            for fabric_id in NoCChannel
            for link in nocs[fabric_id].r2r_links
        ]
        actual_links = [
            (
                mesh.link_to_fabric[link_id],
                *mesh.link_to_core_pair[link_id],
            )
            for link_id in range(mesh.link_count)
        ]
        self.assertEqual(mesh.link_count, 208)
        self.assertEqual(actual_links, expected_links)
        self.assertNotEqual(
            mesh.to_link_index[(NoCChannel.CH0, 0, 1)],
            mesh.to_link_index[(NoCChannel.CH0, 1, 0)],
        )
        self.assertNotEqual(
            mesh.to_link_index[(NoCChannel.CH0, 0, 1)],
            mesh.to_link_index[(NoCChannel.CH1, 0, 1)],
        )

    def test_optional_predictor_labels_and_embedding_use_both_fabrics(self):
        try:
            from torch_geometric.data import HeteroData

            from simulator_detailed.embedding.hw_encoder import build_hardware_graph
            from simulator_detailed.predictor.data_loader import (
                ManycoreDatasetBuilder,
                TimeWindowConfig,
            )
        except ImportError as exc:
            self.skipTest(f"optional predictor dependencies unavailable: {exc}")

        env = simpy.Environment()
        nocs = Arch.build_nocs(env, NoCConfig())
        builder = ManycoreDatasetBuilder(
            4,
            8,
            "Mesh",
            TimeWindowConfig(),
        )
        labeled = builder._apply_failure_labels(
            HeteroData(),
            {
                "link": [
                    {
                        "start_time": 0,
                        "end_time": 10,
                        "fabric_id": "CH1",
                        "router_id": 0,
                        "direction": Direction.EAST,
                    }
                ]
            },
            0,
            10,
        )
        ch0_link = builder.mesh.to_link_index[(NoCChannel.CH0, 0, 1)]
        ch1_link = builder.mesh.to_link_index[(NoCChannel.CH1, 0, 1)]
        self.assertEqual(float(labeled["link"].y[ch0_link, 0]), 0.0)
        self.assertEqual(float(labeled["link"].y[ch1_link, 0]), 1.0)
        self.assertEqual(float(labeled["link"].y.sum()), 1.0)

        node_features, edge_index = build_hardware_graph(nocs)
        self.assertEqual(tuple(node_features.shape), (272, 4))
        self.assertEqual(tuple(edge_index.shape), (2, 416))

    def test_config_and_mesh_shape(self):
        harness = MeshHarness()
        flit_config = harness.config.router.flit
        link_config = harness.config.link
        nmc_config = NMCConfig()

        self.assertEqual((harness.noc.x, harness.noc.y), (4, 8))
        self.assertEqual(len(harness.noc.routers), 32)
        self.assertEqual(len(harness.noc.r2r_links), 104)
        self.assertIs(harness.noc.fabric_id, NoCChannel.CH0)
        self.assertTrue(
            all(
                router.fabric_id is NoCChannel.CH0
                for router in harness.noc.routers
            )
        )
        self.assertTrue(
            all(link.fabric_id is NoCChannel.CH0 for link in harness.noc.r2r_links)
        )
        self.assertEqual(harness.config.aci_clock_mhz, 1125.0)
        self.assertEqual(harness.config.noc_clock_mhz, 2250.0)
        self.assertEqual(harness.config.noc_cycles_per_aci_cycle, 2.0)
        self.assertEqual(harness.config.router.vc, 1)
        self.assertEqual(flit_config.physical_flit_bytes, FLIT_BYTES)
        self.assertEqual(flit_config.payload_capacity_bytes, FLIT_BYTES)
        self.assertEqual(link_config.wire_bits_per_noc_cycle, 579)
        self.assertEqual(link_config.payload_bits_per_noc_cycle, 512)
        self.assertEqual(link_config.serialization_noc_cycles(), 8)
        self.assertEqual(
            link_config.serialization_aci_cycles(
                harness.config.noc_cycles_per_aci_cycle,
            ),
            4.0,
        )
        self.assertAlmostEqual(
            link_config.launch_interval_aci_cycles,
            512.0 / 120.0,
        )
        self.assertEqual(link_config.effective_link_stage_aci_cycles, 0.5)
        self.assertEqual(link_config.sync_credit_return_aci_cycles, 0.0)
        self.assertEqual(link_config.input_buffer_depth_flits, 1)
        self.assertEqual(link_config.effective_in_flight_window_flits, 2)
        self.assertEqual(
            link_config.required_in_flight_window_flits(
                harness.config.noc_cycles_per_aci_cycle
            ),
            2,
        )
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
        self.assertEqual(
            {member.name: member.value for member in BurstLenMode},
            {
                "BURST_LEN_DEFAULT": -1,
                "BURST_LEN_0": 0,
                "BURST_LEN_1": 1,
                "BURST_LEN_3": 3,
                "BURST_LEN_7": 7,
            },
        )
        expected_quanta = {
            BurstLenMode.BURST_LEN_0: 1,
            BurstLenMode.BURST_LEN_1: 2,
            BurstLenMode.BURST_LEN_3: 4,
            BurstLenMode.BURST_LEN_7: 8,
        }
        for mode, expected_quantum in expected_quanta.items():
            with self.subTest(mode=mode):
                self.assertEqual(mode.explicit_quantum_flits(), expected_quantum)
        with self.assertRaisesRegex(ValueError, "requires architecture resolution"):
            BurstLenMode.BURST_LEN_DEFAULT.explicit_quantum_flits()
        with self.assertRaises(ValueError):
            BurstLenMode(2)
        with self.assertRaises(ValueError):
            NoCChannel(2)

    def test_failure_targets_are_fabric_qualified(self):
        legacy_router_failure = RouterFail(
            start_time=1,
            end_time=2,
            router_id=0,
            times=2,
        )
        ch1_link_failure = LinkFail(
            start_time=1,
            end_time=2,
            fabric_id=NoCChannel.CH1,
            router_id=0,
            direction=Direction.NORTH,
            times=2,
        )

        self.assertIs(legacy_router_failure.fabric_id, NoCChannel.CH0)
        self.assertIs(ch1_link_failure.fabric_id, NoCChannel.CH1)

    def test_ambiguous_legacy_transport_config_is_rejected(self):
        with self.assertRaises(ValidationError):
            FlitConfig.model_validate({"flit_size": 512})
        with self.assertRaises(ValidationError):
            FlitConfig(physical_flit_bytes=256, payload_capacity_bytes=512)
        with self.assertRaises(ValidationError):
            FlitConfig(physical_flit_bytes=512, payload_capacity_bytes=256)
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"phit_width": 128})
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"phit_bytes": 128})
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"launch_interval_cycles": 4.0})
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"wire_delay_cycles": 0.5})
        with self.assertRaises(ValidationError):
            LinkConfig.model_validate({"flow_control_window_flits": 2})
        with self.assertRaises(ValidationError):
            LinkConfig(input_buffer_depth_flits=2)
        with self.assertRaises(ValidationError):
            RouterPipelineConfig.model_validate({"rc_cycles": 1.0})
        with self.assertRaises(ValidationError):
            NoCConfig.model_validate({"clock_mhz": 1125.0})
        with self.assertRaises(ValidationError):
            NoCConfig(aci_clock_mhz=1125.0, noc_clock_mhz=1125.0)
        with self.assertRaises(ValidationError):
            LinkConfig(
                wire_bits_per_noc_cycle=511,
                payload_bits_per_noc_cycle=512,
            )
        with self.assertRaises(ValidationError):
            NMCConfig.model_validate({"channels": 2, "sram_port_bw": 106.0})

    def test_canonical_ada2s32_config(self) -> None:
        config = arch_analyzer(DEFAULT_ARCH_PATH)

        self.assertEqual(Path(DEFAULT_ARCH_PATH).name, "ada2s32.json")
        self.assertEqual((config.core.x, config.core.y), (4, 8))
        self.assertEqual((config.noc.x, config.noc.y), (4, 8))
        self.assertEqual(config.core.spm.size, 4 * 1024 * 1024)
        self.assertEqual(config.core.weight_spm.size, 16 * 1024 * 1024)
        self.assertEqual(config.noc.aci_clock_mhz, 1125.0)
        self.assertEqual(config.noc.noc_clock_mhz, 2250.0)

        gm_dma = DMAEngineConfig(
            dma_type=DMAType.GM_RDMA,
            instance_id=0,
            router_id=28,
            local_ports=[PORT_GM_RDMA],
        )
        ddr_dma = DMAEngineConfig(
            dma_type=DMAType.DDR_RDMA,
            instance_id=0,
            router_id=0,
            local_ports=[PORT_DDR_RDMA],
        )
        self.assertEqual(gm_dma.endpoint_clock_mhz, 900.0)
        self.assertEqual(ddr_dma.endpoint_clock_mhz, 1200.0)
        self.assertEqual(gm_dma.model_dump()["endpoint_clock_mhz"], 900.0)
        self.assertEqual(ddr_dma.model_dump()["endpoint_clock_mhz"], 1200.0)
        with self.assertRaises(ValidationError):
            DMAEngineConfig.model_validate(
                {
                    "dma_type": DMAType.GM_RDMA,
                    "instance_id": 0,
                    "router_id": 28,
                    "local_ports": [PORT_GM_RDMA],
                    "clock_scale": 1.0,
                }
            )

    def test_single_flit_properties(self):
        harness = MeshHarness()
        single = harness.flit(FlitType.SINGLE, 1, 0, 1)
        head = harness.flit(FlitType.HEAD, 2, 0, 1)
        body = harness.flit(FlitType.BODY, 2, 0, 1)
        tail = harness.flit(FlitType.TAIL, 2, 0, 1)
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

    def test_message_packetize_uses_fixed_capacity_and_preserves_metadata(self) -> None:
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
                    burst_len_mode=BurstLenMode.BURST_LEN_3,
                )

                flits = message.packetize()

                self.assertEqual([flit.flit_type for flit in flits], expected_types)
                self.assertEqual([flit.payload_bytes for flit in flits], expected_payloads)
                self.assertEqual(sum(flit.payload_bytes for flit in flits), payload_bytes)
                self.assertEqual(
                    sum(flit.transfer_bytes for flit in flits),
                    len(flits) * FLIT_BYTES,
                )
                for flit in flits:
                    self.assertEqual(flit.msg_id, message.index)
                    self.assertIs(flit.fabric_id, NoCChannel.CH0)
                    self.assertEqual(flit.src_router, 28)
                    self.assertEqual(flit.dst_router, 31)
                    self.assertEqual(flit.src_local_port, message.src.local_port)
                    self.assertEqual(flit.dst_local_port, message.dst.local_port)
                    self.assertFalse(flit.is_broadcast)
                    self.assertEqual(flit.broadcast_dst_mask, 0)
                    self.assertEqual(flit.reduce_op, -1)
                    self.assertEqual(flit.sync_mode, 0)
                    self.assertIs(
                        flit.burst_len_mode,
                        BurstLenMode.BURST_LEN_3,
                    )

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
        configured_flits = configured_message.packetize()
        self.assertIs(
            configured_message.burst_len_mode,
            BurstLenMode.BURST_LEN_DEFAULT,
        )
        self.assertEqual(
            [flit.flit_type for flit in configured_flits],
            [FlitType.HEAD, FlitType.TAIL],
        )
        self.assertEqual(
            [flit.payload_bytes for flit in configured_flits],
            [512, 1],
        )
        for flit in configured_flits:
            self.assertIs(
                flit.burst_len_mode,
                BurstLenMode.BURST_LEN_DEFAULT,
            )
            self.assertIs(flit.fabric_id, NoCChannel.CH1)
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

        with self.assertRaises(ValidationError):
            Flit(
                flit_type=FlitType.SINGLE,
                payload_bytes=FLIT_BYTES + 1,
                msg_id=8,
                fabric_id=NoCChannel.CH0,
                src_router=0,
                dst_router=1,
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
                    message.packetize()

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
            message.packetize()

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
        tracer = NoCTracer(NoCChannel.CH0)
        link = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            tracer,
            "probe",
            noc_cycles_per_aci_cycle=2.0,
        )
        flits = [
            self._standalone_flit(FlitType.HEAD, 10, payload_bytes=1),
            self._standalone_flit(FlitType.BODY, 10),
            self._standalone_flit(FlitType.TAIL, 10, payload_bytes=17),
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
        expected_gap = FLIT_BYTES / 120.0
        self.assertAlmostEqual(arrivals[1] - arrivals[0], expected_gap)
        self.assertAlmostEqual(arrivals[2] - arrivals[1], expected_gap)
        self.assertAlmostEqual(FLIT_BYTES / (arrivals[2] - arrivals[1]), 120.0)

    def test_effective_window_must_cover_zero_load_link_residence(self) -> None:
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        with self.assertRaisesRegex(
            ValueError,
            "effective in-flight window must contain at least 2 flits",
        ):
            Link(
                env,
                LinkConfig(effective_in_flight_window_flits=1),
                NoCChannel.CH0,
                tracer,
                "undersized-window",
                noc_cycles_per_aci_cycle=2.0,
            )

    def test_backpressure_is_bounded_and_recovers_at_launch_interval(self) -> None:
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        config = LinkConfig()
        link = Link(
            env,
            config,
            NoCChannel.CH0,
            tracer,
            "backpressure-probe",
            noc_cycles_per_aci_cycle=2.0,
        )
        flits = [
            self._standalone_flit(FlitType.SINGLE, msg_id)
            for msg_id in range(20, 26)
        ]
        arrivals = []

        def sender():
            for flit in flits:
                yield link.send_flit(flit)

        def receiver():
            for index in range(len(flits)):
                flit = yield link.recv_flit()
                arrivals.append((flit.msg_id, env.now))
                if index == 0:
                    yield env.timeout(20.0)
                yield link.ack_credit()

        sender_process = env.process(sender())
        receiver_process = env.process(receiver())
        env.run(until=10.0)

        self.assertEqual(link.in_flight_flits, 2)
        self.assertEqual(len(link.flit_buffer.items), 1)
        self.assertLessEqual(len(link._out_queue.items), 1)
        self.assertFalse(sender_process.triggered)
        self.assertTrue(
            any(event.action is FlitAction.STALL_CREDIT for event in tracer.events)
        )

        env.run(until=receiver_process)
        env.run()

        self.assertTrue(sender_process.triggered)
        self.assertEqual([msg_id for msg_id, _ in arrivals], list(range(20, 26)))
        self.assertEqual(link.in_flight_flits, 0)
        self.assertFalse(link.flit_buffer.items)
        self.assertFalse(link._out_queue.items)

        expected_gap = config.launch_interval_aci_cycles
        recovered_arrivals = [time for _, time in arrivals[2:]]
        for previous, current in zip(
            recovered_arrivals,
            recovered_arrivals[1:],
        ):
            self.assertAlmostEqual(current - previous, expected_gap)

    def test_fail_slow_scales_link_timing_and_reciprocal_recovers(self) -> None:
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        config = LinkConfig()
        link = Link(
            env,
            config,
            NoCChannel.CH0,
            tracer,
            "fail-slow-probe",
            noc_cycles_per_aci_cycle=2.0,
        )

        def transfer(msg_ids):
            arrivals = []

            def sender():
                for msg_id in msg_ids:
                    yield link.send_flit(
                        self._standalone_flit(FlitType.SINGLE, msg_id)
                    )

            def receiver():
                for _ in msg_ids:
                    flit = yield link.recv_flit()
                    arrivals.append((flit.msg_id, env.now))
                    yield link.ack_credit()

            start_time = env.now
            env.process(sender())
            receiver_process = env.process(receiver())
            env.run(until=receiver_process)
            env.run()
            return start_time, arrivals

        link.scale_link_delay(2.0)
        slow_start, slow_arrivals = transfer(range(30, 33))
        self.assertEqual([msg_id for msg_id, _ in slow_arrivals], [30, 31, 32])
        self.assertAlmostEqual(slow_arrivals[0][1] - slow_start, 9.0)
        for (_, previous), (_, current) in zip(
            slow_arrivals,
            slow_arrivals[1:],
        ):
            self.assertAlmostEqual(
                current - previous,
                2.0 * config.launch_interval_aci_cycles,
            )

        link.scale_link_delay(0.5)
        normal_start, normal_arrivals = transfer(range(33, 36))
        self.assertEqual([msg_id for msg_id, _ in normal_arrivals], [33, 34, 35])
        self.assertAlmostEqual(normal_arrivals[0][1] - normal_start, 4.5)
        for (_, previous), (_, current) in zip(
            normal_arrivals,
            normal_arrivals[1:],
        ):
            self.assertAlmostEqual(
                current - previous,
                config.launch_interval_aci_cycles,
            )

    def test_credit_return_uses_sync_plane_without_data_traffic(self) -> None:
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH1)
        link = Link(
            env,
            LinkConfig(sync_credit_return_aci_cycles=2.0),
            NoCChannel.CH1,
            tracer,
            "credit-probe",
            noc_cycles_per_aci_cycle=2.0,
        )

        consumed = link.in_flight_credits.get(1)
        env.run(until=consumed)
        returned = link.ack_credit()
        env.run(until=returned)

        self.assertEqual(env.now, 2.0)
        self.assertEqual(link.in_flight_credits.level, 2)
        self.assertFalse(link._out_queue.items)
        self.assertFalse(link.flit_buffer.items)
        self.assertFalse(
            any(
                event.action in (FlitAction.LINK_SEND, FlitAction.LINK_RECV)
                for event in tracer.events
            )
        )
        self.assertEqual(len(tracer.events), 1)
        credit_event = tracer.events[0]
        self.assertIs(credit_event.action, FlitAction.CREDIT_RETURN)
        self.assertIs(credit_event.fabric_id, NoCChannel.CH1)
        self.assertIs(credit_event.plane, NoCPlane.SYNC)

    def test_cross_fabric_injection_is_rejected_before_state_changes(self):
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        link = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            tracer,
            "probe",
            noc_cycles_per_aci_cycle=2.0,
        )
        foreign_flit = Flit(
            flit_type=FlitType.SINGLE,
            payload_bytes=512,
            msg_id=70,
            fabric_id=NoCChannel.CH1,
            src_router=0,
            dst_router=1,
        )

        initial_credits = link.in_flight_credits.level
        with self.assertRaisesRegex(ValueError, "CH1 flit cannot enter CH0:probe"):
            link.send_flit(foreign_flit)
        self.assertEqual(link.in_flight_credits.level, initial_credits)
        self.assertFalse(link._out_queue.items)
        self.assertFalse(tracer.events)

        noc = NoC(
            env,
            NoCConfig(),
            NoCChannel.CH0,
            tracer,
        ).build_connection_mesh()
        router = noc.routers[0]
        ingress = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            tracer,
            "router-ingress",
            noc_cycles_per_aci_cycle=2.0,
        )
        egress = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            tracer,
            "router-egress",
            noc_cycles_per_aci_cycle=2.0,
        )
        router.bind_link(PORT_PE, ingress, egress)
        ingress.flit_buffer.put(foreign_flit)
        with self.assertRaisesRegex(ValueError, "CH1 flit cannot enter CH0:R0"):
            env.run()
        self.assertFalse(router.reservation)
        self.assertFalse(router._sa_reqs)
        self.assertFalse(tracer.events)

    def test_router_rejects_different_same_fabric_tracer(self):
        env = simpy.Environment()
        noc_tracer = NoCTracer(NoCChannel.CH0)
        link_tracer = NoCTracer(NoCChannel.CH0)
        noc = NoC(
            env,
            NoCConfig(),
            NoCChannel.CH0,
            noc_tracer,
        ).build_connection_mesh()
        link_in = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            link_tracer,
            "foreign-tracer-in",
            noc_cycles_per_aci_cycle=2.0,
        )
        link_out = Link(
            env,
            LinkConfig(),
            NoCChannel.CH0,
            link_tracer,
            "foreign-tracer-out",
            noc_cycles_per_aci_cycle=2.0,
        )

        router = noc.routers[0]
        with self.assertRaisesRegex(ValueError, "with a different tracer"):
            router.bind_link(PORT_PE, link_in, link_out)
        self.assertIsNone(router.port_in.get(PORT_PE))
        self.assertIsNone(router.port_out.get(PORT_PE))
        self.assertNotIn(PORT_PE, router.out_channels)

    def test_trace_records_distinguish_identical_fabric_local_ids(self):
        latencies = {}
        qualified_link_names = {}
        logical_link_names = {}

        for fabric_id in NoCChannel:
            harness = MeshHarness(fabric_id)
            flit = harness.flit(FlitType.SINGLE, 71, 0, 1)
            harness.transfer(0, 1, [flit])

            self.assertTrue(
                all(event.fabric_id is fabric_id for event in harness.tracer.events)
            )
            self.assertTrue(
                all(
                    event.plane
                    is (
                        NoCPlane.SYNC
                        if event.action is FlitAction.CREDIT_RETURN
                        else NoCPlane.DATA
                    )
                    for event in harness.tracer.events
                )
            )
            self.assertIn(f"fabric={fabric_id.name}", harness.tracer.summary(harness.env.now))
            latencies.update(harness.tracer.per_msg_latency())
            qualified_link_names[fabric_id] = {
                event.link_name
                for event in harness.tracer.events
                if event.link_name
            }
            logical_link_names[fabric_id] = {
                name.split(":", maxsplit=1)[1]
                for name in qualified_link_names[fabric_id]
            }

        self.assertEqual(
            set(latencies),
            {(NoCChannel.CH0, 71), (NoCChannel.CH1, 71)},
        )
        self.assertEqual(
            logical_link_names[NoCChannel.CH0],
            logical_link_names[NoCChannel.CH1],
        )
        self.assertTrue(
            qualified_link_names[NoCChannel.CH0].isdisjoint(
                qualified_link_names[NoCChannel.CH1]
            )
        )

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
        expected_gap = FLIT_BYTES / 120.0
        for actual, expected in zip(
            times,
            (38.5, 38.5 + expected_gap, 38.5 + 2 * expected_gap),
        ):
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
        self.assertEqual(arrivals[1][1] - arrivals[0][1], 50)
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
    def _standalone_flit(flit_type, msg_id, payload_bytes=FLIT_BYTES):
        return Flit(
            flit_type=flit_type,
            payload_bytes=payload_bytes,
            msg_id=msg_id,
            fabric_id=NoCChannel.CH0,
            src_router=0,
            dst_router=1,
        )


if __name__ == "__main__":
    unittest.main()
