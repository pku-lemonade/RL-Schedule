import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import simpy

from configs.schemas.arch_config import CoreConfig, LinkConfig
from configs.schemas.failure_configs import FailSlow
from rl_agent.workflow_config import DEFAULT_HW_CONFIG_PATH, DEFAULT_WORKLOAD_PATH
from simulator.architecture import Arch
from simulator.core import Core, LSU, Scheduler, ScratchpadMemory, TPU
from simulator.noc import Link
from simulator.run import arch_analyzer, fail_analyzer
from utils.definitions import Direction
from utils.mapper import NetworkMapper, parse_mapping
from utils.timing_logger import log_timing


class ContinuousBaselineCore(Core):
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


class ContinuousBaselineArch(Arch):
    def build_cores(self, env, config: CoreConfig, mapper: NetworkMapper, c2r_width=128, c2r_delay=1):
        cores = []
        for core_id in range(self.x_size * self.y_size):
            core = ContinuousBaselineCore(env=self.env, core_id=core_id, config=config, mapper=mapper)
            link1 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))
            link2 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))

            link1.bind(0, 0, 0, 0, True)
            link2.bind(0, 0, 0, 0, True)

            core.bind_with_router(link2, link1, self.noc.routers[core_id])
            self.noc.routers[core_id].bind_link(direction="core", link_in=link1, link_out=link2)
            cores.append(core)

        return cores


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


def _is_valid_link_failure(item, x_size: int, y_size: int) -> bool:
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


def shift_failure_window(failure: FailSlow, global_offset: int, x_size: int = 4, y_size: int = 4) -> FailSlow:
    shifted = FailSlow(
        router=_shift_failure_items(failure.router, global_offset),
        link=_shift_failure_items(failure.link, global_offset),
        lsu=_shift_failure_items(failure.lsu, global_offset),
        tpu=_shift_failure_items(failure.tpu, global_offset),
    )
    return FailSlow(
        router=shifted.router,
        link=[item for item in shifted.link if _is_valid_link_failure(item, x_size=x_size, y_size=y_size)],
        lsu=shifted.lsu,
        tpu=shifted.tpu,
    )


def failure_active_during_request(failure: FailSlow, request_cycles: int) -> bool:
    for items in (failure.router, failure.link, failure.lsu, failure.tpu):
        for item in items:
            if item.start_time < request_cycles and item.end_time > 0:
                return True
    return False


def failure_to_dict(failure: FailSlow) -> Dict[str, Any]:
    return failure.model_dump()


def simulate_request_cycles(
    arch_config,
    failure: FailSlow,
    mapper: NetworkMapper,
    max_request_cycles: Optional[int] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    arch = ContinuousBaselineArch(arch=arch_config, mapper=mapper, failures=failure)
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

    maxtime = int(round(arch.env.now))
    core_event_count = 0
    link_event_count = 0

    for core in arch.cores:
        core_event_count += len(core.events)
        for event in core.events:
            maxtime = max(maxtime, event.end_time)

    for link in arch.noc.r2r_links:
        link_event_count += len(link.events)
        for event in link.events:
            maxtime = max(maxtime, event.end_time)

    if timed_out:
        raise TimeoutError(
            f"Request simulation did not finish within {max_request_cycles} cycles (env.now={int(round(arch.env.now))})"
        )

    return {
        "cycles": maxtime,
        "core_event_count": core_event_count,
        "link_event_count": link_event_count,
        "total_duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def rollout_no_mitigation(
    fail_path: str,
    workload_path: str,
    hw_config_path: str,
    times: int,
    max_request_cycles: Optional[int],
) -> Dict[str, Any]:
    network = parse_mapping(workload_path)
    mapper = NetworkMapper(network=network)
    mapper.gen_dfg()
    arch_config = arch_analyzer(hw_config_path)
    failure = fail_analyzer(fail_path)

    global_cycle = 0
    inference_records: List[Dict[str, Any]] = []

    for inference_idx in range(times):
        local_failure = shift_failure_window(
            failure,
            global_cycle,
            x_size=arch_config.noc.x,
            y_size=arch_config.noc.y,
        )
        log_timing(
            "baseline.no_mitigation.start",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            failure_path=fail_path,
            effective_failure=failure_to_dict(local_failure),
        )

        request = simulate_request_cycles(
            arch_config=arch_config,
            failure=local_failure,
            mapper=mapper,
            max_request_cycles=max_request_cycles,
        )

        next_global_cycle = global_cycle + request["cycles"]
        record = {
            "inference": inference_idx + 1,
            "global_start_cycle": global_cycle,
            "request_cycles": request["cycles"],
            "global_end_cycle": next_global_cycle,
            "failure_active_during_request": failure_active_during_request(local_failure, request["cycles"]),
            "effective_failure": failure_to_dict(local_failure),
            "core_event_count": request["core_event_count"],
            "link_event_count": request["link_event_count"],
            "total_duration_ms": request["total_duration_ms"],
            "post_action": None,
        }
        inference_records.append(record)

        log_timing(
            "baseline.no_mitigation",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            request_cycles=request["cycles"],
            global_end_cycle=next_global_cycle,
            failure_active_during_request=record["failure_active_during_request"],
        )
        global_cycle = next_global_cycle

    summary = {
        "method": "no_mitigation",
        "failure_path": fail_path,
        "times": times,
        "total_cycles": global_cycle,
        "final_global_cycle": global_cycle,
        "accepted_actions": 0,
        "rejected_actions": 0,
    }
    return {
        "summary": summary,
        "inferences": inference_records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Continuous-inference baseline with no mitigation: always keep the original mapping."
    )
    parser.add_argument("--fail", required=True, help="Fail-slow JSON used as the long-lived fault scenario")
    parser.add_argument("--workload", default=DEFAULT_WORKLOAD_PATH)
    parser.add_argument("--hw-config", default=DEFAULT_HW_CONFIG_PATH)
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests")
    parser.add_argument("--max-request-cycles", type=int, default=None, help="Optional safety bound for one inference")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    if args.times <= 0:
        raise ValueError("--times must be positive")

    results = rollout_no_mitigation(
        fail_path=args.fail,
        workload_path=args.workload,
        hw_config_path=args.hw_config,
        times=args.times,
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
