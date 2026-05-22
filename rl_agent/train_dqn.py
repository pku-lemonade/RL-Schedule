import os
import torch
import numpy as np
import multiprocessing
from datetime import datetime

# Stable Baselines3
from stable_baselines3 import DQN
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback, CallbackList

from rl_agent.envs.rl_env import FailSlowEnv
from rl_agent.envs.DQNagent import QNetwork
from rl_agent.envs.flatten_action_wrapper import FlattenActionWrapper
from utils.mapper import parse_mapping
from rl_agent.workflow_config import (
    DEFAULT_DATASET_DIR,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    DQN_LOG_DIR,
    DQN_MODEL_DIR,
    build_action_config,
)

LOG_DIR = DQN_LOG_DIR
MODEL_DIR = DQN_MODEL_DIR
DATASET_DIR = DEFAULT_DATASET_DIR
WORKLOAD_PATH = DEFAULT_WORKLOAD_PATH
HW_CONFIG_PATH = DEFAULT_HW_CONFIG_PATH


def make_env(rank: int, seed: int = 0):
    def _init():
        initial_network = parse_mapping(WORKLOAD_PATH)
        action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
        
        env = FlattenActionWrapper(FailSlowEnv(
            initial_mapping = initial_network,
            hardware_config = HW_CONFIG_PATH,
            failure_path = DATASET_DIR,
            num_cores = DEFAULT_NUM_CORES,
            num_links = DEFAULT_NUM_LINKS,
            max_steps = 200,
            performance_wgt = 2.0,
            core_thermal_wgt = 1.0,
            action_config = action_config
        ))

        # use different seeds
        env.reset(seed = seed + rank)
        
        # logging
        log_path = os.path.join(LOG_DIR, f"env_{rank}")
        os.makedirs(log_path, exist_ok=True)
        return Monitor(env, filename=log_path)
    
    return _init


def main():
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    tensorboard_log = os.path.join(LOG_DIR, run_name)
    checkpoint_path = os.path.join(MODEL_DIR, run_name)
    os.makedirs(checkpoint_path, exist_ok=True)

    print(f"Start Training. Run Name: {run_name}")
    print(f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}")

    num_cpu = 64
    print(f"Initializing {num_cpu} parallel environments (SubprocVecEnv)...")
    
    # train environment
    # train_env = DummyVecEnv([make_env(rank=0)])
    train_env = SubprocVecEnv([make_env(rank=i, seed=i) for i in range(num_cpu)])
    # evaluation environment
    # eval_env = DummyVecEnv([make_env(rank=1)])
    eval_env = DummyVecEnv([make_env(rank=num_cpu, seed=num_cpu)])

    # policy definition
    policy_kwargs = dict(
        features_extractor_class = QNetwork,
        features_extractor_kwargs = dict(
            gcn_embed_dim = 1,
            output_dim = 256
        ),
        net_arch = [128, 64] 
    )

    model = DQN(
        policy = "MultiInputPolicy",
        env = train_env,
        learning_rate = 1e-4,             # 学习率
        buffer_size = 500,                # Replay Buffer 大小 (根据内存调整)
        learning_starts = 500,            # 收集多少步经验后开始训练
        batch_size = 64,                  # 批次大小
        tau = 1.0,                        # 目标网络更新系数 (1.0 = 硬更新, <1.0 = 软更新)
        gamma = 0.99,                     # 折扣因子
        train_freq = 4,                   # 每几步训练一次
        gradient_steps = 1,               # 每次训练梯度下降几次
        target_update_interval = 500,     # 目标网络同步频率
        exploration_fraction = 0.3,       # 在训练前 30% 的时间内降低 epsilon
        exploration_initial_eps = 1.0,    # 初始随机探索概率
        exploration_final_eps = 0.05,     # 最终保持的最低探索概率
        policy_kwargs = policy_kwargs,    # 注入自定义网络
        tensorboard_log = tensorboard_log,
        verbose = 1,
        device = "auto"
    )

    checkpoint_callback = CheckpointCallback(
        save_freq = 2000 // num_cpu, 
        save_path = checkpoint_path,
        name_prefix = "dqn_failslow"
    )
    
    eval_callback = EvalCallback(
        eval_env,
        best_model_save_path = checkpoint_path,
        log_path = checkpoint_path,
        eval_freq = 1000 // num_cpu,
        n_eval_episodes = 5,
        deterministic = True,
        render = False
    )

    callbacks = CallbackList([checkpoint_callback, eval_callback])

    total_timesteps = 10000
    try:
        model.learn(
            total_timesteps = total_timesteps, 
            callback = callbacks,
            progress_bar = True
        )
        print("Training finished.")

        train_env.close()
        eval_env.close()
        
        model.save(os.path.join(checkpoint_path, "final_model"))
        
    except KeyboardInterrupt:
        print("Training interrupted manually. Saving current model...")
        train_env.close()
        eval_env.close()

        model.save(os.path.join(checkpoint_path, "interrupted_model"))

if __name__ == "__main__":
    main()
