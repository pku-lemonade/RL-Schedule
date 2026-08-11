"""Feature 8: GM / DDR memory nodes."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import (
    NoCConfig, RouterConfig, LinkConfig, MemoryConfig, ClockConfig,
)
from profiling_sim.definitions import Message, DimSlice
from profiling_sim.noc import NoC
from profiling_sim.memory import Memory, DMANode
from profiling_sim.nodes import NodeType, DataNocLocalId

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
    cfg = NoCConfig(x=8, y=4, router=RouterConfig(),
                    link=LinkConfig(width=width, delay=0))
    return NoC(env, cfg, deterministic=True).build()


def make_rdma(env, noc, memory, width, aggregate=None, clock_scale=1.0,
              router=28, node_id=32):
    mem = memory
    if aggregate is not None:
        mem = Memory(env, "T", 2**40, aggregate)
    return DMANode(env, node_id, NodeType.GM_RDMA, router,
                   [DataNocLocalId.GM_RDMA], noc, memory=mem,
                   engine_width=width * clock_scale, channels=1,
                   is_read=True), mem


print("=== Feature 8: GM/DDR memory nodes ===")

# T8.1 capacities
mcfg = MemoryConfig()
check("T8.1 GM 32 MiB", mcfg.gm_capacity == 32 * 1024 * 1024)
check("T8.1 DDR 128 GiB", mcfg.ddr_capacity == 128 * 1024 * 1024 * 1024)

# T8.2 RDMA read consumes bandwidth, records event
env = simpy.Environment(); noc = make_noc(env)
mem = Memory(env, "GM", 2**40, aggregate_bw=0)
rdma, _ = make_rdma(env, noc, mem, width=10)
def do_read():
    yield env.process(rdma.transfer(100))
env.process(do_read()); env.run()
check("T8.2 100B @10B/cyc = 10 cycles",
      mem.events[-1][2] - mem.events[-1][1] == 10,
      f"{mem.events[-1]}")
check("T8.2 read does not allocate", mem.used == 0)

# T8.3 WDMA write advances used pointer
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, aggregate_bw=0)
wdma = DMANode(env, 36, NodeType.GM_WDMA, 28,
               [10, 11], noc, memory=gmem, engine_width=10,
               channels=2, is_read=False)
def do_write():
    yield env.process(wdma.transfer(200))
env.process(do_write()); env.run()
check("T8.3 write used += 200", gmem.used == 200, f"{gmem.used}")

# T8.4 DDR clock scaling: DDR faster by 1150/1125
N = 1_000_000
env_g = simpy.Environment(); noc_g = make_noc(env_g)
gm = Memory(env_g, "GM", 2**40, 0)
rdma_g = DMANode(env_g, 32, NodeType.GM_RDMA, 28, [14], noc_g,
                 memory=gm, engine_width=16, channels=1, is_read=True)
env_d = simpy.Environment(); noc_d = make_noc(env_d)
dmem = Memory(env_d, "DDR", 2**40, 0)
scale = 1150 / 1125
rdma_d = DMANode(env_d, 40, NodeType.DDR_RDMA, 0, [7], noc_d,
                 memory=dmem, engine_width=16 * scale, channels=1, is_read=True)
tg = {}; td = {}
def run_g():
    s = env_g.now; yield env_g.process(rdma_g.transfer(N)); tg['t'] = env_g.now - s
def run_d():
    s = env_d.now; yield env_d.process(rdma_d.transfer(N)); td['t'] = env_d.now - s
env_g.process(run_g()); env_g.run()
env_d.process(run_d()); env_d.run()
expected = N / 16 * 1125 / 1150
check("T8.4 DDR faster than GM", td['t'] < tg['t'], f"gm={tg['t']} ddr={td['t']}")
check("T8.4 DDR time ≈ gm * 1125/1150",
      abs(td['t'] - expected) <= 2, f"ddr={td['t']} expected~{expected:.1f}")

# T8.5 four concurrent reads overlap (not 4x serial)
def concurrent_reads(aggregate, n_nodes=4, N=100, width=10):
    env = simpy.Environment(); noc = make_noc(env)
    mem = Memory(env, "GM", 2**40, aggregate)
    nodes = []
    for i in range(n_nodes):
        nodes.append(DMANode(env, 32 + i, NodeType.GM_RDMA, 28 + i,
                             [14], noc, memory=mem, engine_width=width,
                             channels=1, is_read=True))
    finish = []
    def task(nd):
        yield env.process(nd.transfer(N)); finish.append(env.now)
    for nd in nodes:
        env.process(task(nd))
    env.run()
    return max(finish)

t_par = concurrent_reads(aggregate=40)
t_ser = concurrent_reads(aggregate=10)
check("T8.5 4 reads with agg=40 finish at ~10 (parallel)",
      t_par == 10, f"{t_par}")
check("T8.5 4 reads with agg=10 finish at ~40 (serialized)",
      t_ser == 40, f"{t_ser}")
check("T8.5 not 4x serial when bandwidth suffices", t_par < 40)

# T8.6 WDMA two channels parallel
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, 0)
wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10, 11], noc,
               memory=gmem, engine_width=10, channels=2, is_read=False)
finish = []
def w(N):
    yield env.process(wdma.transfer(N)); finish.append(env.now)
env.process(w(100)); env.process(w(100)); env.run()
check("T8.6 two WDMA channels finish at 10",
      finish == [10, 10], f"{finish}")

# T8.7 read+write overlap vs aggregate contention
def rw(aggregate):
    env = simpy.Environment(); noc = make_noc(env)
    mem = Memory(env, "GM", 2**40, aggregate)
    rd = DMANode(env, 32, NodeType.GM_RDMA, 28, [14], noc,
                 memory=mem, engine_width=10, channels=1, is_read=True)
    wr = DMANode(env, 36, NodeType.GM_WDMA, 29, [10, 11], noc,
                 memory=mem, engine_width=10, channels=2, is_read=False)
    finish = []
    def rd_task():
        yield env.process(rd.transfer(100)); finish.append(env.now)
    def wr_task():
        yield env.process(wr.transfer(100)); finish.append(env.now)
    env.process(rd_task()); env.process(wr_task()); env.run()
    return max(finish)

check("T8.7 large aggregate: r/w overlap at 10", rw(10000) == 10)
check("T8.7 small aggregate: contention at 20", rw(10) == 20,
      f"{rw(10)}")

# T8.8 write beyond capacity raises
env = simpy.Environment(); noc = make_noc(env)
small = Memory(env, "GM", 50, 0)
wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10, 11], noc,
               memory=small, engine_width=10, channels=1, is_read=False)
raised = False
def overflow():
    global raised
    try:
        yield env.process(wdma.transfer(100))
    except MemoryError:
        raised = True
        raise
env.process(overflow())
try:
    env.run()
except MemoryError:
    pass
check("T8.8 overflow raises MemoryError", raised)

# T8.9 PE<->GM round trip via NoC (message-driven)
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, 0)
rdma = DMANode(env, 32, NodeType.GM_RDMA, 28, [14], noc,
               memory=gmem, engine_width=16, channels=1, is_read=True)
wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10, 11], noc,
               memory=gmem, engine_width=16, channels=2, is_read=False)
pe_in, pe_out = noc.attach_local(0, 0, node_id=0)
pe_out.put(Message(src=0, dst=36, index=1, data=ds(4, 4), dst_local_port=10))
env.run()
check("T8.9 WDMA received write", len(wdma.received) == 1)
check("T8.9 memory used after write", gmem.used > 0, f"{gmem.used}")
check("T8.9 simulation terminated cleanly", env.now > 0)

# T8.10 aggregate saturation: 4 engines x 10B = 40B/cyc aggregate
t_sat = concurrent_reads(aggregate=40, n_nodes=4, N=100, width=10)
check("T8.10 4 concurrent reads saturate aggregate at 10 cyc",
      t_sat == 10, f"{t_sat}")
t_sat2 = concurrent_reads(aggregate=20, n_nodes=4, N=100, width=10)
check("T8.10 half aggregate -> 20 cyc", t_sat2 == 20, f"{t_sat2}")

# T8.11 AIU download SRAM
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, 0)
rdma = DMANode(env, 32, NodeType.GM_RDMA, 28, [14], noc,
               memory=gmem, engine_width=16, channels=1, is_read=True,
               aiu_sram_size=256 * 1024)
check("T8.11 AIU port attached", rdma.aiu_port == DataNocLocalId.GM_RDMA_LOCAL_SRAM)
scalar_in, _ = noc.attach_local(8, 0, node_id=8)
def scalar_sink():
    while True:
        yield scalar_in.get()
env.process(scalar_sink())
aiu_link = rdma.data_in[rdma.aiu_port]
def aiu_send():
    aiu_link.put(Message(src=0, dst=32, index=2, data=ds(4, 4),
                         dst_local_port=rdma.aiu_port))
    yield env.timeout(100)
env.process(aiu_send()); env.run(until=100)
check("T8.11 AIU message received", len(rdma.aiu_events) == 1)
check("T8.11 AIU does not consume main memory events",
      len(gmem.events) == 0)
check("T8.11 AIU used 16B", rdma.aiu_used == 16, f"{rdma.aiu_used}")

bad = False
env2 = simpy.Environment(); noc2 = make_noc(env2)
g2 = Memory(env2, "GM", 2**40, 0)
rd2 = DMANode(env2, 32, NodeType.GM_RDMA, 28, [14], noc2,
              memory=g2, engine_width=16, channels=1, is_read=True)
def bad_aiu():
    rd2.data_in[rd2.aiu_port].put(
        Message(src=0, dst=32, index=3, data=ds(3),
                dst_local_port=rd2.aiu_port))
    yield env2.timeout(50)
env2.process(bad_aiu())
try:
    env2.run(until=50)
except ValueError:
    bad = True
check("T8.11 non-16B-aligned AIU raises", bad)

# T8.12 writeSum overwrite/add/max
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, 0)
wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10], noc,
               memory=gmem, engine_width=16, channels=1, is_read=False)
gmem.write(100, 5, write_sum=0)
gmem.write(100, 3, write_sum=1)
check("T8.12 add: 5+3=8", gmem.read(100) == 8, f"{gmem.read(100)}")
gmem.write(100, 10, write_sum=2)
check("T8.12 max(8,10)=10", gmem.read(100) == 10, f"{gmem.read(100)}")
gmem.write(100, 7, write_sum=2)
check("T8.12 max stays 10", gmem.read(100) == 10)
gmem.write(200, 4, write_sum=2)
gmem.write(200, 9, write_sum=2)
check("T8.12 max(4,9)=9", gmem.read(200) == 9)
gmem.write(300, 1, write_sum=0)
gmem.write(300, 2, write_sum=0)
check("T8.12 overwrite last wins", gmem.read(300) == 2)

# T8.13 AIU completion recorded
check("T8.13 AIU completion event recorded", len(rdma.aiu_events) == 1)

print(f"\nFeature 8: {passed} passed, {failed} failed")
import sys as _s
_s.exit(1 if failed else 0)
