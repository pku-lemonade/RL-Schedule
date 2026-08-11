"""Feature H4: Cycle-accurate shadow pipeline stages (spec section 7.5)."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import (
    ArchConfig, CoreConfig, SPMConfig, TPUConfig, NMCConfig,
    NoCConfig, NodeConfig, ShadowConfig,
)
from profiling_sim.definitions import OperatorType, DimSlice, Message
from profiling_sim.dfg import DFG
from profiling_sim.architecture import Arch
from profiling_sim.noc import NoC
from profiling_sim.shadow import (
    ShadowEntry, ShadowPipeline, sramc_entry, mdma_channel_entry,
)
from profiling_sim.memory import Memory, DMANode
from profiling_sim.nodes import AdaLinkNode, NodeType, DataNocLocalId

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def expect_raise(fn, label):
    try:
        fn()
    except (ValueError, RuntimeError):
        check(label, True)
        return
    check(label, False, "expected exception")


def ds(*dims):
    return [DimSlice(start=0, end=d) for d in dims]


# ---------- MockMapper (mirrors test_smoke) ----------
class MockMapper:
    def __init__(self, dfg: DFG, node_counter: int):
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
        if dst_node.operation in (OperatorType.SEND, OperatorType.STORE):
            dst_node.ready = True

    def all_tasks_completed(self, core_id):
        return all(n.finished for n in self.dfg.nodes.values())


CONV_INPUT = ds(1, 1, 4, 4)
CONV_WEIGHT = [DimSlice(start=0, end=1)] * 5
CONV_OUTPUT = ds(1, 2, 4, 4)
POOL_INPUT = ds(1, 1, 4, 4)
POOL_OUTPUT = ds(1, 4, 2, 2)


def make_arch_cfg(shadow: ShadowConfig, tpu_flops: int = 4):
    return ArchConfig(
        core=CoreConfig(spm=SPMConfig(size=10 ** 9),
                        tpu=TPUConfig(flops=tpu_flops),
                        nmc=NMCConfig(channels=2)),
        noc=NoCConfig(),
        nodes=NodeConfig(enable_dma=False, enable_adalink=False),
        shadow=shadow,
    )


def run_dfgs(dfg, n_nodes, shadow, tpu_flops=4):
    cfg = make_arch_cfg(shadow, tpu_flops=tpu_flops)
    arch = Arch(cfg, MockMapper(dfg, n_nodes), deterministic=True)
    arch.execute()
    return arch


def make_noc(env, width=16):
    return NoC(env, NoCConfig(link_width=width) if False else NoCConfig(),
               deterministic=True).build()


# =========================================================
print("=== Primitive ===")

# T-H4.1 exact entry ids
check("T-H4.1 matrix READ_F=4", ShadowEntry.ST_PE_MATRIX_READ_F == 4)
check("T-H4.1 matrix CAL=6", ShadowEntry.ST_PE_MATRIX_CAL == 6)
check("T-H4.1 matrix WRITE=7", ShadowEntry.ST_PE_MATRIX_WRITE == 7)
check("T-H4.1 vector 8-10",
      [ShadowEntry.ST_PE_VECTOR_READ, ShadowEntry.ST_PE_VECTOR_CAL,
       ShadowEntry.ST_PE_VECTOR_WRITE] == [8, 9, 10])
check("T-H4.1 SRAMC 12/16/20/24",
      [sramc_entry(0, False), sramc_entry(0, True),
       sramc_entry(1, False), sramc_entry(1, True)] == [12, 16, 20, 24])
check("T-H4.1 MDMA 32/36/40/44",
      [ShadowEntry.ST_MDMA_START, mdma_channel_entry(0),
       mdma_channel_entry(1), ShadowEntry.ST_MDMA_AIU_DOWNLOAD] == [32, 36, 40, 44])
check("T-H4.1 ACI 64/68/92",
      [ShadowEntry.ST_ACI_START, ShadowEntry.ST_ACI_FUNC,
       ShadowEntry.ST_ACI_AIU_DOWNLOAD] == [64, 68, 92])

# T-H4.2 single op latency 9
env = simpy.Environment()
p = ShadowPipeline(env, "p", [100, 101, 102], [2, 3, 4], occupancy=4, ii=1)
exits = []
def driver():
    yield env.process(p.enter())
    exits.append(env.now)
env.process(driver()); env.run()
check("T-H4.2 total latency 9", exits == [9], f"{exits}")
check("T-H4.2 three events", len(p.events) == 3)
check("T-H4.2 entry ids ordered",
      [e["entry_id"] for e in p.events] == [100, 101, 102])
check("T-H4.2 stage spans", [e["exit"] - e["enter"] for e in p.events] == [2, 3, 4])
check("T-H4.2 slot freed", p.slots.level == 4)

# T-H4.3 pipelined throughput L=9 D=4 ii=1
env = simpy.Environment()
p = ShadowPipeline(env, "p", [1, 2, 3, 4], [2, 3, 2, 2], occupancy=4, ii=1)
exits = []
def driver(i):
    yield env.process(p.enter(tag=i))
    exits.append((i, env.now))
for i in range(4):
    env.process(driver(i))
env.run()
exits.sort()
check("T-H4.3 exits 9,10,11,12",
      [t for _, t in exits] == [9, 10, 11, 12], f"{exits}")
check("T-H4.3 peak occupancy 4",
      max(infl for _, infl in p.occupancy_log) == 4)

# T-H4.4 ii=2
env = simpy.Environment()
p = ShadowPipeline(env, "p", [1, 2, 3, 4], [2, 3, 2, 2], occupancy=4, ii=2)
exits = []
def driver(i):
    yield env.process(p.enter(tag=i)); exits.append(env.now)
for i in range(4):
    env.process(driver(i))
env.run()
exits.sort()
check("T-H4.4 ii=2 exits 9,11,13,15", exits == [9, 11, 13, 15], f"{exits}")

# T-H4.5 back-pressure D=2
env = simpy.Environment()
p = ShadowPipeline(env, "p", [1, 2, 3], [3, 3, 3], occupancy=2, ii=1)
exits = []
def driver(i):
    yield env.process(p.enter(tag=i)); exits.append(env.now)
for i in range(3):
    env.process(driver(i))
env.run()
exits.sort()
check("T-H4.5 third op stalls to 18", exits == [9, 10, 18], f"{exits}")
check("T-H4.5 occupancy never exceeds 2",
      all(infl <= 2 for _, infl in p.occupancy_log))

# T-H4.6 stage overrides keyed by entry id
env = simpy.Environment()
p = ShadowPipeline(env, "p", [4, 5, 6, 7], [0, 0, 0, 0], occupancy=2, ii=1)
ov = {int(ShadowEntry.ST_PE_MATRIX_CAL): 5}
ev_a = []
def a():
    yield env.process(p.enter(tag="a", stage_overrides=ov)); ev_a.append(env.now)
def b():
    yield env.timeout(0)
    yield env.process(p.enter(tag="b"))
t_b = []
def runner_b():
    yield env.process(b()); t_b.append(env.now)
env.process(a()); env.process(runner_b()); env.run()
check("T-H4.6 overridden op total 5", ev_a == [5], f"{ev_a}")
check("T-H4.6 non-overridden op exits at ii=1", t_b == [1], f"{t_b}")
b_cal = [e for e in p.events if e["tag"] == "b" and e["entry_id"] == 6][0]
check("T-H4.6 non-overridden CAL span 0", b_cal["exit"] - b_cal["enter"] == 0)
cal = [e for e in p.events if e["tag"] == "a" and e["entry_id"] == 6][0]
check("T-H4.6 CAL event spans 5", cal["exit"] - cal["enter"] == 5)
expect_raise(lambda: list(p.enter(stage_overrides={999: 1})),
             "T-H4.6 unknown override key raises")
# Need to actually run the generator to trigger:
env2 = simpy.Environment()
p2 = ShadowPipeline(env2, "p", [1], [0], occupancy=1, ii=1)
def boom():
    try:
        yield env2.process(p2.enter(stage_overrides={999: 1}))
    except ValueError:
        boom.raised = True
boom.raised = False
env2.process(boom()); env2.run()
check("T-H4.6 unknown key raises at runtime", boom.raised)

# T-H4.7 acquire/release wraps body and always releases slot
env = simpy.Environment()
p = ShadowPipeline(env, "p", [36], [3], occupancy=2, ii=1)
hold_during = []
def user(raise_after=None):
    slot = yield env.process(p.acquire())
    hold_during.append(p.occupancy - p.slots.level)
    try:
        yield env.timeout(5)
        if raise_after is not None:
            raise RuntimeError("boom")
    finally:
        yield env.process(p.release(slot))
env.process(user())
def failing():
    try:
        yield env.process(user(raise_after=1))
    except RuntimeError:
        failing.got = True
failing.got = False
env.process(failing()); env.run()
ev = p.events[0]
check("T-H4.7 slot held across body", hold_during[0] >= 1)
check("T-H4.7 event spans overhead+body", ev["exit"] - ev["enter"] == 8)
check("T-H4.7 slot released after body raise",
      p.slots.level == 2 and failing.got)

# T-H4.8 validation
expect_raise(lambda: ShadowConfig(occupancy=0), "T-H4.8 occupancy<1 raises")
expect_raise(lambda: ShadowConfig(ii=0), "T-H4.8 ii<1 raises")
expect_raise(lambda: ShadowConfig(matrix=[0, 0, 0]), "T-H4.8 matrix len 3 raises")
expect_raise(lambda: ShadowConfig(vector=[0, 0]), "T-H4.8 vector len 2 raises")
expect_raise(lambda: ShadowConfig(matrix=[0, -1, 0, 0]), "T-H4.8 neg stage raises")
expect_raise(lambda: ShadowConfig(mdma_channel=-1), "T-H4.8 neg latency raises")
# enabled + nmc.channels > 2 raises in Core
env = simpy.Environment()
bad = CoreConfig(spm=SPMConfig(size=1024), nmc=NMCConfig(channels=3))
expect_raise(
    lambda: __import__("profiling_sim.core", fromlist=["Core"]).Core(
        env, 0, bad, None, shadow_cfg=ShadowConfig(enabled=True)),
    "T-H4.8 enabled + channels>2 raises")

# T-H4.9 determinism
def feed_pipeline(seed):
    env = simpy.Environment()
    p = ShadowPipeline(env, "p", [4, 5, 6, 7], [1, 2, 3, 4],
                       occupancy=3, ii=2)
    def drv(i):
        yield env.timeout(i % 3)
        yield env.process(p.enter(tag=i))
    for i in range(6):
        env.process(drv(i))
    env.run()
    return [(e["entry_id"], e["enter"], e["exit"], e["tag"]) for e in p.events]
check("T-H4.9 deterministic events",
      feed_pipeline(1) == feed_pipeline(2))

# =========================================================
print("\n=== PE compute / SRAMC wiring ===")

# T-H4.10 disabled vs enabled (occ=1, zero stages) identical makespan
def single_conv_chain(shadow):
    dfg = DFG()
    dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=CONV_INPUT)
    dfg.add_node(2, OperatorType.LOAD_WGT, 0, weight_size=CONV_WEIGHT)
    dfg.add_node(3, OperatorType.CONV, 0, input_size=CONV_INPUT,
                 weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
    dfg.add_node(4, OperatorType.STORE, 0, output_size=CONV_OUTPUT)
    dfg.add_edge(1, 3); dfg.add_edge(2, 3); dfg.add_edge(3, 4)
    arch = run_dfgs(dfg, 4, shadow, tpu_flops=4)
    ev = [e for e in arch.cores[0].events if e.end_time]
    return max(e.end_time for e in ev), arch

ms_off, arch_off = single_conv_chain(ShadowConfig())
ms_on, arch_on = single_conv_chain(ShadowConfig(
    enabled=True, occupancy=1, ii=1, matrix=[0, 0, 0, 0]))
check("T-H4.10 identical makespan", ms_off == ms_on, f"{ms_off} vs {ms_on}")
check("T-H4.10 disabled has no shadow events", arch_off.cores[0].shadow_events == [])
check("T-H4.10 enabled records matrix events",
      len(arch_on.cores[0].shadow_events) == 4)

# T-H4.11 occ=1 equivalence + CAL override + 4 events per CONV
shadow = ShadowConfig(enabled=True, occupancy=1, ii=1, matrix=[0, 0, 0, 0])
dfg = DFG()
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=CONV_INPUT)
dfg.add_node(2, OperatorType.LOAD_WGT, 0, weight_size=CONV_WEIGHT)
dfg.add_node(3, OperatorType.CONV, 0, input_size=CONV_INPUT,
             weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
dfg.add_edge(1, 3); dfg.add_edge(2, 3)
arch = run_dfgs(dfg, 3, shadow, tpu_flops=4)
se = arch.cores[0].shadow_events
ids = sorted(e["entry_id"] for e in se)
check("T-H4.11 matrix ids 4-7", ids == [4, 5, 6, 7], f"{ids}")
cal = [e for e in se if e["entry_id"] == 6][0]
# flops = 1*1*1*1*4*4 = 16; flops_per_cycle=4 -> cal=4
check("T-H4.11 CAL spans 4", cal["exit"] - cal["enter"] == 4,
      f"{cal['exit']-cal['enter']}")

# T-H4.12 pipeline overlap: two independent CONVs
def two_independent_convs(shadow):
    dfg = DFG()
    dfg.add_node(1, OperatorType.CONV, 0, input_size=CONV_INPUT,
                 weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
    dfg.add_node(2, OperatorType.CONV, 0, input_size=CONV_INPUT,
                 weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
    arch = run_dfgs(dfg, 2, shadow, tpu_flops=4)
    return arch.cores[0]

c_occ2 = two_independent_convs(ShadowConfig(
    enabled=True, occupancy=2, ii=1, matrix=[2, 2, 0, 2]))
c_occ1 = two_independent_convs(ShadowConfig(
    enabled=True, occupancy=1, ii=1, matrix=[2, 2, 0, 2]))
# L = 2+2+cal(4)+2 = 10
ends2 = sorted(e["exit"] for e in c_occ2.matrix_pipeline.events
               if e["entry_id"] == 7)
ends1 = sorted(e["exit"] for e in c_occ1.matrix_pipeline.events
               if e["entry_id"] == 7)
check("T-H4.12 occ2 overlaps (10,11)", ends2 == [10, 11], f"{ends2}")
check("T-H4.12 occ1 serializes (10,20)", ends1 == [10, 20], f"{ends1}")

# T-H4.13 comp_slots: four independent CONVs launch concurrently when enabled
def four_convs(shadow):
    dfg = DFG()
    for i in range(1, 5):
        dfg.add_node(i, OperatorType.CONV, 0, input_size=CONV_INPUT,
                     weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
    arch = run_dfgs(dfg, 4, shadow, tpu_flops=4)
    return arch.cores[0]

c4_on = four_convs(ShadowConfig(enabled=True, occupancy=4, ii=1))
c4_off = four_convs(ShadowConfig())
starts_on = sorted(e["enter"] for e in c4_on.matrix_pipeline.events
                   if e["entry_id"] == 4)
starts_off = sorted(e.start_time for e in c4_off.events
                    if e.type == OperatorType.CONV)
check("T-H4.13 enabled launches 4 concurrently",
      max(starts_on) - min(starts_on) <= 3, f"{starts_on}")
check("T-H4.13 disabled launches 1 at a time",
      max(starts_off) - min(starts_off) > 3, f"{starts_off}")

# T-H4.14 POOL uses vector, concurrent CONV+POOL overlap
dfg = DFG()
dfg.add_node(1, OperatorType.CONV, 0, input_size=CONV_INPUT,
             weight_size=CONV_WEIGHT, output_size=CONV_OUTPUT)
dfg.add_node(2, OperatorType.POOL, 0, input_size=POOL_INPUT,
             output_size=POOL_OUTPUT)
arch = run_dfgs(dfg, 2, ShadowConfig(
    enabled=True, occupancy=2, ii=1,
    matrix=[2, 2, 0, 2], vector=[2, 0, 2]), tpu_flops=4)
core = arch.cores[0]
vec_ids = sorted(e["entry_id"] for e in core.vector_pipeline.events)
mat_ids = sorted(e["entry_id"] for e in core.matrix_pipeline.events)
check("T-H4.14 POOL uses vector 8-10", vec_ids == [8, 9, 10], f"{vec_ids}")
check("T-H4.14 CONV uses matrix 4-7", mat_ids == [4, 5, 6, 7], f"{mat_ids}")
m_start = min(e["enter"] for e in core.matrix_pipeline.events)
v_start = min(e["enter"] for e in core.vector_pipeline.events)
check("T-H4.14 both pipelines launch at t=0 (overlap)",
      m_start == 0 and v_start == 0, f"m{m_start} v{v_start}")

# T-H4.15 SEND ordering + UPLD adds K; channel 1 entry check
def send_recv_dfgs():
    dfg = DFG()
    dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=CONV_INPUT)
    dfg.add_node(2, OperatorType.LOAD_WGT, 0, weight_size=CONV_WEIGHT)
    dfg.add_node(3, OperatorType.CONV, 0, input_size=CONV_INPUT,
                 weight_size=CONV_WEIGHT, output_size=CONV_INPUT)
    dfg.add_node(4, OperatorType.SEND, 0, output_size=CONV_INPUT)
    dfg.add_node(5, OperatorType.RECV, 1, input_size=CONV_INPUT)
    dfg.add_node(6, OperatorType.STORE, 1, output_size=CONV_INPUT)
    dfg.add_edge(1, 3); dfg.add_edge(2, 3); dfg.add_edge(3, 4)
    dfg.add_edge(4, 5); dfg.add_edge(5, 6)
    return dfg

def run_send(up, dn):
    dfg = send_recv_dfgs()
    arch = run_dfgs(dfg, 6, ShadowConfig(
        enabled=True, occupancy=2, ii=1,
        sramc_upld=up, sramc_dnld=dn, matrix=[1, 1, 4, 1]), tpu_flops=4)
    return arch

arch_k0 = run_send(0, 0)
arch_k2 = run_send(2, 2)
def makespan(a):
    return max(e.end_time for c in a.cores for e in c.events if e.end_time)
upld = [e for c in (arch_k2.cores[0],) for e in c.shadow_events
        if e["entry_id"] == 16]
dnld = [e for e in arch_k2.cores[1].shadow_events if e["entry_id"] == 12]
check("T-H4.15 UPLD entry 16 span 2",
      len(upld) == 1 and upld[0]["exit"] - upld[0]["enter"] == 2)
check("T-H4.15 DNLD entry 12 span 2",
      len(dnld) == 1 and dnld[0]["exit"] - dnld[0]["enter"] == 2)

def dur(arch, core, op):
    for e in arch.cores[core].events:
        if e.type == op:
            return e.end_time - e.start_time
    return None
# Both runs share matrix config; only sramc latencies differ.
send0, send2 = dur(arch_k0, 0, OperatorType.SEND), dur(arch_k2, 0, OperatorType.SEND)
recv0, recv2 = dur(arch_k0, 1, OperatorType.RECV), dur(arch_k2, 1, OperatorType.RECV)
check("T-H4.15 UPLD adds 2 to SEND duration",
      send2 - send0 == 2, f"{send2} vs {send0}")
check("T-H4.15 DNLD adds 2 to RECV duration",
      recv2 - recv0 == 2, f"{recv2} vs {recv0}")
check("T-H4.15 makespan delta = DNLD 2 on critical path",
      makespan(arch_k2) - makespan(arch_k0) == 2,
      f"{makespan(arch_k2)} vs {makespan(arch_k0)}")
# channel 1 entry 24 exists
env = simpy.Environment()
from profiling_sim.core import Core
cfg = CoreConfig(spm=SPMConfig(size=1024), nmc=NMCConfig(channels=2))
core = Core(env, 0, cfg, None, shadow_cfg=ShadowConfig(enabled=True))
check("T-H4.15 SRAMC channel1 UPLD entry 24",
      int(sramc_entry(1, True)) == 24)

# T-H4.16 RECV DNLD after SEND completes (data already sent)
recv_core = arch_k2.cores[1]
dn = [e for e in recv_core.shadow_events if e["entry_id"] == 12][0]
send_ev = [e for e in arch_k2.cores[0].events if e.type == OperatorType.SEND][0]
check("T-H4.16 DNLD enters after SEND completes",
      dn["enter"] >= send_ev.end_time,
      f"dnld {dn['enter']} vs send end {send_ev.end_time}")

# =========================================================
print("\n=== MDMA wiring ===")

# T-H4.17 disabled unchanged
env = simpy.Environment(); noc = make_noc(env)
mem = Memory(env, "GM", 2 ** 40, aggregate_bw=0)
rdma = DMANode(env, 32, NodeType.GM_RDMA, 28, [DataNocLocalId.GM_RDMA],
               noc, memory=mem, engine_width=10, channels=1, is_read=True)
def rd():
    yield env.process(rdma.transfer(100))
env.process(rd()); env.run()
check("T-H4.17 disabled 100B@10 = 10 cyc",
      mem.events[-1][2] - mem.events[-1][1] == 10)
check("T-H4.17 disabled no shadow events", rdma.shadow_events == [])

# T-H4.18 enabled MDMA channel pipeline adds K, entry by channel
def make_wdma(shadow, channels=2):
    env = simpy.Environment(); noc = make_noc(env)
    mem = Memory(env, "GM", 2 ** 40, aggregate_bw=0)
    wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10, 11], noc,
                   memory=mem, engine_width=10, channels=channels,
                   is_read=False, shadow_cfg=shadow)
    return env, wdma, mem

env, wdma, mem = make_wdma(ShadowConfig(enabled=True, occupancy=4,
                                        mdma_channel=3))
def wr():
    yield env.process(wdma.transfer(100))
env.process(wr()); env.run()
# engine_time = ceil(100/10)=10, plus pipeline overhead 3 => 13
check("T-H4.18 enabled 100B: 10 engine + 3 = 13",
      mem.events[-1][2] - mem.events[-1][1] == 13,
      f"{mem.events[-1]}")
ch_events = [e for e in wdma.shadow_events if e["entry_id"] in (36, 40)]
check("T-H4.18 channel event recorded", len(ch_events) == 1)
check("T-H4.18 entry is CHANNEL_0 (36)", ch_events[0]["entry_id"] == 36)
check("T-H4.18 event spans overhead+engine",
      ch_events[0]["exit"] - ch_events[0]["enter"] == 13)

# two concurrent transfers on 2-channel node overlap
env, wdma, mem = make_wdma(ShadowConfig(enabled=True, occupancy=4,
                                        mdma_channel=0), channels=2)
def wr2(i):
    yield env.process(wdma.transfer(100))
    wr2.done.append(env.now)
wr2.done = []
env.process(wr2(0)); env.process(wr2(1)); env.run()
wr2.done.sort()
check("T-H4.18 two-channel transfers fully overlap (10,10)",
      wr2.done == [10, 10], f"{wr2.done}")

# T-H4.19 AIU download traverses entry 44
env, wdma, mem = make_wdma(ShadowConfig(enabled=True, occupancy=2,
                                        mdma_aiu=5), channels=1)
from profiling_sim.nodes import NoCNode as _NoCNode
_NoCNode(env, 800, NodeType.PE, 8, [0], wdma.noc)
msg = Message(src=0, dst=wdma.id, index=0, data=ds(16), element_bytes=1,
              is_aiu=True)
wdma.aiu_in.put(msg)
env.run()
aiu_ev = [e for e in wdma.shadow_events if e["entry_id"] == 44]
check("T-H4.19 AIU shadow event 44", len(aiu_ev) == 1)
check("T-H4.19 AIU event spans 5 overhead",
      aiu_ev[0]["exit"] - aiu_ev[0]["enter"] == 5)
check("T-H4.19 AIU message accounted", len(wdma.aiu_events) == 1)

# =========================================================
print("\n=== ACI wiring ===")

# T-H4.20 disabled AdaLink latency unchanged
env = simpy.Environment(); noc = make_noc(env)
link = AdaLinkNode(env, 48, 28, [16], noc, latency=8)
def h():
    s = env.now
    msg = Message(src=0, dst=48, index=1, data=ds(16), element_bytes=1)
    yield env.process(link.handle(16, msg))
    h.t = env.now - s
env.process(h()); env.run()
check("T-H4.20 disabled latency 8", h.t == 8, f"{h.t}")
check("T-H4.20 disabled no shadow events", link.shadow_events == [])

# T-H4.21 enabled adds K, FUNC=68; is_aiu -> 92
env = simpy.Environment(); noc = make_noc(env)
link = AdaLinkNode(env, 48, 28, [16], noc, latency=8,
                   shadow_cfg=ShadowConfig(enabled=True, occupancy=2,
                                           aci_func=3, aci_aiu=7))
def hf():
    msg = Message(src=0, dst=48, index=1, data=ds(16), element_bytes=1)
    s = env.now
    yield env.process(link.handle(16, msg))
    hf.t = env.now - s
def ha():
    yield env.timeout(0)
    msg = Message(src=0, dst=48, index=2, data=ds(16), element_bytes=1,
                  is_aiu=True)
    s = env.now
    yield env.process(link.handle(16, msg))
    ha.t = env.now - s
env.process(hf()); env.process(ha()); env.run()
check("T-H4.21 FUNC adds 3 (8+3=11)", hf.t == 11, f"{hf.t}")
check("T-H4.21 AIU adds 7 (8+7=15)", ha.t == 15, f"{ha.t}")
func_ev = [e for e in link.shadow_events if e["entry_id"] == 68]
aiu_ev = [e for e in link.shadow_events if e["entry_id"] == 92]
check("T-H4.21 FUNC event 68", len(func_ev) == 1 and
      func_ev[0]["exit"] - func_ev[0]["enter"] == 11)
check("T-H4.21 AIU event 92", len(aiu_ev) == 1 and
      aiu_ev[0]["exit"] - aiu_ev[0]["enter"] == 15)

# =========================================================
print("\n=== Integration / regression ===")

# T-H4.22 end-to-end exact schedule
ms0 = makespan(run_send(0, 0))
ms2 = makespan(run_send(2, 2))
arch_e2e = run_send(2, 2)
all_ids = sorted(e["entry_id"] for c in arch_e2e.cores for e in c.shadow_events)
check("T-H4.22 matrix(4-7)+DNLD(12)+UPLD(16) events",
      all_ids == [4, 5, 6, 7, 12, 16], f"{all_ids}")
check("T-H4.22 exact makespan delta 2 (DNLD on critical path)",
      ms2 - ms0 == 2, f"{ms2} vs {ms0}")

# T-H4.23 full baseline regression is run separately by the runner; here just
# confirm disabled Arch builds and runs a 32-core smoke DFG.
dfg = DFG()
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(16, 4, 4, 4))
dfg.add_node(2, OperatorType.LOAD_WGT, 0,
             weight_size=[DimSlice(start=0, end=1), DimSlice(start=0, end=8),
                          DimSlice(start=0, end=4), DimSlice(start=0, end=3),
                          DimSlice(start=0, end=3)])
dfg.add_node(3, OperatorType.CONV, 0, input_size=ds(16, 4, 4, 4),
             weight_size=[DimSlice(start=0, end=1), DimSlice(start=0, end=8),
                          DimSlice(start=0, end=4), DimSlice(start=0, end=3),
                          DimSlice(start=0, end=3)],
             output_size=ds(16, 8, 2, 2))
dfg.add_node(4, OperatorType.STORE, 0, output_size=ds(16, 8, 2, 2))
dfg.add_edge(1, 3); dfg.add_edge(2, 3); dfg.add_edge(3, 4)
cfg = ArchConfig(core=CoreConfig.model_construct(spm=SPMConfig(size=10 ** 9)),
                 noc=NoCConfig())
cfg.nodes.enable_dma = False
cfg.nodes.enable_adalink = False
arch = Arch(cfg, MockMapper(dfg, 4), deterministic=True)
arch.execute()
check("T-H4.23 disabled baseline runs",
      any(e.end_time > 0 for e in arch.cores[0].events))

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
sys.exit(1 if failed else 0)
