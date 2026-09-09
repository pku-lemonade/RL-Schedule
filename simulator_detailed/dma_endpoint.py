"""Runtime ownership for one configured GM or DDR DMA endpoint."""

import math
from dataclasses import dataclass
from typing import cast

import simpy
from simpy.events import Event as SimpyEvent
from simpy.events import Process, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .benchmark_references import DDR_DMA_REFERENCE
from .command_coordination import DMACommandCoordinator
from .configs.schemas.arch_config import DMAEngineConfig
from .endpoint_registry import dma_node_type
from .noc import Link, RoundRobinArbiter, Router
from .utils.definitions import (
    DMA_RDMA_NODE_TYPES,
    DMA_WDMA_NODE_TYPES,
    FLIT_BYTES,
    DMAAttachmentMode,
    DMACommandMode,
    EndpointAddress,
    Flit,
    FlitTrafficType,
    Message,
    NoCChannel,
    NodeType,
    TransType,
)

_GM_WDMA_DESCRIPTOR_ISSUE_CYCLES = 40.0
_GM_WDMA_MAX_OUTSTANDING_DESCRIPTORS_PER_CHANNEL = 4
_GM_WDMA_MIN_COMMAND_COMPLETION_INTERVAL_ACI_CYCLES = 273.0
_GM_WDMA_ZERO_HOP_OPERATION_LATENCY_ACI_CYCLES = 138.0
_GM_WDMA_OPERATION_HOP_SLOPE_ACI_CYCLES = 17.0
_GM_WDMA_SERVICE_BYTES_PER_ACI_CYCLE = 110.0
_GM_RDMA_SERVICE_BYTES_PER_ACI_CYCLE = 110.0

@dataclass(frozen=True)
class DMAClockDomain:
    """Convert native endpoint cycles to and from the ACI simulation timebase."""

    endpoint_clock_mhz: float
    aci_clock_mhz: float

    def __post_init__(self) -> None:
        for name, value in (
            ("endpoint_clock_mhz", self.endpoint_clock_mhz),
            ("aci_clock_mhz", self.aci_clock_mhz),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

    @property
    def endpoint_cycles_per_aci_cycle(self) -> float:
        return self.endpoint_clock_mhz / self.aci_clock_mhz

    def endpoint_cycles_to_aci_cycles(self, endpoint_cycles: float) -> float:
        """Return elapsed ACI cycles for a native endpoint-cycle duration."""
        if not math.isfinite(endpoint_cycles) or endpoint_cycles < 0:
            raise ValueError("endpoint_cycles must be finite and non-negative")
        return endpoint_cycles / self.endpoint_cycles_per_aci_cycle

    def aci_cycles_to_endpoint_cycles(self, aci_cycles: float) -> float:
        """Return elapsed native endpoint cycles for an ACI-cycle duration."""
        if not math.isfinite(aci_cycles) or aci_cycles < 0:
            raise ValueError("aci_cycles must be finite and non-negative")
        return aci_cycles * self.endpoint_cycles_per_aci_cycle


@dataclass(frozen=True)
class DMAChannelBinding:
    """One DMA endpoint's physical attachment to one data NoC fabric."""

    address: EndpointAddress
    tx_link: Link
    rx_link: Link
    router: Router

    def __post_init__(self) -> None:
        if self.address.node_type is NodeType.PE:
            raise ValueError("DMA channel binding requires a DMA endpoint address")
        fabric_id = self.address.fabric_id
        if self.router.fabric_id is not fabric_id:
            raise ValueError(
                f"{fabric_id.name} DMA address cannot bind {self.router.name}"
            )
        for link in (self.tx_link, self.rx_link):
            if link.fabric_id is not fabric_id:
                raise ValueError(
                    f"{fabric_id.name} DMA address cannot bind {link.link_name}"
                )
            if link.tracer is not self.router.tracer:
                raise ValueError(
                    f"{link.link_name} and {self.router.name} use different tracers"
                )
        if self.tx_link is self.rx_link:
            raise ValueError("DMA TX and RX must use distinct physical links")
        if self.router.id != self.address.router_id:
            raise ValueError(
                f"DMA address maps to router {self.address.router_id}, "
                f"not router {self.router.id}"
            )


@dataclass(frozen=True)
class DMAServiceEvent:
    """One completed payload-flit service interval, excluding queue waits."""

    node_type: NodeType
    instance_id: int
    fabric_id: NoCChannel
    message_id: int
    payload_bytes: int
    start_time: float
    end_time: float
    delay_factor: float


@dataclass(frozen=True)
class DMAReceiveEntry:
    """One flit after shared WDMA service."""

    flit: Flit
    service_completion_time_aci_cycles: float


@dataclass(frozen=True)
class DMATransmitResult:
    """One RDMA command completed through final local-link handoff."""

    message: Message
    flits: tuple[Flit, ...]
    submission_time_aci_cycles: float
    descriptor_acceptance_time_aci_cycles: float
    final_local_handoff_time_aci_cycles: float
    operation_completion_time_aci_cycles: float

    @property
    def operation_latency_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.submission_time_aci_cycles
        )


