import time
import logging
from typing import Tuple

from .architecture import Arch
from .tracing import process_events
from .config import load_arch, ArchConfig
from .definitions import Trace, Event


def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
    )


def simulate(arch_path: str,
             mapper,
             slice_num: int = 11,
             deterministic: bool = True,
             verbose: bool = False) -> Tuple[int, Trace, Arch]:
    """
    Run profiling simulation on a 2D Mesh NoC.

    Args:
        arch_path: Path to architecture JSON config.
        mapper: A NetworkMapper-like object with .dfg, .zero_degree(),
                .update(), .all_tasks_completed() already prepared
                (mapper.gen_dfg() must have been called).
        slice_num: Number of time slices for utilization tracing.
        deterministic: If True, link bandwidth is fixed (no random variance).
        verbose: Print progress.

    Returns:
        (maxtime, traces, arch)
    """
    if verbose:
        print("Start profiling simulation.")

    arch_config = load_arch(arch_path)

    if verbose:
        print(f"  Mesh: {arch_config.noc.x}x{arch_config.noc.y}, "
              f"{arch_config.noc.x * arch_config.noc.y} PEs")

    t0 = time.time()
    arch = Arch(arch_config, mapper, deterministic=deterministic)
    arch.execute()
    elapsed = time.time() - t0
    if verbose:
        print(f"  Simulation done in {elapsed:.2f}s")

    core_events = [arch.cores[i].events
                   for i in range(arch.x_size * arch.y_size)]
    link_events = [arch.noc.r2r_links[i].events
                   for i in range(len(arch.noc.r2r_links))]
    dma_events = [lk.events for lk in arch.noc.local_links]

    mem_events = []
    for mem in (getattr(arch, "gm", None), getattr(arch, "ddr", None)):
        if mem is None:
            continue
        mem_events.append([
            Event(src_id=mid, start_time=start, end_time=end,
                  data_size=n_bytes)
            for (mid, start, end, n_bytes) in mem.events
        ])

    maxtime = 0
    for evs in core_events + link_events + dma_events + mem_events:
        for ev in evs:
            if ev.end_time > maxtime:
                maxtime = ev.end_time

    if verbose:
        print(f"  Total simulated time: {maxtime}")

    traces = process_events(maxtime, slice_num, core_events, link_events,
                            dma_events=dma_events, mem_events=mem_events)
    return maxtime, traces, arch


def save_trace(traces: Trace, path: str = "trace.json"):
    with open(path, "w") as f:
        f.write(traces.model_dump_json(indent=4))
