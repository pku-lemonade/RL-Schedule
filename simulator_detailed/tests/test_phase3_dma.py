"""DMA protocol tests use synthetic endpoint maps, clocks and packet formats."""

import unittest

from pydantic import ValidationError

from simulator_detailed.configs.schemas.arch_config import (
    DMAEngineConfig,
    DMAType,
    FlitConfig,
    LinkConfig,
    NoCConfig,
    RouterConfig,
)
from simulator_detailed.configs.schemas.failure_configs import DMAFail
from simulator_detailed.tests.test_phase2_noc import build_runtime
from simulator_detailed.tracing import collect_dma_service_events
from simulator_detailed.utils.definitions import (
    BurstLenMode,
    DimSlice,
    DMAAttachmentMode,
    DMACommandMode,
    FlitTrafficType,
    Message,
    NMCShapeMode,
    NoCChannel,
    NodeType,
    TransType,
)

KINDS = (
    (DMAType.GM_RDMA, NodeType.GM_RDMA, True),
    (DMAType.GM_WDMA, NodeType.GM_WDMA, False),
    (DMAType.DDR_RDMA, NodeType.DDR_RDMA, True),
    (DMAType.DDR_WDMA, NodeType.DDR_WDMA, False),
)


def dma_runtime(
    kind,
    read,
    mode,
    *,
    capacity=7,
    physical=9,
    fabrics=(NoCChannel.CH0, NoCChannel.CH1),
    issuers=False,
    bandwidth=3.0,
    descriptor_slots=2,
):
    attachment = (
        DMAAttachmentMode.SINGLE_SIDE
        if read or mode is DMACommandMode.SINGLE_SIDE
        else DMAAttachmentMode.DUAL_SIDE
    )
    dma = DMAEngineConfig(
        dma_type=kind,
        instance_id=17,
        router_id=2,
        fabric_ids=fabrics,
        channels=len(fabrics),
        local_ports=[40 + i for i in range(len(fabrics))],
        attachment_mode=attachment,
        endpoint_clock_mhz=2.5,
        descriptor_issuers_per_fabric=issuers,
        port_bw=bandwidth,
        descriptor_issue_cycles=2,
        max_outstanding_descriptors_per_channel=descriptor_slots,
    )
    format = FlitConfig(
        physical_flit_bytes=physical, payload_capacity_bytes=capacity, header_bytes=2
    )
    link = LinkConfig(launch_interval_aci_cycles=2)
    config = NoCConfig(
        x=3,
        y=2,
        fabric_ids=fabrics,
        aci_clock_mhz=1.25,
        noc_clock_mhz=2.5,
        router=RouterConfig(flit=format, default_burst_len_mode=BurstLenMode(3)),
        link=link,
        c2r_link=link,
        pe_local_port=19,
        dma_engines=[dma],
    )
    return build_runtime(config)


def start_command(arch, cores, node_type, read, mode, fabric, size=22, index=1):
    endpoint = arch.dma_endpoints[node_type, 17]
    pe = cores[5].nmc_channel_for(fabric)
    address = endpoint.binding_for(fabric).address
    message = Message(
        src=address if read else pe.binding.address,
        dst=pe.binding.address if read else address,
        index=index,
        data=[DimSlice(start=0, end=size)],
        dma_command_mode=mode,
    )
    if read:
        receive = pe.recv_message(message, NMCShapeMode.DYNAMIC)
        operations = [receive]
        if mode is DMACommandMode.DUAL_SIDE:
            operations.append(endpoint.send(message))
    else:
        operations = [pe.send(message)]
        if mode is DMACommandMode.DUAL_SIDE:
            operations.append(endpoint.recv_message(message))
    return message, operations


