import numpy as np
import torch
import gymnasium as gym
from typing import Optional, Union, Tuple
from embedding.action_decoder import ActionDecoder


class ActionSpace:
    def __init__(self, num_cores: int = 16):
        self.decoder = ActionDecoder(num_cores=num_cores)
        self.n_actions = self.decoder.total_actions
        
        self.gym_space = gym.spaces.Discrete(self.n_actions)


    def get_gym_space(self) -> gym.spaces.Discrete:
        return self.gym_space

    # TODO
    def get_mask(self) -> np.ndarray:
        return self.decoder.get_action_mask()


    def decode(self, action_id: int):
        return self.decoder.decode(action_id)


    def select_action_from_q(self, 
                             q_values: Union[np.ndarray, torch.Tensor], 
                             mask: Optional[np.ndarray] = None) -> int:
        
        if isinstance(q_values, torch.Tensor):
            q_numpy = q_values.detach().cpu().numpy()
        else:
            q_numpy = np.array(q_values)
            
        q_numpy = q_numpy.flatten()
        
        if len(q_numpy) != self.n_actions:
            raise ValueError(f"Q-values dimension mismatch. Expected {self.n_actions}, got {len(q_numpy)}")

        if mask is not None:
            assert len(mask) == self.n_actions, "Mask dimension mismatch"
            
            neg_inf = np.full_like(q_numpy, -1e9)
            q_numpy = np.where(mask.astype(bool), q_numpy, neg_inf)

        action_id = np.argmax(q_numpy)
        return int(action_id)


    def sample_random_action(self, mask: Optional[np.ndarray] = None) -> int:
        if mask is None:
            return self.gym_space.sample()
        else:
            valid_indices = np.where(mask)[0]
            if len(valid_indices) == 0:
                raise RuntimeError("No valid actions available in the mask!")
            
            return int(np.random.choice(valid_indices))
