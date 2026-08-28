from dataclasses import dataclass

import simpy

from .configs.schemas.arch_config import NMCChannelConfig
from .noc import Link, Router
from .utils.definitions import EndpointAddress, NoCChannel, NodeType


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


class NMCChannel:
    """Runtime resources for one independent, full-duplex PE NMC channel."""

    def __init__(
        self,
        env: simpy.Environment,
        config: NMCChannelConfig,
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
        self.binding = binding
        self.tx_datapath = simpy.Resource(env, capacity=1)
        self.rx_datapath = simpy.Resource(env, capacity=1)

        # Hardware data-FIFO depths are unresolved. Descriptor capacity is a
        # separate command-queue limit and is introduced in Fix 10.
        self.tx_data_queue = simpy.Store(env)
        self.rx_data_queue = simpy.Store(env)

    @property
    def fabric_id(self) -> NoCChannel:
        return self.binding.address.fabric_id