class DMAConfigurationTests(unittest.TestCase):
    def assert_drained(self, arch, cores):
        self.assertEqual(arch.dma_commands.pending_dual_side_commands, 0)
        self.assertEqual(arch.dma_commands.pending_single_side_commands, 0)
        for endpoint in arch.dma_endpoints.values():
            self.assertEqual(endpoint.rx_data_queue.items, [])
            for resource in (
                endpoint.internal_datapath,
                *endpoint.descriptor_issuers.values(),
                *endpoint.descriptor_slots.values(),
            ):
                self.assertFalse(resource.users)
                self.assertFalse(resource.queue)
        for core in cores:
            for channel in core.nmc_channels.values():
                self.assertEqual(channel.outstanding_descriptor_count, 0)
        for noc in arch.nocs.values():
            for router in noc.routers:
                self.assertFalse(router.reservation)
                self.assertFalse(router._switch_grants)

    def test_all_directions_modes_and_fabrics_use_configured_packets(self):
        for kind, node_type, read in KINDS:
            for mode in DMACommandMode:
                for capacity, physical in ((7, 9), (19, 19)):
                    with self.subTest(kind=kind, mode=mode, capacity=capacity):
                        env, arch, cores = dma_runtime(
                            kind, read, mode, capacity=capacity, physical=physical
                        )
                        operations = []
                        messages = []
                        for fabric in arch.nocs:
                            message, tasks = start_command(
                                arch,
                                cores,
                                node_type,
                                read,
                                mode,
                                fabric,
                                size=capacity * 3 + 1,
                            )
                            operations.extend(tasks)
                            messages.append(message)
                        env.run(until=env.all_of(operations))
                        for task in operations:
                            self.assertEqual(
                                [f.payload_bytes for f in task.value.flits],
                                [capacity, capacity, capacity, 1],
                            )
                            self.assertTrue(
                                all(
                                    f.transfer_bytes == physical
                                    for f in task.value.flits
                                )
                            )
                        env.run()
                        events = [
                            event
                            for noc in arch.nocs.values()
                            for event in noc.tracer.events
                        ]
                        if mode is DMACommandMode.SINGLE_SIDE:
                            control_type = (
                                FlitTrafficType.DMA_REQUEST
                                if read
                                else FlitTrafficType.DMA_RESPONSE
                            )
                            self.assertTrue(
                                any(e.traffic_type is control_type for e in events)
                            )
                        self.assert_drained(arch, cores)

    def test_arbitrary_instance_router_ports_and_single_fabric(self):
        env, arch, cores = dma_runtime(
            DMAType.DDR_WDMA, False, DMACommandMode.DUAL_SIDE, fabrics=(NoCChannel(4),)
        )
        endpoint = arch.dma_endpoints[NodeType.DDR_WDMA, 17]
        binding = endpoint.binding_for(NoCChannel(4))
        self.assertEqual(
            (binding.address.router_id, binding.address.local_port), (2, 40)
        )
        self.assertEqual(endpoint.clock_domain.endpoint_cycles_per_aci_cycle, 2)
        message, tasks = start_command(
            arch,
            cores,
            NodeType.DDR_WDMA,
            False,
            DMACommandMode.DUAL_SIDE,
            NoCChannel(4),
        )
        env.run(until=env.all_of(tasks))
        self.assertEqual(len(tasks[-1].value.flits), message.flit_count())
        env.run()
        self.assert_drained(arch, cores)

    def test_descriptor_sharing_is_independent_of_memory_type(self):
        for kind, node_type, read in KINDS:
            for separate in (False, True):
                env, arch, _ = dma_runtime(
                    kind, read, DMACommandMode.DUAL_SIDE, issuers=separate
                )
                endpoint = arch.dma_endpoints[node_type, 17]
                first, second = endpoint.descriptor_issuers.values()
                self.assertEqual(first is second, not separate)
                env.run()

    def test_dual_side_source_waits_for_destination(self):
        env, arch, cores = dma_runtime(DMAType.GM_RDMA, True, DMACommandMode.DUAL_SIDE)
        endpoint = arch.dma_endpoints[NodeType.GM_RDMA, 17]
        pe = cores[5].nmc_channel_for(NoCChannel.CH0)
        message = Message(
            src=endpoint.binding_for(NoCChannel.CH0).address,
            dst=pe.binding.address,
            index=1,
            data=[DimSlice(start=0, end=20)],
            dma_command_mode=DMACommandMode.DUAL_SIDE,
        )
        tx = endpoint.send(message)
        env.run(until=10)
        self.assertFalse(tx.triggered)
        self.assertFalse(endpoint.service_events)
        rx = pe.recv_message(message, NMCShapeMode.STATIC)
        env.run(until=env.all_of((tx, rx)))
        env.run()
        self.assert_drained(arch, cores)

    def test_fault_timing_is_configurable_and_recovers(self):
        for kind, node_type, read in KINDS:
            for mode in DMACommandMode:
                durations = []
                for factor in (1, 3):
                    env, arch, cores = dma_runtime(kind, read, mode, bandwidth=1.0)
                    endpoint = arch.dma_endpoints[node_type, 17]
                    endpoint.scale_service_delay(factor)
                    _, tasks = start_command(
                        arch, cores, node_type, read, mode, NoCChannel.CH0, size=43
                    )
                    env.run(until=env.all_of(tasks))
                    durations.append(env.now)
                    endpoint.scale_service_delay(1 / factor)
                    env.run()
                    self.assert_drained(arch, cores)
                self.assertGreater(durations[1], durations[0])

    def test_concurrent_commands_release_descriptor_capacity(self):
        for read, kind, node_type in (
            (True, DMAType.GM_RDMA, NodeType.GM_RDMA),
            (False, DMAType.GM_WDMA, NodeType.GM_WDMA),
        ):
            env, arch, cores = dma_runtime(
                kind, read, DMACommandMode.DUAL_SIDE, descriptor_slots=1
            )
            tasks = []
            for index in range(5):
                for fabric in arch.nocs:
                    _, operations = start_command(
                        arch,
                        cores,
                        node_type,
                        read,
                        DMACommandMode.DUAL_SIDE,
                        fabric,
                        size=15 + index,
                        index=index,
                    )
                    tasks.extend(operations)
            env.run(until=env.all_of(tasks))
            self.assertTrue(all(t.triggered for t in tasks))
            env.run()
            self.assert_drained(arch, cores)
            events = collect_dma_service_events(arch.dma_endpoints)
            self.assertTrue(events)

    def test_protocol_and_fixed_path_validation_precedes_admission(self):
        env, arch, cores = dma_runtime(DMAType.GM_WDMA, False, DMACommandMode.DUAL_SIDE)
        endpoint = arch.dma_endpoints[NodeType.GM_WDMA, 17]
        pe = cores[2].nmc_channel_for(NoCChannel.CH0)
        fields = dict(
            src=pe.binding.address,
            dst=endpoint.binding_for(NoCChannel.CH0).address,
            index=1,
            data=[],
        )
        with self.assertRaises(ValidationError):
            Message(**fields)
        with self.assertRaises(ValidationError):
            Message(**fields, dma_command_mode=DMACommandMode.SINGLE_SIDE)
        message = Message(
            **fields,
            dma_command_mode=DMACommandMode.DUAL_SIDE,
            trans_type=TransType.FIXPATH,
            fixed_path=(2,),
        )
        with self.assertRaises(NotImplementedError):
            pe.send(message)
        with self.assertRaises(NotImplementedError):
            endpoint.recv_message(message)
        env.run()
        self.assert_drained(arch, cores)

    def test_dma_config_validation_and_failure_ids_have_no_device_limit(self):
        base = dict(
            dma_type=DMAType.GM_RDMA,
            instance_id=17,
            router_id=2,
            local_ports=[40],
            attachment_mode=DMAAttachmentMode.SINGLE_SIDE,
        )
        for changed in (
            {"local_ports": []},
            {"channels": 2},
            {"local_ports": [-1]},
            {"endpoint_clock_mhz": 0},
            {"fabric_ids": ()},
            {"port_bw": 0},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                DMAEngineConfig(**(base | changed))
        fault = DMAFail(
            start_time=0,
            end_time=3,
            node_type=NodeType.GM_RDMA,
            instance_id=17,
            times=2,
        )
        self.assertEqual(fault.instance_id, 17)
        with self.assertRaises(ValidationError):
            DMAFail(
                start_time=3,
                end_time=2,
                node_type=NodeType.GM_RDMA,
                instance_id=17,
                times=2,
            )


if __name__ == "__main__":
    unittest.main()
