"""Transport regression tests using synthetic, explicitly configurable devices."""

import math
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import Mock

import simpy
from pydantic import ValidationError

from simulator_detailed.architecture import Arch
from simulator_detailed.configs.schemas.arch_config import (
    ArchConfig,
    CoreConfig,
    DMAEngineConfig,
    DMAType,
    FlitConfig,
    LinkConfig,
    NMCChannelConfig,
    NMCShapeTimingConfig,
    NoCConfig,
    RouterConfig,
)
from simulator_detailed.endpoint_registry import EndpointRegistry
from simulator_detailed.noc import FlitAction, Link, NoC, NoCTracer, RoundRobinArbiter
from simulator_detailed.pe_channel import NMCChannel, PEChannelBinding
from simulator_detailed.predictor.topology import Mesh, parse_fabric_id
from simulator_detailed.tracing import collect_noc_link_events
from simulator_detailed.utils.definitions import (
    DIR_EAST,
    BurstLenMode,
    DimSlice,
    DMAAttachmentMode,
    Flit,
    FlitType,
    Message,
    NMCShapeMode,
    NoCChannel,
    NodeType,
    TransType,
    compute_flit_count,
)

FLIT_BYTES = FlitConfig().payload_capacity_bytes
PORT_PE = NoCConfig().pe_local_port


def build_runtime(config, core_config=None):
    env = simpy.Environment()
    arch = object.__new__(Arch)
    arch.env = env
    arch.x_size, arch.y_size = config.x, config.y
    arch.endpoint_registry = EndpointRegistry(config)
    arch.nocs = Arch.build_nocs(env, config)
    arch.dma_endpoints = arch.build_dma_endpoints(env, config)
    mapper = Mock()
    mapper.all_tasks_completed.return_value = True
    cores = arch.build_cores(env, core_config or CoreConfig(), config, mapper)
    return env, arch, cores


