from dataclasses import dataclass
from typing import cast

import simpy
from simpy.events import Event as SimpyEvent
from simpy.events import Process, ProcessGenerator
from simpy.resources.resource import Request, Resource

from .configs.schemas.arch_config import NMCChannelConfig, NMCShapeTimingConfig
from .noc import Link, Router
from .utils.definitions import (
    FLIT_BYTES,
    EndpointAddress,
    Flit,
    Message,
    NMCShapeMode,
    NoCChannel,
    NodeType,
)


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

    flits: tuple[Flit, ...]
    shape_mode: NMCShapeMode
    submission_time: float
    descriptor_acceptance_time: float
    endpoint_ready_time: float
    completion: SimpyEvent


@dataclass(frozen=True)
class NMCReceiveEntry:
    """One flit after completion of the directional download service."""

    flit: Flit
    completion_time: float


class NMCChannel:
    """Runtime resources for one independent, full-duplex PE NMC channel."""

    def __init__(
        self,
        env: simpy.Environment,
        config: NMCChannelConfig,
        shape_timing: NMCShapeTimingConfig,
        binding: PEChannelBinding,
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

        self.env = env
        self.config = config
        self.shape_timing = shape_timing
        self.binding = binding
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
        self.rx_data_queue = simpy.Store(env)
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

    def endpoint_setup_residual_aci_cycles(
        self,
        shape_mode: NMCShapeMode,
    ) -> float:
        """Return setup time not represented by posting and source transport."""
        represented_cycles = (
            self.config.descriptor_issue_cycles
            + self.first_injection_transport_aci_cycles
        )
        residual_cycles = (
            self.shape_timing.endpoint_setup_target_aci_cycles(shape_mode)
            - represented_cycles
        )
        return max(0.0, residual_cycles)

    def send(self, message: Message) -> Process:
        """Queue one source-owned message and complete after NMC TX service."""
        if message.src != self.binding.address:
            raise ValueError(
                f"{self.fabric_id.name} NMC channel at PE "
                f"{self.binding.address.node_id} cannot send from "
                f"{message.src.node_type.name}[{message.src.node_id}]"
            )
        flits = tuple(message.packetize())
        return self.env.process(
            self._submit_tx(flits, message.nmc_shape_mode)
        )

    def recv_flit(self) -> Process:
        """Wait for one flit after calibrated NMC RX service."""
        return self.env.process(self._recv_flit())

    def _submit_tx(
        self,
        flits: tuple[Flit, ...],
        shape_mode: NMCShapeMode,
    ) -> ProcessGenerator:
        submission_time = float(self.env.now)
        descriptor_request: Request | None = None
        try:
            issue_request = self.descriptor_issuer.request()
            with issue_request:
                yield issue_request
                descriptor_request = self.descriptor_slots.request()
                yield descriptor_request
                descriptor_issue_start_time = float(self.env.now)
                yield self.env.timeout(self.config.descriptor_issue_cycles)

            descriptor_acceptance_time = float(self.env.now)
            endpoint_ready_time = (
                descriptor_issue_start_time
                + self.config.descriptor_issue_cycles
                + self.endpoint_setup_residual_aci_cycles(shape_mode)
            )

            completion = self.env.event()
            yield self.tx_data_queue.put(
                NMCTransmitEntry(
                    flits=flits,
                    shape_mode=shape_mode,
                    submission_time=submission_time,
                    descriptor_acceptance_time=descriptor_acceptance_time,
                    endpoint_ready_time=endpoint_ready_time,
                    completion=completion,
                )
            )
            yield completion
        finally:
            if descriptor_request is not None:
                if descriptor_request.triggered:
                    self.descriptor_slots.release(descriptor_request)
                else:
                    descriptor_request.cancel()

    def _recv_flit(self) -> ProcessGenerator:
        entry = cast(NMCReceiveEntry, (yield self.rx_data_queue.get()))
        return entry.flit

    def _tx_service_loop(self) -> ProcessGenerator:
        while True:
            entry = cast(NMCTransmitEntry, (yield self.tx_data_queue.get()))
            setup_wait = entry.endpoint_ready_time - float(self.env.now)
            if setup_wait > 0:
                yield self.env.timeout(setup_wait)
            request = self.tx_datapath.request()
            with request:
                yield request
                for flit in entry.flits:
                    yield self.env.timeout(self.tx_service_interval_aci_cycles)
                    yield self.binding.tx_link.send_flit(flit)
            entry.completion.succeed()

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
                        completion_time=float(self.env.now),
                    )
                )
                yield self.binding.rx_link.ack_credit()
