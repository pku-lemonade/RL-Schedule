import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import simpy
from gymnasium import spaces
from stable_baselines3 import DQN, PPO

from configs.schemas.arch_config import ArchConfig, CoreConfig, LinkConfig
from configs.schemas.failure_configs import FailSlow, LinkFail, LsuFail, RouterFail, TpuFail
from predictor.predict import detect
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
from simulator.architecture import Arch
from simulator.core import Core, LSU, Scheduler, ScratchpadMemory, TPU
from simulator.noc import Link
from simulator.run import arch_analyzer, fail_analyzer
from simulator.tracing import process_events
from utils.definitions import Direction
from utils.mapper import NetworkMapper, parse_mapping
from utils.timing_logger import log_timing


class ContinuousEvalCore(Core):
    def __init__(self, env, core_id: int, config: CoreConfig, mapper: NetworkMapper):
        self.env = env
        self.id = core_id
        self.mapper = mapper

        self.scheduler = Scheduler(id=self.id, mapper=mapper)
        self.spm = ScratchpadMemory(env=self.env, id=self.id, config=config.spm)
        self.lsu = LSU(env=self.env, config=config.lsu)
        self.tpu = TPU(env=self.env, config=config.tpu)

        self.events = []
        self.index2id = {}
        self.process = self.env.process(self.execute())


class ContinuousEvalArch(Arch):
    def build_cores(self, env, config: CoreConfig, mapper: NetworkMapper, c2r_width=128, c2r_delay=1):
        cores = []
        for core_id in range(self.x_size * self.y_size):
            core = ContinuousEvalCore(env=self.env, core_id=core_id, config=config, mapper=mapper)
            link1 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))
            link2 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))

            link1.bind(0, 0, 0, 0, True)
            link2.bind(0, 0, 0, 0, True)

            core.bind_with_router(link2, link1, self.noc.routers[core_id])
            self.noc.routers[core_id].bind_link(direction="core", link_in=link1, link_out=link2)
            cores.append(core)

        return cores


def _load_model(algo: str, model_path: str, device: str):
    if algo == "ppo":
        return PPO.load(model_path, device=device)
    if algo == "dqn":
        return DQN.load(model_path, device=device)
    raise ValueError(f"Unsupported algorithm '{algo}'")


def _serialize_action(action: Any) -> Any:
    if hasattr(action, "tolist"):
        return action.tolist()
    try:
        return int(action)
    except TypeError:
        return action


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


def _shift_failure_items(items, global_offset: int):
    shifted = []
    for item in items:
        local_start = item.start_time - global_offset
        local_end = item.end_time - global_offset
        if local_end <= 0:
            continue
        local_start = max(0, local_start)
        if local_end <= local_start:
            continue
        shifted.append(item.model_copy(update={"start_time": int(local_start), "end_time": int(local_end)}))
    return shifted


def _is_valid_link_failure(item: LinkFail, x_size: int, y_size: int) -> bool:
    router_id = int(item.router_id)
    if router_id < 0 or router_id >= x_size * y_size:
        return False

    x = router_id // y_size
    y = router_id % y_size
    direction = int(item.direction)
    if direction == int(Direction.NORTH):
        return y < y_size - 1
    if direction == int(Direction.SOUTH):
        return y > 0
    if direction == int(Direction.EAST):
        return x < x_size - 1
    if direction == int(Direction.WEST):
        return x > 0
    return False


def _sanitize_failure(failure: FailSlow, x_size: int, y_size: int) -> FailSlow:
    valid_links = [
        item
        for item in failure.link
        if _is_valid_link_failure(item, x_size=x_size, y_size=y_size)
    ]
    return FailSlow(
        router=failure.router,
        link=valid_links,
        lsu=failure.lsu,
        tpu=failure.tpu,
    )


def shift_failure_window(failure: FailSlow, global_offset: int, x_size: int = 4, y_size: int = 4) -> FailSlow:
    return FailSlow(
        **_sanitize_failure(
            FailSlow(
                router=_shift_failure_items(failure.router, global_offset),
                link=_shift_failure_items(failure.link, global_offset),
                lsu=_shift_failure_items(failure.lsu, global_offset),
                tpu=_shift_failure_items(failure.tpu, global_offset),
            ),
            x_size=x_size,
            y_size=y_size,
        ).model_dump()
    )


def failure_active_during_request(failure: FailSlow, request_cycles: int) -> bool:
    for items in (failure.router, failure.link, failure.lsu, failure.tpu):
        for item in items:
            if item.start_time < request_cycles and item.end_time > 0:
                return True
    return False


def failure_to_dict(failure: FailSlow) -> Dict[str, Any]:
    return failure.model_dump()


def _collect_event_json(arch: ContinuousEvalArch) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]], List[List[Any]], List[List[Any]]]:
    maxtime = 0
    core_events = [arch.cores[idx].events for idx in range(arch.x_size * arch.y_size)]
    link_events = [arch.noc.r2r_links[idx].events for idx in range(len(arch.noc.r2r_links))]

    core_events_json = []
    link_events_json = []

    for single_core_events in core_events:
        for event in single_core_events:
            maxtime = max(maxtime, event.end_time)
            core_events_json.append(event.model_dump())

    for single_link_events in link_events:
        for event in single_link_events:
            maxtime = max(maxtime, event.end_time)
            link_events_json.append(event.model_dump())

    maxtime = max(maxtime, int(round(arch.env.now)))
    return maxtime, core_events_json, link_events_json, core_events, link_events


