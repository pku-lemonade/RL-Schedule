# Medium-Difficulty Features — Test Plan (Revised after review)

This document specifies test cases for the six medium-difficulty features
(items 6–11).  It was revised after a code/spec review to fix coordinate
errors, mask widths, bandwidth models, and scope boundaries.

## Scope and Conventions

- **Time unit**: all simulation time is in **ACI cycles** (1125 MHz). DDR-domain
  DMA scales its effective bandwidth by `1150/1125` so more bytes complete per
  ACI cycle; no independent clock simulation.
- **Coordinates**: the config uses `x=8` (rows, Y-axis in spec) and `y=4`
  (columns, X-axis in spec) for backward compatibility. `to_xy(id) = (id//4, id%4)`
  gives `(row, col)`. Tests assert on **router ID sequences**, not EAST/NORTH
  words, to avoid the naming mismatch. XY routing traverses the row-axis first,
  then the column-axis.
- **Deterministic mode** is used in all tests.
- **Out of scope this round** (classified difficult or folded): in-router
  reduction Add/Max (§3.4), burst/priority/FIFO QoS (§3.5–3.6), 10D
  stride/gather-scatter/transpose/BAUA/mask (§4.4–4.9, folded into byte count),
  cycle-accurate shadow stages (§7.5), multi-chip AdaLink routing.

## Concrete Modelling Decisions

1. **Single-side overhead**: data transfer takes `data_time / 0.9` (header/
   per-packet overhead → 10% throughput loss); two 16-byte control packets
   (request + response) traverse the reverse path to model bidirectional
   pressure. No double counting.
2. **Memory bandwidth**: two-layer — per-engine DMA channels (`DMAEngine`)
   plus one shared aggregate `simpy.Resource` per memory (GM/DDR). Reads and
   writes share the aggregate pool by default.
3. **Multicast**: 64-bit mask. At each router: deliver locally if this router's
   bit is set; replicate onto any row/column output port whose subtree contains
   at least one set bit. Bits ≥ 32 ignored. BROADCAST (trans_type=3) targets a
   node-type group.
4. **Outer sync**: release/acquire packets (1 flit each) traverse the NoC;
   handshake adds real latency and contention. Two independent unidirectional
   handshakes per transfer (no cyclic wait → no deadlock).
5. **AdaLink (single-chip)**: fixed-latency sink/source endpoint; CommID table
   stub with 5 counter types; ALL_ADALINK multicast port represented; no
   cross-chip blocking.
6. **Unbound port / invalid config**: raise a clear exception (never silent
   drop/fallback).

---

## Feature 6 — Router Multi-Local-Port

**T6.1** Local port dict: attach ports 0 and 7; assert distinct pairs,
`len(local_ports)==2`, port 0 ≠ port 7 objects.
**T6.2** Message has `dst_local_port` default 0; values 0–22 accepted; >22 raises.
**T6.3** Message to dst=3 port 7 arrives at port-7 sink only, not port 0.
**T6.4** Two messages to ports 0 and 14 on same router delivered independently.
**T6.5** Messages injected into 3 different local ports all get routed; run twice
with reversed injection order to assert order-independence (not just lucky
scheduling).
**T6.6** Backward compat: PE on port 0 runs the original PE0→PE3 workload with
identical event count and makespan (deterministic).
**T6.7** Message to unbound port raises `UnboundLocalPortError`.
**T6.8** Link events carry `(src_router, src_port, dst_router, dst_port)` endpoint
fields (not the old `corefromid=0` placeholder).
**T6.9** Injection from a local port: a DMA node pushes a message into its local
out-link; the router forwards it onto the mesh like any locally-sourced packet.

---

## Feature 7 — Non-PE Node Attachment

Node IDs: PE 0–31, GM_RDMA 32–35, GM_WDMA 36–39, DDR_RDMA 40–43,
DDR_WDMA 44–47, AdaLink 48–65.

