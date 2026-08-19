"""Feature 9: Single-side vs dual-side transfer tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy, math
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransferMode, TransType, UnsupportedTransferMode,
)
from profiling_sim.noc import NoC

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


def run_xfer(src_node, dst_node, src_router, dst_router, dst_port,
             n_bytes, mode, width=16, extra_msgs=None):
    """Send one PE(0)->GM_WDMA(36) style message; return (makespan, noc)."""
    env = simpy.Environment()
    noc = make_noc(env, width=width)
    _, src_out = noc.attach_local(src_router, 0, node_id=src_node)
    sink_in, _ = noc.attach_local(dst_router, dst_port, node_id=dst_node)
    arrivals = []

    def sink():
        while True:
            m = yield sink_in.get()
            if not m.is_control:
                arrivals.append((env.now, m))
                break

    env.process(sink())
    src_out.put(Message(
        src=src_node, dst=dst_node, index=1, data=ds(n_bytes),
        element_bytes=1, dst_local_port=dst_port,
        transfer_mode=mode, header_bytes=(8 if mode == TransferMode.SINGLE_SIDE else 0),
    ))
    if extra_msgs:
        for at, m in extra_msgs:
            def _send(at=at, m=m):
                yield env.timeout(at)
                src_out.put(m)
            env.process(_send())
    env.run()
    return arrivals[0][0] if arrivals else -1, noc


def hops(a, b):
    ax, ay = a // 4, a % 4
    bx, by = b // 4, b % 4
    return abs(ax - bx) + abs(ay - by)


print("=== Feature 9: Single-side vs dual-side ===")

SRC, DST = 0, 36
SR, DR = 0, 28
HP = hops(SR, DR)

# T9.1 dual-side baseline
dual_t, _ = run_xfer(SRC, DST, SR, DR, 10, 1600, TransferMode.DUAL_SIDE)
expected_dual = (HP + 2) * math.ceil(1600 / 16) + HP
check("T9.1 dual makespan == (hops+2)*data/w + hops", dual_t == expected_dual,
      f"{dual_t} vs {expected_dual}")

# T9.2 single-side strictly greater
single_t, noc_s = run_xfer(SRC, DST, SR, DR, 10, 1600, TransferMode.SINGLE_SIDE)
check("T9.2 single-side slower", single_t > dual_t,
      f"{single_t} vs {dual_t}")

# T9.3 large transfer ratio ~0.9
N = 16 * 10000
dt, _ = run_xfer(SRC, DST, SR, DR, 10, N, TransferMode.DUAL_SIDE)
st, _ = run_xfer(SRC, DST, SR, DR, 10, N, TransferMode.SINGLE_SIDE)
ratio = dt / st
check("T9.3 large xfer dual/single ~0.9", 0.89 <= ratio <= 0.91,
      f"ratio={ratio:.4f}")

# T9.4 control packets on r2r links (request forward + response reverse)
ctrl_events = [e for lk in noc_s.r2r_links for e in lk.events
               if e.is_control and e.data_size == 16]
check("T9.4 control events == 2*hops", len(ctrl_events) == 2 * HP,
      f"{len(ctrl_events)} vs {2*HP}")
check("T9.4 all control 16B", all(e.data_size == 16 for e in ctrl_events))
_, noc_d = run_xfer(SRC, DST, SR, DR, 10, 1600, TransferMode.DUAL_SIDE)
dual_ctrl = [e for lk in noc_d.r2r_links for e in lk.events if e.is_control]
check("T9.4 dual-side no control packets", len(dual_ctrl) == 0,
      f"{len(dual_ctrl)}")

# T9.5 per-task mode: dual and single from same PE, only single slower
env = simpy.Environment(); noc = make_noc(env)
_, out = noc.attach_local(SR, 0, node_id=SRC)
sin, _ = noc.attach_local(DR, 10, node_id=DST)
arr = {}
def sink():
    while True:
        m = yield sin.get()
        if not m.is_control:
            arr[m.index] = (env.now, m)
        if len(arr) == 2:
            break
env.process(sink())
out.put(Message(src=SRC, dst=DST, index=10, data=ds(1600),
                dst_local_port=10, transfer_mode=TransferMode.DUAL_SIDE))
out.put(Message(src=SRC, dst=DST, index=11, data=ds(1600),
                dst_local_port=10, transfer_mode=TransferMode.SINGLE_SIDE,
                header_bytes=8))
env.run()
check("T9.5 single slower than dual per-task", arr[11][0] > arr[10][0],
      f"{arr[11][0]} vs {arr[10][0]}")
ctrl5 = [e for lk in noc.r2r_links for e in lk.events if e.is_control]
check("T9.5 only single-side produced control packets", len(ctrl5) == 2 * HP,
      f"{len(ctrl5)}")

# T9.6 PE->PE single-side raises
env = simpy.Environment(); noc = make_noc(env)
_, out = noc.attach_local(0, 0, node_id=0)
noc.attach_local(1, 0, node_id=1)
out.put(Message(src=0, dst=1, index=1, data=ds(64),
                transfer_mode=TransferMode.SINGLE_SIDE))
raised = False
try:
    env.run()
except UnsupportedTransferMode:
    raised = True
check("T9.6 PE->PE single-side raises", raised)

# T9.7 PE->GM_WDMA both modes work, single slower
dt7, _ = run_xfer(SRC, 36, SR, 28, 10, 3200, TransferMode.DUAL_SIDE)
st7, _ = run_xfer(SRC, 36, SR, 28, 10, 3200, TransferMode.SINGLE_SIDE)
check("T9.7 PE->GM_WDMA single slower", st7 > dt7, f"{st7} vs {dt7}")

# T9.8 GM_RDMA(32,router28)->PE(0,router0)
dt8, _ = run_xfer(32, DST_NODE := 0, 28, 0, 0, 3200, TransferMode.DUAL_SIDE)
st8, _ = run_xfer(32, 0, 28, 0, 0, 3200, TransferMode.SINGLE_SIDE)
check("T9.8 GM_RDMA->PE single slower", st8 > dt8, f"{st8} vs {dt8}")

# T9.9 header_bytes contributes to total_bytes and latency
m = Message(src=0, dst=36, index=1, data=ds(100), element_bytes=1, header_bytes=8)
check("T9.9 total_bytes includes header", m.total_bytes() == 108,
      f"{m.total_bytes()}")
env = simpy.Environment(); noc = make_noc(env, width=16)
_, o1 = noc.attach_local(0, 0, node_id=0)
s1, _ = noc.attach_local(28, 10, node_id=36)
a1 = []
def sk1():
    m = yield s1.get(); a1.append(env.now)
env.process(sk1())
o1.put(Message(src=0, dst=36, index=1, data=ds(160), element_bytes=1,
               dst_local_port=10, header_bytes=16))
env.run()
# 176 bytes /16 = 11 per link, 9 links + HP per-hop overhead
check("T9.9 header adds latency", a1[0] == (HP + 2) * 11 + HP,
      f"{a1[0]} vs {(HP + 2) * 11 + HP}")

# T9.10 two overlapping single-side transfers contend
env = simpy.Environment(); noc = make_noc(env, width=16)
_, out = noc.attach_local(SR, 0, node_id=SRC)
sin, _ = noc.attach_local(DR, 10, node_id=DST)
last = []
def sink():
    while True:
        m = yield sin.get()
        if not m.is_control:
            last.append(env.now)
        if len(last) == 2:
            break
env.process(sink())
def sender(idx, delay):
    yield env.timeout(delay)
    out.put(Message(src=SRC, dst=DST, index=idx, data=ds(1600),
                    dst_local_port=10,
                    transfer_mode=TransferMode.SINGLE_SIDE, header_bytes=8))
env.process(sender(1, 0))
env.process(sender(2, 0))
env.run()
single_indep, _ = run_xfer(SRC, DST, SR, DR, 10, 1600, TransferMode.SINGLE_SIDE)
check("T9.10 contention beyond one independent xfer",
      last[-1] > single_indep,
      f"last={last[-1]} indep={single_indep}")
check("T9.10 two transfers serialized in order", last[0] < last[-1])

# T9.11 FixPath deterministic latency == XY path length
env = simpy.Environment(); noc = make_noc(env, width=16)
_, out = noc.attach_local(0, 0, node_id=0)
sin, _ = noc.attach_local(28, 10, node_id=36)
arr = []
def sink():
    m = yield sin.get(); arr.append(env.now)
env.process(sink())
out.put(Message(src=0, dst=36, index=1, data=ds(160), element_bytes=1,
                dst_local_port=10, trans_type=TransType.FIXPATH))
env.run()
expected_fp = (HP + 2) * math.ceil((160 + 4) / 16) + HP
check("T9.11 FixPath deterministic == (hops+2)*ceil((160+4)/w)+hops",
      arr[0] == expected_fp, f"{arr[0]} vs {expected_fp}")

print(f"\nFeature 9: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