def simulate_request(
    arch_config: ArchConfig,
    failure: FailSlow,
    mapper: NetworkMapper,
    state_manager: StateSpace,
    max_request_cycles: Optional[int] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    simulation_started = time.perf_counter()
    arch = ContinuousEvalArch(arch=arch_config, mapper=mapper, failures=failure)
    arch.run_fail_slow()

    done_event = simpy.events.AllOf(arch.env, [core.process for core in arch.cores])
    timed_out = False

    if max_request_cycles is None:
        arch.env.run(until=done_event)
    else:
        timeout_event = arch.env.timeout(max_request_cycles)
        stop_event = simpy.events.AnyOf(arch.env, [done_event, timeout_event])
        arch.env.run(until=stop_event)
        timed_out = bool(timeout_event.processed and not done_event.processed)

    maxtime, core_events_json, link_events_json, core_events, link_events = _collect_event_json(arch)
    simulation_duration_ms = round((time.perf_counter() - simulation_started) * 1000, 3)
    if timed_out:
        raise TimeoutError(
            f"Request simulation did not finish within {max_request_cycles} cycles (env.now={int(round(arch.env.now))})"
        )

    detect_started = time.perf_counter()
    core_probs, link_probs = detect(
        env_time=maxtime,
        slice_num=11,
        arch_config=arch_config,
        core_events_json=core_events_json,
        link_events_json=link_events_json,
    )
    detect_duration_ms = round((time.perf_counter() - detect_started) * 1000, 3)

    traces = process_events(maxtime, 11, core_events, link_events)

    for time_id, time_slice in enumerate(traces.time_slices):
        for idx, core in enumerate(time_slice.cores):
            core.slow = float(core_probs[time_id][idx])
        for idx, link in enumerate(time_slice.links):
            link.slow = float(link_probs[time_id][idx])

    observation = state_manager.get_observation(trace=traces, noc=arch.noc)
    return {
        "cycles": maxtime,
        "trace": traces,
        "noc": arch.noc,
        "observation": observation,
        "simulation_duration_ms": simulation_duration_ms,
        "detect_duration_ms": detect_duration_ms,
        "total_duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _apply_policy_action(
    mapper: NetworkMapper,
    algo: str,
    model,
    observation: Dict[str, np.ndarray],
    action_config: Dict[str, int],
    trace,
    deterministic: bool,
) -> Dict[str, Any]:
    action, _ = model.predict(observation, deterministic=deterministic)
    decoded = _decode_action(action, model.action_space, action_config)
    if len(decoded) != 4:
        raise ValueError(
            "Loaded model action space is incompatible with continuous local-remap evaluation: "
            f"expected 4 action dimensions, got {decoded} from action space {model.action_space}"
        )
    action_type = ACTION_ID_TO_NAME[int(decoded[3])]
    report = mapper.apply_local_remap(
        layer_id=int(decoded[0]),
        action_type=action_type,
        src_core=int(decoded[1]),
        dst_core=int(decoded[2]),
        trace=trace,
    )
    return {
        "raw_action": _serialize_action(action),
        "decoded_action": [int(v) for v in decoded],
        "action_type": action_type,
        "accepted": report.accepted,
        "reason": report.reason,
        "details": report.details,
    }


def rollout_continuous(
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
    inference_records = []

    for inference_idx in range(times):
        local_failure = shift_failure_window(
            failure,
            global_cycle,
            x_size=arch_config.noc.x,
            y_size=arch_config.noc.y,
        )
        log_timing(
            "continuous.inference.start",
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
            action_report = _apply_policy_action(
                mapper=mapper,
                algo=algo,
                model=model,
                observation=request["observation"],
                action_config=action_config,
                trace=request["trace"],
                deterministic=deterministic,
            )
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
            "continuous.inference",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            request_cycles=request["cycles"],
            global_end_cycle=next_global_cycle,
            failure_active_during_request=record["failure_active_during_request"],
            post_action=action_report,
        )
        global_cycle = next_global_cycle

    summary = {
        "algorithm": algo,
        "model_path": model_path,
        "failure_path": fail_path,
        "times": times,
        "total_cycles": global_cycle,
        "accepted_actions": accepted_actions,
        "rejected_actions": rejected_actions,
        "final_global_cycle": global_cycle,
    }
    return {
        "summary": summary,
        "inferences": inference_records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a trained policy in a continuous-inference setting with a persistent mapping."
    )
    parser.add_argument("--algo", choices=["ppo", "dqn"], default="ppo")
    parser.add_argument("--model", required=True, help="Path to a trained PPO or DQN checkpoint")
    parser.add_argument("--fail", default=os.path.join(DEFAULT_DATASET_DIR, "fail1.json"))
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

    results = rollout_continuous(
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
