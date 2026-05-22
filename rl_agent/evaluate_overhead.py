import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

from rl_agent.evaluate_continuous import (
    _decode_action,
    _load_model,
    _serialize_action,
    failure_active_during_request,
    failure_to_dict,
    shift_failure_window,
    simulate_request,
)
from rl_agent.workflow_config import (
    ACTION_ID_TO_NAME,
    DEFAULT_HW_CONFIG_PATH,
    DEFAULT_NUM_CORES,
    DEFAULT_NUM_LINKS,
    DEFAULT_WORKLOAD_PATH,
    build_action_config,
)
from rl_agent.spaces.states import StateSpace
from simulator.run import arch_analyzer, fail_analyzer
from utils.mapper import NetworkMapper, parse_mapping


def _observation_bytes(observation: Dict[str, np.ndarray]) -> int:
    total = 0
    for value in observation.values():
        if hasattr(value, "nbytes"):
            total += int(value.nbytes)
    return total


def _trace_json_bytes(trace) -> int:
    return len(trace.model_dump_json().encode("utf-8"))


def _action_size_bytes(action: Any) -> Dict[str, int]:
    serialized = _serialize_action(action)
    try:
        array = np.asarray(action)
    except Exception:
        array = np.asarray(serialized)
    return {
        "raw_bytes": int(array.nbytes),
        "json_bytes": len(json.dumps(serialized).encode("utf-8")),
    }


def _metric_summary(values: List[float]) -> Optional[Dict[str, float]]:
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    return {
        "count": int(arr.size),
        "mean": round(float(arr.mean()), 6),
        "min": round(float(arr.min()), 6),
        "max": round(float(arr.max()), 6),
        "p50": round(float(np.percentile(arr, 50)), 6),
        "p95": round(float(np.percentile(arr, 95)), 6),
    }


def _measure_policy_action(
    mapper: NetworkMapper,
    model,
    observation: Dict[str, np.ndarray],
    action_config: Dict[str, int],
    trace,
    deterministic: bool,
) -> Dict[str, Any]:
    inference_started = time.perf_counter()
    action, _ = model.predict(observation, deterministic=deterministic)
    policy_inference_ms = round((time.perf_counter() - inference_started) * 1000, 3)

    decode_started = time.perf_counter()
    decoded = _decode_action(action, model.action_space, action_config)
    action_decode_ms = round((time.perf_counter() - decode_started) * 1000, 3)
    generation_ms = round(policy_inference_ms + action_decode_ms, 3)

    if len(decoded) != 4:
        raise ValueError(
            "Loaded model action space is incompatible with runtime-overhead evaluation: "
            f"expected 4 action dimensions, got {decoded} from action space {model.action_space}"
        )

    action_type = ACTION_ID_TO_NAME[int(decoded[3])]
    action_sizes = _action_size_bytes(action)

    application_started = time.perf_counter()
    report = mapper.apply_local_remap(
        layer_id=int(decoded[0]),
        action_type=action_type,
        src_core=int(decoded[1]),
        dst_core=int(decoded[2]),
        trace=trace,
    )
    application_ms = round((time.perf_counter() - application_started) * 1000, 3)

    return {
        "raw_action": _serialize_action(action),
        "decoded_action": [int(v) for v in decoded],
        "action_type": action_type,
        "accepted": report.accepted,
        "reason": report.reason,
        "details": report.details,
        "policy_inference_ms": policy_inference_ms,
        "action_decode_ms": action_decode_ms,
        "generation_ms": generation_ms,
        "application_ms": application_ms,
        "raw_action_bytes": action_sizes["raw_bytes"],
        "json_action_bytes": action_sizes["json_bytes"],
    }


