"""Validate legacy graph inputs before loading optional tensor dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..noc import NoC
from ..topology_compatibility import legacy_coordinates, require_legacy_nocs
from ..utils.definitions import NoCChannel

if TYPE_CHECKING:
    import torch

    from ._hardware_embedding import HardwareEmbedding

__all__ = ["HardwareEmbedding", "build_hardware_graph"]


def __getattr__(name: str):
    if name != "HardwareEmbedding":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ._hardware_embedding import HardwareEmbedding

    # Keep the public import/pickle location and cache the same class object.
    HardwareEmbedding.__module__ = __name__
    globals()[name] = HardwareEmbedding
    return HardwareEmbedding


def build_hardware_graph(
    noc_instances: Mapping[NoCChannel, NoC],
) -> tuple[torch.Tensor, torch.Tensor]:
    """
    return: 
        node_feature: (N_c + N_l, feature_dim)
        edge_feature: (2, num_edges)
    """
    noc_instances, topology = require_legacy_nocs(noc_instances)
    import torch

    coordinates = legacy_coordinates(topology)

    routers = [
        (fabric_id, router)
        for fabric_id in sorted(noc_instances)
        for router in noc_instances[fabric_id].routers
    ]
    links = [
        (fabric_id, link)
        for fabric_id in sorted(noc_instances)
        for link in noc_instances[fabric_id].r2r_links
    ]
    num_routers = len(routers)

    node_features = []
    router_indices = {}
    
    # router node feature
    for router_index, (fabric_id, router) in enumerate(routers):
        r_x, r_y = coordinates[(int(fabric_id), router.id)]
        node_features.append([0, fabric_id.value, r_x, r_y])
        router_indices[(fabric_id, router.id)] = router_index
        
    # link node feature
    for fabric_id, link in links:
        identity = link.identity
        src_x, src_y = coordinates[(int(fabric_id), identity.src_router)]
        node_features.append([1, fabric_id.value, src_x, src_y])

    x = torch.tensor(node_features, dtype=torch.float)

    # connection feature
    source_nodes = []
    target_nodes = []
    
    for i, (fabric_id, link) in enumerate(links):
        identity = link.identity
        link_node_idx = num_routers + i
        src_router_idx = router_indices[(fabric_id, identity.src_router)]
        dst_router_idx = router_indices[(fabric_id, identity.dst_router)]
        
        # Router -> Link
        source_nodes.append(src_router_idx)
        target_nodes.append(link_node_idx)
        
        # Link -> Router
        source_nodes.append(link_node_idx)
        target_nodes.append(dst_router_idx)

    edge_index = torch.tensor([source_nodes, target_nodes], dtype=torch.long)
    
    return x, edge_index
