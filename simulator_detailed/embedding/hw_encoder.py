import torch
import numpy as np
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv

from ..noc import NoC


def build_hardware_graph(noc_instance: NoC):
    """
    return: 
        node_feature: (N_c + N_l, feature_dim)
        edge_feature: (2, num_edges)
    """
    routers = noc_instance.routers
    links = noc_instance.r2r_links
    
    num_routers = len(routers) # 16 for 4x4
    num_links = len(links)     # 48 for 4x4
    total_nodes = num_routers + num_links

    node_features = []
    
    # router node feature
    for r in routers:
        r_row, r_col = r.to_xy(r.id)
        node_features.append([0, r_row, r_col]) 
        
    # link nore feature
    for i, link in enumerate(links):
        src_id = link.corefromid
        src_row = src_id // noc_instance.y
        src_col = src_id % noc_instance.y
        node_features.append([1, src_row, src_col])

    x = torch.tensor(node_features, dtype=torch.float)

    # connection feature
    source_nodes = []
    target_nodes = []
    
    for i, link in enumerate(links):
        link_node_idx = num_routers + i
        src_router_idx = link.corefromid
        dst_router_idx = link.coretoid
        
        # Router -> Link
        source_nodes.append(src_router_idx)
        target_nodes.append(link_node_idx)
        
        # Link -> Router
        source_nodes.append(link_node_idx)
        target_nodes.append(dst_router_idx)

    edge_index = torch.tensor([source_nodes, target_nodes], dtype=torch.long)
    
    return x, edge_index


class HardwareEmbedding(nn.Module):
    def __init__(self, num_nodes, input_dim=3, hidden_dim=16, output_dim=1):
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