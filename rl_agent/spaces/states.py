import numpy as np
import gymnasium as gym
from typing import List, Tuple
from pydantic import BaseModel
from utils.definitions import Trace, TraceItem, TimeSlice
from simulator.architecture import NoC
from embedding.hw_encoder import build_hardware_graph


class StateSpace:
    def __init__(self, num_cores: int, num_links: int, max_op_num: int):
        """
        Args:
            num_cores (int): N_c
            num_links (int): N_l
        """
        self.num_cores = num_cores
        self.num_links = num_links
        self.total_nodes = num_cores + num_links
        # (id, slow, ultilization, op_num)
        self.feats_per_node = 4 

        self.norm_core_id = float(max(1, num_cores - 1))
        self.norm_link_id = float(max(1, num_links - 1))

        self.norm_op_num = float(max(1, max_op_num))

        self._cached_hw_node = None
        self._cached_hw_edge = None


    def get_observation(self, trace: Trace, noc: NoC) -> dict:
        runtime = self.get_runtime_state(trace=trace)
        hw_node, hw_edge = self.get_hw_state(noc=noc)

        observation = {
            'runtime': runtime,
            'hw_node': hw_node,
            'hw_edge': hw_edge
        }
        return observation


    # def get_hw_state(self, noc: NoC) -> np.ndarray:
    #     node_feature, edge_feature = build_hardware_graph(noc)
    #     return node_feature, edge_feature
    

    def get_hw_state(self, noc: NoC) -> Tuple[np.ndarray, np.ndarray]:
        if self._cached_hw_node is None or self._cached_hw_edge is None:
            node_feature, edge_feature = build_hardware_graph(noc)
            
            self._cached_hw_node = node_feature.detach().cpu().numpy().astype(np.float32)
            self._cached_hw_edge = edge_feature.detach().cpu().numpy().astype(np.int64) 

        return self._cached_hw_node, self._cached_hw_edge


    def get_runtime_state(self, trace: Trace) -> np.ndarray:
        time_slices = trace.time_slices
        N = len(time_slices)
        
        if N == 0:
            return np.zeros((0, self.total_nodes), dtype=np.float32)

        # [N, 4, N_c + N_l]
        raw_matrix = np.zeros((N, self.feats_per_node, self.total_nodes), dtype=np.float32)

        for t, slice_data in enumerate(time_slices):
            sorted_cores = sorted(slice_data.cores, key=lambda x: x.id) 
            
            for i, core in enumerate(sorted_cores):
                if i >= self.num_cores: break
                
                raw_matrix[t, 0, i] = float(core.id) / self.norm_core_id
                raw_matrix[t, 1, i] = core.slow
                raw_matrix[t, 2, i] = core.ultilization
                raw_matrix[t, 3, i] = float(core.op_num) / self.norm_op_num

            sorted_links = sorted(slice_data.links, key=lambda x: x.id)
            
            for j, link in enumerate(sorted_links):
                if j >= self.num_links: break
                
                col_idx = self.num_cores + j
                
                raw_matrix[t, 0, col_idx] = float(link.id) / self.norm_link_id
                raw_matrix[t, 1, col_idx] = link.slow
                raw_matrix[t, 2, col_idx] = link.ultilization
                raw_matrix[t, 3, col_idx] = float(link.op_num) / self.norm_op_num

        # [N, 4, N_c + N_l] -> [4N, N_c + N_l]
        runtime_state = raw_matrix.reshape(-1, self.total_nodes)

        return runtime_state