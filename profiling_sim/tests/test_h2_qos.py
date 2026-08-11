"""Feature H2: Arbitration / Priority / Burst / FIFO flow-control tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransType, TransferMode, ProfilingSimError,
)
from profiling_sim.noc import NoC

passed = failed = 0
W = 16


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def ds(n):
    return [DimSlice(start=0, end=n)]


def make_noc(env, width=W, delay=0, burst_bubble=1):
    cfg = NoCConfig(x=8, y=4,
                    router=RouterConfig(burst_bubble=burst_bubble),
                    link=LinkConfig(width=width, delay=delay))
    return NoC(env, cfg, deterministic=True).build()


def find_r2r(noc, src, dst):
    for lk in noc.r2r_links:
        if lk.src_id == src and lk.dst_id == dst:
            return lk
    raise AssertionError(f"r2r link {src}->{dst} not found")


def attach(noc, router_id, port, node_id, consume=0, drain=True, arrivals=None):
    r2n, n2r = noc.attach_local(router_id, port, node_id=node_id)
    if drain:
        def sink():
            while True:
                if consume:
                    yield noc.env.timeout(consume)
                m = yield r2n.get()
                if arrivals is not None and not m.is_control:
                    arrivals.append((noc.env.now, m))
        noc.env.process(sink())
    return n2r, r2n


def sample_level(env, container, interval=1):
    samples = []

    def _s():
        while True:
            samples.append((env.now, container.level))
            yield env.timeout(interval)
    env.process(_s())
    return samples


def contention_run(prio0, prio1, n0=8 * W, n1=8 * W, burst0=-1, burst1=-1,
                   inject0=0, inject1=0):
    env = simpy.Environment()
    noc = make_noc(env)
    out0, _ = attach(noc, 4, 0, 400)
    out1, _ = attach(noc, 4, 1, 401)
    attach(noc, 8, 0, 800)

    def sched(out, idx, n, p, b, at, src):
        yield env.timeout(at)
        out.put(Message(src=src, dst=8, index=idx, data=ds(n),
                        element_bytes=1, dst_local_port=0,
                        priority=p, burst_len_mode=b))
    env.process(sched(out0, 1, n0, prio0, burst0, inject0, 400))
    env.process(sched(out1, 2, n1, prio1, burst1, inject1, 401))
    env.run()
    lk = find_r2r(noc, 4, 8)
    by_idx = {}
    for e in lk.events:
        if not e.is_control:
            by_idx.setdefault(e.index, []).append(e)
    return noc, by_idx, lk


def unicast_run(n, burst=-1, prio=0, mode=TransferMode.DUAL_SIDE, header=0,
                src_node=0, dst_node=4, sr=0, dr=4, dport=0, width=W,
                delay=0, bubble=1, fifo=None, sink_consume=0,
                run_until=None):
    env = simpy.Environment()
    noc = make_noc(env, width=width, delay=delay, burst_bubble=bubble)
    out, _ = attach(noc, sr, 0, src_node)
    arrivals = []
    attach(noc, dr, dport, dst_node, consume=sink_consume, arrivals=arrivals)
    if fifo:
        noc.register_fifo(*fifo)
    hw, logic = (fifo[0], fifo[1]) if fifo else (-1, -1)
    ctype = 0 if fifo else -1
    out.put(Message(src=src_node, dst=dst_node, index=1, data=ds(n),
                    element_bytes=1, src_local_port=0, dst_local_port=dport,
                    transfer_mode=mode, header_bytes=header,
                    priority=prio, burst_len_mode=burst,
                    fifo_hw_id=hw, fifo_logic_id=logic,
                    fifo_check_type=ctype))
    if run_until is not None:
        env.run(until=run_until)
    else:
        env.run()
    mk = arrivals[-1][0] if arrivals else -1
    return env, noc, mk, arrivals


def hops(a, b):
    return abs(a // 4 - b // 4) + abs(a % 4 - b % 4)


# =====================================================================
print("=== H2.1-H2.4: priority arbitration ===")

# T-H2.1 equal-priority FIFO
_, by, lk = contention_run(0, 0)
e1, e2 = by[1][0], by[2][0]
check("T-H2.1 equal-priority bursts contiguous",
      e2.start_time == e1.end_time,
      f"{e1.end_time} -> {e2.start_time}")
check("T-H2.1 total busy == 2c",
      (e1.end_time - e1.start_time) + (e2.end_time - e2.start_time) == 16)

# T-H2.2 high priority wins among queued requesters.
# A blocker on port2 holds r4->r8 during [9,17); low(prio0) and high(prio3)
# both request at t=10 and queue. When the blocker releases, the high-priority
# requester wins regardless of which port it sits on.
def priority_contest(high_on_port1):
    env = simpy.Environment()
    noc = make_noc(env)
    ob, _ = attach(noc, 4, 2, 402)
    ol, _ = attach(noc, 4, 0, 400)
    oh, _ = attach(noc, 4, 1, 401)
    attach(noc, 8, 0, 800)

    def sched(out, idx, n, p, at):
        yield env.timeout(at)
        out.put(Message(src=400 + idx, dst=8, index=idx, data=ds(n),
                        element_bytes=1, dst_local_port=0, priority=p))
    env.process(sched(ob, 9, 8 * W, 0, 0))   # blocker
    if high_on_port1:
        env.process(sched(ol, 1, W, 0, 8))    # low on port0
        env.process(sched(oh, 2, W, 3, 8))    # high on port1
    else:
        env.process(sched(ol, 1, W, 3, 8))    # high on port0
        env.process(sched(oh, 2, W, 0, 8))    # low on port1
    env.run()
    lk = find_r2r(noc, 4, 8)
    st = {}
    for e in lk.events:
        if not e.is_control and e.index in (1, 2):
            st[e.index] = (e.start_time, e.end_time, e.priority)
    return st


sa = priority_contest(high_on_port1=True)
sb = priority_contest(high_on_port1=False)
ok_a = sa[2][0] == 17 and sa[2][0] < sa[1][0] and sa[2][2] == 3
ok_b = sb[1][0] == 17 and sb[1][0] < sb[2][0] and sb[1][2] == 3
check("T-H2.2 high priority wins among queued requeters (both port orders)",
      ok_a and ok_b, f"a={sa} b={sb}")

# T-H2.3 non-preemptive
_, by3, _ = contention_run(0, 3, n0=8 * W, n1=W, burst0=-1, burst1=-1,
                           inject0=0, inject1=8)
low = by3[1][0]
high = by3[2][0]
check("T-H2.3 high waits for whole low burst (non-preemptive)",
      high.start_time == low.end_time == 17 and high.end_time == 18,
      f"low={low.start_time}-{low.end_time} high={high.start_time}-{high.end_time}")

# T-H2.4 priority interleaves with burst
_, by4, _ = contention_run(0, 3, n0=16 * W, n1=W, burst0=7, burst1=-1,
                           inject0=0, inject1=16)
low_bursts = sorted(by4[1], key=lambda e: e.burst_index)
high4 = by4[2][0]
check("T-H2.4a low split into two bursts",
      len(low_bursts) == 2 and low_bursts[0].start_time == 18
      and low_bursts[0].end_time == 26,
      f"{[(e.start_time, e.end_time) for e in low_bursts]}")
check("T-H2.4b high starts at end of low first burst (26..27)",
      high4.start_time == 26 and high4.end_time == 27,
      f"{high4.start_time}-{high4.end_time}")
check("T-H2.4c low second burst after high",
      low_bursts[1].start_time == 27 and low_bursts[1].end_time == 35,
      f"{low_bursts[1].start_time}-{low_bursts[1].end_time}")

# =====================================================================
print("=== H2.5-H2.12: burst segmentation ===")

# T-H2.5 default preserves baseline
c = 8
HP = hops(0, 4)
_, _, mk5, _ = unicast_run(8 * W, burst=-1, src_node=0, dst_node=4)
check("T-H2.5 default makespan == (HP+2)*c+HP",
      mk5 == (HP + 2) * c + HP == 25, f"{mk5}")

# T-H2.6 per-link event counts / sizes / burst metadata
expected = {0: (8, 16), 1: (4, 32), 3: (2, 64), 7: (1, 128), -1: (1, 128)}
for mode, (k, size) in expected.items():
    _, noc6, _, _ = unicast_run(8 * W, burst=mode)
    groups = {}
    for lk in noc6.r2r_links + noc6.local_links:
        for e in lk.events:
            if not e.is_control and e.data_size > 0:
                groups.setdefault((lk.src_id, lk.dst_id), []).append(e)
    n_links = len(groups)
    ok = n_links == 3
    detail = f"mode={mode} links={n_links}"
    for key, evs in groups.items():
        idxs = sorted(e.burst_index for e in evs)
        if (len(evs) != k or any(e.data_size != size for e in evs)
                or any(e.burst_count != k for e in evs)
                or idxs != list(range(k))):
            ok = False
            detail += f" {key}:{[(e.data_size, e.burst_index, e.burst_count) for e in evs]}"
    check(f"T-H2.6 mode {mode:+d}: {k} burst(s) x {size}B on 3 links",
          ok, detail)

# T-H2.7 bubble overhead
_, _, mk7_b, _ = unicast_run(8 * W, burst=0)
check("T-H2.7 burst0 overhead == (HP+2)*(8-1)*b = 21",
      mk7_b - mk5 == 21, f"burst0={mk7_b} default={mk5}")

# T-H2.8 larger bursts faster
mks = {}
for m in (0, 1, 3, 7, -1):
    _, _, mk8, _ = unicast_run(16 * W, burst=m)
    mks[m] = mk8
check("T-H2.8 ordering 0 > 1 > 3 > 7 >= -1",
      mks[0] > mks[1] > mks[3] > mks[7] >= mks[-1],
      f"{mks}")

# T-H2.9 boundary remainder
_, noc9, _, _ = unicast_run(9 * W, burst=7)
lk9 = find_r2r(noc9, 0, 4)
ev9 = sorted([e for e in lk9.events if not e.is_control],
             key=lambda e: e.burst_index)
check("T-H2.9 9W / mode7 -> two bursts 8W+1W, count=2",
      len(ev9) == 2 and ev9[0].data_size == 8 * W and ev9[1].data_size == W
      and ev9[0].burst_count == 2 and ev9[1].burst_count == 2,
      f"{[(e.data_size, e.burst_count) for e in ev9]}")

# T-H2.10 burst-enabled interleaving
_, by10, lk10 = contention_run(0, 0, n0=4 * W, n1=4 * W,
                               burst0=0, burst1=0)
seq = []
for e in sorted(lk10.events, key=lambda e: (e.start_time, e.burst_index)):
    if not e.is_control:
        seq.append(e.index)
alt = (len(seq) == 8
       and all(seq[i] != seq[i + 1] for i in range(7))
       and seq.count(1) == 4 and seq.count(2) == 4)
check("T-H2.10 two flows strictly alternate bursts", alt, f"{seq}")

# T-H2.11 single-side per-burst rounding
env11, noc11, _, _ = unicast_run(
    16 * W, burst=7, mode=TransferMode.SINGLE_SIDE, header=0,
    src_node=0, dst_node=36, sr=0, dr=28, dport=10)
lk11 = find_r2r(noc11, 0, 4)
busy_ss = sum(e.end_time - e.start_time for e in lk11.events
              if not e.is_control and e.index == 1)
_, noc11d, _, _ = unicast_run(16 * W, burst=7, src_node=0, dst_node=4)
lk11d = find_r2r(noc11d, 0, 4)
busy_ds = sum(e.end_time - e.start_time for e in lk11d.events
              if not e.is_control and e.index == 1)
check("T-H2.11 single-side per-link busy == 18 > dual-side 16",
      busy_ss == 18 and busy_ds == 16 and busy_ss > busy_ds,
      f"ss={busy_ss} ds={busy_ds}")

# T-H2.12 delay charged once per link
_, noc12, mk12, _ = unicast_run(8 * W, burst=0, delay=2)
lk12 = find_r2r(noc12, 0, 4)
wall = max(e.end_time for e in lk12.events if not e.is_control) - \
    min(e.start_time for e in lk12.events if not e.is_control)
check("T-H2.12 delay once: link wall=17, makespan=52",
      wall == 17 and mk12 == 52, f"wall={wall} mk={mk12}")

# =====================================================================
print("=== H2.13-H2.20: FIFO flow control ===")

# T-H2.13 register_fifo validation
env = simpy.Environment()
noc = make_noc(env)
noc.register_fifo(0, 0, 2)


def expect_raise(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True


check("T-H2.13 duplicate (hw,logic) raises",
      expect_raise(noc.register_fifo, 0, 0, 2))
check("T-H2.13 depth<1 raises", expect_raise(noc.register_fifo, 0, 1, 0))
check("T-H2.13 hw_id out of range raises",
      expect_raise(noc.register_fifo, 64, 0, 2)
      and expect_raise(noc.register_fifo, -1, 0, 2))
check("T-H2.13 logic_id out of range raises",
      expect_raise(noc.register_fifo, 0, 32, 2)
      and expect_raise(noc.register_fifo, 0, -1, 2))


def fifo_workload(D, consume, K=8, use_fifo=True, n=W):
    env = simpy.Environment()
    noc = make_noc(env)
    out, _ = attach(noc, 4, 0, 400)
    arr = []
    attach(noc, 8, 0, 800, consume=consume, arrivals=arr)
    samples = []
    if use_fifo:
        f = noc.register_fifo(0, 0, D)
        samples = sample_level(env, f['credits'])
    for i in range(1, K + 1):
        def s(i=i):
            yield env.timeout(0)
            out.put(Message(src=400, dst=8, index=i, data=ds(n),
                            element_bytes=1, dst_local_port=0,
                            fifo_hw_id=0 if use_fifo else -1,
                            fifo_logic_id=0 if use_fifo else -1,
                            fifo_check_type=0 if use_fifo else -1))
        env.process(s())
    env.run(until=2000)
    lk = find_r2r(noc, 4, 8)
    fwd = {}
    for e in lk.events:
        if not e.is_control and e.burst_index == 0 and e.index not in fwd:
            fwd[e.index] = e.start_time
    return env, arr, fwd, samples


# T-H2.14 no FIFO
_, arr14, fwd14, _ = fifo_workload(2, 0, use_fifo=False)
check("T-H2.14 no-FIFO delivers all 8 with no stall",
      len(arr14) == 8 and all(fwd14[i + 1] <= fwd14[i] + 2
                              for i in range(1, 8)),
      f"{sorted(fwd14.items())}")

# T-H2.15 WRITE_FULL backpressure with slow sink
_, arr15, fwd15, s15 = fifo_workload(2, 50)
min_level = min(l for _, l in s15)
inc = all(fwd15[i + 1] > fwd15[i] for i in range(3, 8))
mk_slow = arr15[-1][0]
_, arr_fast, _, _ = fifo_workload(2, 0)
mk_fast = arr_fast[-1][0]
check("T-H2.15 slow sink backpressure (8 arrivals, level hits 0, "
      "packets 3..8 increasing, slow >> fast)",
      len(arr15) == 8 and min_level == 0 and inc and mk_slow > mk_fast + 100,
      f"arr={len(arr15)} min={min_level} fwd={sorted(fwd15.items())} "
      f"slow={mk_slow} fast={mk_fast}")

# T-H2.16 fast receiver no stall (depth >= pipeline depth => no credit stall)
_, arr16, _, _ = fifo_workload(8, 0)
_, arr16b, _, _ = fifo_workload(8, 0, use_fifo=False)
check("T-H2.16 fast sink + deep FIFO: makespan == no-FIFO baseline",
      arr16[-1][0] == arr16b[-1][0],
      f"{arr16[-1][0]} vs {arr16b[-1][0]}")

# T-H2.17 depth scales concurrency (consume < credit round-trip so D matters)
_, arr_d4, _, _ = fifo_workload(4, 2)
_, arr_d1, _, _ = fifo_workload(1, 2)
check("T-H2.17 D=4 finishes sooner than D=1 under latency-bound sink",
      arr_d4[-1][0] < arr_d1[-1][0],
      f"D4={arr_d4[-1][0]} D1={arr_d1[-1][0]}")

# T-H2.18 isolation + no cross-talk
env18 = simpy.Environment()
noc18 = make_noc(env18)
out_a, _ = attach(noc18, 4, 0, 400)
out_b, _ = attach(noc18, 4, 1, 401)
out_c, _ = attach(noc18, 4, 2, 402)
arr_a, arr_b, arr_c = [], [], []
attach(noc18, 8, 0, 800, consume=40, arrivals=arr_a)
attach(noc18, 8, 1, 801, consume=40, arrivals=arr_b)
attach(noc18, 8, 2, 802, consume=0, arrivals=arr_c)
noc18.register_fifo(0, 0, 1)
noc18.register_fifo(1, 1, 1)


def stream(out, idx0, dst_port, hw, logic, ctype, src):
    for k in range(3):
        def s(k=k):
            yield env18.timeout(0)
            out.put(Message(src=src, dst=8, index=idx0 + k, data=ds(W),
                            element_bytes=1, dst_local_port=dst_port,
                            fifo_hw_id=hw, fifo_logic_id=logic,
                            fifo_check_type=ctype))
        env18.process(s())


stream(out_a, 10, 0, 0, 0, 0, 400)
stream(out_b, 20, 1, 1, 1, 0, 401)
stream(out_c, 30, 2, -1, -1, -1, 402)
env18.run(until=500)
check("T-H2.18 independent streams all arrive; no-FIFO stream not blocked",
      len(arr_a) == 3 and len(arr_b) == 3 and len(arr_c) == 3
      and arr_c[-1][0] < arr_a[-1][0] and arr_c[-1][0] < arr_b[-1][0],
      f"A={arr_a[-1][0]} B={arr_b[-1][0]} C={arr_c[-1][0]}")

# T-H2.19 UPDATE return is capped/non-blocking
env19 = simpy.Environment()
noc19 = make_noc(env19)
f19 = noc19.register_fifo(0, 0, 2)
p0_out, _ = attach(noc19, 0, 0, 500, drain=False)
arr19 = []
p1_out, _ = attach(noc19, 0, 1, 501, arrivals=arr19)
samples19 = sample_level(env19, f19['credits'])


def put19(idx, port, out, ctype, at):
    yield env19.timeout(at)
    out.put(Message(src=500 + port, dst=500 + port, index=idx, data=ds(W),
                    element_bytes=1, dst_local_port=port,
                    fifo_hw_id=0, fifo_logic_id=0, fifo_check_type=ctype))


env19.process(put19(1, 0, p0_out, 0, 0))   # WRITE_FULL (returns immediately)
env19.process(put19(2, 0, p0_out, 0, 0))   # WRITE_FULL (held -> level=1)
env19.process(put19(3, 1, p1_out, 2, 10))  # UPDATE -> level 2
env19.process(put19(4, 1, p1_out, 2, 20))  # UPDATE when full -> dropped
env19.run(until=30)
level_before_C = [l for t, l in samples19 if 5 <= t <= 9]
level_after_C = [l for t, l in samples19 if 13 <= t <= 19]
max_level = max(l for _, l in samples19)
final_level = samples19[-1][1]
got_C = any(m.index == 3 for _, m in arr19)
got_D = any(m.index == 4 for _, m in arr19)
check("T-H2.19 capped UPDATE: one credit held, C/D delivered, level tops at 2",
      bool(level_before_C) and min(level_before_C) == 1
      and bool(level_after_C) and min(level_after_C) == 2
      and max_level <= 2 and final_level == 2 and got_C and got_D,
      f"before={level_before_C} after={level_after_C} "
      f"max={max_level} final={final_level} C={got_C} D={got_D}")

# T-H2.20 control/sync packets do not touch FIFO
env20 = simpy.Environment()
noc20 = make_noc(env20)
SRC, DST, SR, DR, DPORT = 0, 36, 0, 28, 10
out20, _ = attach(noc20, SR, 0, SRC)
r2n, _ = noc20.attach_local(DR, DPORT, node_id=DST)
all_arr = []


def sink20():
    while True:
        m = yield r2n.get()
        all_arr.append((env20.now, m))


noc20.env.process(sink20())
f20 = noc20.register_fifo(1, 1, 2)
sample_level(env20, f20['credits'])
returns = []
_orig = noc20.routers[DR]._fifo_return


def wrapped(msg):
    returns.append(msg)
    yield from _orig(msg)


noc20.routers[DR]._fifo_return = wrapped
out20.put(Message(src=SRC, dst=DST, index=1, data=ds(160), element_bytes=1,
                  dst_local_port=DPORT, transfer_mode=TransferMode.SINGLE_SIDE,
                  header_bytes=8, fifo_hw_id=1, fifo_logic_id=1,
                  fifo_check_type=0))
env20.run(until=200)
data_returns = [m for m in returns if m.fifo_check_type in (0, 2)]
ctrl_bad = [m for m in returns if m.is_control and m.fifo_check_type != -1]
ctrl_msgs = [m for _, m in all_arr if m.is_control]
ctrl_clean = all(m.fifo_check_type == -1 and m.priority == 0
                 and m.burst_len_mode == -1 for m in ctrl_msgs)
data_got = any(not m.is_control and m.index == 1 for _, m in all_arr)
final20 = f20['credits'].level
check("T-H2.20 single-side WRITE_FULL: credit returned once, controls clean",
      data_got and len(data_returns) == 1 and len(ctrl_bad) == 0
      and ctrl_clean and final20 == 2,
      f"data_got={data_got} data_returns={len(data_returns)} "
      f"ctrl_bad={len(ctrl_bad)} ctrl_clean={ctrl_clean} final={final20}")

# =====================================================================
print("=== H2.21-H2.25: robustness / regression / validation ===")

# T-H2.21 all-default regression
HP3 = hops(0, 3)
_, _, mk21, _ = unicast_run(1600, src_node=0, dst_node=3, sr=0, dr=3)
check("T-H2.21 PE0->PE3 default makespan == 503",
      mk21 == (HP3 + 2) * 100 + HP3 == 503, f"{mk21}")

# T-H2.22 non-unicast unaffected
env22 = simpy.Environment()
noc22 = make_noc(env22)
mask = (1 << 1) | (1 << 4) | (1 << 12) | (1 << 28)
mc_arr = []
for r in (1, 4, 12, 28):
    r2n, _ = noc22.attach_local(r, 0, node_id=r)

    def cons(r2n=r2n):
        while True:
            m = yield r2n.get()
            if not m.is_control:
                mc_arr.append(m)
    noc22.env.process(cons())
_, src_out = noc22.attach_local(0, 22, node_id=1000)
src_out.put(Message(src=1000, dst=0, index=1, data=ds(W), element_bytes=1,
                    dst_local_port=0, trans_type=TransType.MULTICAST,
                    dst_mask=mask, burst_len_mode=7))
env22.run()
check("T-H2.22a multicast with burst mode delivers to all 4 members",
      len(mc_arr) == 4, f"{len(mc_arr)}")

# reduce with default QoS fields still works
env22b = simpy.Environment()
noc22b = make_noc(env22b)
noc22b.register_reduce(1, [0, 4], 12, op=1)
for s in (0, 4):
    _, o = noc22b.attach_local(s, 22 if s == 12 else 0, node_id=s)
    o.put(Message(src=s, dst=12, index=s + 1, data=ds(W), element_bytes=1,
                  trans_type=TransType.REDUCE, task_id=1, reduce_op=1,
                  dst_local_port=0))
sink_in, _ = noc22b.attach_local(12, 0, node_id=1012)
red_arr = []


def rsink():
    while True:
        m = yield sink_in.get()
        if not m.is_control:
            red_arr.append(m)


noc22b.env.process(rsink())
env22b.run()
check("T-H2.22b reduce unaffected by default QoS fields",
      len(red_arr) >= 1, f"{len(red_arr)}")

# T-H2.23 determinism
runs = []
for _ in range(2):
    env23 = simpy.Environment()
    noc23 = make_noc(env23)
    o0, _ = attach(noc23, 4, 0, 400)
    o1, _ = attach(noc23, 4, 1, 401)
    attach(noc23, 8, 0, 800)
    noc23.register_fifo(0, 0, 2)

    def inj(out, idx, p, b, src):
        yield env23.timeout(0)
        out.put(Message(src=src, dst=8, index=idx, data=ds(4 * W),
                        element_bytes=1, dst_local_port=0,
                        priority=p, burst_len_mode=b,
                        fifo_hw_id=0, fifo_logic_id=0, fifo_check_type=0))
    env23.process(inj(o0, 1, 0, 0, 400))
    env23.process(inj(o1, 2, 3, 7, 401))
    env23.run()
    sig = []
    for lk in noc23.r2r_links + noc23.local_links:
        for e in lk.events:
            if e.index in (1, 2):
                sig.append((lk.src_id, lk.dst_id, e.index, e.burst_index,
                            e.start_time, e.end_time, e.priority))
    runs.append(sorted(sig))
check("T-H2.23 two identical QoS runs produce identical event traces",
      runs[0] == runs[1], "mismatch")

# T-H2.24 validation
try:
    Message(src=0, dst=4, index=1, data=ds(W), burst_len_mode=5)
    raised_b = False
except ValueError:
    raised_b = True
try:
    Message(src=0, dst=4, index=1, data=ds(W), priority=4)
    raised_p = False
except ValueError:
    raised_p = True
# unregistered FIFO at runtime
env24 = simpy.Environment()
noc24 = make_noc(env24)
o24, _ = attach(noc24, 0, 0, 0)  # drains too
o24.put(Message(src=0, dst=0, index=1, data=ds(W), element_bytes=1,
                fifo_hw_id=3, fifo_logic_id=3, fifo_check_type=0))
raised_f = False
try:
    env24.run()
except ProfilingSimError:
    raised_f = True
check("T-H2.24 invalid burst/priority raise ValueError; unregistered FIFO "
      "raises ProfilingSimError",
      raised_b and raised_p and raised_f,
      f"burst={raised_b} prio={raised_p} fifo={raised_f}")

# T-H2.25 self-loop FIFO
env25 = simpy.Environment()
noc25 = make_noc(env25)
arr25 = []
o25, _ = attach(noc25, 0, 0, 0, consume=40, arrivals=arr25)
f25 = noc25.register_fifo(0, 0, 1)
sample_level(env25, f25['credits'])
for i in (1, 2):
    def s(i=i):
        yield env25.timeout(0)
        o25.put(Message(src=0, dst=0, index=i, data=ds(W), element_bytes=1,
                        fifo_hw_id=0, fifo_logic_id=0, fifo_check_type=0))
    env25.process(s())
env25.run(until=200)
check("T-H2.25 self-loop FIFO: both packets drain with credit accounting",
      len(arr25) == 2 and arr25[1][0] >= 40 and f25['credits'].level == 1,
      f"arr={[t for t,_ in arr25]} level={f25['credits'].level}")

# =====================================================================
print(f"\n{'='*50}")
print(f"Feature H2: {passed} passed, {failed} failed, {passed+failed} total")
sys.exit(1 if failed else 0)
