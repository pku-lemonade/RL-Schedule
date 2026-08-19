"""Feature H1: In-Router Reduction (Route Reduce) tests."""
import sys, os, math
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransType, Direction, ProfilingSimError,
)
from profiling_sim.noc import NoC
from profiling_sim.nodes import NodeType, DataNocLocalId
from profiling_sim.memory import Memory, DMANode

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def ds(n):
    return [DimSlice(start=0, end=n)]


def make_noc(env, width=16, reduce_latency=2):
    cfg = NoCConfig(x=4, y=8,
                    router=RouterConfig(reduce_latency=reduce_latency),
                    link=LinkConfig(width=width, delay=0))
    return NoC(env, cfg, deterministic=True).build()


def find_link(noc, src, dst):
    for lk in noc.r2r_links:
        if lk.src_id == src and lk.dst_id == dst:
            return lk
    raise AssertionError(f"link {src}->{dst} not found")


def reduce_run(sources, root, n=1600, op=1, values=None, root_port=0,
               width=16, reduce_latency=2, inject_at=None,
               extra_unicasts=None):
    """Build a NoC, register a reduce tree, inject sources, return results.

    Returns (env, noc, arrivals, extra) where arrivals is a list of
    (time, message) delivered to the root sink.
    """
    env = simpy.Environment()
    noc = make_noc(env, width=width, reduce_latency=reduce_latency)
    task_id = 1
    noc.register_reduce(task_id, list(sources), root, op=op)
    outs = {}
    for s in sources:
        port = 22 if s == root else 0
        _, out = noc.attach_local(s, port, node_id=s)
        outs[s] = (out, port)
    sink_in, _ = noc.attach_local(root, root_port, node_id=root + 1000)
    arrivals = []

    def sink():
        while True:
            m = yield sink_in.get()
            if not m.is_control:
                arrivals.append((env.now, m))

    env.process(sink())
    inject_at = inject_at or {}
    for i, s in enumerate(sources):
        out, port = outs[s]
        v = values[i] if values else 0
        at = inject_at.get(s, 0)

        def _send(out=out, port=port, s=s, v=v, at=at):
            yield env.timeout(at)
            out.put(Message(
                src=s, dst=root, index=i + 1, data=ds(n), element_bytes=1,
                trans_type=TransType.REDUCE, task_id=task_id, reduce_op=op,
                reduce_count=1, value=v, src_local_port=port,
                dst_local_port=root_port, header_bytes=0))

        env.process(_send())

    if extra_unicasts:
        u_outs = {}
        for u in extra_unicasts:
            _, o = noc.attach_local(u['src'], 0,
                                    node_id=u['src'] + 2000)
            u_outs[id(u)] = o

        def _u(u=u):
            yield env.timeout(u.get('at', 0))
            o = u_outs[id(u)]
            o.put(Message(
                src=u['src'], dst=u['dst'], index=u.get('index', 900),
                data=ds(n), element_bytes=1,
                dst_local_port=u.get('dst_port', 0), header_bytes=0))
        for u in extra_unicasts:
            env.process(_u(u))

    env.run()
    return env, noc, arrivals


def data_events(link):
    return [e for e in link.events if not e.is_control]


# ----------------------------------------------------------------------
print("=== Tree builder (unit) ===")

env = simpy.Environment()
noc = make_noc(env)
tree = noc.register_reduce(1, [0, 4], 28, op=1)
check("T-H1.1a r0 children {local} parent NORTH",
      tree[0]['children'] == {"local"} and tree[0]['parent'] == Direction.NORTH,
      f"{tree[0]}")
check("T-H1.1b r4 children {SOUTH,local} parent NORTH",
      tree[4]['children'] == {Direction.SOUTH, "local"}
      and tree[4]['parent'] == Direction.NORTH, f"{tree[4]}")
for r in (8, 12, 16, 20, 24):
    check(f"T-H1.1c r{r} pass-through {{SOUTH}} parent NORTH",
          tree[r]['children'] == {Direction.SOUTH}
          and tree[r]['parent'] == Direction.NORTH, f"{tree[r]}")
