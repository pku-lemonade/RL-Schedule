import json
import time
import simpy
import logging
import argparse
from pathlib import Path
from typing import List, Tuple
from pydantic import ValidationError

if __name__ == '__main__' and __package__ is None:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    __package__ = 'simulator_detailed'

from .architecture import Arch, NoC
from .tracing import process_events
from .utils.mapper import NetworkMapper, parse_mapping
from .utils.definitions import Trace
from .configs.schemas.arch_config import ArchConfig
from .configs.schemas.failure_configs import FailSlow
try:
    from .predictor.predict import detect
    from .embedding.hw_encoder import build_hardware_graph, HardwareEmbedding
    _HAS_DETECTOR = True
except ImportError:
    detect = None
    build_hardware_graph = None
    HardwareEmbedding = None
    _HAS_DETECTOR = False
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


def simulate_old() -> Tuple[int, Trace, NoC]:
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
    result = arch.execute()
    end_time = time.time()

    simulation_time = end_time - start_time
    print(f"Simulation time is {simulation_time:.2f} s.")

    # === Step 5: Generate trace data ===
    print("Step 5: Generate trace data...")

    maxtime = 0
    
    core_events = [arch.cores[idx].events for idx in range(arch.x_size * arch.y_size)]
    link_events = [arch.noc.r2r_links[idx].events for idx in range(len(arch.noc.r2r_links))]

    core_events_json = []
    link_events_json = []

    for single_core_events in core_events:
        for event in single_core_events:
            maxtime = max(maxtime, event.end_time)
            core_events_json.append(event.model_dump())

    for single_link_events in link_events:
        for event in single_link_events:
            maxtime = max(maxtime, event.end_time)
            link_events_json.append(event.model_dump())

    # with open("core.json", "w") as file:
    #     print(core_events_json, file=file)
    # with open("link.json", "w") as file:
    #     print(link_events_json, file=file)

    # === Step 6: Failslow detection ===
    print("Step 6: Failslow detection...")

    if _HAS_DETECTOR:
        assert detect is not None
        core_probs, link_probs = detect(env_time=maxtime,
                                        slice_num=args.slice,
                                        arch_config=arch_config,
                                        core_events_json=core_events_json,
                                        link_events_json=link_events_json)
    else:
        core_probs, link_probs = None, None

    # === Step 7: Generate execution statistics ===
    print("Step 7: Generate execution statistics...")

    traces = process_events(maxtime, args.slice, core_events, link_events)

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
    return maxtime , traces, arch.noc


def simulate(arch_path: str, failure_path: str, mapper: NetworkMapper, verbose: bool = False) -> Tuple[int, Trace, NoC]:
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
    result = arch.execute()
    end_time = time.time()
    execute_duration_ms = round((time.perf_counter() - execute_started) * 1000, 3)

    simulation_time = end_time - start_time
    if verbose: print(f"Simulation time is {simulation_time:.2f} s.")

    # === Step 5: Generate trace data ===
    if verbose: print("Step 5: Generate trace data...")
    event_build_started = time.perf_counter()

    maxtime = 0
    
    core_events = [arch.cores[idx].events for idx in range(arch.x_size * arch.y_size)]
    link_events = [arch.noc.r2r_links[idx].events for idx in range(len(arch.noc.r2r_links))]

    core_events_json = []
    link_events_json = []

    for single_core_events in core_events:
        for event in single_core_events:
            maxtime = max(maxtime, event.end_time)
            core_events_json.append(event.model_dump())

    for single_link_events in link_events:
        for event in single_link_events:
            maxtime = max(maxtime, event.end_time)
            link_events_json.append(event.model_dump())
    event_build_duration_ms = round((time.perf_counter() - event_build_started) * 1000, 3)

    # === Step 6: Failslow detection ===
    if verbose: print("Step 6: Failslow detection...")

    # print(f"maxtime: {maxtime}")
    # print(f"slice_num: {slice_num}")
    # print(f"arch_config: {arch_config}")
    # print(f"core_events_json: {core_events_json}")
    # print(f"link_events_json: {link_events_json}")

    detect_started = time.perf_counter()
    if _HAS_DETECTOR:
        assert detect is not None
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

    traces = process_events(maxtime, slice_num, core_events, link_events)

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
    return maxtime , traces, arch.noc


if __name__ == '__main__':
    network = parse_mapping("workloads/darknet19-4-4.json")
    mapper = NetworkMapper(network=network)
    mapper.gen_dfg()

    simulate(arch_path=DEFAULT_ARCH_PATH,
             failure_path=DEFAULT_FAILURE_PATH,
             mapper=mapper,
             verbose=True)
    