**T7.1** Total 66 nodes; counts 32/4/4/4/4/18.
**T7.2** Global IDs and node types: 32=GM_RDMA, 39=GM_WDMA, 43=DDR_RDMA,
47=DDR_WDMA, 65=ADALINK; type-local id helper correct.
**T7.3** GM_RDMA 0..3 → routers 28,29,30,31 port 14.
**T7.4** DDR_RDMA 0..3 → routers 0,28,3,31 port 7.
**T7.5** GM_WDMA ch0/ch1 on ports 10/11; DDR_WDMA on 3/4; each channel has
independent in/out Link objects.
**T7.6** AdaLink: 48–52→r28 ports16–20; 53–57→r29; 58–61→r30 ports16–19;
62–65→r31 ports16–19.
**T7.7** Router 28 data-plane local ports: PE(0), GM_RDMA(14), GM_WDMA(10,11),
DDR_RDMA(7), DDR_WDMA(3,4), 5 AdaLinks(16–20), ALL_ADALINK(21) = 13 entries.
(Control ports 1,2,5,6,8,12,13,15,22 are modelled only where data-carrying;
AIU ports 5/6/12/13 are included per T8.11.)
**T7.8** GM_RDMA injects to PE0; assert PE0 receives; path is router sequence
28→24→20→16→12→8→4→0 (7 row-axis hops, no column hops).
**T7.9** PE0 sends to GM_WDMA r28 ch0; assert ch0 receives, ch1 does not.
**T7.10** AdaLink node receives a message sent to its port; events recorded.
**T7.11** Helpers `is_pe/is_gm_rdma/is_gm_wdma/is_ddr_rdma/is_ddr_wdma/is_adalink`.
**T7.12** Config omitting AdaLink → 48 nodes; port counts drop.
**T7.13** Negative: duplicate port attachment raises; invalid router id raises;
port >22 raises.
**T7.14** `route_pos` encoding: `((id//4)<<3)|(id%4)` matches spec §2.2 for
ids 0,3,28,31.
**T7.15** `get_route_id(node_type, local_id)` and `get_data_noc_local_id(type,
channel, side)` helpers return correct values per spec §11.3.

---

## Feature 8 — GM / DDR Memory Nodes

GM: 32 MiB, ACI clock; DDR: 128 GiB, DDR clock.

**T8.1** Capacities: GM 32*1024², DDR 128*1024³.
**T8.2** RDMA read N bytes: PE receives N; memory read-bandwidth resource held
for `ceil(N, per_engine_width)` cycles, and aggregate resource acquired.
**T8.3** WDMA write N bytes: memory write pointer advances N; write bandwidth
held expected duration.
**T8.4** DDR vs GM: same configured width, DDR completes in
`gm_cycles * 1125/1150` ACI cycles (ratio asserted within 1e-6).
**T8.5** Four concurrent reads on 4 RDMAs: total time ≈ `4N / min(4*per_engine,
aggregate_bw)`, NOT 4× serial. Assert overlap when aggregate > per-engine.
**T8.6** WDMA 2 channels: two concurrent N-byte writes finish in parallel.
**T8.7** One read + one write on same memory overlap (independent engine
channels) but both consume the shared aggregate resource; assert aggregate
contention when sum > capacity.
**T8.8** Write beyond capacity raises (no silent overflow).
**T8.9** PE→GM_WDMA then GM_RDMA→PE round trip completes; makespan in
[max(stages), sum(stages)] due to overlap.
**T8.10** Aggregate calibration: 4 concurrent RDMA reads saturate aggregate GM
read bandwidth (configured total ≈ 576 GB/s scaled to B/cycle).
**T8.11** AIU download SRAM (256 KiB) per DMA node on ports 5/6/12/13: control
message received without consuming main memory bandwidth; 16-byte alignment
enforced (non-multiple raises); capacity cap raises.
**T8.12** GM_WDMA `writeSum=0/1/2`: two concurrent writes to same address —
overwrite / atomic add / atomic max; assert resulting value.
**T8.13** AIU download completion signals scalar (outer sync to unit 8); assert
sync event recorded.

---

## Feature 9 — Single-Side vs Dual-Side

