"""Optional tensor model, loaded through hw_encoder.HardwareEmbedding."""

import torch.nn.functional as F
from torch import nn
from torch_geometric.nn import GCNConv


class HardwareEmbedding(nn.Module):
    def __init__(self, num_nodes, input_dim=4, hidden_dim=16, output_dim=1):
        super().__init__()
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
