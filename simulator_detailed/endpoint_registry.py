"""Resolution and validation of logical endpoints to physical NoC attachments."""

from .configs.schemas.arch_config import DMAEngineConfig, DMAType, NoCConfig
from .utils.definitions import (
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
    EndpointAddress,
    NodeType,
    expected_endpoint_router,
    valid_endpoint_local_ports,
)

EndpointKey = tuple[NodeType, int]
PhysicalPort = tuple[int, int]

_DMA_NODE_TYPES = {
    DMAType.GM_RDMA: NodeType.GM_RDMA,
    DMAType.GM_WDMA: NodeType.GM_WDMA,
    DMAType.DDR_RDMA: NodeType.DDR_RDMA,
    DMAType.DDR_WDMA: NodeType.DDR_WDMA,
}

_VALID_DMA_PORT_LAYOUTS = {
    NodeType.GM_RDMA: {(PORT_GM_RDMA,), (PORT_GM_RDMA_LOC,)},
    NodeType.GM_WDMA: {
        (PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1),
        (PORT_GM_WDMA_LOC,),
        (PORT_GM_WDMA,),
    },
    NodeType.DDR_RDMA: {(PORT_DDR_RDMA,), (PORT_DDR_RDMA_LOC,)},
    NodeType.DDR_WDMA: {
        (PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1),
        (PORT_DDR_WDMA_LOC,),
        (PORT_DDR_WDMA,),
    },
}


class EndpointRegistry:
    """Architecture-owned map from endpoint identity to NoC attachment."""

    def __init__(self, noc_config: NoCConfig):
        if (noc_config.x, noc_config.y) != (4, 8):
            raise ValueError("ADA2S-32 endpoint mapping requires a 4x8 NoC")
        router_count = noc_config.x * noc_config.y

        self._addresses: dict[EndpointKey, tuple[EndpointAddress, ...]] = {}
        self._physical_ports: dict[PhysicalPort, EndpointKey] = {}

        for pe_id in range(router_count):
            self._register(
                (NodeType.PE, pe_id),
                (
                    EndpointAddress(
                        node_type=NodeType.PE,
                        node_id=pe_id,
                        router_id=pe_id,
                        local_port=PORT_PE,
                    ),
                ),
            )

        for dma_config in noc_config.dma_engines:
            self._register_dma(dma_config)

    def resolve(
        self,
        node_type: NodeType,
        node_id: int,
        *,
        local_port: int | None = None,
    ) -> EndpointAddress:
        """Resolve one endpoint, requiring a port when its attachment is ambiguous."""
        key = (node_type, node_id)
        addresses = self._addresses.get(key)
        if addresses is None:
            raise KeyError(f"endpoint {node_type.name}[{node_id}] is not configured")

        if local_port is not None:
            for address in addresses:
                if address.local_port == local_port:
                    return address
            raise ValueError(
                f"endpoint {node_type.name}[{node_id}] has no local port {local_port}"
            )

        if len(addresses) != 1:
            ports = ", ".join(str(address.local_port) for address in addresses)
            raise ValueError(
                f"endpoint {node_type.name}[{node_id}] has multiple local ports "
                f"({ports}); select one explicitly"
            )
        return addresses[0]

    def _register_dma(self, config: DMAEngineConfig) -> None:
        node_type = _DMA_NODE_TYPES[config.dma_type]
        if not 0 <= config.instance_id < 4:
            raise ValueError(f"{node_type.name} instance_id must be between 0 and 3")
        expected_router = expected_endpoint_router(node_type, config.instance_id)
        if config.router_id != expected_router:
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] must attach to router "
                f"{expected_router}, not router {config.router_id}"
            )
        if config.channels <= 0:
            raise ValueError(f"{node_type.name}[{config.instance_id}] has no channels")
        if len(config.local_ports) != config.channels:
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] declares {config.channels} "
                f"channels but {len(config.local_ports)} local ports"
            )
        if len(set(config.local_ports)) != len(config.local_ports):
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] repeats a local port"
            )
        valid_ports = valid_endpoint_local_ports(node_type)
        invalid_ports = [
            port for port in config.local_ports if port not in valid_ports
        ]
        if invalid_ports:
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] uses invalid local ports "
                f"{invalid_ports}"
            )
        port_layout = tuple(config.local_ports)
        if port_layout not in _VALID_DMA_PORT_LAYOUTS[node_type]:
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] uses unsupported local-port "
                f"layout {port_layout}"
            )

        addresses = tuple(
            EndpointAddress(
                node_type=node_type,
                node_id=config.instance_id,
                router_id=config.router_id,
                local_port=local_port,
            )
            for local_port in config.local_ports
        )
        self._register((node_type, config.instance_id), addresses)

    def _register(
        self,
        key: EndpointKey,
        addresses: tuple[EndpointAddress, ...],
    ) -> None:
        if key in self._addresses:
            raise ValueError(f"endpoint {key[0].name}[{key[1]}] is configured twice")

        for address in addresses:
            physical_port = (address.router_id, address.local_port)
            owner = self._physical_ports.get(physical_port)
            if owner is not None:
                raise ValueError(
                    f"router {address.router_id} local port {address.local_port} "
                    f"is shared by {owner[0].name}[{owner[1]}] and "
                    f"{key[0].name}[{key[1]}]"
                )

        self._addresses[key] = addresses
        for address in addresses:
            self._physical_ports[(address.router_id, address.local_port)] = key
