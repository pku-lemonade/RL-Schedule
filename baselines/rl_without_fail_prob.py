import argparse
import json
import multiprocessing
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import gymnasium as gym
import numpy as np
import torch
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CallbackList, CheckpointCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from rl_agent.envs.PPOagent import CustomFeatureExtractor
from rl_agent.envs.rl_env import FailSlowEnv
from rl_agent.evaluate_continuous import (
    failure_active_during_request,
    failure_to_dict,
    shift_failure_window,
    simulate_request,
)
from rl_agent.spaces.states import StateSpace
from rl_agent.workflow_config import (
    ACTION_ID_TO_NAME,
    DEFAULT_DATASET_DIR,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    build_action_config,
)
from simulator.run import arch_analyzer, fail_analyzer
from utils.mapper import NetworkMapper, parse_mapping
from utils.timing_logger import log_timing


BASELINE_NAME = "rl_without_fail_prob"
BASELINE_ROOT = os.path.join("baselines", BASELINE_NAME)
LOG_DIR = os.path.join(BASELINE_ROOT, "logs", "ppo_tensorboard")
MODEL_DIR = os.path.join(BASELINE_ROOT, "models", "ppo_checkpoints")
MONITOR_DIR = os.path.join(BASELINE_ROOT, "logs", "monitor")


def mask_runtime_slow_channel(observation: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    runtime = np.array(observation["runtime"], copy=True)
    runtime[1::4, :] = 0.0
    masked = dict(observation)
    masked["runtime"] = runtime
    return masked


class ZeroFailProbWrapper(gym.ObservationWrapper):
    def __init__(self, env: gym.Env):
        super().__init__(env)
        self.observation_space = env.observation_space

    def observation(self, observation):
        return mask_runtime_slow_channel(observation)


def _decode_action(action: Any, action_space, action_config: Dict[str, int]) -> List[int]:
    nvec = (
        action_config["num_layers"],
        action_config["num_cores"],
        action_config["num_cores"],
        action_config["num_ops"],
    )
    if isinstance(action_space, spaces.Discrete):
        return list(np.unravel_index(int(action), nvec))
    decoded = list(np.asarray(action, dtype=np.int64).reshape(-1))
    if len(decoded) == 1:
        return list(np.unravel_index(int(decoded[0]), nvec))
    return decoded


def _load_model(model_path: str, device: str):
    return PPO.load(model_path, device=device)


def _build_action_config_from_workload(workload_path: str) -> Dict[str, int]:
    initial_network = parse_mapping(workload_path)
    return build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)


def make_env(workload_path: str, hw_config_path: str, dataset_dir: str, rank: int, seed: int = 0):
    def _init():
        initial_network = parse_mapping(workload_path)
        action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
        env = FailSlowEnv(
            initial_mapping=initial_network,
            hardware_config=hw_config_path,
            failure_path=dataset_dir,
            num_cores=DEFAULT_NUM_CORES,
            num_links=DEFAULT_NUM_LINKS,
            max_steps=200,
            performance_wgt=2.0,
            core_thermal_wgt=1.0,
            action_config=action_config,
        )
        env = ZeroFailProbWrapper(env)
        env.reset(seed=seed + rank)

        os.makedirs(MONITOR_DIR, exist_ok=True)
        log_path = os.path.join(MONITOR_DIR, f"env_{rank}")
        return Monitor(env, filename=log_path)

    return _init