class MeshHarness:
    def __init__(
        self,
        fabric_id=NoCChannel.CH0,
        config=None,
        env=None,
    ):
        self.env = env if env is not None else simpy.Environment()
        self.config = config or NoCConfig(x=4, y=2)
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

    @property
    def hop_latency_aci_cycles(self):
        pipeline = self.config.router.pipeline
        link = self.noc.r2r_links[0]
        return (
            pipeline.effective_rc_aci_cycles
            + pipeline.effective_sa_aci_cycles
            + pipeline.effective_st_aci_cycles
            + link.serialization_aci_cycles
            + link.effective_link_stage_aci_cycles
        )

    def attach(self, router_id):
        c2r = Link(
            self.env,
            self.config.c2r_link,
            self.fabric_id,
            self.tracer,
            f"PE{router_id}->R{router_id}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
            flit_format=self.config.router.flit,
        )
        r2c = Link(
            self.env,
            self.config.c2r_link,
            self.fabric_id,
            self.tracer,
            f"R{router_id}->PE{router_id}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
            flit_format=self.config.router.flit,
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
        payload=FLIT_BYTES,
        burst_len_mode=BurstLenMode.FLITS_8,
    ):
        return Flit(
            format=self.config.router.flit,
            mesh_x=self.config.x,
            mesh_y=self.config.y,
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
    def test_json_example_runs_with_custom_fabric_overrides(self):
        path = Path(__file__).parents[1] / "configs/instances/mesh_example.json"
        config = ArchConfig.model_validate_json(path.read_text())
        config = ArchConfig.model_validate_json(config.model_dump_json())
        custom_fabric = NoCChannel(2)
        self.assertIs(config.noc.fabric_ids[-1], custom_fabric)
        for endpoint in config.noc.dma_engines:
            self.assertIs(endpoint.fabric_ids[-1], custom_fabric)
        self.assertIs(config.noc.router.default_burst_len_mode, BurstLenMode(3))
        env, arch, cores = build_runtime(config.noc, config.core)
        operations = []
        for fabric in config.noc.fabric_ids:
            source = cores[0].nmc_channel_for(fabric)
            target = cores[-1].nmc_channel_for(fabric)
            message = Message(
                src=source.binding.address,
                dst=target.binding.address,
                index=int(fabric),
                data=[DimSlice(start=0, end=29)],
            )
            operations.extend(
                (
                    target.recv_message(message, NMCShapeMode.DYNAMIC),
                    source.send(message),
                )
            )
        self.assertEqual(
            cores[0].nmc_channel_for(NoCChannel(2)).config.tx_bytes_per_cycle, 3
        )
        self.assertEqual(
            cores[0].nmc_channel_for(NoCChannel.CH0).config.tx_bytes_per_cycle, 5
        )
        env.run(until=env.all_of(operations))
        self.assertTrue(all(operation.triggered for operation in operations))
        env.run()

    def test_binding_rejects_incompatible_configuration(self):
        harness = MeshHarness()
        channel = harness.attach_nmc(0)
        binding = channel.binding
        for updates in (
            {"format": FlitConfig(physical_flit_bytes=7, payload_capacity_bytes=7)},
            {"mesh_x": 20},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                replace(binding, address=binding.address.model_copy(update=updates))

    def test_invalid_configuration_fails_before_simulation(self):
        for model, values in (
            (NoCConfig, {"aci_clock_mhz": float("inf")}),
            (LinkConfig, {"launch_interval_aci_cycles": float("nan")}),
            (NMCChannelConfig, {"tx_bytes_per_cycle": float("inf")}),
            (RouterConfig, {"unknown_option": 1}),
        ):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                model.model_validate(values)

    @staticmethod
    def _packet(harness, msg_id, src, dst, flit_count, burst_len_mode):
        message = harness.message(msg_id, src, dst, flit_count)
        message.burst_len_mode = burst_len_mode
        return message.packetize()

    def test_packet_format_boundaries_and_round_trip(self):
        for capacity in (3, 11, 24):
            format = FlitConfig(
                physical_flit_bytes=capacity + 2,
                payload_capacity_bytes=capacity,
                header_bytes=2,
            )
            config = NoCConfig(router=RouterConfig(flit=format))
            registry = EndpointRegistry(config)
            for size in (0, 1, capacity - 1, capacity, capacity + 1, capacity * 3):
                with self.subTest(capacity=capacity, payload=size):
                    message = Message(
                        src=registry.resolve(NodeType.PE, 0, fabric_id=NoCChannel.CH0),
                        dst=registry.resolve(NodeType.PE, 5, fabric_id=NoCChannel.CH0),
                        index=size,
                        data=[DimSlice(start=0, end=size)],
                    )
                    flits = message.packetize()
                    self.assertEqual(len(flits), max(1, math.ceil(size / capacity)))
                    self.assertEqual(sum(f.payload_bytes for f in flits), size)
                    for i, flit in enumerate(flits):
                        self.assertEqual(flit.transfer_bytes, capacity + 2)
                        self.assertEqual(
                            Flit.model_validate_json(flit.model_dump_json()), flit
                        )
                        message.validate_flit(flit, i, len(flits))

    def test_invalid_packet_formats(self):
        for fields in (
            {"physical_flit_bytes": 0},
            {"payload_capacity_bytes": 0},
            {"physical_flit_bytes": 2, "payload_capacity_bytes": 3},
            {"header_bytes": 100},
            {"physical_flit_bytes": 1.5},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                FlitConfig(**fields)
        for size, capacity in ((-1, 3), (1, 0)):
            with self.assertRaises(ValueError):
                compute_flit_count(size, capacity)

    def test_mesh_fabrics_and_ports_are_selected_by_config(self):
        for x, y, ids, port in (
            (1, 1, (NoCChannel.CH1,), 7),
            (2, 3, (NoCChannel.CH0,), 41),
            (3, 2, (NoCChannel.CH0, NoCChannel.CH1, NoCChannel(5)), 9),
        ):
            with self.subTest(shape=(x, y), fabrics=ids):
                config = NoCConfig(x=x, y=y, fabric_ids=ids, pe_local_port=port)
                env, arch, cores = build_runtime(config)
                self.assertEqual(set(arch.nocs), set(ids))
                self.assertEqual(len(cores), x * y)
                for core in cores:
                    self.assertEqual(set(core.nmc_channels), set(ids))
                    for channel in core.nmc_channels.values():
                        self.assertEqual(channel.binding.address.local_port, port)
                events, identities = collect_noc_link_events(arch.nocs)
                mesh = Mesh(x, y, ids)
                self.assertEqual(len(events), mesh.link_count)
                self.assertEqual(
                    len(identities), len(ids) * 2 * ((x - 1) * y + (y - 1) * x)
                )
                env.run()

    def test_configured_packet_sizes_execute_without_global_state(self):
        runtimes = []
        for capacity, clock_ratio in ((5, 1.5), (21, 3.0)):
            format = FlitConfig(
                physical_flit_bytes=capacity, payload_capacity_bytes=capacity
            )
            link = LinkConfig(
                payload_bits_per_noc_cycle=8,
                wire_bits_per_noc_cycle=8,
                launch_interval_aci_cycles=capacity,
            )
            config = NoCConfig(
                router=RouterConfig(flit=format),
                link=link,
                c2r_link=link,
                noc_clock_mhz=clock_ratio,
            )
            env, arch, cores = build_runtime(config)
            source = cores[0].nmc_channel_for(NoCChannel.CH0)
            target = cores[5].nmc_channel_for(NoCChannel.CH0)
            message = Message(
                src=source.binding.address,
                dst=target.binding.address,
                index=1,
                data=[DimSlice(start=0, end=capacity * 2 + 1)],
            )
            rx = target.recv_message(message, NMCShapeMode.STATIC)
            tx = source.send(message)
            runtimes.append((env, arch, rx, tx, capacity))
        for env, arch, rx, tx, capacity in reversed(runtimes):
            env.run(until=env.all_of((rx, tx)))
            self.assertEqual(
                [f.payload_bytes for f in rx.value.flits], [capacity, capacity, 1]
            )
            self.assertTrue(all(f.transfer_bytes == capacity for f in tx.value.flits))
            env.run()

    def test_mismatched_format_is_rejected_before_admission(self):
        harness = MeshHarness()
        harness.attach(0)
        flit = harness.flit(FlitType.SINGLE, 1, 0, 1)
        incompatible = flit.model_copy(
            update={
                "format": FlitConfig(physical_flit_bytes=7, payload_capacity_bytes=7)
            }
        )
        with self.assertRaisesRegex(ValueError, "formats"):
            harness.endpoints[0][0].send_flit(incompatible)
        self.assertFalse(harness.tracer.events)
        bad = harness.message(2, 0, 1, 1).model_dump()
        bad["dst"]["format"]["physical_flit_bytes"] = 23
        with self.assertRaises(ValidationError):
            Message.model_validate(bad)

    def test_fixed_path_uses_configured_geometry(self):
        for x, y, path in ((3, 2, (0, 1, 4, 5)), (2, 3, (0, 2, 4, 5))):
            registry = EndpointRegistry(NoCConfig(x=x, y=y))
            fields = dict(
                src=registry.resolve(NodeType.PE, 0, fabric_id=NoCChannel.CH0),
                dst=registry.resolve(NodeType.PE, 5, fabric_id=NoCChannel.CH0),
                index=1,
                data=[],
                trans_type=TransType.FIXPATH,
            )
            msg = Message(**fields, fixed_path=path)
            with self.assertRaises(NotImplementedError):
                msg.packetize()
            for bad in ((0, 5), (0, 1, 0, 5), (0, x * y, 5)):
                with self.assertRaises(ValidationError):
                    Message(**fields, fixed_path=bad)

    def test_configurable_burst_quantum(self):
        for quantum in (1, 3, 6, 11):
            mode = BurstLenMode(quantum)
            config = RouterConfig(default_burst_len_mode=mode)
            self.assertEqual(
                config.resolve_burst_quantum_flits(BurstLenMode.DEFAULT), quantum
            )
            self.assertEqual(config.resolve_burst_quantum_flits(BurstLenMode(2)), 2)
        with self.assertRaises(ValidationError):
            RouterConfig(default_burst_len_mode=BurstLenMode.DEFAULT)
        self.assertEqual(parse_fabric_id("CH5"), NoCChannel(5))

    def test_link_serialization_and_buffer_configuration(self):
        format = FlitConfig(physical_flit_bytes=13, payload_capacity_bytes=11)
        config = LinkConfig(
            payload_bits_per_noc_cycle=24,
            wire_bits_per_noc_cycle=27,
            input_buffer_depth_flits=3,
            launch_interval_aci_cycles=5,
        )
        self.assertEqual(config.serialization_noc_cycles(format), 5)
        self.assertEqual(config.serialization_aci_cycles(2.5, format), 2)
        env = simpy.Environment()
        link = Link(
            env,
            config,
            NoCChannel.CH0,
            NoCTracer(NoCChannel.CH0),
            noc_cycles_per_aci_cycle=2.5,
            flit_format=format,
        )
        self.assertEqual(link.flit_buffer.capacity, 3)
        with self.assertRaises(ValueError):
            Link(
                env,
                config.model_copy(update={"launch_interval_aci_cycles": 1}),
                NoCChannel.CH0,
                NoCTracer(NoCChannel.CH0),
                noc_cycles_per_aci_cycle=2.5,
                flit_format=format,
            )

    def test_registry_rejects_collisions_and_out_of_bounds(self):
        for fields in (
            {"x": 0},
            {"fabric_ids": ()},
            {"fabric_ids": (NoCChannel.CH0, NoCChannel.CH0)},
        ):
            with self.assertRaises(ValidationError):
                NoCConfig(**fields)
        dma = DMAEngineConfig(
            dma_type=DMAType.GM_RDMA,
            instance_id=17,
            router_id=2,
            local_ports=[0],
            attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
        )
        with self.assertRaisesRegex(ValueError, "shared"):
            EndpointRegistry(NoCConfig(dma_engines=[dma]))
        with self.assertRaises(ValidationError):
            NoCConfig(dma_engines=[dma.model_copy(update={"router_id": 99})])

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
            burst_len_mode=BurstLenMode.DEFAULT,
        )
        default_route = router._rc_compute(PORT_PE, defaulted)
        self.assertEqual(
            default_route.burst_quantum_flits,
            harness.config.router.default_burst_len_mode.value,
        )
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
            burst_len_mode=BurstLenMode.FLITS_4,
        )
        route_state = router._rc_compute(PORT_PE, head)
        self.assertEqual(route_state.burst_quantum_flits, 4)

        interleaved_head = harness.flit(
            FlitType.HEAD,
            73,
            0,
            1,
            burst_len_mode=BurstLenMode.FLITS_4,
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
            burst_len_mode=BurstLenMode.FLITS_4,
        )
        with self.assertRaisesRegex(RuntimeError, "BODY without HEAD"):
            router._rc_compute(PORT_PE, wrong_message)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        changed_mode = harness.flit(
            FlitType.BODY,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.FLITS_2,
        )
        with self.assertRaisesRegex(RuntimeError, "changed burst mode"):
            router._rc_compute(PORT_PE, changed_mode)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        duplicate_head = harness.flit(
            FlitType.HEAD,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.FLITS_4,
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate HEAD"):
            router._rc_compute(PORT_PE, duplicate_head)
        self.assertIs(router.reservation[(PORT_PE, head.msg_id)], route_state)

        valid_body = harness.flit(
            FlitType.BODY,
            72,
            0,
            1,
            burst_len_mode=BurstLenMode.FLITS_4,
        )
        self.assertIs(router._rc_compute(PORT_PE, valid_body), route_state)

    def test_router_releases_grants_at_explicit_burst_boundaries(self):
        expected_grants = {
            BurstLenMode.FLITS_1: [1] * 10,
            BurstLenMode.FLITS_2: [2] * 5,
            BurstLenMode.FLITS_4: [4, 4, 2],
            BurstLenMode.FLITS_8: [8, 2],
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
            burst_len_mode=BurstLenMode.FLITS_1,
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
        harness = MeshHarness(
            config=NoCConfig(
                x=4,
                y=2,
                link=LinkConfig(launch_interval_aci_cycles=3),
                c2r_link=LinkConfig(launch_interval_aci_cycles=3),
            )
        )
        for router_id in (2, 7, 3):
            harness.attach(router_id)
        flows = {
            50: self._packet(
                harness,
                msg_id=50,
                src=2,
                dst=3,
                flit_count=8,
                burst_len_mode=BurstLenMode.FLITS_2,
            ),
            51: self._packet(
                harness,
                msg_id=51,
                src=7,
                dst=3,
                flit_count=8,
                burst_len_mode=BurstLenMode.FLITS_4,
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


if __name__ == "__main__":
    unittest.main()
