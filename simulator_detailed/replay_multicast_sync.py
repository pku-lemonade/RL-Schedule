"""CLI for finite multicast/synchronization workloads."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .configs.schemas.multicast_sync import MulticastSyncWorkload
from .configs.schemas.topology import CanonicalTopology
from .memory_plan import bind_memory_system
from .multicast_memory import MulticastMemoryExecutor
from .multicast_memory_runtime import MulticastMemoryRuntime
from .multicast_pipeline import (
    FinitePipelineExecutor,
    FinitePipelinePlan,
    FinitePipelineWorkload,
)
from .multicast_plan import MulticastSyncPlan
from .multicast_scalar import ScalarExecutor


def _atomic_output(path: Path, payload: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".multicast-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_workload(path: Path):
    document = json.loads(path.read_text())
    if document.get("kind") == "multicast_pipeline_workload":
        workload = FinitePipelineWorkload.model_validate(document)
        source = workload.multicast.memory.source
    else:
        workload = MulticastSyncWorkload.model_validate(document)
        source = workload.memory.source
    graph_path = (path.parent / (source.graph_path if source.kind == "canonical_graph" else source.profile_path)).resolve()
    document = json.loads(graph_path.read_text())
    graph = (CanonicalTopology.model_validate(document) if isinstance(workload, FinitePipelineWorkload)
             else bind_memory_system(workload.memory, document))
    return workload, graph, graph_path


def run_workload(path: Path) -> tuple[dict[str, object], Path]:
    workload, graph, graph_path = load_workload(path)
    if isinstance(workload, FinitePipelineWorkload):
        result = FinitePipelineExecutor(FinitePipelinePlan.compile(workload, graph)).run()
        if result.elapsed_aci_cycles > workload.multicast.memory.max_aci_cycles:
            raise ValueError("pipeline projection exceeded its configured horizon; bounded snapshot unavailable")
        return {**result.model_dump(mode="json"), "execution_policy": "serial_multicast_projection_v1"}, graph_path
    plan = MulticastSyncPlan.compile(workload, graph)
    if workload.runtime is not None:
        return MulticastMemoryRuntime(plan).run().model_dump(mode="json"), graph_path
    memory = MulticastMemoryExecutor.compile(plan).run()
    completed = tuple(operation.operation_id for operation in memory.operations if operation.status == "complete")
    scalar = ScalarExecutor.compile(plan).run(external_completed=completed)
    if max(memory.elapsed_aci_cycles, scalar.elapsed_aci_cycles) > workload.memory.max_aci_cycles:
        raise ValueError("multicast projection exceeded its configured horizon; bounded snapshot unavailable")
    return {
        "kind": "multicast_sync_execution_result",
        "schema_version": 1,
        "execution_policy": "serial_multicast_projection_v1",
        "status": "complete" if (memory.status == "complete" and not memory.snapshot.pending_operation_ids
                                 and scalar.status == "complete") else "incomplete",
        "elapsed_aci_cycles": max(memory.elapsed_aci_cycles, scalar.elapsed_aci_cycles),
        "transport": memory.transport.model_dump(mode="json"),
        "operations": [item.model_dump(mode="json") for item in memory.operations],
        "buffers": [item.model_dump(mode="json") for item in memory.buffers],
        "events": [item.model_dump(mode="json") for item in memory.events],
        "scalar": scalar.model_dump(mode="json"),
        "snapshot": memory.snapshot.model_dump(mode="json"),
    }, graph_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a finite multicast/synchronization workload.")
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        workload_path = args.workload.resolve()
        result, graph_path = run_workload(workload_path)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        if args.output is not None:
            output = args.output.resolve()
            if not output.parent.is_dir():
                raise ValueError("output parent directory must already exist")
            if output in {workload_path, graph_path} or (output.exists() and any(
                output.samefile(asset) for asset in (workload_path, graph_path)
            )):
                raise ValueError("output must not replace a declared input asset")
            _atomic_output(output, payload)
        sys.stdout.write(payload)
        policy = "shared finite runtime" if result.get("kind") == "multicast_sync_result" else "serial projection; shared runtime unavailable"
        print(f"multicast replay {result['status']}; {policy}; source={workload_path.name}", file=sys.stderr)
        return 0 if result["status"] == "complete" else 1
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