def train(
    workload_path: str,
    hw_config_path: str,
    dataset_dir: str,
    num_cpu: int,
    total_timesteps: int,
    n_steps: Optional[int],
    batch_size: int,
):
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    tensorboard_log = os.path.join(LOG_DIR, run_name)
    checkpoint_path = os.path.join(MODEL_DIR, run_name)
    os.makedirs(checkpoint_path, exist_ok=True)

    print(f"Start Training ({BASELINE_NAME}). Run Name: {run_name}")
    print(f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}")
    print(f"Initializing {num_cpu} parallel environments ({'DummyVecEnv' if num_cpu == 1 else 'SubprocVecEnv'})...")

    if num_cpu == 1:
        train_env = DummyVecEnv([make_env(workload_path, hw_config_path, dataset_dir, rank=0, seed=0)])
    else:
        train_env = SubprocVecEnv([make_env(workload_path, hw_config_path, dataset_dir, rank=i, seed=i) for i in range(num_cpu)])
    eval_env = DummyVecEnv([make_env(workload_path, hw_config_path, dataset_dir, rank=num_cpu, seed=num_cpu)])

    policy_kwargs = dict(
        features_extractor_class=CustomFeatureExtractor,
        features_extractor_kwargs=dict(gcn_embed_dim=1, features_dim=256),
        net_arch=dict(pi=[128, 64], vf=[128, 64]),
    )

    model = PPO(
        policy="MultiInputPolicy",
        env=train_env,
        learning_rate=3e-4,
        n_steps=max(1, n_steps if n_steps is not None else 2048 // num_cpu),
        batch_size=batch_size,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=policy_kwargs,
        tensorboard_log=tensorboard_log,
        verbose=1,
        device="auto",
    )

    checkpoint_callback = CheckpointCallback(
        save_freq=max(1, 2000 // num_cpu),
        save_path=checkpoint_path,
        name_prefix="ppo_without_fail_prob",
    )

    eval_callback = EvalCallback(
        eval_env,
        best_model_save_path=checkpoint_path,
        log_path=checkpoint_path,
        eval_freq=max(1, 1000 // num_cpu),
        n_eval_episodes=5,
        deterministic=True,
        render=False,
    )

    callbacks = CallbackList([checkpoint_callback, eval_callback])

    try:
        model.learn(total_timesteps=total_timesteps, callback=callbacks, progress_bar=True)
        print("Training finished.")
        train_env.close()
        eval_env.close()
        model.save(os.path.join(checkpoint_path, "final_model"))
    except KeyboardInterrupt:
        print("Training interrupted manually. Saving current model...")
        train_env.close()
        eval_env.close()
        model.save(os.path.join(checkpoint_path, "interrupted_model"))


def _serialize_action(action: Any) -> Any:
    if hasattr(action, "tolist"):
        return action.tolist()
    try:
        return int(action)
    except TypeError:
        return action


def rollout_eval(
    model_path: str,
    fail_path: str,
    workload_path: str,
    hw_config_path: str,
    times: int,
    deterministic: bool,
    device: str,
    max_request_cycles: Optional[int],
) -> Dict[str, Any]:
    network = parse_mapping(workload_path)
    mapper = NetworkMapper(network=network)
    mapper.gen_dfg()
    arch_config = arch_analyzer(hw_config_path)
    failure = fail_analyzer(fail_path)
    model = _load_model(model_path, device=device)
    action_config = _build_action_config_from_workload(workload_path)
    state_manager = StateSpace(DEFAULT_NUM_CORES, DEFAULT_NUM_LINKS, max_op_num=1000)

    global_cycle = 0
    accepted_actions = 0
    rejected_actions = 0
    inference_records: List[Dict[str, Any]] = []

    for inference_idx in range(times):
        local_failure = shift_failure_window(failure, global_cycle)
        log_timing(
            "baseline.rl_without_fail_prob.start",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            failure_path=fail_path,
            effective_failure=failure_to_dict(local_failure),
        )

        request = simulate_request(
            arch_config=arch_config,
            failure=local_failure,
            mapper=mapper,
            state_manager=state_manager,
            max_request_cycles=max_request_cycles,
        )

        next_global_cycle = global_cycle + request["cycles"]
        action_report = None
        if inference_idx < times - 1:
            masked_observation = mask_runtime_slow_channel(request["observation"])
            action, _ = model.predict(masked_observation, deterministic=deterministic)
            decoded = _decode_action(action, model.action_space, action_config)
            if len(decoded) != 4:
                raise ValueError(
                    "Loaded model action space is incompatible with rl_without_fail_prob evaluation: "
                    f"expected 4 action dimensions, got {decoded} from action space {model.action_space}"
                )
            action_type = ACTION_ID_TO_NAME[int(decoded[3])]
            report = mapper.apply_local_remap(
                layer_id=int(decoded[0]),
                action_type=action_type,
                src_core=int(decoded[1]),
                dst_core=int(decoded[2]),
                trace=request["trace"],
            )
            action_report = {
                "raw_action": _serialize_action(action),
                "decoded_action": [int(v) for v in decoded],
                "action_type": action_type,
                "accepted": report.accepted,
                "reason": report.reason,
                "details": report.details,
            }
            if action_report["accepted"]:
                accepted_actions += 1
            else:
                rejected_actions += 1

        record = {
            "inference": inference_idx + 1,
            "global_start_cycle": global_cycle,
            "request_cycles": request["cycles"],
            "global_end_cycle": next_global_cycle,
            "failure_active_during_request": failure_active_during_request(local_failure, request["cycles"]),
            "effective_failure": failure_to_dict(local_failure),
            "detect_duration_ms": request["detect_duration_ms"],
            "total_duration_ms": request["total_duration_ms"],
            "post_action": action_report,
        }
        inference_records.append(record)

        log_timing(
            "baseline.rl_without_fail_prob",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            request_cycles=request["cycles"],
            global_end_cycle=next_global_cycle,
            failure_active_during_request=record["failure_active_during_request"],
            post_action=action_report,
        )
        global_cycle = next_global_cycle

    summary = {
        "method": BASELINE_NAME,
        "algorithm": "ppo",
        "model_path": model_path,
        "failure_path": fail_path,
        "times": times,
        "total_cycles": global_cycle,
        "final_global_cycle": global_cycle,
        "accepted_actions": accepted_actions,
        "rejected_actions": rejected_actions,
    }
    return {
        "summary": summary,
        "inferences": inference_records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Baseline PPO without fail-slow probability in the observation. Supports training and continuous evaluation."
    )
    parser.add_argument("--mode", choices=["train", "eval"], required=True)
    parser.add_argument("--workload", default=DEFAULT_WORKLOAD_PATH)
    parser.add_argument("--hw-config", default=DEFAULT_HW_CONFIG_PATH)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--num-cpu", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--total-timesteps", type=int, default=10000)
    parser.add_argument("--n-steps", type=int, default=None, help="Optional PPO rollout length override for train mode")
    parser.add_argument("--batch-size", type=int, default=64, help="Optional PPO batch size for train mode")
    parser.add_argument("--model", default=None, help="Checkpoint path for eval mode")
    parser.add_argument("--fail", default=None, help="Fail-slow JSON for eval mode")
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests in eval mode")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--max-request-cycles", type=int, default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.mode == "train":
        train(
            workload_path=args.workload,
            hw_config_path=args.hw_config,
            dataset_dir=args.dataset_dir,
            num_cpu=max(1, args.num_cpu),
            total_timesteps=args.total_timesteps,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
        )
        return

    if not args.model:
        raise ValueError("--model is required in eval mode")
    if not args.fail:
        raise ValueError("--fail is required in eval mode")
    if args.times <= 0:
        raise ValueError("--times must be positive")

    results = rollout_eval(
        model_path=args.model,
        fail_path=args.fail,
        workload_path=args.workload,
        hw_config_path=args.hw_config,
        times=args.times,
        deterministic=args.deterministic,
        device=args.device,
        max_request_cycles=args.max_request_cycles,
    )

    print(json.dumps(results["summary"], indent=2))
    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)


if __name__ == "__main__":
    main()
