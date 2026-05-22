import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from rl_agent.evaluate_continuous import (
    failure_active_during_request,
    failure_to_dict,
    shift_failure_window,
    simulate_request,
)
from rl_agent.spaces.states import StateSpace
from rl_agent.workflow_config import DEFAULT_HW_CONFIG_PATH, DEFAULT_NUM_CORES, DEFAULT_NUM_LINKS, DEFAULT_WORKLOAD_PATH
from simulator.run import arch_analyzer, fail_analyzer
from utils.definitions import Slice
from utils.mapper import NetworkMapper, parse_mapping
from utils.timing_logger import log_timing


def _core_slow_scores(trace) -> Dict[int, float]:
    totals: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    for time_slice in trace.time_slices:
        for core in time_slice.cores:
            totals[core.id] = totals.get(core.id, 0.0) + float(core.slow)
            counts[core.id] = counts.get(core.id, 0) + 1
    return {core_id: totals[core_id] / counts[core_id] for core_id in totals}


def _adjacent_axis(lhs, rhs) -> Optional[int]:
    changed = []
    for axis in range(len(lhs)):
        if lhs[axis].start != rhs[axis].start or lhs[axis].end != rhs[axis].end:
            changed.append(axis)
    if len(changed) != 1:
        return None
    axis = changed[0]
    for other_axis in range(len(lhs)):
        if other_axis == axis:
            continue
        if lhs[other_axis].start != rhs[other_axis].start or lhs[other_axis].end != rhs[other_axis].end:
            return None
    if lhs[axis].end == rhs[axis].start or rhs[axis].end == lhs[axis].start:
        return axis
    return None


def _binding_size(binding) -> int:
    return Slice(tensor_slice=binding.output_slice).size()


def _select_simple_remove_action(mapper: NetworkMapper, trace) -> Optional[Dict[str, Any]]:
    scores = _core_slow_scores(trace)
    if not scores:
        return None

    src_core = max(scores.items(), key=lambda item: (item[1], -item[0]))[0]
    candidate_actions: List[Tuple[Tuple[float, float, float, int], Dict[str, Any]]] = []

    for layer_id, view in mapper.layer_views.items():
        src_binding = mapper._find_binding(view, src_core)
        if src_binding is None:
            continue

        for dst_binding in view.bindings:
            if dst_binding.core_id == src_core:
                continue
            if _adjacent_axis(src_binding.output_slice, dst_binding.output_slice) is None:
                continue

            dst_score = scores.get(dst_binding.core_id, 0.0)
            dst_capacity = mapper._capacity(trace, dst_binding.core_id)
            src_workload = float(_binding_size(src_binding))
            candidate_actions.append(
                (
                    (-src_workload, dst_score, -dst_capacity, dst_binding.core_id),
                    {
                        "layer_id": layer_id,
                        "src_core": src_core,
                        "dst_core": dst_binding.core_id,
                        "src_score": scores.get(src_core, 0.0),
                        "dst_score": dst_score,
                        "src_workload": src_workload,
                    },
                )
            )

    if not candidate_actions:
        return {
            "action_type": "remove",
            "selected_core": src_core,
            "accepted": False,
            "reason": "highest-probability core is not active in any removable adjacent block pair",
            "details": {},
        }

    candidate_actions.sort(key=lambda item: item[0])
    for _, candidate in candidate_actions:
        report = mapper.apply_local_remap(
            layer_id=int(candidate["layer_id"]),
            action_type="remove",
            src_core=int(candidate["src_core"]),
            dst_core=int(candidate["dst_core"]),
            trace=trace,
        )
        result = {
            "action_type": "remove",
            "selected_core": src_core,
            "decoded_action": [candidate["layer_id"], candidate["src_core"], candidate["dst_core"], "remove"],
            "accepted": report.accepted,
            "reason": report.reason,
            "details": report.details,
            "heuristic": {
                "src_score": candidate["src_score"],
                "dst_score": candidate["dst_score"],
                "src_workload": candidate["src_workload"],
            },
        }
        if report.accepted:
            return result

    return {
        "action_type": "remove",
        "selected_core": src_core,
        "accepted": False,
        "reason": "no removable adjacent destination passed mapper validation",
        "details": {},
    }


def rollout_simple_mitigation(
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
    state_manager = StateSpace(DEFAULT_NUM_CORES, DEFAULT_NUM_LINKS, max_op_num=1000)

    global_cycle = 0
    accepted_actions = 0
    rejected_actions = 0
    inference_records: List[Dict[str, Any]] = []

    for inference_idx in range(times):
        local_failure = shift_failure_window(failure, global_cycle)
        log_timing(
            "baseline.simple_mitigation.start",
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
            action_report = _select_simple_remove_action(mapper, request["trace"])
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
            "baseline.simple_mitigation",
            inference_index=inference_idx + 1,
            global_start_cycle=global_cycle,
            request_cycles=request["cycles"],
            global_end_cycle=next_global_cycle,
            failure_active_during_request=record["failure_active_during_request"],
            post_action=action_report,
        )
        global_cycle = next_global_cycle

    summary = {
        "method": "simple_mitigation",
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
        description="Continuous-inference heuristic baseline: remove the highest-probability fail-slow core from one active layer block."
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

    results = rollout_simple_mitigation(
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
