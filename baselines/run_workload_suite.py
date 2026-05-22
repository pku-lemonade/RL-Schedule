import argparse
import csv
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Dict, List

from pydantic import ValidationError

from configs.schemas.mapping_config import Network


DEFAULT_WORKLOADS = [
    "workloads/googlenet-4-4.json",
    "workloads/resnet50-4-4.json",
    "workloads/vgg-4-4.json",
]


def _empty_stats() -> Dict:
    return {
        "cases": 0,
        "total_cycles": 0,
        "average_total_cycles": None,
        "accepted_actions": 0,
        "rejected_actions": 0,
        "error_cases": 0,
    }


def _accumulate_summary(stats: Dict, other: Dict):
    stats["cases"] += int(other["cases"])
    stats["total_cycles"] += int(other["total_cycles"])
    stats["accepted_actions"] += int(other["accepted_actions"])
    stats["rejected_actions"] += int(other["rejected_actions"])
    stats["error_cases"] += int(other["error_cases"])
    stats["average_total_cycles"] = stats["total_cycles"] / stats["cases"] if stats["cases"] else None


def _resolve_workload_path(workload: str) -> Path:
    candidate = Path(workload)
    if candidate.exists():
        return candidate.resolve()

    if not workload.endswith(".json"):
        candidate = Path("workloads") / f"{workload}.json"
        if candidate.exists():
            return candidate.resolve()

    candidate = Path("workloads") / workload
    if candidate.exists():
        return candidate.resolve()

    raise FileNotFoundError(f"Workload file not found: {workload}")


def _workload_name(workload_path: Path) -> str:
    return workload_path.stem


def _validate_workload_schema(workload_path: Path):
    data = json.loads(workload_path.read_text(encoding="utf-8"))
    try:
        Network.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"{workload_path.name} is not in the current Network mapping schema: {exc.errors(include_url=False)}"
        ) from exc


def _run_single_workload(workload_path: Path, output_dir: Path, args) -> Dict:
    cmd = [
        sys.executable,
        "-m",
        "baselines.run_suite",
        "--root",
        args.root,
        "--patterns",
        *args.patterns,
        "--methods",
        *args.methods,
        "--times",
        str(args.times),
        "--jobs",
        str(args.jobs),
        "--workload",
        str(workload_path),
        "--hw-config",
        args.hw_config,
        "--max-request-cycles",
        str(args.max_request_cycles),
        "--output-dir",
        str(output_dir),
    ]
    if args.limit is not None:
        cmd.extend(["--limit", str(args.limit)])
    if args.save_per_case:
        cmd.append("--save-per-case")
    if args.resume:
        cmd.append("--resume")

    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Workload suite failed for {workload_path.name} with exit code {completed.returncode}"
        )

    summary_path = output_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Expected workload summary at {summary_path}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_existing_workload_summaries(output_dir: Path, workload_names: List[str]) -> Dict[str, Dict]:
    summaries: Dict[str, Dict] = {}
    for workload_name in workload_names:
        summary_path = output_dir / workload_name / "summary.json"
        if summary_path.exists():
            summaries[workload_name] = json.loads(summary_path.read_text(encoding="utf-8"))
    return summaries


def _build_combined_summary(args, workload_paths: List[Path], workload_summaries: Dict[str, Dict], runner_errors: List[Dict]) -> Dict:
    methods = {}
    patterns = list(args.patterns)
    for method in args.methods:
        methods[method] = {
            "overall": _empty_stats(),
            "per_workload": {name: _empty_stats() for name in workload_summaries.keys()},
            "per_pattern": {pattern: _empty_stats() for pattern in patterns},
            "per_workload_pattern": {
                name: {pattern: _empty_stats() for pattern in patterns}
                for name in workload_summaries.keys()
            },
        }

    for workload_name, summary in workload_summaries.items():
        for method in args.methods:
            method_summary = summary["methods"][method]
            _accumulate_summary(methods[method]["overall"], method_summary["overall"])
            _accumulate_summary(methods[method]["per_workload"][workload_name], method_summary["overall"])
            for pattern, pattern_summary in method_summary["per_pattern"].items():
                _accumulate_summary(methods[method]["per_pattern"][pattern], pattern_summary)
                _accumulate_summary(
                    methods[method]["per_workload_pattern"][workload_name][pattern],
                    pattern_summary,
                )

    return {
        "config": {
            "root": args.root,
            "patterns": args.patterns,
            "methods": args.methods,
            "workloads": [str(path) for path in workload_paths],
            "times": args.times,
            "limit": args.limit,
            "jobs": args.jobs,
            "hw_config": args.hw_config,
            "max_request_cycles": args.max_request_cycles,
        },
        "methods": methods,
        "workloads": {
            workload_name: {
                "path": str(next(path for path in workload_paths if _workload_name(path) == workload_name)),
                "summary_path": str((Path(args.output_dir) / workload_name / "summary.json").resolve()),
            }
            for workload_name in workload_summaries.keys()
        },
        "runner_errors": runner_errors,
    }


