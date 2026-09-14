"""Resolution and validation of logical endpoints to physical NoC attachments."""

from .configs.schemas.arch_config import DMAEngineConfig, DMAType, NoCConfig
from .utils.definitions import (
    DMAAttachmentMode,
    EndpointAddress,
    NoCChannel,
    NodeType,
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
        self.config = noc_config
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
                        local_port=noc_config.pe_local_port,
                        format=noc_config.router.flit,
                        mesh_x=noc_config.x,
                        mesh_y=noc_config.y,
                    )
                    for fabric_id in noc_config.fabric_ids
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
        addresses = tuple(
            EndpointAddress(
                node_type=node_type,
                node_id=config.instance_id,
                fabric_id=fabric_id,
                router_id=config.router_id,
                local_port=config.local_ports[
                    0 if len(config.local_ports) == 1 else index
                ],
                attachment_mode=config.attachment_mode,
                format=self.config.router.flit,
                mesh_x=self.config.x,
                mesh_y=self.config.y,
            )
            for index, fabric_id in enumerate(config.fabric_ids)
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
