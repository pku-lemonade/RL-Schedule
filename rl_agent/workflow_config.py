DEFAULT_WORKLOAD_PATH = "workloads/darknet19-4-4.json"
DEFAULT_HW_CONFIG_PATH = "configs/instances/gemini4_4.json"
DEFAULT_DATASET_DIR = "./fail_dataset"

DEFAULT_NUM_CORES = 16
DEFAULT_NUM_LINKS = 48
DEFAULT_MAX_STEPS = 200

PPO_LOG_DIR = "rl_agent/logs/ppo_tensorboard"
PPO_MODEL_DIR = "rl_agent/models/ppo_checkpoints"
DQN_LOG_DIR = "rl_agent/logs/dqn_tensorboard"
DQN_MODEL_DIR = "rl_agent/models/dqn_checkpoints"

LOCAL_REMAP_ACTIONS = ("replace", "split", "shift", "remove")
ACTION_NAME_TO_ID = {name: idx for idx, name in enumerate(LOCAL_REMAP_ACTIONS)}
ACTION_ID_TO_NAME = {idx: name for idx, name in enumerate(LOCAL_REMAP_ACTIONS)}


def build_action_config(num_layers: int, num_cores: int = DEFAULT_NUM_CORES) -> dict:
    return {
        "num_layers": num_layers,
        "num_cores": num_cores,
        "num_ops": len(LOCAL_REMAP_ACTIONS),
    }
