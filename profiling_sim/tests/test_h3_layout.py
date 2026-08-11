"""Feature H3: 10-dimensional advanced data layout tests."""
import sys, os
sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import simpy
from profiling_sim.config import NoCConfig, RouterConfig, LinkConfig
from profiling_sim.definitions import (
    Message, DimSlice, TransType, TransferMode,
)
from profiling_sim.layout import (
    TensorLayout, AdaType, ReorderMode, MaskAxis, BAUA, GatherScatter,
)
from profiling_sim.noc import NoC, Router
from profiling_sim.nodes import NodeType
from profiling_sim.memory import Memory, DMANode

passed = failed = 0
W = 16


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  [PASS] {name}"); passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}"); failed += 1


def expect_raise(fn, label):
    try:
        fn()
    except (ValueError, Exception) as e:
        check(label, isinstance(e, ValueError), f"got {type(e).__name__}: {e}")
        return
    check(label, False, "no exception raised")


def ds(n):
    return [DimSlice(start=0, end=n)]


def make_noc(env, width=W, delay=0):
    cfg = NoCConfig(x=8, y=4, router=RouterConfig(),
                    link=LinkConfig(width=width, delay=delay))
    return NoC(env, cfg, deterministic=True).build()


def hops(a, b):
    return abs(a // 4 - b // 4) + abs(a % 4 - b % 4)


def layout_unicast(layout, sr=0, dr=1, snode=0, dnode=1, dport=0,
                   self_loop=False, transpose_overhead=None, header=0):
    env = simpy.Environment()
    noc = make_noc(env)
    arrivals = []
    if self_loop:
        r2n, n2r = noc.attach_local(sr, 0, node_id=snode)

        def sink():
            while True:
                m = yield r2n.get()
                if not m.is_control:
                    arrivals.append((env.now, m))
        env.process(sink())
        msg = Message(src=snode, dst=snode, index=1, data=ds(0),
                      dst_local_port=0, header_bytes=header, layout=layout)
        n2r.put(msg)
    else:
        _, n2r = noc.attach_local(sr, 0, node_id=snode)
        r2n, _ = noc.attach_local(dr, dport, node_id=dnode)

        def sink():
            while True:
                m = yield r2n.get()
                if not m.is_control:
                    arrivals.append((env.now, m))
        env.process(sink())
        msg = Message(src=snode, dst=dnode, index=1, data=ds(0),
                      src_local_port=0, dst_local_port=dport,
                      header_bytes=header, layout=layout)
        n2r.put(msg)
    env.run()
    mk = arrivals[-1][0] if arrivals else -1
    return env, noc, mk, arrivals


def L(**kw):
    kw.setdefault('loop_cnt', kw.pop('cnt', None) or [16])
    if 'loop_stride' not in kw:
        kw['loop_stride'] = [0] * len(kw['loop_cnt'])
    return TensorLayout(**kw)


# =====================================================================
print("=== H3.1-H3.9: descriptor / dtype / dimensions ===")

l = TensorLayout(loop_cnt=[32], loop_stride=[1])
check("T-H3.1 1D contiguous payload/footprint",
      l.payload_bytes() == 32 and l.footprint_bytes() == 32,
      f"{l.payload_bytes()} {l.footprint_bytes()}")

l = TensorLayout(loop_cnt=[4, 6], loop_stride=[1, 8])
expect_fp = (4 - 1) * 1 + (6 - 1) * 8 + 1
check("T-H3.2 2D strided",
      l.payload_bytes() == 24 and l.footprint_bytes() == expect_fp,
      f"p={l.payload_bytes()} f={l.footprint_bytes()} exp={expect_fp}")

l = TensorLayout(loop_cnt=[2] * 10, loop_stride=[0] * 10)
check("T-H3.3 10D accepted, product 1024",
      l.ndim() == 10 and l.element_count_raw() == 1024
      and l.payload_bytes() == 1024)

expect_raise(lambda: TensorLayout(loop_cnt=[2] * 11, loop_stride=[0] * 11),
             "T-H3.4 11 dims raises")
empty = TensorLayout()
check("T-H3.4 empty layout is zero-element",
      empty.element_count_raw() == 0 and empty.payload_bytes() == 0
      and empty.footprint_bytes() == 0)

widths = {AdaType.INT16: 200, AdaType.INT32: 400, AdaType.INT64: 800,
          AdaType.FP16: 200, AdaType.FP32: 400, AdaType.BF16: 200,
          AdaType.TF32: 400, AdaType.INT8: 100, AdaType.FP8_E4M3: 100,
          AdaType.FP8_E5M2: 100}
ok = all(TensorLayout(loop_cnt=[100], loop_stride=[0], dtype=dt
                      ).payload_bytes() == w for dt, w in widths.items())
check("T-H3.5 dtype widths", ok)

u4_8 = TensorLayout(loop_cnt=[8], loop_stride=[0], dtype=AdaType.UINT4)
u4_9 = TensorLayout(loop_cnt=[9], loop_stride=[0], dtype=AdaType.UINT4)
u4x2 = TensorLayout(loop_cnt=[8], loop_stride=[0], dtype=AdaType.UINT4X2)
check("T-H3.6 UINT4 payload packing",
      u4_8.payload_bytes() == 4 and u4_9.payload_bytes() == 5
      and u4x2.payload_bytes() == 8,
      f"{u4_8.payload_bytes()} {u4_9.payload_bytes()} {u4x2.payload_bytes()}")

bcst = TensorLayout(loop_cnt=[8], loop_stride=[0], dtype=AdaType.INT8)
check("T-H3.7 UINT4 footprint vs broadcast",
      u4_8.footprint_bytes() == 4 and u4_9.footprint_bytes() == 5
      and bcst.footprint_bytes() == 1,
      f"u4f={u4_8.footprint_bytes()} bcst={bcst.footprint_bytes()}")

expect_raise(lambda: TensorLayout(loop_cnt=[4, 4], loop_stride=[1]),
             "T-H3.8 cnt/stride length mismatch raises")
expect_raise(lambda: TensorLayout(loop_cnt=[0], loop_stride=[1]),
             "T-H3.8 cnt=0 raises")
expect_raise(lambda: TensorLayout(loop_cnt=[4], loop_stride=[-1]),
             "T-H3.8 negative stride raises")

g6 = TensorLayout(loop_cnt=[2] * 6, loop_stride=[0] * 6, is_global=True)
expect_raise(lambda: TensorLayout(loop_cnt=[2] * 7, loop_stride=[0] * 7,
                                  is_global=True),
             "T-H3.9 global 7 dims raises")
l10 = TensorLayout(loop_cnt=[2] * 10, loop_stride=[0] * 10)
expect_raise(lambda: TensorLayout(loop_cnt=[2] * 11, loop_stride=[0] * 11),
             "T-H3.9 non-global 11 dims raises")
check("T-H3.9 global 6 / local 10 accepted",
      g6.ndim() == 6 and l10.ndim() == 10)

# =====================================================================
print("=== H3.10-H3.13: mask / padding ===")

m = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                 mask_first=MaskAxis(axis=1, num=2))
check("T-H3.10 mask outer axis removes 2*4=8",
      m.effective_element_count() == 8 and m.payload_bytes() == 8,
      f"eff={m.effective_element_count()} p={m.payload_bytes()}")

