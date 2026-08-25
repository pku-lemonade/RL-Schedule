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
                 gru_hidden2: int = 32,
                 gru_layers: int = 1,
                 core_count: int = 32,
                 link_count: int = 208,
                 # 池化参数
                 pool_to_length: int = 5,       # 池化后序列长度(None表示不池化)
                 pool_threshold: int = 5):      # 池化阈值,只有当原始长度 > 此阈值时才池化
        super().__init__()
        self.hidden_dim = hidden_dim
        self.core_count = core_count
        self.link_count = link_count
        self.gru_hidden = gru_hidden
        self.gru_hidden2 = gru_hidden2
        self.pool_to_length = pool_to_length
        self.pool_threshold = pool_threshold

        # Type-specific input projections
        self.in_proj = nn.ModuleDict({
            'core': nn.Linear(in_dims['core'], hidden_dim),
            'link': nn.Linear(in_dims['link'], hidden_dim),
        })

        # 第一层 GRU
        self.core_gru1 = nn.GRU(hidden_dim, gru_hidden, num_layers=gru_layers, batch_first=True)
        self.link_gru1 = nn.GRU(hidden_dim, gru_hidden, num_layers=gru_layers, batch_first=True)
        
        # 池化层(可选)
        self.time_pool = nn.AdaptiveAvgPool1d(pool_to_length) if pool_to_length else None
        
        # 第二层 GRU
        self.core_gru2 = nn.GRU(gru_hidden, gru_hidden2, num_layers=gru_layers, batch_first=True)
        self.link_gru2 = nn.GRU(gru_hidden, gru_hidden2, num_layers=gru_layers, batch_first=True)

        # 节点级 MLP（输入维度：第一层隐状态维 + 第二层隐状态维）
        node_in_dim = gru_hidden + gru_hidden2
        self.core_mlp = nn.Sequential(
            nn.Linear(node_in_dim, gru_hidden), nn.ReLU(), nn.Linear(gru_hidden, 1)
        )
        self.link_mlp = nn.Sequential(
            nn.Linear(node_in_dim, gru_hidden), nn.ReLU(), nn.Linear(gru_hidden, 1)
        )

        # 全局 MLP
        global_in_dim = node_in_dim * 2
        self.global_mlp = nn.Sequential(
            nn.Linear(global_in_dim, gru_hidden), 
            nn.ReLU(), 
            nn.Linear(gru_hidden, 1)
        )

        self.noinst_core = nn.Parameter(torch.zeros(self.hidden_dim))
        self.noinst_link = nn.Parameter(torch.zeros(self.hidden_dim))

        self.num_layers = num_layers
        self.num_heads = num_heads

    def _hetero_block(self, data: HeteroData, x_dict):
        conv = HGTConv(in_channels={k: self.hidden_dim for k in x_dict.keys()},
                       out_channels=self.hidden_dim,
                       metadata=data.metadata(),
                       heads=self.num_heads)
        device = next(iter(x_dict.values())).device
        conv = conv.to(device)
        return conv(x_dict, data.edge_index_dict)

    def _stack_time(self, xs: List[Dict[str, torch.Tensor]]):
        stacked = {}
        for t, x_dict in enumerate(xs):
            for k, v in x_dict.items():
                stacked.setdefault(k, []).append(v)
        for k in stacked:
            stacked[k] = torch.stack(stacked[k], dim=0)  # [T, N_type, D]
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
            for _ in range(self.num_layers):
                x_dict_prev = x_dict # 保存上一层特征
                x_dict_new = self._hetero_block(data, x_dict)
                # 残差连接：先激活新特征，再加上原特征
                x_dict = {k: x_dict_prev[k] + F.relu(x_dict_new[k]) for k in x_dict_new.keys()}
            x_time.append(x_dict)

        stacked = self._stack_time(x_time)  # {'core': [T, N_core, D], 'link': [T, N_link, D]}

        # Prepare for GRU: [N, T, D]
        core_seq = stacked['core'].permute(1, 0, 2)   # [N_core, T, hidden_dim]
        link_seq = stacked['link'].permute(1, 0, 2)   # [N_link, T, hidden_dim]

        # 第一层 GRU
        core_out1, _ = self.core_gru1(core_seq)   # [N_core, T, gru_hidden]
        link_out1, _ = self.link_gru1(link_seq)   # [N_link, T, gru_hidden]

        original_core_out1 = core_out1
        original_link_out1 = link_out1
        # 时间维度池化压缩(当长度超过阈值pool_threshold时)
        T_orig = core_out1.size(1)
        if self.time_pool is not None and T_orig > self.pool_threshold:
            # 使用自适应平均池化，自动处理任意长度（无需担心整除）
            # 输入形状 [N, T, D] -> 转为 [N, D, T] 进行池化
            core_out1_perm = core_out1.permute(0, 2, 1)   # [N_core, gru_hidden, T]
            link_out1_perm = link_out1.permute(0, 2, 1)   # [N_link, gru_hidden, T]
            # 自适应池化到目标长度
            core_pooled = self.time_pool(core_out1_perm)            # [N_core, gru_hidden, T']
            link_pooled = self.time_pool(link_out1_perm)            # [N_link, gru_hidden, T']
            # 转回 [N, T', D]
            core_out1 = core_pooled.permute(0, 2, 1)      # [N_core, T', gru_hidden]
            link_out1 = link_pooled.permute(0, 2, 1)      # [N_link, T', gru_hidden]


        # 第二层 GRU(输入可能是压缩后的序列)
        core_out2, _ = self.core_gru2(core_out1)   # [N_core, T', gru_hidden2]
        link_out2, _ = self.link_gru2(link_out1)   # [N_link, T', gru_hidden2]

        # 取最后一时间步输出
        core_last1 = original_core_out1[:, -1, :]   # [N_core, gru_hidden]
        link_last1 = original_link_out1[:, -1, :]   # [N_link, gru_hidden]
        core_last2 = core_out2[:, -1, :]   # [N_core, gru_hidden2]
        link_last2 = link_out2[:, -1, :]   # [N_link, gru_hidden2]

        core_feat = torch.cat([core_last1, core_last2], dim=1)  # [N_core, gru_hidden+gru_hidden2]
        link_feat = torch.cat([link_last1, link_last2], dim=1)  # [N_link, gru_hidden+gru_hidden2]

        # 节点级预测
        core_logits = self.core_mlp(core_feat)
        link_logits = self.link_mlp(link_feat)

        # 全局预测
        core_global = torch.mean(core_feat, dim=0, keepdim=True)
        link_global = torch.mean(link_feat, dim=0, keepdim=True)
        global_feat = torch.cat([core_global, link_global], dim=1)
        global_logits = self.global_mlp(global_feat)

        return core_logits, link_logits, global_logits
