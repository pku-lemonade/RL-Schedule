from collections.abc import Mapping

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv

from ..noc import NoC
from ..utils.definitions import NoCChannel


def build_hardware_graph(
    noc_instances: Mapping[NoCChannel, NoC],
) -> tuple[torch.Tensor, torch.Tensor]:
    """
    return: 
        node_feature: (N_c + N_l, feature_dim)
        edge_feature: (2, num_edges)
    """
    if set(noc_instances) != set(NoCChannel):
        raise ValueError("hardware graph requires both CH0 and CH1 NoC fabrics")

    routers = [
        (fabric_id, router)
        for fabric_id in NoCChannel
        for router in noc_instances[fabric_id].routers
    ]
    links = [
        (fabric_id, link)
        for fabric_id in NoCChannel
        for link in noc_instances[fabric_id].r2r_links
    ]
    num_routers = len(routers)

    node_features = []
    router_indices = {}
    
    # router node feature
    for router_index, (fabric_id, router) in enumerate(routers):
        r_x, r_y = router.to_xy(router.id)
        node_features.append([0, fabric_id.value, r_x, r_y])
        router_indices[(fabric_id, router.id)] = router_index
        
    # link node feature
    for fabric_id, link in links:
        identity = link.identity
        noc = noc_instances[fabric_id]
        src_x = identity.src_router % noc.x
        src_y = identity.src_router // noc.x
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


class HardwareEmbedding(nn.Module):
    def __init__(self, num_nodes, input_dim=4, hidden_dim=16, output_dim=1):
        super(HardwareEmbedding, self).__init__()
        
        self.conv1 = GCNConv(input_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, hidden_dim)
        
        self.lin = nn.Linear(hidden_dim, output_dim)
        

    def forward(self, x, edge_index):
        # [N_c + N_l, feature_dim]
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=0.1, training=self.training)
        
        # [N_c + N_l, hidden_dim]
        x = self.conv2(x, edge_index)
        x = F.relu(x)
        
        # [N_c + N_l, 1]
        x = self.lin(x)
        return x
