import os
import argparse
import torch
import multiprocessing
from datetime import datetime

# Switched to PPO
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback, CallbackList

from rl_agent.envs.rl_env import FailSlowEnv
# Import CustomFeatureExtractor
from rl_agent.envs.PPOagent import CustomFeatureExtractor 
from utils.mapper import parse_mapping
from rl_agent.workflow_config import (
    DEFAULT_DATASET_DIR,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    PPO_LOG_DIR,
    PPO_MODEL_DIR,
    build_action_config,
)

LOG_DIR = PPO_LOG_DIR
MODEL_DIR = PPO_MODEL_DIR
DATASET_DIR = DEFAULT_DATASET_DIR
WORKLOAD_PATH = DEFAULT_WORKLOAD_PATH
HW_CONFIG_PATH = DEFAULT_HW_CONFIG_PATH

def make_env(rank: int, seed: int = 0):
    def _init():
        initial_network = parse_mapping(WORKLOAD_PATH)
        action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
        
        env = FailSlowEnv(
            initial_mapping = initial_network,
            hardware_config = HW_CONFIG_PATH,
            failure_path = DATASET_DIR,
            num_cores = DEFAULT_NUM_CORES,
            num_links = DEFAULT_NUM_LINKS,
            max_steps = 200,
            performance_wgt = 2.0,
            core_thermal_wgt = 1.0,
            action_config = action_config
        )

        env.reset(seed = seed + rank)
        
        log_path = os.path.join(LOG_DIR, f"env_{rank}")
        os.makedirs(log_path, exist_ok=True)
        return Monitor(env, filename=log_path)
    
    return _init


def main():
    parser = argparse.ArgumentParser(description="Train PPO for fail-slow mitigation")
    parser.add_argument("--num-cpu", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--total-timesteps", type=int, default=10000)
    args = parser.parse_args()

    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    tensorboard_log = os.path.join(LOG_DIR, run_name)
    checkpoint_path = os.path.join(MODEL_DIR, run_name)
    os.makedirs(checkpoint_path, exist_ok=True)

    print(f"Start Training (PPO). Run Name: {run_name}")
    print(f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}")

    num_cpu = max(1, args.num_cpu)
    print(f"Initializing {num_cpu} parallel environments (SubprocVecEnv)...")
    
    train_env = SubprocVecEnv([make_env(rank=i, seed=i) for i in range(num_cpu)])
    eval_env = DummyVecEnv([make_env(rank=num_cpu, seed=num_cpu)])

    # Policy Kwargs for PPO
    policy_kwargs = dict(
        features_extractor_class = CustomFeatureExtractor,
        features_extractor_kwargs = dict(
            gcn_embed_dim = 1,
            features_dim = 256 # Should match last linear layer output
        ),
        # Architecture for the separate Actor (pi) and Critic (vf) heads
        net_arch = dict(pi=[128, 64], vf=[128, 64]) 
    )

    model = PPO(
        policy = "MultiInputPolicy",
        env = train_env,
        learning_rate = 3e-4,             
        n_steps = 2048 // num_cpu,        
        batch_size = 64,                  
        n_epochs = 10,                    
        gamma = 0.99,                     
        gae_lambda = 0.95,
        clip_range = 0.2,
        ent_coef = 0.01, # Crucial for MultiDiscrete exploration
        vf_coef = 0.5,
        max_grad_norm = 0.5,
        policy_kwargs = policy_kwargs,    
        tensorboard_log = tensorboard_log,
        verbose = 1,
        device = "auto"
    )

    checkpoint_callback = CheckpointCallback(
        save_freq = 2000 // num_cpu, 
        save_path = checkpoint_path,
        name_prefix = "ppo_failslow"
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

    total_timesteps = args.total_timesteps
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
