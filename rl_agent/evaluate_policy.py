import argparse
import json
import os
from typing import Any, Dict

from stable_baselines3 import DQN, PPO

from rl_agent.envs.flatten_action_wrapper import FlattenActionWrapper
from rl_agent.envs.rl_env import FailSlowEnv
from rl_agent.workflow_config import (
    ACTION_ID_TO_NAME,
    DEFAULT_DATASET_DIR,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    build_action_config,
)
from utils.mapper import parse_mapping


def _build_env(algo: str, workload_path: str, hw_config: str, dataset_dir: str):
    initial_network = parse_mapping(workload_path)
    action_config = build_action_config(len(initial_network.layers), num_cores=DEFAULT_NUM_CORES)
    env = FailSlowEnv(
        initial_mapping=initial_network,
        hardware_config=hw_config,
        failure_path=dataset_dir,
        num_cores=DEFAULT_NUM_CORES,
        num_links=DEFAULT_NUM_LINKS,
        action_config=action_config,
    )
    if algo == "dqn":
        return FlattenActionWrapper(env)
    return env


def _baseline_result(fail_path: str, workload_path: str, hw_config: str, dataset_dir: str) -> Dict[str, Any]:
    env = _build_env("ppo", workload_path, hw_config, dataset_dir)
    try:
        env.reset(options={"config_path": fail_path})
        return {
            "cycles": env.last_cycles,
            "failure_path": fail_path,
        }
    finally:
        env.close()


def _load_model(algo: str, model_path: str, device: str):
    if algo == "ppo":
        return PPO.load(model_path, device=device)
    if algo == "dqn":
        return DQN.load(model_path, device=device)
    raise ValueError(f"Unsupported algorithm '{algo}'")


def _serialize_action(action) -> Any:
    if hasattr(action, "tolist"):
        return action.tolist()
    try:
        return int(action)
    except TypeError:
        return action


def rollout(
    algo: str,
    model_path: str,
    fail_path: str,
    workload_path: str,
    hw_config: str,
    dataset_dir: str,
    max_steps: int,
    deterministic: bool,
    device: str,
) -> Dict[str, Any]:
    env = _build_env(algo, workload_path, hw_config, dataset_dir)
    model = _load_model(algo, model_path, device=device)

    try:
        obs, reset_info = env.reset(options={"config_path": fail_path})
        root_env = env.unwrapped
        baseline = _baseline_result(fail_path, workload_path, hw_config, dataset_dir)

        step_results = []
        total_reward = 0.0
        truncated = False
        terminated = False

        for step_idx in range(max_steps):
            action, _ = model.predict(obs, deterministic=deterministic)
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += float(reward)
            action_report = info.get("action_report", {})

            step_results.append({
                "step": step_idx + 1,
                "action": _serialize_action(action),
                "action_type": action_report.get("action_type"),
                "accepted": action_report.get("accepted", False),
                "reason": action_report.get("reason", ""),
                "details": action_report.get("details", {}),
                "reward": float(reward),
                "reward_components": info.get("reward", {}),
            })

            if terminated or truncated:
                break

        mitigated_cycles = root_env.last_cycles
        summary = {
            "algorithm": algo,
            "model_path": model_path,
            "failure_path": fail_path,
            "baseline_cycles": baseline["cycles"],
            "mitigated_cycles": mitigated_cycles,
            "cycle_improvement": None if baseline["cycles"] is None or mitigated_cycles is None else baseline["cycles"] - mitigated_cycles,
            "improved": False if baseline["cycles"] is None or mitigated_cycles is None else mitigated_cycles < baseline["cycles"],
            "total_reward": total_reward,
            "steps": len(step_results),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "accepted_actions": sum(1 for step in step_results if step["accepted"]),
            "rejected_actions": sum(1 for step in step_results if not step["accepted"]),
        }

        return {
            "baseline": baseline,
            "mitigated": {
                "cycles": mitigated_cycles,
                "failure_path": fail_path,
                "steps": step_results,
            },
            "summary": summary,
        }
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description="Evaluate a trained RL mitigation policy on a fixed fail-slow scenario")
    parser.add_argument("--algo", choices=["ppo", "dqn"], default="ppo")
    parser.add_argument("--model", required=True, help="Path to a trained PPO or DQN checkpoint")
    parser.add_argument("--fail", default=os.path.join(DEFAULT_DATASET_DIR, "fail1.json"), help="Fixed fail-slow scenario JSON")
    parser.add_argument("--workload", default=DEFAULT_WORKLOAD_PATH)
    parser.add_argument("--hw-config", default=DEFAULT_HW_CONFIG_PATH)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--deterministic", action="store_true", help="Use deterministic policy rollout")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    results = rollout(
        algo=args.algo,
        model_path=args.model,
        fail_path=args.fail,
        workload_path=args.workload,
        hw_config=args.hw_config,
        dataset_dir=args.dataset_dir,
        max_steps=args.max_steps,
        deterministic=args.deterministic,
        device=args.device,
    )

    print(json.dumps(results["summary"], indent=2))
    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w") as file:
            json.dump(results, file, indent=2)


if __name__ == "__main__":
    main()
