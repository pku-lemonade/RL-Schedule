"""Smoke tests for profiling_sim: topology, routing, end-to-end run, traces."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from profiling_sim.config import load_arch
from profiling_sim.definitions import (
    OperatorType, Direction, DimSlice, Event, Trace
)
from profiling_sim.dfg import DFG
from profiling_sim.noc import NoC
from profiling_sim.architecture import Arch
from profiling_sim.tracing import process_events
from profiling_sim.run import simulate
import simpy


class MockMapper:
    """Minimal mapper matching NetworkMapper's simulator-facing API."""

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
            if (dst_node.received_input != dst_node.input_slice().size()):
                return
            if (dst_node.weight_slice().size()
                    and dst_node.received_weight != dst_node.weight_slice().size()):
                return
            dst_node.ready = True
        if dst_node.operation in (OperatorType.LOAD_FEAT, OperatorType.RECV):
            if dst_node.received_input != dst_node.input_slice().size():
                return
            dst_node.ready = True

    def all_tasks_completed(self, core_id):
        return all(n.finished for n in self.dfg.nodes.values())


def ds(*dims):
    """Build a 4D (N,C,H,W) slice list from integer extents."""
    return [DimSlice(start=0, end=d) for d in dims]


passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        failed += 1


# =========================================================
print("=== Test 1: Config loading & 8x4 Mesh topology ===")
cfg = load_arch("profiling_sim/configs/mesh_8x4.json")
check("config is Mesh", cfg.noc.type == "Mesh")
check("config is 8x4", cfg.noc.x == 8 and cfg.noc.y == 4)
check("router type XY", cfg.noc.router.type == "XY")

env = simpy.Environment()
noc = NoC(env, cfg.noc, deterministic=True).build()
check("32 routers", len(noc.routers) == 32, f"got {len(noc.routers)}")
check("104 r2r links", len(noc.r2r_links) == 104, f"got {len(noc.r2r_links)}")

r0 = noc.routers[0]
# (row=0,col=0): EAST (row+1) and NORTH (col+1) exist; WEST/SOUTH do not
check("router 0 has EAST out", r0.links[Direction.EAST]['out'] is not None)
check("router 0 has NORTH out", r0.links[Direction.NORTH]['out'] is not None)
check("router 0 has no WEST out", r0.links[Direction.WEST]['out'] is None)
check("router 0 has no SOUTH out", r0.links[Direction.SOUTH]['out'] is None)

r31 = noc.routers[31]
# (row=7,col=3): WEST (row-1) and SOUTH (col-1) exist; EAST/NORTH do not
check("router 31 has WEST out", r31.links[Direction.WEST]['out'] is not None)
check("router 31 has SOUTH out", r31.links[Direction.SOUTH]['out'] is not None)
check("router 31 has no EAST out", r31.links[Direction.EAST]['out'] is None)
check("router 31 has no NORTH out", r31.links[Direction.NORTH]['out'] is None)

r13 = noc.routers[13]
for d in Direction:
    check(f"router 13 has {d.name} in+out",
          r13.links[d]['out'] is not None and r13.links[d]['in'] is not None)


# =========================================================
print("\n=== Test 2: XY routing correctness ===")
check("0->31 first hop EAST", r0.calculate_next_router(31) == Direction.EAST)
check("31->0 first hop WEST", r31.calculate_next_router(0) == Direction.WEST)
check("0->3 goes NORTH (Y-dir)", r0.calculate_next_router(3) == Direction.NORTH)
check("0->28 goes EAST (X-dir)", r0.calculate_next_router(28) == Direction.EAST)
check("13->18 goes EAST", r13.calculate_next_router(18) == Direction.EAST)
check("13->12 goes SOUTH", r13.calculate_next_router(12) == Direction.SOUTH)
check("13->9 goes WEST", r13.calculate_next_router(9) == Direction.WEST)
check("13->14 goes NORTH", r13.calculate_next_router(14) == Direction.NORTH)

for rid in range(32):
    x, y = r0.to_xy(rid)
    check(f"to_x(to_xy({rid}))=={rid}", r0.to_x(x, y) == rid)


# =========================================================
print("\n=== Test 3: End-to-end single-PE pipeline (LOAD->CONV->STORE) ===")
dfg = DFG()
dfg.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(16, 4, 4, 4))
dfg.add_node(2, OperatorType.LOAD_WGT, 0,
             weight_size=[DimSlice(start=0, end=1), DimSlice(start=0, end=8),
                          DimSlice(start=0, end=4), DimSlice(start=0, end=3),
                          DimSlice(start=0, end=3)])
dfg.add_node(3, OperatorType.CONV, 0,
             input_size=ds(16, 4, 4, 4),
             weight_size=[DimSlice(start=0, end=1), DimSlice(start=0, end=8),
                          DimSlice(start=0, end=4), DimSlice(start=0, end=3),
                          DimSlice(start=0, end=3)],
             output_size=ds(16, 8, 2, 2))
dfg.add_node(4, OperatorType.STORE, 0, output_size=ds(16, 8, 2, 2))
dfg.add_edge(1, 3)
dfg.add_edge(2, 3)
dfg.add_edge(3, 4)

arch = Arch(cfg, MockMapper(dfg, 4), deterministic=True)
arch.execute()

