import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import json
import multiprocessing
import os
import traceback
from pathlib import Path
from typing import Dict, List

from rl_agent.evaluate_overhead import rollout_overhead, _metric_summary


METRIC_NAMES = [
    "dnn_inference_cycles",
    "simulation_duration_ms",
    "trace_json_bytes",
    "observation_bytes",
    "request_total_duration_ms",
    "detect_duration_ms",
    "policy_inference_ms",
    "action_decode_ms",
    "action_generation_ms",
    "action_application_ms",
    "accepted_action_application_ms",
    "rejected_action_application_ms",
    "raw_action_bytes",
    "json_action_bytes",
]


def _discover_fail_files(root: Path, patterns: List[str], limit: int | None) -> List[Path]:
    fail_files: List[Path] = []
    for pattern in patterns:
        pattern_dir = root / pattern
        if not pattern_dir.exists():
            raise FileNotFoundError(f"Pattern directory not found: {pattern_dir}")
        files = sorted(
            path for path in pattern_dir.glob("fail*.json")
            if path.is_file()
        )
        if limit is not None:
            files = files[:limit]
        fail_files.extend(files)
    return fail_files


def _empty_stats() -> Dict:
    stats = {
        "cases": 0,
        "total_cycles": 0,
        "average_total_cycles": None,
        "accepted_actions": 0,
        "rejected_actions": 0,
        "error_cases": 0,
    }
    for name in METRIC_NAMES:
        stats[name] = None
    return stats


def _new_metric_accumulator() -> Dict[str, List[float]]:
    return {name: [] for name in METRIC_NAMES}


def _extract_metric_samples(result: Dict) -> Dict[str, List[float]]:
    samples = _new_metric_accumulator()
    for inference in result["inferences"]:
        for name in (
            "dnn_inference_cycles",
            "simulation_duration_ms",
            "trace_json_bytes",
            "observation_bytes",
            "request_total_duration_ms",
            "detect_duration_ms",
        ):
            value = inference.get(name)
            if value is not None:
                samples[name].append(float(value))

        post_action = inference.get("post_action")
        if not post_action:
            continue

        for name in (
            "policy_inference_ms",
            "action_decode_ms",
            "action_generation_ms",
            "raw_action_bytes",
            "json_action_bytes",
        ):
            value = post_action.get(name)
            if value is not None:
                samples[name].append(float(value))

        application_ms = post_action.get("application_ms")
        if application_ms is not None:
            samples["action_application_ms"].append(float(application_ms))

        if post_action.get("accepted"):
            if application_ms is not None:
                samples["accepted_action_application_ms"].append(float(application_ms))
        elif application_ms is not None:
            samples["rejected_action_application_ms"].append(float(application_ms))
    return samples


def _update_metric_summaries(stats: Dict, metric_accumulator: Dict[str, List[float]]):
    for name in METRIC_NAMES:
        stats[name] = _metric_summary(metric_accumulator[name])


def _accumulate_success(stats: Dict, metric_accumulator: Dict[str, List[float]], summary: Dict, metric_samples: Dict[str, List[float]]):
    stats["cases"] += 1
    stats["total_cycles"] += int(summary["total_cycles"])
    stats["accepted_actions"] += int(summary["accepted_actions"])
    stats["rejected_actions"] += int(summary["rejected_actions"])
    stats["average_total_cycles"] = stats["total_cycles"] / stats["cases"] if stats["cases"] else None
    for name in METRIC_NAMES:
        metric_accumulator[name].extend(float(v) for v in metric_samples.get(name, []))
    _update_metric_summaries(stats, metric_accumulator)


def _record_error(stats: Dict):
    stats["error_cases"] += 1


def _task_key(method_name: str, fail_path: str) -> str:
    fail_file = Path(fail_path)
    return f"{method_name}::{fail_file.parent.name}::{fail_file.stem}"


def _append_progress(progress_path: Path, record: Dict):
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _initial_suite_summary(args) -> Dict:
    return {
        "config": {
            "root": args.root,
            "patterns": args.patterns,
            "times": args.times,
            "limit": args.limit,
            "jobs": args.jobs,
            "algo": args.algo,
            "model": args.model,
            "workload": args.workload,
            "hw_config": args.hw_config,
            "max_request_cycles": args.max_request_cycles,
            "deterministic": args.deterministic,
            "device": args.device,
        },
        "method_name": args.method_name,
        "method": {
            "overall": _empty_stats(),
            "per_pattern": {pattern: _empty_stats() for pattern in args.patterns},
        },
        "errors": [],
    }


