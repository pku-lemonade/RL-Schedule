"""Feature 6: Router multi-local-port tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig, load_arch
from profiling_sim.definitions import (
    Message, DimSlice, Slice, OperatorType, UnboundLocalPortError,
)
from profiling_sim.noc import NoC
from profiling_sim.architecture import Arch
from profiling_sim.dfg import DFG

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


def producer(env, link, msgs):
    for at, m in msgs:
        yield env.timeout(at)
        link.put(m)


def consumer(env, link, bucket):
    while True:
        m = yield link.get()
        bucket.append((env.now, m))


print("=== Feature 6: Router multi-local-port ===")

# T6.1
env = simpy.Environment(); noc = make_noc(env)
noc.attach_local(0, 0, node_id=100)
noc.attach_local(0, 7, node_id=101)
lp = noc.routers[0].local_ports
check("T6.1 two local ports", len(lp) == 2, f"{len(lp)}")
check("T6.1 ports distinct", lp[0]['in'] is not lp[7]['in'])

# T6.2
check("T6.2 default port 0", Message(src=0, dst=1, index=0, data=ds(4)).dst_local_port == 0)
check("T6.2 port 22 ok", Message(src=0, dst=1, index=0, data=ds(4), dst_local_port=22).dst_local_port == 22)
try:
    Message(src=0, dst=1, index=0, data=ds(4), dst_local_port=23)
    check("T6.2 port 23 raises", False)
except Exception:
    check("T6.2 port 23 raises", True)

# T6.3
env = simpy.Environment(); noc = make_noc(env)
_, src_out = noc.attach_local(0, 0)
p0_in, _ = noc.attach_local(3, 0, node_id=3)
p7_in, _ = noc.attach_local(3, 7, node_id=30)
b0, b7 = [], []
env.process(consumer(env, p0_in, b0)); env.process(consumer(env, p7_in, b7))
env.process(producer(env, src_out, [(0, Message(src=0, dst=3, index=1, data=ds(16), dst_local_port=7))]))
env.run(until=100)
check("T6.3 port7 got msg", len(b7) == 1, f"{len(b7)}")
check("T6.3 port0 empty", len(b0) == 0, f"{len(b0)}")

# T6.4
env = simpy.Environment(); noc = make_noc(env)
_, s0 = noc.attach_local(1, 0)
i0, _ = noc.attach_local(5, 0); i14, _ = noc.attach_local(5, 14, node_id=50)
c0, c14 = [], []
env.process(consumer(env, i0, c0)); env.process(consumer(env, i14, c14))
env.process(producer(env, s0, [
    (0, Message(src=1, dst=5, index=2, data=ds(8), dst_local_port=0)),
    (0, Message(src=1, dst=5, index=3, data=ds(8), dst_local_port=14))]))
env.run(until=100)
check("T6.4 port0 delivery", len(c0) == 1 and c0[0][1].dst_local_port == 0)
check("T6.4 port14 delivery", len(c14) == 1 and c14[0][1].dst_local_port == 14)

# T6.5 order independence
def three_port(order):
    env = simpy.Environment(); noc = make_noc(env)
    outs = {}
    for p in (0, 10, 14):
        _, outs[p] = noc.attach_local(2, p, node_id=200 + p)
    sinks = {}
    for p in (0, 10, 14):
        si, _ = noc.attach_local(6, p, node_id=600 + p)
        sinks[p] = []
        env.process(consumer(env, si, sinks[p]))
    env.process(producer(env, outs[order[0]], [
        (i, Message(src=2, dst=6, index=100 + p, data=ds(4), dst_local_port=p))
        for i, p in enumerate(order)]))
    for p in order[1:]:
        env.process(producer(env, outs[p], []))
    env.run(until=200)
    return {p: len(v) for p, v in sinks.items()}

# Need each producer on its own out link; redo properly
def three_port2(order):
    env = simpy.Environment(); noc = make_noc(env)
    outs = {}
    for p in (0, 10, 14):
        _, outs[p] = noc.attach_local(2, p, node_id=200 + p)
    sinks = {}
    for p in (0, 10, 14):
        si, _ = noc.attach_local(6, p, node_id=600 + p)
        sinks[p] = []
        env.process(consumer(env, si, sinks[p]))
    for i, p in enumerate(order):
        env.process(producer(env, outs[p],
            [(i, Message(src=2, dst=6, index=100 + p, data=ds(4), dst_local_port=p))]))
    env.run(until=200)
    return {p: len(v) for p, v in sinks.items()}

check("T6.5 forward order", three_port2([0, 10, 14]) == {0: 1, 10: 1, 14: 1})
check("T6.5 reverse order", three_port2([14, 10, 0]) == {0: 1, 10: 1, 14: 1})

# T6.6 backward compat full arch
class MockMapper:
    def __init__(self, dfg, n):
        self.dfg = dfg; self.n = n
        from profiling_sim.definitions import comp_operator
        self._comp = comp_operator
    def zero_degree(self):
        nodes = []
        for nid in range(1, self.n + 1):
            nd = self.dfg.get_node(nid)
            if not nd.father:
                nd.ready = True; nd.executed = True; nodes.append(nd)
        return nodes
    def update(self, src_node, dst_node):
        match src_node.operation:
            case OperatorType.LOAD_FEAT | OperatorType.RECV:
                dst_node.received_input += Slice(tensor_slice=src_node.input_size).size()
            case OperatorType.STORE | OperatorType.SEND:
                dst_node.received_input += Slice(tensor_slice=src_node.output_size).size()
            case OperatorType.LOAD_WGT:
                dst_node.received_weight += src_node.weight_slice().size()
            case _:
                dst_node.ready = True
        if dst_node.operation in self._comp:
            if dst_node.received_input != dst_node.input_slice().size(): return
            if dst_node.weight_slice().size() and dst_node.received_weight != dst_node.weight_slice().size(): return
            dst_node.ready = True
        if dst_node.operation in (OperatorType.LOAD_FEAT, OperatorType.RECV):
            if dst_node.received_input != dst_node.input_slice().size(): return
            dst_node.ready = True
    def all_tasks_completed(self, cid):
        return all(n.finished for n in self.dfg.nodes.values())

cfg = load_arch("profiling_sim/configs/mesh_8x4.json")
dfg = DFG()
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(1, 4, 4, 4))
dfg.add_node(2, OperatorType.POOL, 0, input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 4, 4))
dfg.add_node(3, OperatorType.SEND, 0, output_size=ds(1, 4, 4, 4))
dfg.add_node(4, OperatorType.RECV, 3, input_size=ds(1, 4, 4, 4))
dfg.add_node(5, OperatorType.POOL, 3, input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 1, 1))
dfg.add_node(6, OperatorType.STORE, 3, output_size=ds(1, 4, 1, 1))
dfg.add_edge(1, 2); dfg.add_edge(2, 3); dfg.add_edge(3, 4); dfg.add_edge(4, 5); dfg.add_edge(5, 6)
arch = Arch(cfg, MockMapper(dfg, 6), deterministic=True)
arch.execute()
le = [e for l in arch.noc.r2r_links for e in l.events if e.end_time > 0]
check("T6.6 PE0->PE3 3 r2r links", len(le) == 3, f"{len(le)}")
check("T6.6 PE3 STORE done", any(e.type == OperatorType.STORE and e.end_time > 0 for e in arch.cores[3].events))

# T6.7 unbound port raises
env = simpy.Environment(); noc = make_noc(env)
_, o = noc.attach_local(0, 0)
def bad():
    o.put(Message(src=0, dst=1, index=0, data=ds(4), dst_local_port=9))
    yield env.timeout(50)
env.process(bad())
raised = False
try:
    env.run(until=50)
except UnboundLocalPortError:
    raised = True
check("T6.7 unbound port raises", raised)

# T6.8 endpoint port fields
env = simpy.Environment(); noc = make_noc(env)
_, o = noc.attach_local(0, 0, node_id=0)
noc.attach_local(3, 7, node_id=30)
def s():
    o.put(Message(src=0, dst=3, index=0, data=ds(16), dst_local_port=7))
    yield env.timeout(50)
env.process(s()); env.run(until=50)
pe = [e for l in noc.local_links for e in l.events if e.end_time > 0]
re = [e for l in noc.r2r_links for e in l.events if e.end_time > 0]
check("T6.8 local link ports set", all(e.src_port >= 0 and e.dst_port >= 0 for e in pe))
check("T6.8 r2r link ports set", all(e.src_port >= 0 and e.dst_port >= 0 for e in re))
check("T6.8 delivery event dst=30 port7",
      any(e.dst_id == 30 and e.dst_port == 7 for e in pe))

# T6.9 injection from a local (non-PE) port forwards onto mesh
env = simpy.Environment(); noc = make_noc(env)
_, dma_out = noc.attach_local(28, 14, node_id=32)
pe_in, _ = noc.attach_local(0, 0, node_id=0)
got = []
env.process(consumer(env, pe_in, got))
env.process(producer(env, dma_out, [(0, Message(src=32, dst=0, index=0, data=ds(8), src_local_port=14))]))
env.run(until=100)
check("T6.9 DMA local port injects onto mesh", len(got) == 1, f"{len(got)}")
check("T6.9 PE0 received", got and got[0][1].dst == 0)

print(f"\nFeature 6: {passed} passed, {failed} failed")
import sys as _s
_s.exit(1 if failed else 0)
