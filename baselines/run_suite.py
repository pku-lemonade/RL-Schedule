import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import json
import multiprocessing
import os
import traceback
from pathlib import Path
from typing import Dict, List

from baselines.no_mitigation import rollout_no_mitigation
from baselines.simple_mitigation import rollout_simple_mitigation


METHODS = {
    "no_mitigation": rollout_no_mitigation,
    "simple_mitigation": rollout_simple_mitigation,
}


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
    return {
        "cases": 0,
        "total_cycles": 0,
        "average_total_cycles": None,
        "accepted_actions": 0,
        "rejected_actions": 0,
        "error_cases": 0,
    }


def _stats_from_results(results: List[Dict]) -> Dict:
    stats = _empty_stats()
    for item in results:
        _accumulate_stats(stats, item["summary"])
    return stats


def _accumulate_stats(stats: Dict, summary: Dict):
    stats["cases"] += 1
    stats["total_cycles"] += int(summary["total_cycles"])
    stats["accepted_actions"] += int(summary["accepted_actions"])
    stats["rejected_actions"] += int(summary["rejected_actions"])
    stats["average_total_cycles"] = stats["total_cycles"] / stats["cases"] if stats["cases"] else None


def _record_error(stats: Dict):
    stats["error_cases"] += 1


def _summary_csv_rows(suite_summary: Dict) -> List[Dict]:
    rows: List[Dict] = []
    for method, payload in suite_summary["methods"].items():
        rows.append({
            "pattern": "ALL",
            "method": method,
            "cases": payload["overall"]["cases"],
            "times": suite_summary["config"]["times"],
            "total_cycles": payload["overall"]["total_cycles"],
            "average_total_cycles": payload["overall"]["average_total_cycles"],
            "accepted_actions": payload["overall"]["accepted_actions"],
            "rejected_actions": payload["overall"]["rejected_actions"],
            "error_cases": payload["overall"]["error_cases"],
        })
        for pattern, stats in payload["per_pattern"].items():
            rows.append({
                "pattern": pattern,
                "method": method,
                "cases": stats["cases"],
                "times": suite_summary["config"]["times"],
                "total_cycles": stats["total_cycles"],
                "average_total_cycles": stats["average_total_cycles"],
                "accepted_actions": stats["accepted_actions"],
                "rejected_actions": stats["rejected_actions"],
                "error_cases": stats["error_cases"],
            })
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


def _task_key(method: str, fail_path: str) -> str:
    fail_file = Path(fail_path)
    return f"{method}::{fail_file.parent.name}::{fail_file.stem}"


def _append_progress(progress_path: Path, record: Dict):
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _initial_suite_summary(args) -> Dict[str, Dict]:
    suite_summary: Dict[str, Dict] = {
        "config": {
            "root": args.root,
            "patterns": args.patterns,
            "methods": args.methods,
            "times": args.times,
            "limit": args.limit,
            "workload": args.workload,
            "hw_config": args.hw_config,
            "max_request_cycles": args.max_request_cycles,
            "jobs": args.jobs,
        },
        "methods": {},
        "errors": [],
    }
    for method in args.methods:
        suite_summary["methods"][method] = {
            "overall": _empty_stats(),
            "per_pattern": {pattern: _empty_stats() for pattern in args.patterns},
        }
    return suite_summary


def _apply_progress_record(suite_summary: Dict, record: Dict):
    method = record["method"]
    pattern = record["pattern"]
    if record["status"] == "ok":
        _accumulate_stats(suite_summary["methods"][method]["overall"], record["summary"])
        _accumulate_stats(suite_summary["methods"][method]["per_pattern"][pattern], record["summary"])
        return

    suite_summary["errors"].append({
        "method": method,
        "pattern": pattern,
        "case": record["case"],
        "fail_path": record["fail_path"],
        "error_type": record["error_type"],
        "error_message": record["error_message"],
        "traceback": record["traceback"],
    })
    _record_error(suite_summary["methods"][method]["overall"])
    _record_error(suite_summary["methods"][method]["per_pattern"][pattern])