mlast = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                     mask_last=MaskAxis(axis=1, num=2))
check("T-H3.10 mask_last symmetric",
      mlast.effective_element_count() == 8)

m2 = TensorLayout(loop_cnt=[4, 6], loop_stride=[1, 4],
                  mask_first=MaskAxis(axis=1, num=1),
                  mask_last=MaskAxis(axis=0, num=2))
check("T-H3.11 masks on two axes: 24-4-2=18",
      m2.effective_element_count() == 18 and m2.payload_bytes() == 18,
      f"eff={m2.effective_element_count()}")

expect_raise(lambda: TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                                  mask_first=MaskAxis(axis=5, num=1)),
             "T-H3.12 mask axis out of range raises")
expect_raise(lambda: TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                                  mask_first=MaskAxis(axis=0, num=5)),
             "T-H3.12 mask num>cnt raises")
expect_raise(lambda: TensorLayout(
    loop_cnt=[4, 4], loop_stride=[1, 4],
    mask_first=MaskAxis(axis=0, num=3), mask_last=MaskAxis(axis=0, num=3)),
    "T-H3.12 same-axis first+last>cnt raises")
expect_raise(lambda: TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                                  mask_first=MaskAxis(axis=0, num=-1)),
             "T-H3.12 negative mask num raises")

