import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import Mock, patch

import simpy
from pydantic import ValidationError

from simulator_detailed.architecture import Arch
from simulator_detailed.benchmark_references import (
    NMC_32K_BATCH_REFERENCE,
    RB53_CONTENTION_REFERENCE,
    RB54_LATENCY_REFERENCE,
)
from simulator_detailed.benchmark_workloads import (
    BatchedNMCReplayResult,
    BatchedNMCStream,
    NMCBenchmarkScenario,
    SequentialPingPongResult,
    replay_dual_channel_full_duplex_batch,
    replay_dual_channel_same_direction_batch,
    replay_sequential_ping_pong,
    replay_shared_link_contention_batch,
    replay_single_channel_batch,
)
from simulator_detailed.configs.schemas.arch_config import (
    CoreConfig,
    DMAEngineConfig,
    DMAType,
    FlitConfig,
    LinkConfig,
    NMCChannelConfig,
    NMCConfig,
    NMCShapeTimingConfig,
    NoCConfig,
    RouterConfig,
    RouterPipelineConfig,
)
from simulator_detailed.configs.schemas.failure_configs import LinkFail, RouterFail
from simulator_detailed.core import Core
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.noc import (
    FlitAction,
    Link,
    MessageFabricTiming,
    NoC,
    NoCTracer,
    RoundRobinArbiter,
)
from simulator_detailed.pe_channel import (
    NMCChannel,
    NMCReceiveResult,
    NMCTransmitResult,
    PEChannelBinding,
)
from simulator_detailed.run import DEFAULT_ARCH_PATH, arch_analyzer
from simulator_detailed.tracing import collect_noc_link_events, process_events
from simulator_detailed.utils.definitions import (
    DIR_EAST,
    FLIT_BYTES,
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
    BurstLenMode,
    DimSlice,
    Direction,
    DMAAttachmentMode,
    DMACommandMode,
    EndpointAddress,
    Flit,
    FlitType,
    Message,
    NMCShapeMode,
    NoCChannel,
    NoCPlane,
    NodeType,
    OperatorType,
    TransType,
    compute_flit_count,
)
from simulator_detailed.utils.dfg import DFG
from simulator_detailed.utils.task import Task


