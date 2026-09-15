"""Resolution and validation of logical endpoints to physical NoC attachments."""

from .configs.schemas.arch_config import DMAType, NoCConfig
from .topology import Topology, topology_from_legacy
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

    def __init__(self, noc_config: NoCConfig, topology: Topology | None = None):
        self.config = noc_config
        self.topology = topology if topology is not None else topology_from_legacy(noc_config)

        self._addresses: dict[EndpointKey, tuple[EndpointAddress, ...]] = {}
        self._physical_ports: dict[PhysicalPort, EndpointKey] = {}

        grouped: dict[EndpointKey, list[EndpointAddress]] = {}
        for endpoint in self.topology.graph.attachments:
            binding = endpoint.legacy_binding
            if binding is None:
                continue
            if endpoint.enabled is not True or not endpoint.permissions_resolved:
                raise ValueError("legacy endpoint binding must be available and resolved")
            if endpoint.inject_port is None or endpoint.inject_port != endpoint.eject_port:
                raise ValueError("legacy endpoint requires a paired local port")
            node_type = NodeType[binding.node_type]
            address = EndpointAddress(
                node_type=node_type, node_id=binding.node_id,
                fabric_id=NoCChannel(endpoint.fabric_id),
                router_id=self.topology.router_indices[(endpoint.fabric_id, endpoint.router_id)],
                local_port=self.topology.port_indices[(endpoint.fabric_id, endpoint.router_id, endpoint.inject_port)],
                attachment_mode=(None if binding.attachment_mode is None else DMAAttachmentMode(binding.attachment_mode)),
                format=noc_config.router.flit, mesh_x=noc_config.x, mesh_y=noc_config.y,
            )
            grouped.setdefault((node_type, binding.node_id), []).append(address)
        for key, addresses in grouped.items():
            self._register(key, tuple(sorted(addresses, key=lambda a: noc_config.fabric_ids.index(a.fabric_id))))

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
