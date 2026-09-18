"""Named adapters at the public, pre-allocation admission boundaries."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..compute_runtime import ComputeOverlapRuntime
from ..configs.schemas.hardware_profile import HardwareProfileConfig
from ..configs.schemas.torus_replay import ProfileSource, TorusReplay
from ..configs.schemas.validation import AdapterName, CanonicalJSON
from ..hardware_profile import inspect_profile
from ..memory_runtime import MemoryRuntime
from ..replay_compute import load_plan as load_compute
from ..replay_memory import load_plan as load_memory
from ..replay_topology import inspect_topology
from ..torus import TorusPlan
from ..torus_transport import TorusTransport
from ..transport import ReplayPlan, ReplayRuntime
from .data import Data, number, obj, parse, text
from .identity import canonical_record, resolve_asset


@dataclass(frozen=True)
class Admission:
    adapter: AdapterName
    path: Path
    inputs: dict[str, Path]
    configuration: Data
    graph: Data
    effective: CanonicalJSON
    execute: Callable[[tuple[float, ...]], tuple[Data, ...]]


def admit(adapter: AdapterName, path: Path, *, horizon: float | None = None) -> Admission:
    """Resolve all simulator inputs and validate without allocating a runtime."""
    path = path.resolve()
    document = parse(path.read_text())
    inputs = {"case.json": path}
    config = document
    graph: Data = {}
    effective: object
    execute: Callable[[tuple[float, ...]], tuple[Data, ...]]
    if adapter == "profile_inspection_v1":
        profile = HardwareProfileConfig.model_validate_json(path.read_text())
        report = inspect_profile(profile)
        effective = report.model_dump(mode="json")
        execute = lambda stops: (obj(report.model_dump(mode="json")),)
    elif adapter == "topology_inspection_v1":
        topology = inspect_topology(path)
        graph = obj(topology.export())
        effective = graph
        execute = lambda stops: (graph,)
    elif adapter == "topology_replay_v1":
        v1 = ReplayPlan.load(path)
        config = obj(v1.config.model_dump(mode="json"))
        graph = obj(v1.topology.graph.model_dump(mode="json"))
        inputs["source.json"] = resolve_asset(path, v1.config.graph_path)
        effective = parse(v1.effective_json)
        execute = lambda stops: (obj(ReplayRuntime(v1).run().model_dump(mode="json")),)
    elif adapter == "torus_replay_v2":
        torus = TorusReplay.model_validate_json(path.read_text())
        source = torus.source.profile_path if isinstance(torus.source, ProfileSource) else torus.source.graph_path
        inputs["source.json"] = resolve_asset(path, source)
        plan = TorusPlan.compile(torus, parse(inputs["source.json"].read_text()))
        config = parse(plan.record.configuration_json)
        graph = obj(plan.record.graph.model_dump(mode="json"))
        effective = plan.record
        execute = lambda stops: (obj(TorusTransport(plan).run().model_dump(mode="json")),)
    elif adapter == "memory_replay_v1":
        memory = load_memory(path)
        config = obj(memory.memory.config.model_dump(mode="json"))
        graph = obj(memory.memory.graph.model_dump(mode="json"))
        effective = {"memory": memory.memory.record.model_dump(mode="json"), "runtime": memory.settings.model_dump(mode="json")}

        def memory_execute(stops: tuple[float, ...]) -> tuple[Data, ...]:
            runtime = MemoryRuntime(memory)
            snapshots = [obj(runtime.advance(max_aci_cycles=stop).model_dump(mode="json")) for stop in stops]
            return (*snapshots, obj(runtime.run().model_dump(mode="json")))

        execute = memory_execute
    else:
        compute = load_compute(path)
        config = obj(compute.workload.config.model_dump(mode="json"))
        graph = obj(compute.workload.graph.model_dump(mode="json"))
        effective = {"workload": compute.workload.record.model_dump(mode="json"), "runtime": compute.session.execution.settings.model_dump(mode="json")}

        def compute_execute(stops: tuple[float, ...]) -> tuple[Data, ...]:
            runtime = ComputeOverlapRuntime(compute)
            snapshots = [obj(runtime.advance(max_aci_cycles=stop).model_dump(mode="json")) for stop in stops]
            return (*snapshots, obj(runtime.run().model_dump(mode="json")))

        execute = compute_execute
    if adapter in ("memory_replay_v1", "compute_workload_v1"):
        memory_config = obj(config["memory"]) if adapter == "compute_workload_v1" else config
        binding = obj(memory_config["source"])
        inputs["source.json"] = resolve_asset(path, text(binding.get("graph_path", binding.get("profile_path"))))
    if "inspection" not in adapter:
        bounded = obj(config["memory"]) if adapter == "compute_workload_v1" else config
        limit = number(bounded["max_aci_cycles"])
        if horizon is not None and limit > horizon:
            raise ValueError(f"input simulation horizon {limit} exceeds case budget {horizon}")
    return Admission(adapter, path, inputs, config, graph, canonical_record(effective), execute)
