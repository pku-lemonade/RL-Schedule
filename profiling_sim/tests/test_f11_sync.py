"""Feature 11: Synchronization framework tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransType, TransferMode,
)
from profiling_sim.noc import NoC
from profiling_sim.nodes import AdaLinkNode, NodeType, attach_nodes
from profiling_sim.memory import Memory, DMANode

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


def endpoint(env, noc, router, node_id, port=0):
    rin, rout = noc.attach_local(router, port, node_id=node_id)
    received = []

    def consume():
        while True:
            m = yield rin.get()
            received.append((env.now, m))
    env.process(consume())
    return rout, received


def hops(a, b):
    return abs(a // 4 - b // 4) + abs(a % 4 - b % 4)


def unicast_sync(src, dst, sr, dr, n=1600, sync=True, dport=0, sport=0):
    env = simpy.Environment(); noc = make_noc(env)
    src_out, src_recv = endpoint(env, noc, sr, src, port=sport)
    _, dst_recv = endpoint(env, noc, dr, dst, port=dport)
    src_out.put(Message(src=src, dst=dst, index=1, data=ds(n),
                        element_bytes=1, dst_local_port=dport,
                        src_local_port=sport, sync=sync, header_bytes=0))
    env.run()
    data_dst = [t for t, m in dst_recv if not m.is_control]
    acq = [t for t, m in src_recv if m.is_control and m.sync]
    return noc, (data_dst[0] if data_dst else -1), (max(acq) if acq else None)


print("=== Feature 11: Synchronization framework ===")

HP = hops(0, 28)

# T11.1 outer sync enabled: completion = data delivery + acquire return
noc_s, dd_s, comp_s = unicast_sync(0, 36, 0, 28, n=1600, sync=True, dport=10)
check("T11.1 sync transfer delivered", dd_s > 0, f"{dd_s}")
check("T11.1 sync completion after delivery",
      comp_s is not None and comp_s > dd_s, f"{comp_s} vs {dd_s}")
check("T11.1 deterministic handshake overhead == 2*hops",
      comp_s - dd_s == 2 * HP, f"overhead={comp_s - dd_s} vs {2*HP}")

# T11.2 sync disabled: makespan equals dual-side baseline, no acquire
noc_n, dd_n, comp_n = unicast_sync(0, 36, 0, 28, n=1600, sync=False, dport=10)
baseline = (HP + 2) * (1600 // 16) + HP
check("T11.2 no-sync data makespan == baseline",
      dd_n == baseline, f"{dd_n} vs {baseline}")
check("T11.2 no acquire without sync", comp_n is None)

# T11.3 constant overhead independent of payload size
_, dd_a, comp_a = unicast_sync(0, 36, 0, 28, n=160, sync=True, dport=10)
_, dd_b, comp_b = unicast_sync(0, 36, 0, 28, n=16000, sync=True, dport=10)
oh_a, oh_b = comp_a - dd_a, comp_b - dd_b
check("T11.3 overhead constant (160B vs 16000B)",
      oh_a == oh_b == 2 * HP, f"{oh_a} vs {oh_b}")

# T11.4 inner sync: DMA channel serialization
def two_dma(channels):
    env = simpy.Environment(); noc = make_noc(env)
    mem = Memory(env, "GM", 2**40, 0)
    wdma = DMANode(env, 36, NodeType.GM_WDMA, 28, [10, 11], noc,
                   memory=mem, engine_width=16, channels=channels, is_read=False)
    finishes = []

    def send_two():
        p1 = env.process(wdma.transfer(160))
        p2 = env.process(wdma.transfer(160))
        yield p1 & p2
        finishes.append(env.now)
    env.process(send_two()); env.run()
    return finishes[0]

ser = two_dma(1)
par = two_dma(2)
check("T11.4 single channel serializes (20 cyc)", ser == 20, f"{ser}")
check("T11.4 two channels parallelize (10 cyc)", par == 10, f"{par}")

# T11.5 sync packets (1 flit each) appear on links
sync_events = [e for lk in noc_s.r2r_links for e in lk.events
               if e.is_sync and e.data_size == 16]
check("T11.5 sync flit events == 2*hops",
      len(sync_events) == 2 * HP, f"{len(sync_events)} vs {2*HP}")
check("T11.5 all sync flits 16B",
      all(e.data_size == 16 for e in sync_events))
nsync_events = [e for lk in noc_n.r2r_links for e in lk.events
                if e.is_sync and e.data_size == 16]
check("T11.5 no sync flits without flag", len(nsync_events) == 0,
      f"{len(nsync_events)}")

# T11.6 sender completion >= receiver ready
check("T11.6 sender completion >= receiver delivery",
      comp_s >= dd_s, f"{comp_s} vs {dd_s}")

# T11.7 per-transfer flag: only sync task carries handshake
env = simpy.Environment(); noc = make_noc(env)
so, sr = endpoint(env, noc, 0, 0)
endpoint(env, noc, 28, 36, port=10)
so.put(Message(src=0, dst=36, index=1, data=ds(160), dst_local_port=10,
               sync=False))
so.put(Message(src=0, dst=36, index=2, data=ds(160), dst_local_port=10,
               sync=True))
env.run()
flits = [e for lk in noc.r2r_links for e in lk.events
         if e.is_sync and e.data_size == 16]
check("T11.7 only one sync handshake present",
      len(flits) == 2 * HP, f"{len(flits)}")

# T11.8 pattern A (PE->DMA) and B (DMA->PE)
_, ddA, cA = unicast_sync(0, 36, 0, 28, n=320, sync=True, dport=10)
_, ddB, cB = unicast_sync(32, 0, 28, 0, n=320, sync=True)
check("T11.8 pattern A handshake", cA is not None and cA > ddA)
check("T11.8 pattern B handshake", cB is not None and cB > ddB)

# T11.9 patterns C (PE->PE), D (DMA->DMA), E (multicast)
_, ddC, cC = unicast_sync(0, 31, 0, 31, n=320, sync=True)
check("T11.9 pattern C PE->PE handshake (allowed for sync)",
      cC is not None and cC > ddC)
_, ddD, cD = unicast_sync(32, 44, 28, 0, n=320, sync=True)
check("T11.9 pattern D DMA->DMA handshake",
      cD is not None and cD > ddD, f"dd={ddD} c={cD}")

# pattern E: multicast GM_RDMA(32,router28) -> PEs {0,3}
env = simpy.Environment(); noc = make_noc(env)
src_out, src_recv = endpoint(env, noc, 28, 32, port=14)
_, r0 = endpoint(env, noc, 0, 0)
_, r3 = endpoint(env, noc, 3, 3)
src_out.put(Message(src=32, dst=28, index=1, data=ds(160), element_bytes=1,
                    dst_local_port=0, src_local_port=14, sync=True,
                    trans_type=TransType.MULTICAST,
                    dst_mask=(1 << 0) | (1 << 3)))
env.run()
data_recv = len([1 for _, m in r0 + r3 if not m.is_control])
acq_cnt = len([1 for _, m in src_recv if m.is_control and m.sync])
check("T11.9 pattern E multicast delivered to 2 PEs", data_recv == 2,
      f"{data_recv}")
check("T11.9 pattern E one acquire per member", acq_cnt == 2, f"{acq_cnt}")

# T11.10 mutual cross-send with sync, no deadlock
env = simpy.Environment(); noc = make_noc(env)
o0, r0 = endpoint(env, noc, 0, 0)
o31, r31 = endpoint(env, noc, 31, 31)
o0.put(Message(src=0, dst=31, index=1, data=ds(160), sync=True))
o31.put(Message(src=31, dst=0, index=2, data=ds(160), sync=True))
env.run()
check("T11.10 both data delivered",
      any(not m.is_control for _, m in r0)
      and any(not m.is_control for _, m in r31))
check("T11.10 both acquires returned (no deadlock)",
      any(m.is_control and m.sync for _, m in r0)
      and any(m.is_control and m.sync for _, m in r31))

# T11.11 CommID stub: 5 counters x 64 entries
env = simpy.Environment(); noc = make_noc(env)
ada = AdaLinkNode(env, 48, 28, [16], noc)
check("T11.11 5 counter types", len(ada.commids) == 5,
      f"{list(ada.commids)}")
check("T11.11 64 entries each",
      all(len(v) == 64 for v in ada.commids.values()))
ada.release_comm_id(3, "PRODUCE")
ada.release_comm_id(3, "PRODUCE")
v = ada.acquire_comm_id(3, "CREDIT")
check("T11.11 release increments counter", ada.commids["PRODUCE"][3] == 2)
check("T11.11 acquire returns/updates without blocking",
      ada.commids["CREDIT"][3] == 1 and v == 1)
check("T11.11 other entries untouched", ada.commids["PRODUCE"][0] == 0
      and ada.commids["PRODUCE"][4] == 0)

# T11.12 AIU download -> scalar outer sync (pattern F)
env = simpy.Environment(); noc = make_noc(env)
gmem = Memory(env, "GM", 2**40, 0)
rdma = DMANode(env, 32, NodeType.GM_RDMA, 28, [14], noc,
               memory=gmem, engine_width=16, channels=1, is_read=True)
_, scalar_recv = endpoint(env, noc, 8, 8, port=0)
aiu_in = rdma.data_in[rdma.aiu_port]
aiu_in.put(Message(src=8, dst=32, index=1, data=ds(16), element_bytes=1,
                   is_aiu=True))
env.run()
check("T11.12 AIU download recorded", len(rdma.aiu_events) == 1)
scalar_sync = [m for _, m in scalar_recv if m.is_control and m.sync]
check("T11.12 scalar sync packet received at PE8",
      len(scalar_sync) == 1, f"{len(scalar_sync)}")
sync_to_scalar = [e for lk in noc.r2r_links for e in lk.events
                  if e.is_sync and e.dst_id in (8, 9, 10, 11)]
check("T11.12 sync flit traversed NoC toward scalar",
      len(sync_to_scalar) > 0, f"{len(sync_to_scalar)}")

print(f"\nFeature 11: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
