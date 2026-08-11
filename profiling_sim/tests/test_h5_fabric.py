"""Feature H5: Multi-chip AdaLink routing (fabric, inter-chip links, CommID)."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import (
    ArchConfig, CoreConfig, SPMConfig, NoCConfig, ShadowConfig,
)
from profiling_sim.definitions import (
    Message, DimSlice, AdaLinkOp, ProfilingSimError,
)
from profiling_sim.noc import NoC
from profiling_sim.nodes import AdaLinkNode, NoCNode, NodeType
from profiling_sim.fabric import (
    MultiChipFabric, AdaLinkRelocateTable, InterChipLink,
)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def ds(*dims):
    return [DimSlice(start=0, end=d) for d in dims]


class NullMapper:
    def zero_degree(self):
        return []

    def update(self, **kw):
        pass

    def all_tasks_completed(self, core_id):
        return False


def make_noc(env, width=16):
    return NoC(env, NoCConfig(x=8, y=4), deterministic=True).build()


def base_cfg(adalink_latency=0, shadow=None):
    cfg = ArchConfig(
        core=CoreConfig.model_construct(spm=SPMConfig(size=10 ** 9)),
        noc=NoCConfig())
    cfg.nodes.enable_dma = True
    cfg.nodes.adalink_latency = adalink_latency
    if shadow is not None:
        cfg.shadow = shadow
    return cfg


def make_fabric(topology, ranks=2, link_latency=5, credits=64, atomic_latency=4,
                adalink_latency=0, shadow=None, deterministic=True):
    env = simpy.Environment()
    rank_ids = list(range(ranks)) if isinstance(ranks, int) else list(ranks)
    configs = {r: base_cfg(adalink_latency, shadow) for r in rank_ids}
    mappers = {r: NullMapper() for r in rank_ids}
    fab = MultiChipFabric(env, configs, mappers, topology,
                          link_latency=link_latency, credits=credits,
                          atomic_latency=atomic_latency,
                          deterministic=deterministic)
    return env, fab


def inject(env, chip, msg, src_core=0):
    def p():
        yield chip.cores[src_core].data_out.put(msg)
    env.process(p())


# =========================================================
print("=== Relocate table ===")

# T-H5.1
tbl = AdaLinkRelocateTable()
check("T-H5.1 20-entry table; unset lookup raises",
      len(tbl._entries) == 0)
try:
    tbl.lookup(0); raised = False
except KeyError:
    raised = True
check("T-H5.1 unset lookup KeyError", raised)

# T-H5.2
tbl.set(0, 3, 2)
check("T-H5.2 set/lookup round-trip", tbl.lookup(0) == (3, 2))
tbl.set_self(1, 0)
check("T-H5.2 entry 19 holds self", tbl.lookup(AdaLinkRelocateTable.SELF) == (1, 0))

# T-H5.3
def expect_ve(fn):
    try:
        fn(); return False
    except ValueError:
        return True


check("T-H5.3 port>3 raises", expect_ve(lambda: tbl.set(1, 1, 4)))
check("T-H5.3 port<0 raises", expect_ve(lambda: tbl.set(2, 1, -1)))
check("T-H5.3 rank<0 raises", expect_ve(lambda: tbl.set(3, -1, 0)))
check("T-H5.3 duplicate wiring raises", expect_ve(lambda: tbl.set(0, 9, 9)))
check("T-H5.3 entry 18 reserved raises",
      expect_ve(lambda: tbl.lookup(AdaLinkRelocateTable.RESERVED)))

# T-H5.4
check("T-H5.4 pack rank<<2 | port",
      AdaLinkRelocateTable.pack(5, 3) == (5 << 2) | 3)
check("T-H5.4 unpack reverses",
      AdaLinkRelocateTable.unpack(AdaLinkRelocateTable.pack(7, 2)) == (7, 2))
check("T-H5.4 port 3 only bits [1:0]",
      AdaLinkRelocateTable.pack(1, 3) & 0x3 == 3
      and AdaLinkRelocateTable.pack(1, 3) >> 2 == 1)

# =========================================================
print("\n=== Message ===")

# T-H5.5
m0 = Message(src=0, dst=1, index=0, data=ds(16))
check("T-H5.5 default dst_rank=0", m0.dst_rank == 0)
check("T-H5.5 default adalink_op WRITE", m0.adalink_op == int(AdaLinkOp.WRITE))
check("T-H5.5 default imm=0, imm_bytes=0", m0.imm == 0 and m0.imm_bytes == 0)

# T-H5.6
try:
    Message(src=0, dst=1, index=0, data=ds(16), dst_rank=-1); d_ok = False
except Exception:
    d_ok = True
check("T-H5.6 dst_rank<0 raises", d_ok)
try:
    Message(src=0, dst=1, index=0, data=ds(16), imm=-1); i_ok = False
except Exception:
    i_ok = True
check("T-H5.6 negative imm raises", i_ok)
unk = Message(src=0, dst=1, index=0, data=ds(16), adalink_op=0x7F)
check("T-H5.6 unknown adalink_op accepted", unk.adalink_op == 0x7F)

# T-H5.7
for op in (AdaLinkOp.WRITE_WITH_IMM2, AdaLinkOp.WRITE_SUM_WITH_IMM2,
           AdaLinkOp.WRITE_MAX_WITH_IMM2):
    m = Message(src=0, dst=1, index=0, data=ds(16), adalink_op=int(op))
    check(f"T-H5.7 {op.name} imm_bytes==8", m.imm_bytes == 8)
check("T-H5.7 plain WRITE imm_bytes==0", m0.imm_bytes == 0)
m_imm = Message(src=0, dst=1, index=0, data=ds(16),
                adalink_op=int(AdaLinkOp.WRITE_WITH_IMM2), header_bytes=4)
check("T-H5.7 byte_size unchanged by imm", m_imm.byte_size() == 16)
check("T-H5.7 total_bytes_with_imm adds imm+header",
      m_imm.total_bytes_with_imm() == 16 + 4 + 8)

# =========================================================
print("\n=== InterChipLink ===")

class _FakePeer:
    def __init__(self, env):
        self.interchip_in = simpy.Store(env)


# T-H5.8
env = simpy.Environment()
link = InterChipLink(env, latency=5, credits=64)
peer = _FakePeer(env)
link.bind(peer)
arrivals = []
def snd():
    yield env.process(link.send(Message(src=0, dst=1, index=0, data=ds(16))))
def drv():
    env.process(snd())
    yield env.timeout(0)
env.process(drv())
def collect():
    msg = yield peer.interchip_in.get()
    arrivals.append(env.now)
env.process(collect())
env.run()
check("T-H5.8 single message latency L=5", arrivals == [5], f"{arrivals}")

# T-H5.9 credits=1
env = simpy.Environment()
link = InterChipLink(env, latency=5, credits=1)
peer = _FakePeer(env)
link.bind(peer)
times = []
def sender(i):
    yield env.process(link.send(Message(src=0, dst=1, index=i, data=ds(16))))
    times.append(env.now)
def collector():
    for _ in range(2):
        m = yield peer.interchip_in.get()
        link.return_credit()
env.process(sender(0)); env.process(sender(1)); env.process(collector())
env.run()
check("T-H5.9 credits=1 serializes (5,10)", times == [5, 10], f"{times}")

# T-H5.10
check("T-H5.10 credits<1 raises",
      expect_ve(lambda: InterChipLink(simpy.Environment(), 5, credits=0)))
env = simpy.Environment()
lk = InterChipLink(env, latency=0, credits=2)
lk.return_credit(); lk.return_credit(); lk.return_credit()
check("T-H5.10 return_credit capped at capacity", lk.credits.level == 2)

# =========================================================
print("\n=== Egress routing ===")

# T-H5.11 cross-chip msg reaches egress AdaLink and crosses
env, fab = make_fabric([(0, 0, 1, 0)], link_latency=0)
eg = fab.chips[0].nodes[48]
ing = fab.chips[1].nodes[48]
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=1, data=ds(16), dst_rank=1))
env.run(until=500)
check("T-H5.11 egress AdaLink saw message",
      any(m.index == 1 for _, m in eg.received))
check("T-H5.11 message crossed to chip1 ingress",
      any(m.index == 1 for _, m in ing.interchip_events))
check("T-H5.11 egress SEND counter incremented", eg.commids["SEND"][1] >= 1)

# T-H5.12 port / fields preserved
crossed = [m for _, m in ing.interchip_events if m.index == 1][0]
check("T-H5.12 final dst preserved", crossed.dst == 31)
check("T-H5.12 final dst_local_port preserved (not mutated to 16)",
      crossed.dst_local_port == 0)
check("T-H5.12 dst_rank on wire is remote (1)", crossed.dst_rank == 1)

# T-H5.13 unknown rank raises
env, fab = make_fabric([(0, 0, 1, 0)], link_latency=0)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=2, data=ds(16), dst_rank=99))
try:
    env.run(until=500); h513 = False
except ProfilingSimError:
    h513 = True
check("T-H5.13 unknown dst_rank raises ProfilingSimError", h513)

# T-H5.13b cross-chip sync raises
env, fab = make_fabric([(0, 0, 1, 0)], link_latency=0)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=3, data=ds(16), dst_rank=1, sync=True))
try:
    env.run(until=500); h513b = False
except ProfilingSimError:
    h513b = True
check("T-H5.13b cross-chip sync raises ProfilingSimError", h513b)

# =========================================================
print("\n=== End-to-end cross-chip ===")

def e2e(op=AdaLinkOp.WRITE, imm=0, link_latency=5, atomic_latency=4,
        shadow=None, is_aiu=False, dst_rank=1, dst=31):
    env, fab = make_fabric([(0, 0, 1, 0)], link_latency=link_latency,
                           atomic_latency=atomic_latency, shadow=shadow)
    probe = NoCNode(env, 900, NodeType.PE, dst, [9], fab.chips[1].noc)
    inject(env, fab.chips[0],
           Message(src=0, dst=dst, index=1, data=ds(16), dst_rank=dst_rank,
                   dst_local_port=9, adalink_op=int(op), imm=imm,
                   is_aiu=is_aiu))
    env.run(until=2000)
    arr = probe.received[0][0] if probe.received else None
    return arr, fab, probe


# T-H5.14 plain WRITE arrival == 24 + L
arr, fab, probe = e2e(AdaLinkOp.WRITE)
check("T-H5.14 plain WRITE arrives at 24+L=29", arr == 29, f"{arr}")
delivered = probe.received[0][1]
check("T-H5.14 fields preserved",
      delivered.dst == 31 and delivered.index == 1 and delivered.value == 0)

# T-H5.15 atomics
arr_s, fab_s, _ = e2e(AdaLinkOp.WRITE_SUM)
arr_m, fab_m, _ = e2e(AdaLinkOp.WRITE_MAX)
arr_w, _, _ = e2e(AdaLinkOp.WRITE)
check("T-H5.15 WRITE_SUM arrives at 24+L+A=33", arr_s == 33, f"{arr_s}")
check("T-H5.15 WRITE_MAX arrives at 33", arr_m == 33, f"{arr_m}")
check("T-H5.15 plain WRITE no atomic latency (29)", arr_w == 29)
sum_ev = [e for e in fab_s.chips[1].nodes[48].atomic_events]
max_ev = [e for e in fab_m.chips[1].nodes[48].atomic_events]
check("T-H5.15 SUM atomic event recorded",
      len(sum_ev) == 1 and sum_ev[0]["op"] == int(AdaLinkOp.WRITE_SUM))
check("T-H5.15 MAX atomic event recorded",
      len(max_ev) == 1 and max_ev[0]["op"] == int(AdaLinkOp.WRITE_MAX))

# T-H5.16 immediates
arr_imm, _, _ = e2e(AdaLinkOp.WRITE_WITH_IMM2)
check("T-H5.16 WITH_IMM2 adds H+H'+4=14 -> 43", arr_imm == 43, f"{arr_imm}")

# T-H5.17 3-chip chain
env, fab = make_fabric([(0, 0, 1, 0), (1, 1, 2, 0)], ranks=3,
                       link_latency=0)
probe1 = NoCNode(env, 901, NodeType.PE, 31, [9], fab.chips[1].noc)
probe2 = NoCNode(env, 902, NodeType.PE, 31, [9], fab.chips[2].noc)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=1, data=ds(16), dst_rank=1,
               dst_local_port=9))
env.run(until=1000)
check("T-H5.17 0->1 delivers", len(probe1.received) == 1)
env, fab = make_fabric([(0, 0, 1, 0), (1, 1, 2, 0)], ranks=3,
                       link_latency=0)
probe2 = NoCNode(env, 902, NodeType.PE, 31, [9], fab.chips[2].noc)
inject(env, fab.chips[1],
       Message(src=0, dst=31, index=2, data=ds(16), dst_rank=2,
               dst_local_port=9))
env.run(until=1000)
check("T-H5.17 1->2 delivers",
      len(fab.chips[2].nodes[48].interchip_events) >= 1
      and len(probe2.received) == 1)
env, fab = make_fabric([(0, 0, 1, 0), (1, 1, 2, 0)], ranks=3,
                       link_latency=0)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=3, data=ds(16), dst_rank=2,
               dst_local_port=9))
try:
    env.run(until=1000); chain_bad = False
except ProfilingSimError:
    chain_bad = True
check("T-H5.17 0->2 raises (no direct neighbour)", chain_bad)

# T-H5.18 ALL_ADALINK broadcast
env, fab = make_fabric([(0, 0, 1, 0), (0, 1, 2, 0)], ranks=4,
                       link_latency=0)
def bcast():
    msg = Message(src=28, dst=28, index=7, data=ds(16),
                  dst_rank=0, dst_local_port=21)
    yield fab.chips[0].cores[28].data_out.put(msg)
env.process(bcast())
env.run(until=1000)
check("T-H5.18 wired neighbour 1 received",
      any(m.index == 7 for _, m in fab.chips[1].nodes[48].interchip_events))
check("T-H5.18 wired neighbour 2 received",
      any(m.index == 7 for _, m in fab.chips[2].nodes[48].interchip_events))
check("T-H5.18 non-wired chip 3 did NOT receive",
      not any(m.index == 7
              for _, m in fab.chips[3].nodes[48].interchip_events))

# =========================================================
print("\n=== CommID sync ===")

# T-H5.19 wait_comm_id blocking
env = simpy.Environment(); noc = make_noc(env)
ada = AdaLinkNode(env, 48, 28, [16], noc)
def releaser():
    for t in (1, 2, 3):
        yield env.timeout(1)
        ada.release_comm_id(5, "PRODUCE")
unblocked = []
def waiter():
    yield env.process(ada.wait_comm_id(5, "PRODUCE", expected=3))
    unblocked.append(env.now)
env.process(waiter()); env.process(releaser()); env.run()
check("T-H5.19 wait unblocks at third release (t=3)", unblocked == [3],
      f"{unblocked}")

# fast path
env = simpy.Environment(); noc = make_noc(env)
ada = AdaLinkNode(env, 48, 28, [16], noc)
ada.release_comm_id(5, "PRODUCE")
fp = []
def fpwait():
    yield env.process(ada.wait_comm_id(5, "PRODUCE", expected=1))
    fp.append(env.now)
env.process(fpwait()); env.run()
check("T-H5.19 already-satisfied fast path returns at t=0", fp == [0], f"{fp}")

# two concurrent waiters
env = simpy.Environment(); noc = make_noc(env)
ada = AdaLinkNode(env, 48, 28, [16], noc)
woke = []
def w(i):
    yield env.process(ada.wait_comm_id(9, "CREDIT", expected=1))
    woke.append((i, env.now))
env.process(w(0)); env.process(w(1))
def rel():
    yield env.timeout(5)
    ada.release_comm_id(9, "CREDIT")
env.process(rel()); env.run()
check("T-H5.19 two concurrent waiters both wake",
      sorted(woke) == [(0, 5), (1, 5)], f"{woke}")

# T-H5.20 SEND/RECEIVE + credit refill
env, fab = make_fabric([(0, 0, 1, 0)], link_latency=0, credits=4)
probe = NoCNode(env, 900, NodeType.PE, 31, [9], fab.chips[1].noc)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=4, data=ds(16), dst_rank=1,
               dst_local_port=9))
env.run(until=500)
eg = fab.chips[0].nodes[48]
ing = fab.chips[1].nodes[48]
check("T-H5.20 SEND counter on egress", eg.commids["SEND"][4] == 1)
check("T-H5.20 RECEIVE counter on ingress", ing.commids["RECEIVE"][4] == 1)
# link egress->ingress is eg._peer_link; credit should be refilled
check("T-H5.20 credit refilled after transfer",
      eg._peer_link.credits.level == eg._peer_link.capacity)

# T-H5.20b AIU terminates at ingress (no re-inject)
env, fab = make_fabric([(0, 0, 1, 0)], link_latency=0,
                       shadow=ShadowConfig(enabled=True, aci_func=0,
                                           aci_aiu=3))
probe = NoCNode(env, 900, NodeType.PE, 31, [9], fab.chips[1].noc)
inject(env, fab.chips[0],
       Message(src=0, dst=31, index=5, data=ds(16), dst_rank=1,
               dst_local_port=9, is_aiu=True))
env.run(until=500)
ing = fab.chips[1].nodes[48]
check("T-H5.20b AIU message recorded at ingress",
      any(m.index == 5 for _, m in ing.interchip_events))
check("T-H5.20b AIU NOT re-injected to probe", len(probe.received) == 0)
check("T-H5.20b AIU ACI entry 92 on ingress",
      any(e["entry_id"] == 92 for e in ing.shadow_events))

# =========================================================
print("\n=== Backward compatibility & integration ===")

# T-H5.21 single-chip stub sinks in adalink_latency
env = simpy.Environment(); noc = make_noc(env)
ada = AdaLinkNode(env, 48, 28, [16], noc, latency=8)
done = []
def h():
    s = env.now
    yield env.process(ada.handle(16, Message(src=0, dst=48, index=1,
                                             data=ds(16))))
    done.append(env.now - s)
env.process(h()); env.run()
check("T-H5.21 single-chip sink latency 8", done == [8], f"{done}")
check("T-H5.21 no peer/interchip events",
      ada._peer_link is None and ada.interchip_events == []
      and ada.atomic_events == [])

# T-H5.22 shadow adds 2k across the two ACI endpoints
arr_off, _, _ = e2e(AdaLinkOp.WRITE, link_latency=0,
                    shadow=ShadowConfig(enabled=True, aci_func=0))
arr_on, fab_on, _ = e2e(AdaLinkOp.WRITE, link_latency=0,
                        shadow=ShadowConfig(enabled=True, aci_func=3))
check("T-H5.22 aci_func=3 adds 2*3=6 cycles",
      arr_on - arr_off == 6, f"{arr_on} vs {arr_off}")
func_ids = [e["entry_id"] for e in fab_on.shadow_events if e["entry_id"] == 68]
check("T-H5.22 FUNC entry 68 appears once per side (2 total)",
      len(func_ids) == 2, f"{len(func_ids)}")
# AIU shows 92 on each side
arr_a, fab_a, _ = e2e(AdaLinkOp.WRITE, link_latency=0, is_aiu=True,
                      shadow=ShadowConfig(enabled=True, aci_aiu=2))
aiu_ids = [e["entry_id"] for e in fab_a.shadow_events if e["entry_id"] == 92]
check("T-H5.22 AIU entry 92 appears once per side (2 total)",
      len(aiu_ids) == 2, f"{len(aiu_ids)}")

# T-H5.23 disabled single-chip Arch builds and runs with defaults
cfg = ArchConfig(core=CoreConfig.model_construct(spm=SPMConfig(size=10 ** 9)),
                 noc=NoCConfig())
cfg.nodes.enable_dma = False
cfg.nodes.enable_adalink = False
from profiling_sim.architecture import Arch
arch = Arch(cfg, NullMapper(), deterministic=True)
check("T-H5.23 default Arch creates its own env", arch.env is not None)
check("T-H5.23 default rank==0", arch.rank == 0 and arch.noc.rank == 0)
check("T-H5.23 default egress table empty", arch.noc.egress == {})
arch.env.run(until=50)
check("T-H5.23 default single-chip Arch steps without error", True)

# =========================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
sys.exit(1 if failed else 0)
