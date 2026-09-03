"""Runtime ownership for one configured GM DMA endpoint."""

from dataclasses import dataclass
from typing import cast

import simpy
from simpy.events import Event as SimpyEvent
from simpy.events import Process, ProcessGenerator
from simpy.resources.resource import Resource

from .configs.schemas.arch_config import DMAEngineConfig, DMAType
from .noc import Link, Router
from .utils.definitions import (
    FLIT_BYTES,
    DMAAttachmentMode,
    EndpointAddress,
    Flit,
    Message,
    NoCChannel,
    NodeType,
    TransType,
)


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
class DMAReceiveEntry:
    """One flit after shared GM WDMA service."""

    flit: Flit
    service_completion_time_aci_cycles: float


@dataclass(frozen=True)
class DMATransmitResult:
    """One GM RDMA command completed through final local-link handoff."""

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
    """One GM WDMA command completed after its TAIL reaches GM service."""

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
    """One GM RDMA or WDMA whose internal datapath is shared by both fabrics."""

    def __init__(
        self,
        env: simpy.Environment,
        config: DMAEngineConfig,
        node_type: NodeType,
    ) -> None:
        if node_type not in (NodeType.GM_RDMA, NodeType.GM_WDMA):
            raise ValueError("Phase 3 DMA runtime supports GM endpoints only")
        expected_dma_type = (
            DMAType.GM_RDMA
            if node_type is NodeType.GM_RDMA
            else DMAType.GM_WDMA
        )
        if config.dma_type is not expected_dma_type:
            raise ValueError(
                f"{node_type.name} endpoint requires {expected_dma_type.name} config"
            )
        self.env = env
        self.config = config
        self.node_type = node_type
        self.bindings: dict[NoCChannel, DMAChannelBinding] = {}

        # The hardware endpoint has one internal engine shared by CH0 and CH1.
        self.internal_datapath = Resource(env, capacity=1)
        self.descriptor_issuer = Resource(env, capacity=1)
        self.tx_command_slots = {
            fabric_id: Resource(env, capacity=1) for fabric_id in NoCChannel
        }
        self.rx_data_queue = simpy.FilterStore(env)
        self._claimed_receive_ids: set[int] = set()
        self._receive_ready: dict[int, SimpyEvent] = {}

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
        if self.node_type is NodeType.GM_WDMA:
            self.env.process(self._rx_service_loop(binding))

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
        return FLIT_BYTES / self.config.port_bw

    def send(self, message: Message) -> Process:
        """Post one GM RDMA command and inject it into one data fabric."""
        if self.node_type is not NodeType.GM_RDMA:
            raise NotImplementedError("GM_WDMA cannot inject payload data")
        self._validate_message_transport(message)
        binding = self.binding_for(message.src.fabric_id)
        if message.src != binding.address:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot send from "
                f"{message.src.node_type.name}[{message.src.node_id}]"
            )
        if message.dst.node_type is not NodeType.PE:
            raise NotImplementedError(
                "Phase 3B GM RDMA execution supports GM-to-PE transfers only"
            )
        return self.env.process(
            self._send(message, tuple(message.packetize()), binding)
        )

    def recv_message(self, message: Message) -> Process:
        """Post one GM WDMA command and complete after shared endpoint service."""
        if self.node_type is not NodeType.GM_WDMA:
            raise NotImplementedError("GM_RDMA cannot consume payload data")
        self._validate_message_transport(message)
        binding = self.binding_for(message.dst.fabric_id)
        if message.dst != binding.address:
            raise ValueError(
                f"{self.node_type.name}[{self.instance_id}] cannot receive for "
                f"{message.dst.node_type.name}[{message.dst.node_id}]"
            )
        if message.src.node_type is not NodeType.PE:
            raise NotImplementedError(
                "Phase 3B GM WDMA execution supports PE-to-GM transfers only"
            )
        if message.index in self._claimed_receive_ids:
            raise ValueError(
                f"message {message.index} already has a receive command on "
                f"{self.node_type.name}[{self.instance_id}]"
            )
        self._claimed_receive_ids.add(message.index)
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
    ) -> ProcessGenerator:
        submission_time_aci_cycles = float(self.env.now)
        yield from self._dispatch_descriptor()
        descriptor_acceptance_time_aci_cycles = float(self.env.now)
        if self.config.cdc_penalty > 0:
            yield self.env.timeout(self.config.cdc_penalty)

        command_request = self.tx_command_slots[message.src.fabric_id].request()
        with command_request:
            yield command_request
            for flit in flits:
                datapath_request = self.internal_datapath.request()
                with datapath_request:
                    yield datapath_request
                    yield self.env.timeout(self.service_interval_aci_cycles)
                yield binding.tx_link.send_flit(flit)

        final_local_handoff_time_aci_cycles = float(self.env.now)
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
        yield from self._dispatch_descriptor()
        descriptor_acceptance_time_aci_cycles = float(self.env.now)
        if self.config.cdc_penalty > 0:
            yield self.env.timeout(self.config.cdc_penalty)

        receive_ready = self._receive_ready_event(message.index)
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
            tail_service_completion_time_aci_cycles = (
                entry.service_completion_time_aci_cycles
            )

        if tail_service_completion_time_aci_cycles is None:
            raise RuntimeError(f"message {message.index} has no GM service boundary")
        self._receive_ready.pop(message.index, None)
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

    def _dispatch_descriptor(self) -> ProcessGenerator:
        request = self.descriptor_issuer.request()
        with request:
            yield request
            yield self.env.timeout(self.config.dispatch_interval)

    def _receive_ready_event(self, message_id: int) -> SimpyEvent:
        event = self._receive_ready.get(message_id)
        if event is None:
            event = self.env.event()
            self._receive_ready[message_id] = event
        return event

    def _rx_service_loop(
        self,
        binding: DMAChannelBinding,
    ) -> ProcessGenerator:
        while True:
            flit = cast(Flit, (yield binding.rx_link.recv_flit()))
            yield self._receive_ready_event(flit.msg_id)
            request = self.internal_datapath.request()
            with request:
                yield request
                yield self.env.timeout(self.service_interval_aci_cycles)
                yield self.rx_data_queue.put(
                    DMAReceiveEntry(
                        flit=flit,
                        service_completion_time_aci_cycles=float(self.env.now),
                    )
                )
            yield binding.rx_link.ack_credit()


DMAEndpointKey = tuple[NodeType, int]
DMAEndpoints = dict[DMAEndpointKey, DMAEndpoint]


__all__ = [
    "DMAChannelBinding",
    "DMAEndpoint",
    "DMAEndpointKey",
    "DMAEndpoints",
    "DMAReceiveEntry",
    "DMAReceiveResult",
    "DMATransmitResult",
]
