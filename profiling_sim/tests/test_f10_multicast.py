"""Feature 10: Multicast / broadcast tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransType,
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


def attach_sink(env, noc, router, port, bucket):
    sink_in, _ = noc.attach_local(router, port, node_id=router)

    def consume():
        while True:
            m = yield sink_in.get()
            if not m.is_control:
                bucket.append((router, port, env.now, m))
    env.process(consume())


def link_map(noc):
    return {(lk.src_id, lk.dst_id): lk for lk in noc.r2r_links}


def multicast(env, noc, src_router, mask, port=0, n=16, width=16,
              trans=TransType.MULTICAST, element_bytes=1, dst_port=None):
    arrivals = []
    eff_mask = mask
    if trans == TransType.BROADCAST and not eff_mask:
        eff_mask = (1 << 32) - 1
    for r in range(32):
        if eff_mask & (1 << r):
            attach_sink(env, noc, r, dst_port if dst_port is not None else port,
                        arrivals)
    _, src_out = noc.attach_local(src_router, 22, node_id=1000 + src_router)
    src_out.put(Message(
        src=1000 + src_router, dst=src_router, index=1,
        data=ds(n), element_bytes=element_bytes,
        dst_local_port=dst_port if dst_port is not None else port,
        trans_type=trans, dst_mask=mask, header_bytes=0))
    env.run()
    return arrivals


print("=== Feature 10: Multicast / Broadcast ===")

# T10.1 single-bit mask behaves as unicast
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, 1 << 3)
check("T10.1 only target 3 received",
      sorted(set(r for r, _, _, _ in arr)) == [3], f"{arr}")
lm = link_map(noc)
path_links = [(0, 1), (1, 2), (2, 3)]
check("T10.1 exactly 3 XY links traversed",
      all(len(lm[p].events) == 1 for p in path_links),
      f"{[len(lm[p].events) for p in path_links]}")
check("T10.1 no off-path links",
      all(len(lm[k].events) == 0 for k in lm if k not in path_links
          and k[0] in (0, 1, 2, 3)))

# T10.2 mask {0,3} from router 1
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 1, (1 << 0) | (1 << 3))
check("T10.2 0 and 3 received",
      sorted(set(r for r, _, _, _ in arr)) == [0, 3])
lm = link_map(noc)
check("T10.2 minimal tree (south + north)",
      len(lm[(1, 0)].events) == 1 and len(lm[(1, 2)].events) == 1
      and len(lm[(2, 3)].events) == 1)

# T10.3 mask {0,28} from router 4
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 4, (1 << 0) | (1 << 28))
got = sorted(set(r for r, _, _, _ in arr))
check("T10.3 0 and 28 received", got == [0, 28], f"{got}")

# T10.4 four corners from 15
env = simpy.Environment(); noc = make_noc(env)
corners = (1 << 0) | (1 << 3) | (1 << 28) | (1 << 31)
arr = multicast(env, noc, 15, corners)
got = sorted(set(r for r, _, _, _ in arr))
check("T10.4 all 4 corners received exactly once",
      got == [0, 3, 28, 31] and len(arr) == 4, f"{got} len={len(arr)}")

# T10.5 broadcast all 32 from 0
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, (1 << 32) - 1)
got = sorted(set(r for r, _, _, _ in arr))
check("T10.5 all 32 routers receive", got == list(range(32)), f"{len(got)}")
check("T10.5 exactly 32 deliveries (no duplicates)", len(arr) == 32,
      f"{len(arr)}")

# T10.6 shared link one copy, parallel branches after fork {19,21} from 0
# X-first routing: both go EAST to r1, then r19 goes EAST, r21 goes NORTH
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, (1 << 19) | (1 << 21), n=16)
got = sorted(set(r for r, _, _, _ in arr))
check("T10.6 both targets received", got == [19, 21], f"{got}")
lm = link_map(noc)
check("T10.6 shared prefix link 0->1 one copy",
      len(lm[(0, 1)].events) == 1, f"{len(lm[(0,1)].events)}")
e_branch = lm[(1, 2)].events
n_branch = lm[(1, 5)].events
check("T10.6 one east branch copy", len(e_branch) == 1)
check("T10.6 one north branch copy", len(n_branch) == 1)
check("T10.6 branches start at same time (parallel)",
      e_branch[0].start_time == n_branch[0].start_time,
      f"{e_branch[0].start_time} vs {n_branch[0].start_time}")

# T10.7 dst_local_port=14 delivers to GM_RDMA port, not PE port
env = simpy.Environment(); noc = make_noc(env)
gm_ports = [28, 29, 30, 31]
gm_arrivals = []
pe0_arrivals = []
for r in gm_ports:
    attach_sink(env, noc, r, 14, gm_arrivals)
    attach_sink(env, noc, r, 0, pe0_arrivals)
_, src_out = noc.attach_local(0, 0, node_id=1000)
gm_mask = (1 << 28) | (1 << 29) | (1 << 30) | (1 << 31)
src_out.put(Message(src=1000, dst=0, index=1, data=ds(16),
                    dst_local_port=14, trans_type=TransType.MULTICAST,
                    dst_mask=gm_mask))
env.run()
check("T10.7 all GM_RDMA port 14 receive",
      sorted(set(r for r, _, _, _ in gm_arrivals)) == gm_ports,
      f"{sorted(set(r for r,_,_,_ in gm_arrivals))}")
check("T10.7 PE port 0 gets nothing", len(pe0_arrivals) == 0,
      f"{len(pe0_arrivals)}")

# T10.8 non-member routers receive nothing
env = simpy.Environment(); noc = make_noc(env)
# attach a sink on a non-member router 5
non_member = []
attach_sink(env, noc, 5, 0, non_member)
arr = multicast(env, noc, 0, (1 << 3) | (1 << 28))
check("T10.8 non-member router 5 gets nothing", len(non_member) == 0,
      f"{len(non_member)}")

# T10.9 max hop count <= diameter 10 (data=16B width=16 -> 1 cycle/link)
env = simpy.Environment(); noc = make_noc(env, width=16)
arr = multicast(env, noc, 0, (1 << 32) - 1, n=16, width=16)
max_t = max(t for _, _, t, _ in arr)
# 10 r2r hops + source local + dest local = 12 (zero per-hop for multicast)
check("T10.9 farthest delivery <= diameter+2 locals",
      max_t <= 12, f"max_t={max_t}")

# T10.10 element_bytes=2: every replica event carries elements*2 bytes
env = simpy.Environment(); noc = make_noc(env)
mask = (1 << 3) | (1 << 28)
arr = multicast(env, noc, 0, mask, n=100, element_bytes=2)
r2r_events = [e for lk in noc.r2r_links for e in lk.events]
check("T10.10 all replica link events 200B",
      all(e.data_size == 200 for e in r2r_events),
      f"{set(e.data_size for e in r2r_events)}")
total_bytes = sum(e.data_size for e in r2r_events)
check("T10.10 total link bytes = #events*200",
      total_bytes == 200 * len(r2r_events), f"{total_bytes}")

# T10.11 bits >= 32 ignored
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, (1 << 35) | (1 << 3))
check("T10.11 only router 3 received (bits>=32 ignored)",
      sorted(set(r for r, _, _, _ in arr)) == [3],
      f"{sorted(set(r for r,_,_,_ in arr))}")

# T10.12 empty mask: no transmission
env = simpy.Environment(); noc = make_noc(env)
sink = []
attach_sink(env, noc, 0, 0, sink)
_, src_out = noc.attach_local(1, 0, node_id=1001)
src_out.put(Message(src=1001, dst=1, index=1, data=ds(16),
                    trans_type=TransType.MULTICAST, dst_mask=0))
env.run()
r2r_events = [e for lk in noc.r2r_links for e in lk.events]
check("T10.12 empty mask: no r2r events", len(r2r_events) == 0,
      f"{len(r2r_events)}")
check("T10.12 empty mask: no local delivery", len(sink) == 0)

# T10.13 source router in mask: local delivery without hairpin
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, (1 << 0) | (1 << 31))
src_local = [a for a in arr if a[0] == 0]
check("T10.13 source 0 local delivery", len(src_local) == 1,
      f"{len(src_local)}")
lm = link_map(noc)
hairpin = [lk for k, lk in lm.items() if k[1] == 0 and lk.events]
check("T10.13 no hairpin link into source", len(hairpin) == 0,
      f"{[(k, len(lk.events)) for k, lk in lm.items() if lk.events]}")

# T10.14 BROADCAST defaults to all-32; explicit mask targets subset
env = simpy.Environment(); noc = make_noc(env)
arr = multicast(env, noc, 0, 0, trans=TransType.BROADCAST)
check("T10.14 BROADCAST (no mask) reaches all 32",
      sorted(set(r for r, _, _, _ in arr)) == list(range(32)),
      f"{len(arr)}")
env = simpy.Environment(); noc = make_noc(env)
dma_mask = (1 << 28) | (1 << 29) | (1 << 30) | (1 << 31)
arr = multicast(env, noc, 0, dma_mask, trans=TransType.BROADCAST)
check("T10.14 BROADCAST with DMA mask targets only DMA routers",
      sorted(set(r for r, _, _, _ in arr)) == [28, 29, 30, 31],
      f"{sorted(set(r for r,_,_,_ in arr))}")

print(f"\nFeature 10: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