**T9.1** Dual-side baseline = data tx + hop latency, no extra overhead.
**T9.2** Single-side adds 2 reverse control packets (16 B each) and data tx time
×1/0.9; makespan strictly greater.
**T9.3** Large transfer (data_time ≫ RTT): `dual_time/single_time ∈ [0.89,0.91]`.
**T9.4** Reverse-direction links carry exactly 2 control packets per single-side
transfer (request + response), with recorded data_size.
**T9.5** Per-task mode: one dual-side and one single-side SEND from same PE;
only single-side has overhead.
**T9.6** PE→PE single-side raises `UnsupportedTransferMode` (no fallback).
**T9.7** PE→GM_WDMA: both modes work; single-side slower.
**T9.8** GM_RDMA→PE: both modes work; single-side slower.
**T9.9** Single-side data message carries header overhead (assert
`header_bytes > 0` field, contributing to 0.9×).
**T9.10** Two overlapping single-side transfers contend on shared links; total
time > independent sum / 2.
**T9.11** FixPath trans_type: source-specified path has deterministic latency
equal to XY path length (no adaptive variation).

---

## Feature 10 — Multicast / Broadcast

64-bit `dst_mask`; trans_type SINGLECAST=0, MULTICAST=2, BROADCAST=3.

**T10.1** Single-bit mask behaves as unicast: one copy, one path.
**T10.2** Mask {0,3} from router 1: both receive; assert exact set of links
traversed is the minimal XY tree (no duplicate links beyond necessary
replication).
**T10.3** Mask {0,28} from router 4: both receive; minimal tree.
**T10.4** Mask {0,3,28,31} from 15: all 4 corners receive exactly once.
**T10.5** Broadcast mask `(1<<32)-1` from 0: all 32 PEs receive exactly once.
**T10.6** Shared link carries one copy before branch; after branch two copies
proceed in parallel; assert non-overlapping start times on shared link.
**T10.7** `route_local_id` delivers to that port on all destinations; port 14
delivers to GM_RDMA not PE.
**T10.8** Non-member routers receive nothing on local ports.
**T10.9** Max hop count from corner 0 ≤ mesh diameter 10 (7 row + 3 col).
**T10.10** Multicast with element_bytes=2: each replica link event has
data_size = elements×2; total link bytes = fanout × elements × 2.
**T10.11** Bits ≥32 in mask are ignored (no crash, no delivery).
**T10.12** Empty mask: no transmission, warning logged.
**T10.13** Source router itself in mask: local delivery happens at source
without hairpin through mesh.
**T10.14** BROADCAST trans_type to PE group equals all-32 mask; to DMA group
targets only DMA nodes.

---

## Feature 11 — Synchronization Framework

**T11.1** Outer sync enabled: makespan = baseline + handshake latency
(release forward 1 flit + acquire back 1 flit over the path). Deterministic
formula, no "either/or".
**T11.2** Outer sync disabled: makespan equals dual-side baseline (no regression).
**T11.3** Sync overhead is constant per transfer (not per byte): assert same
absolute overhead for 100 B and 10000 B.
**T11.4** Inner sync: two DOWNLOAD tasks on same NMC channel serialize; on
different channels they run in parallel (channel-level resource conflict,
beyond plain DFG ordering).
**T11.5** Sync packets (1 flit each) appear on NoC links; count them per
transfer.
**T11.6** Receiver busy: sender's SEND end_time ≥ receiver's ready time
(handshake blocks until acquire).
**T11.7** Per-transfer sync flag: one SEND with sync, one without; only the
sync task carries handshake packets.
**T11.8** Pattern A (PE→DMA) and pattern B (DMA→PE) both apply handshake.
**T11.9** Patterns C (PE→PE), D (DMA→DMA), E (multicast) apply handshake;
parametrized over A–F.
**T11.10** Mutual cross-send with sync: both complete, no deadlock
(independent unidirectional handshakes).
**T11.11** CommID stub: each AdaLink port exposes 5 counter types
(PRODUCE/SEND/RECEIVE/CREDIT/RNIC_PRODUCE), 64 entries; acquire/release update
counters without cross-chip blocking.
**T11.12** AIU download → scalar outer sync (pattern F) recorded.

---

## Cross-Cutting Integration Tests

**X1** Full 66-node architecture builds; correct node/port counts.
**X2** All previous tests (test_smoke.py + test_features.py) still pass.
**X3** GM_RDMA→PE→compute→GM_WDMA end-to-end; makespan ∈ [max, sum] of stages.
**X4** GM_RDMA multicast to 4 PEs → compute → WDMA write-back completes.
**X5** Deterministic: two runs produce identical event start/end times and
makespan; utilization within 1e-9 tolerance.
**X6** Trace includes DMA link events and memory bandwidth utilization segments
with defined schema (`dma_links`, `mem_bw`).