ev0 = [e for e in arch.cores[0].events if e.end_time > 0]
check("core 0 executed 4 events", len(ev0) == 4, f"got {len(ev0)}")
check("all events have end_time", all(e.end_time > 0 for e in ev0))
makespan = max(e.end_time for e in ev0)
check("makespan > 0", makespan > 0, f"makespan={makespan}")

conv_ev = [e for e in ev0 if e.type == OperatorType.CONV][0]
expected_flops = 16 * 4 * 8 * 3 * 2 * 2
check("CONV flops recorded", conv_ev.flops > 0)
check("CONV flops value correct", conv_ev.flops == expected_flops,
      f"got {conv_ev.flops}, expected {expected_flops}")


# =========================================================
print("\n=== Test 4: Multi-PE communication (PE0 -> PE3 via XY) ===")
# Realistic pattern: LOAD->POOL->SEND on PE0, RECV->POOL->STORE on PE3.
# Only compute nodes (POOL) directly mark successors ready.
dfg2 = DFG()
dfg2.add_node(1, OperatorType.LOAD_FEAT, 0, input_size=ds(1, 4, 4, 4))
dfg2.add_node(2, OperatorType.POOL, 0,
              input_size=ds(1, 4, 4, 4), output_size=ds(1, 4, 2, 2))
dfg2.add_node(3, OperatorType.SEND, 0, output_size=ds(1, 4, 2, 2))
dfg2.add_node(4, OperatorType.RECV, 3, input_size=ds(1, 4, 2, 2))
dfg2.add_node(5, OperatorType.POOL, 3,
              input_size=ds(1, 4, 2, 2), output_size=ds(1, 4, 1, 1))
dfg2.add_node(6, OperatorType.STORE, 3, output_size=ds(1, 4, 1, 1))
dfg2.add_edge(1, 2)
dfg2.add_edge(2, 3)
dfg2.add_edge(3, 4)
dfg2.add_edge(4, 5)
dfg2.add_edge(5, 6)

arch2 = Arch(cfg, MockMapper(dfg2, 6), deterministic=True)
arch2.execute()

c0 = [e for e in arch2.cores[0].events if e.end_time > 0]
c3 = [e for e in arch2.cores[3].events if e.end_time > 0]
check("PE0 ran 3 events (LOAD,POOL,SEND)", len(c0) == 3, f"got {len(c0)}")
check("PE3 ran 3 events (RECV,POOL,STORE)", len(c3) == 3, f"got {len(c3)}")

link_events = [e for l in arch2.noc.r2r_links for e in l.events if e.end_time > 0]
check("network carried packets", len(link_events) > 0, f"got {len(link_events)}")
# 0->3: same row, col 0->3 = 3 NORTH hops
check("path 0->3 used 3 links", len(link_events) == 3,
      f"got {len(link_events)}")

send_ev = [e for e in c0 if e.type == OperatorType.SEND][0]
recv_ev = [e for e in c3 if e.type == OperatorType.RECV][0]
check("RECV starts after SEND begins",
      recv_ev.start_time >= send_ev.start_time)
check("RECV finishes after SEND finishes",
      recv_ev.end_time >= send_ev.end_time)


# =========================================================
print("\n=== Test 5: Trace generation ===")
core_events = [arch.cores[i].events for i in range(32)]
link_events = [arch.noc.r2r_links[i].events for i in range(len(arch.noc.r2r_links))]
maxtime = max(e.end_time for evs in core_events + link_events
              for e in evs if e.end_time > 0)
traces = process_events(maxtime, 5, core_events, link_events)
check("trace has 5 slices", len(traces.time_slices) == 5)
check("each slice has 32 core entries",
      all(len(s.cores) == 32 for s in traces.time_slices))
check("each slice has 104 link entries",
      all(len(s.links) == 104 for s in traces.time_slices))
c0_utils = [c.ultilization for s in traces.time_slices
            for c in s.cores if c.id == 0]
check("core 0 has nonzero utilization", any(u > 0 for u in c0_utils),
      f"utils={c0_utils}")
idle_utils = [c.ultilization for s in traces.time_slices
              for c in s.cores if c.id == 1]
check("idle core 1 has zero utilization", all(u == 0 for u in idle_utils))


# =========================================================
print("\n=== Test 6: simulate() high-level API ===")
dfg3 = DFG()
dfg3.add_node(1, OperatorType.LOAD_FEAT, 5, input_size=ds(2, 4, 4, 4))
dfg3.add_node(2, OperatorType.POOL, 5,
              input_size=ds(2, 4, 4, 4), output_size=ds(2, 4, 2, 2))
dfg3.add_node(3, OperatorType.STORE, 5, output_size=ds(2, 4, 2, 2))
dfg3.add_edge(1, 2)
dfg3.add_edge(2, 3)
maxtime3, traces3, arch3 = simulate(
    "profiling_sim/configs/mesh_8x4.json",
    mapper=MockMapper(dfg3, 3), slice_num=4,
    deterministic=True, verbose=False)
check("simulate() returns positive maxtime", maxtime3 > 0)
check("simulate() returns 4-slice trace", len(traces3.time_slices) == 4)
check("simulate() returns Arch with 32 cores", len(arch3.cores) == 32)
c5 = [e for e in arch3.cores[5].events if e.end_time > 0]
check("core 5 executed 3 tasks", len(c5) == 3, f"got {len(c5)}")


# =========================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
sys.exit(1 if failed else 0)
