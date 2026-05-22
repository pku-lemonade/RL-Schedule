import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from simulator.architecture import Arch
from simulator.run import arch_analyzer, fail_analyzer
from utils.mapper import NetworkMapper, parse_mapping
from utils.definitions import OperatorType


def ds_list(tensor_slice):
    return [(dim.start, dim.end) for dim in tensor_slice] if tensor_slice else []


def make_trace():
    cores = []
    for cid in range(16):
        slow = 0.2
        util = 0.4
        if cid == 14:
            slow = 0.9
            util = 0.9
        elif cid == 7:
            slow = 0.1
            util = 0.1
        cores.append(SimpleNamespace(id=cid, slow=slow, ultilization=util))
    return SimpleNamespace(time_slices=[SimpleNamespace(cores=cores)])


def node_payload(mapper, node_id, layer_ranges):
    node = mapper.dfg.nodes[node_id]
    layer = "other"
    for layer_id, (start, end) in layer_ranges.items():
        if start <= node_id <= end:
            layer = layer_id
            break
    return {
        "index": node.index,
        "layer": layer,
        "op": node.operation.name,
        "core": node.core_id,
        "input": ds_list(node.input_size),
        "weight": ds_list(node.weight_size),
        "output": ds_list(node.output_size),
        "father_count": len(node.father),
        "son_count": len(node.son),
        "received_input": node.received_input,
        "received_weight": node.received_weight,
        "expected_input": node.input_slice().size() if node.input_size else 0,
        "expected_weight": node.weight_slice().size() if node.weight_size else 0,
        "ready": node.ready,
        "executed": node.executed,
        "finished": node.finished,
    }


def first_blocked_node(mapper):
    topo = mapper.toposort()
    first_unfinished = None
    first_blocked = None
    for node_id in topo:
        node = mapper.dfg.nodes[node_id]
        if node.finished:
            continue
        if first_unfinished is None:
            first_unfinished = node_id
        if all(mapper.dfg.nodes[father].finished for father in node.father):
            first_blocked = node_id
            break
    return first_blocked, first_unfinished


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", default="/home/wjc/thermal/workloads/darknet19-4-4.json")
    parser.add_argument("--arch", default="/home/wjc/thermal/configs/instances/gemini4_4.json")
    parser.add_argument("--fail", default="/home/wjc/thermal/configs/instances/normal.json")
    parser.add_argument("--until", type=int, default=30000000)
    parser.add_argument("--output", default="/home/wjc/thermal/logs/stuck_node_report.json")
    args = parser.parse_args()

    mapper = NetworkMapper(parse_mapping(args.mapping))
    trace = make_trace()
    remove_report = mapper.apply_local_remap(18, "remove", 2, 0, trace)
    shift_report = mapper.apply_local_remap(18, "shift", 14, 7, trace)

    arch = Arch(
        arch=arch_analyzer(args.arch),
        mapper=mapper,
        failures=fail_analyzer(args.fail),
    )
    arch.run_fail_slow()
    arch.env.run(until=args.until)

    layer_ranges = mapper.layer_node_ranges
    blocked_id, unfinished_id = first_blocked_node(mapper)
    target_id = blocked_id if blocked_id is not None else unfinished_id

    report = {
        "until": args.until,
        "env_now": arch.env.now,
        "remove_report": remove_report.to_dict() if hasattr(remove_report, "to_dict") else remove_report,
        "shift_report": shift_report.to_dict() if hasattr(shift_report, "to_dict") else shift_report,
        "blocked_node_id": blocked_id,
        "first_unfinished_node_id": unfinished_id,
        "ready_unfinished_nodes": sum(1 for node in mapper.dfg.nodes.values() if node.ready and not node.finished),
        "unfinished_nodes": sum(1 for node in mapper.dfg.nodes.values() if not node.finished),
    }

    if target_id is not None:
        report["target_node"] = node_payload(mapper, target_id, layer_ranges)
        report["father_nodes"] = [
            node_payload(mapper, father_id, layer_ranges)
            for father_id in mapper.dfg.nodes[target_id].father
        ]

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