mp = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                  mask_first=MaskAxis(axis=1, num=2), pad_value=7)
check("T-H3.13 pad_value round-trip, payload reduced",
      mp.pad_value == 7 and mp.payload_bytes() == 8)

# =====================================================================
print("=== H3.14: BAUA ===")

baua = BAUA(base_axis_first=0, unalign_axis_first=1, num_first=2,
            base_axis_second=1, unalign_axis_second=0, num_second=1)
lb = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4], baua=baua)
base = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4])
check("T-H3.14 BAUA accepted, payload/footprint unchanged",
      lb.payload_bytes() == base.payload_bytes() == 16
      and lb.footprint_bytes() == base.footprint_bytes(),
      f"p={lb.payload_bytes()} f={lb.footprint_bytes()}")
expect_raise(lambda: TensorLayout(
    loop_cnt=[4, 4], loop_stride=[1, 4],
    baua=BAUA(base_axis_first=9)),
    "T-H3.14 BAUA axis out of range raises")
expect_raise(lambda: TensorLayout(
    loop_cnt=[4, 4], loop_stride=[1, 4],
    baua=BAUA(num_first=-1)),
    "T-H3.14 BAUA negative num raises")

# =====================================================================
print("=== H3.15-H3.17: transpose / reorder ===")

lt = TensorLayout(loop_cnt=[4, 8], loop_stride=[1, 8],
                  transpose=True, transpose_overhead=3)
lr = TensorLayout(loop_cnt=[4, 8], loop_stride=[1, 8],
                  reorder=ReorderMode.BMM)
lplain = TensorLayout(loop_cnt=[4, 8], loop_stride=[1, 8])
check("T-H3.15a transpose/reorder do not change payload/footprint",
      lt.payload_bytes() == lplain.payload_bytes()
      and lt.footprint_bytes() == lplain.footprint_bytes()
      and lr.payload_bytes() == lplain.payload_bytes()
      and lr.footprint_bytes() == lplain.footprint_bytes())

for r in range(5):
    TensorLayout(loop_cnt=[4], loop_stride=[0], reorder=r)
check("T-H3.15b ReorderMode 0..4 accepted", True)
expect_raise(lambda: TensorLayout(loop_cnt=[4], loop_stride=[0], reorder=9),
             "T-H3.15b unknown reorder raises")

base_layout = TensorLayout(loop_cnt=[W], loop_stride=[1])
oh_layout = TensorLayout(loop_cnt=[W], loop_stride=[1],
                         transpose=True, transpose_overhead=10)
_, _, mk0, _ = layout_unicast(base_layout, sr=0, dr=1, snode=0, dnode=1)
_, _, mkK, _ = layout_unicast(oh_layout, sr=0, dr=1, snode=0, dnode=1)
check("T-H3.16 transpose overhead adds exactly K cycles",
      mkK - mk0 == 10, f"mk0={mk0} mkK={mkK} delta={mkK - mk0}")

lr_none = TensorLayout(loop_cnt=[W], loop_stride=[1], reorder=ReorderMode.NONE)
lr_bmm = TensorLayout(loop_cnt=[W], loop_stride=[1], reorder=ReorderMode.BMM)
_, _, mkn, _ = layout_unicast(lr_none, sr=0, dr=1, snode=0, dnode=1)
_, _, mkb, _ = layout_unicast(lr_bmm, sr=0, dr=1, snode=0, dnode=1)
check("T-H3.17 reorder-only adds no latency",
      mkn == mkb, f"{mkn} vs {mkb}")

