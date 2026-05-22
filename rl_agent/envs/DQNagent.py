import torch
import torch.nn as nn
import gymnasium as gym
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from embedding.hw_encoder import HardwareEmbedding


class QNetwork(BaseFeaturesExtractor):
    def __init__(self, observation_space: gym.spaces.Dict, gcn_embed_dim=1, output_dim=160):
        """
        observation_space: gym.spaces.Dict
        gcn_embed_dim: default = 1
        """
        super().__init__(observation_space, features_dim=output_dim)
        
        # runtime.shape -> (4N, N_c + N_l)
        runtime_shape = observation_space['runtime'].shape
        self.seq_len_runtime = runtime_shape[0]  # 4N
        self.num_nodes = runtime_shape[1]        # Nc + Nl
        
        # hw_x.shape -> (Nc + Nl, hw_feat_dim)
        self.hw_feat_dim = observation_space['hw_node'].shape[1]

        # hardware embedding
        self.encoder = HardwareEmbedding(num_nodes=self.num_nodes,
                                         input_dim=self.hw_feat_dim,
                                         output_dim=gcn_embed_dim)

        self.total_input_len = self.seq_len_runtime + gcn_embed_dim 
        
        self.conv_layer = nn.Sequential(
            nn.Conv1d(
                in_channels  = self.num_nodes,
                out_channels = 128,           
                kernel_size  = 5,             
                stride       = 5
            ),
            nn.LeakyReLU(),
            nn.Flatten()
        )

        with torch.no_grad():
            dummy_input = torch.zeros(1, self.num_nodes, self.total_input_len)
            flatten_output = self.conv_layer(dummy_input)
            self.flatten_dim = flatten_output.shape[1]
        
        self.linear_layer = nn.Sequential(
            nn.Linear(self.flatten_dim, 2048),
            nn.ReLU(),
            nn.Linear(2048, 1024),
            nn.ReLU(),
            nn.Linear(1024, output_dim),
            nn.ReLU(),
        )


    def forward(self, observations: gym.spaces.Dict):
        """
        SB3 requires 1 parameter only (observations)
        """
        # runtime: [Batch, 4N, N_c + N_l]
        runtime_stats = observations['runtime'] 
        hw_node = observations['hw_node']           
        hw_edge = observations['hw_edge'] 
        batch_size = runtime_stats.shape[0]
        
        # embedding: [Batch, N_c + N_l, 1]
        # hw_embedding = self.encoder(hw_node, hw_edge)

        gcn_outputs = []
        for i in range(batch_size):
            out = self.encoder(hw_node[i], hw_edge[i])
            gcn_outputs.append(out)
        
        hw_embedding = torch.stack(gcn_outputs)

        # [Batch, N_c + N_l, 4N]
        runtime_stats_t = runtime_stats.permute(0, 2, 1)
        
        # [Batch, Nodes, 4N] + [Batch, Nodes, 1] -> [Batch, Nodes, 4N+1]
        combined_state = torch.cat([runtime_stats_t, hw_embedding], dim=2)
        
        # [Batch, 1024]
        flattened = self.conv_layer(combined_state)

        # [Batch, output_dim]
        output = self.linear_layer(flattened)

        return output