def _initial_metric_state(patterns: List[str]) -> Dict:
    return {
        "overall": _new_metric_accumulator(),
        "per_pattern": {pattern: _new_metric_accumulator() for pattern in patterns},
    }


def _apply_progress_record(suite_summary: Dict, metric_state: Dict, record: Dict):
    pattern = record["pattern"]
    if record["status"] == "ok":
        _accumulate_success(
            suite_summary["method"]["overall"],
            metric_state["overall"],
            record["summary"],
            record["metric_samples"],
        )
        _accumulate_success(
            suite_summary["method"]["per_pattern"][pattern],
            metric_state["per_pattern"][pattern],
            record["summary"],
            record["metric_samples"],
        )
        return

    suite_summary["errors"].append({
        "method": record["method"],
        "pattern": pattern,
        "case": record["case"],
        "fail_path": record["fail_path"],
        "error_type": record["error_type"],
        "error_message": record["error_message"],
        "traceback": record["traceback"],
    })
    _record_error(suite_summary["method"]["overall"])
    _record_error(suite_summary["method"]["per_pattern"][pattern])


def _load_existing_progress(progress_path: Path, args) -> tuple[Dict, Dict, set[str]]:
    suite_summary = _initial_suite_summary(args)
    metric_state = _initial_metric_state(args.patterns)
    completed: set[str] = set()
    if not progress_path.exists():
        return suite_summary, metric_state, completed

    with progress_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            completed.add(record["task_key"])
            _apply_progress_record(suite_summary, metric_state, record)
    return suite_summary, metric_state, completed


def _flatten_metric_columns(row: Dict, metric_name: str, stats: Dict | None):
    suffixes = ("count", "mean", "min", "max", "p50", "p95")
    for suffix in suffixes:
        row[f"{metric_name}_{suffix}"] = None if stats is None else stats.get(suffix)


def _summary_csv_rows(suite_summary: Dict) -> List[Dict]:
    rows: List[Dict] = []
    method = suite_summary["method_name"]
    payload = suite_summary["method"]
    targets = [("ALL", payload["overall"])] + list(payload["per_pattern"].items())
    for pattern, stats in targets:
        row = {
            "pattern": pattern,
            "method": method,
            "cases": stats["cases"],
            "times": suite_summary["config"]["times"],
            "total_cycles": stats["total_cycles"],
            "average_total_cycles": stats["average_total_cycles"],
            "accepted_actions": stats["accepted_actions"],
            "rejected_actions": stats["rejected_actions"],
            "error_cases": stats["error_cases"],
        }
        for name in METRIC_NAMES:
            _flatten_metric_columns(row, name, stats.get(name))
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: List[Dict]):
    fieldnames = [
        "pattern",
        "method",
        "cases",
        "times",
        "total_cycles",
        "average_total_cycles",
        "accepted_actions",
        "rejected_actions",
        "error_cases",
    ]
    for name in METRIC_NAMES:
        for suffix in ("count", "mean", "min", "max", "p50", "p95"):
            fieldnames.append(f"{name}_{suffix}")

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(output_dir: Path, suite_summary: Dict):
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(suite_summary, handle, indent=2)
    _write_csv(output_dir / "summary.csv", _summary_csv_rows(suite_summary))
    with (output_dir / "errors.json").open("w", encoding="utf-8") as handle:
        json.dump(suite_summary.get("errors", []), handle, indent=2)


def _run_case(task: Dict) -> Dict:
    fail_path = task["fail_path"]
    fail_file = Path(fail_path)
    try:
        result = rollout_overhead(
            algo=task["algo"],
            model_path=task["model_path"],
            fail_path=fail_path,
            workload_path=task["workload_path"],
            hw_config_path=task["hw_config_path"],
            times=task["times"],
            deterministic=task["deterministic"],
            device=task["device"],
            max_request_cycles=task["max_request_cycles"],
        )
        result["summary"]["method"] = task["method_name"]
        return {
            "status": "ok",
            "task_key": _task_key(task["method_name"], fail_path),
            "method": task["method_name"],
            "pattern": fail_file.parent.name,
            "case": fail_file.stem,
            "fail_path": fail_path,
            "summary": result["summary"],
            "metric_samples": _extract_metric_samples(result),
            "result": result if task["save_per_case"] else None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "task_key": _task_key(task["method_name"], fail_path),
            "method": task["method_name"],
            "pattern": fail_file.parent.name,
            "case": fail_file.stem,
            "fail_path": fail_path,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
        }


