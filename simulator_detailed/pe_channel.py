from dataclasses import dataclass
from typing import Literal, cast

import simpy
from simpy.events import Event as SimpyEvent
from simpy.events import Process, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .command_coordination import PairedDMACommandCoordinator
from .configs.schemas.arch_config import NMCChannelConfig, NMCShapeTimingConfig
from .noc import Link, Router
from .utils.definitions import (
    FLIT_BYTES,
    DMAAttachmentMode,
    EndpointAddress,
    Flit,
    Message,
    NMCShapeMode,
    NoCChannel,
    NodeType,
    TransType,
)

_GM_RDMA_ZERO_HOP_OPERATION_LATENCY_ACI_CYCLES = 246.0
_GM_RDMA_OPERATION_HOP_SLOPE_ACI_CYCLES = 17.0


@dataclass(frozen=True)
class PEChannelBinding:
    """One PE's physical attachment to one data NoC fabric."""

    address: EndpointAddress
    tx_link: Link
    rx_link: Link
    router: Router

    def __post_init__(self) -> None:
        if self.address.node_type is not NodeType.PE:
            raise ValueError("PE channel binding requires a PE endpoint address")
        fabric_id = self.address.fabric_id
        if self.router.fabric_id is not fabric_id:
            raise ValueError(
                f"{fabric_id.name} PE address cannot bind {self.router.name}"
            )
        for link in (self.tx_link, self.rx_link):
            if link.fabric_id is not fabric_id:
                raise ValueError(
                    f"{fabric_id.name} PE address cannot bind {link.link_name}"
                )
            if link.tracer is not self.router.tracer:
                raise ValueError(
                    f"{link.link_name} and {self.router.name} use different tracers"
                )
        if self.tx_link is self.rx_link:
            raise ValueError("PE TX and RX must use distinct physical links")
        if self.router.id != self.address.router_id:
            raise ValueError(
                f"PE address maps to router {self.address.router_id}, "
                f"not router {self.router.id}"
            )


@dataclass(frozen=True)
class NMCTransmitEntry:
    """One packetized message waiting for the directional upload engine."""

    message: Message
    flits: tuple[Flit, ...]
    shape_mode: NMCShapeMode
    submission_time_aci_cycles: float
    descriptor_acceptance_time_aci_cycles: float
    endpoint_ready_time_aci_cycles: float
    destination_ready: SimpyEvent | None
    completion: SimpyEvent


@dataclass(frozen=True)
class NMCTransmitResult:
    """Endpoint-local SEND timing through final local Link handoff."""

    flits: tuple[Flit, ...]
    shape_mode: NMCShapeMode
    submission_time_aci_cycles: float
    descriptor_acceptance_time_aci_cycles: float
    endpoint_ready_time_aci_cycles: float
    final_local_handoff_time_aci_cycles: float
    operation_completion_time_aci_cycles: float

    @property
    def operation_latency_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.submission_time_aci_cycles
        )


@dataclass(frozen=True)
class NMCReceiveEntry:
    """One flit after completion of the directional download service."""

    flit: Flit
    rx_service_completion_time_aci_cycles: float


@dataclass(frozen=True)
class NMCReceiveResult:
    """One validated command-level receive completed after its TAIL flit."""

    message: Message
    flits: tuple[Flit, ...]
    shape_mode: NMCShapeMode
    submission_time_aci_cycles: float
    descriptor_acceptance_time_aci_cycles: float
    endpoint_ready_time_aci_cycles: float
    tail_rx_service_completion_time_aci_cycles: float
    operation_completion_time_aci_cycles: float

    @property
    def operation_latency_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.submission_time_aci_cycles
        )


