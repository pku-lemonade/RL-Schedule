import json
import time
import logging
import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Dict, List, Protocol, Tuple, cast
from pydantic import ValidationError

if __name__ == '__main__' and __package__ is None:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    __package__ = 'simulator_detailed'

from .architecture import Arch, NoCFabrics
from .noc import NoCLinkIdentity
from .tracing import collect_noc_link_events, process_events
from .utils.mapper import NetworkMapper, parse_mapping
from .utils.definitions import Event, Trace
from .configs.schemas.arch_config import ArchConfig
from .configs.schemas.failure_configs import FailSlow

JsonEvent = Dict[str, object]
DetectionScores = Sequence[Sequence[float]]


class Detector(Protocol):
    def __call__(
        self,
        env_time: float,
        slice_num: int,
        arch_config: ArchConfig,
        core_events_json: List[JsonEvent],
        link_events_json: List[JsonEvent],
    ) -> Tuple[DetectionScores, DetectionScores]: ...


detect: Detector | None
try:
    from .predictor.predict import detect as imported_detect

    detect = cast(Detector, imported_detect)
except ImportError:
    detect = None
from .utils.timing_logger import log_timing

_INSTANCE_CONFIG_DIR = Path(__file__).resolve().parent / "configs" / "instances"
DEFAULT_ARCH_PATH = str(_INSTANCE_CONFIG_DIR / "ada2s32.json")
DEFAULT_FAILURE_PATH = str(_INSTANCE_CONFIG_DIR / "normal.json")


def fail_analyzer(filename: str) -> FailSlow:
    """Load and validate fail-slow configuration."""
    with open(filename, 'r') as file:
        data = json.load(file)
        try:
            return FailSlow.model_validate(data)
        except ValidationError as e:
            print(e.json())
            raise


def arch_analyzer(filename: str) -> ArchConfig:
    """Load and validate architecture configuration."""
    with open(filename, 'r') as file:
        data = json.load(file)
        try:
            return ArchConfig.model_validate(data)
        except ValidationError as e:
            print(e.json())
            raise


def setup_logging(filename: str, level: int):
    """Configure logging output."""
    logging.basicConfig(
        level    = level,
        format   = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt  = '%Y-%m-%d %H:%M:%S',
        filename = filename,
        filemode = 'w',
    )


def _collect_simulation_events(
    arch: Arch,
) -> Tuple[
    float,
    List[List[Event]],
    List[List[Event]],
    List[NoCLinkIdentity],
    List[JsonEvent],
    List[JsonEvent],
]:
    core_events: List[List[Event]] = [core.events for core in arch.cores]
    link_events, link_identities = collect_noc_link_events(arch.nocs)
    core_events_json: List[JsonEvent] = []
    link_events_json: List[JsonEvent] = []
    maxtime = 0.0

    for event_stream in core_events:
        for event in event_stream:
            maxtime = max(maxtime, event.end_time)
            core_events_json.append(event.model_dump(mode="json"))

    for event_stream in link_events:
        for event in event_stream:
            maxtime = max(maxtime, event.end_time)
            link_events_json.append(event.model_dump(mode="json"))

    return (
        maxtime,
        core_events,
        link_events,
        link_identities,
        core_events_json,
        link_events_json,
    )


