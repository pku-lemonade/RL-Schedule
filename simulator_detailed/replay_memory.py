"""JSON-only entry point for explicitly configured addressed memory replays."""

import argparse
import json
import sys
from pathlib import Path

from .configs.schemas.memory_replay import MemoryProfileSource, MemoryReplay
from .memory_execution import (
    MemoryExecutionPlan,
    MemoryExecutionReplay,
    MemoryExecutionResult,
)
from .memory_plan import MemoryPlan
from .memory_runtime import MemoryRuntime


def load_plan(path: str | Path) -> MemoryExecutionPlan:
    """Resolve source paths relative to the replay and finish admission before runtime."""
    path = Path(path)
    executable = MemoryExecutionReplay.model_validate_json(path.read_text())
    config = MemoryReplay.model_validate(executable.model_dump(exclude={"runtime"}))
    source_path = config.source.profile_path if isinstance(config.source, MemoryProfileSource) else config.source.graph_path
    source: object = json.loads((path.parent / source_path).read_text())
    return MemoryExecutionPlan.compile(MemoryPlan.compile(config, source), executable.runtime)


def run_replay(path: str | Path) -> MemoryExecutionResult:
    return MemoryRuntime(load_plan(path)).run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay admitted addressed memory operations with explicit assumed timing.")
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run_replay(args.replay)
        output = result.model_dump_json(indent=2)
        if args.output is not None:
            args.output.write_text(output + "\n")
        print(output)
        return 0 if result.status == "complete" else 2
    except (OSError, ValueError, TypeError, NotImplementedError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