def _summary_csv_rows(summary: Dict) -> List[Dict]:
    rows: List[Dict] = []
    for method, payload in summary["methods"].items():
        rows.append({
            "workload": "ALL",
            "pattern": "ALL",
            "method": method,
            "cases": payload["overall"]["cases"],
            "times": summary["config"]["times"],
            "total_cycles": payload["overall"]["total_cycles"],
            "average_total_cycles": payload["overall"]["average_total_cycles"],
            "accepted_actions": payload["overall"]["accepted_actions"],
            "rejected_actions": payload["overall"]["rejected_actions"],
            "error_cases": payload["overall"]["error_cases"],
        })
        for pattern, stats in payload["per_pattern"].items():
            rows.append({
                "workload": "ALL",
                "pattern": pattern,
                "method": method,
                "cases": stats["cases"],
                "times": summary["config"]["times"],
                "total_cycles": stats["total_cycles"],
                "average_total_cycles": stats["average_total_cycles"],
                "accepted_actions": stats["accepted_actions"],
                "rejected_actions": stats["rejected_actions"],
                "error_cases": stats["error_cases"],
            })
        for workload_name, stats in payload["per_workload"].items():
            rows.append({
                "workload": workload_name,
                "pattern": "ALL",
                "method": method,
                "cases": stats["cases"],
                "times": summary["config"]["times"],
                "total_cycles": stats["total_cycles"],
                "average_total_cycles": stats["average_total_cycles"],
                "accepted_actions": stats["accepted_actions"],
                "rejected_actions": stats["rejected_actions"],
                "error_cases": stats["error_cases"],
            })
            for pattern, pattern_stats in payload["per_workload_pattern"][workload_name].items():
                rows.append({
                    "workload": workload_name,
                    "pattern": pattern,
                    "method": method,
                    "cases": pattern_stats["cases"],
                    "times": summary["config"]["times"],
                    "total_cycles": pattern_stats["total_cycles"],
                    "average_total_cycles": pattern_stats["average_total_cycles"],
                    "accepted_actions": pattern_stats["accepted_actions"],
                    "rejected_actions": pattern_stats["rejected_actions"],
                    "error_cases": pattern_stats["error_cases"],
                })
    return rows


def _write_csv(path: Path, rows: List[Dict]):
    fieldnames = [
        "workload",
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


def _write_combined_outputs(output_dir: Path, summary: Dict):
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    _write_csv(output_dir / "summary.csv", _summary_csv_rows(summary))
    with (output_dir / "errors.json").open("w", encoding="utf-8") as handle:
        json.dump(summary.get("runner_errors", []), handle, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Run baseline experiments across multiple workloads and emit a combined summary."
    )
    parser.add_argument("--root", default="fail-slow", help="Root directory containing fail-slow/<pattern>/")
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["center"],
        help="Dataset patterns to include",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["no_mitigation", "simple_mitigation"],
        choices=["no_mitigation", "simple_mitigation"],
        help="Baseline methods to run",
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        default=DEFAULT_WORKLOADS,
        help="Workload JSON files or workload names to run",
    )
    parser.add_argument("--times", type=int, default=10, help="Number of continuous inference requests per case")
    parser.add_argument("--limit", type=int, default=None, help="Max fail*.json cases per pattern to run")
    parser.add_argument("--jobs", type=int, default=32, help="Number of fail cases to run in parallel within each workload")
    parser.add_argument("--hw-config", default="configs/instances/gemini4_4.json")
    parser.add_argument("--max-request-cycles", type=int, default=1000000000)
    parser.add_argument(
        "--output-dir",
        default="../data/rl-result/baselines/workloads_center_times10",
        help="Directory to store aggregated multi-workload summaries",
    )
    parser.add_argument("--save-per-case", action="store_true", help="Also save per-case JSON outputs in workload subdirs")
    parser.add_argument("--resume", action="store_true", help="Resume each workload run from its progress log if present")
    args = parser.parse_args()

    workload_paths = [_resolve_workload_path(item) for item in args.workloads]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "root": args.root,
        "patterns": args.patterns,
        "methods": args.methods,
        "workloads": [str(path) for path in workload_paths],
        "times": args.times,
        "limit": args.limit,
        "jobs": args.jobs,
        "hw_config": args.hw_config,
        "max_request_cycles": args.max_request_cycles,
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    runner_errors: List[Dict] = []
    workload_summaries = _load_existing_workload_summaries(
        output_dir,
        [_workload_name(path) for path in workload_paths],
    ) if args.resume else {}

    initial_summary = _build_combined_summary(args, workload_paths, workload_summaries, runner_errors)
    _write_combined_outputs(output_dir, initial_summary)

    for workload_path in workload_paths:
        workload_name = _workload_name(workload_path)
        workload_output_dir = output_dir / workload_name
        try:
            _validate_workload_schema(workload_path)
            workload_output_dir.mkdir(parents=True, exist_ok=True)
            summary = _run_single_workload(workload_path, workload_output_dir, args)
            workload_summaries[workload_name] = summary
        except Exception as exc:
            runner_errors.append({
                "workload": workload_name,
                "workload_path": str(workload_path),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(),
            })

        combined_summary = _build_combined_summary(args, workload_paths, workload_summaries, runner_errors)
        _write_combined_outputs(output_dir, combined_summary)
        print(json.dumps({
            "workload": workload_name,
            "status": "ok" if workload_name in workload_summaries else "error",
        }, indent=2))

    final_summary = _build_combined_summary(args, workload_paths, workload_summaries, runner_errors)
    _write_combined_outputs(output_dir, final_summary)
    print(json.dumps(final_summary, indent=2))


if __name__ == "__main__":
    main()
