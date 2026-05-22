from typing import Dict, List

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import HGTConv
from torch_geometric.data import HeteroData


class HeteroTemporalModel(nn.Module):
    def __init__(self,
                 in_dims: Dict[str, int],
                 hidden_dim: int = 64,
                 num_heads: int = 1,
                 num_layers: int = 2,
                 gru_hidden: int = 64,
                 gru_layers: int = 1,
                 core_count: int = 64,
                 link_count: int = 112):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.core_count = core_count
        self.link_count = link_count

        # Type-specific input projections
        self.in_proj = nn.ModuleDict({
            'core': nn.Linear(in_dims['core'], hidden_dim),
            'link': nn.Linear(in_dims['link'], hidden_dim),
        })

        # Define metadata for relations expected
        # We'll infer metapaths from data passed at forward
        self.num_layers = num_layers
        self.num_heads = num_heads

        # GRU over time for each node type separately
        self.core_gru = nn.GRU(hidden_dim, gru_hidden, num_layers=gru_layers, batch_first=True)
        self.link_gru = nn.GRU(hidden_dim, gru_hidden, num_layers=gru_layers, batch_first=True)

        # Node-level failure predictors (每个节点一个概率)
        self.core_mlp = nn.Sequential(
            nn.Linear(gru_hidden, gru_hidden), nn.ReLU(), nn.Linear(gru_hidden, 1)
        )
        self.link_mlp = nn.Sequential(
            nn.Linear(gru_hidden, gru_hidden), nn.ReLU(), nn.Linear(gru_hidden, 1)
        )
        
        # Global failure predictor (全局是否存在故障)
        self.global_mlp = nn.Sequential(
            nn.Linear(gru_hidden * 2, gru_hidden),  # 拼接core和link的全局特征
            nn.ReLU(), 
            nn.Linear(gru_hidden, 1)
        )
        self.noinst_core = nn.Parameter(torch.zeros(self.hidden_dim))
        self.noinst_link = nn.Parameter(torch.zeros(self.hidden_dim))

    def _hetero_block(self, data: HeteroData, x_dict):
        # Build an HGT layer using runtime metadata
        conv = HGTConv(in_channels={k: self.hidden_dim for k in x_dict.keys()},
                       out_channels=self.hidden_dim,
                       metadata=data.metadata(),
                       heads=self.num_heads)
        # Move the convolution layer to the same device as input
        device = next(iter(x_dict.values())).device
        conv = conv.to(device)
        return conv(x_dict, data.edge_index_dict)

    def _stack_time(self, xs: List[Dict[str, torch.Tensor]]):
        # xs: list over time of x_dict for each type
        # Stack per type -> [T, N_type, D]
        stacked = {}
        for t, x_dict in enumerate(xs):
            for k, v in x_dict.items():
                stacked.setdefault(k, []).append(v)
        for k in stacked:
            stacked[k] = torch.stack(stacked[k], dim=0)
        return stacked

    def forward(self, seq: List[HeteroData]):
        # seq length T, each HeteroData contains types 'core' and 'link'
        x_time: List[Dict[str, torch.Tensor]] = []
        for t, data in enumerate(seq):
            core_in = data['core'].x
            link_in = data['link'].x
            core_x = F.relu(self.in_proj['core'](core_in))
            link_x = F.relu(self.in_proj['link'](link_in))

            core_mask = (core_in.abs().sum(dim=1) == 0)
            if core_mask.any():
                core_x = torch.where(core_mask.unsqueeze(1), 
                                   self.noinst_core.unsqueeze(0).expand_as(core_x), 
                                   core_x)
            link_mask = (link_in.abs().sum(dim=1) == 0)
            if link_mask.any():
                link_x = torch.where(link_mask.unsqueeze(1), 
                                   self.noinst_link.unsqueeze(0).expand_as(link_x), 
                                   link_x)

            x_dict = {'core': core_x, 'link': link_x}

            # Run a few hetero layers with residual connections
            for _ in range(self.num_layers):
                x_dict_prev = x_dict  # 保存上一层特征
                x_dict_new = self._hetero_block(data, x_dict)
                # 残差连接：先激活新特征，再加上原特征
                x_dict = {k: x_dict_prev[k] + F.relu(x_dict_new[k]) for k in x_dict_new.keys()}
            x_time.append(x_dict)

        stacked = self._stack_time(x_time)  # {'core': [T,Nc,D], 'link': [T,Nl,D]}

        # Prepare for GRU: [N, T, D]
        core_seq = stacked['core'].permute(1, 0, 2)
        link_seq = stacked['link'].permute(1, 0, 2)

        core_out, _ = self.core_gru(core_seq)
        link_out, _ = self.link_gru(link_seq)

        core_last = core_out[:, -1, :]  # [N_core, hidden]
        link_last = link_out[:, -1, :]  # [N_link, hidden]

        # Node-level predictions (每个节点一个故障概率)
        core_logits = self.core_mlp(core_last)  # [N_core, 1]
        link_logits = self.link_mlp(link_last)  # [N_link, 1]

        # Global prediction (全局故障概率)
        # 使用全局池化得到core和link的全局特征
        core_global = torch.mean(core_last, dim=0, keepdim=True)  # [1, hidden]
        link_global = torch.mean(link_last, dim=0, keepdim=True)  # [1, hidden]
        global_feat = torch.cat([core_global, link_global], dim=1)  # [1, hidden*2]
        global_logits = self.global_mlp(global_feat)  # [1, 1]

        return core_logits, link_logits, global_logits


