"""JSON CLI for finite abstract compute workloads with explicitly assumed rates."""

import argparse
import json
import sys
from pathlib import Path

from .compute_memory import ComputeMemoryPlan
from .compute_plan import ComputePlan
from .compute_runtime import ComputeExecutionResult, ComputeOverlapRuntime
from .configs.schemas.compute_workload import ComputeWorkload
from .configs.schemas.memory_replay import MemoryProfileSource
from .memory_execution import MemoryRuntimeConfig


class ComputeExecutionWorkload(ComputeWorkload):
    """CLI input requires transport/control settings as well as workload costs."""

    runtime: MemoryRuntimeConfig


def load_plan(path: str | Path) -> ComputeMemoryPlan:
    """Resolve the graph/profile relative to the workload; admit before allocation."""
    path = Path(path)
    executable = ComputeExecutionWorkload.model_validate_json(path.read_text())
    config = ComputeWorkload.model_validate(executable.model_dump(exclude={"runtime"}))
    binding = config.memory.source
    source_path = binding.profile_path if isinstance(binding, MemoryProfileSource) else binding.graph_path
    source: object = json.loads((path.parent / source_path).read_text())
    return ComputeMemoryPlan.compile(ComputePlan.compile(config, source), executable.runtime)


def run_workload(path: str | Path) -> ComputeExecutionResult:
    return ComputeOverlapRuntime(load_plan(path)).run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute finite FC/matmul scheduling and traffic with explicit costs.")
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run_workload(args.workload)
        output = result.model_dump_json(indent=2)
        if args.output is not None:
            args.output.write_text(output + "\n")
        print(output)
        return 0 if result.status == "complete" else 1
    except (OSError, ValueError, TypeError, NotImplementedError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