check("T-H1.1d r28 root {SOUTH} parent None",
      tree[28]['children'] == {Direction.SOUTH}
      and tree[28]['parent'] is None and tree[28]['is_root'])
check("T-H1.1e no extra routers in tree",
      set(tree.keys()) == {0, 4, 8, 12, 16, 20, 24, 28},
      f"{set(tree.keys())}")

tree2 = noc.register_reduce(2, [24, 25, 29, 30], 28, op=1)
check("T-H1.2a r29 children {EAST,local} parent WEST",
      tree2[29]['children'] == {Direction.EAST, "local"}
      and tree2[29]['parent'] == Direction.WEST, f"{tree2[29]}")
check("T-H1.2b r28 root children {EAST,SOUTH}",
      tree2[28]['children'] == {Direction.EAST, Direction.SOUTH}
      and tree2[28]['parent'] is None)
for r in (25, 30):
    check(f"T-H1.2c r{r} source {{local}}",
          tree2[r]['children'] == {"local"}, f"{tree2[r]}")
check("T-H1.2c r24 children {EAST,local} parent NORTH",
      tree2[24]['children'] == {Direction.EAST, "local"}
      and tree2[24]['parent'] == Direction.NORTH, f"{tree2[24]}")

check("T-H1.3a duplicate task_id raises", True)
try:
    noc.register_reduce(1, [1], 0)
    check("T-H1.3a duplicate task_id raises", False, "no error")
except ValueError:
    pass
for bad in ([0, 0], [0, 0, 4]):
    try:
        noc.register_reduce(99, bad, 0)
        check(f"T-H1.3b duplicate source {bad} raises", False, "no error")
    except ValueError:
        pass
def expect_raise(task_id, sources, root):
    try:
        noc.register_reduce(task_id, sources, root)
        check(f"T-H1.3c invalid tid={task_id} src={sources} root={root} raises",
              False, "no error")
    except ValueError:
        pass

expect_raise(90, [], 0)
expect_raise(91, [99], 0)
expect_raise(92, [0], 99)

# ----------------------------------------------------------------------
print("\n=== Single-source and baseline ===")
W, R = 16, 2
c = 100  # n=1600

_, _, arr = reduce_run([0], 28, n=W * c)
check("T-H1.4 single source: one packet count=1 last=True",
      len(arr) == 1 and arr[0][1].reduce_count == 1
      and arr[0][1].reduce_is_last, f"{arr}")
check("T-H1.4 single source makespan == unicast 9c+7 = 907",
      arr[0][0] == 9 * c + 7, f"{arr[0][0]}")

_, _, arr = reduce_run([28], 28, n=W * c)
check("T-H1.5 zero-hop makespan == 2c = 200",
      len(arr) == 1 and arr[0][0] == 2 * c, f"{arr}")

_, _, arr = reduce_run([0], 28, n=W * c, op=2)
check("T-H1.6 Max single source reduce_op==2",
      len(arr) == 1 and arr[0][1].reduce_op == 2, f"{arr}")

# ----------------------------------------------------------------------
print("\n=== Multi-source merges ===")
_, _, arr = reduce_run([0, 4], 28, n=W * c)
check("T-H1.7 two-source: one packet count=2 makespan 9c+7+R=909",
      len(arr) == 1 and arr[0][1].reduce_count == 2
      and arr[0][1].reduce_is_last and arr[0][0] == 9 * c + 7 + R,
      f"{[(a[0], a[1].reduce_count) for a in arr]}")

_, _, arr = reduce_run([0, 4, 8, 12], 28, n=W * c)
check("T-H1.8 four-chain: count=4 makespan 9c+7+3R=913",
      len(arr) == 1 and arr[0][1].reduce_count == 4
      and arr[0][0] == 9 * c + 7 + 3 * R,
      f"{arr[0][0] if arr else 'none'}")

