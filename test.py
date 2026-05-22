import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
import torch

print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
print("-" * 30)

try:
    # 1. 创建一个经典环境
    env = gym.make("CartPole-v1")
    print("Gymnasium environment 'CartPole-v1' created successfully.")

    # 2. 检查环境是否符合 SB3 的规范
    check_env(env)
    print("Environment check passed.")

    # 3. 创建一个 PPO 模型 (它会自动使用 GPU 如果可用)
    # device='auto' 会让 SB3 自动检测并使用 GPU
    model = PPO("MlpPolicy", env, verbose=0, device='auto')
    print("Stable-Baselines3 PPO model created.")
    print(f"Model is running on device: {model.device}")

    # 4. 简单训练几步，验证整个流程
    print("Training for 100 timesteps as a test...")
    model.learn(total_timesteps=100)
    
    print("-" * 30)
    print("✅ Success! Your RL environment is ready to go!")

except Exception as e:
    print(f"❌ An error occurred: {e}")

finally:
    if 'env' in locals():
        env.close()