def _load_existing_progress(progress_path: Path, args) -> tuple[Dict, set[str]]:
    suite_summary = _initial_suite_summary(args)
    completed: set[str] = set()
    if not progress_path.exists():
        return suite_summary, completed

    with progress_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            completed.add(record["task_key"])
            _apply_progress_record(suite_summary, record)
    return suite_summary, completed


def _run_case(task: Dict) -> Dict:
    method = task["method"]
    fail_path = task["fail_path"]
    fail_file = Path(fail_path)
    try:
        result = METHODS[method](
            fail_path=fail_path,
            workload_path=task["workload_path"],
            hw_config_path=task["hw_config_path"],
            times=task["times"],
            max_request_cycles=task["max_request_cycles"],
        )
        return {
            "status": "ok",
            "task_key": _task_key(method, fail_path),
            "method": method,
            "pattern": fail_file.parent.name,
            "case": fail_file.stem,
            "fail_path": fail_path,
            "summary": result["summary"],
            "result": result if task["save_per_case"] else None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "task_key": _task_key(method, fail_path),
            "method": method,
            "pattern": fail_file.parent.name,
            "case": fail_file.stem,
            "fail_path": fail_path,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
        }


def _persist_result(output_dir: Path, record: Dict, save_per_case: bool):
    _apply_progress_record_to_disk = record.copy()
    if "result" in _apply_progress_record_to_disk:
        _apply_progress_record_to_disk.pop("result")
    _append_progress(output_dir / "progress.jsonl", _apply_progress_record_to_disk)

    if save_per_case and record["status"] == "ok" and record.get("result") is not None:
        case_output = output_dir / f"{record['method']}__{record['pattern']}__{record['case']}.json"
        with case_output.open("w", encoding="utf-8") as handle:
            json.dump(record["result"], handle, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Run baseline suites across fail-slow dataset patterns and emit aggregated summaries for plotting."
    )
    parser.add_argument("--root", default="fail-slow", help="Root directory containing fail-slow/<pattern>/")
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["center", "edge_ring", "scratch", "uniform"],
        help="Dataset patterns to include",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=sorted(METHODS.keys()),
        default=["no_mitigation", "simple_mitigation"],
        help="Baselines to run",
    )
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests per case")
    parser.add_argument("--limit", type=int, default=None, help="Max fail*.json cases per pattern to run")
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="Number of fail cases to run in parallel",
    )
    parser.add_argument("--workload", default="workloads/darknet19-4-4.json")
    parser.add_argument("--hw-config", default="configs/instances/gemini4_4.json")
    parser.add_argument("--max-request-cycles", type=int, default=40000000)
    parser.add_argument(
        "--output-dir",
        default="../data/rl-result/baselines",
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
        "methods": args.methods,
        "times": args.times,
        "limit": args.limit,
        "jobs": args.jobs,
        "fail_files": [str(path) for path in fail_files],
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(run_manifest, handle, indent=2)

    if args.resume:
        suite_summary, completed = _load_existing_progress(progress_path, args)
    else:
        suite_summary = _initial_suite_summary(args)
        completed = set()
        if progress_path.exists():
            progress_path.write_text("", encoding="utf-8")
    _write_summary(output_dir, suite_summary)

    tasks = []
    for method in args.methods:
        for fail_file in fail_files:
            fail_path = str(fail_file)
            task_key = _task_key(method, fail_path)
            if task_key in completed:
                continue
            tasks.append({
                "method": method,
                "fail_path": fail_path,
                "workload_path": args.workload,
                "hw_config_path": args.hw_config,
                "times": args.times,
                "max_request_cycles": args.max_request_cycles,
                "save_per_case": args.save_per_case,
            })

    if args.jobs == 1:
        for task in tasks:
            record = _run_case(task)
            _persist_result(output_dir, record, args.save_per_case)
            _apply_progress_record(suite_summary, record)
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
                _apply_progress_record(suite_summary, record)
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