# =====================================================================
print("=== H3.18-H3.21: gather / scatter ===")

g = GatherScatter(num_entries=10, row_bytes=32, per_row_overhead=2)
lg = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4], gather=g)
check("T-H3.18 gather payload = entries*row_bytes",
      lg.payload_bytes() == 320, f"{lg.payload_bytes()}")

check("T-H3.19 gather footprint & overhead",
      lg.footprint_bytes() == 10 * 8 + 320
      and lg.endpoint_overhead_cycles() == 20,
      f"f={lg.footprint_bytes()} oh={lg.endpoint_overhead_cycles()}")

expect_raise(lambda: GatherScatter(num_entries=-1, row_bytes=4),
             "T-H3.20 negative num_entries raises")
expect_raise(lambda: GatherScatter(num_entries=1, row_bytes=-4),
             "T-H3.20 negative row_bytes raises")
expect_raise(lambda: GatherScatter(num_entries=1, row_bytes=4,
                                   per_row_overhead=-1),
             "T-H3.20 negative per_row_overhead raises")

gmask = TensorLayout(loop_cnt=[4, 4], loop_stride=[1, 4],
                     mask_first=MaskAxis(axis=1, num=2),
                     gather=GatherScatter(num_entries=5, row_bytes=16,
                                          per_row_overhead=1))
check("T-H3.21 gather+mask precedence (mask ignored)",
      gmask.payload_bytes() == 80 and gmask.footprint_bytes() == 5 * 8 + 80
      and gmask.endpoint_overhead_cycles() == 5,
      f"p={gmask.payload_bytes()} f={gmask.footprint_bytes()} "
      f"oh={gmask.endpoint_overhead_cycles()}")

# =====================================================================
print("=== H3.22-H3.28: integration / regression ===")

m1 = Message(src=0, dst=1, index=1, data=ds(0),
             layout=TensorLayout(loop_cnt=[16], loop_stride=[1]))
m2 = Message(src=0, dst=1, index=2, data=ds(0),
             layout=TensorLayout(loop_cnt=[32], loop_stride=[1]),
             header_bytes=4)
check("T-H3.22 byte_size/total_bytes delegate; __lt__ consistent",
      m1.byte_size() == 16 and m2.total_bytes() == 36 and m1 < m2,
      f"{m1.byte_size()} {m2.total_bytes()} {m1 < m2}")

expect_raise(lambda: Message(
    src=0, dst=1, index=1, data=ds(16), trans_type=TransType.MULTICAST,
    layout=TensorLayout(loop_cnt=[16], loop_stride=[1])),
    "T-H3.23 MULTICAST+layout raises")
expect_raise(lambda: Message(
    src=0, dst=1, index=1, data=ds(16), trans_type=TransType.BROADCAST,
    layout=TensorLayout(loop_cnt=[16], loop_stride=[1])),
    "T-H3.23 BROADCAST+layout raises")
expect_raise(lambda: Message(
    src=0, dst=1, index=1, data=ds(16), trans_type=TransType.REDUCE,
    layout=TensorLayout(loop_cnt=[16], loop_stride=[1])),
    "T-H3.23 REDUCE+layout raises")

# T-H3.24 backward compatibility: layout-less PE0->PE3, n=1600, HP=3
env24 = simpy.Environment()
noc24 = make_noc(env24)
_, n2r = noc24.attach_local(0, 0, node_id=0)
r2n, _ = noc24.attach_local(3, 0, node_id=3)
arr24 = []


def sink24():
    while True:
        m = yield r2n.get()
        if not m.is_control:
            arr24.append((env24.now, m))