class MeshHarness:
    def __init__(
        self,
        fabric_id=NoCChannel.CH0,
        config=None,
        env=None,
    ):
        self.env = env if env is not None else simpy.Environment()
        self.config = config or NoCConfig()
        self.fabric_id = fabric_id
        self.tracer = NoCTracer(fabric_id)
        self.noc = NoC(
            self.env,
            self.config,
            fabric_id,
            self.tracer,
        ).build_connection_mesh()
        self.endpoints = {}
        self.nmc_channels = {}
        self.registry = EndpointRegistry(self.config)

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

    def attach_nmc(
        self,
        router_id,
        channel_config=None,
        shape_timing=None,
    ):
        if router_id in self.nmc_channels:
            raise ValueError(f"PE{router_id} already has an NMC channel")
        if router_id not in self.endpoints:
            self.attach(router_id)
        tx_link, rx_link = self.endpoints[router_id]
        channel = NMCChannel(
            env=self.env,
            config=channel_config or NMCChannelConfig(),
            shape_timing=shape_timing or NMCShapeTimingConfig(),
            binding=PEChannelBinding(
                address=self.registry.resolve(
                    NodeType.PE,
                    router_id,
                    fabric_id=self.fabric_id,
                ),
                tx_link=tx_link,
                rx_link=rx_link,
                router=self.noc.routers[router_id],
            ),
        )
        self.nmc_channels[router_id] = channel
        return channel

    def message(self, msg_id, src, dst, flit_count):
        return Message(
            src=self.registry.resolve(
                NodeType.PE,
                src,
                fabric_id=self.fabric_id,
            ),
            dst=self.registry.resolve(
                NodeType.PE,
                dst,
                fabric_id=self.fabric_id,
            ),
            index=msg_id,
            data=[DimSlice(start=0, end=flit_count * FLIT_BYTES)],
        )

    def flit(
        self,
        flit_type,
        msg_id,
        src,
        dst,
        payload=512,
        burst_len_mode=BurstLenMode.BURST_LEN_7,
    ):
        return Flit(
            flit_type=flit_type,
            payload_bytes=payload,
            msg_id=msg_id,
            fabric_id=self.fabric_id,
            src_router=src,
            dst_router=dst,
            burst_len_mode=burst_len_mode,
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
    @staticmethod
    def _build_task_cores(dfg):
        env = simpy.Environment()
        noc_config = NoCConfig()
        arch = object.__new__(Arch)
        arch.env = env
        arch.x_size = noc_config.x
        arch.y_size = noc_config.y
        arch.endpoint_registry = EndpointRegistry(noc_config)
        arch.nocs = Arch.build_nocs(env, noc_config)
        mapper = Mock()
        mapper.dfg = dfg
        mapper.all_tasks_completed.return_value = True
        cores = arch.build_cores(
            env=env,
            config=CoreConfig(),
            noc_config=noc_config,
            mapper=mapper,
        )
        return env, arch, cores

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
            for arbiter in router.output_arbiters.values()
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
        nmc_channels = []
        for core in cores:
            self.assertEqual(set(core.channel_bindings), set(NoCChannel))
            self.assertEqual(set(core.nmc_channels), set(NoCChannel))
            self.assertFalse(hasattr(core, "data_in"))
            self.assertFalse(hasattr(core, "data_out"))
            self.assertFalse(hasattr(core, "router"))

            for fabric_id in NoCChannel:
                channel = core.nmc_channel_for(fabric_id)
                binding = core.binding_for(fabric_id)
                self.assertIs(channel.binding, binding)
                self.assertIs(channel.fabric_id, fabric_id)
                self.assertIs(channel.env, env)
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
                nmc_channels.append(channel)

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
        self.assertEqual(len(nmc_channels), 64)
        for attribute in (
            "tx_datapath",
            "rx_datapath",
            "descriptor_slots",
            "descriptor_issuer",
            "tx_data_queue",
            "rx_data_queue",
        ):
            self.assertEqual(
                len({id(getattr(channel, attribute)) for channel in nmc_channels}),
                64,
            )
        self.assertTrue(
            all(
                channel.descriptor_slots.capacity == 24
                for channel in nmc_channels
            )
        )

        core0_ch0 = cores[0].binding_for(NoCChannel.CH0)
        core0_ch1 = cores[0].binding_for(NoCChannel.CH1)
        with self.assertRaisesRegex(ValueError, "cannot bind CH1:R0"):
            PEChannelBinding(
                address=core0_ch0.address,
                tx_link=core0_ch1.tx_link,
                rx_link=core0_ch1.rx_link,
                router=core0_ch1.router,
            )
        with self.assertRaisesRegex(ValueError, "already has a CH0 NMC channel"):
            cores[0].bind_channel(
                cores[0].nmc_channel_for(NoCChannel.CH0)
            )

    def test_nmc_channels_have_independent_directional_resources(self):
        env = simpy.Environment()
        noc_config = NoCConfig()
        arch = object.__new__(Arch)
        arch.env = env
        arch.x_size = noc_config.x
        arch.y_size = noc_config.y
        arch.endpoint_registry = EndpointRegistry(noc_config)
        arch.nocs = Arch.build_nocs(env, noc_config)
        core_config = CoreConfig(
            nmc=NMCConfig(
                ch0=NMCChannelConfig(
                    tx_bytes_per_cycle=117.0,
                    rx_bytes_per_cycle=118.0,
                    max_outstanding_descriptors=2,
                ),
                ch1=NMCChannelConfig(
                    tx_bytes_per_cycle=119.0,
                    rx_bytes_per_cycle=120.0,
                    max_outstanding_descriptors=3,
                ),
            )
        )
        core = arch.build_cores(
            env=env,
            config=core_config,
            noc_config=noc_config,
            mapper=Mock(),
        )[0]
        ch0 = core.nmc_channel_for(NoCChannel.CH0)
        ch1 = core.nmc_channel_for(NoCChannel.CH1)

        self.assertEqual(ch0.config.tx_bytes_per_cycle, 117.0)
        self.assertEqual(ch0.config.rx_bytes_per_cycle, 118.0)
        self.assertEqual(ch1.config.tx_bytes_per_cycle, 119.0)
        self.assertEqual(ch1.config.rx_bytes_per_cycle, 120.0)
        self.assertEqual(ch0.descriptor_slots.capacity, 2)
        self.assertEqual(ch1.descriptor_slots.capacity, 3)
        self.assertIsNot(ch0.descriptor_slots, ch1.descriptor_slots)
        self.assertIsNot(ch0.descriptor_issuer, ch1.descriptor_issuer)

        intervals = {}

        def occupy(resource, name, duration):
            with resource.request() as request:
                yield request
                intervals[name] = [env.now, None]
                yield env.timeout(duration)
                intervals[name][1] = env.now

        env.process(occupy(ch0.tx_datapath, "ch0_tx_first", 10))
        env.process(occupy(ch0.tx_datapath, "ch0_tx_second", 2))
        env.process(occupy(ch0.rx_datapath, "ch0_rx", 3))
        env.process(occupy(ch1.tx_datapath, "ch1_tx", 4))
        env.process(occupy(ch1.rx_datapath, "ch1_rx", 5))
        env.run()

        self.assertEqual(intervals["ch0_tx_first"], [0, 10])
        self.assertEqual(intervals["ch0_tx_second"], [10, 12])
        self.assertEqual(intervals["ch0_rx"], [0, 3])
        self.assertEqual(intervals["ch1_tx"], [0, 4])
        self.assertEqual(intervals["ch1_rx"], [0, 5])

        self.assertFalse(ch0.tx_data_queue.items)
        self.assertFalse(ch0.rx_data_queue.items)
        self.assertFalse(ch1.tx_data_queue.items)
        self.assertFalse(ch1.rx_data_queue.items)

        with self.assertRaisesRegex(ValueError, "same SimPy environment"):
            NMCChannel(
                env=simpy.Environment(),
                config=NMCChannelConfig(),
                shape_timing=NMCShapeTimingConfig(),
                binding=ch0.binding,
            )

        other_env = simpy.Environment()
        other_core = Core(
            env=other_env,
            core_id=0,
            config=CoreConfig(),
            mapper=Mock(),
            endpoint_registry=arch.endpoint_registry,
        )
        with self.assertRaisesRegex(ValueError, "same SimPy environment"):
            other_core.bind_channel(ch0)

    def test_nmc_descriptor_capacity_backpressures_and_releases(self):
        harness = MeshHarness()
        source = harness.attach_nmc(
            0,
            NMCChannelConfig(
                tx_bytes_per_cycle=1.0,
                max_outstanding_descriptors=2,
            ),
        )
        destination = harness.attach_nmc(1)
        messages = [
            harness.message(msg_id, 0, 1, 1)
            for msg_id in (110, 111, 112)
        ]
        received = []

        def receive_messages():
            for _ in messages:
                received.append((yield destination.recv_flit()))

        send_processes = [source.send(message) for message in messages]
        receive_process = harness.env.process(receive_messages())

        harness.env.run(until=1.0)
        self.assertEqual(source.outstanding_descriptor_count, 1)
        self.assertEqual(len(source.descriptor_issuer.users), 1)
        self.assertEqual(len(source.descriptor_issuer.queue), 2)
        self.assertFalse(source.descriptor_slots.queue)
        self.assertFalse(source.tx_data_queue.items)
        self.assertFalse(any(process.triggered for process in send_processes))

        harness.env.run(until=115.0)
        self.assertEqual(source.outstanding_descriptor_count, 2)
        self.assertEqual(len(source.descriptor_slots.queue), 1)
        self.assertEqual(len(source.descriptor_issuer.users), 1)
        self.assertFalse(source.descriptor_issuer.queue)
        self.assertEqual(len(source.tx_data_queue.items), 1)
        self.assertFalse(any(process.triggered for process in send_processes))

        harness.env.run(until=570.0)
        self.assertTrue(send_processes[0].triggered)
        self.assertFalse(send_processes[1].triggered)
        self.assertFalse(send_processes[2].triggered)
        self.assertEqual(source.outstanding_descriptor_count, 2)
        self.assertFalse(source.descriptor_slots.queue)
        self.assertEqual(len(source.descriptor_issuer.users), 1)
        self.assertFalse(source.tx_data_queue.items)

        harness.env.run(
            until=harness.env.all_of((*send_processes, receive_process))
        )
        self.assertEqual(source.outstanding_descriptor_count, 0)
        self.assertFalse(source.descriptor_slots.queue)
        self.assertEqual(
            [flit.msg_id for flit in received],
            [110, 111, 112],
        )

    def test_nmc_descriptor_issue_is_serialized_and_payload_independent(self):
        harness = MeshHarness()
        source = harness.attach_nmc(
            0,
            NMCChannelConfig(descriptor_issue_cycles=13.0),
        )
        destination = harness.attach_nmc(1)
        messages = [
            harness.message(msg_id, 0, 1, flit_count)
            for msg_id, flit_count in ((120, 1), (121, 2), (122, 4))
        ]
        received = []

        def receive_messages():
            for _ in range(sum(message.flit_count() for message in messages)):
                received.append((yield destination.recv_flit()))

        send_processes = [source.send(message) for message in messages]
        receive_process = harness.env.process(receive_messages())
        harness.env.run(
            until=harness.env.all_of((*send_processes, receive_process))
        )

        first_launch_times = {}
        for event in harness.tracer.events:
            if (
                event.action is FlitAction.LINK_SEND
                and event.link_name == source.binding.tx_link.link_name
            ):
                first_launch_times.setdefault(event.msg_id, event.time)

        self.assertEqual(set(first_launch_times), {120, 121, 122})
        self.assertEqual(
            [
                process.value.descriptor_acceptance_time_aci_cycles
                for process in send_processes
            ],
            [13.0, 26.0, 39.0],
        )
        self.assertEqual(
            sorted(first_launch_times, key=first_launch_times.__getitem__),
            [120, 121, 122],
        )
        for previous, current in zip(
            (120, 121),
            (121, 122),
        ):
            self.assertGreaterEqual(
                first_launch_times[current] - first_launch_times[previous],
                source.config.inter_command_turnaround_aci_cycles,
            )

    def test_nmc_inter_command_turnaround_expires_while_idle(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        destination = harness.attach_nmc(1)

        first_send = source.send(harness.message(123, 0, 1, 1))
        first_receive = destination.recv_flit()
        harness.env.run(until=harness.env.all_of((first_send, first_receive)))

        harness.env.run(
            until=harness.env.timeout(
                source.config.inter_command_turnaround_aci_cycles + 1.0
            )
        )
        second_send = source.send(harness.message(124, 0, 1, 1))
        second_receive = destination.recv_flit()
        harness.env.run(
            until=harness.env.all_of((second_send, second_receive))
        )

        self.assertAlmostEqual(
            second_send.value.operation_latency_aci_cycles,
            first_send.value.operation_latency_aci_cycles,
        )

    def test_nmc_descriptor_issuers_are_independent_across_channels(self):
        env = simpy.Environment()
        harnesses = {
            fabric_id: MeshHarness(fabric_id=fabric_id, env=env)
            for fabric_id in NoCChannel
        }
        sources = {
            fabric_id: harness.attach_nmc(0)
            for fabric_id, harness in harnesses.items()
        }
        destinations = {
            fabric_id: harness.attach_nmc(1)
            for fabric_id, harness in harnesses.items()
        }
        processes = []

        for fabric_id, msg_id in (
            (NoCChannel.CH0, 130),
            (NoCChannel.CH1, 131),
        ):
            harness = harnesses[fabric_id]
            processes.append(
                sources[fabric_id].send(
                    harness.message(msg_id, 0, 1, 1)
                )
            )
            processes.append(destinations[fabric_id].recv_flit())

        env.run(until=env.all_of(processes))

        first_launch_times = {}
        for fabric_id, msg_id in (
            (NoCChannel.CH0, 130),
            (NoCChannel.CH1, 131),
        ):
            source = sources[fabric_id]
            first_launch_times[fabric_id] = next(
                event.time
                for event in harnesses[fabric_id].tracer.events
                if event.action is FlitAction.LINK_SEND
                and event.link_name == source.binding.tx_link.link_name
                and event.msg_id == msg_id
            )

        self.assertAlmostEqual(
            first_launch_times[NoCChannel.CH0],
            first_launch_times[NoCChannel.CH1],
        )
        self.assertAlmostEqual(
            first_launch_times[NoCChannel.CH0],
            sources[NoCChannel.CH0]
            .shape_timing.dynamic_endpoint_setup_aci_cycles
            - sources[NoCChannel.CH0].binding.tx_link.serialization_aci_cycles
            - sources[NoCChannel.CH0]
            .binding.tx_link.effective_link_stage_aci_cycles
            - sources[NoCChannel.CH0]
            .post_injection_endpoint_completion_aci_cycles,
        )

    def test_nmc_directional_service_uses_the_slowest_pipeline_stage(self):
        cases = (
            (64.0, 120.0, 120.0, 64.0),
            (120.0, 64.0, 120.0, 64.0),
            (120.0, 120.0, 80.0, 80.0),
            (117.0, 118.0, 120.0, 117.0),
        )
        flit_count = 64

        for tx_rate, rx_rate, link_rate, expected_rate in cases:
            with self.subTest(
                tx_rate=tx_rate,
                rx_rate=rx_rate,
                link_rate=link_rate,
            ):
                link_config = LinkConfig(
                    launch_interval_aci_cycles=FLIT_BYTES / link_rate
                )
                harness = MeshHarness(
                    config=NoCConfig(c2r_link=link_config)
                )
                source = harness.attach_nmc(
                    0,
                    NMCChannelConfig(
                        tx_bytes_per_cycle=tx_rate,
                        rx_bytes_per_cycle=120.0,
                    ),
                )
                destination = harness.attach_nmc(
                    1,
                    NMCChannelConfig(
                        tx_bytes_per_cycle=120.0,
                        rx_bytes_per_cycle=rx_rate,
                    ),
                )
                message = harness.message(80, 0, 1, flit_count)
                arrival_times = []

                def receive_packet(channel, times, env):
                    for _ in range(flit_count):
                        yield channel.recv_flit()
                        times.append(float(env.now))

                send_process = source.send(message)
                receive_process = harness.env.process(
                    receive_packet(
                        destination,
                        arrival_times,
                        harness.env,
                    )
                )
                harness.env.run(
                    until=harness.env.all_of(
                        (send_process, receive_process)
                    )
                )

                observed_rate = (
                    (flit_count - 1) * FLIT_BYTES
                    / (arrival_times[-1] - arrival_times[0])
                )
                source_launch_times = [
                    event.time
                    for event in harness.tracer.events
                    if event.action is FlitAction.LINK_SEND
                    and event.link_name == source.binding.tx_link.link_name
                    and event.msg_id == message.index
                ]
                source_launch_rate = (
                    (flit_count - 1) * FLIT_BYTES
                    / (source_launch_times[-1] - source_launch_times[0])
                )
                source_cap = min(tx_rate, link_rate)
                self.assertLessEqual(source_launch_rate, source_cap + 1e-6)
                if rx_rate >= source_cap:
                    self.assertAlmostEqual(
                        source_launch_rate,
                        source_cap,
                        places=6,
                    )
                self.assertAlmostEqual(
                    observed_rate,
                    expected_rate,
                    delta=expected_rate * 0.02,
                )

    def test_nmc_channels_reach_full_duplex_aggregate_rates(self):
        env = simpy.Environment()
        channel_config = NMCChannelConfig(
            tx_bytes_per_cycle=117.0,
            rx_bytes_per_cycle=117.0,
        )
        harnesses = {
            fabric_id: MeshHarness(fabric_id=fabric_id, env=env)
            for fabric_id in NoCChannel
        }
        channels = {
            (fabric_id, pe_id): harness.attach_nmc(pe_id, channel_config)
            for fabric_id, harness in harnesses.items()
            for pe_id in (0, 1)
        }
        flit_count = 64
        flows = (
            (NoCChannel.CH0, 0, 1, 90),
            (NoCChannel.CH0, 1, 0, 91),
            (NoCChannel.CH1, 0, 1, 92),
            (NoCChannel.CH1, 1, 0, 93),
        )
        arrival_times = {msg_id: [] for _, _, _, msg_id in flows}
        processes = []

        def receive_packet(channel, msg_id):
            for _ in range(flit_count):
                flit = yield channel.recv_flit()
                self.assertEqual(flit.msg_id, msg_id)
                arrival_times[msg_id].append(float(env.now))

        for fabric_id, src, dst, msg_id in flows:
            harness = harnesses[fabric_id]
            processes.append(
                channels[(fabric_id, src)].send(
                    harness.message(msg_id, src, dst, flit_count)
                )
            )
            processes.append(
                env.process(
                    receive_packet(channels[(fabric_id, dst)], msg_id)
                )
            )

        env.run(until=env.all_of(processes))

        def aggregate_rate(msg_ids):
            start = min(arrival_times[msg_id][0] for msg_id in msg_ids)
            end = max(arrival_times[msg_id][-1] for msg_id in msg_ids)
            return len(msg_ids) * (flit_count - 1) * FLIT_BYTES / (end - start)

        for _, _, _, msg_id in flows:
            self.assertAlmostEqual(
                aggregate_rate((msg_id,)),
                117.0,
                delta=117.0 * 0.02,
            )
        self.assertAlmostEqual(
            aggregate_rate((90, 91)),
            234.0,
            delta=234.0 * 0.02,
        )
        self.assertAlmostEqual(
            aggregate_rate((90, 92)),
            234.0,
            delta=234.0 * 0.02,
        )
        self.assertAlmostEqual(
            aggregate_rate((90, 91, 92, 93)),
            468.0,
            delta=468.0 * 0.02,
        )

    def test_nmc_send_validates_source_and_preserves_packet_order(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        destination = harness.attach_nmc(1)
        wrong_source = harness.message(100, 1, 0, 1)

        with self.assertRaisesRegex(ValueError, r"cannot send from PE\[1\]"):
            source.send(wrong_source)

        first = harness.message(101, 0, 1, 3)
        second = harness.message(102, 0, 1, 2)
        received = []

        def receive_packets():
            for _ in range(first.flit_count() + second.flit_count()):
                received.append((yield destination.recv_flit()))

        send_first = source.send(first)
        send_second = source.send(second)
        receive_process = harness.env.process(receive_packets())
        harness.env.run(
            until=harness.env.all_of(
                (send_first, send_second, receive_process)
            )
        )

        self.assertEqual(
            [flit.msg_id for flit in received],
            [101, 101, 101, 102, 102],
        )
        self.assertEqual(
            [flit.flit_type for flit in received],
            [
                FlitType.HEAD,
                FlitType.BODY,
                FlitType.TAIL,
                FlitType.HEAD,
                FlitType.TAIL,
            ],
        )

    def test_nmc_command_receive_reassembles_by_message_id(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        destination = harness.attach_nmc(1)
        first = harness.message(103, 0, 1, 3)
        second = harness.message(104, 0, 1, 2)

        receive_second = destination.recv_message(
            second,
            NMCShapeMode.STATIC,
        )
        receive_first = destination.recv_message(
            first,
            NMCShapeMode.DYNAMIC,
        )
        send_first = source.send(first)
        send_second = source.send(second)
        harness.env.run(
            until=harness.env.all_of(
                (receive_first, receive_second, send_first, send_second)
            )
        )

        first_result = receive_first.value
        second_result = receive_second.value
        self.assertIsInstance(first_result, NMCReceiveResult)
        self.assertIsInstance(second_result, NMCReceiveResult)
        self.assertEqual(
            [flit.msg_id for flit in first_result.flits],
            [103, 103, 103],
        )
        self.assertEqual(
            [flit.flit_type for flit in first_result.flits],
            [FlitType.HEAD, FlitType.BODY, FlitType.TAIL],
        )
        self.assertEqual(
            [flit.msg_id for flit in second_result.flits],
            [104, 104],
        )
        self.assertTrue(first_result.flits[-1].is_tail)
        self.assertTrue(second_result.flits[-1].is_tail)
        self.assertEqual(first_result.submission_time_aci_cycles, 0.0)
        self.assertEqual(second_result.submission_time_aci_cycles, 0.0)
        self.assertEqual(
            second_result.descriptor_acceptance_time_aci_cycles,
            57.0,
        )
        self.assertEqual(
            first_result.descriptor_acceptance_time_aci_cycles,
            114.0,
        )
        self.assertEqual(second_result.endpoint_ready_time_aci_cycles, 79.5)
        self.assertEqual(first_result.endpoint_ready_time_aci_cycles, 182.0)
        self.assertGreaterEqual(
            first_result.operation_completion_time_aci_cycles,
            first_result.endpoint_ready_time_aci_cycles,
        )
        self.assertGreaterEqual(
            second_result.operation_completion_time_aci_cycles,
            second_result.endpoint_ready_time_aci_cycles,
        )
        self.assertEqual(destination.outstanding_descriptor_count, 0)
        with self.assertRaisesRegex(ValueError, "already has a receive command"):
            destination.recv_message(first, NMCShapeMode.STATIC)
        with self.assertRaisesRegex(RuntimeError, "cannot mix raw-flit"):
            destination.recv_flit()

    def test_nmc_operation_results_name_endpoint_timing_boundaries(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        destination = harness.attach_nmc(1)
        message = harness.message(105, 0, 1, 1)

        receive_process = destination.recv_message(
            message,
            NMCShapeMode.DYNAMIC,
        )
        send_process = source.send(message)
        harness.env.run(
            until=harness.env.all_of((send_process, receive_process))
        )

        send_result = send_process.value
        receive_result = receive_process.value
        fabric_timing = harness.tracer.message_fabric_timings()[
            (NoCChannel.CH0, message.index)
        ]

        self.assertIsInstance(send_result, NMCTransmitResult)
        self.assertIsInstance(receive_result, NMCReceiveResult)
        self.assertLessEqual(
            send_result.submission_time_aci_cycles,
            send_result.descriptor_acceptance_time_aci_cycles,
        )
        self.assertLessEqual(
            send_result.descriptor_acceptance_time_aci_cycles,
            send_result.endpoint_ready_time_aci_cycles,
        )
        self.assertLessEqual(
            send_result.endpoint_ready_time_aci_cycles,
            send_result.final_local_handoff_time_aci_cycles,
        )
        self.assertEqual(
            send_result.final_local_handoff_time_aci_cycles,
            send_result.operation_completion_time_aci_cycles,
        )
        self.assertAlmostEqual(
            send_result.operation_completion_time_aci_cycles,
            send_result.endpoint_ready_time_aci_cycles
            + source.tx_service_interval_aci_cycles,
        )
        self.assertEqual(
            send_result.operation_latency_aci_cycles,
            send_result.operation_completion_time_aci_cycles
            - send_result.submission_time_aci_cycles,
        )

        self.assertLessEqual(
            receive_result.submission_time_aci_cycles,
            receive_result.descriptor_acceptance_time_aci_cycles,
        )
        self.assertLessEqual(
            receive_result.descriptor_acceptance_time_aci_cycles,
            receive_result.endpoint_ready_time_aci_cycles,
        )
        self.assertLessEqual(
            receive_result.endpoint_ready_time_aci_cycles,
            receive_result.operation_completion_time_aci_cycles,
        )
        self.assertLessEqual(
            receive_result.tail_rx_service_completion_time_aci_cycles,
            receive_result.operation_completion_time_aci_cycles,
        )
        self.assertEqual(
            receive_result.operation_completion_time_aci_cycles,
            max(
                receive_result.endpoint_ready_time_aci_cycles,
                receive_result.tail_rx_service_completion_time_aci_cycles,
            ),
        )
        self.assertEqual(
            receive_result.operation_latency_aci_cycles,
            receive_result.operation_completion_time_aci_cycles
            - receive_result.submission_time_aci_cycles,
        )

        self.assertLess(
            send_result.operation_completion_time_aci_cycles,
            fabric_timing.first_injection_time_aci_cycles,
        )
        self.assertGreater(
            receive_result.tail_rx_service_completion_time_aci_cycles,
            fabric_timing.final_ejection_time_aci_cycles,
        )
        self.assertNotEqual(
            send_result.operation_latency_aci_cycles,
            fabric_timing.first_flit_fabric_latency_aci_cycles,
        )
        self.assertNotEqual(
            send_result.operation_latency_aci_cycles,
            fabric_timing.packet_fabric_completion_latency_aci_cycles,
        )

    def test_dfg_communication_channel_defaults_and_pair_validation(self):
        dfg = DFG()
        default_send = dfg.add_node(1, OperatorType.SEND, 0)
        explicit_receive = dfg.add_node(
            2,
            OperatorType.RECV,
            1,
            fabric_id=NoCChannel.CH1,
            nmc_shape_mode=NMCShapeMode.STATIC,
        )

        self.assertIs(default_send.fabric_id, NoCChannel.CH0)
        self.assertIs(default_send.nmc_shape_mode, NMCShapeMode.DYNAMIC)
        self.assertIs(explicit_receive.fabric_id, NoCChannel.CH1)
        self.assertIs(explicit_receive.nmc_shape_mode, NMCShapeMode.STATIC)
        with self.assertRaisesRegex(
            ValueError,
            "SEND 1 uses CH0 but paired RECV 2 uses CH1",
        ):
            dfg.add_edge(1, 2)

    def test_task_send_receive_uses_explicit_channel_and_endpoint_modes(self):
        payload_bytes = 1025
        payload = [DimSlice(start=0, end=payload_bytes)]
        dfg = DFG()
        send_node = dfg.add_node(
            1,
            OperatorType.SEND,
            0,
            output_size=payload,
            fabric_id=NoCChannel.CH1,
            nmc_shape_mode=NMCShapeMode.STATIC,
        )
        receive_node = dfg.add_node(
            2,
            OperatorType.RECV,
            1,
            input_size=payload,
            fabric_id=NoCChannel.CH1,
            nmc_shape_mode=NMCShapeMode.DYNAMIC,
        )
        dfg.add_edge(send_node.index, receive_node.index)
        receive_node.received_input = payload_bytes
        env, arch, cores = self._build_task_cores(dfg)
        cores[0].spm.container.get(payload_bytes)

        receive_process = env.process(Task(receive_node).execute(cores[1]))
        send_process = env.process(Task(send_node).execute(cores[0]))
        env.run(until=env.all_of((send_process, receive_process)))

        ch1_events = [
            event
            for event in arch.nocs[NoCChannel.CH1].tracer.events
            if event.msg_id == receive_node.index
        ]
        self.assertTrue(ch1_events)
        self.assertFalse(
            any(
                event.msg_id == receive_node.index
                for event in arch.nocs[NoCChannel.CH0].tracer.events
            )
        )
        first_injection = next(
            event.time
            for event in ch1_events
            if event.action is FlitAction.INJECT
        )
        source_channel = cores[0].nmc_channel_for(NoCChannel.CH1)
        self.assertAlmostEqual(
            first_injection,
            79.5
            - source_channel.post_injection_endpoint_completion_aci_cycles,
        )
        self.assertGreaterEqual(float(env.now), 125.0)

    def test_task_channels_support_dual_fabric_and_full_duplex_flows(self):
        payload_bytes = 512
        payload = [DimSlice(start=0, end=payload_bytes)]
        dfg = DFG()
        flow_specs = (
            (10, 11, 0, 1, NoCChannel.CH0),
            (12, 13, 0, 1, NoCChannel.CH1),
            (14, 15, 1, 0, NoCChannel.CH0),
        )
        task_pairs = []
        for send_id, receive_id, src, dst, fabric_id in flow_specs:
            send_node = dfg.add_node(
                send_id,
                OperatorType.SEND,
                src,
                output_size=payload,
                fabric_id=fabric_id,
            )
            receive_node = dfg.add_node(
                receive_id,
                OperatorType.RECV,
                dst,
                input_size=payload,
                fabric_id=fabric_id,
            )
            dfg.add_edge(send_id, receive_id)
            receive_node.received_input = payload_bytes
            task_pairs.append((send_node, receive_node))

        env, arch, cores = self._build_task_cores(dfg)
        for send_node, _ in task_pairs:
            cores[send_node.core_id].spm.container.get(payload_bytes)
        processes = []
        for send_node, receive_node in task_pairs:
            processes.append(
                env.process(Task(receive_node).execute(cores[receive_node.core_id]))
            )
            processes.append(
                env.process(Task(send_node).execute(cores[send_node.core_id]))
            )
        env.run(until=env.all_of(processes))

        ch0_routes = {
            (event.msg_id, event.src_router, event.dst_router)
            for event in arch.nocs[NoCChannel.CH0].tracer.events
            if event.action is FlitAction.INJECT
        }
        ch1_routes = {
            (event.msg_id, event.src_router, event.dst_router)
            for event in arch.nocs[NoCChannel.CH1].tracer.events
            if event.action is FlitAction.INJECT
        }
        self.assertIn((11, 0, 1), ch0_routes)
        self.assertIn((15, 1, 0), ch0_routes)
        self.assertIn((13, 0, 1), ch1_routes)
        self.assertTrue(all(process.triggered for process in processes))

    def test_nmc_command_path_rejects_missing_dma_runtime_and_collectives(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        pe_registry = EndpointRegistry(harness.config)
        collective = Message(
            src=pe_registry.resolve(
                NodeType.PE,
                0,
                fabric_id=NoCChannel.CH0,
            ),
            dst=pe_registry.resolve(
                NodeType.PE,
                1,
                fabric_id=NoCChannel.CH0,
            ),
            index=105,
            data=[DimSlice(start=0, end=1)],
            trans_type=TransType.BROADCAST,
        )
        with self.assertRaisesRegex(NotImplementedError, "SINGLECAST only"):
            source.send(collective)

        dma_registry = EndpointRegistry(
            NoCConfig(
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
        )
        pe_to_gm = Message(
            src=pe_registry.resolve(
                NodeType.PE,
                0,
                fabric_id=NoCChannel.CH0,
            ),
            dst=dma_registry.resolve(
                NodeType.GM_WDMA,
                0,
                fabric_id=NoCChannel.CH0,
                attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
            ),
            index=106,
            data=[DimSlice(start=0, end=1)],
            dma_command_mode=DMACommandMode.SINGLE_SIDE,
        )
        send_process = source.send(pe_to_gm)
        with self.assertRaisesRegex(
            RuntimeError,
            "DMA command coordinator is unavailable",
        ):
            harness.env.run(until=send_process)

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

    def test_nmc_shape_timing_has_exactly_two_endpoint_modes(self):
        config = NMCConfig()
        expected_targets = {
            NMCShapeMode.STATIC: 79.5,
            NMCShapeMode.DYNAMIC: 125.0,
        }

        self.assertEqual(
            {member.name: member.value for member in NMCShapeMode},
            {"STATIC": "static", "DYNAMIC": "dynamic"},
        )
        for shape_mode, expected_target in expected_targets.items():
            with self.subTest(shape_mode=shape_mode):
                self.assertEqual(
                    config.shape_timing.endpoint_setup_target_aci_cycles(
                        shape_mode
                    ),
                    expected_target,
                )

    def test_nmc_shape_timing_rejects_old_profiles_and_invalid_targets(self):
        for old_config in (
            {"latency_calibration_mode": "empirical"},
            {"operation_latency_profiles": {}},
            {"shape_timing": {"send_with_sync": 204.0}},
        ):
            with self.subTest(old_config=old_config), self.assertRaises(
                ValidationError
            ):
                NMCConfig.model_validate(old_config)

        for invalid_target in (-1.0, float("inf"), float("nan")):
            with self.subTest(invalid_target=invalid_target), self.assertRaises(
                ValidationError
            ):
                NMCShapeTimingConfig(
                    static_endpoint_setup_aci_cycles=invalid_target
                )
        with self.assertRaises(ValueError):
            NMCShapeMode("send_with_sync")
        with self.assertRaises(ValueError):
            NMCShapeMode("rb54")

    def test_rb54_is_validation_evidence_not_runtime_shape_timing(self):
        reference = RB54_LATENCY_REFERENCE

        self.assertEqual(reference.kernel, "noc_rb54.cpp")
        self.assertEqual(reference.flit_bytes, FLIT_BYTES)
        self.assertEqual(
            reference.default_burst_flits,
            BurstLenMode.BURST_LEN_7.explicit_quantum_flits(),
        )
        self.assertEqual(
            reference.hidden_serialization_through_bytes,
            reference.flit_bytes * reference.default_burst_flits,
        )
        for payload_bytes, _, expected_flits in reference.payload_samples:
            with self.subTest(payload_bytes=payload_bytes):
                self.assertEqual(
                    compute_flit_count(payload_bytes),
                    expected_flits,
                )
        for hops, measured_rtt in reference.hop_samples:
            with self.subTest(hops=hops):
                self.assertEqual(
                    reference.linear_rtt_aci_cycles(hops),
                    measured_rtt,
                )
        with self.assertRaisesRegex(ValueError, "hop count must be positive"):
            reference.linear_rtt_aci_cycles(0)

        self.assertNotIn("benchmark", NMCConfig.model_fields)
        self.assertNotIn("rb54", NMCShapeTimingConfig.model_fields)

    def test_32k_batch_reference_preserves_rb56_rb58_measurements(self):
        reference = NMC_32K_BATCH_REFERENCE

        self.assertEqual(
            reference.kernels,
            ("noc_rb56.cpp", "noc_rb58.cpp"),
        )
        self.assertEqual(reference.message_bytes, 32 * 1024)
        self.assertEqual(reference.messages_per_stream, 32)
        self.assertEqual(reference.payload_bytes_per_stream, 1024 * 1024)
        self.assertEqual(reference.simplex_tx_bytes_per_aci_cycle, 87.4)
        self.assertEqual(reference.simplex_rx_bytes_per_aci_cycle, 86.1)
        self.assertEqual(
            reference.dual_same_direction_bytes_per_aci_cycle,
            171.0,
        )
        self.assertEqual(
            reference.dual_full_duplex_bytes_per_aci_cycle,
            339.7,
        )
        self.assertAlmostEqual(
            reference.dual_full_duplex_efficiency,
            0.975,
            delta=0.005,
        )

    def test_sequential_ping_pong_replays_static_dynamic_and_mixed_rtt(self):
        mode_pairs = (
            (NMCShapeMode.STATIC, NMCShapeMode.STATIC),
            (NMCShapeMode.DYNAMIC, NMCShapeMode.DYNAMIC),
            (NMCShapeMode.STATIC, NMCShapeMode.DYNAMIC),
            (NMCShapeMode.DYNAMIC, NMCShapeMode.STATIC),
        )
        for forward_mode, reverse_mode in mode_pairs:
            with self.subTest(
                forward_mode=forward_mode,
                reverse_mode=reverse_mode,
            ):
                harness = MeshHarness()
                initiator = harness.attach_nmc(0)
                responder = harness.attach_nmc(1)
                forward = harness.message(160, 0, 1, 1).model_copy(
                    update={"nmc_shape_mode": forward_mode}
                )
                reverse = harness.message(161, 1, 0, 1).model_copy(
                    update={"nmc_shape_mode": reverse_mode}
                )

                replay = replay_sequential_ping_pong(
                    initiator,
                    responder,
                    forward,
                    reverse,
                    forward_receive_shape_mode=forward_mode,
                    reverse_receive_shape_mode=reverse_mode,
                )
                harness.env.run(until=replay)
                result = replay.value
                expected_rtt = (
                    initiator.shape_timing.endpoint_setup_target_aci_cycles(
                        forward_mode
                    )
                    + responder.shape_timing.endpoint_setup_target_aci_cycles(
                        reverse_mode
                    )
                    + RB54_LATENCY_REFERENCE.rtt_hop_slope_aci_cycles
                )

                self.assertIsInstance(result, SequentialPingPongResult)
                self.assertIs(
                    result.scenario,
                    NMCBenchmarkScenario.SEQUENTIAL_PING_PONG,
                )
                self.assertAlmostEqual(
                    result.operation_rtt_aci_cycles,
                    expected_rtt,
                )
                self.assertAlmostEqual(
                    result.operation_rtt_aci_cycles,
                    result.forward_operation_latency_aci_cycles
                    + result.reverse_operation_latency_aci_cycles,
                )

        for hops, measured_rtt in RB54_LATENCY_REFERENCE.hop_samples:
            with self.subTest(rb54_hops=hops):
                x = min(3, hops)
                y = hops - x
                destination_id = y * 4 + x
                harness = MeshHarness()
                initiator = harness.attach_nmc(0)
                responder = harness.attach_nmc(destination_id)
                forward = harness.message(
                    170 + hops,
                    0,
                    destination_id,
                    1,
                ).model_copy(
                    update={"nmc_shape_mode": NMCShapeMode.STATIC}
                )
                reverse = harness.message(
                    180 + hops,
                    destination_id,
                    0,
                    1,
                ).model_copy(
                    update={"nmc_shape_mode": NMCShapeMode.DYNAMIC}
                )

                replay = replay_sequential_ping_pong(
                    initiator,
                    responder,
                    forward,
                    reverse,
                    forward_receive_shape_mode=NMCShapeMode.STATIC,
                    reverse_receive_shape_mode=NMCShapeMode.DYNAMIC,
                )
                harness.env.run(until=replay)

                self.assertAlmostEqual(
                    replay.value.operation_rtt_aci_cycles,
                    measured_rtt,
                    delta=1.0,
                )

    def test_named_32k_batch_replays_match_rb56_rb58_rates(self):
        reference = NMC_32K_BATCH_REFERENCE

        def make_stream(
            harness,
            source,
            destination,
            *,
            name,
            first_message_id,
        ):
            messages = tuple(
                harness.message(
                    first_message_id + index,
                    source.binding.address.node_id,
                    destination.binding.address.node_id,
                    reference.message_bytes // FLIT_BYTES,
                )
                for index in range(reference.messages_per_stream)
            )
            return BatchedNMCStream(
                name=name,
                source=source,
                destination=destination,
                messages=messages,
                receive_shape_mode=NMCShapeMode.DYNAMIC,
            )

        for src, dst, name, expected_rate in (
            (0, 1, "tx_ch0", reference.simplex_tx_bytes_per_aci_cycle),
            (1, 0, "rx_ch0", reference.simplex_rx_bytes_per_aci_cycle),
        ):
            harness = MeshHarness()
            channels = {
                pe_id: harness.attach_nmc(pe_id) for pe_id in (0, 1)
            }
            stream = make_stream(
                harness,
                channels[src],
                channels[dst],
                name=name,
                first_message_id=200 if src == 0 else 300,
            )
            replay = replay_single_channel_batch(stream)
            harness.env.run(until=replay)
            result = replay.value

            self.assertIsInstance(result, BatchedNMCReplayResult)
            self.assertIs(
                result.scenario,
                NMCBenchmarkScenario.SINGLE_CHANNEL_BATCH,
            )
            self.assertEqual(
                result.aggregate_payload_bytes,
                reference.payload_bytes_per_stream,
            )
            self.assertAlmostEqual(
                result.aggregate_throughput_bytes_per_aci_cycle,
                expected_rate,
                delta=expected_rate * 0.05,
            )
            self.assertEqual(
                result.stream_throughput_bytes_per_aci_cycle(name),
                result.aggregate_throughput_bytes_per_aci_cycle,
            )

        env = simpy.Environment()
        harnesses = {
            fabric_id: MeshHarness(fabric_id, env=env)
            for fabric_id in NoCChannel
        }
        channels = {
            (fabric_id, pe_id): harness.attach_nmc(pe_id)
            for fabric_id, harness in harnesses.items()
            for pe_id in (0, 1)
        }
        same_direction_streams = tuple(
            make_stream(
                harnesses[fabric_id],
                channels[(fabric_id, 0)],
                channels[(fabric_id, 1)],
                name=f"{fabric_id.name.lower()}_forward",
                first_message_id=400,
            )
            for fabric_id in NoCChannel
        )
        dual_same_direction = replay_dual_channel_same_direction_batch(
            same_direction_streams[0],
            same_direction_streams[1],
        )
        env.run(until=dual_same_direction)
        dual_same_result = dual_same_direction.value

        self.assertIs(
            dual_same_result.scenario,
            NMCBenchmarkScenario.DUAL_CHANNEL_SAME_DIRECTION_BATCH,
        )
        self.assertAlmostEqual(
            dual_same_result.aggregate_throughput_bytes_per_aci_cycle,
            reference.dual_same_direction_bytes_per_aci_cycle,
            delta=reference.dual_same_direction_bytes_per_aci_cycle * 0.05,
        )

        env = simpy.Environment()
        harnesses = {
            fabric_id: MeshHarness(fabric_id, env=env)
            for fabric_id in NoCChannel
        }
        channels = {
            (fabric_id, pe_id): harness.attach_nmc(pe_id)
            for fabric_id, harness in harnesses.items()
            for pe_id in (0, 1)
        }
        full_duplex_streams = []
        for fabric_id in NoCChannel:
            harness = harnesses[fabric_id]
            full_duplex_streams.extend(
                (
                    make_stream(
                        harness,
                        channels[(fabric_id, 0)],
                        channels[(fabric_id, 1)],
                        name=f"{fabric_id.name.lower()}_forward",
                        first_message_id=500,
                    ),
                    make_stream(
                        harness,
                        channels[(fabric_id, 1)],
                        channels[(fabric_id, 0)],
                        name=f"{fabric_id.name.lower()}_reverse",
                        first_message_id=600,
                    ),
                )
            )
        dual_full_duplex = replay_dual_channel_full_duplex_batch(
            *full_duplex_streams
        )
        env.run(until=dual_full_duplex)
        dual_full_result = dual_full_duplex.value

        self.assertIs(
            dual_full_result.scenario,
            NMCBenchmarkScenario.DUAL_CHANNEL_FULL_DUPLEX_BATCH,
        )
        self.assertAlmostEqual(
            dual_full_result.aggregate_throughput_bytes_per_aci_cycle,
            reference.dual_full_duplex_bytes_per_aci_cycle,
            delta=reference.dual_full_duplex_bytes_per_aci_cycle * 0.05,
        )

    def test_rb53_shared_link_replay_matches_offered_load_transition(self):
        reference = RB53_CONTENTION_REFERENCE

        self.assertEqual(reference.kernel, "noc_rb53.cpp")
        self.assertEqual(reference.messages_per_stream, 16)

        def make_stream(
            harness,
            source,
            destination,
            *,
            name,
            message_bytes,
            first_message_id,
        ):
            return BatchedNMCStream(
                name=name,
                source=source,
                destination=destination,
                messages=tuple(
                    harness.message(
                        first_message_id + index,
                        source.binding.address.node_id,
                        destination.binding.address.node_id,
                        message_bytes // FLIT_BYTES,
                    )
                    for index in range(reference.messages_per_stream)
                ),
                receive_shape_mode=NMCShapeMode.DYNAMIC,
            )

        for sample in reference.samples:
            with self.subTest(message_bytes=sample.message_bytes):
                isolated_rates = []
                for stream_index, (source_id, destination_id) in enumerate(
                    ((0, 2), (1, 3))
                ):
                    harness = MeshHarness()
                    source = harness.attach_nmc(source_id)
                    destination = harness.attach_nmc(destination_id)
                    stream = make_stream(
                        harness,
                        source,
                        destination,
                        name="isolated",
                        message_bytes=sample.message_bytes,
                        first_message_id=1000 + 100 * stream_index,
                    )
                    replay = replay_single_channel_batch(stream)
                    harness.env.run(until=replay)
                    isolated_rates.append(
                        replay.value.aggregate_throughput_bytes_per_aci_cycle
                    )

                expected_isolated_rates = (
                    sample.first_isolated_bytes_per_aci_cycle,
                    sample.second_isolated_bytes_per_aci_cycle,
                )
                for observed, expected in zip(
                    isolated_rates,
                    expected_isolated_rates,
                ):
                    self.assertAlmostEqual(
                        observed,
                        expected,
                        delta=expected * 0.10,
                    )

                harness = MeshHarness()
                channels = {
                    pe_id: harness.attach_nmc(pe_id)
                    for pe_id in (0, 1, 2, 3)
                }
                first_stream = make_stream(
                    harness,
                    channels[0],
                    channels[2],
                    name="pe0_to_pe2",
                    message_bytes=sample.message_bytes,
                    first_message_id=2000,
                )
                second_stream = make_stream(
                    harness,
                    channels[1],
                    channels[3],
                    name="pe1_to_pe3",
                    message_bytes=sample.message_bytes,
                    first_message_id=3000,
                )
                replay = replay_shared_link_contention_batch(
                    first_stream,
                    second_stream,
                )
                harness.env.run(until=replay)
                result = replay.value

                self.assertIs(
                    result.scenario,
                    NMCBenchmarkScenario.SHARED_LINK_CONTENTION_BATCH,
                )
                self.assertAlmostEqual(
                    result.aggregate_throughput_bytes_per_aci_cycle,
                    sample.aggregate_contending_bytes_per_aci_cycle,
                    delta=(
                        sample.aggregate_contending_bytes_per_aci_cycle * 0.10
                    ),
                )
                observed_stream_rates = (
                    result.stream_throughput_bytes_per_aci_cycle(
                        "pe0_to_pe2"
                    ),
                    result.stream_throughput_bytes_per_aci_cycle(
                        "pe1_to_pe3"
                    ),
                )
                expected_stream_rates = (
                    sample.first_contending_bytes_per_aci_cycle,
                    sample.second_contending_bytes_per_aci_cycle,
                )
                for observed, expected in zip(
                    observed_stream_rates,
                    expected_stream_rates,
                ):
                    self.assertAlmostEqual(
                        observed,
                        expected,
                        delta=expected * 0.10,
                    )

                observed_retention = (
                    result.aggregate_throughput_bytes_per_aci_cycle
                    / sum(isolated_rates)
                )
                if sample.message_bytes <= 8 * 1024:
                    self.assertGreaterEqual(observed_retention, 0.95)
                else:
                    self.assertLess(observed_retention, 0.95)
                    self.assertAlmostEqual(
                        observed_stream_rates[0],
                        observed_stream_rates[1],
                        delta=max(observed_stream_rates) * 0.02,
                    )

    def test_nmc_shape_mode_reaches_send_admission_but_not_flits(self):
        harness = MeshHarness()
        source = harness.attach_nmc(0)
        destination = harness.attach_nmc(1)
        dynamic_message = harness.message(150, 0, 1, 2)
        static_message = dynamic_message.model_copy(
            update={"nmc_shape_mode": NMCShapeMode.STATIC}
        )

        self.assertIs(
            dynamic_message.nmc_shape_mode,
            NMCShapeMode.DYNAMIC,
        )
        self.assertEqual(
            static_message.packetize(),
            dynamic_message.packetize(),
        )
        self.assertNotIn("nmc_shape_mode", Flit.model_fields)

        received = []

        def receive_message():
            for _ in range(static_message.flit_count()):
                received.append((yield destination.recv_flit()))

        with patch.object(
            source.tx_data_queue,
            "put",
            wraps=source.tx_data_queue.put,
        ) as queue_put:
            send_process = source.send(static_message)
            receive_process = harness.env.process(receive_message())
            harness.env.run(
                until=harness.env.all_of((send_process, receive_process))
            )

        admitted_entry = queue_put.call_args.args[0]
        self.assertIs(admitted_entry.shape_mode, NMCShapeMode.STATIC)
        self.assertEqual(admitted_entry.submission_time_aci_cycles, 0.0)
        self.assertEqual(
            admitted_entry.descriptor_acceptance_time_aci_cycles,
            source.config.descriptor_issue_cycles,
        )
        self.assertAlmostEqual(
            admitted_entry.endpoint_ready_time_aci_cycles,
            (
                source.shape_timing.static_endpoint_setup_aci_cycles
                - source.first_injection_transport_aci_cycles
                - source.post_injection_endpoint_completion_aci_cycles
            ),
        )
        self.assertEqual(received, static_message.packetize())

    def test_nmc_shape_timing_calibrates_idle_endpoint_completion(self):
        expected_targets = {
            NMCShapeMode.STATIC: 79.5,
            NMCShapeMode.DYNAMIC: 125.0,
        }

        for msg_id, (shape_mode, expected_target) in enumerate(
            expected_targets.items(),
            start=151,
        ):
            with self.subTest(shape_mode=shape_mode):
                harness = MeshHarness()
                source = harness.attach_nmc(0)
                destination = harness.attach_nmc(1)
                message = harness.message(msg_id, 0, 1, 1).model_copy(
                    update={"nmc_shape_mode": shape_mode}
                )

                send_process = source.send(message)
                receive_process = destination.recv_message(
                    message,
                    shape_mode,
                )
                harness.env.run(
                    until=harness.env.all_of(
                        (send_process, receive_process)
                    )
                )

                injection_time = next(
                    event.time
                    for event in harness.tracer.events
                    if event.action is FlitAction.INJECT
                    and event.msg_id == msg_id
                )
                represented_cycles = (
                    source.config.descriptor_issue_cycles
                    + source.first_injection_transport_aci_cycles
                    + source.post_injection_endpoint_completion_aci_cycles
                )
                self.assertAlmostEqual(
                    injection_time,
                    expected_target
                    - source.post_injection_endpoint_completion_aci_cycles,
                )
                self.assertAlmostEqual(
                    receive_process.value.operation_completion_time_aci_cycles,
                    expected_target
                    + RB54_LATENCY_REFERENCE.rtt_hop_slope_aci_cycles / 2,
                )
                self.assertAlmostEqual(
                    source.endpoint_setup_residual_aci_cycles(shape_mode),
                    expected_target - represented_cycles,
                )

        slow_harness = MeshHarness()
        slow_source = slow_harness.attach_nmc(
            0,
            NMCChannelConfig(tx_bytes_per_cycle=1.0),
        )
        self.assertEqual(
            slow_source.endpoint_setup_residual_aci_cycles(
                NMCShapeMode.STATIC
            ),
            0.0,
        )

    def test_nmc_pipelined_shape_setup_preserves_command_order(self):
        harness = MeshHarness()
        source = harness.attach_nmc(
            0,
            NMCChannelConfig(descriptor_issue_cycles=13.0),
        )
        destination = harness.attach_nmc(1)
        dynamic_message = harness.message(153, 0, 1, 1)
        static_message = harness.message(154, 0, 1, 1).model_copy(
            update={"nmc_shape_mode": NMCShapeMode.STATIC}
        )
        received = []

        def receive_messages():
            for _ in range(2):
                received.append((yield destination.recv_flit()))

        send_processes = (
            source.send(dynamic_message),
            source.send(static_message),
        )
        receive_process = harness.env.process(receive_messages())
        harness.env.run(
            until=harness.env.all_of((*send_processes, receive_process))
        )

        injection_events = [
            event
            for event in harness.tracer.events
            if event.action is FlitAction.INJECT
            and event.msg_id in (153, 154)
        ]
        self.assertEqual(
            [event.msg_id for event in injection_events],
            [153, 154],
        )
        self.assertEqual([flit.msg_id for flit in received], [153, 154])
        self.assertAlmostEqual(
            injection_events[0].time,
            125.0 - source.post_injection_endpoint_completion_aci_cycles,
        )
        self.assertGreater(injection_events[1].time, injection_events[0].time)

    def test_router_burst_default_resolves_to_hardware_burst_len_7(self):
        explicit_quanta = {
            BurstLenMode.BURST_LEN_0: 1,
            BurstLenMode.BURST_LEN_1: 2,
            BurstLenMode.BURST_LEN_3: 4,
            BurstLenMode.BURST_LEN_7: 8,
        }
        configured = RouterConfig()
        self.assertIs(
            configured.default_burst_len_mode,
            BurstLenMode.BURST_LEN_7,
        )
        for mode, expected_quantum in explicit_quanta.items():
            with self.subTest(explicit_mode=mode):
                self.assertEqual(
                    configured.resolve_burst_quantum_flits(mode),
                    expected_quantum,
                )
        self.assertEqual(
            configured.resolve_burst_quantum_flits(
                BurstLenMode.BURST_LEN_DEFAULT
            ),
            8,
        )

        for contradictory_mode in (
            BurstLenMode.BURST_LEN_DEFAULT,
            BurstLenMode.BURST_LEN_0,
            BurstLenMode.BURST_LEN_1,
            BurstLenMode.BURST_LEN_3,
        ):
            with self.subTest(
                contradictory_mode=contradictory_mode
            ), self.assertRaisesRegex(
                ValidationError,
                "must resolve to BURST_LEN_7",
            ):
                RouterConfig(default_burst_len_mode=contradictory_mode)
        with self.assertRaises(ValidationError):
            RouterConfig.model_validate({"default_burst_len_mode": 2})

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
        self.assertEqual(
            config.core.nmc.shape_timing.static_endpoint_setup_aci_cycles,
            79.5,
        )
        self.assertEqual(
            config.core.nmc.shape_timing.dynamic_endpoint_setup_aci_cycles,
            125.0,
        )

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
                for flit_index, flit in enumerate(flits):
                    message.validate_flit(
                        flit,
                        flit_index,
                        len(flits),
                    )
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

                invalid_flit = flits[-1].model_copy(update={"sync_mode": 1})
                with self.assertRaisesRegex(RuntimeError, "invalid flit"):
                    message.validate_flit(
                        invalid_flit,
                        len(flits) - 1,
                        len(flits),
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
            dma_command_mode=DMACommandMode.DUAL_SIDE,
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
            dma_command_mode=DMACommandMode.DUAL_SIDE,
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
            with self.subTest(
                expected_error=expected_error
            ), self.assertRaisesRegex(ValueError, expected_error):
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
        for previous, current in pairwise(recovered_arrivals):
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
        for (_, previous), (_, current) in pairwise(slow_arrivals):
            self.assertAlmostEqual(
                current - previous,
                2.0 * config.launch_interval_aci_cycles,
            )

        link.scale_link_delay(0.5)
        normal_start, normal_arrivals = transfer(range(33, 36))
        self.assertEqual([msg_id for msg_id, _ in normal_arrivals], [33, 34, 35])
        self.assertAlmostEqual(normal_arrivals[0][1] - normal_start, 4.5)
        for (_, previous), (_, current) in pairwise(normal_arrivals):
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
        self.assertFalse(router._switch_grants)
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
        self.assertNotIn(PORT_PE, router.output_arbiters)

    def test_trace_records_distinguish_identical_fabric_local_ids(self):
        fabric_timings = {}
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
            summary = harness.tracer.summary(harness.env.now)
            self.assertIn(f"fabric={fabric_id.name}", summary)
            self.assertIn("timebase=aci_cycles", summary)
            self.assertIn("avg_first_flit_fabric_latency_aci_cycles=", summary)
            self.assertIn(
                "avg_packet_fabric_completion_latency_aci_cycles=",
                summary,
            )
            fabric_timings.update(harness.tracer.message_fabric_timings())
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
            set(fabric_timings),
            {(NoCChannel.CH0, 71), (NoCChannel.CH1, 71)},
        )
        self.assertTrue(
            all(
                timing.fabric_id is fabric_id
                and timing.msg_id == 71
                for (fabric_id, _), timing in fabric_timings.items()
            )
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

    def test_tracer_separates_first_flit_and_packet_fabric_latency(self):
        harness = MeshHarness()
        message = harness.message(72, 0, 2, 3)
        harness.transfer(0, 2, message.packetize())

        message_key = (NoCChannel.CH0, 72)
        timing = harness.tracer.message_fabric_timings()[message_key]
        injection_times = [
            event.time
            for event in harness.tracer.events
            if event.msg_id == 72 and event.action is FlitAction.INJECT
        ]
        ejection_times = [
            event.time
            for event in harness.tracer.events
            if event.msg_id == 72 and event.action is FlitAction.EJECT
        ]

        self.assertIsInstance(timing, MessageFabricTiming)
        self.assertEqual(
            timing.first_injection_time_aci_cycles,
            injection_times[0],
        )
        self.assertEqual(
            timing.first_ejection_time_aci_cycles,
            ejection_times[0],
        )
        self.assertEqual(
            timing.final_ejection_time_aci_cycles,
            ejection_times[-1],
        )
        self.assertLess(
            timing.first_flit_fabric_latency_aci_cycles,
            timing.packet_fabric_completion_latency_aci_cycles,
        )
        self.assertEqual(
            harness.tracer.first_flit_fabric_latencies()[message_key],
            timing.first_flit_fabric_latency_aci_cycles,
        )
        self.assertEqual(
            harness.tracer.packet_fabric_completion_latencies()[message_key],
            timing.packet_fabric_completion_latency_aci_cycles,
        )
        self.assertEqual(
            harness.tracer.per_msg_latency()[message_key],
            timing.packet_fabric_completion_latency_aci_cycles,
        )

        partial_tracer = NoCTracer(NoCChannel.CH0)
        partial_flit = harness.flit(FlitType.HEAD, 73, 0, 2)
        partial_tracer.log(0.0, FlitAction.INJECT, flit=partial_flit)
        partial_tracer.log(1.0, FlitAction.EJECT, flit=partial_flit)
        self.assertNotIn(
            (NoCChannel.CH0, 73),
            partial_tracer.message_fabric_timings(),
        )

    def test_single_flit_latency_for_one_to_ten_hops(self):
        one_way_hop_slope = (
            RB54_LATENCY_REFERENCE.rtt_hop_slope_aci_cycles / 2
        )
        for hops in range(1, 11):
            x = min(3, hops)
            y = hops - x
            dst = y * 4 + x
            harness = MeshHarness()
            flit = harness.flit(FlitType.SINGLE, hops, 0, dst)
            arrivals = harness.transfer(0, dst, [flit])
            self.assertAlmostEqual(
                arrivals[0][1],
                one_way_hop_slope * hops + 13.0,
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

    def test_round_robin_arbiter_rotates_across_three_requesters(self):
        env = simpy.Environment()
        arbiter = RoundRobinArbiter(env)
        grant_order = []

        def contender(input_port):
            for _ in range(4):
                yield arbiter.request(input_port)
                grant_order.append(input_port)
                yield env.timeout(0)
                arbiter.release(input_port)

        for input_port in (10, 20, 30):
            env.process(contender(input_port))
        env.run()

        self.assertEqual(grant_order, [10, 20, 30] * 4)
        self.assertIsNone(arbiter.owner)
        self.assertFalse(arbiter.pending_ports)

    def test_round_robin_arbiter_wraps_and_rejects_invalid_ownership(self):
        env = simpy.Environment()
        arbiter = RoundRobinArbiter(env)

        owner_request = arbiter.request(20)
        self.assertTrue(owner_request.triggered)
        self.assertEqual(arbiter.owner, 20)
        with self.assertRaisesRegex(RuntimeError, "already owns"):
            arbiter.request(20)

        wrapped_request = arbiter.request(10)
        next_request = arbiter.request(30)
        self.assertFalse(wrapped_request.triggered)
        self.assertFalse(next_request.triggered)
        self.assertEqual(arbiter.pending_ports, (10, 30))
        with self.assertRaisesRegex(RuntimeError, "already owns or awaits"):
            arbiter.request(10)
        with self.assertRaisesRegex(RuntimeError, "cannot release owner 20"):
            arbiter.release(10)
        self.assertEqual(arbiter.owner, 20)
        self.assertEqual(arbiter.pending_ports, (10, 30))

        arbiter.release(20)
        self.assertTrue(next_request.triggered)
        self.assertFalse(wrapped_request.triggered)
        self.assertEqual(arbiter.owner, 30)
        arbiter.release(30)
        self.assertTrue(wrapped_request.triggered)
        self.assertEqual(arbiter.owner, 10)
        arbiter.release(10)
        self.assertIsNone(arbiter.owner)
        self.assertFalse(arbiter.pending_ports)
        with self.assertRaisesRegex(RuntimeError, "cannot release owner None"):
            arbiter.release(10)

    def test_router_rejects_invalid_packet_state_transitions(self):
        harness = MeshHarness()
        router = harness.noc.routers[0]

        defaulted = harness.flit(
            FlitType.SINGLE,
            70,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_DEFAULT,
        )
        default_route = router._rc_compute(PORT_PE, defaulted)
        self.assertEqual(default_route.burst_quantum_flits, 8)
        del router.reservation[(PORT_PE, defaulted.msg_id)]
        self.assertFalse(router.reservation)

        body_without_head = harness.flit(FlitType.BODY, 71, 0, 1)
        with self.assertRaisesRegex(RuntimeError, "BODY without HEAD"):
            router._rc_compute(PORT_PE, body_without_head)
        self.assertFalse(router.reservation)

        head = harness.flit(
            FlitType.HEAD,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_3,
        )
        route_state = router._rc_compute(PORT_PE, head)
        self.assertEqual(route_state.burst_quantum_flits, 4)

        interleaved_head = harness.flit(
            FlitType.HEAD,
            73,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_3,
        )
        interleaved_route_state = router._rc_compute(
            PORT_PE,
            interleaved_head,
        )
        self.assertIs(
            router.reservation[(PORT_PE, interleaved_head.msg_id)],
            interleaved_route_state,
        )

        wrong_message = harness.flit(
            FlitType.BODY,
            74,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_3,
        )
        with self.assertRaisesRegex(RuntimeError, "BODY without HEAD"):
            router._rc_compute(PORT_PE, wrong_message)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        changed_mode = harness.flit(
            FlitType.BODY,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_1,
        )
        with self.assertRaisesRegex(RuntimeError, "changed burst mode"):
            router._rc_compute(PORT_PE, changed_mode)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        duplicate_head = harness.flit(
            FlitType.HEAD,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_3,
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate HEAD"):
            router._rc_compute(PORT_PE, duplicate_head)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        valid_body = harness.flit(
            FlitType.BODY,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_3,
        )
        self.assertIs(router._rc_compute(PORT_PE, valid_body), route_state)

    def test_router_default_mode_uses_hardware_burst_len_7(self):
        harness = MeshHarness()
        flits = self._packet(
            harness,
            msg_id=76,
            src=0,
            dst=1,
            flit_count=10,
            burst_len_mode=BurstLenMode.BURST_LEN_DEFAULT,
        )
        harness.transfer(0, 1, flits)
        for router_id in (0, 1):
            releases = [
                event.grant_flits
                for event in harness.tracer.events
                if event.action is FlitAction.ROUTER_SA_RELEASE
                and event.router_id == router_id
                and event.msg_id == 76
            ]
            self.assertEqual(releases, [8, 2])
            router = harness.noc.routers[router_id]
            self.assertFalse(router.reservation)
            self.assertFalse(router._switch_grants)

    def test_router_releases_grants_at_explicit_burst_boundaries(self):
        expected_grants = {
            BurstLenMode.BURST_LEN_0: [1] * 10,
            BurstLenMode.BURST_LEN_1: [2] * 5,
            BurstLenMode.BURST_LEN_3: [4, 4, 2],
            BurstLenMode.BURST_LEN_7: [8, 2],
        }
        for mode, expected_lengths in expected_grants.items():
            with self.subTest(mode=mode):
                harness = MeshHarness()
                flits = self._packet(
                    harness,
                    msg_id=42,
                    src=0,
                    dst=1,
                    flit_count=10,
                    burst_len_mode=mode,
                )
                arrivals = harness.transfer(0, 1, flits)
                for router_id in (0, 1):
                    release_events = [
                        event
                        for event in harness.tracer.events
                        if event.action is FlitAction.ROUTER_SA_RELEASE
                        and event.router_id == router_id
                        and event.msg_id == 42
                    ]
                    self.assertEqual(
                        [event.grant_flits for event in release_events],
                        expected_lengths,
                    )
                    self.assertEqual(
                        sum(event.grant_flits for event in release_events),
                        len(flits),
                    )
                arrival_times = [time for _, time in arrivals]
                for previous, current in pairwise(arrival_times):
                    self.assertAlmostEqual(
                        current - previous,
                        harness.config.link.launch_interval_aci_cycles,
                    )
                for router_id in (0, 1):
                    router = harness.noc.routers[router_id]
                    self.assertFalse(router.reservation)
                    self.assertFalse(router._switch_grants)

    def test_output_credit_stall_does_not_consume_burst_grant(self):
        harness = MeshHarness()
        harness.attach(0)
        harness.attach(1)
        router = harness.noc.routers[0]
        output_link = router.port_out[DIR_EAST]
        assert output_link is not None

        drain_credits = output_link.in_flight_credits.get(
            output_link.in_flight_credits.capacity
        )
        harness.env.run(until=drain_credits)
        flit = harness.flit(
            FlitType.SINGLE,
            77,
            0,
            1,
            burst_len_mode=BurstLenMode.BURST_LEN_0,
        )
        received = []

        def sender():
            yield harness.endpoints[0][0].send_flit(flit)

        def receiver():
            received_flit = yield harness.endpoints[1][1].recv_flit()
            received.append(received_flit)
            harness.endpoints[1][1].ack_credit()

        sender_process = harness.env.process(sender())
        receiver_process = harness.env.process(receiver())
        harness.env.run(until=12.0)

        grant_state = router._switch_grants[PORT_PE]
        self.assertEqual(grant_state.transmitted_flits, 0)
        self.assertEqual(router.reservation[(PORT_PE, flit.msg_id)].msg_id, 77)
        self.assertEqual(router.output_arbiters[DIR_EAST].owner, PORT_PE)
        self.assertFalse(
            any(
                event.action is FlitAction.ROUTER_SA_RELEASE
                and event.router_id == 0
                and event.msg_id == 77
                for event in harness.tracer.events
            )
        )
        self.assertTrue(
            any(
                event.action is FlitAction.STALL_CREDIT
                and event.link_name == output_link.link_name
                for event in harness.tracer.events
            )
        )

        output_link.ack_credit()
        harness.env.run(until=receiver_process)
        harness.env.run()

        self.assertTrue(sender_process.triggered)
        self.assertEqual([item.msg_id for item in received], [77])
        self.assertFalse(router.reservation)
        self.assertFalse(router._switch_grants)
        self.assertIsNone(router.output_arbiters[DIR_EAST].owner)
        releases = [
            event.grant_flits
            for event in harness.tracer.events
            if event.action is FlitAction.ROUTER_SA_RELEASE
            and event.router_id == 0
            and event.msg_id == 77
        ]
        self.assertEqual(releases, [1])

    def test_competing_packets_rearbitrate_at_their_own_burst_boundaries(self):
        harness = MeshHarness()
        for router_id in (2, 7, 3):
            harness.attach(router_id)
        flows = {
            50: self._packet(
                harness,
                msg_id=50,
                src=2,
                dst=3,
                flit_count=8,
                burst_len_mode=BurstLenMode.BURST_LEN_1,
            ),
            51: self._packet(
                harness,
                msg_id=51,
                src=7,
                dst=3,
                flit_count=8,
                burst_len_mode=BurstLenMode.BURST_LEN_3,
            ),
        }
        received = []
        arrival_times = []

        def sender(src, flits):
            for flit in flits:
                yield harness.endpoints[src][0].send_flit(flit)

        def receiver():
            output_link = harness.endpoints[3][1]
            for _ in range(16):
                flit = yield output_link.recv_flit()
                received.append(flit)
                arrival_times.append(harness.env.now)
                output_link.ack_credit()

        harness.env.process(sender(2, flows[50]))
        harness.env.process(sender(7, flows[51]))
        done = harness.env.process(receiver())
        harness.env.run(until=done)

        releases = [
            event
            for event in harness.tracer.events
            if event.action is FlitAction.ROUTER_SA_RELEASE
            and event.router_id == 3
            and event.out_port == PORT_PE
        ]
        contended_releases = releases[:4]
        self.assertEqual(len(contended_releases), 4)
        self.assertNotEqual(
            contended_releases[0].msg_id,
            contended_releases[1].msg_id,
        )
        self.assertEqual(
            [event.msg_id for event in contended_releases[:2]],
            [event.msg_id for event in contended_releases[2:]],
        )
        expected_quantum = {50: 2, 51: 4}
        self.assertEqual(
            [event.grant_flits for event in contended_releases],
            [expected_quantum[event.msg_id] for event in contended_releases],
        )
        for msg_id, flits in flows.items():
            self.assertEqual(
                [flit.flit_type for flit in received if flit.msg_id == msg_id],
                [flit.flit_type for flit in flits],
            )
        for previous, current in pairwise(arrival_times):
            self.assertAlmostEqual(
                current - previous,
                harness.config.link.launch_interval_aci_cycles,
            )
        self.assertFalse(harness.noc.routers[3].reservation)
        self.assertFalse(harness.noc.routers[3]._switch_grants)

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
            self.assertFalse(router._switch_grants)

    @staticmethod
    def _packet(
        harness,
        *,
        msg_id,
        src,
        dst,
        flit_count,
        burst_len_mode,
    ):
        flits = []
        for index in range(flit_count):
            if flit_count == 1:
                flit_type = FlitType.SINGLE
            elif index == 0:
                flit_type = FlitType.HEAD
            elif index == flit_count - 1:
                flit_type = FlitType.TAIL
            else:
                flit_type = FlitType.BODY
            flits.append(
                harness.flit(
                    flit_type,
                    msg_id,
                    src,
                    dst,
                    burst_len_mode=burst_len_mode,
                )
            )
        return flits

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