def simulate_old() -> Tuple[float, Trace, NoCFabrics]:
    print("Start simulation.")
    # === Step 1: Load configuration files ===
    print("Step 1: Load configuration files...")
    parser = argparse.ArgumentParser()

    parser.add_argument("--mapping", type=str, default="workloads/darknet19-4-4.json")
    parser.add_argument("--arch", type=str, default=DEFAULT_ARCH_PATH)
    parser.add_argument("--fail", type=str, default=DEFAULT_FAILURE_PATH)
    parser.add_argument("--log", type=str, default="logs/simulation.log")
    parser.add_argument("--level", type=str, default="info")
    parser.add_argument("--slice", type=int, default=11)

    args = parser.parse_args()

    arch_config = arch_analyzer(args.arch)
    failure = fail_analyzer(args.fail)

    network = parse_mapping(args.mapping)
    mapper = NetworkMapper(network=network)
    mapper.gen_dfg()
    mapper.dfg.print("dfg.txt")

    # === Step 2: Setup logging ===
    print("Step 2: Setup logging...")
    if args.level == "debug":
        level = logging.DEBUG
    elif args.level == "info":
        level = logging.INFO
    else:
        level = logging.WARNING

    setup_logging(filename = args.log, level = level)


    # === Step 3: Initialize architecture ===
    print("Step 3: Initialize architecture...")
    arch = Arch(arch=arch_config, mapper=mapper, failures=failure)

    # === Step 4: Run simulation ===
    print("Step 4: Run simulation...")
    start_time = time.time()
    arch.execute()
    end_time = time.time()

    simulation_time = end_time - start_time
    print(f"Simulation time is {simulation_time:.2f} s.")

    # === Step 5: Generate trace data ===
    print("Step 5: Generate trace data...")

    (
        maxtime,
        core_events,
        link_events,
        link_identities,
        core_events_json,
        link_events_json,
    ) = _collect_simulation_events(arch)

    # with open("core.json", "w") as file:
    #     print(core_events_json, file=file)
    # with open("link.json", "w") as file:
    #     print(link_events_json, file=file)

    # === Step 6: Failslow detection ===
    print("Step 6: Failslow detection...")

    core_probs: DetectionScores | None
    link_probs: DetectionScores | None
    if detect is not None:
        core_probs, link_probs = detect(env_time=maxtime,
                                        slice_num=args.slice,
                                        arch_config=arch_config,
                                        core_events_json=core_events_json,
                                        link_events_json=link_events_json)
    else:
        core_probs, link_probs = None, None

    # === Step 7: Generate execution statistics ===
    print("Step 7: Generate execution statistics...")

    traces = process_events(
        maxtime,
        args.slice,
        core_events,
        link_events,
        link_identities,
    )

    if core_probs is None or link_probs is None:
        core_probs = [[0.0] * len(ts.cores) for ts in traces.time_slices]
        link_probs = [[0.0] * len(ts.links) for ts in traces.time_slices]

    for time_id, time_slice in enumerate(traces.time_slices):
        for id, core in enumerate(time_slice.cores):
            core.slow = float(core_probs[time_id][id])
        
        for id, link in enumerate(time_slice.links):
            link.slow = float(link_probs[time_id][id])
    
    with open("trace.json", "w") as file:
        trace_json = traces.model_dump_json(indent=4)
        print(trace_json, file=file)
    
    # print(f"Cycles {maxtime}")
    print("Simulation finished.")
    return maxtime, traces, arch.nocs