env24.process(sink24())
n2r.put(Message(src=0, dst=3, index=1, data=ds(1600), dst_local_port=0))
env24.run()
mk24 = arr24[-1][0] if arr24 else -1
HP3 = hops(0, 3)
check("T-H3.24 layout-less PE0->PE3 makespan == 503",
      mk24 == (HP3 + 2) * (1600 // W) + HP3 == 503, f"{mk24}")

# T-H3.25 control/sync reset
data_msg = Message(src=32, dst=0, index=7, data=ds(0),
                   layout=TensorLayout(loop_cnt=[16], loop_stride=[1]),
                   transfer_mode=TransferMode.SINGLE_SIDE)
ctrl = Router._control_msg(data_msg, 0, 32, req=True)
sync = Router._sync_msg(data_msg, 0, 32, to_dst=True)
check("T-H3.25 control/sync packets reset layout",
      ctrl.layout is None and sync.layout is None,
      f"ctrl={ctrl.layout} sync={sync.layout}")

# AIU scalar_sync reset
env25 = simpy.Environment()
noc25 = make_noc(env25)
mem25 = Memory(env25, "GM", 2 ** 40, aggregate_bw=0)
rdma = DMANode(env25, 32, NodeType.GM_RDMA, 28, [14], noc25,
               memory=mem25, engine_width=16, channels=1, is_read=True)
aiu_port = rdma.aiu_port
captured = []
orig_out = rdma.data_out[aiu_port]
rdma.data_out[aiu_port] = simpy.Store(env25)


def collect_sync():
    while True:
        m = yield rdma.data_out[aiu_port].get()
        captured.append(m)


env25.process(collect_sync())
aiu_msg = Message(src=0, dst=32, index=9, data=ds(0),
                  dst_local_port=aiu_port,
                  layout=TensorLayout(loop_cnt=[16], loop_stride=[1]))
rdma.aiu_in.put(aiu_msg)
env25.run(until=50)
check("T-H3.25 AIU scalar_sync resets layout",
      len(captured) == 1 and captured[0].layout is None,
      f"captured={len(captured)} layout={captured[0].layout if captured else 'NA'}")

# T-H3.26 end-to-end 512B PE0->PE1 HP=1
lay26 = TensorLayout(loop_cnt=[16, 8, 4], loop_stride=[1, 16, 128])
env26, noc26, mk26, _ = layout_unicast(lay26, sr=0, dr=1, snode=0, dnode=1)
sizes = [e.data_size for lk in noc26.r2r_links + noc26.local_links
         for e in lk.events if not e.is_control]
expect_mk = (1 + 2) * -(-512 // W) + 1
check("T-H3.26 3D 512B layout makespan 97, link data_size 512",
      mk26 == expect_mk == 97 and all(s == 512 for s in sizes) and sizes,
      f"mk={mk26} exp={expect_mk} sizes={set(sizes)}")

# T-H3.27 self-loop charges overhead
lay27 = TensorLayout(loop_cnt=[W], loop_stride=[1],
                     transpose=True, transpose_overhead=5)
_, _, mk27, _ = layout_unicast(lay27, sr=2, snode=2, self_loop=True)
check("T-H3.27 self-loop makespan 2*c + O",
      mk27 == 2 * 1 + 5, f"{mk27}")

# T-H3.28 determinism
gd = GatherScatter(num_entries=8, row_bytes=64, per_row_overhead=1)
lay28 = TensorLayout(loop_cnt=[16], loop_stride=[1],
                     transpose=True, transpose_overhead=4, gather=gd)


def run28():
    env = simpy.Environment()
    noc = make_noc(env)
    _, n2r = noc.attach_local(4, 0, node_id=400)
    r2n, _ = noc.attach_local(8, 0, node_id=800)

    def sk():
        while True:
            yield r2n.get()
    env.process(sk())
    n2r.put(Message(src=400, dst=800, index=1, data=ds(0),
                    layout=lay28.model_copy()))
    env.run()
    return [(e.start_time, e.end_time, e.data_size)
            for lk in noc.r2r_links + noc.local_links
            for e in lk.events]


trace_a = run28()
trace_b = run28()
check("T-H3.28 identical layout runs are deterministic",
      trace_a == trace_b and len(trace_a) > 0,
      f"len={len(trace_a)} equal={trace_a == trace_b}")

# =====================================================================
print(f"\n{'='*60}")
print(f"H3 RESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