_, _, arr = reduce_run([24, 25, 29, 30], 28, n=W * c)
check("T-H1.9 two-level 2x2-way: count=4 makespan 4c+2+2R=406",
      len(arr) == 1 and arr[0][1].reduce_count == 4
      and arr[0][0] == 4 * c + 2 + 2 * R,
      f"{arr[0][0] if arr else 'none'}")

# T-H1.10 size-independent overhead
for nn, cc in [(160, 10), (16000, 1000)]:
    _, _, a = reduce_run([0, 4, 8, 12], 28, n=nn)
    overhead = a[0][0] - (9 * cc + 7)
    check(f"T-H1.10 overhead = 3R for n={nn} (c={cc})",
          overhead == 3 * R, f"{overhead}")
# T-H1.9 uses two parallel 2-way merges (R each) at r24/r29 plus a 2-way
# merge (R) at the root. Compare against a 2-source variant on the same
# topology to isolate the first-level merge cost.
_, _, a2 = reduce_run([25, 29], 28, n=W * c)  # root 2-way (R) only
check("T-H1.10b 2-way tree (25,29)->28 makespan 4c+2+R=404",
      a2[0][0] == 4 * c + 2 + R, f"{a2[0][0]}")
_, _, a3 = reduce_run([25, 29, 24, 30], 28, n=W * c)  # T-H1.9 topology
check("T-H1.10c adding operands r24,r30 adds exactly R (parallel first-level merges)",
      a3[0][0] - a2[0][0] == R, f"{a3[0][0] - a2[0][0]}")

_, _, arr = reduce_run([0, 28], 28, n=W * c)
check("T-H1.11 source co-located with root: count=2 makespan 9c+7+R=909",
      len(arr) == 1 and arr[0][1].reduce_count == 2
      and arr[0][0] == 9 * c + 7 + R, f"{arr[0][0] if arr else 'none'}")

_, _, arr = reduce_run([0, 4, 8], 28, n=W * c)
check("T-H1.12 three-source: count=3 makespan 9c+7+2R=911",
      len(arr) == 1 and arr[0][1].reduce_count == 3
      and arr[0][0] == 9 * c + 7 + 2 * R,
      f"{arr[0][0] if arr else 'none'}")