def _persist_result(output_dir: Path, record: Dict, save_per_case: bool):
    to_disk = record.copy()
    if "result" in to_disk:
        to_disk.pop("result")
    _append_progress(output_dir / "progress.jsonl", to_disk)

    if save_per_case and record["status"] == "ok" and record.get("result") is not None:
        case_output = output_dir / f"{record['method']}__{record['pattern']}__{record['case']}.json"
        with case_output.open("w", encoding="utf-8") as handle:
            json.dump(record["result"], handle, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Run overhead-evaluation suites for the trained RL method and emit aggregated summaries."
    )
    parser.add_argument("--algo", choices=["ppo", "dqn"], default="ppo")
    parser.add_argument("--model", required=True, help="Path to the trained PPO or DQN checkpoint")
    parser.add_argument("--method-name", default="rl_with_fail_prob_overhead", help="Method name written into summaries")
    parser.add_argument("--root", default="fail-slow", help="Root directory containing fail-slow/<pattern>/")
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["center"],
        help="Dataset patterns to include",
    )
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests per case")
    parser.add_argument("--limit", type=int, default=None, help="Max fail*.json cases per pattern to run")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="Number of fail cases to run in parallel")
    parser.add_argument("--workload", default="workloads/darknet19-4-4.json")
    parser.add_argument("--hw-config", default="configs/instances/gemini4_4.json")
    parser.add_argument("--max-request-cycles", type=int, default=1000000000)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument(
        "--output-dir",
        default="../data/rl-result/overhead",
        help="Directory to store aggregated summaries",
    )
    parser.add_argument("--save-per-case", action="store_true", help="Also save per-case JSON outputs")
    parser.add_argument("--resume", action="store_true", help="Resume from output-dir/progress.jsonl if it exists")
    args = parser.parse_args()

    if args.jobs <= 0:
        raise ValueError("--jobs must be positive")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.jsonl"

    fail_files = _discover_fail_files(Path(args.root), args.patterns, args.limit)
    run_manifest = {
        "root": args.root,
        "patterns": args.patterns,
        "times": args.times,
        "limit": args.limit,
        "jobs": args.jobs,
        "algo": args.algo,
        "model": args.model,
        "method_name": args.method_name,
        "fail_files": [str(path) for path in fail_files],
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(run_manifest, handle, indent=2)

    if args.resume:
        suite_summary, metric_state, completed = _load_existing_progress(progress_path, args)
    else:
        suite_summary = _initial_suite_summary(args)
        metric_state = _initial_metric_state(args.patterns)
        completed = set()
        if progress_path.exists():
            progress_path.write_text("", encoding="utf-8")
    _write_summary(output_dir, suite_summary)

    tasks = []
    for fail_file in fail_files:
        fail_path = str(fail_file)
        task_key = _task_key(args.method_name, fail_path)
        if task_key in completed:
            continue
        tasks.append({
            "algo": args.algo,
            "model_path": args.model,
            "method_name": args.method_name,
            "fail_path": fail_path,
            "workload_path": args.workload,
            "hw_config_path": args.hw_config,
            "times": args.times,
            "deterministic": args.deterministic,
            "device": args.device,
            "max_request_cycles": args.max_request_cycles,
            "save_per_case": args.save_per_case,
        })

    if args.jobs == 1:
        for task in tasks:
            record = _run_case(task)
            _persist_result(output_dir, record, args.save_per_case)
            _apply_progress_record(suite_summary, metric_state, record)
            _write_summary(output_dir, suite_summary)
            print(json.dumps({
                "status": record["status"],
                "method": record["method"],
                "pattern": record["pattern"],
                "case": record["case"],
            }, indent=2))
    else:
        with ProcessPoolExecutor(
            max_workers=args.jobs,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            futures = [executor.submit(_run_case, task) for task in tasks]
            for future in as_completed(futures):
                record = future.result()
                _persist_result(output_dir, record, args.save_per_case)
                _apply_progress_record(suite_summary, metric_state, record)
                _write_summary(output_dir, suite_summary)
                print(json.dumps({
                    "status": record["status"],
                    "method": record["method"],
                    "pattern": record["pattern"],
                    "case": record["case"],
                }, indent=2))

    print(json.dumps(suite_summary, indent=2))


if __name__ == "__main__":
    main()
