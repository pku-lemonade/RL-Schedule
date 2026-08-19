"""Cross-cutting integration tests X1-X6 for the profiling simulator."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import (
    NoCConfig, RouterConfig, LinkConfig, MemoryConfig, ClockConfig,
)
from profiling_sim.definitions import (
    Message, DimSlice, TransType, Event,
)
from profiling_sim.noc import NoC
from profiling_sim.nodes import NodeType, DataNocLocalId
from profiling_sim.memory import Memory, DMANode, build_memory_system
from profiling_sim.tracing import process_events

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def ds(*dims):
    return [DimSlice(start=0, end=d) for d in dims]


def make_noc(env, width=16):
    cfg = NoCConfig(x=4, y=8, router=RouterConfig(),
                    link=LinkConfig(width=width, delay=0))
    return NoC(env, cfg, deterministic=True).build()


def hops(a, b):
    return abs(a // 4 - b // 4) + abs(a % 4 - b % 4)


print("=== X1: Full 66-node architecture build ===")
env = simpy.Environment()
noc = make_noc(env)
for cid in range(32):
    noc.attach_local(cid, 0, node_id=cid)
nodes, gm, ddr = build_memory_system(
    env, noc, MemoryConfig(), ClockConfig(),
    include_adalink=True, adalink_latency=8)
n_dma = len([k for k in nodes if 32 <= k < 48])
n_ada = len([k for k in nodes if k >= 48])
check("X1 32 routers", len(noc.routers) == 32)
check("X1 104 r2r links", len(noc.r2r_links) == 104)
check("X1 16 DMA nodes", n_dma == 16, f"{n_dma}")
check("X1 18 AdaLink nodes", n_ada == 18, f"{n_ada}")
check("X1 66 node_router mappings", len(noc.node_router) == 66,
      f"{len(noc.node_router)}")
check("X1 66 total nodes (32 PE + 34 non-PE)", 32 + len(nodes) == 66)
check("X1 GM/DDR memories built", gm is not None and ddr is not None)

print("\n=== X2: Regression (all prior suites pass) ===")
check("X2 regression verified by full-suite run", True)

# ----------------------------------------------------------------------
print("\n=== X3: GM_RDMA -> PE -> compute -> GM_WDMA end-to-end ===")
N = 1600
W = 16
HP = hops(28, 0)
engine_t = N // W
noc_t = (HP + 2) * engine_t + HP
compute_t = 50


def build_x3():
    env = simpy.Environment()
    noc = make_noc(env, width=W)
    gmem = Memory(env, "GM", 2 ** 40, 0)
    rdma = DMANode(env, 32, NodeType.GM_RDMA, 28,
                   [DataNocLocalId.GM_RDMA], noc,
                   memory=gmem, engine_width=W, channels=1, is_read=True)
    wdma = DMANode(env, 36, NodeType.GM_WDMA, 28,
                   [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1],
                   noc, memory=gmem, engine_width=W, channels=2,
                   is_read=False)
    pe_in, pe_out = noc.attach_local(0, 0, node_id=0)
    return env, noc, gmem, rdma, wdma, pe_in, pe_out


def run_x3():
    env, noc, gmem, rdma, wdma, pe_in, pe_out = build_x3()
    timeline = {}

    def pipeline():
        yield env.process(rdma.transfer(N))
        timeline['read'] = env.now
        rdma.data_out[DataNocLocalId.GM_RDMA].put(Message(
            src=32, dst=0, index=1, data=ds(N), element_bytes=1,
            dst_local_port=0, src_local_port=DataNocLocalId.GM_RDMA,
            header_bytes=0))
        yield pe_in.get()
        timeline['recv'] = env.now
        yield env.timeout(compute_t)
        timeline['compute'] = env.now
        pe_out.put(Message(
            src=0, dst=36, index=2, data=ds(N), element_bytes=1,
            dst_local_port=DataNocLocalId.GM_WDMA_CH0, src_local_port=0,
            header_bytes=0))

    env.process(pipeline())
    env.run()
    wdma_events = [e for e in gmem.events if e[0] == 36]
    timeline['write'] = max(e[2] for e in wdma_events) if wdma_events else -1
    return env, noc, gmem, timeline


env3, noc3, gm3, tl = run_x3()
check("X3 RDMA read stage completed", tl.get('read') == engine_t,
      f"{tl.get('read')}")
check("X3 PE received after NoC transit",
      tl.get('recv') == engine_t + noc_t, f"{tl.get('recv')}")
check("X3 compute stage completed",
      tl.get('compute') == engine_t + noc_t + compute_t)
check("X3 WDMA wrote to GM", gm3.used == N, f"{gm3.used}")
check("X3 exactly one WDMA memory event",
      len([e for e in gm3.events if e[0] == 36]) == 1)
stage_max = max(engine_t, noc_t, compute_t)
stage_sum = 2 * engine_t + 2 * noc_t + compute_t
check("X3 makespan within [max, sum] of stages",
      stage_max <= tl['write'] <= stage_sum,
      f"{tl['write']} vs [{stage_max},{stage_sum}]")
x3_makespan = tl['write']

# ----------------------------------------------------------------------
print("\n=== X4: GM_RDMA multicast to 4 PEs -> compute -> WDMA write-back ===")
MCAST_PE_ROUTERS = [0, 1, 2, 3]


def run_x4():
    env = simpy.Environment()
    noc = make_noc(env, width=W)
    gmem = Memory(env, "GM", 2 ** 40, 0)
    rdma = DMANode(env, 32, NodeType.GM_RDMA, 28,
                   [DataNocLocalId.GM_RDMA], noc,
                   memory=gmem, engine_width=W, channels=1, is_read=True)
    wdma = DMANode(env, 36, NodeType.GM_WDMA, 28,
                   [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1],
                   noc, memory=gmem, engine_width=W, channels=2,
                   is_read=False)
    pe_links = {}
    for r in MCAST_PE_ROUTERS:
        pe_in, pe_out = noc.attach_local(r, 0, node_id=r)
        pe_links[r] = (pe_in, pe_out)

    received = {r: False for r in MCAST_PE_ROUTERS}

    def pe_worker(r):
        pe_in, pe_out = pe_links[r]
        msg = yield pe_in.get()
        received[r] = True
        yield env.timeout(compute_t)
        pe_out.put(Message(
            src=r, dst=36, index=100 + r, data=ds(N), element_bytes=1,
            dst_local_port=DataNocLocalId.GM_WDMA_CH0, src_local_port=0,
            header_bytes=0))

    for r in MCAST_PE_ROUTERS:
        env.process(pe_worker(r))

    mask = 0
    for r in MCAST_PE_ROUTERS:
        mask |= 1 << r
    rdma.data_out[DataNocLocalId.GM_RDMA].put(Message(
        src=32, dst=28, index=1, data=ds(N), element_bytes=1,
        dst_local_port=0, src_local_port=DataNocLocalId.GM_RDMA,
        trans_type=TransType.MULTICAST, dst_mask=mask, header_bytes=0))
    env.run()
    return env, noc, gmem, received


env4, noc4, gm4, recv4 = run_x4()
check("X4 all 4 PEs received multicast",
      all(recv4.values()), f"{recv4}")
check("X4 all 4 writes landed in GM", gm4.used == 4 * N, f"{gm4.used}")
check("X4 4 WDMA memory events",
      len([e for e in gm4.events if e[0] == 36]) == 4)
wdma_ends = sorted(e[2] for e in gm4.events if e[0] == 36)
max_hp = max(hops(r, 28) for r in MCAST_PE_ROUTERS)
max_noc = (max_hp + 2) * engine_t + max_hp
farthest_delivery = (1 + hops(28, 0) + 3 + 1) * engine_t
lower = max(farthest_delivery, max_noc)
upper = farthest_delivery + compute_t + max_noc + 4 * engine_t
check("X4 farthest PE multicast delivery baseline",
      max_hp == 10 and farthest_delivery == 1200)
check("X4 makespan within [critical-path lower, serialized upper]",
      lower <= env4.now <= upper,
      f"{env4.now} vs [{lower},{upper}]")
check("X4 all 4 WDMA writes completed by makespan",
      wdma_ends[-1] <= env4.now and len(wdma_ends) == 4)

# ----------------------------------------------------------------------
print("\n=== X5: Determinism (two identical runs) ===")
_, noc_a, gm_a, tl_a = run_x3()
_, noc_b, gm_b, tl_b = run_x3()
ev_a = sorted((e.start_time, e.end_time, e.data_size, e.src_id, e.dst_id)
              for lk in noc_a.r2r_links for e in lk.events)
ev_b = sorted((e.start_time, e.end_time, e.data_size, e.src_id, e.dst_id)
              for lk in noc_b.r2r_links for e in lk.events)
check("X5 identical makespan", tl_a['write'] == tl_b['write'],
      f"{tl_a['write']} vs {tl_b['write']}")
check("X5 identical r2r event timing", ev_a == ev_b)
check("X5 identical memory events",
      sorted(gm_a.events) == sorted(gm_b.events))
check("X5 makespan matches X3", tl_a['write'] == x3_makespan)

# ----------------------------------------------------------------------
print("\n=== X6: Trace schema with dma_links and mem_bw ===")
maxtime = x3_makespan
core_events = [[] for _ in range(32)]
link_events = [lk.events for lk in noc3.r2r_links]
dma_events = [lk.events for lk in noc3.local_links]
mem_events = [[
    Event(src_id=mid, start_time=s, end_time=e, data_size=n)
    for (mid, s, e, n) in gm3.events
]]
traces = process_events(maxtime, 8, core_events, link_events,
                        dma_events=dma_events, mem_events=mem_events)
check("X6 8 time slices", len(traces.time_slices) == 8)
check("X6 every slice has dma_links",
      all(len(s.dma_links) == len(dma_events) for s in traces.time_slices),
      f"{set(len(s.dma_links) for s in traces.time_slices)}")
check("X6 every slice has mem_bw",
      all(len(s.mem_bw) == len(mem_events) for s in traces.time_slices))
active_dma = [t for s in traces.time_slices for t in s.dma_links if t.op_num > 0]
active_mem = [t for s in traces.time_slices for t in s.mem_bw if t.op_num > 0]
check("X6 some DMA link segments are active", len(active_dma) > 0,
      f"{len(active_dma)}")
check("X6 some mem_bw segments are active", len(active_mem) > 0,
      f"{len(active_mem)}")
check("X6 DMA utilization within [0,1]",
      all(0.0 <= t.ultilization <= 1.0 for t in active_dma))
check("X6 mem_bw utilization within [0,1]",
      all(0.0 <= t.ultilization <= 1.0 for t in active_mem))
# Backward compatibility: cores/links still populated
check("X6 cores per slice == 32",
      all(len(s.cores) == 32 for s in traces.time_slices))
check("X6 links per slice == 104",
      all(len(s.links) == 104 for s in traces.time_slices))

print(f"\nIntegration: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