class NMCChannel:
    """Runtime resources for one independent, full-duplex PE NMC channel."""

    def __init__(
        self,
        env: simpy.Environment,
        config: NMCChannelConfig,
        shape_timing: NMCShapeTimingConfig,
        binding: PEChannelBinding,
        paired_dma_commands: PairedDMACommandCoordinator | None = None,
    ) -> None:
        if any(
            component_env is not env
            for component_env in (
                binding.tx_link.env,
                binding.rx_link.env,
                binding.router.env,
            )
        ):
            raise ValueError(
                f"{binding.address.fabric_id.name} NMC channel and binding "
                "must use the same SimPy environment"
            )
        if (
            paired_dma_commands is not None
            and paired_dma_commands.env is not env
        ):
            raise ValueError(
                "NMC channel and paired command coordinator must use the same "
                "environment"
            )

        self.env = env
        self.config = config
        self.shape_timing = shape_timing
        self.binding = binding
        self.paired_dma_commands = paired_dma_commands
        self.tx_datapath = Resource(env, capacity=1)
        self.rx_datapath = Resource(env, capacity=1)
        self.descriptor_slots = Resource(
            env,
            capacity=config.max_outstanding_descriptors,
        )
        self.descriptor_issuer = Resource(env, capacity=1)

        # Hardware data-FIFO depths are unresolved and remain distinct from the
        # measured descriptor capacity enforced above.
        self.tx_data_queue = simpy.Store(env)
        self.rx_data_queue = simpy.FilterStore(env)
        self._receive_api_mode: Literal["flit", "message"] | None = None
        self._claimed_receive_ids: set[int] = set()
        self.env.process(self._tx_service_loop())
        self.env.process(self._rx_service_loop())

    @property
    def fabric_id(self) -> NoCChannel:
        return self.binding.address.fabric_id

    @property
    def tx_service_interval_aci_cycles(self) -> float:
        return FLIT_BYTES / self.config.tx_bytes_per_cycle

    @property
    def rx_service_interval_aci_cycles(self) -> float:
        return FLIT_BYTES / self.config.rx_bytes_per_cycle

    @property
    def outstanding_descriptor_count(self) -> int:
        return len(self.descriptor_slots.users)

    @property
    def first_injection_transport_aci_cycles(self) -> float:
        """Nominal TX and PE-link time from endpoint-ready to router injection."""
        return (
            self.tx_service_interval_aci_cycles
            + self.binding.tx_link.serialization_aci_cycles
            + self.binding.tx_link.effective_link_stage_aci_cycles
        )

    @property
    def post_injection_endpoint_completion_aci_cycles(self) -> float:
        """Fixed non-hop time from router injection through PE RX service."""
        pipeline = self.binding.router.config.pipeline
        return (
            pipeline.effective_rc_aci_cycles
            + pipeline.effective_sa_aci_cycles
            + pipeline.effective_st_aci_cycles
            + self.binding.rx_link.serialization_aci_cycles
            + self.binding.rx_link.effective_link_stage_aci_cycles
            + self.rx_service_interval_aci_cycles
        )

    def endpoint_setup_residual_aci_cycles(
        self,
        shape_mode: NMCShapeMode,
    ) -> float:
        """Return setup not represented through destination RX completion."""
        represented_cycles = (
            self.config.descriptor_issue_cycles
            + self.first_injection_transport_aci_cycles
            + self.post_injection_endpoint_completion_aci_cycles
        )
        residual_cycles = (
            self.shape_timing.endpoint_setup_target_aci_cycles(shape_mode)
            - represented_cycles
        )
        return max(0.0, residual_cycles)

    def receive_endpoint_setup_residual_aci_cycles(
        self,
        shape_mode: NMCShapeMode,
    ) -> float:
        """Return receive setup not represented by descriptor posting."""
        target_cycles = self.shape_timing.endpoint_setup_target_aci_cycles(
            shape_mode
        )
        return max(0.0, target_cycles - self.config.descriptor_issue_cycles)

    def minimum_receive_operation_latency_aci_cycles(
        self,
        message: Message,
    ) -> float:
        """Return a measured direction-specific completion floor, if known."""
        if message.src.node_type is not NodeType.GM_RDMA:
            return 0.0
        source_x, source_y = self.binding.router.to_xy(message.src.router_id)
        destination_x, destination_y = self.binding.router.to_xy(
            message.dst.router_id
        )
        hops = abs(destination_x - source_x) + abs(destination_y - source_y)
        return (
            _GM_RDMA_ZERO_HOP_OPERATION_LATENCY_ACI_CYCLES
            + hops * _GM_RDMA_OPERATION_HOP_SLOPE_ACI_CYCLES
        )

    def send(self, message: Message) -> Process:
        """Queue one source-owned message and complete after NMC TX service."""
        self._validate_send_message(message)
        if message.src != self.binding.address:
            raise ValueError(
                f"{self.fabric_id.name} NMC channel at PE "
                f"{self.binding.address.node_id} cannot send from "
                f"{message.src.node_type.name}[{message.src.node_id}]"
            )
        flits = tuple(message.packetize())
        return self.env.process(
            self._submit_tx(message, flits, message.nmc_shape_mode)
        )

    def recv_flit(self) -> Process:
        """Wait for one flit after calibrated NMC RX service."""
        self._select_receive_api("flit")
        return self.env.process(self._recv_flit())

    def recv_message(
        self,
        message: Message,
        shape_mode: NMCShapeMode,
    ) -> Process:
        """Post one receive command and complete after its validated TAIL."""
        self._validate_receive_message(message)
        if message.dst != self.binding.address:
            raise ValueError(
                f"{self.fabric_id.name} NMC channel at PE "
                f"{self.binding.address.node_id} cannot receive for "
                f"{message.dst.node_type.name}[{message.dst.node_id}]"
            )
        self._select_receive_api("message")
        if message.index in self._claimed_receive_ids:
            raise ValueError(
                f"message {message.index} already has a receive command on "
                f"{self.fabric_id.name} PE{self.binding.address.node_id}"
            )
        self._claimed_receive_ids.add(message.index)
        return self.env.process(self._recv_message(message, shape_mode))

    @staticmethod
    def _validate_message_transport(message: Message) -> None:
        if message.trans_type is not TransType.SINGLECAST:
            raise NotImplementedError(
                "NMC command execution supports SINGLECAST only"
            )
        if (
            message.src.attachment_mode is DMAAttachmentMode.AIU_LOCAL
            or message.dst.attachment_mode is DMAAttachmentMode.AIU_LOCAL
        ):
            raise NotImplementedError(
                "NMC command execution does not support AIU-local paths"
            )

    def _validate_send_message(self, message: Message) -> None:
        self._validate_message_transport(message)
        if message.src.node_type is not NodeType.PE or message.dst.node_type not in (
            NodeType.PE,
            NodeType.GM_WDMA,
        ):
            raise NotImplementedError(
                "NMC send supports PE-to-PE and PE-to-GM transfers only"
            )

    def _validate_receive_message(self, message: Message) -> None:
        self._validate_message_transport(message)
        if message.dst.node_type is not NodeType.PE or message.src.node_type not in (
            NodeType.PE,
            NodeType.GM_RDMA,
        ):
            raise NotImplementedError(
                "NMC receive supports PE-to-PE and GM-to-PE transfers only"
            )

    def _select_receive_api(
        self,
        mode: Literal["flit", "message"],
    ) -> None:
        if self._receive_api_mode is None:
            self._receive_api_mode = mode
            return
        if self._receive_api_mode != mode:
            raise RuntimeError(
                f"{self.fabric_id.name} PE{self.binding.address.node_id} cannot "
                "mix raw-flit and command-level receive APIs"
            )

    def _submit_tx(
        self,
        message: Message,
        flits: tuple[Flit, ...],
        shape_mode: NMCShapeMode,
    ) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        descriptor_request: Request | None = None
        try:
            issue_request = self.descriptor_issuer.request()
            with issue_request:
                yield issue_request
                descriptor_request = self.descriptor_slots.request()
                yield descriptor_request
                descriptor_issue_start_time = float(self.env.now)
                yield self.env.timeout(self.config.descriptor_issue_cycles)

            descriptor_acceptance_time_aci_cycles = float(self.env.now)
            endpoint_ready_time_aci_cycles = (
                descriptor_issue_start_time
                + self.config.descriptor_issue_cycles
                + self.endpoint_setup_residual_aci_cycles(shape_mode)
            )

            completion = self.env.event()
            destination_ready: SimpyEvent | None = None
            if (
                self.paired_dma_commands is not None
                and message.dst.node_type is NodeType.GM_WDMA
                and message.dst.attachment_mode is DMAAttachmentMode.DUAL_SIDE
            ):
                destination_ready = (
                    self.paired_dma_commands.destination_ready_event(message)
                )
            yield self.tx_data_queue.put(
                NMCTransmitEntry(
                    message=message,
                    flits=flits,
                    shape_mode=shape_mode,
                    submission_time_aci_cycles=submission_time_aci_cycles,
                    descriptor_acceptance_time_aci_cycles=(
                        descriptor_acceptance_time_aci_cycles
                    ),
                    endpoint_ready_time_aci_cycles=(
                        endpoint_ready_time_aci_cycles
                    ),
                    destination_ready=destination_ready,
                    completion=completion,
                )
            )
            return cast(NMCTransmitResult, (yield completion))
        finally:
            if descriptor_request is not None:
                if descriptor_request.triggered:
                    self.descriptor_slots.release(descriptor_request)
                else:
                    descriptor_request.cancel()

    def _recv_flit(self) -> ProcessGenerator:
        entry = cast(NMCReceiveEntry, (yield self.rx_data_queue.get()))
        return entry.flit

    def _recv_message(
        self,
        message: Message,
        shape_mode: NMCShapeMode,
    ) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        descriptor_request: Request | None = None
        try:
            issue_request = self.descriptor_issuer.request()
            with issue_request:
                yield issue_request
                descriptor_request = self.descriptor_slots.request()
                yield descriptor_request
                descriptor_issue_start_time = float(self.env.now)
                yield self.env.timeout(self.config.descriptor_issue_cycles)

            descriptor_acceptance_time_aci_cycles = float(self.env.now)
            endpoint_ready_time_aci_cycles = (
                descriptor_issue_start_time
                + self.config.descriptor_issue_cycles
                + self.receive_endpoint_setup_residual_aci_cycles(shape_mode)
            )
            flits: list[Flit] = []
            tail_rx_service_completion_time_aci_cycles: float | None = None
            expected_flit_count = message.flit_count()
            for flit_index in range(expected_flit_count):
                entry = cast(
                    NMCReceiveEntry,
                    (
                        yield self.rx_data_queue.get(
                            lambda queued, message_id=message.index: (
                                queued.flit.msg_id == message_id
                            )
                        )
                    ),
                )
                message.validate_flit(
                    entry.flit,
                    flit_index,
                    expected_flit_count,
                )
                flits.append(entry.flit)
                tail_rx_service_completion_time_aci_cycles = (
                    entry.rx_service_completion_time_aci_cycles
                )

            if tail_rx_service_completion_time_aci_cycles is None:
                raise RuntimeError(
                    f"message {message.index} has no receive-service boundary"
                )
            setup_wait = endpoint_ready_time_aci_cycles - float(self.env.now)
            if setup_wait > 0:
                yield self.env.timeout(setup_wait)
            operation_completion_floor = (
                submission_time_aci_cycles
                + self.minimum_receive_operation_latency_aci_cycles(message)
            )
            completion_wait = operation_completion_floor - float(self.env.now)
            if completion_wait > 0:
                yield self.env.timeout(completion_wait)
            return NMCReceiveResult(
                message=message,
                flits=tuple(flits),
                shape_mode=shape_mode,
                submission_time_aci_cycles=submission_time_aci_cycles,
                descriptor_acceptance_time_aci_cycles=(
                    descriptor_acceptance_time_aci_cycles
                ),
                endpoint_ready_time_aci_cycles=endpoint_ready_time_aci_cycles,
                tail_rx_service_completion_time_aci_cycles=(
                    tail_rx_service_completion_time_aci_cycles
                ),
                operation_completion_time_aci_cycles=float(self.env.now),
            )
        finally:
            if descriptor_request is not None:
                if descriptor_request.triggered:
                    self.descriptor_slots.release(descriptor_request)
                else:
                    descriptor_request.cancel()

    def _tx_service_loop(self) -> ProcessGenerator:
        next_command_service_time_aci_cycles = float(self.env.now)
        while True:
            entry = cast(NMCTransmitEntry, (yield self.tx_data_queue.get()))
            if entry.destination_ready is not None:
                yield entry.destination_ready
                if self.paired_dma_commands is None:
                    raise RuntimeError("paired DMA coordinator is unavailable")
                self.paired_dma_commands.retire(
                    entry.message,
                    entry.destination_ready,
                )
            turnaround_wait = (
                next_command_service_time_aci_cycles - float(self.env.now)
            )
            if turnaround_wait > 0:
                yield self.env.timeout(turnaround_wait)
            setup_wait = (
                entry.endpoint_ready_time_aci_cycles - float(self.env.now)
            )
            if setup_wait > 0:
                yield self.env.timeout(setup_wait)
            request = self.tx_datapath.request()
            with request:
                yield request
                command_service_start_time_aci_cycles = float(self.env.now)
                next_command_service_time_aci_cycles = (
                    command_service_start_time_aci_cycles
                    + len(entry.flits) * self.tx_service_interval_aci_cycles
                    + self.config.inter_command_turnaround_aci_cycles
                )
                for flit in entry.flits:
                    yield self.env.timeout(self.tx_service_interval_aci_cycles)
                    yield self.binding.tx_link.send_flit(flit)
            final_local_handoff_time_aci_cycles = float(self.env.now)
            entry.completion.succeed(
                NMCTransmitResult(
                    flits=entry.flits,
                    shape_mode=entry.shape_mode,
                    submission_time_aci_cycles=(
                        entry.submission_time_aci_cycles
                    ),
                    descriptor_acceptance_time_aci_cycles=(
                        entry.descriptor_acceptance_time_aci_cycles
                    ),
                    endpoint_ready_time_aci_cycles=(
                        entry.endpoint_ready_time_aci_cycles
                    ),
                    final_local_handoff_time_aci_cycles=(
                        final_local_handoff_time_aci_cycles
                    ),
                    operation_completion_time_aci_cycles=float(self.env.now),
                )
            )

    def _rx_service_loop(self) -> ProcessGenerator:
        while True:
            flit = cast(Flit, (yield self.binding.rx_link.recv_flit()))
            request = self.rx_datapath.request()
            with request:
                yield request
                yield self.env.timeout(self.rx_service_interval_aci_cycles)
                yield self.rx_data_queue.put(
                    NMCReceiveEntry(
                        flit=flit,
                        rx_service_completion_time_aci_cycles=(
                            float(self.env.now)
                        ),
                    )
                )
                yield self.binding.rx_link.ack_credit()
