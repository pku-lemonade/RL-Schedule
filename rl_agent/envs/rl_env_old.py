import os
import glob
import random
import numpy as np
import gymnasium as gym
from gymnasium import spaces

from rl_agent.spaces.states import StateSpace
from rl_agent.spaces.actions import ActionSpace
from embedding.action_decoder import ActionType

from simulator.run import simulate
from simulator.architecture import NoC
from utils.mapper import NetworkMapper
from configs.schemas.mapping_config import *
from rl_agent.reward.reward_function import RewardCalculator


class FailSlowEnv(gym.Env):
    # default: mesh 4x4
    def __init__(self, 
                 initial_mapping: Network, 
                 hardware_config: str = "configs/instances/gemini4_4.json",
                 failure_path: str = "fail_dataset",
                 num_cores: int = 16, 
                 num_links: int = 48, 
                 max_op_num: int = 1000, 
                 max_steps: int = 200,
                 performance_wgt: float = 2.0,
                 core_thermal_wgt: float = 1.0,
                 link_thermal_wgt: float = 0.5):
        
        super(FailSlowEnv, self).__init__()

        # basic parameters
        self.num_cores = num_cores
        self.num_links = num_links
        self.max_steps = max_steps
        
        self.hw_config = hardware_config
        self.initial_mapping = initial_mapping 

        # failslow dataset
        if not os.path.exists(failure_path):
            raise ValueError(f"Dataset directory not found: {failure_path}")
        
        self.fault_files = sorted(glob.glob(os.path.join(failure_path, "*.json")))
        
        if len(self.fault_files) == 0:
            raise ValueError(f"No .json files found in {failure_path}")
        
        print(f"[FailSlowEnv] Loaded {len(self.fault_files)} failslow scenarios from {failure_path}")
        
        self.cur_failure_path = None

        # reward function
        self.reward = RewardCalculator(alpha = performance_wgt,
                                       beta_core = core_thermal_wgt,
                                       beta_link = link_thermal_wgt)

        # space generators
        self.state_manager = StateSpace(num_cores, num_links, max_op_num)
        self.action_manager = ActionSpace(num_cores)

        self.mapper = NetworkMapper(network=initial_mapping)
        self.step_count = 0

        # space definitions
        self.action_space = self.action_manager.get_gym_space()

        self.seq_len_runs = 11   # hard code now
        total_nodes = num_cores + num_links
        hw_node_feats = 3
        num_edges = num_links * 2

        self.observation_space = spaces.Dict({
            'runtime': spaces.Box(
                            low = -np.inf, high = np.inf, 
                            shape = (self.seq_len_runs * 4, total_nodes), 
                            dtype = np.float32
                        ),
            'hw_node': spaces.Box(
                            low = -np.inf, high = np.inf, 
                            shape = (total_nodes, hw_node_feats), 
                            dtype = np.float32
                        ),
            'hw_edge': spaces.Box(
                            low = -np.inf, high = np.inf, 
                            shape = (2, num_edges), 
                            dtype = np.float32
                        ),
        })


    def reset(self, seed = None, options = None):
        super().reset(seed=seed)
        self.step_count = 0

        # reset mapper
        self.mapper = NetworkMapper(network=self.initial_mapping)

        # failslow setup
        if options and 'config_path' in options:
            self.cur_failure_path = options['config_path']
        else:
            self.cur_failure_path = random.choice(self.fault_files)
            print(f"[FailSlowEnv] Choose {self.cur_failure_path} as failslow setting.")

        # initial observation
        cycles, traces, noc_instance = simulate(self.hw_config, self.cur_failure_path, self.mapper)
        self.reward.reset(initial_cycles=cycles)

        obs = self.state_manager.get_observation(
            trace = traces,
            noc = noc_instance
        )
        self.last_obs = obs

        # TODO: initial masking
        info = self._get_info()
        return obs, info

    # apply an action
    def step(self, action_id):
        # action_detail = self.action_manager.decode(int(action_id))
        # legal_move = self._apply_action_to_mapper(action_detail)
        try:
            action_detail = self.action_manager.decode(int(action_id))
            legal_move = self._apply_action_to_mapper(action_detail)
        except (ValueError, IndexError) as e:
            legal_move = False
            action_detail = None

        if legal_move:
            cycles, traces, noc_instance = simulate(self.hw_config, self.cur_failure_path, self.mapper)

            obs = self.state_manager.get_observation(
                trace = traces,
                noc = noc_instance
            )
            self.last_obs = obs
        
            reward = self.reward.calculate(
                cycles = cycles,
                trace = traces,
                legal_move = legal_move
            )
        else:
            obs = self.last_obs
            reward = self.reward.calculate(legal_move=legal_move)

        self.step_count += 1
        terminated = False
        truncated = self.step_count >= self.max_steps
        info = self._get_info()
        reward_info = reward[1]

        return obs, reward[0], terminated, truncated, info


    def _apply_action_to_mapper(self, action_detail):
        action_type = action_detail['type']
        
        try:
            if action_type == ActionType.ADD:
                core_id = action_detail['core_id']
                self.mapper.add_core(core=core_id)
                
            elif action_type == ActionType.REMOVE:
                core_id = action_detail['core_id']
                self.mapper.remove_core(core=core_id)
                
            elif action_type == ActionType.SWAP:
                c1, c2 = action_detail['core_pair']
                self.mapper.swap_core(core1=c1, core2=c2)

            return True
        
        except Exception:
            return False
        

    def _get_info(self):
        return {}
