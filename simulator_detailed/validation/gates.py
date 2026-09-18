"""Fixed shell-free regression commands and honest process outcome classification."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from ..configs.schemas.validation import CheckResult, GateName, RegressionGate
from .data import integer, parse
from .outcomes import test_gate_outcome

ROOT = Path(__file__).resolve().parents[2]

ROOT_SMOKE = '''
import json, os, tempfile
from pathlib import Path
from configs.schemas.arch_config import ArchConfig
from configs.schemas.failure_configs import FailSlow
from utils.mapper import NetworkMapper, parse_mapping
from utils.definitions import Trace
from simulator.architecture import Arch
from simulator.tracing import process_events
with tempfile.TemporaryDirectory(prefix="validation-root-smoke-") as directory:
    os.environ["THERMAL_TIMING_LOG"] = str(Path(directory) / "timing.jsonl")
    mapper = NetworkMapper(parse_mapping("workloads/darknet19-4-4.json"))
    mapper.gen_dfg()
    arch = Arch(ArchConfig.model_validate_json(Path("configs/instances/gemini4_4.json").read_text()), mapper,
                FailSlow.model_validate_json(Path("configs/instances/normal.json").read_text()))
    arch.execute()
    cores = [c.events for c in arch.cores]
    links = [link.events for link in arch.noc.r2r_links]
    assert all(node.finished for node in mapper.dfg.nodes.values())
    end = max(e.end_time for group in cores + links for e in group)
    trace = process_events(end, 11, cores, links)
    path = Path(directory) / "trace.json"
    path.write_text(trace.model_dump_json())
    assert Trace.model_validate_json(path.read_text()) == trace
    assert len(trace.time_slices) == 11
    assert all(len(t.cores) == 16 and len(t.links) == 48 for t in trace.time_slices)
    print(json.dumps({"completed_nodes": len(mapper.dfg.nodes), "cores": len(cores), "links": len(links), "windows": 11, "json_roundtrip": True}))
'''


def gate_command(gate: GateName) -> list[str]:
    if gate.endswith("unittest"):
        return [sys.executable, "-m", "simulator_detailed.validation.gate_worker", gate]
    if gate == "strict_pyright":
        return [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, "--project", "simulator_detailed/pyrightconfig.phase2.json"]
    if gate == "scoped_ruff":
        return [sys.executable, "-m", "ruff", "check", "simulator_detailed/validation", "simulator_detailed/configs/schemas/validation.py",
                *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "simulator_detailed/tests").glob("test_validation*.py"))]]
    if gate == "root_darknet19_smoke":
        return [sys.executable, "-c", ROOT_SMOKE]
    return [sys.executable, "-m", "unittest", "simulator_detailed.tests.test_memory_adapters.MemoryConsumerBoundaryTests.test_optional_encoder_public_class_graph_shape_and_state_dict"]


def run_gate(selection: RegressionGate) -> CheckResult:
    prerequisites = ("torch", "torch_geometric") if selection.gate == "optional_ml" else ("pyright",) if selection.gate == "strict_pyright" else ("ruff",) if selection.gate == "scoped_ruff" else ("pydantic", "simpy", "numpy", "scipy")
    missing = [name for name in prerequisites if importlib.util.find_spec(name) is None]
    if missing:
        return CheckResult(check_id=selection.gate_id, required=selection.required, tier="model_invariant", outcome="blocked", executed=False, reason="missing prerequisite: " + ", ".join(missing))
    try:
        process = subprocess.run(gate_command(selection.gate), cwd=ROOT, capture_output=True, text=True,
                                 timeout=selection.wall_time_seconds, check=False)
    except FileNotFoundError as exc:
        return CheckResult(check_id=selection.gate_id, required=selection.required, tier="model_invariant", outcome="blocked", executed=False, reason=str(exc))
    except subprocess.TimeoutExpired:
        return CheckResult(check_id=selection.gate_id, required=selection.required, tier="model_invariant", outcome="fail", executed=True, reason="regression gate exceeded enforced wall-time budget")
    outcome = "pass" if process.returncode == 0 else "fail"
    reason = f"exit={process.returncode}; " + (process.stdout + process.stderr)[-14000:]
    if selection.gate.endswith("unittest") and process.returncode == 0:
        counts = parse(process.stdout)
        skipped = counts["skipped"]
        outcome = test_gate_outcome(passed=integer(counts["passed"]), failed=integer(counts["failed"]),
                                    skipped=len(skipped) + integer(counts["expected_failures"]) if isinstance(skipped, list) else 0)
        reason = json.dumps(counts)
    return CheckResult(check_id=selection.gate_id, required=selection.required, tier="model_invariant", outcome=outcome, executed=outcome != "not_run", reason=reason)
