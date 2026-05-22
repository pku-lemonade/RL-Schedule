import copy
import os
import glob
import random
import time
import numpy as np
import gymnasium as gym
from gymnasium import spaces

from rl_agent.spaces.states import StateSpace
# from rl_agent.spaces.actions import ActionSpace # 不再需要，我们手动处理多维动作

from simulator.run import simulate
from simulator.architecture import NoC
from utils.mapper import NetworkMapper
from configs.schemas.mapping_config import *
from rl_agent.reward.reward_function import RewardCalculator
from rl_agent.workflow_config import ACTION_ID_TO_NAME, build_action_config
from utils.timing_logger import log_timing


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
                 link_thermal_wgt: float = 0.5,
                 action_config: dict = None):
        
        super(FailSlowEnv, self).__init__()

        # basic parameters
        self.num_cores = num_cores
        self.num_links = num_links
        self.max_steps = max_steps
        
        self.hw_config = hardware_config
        if hasattr(initial_mapping, "model_copy"):
            self.initial_mapping = initial_mapping.model_copy(deep=True)
        else:
            self.initial_mapping = copy.deepcopy(initial_mapping)

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
        
        self.mapper = NetworkMapper(network=initial_mapping)
        self.step_count = 0

        # ActionSpace (MultiDiscrete)
        if action_config is None:
            action_config = build_action_config(num_layers=len(self.initial_mapping.layers), num_cores=num_cores)
        self.action_config = action_config
        
        # [Layer_ID, Src_Core_ID, Dst_Core_ID, Op_Type]
        self.action_space = spaces.MultiDiscrete([
            action_config['num_layers'],
            action_config['num_cores'],
            action_config['num_cores'],
            action_config['num_ops']
        ])

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

        self.last_obs = None
        self.last_trace = None
        self.last_cycles = None
        self.last_info = {}


    def _build_info(self, action_report=None, reward_info=None):
        info = {
            "failure_path": self.cur_failure_path,
            "step_count": self.step_count,
            "cycles": self.last_cycles,
        }
        if action_report is not None:
            info["action_report"] = action_report
        if reward_info is not None:
            info["reward"] = reward_info
        return info


    def _run_current_mapping(self):
        cycles, traces, noc_instance = simulate(self.hw_config, self.cur_failure_path, self.mapper)
        obs = self.state_manager.get_observation(trace=traces, noc=noc_instance)
        self.last_obs = obs
        self.last_trace = traces
        self.last_cycles = cycles
        return cycles, traces, noc_instance, obs


    def reset(self, seed = None, options = None):
        reset_started = time.perf_counter()
        super().reset(seed=seed)
        self.step_count = 0

        # reset mapper to a clean baseline copy
        self.mapper = NetworkMapper(network=self.initial_mapping)
        try:
            self.mapper.gen_dfg()
        except Exception as e:
            print(f"Warning: mapper.gen_dfg() failed during reset: {e}")

        # failslow setup
        if options and 'config_path' in options:
            self.cur_failure_path = options['config_path']
        else:
            self.cur_failure_path = random.choice(self.fault_files)
            print(f"[FailSlowEnv] Choose {self.cur_failure_path} as failslow setting.")

        log_timing(
            "env.reset.start",
            seed=seed,
            failure_path=self.cur_failure_path,
        )

        # initial observation
        cycles, traces, noc_instance, obs = self._run_current_mapping()
        self.reward.reset(initial_cycles=cycles)
        self.last_info = self._build_info()
        info = self.last_info
        log_timing(
            "env.reset",
            seed=seed,
            failure_path=self.cur_failure_path,
            cycles=cycles,
            total_duration_ms=round((time.perf_counter() - reset_started) * 1000, 3),
        )
        return obs, info

    # apply an action
    def step(self, action):
        step_started = time.perf_counter()
        action_report = None
        remap_duration_ms = 0.0
        rerun_duration_ms = 0.0
        current_step_index = self.step_count + 1
        raw_action = list(np.asarray(action, dtype=np.int64))
        log_timing(
            "env.step.start",
            step_index=current_step_index,
            action=raw_action,
            failure_path=self.cur_failure_path,
            cycles=self.last_cycles,
        )
        try:
            remap_started = time.perf_counter()
            action_report = self._apply_action_to_mapper(action)
            remap_duration_ms = round((time.perf_counter() - remap_started) * 1000, 3)
            legal_move = action_report["accepted"]
        except Exception as e:
            print(f"[Env Error] Action Application Failed: {e}")
            action_report = {
                "accepted": False,
                "reason": str(e),
                "action": raw_action,
            }
            legal_move = False

        if legal_move:
            rerun_started = time.perf_counter()
            cycles, traces, noc_instance, obs = self._run_current_mapping()
            rerun_duration_ms = round((time.perf_counter() - rerun_started) * 1000, 3)
        
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
        
        reward_info = reward[1]
        info = self._build_info(action_report=action_report, reward_info=reward_info)
        self.last_info = info

        log_timing(
            "env.step",
            step_index=self.step_count,
            action=raw_action,
            accepted=bool(action_report and action_report.get("accepted")),
            action_type=None if action_report is None else action_report.get("action_type"),
            reason=None if action_report is None else action_report.get("reason"),
            cycles=self.last_cycles,
            remap_duration_ms=remap_duration_ms,
            rerun_duration_ms=rerun_duration_ms,
            total_duration_ms=round((time.perf_counter() - step_started) * 1000, 3),
        )

        return obs, reward[0], terminated, truncated, info


    def _apply_action_to_mapper(self, action):
        layer_id_raw = int(action[0])
        src_core = int(action[1])
        dst_core = int(action[2])
        op_type = int(action[3])

        num_layers = len(self.mapper.network.layers)
        if num_layers == 0:
            return {"accepted": False, "reason": "no layers available", "action": [layer_id_raw, src_core, dst_core, op_type]}
        
        real_layer_id = min(layer_id_raw, num_layers - 1)
        action_name = ACTION_ID_TO_NAME.get(op_type)
        if action_name is None:
            return {
                "accepted": False,
                "reason": f"unknown action id {op_type}",
                "action": [real_layer_id, src_core, dst_core, op_type],
            }

        report = self.mapper.apply_local_remap(
            layer_id=real_layer_id,
            action_type=action_name,
            src_core=src_core,
            dst_core=dst_core,
            trace=self.last_trace,
        )
        return {
            "accepted": report.accepted,
            "reason": report.reason,
            "action": [real_layer_id, src_core, dst_core, op_type],
            "action_type": action_name,
            "details": report.details,
        }


    def _get_info(self):
        return self.last_info