def rollout_overhead(
    algo: str,
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
    model = _load_model(algo, model_path, device=device)
    action_config = build_action_config(num_layers=len(network.layers), num_cores=DEFAULT_NUM_CORES)
    state_manager = StateSpace(DEFAULT_NUM_CORES, DEFAULT_NUM_LINKS, max_op_num=1000)

    global_cycle = 0
    accepted_actions = 0
    rejected_actions = 0
    inference_records: List[Dict[str, Any]] = []

    trace_sizes: List[float] = []
    observation_sizes: List[float] = []
    inference_cycles: List[float] = []
    simulation_durations: List[float] = []
    request_durations: List[float] = []
    detect_durations: List[float] = []
    policy_inference_durations: List[float] = []
    action_decode_durations: List[float] = []
    action_generation_durations: List[float] = []
    action_application_durations: List[float] = []
    action_raw_sizes: List[float] = []
    action_json_sizes: List[float] = []
    accepted_application_durations: List[float] = []
    rejected_application_durations: List[float] = []

    for inference_idx in range(times):
        local_failure = shift_failure_window(
            failure,
            global_cycle,
            x_size=arch_config.noc.x,
            y_size=arch_config.noc.y,
        )
        request = simulate_request(
            arch_config=arch_config,
            failure=local_failure,
            mapper=mapper,
            state_manager=state_manager,
            max_request_cycles=max_request_cycles,
        )

        trace_json_bytes = _trace_json_bytes(request["trace"])
        observation_bytes = _observation_bytes(request["observation"])
        inference_cycles.append(float(request["cycles"]))
        simulation_durations.append(float(request["simulation_duration_ms"]))
        trace_sizes.append(float(trace_json_bytes))
        observation_sizes.append(float(observation_bytes))
        request_durations.append(float(request["total_duration_ms"]))
        detect_durations.append(float(request["detect_duration_ms"]))

        next_global_cycle = global_cycle + request["cycles"]
        action_report = None
        if inference_idx < times - 1:
            action_report = _measure_policy_action(
                mapper=mapper,
                model=model,
                observation=request["observation"],
                action_config=action_config,
                trace=request["trace"],
                deterministic=deterministic,
            )
            policy_inference_durations.append(float(action_report["policy_inference_ms"]))
            action_decode_durations.append(float(action_report["action_decode_ms"]))
            action_generation_durations.append(float(action_report["generation_ms"]))
            action_application_durations.append(float(action_report["application_ms"]))
            action_raw_sizes.append(float(action_report["raw_action_bytes"]))
            action_json_sizes.append(float(action_report["json_action_bytes"]))
            if action_report["accepted"]:
                accepted_actions += 1
                accepted_application_durations.append(float(action_report["application_ms"]))
            else:
                rejected_actions += 1
                rejected_application_durations.append(float(action_report["application_ms"]))

        record = {
            "inference": inference_idx + 1,
            "global_start_cycle": global_cycle,
            "request_cycles": request["cycles"],
            "global_end_cycle": next_global_cycle,
            "failure_active_during_request": failure_active_during_request(local_failure, request["cycles"]),
            "effective_failure": failure_to_dict(local_failure),
            "dnn_inference_cycles": request["cycles"],
            "simulation_duration_ms": request["simulation_duration_ms"],
            "trace_json_bytes": trace_json_bytes,
            "observation_bytes": observation_bytes,
            "detect_duration_ms": request["detect_duration_ms"],
            "request_total_duration_ms": request["total_duration_ms"],
            "post_action": action_report,
        }
        inference_records.append(record)
        global_cycle = next_global_cycle

    summary = {
        "algorithm": algo,
        "model_path": model_path,
        "failure_path": fail_path,
        "times": times,
        "total_cycles": global_cycle,
        "final_global_cycle": global_cycle,
        "action_decisions": max(0, times - 1),
        "accepted_actions": accepted_actions,
        "rejected_actions": rejected_actions,
        "dnn_inference_cycles": _metric_summary(inference_cycles),
        "simulation_duration_ms": _metric_summary(simulation_durations),
        "trace_json_bytes": _metric_summary(trace_sizes),
        "observation_bytes": _metric_summary(observation_sizes),
        "request_total_duration_ms": _metric_summary(request_durations),
        "detect_duration_ms": _metric_summary(detect_durations),
        "policy_inference_ms": _metric_summary(policy_inference_durations),
        "action_decode_ms": _metric_summary(action_decode_durations),
        "action_generation_ms": _metric_summary(action_generation_durations),
        "action_application_ms": _metric_summary(action_application_durations),
        "accepted_action_application_ms": _metric_summary(accepted_application_durations),
        "rejected_action_application_ms": _metric_summary(rejected_application_durations),
        "raw_action_bytes": _metric_summary(action_raw_sizes),
        "json_action_bytes": _metric_summary(action_json_sizes),
    }
    return {
        "summary": summary,
        "inferences": inference_records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Measure runtime overheads for online RL mitigation: trace size, action generation time/size, and action application time."
    )
    parser.add_argument("--algo", choices=["ppo", "dqn"], default="ppo")
    parser.add_argument("--model", required=True, help="Path to a trained PPO or DQN checkpoint")
    parser.add_argument("--fail", required=True, help="Fail-slow JSON for the continuous-inference scenario")
    parser.add_argument("--workload", default=DEFAULT_WORKLOAD_PATH)
    parser.add_argument("--hw-config", default=DEFAULT_HW_CONFIG_PATH)
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--max-request-cycles", type=int, default=None, help="Optional safety bound for one inference")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    if args.times <= 0:
        raise ValueError("--times must be positive")

    results = rollout_overhead(
        algo=args.algo,
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
