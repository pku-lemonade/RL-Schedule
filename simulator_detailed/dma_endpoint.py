"""Runtime ownership for one configured GM DMA endpoint."""

from dataclasses import dataclass

import simpy
from simpy.resources.resource import Resource

from .configs.schemas.arch_config import DMAEngineConfig
from .noc import Link, Router
from .utils.definitions import EndpointAddress, NoCChannel, NodeType


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


class DMAEndpoint:
    """One GM RDMA or WDMA whose internal datapath is shared by both fabrics."""

    def __init__(
        self,
        env: simpy.Environment,
        config: DMAEngineConfig,
        node_type: NodeType,
    ) -> None:
        if node_type not in (NodeType.GM_RDMA, NodeType.GM_WDMA):
            raise ValueError("Phase 3A DMA runtime supports GM endpoints only")
        self.env = env
        self.config = config
        self.node_type = node_type
        self.bindings: dict[NoCChannel, DMAChannelBinding] = {}

        # The hardware endpoint has one internal engine shared by CH0 and CH1.
        self.internal_datapath = Resource(env, capacity=1)

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


DMAEndpointKey = tuple[NodeType, int]
DMAEndpoints = dict[DMAEndpointKey, DMAEndpoint]


__all__ = [
    "DMAChannelBinding",
    "DMAEndpoint",
    "DMAEndpointKey",
    "DMAEndpoints",
]
