"""Feature H6: Pipeline auto-sync (inner sync) buffer-hazard scoreboard."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import random
from enum import IntEnum

import simpy

from profiling_sim.pipeline import Pipeline, Access, BufferSlot
from profiling_sim.shadow import ShadowPipeline

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def launch(env, pipe, access, buf, chunk, work, rec, start=None,
           manual_wait=False):
    def p():
        if start is not None and env.now < start:
            yield env.timeout(start - env.now)
        if manual_wait and not pipe.auto_consume:
            yield env.process(pipe.wait(buf, chunk, access))
        if access == Access.WRITE:
            slot = yield env.process(pipe.write(buf, chunk))
        else:
            slot = yield env.process(pipe.read(buf, chunk))
        acq = env.now
        yield env.timeout(work)
        rel = env.now
        slot.release()
        rec.append({"access": access, "register": slot.register_time,
                    "acquire": acq, "release": rel, "slot": slot})
    env.process(p())
    return rec


def phases(events, access, phase):
    return [e for e in events if e["access"] == access and e["phase"] == phase]


# =========================================================
print("=== Construction & validation ===")

# T-H6.1
env = simpy.Environment()
p = Pipeline(env)
check("T-H6.1 default auto_consume=True", p.auto_consume is True)
check("T-H6.1 events empty", p.events == [])
for bad in (-1, 1.5, "x", None, 1.0):
    try:
        list(p.write(0, bad)); raised = False
    except ValueError:
        raised = True
    check(f"T-H6.1 chunk {bad!r} raises ValueError", raised)
try:
    list(p.write(0, 0)); chunk0_ok = True
except ValueError:
    chunk0_ok = False
check("T-H6.1 chunk=0 accepted", chunk0_ok)


def run_write(pipe, buf=0, chunk=0):
    slot_rec = []
    def p():
        s = yield env.process(pipe.write(buf, chunk))
        slot_rec.append(s)
    env.process(p()); env.run()
    return slot_rec[0]


# T-H6.2 three-phase log for write, double-release no-op
env = simpy.Environment(); p = Pipeline(env)
slot = run_write(p)
check("T-H6.2 returns BufferSlot WRITE",
      isinstance(slot, BufferSlot) and slot.access == Access.WRITE)
check("T-H6.2 slot exposes buf_id/chunk/register_time",
      slot.buf_id == 0 and slot.chunk == 0 and slot.register_time == 0)
check("T-H6.2 register+acquire logged",
      len(phases(p.events, Access.WRITE, "register")) == 1
      and len(phases(p.events, Access.WRITE, "acquire")) == 1)
check("T-H6.2 no release before release()",
      len(phases(p.events, Access.WRITE, "release")) == 0)
slot.release()
check("T-H6.2 release logged once",
      len(phases(p.events, Access.WRITE, "release")) == 1)
slot.release()
check("T-H6.2 double-release is no-op (no second event)",
      len(phases(p.events, Access.WRITE, "release")) == 1)

# T-H6.3 read slot + aliases
env = simpy.Environment(); p = Pipeline(env)
rec = []
def rdr():
    s = yield env.process(p.read(7, 0)); rec.append(s)
env.process(rdr()); env.run()
rslot = rec[0]
check("T-H6.3 read returns Access.READ", rslot.access == Access.READ)
# cross-alias RAW: produce then consume blocks
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
def consume_alias():
    s = yield env.process(p.consume(0, 0))
    rec.append({"access": Access.READ, "acquire": env.now})
    yield env.timeout(1); s.release()
env.process(consume_alias()); env.run()
cons = [r for r in rec if isinstance(r, dict) and "slot" not in r]
check("T-H6.3 produce->consume triggers RAW (acquire=10)",
      any(r.get("acquire") == 10 for r in rec if isinstance(r, dict)),
      f"{rec}")
# produce->produce WAW
env = simpy.Environment(); p = Pipeline(env); w = []
launch(env, p, Access.WRITE, 1, 0, 8, w)
def produce2():
    s = yield env.process(p.produce(1, 0)); w.append({"acquire": env.now})
    yield env.timeout(2); s.release()
env.process(produce2()); env.run()
check("T-H6.3 produce->produce triggers WAW (second acquire=8)",
      any(r.get("acquire") == 8 for r in w if isinstance(r, dict)), f"{w}")

# =========================================================
print("\n=== Independence ===")

# T-H6.4 different chunks / buf_ids / instances do not interfere
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.READ, 0, 1, 5, rec)
env.run()
r0 = next(r for r in rec if r["slot"].chunk == 0)
r1 = next(r for r in rec if r["slot"].chunk == 1)
check("T-H6.4 different chunks independent (R acquires 0)",
      r1["acquire"] == 0, f"{r1['acquire']}")

env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.READ, 9, 0, 5, rec)
env.run()
check("T-H6.4 different buf_id independent",
      [r for r in rec if r["slot"].buf_id == 9][0]["acquire"] == 0)

env = simpy.Environment()
p1 = Pipeline(env); p2 = Pipeline(env); rec1 = []; rec2 = []
launch(env, p1, Access.WRITE, 0, 0, 10, rec1)
launch(env, p2, Access.READ, 0, 0, 5, rec2)
env.run()
check("T-H6.4 two Pipeline instances isolated",
      rec2[0]["acquire"] == 0)

# =========================================================
print("\n=== Hazards ===")

# T-H6.5 RAW
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.READ, 0, 0, 5, rec)
env.run()
w = next(r for r in rec if r["access"] == Access.WRITE)
r = next(r for r in rec if r["access"] == Access.READ)
check("T-H6.5 RAW read acquires at write release (10)",
      r["acquire"] == w["release"] == 10, f"{r['acquire']} vs {w['release']}")
wrel = phases(p.events, Access.WRITE, "release")[0]["time"]
racq = phases(p.events, Access.READ, "acquire")[0]["time"]
check("T-H6.5 event order WRITE.release <= READ.acquire", wrel <= racq)

# T-H6.6 WAW
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.WRITE, 0, 0, 10, rec)
env.run()
w0, w1 = [r for r in rec if r["access"] == Access.WRITE]
check("T-H6.6 WAW second acquires at first release",
      w1["acquire"] == w0["release"] == 10, f"{w1['acquire']} vs {w0['release']}")
check("T-H6.6 WAW makespan 20", max(r["release"] for r in rec) == 20)
check("T-H6.6 write intervals do not overlap",
      w0["release"] <= w1["acquire"])

# T-H6.7 WAR
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 10, rec)
launch(env, p, Access.WRITE, 0, 0, 5, rec)
env.run()
r = next(x for x in rec if x["access"] == Access.READ)
w = next(x for x in rec if x["access"] == Access.WRITE)
check("T-H6.7 WAR write acquires at read release (10)",
      w["acquire"] == r["release"] == 10, f"{w['acquire']} vs {r['release']}")

# T-H6.8 R-R parallel
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 10, rec)
launch(env, p, Access.READ, 0, 0, 10, rec)
env.run()
r0, r1 = [x for x in rec if x["access"] == Access.READ]
check("T-H6.8 R-R both acquire at 0",
      r0["acquire"] == r1["acquire"] == 0)
check("T-H6.8 R-R overlap (both release 10)",
      r0["release"] == r1["release"] == 10)
st = p._states[(0, 0)]
check("T-H6.8 read_count returns to 0 after both release",
      st.read_batch is None or st.read_batch[1] == 0)

# T-H6.9 multiple readers + writer waits for all
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 6, rec)
launch(env, p, Access.READ, 0, 0, 10, rec)
launch(env, p, Access.WRITE, 0, 0, 5, rec)
env.run()
rs = [x for x in rec if x["access"] == Access.READ]
w = next(x for x in rec if x["access"] == Access.WRITE)
check("T-H6.9 writer acquires at max reader release (10)",
      w["acquire"] == max(x["release"] for x in rs) == 10,
      f"{w['acquire']}")

# T-H6.10 chain W1 -> R -> W2
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 3, rec, start=0)
launch(env, p, Access.READ, 0, 0, 4, rec, start=0)
launch(env, p, Access.WRITE, 0, 0, 2, rec, start=0)
env.run()
w0 = next(x for x in rec if x["access"] == Access.WRITE and x["register"] == 0
          and x["acquire"] == 0)
rd = next(x for x in rec if x["access"] == Access.READ)
w1 = next(x for x in rec if x["access"] == Access.WRITE and x is not w0)
check("T-H6.10 chain ordering W(0-3) R(3-7) W(7-9)",
      w0["release"] == 3 and rd["acquire"] == 3 and rd["release"] == 7
      and w1["acquire"] == 7 and w1["release"] == 9,
      f"{rec}")

# T-H6.11 zero-cost ready path + work=0
env = simpy.Environment(); p = Pipeline(env)
def writer_then_reader():
    s = yield env.process(p.write(0, 0))
    yield env.timeout(10)
    s.release()
    yield env.timeout(10)
    # reader asks at t=20, buffer ready
    rs = yield env.process(p.read(0, 0))
    ready_acq.append(env.now)
    yield env.timeout(0)
    rs.release()
ready_acq = []
env.process(writer_then_reader()); env.run()
check("T-H6.11 ready path adds 0 cycles (acquire=20)",
      ready_acq == [20], f"{ready_acq}")
# work=0: acquire==release
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 0, rec)
env.run()
check("T-H6.11 work=0 acquire==release timestamp",
      rec[0]["acquire"] == rec[0]["release"] == 0)

# =========================================================
print("\n=== Manual mode ===")

# T-H6.12 manual mode no auto-stall
env = simpy.Environment(); p = Pipeline(env, auto_consume=False); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.READ, 0, 0, 5, rec)
env.run()
w = next(x for x in rec if x["access"] == Access.WRITE)
r = next(x for x in rec if x["access"] == Access.READ)
check("T-H6.12 manual W/R both register/acquire at 0 (no stall)",
      w["acquire"] == r["acquire"] == 0, f"{w['acquire']},{r['acquire']}")
check("T-H6.12 manual write/read logged no auto acquire event",
      len(phases(p.events, Access.WRITE, "acquire")) == 0
      and len(phases(p.events, Access.READ, "acquire")) == 0)

# T-H6.13 manual explicit RAW barrier
env = simpy.Environment(); p = Pipeline(env, auto_consume=False); rec = []
launch(env, p, Access.WRITE, 0, 0, 10, rec)
launch(env, p, Access.READ, 0, 0, 5, rec, manual_wait=True)
env.run()
w = next(x for x in rec if x["access"] == Access.WRITE)
r = next(x for x in rec if x["access"] == Access.READ)
check("T-H6.13 manual wait READ blocks until write release (10)",
      r["acquire"] == w["release"] == 10, f"{r['acquire']}")
check("T-H6.13 wait logged one acquire event",
      len(phases(p.events, Access.READ, "acquire")) == 1)

# T-H6.14 manual explicit WAW+WAR barrier
env = simpy.Environment(); p = Pipeline(env, auto_consume=False); rec = []
launch(env, p, Access.READ, 0, 0, 6, rec)
launch(env, p, Access.WRITE, 0, 0, 3, rec, manual_wait=True)
env.run()
r = next(x for x in rec if x["access"] == Access.READ)
w = next(x for x in rec if x["access"] == Access.WRITE)
check("T-H6.14 manual wait WRITE blocks for readers (6)",
      w["acquire"] == r["release"] == 6, f"{w['acquire']}")
# second manual writer WITHOUT wait runs concurrently (caller responsibility)
env = simpy.Environment(); p = Pipeline(env, auto_consume=False); rec = []
launch(env, p, Access.WRITE, 0, 0, 5, rec)
launch(env, p, Access.WRITE, 0, 0, 5, rec)
env.run()
check("T-H6.14 second manual writer without wait does not block",
      rec[1]["acquire"] == 0)

# =========================================================
print("\n=== Double buffering ===")

# T-H6.15 two chunks overlap
def dblbuf(same_chunk):
    env = simpy.Environment(); p = Pipeline(env); rec = []
    c0 = 0 if same_chunk else 0
    c1 = 0 if same_chunk else 1
    launch(env, p, Access.WRITE, 0, c0, 10, rec, start=0)
    launch(env, p, Access.READ, 0, c0, 10, rec, start=0)
    launch(env, p, Access.WRITE, 0, c1, 10, rec, start=0)
    launch(env, p, Access.READ, 0, c1, 10, rec, start=0)
    env.run()
    return max(x["release"] for x in rec)

single = dblbuf(True)
double = dblbuf(False)
check("T-H6.15 single-buffer serial makespan 40", single == 40, f"{single}")
check("T-H6.15 double-buffer makespan 20", double == 20, f"{double}")

# =========================================================
print("\n=== Events, late waiters, stress ===")

# T-H6.16 event log fidelity
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, 0, 0, 4, rec)
launch(env, p, Access.READ, 0, 0, 3, rec)
env.run()
reg = phases(p.events, Access.WRITE, "register")[0]
acq = phases(p.events, Access.READ, "acquire")[0]
rel = phases(p.events, Access.WRITE, "release")[0]
check("T-H6.16 register/acquire/release labels present",
      reg and acq and rel)
times = [e["time"] for e in p.events]
check("T-H6.16 timestamps non-decreasing",
      times == sorted(times), f"{times}")
check("T-H6.16 hazard invariant READ.acquire>=WRITE.release",
      acq["time"] >= rel["time"])

# T-H6.17 late waiter (release-before-wait)
env = simpy.Environment(); p = Pipeline(env)
late = []
def early():
    s = yield env.process(p.write(0, 0)); yield env.timeout(5); s.release()
def late_reader():
    yield env.timeout(20)
    s = yield env.process(p.read(0, 0))
    late.append(env.now)
    yield env.timeout(0); s.release()
env.process(early()); env.process(late_reader()); env.run()
check("T-H6.17 late reader acquires immediately at 20", late == [20], f"{late}")

# T-H6.18 N-concurrent-writer stress
random.seed(42)
env = simpy.Environment(); p = Pipeline(env); rec = []
total_work = 0
for i in range(32):
    w = random.randint(1, 5)
    total_work += w
    launch(env, p, Access.WRITE, 0, 0, w, rec, start=random.randint(0, 50))
env.run()
writes = sorted((x for x in rec if x["access"] == Access.WRITE),
                key=lambda x: x["acquire"])
no_overlap = all(writes[i]["release"] <= writes[i + 1]["acquire"]
                 for i in range(len(writes) - 1))
check("T-H6.18 32 writers serialize with no overlap", no_overlap)
check("T-H6.18 makespan == sum of work (no overlap/starvation)",
      writes[-1]["release"] - writes[0]["acquire"] == total_work,
      f"{writes[-1]['release']} vs {total_work}")

# =========================================================
print("\n=== Combined hazards ===")

# T-H6.19 WAW+WAR independent: R1(4), W1(1), R2(10 after W1), W2 at t=1
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 4, rec, start=0)
launch(env, p, Access.WRITE, 0, 0, 1, rec, start=0)
launch(env, p, Access.READ, 0, 0, 10, rec, start=0)
launch(env, p, Access.WRITE, 0, 0, 5, rec, start=1)
env.run()
by_acq = {x["access"]: x for x in rec}
reads = sorted([x for x in rec if x["access"] == Access.READ],
               key=lambda x: x["acquire"])
writes = sorted([x for x in rec if x["access"] == Access.WRITE],
                key=lambda x: x["acquire"])
w1, w2 = writes
r1, r2 = reads
check("T-H6.19 W1 waits for R1 (acq=4)", w1["acquire"] == 4 and w1["release"] == 5,
      f"{w1['acquire']},{w1['release']}")
check("T-H6.19 R2 waits for W1 (acq=5)", r2["acquire"] == 5 and r2["release"] == 15,
      f"{r2['acquire']},{r2['release']}")
check("T-H6.19 W2 waits for BOTH W1(5) and R2(15) -> acq=15",
      w2["acquire"] == 15 and w2["release"] == 20,
      f"{w2['acquire']},{w2['release']}")

# T-H6.20 pending writer + late reader
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.READ, 0, 0, 10, rec, start=0)
launch(env, p, Access.WRITE, 0, 0, 5, rec, start=0)
launch(env, p, Access.READ, 0, 0, 5, rec, start=2)
env.run()
r1 = next(x for x in rec if x["access"] == Access.READ and x["register"] == 0)
w = next(x for x in rec if x["access"] == Access.WRITE)
r2 = next(x for x in rec if x["access"] == Access.READ and x["register"] == 2)
check("T-H6.20 W waits for R1 (acq=10)", w["acquire"] == 10 and w["release"] == 15)
check("T-H6.20 late R2 waits for W (acq=15)",
      r2["acquire"] == w["release"] == 15, f"{r2['acquire']} vs {w['release']}")

# =========================================================
print("\n=== wait() behaviour ===")

# T-H6.21 valid/invalid; wait does not register
env = simpy.Environment(); p = Pipeline(env)
for bad in ("BOGUS", 123, None, "read"):
    try:
        list(p.wait(0, 0, bad)); ok = False
    except ValueError:
        ok = True
    check(f"T-H6.21 wait({bad!r}) raises ValueError", ok)


def run_wait(pipe, access):
    done = []
    def p():
        yield env.process(pipe.wait(0, 0, access))
        done.append(env.now)
    env.process(p()); env.run()
    return done[0]


env = simpy.Environment(); p = Pipeline(env)
check("T-H6.21 wait READ on fresh buffer returns at 0", run_wait(p, Access.READ) == 0)
check("T-H6.21 wait logged only an acquire (no register)",
      len(phases(p.events, Access.READ, "register")) == 0
      and len(phases(p.events, Access.READ, "acquire")) == 1)
st = p._states[(0, 0)]
check("T-H6.21 wait did not create a read batch", st.read_batch is None)

# =========================================================
print("\n=== Real work + H4 composition ===")

# T-H6.22 single vs double buffer; pipeline-before-shadow ordering
def pipeline_run(double):
    env = simpy.Environment(); p = Pipeline(env); rec = []
    D, C = 6, 4
    launch(env, p, Access.WRITE, 0, 0, D, rec, start=0)
    launch(env, p, Access.READ, 0, 0, C, rec, start=0)
    if double:
        launch(env, p, Access.WRITE, 0, 1, D, rec, start=0)
        launch(env, p, Access.READ, 0, 1, C, rec, start=0)
    env.run()
    return max(x["release"] for x in rec)

single_mk = pipeline_run(False)
double_mk = pipeline_run(True)
check("T-H6.22 single-buffer download->compute D+C=10", single_mk == 10,
      f"{single_mk}")
check("T-H6.22 double-buffer < 2*(D+C)=20 (overlap)",
      double_mk < 20, f"{double_mk}")

# hazard wait happens before shadow.enter -> blocked waiter holds no shadow slot
env = simpy.Environment()
pipe = Pipeline(env)
shadow = ShadowPipeline(env, "sh", [100], [2], occupancy=2, ii=1)
probe = []
def task_a():
    slot = yield env.process(pipe.write(0, 0))
    yield env.process(shadow.enter("A"))
    yield env.timeout(8)
    slot.release()
def task_b():
    # blocked on buffer hazard; must not yet hold a shadow slot
    slot = yield env.process(pipe.write(0, 0))
    yield env.process(shadow.enter("B"))
    yield env.timeout(2)
    slot.release()
def observer():
    yield env.timeout(1)
    probe.append(shadow.slots.level)
env.process(task_a()); env.process(task_b()); env.process(observer()); env.run()
check("T-H6.22 blocked hazard waiter holds no shadow slot (level=1 of 2)",
      probe == [1], f"{probe}")

# =========================================================
print("\n=== No DFG regression ===")

# T-H6.23 trivial DFG runs to completion without referencing Pipeline
from profiling_sim.config import ArchConfig, CoreConfig, SPMConfig, NoCConfig
from profiling_sim.definitions import OperatorType, DimSlice
from profiling_sim.dfg import DFG
from profiling_sim.architecture import Arch


class MockMapper:
    def __init__(self, dfg, n):
        self.dfg = dfg; self.n = n
        from profiling_sim.definitions import comp_operator
        self._comp = comp_operator

    def zero_degree(self):
        out = []
        for nid in range(1, self.n + 1):
            n = self.dfg.get_node(nid)
            if not n.father:
                n.ready = True; n.executed = True; out.append(n)
        return out

    def update(self, src_node, dst_node):
        from profiling_sim.definitions import Slice
        if src_node.operation in (OperatorType.LOAD_FEAT, OperatorType.RECV):
            dst_node.received_input += Slice(
                tensor_slice=src_node.input_size).size()
        elif src_node.operation in (OperatorType.STORE, OperatorType.SEND):
            dst_node.received_input += Slice(
                tensor_slice=src_node.output_size).size()
        if dst_node.operation in self._comp:
            if dst_node.received_input != Slice(
                    tensor_slice=dst_node.input_size).size():
                return
            dst_node.ready = True
        if dst_node.operation in (OperatorType.SEND, OperatorType.STORE):
            dst_node.ready = True

    def all_tasks_completed(self, core_id):
        return all(n.finished for n in self.dfg.nodes.values())


def ds(*d):
    return [DimSlice(start=0, end=x) for x in d]


dfg = DFG()
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(4, 4, 4, 4))
dfg.add_node(2, OperatorType.POOL, 0,
             input_size=ds(4, 4, 4, 4),
             output_size=ds(4, 4, 4, 4))
dfg.add_node(3, OperatorType.STORE, 0, output_size=ds(4, 4, 4, 4))
dfg.add_edge(1, 2); dfg.add_edge(2, 3)
cfg = ArchConfig(core=CoreConfig.model_construct(spm=SPMConfig(size=10 ** 9)),
                 noc=NoCConfig())
cfg.nodes.enable_dma = False; cfg.nodes.enable_adalink = False
arch = Arch(cfg, MockMapper(dfg, 3), deterministic=True)
arch.execute()
makespan = max(e.end_time or 0 for e in arch.cores[0].events)
check("T-H6.23 trivial DFG completes (makespan>0)", makespan > 0, f"{makespan}")

# =========================================================
print("\n=== buf_id typing ===")

# T-H6.24 IntEnum vs int equivalence


class Buf(IntEnum):
    A = 0
    B = 1


env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, Buf.A, 0, 10, rec)
launch(env, p, Access.READ, 0, 0, 3, rec)
env.run()
r = next(x for x in rec if x["access"] == Access.READ)
check("T-H6.24 IntEnum buf_id equals int key (RAW blocks at 10)",
      r["acquire"] == 10, f"{r['acquire']}")
env = simpy.Environment(); p = Pipeline(env); rec = []
launch(env, p, Access.WRITE, Buf.A, 0, 10, rec)
launch(env, p, Access.READ, Buf.B, 0, 3, rec)
env.run()
check("T-H6.24 distinct enum values independent",
      next(x for x in rec if x["access"] == Access.READ)["acquire"] == 0)

# =========================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
sys.exit(1 if failed else 0)