def simulate(
    arch_path: str,
    failure_path: str,
    mapper: NetworkMapper,
    verbose: bool = False,
) -> Tuple[float, Trace, NoCFabrics]:
    # === Step 0: Parameter definition ===
    log_path = "logs/simulation.log"
    level = "debug"
    slice_num = 11
    simulate_started = time.perf_counter()
    load_duration_ms = 0.0
    init_duration_ms = 0.0
    execute_duration_ms = 0.0
    event_build_duration_ms = 0.0
    detect_duration_ms = 0.0
    trace_duration_ms = 0.0
    last_certification = None
    _mapper_cert = getattr(mapper, "last_certification", None)
    if _mapper_cert is not None:
        last_certification = _mapper_cert.to_dict()

    log_timing(
        "simulate.start",
        arch_path=arch_path,
        failure_path=failure_path,
        mapper_nodes=mapper.node_counter,
        last_certification=last_certification,
    )

    if verbose: print("Start simulation.")
    # === Step 1: Load configuration files ===
    if verbose: print("Step 1: Load configuration files...")
    load_started = time.perf_counter()
    arch_config = arch_analyzer(arch_path)
    failure = fail_analyzer(failure_path)
    load_duration_ms = round((time.perf_counter() - load_started) * 1000, 3)

    # === Step 2: Setup logging ===
    if verbose: print("Step 2: Setup logging...")
    if level == "debug":
        level = logging.DEBUG
    elif level == "info":
        level = logging.INFO
    else:
        level = logging.WARNING

    setup_logging(filename = log_path, level = level)


    # === Step 3: Initialize architecture ===
    if verbose: print("Step 3: Initialize architecture...")
    init_started = time.perf_counter()
    arch = Arch(arch=arch_config, mapper=mapper, failures=failure)
    init_duration_ms = round((time.perf_counter() - init_started) * 1000, 3)

    # === Step 4: Run simulation ===
    if verbose: print("Step 4: Run simulation...")
    start_time = time.time()
    execute_started = time.perf_counter()
    arch.execute()
    end_time = time.time()
    execute_duration_ms = round((time.perf_counter() - execute_started) * 1000, 3)

    simulation_time = end_time - start_time
    if verbose: print(f"Simulation time is {simulation_time:.2f} s.")

    # === Step 5: Generate trace data ===
    if verbose: print("Step 5: Generate trace data...")
    event_build_started = time.perf_counter()

    (
        maxtime,
        core_events,
        link_events,
        link_identities,
        core_events_json,
        link_events_json,
    ) = _collect_simulation_events(arch)
    event_build_duration_ms = round((time.perf_counter() - event_build_started) * 1000, 3)

    # === Step 6: Failslow detection ===
    if verbose: print("Step 6: Failslow detection...")

    # print(f"maxtime: {maxtime}")
    # print(f"slice_num: {slice_num}")
    # print(f"arch_config: {arch_config}")
    # print(f"core_events_json: {core_events_json}")
    # print(f"link_events_json: {link_events_json}")

    detect_started = time.perf_counter()
    core_probs: DetectionScores | None
    link_probs: DetectionScores | None
    if detect is not None:
        core_probs, link_probs = detect(env_time=maxtime,
                                        slice_num=slice_num,
                                        arch_config=arch_config,
                                        core_events_json=core_events_json,
                                        link_events_json=link_events_json)
    else:
        core_probs, link_probs = None, None
    detect_duration_ms = round((time.perf_counter() - detect_started) * 1000, 3)

    # === Step 7: Generate execution statistics ===
    if verbose: print("Step 7: Generate execution statistics...")
    trace_started = time.perf_counter()

    traces = process_events(
        maxtime,
        slice_num,
        core_events,
        link_events,
        link_identities,
    )

    if core_probs is None or link_probs is None:
        core_probs = [[0.0] * len(ts.cores) for ts in traces.time_slices]
        link_probs = [[0.0] * len(ts.links) for ts in traces.time_slices]

    for time_id, time_slice in enumerate(traces.time_slices):
        for id, core in enumerate(time_slice.cores):
            core.slow = float(core_probs[time_id][id])
        
        for id, link in enumerate(time_slice.links):
            link.slow = float(link_probs[time_id][id])

    with open("trace.json", "w") as file:
        trace_json = traces.model_dump_json(indent=4)
        print(trace_json, file=file)
    trace_duration_ms = round((time.perf_counter() - trace_started) * 1000, 3)
    
    # print(f"Cycles {maxtime}")
    if verbose: print("Simulation finished.")

    total_duration_ms = round((time.perf_counter() - simulate_started) * 1000, 3)
    log_timing(
        "simulate",
        arch_path=arch_path,
        failure_path=failure_path,
        mapper_nodes=mapper.node_counter,
        maxtime=maxtime,
        core_event_count=len(core_events_json),
        link_event_count=len(link_events_json),
        load_duration_ms=load_duration_ms,
        init_duration_ms=init_duration_ms,
        execute_duration_ms=execute_duration_ms,
        event_build_duration_ms=event_build_duration_ms,
        detect_duration_ms=detect_duration_ms,
        trace_duration_ms=trace_duration_ms,
        total_duration_ms=total_duration_ms,
    )
    return maxtime, traces, arch.nocs


if __name__ == '__main__':
    network = parse_mapping("workloads/darknet19-4-4.json")
    mapper = NetworkMapper(network=network)
    mapper.gen_dfg()

    simulate(arch_path=DEFAULT_ARCH_PATH,
             failure_path=DEFAULT_FAILURE_PATH,
             mapper=mapper,
             verbose=True)
    