# T-H1.13 root in the middle (r12), sources r3 (HP=6) and r28 (HP=4)
hp3 = abs(3 // 4 - 12 // 4) + abs(3 % 4 - 12 % 4)
hp28 = abs(28 // 4 - 12 // 4) + abs(28 % 4 - 12 % 4)
slow = max(hp3, hp28)
expected_mid = (slow + 2) * c + slow + R
_, noc_mid, arr = reduce_run([3, 28], 12, n=W * c)
check("T-H1.13a root r12 has children {NORTH,SOUTH}",
      noc_mid.reduce_trees[1][12]['children'] == {Direction.NORTH, Direction.SOUTH},
      f"{noc_mid.reduce_trees[1][12]}")
check("T-H1.13b middle-root makespan == (HP+2)c+HP+R",
      len(arr) == 1 and arr[0][1].reduce_count == 2
      and arr[0][0] == expected_mid,
      f"{arr[0][0] if arr else 'none'} vs {expected_mid} (hp3={hp3},hp28={hp28})")

# ----------------------------------------------------------------------
print("\n=== Scalar value reduction ===")
_, _, arr = reduce_run([0, 4, 8, 12], 28, n=W * c, op=1,
                       values=[1, 2, 3, 4])
check("T-H1.14a Add merges scalar value -> 10",
      len(arr) == 1 and arr[0][1].value == 10, f"{arr[0][1].value if arr else '?'}")
_, _, arr = reduce_run([0, 4, 8, 12], 28, n=W * c, op=2,
                       values=[7, 2, 9, 4])
check("T-H1.14b Max merges scalar value -> 9",
      len(arr) == 1 and arr[0][1].value == 9, f"{arr[0][1].value if arr else '?'}")

# ----------------------------------------------------------------------
print("\n=== Bandwidth compression and per-hop reduce_count ===")
_, noc_r, arr = reduce_run([0, 4], 28, n=W * c)
post = find_link(noc_r, 4, 8)
post_evs = data_events(post)
check("T-H1.15a post-merge link r4->r8 has ONE event n bytes count=2",
      len(post_evs) == 1 and post_evs[0].data_size == W * c
      and post_evs[0].reduce_count == 2,
      f"{len(post_evs)}, sizes={[e.data_size for e in post_evs]}")

# Control: two independent unicasts 0->28 and 4->28
envc = simpy.Environment()
nocc = make_noc(envc)
_, o0 = nocc.attach_local(0, 0, node_id=0)
_, o4 = nocc.attach_local(4, 0, node_id=4)
sin, _ = nocc.attach_local(28, 0, node_id=999)
def sk():
    while True:
        yield sin.get()
envc.process(sk())
o0.put(Message(src=0, dst=28, index=1, data=ds(W * c), element_bytes=1,
               dst_local_port=0))
o4.put(Message(src=4, dst=28, index=2, data=ds(W * c), element_bytes=1,
               dst_local_port=0))
envc.run()
post_c = find_link(nocc, 4, 8)
post_c_evs = data_events(post_c)
check("T-H1.15b control unicasts: TWO events totaling 2n on r4->r8",
      len(post_c_evs) == 2 and sum(e.data_size for e in post_c_evs) == 2 * W * c,
      f"{len(post_c_evs)}")
check("T-H1.15c reduce halves post-merge byte total",
      sum(e.data_size for e in post_evs) * 2
      == sum(e.data_size for e in post_c_evs))

_, noc_c4, _ = reduce_run([0, 4, 8, 12], 28, n=W * c)
expected_counts = {(0, 4): 1, (4, 8): 2, (8, 12): 3, (12, 16): 4}
ok = True
detail = []
for (s, d), cnt in expected_counts.items():
    lk = find_link(noc_c4, s, d)
    evs = data_events(lk)
    if len(evs) != 1 or evs[0].reduce_count != cnt:
        ok = False
        detail.append(f"{s}->{d}: {len(evs)} evs count={[e.reduce_count for e in evs]}")
check("T-H1.16 per-hop reduce_count 1->2->3->4", ok, "; ".join(detail))

# T-H1.17 contention: reduce (0,4)->28 plus unicast 8->28 injected at t=200
_, noc_con, arr_con = reduce_run(
    [0, 4], 28, n=W * c,
    extra_unicasts=[{'src': 8, 'dst': 28, 'at': 200, 'dst_port': 0}])
shared = find_link(noc_con, 12, 16)
shared_evs = data_events(shared)
reduce_only_makespan = 9 * c + 7 + R
check("T-H1.17a shared link r12->r16 carries reduce + unicast (2 events)",
      len(shared_evs) == 2, f"{len(shared_evs)}")
reduce_arrival = next(t for t, m in arr_con if m.task_id == 1)
check("T-H1.17b contention makes reduce wait (makespan > 909)",
      reduce_arrival > reduce_only_makespan,
      f"{reduce_arrival} vs {reduce_only_makespan}")

# ----------------------------------------------------------------------
print("\n=== Memory / writeSum integration ===")


def build_gm_reduce(sources, values, n=1600, write_sum=0, addr=100,
                    op=1, root_port=DataNocLocalId.GM_WDMA_CH0):
    env = simpy.Environment()
    noc = make_noc(env)
    task_id = 7
    noc.register_reduce(task_id, list(sources), 28, op=op)
    gmem = Memory(env, "GM", 2 ** 40, 0)
    wdma = DMANode(env, 36, NodeType.GM_WDMA, 28,
                   [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1],
                   noc, memory=gmem, engine_width=16, channels=2,
                   is_read=False)
    outs = {}
    for s in sources:
        _, out = noc.attach_local(s, 0, node_id=s)
        outs[s] = out
    for i, s in enumerate(sources):
        outs[s].put(Message(
            src=s, dst=36, index=i + 1, data=ds(n), element_bytes=1,
            trans_type=TransType.REDUCE, task_id=task_id, reduce_op=op,
            reduce_count=1, value=values[i], src_local_port=0,
            dst_local_port=root_port, addr=addr, write_sum=write_sum))
    env.run()
    return env, noc, gmem


_, _, gmem = build_gm_reduce([0, 4, 8, 12], [1, 2, 3, 4])
wdma_events = [e for e in gmem.events if e[0] == 36]
check("T-H1.18a exactly one GM write event of n bytes",
      len(wdma_events) == 1 and wdma_events[0][3] == W * c,
      f"{wdma_events}")
check("T-H1.18b GM.used == n", gmem.used == W * c, f"{gmem.used}")

env2 = simpy.Environment()
noc2 = make_noc(env2)
ADDR = 100
noc2.register_reduce(7, [0, 4, 8, 12], 28, op=1)
noc2.register_reduce(8, [0, 4, 8, 12], 28, op=1)
gmem2 = Memory(env2, "GM", 2 ** 40, 0)
wdma2 = DMANode(env2, 36, NodeType.GM_WDMA, 28,
                [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1],
                noc2, memory=gmem2, engine_width=16, channels=2,
                is_read=False)
outs2 = {}
for s in (0, 4, 8, 12):
    _, o = noc2.attach_local(s, 0, node_id=s)
    outs2[s] = o
for i, s in enumerate((0, 4, 8, 12)):
    outs2[s].put(Message(
        src=s, dst=36, index=i + 1, data=ds(W * c), element_bytes=1,
        trans_type=TransType.REDUCE, task_id=7, reduce_op=1,
        reduce_count=1, value=[1, 2, 3, 4][i], src_local_port=0,
        dst_local_port=DataNocLocalId.GM_WDMA_CH0, addr=ADDR, write_sum=0))
first_val = {}
def second_phase():
    yield env2.timeout(2000)
    first_val['v'] = gmem2.read(ADDR)
    for i, s in enumerate((0, 4, 8, 12)):
        outs2[s].put(Message(
            src=s, dst=36, index=i + 10, data=ds(W * c), element_bytes=1,
            trans_type=TransType.REDUCE, task_id=8, reduce_op=1,
            reduce_count=1, value=[10, 20, 30, 40][i], src_local_port=0,
            dst_local_port=DataNocLocalId.GM_WDMA_CH0,
            addr=ADDR, write_sum=1))
env2.process(second_phase())
env2.run(until=4000)
check("T-H1.19a first overwrite stores in-router sum = 10",
      first_val['v'] == 10, f"{first_val['v']}")
check("T-H1.19b second atomic-add: 10 + 100 = 110",
      gmem2.read(ADDR) == 110, f"{gmem2.read(ADDR)}")

# ----------------------------------------------------------------------
print("\n=== Robustness and regression ===")

# T-H1.20 unregistered task_id / wrong router
try:
    envb = simpy.Environment()
    nocb = make_noc(envb)
    _, out = nocb.attach_local(0, 0, node_id=0)
    sin, _ = nocb.attach_local(28, 0, node_id=999)
    def sb():
        while True:
            yield sin.get()
    envb.process(sb())
    out.put(Message(src=0, dst=28, index=1, data=ds(16), element_bytes=1,
                    trans_type=TransType.REDUCE, task_id=42,
                    dst_local_port=0))
    envb.run()
    check("T-H1.20a unregistered task_id raises", False, "no error")
except ProfilingSimError:
    pass

# T-H1.21 no cross-talk: two reduces to same root router, different task_ids
envx = simpy.Environment()
nocx = make_noc(envx)
nocx.register_reduce(1, [0, 4], 28, op=1)
nocx.register_reduce(2, [1, 5], 28, op=1)
outs_a = {}
for s in (0, 4):
    _, o = nocx.attach_local(s, 0, node_id=s)
    outs_a[s] = o
outs_b = {}
for s in (1, 5):
    _, o = nocx.attach_local(s, 0, node_id=s)
    outs_b[s] = o
sinx, _ = nocx.attach_local(28, 0, node_id=999)
got = []
def sx():
    while True:
        m = yield sinx.get()
        if not m.is_control:
            got.append(m)
envx.process(sx())
for i, s in enumerate((0, 4)):
    outs_a[s].put(Message(src=s, dst=28, index=i + 1, data=ds(W * c),
                          element_bytes=1, trans_type=TransType.REDUCE,
                          task_id=1, reduce_op=1, reduce_count=1,
                          value=[1, 2][i], dst_local_port=0))
for i, s in enumerate((1, 5)):
    outs_b[s].put(Message(src=s, dst=28, index=i + 10, data=ds(W * c),
                          element_bytes=1, trans_type=TransType.REDUCE,
                          task_id=2, reduce_op=1, reduce_count=1,
                          value=[10, 20][i], dst_local_port=0))
envx.run()
by_task = {}
for m in got:
    by_task.setdefault(m.task_id, []).append(m)
ok_a = (len(by_task.get(1, [])) == 1 and by_task[1][0].reduce_count == 2
        and by_task[1][0].value == 3)
ok_b = (len(by_task.get(2, [])) == 1 and by_task[2][0].reduce_count == 2
        and by_task[2][0].value == 30)
check("T-H1.21 no cross-talk: each task produces one correct packet",
      ok_a and ok_b, f"{[(m.task_id, m.reduce_count, m.value) for m in got]}")

# T-H1.22 determinism
_, n1, a1 = reduce_run([0, 4, 8, 12], 28, n=W * c)
_, n2, a2 = reduce_run([0, 4, 8, 12], 28, n=W * c)
ev1 = sorted((e.start_time, e.end_time, e.data_size, e.reduce_count)
             for lk in n1.r2r_links for e in lk.events)
ev2 = sorted((e.start_time, e.end_time, e.data_size, e.reduce_count)
             for lk in n2.r2r_links for e in lk.events)
check("T-H1.22 deterministic makespan", a1[0][0] == a2[0][0])
check("T-H1.22 deterministic link events", ev1 == ev2)

# T-H1.23 regression: non-reduce workloads unaffected.
# (a) PE0 -> PE3 unicast baseline makespan 907
envs = simpy.Environment()
nocs = make_noc(envs)
_, so = nocs.attach_local(0, 0, node_id=0)
si, _ = nocs.attach_local(3, 0, node_id=3)
sarr = []
def ss():
    while True:
        m = yield si.get()
        if not m.is_control:
            sarr.append(envs.now)
envs.process(ss())
so.put(Message(src=0, dst=3, index=1, data=ds(W * c), element_bytes=1,
               dst_local_port=0, header_bytes=0))
envs.run()
# 0->3 HP=3: (3+2)*100+3 = 503
check("T-H1.23a unicast 0->3 still 503 (no reduce interference)",
      len(sarr) == 1 and sarr[0] == 503, f"{sarr}")
# (b) 4-member multicast delivers 4 copies
envs2 = simpy.Environment()
nocm = make_noc(envs2)
mask = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
_, mo = nocm.attach_local(15, 22, node_id=15)
marr = {r: 0 for r in range(4)}
for r in range(4):
    si, _ = nocm.attach_local(r, 0, node_id=r)
    def mk_sink(r=r, si=si):
        while True:
            m = yield si.get()
            if not m.is_control:
                marr[r] += 1
    envs2.process(mk_sink())
mo.put(Message(src=15, dst=15, index=1, data=ds(W * c), element_bytes=1,
               trans_type=TransType.MULTICAST, dst_mask=mask,
               src_local_port=22, dst_local_port=0))
envs2.run()
check("T-H1.23b multicast still delivers exactly 4 copies",
      all(v == 1 for v in marr.values()), f"{marr}")

print(f"\nFeature H1: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
