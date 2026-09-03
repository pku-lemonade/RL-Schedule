"""Resolution and validation of logical endpoints to physical NoC attachments."""

from .configs.schemas.arch_config import DMAEngineConfig, DMAType, NoCConfig
from .utils.definitions import (
    PORT_PE,
    DMAAttachmentMode,
    EndpointAddress,
    NoCChannel,
    NodeType,
    dma_port_layout,
    endpoint_local_port,
    expected_endpoint_router,
    valid_dma_attachment_modes,
    valid_endpoint_local_ports,
)

EndpointKey = tuple[NodeType, int]
PhysicalPort = tuple[NoCChannel, int, int]

_DMA_NODE_TYPES = {
    DMAType.GM_RDMA: NodeType.GM_RDMA,
    DMAType.GM_WDMA: NodeType.GM_WDMA,
    DMAType.DDR_RDMA: NodeType.DDR_RDMA,
    DMAType.DDR_WDMA: NodeType.DDR_WDMA,
}


def dma_node_type(dma_type: DMAType) -> NodeType:
    """Return the endpoint node type represented by a DMA configuration."""
    return _DMA_NODE_TYPES[dma_type]


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
                tuple(
                    EndpointAddress(
                        node_type=NodeType.PE,
                        node_id=pe_id,
                        fabric_id=fabric_id,
                        router_id=pe_id,
                        local_port=PORT_PE,
                    )
                    for fabric_id in NoCChannel
                ),
            )

        for dma_config in noc_config.dma_engines:
            self._register_dma(dma_config)

    def resolve(
        self,
        node_type: NodeType,
        node_id: int,
        *,
        fabric_id: NoCChannel,
        attachment_mode: DMAAttachmentMode | None = None,
    ) -> EndpointAddress:
        """Resolve one endpoint on an explicit fabric and DMA attachment mode."""
        key = (node_type, node_id)
        addresses = self._addresses.get(key)
        if addresses is None:
            raise KeyError(f"endpoint {node_type.name}[{node_id}] is not configured")

        if node_type is NodeType.PE and attachment_mode is not None:
            raise ValueError(
                "PE endpoint resolution does not use a DMA attachment mode"
            )
        if node_type is not NodeType.PE and attachment_mode is None:
            raise ValueError(
                f"endpoint {node_type.name}[{node_id}] requires an attachment mode"
            )

        for address in addresses:
            if (
                address.fabric_id is fabric_id
                and address.attachment_mode is attachment_mode
            ):
                return address
        mode_name = attachment_mode.name if attachment_mode is not None else "PE"
        raise ValueError(
            f"endpoint {node_type.name}[{node_id}] has no {mode_name} attachment "
            f"on {fabric_id.name}"
        )

    def configured_addresses(
        self,
        node_type: NodeType,
        node_id: int,
    ) -> tuple[EndpointAddress, ...]:
        """Return all validated physical addresses for a configured endpoint."""
        key = (node_type, node_id)
        try:
            return self._addresses[key]
        except KeyError as exc:
            raise KeyError(
                f"endpoint {node_type.name}[{node_id}] is not configured"
            ) from exc

    def _register_dma(self, config: DMAEngineConfig) -> None:
        node_type = dma_node_type(config.dma_type)
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
        matching_modes = tuple(
            attachment_mode
            for attachment_mode in valid_dma_attachment_modes(node_type)
            if dma_port_layout(node_type, attachment_mode) == port_layout
        )
        if len(matching_modes) != 1:
            raise ValueError(
                f"{node_type.name}[{config.instance_id}] uses unsupported local-port "
                f"layout {port_layout}"
            )
        attachment_mode = matching_modes[0]

        addresses = tuple(
            EndpointAddress(
                node_type=node_type,
                node_id=config.instance_id,
                fabric_id=fabric_id,
                router_id=config.router_id,
                local_port=endpoint_local_port(
                    node_type, fabric_id, attachment_mode
                ),
            )
            for fabric_id in NoCChannel
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
            physical_port = (
                address.fabric_id,
                address.router_id,
                address.local_port,
            )
            owner = self._physical_ports.get(physical_port)
            if owner is not None:
                raise ValueError(
                    f"{address.fabric_id.name} router {address.router_id} local port "
                    f"{address.local_port} "
                    f"is shared by {owner[0].name}[{owner[1]}] and "
                    f"{key[0].name}[{key[1]}]"
                )

        self._addresses[key] = addresses
        for address in addresses:
            self._physical_ports[
                (address.fabric_id, address.router_id, address.local_port)
            ] = key