@dataclass(frozen=True)
class DMAReceiveResult:
    """One WDMA command through TAIL service and completion processing."""

    message: Message
    flits: tuple[Flit, ...]
    submission_time_aci_cycles: float
    descriptor_acceptance_time_aci_cycles: float
    tail_service_completion_time_aci_cycles: float
    operation_completion_time_aci_cycles: float

    @property
    def operation_latency_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.submission_time_aci_cycles
        )


class DMAEndpoint:
    """One DMA endpoint whose internal datapath is shared by both fabrics."""

    def __init__(
        self,
        env: simpy.Environment,
        config: DMAEngineConfig,
        node_type: NodeType,
        dma_commands: DMACommandCoordinator | None = None,
        *,
        aci_clock_mhz: float,
    ) -> None:
        expected_node_type = dma_node_type(config.dma_type)
        if node_type is not expected_node_type:
            raise ValueError(
                f"{node_type.name} endpoint cannot use {config.dma_type.name} config"
            )
        if (
            dma_commands is not None
            and dma_commands.env is not env
        ):
            raise ValueError(
                "DMA endpoint and command coordinator must use the same "
                "environment"
            )
        self.env = env
        self.config = config
        self.node_type = node_type
        self.dma_commands = dma_commands
        self.clock_domain = DMAClockDomain(
            endpoint_clock_mhz=config.endpoint_clock_mhz,
            aci_clock_mhz=aci_clock_mhz,
        )
        self.bindings: dict[NoCChannel, DMAChannelBinding] = {}
        self.service_events: list[DMAServiceEvent] = []
        self._service_delay_factor = 1.0

        # The hardware endpoint has one internal engine shared by CH0 and CH1.
        self.internal_datapath = Resource(env, capacity=1)
        if self.node_type is NodeType.DDR_WDMA:
            # DDR WDMA measurements show CH0/CH1 setup overlap for small commands.
            self.descriptor_issuers = {
                fabric_id: Resource(env, capacity=1)
                for fabric_id in NoCChannel
            }
        else:
            shared_descriptor_issuer = Resource(env, capacity=1)
            self.descriptor_issuers = {
                fabric_id: shared_descriptor_issuer
                for fabric_id in NoCChannel
            }
        self.command_completion_sequencer = Resource(env, capacity=1)
        self._last_command_completion_time_aci_cycles: float | None = None
        self.tx_burst_arbiters = {
            fabric_id: RoundRobinArbiter(env) for fabric_id in NoCChannel
        }
        self.descriptor_slots: dict[NoCChannel, Resource] = {}
        descriptor_capacity: int | None = None
        if self.node_type is NodeType.GM_WDMA:
            descriptor_capacity = self.max_outstanding_descriptors_per_channel
        elif self.node_type is NodeType.DDR_WDMA:
            descriptor_capacity = (
                self.config.max_outstanding_descriptors_per_channel
            )
        if descriptor_capacity is not None:
            self.descriptor_slots = {
                fabric_id: Resource(
                    env,
                    capacity=descriptor_capacity,
                )
                for fabric_id in NoCChannel
            }
        self.rx_data_queue = simpy.FilterStore(env)
        self._claimed_receive_ids: set[tuple[NoCChannel, int]] = set()
        self._receive_ready: dict[tuple[NoCChannel, int], SimpyEvent] = {}

    @property
    def instance_id(self) -> int:
        return self.config.instance_id

    def bind_channel(self, binding: DMAChannelBinding) -> None:
        address = binding.address
        if address.node_type is not self.node_type:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot bind "
                f"{address.node_type.name}[{address.node_id}]"
            )
        if address.node_id != self.instance_id:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot bind instance "
                f"{address.node_id}"
            )
        if any(
            component.env is not self.env
            for component in (
                binding.tx_link,
                binding.rx_link,
                binding.router,
            )
        ):
            raise ValueError("DMA endpoint and binding must use the same environment")
        if address.fabric_id in self.bindings:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] already has a "
                f"{address.fabric_id.name} binding"
            )
        self.bindings[address.fabric_id] = binding
        if self.node_type in DMA_WDMA_NODE_TYPES:
            self.env.process(self._rx_service_loop(binding))
        elif self.node_type in DMA_RDMA_NODE_TYPES:
            self.env.process(self._request_service_loop(binding))
        else:
            raise RuntimeError(f"unsupported DMA endpoint type: {self.node_type!r}")

    def binding_for(self, fabric_id: NoCChannel) -> DMAChannelBinding:
        try:
            return self.bindings[fabric_id]
        except KeyError as exc:
            raise KeyError(
                f"{self.node_type.name}[{self.instance_id}] has no "
                f"{fabric_id.name} binding"
            ) from exc

    def validate_channel_bindings(self) -> None:
        missing = set(NoCChannel) - self.bindings.keys()
        if missing:
            missing_names = ", ".join(
                fabric_id.name for fabric_id in sorted(missing)
            )
            raise RuntimeError(
                f"{self.node_type.name}[{self.instance_id}] is missing "
                f"bindings for {missing_names}"
            )

    @property
    def service_interval_aci_cycles(self) -> float:
        return (
            FLIT_BYTES / self.service_bytes_per_aci_cycle
            * self._service_delay_factor
        )

    @property
    def service_delay_factor(self) -> float:
        return self._service_delay_factor

    def scale_service_delay(self, factor: float) -> None:
        """Compose fault factors for subsequent flits on both fabrics."""
        combined = self._service_delay_factor * factor
        if (
            not math.isfinite(factor) or factor <= 0
            or not math.isfinite(combined) or combined <= 0
        ):
            raise ValueError("DMA service delay factor must be finite and positive")
        self._service_delay_factor = combined

    def _service_payload(self, flit: Flit) -> ProcessGenerator:
        """Called while holding the endpoint's shared payload datapath."""
        start_time = float(self.env.now)
        delay_factor = self._service_delay_factor
        yield self.env.timeout(self.service_interval_aci_cycles)
        self.service_events.append(
            DMAServiceEvent(
                node_type=self.node_type,
                instance_id=self.instance_id,
                fabric_id=flit.fabric_id,
                message_id=flit.msg_id,
                payload_bytes=flit.payload_bytes,
                start_time=start_time,
                end_time=float(self.env.now),
                delay_factor=delay_factor,
            )
        )

    @property
    def service_bytes_per_aci_cycle(self) -> float:
        configured_rate = self.config.port_bw
        if configured_rate is not None:
            return configured_rate
        if self.node_type is NodeType.GM_WDMA:
            return _GM_WDMA_SERVICE_BYTES_PER_ACI_CYCLE
        if self.node_type is NodeType.GM_RDMA:
            return _GM_RDMA_SERVICE_BYTES_PER_ACI_CYCLE
        if self.node_type is NodeType.DDR_WDMA:
            return DDR_DMA_REFERENCE.wdma_service_bytes_per_aci_cycle
        if self.node_type is NodeType.DDR_RDMA:
            return DDR_DMA_REFERENCE.rdma_service_bytes_per_aci_cycle
        raise RuntimeError(f"{self.node_type.name} service rate is uncalibrated")

    @property
    def descriptor_issue_cycles(self) -> float:
        configured_cycles = self.config.descriptor_issue_cycles
        if configured_cycles is not None:
            return configured_cycles
        if self.node_type is NodeType.GM_WDMA:
            return _GM_WDMA_DESCRIPTOR_ISSUE_CYCLES
        raise RuntimeError(
            f"{self.node_type.name} descriptor issue timing is uncalibrated; "
            "configure descriptor_issue_cycles explicitly"
        )

    @property
    def max_outstanding_descriptors_per_channel(self) -> int:
        configured_capacity = (
            self.config.max_outstanding_descriptors_per_channel
        )
        if configured_capacity is not None:
            return configured_capacity
        if self.node_type is NodeType.GM_WDMA:
            return _GM_WDMA_MAX_OUTSTANDING_DESCRIPTORS_PER_CHANNEL
        raise RuntimeError(
            f"{self.node_type.name} descriptor capacity is not characterized"
        )

    def outstanding_descriptor_count(self, fabric_id: NoCChannel) -> int:
        """Return active WDMA receive descriptors on one fabric."""
        return len(self._descriptor_slots_for(fabric_id).users)

    def send(self, message: Message) -> Process:
        """Post one direct dual-side RDMA command and inject its payload."""
        if self.node_type not in DMA_RDMA_NODE_TYPES:
            raise NotImplementedError("WDMA endpoints cannot inject payload data")
        self._validate_message_transport(message)
        binding = self.binding_for(message.src.fabric_id)
        if message.src != binding.address:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot send from "
                f"{message.src.node_type.name}[{message.src.node_id}]"
            )
        if message.dst.node_type is not NodeType.PE:
            raise NotImplementedError(
                "RDMA execution supports DMA-to-PE transfers only"
            )
        if message.dma_command_mode is DMACommandMode.SINGLE_SIDE:
            raise ValueError(
                "single-side downloads are initiated by NMCChannel.recv_message"
            )
        if message.dma_command_mode is not DMACommandMode.DUAL_SIDE:
            raise RuntimeError(f"message {message.index} has no DMA command mode")
        self._validate_paired_execution_config()
        return self.env.process(
            self._send(
                message,
                tuple(message.packetize()),
                binding,
                dispatch_descriptor=True,
            )
        )

    def recv_message(self, message: Message) -> Process:
        """Post one dual-side WDMA receive command."""
        if self.node_type not in DMA_WDMA_NODE_TYPES:
            raise NotImplementedError("RDMA endpoints cannot consume payload data")
        self._validate_message_transport(message)
        binding = self.binding_for(message.dst.fabric_id)
        if message.dst != binding.address:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot receive for "
                f"{message.dst.node_type.name}[{message.dst.node_id}]"
            )
        if message.src.node_type is not NodeType.PE:
            raise NotImplementedError(
                "WDMA execution supports PE-to-DMA transfers only"
            )
        if message.dma_command_mode is DMACommandMode.SINGLE_SIDE:
            raise ValueError(
                "single-side uploads are initiated by NMCChannel.send"
            )
        if message.dma_command_mode is not DMACommandMode.DUAL_SIDE:
            raise RuntimeError(f"message {message.index} has no DMA command mode")
        self._validate_paired_execution_config()
        receive_key = (message.dst.fabric_id, message.index)
        if receive_key in self._claimed_receive_ids:
            raise ValueError(
                f"message {message.index} already has a receive command on "
                f"{self.node_type.name}[{self.instance_id}]"
            )
        self._claimed_receive_ids.add(receive_key)
        return self.env.process(self._recv_message(message))

    @staticmethod
    def _validate_message_transport(message: Message) -> None:
        if message.trans_type is not TransType.SINGLECAST:
            raise NotImplementedError(
                "Phase 3 DMA command execution supports SINGLECAST only"
            )
        if (
            message.src.attachment_mode is DMAAttachmentMode.AIU_LOCAL
            or message.dst.attachment_mode is DMAAttachmentMode.AIU_LOCAL
        ):
            raise NotImplementedError(
                "Phase 3 DMA command execution does not support AIU-local paths"
            )

    def _send(
        self,
        message: Message,
        flits: tuple[Flit, ...],
        binding: DMAChannelBinding,
        *,
        dispatch_descriptor: bool,
    ) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        if dispatch_descriptor:
            yield from self._dispatch_descriptor(message.src.fabric_id)
        descriptor_acceptance_time_aci_cycles = float(self.env.now)
        dual_side_ready: SimpyEvent | None = None
        if message.dma_command_mode is DMACommandMode.DUAL_SIDE:
            dual_side_ready = self._require_dma_commands().post_dual_side_source(
                message
            )
            yield dual_side_ready
        elif message.dma_command_mode is not DMACommandMode.SINGLE_SIDE:
            raise RuntimeError(f"message {message.index} has no DMA command mode")
        if self.config.cdc_penalty > 0:
            yield self.env.timeout(self.config.cdc_penalty)

        burst_quantum_flits = binding.router.config.resolve_burst_quantum_flits(
            message.burst_len_mode
        )
        burst_arbiter = self.tx_burst_arbiters[message.src.fabric_id]
        for burst_start in range(0, len(flits), burst_quantum_flits):
            yield burst_arbiter.request(message.index)
            try:
                burst = flits[burst_start : burst_start + burst_quantum_flits]
                for flit in burst:
                    datapath_request = self.internal_datapath.request()
                    with datapath_request:
                        yield datapath_request
                        yield from self._service_payload(flit)
                    yield binding.tx_link.send_flit(flit)
            finally:
                burst_arbiter.release(message.index)

        final_local_handoff_time_aci_cycles = float(self.env.now)
        if dual_side_ready is not None:
            self._require_dma_commands().complete_dual_side_source(
                message,
                dual_side_ready,
            )
        return DMATransmitResult(
            message=message,
            flits=flits,
            submission_time_aci_cycles=submission_time_aci_cycles,
            descriptor_acceptance_time_aci_cycles=(
                descriptor_acceptance_time_aci_cycles
            ),
            final_local_handoff_time_aci_cycles=(
                final_local_handoff_time_aci_cycles
            ),
            operation_completion_time_aci_cycles=float(self.env.now),
        )

    def _recv_message(self, message: Message) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        descriptor_slots = self._descriptor_slots_for(message.dst.fabric_id)
        descriptor_request: Request | None = None
        try:
            descriptor_request = descriptor_slots.request()
            yield descriptor_request
            yield from self._dispatch_descriptor(message.dst.fabric_id)
            descriptor_acceptance_time_aci_cycles = float(self.env.now)
            self._require_dma_commands().post_dual_side_destination(message)
            result = (
                yield from self._receive_payload(
                    message,
                    submission_time_aci_cycles,
                    descriptor_acceptance_time_aci_cycles,
                )
            )
            self._require_dma_commands().complete_dual_side_destination(message)
            return result
        finally:
            if descriptor_request is not None:
                if descriptor_request.triggered:
                    descriptor_slots.release(descriptor_request)
                else:
                    descriptor_request.cancel()

    def _receive_payload(
        self,
        message: Message,
        submission_time_aci_cycles: float,
        descriptor_acceptance_time_aci_cycles: float,
    ) -> ProcessGenerator:
        if self.config.cdc_penalty > 0:
            yield self.env.timeout(self.config.cdc_penalty)

        receive_ready = self._receive_ready_event(
            message.dst.fabric_id,
            message.index,
        )
        if not receive_ready.triggered:
            receive_ready.succeed()

        flits: list[Flit] = []
        tail_service_completion_time_aci_cycles: float | None = None
        expected_flit_count = message.flit_count()
        for flit_index in range(expected_flit_count):
            entry = cast(
                DMAReceiveEntry,
                (
                    yield self.rx_data_queue.get(
                        lambda queued,
                        message_id=message.index,
                        fabric_id=message.dst.fabric_id: (
                            queued.flit.msg_id == message_id
                            and queued.flit.fabric_id is fabric_id
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
            tail_service_completion_time_aci_cycles = (
                entry.service_completion_time_aci_cycles
            )

        if tail_service_completion_time_aci_cycles is None:
            raise RuntimeError(
                f"message {message.index} has no DMA service boundary"
            )
        yield from self._complete_wdma_command(
            message,
            submission_time_aci_cycles,
        )
        self._receive_ready.pop(
            (message.dst.fabric_id, message.index),
            None,
        )
        return DMAReceiveResult(
            message=message,
            flits=tuple(flits),
            submission_time_aci_cycles=submission_time_aci_cycles,
            descriptor_acceptance_time_aci_cycles=(
                descriptor_acceptance_time_aci_cycles
            ),
            tail_service_completion_time_aci_cycles=(
                tail_service_completion_time_aci_cycles
            ),
            operation_completion_time_aci_cycles=float(self.env.now),
        )

    def _recv_single_side_upload(
        self,
        message: Message,
        binding: DMAChannelBinding,
    ) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        yield from self._receive_payload(
            message,
            submission_time_aci_cycles,
            submission_time_aci_cycles,
        )
        response_flit = (
            self._require_dma_commands().create_single_side_upload_response(
                message,
            )
        )
        yield binding.tx_link.send_flit(response_flit)

    def _dispatch_descriptor(self, fabric_id: NoCChannel) -> ProcessGenerator:
        request = self.descriptor_issuers[fabric_id].request()
        with request:
            yield request
            yield self.env.timeout(self.descriptor_issue_cycles)

    def _descriptor_slots_for(self, fabric_id: NoCChannel) -> Resource:
        try:
            return self.descriptor_slots[fabric_id]
        except KeyError as exc:
            raise RuntimeError(
                f"{self.node_type.name} does not own receive descriptor slots"
            ) from exc

    def _complete_wdma_command(
        self,
        message: Message,
        submission_time_aci_cycles: float,
    ) -> ProcessGenerator:
        if self.node_type is NodeType.DDR_WDMA:
            if message.dma_command_mode is DMACommandMode.DUAL_SIDE:
                completion_floor = (
                    submission_time_aci_cycles
                    + DDR_DMA_REFERENCE.wdma_min_operation_latency_aci_cycles(
                        message.payload_bytes()
                    )
                )
                remaining_cycles = completion_floor - float(self.env.now)
                if remaining_cycles > 0:
                    yield self.env.timeout(remaining_cycles)
            # Single-side DDR completes after payload service and its response;
            # a separate fixed latency and completion cadence are unmeasured.
            return
        if self.node_type is not NodeType.GM_WDMA:
            raise RuntimeError(f"{self.node_type.name} cannot complete WDMA commands")
        request = self.command_completion_sequencer.request()
        with request:
            yield request
            completion_floor = float(self.env.now)
            if message.dma_command_mode is DMACommandMode.DUAL_SIDE:
                completion_floor = max(
                    completion_floor,
                    submission_time_aci_cycles
                    + self._minimum_operation_latency_aci_cycles(message),
                )
            if self._last_command_completion_time_aci_cycles is not None:
                completion_floor = max(
                    completion_floor,
                    self._last_command_completion_time_aci_cycles
                    + _GM_WDMA_MIN_COMMAND_COMPLETION_INTERVAL_ACI_CYCLES,
                )
            remaining_cycles = completion_floor - float(self.env.now)
            if remaining_cycles > 0:
                yield self.env.timeout(remaining_cycles)
            self._last_command_completion_time_aci_cycles = float(self.env.now)

    def _minimum_operation_latency_aci_cycles(self, message: Message) -> float:
        source_x, source_y = self.binding_for(
            message.dst.fabric_id
        ).router.to_xy(message.src.router_id)
        destination_x, destination_y = self.binding_for(
            message.dst.fabric_id
        ).router.to_xy(message.dst.router_id)
        hops = abs(destination_x - source_x) + abs(destination_y - source_y)
        return (
            _GM_WDMA_ZERO_HOP_OPERATION_LATENCY_ACI_CYCLES
            + hops * _GM_WDMA_OPERATION_HOP_SLOPE_ACI_CYCLES
        )

    def _receive_ready_event(
        self,
        fabric_id: NoCChannel,
        message_id: int,
    ) -> SimpyEvent:
        key = (fabric_id, message_id)
        event = self._receive_ready.get(key)
        if event is None:
            event = self.env.event()
            self._receive_ready[key] = event
        return event

    def _request_service_loop(
        self,
        binding: DMAChannelBinding,
    ) -> ProcessGenerator:
        while True:
            request_flit = cast(Flit, (yield binding.rx_link.recv_flit()))
            if request_flit.traffic_type is not FlitTrafficType.DMA_REQUEST:
                raise RuntimeError(
                    f"{self.node_type.name}[{self.instance_id}] received "
                    f"unexpected {request_flit.traffic_type.name} flit"
                )
            message = (
                self._require_dma_commands().accept_single_side_download_request(
                    request_flit,
                    binding.address,
                )
            )
            yield binding.rx_link.ack_credit()
            self.env.process(
                self._send(
                    message,
                    tuple(message.packetize()),
                    binding,
                    dispatch_descriptor=False,
                )
            )

    def _rx_service_loop(
        self,
        binding: DMAChannelBinding,
    ) -> ProcessGenerator:
        while True:
            flit = cast(Flit, (yield binding.rx_link.recv_flit()))
            if flit.traffic_type is not FlitTrafficType.PAYLOAD:
                raise RuntimeError(
                    f"{self.node_type.name}[{self.instance_id}] received "
                    f"unexpected {flit.traffic_type.name} flit"
                )
            if flit.is_head and flit.dma_header_bytes > 0:
                message = (
                    self._require_dma_commands().accept_single_side_upload_header(
                        flit,
                        binding.address,
                    )
                )
                self.env.process(self._recv_single_side_upload(message, binding))
            yield self._receive_ready_event(flit.fabric_id, flit.msg_id)
            request = self.internal_datapath.request()
            with request:
                yield request
                yield from self._service_payload(flit)
                yield self.rx_data_queue.put(
                    DMAReceiveEntry(
                        flit=flit,
                        service_completion_time_aci_cycles=float(self.env.now),
                    )
                )
            yield binding.rx_link.ack_credit()

    def _require_dma_commands(self) -> DMACommandCoordinator:
        if self.dma_commands is None:
            raise RuntimeError("DMA command coordinator is unavailable")
        return self.dma_commands

    def _validate_paired_execution_config(self) -> None:
        _ = self.service_bytes_per_aci_cycle
        _ = self.descriptor_issue_cycles
        if self.node_type in DMA_WDMA_NODE_TYPES:
            _ = self.max_outstanding_descriptors_per_channel
            for fabric_id in NoCChannel:
                self._descriptor_slots_for(fabric_id)


DMAEndpointKey = tuple[NodeType, int]
DMAEndpoints = dict[DMAEndpointKey, DMAEndpoint]


__all__ = [
    "DMAChannelBinding",
    "DMAClockDomain",
    "DMAEndpoint",
    "DMAEndpointKey",
    "DMAEndpoints",
    "DMAReceiveEntry",
    "DMAReceiveResult",
    "DMAServiceEvent",
    "DMATransmitResult",
]
