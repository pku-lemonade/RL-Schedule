import numpy as np
import gymnasium as gym
from enum import Enum, unique
from typing import Dict, Any, List, Tuple


@unique
class ActionType(Enum):
    ADD = "add"
    REMOVE = "remove"
    SWAP = "swap"


class ActionDecoder:
    def __init__(self, num_cores: int = 16):
        self.num_cores = num_cores
        
        # Add Action: 16 (Index 0 ~ 15)
        self.idx_add_start = 0
        self.idx_add_end = num_cores
        
        # Remove Action: 16 (Index 16 ~ 31)
        self.idx_remove_start = self.idx_add_end
        self.idx_remove_end = self.idx_remove_start + num_cores
        
        # Swap Action (8 nop): 128 (Index 32 ~ 159)
        self.idx_swap_start = self.idx_remove_end
        self.num_swap_slots = 128 
        self.idx_swap_end = self.idx_swap_start + self.num_swap_slots
        
        self.total_actions = self.idx_swap_end
        
        self.action_space = gym.spaces.Discrete(self.total_actions)

        self.swap_mapping: List[Tuple[int, int]] = []
        for i in range(self.num_cores):
            for j in range(i + 1, self.num_cores):
                self.swap_mapping.append((i, j))
        
        self.valid_swap_count = len(self.swap_mapping)


    def decode(self, action_id: int) -> Dict[str, Any]:
        if not (0 <= action_id < self.total_actions):
            raise ValueError(f"Action ID {action_id} out of bounds.")

        # 1. ADD: Add task to Core i
        if self.idx_add_start <= action_id < self.idx_add_end:
            target_core = action_id
            return {
                "type": ActionType.ADD,
                "core_id": target_core
            }

        # 2. REMOVE: Remove task from Core i
        elif self.idx_remove_start <= action_id < self.idx_remove_end:
            target_core = action_id - self.idx_remove_start
            return {
                "type": ActionType.REMOVE,
                "core_id": target_core
            }

        # 3. SWAP: Swap tasks between Core A and Core B
        elif self.idx_swap_start <= action_id < self.idx_swap_end:
            local_idx = action_id - self.idx_swap_start
            
            if local_idx < self.valid_swap_count:
                core_a, core_b = self.swap_mapping[local_idx]
                return {
                    "type": ActionType.SWAP,
                    "core_pair": (core_a, core_b)
                }
            else:
                raise ValueError(f"Action {action_id} maps to invalid Swap Padding region.")
        
        else:
            raise ValueError("Unknown Action ID")

    def get_action_mask(self) -> np.ndarray:
        raise NotImplementedError
