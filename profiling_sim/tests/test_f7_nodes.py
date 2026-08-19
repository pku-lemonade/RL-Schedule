"""Feature 7: Non-PE node attachment (DMA / AdaLink)."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig, ArchConfig, CoreConfig, SPMConfig
from profiling_sim.definitions import Message, DimSlice
from profiling_sim.noc import NoC
from profiling_sim.nodes import (
    NodeType, DataNocLocalId, node_type, type_local_id, global_node_id,
    is_pe, is_gm_rdma, is_gm_wdma, is_ddr_rdma, is_ddr_wdma, is_adalink,
    route_pos, get_route_id, get_data_noc_local_id, attach_nodes,
    GM_RDMA_ROUTERS, ADALINK_ATTACH,
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


def make_noc(env, width=16):
    cfg = NoCConfig(x=4, y=8, router=RouterConfig(),
                    link=LinkConfig(width=width, delay=0))
    return NoC(env, cfg, deterministic=True).build()


print("=== Feature 7: Non-PE node attachment ===")

env = simpy.Environment()
noc = make_noc(env)
nodes = attach_nodes(env, noc)

# T7.1 counts
check("T7.1 34 non-PE nodes", len(nodes) == 34, f"{len(nodes)}")
pe = [g for g in range(66) if is_pe(g)]
check("T7.1 32 PE ids", len(pe) == 32 and pe[0] == 0 and pe[-1] == 31)
check("T7.1 4 GM_RDMA", sum(1 for g in nodes if is_gm_rdma(g)) == 4)
check("T7.1 4 GM_WDMA", sum(1 for g in nodes if is_gm_wdma(g)) == 4)
check("T7.1 4 DDR_RDMA", sum(1 for g in nodes if is_ddr_rdma(g)) == 4)
check("T7.1 4 DDR_WDMA", sum(1 for g in nodes if is_ddr_wdma(g)) == 4)
check("T7.1 18 AdaLink", sum(1 for g in nodes if is_adalink(g)) == 18)

# T7.2 global ids / types
check("T7.2 id32 GM_RDMA", node_type(32) == NodeType.GM_RDMA)
check("T7.2 id39 GM_WDMA", node_type(39) == NodeType.GM_WDMA)
check("T7.2 id43 DDR_RDMA", node_type(43) == NodeType.DDR_RDMA)
check("T7.2 id47 DDR_WDMA", node_type(47) == NodeType.DDR_WDMA)
check("T7.2 id65 ADALINK", node_type(65) == NodeType.ADALINK)
check("T7.2 local_id of 39 = 3", type_local_id(39) == 3)
check("T7.2 local_id of 65 = 17", type_local_id(65) == 17)
check("T7.2 global_node_id(ADALINK,17)=65",
      global_node_id(NodeType.ADALINK, 17) == 65)

# T7.3 GM_RDMA routers/ports
for i, r in enumerate(GM_RDMA_ROUTERS):
    gid = global_node_id(NodeType.GM_RDMA, i)
    n = nodes[gid]
    check(f"T7.3 GM_RDMA{i} router {r} port14",
          n.router_id == r and n.ports == [DataNocLocalId.GM_RDMA],
          f"{n.router_id} {n.ports}")

# T7.4 DDR_RDMA routers
ddr_routers = [0, 28, 3, 31]
for i, r in enumerate(ddr_routers):
    n = nodes[global_node_id(NodeType.DDR_RDMA, i)]
    check(f"T7.4 DDR_RDMA{i} router {r}",
          n.router_id == r and n.ports == [DataNocLocalId.DDR_RDMA])

# T7.5 WDMA dual channels
gmw0 = nodes[global_node_id(NodeType.GM_WDMA, 0)]
check("T7.5 GM_WDMA0 ports 10,11",
      gmw0.ports == [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1])
check("T7.5 two independent in links",
      gmw0.data_in[10] is not gmw0.data_in[11])
check("T7.5 two independent out links",
      gmw0.data_out[10] is not gmw0.data_out[11])
ddrw0 = nodes[global_node_id(NodeType.DDR_WDMA, 0)]
check("T7.5 DDR_WDMA0 ports 3,4",
      ddrw0.ports == [DataNocLocalId.DDR_WDMA_CH0, DataNocLocalId.DDR_WDMA_CH1])

# T7.6 AdaLink router map
expect = []
for router, ports in ADALINK_ATTACH:
    for p in ports:
        expect.append((router, p))
for i, (r, p) in enumerate(expect):
    n = nodes[global_node_id(NodeType.ADALINK, i)]
    check(f"T7.6 adalink{i} r{r} p{int(p)}",
          n.router_id == r and n.ports == [p], f"{n.router_id} {n.ports}")

# T7.7 router 28 data-plane ports = 13 (PE port 0 + 12 non-PE)
noc.attach_local(28, 0, node_id=28)
r28 = noc.routers[28].local_ports
check("T7.7 router28 has 13 local ports", len(r28) == 13,
      f"{len(r28)}: {sorted(int(k) for k in r28.keys())}")
expected_ports = {0, 3, 4, 7, 10, 11, 14, 16, 17, 18, 19, 20, 21}
check("T7.7 router28 port set correct",
      {int(k) for k in r28.keys()} == expected_ports,
      f"{sorted(int(k) for k in r28.keys())}")

# T7.8 GM_RDMA (r28) injects to PE0; path 28->24->...->0
gm = nodes[32]
pe_in, _ = noc.attach_local(0, 0, node_id=0)
got = []
def consume():
    while True:
        m = yield pe_in.get()
        got.append((env.now, m))
env.process(consume())
def inject():
    gm.send(14, Message(src=32, dst=0, index=1, data=ds(16)))
    yield env.timeout(200)
env.process(inject())
env.run(until=200)
check("T7.8 PE0 received from GM_RDMA", len(got) == 1, f"{len(got)}")
path = []
for l in noc.r2r_links:
    for e in l.events:
        if e.index == 1:
            path.append((e.src_id, e.dst_id))
path.sort(key=lambda x: x[0])
seq = [28, 24, 20, 16, 12, 8, 4, 0]
expected_edges = list(zip(seq, seq[1:]))
check("T7.8 path is 7 row-axis hops",
      sorted(path) == sorted(expected_edges), f"{path}")

# T7.9 PE0 sends to GM_WDMA r28 ch0 only
env2 = simpy.Environment(); noc2 = make_noc(env2)
nodes2 = attach_nodes(env2, noc2)
_, pe_out = noc2.attach_local(0, 0, node_id=0)
gmw = nodes2[36]
def s2():
    pe_out.put(Message(src=0, dst=36, index=2, data=ds(8), dst_local_port=10))
    yield env2.timeout(200)
env2.process(s2())
env2.run(until=200)
ch0 = [t for (t, m) in gmw.received if m.index == 2 and m.dst_local_port == 10]
ch1 = [t for (t, m) in gmw.received if m.index == 2 and m.dst_local_port == 11]
check("T7.9 ch0 received", len(ch0) == 1)
check("T7.9 ch1 did not", len(ch1) == 0)

# T7.10 AdaLink receives
env3 = simpy.Environment(); noc3 = make_noc(env3)
nodes3 = attach_nodes(env3, noc3)
_, pe_out = noc3.attach_local(0, 0, node_id=0)
ada = nodes3[48]
def s3():
    pe_out.put(Message(src=0, dst=48, index=3, data=ds(8), dst_local_port=16))
    yield env3.timeout(200)
env3.process(s3())
env3.run(until=200)
check("T7.10 AdaLink received msg", len(ada.received) >= 1)
check("T7.10 AdaLink events recorded", len(ada.events) >= 1)

# T7.11 helper predicates
check("T7.11 is_pe(5)", is_pe(5))
check("T7.11 is_gm_rdma(33)", is_gm_rdma(33))
check("T7.11 is_gm_wdma(38)", is_gm_wdma(38))
check("T7.11 is_ddr_rdma(42)", is_ddr_rdma(42))
check("T7.11 is_ddr_wdma(45)", is_ddr_wdma(45))
check("T7.11 is_adalink(50)", is_adalink(50))

# T7.12 config omit AdaLink -> 16 non-PE nodes
env4 = simpy.Environment(); noc4 = make_noc(env4)
nodes4 = attach_nodes(env4, noc4, include_adalink=False)
check("T7.12 16 DMA nodes without AdaLink", len(nodes4) == 16, f"{len(nodes4)}")
check("T7.12 no ALL_ADALINK port on r28",
      21 not in noc4.routers[28].local_ports)

# T7.13 negative cases
try:
    noc4.attach_local(0, 7, node_id=999)
    check("T7.13 duplicate port raises", False)
except Exception:
    check("T7.13 duplicate port raises", True)
try:
    noc4.attach_local(999, 5, node_id=1)
    check("T7.13 invalid router raises", False)
except Exception:
    check("T7.13 invalid router raises", True)
try:
    noc4.attach_local(0, 25, node_id=1)
    check("T7.13 port>22 raises", False)
except Exception:
    check("T7.13 port>22 raises", True)

# T7.14 route_pos encoding
check("T7.14 route_pos(0)=0", route_pos(0) == 0)
check("T7.14 route_pos(3)=3", route_pos(3) == 3)
check("T7.14 route_pos(28)=0x38=56", route_pos(28) == ((28//4) << 3) | (28 % 4))
check("T7.14 route_pos(31)", route_pos(31) == ((31//4) << 3) | (31 % 4))

# T7.15 helper APIs
check("T7.15 get_route_id GM_RDMA0=28",
      get_route_id(NodeType.GM_RDMA, 0) == 28)
check("T7.15 get_route_id DDR_RDMA2=3",
      get_route_id(NodeType.DDR_RDMA, 2) == 3)
check("T7.15 local id GM_WDMA ch0=10",
      get_data_noc_local_id(NodeType.GM_WDMA, channel=0) == 10)
check("T7.15 local id DDR_WDMA ch1=4",
      get_data_noc_local_id(NodeType.DDR_WDMA, channel=1) == 4)
check("T7.15 local id GM_RDMA=14",
      get_data_noc_local_id(NodeType.GM_RDMA) == 14)
check("T7.15 local id AdaLink ch2=18",
      get_data_noc_local_id(NodeType.ADALINK, channel=2) == 18)

print(f"\nFeature 7: {passed} passed, {failed} failed")
import sys as _s
_s.exit(1 if failed else 0)
