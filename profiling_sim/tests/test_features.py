"""Tests for NMC dual-channel, element_bytes, clock domains, DMAEngine."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import (
    load_arch, ClockConfig, DMAEngineConfig, NMCConfig,
    SPMConfig, TPUConfig, LSUConfig, CoreConfig, NoCConfig, ArchConfig,
)
from profiling_sim.definitions import (
    OperatorType, Direction, DimSlice, Message, Slice
)
from profiling_sim.dfg import DFG
from profiling_sim.noc import NoC
from profiling_sim.architecture import Arch
from profiling_sim.dma import DMAEngine
from profiling_sim.core import NMC, Core

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        failed += 1


class MockMapper:
    def __init__(self, dfg, node_counter):
        self.dfg = dfg
        self.node_counter = node_counter
        from profiling_sim.definitions import comp_operator
        self._comp = comp_operator

    def zero_degree(self):
        nodes = []
        for nid in range(1, self.node_counter + 1):
            n = self.dfg.get_node(nid)
            if len(n.father) == 0:
                n.ready = True
                n.executed = True
                nodes.append(n)
        return nodes

    def update(self, src_node, dst_node):
        from profiling_sim.definitions import Slice
        match src_node.operation:
            case OperatorType.LOAD_FEAT | OperatorType.RECV:
                dst_node.received_input += Slice(
                    tensor_slice=src_node.input_size).size()
            case OperatorType.STORE | OperatorType.SEND:
                dst_node.received_input += Slice(
                    tensor_slice=src_node.output_size).size()
            case OperatorType.LOAD_WGT:
                dst_node.received_weight += src_node.weight_slice().size()
            case OperatorType.CONV | OperatorType.POOL | OperatorType.FC:
                dst_node.ready = True
        if dst_node.operation in self._comp:
            if dst_node.received_input != dst_node.input_slice().size():
                return
            if (dst_node.weight_slice().size()
                    and dst_node.received_weight != dst_node.weight_slice().size()):
                return
            dst_node.ready = True
        if dst_node.operation in (OperatorType.LOAD_FEAT, OperatorType.RECV):
            if dst_node.received_input != dst_node.input_slice().size():
                return
            dst_node.ready = True

    def all_tasks_completed(self, core_id):
        return all(n.finished for n in self.dfg.nodes.values())


def ds(*dims):
    return [DimSlice(start=0, end=d) for d in dims]


def make_cfg(nmc_channels=2, element_bytes=1, link_width=16):
    return ArchConfig(
        core=CoreConfig(
            spm=SPMConfig(size=2**30),
            tpu=TPUConfig(flops=1024),
            lsu=LSUConfig(),
            nmc=NMCConfig(channels=nmc_channels, start_up_time=1),
            element_bytes=element_bytes,
        ),
        noc=NoCConfig(
            x=8, y=4,
            link=type('L', (), {'width': link_width, 'delay': 0})(),
        ),
    )


# =========================================================
print("=== Test A: Clock config & DDR clock scaling ===")
clk = ClockConfig(aci_mhz=1125, ddr_mhz=1150)
check("ddr_scale = 1150/1125", abs(clk.ddr_scale - 1150/1125) < 1e-9,
      f"got {clk.ddr_scale}")
check("ddr_scale > 1 (DDR faster)", clk.ddr_scale > 1.0)

# DMA engine with ACI clock (scale=1.0) vs DDR clock (scale=1150/1125)
env = simpy.Environment()
dma_aci = DMAEngine(env, DMAEngineConfig(channels=2, width=16), clock_scale=1.0)
dma_ddr = DMAEngine(env, DMAEngineConfig(channels=2, width=16),
                    clock_scale=clk.ddr_scale)
check("ACI DMA width = 16.0", dma_aci.width == 16.0)
check("DDR DMA width scaled > 16", dma_ddr.width > 16.0,
      f"got {dma_ddr.width}")
check("DDR DMA width ≈ 16.356", abs(dma_ddr.width - 16*1150/1125) < 1e-6)

# Verify DDR DMA is faster for same transfer size
results = {}
def run_dma(dma, size, tag):
    yield env.process(dma.transfer(size))
    results[tag] = env.now

env2 = simpy.Environment()
dma_a = DMAEngine(env2, DMAEngineConfig(channels=1, width=10), clock_scale=1.0)
dma_d = DMAEngine(env2, DMAEngineConfig(channels=1, width=10), clock_scale=2.0)
def run_dma2(dma, size, tag):
    yield env2.process(dma.transfer(size))
    results[tag] = env2.now
p1 = env2.process(run_dma2(dma_a, 100, "aci"))
p2 = env2.process(run_dma2(dma_d, 100, "ddr"))
env2.run()
check("ACI transfer = 10 cycles (100B / 10 B/cyc)", results["aci"] == 10,
      f"got {results['aci']}")
check("DDR transfer = 5 cycles (2x clock scale)", results["ddr"] == 5,
      f"got {results['ddr']}")


# =========================================================
print("\n=== Test B: element_bytes (dtype) affects transfer latency ===")
# Same tensor shape, element_bytes=1 vs element_bytes=2 -> 2x latency
cfg1 = load_arch("profiling_sim/configs/mesh_8x4.json")

# Build two DFGs: LOAD->POOL->SEND on PE0, RECV->POOL->STORE on PE1
def build_comm_dfg(element_bytes):
    dfg = DFG()
    dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(1, 4, 4, 4))
    dfg.add_node(2, OperatorType.POOL, 0,
                 input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 4, 4))
    dfg.add_node(3, OperatorType.SEND, 0, output_size=ds(1, 4, 4, 4),
                 element_bytes=element_bytes)
    dfg.add_node(4, OperatorType.RECV, 1, input_size=ds(1, 4, 4, 4))
    dfg.add_node(5, OperatorType.POOL, 1,
                 input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 1, 1))
    dfg.add_node(6, OperatorType.STORE, 1, output_size=ds(1, 4, 1, 1))
    dfg.add_edge(1, 2); dfg.add_edge(2, 3); dfg.add_edge(3, 4)
    dfg.add_edge(4, 5); dfg.add_edge(5, 6)
    return dfg

# element_bytes=1
arch1 = Arch(cfg1, MockMapper(build_comm_dfg(1), 6), deterministic=True)
arch1.execute()
link_evts_1 = [e for l in arch1.noc.r2r_links for e in l.events if e.end_time > 0]
total_lat_1 = sum(e.end_time - e.start_time for e in link_evts_1)

# element_bytes=2 (same config, but DFG node sets it)
arch2 = Arch(cfg1, MockMapper(build_comm_dfg(2), 6), deterministic=True)
arch2.execute()
link_evts_2 = [e for l in arch2.noc.r2r_links for e in l.events if e.end_time > 0]
total_lat_2 = sum(e.end_time - e.start_time for e in link_evts_2)

check("element_bytes=2 doubles total link latency",
      total_lat_2 == 2 * total_lat_1,
      f"1B={total_lat_1}, 2B={total_lat_2}")
check("data_size in events reflects element_bytes",
      link_evts_2[0].data_size == 2 * link_evts_1[0].data_size,
      f"1B={link_evts_1[0].data_size}, 2B={link_evts_2[0].data_size}")

# Message.byte_size() helper
msg1 = Message(src=0, dst=1, index=0, data=ds(2, 3), element_bytes=1)
msg2 = Message(src=0, dst=1, index=0, data=ds(2, 3), element_bytes=4)
check("Message.byte_size() with elem=1", msg1.byte_size() == 6)
check("Message.byte_size() with elem=4 (INT32)", msg2.byte_size() == 24)


# =========================================================
print("\n=== Test C: NMC dual-channel allows concurrent upload+download ===")
# PE0 sends to PE1 AND PE1 sends to PE0 simultaneously.
# With 2 channels, SEND and RECV on the same PE can overlap.
dfg = DFG()
# PE0: LOAD -> POOL -> SEND(to PE1)
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(1, 4, 4, 4))
dfg.add_node(2, OperatorType.POOL, 0,
             input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 4, 4))
dfg.add_node(3, OperatorType.SEND, 0, output_size=ds(1, 4, 4, 4))
# PE0 also receives from PE1: RECV -> POOL -> STORE
dfg.add_node(7, OperatorType.RECV, 0, input_size=ds(1, 4, 4, 4))
dfg.add_node(8, OperatorType.POOL, 0,
             input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 1, 1))
dfg.add_node(9, OperatorType.STORE, 0, output_size=ds(1, 4, 1, 1))
# PE1: LOAD -> POOL -> SEND(to PE0)
dfg.add_node(4, OperatorType.LOAD_FEAT, 1, input_size=ds(1, 4, 4, 4))
dfg.add_node(5, OperatorType.POOL, 1,
             input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 4, 4))
dfg.add_node(6, OperatorType.SEND, 1, output_size=ds(1, 4, 4, 4))
# PE1 also receives from PE0
dfg.add_node(10, OperatorType.RECV, 1, input_size=ds(1, 4, 4, 4))
dfg.add_node(11, OperatorType.POOL, 1,
             input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 1, 1))
dfg.add_node(12, OperatorType.STORE, 1, output_size=ds(1, 4, 1, 1))
# Edges
dfg.add_edge(1, 2); dfg.add_edge(2, 3)
dfg.add_edge(4, 5); dfg.add_edge(5, 6)
dfg.add_edge(3, 10); dfg.add_edge(6, 7)
dfg.add_edge(7, 8); dfg.add_edge(8, 9)
dfg.add_edge(10, 11); dfg.add_edge(11, 12)

arch = Arch(cfg1, MockMapper(dfg, 12), deterministic=True)
arch.execute()

# Both PE0 and PE1 should each have 6 events (LOAD,POOL,SEND,RECV,POOL,STORE)
c0 = [e for e in arch.cores[0].events if e.end_time > 0]
c1 = [e for e in arch.cores[1].events if e.end_time > 0]
check("PE0 executed 6 events", len(c0) == 6, f"got {len(c0)}")
check("PE1 executed 6 events", len(c1) == 6, f"got {len(c1)}")

# Verify NMC has 2 channels
check("Core0 NMC has 2 channels",
      arch.cores[0].nmc.channel_store.capacity == 2)
check("Core0 NMC channel store initialized with 2 tokens",
      len(arch.cores[0].nmc.channel_store.items) == 2)

# Verify Scheduler allows 2 concurrent comm slots
check("Scheduler comm_slots = 2",
      arch.cores[0].scheduler.comm_slots == 2)


# =========================================================
print("\n=== Test D: NMC single-channel (1) still works (backward compat) ===")
cfg1ch = ArchConfig(
    core=CoreConfig(
        spm=SPMConfig(size=2**30),
        tpu=TPUConfig(flops=1024),
        lsu=LSUConfig(),
        nmc=NMCConfig(channels=1, start_up_time=1),
    ),
    noc=NoCConfig(x=8, y=4),
)
dfg_s = DFG()
dfg_s.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(1, 4, 4, 4))
dfg_s.add_node(2, OperatorType.POOL, 0,
               input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 2, 2))
dfg_s.add_node(3, OperatorType.STORE, 0, output_size=ds(1, 4, 2, 2))
dfg_s.add_edge(1, 2); dfg_s.add_edge(2, 3)
arch_s = Arch(cfg1ch, MockMapper(dfg_s, 3), deterministic=True)
arch_s.execute()
c0s = [e for e in arch_s.cores[0].events if e.end_time > 0]
check("1-channel NMC: core executed 3 events", len(c0s) == 3,
      f"got {len(c0s)}")
check("1-channel NMC: channel store capacity 1",
      arch_s.cores[0].nmc.channel_store.capacity == 1)


# =========================================================
print("\n=== Test E: DMAEngine dual-channel concurrency ===")
env3 = simpy.Environment()
dma = DMAEngine(env3, DMAEngineConfig(channels=2, width=10), clock_scale=1.0)
timings = {}
def dma_task(tag, size):
    start = env3.now
    yield env3.process(dma.transfer(size))
    timings[tag] = (start, env3.now)

# Launch 2 transfers of 100B simultaneously on 2 channels
env3.process(dma_task("A", 100))
env3.process(dma_task("B", 100))
env3.run()
# Each takes 10 cycles; with 2 channels they run in parallel -> both finish at 10
check("DMA A finished at 10 cycles", timings["A"][1] == 10,
      f"got {timings['A'][1]}")
check("DMA B finished at 10 cycles (parallel)", timings["B"][1] == 10,
      f"got {timings['B'][1]}")
check("DMA A and B overlap (both start at 0)",
      timings["A"][0] == 0 and timings["B"][0] == 0)

# 3 transfers on 2 channels -> 3rd waits, finishes at 20
env4 = simpy.Environment()
dma2 = DMAEngine(env4, DMAEngineConfig(channels=2, width=10), clock_scale=1.0)
finish_times = []
def dma_task2(size):
    yield env4.process(dma2.transfer(size))
    finish_times.append(env4.now)
for _ in range(3):
    env4.process(dma_task2(100))
env4.run()
finish_times.sort()
check("3 transfers on 2 channels: two finish at 10",
      finish_times.count(10) == 2, f"got {finish_times}")
check("3 transfers on 2 channels: one finishes at 20",
      finish_times.count(20) == 1, f"got {finish_times}")


# =========================================================
print("\n=== Test F: Calibrated config values ===")
cfg = load_arch("profiling_sim/configs/mesh_8x4.json")
check("calibrated clock ACI=1125", cfg.clock.aci_mhz == 1125)
check("calibrated clock DDR=1150", cfg.clock.ddr_mhz == 1150)
check("NMC has 2 channels", cfg.core.nmc.channels == 2)
check("NMC start_up_time=1", cfg.core.nmc.start_up_time == 1)
check("element_bytes=2 (FP16/BF16)", cfg.core.element_bytes == 2)
check("link width=16 bytes/cycle", cfg.noc.link.width == 16)
check("SPM size=19MiB (3+16)",
      cfg.core.spm.size == 3*1024*1024 + 16*1024*1024,
      f"got {cfg.core.spm.size}")
check("Mesh is 8x4", cfg.noc.x == 8 and cfg.noc.y == 4)
check("router is XY", cfg.noc.router.type == "XY")
# 16 B/cycle at 1125 MHz = 18 GB/s
bw_gbs = 16 * cfg.clock.aci_mhz * 1e6 / 1e9
check("16 B/cyc @ 1125MHz ≈ 18 GB/s", abs(bw_gbs - 18.0) < 0.1,
      f"got {bw_gbs:.2f} GB/s")


# =========================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
import sys
sys.exit(1 if failed else 0)
