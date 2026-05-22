import os
import torch
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
from stable_baselines3.common.vec_env import DummyVecEnv

# 引入你的模块
from rl_agent.envs.rl_env import FailSlowEnv
from rl_agent.envs.PPOagent import CustomFeatureExtractor
from utils.mapper import parse_mapping
from rl_agent.workflow_config import (
    DEFAULT_DATASET_DIR,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    build_action_config,
)

WORKLOAD_PATH = DEFAULT_WORKLOAD_PATH
HW_CONFIG_PATH = DEFAULT_HW_CONFIG_PATH
DATASET_DIR = DEFAULT_DATASET_DIR

def test_environment_logic():
    print("="*50)
    print("TEST 1: Environment Logic & Mapping")
    print("="*50)

    # 1. 加载 Mapping
    print(f"Loading workload from {WORKLOAD_PATH}...")
    try:
        initial_network = parse_mapping(WORKLOAD_PATH)
    except FileNotFoundError:
        print(f"❌ Error: Workload file not found at {WORKLOAD_PATH}")
        return

    # 2. 初始化环境
    try:
        action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
        env = FailSlowEnv(
            initial_mapping=initial_network,
            hardware_config=HW_CONFIG_PATH,
            failure_path=DATASET_DIR,
            num_cores=DEFAULT_NUM_CORES,
            num_links=DEFAULT_NUM_LINKS,
            max_steps=10, # 短一点
            action_config=action_config
        )
        print("✅ Environment initialized successfully.")
    except Exception as e:
        print(f"❌ Error initializing environment: {e}")
        return

    # 3. 检查观察空间形状
    obs, info = env.reset()
    print("\nObservation Shapes:")
    for key, val in obs.items():
        print(f"  - {key}: {val.shape}")

    # 4. 检查动作空间
    print(f"\nAction Space: {env.action_space}") # 应该是 MultiDiscrete([layers, 16, 16, ops])
    
    # 5. 测试特定动作 (验证显式 local remap 动作接口)
    print("\nTesting local action [Layer=5, Src=1, Dst=0, Op=Split]...")
    edge_action = np.array([5, 1, 0, 1])
    
    try:
        obs, reward, term, trunc, info = env.step(edge_action)
        print(f"✅ Edge action executed. Reward: {reward}")
    except Exception as e:
        print(f"❌ Crash on edge action: {e}")
        import traceback
        traceback.print_exc()

    # 6. 测试随机动作循环
    print("\nTesting random loop (5 steps)...")
    for i in range(5):
        action = env.action_space.sample()
        obs, reward, term, trunc, info = env.step(action)
        print(f"  Step {i+1}: Action={action}, Reward={reward:.4f}")
        if term or trunc:
            env.reset()
    
    env.close()

def test_ppo_integration():
    print("\n" + "="*50)
    print("TEST 2: PPO Model Integration")
    print("="*50)

    # 1. 设置 Vector Environment
    initial_network = parse_mapping(WORKLOAD_PATH)
    action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
    env = FailSlowEnv(
        initial_mapping=initial_network,
        hardware_config=HW_CONFIG_PATH,
        failure_path=DATASET_DIR,
        num_cores=DEFAULT_NUM_CORES,
        num_links=DEFAULT_NUM_LINKS,
        action_config=action_config
    )
    # 包装成 SB3 需要的 VecEnv
    vec_env = DummyVecEnv([lambda: env])

    # 2. 网络参数
    policy_kwargs = dict(
        features_extractor_class=CustomFeatureExtractor,
        features_extractor_kwargs=dict(gcn_embed_dim=1, features_dim=256),
        net_arch=dict(pi=[32, 32], vf=[32, 32]) # 用小一点的网络快速测试
    )

    # 3. 初始化 PPO
    try:
        model = PPO(
            "MultiInputPolicy",
            vec_env,
            policy_kwargs=policy_kwargs,
            verbose=1,
            device="cpu" # 强制 CPU 方便调试
        )
        print("✅ PPO Model initialized.")
    except Exception as e:
        print(f"❌ Error initializing PPO: {e}")
        return

    # 4. 尝试训练 (Forward + Backward Pass)
    print("\nRunning .learn() for 100 timesteps...")
    try:
        model.learn(total_timesteps=100)
        print("✅ PPO training loop check passed.")
    except Exception as e:
        print(f"❌ Error during training loop: {e}")
        import traceback
        traceback.print_exc()

    vec_env.close()

if __name__ == "__main__":
    # 检查 CUDA
    print(f"CUDA Available: {torch.cuda.is_available()}")
    
    test_environment_logic()
    test_ppo_integration()
