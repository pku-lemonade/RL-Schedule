# High-Difficulty Features — Test Plan

This document covers the high-difficulty features classified out of scope in
[TEST_PLAN_MEDIUM.md](TEST_PLAN_MEDIUM.md). Each feature follows the same
workflow: draft test cases → subagent review → iterate until approved →
implement → test.

Conventions carry over from the medium plan: deterministic mode, ACI-cycle
time unit, `to_xy(id)=(id//4, id%4)=(row,col)`, XY routing (row axis first via
EAST/WEST, then column via NORTH/SOUTH), link width 16 B/cycle unless stated,
zero link delay, `per_hop_time=1`.

Unicast baseline formula (verified in T9.1): for an n-byte message over HP
r2r hops, makespan = `(HP+2)*ceil(n/W) + HP`.

---

## Feature H1 — In-Router Reduction (Route Reduce)

Spec: [NOC_ARCHITECTURE.md §3.4](../../NOC_ARCHITECTURE.md). The router merges
multiple in-flight packets belonging to the same reduction tree (matched by
`task_id`) using Add or Max, then forwards ONE result instead of N packets.
This builds a hardware reduction tree across the mesh and halves upstream
bandwidth at each merge level. The final hop (`insSyncMode=3`) writes the
result to the local port (PE or GM_WDMA).

### Modelling Decisions

1. **New enum value**: `TransType.REDUCE = 4`. This is a **simulator-internal
   dispatch marker only**; the hardware header encodes trans_type in 2 bits
   (§3.2) and triggers reduction via `insSyncMode=2/3`, not a 5th trans_type.
   The internal marker keeps routing dispatch symmetric with MULTICAST/
   BROADCAST and does not correspond to any header bit pattern.
2. **New Message fields** (all backward-compatible defaults):
   - `reduce_op: int = 0` — 0=None, 1=Add, 2=Max (maps `AdaNocReduceType`).
   - `task_id: int = 0` — groups packets of one reduction tree.
   - `reduce_is_last: bool = False` — set on the final merged packet at root
     (corresponds to `insSyncMode=3`).
   - `reduce_count: int = 1` — number of source operands folded into this
     packet (for verification/bandwidth accounting; does not affect routing).
3. **New Event field**: `reduce_count: int = 0` populated by `Link.put` so
   per-hop link events expose how many operands a packet carries (enables
   intermediate merge-level assertions without peeking at router state).
4. **Tree registration**: `noc.register_reduce(task_id, source_routers,
   root_router, op=1)` validates that `source_routers` are unique and in range,
   non-empty, and `root_router` is valid (raises `ValueError`/
   `ProfilingSimError` otherwise; duplicate `task_id` raises). It precomputes,
   for every router on any source→root XY path:
   - `children`: set of incoming keys (`Direction` or the string `"local"`).
     For a source at this router the key is `"local"`; otherwise the key is
     the direction from this router back toward its predecessor on the path
     (i.e. the direction from which the operand arrives).
   - `parent`: outgoing `Direction` toward root (`None` at root).
   - `is_root`: `True` at `root_router`.
   This set corresponds to the hardware `op_type_src0`/`op_type_src1` source
   selectors generalised to k inputs.
5. **Binary reduction at merge points (faithful to 2-input reducer)**: the
   hardware reducer has two source operands. A router with `k = len(children)`
   operands therefore performs a binary reduction of `ceil(log2(k))` stages,
   each costing `reduce_latency` cycles (new `RouterConfig.reduce_latency`,
   default 2 — a pipelined reduction unit). The latency at a merge point is
   `R * ceil(log2(k))` regardless of payload size. For k=2 this is R; k=3 is
   2R; k=4 is 2R. The result is ONE packet.
6. **Router runtime**:
   - A reduce packet is NOT forwarded immediately. It is buffered in an
     **unbounded** per-`(router, task_id)` `simpy.Store` (unbounded is safe:
     the tree is acyclic and at most k operands ever arrive).
   - On first arrival for a `(router, task_id)`, a reducer process starts and
     reads exactly `k = len(children)` packets.
   - The reducer waits until all k operands have arrived, then holds for
     `R * ceil(log2(k))` cycles (zero when k=1 / pass-through).
   - It merges scalar metadata: `reduce_count = sum(child counts)`; for the
     scalar `value` field it applies `reduce_op` (Add → sum, Max → max),
     giving a functional check at scalar granularity even though tensor
     payloads are not modelled.
   - Before forwarding to `parent` it yields `per_hop_time` (both merge and
     pass-through), exactly mirroring the `yield env.timeout(per_hop_time)`
     in the normal unicast forward branch. At root it delivers locally via
     `route_local` with **no** `per_hop_time` (mirroring `target==self.id`).
   - A reduce packet for an unregistered `task_id`, or arriving at a router
     with no tree entry, raises `ProfilingSimError` (never silent drop).
7. **Functional scope**: this is a profiling simulator — `data` is a
   `DimSlice` shape with no element payload, so Add/Max produce identical
   timing and identical byte counts. `reduce_op` is applied to the scalar
   `value` only (see decision 6); element-value correctness over tensors is
   out of scope. Bandwidth compression (N payloads → 1 after each merge) and
   binary merge latency are the performance effects under test.
8. **No regression**: reduce uses a dedicated trans_type; normal unicast,
   fixpath, multicast, broadcast, single-side, and sync paths are unchanged.

### Concrete Timing Derivations

With W=16, c = ceil(n/16), R = reduce_latency, L(k) = R*ceil(log2(k)).

**Two-source linear merge** (sources r0 and r4, root r28):
- Paths: 0→4→8→…→28 and 4→8→…→28. Merge point = r4
  (children `{local, WEST}`, k=2 → L=R).
- r0's operand reaches r4 after 1 hop: 2c+1.
- r4's own local operand reaches r4 at c.
- Merge starts at 2c+1, takes R. Remaining path r4→r28 is 6 hops:
  6 per-hop + 6 r2r links + 1 dest local = 6+7c.
- **Makespan = 9c + 7 + R** (= unicast 0→28 baseline 9c+7 + R).

**Four-source linear chain** (sources r0,r4,r8,r12, root r28):
- Merge points r4, r8, r12 (each k=2 → R). r16/r20/r24 are pass-through.
  Critical path (source 0) crosses 3 merge points.
- **Makespan = 9c + 7 + 3R**, root `reduce_count=4`.

**Two-level tree with a 3-way merge** (sources r24,r25,r29,r30, root r28):
- r29 children `{local, WEST(from r25), NORTH(from r30)}` (k=3 → L=2R),
  parent SOUTH; r28 root children `{WEST(from r24), NORTH(from r29)}`
  (k=2 → L=R).
- Slowest operands r25/r30 reach r29 at 2c+1; r29 merge starts at 2c+1
  (r29 local arrives at c), takes 2R; r29→r28 one hop = per_hop 1 + link c,
  arrives root at 3c+2+2R. Root also waits for r24 (arrives 2c+1). Root merge
  starts at 3c+2+2R, takes R, then dest local c.
- **Makespan = 4c + 2 + 3R** = 408 (c=100, R=2). Root `reduce_count=4`.

### Test Cases

**Tree builder (unit, no simulation)**

- **T-H1.1** Register reduce task with sources `{0,4}`, root 28, op=Add.
  Assert: r0 children == `{"local"}`, parent EAST; r4 children ==
  `{WEST, "local"}`, parent EAST; r8,r12,r16,r20,r24 children == `{WEST}`,
  parent EAST; r28 children == `{WEST}`, parent None, is_root. No other
  routers appear in the descriptor.
- **T-H1.2** Sources `{24,25,29,30}`, root 28: assert r25/r30 have
  `{"local"}`; r24 has `{"local"}`; r29 has 3 children
  `{WEST, NORTH, "local"}` and parent SOUTH; r28 root has 2 children
  `{WEST, NORTH}` and parent None.
- **T-H1.3** Validation: duplicate `task_id` raises; duplicate source router
  in `source_routers` raises; out-of-range router id raises; empty source set
  raises; root out of range raises; root not required to be a source.

**Single-source and baseline**

- **T-H1.4** One source r0, root r28: behaves as unicast. Root sink receives
  exactly one packet, `reduce_count=1`, `reduce_is_last=True`, makespan ==
  `9c+7` (NO merge latency, because every router on path has k=1).
- **T-H1.5** One source r28, root r28 (zero-hop): makespan == 2c (two local
  links, no per_hop, no merge), root receives one packet `reduce_count=1`.
- **T-H1.6** Reduce with `op=Max` (single source r0→r28): packet delivered,
  `reduce_op==2` observable at sink; same timing as Add.

**Multi-source merges**

- **T-H1.7** Two sources r0,r4 → root r28 (Add): root sink receives exactly
  ONE packet (not two), `reduce_count=2`, `reduce_is_last=True`; makespan ==
  `9c+7+R` with c=100 (n=1600), R=2 → 909.
- **T-H1.8** Four-source linear chain r0,r4,r8,r12 → root r28: root receives
  one packet `reduce_count=4`; makespan == `9c+7+3R` = 913 (c=100, R=2).
- **T-H1.9** Two-level tree sources r24,r25,r29,r30 → root r28: root receives
  one packet `reduce_count=4`; makespan == `4c+2+3R` = 408 (c=100, R=2),
  exercising the binary 3-way merge (2R) at r29 and the 2-way merge (R) at
  r28.
- **T-H1.10** Merge latency scales as `ceil(log2(k))`, fixed per stage and
  independent of payload size: (a) run T-H1.8 with n=160 (c=10) and n=16000
  (c=1000); overhead = makespan − `9c+7` equals `3R` (6 cycles) for both.
  (b) A 4-child merge point costs 2R while a 2-child costs R (assert via the
  T-H1.9 vs T-H1.7 merge accounting, or a contrived root-in-middle topology).
- **T-H1.11** Source co-located with root: sources r28 and r0, root r28. r28
  children `{WEST, "local"}` (k=2); root merges local + incoming, receives
  one packet `reduce_count=2`. Makespan = unicast 0→28 (9c+7) + R.
- **T-H1.12** Three-source odd count: sources r0,r4,r8 → root r28. Merge
  points r4 (k=2,R) and r8 (k=2,R; receives merged-from-r4 count 2 + local
  r8 count 1 → count 3). Root receives one packet `reduce_count=3`; makespan
  == `9c+7+2R` = 911 (c=100, R=2).
- **T-H1.13** Root in the middle of the mesh: sources r3 and r28, root r12
  (row 3, col 0). Assert the tree children/directions and that root receives
  one packet `reduce_count=2`; makespan derived from the two path lengths to
  r12 plus one R (no hard-coded number — compute from HP of the slower
  source).

**Scalar value reduction**

- **T-H1.14** Add merges scalar `value`: sources r0,r4,r8,r12 carry values
  1,2,3,4 respectively; the root packet's `value == 10`. Max variant with
  values 7,2,9,4 yields `value == 9`. This is the only functional (non-timing)
  assertion, at scalar granularity.

**Bandwidth compression and per-hop reduce_count**

- **T-H1.15** With sources r0,r4 → root r28, assert the post-merge link
  r4→r8 carries exactly ONE data event of n bytes with `reduce_count==2`. In a
  control run with two independent unicasts (0→28 and 4→28), the same link
  carries TWO data events totaling 2n bytes (serialized by `busy`). The
  reduce run's post-merge byte total is exactly half.
- **T-H1.16** Four-source chain: link r0→r4 event has `reduce_count==1`;
  link r4→r8 has `reduce_count==2`; r8→r12 has 3; r12→r16 has 4; every
  downstream link carries exactly one event of n bytes for this task_id.
- **T-H1.17** Contention: a reduce (r0,r4→r28) and a concurrent unicast
  (r8→r28) share the link r12→r16 (and beyond); their packets serialize via
  `busy`, so the combined makespan is strictly greater than the reduce alone.

**Memory / writeSum integration**

- **T-H1.18** Reduction root is GM_WDMA (router 28, port 10): four PE sources
  reduce to node 36. Exactly ONE GM memory write event of n bytes is recorded
  (not four), `GM.used == n`, and the DMA saw one reduced packet with
  `reduce_count=4`.
- **T-H1.19** In-router scalar value merge composes with GM `write_sum`:
  reduce sources values 1,2,3,4 to GM addr A with `write_sum=0` (overwrite) →
  `GM.read(A) == 10`. Then a second reduce with values 10,20,30,40 to the same
  addr with `write_sum=1` → `GM.read(A) == 110` (in-router sum 100 plus the
  previously stored 10). This exercises both the reducer's scalar Add and the
  existing GM atomic-add path.

**Robustness and regression**

- **T-H1.20** Reduce packet for an unregistered `task_id` raises
  `ProfilingSimError`; a reduce packet arriving at a router that is not on its
  registered tree (e.g. wrong dst routing) raises.
- **T-H1.21** No cross-talk: two concurrent reductions with different
  `task_id`s (disjoint source sets, different roots) each produce exactly one
  result packet with the correct `reduce_count` and scalar `value`; their link
  events are kept separate by `task_id`.
- **T-H1.22** Determinism: two identical T-H1.8 runs produce identical
  per-link event start/end times (including `reduce_count`) and identical
  makespan.
- **T-H1.23** Regression (concrete baseline, no git comparison): with the
  reduce feature present but unused, (a) the original PE0→PE3 smoke workload
  still yields makespan 907 and the same event count as test_smoke, and
  (b) a 4-member multicast still delivers exactly 4 copies (the reduce
  routing branch is inert for trans_type 0–3).

### Out of Scope for H1

- Interaction of reduce with outer sync handshake (pattern C in §7.3); can be
  added as H1.x later. Reductions under test use dual-side, sync=False.
- Element-value correctness of Add/Max over real tensors (no payload model).
- Adaptive reduction-tree construction; the tree is XY-deterministic given
  sources/root.
- `opTypeSrcNode0/1` node-type filtering beyond the children set.

---

## Feature H2 — Arbitration / Priority / Burst / FIFO Flow Control

Spec: [NOC_ARCHITECTURE.md §3.5–3.6](../../NOC_ARCHITECTURE.md). This feature
adds three orthogonal QoS mechanisms to the hop-by-hop router/link model:

1. **Shared-buffer priority** (`shr_buf_port_priority`, 0–3) — non-preemptive
   output-link arbitration; higher priority wins among requesters.
2. **Burst length control** (`burstLenMode` ∈ {-1,0,1,3,7} → {∞,1,2,4,8}
   beats) — a transfer is segmented into bursts; the output link is released
   and re-arbitrated between bursts, with a one-cycle arbitration bubble.
3. **Endpoint FIFO flow control** (`hw_id`/`logic_id`/`check_type`) — a
   credit-counter at the sender blocks injection when the receiver FIFO is
   full; credits are returned when the receiver drains the message.

A "beat" is one link-width transfer (W bytes). All three mechanisms are
backward compatible: defaults reproduce the current baseline exactly.

### Modelling Decisions

1. **New Message fields** (all backward-compatible defaults):
   - `priority: int = 0` — 0–3, maps `shr_buf_port_priority`. Higher value
     wins arbitration. Validated to 0–3.
   - `burst_len_mode: int = -1` — -1=default (one burst per link, current
     behaviour), 0/1/3/7 = 1/2/4/8-beat bursts. Invalid values raise
     `ValueError`.
   - `fifo_hw_id: int = -1`, `fifo_logic_id: int = -1`,
     `fifo_check_type: int = -1` — -1 means "no FIFO" for all three.
     `check_type` 0=WRITE_FULL, 1=READ_EMPTY, 2=UPDATE.
2. **New Event fields**: `priority: int = 0` (carried from the message) and
   `burst_index: int = 0` / `burst_count: int = 1` so per-link QoS traces can
   expose which priority and which burst each event represents.
3. **New config**: `RouterConfig.burst_bubble: int = 1` — the inter-burst
   re-arbitration gap in cycles, paid on every link for every burst boundary.
4. **Priority arbitration (non-preemptive)**: `Link.busy` changes from
   `simpy.Resource(capacity=1)` to `simpy.PriorityResource(capacity=1)`. Each
   burst requests the link with `priority = -msg.priority` (simpy orders
   smaller numbers first). A burst already in flight is **never** preempted;
   once it releases, the highest-priority waiting requester wins. Equal
   priorities remain FIFO (simpy tie-breaks by event id).
5. **Control/sync packets reset QoS fields (BLOCKING fix)**: `_control_msg`
   and `_sync_msg` explicitly reset `fifo_check_type=-1`, `fifo_hw_id=-1`,
   `fifo_logic_id=-1`, `burst_len_mode=-1`, `priority=0` in their
   `model_copy(update=...)`. This prevents a single-side request/ACK or
   sync release/acquire from inheriting the data packet's FIFO id and
   double-returning a credit or entering burst mode.
6. **Burst segmentation in `Link.calc_latency`**: instead of one
   `timeout(tx_time)`, the message is split into bursts on **each** link.
   The number of bytes per burst is `burst_beats * W` (mode -1 → one burst
   covering all bytes). Segmentation uses `msg.total_bytes()` (payload +
   `header_bytes`); the last burst carries the remainder. Per burst:
   acquire `busy` (priority-ordered) → record event `start_time` (the
   acquire instant) → `timeout(ceil(burst_bytes, var_bw))` → set event
   `end_time` → release → (if more bursts) `timeout(burst_bubble)`.
   - `var_bw` (including the single-side ×0.9 factor) is sampled **once per
     link per message** and reused across that link's bursts, preserving
     each link's independent distribution.
   - `LinkConfig.delay` is charged **once per link** on the first burst
     only (never multiplied by the burst count), so mode -1 is unchanged
     and a non-zero `delay` does not scale with segmentation.
7. **Per-burst events / `start_time` semantics (M-3)**: one `Event` is
   recorded per burst. For every mode (including -1), `start_time` is the
   simulation time the burst **acquires** the output link and `end_time` is
   the time its tx finishes (busy released). Under no contention acquire
   time equals the old enqueue time, so every existing contention-free
   baseline is bit-for-bit identical. Under contention `start_time` now
   excludes queuing (previously it was set at enqueue), which makes
   per-event intervals the actual link-busy intervals and avoids
   double-counting in utilization traces. `index2id[msg.index]` points at
   the **last** burst event of the message on that link; `burst_count`
   gives the total.
8. **FIFO registry**: `noc.register_fifo(hw_id, logic_id, depth)` creates a
   `simpy.Container(init=depth, capacity=depth)` of free write slots, keyed
   by `(hw_id, logic_id)`. Valid ranges: hw_id 0–63, logic_id 0–31,
   depth ≥ 1. Duplicate registration or invalid values raise `ValueError`.
   The registry is shared to all routers (like `reduce_trees`).
9. **WRITE_FULL backpressure (non-control SINGLECAST only)**: at the
   **source** router, a non-control SINGLECAST message with
   `fifo_check_type==0` acquires one credit (`yield fifo.credits.get(1)`)
   immediately before it is delivered locally (self-loop) or forwarded into
   the network, blocking injection when the FIFO is exhausted. For
   single-side transfers this acquire sits in the data-forward branch and
   does NOT delay the control-request preamble. At the **destination**
   router, after `route_local` fully completes (the packet has been delivered
   into the node-side receive store, capacity 1 — a slow sink that does not
   drain keeps `store.put` blocked, which in turn delays credit return), one
   credit is returned. The source credit cap is what bounds in-flight
   packets; the capacity-1 receive store merely gates *when* credits return,
   giving indirect slow-consumer backpressure without extra plumbing.
10. **Other FIFO check types**: `UPDATE` (2) returns one credit at the
    destination with no acquire at the sender. The return is **non-blocking
    and capped**: if `credits.level == depth` the credit is silently dropped
    (debug log) instead of blocking the router forever. `READ_EMPTY` (1) is
    accepted but adds no stall (in an event-driven model data is only read
    after it arrives). FIFO checks are ignored for MULTICAST/BROADCAST/REDUCE
    (those trans_types never carry `fifo_check_type != -1` from a DMA
    descriptor in scope; defaults keep them inert).
11. **Priority applies per burst**: because every burst re-requests the
    output link, a high-priority transfer that arrives while a low-priority
    transfer is mid-burst waits only for that one burst to finish, then goes
    ahead of the low-priority transfer's *remaining* bursts. With default
    (-1) the whole message is one burst, so the high-priority transfer waits
    for the entire low-priority message (the "large burst increases
    arbitration latency" effect from §8.3).
12. **No regression**: default `priority=0`, `burst_len_mode=-1`,
    `fifo_check_type=-1` reproduces the unicast baseline
    `(HP+2)*ceil(n/W)+HP` under no contention, and all existing
    smoke/feature/integration tests.

### Concrete Timing Derivations (no contention, deterministic dual-side)

Let W=16, c = ceil(n/W), b = burst_bubble (=1), B = burst beats
(∞ for -1). With deterministic dual-side `var_bw=W` exactly divides every
burst, so per-link tx sums to c cycles. Per-link wall time =
`c + (ceil(c/B)-1)*b`; over HP r2r hops plus 2 local links:

- **Default (-1)**: `(HP+2)*c + HP` (unchanged baseline).
- **Burst B**: `(HP+2)*(c + (ceil(c/B)-1)*b) + HP`.
- Per-hop burst overhead = `(ceil(c/B)-1)*b`; ordering for c>8:
  `-1` (fastest) < `7` < `3` < `1` < `0` (slowest).
- In single-side or non-deterministic mode, per-burst `ceil(burst_bytes,
  var_bw)` rounding makes the exact total `Σ ceil(burst_bytes_i, var_bw) +
  bubbles`, which is NOT guaranteed monotonic in B; burst timing tests are
  therefore run in deterministic dual-side unless explicitly stated.
- **Two equal-priority flows on one link, same time, default**: serialized
  FIFO — second starts exactly when first ends; link events are contiguous.
- **High vs low priority, same time**: high-priority burst has the earlier
  `start_time` on the contended link; with -1 low-priority starts after the
  whole high message; with burst modes it starts after high's first burst.

### Test Cases

All burst-timing tests use deterministic dual-side mode unless stated.
Concrete contention topologies use r4 (row 1, col 0): two *local* ports on
r4 (port 0 and port 1) both route SOUTH to r8, sharing output link r4→r8; a
packet injected on port p at t=0 requests r4→r8 at `c+1` (c-cycle local
link + 1 per_hop). This lets us control exactly when each flow requests the
contended link without pre-traversal bias.

**Priority arbitration**

- **T-H2.1** Equal-priority FIFO baseline: two unicasts on r4 port 0 and
  port 1, both priority=0, n=8W, injected at t=0, dst=8. Both request
  r4→r8 at `c+1`; their two data events on r4→r8 are contiguous (second
  `start_time` == first `end_time`), and total busy time = 2c.
- **T-H2.2** High priority wins: same topology, port-0 packet priority=0,
  port-1 packet priority=3 (and a reversed-order run to prove it is not
  port order). On r4→r8 the priority=3 packet's first `start_time` is
  strictly smaller than the priority=0 packet's, regardless of port.
- **T-H2.3** Non-preemptive: port-0 low-priority (0), n=8W, default -1,
  injected t=0; its single r4→r8 burst runs `c+1 .. 2c+1`. Port-1
  high-priority (3), n=W, injected at t=c/2 so it requests r4→r8 during
  the low burst (after `c+1`, before `2c+1`). Assert the high burst starts
  exactly at `2c+1` (== low end_time) — it waits out the whole burst, never
  preempting.
- **T-H2.4** Priority interacts with burst: same as T-H2.3 but low uses
  `burst_len_mode=7`, n=16W (c=16, two <[PLHD76_never_used_51bce0c785ca2f68081bfa7d91973934]>bursts). Low first burst on
  r4→r8 runs `c+1 .. c+1+8`; the high (n=W) requests during that window.
  Assert high `start_time == c+9` (end of low's *first* burst), high
  `end_time == c+10`, and low's second burst starts at/after high ends
  (high interleaves ahead of low's second burst).

**Burst segmentation**

- **T-H2.5** Default preserves baseline: unicast with `burst_len_mode=-1`
  over HP hops has makespan `(HP+2)*c + HP` exactly (regression anchor).
- **T-H2.6** Segmentation event count on every link: n=8W (8 beats).
  `burst_len_mode=0` produces exactly 8 data events of W bytes on every
  traversed link **including the source and destination local links**;
  mode 1 → 4 events of 2W; mode 3 → 2 events of 4W; mode 7 → 1 event of
  8W. Each event carries correct `burst_index` (0..k-1) and
  `burst_count=k`.
- **T-H2.7** Bubble overhead (no contention): n=8W, HP=1, burst=0. Makespan
  minus the default makespan equals `(HP+2)*(8-1)*b = 21` cycles (one bubble
  per burst boundary on each of the 3 links).
- **T-H2.8** Larger bursts are faster (no contention): for n=16W,
  makespan(burst=0) > makespan(burst=1) > makespan(burst=3) >
  makespan(burst=7) ≥ makespan(-1).
- **T-H2.9** Mode mapping boundary: n=9W with mode 7 (8-beat bursts) yields
  2 bursts per link (8W + 1W); verify event sizes 8W and W and
  `burst_count=2`.
- **T-H2.10** Burst-enabled interleaving: two same-priority flows on r4
  port 0 / port 1, both burst=0 (1-beat), n=4W, injected simultaneously.
  On r4→r8 the 8 burst events strictly alternate between the two flows
  (no two consecutive bursts from the same flow); do not hard-code which
  flow is first.
- **T-H2.11** Single-side per-burst rounding: single-side transfer,
  `burst_len_mode=7`, n=16W (two bursts, 8W each, var_bw=14.4 → each burst
  `ceil(128,14.4)=9`, total 18). Assert per-link busy total == 18 cycles
  (uses exact `Σceil`, not the 0.9× heuristic), and it is larger than the
  dual-side total of 16.
- **T-H2.12** `delay` charged once per link: configure `LinkConfig.delay=2`,
  n=8W, burst=0 (8 bursts). Assert a traversed link's wall time equals
  `8 + delay + 7*b` (delay on the first burst only, not 8×delay); compare
  against an analytic control.

**FIFO flow control**

- **T-H2.13** `register_fifo` validation: duplicate `(hw_id,logic_id)`
  raises; `depth<1` raises; hw_id outside 0–63 and logic_id outside 0–31
  raise.
- **T-H2.14** No FIFO (default): a fast sender injects K=8 packets
  back-to-back to a fast receiver; injection times are all at t≈0 (no
  blocking), and `fifo_check_type=-1` requires no registration.
- **T-H2.15** WRITE_FULL backpressure: fast sender sends K=8 packets
  through FIFO depth D=2 to a **slow** sink (consume time ≫ packet tx) on
  a dedicated dst port. The sender blocks after 2 in-flight packets;
  makespan is bounded below by the slow-receiver drain time and strictly
  exceeds a fast-sink control run. Assert packets 3..K have strictly
  increasing injection timestamps (credit stall), and the FIFO `Container`
  level never drops below 0 / outstanding never exceeds D.
- **T-H2.16** Fast receiver = no stall: same as T-H2.15 but a fast sink;
  credits return immediately, makespan equals the no-FIFO baseline.
- **T-H2.17** Depth scales concurrency: the same slow-sink workload
  finishes sooner with D=4 than with D=1 (makespan(D=4) < makespan(D=1)).
- **T-H2.18** FIFO isolation + no cross-talk: two streams use different
  `(hw_id,logic_id)` pairs, depth 1 each, delivered to **distinct dst
  ports** so they do not share a receive store; each blocks independently
  and a third stream with `check_type=-1` is unaffected by either.
- **T-H2.19** UPDATE credit return is capped: register FIFO depth 2,
  consume one credit with a held WRITE_FULL packet, then deliver an UPDATE
  (`check_type=2`) packet → level returns to 2; delivering a second UPDATE
  while level==2 does NOT block and leaves level at 2 (no overflow).
- **T-H2.20** Control/sync packets do not touch FIFO: a single-side
  WRITE_FULL transfer triggers its request/ACK control packets; assert the
  FIFO credit count is acquired once and returned once (no double return)
  by observing the Container level across the transfer.

**Robustness and regression**

- **T-H2.21** All-default regression: PE0→PE3 smoke unicast (priority=0,
  burst=-1, fifo=-1) still yields makespan 503 and unchanged link event
  count.
- **T-H2.22** Non-unicast unaffected: a 4-member multicast and a 2-source
  reduce still produce correct results with priority/burst fields at
  defaults; setting a burst mode on a multicast does not crash.
- **T-H2.23** Determinism: two identical runs with priority+burst+FIFO
  produce identical per-link event start/end times (including `priority`
  and `burst_index`).
- **T-H2.24** Validation: invalid `burst_len_mode` (e.g. 5) raises
  `ValueError`; `priority` outside 0–3 raises `ValueError`; a WRITE_FULL
  packet referencing an unregistered `(hw_id,logic_id)` raises
  `ProfilingSimError` rather than silently stalling.
- **T-H2.25** Self-loop FIFO: a WRITE_FULL packet with src==dst same router
  acquires one credit before local delivery and returns it after the
  (slow) sink drains; a second packet to the same FIFO blocks until the
  first drains (credit semantics hold for the local case).

### Out of Scope for H2

- Preemptive priority (interrupting a burst in flight).
- Virtual-channel allocation / deadlock avoidance beyond the existing
  per-link capacity-1 serialization.
- End-to-end credit-return *packets* crossing the NoC (credits return
  on delivery; credit-return latency can be added later).
- FIFO flow control for multicast/broadcast/reduce (honoured for non-control
  SINGLECAST only).
- Explicit READ_EMPTY receiver-side gating beyond the natural event-driven
  "data arrives before it is read" semantics.
- Non-deterministic burst-monotonicity guarantees (per-burst ceil rounding
  can violate strict ordering; documented in the timing section).

---

## Feature H3 — 10-Dimensional Advanced Data Layouts

Spec: [NOC_ARCHITECTURE.md §4.4–4.9](../../NOC_ARCHITECTURE.md). The current
simulator models tensor size only as `product(DimSlice extents) * element_bytes`,
which the medium plan "folded into byte count". H3 promotes this to a faithful
**10D strided DMA layout descriptor** with dtype packing, mask/padding, BAUA,
transpose/reorder, and gather/scatter. The NoC still carries one contiguous
byte stream, so the descriptor's profiling-relevant job is to compute **exactly
how many payload bytes traverse the NoC**, the **strided memory footprint**, and
any **endpoint setup overhead** (transpose / gather) charged at injection.

### Modelling Decisions

1. **New model `TensorLayout`** (new `layout.py`, Pydantic v2), attached to a
   `Message` via the optional field `layout: Optional["TensorLayout"] = None`.
   To avoid a circular import, `definitions.py` uses a string forward reference
   and `layout.py` calls `Message.model_rebuild()` at import time (or enums /
   sub-models live in a dependency-free module). Fields:
   - `loop_cnt: List[int]`, `loop_stride: List[int]` — innermost (x0) first,
     equal length. `loop_cnt[i] >= 1`, `loop_stride[i] >= 0` (0 = broadcast on
     byte/word dtypes; sub-byte packing is handled separately, see decision 5).
   - `dtype: int = AdaType.INT8`.
   - `mask_first: Optional[MaskAxis] = None`, `mask_last: Optional[MaskAxis] = None`
     — `{axis, num}` boundary masking along an iteration axis; `pad_value: int = 0`.
   - `baua: Optional[BAUA] = None` — `{base_axis_first, unalign_axis_first,
     num_first, base_axis_second, unalign_axis_second, num_second}`.
   - `transpose: bool = False`, `reorder: int = ReorderMode.NONE`,
     `transpose_overhead: int = 0` (endpoint cycles).
   - `gather: Optional[GatherScatter] = None` — `{enabled, table_addr,
     addr_offset, num_entries, row_bytes, per_row_overhead}`.
   - `is_global: bool = False` — marks the 6D GM/DDR side (validates ndim ≤ 6
     instead of 10).
2. **`AdaType` enum / `ELEM_BITS`**:
   `INT8=0:8, INT16=1:16, INT32=2:32, FP8_E4M3=3:8, FP16=4:16, BF16=5:16,
   FP32=6:32, TF32=7:32, INT64=8:64, FP8_E5M2=9:8, UINT4=10:4, UINT4X2=11:8`.
   `ReorderMode`: `NONE=0, BMM=1, CONV2D=2, BISA=3, CONV3D=4`.
   `elem_bytes = ceil(elem_bits, 8)` (integer ceil `(a+b-1)//b`).
3. **Element counts** (all integer arithmetic):
   - Empty layout (`loop_cnt == []`) is a **zero-element transfer**:
     `element_count_raw() == 0`, payload and footprint both 0.
   - Otherwise `element_count_raw() = ∏ loop_cnt[i]`.
   - Masking removes **iterations along an axis**, not scalar elements.
     Removing `num` iterations on axis `a` discards
     `num * ∏_{j < a} loop_cnt[j]` scalar elements (all inner dims). When
     `mask_first` and `mask_last` share the same axis their `num` are summed
     before multiplying. `effective_element_count() = raw − removed`.
   - **Modelling simplification (vs §4.7)**: the spec's `maskType` (zero-fill on
     download / zeroPoint on upload) implies masked lanes may still cross the
     NoC. H3 models the **skip** variant only — the NMC/DMA does not read or
     inject masked boundary segments on upload, so they reduce payload. This is
     explicitly a profiling simplification; `maskType` is not modelled.
4. **`payload_bytes()`** (bytes that traverse the NoC):
   - Gather/scatter path takes precedence and **ignores mask / cnt / stride**:
     `num_entries * row_bytes`.
   - Otherwise `ceil(effective_element_count * elem_bits, 8)`.
   - Transpose / reorder / BAUA do **not** change payload bytes (BAUA is a
     descriptor-only edge-alignment annotation; it does not alter burst
     segmentation either — documented simplification).
5. **`footprint_bytes()`** (DMA address span / working set; **not** wired into
   NoC or memory timing in H3 — it is a descriptor self-check only):
   - Gather: `num_entries * 8 + num_entries * row_bytes` (8 B table entries +
     rows).
   - Otherwise:
     - innermost axis: if `elem_bits < 8` (sub-byte dtype, i.e. UINT4) its span
       is the packed size `ceil(loop_cnt[0] * elem_bits, 8)` (stride ignored for
       the packed inner axis); for byte/word dtypes it is
       `(loop_cnt[0] - 1) * loop_stride[0] + elem_bytes`.
     - outer axes `i >= 1`: `(loop_cnt[i] - 1) * loop_stride[i]`.
     - `footprint = inner_span + Σ outer_span`.
     This correctly distinguishes packed UINT4 (`cnt=[8] → 4 B`) from a true
     broadcast of a byte dtype (`INT8 cnt=[8], stride=[0] → 1 B`).
6. **`endpoint_overhead_cycles()`** (charged once at source injection):
   `(transpose_overhead if transpose else 0) + (gather.num_entries *
   gather.per_row_overhead if gather else 0)`. Selecting a `reorder` mode
   alone adds 0.
7. **Validation raises `ValueError`**: cnt/stride length mismatch; non-global
   ndim > 10; global ndim > 6; cnt < 1; stride < 0; mask axis out of range
   (`0 <= axis < ndim`), `num < 0`, `num > cnt[axis]`, or same-axis
   `first.num + last.num > cnt[axis]`; BAUA axis out of range (relative to
   `ndim`) or any `num < 0`; unknown dtype / reorder; `transpose_overhead < 0`;
   `pad_value` outside `[0, 2^32)`; gather `num_entries < 0`, `row_bytes < 0`,
   or `per_row_overhead < 0`.
8. **Message integration**:
   - New field `layout: Optional["TensorLayout"] = None`.
   - **Layout is only valid for `TransType.SINGLECAST`.** A `model_validator`
     raises `ValueError` if `layout is not None and trans_type != SINGLECAST`
     (REDUCE / MULTICAST / BROADCAST early-return in `Router.routing` before the
     injection overhead point, so they cannot be charged correctly; H3 forbids
     the combination rather than silently dropping overhead).
   - `Message.byte_size()` returns `layout.payload_bytes()` when a layout is
     attached, else the legacy `Slice.size() * element_bytes`. `total_bytes()`
     still adds `header_bytes`.
   - `Message.__lt__` is changed to compare `byte_size()` so it stays
     consistent with layout messages.
   - Every control/sync `model_copy` site resets `'layout': None`:
     `Router._control_msg`, `Router._sync_msg` (noc.py), **and** the AIU
     `scalar_sync` copy in memory.py. Multicast-replica and reduce-merged copies
     never carry a layout because non-SINGLECAST layouts are rejected.
9. **Endpoint overhead charging**: in `Router.routing`, on the `from_local`
   branch, after the control/sync preamble and `_fifo_acquire`, and for the
   SINGLECAST path only, `yield env.timeout(msg.layout.endpoint_overhead_cycles())`
   when a layout is present. This is paid exactly once at injection (including
   the self-loop / target==self.id local-delivery case) and is 0 for layout-less
   messages and for forwarded/in-transit packets.
10. **No regression / determinism**: layout-less messages reproduce every
    existing baseline; two identical layout runs produce identical per-link
    event timings and `data_size`.

### Concrete Formulas (deterministic dual-side, W=16)

For a non-gather layout, payload `P = ceil(eff_count * B, 8)`, B = elem bits.
A unicast over HP hops has makespan `(HP+2)*ceil(P,W) + HP + O`, where `O` is
the one-time endpoint overhead. Strides affect `footprint_bytes()` only, never
the NoC makespan (the DMA packs strided reads into a contiguous stream).

### Test Cases

**Descriptor / dtype / dimensions**

- **T-H3.1** 1D contiguous: `cnt=[N], stride=[1]`, INT8 → `payload==N`,
  `footprint==N`.
- **T-H3.2** 2D strided: `cnt=[C,R], stride=[1,S]` (x0 innermost) → payload
  `R*C`; footprint `(C−1)*1 + (R−1)*S + 1`.
- **T-H3.3** 10D accepted: `cnt=[2]*10` → raw count 1024, payload 1024 (INT8).
- **T-H3.4** 11 dimensions raise `ValueError`; an empty layout (`cnt=[]`) is
  valid with `element_count_raw()==0`, payload 0, footprint 0.
- **T-H3.5** dtype widths: for `cnt=[100]`, INT16/FP16/BF16 → 200 B,
  INT32/FP32/TF32 → 400 B, INT64 → 800 B, INT8/FP8_E4M3/FP8_E5M2 → 100 B.
- **T-H3.6** UINT4 packing (payload): `cnt=[8]` → 4 B; `cnt=[9]` → 5 B;
  UINT4X2 `cnt=[8]` (8 bits/elem) → 8 B.
- **T-H3.7** UINT4 / broadcast footprint distinction: UINT4
  `cnt=[8], stride=[0]` → footprint 4 B; UINT4 `cnt=[9]` → 5 B; INT8
  `cnt=[8], stride=[0]` (true broadcast) → footprint 1 B.
- **T-H3.8** validation: cnt/stride length mismatch raises, `cnt=0` raises,
  negative stride raises.
- **T-H3.9** global side: `is_global=True` accepts 6 dims, rejects 7;
  non-global accepts 10, rejects 11.

**Mask / padding (axis-iteration semantics)**

- **T-H3.10** mask first: `cnt=[4,4]` (x0=4 inner, x1=4 outer),
  `mask_first={axis:1,num:2}` removes `2*4=8` elements → effective 8,
  payload 8; `mask_last` symmetric.
- **T-H3.11** masks on two axes: `cnt=[4,6]`, `mask_first={axis:1,num:1}`
  removes `1*4=4`, `mask_last={axis:0,num:2}` removes 2 → effective
  `24 − 6 = 18`, payload 18.
- **T-H3.12** invalid mask: axis out of range, `num > cnt[axis]`, same-axis
  `first.num+last.num > cnt[axis]`, and `num < 0` all raise.
- **T-H3.13** `pad_value` round-trips: `cnt=[4,4]`,
  `mask_first={axis:1,num:2}, pad_value=7` → `pad_value==7` and payload 8
  (padding is not added to NoC payload).

**BAUA**

- **T-H3.14** BAUA descriptor accepted with `cnt=[4,4]` and valid axes
  (`< ndim`); out-of-range axis or negative `num` raises; BAUA leaves
  `payload_bytes()` and `footprint_bytes()` unchanged.

**Transpose / reorder**

- **T-H3.15a** enabling `transpose` or any `reorder` mode leaves
  `payload_bytes()` and `footprint_bytes()` identical to the same layout
  without them.
- **T-H3.15b** `ReorderMode` 0..4 accepted; unknown reorder raises.
- **T-H3.16** endpoint overhead: two independent fresh NoC/environment runs,
  no FIFO/contention, deterministic, same 1-hop payload — one with
  `transpose=True, transpose_overhead=K` — differ in makespan by exactly `K`.
- **T-H3.17** reorder-only adds no latency: a `reorder=BMM, transpose=False`
  transfer has the identical makespan as `reorder=NONE`.

**Gather / scatter**

- **T-H3.18** gather payload = `num_entries * row_bytes` and ignores
  cnt/stride.
- **T-H3.19** gather footprint = `num_entries*8 + num_entries*row_bytes`;
  `endpoint_overhead_cycles() == num_entries * per_row_overhead`.
- **T-H3.20** invalid gather (`num_entries<0`, `row_bytes<0`,
  `per_row_overhead<0`) raises.
- **T-H3.21** gather + mask precedence: a layout with both `gather` and
  `mask_first` still has payload `num_entries*row_bytes`, gather footprint,
  and gather overhead (mask ignored, no error).

**Integration / regression**

- **T-H3.22** `Message.byte_size()`/`total_bytes()` delegate to an attached
  layout; `total_bytes()` still adds `header_bytes`; `__lt__` agrees with
  `byte_size()` for layout messages.
- **T-H3.23** SINGLECAST-only: a message with `trans_type=MULTICAST` (or
  BROADCAST/REDUCE) and a non-None layout raises `ValueError`.
- **T-H3.24** backward compatibility: a layout-less PE0→PE3 unicast
  (`data=[DimSlice(0,1600)]`, W=16, HP=3) still has makespan 503 and unchanged
  link-event data size.
- **T-H3.25** control/sync reset: a layout-carrying single-side transfer's
  request/ACK control packets and the AIU `scalar_sync` packet all have
  `layout is None` (verified by intercepting `_control_msg`/`_sync_msg` output
  and an AIU download whose scalar-sync reply is inspected).
- **T-H3.26** end-to-end: a `cnt=[16,8,4], stride=[1,16,128]` INT8 layout
  (payload 512, contiguous footprint 512) PE0→PE1 (HP=1) gives makespan
  `(1+2)*ceil(512,16)+1 = 97`, and every non-control data event on the
  traversed links has `data_size == 512`.
- **T-H3.27** self-loop charges overhead: a PE→itself transfer with
  `transpose_overhead=K` has makespan `2*ceil(P,W) + K` (two local links plus
  the one-time endpoint cost).
- **T-H3.28** determinism: two identical gather+transpose runs produce
  identical per-link start/end times and data_size.

### Out of Scope for H3

- Functional data rearrangement (actually transposing/reordering element
  values in simulated memory) — only byte counts, footprint, and setup overhead
  are modelled.
- A microarchitectural NMC pipeline / tile-buffer timing model for transpose
  and reorder; `maskType` / zero-fill behaviour (only skip-mask payload
  reduction is modelled).
- Runtime index-table contents (gather uses `num_entries`/`row_bytes` only; it
  does not dereference `table_addr`).
- Per-packet stride metadata on the wire (the NoC sees one packed stream).
- Wiring `footprint_bytes()` into the memory/GDDR bandwidth model; it is a
  descriptor self-check in H3.
- Layouts on multicast/broadcast/reduce transfers (rejected at construction).

---

## Feature H4 — Cycle-Accurate Shadow Pipeline Stages

Spec: [NOC_ARCHITECTURE.md §7.5](../../NOC_ARCHITECTURE.md). The hardware exposes
"shadow entry" IDs that track occupancy through each unit's pipeline, with a
**4-entry spacing** (each unit reserves 4 shadow IDs, i.e. up to 4 in-flight
operations). Today every unit is an atomic `simpy.Resource` (TPU/LSU/NMC/DMA)
that serialises operations with no pipelined overlap and no per-stage occupancy
visibility. H4 introduces a reusable, cycle-accurate **pipelined resource**
primitive, the enumerated shadow-entry IDs from §7.5, and opt-in wiring into PE
compute, SRAM controllers, MDMA, and ACI/AdaLink. It is **disabled by default**,
so every existing baseline is unchanged.

### Modelling Decisions

1. **New module `shadow.py`**:
   - `ShadowEntry` (`IntEnum`) with the exact §7.5 values:
     PE matrix `READ_F=4, READ_W=5, CAL=6, WRITE=7`;
     PE vector `READ=8, CAL=9, WRITE=10`;
     SRAMC `DNLD_0=12, UPLD_0=16, DNLD_1=20, UPLD_1=24`;
     MDMA `START=32, CHANNEL_0=36, CHANNEL_1=40, AIU_DOWNLOAD=44`;
     ACI `START=64, FUNC=68, AIU_DOWNLOAD=92`.
   - `ShadowStage` model: `{entry_id: int, latency: int = 0}`.
   - `ShadowPipeline(env, name, stages, occupancy=4, ii=1)`:
     - `stages` is a list of per-stage latencies (ints) or `ShadowStage`s;
       `latency = Σ stage latency`.
     - `slots = simpy.Container(capacity=occupancy, init=occupancy)` bounds
       in-flight operations (the 4-entry spacing → default `occupancy=4`).
     - `admit = simpy.Resource(capacity=1)` + `_next_admit` enforce the
       **initiation interval** `ii` (cycles between two admissions).
     - Two generator APIs:
       - **`enter(tag=None, stage_overrides=None)`** — self-contained traversal
         for PE compute:
         1. `yield slots.get(1)` (back-pressure when full);
         2. inside `with admit.request() as req: yield req` (held across the
            whole check-wait-set): if `now < _next_admit` wait the delta, then
            set `_next_admit = now + ii`;
         3. traverse each stage `yield timeout(latency)` inside try/finally,
            recording one event per stage;
         4. `yield slots.put(1)` in the finally (slot never leaks on interrupt).
       - **`acquire(tag=None)` / `release()`** — for MDMA/AdaLink, where the
         pipeline slot must wrap the caller's existing critical section:
         `slot = yield pipeline.acquire(tag)` performs the slot get + II admit
         and records a stage **enter** at the pipeline's first `entry_id`; the
         caller then runs its body (lane/engine-time / fixed latency);
         `yield pipeline.release()` records the matching **exit** and
         `slots.put(1)`. The slot is held across the body, so occupancy bounds
         overlap correctly.
     - `stage_overrides` is `Dict[int, int]` keyed by the **`ShadowEntry`
       value** (entry id); unknown keys raise `ValueError`. It replaces that
       stage's latency for one traversal only (used to inject the flop-derived
       CAL latency while READ/WRITE stages stay at configured values).
     - Each stage event is the dict `{entry_id, enter, exit, tag}`. Pipelines
       expose their own `events` and `occupancy_log`; `occupancy_log` records
       `(time, in_flight)` where `in_flight = capacity − slots.level`, sampled
       after each get/put resumes. `.occupancy`/`.latency` properties provided.
   - Pipelines are fully deterministic (no RNG).
2. **Config (`config.py`)**: new `ShadowConfig` (pydantic, lists via
   `Field(default_factory=...)`), added to `CoreConfig` and threaded through
   `Arch` to `build_memory_system` / `attach_nodes`:
   ```
   enabled=False, occupancy=4, ii=1,
   matrix=[0,0,0,0], vector=[0,0,0],
   sramc_dnld=0, sramc_upld=0,
   mdma_channel=0, mdma_aiu=0, aci_func=0, aci_aiu=0
   ```
   Validators: `occupancy≥1`, `ii≥1`, all latencies ≥0, `matrix` length 4,
   `vector` length 3; when `enabled`, `nmc.channels ≤ 2` (only SRAMC 0/1
   exist).
3. **PE wiring (`core.py`)** — when `shadow.enabled`, `Core` builds pipelines:
   matrix (4/5/6/7 from `cfg.matrix`), vector (8/9/10 from `cfg.vector`), and
   four single-stage SRAMC pipelines DNLD_0/UPLD_0/DNLD_1/UPLD_1 (each with the
   global `occupancy`; per-channel occupancy, matching the 4-entry spacing).
   It exposes `enter_matrix(tag, cal_cycles)`, `enter_vector(tag, cal_cycles)`
   (thin wrappers building `{CAL: cal_cycles}` overrides), and
   `enter_sramc(channel, upload, tag)` returning the full traversal. All stage
   events are aggregated on `core.shadow_events` (a separate list, never mixed
   into `core.events`).
   **Scheduler changes (all three required):**
   1. `Scheduler.__init__` gains `comp_slots: int = 1`;
   2. `schedule()` pops up to `comp_slots` ready COMP tasks into a
      `comp_tasks: List[Task]` (mirrors the existing `comm_tasks` while-loop);
   3. `execute()` iterates `for ct in comp_tasks: pending.append((ct,"Comp"))`.
   `Core` constructs `Scheduler(..., comp_slots=cfg.occupancy if enabled else 1)`.
4. **Task wiring (`task.py`)** — exact ordering:
   - **CONV** (shadow on): SPM allocate output; compute `cal = flop//tpu.flops`;
     `yield env.process(core.enter_matrix(self.index, cal))` **instead of**
     `tpu.occupy(flops)`; SPM release. `flops` recorded unchanged. With
     `matrix=[0,0,0,0]` the per-op latency is `cal` (same as before), but
     throughput may rise when `occupancy>1`; matrix CONV and vector POOL use
     separate pipelines and may overlap (intended new behaviour).
   - **POOL** (shadow on): same through the vector pipeline with
     `cal = flop//tpu.flops`.
   - **SEND** (shadow on): `nmc.acquire()` → `yield enter_sramc(ch, upload=True)`
     → `yield timeout(start_up_time)` → put successor messages → SPM release →
     `nmc.release(ch)`. The NMC **channel token is held across the UPLD
     traversal**, so UPLD back-pressure/stall keeps the channel reserved.
   - **RECV** (shadow on): `nmc.acquire()` → SPM allocate → `data_in.get()`
     (data must arrive first) → `yield enter_sramc(ch, upload=False)` (DNLD
     writes arrived data into SRAM) → `nmc.release(ch)`.
   - FC unchanged; when shadow is off, every path is byte-for-byte the old
     behaviour. Tests use a large SPM so SPM back-pressure never interferes with
     pipeline-overlap assertions.
5. **MDMA wiring (`memory.py`)**: `DMANode` accepts `shadow_cfg`. When enabled it
   builds `CHANNEL_0` (36)/`CHANNEL_1` (40) selected by the acquired channel
   `ch` (a 1-channel RDMA only builds CHANNEL_0), plus `AIU_DOWNLOAD` (44). In
   `transfer()`: `slot = yield ch_pipeline.acquire()` **before** the
   `with lane.request()` block, run the existing engine-time critical section,
   then `yield ch_pipeline.release()` in `finally`. With default
   `mdma_channel=0` the slot is acquired/released around the unchanged engine
   time (no extra cycles, but occupancy bounds concurrency); a nonzero latency
   adds the stage span. The AIU-download listener enters the AIU pipeline around
   its SRAM accounting. Stage events are stored on `node.shadow_events`.
6. **ACI wiring (`nodes.py`)**: `AdaLinkNode` accepts `shadow_cfg`; when enabled
   it builds `FUNC` (68) and `AIU_DOWNLOAD` (92) ACI pipelines. `handle()`
   selects FUNC (or AIU_DOWNLOAD when `msg.is_aiu`) and `acquire()`/`release()`
   around the existing fixed `timeout(self.latency)`. The AIU branch is an H5
   multi-chip reservation; H4 exercises it with a directly constructed
   `is_aiu=True` message. Events stored on `node.shadow_events`.
7. **Regression anchor**: with `ShadowConfig()` defaults (`enabled=False`), no
   pipeline objects are created and no execution path changes. Enabling shadow
   with `occupancy=1, ii=1` and all stage latencies 0 is timing-equivalent to
   disabled for a single CONV stream (one slot = the old atomic TPU mutex);
   with `occupancy>1` throughput intentionally increases. All 467 prior checks
   must still pass when disabled.
8. **Determinism**: two runs with identical shadow config produce identical
   per-stage event times and occupancies.

### Concrete Timing (deterministic)

For a pipeline with stage latencies summing to `L`, occupancy `D`, and II `g`,
whether ops are all ready at t=0 or injected one per cycle, the `admit`
Resource serialises admissions, so:
- op `i` admits at `i*g`, starts its first stage then, and **exits at
  `i*g + L`** while fewer than `D` are in flight;
- op `D` (0-indexed) cannot get a slot until op 0 exits, so it admits at
  `max(D*g, L)` and exits at `+L`.
Thus N back-to-back ops complete at `L + (N-1)*g` when `N ≤ D`, sustained
throughput one op every `g` cycles.

### Test Cases

**Primitive**

- **T-H4.1** `ShadowEntry` values exactly match §7.5 (matrix 4–7, vector 8–10,
  SRAMC 12/16/20/24, MDMA 32/36/40/44, ACI 64/68/92).
- **T-H4.2** single op via `enter()`: stages `[2,3,4]` ⇒ total latency 9;
  per-stage events have entry IDs in order and `exit-enter` equal each stage
  latency; pipeline occupied for 9 cycles then frees.
- **T-H4.3** pipelined throughput via `enter()`: L=9, D=4, ii=1, 4 ops ready at
  t=0 ⇒ exit times 9,10,11,12; occupancy peaks at 4.
- **T-H4.4** II spacing: ii=2 ⇒ exit times 9,11,13,15.
- **T-H4.5** back-pressure: D=2, L=9, ii=1, 3 immediate ops ⇒ third admits at
  t=9 (first exit), exits at 18 (not 11); occupancy never exceeds 2.
- **T-H4.6** stage overrides keyed by entry id: override `CAL(6)` to 5 with
  other stages 0 ⇒ that op's total is 5 and its CAL event spans 5; a second op
  without override uses the configured latency; an unknown override key raises.
- **T-H4.7** `acquire()/release()` wraps a body: the slot stays occupied across
  a caller `timeout(K)` (occupancy stays 1 during the body), enter/exit are K
  apart, and the slot is returned even if the body raises.
- **T-H4.8** validation: `occupancy<1`, `ii<1`, negative stage latency, wrong
  matrix/vector stage-list lengths, and enabled + `nmc.channels>2` all raise.
- **T-H4.9** determinism: two identical pipelines fed the same schedule produce
  identical event lists.

**PE compute / SRAMC wiring**

- **T-H4.10** default disabled: a LOAD→CONV→STORE DFG on one PE has identical
  makespan/event count with vs without a default `ShadowConfig()`.
- **T-H4.11** enabled `occupancy=1, ii=1, matrix=[0,0,0,0]` is timing-equivalent
  to disabled for a stream of back-to-back CONVs; CONV latency equals
  `flop//flops` and four matrix events (4–7) per CONV land on `core.shadow_events`.
- **T-H4.12** pipeline overlap: two independent CONVs with `occupancy=2, ii=1`
  and nonzero READ/WRITE stages finish `ii` apart; with `occupancy=1` they are
  serial (difference = L).
- **T-H4.13** comp_slots: shadow on, `occupancy=4`, four independent ready COMP
  tasks launch concurrently (overlapping start times); shadow off launches at
  most one at a time.
- **T-H4.14** POOL routes through vector (entries 8–10), not matrix; a
  concurrent CONV+POOL overlap via the two distinct pipelines.
- **T-H4.15** SEND ordering and UPLD: acquire → UPLD → startup → put; nonzero
  `sramc_upld=K` adds exactly K to each SEND; channel 1 records UPLD_1 (24); a
  second SEND contending for the same channel includes the UPLD hold time.
- **T-H4.16** RECV ordering and DNLD: DNLD enter time is strictly after the
  data message arrival (`data_in.get` completes); nonzero `sramc_dnld=K` adds K.

**MDMA wiring**

- **T-H4.17** MDMA default-disabled: `DMANode.transfer` timing unchanged.
- **T-H4.18** enabled MDMA, `mdma_channel=K`: the channel pipeline (36/40 by
  channel id) is held across the engine-time section (occupancy=1 during it),
  adds K cycles, and records one stage event per transfer; two concurrent
  transfers on a 2-channel node overlap up to occupancy.
- **T-H4.19** AIU download traverses MDMA AIU_DOWNLOAD (44): an AIU-port message
  records an AIU shadow event on `node.shadow_events`.

**ACI wiring**

- **T-H4.20** AdaLink default-disabled: `handle` latency unchanged.
- **T-H4.21** enabled AdaLink, `aci_func=K`: each non-AIU message adds K,
  records FUNC (68), and the pipeline is held across the fixed latency; a
  direct `is_aiu=True` message records AIU_DOWNLOAD (92) instead.

**Integration / regression**

- **T-H4.22** end-to-end: a PE0→PE1 transfer whose PE does download(16B)→CONV
  (cal=4)→upload(16B), shadow enabled with matrix `[1,1,4,1]` and
  `sramc_dnld=sramc_upld=2`, produces matrix events 4–7 + DNLD/UPLD events;
  makespan equals the NoC path `(1+2)*1+1=4` (P=W=16, HP=1) plus 2 (DNLD) on
  the receive side, with compute/upload overlapped as scheduled — asserted
  exactly against the computed schedule, not qualitatively.
- **T-H4.23** full regression with `enabled=False`: all 467 prior checks pass
  (PE0→PE3 = 503 etc.).

### Out of Scope for H4

- Power/thermal or area modelling from shadow occupancy.
- Dynamically varying per-stage latency based on operand values (only the CAL
  stage is overridden by the flop-derived latency; all others are static config).
- Inter-instruction hazard/stall logic beyond II + occupancy (structural
  contention only; WAW/WAR/RAW hazard auto-sync is H6).
- The ACI multi-chip routing itself (H5); H4 only attaches the ACI shadow
  pipeline to the existing single-chip AdaLink stub; the `is_aiu` ACI path is
  unit-tested by direct message injection.

## Feature H5 — Multi-Chip AdaLink Routing

Spec: §6 (AdaLink Inter-Chip Interconnect), §6.4 CommID, §6.3 opcodes,
§7.1 CommID Sync level.

The current `AdaLinkNode` is a single-chip fixed-latency sink. H5 promotes it
to a real inter-chip endpoint: multiple `Arch` chips share one `simpy`
environment, AdaLink ports are wired peer-to-peer through `InterChipLink`s,
messages carry a `dst_rank`, and routers egress cross-chip traffic to the
correct AdaLink port. Credit-based CommID flow control, atomic AdaLink
opcodes, and the ALL_ADALINK neighbor fanout complete the model. H4's ACI
shadow pipeline is reused on both egress and ingress.

### Modelling Decisions

1. **Shared environment, ranked chips.** `Arch` gains optional
   `env=None, rank=0`; when `env` is supplied it is reused (instead of
   creating a new one), and `self.rank` is propagated to `NoC`/`Router`.
   Default behaviour (own env, rank 0) is unchanged, so every existing
   caller/test is unaffected.

2. **Relocate table (§6.4).** `AdaLinkRelocateTable` holds 20 entries.
   Entries 0–17 select a local AdaLink *node* (global id 48–65) and store the
   peer's `(remote_rank, remote_port)`; entry 18 is reserved/unused; entry 19
   stores `(self_rank, self_port)`. Per §6.4 the remote `port_id` occupies
   bits [1:0], so it is validated as **0–3** (the 5th physical ADALINK port
   on routers 28/29 exists in the attachment table but is not encodable in
   the 2-bit relocate field). `set(local_id, rank, port)` /
   `lookup(local_id) -> (rank, port)`. `rank >= 0`; wiring the same
   `(rank, port)` twice raises. The pack helper is
   `pack(rank, port) = (rank << 2) | port` and `unpack` reverses it; port
   must fit 2 bits.

3. **Inter-chip link.** `InterChipLink(env, latency, credits=64)` models one
   directed wire with a credit `simpy.Container`. `send(msg)` is a generator
   that runs inside `try/finally`: `yield credits.get(1)` →
   `timeout(latency)` → deliver to the peer's `interchip_in` store; the
   `finally` releases the credit if the send was interrupted before the
   peer accepted it. After the remote AdaLink accepts the message it calls
   `link.return_credit()` (a guarded `credits.put(1)` that never exceeds
   capacity). This is **link-level** credit (the wire buffer frees once the
   remote endpoint has accepted the flit), not end-to-end flow control; it
   is released when ingress processing *starts*, not after the remote NoC
   fully delivers. `credits` must be >= 1. A bidirectional pair is built by
   `MultiChipFabric` for each topology edge.

4. **Fabric.** `MultiChipFabric(env, chip_configs, mappers, topology,
   link_latency=8, credits=64, atomic_latency=4, deterministic=False)`:
   - `chip_configs: Dict[int, ArchConfig]`, `mappers: Dict[int, mapper]`.
   - `topology: List[Tuple[int,int,int,int]]` of
     `(rank_a, adalink_idx_a, rank_b, adalink_idx_b)`, where `adalink_idx`
     is the local AdaLink node index 0–17 (global id 48–65), NOT the 2-bit
     relocate port_id. Each chip side also records the relocate
     `(remote_rank, remote_port)` for observability.
   - Builds one `Arch` per rank (sorted ascending by rank) with the shared
     env and `rank=` set, then pairs the AdaLinkNodes with two
     `InterChipLink`s in topology order, and calls
     `AdaLinkNode.bind_peer(link, remote_rank, remote_port, return_link)`.
     Sorted construction makes same-timestamp process ordering deterministic.
   - Populates `noc.egress[remote_rank] = (router_id, local_port)` on each
     chip from its side of every edge (the router/port of the local AdaLink
     that reaches that rank). A rank reachable through two different ports
     raises (one egress per rank in this model).
   - `execute()` runs the shared env; `chips[rank]`, `links`, and aggregate
     `shadow_events`/`atomic_events`/`interchip_events` are exposed.
   - **Test injection.** H5 validates the interconnect by direct message
     injection, not by DFG-driven cross-chip task placement (the scheduler
     is rank-unaware — out of scope). A source message is launched with
     `env.process(chip.cores[src].data_out.put(msg))` (the PE node→router
     link already attached at router `src`, port 0). A destination probe is
     a `NoCNode` attached to a free local port (e.g. 5) on the destination
     router, with `msg.dst_local_port` set to that port; the test drains
     `probe.data_in[port]`. No second `attach_local(..., 0, ...)` is used
     (port 0 is owned by the PE).

5. **Cross-chip routing.** `Message` gains `dst_rank: int = 0`,
   `adalink_op: int = int(AdaLinkOp.WRITE)` (typed `int`, so unknown opcodes
   are carried opaquely), `imm: int = 0`. `Router` gains `chip_rank` and
   `egress_table` (references set by `NoC.build`). Inside `routing()`:
   - `cross = msg.dst_rank != self.chip_rank`. When `cross` and
     `msg.sync and not msg.is_control`, raise `ProfilingSimError` — outer
     sync handshakes do not cross chips in H5; CommID sync is the cross-chip
     mechanism.
   - When `cross`, target router is forced to
     `egress_table[msg.dst_rank][0]`; an unknown rank raises
     `ProfilingSimError` (no multi-hop relay in H5 — every destination rank
     must be a directly wired neighbour).
   - The SINGLE_SIDE PE↔PE rejection is skipped when `cross` (cross-chip
     writes use DUAL_SIDE by construction).
   - When the egress router is reached (`target == self.id` and `cross`):
     deliver the **original** message via
     `route_local(msg, egress_table[dst_rank][1])` — `route_local` takes the
     port as an argument, so `msg.dst_local_port` is NOT mutated and the
     final-destination port survives the crossing. `_fifo_return(msg)` IS
     still invoked (the FIFO credit protects the source-chip injection and
     must be returned at the chip egress boundary; skipping it would leak
     credits and deadlock WRITE_FULL traffic). The end-to-end single-side
     control-back and sync-acquire packets ARE skipped (they belong to the
     final hop, which has not happened yet), then routing returns.
   - Non-cross paths are byte-for-byte unchanged.

6. **AdaLinkNode egress/ingress split.** A `chip_rank` keyword (default 0)
   is threaded through `attach_nodes(..., rank=0)` /
   `build_memory_system(..., rank=0)` → `AdaLinkNode(..., chip_rank=0)`, so
   direct single-chip construction still defaults to rank 0.
   - Egress (message arrives on a local `data_in[port]`,
     `msg.dst_rank != chip_rank`): run the H4 ACI pipeline (FUNC, or
     AIU_DOWNLOAD when `msg.is_aiu`) wrapping the fixed `self.latency`;
     increment the SEND counter as `peer_link.send` is launched; then
     `yield peer_link.send(msg)`. The ACI slot is released in `finally` so a
     blocked/interrupted send cannot leak a slot.
   - Ingress (a separate `_listen_interchip` process drains
     `interchip_in`): append to both `self.received` and a new
     `self.interchip_events`; increment RECEIVE; acquire the receiving-side
     ACI pipeline (FUNC/AIU) inside its own `try/finally` so the slot is
     always released. For non-AIU messages: if `WRITE_SUM`/`WRITE_MAX`,
     incur `atomic_latency` and append `atomic_event`
     `{time, op, value, index, src_rank}` (time stamped when the atomic
     begins), then re-inject with `self.send(port, msg)` — `msg.dst_rank`
     already equals this chip's rank, so the local NoC delivers to the final
     `msg.dst`/`dst_local_port`. For AIU messages the payload terminates at
     this AdaLink node's local AIU SRAM (§5: 256 KB per AdaLink/DMA node);
     it is NOT re-injected and no scalar outer-sync is generated. Finally
     `return_link.return_credit()`.
   - When no peer is bound (single-chip stub), `handle` keeps the old
     behaviour: RECEIVE counter increment + ACI pipeline around
     `timeout(latency)`, message consumed. This preserves T7/T-H4 tests.

7. **CommID blocking sync.** The existing `acquire_comm_id`/
   `release_comm_id` (increment-and-return, used by T11.11) are kept
   verbatim. A new generator `wait_comm_id(comm_id, counter_type,
   expected)` first checks `commids[counter_type][comm_id & 63] >= expected`
   and returns immediately if already satisfied; otherwise it yields a
   per-`(counter,cid)` `simpy.Event` that is re-armed after each
   `release_comm_id`. `release_comm_id` increments, then succeeds and
   replaces the event (waking all waiters). This models
   `ada_sync_acquire_comm_id` / `ada_sync_release_comm_id` (§6.4) without
   touching the stub semantics.

8. **Atomic opcodes (§6.3).** `AdaLinkOp(IntEnum)`:
   `WRITE=0x02, WRITE_SUM=0x03, WRITE_MAX=0x05,
   WRITE_WITH_IMM2=0xC2, WRITE_SUM_WITH_IMM2=0xC3,
   WRITE_MAX_WITH_IMM2=0xC5` (two immediate data words = 8 bytes, per spec).
   On ingress, SUM/MAX add `atomic_latency` and record an event; the model
   does not mutate memory state — the atomic RMW latency is the
   architecturally visible effect. `value` carries the operand, `imm >= 0`
   (no upper-bound check).
   `Message.imm_bytes` is a property returning 8 for the three
   `_WITH_IMM2` opcodes and 0 otherwise. `Link.calc_latency` adds
   `msg.imm_bytes` to the **first burst's** `tx_bytes` (in addition to
   `header_bytes`), so immediates travel in the header and add
   `ceil(imm_bytes, W)` beats on every link the packet traverses.
   `byte_size()` (payload only) is unchanged; a new
   `total_bytes_with_imm()` test hook reports `byte_size()+header_bytes+
   imm_bytes`.

9. **ALL_ADALINK (port 21) cross-chip fanout.** A broadcast is sent to a
   router's ALL_ADALINK port with `dst_rank == chip_rank` (local delivery to
   port 21). The existing per-router fanout clones the message to every
   local AdaLink node using `model_copy()`. Each AdaLinkNode, when
   `port == ALL_ADALINK (21)`, ignores `msg.dst_rank` and forwards the clone
   through its OWN bound peer link (if a peer is bound); AdaLink nodes with
   no peer keep the single-chip sink behaviour (latency, consume). This is
   the one place per-node peer selection overrides the egress table, and is
   what makes a single port-21 injection reach every directly-wired
   neighbour. Unicast cross-chip traffic instead uses the specific AdaLink
   port (16–20) chosen by the egress table.

10. **Determinism.** With `deterministic=True`, all link bandwidth is the
    gamma mean (W=16 B/cyc ⇒ 1 cyc per 16-B beat), and there is no RNG. A
    16-B control message costs 1 cycle of link tx per link plus 1 cycle
    per intermediate router (`per_hop_time`), matching the formula used by
    T9/T-H2: `(links_traversed)*ceil(bytes/W) + (routers_traversed)*1`.

### Out of Scope (H5)

- Multi-hop relay through an intermediate chip (rank must be a direct
  neighbour). Source routing / DOR across chips is future work.
- True shared memory semantics for WRITE_SUM/MAX (latency + event only).
- The `RNIC_PRODUCE` counter beyond being stored in the table.
- Bit-accurate DLCM encoding of the relocate table (stored as Python
  ints; entry 19 self rank/port is exposed).
- Hot-swap / topology reconfiguration at run time.

### Concrete Timing (deterministic, W=16, per_hop=1, shadow disabled)

For a 16-B message (1 beat, no header), a path with `H` r2r hops on the
source chip, `H'` r2r hops on the destination chip, inter-chip link latency
`L`, and (for atomics) `A`, the dual-side NoC cost per chip is
`(hops+2)*beats + hops` (matching T9/T-H2: source local link + H r2r links +
dest local link, plus one `per_hop_time` per intermediate router):

```
egress NoC   = (H + 2) * 1 + H          = 2H + 2   (local src + H r2r + egress local)
inter-chip   = L
ingress NoC  = (H' + 2) * 1 + H'        = 2H' + 2  (ingress local + H' r2r + local dst)
atomic       = A   (only WRITE_SUM/MAX on ingress)
total        = 2H + 2 + L + 2H' + 2 [+ A] = 2H + 2H' + L + 4 [+ A]
```

Reference topology used by T-H5.14/15 (y_size=4, so router id = x*4 + y):
- Chip 0 PE0 (router 0 = (0,0)) → egress AdaLink on router 28 = (7,0):
  7 EAST hops ⇒ H=7 ⇒ egress NoC = 16.
- Link latency L.
- Chip 1 ingress AdaLink on router 28 = (7,0) → dest router 31 = (7,3):
  3 SOUTH hops ⇒ H'=3 ⇒ ingress NoC = 8.
- Plain WRITE total = 16 + L + 8 = 24 + L.
- WRITE_SUM total = 24 + L + A.

When shadow is enabled with `aci_func=k` and `adalink_latency=0`, each side
adds `k` (the ACI FUNC stage), so the cross-chip delta vs disabled is `2k`
(egress + ingress). A `_WITH_IMM2` opcode adds 8 header bytes, i.e. one
extra beat on every (sub-)link; across the full path that is
`(H+2)+(H'+2) = H+H'+4` added cycles.

### Test Cases

**Relocate table & config**
- T-H5.1  default table has 20 entries (0–19); lookup of an unset entry
  raises KeyError.
- T-H5.2  set/local lookup round-trips for local indices 0–17; entry 19
  holds self `(rank, port)`.
- T-H5.3  port >3 or <0 raises (2-bit field); rank<0 raises; duplicate
  wiring of the same remote `(rank, port)` raises; entry 18 is reserved and
  set/lookup raises.
- T-H5.4  pack/unpack helpers: `pack(rank,port) == (rank<<2)|port`;
  unpack reverses it; port 3 sets only bits [1:0], rank starts at bit 2.

**Message**
- T-H5.5  defaults: dst_rank=0, adalink_op=WRITE(0x02), imm=0,
  `imm_bytes==0`.
- T-H5.6  dst_rank<0 raises; negative imm raises; an unknown adalink_op
  (e.g. 0x7F) is accepted because the field is `int` (opaque carry).
- T-H5.7  three `_WITH_IMM2` opcodes report `imm_bytes == 8`; plain WRITE
  reports 0; `byte_size()` is unchanged by imm; `total_bytes_with_imm()`
  equals `byte_size()+header_bytes+imm_bytes`.

**InterChipLink**
- T-H5.8  single message latency == L (peer interchip_in fires at t=L).
- T-H5.9  credits=1: second concurrent send blocks until the first credit
  is returned; after `return_credit()` the second proceeds with a gap of L.
- T-H5.10 credits<1 raises; `return_credit()` never raises when the
  container is already at capacity (capped).

**Egress routing (2-chip fabric, shadow off, adalink_latency=0, L=0)**
- T-H5.11 inject a dst_rank=1 16-B message via
  `env.process(chip0.cores[0].data_out.put(msg))`; it is consumed by the
  egress AdaLink (appears in its `interchip_events`/egress record) and does
  NOT arrive at any chip0 PE data path.
- T-H5.12 at the egress router the message is delivered to the AdaLink port
  from the egress table (16–20) while `msg.dst_local_port` is unchanged;
  the message forwarded to the peer preserves original `dst`,
  `dst_local_port`, `index`, `data`, `value`.
- T-H5.13 dst_rank pointing at an unwired rank raises ProfilingSimError.
- T-H5.13b a cross-chip message with `sync=True` raises ProfilingSimError
  (outer sync does not cross chips).

**End-to-end cross-chip**
- T-H5.14 chip0 PE0 → chip1 router31 probe (probe attached on free local
  port 5): arrival time == 24 + L for 16 B; message fields preserved.
- T-H5.15 WRITE_SUM adds `atomic_latency` (arrival == 24+L+A) and records
  exactly one `atomic_event` with op SUM and the operand value; WRITE_MAX
  records op MAX; plain WRITE records zero atomic events.
- T-H5.16 immediates add latency at the Link level: a 16-B plain WRITE on a
  single isolated link takes 1 beat, while a WITH_IMM2 message takes
  `ceil((16+8)/16)=2` beats; in the reference e2e path WITH_IMM2 adds
  `H+H'+4 = 14` cycles; `imm` is preserved.
- T-H5.17 3-chip chain 0↔1↔2: 0→1 and 1→2 deliver; 0→2 raises
  ProfilingSimError (no direct neighbour).
- T-H5.18 ALL_ADALINK broadcast: inject a message to chip0 router 28 with
  `dst_local_port=21` and `dst_rank=0`; with two wired neighbours both peer
  chips receive it; a third, non-wired chip does not.

**CommID sync**
- T-H5.19 `wait_comm_id` fast path: already-satisfied expected returns at
  t=0; otherwise blocks until `release_comm_id` reaches expected (spawn a
  waiter at expected=3, release 1/2/3, unblock exactly at the third); two
  concurrent waiters at the same expected both wake.
- T-H5.20 per cross-chip transfer: SEND counter increments on the egress
  node and RECEIVE on the ingress node (index `comm_id & 63`); the egress
  InterChipLink credit is consumed and refilled after the ingress node
  accepts the message.
- T-H5.20b an `is_aiu=True` cross-chip message terminates at the ingress
  AdaLink (records ACI entry 92, appended to `received`/
  `interchip_events`) and is NOT re-injected into the remote NoC.

**Backward compatibility & integration**
- T-H5.21 single-chip Arch (no fabric): AdaLink still sinks a message in
  exactly `adalink_latency` with shadow disabled and records no
  interchip/atomic events; the existing f7/f11/T-H4 suites remain green.
- T-H5.22 shadow enabled with `aci_func=k`, adalink_latency=0: a plain
  cross-chip e2e is the disabled baseline + 2k; FUNC entry 68 appears once
  on each side; an AIU message shows entry 92 on each side and terminates
  at ingress.
- T-H5.23 regression gate: all pre-H5 test suites (smoke/features/f6–f11/
  integration/h1–h4) still pass with zero failures when H5 is built but no
  fabric is configured (default `dst_rank=0` paths are unchanged).

---

## Feature H6 — Pipeline Auto-Sync Framework (§7.4)

The `cute::ada::Pipeline` template tracks per-buffer state across pipeline
stages and inserts inner sync (release/acquire) to resolve buffer hazards.
It only models **buffer readiness** — not protocol-level sync inside IO
helpers and not per-unit busy/occupancy (that is H4 shadow). This feature
adds a standalone, reusable discrete-event primitive in `pipeline.py`
(analogous to `ShadowPipeline`) plus direct unit tests; it is **not** wired
into the DFG `Task` path (DFG tasks do not carry `(buf_id, chunk)`
granularity).

### Modelling decisions

1. **Buffer key**: each buffer is `(buf_id, chunk)`. `chunk` must be a
   non-negative int (including `0`); `buf_id` is an int/IntEnum/hashable
   (IntEnum with equal value hashes/equals the matching int). Different keys
   are fully independent, as are different `Pipeline` instances.
2. **State per key** (lazily created on first access):
   - `write_event` (simpy.Event): succeeds when the latest *registered*
     write finishes. A fresh key starts with an already-succeeded event (no
     prior producer).
   - `reads_event` (simpy.Event): succeeds when all in-flight reads
     finish. A fresh key starts with an already-succeeded event.
   - `read_count` (int): number of in-flight reads (multiple readers
     coexist; no R-R hazard).
   - **Lifecycle/reset**: a `simpy.Event` can only succeed once, so the
     scoreboard *publishes fresh events* on each new registration:
     - a WRITE registration captures the current `write_event`/`reads_event`
       as its hazards, then replaces `write_event` with a new pending event
       and resets `reads_event` to a new pending event + `read_count=0`.
     - a READ registration, when `read_count` transitions `0→1`, replaces
       `reads_event` with a new pending event (the old one has already
       succeeded); it then increments `read_count`.
     - READ release decrements `read_count`; when it reaches `0` the current
       `reads_event` is succeeded. Re-succeeding an event is impossible by
     construction.
3. **Hazards** (resolved only in auto mode, or explicitly in manual mode):
   - **RAW** (read after write): a reader waits for the captured
     `write_event`.
   - **WAW** (write after write): a writer waits for the captured
     `write_event`.
   - **WAR** (write after read): a writer waits for the captured
     `reads_event` (all in-flight readers drain).
   - R-R never stalls.
4. **Register-before-wait (atomic publish)**: `write`/`read` FIRST capture
   the current hazard events and publish their own pending release event
   into the scoreboard, THEN yield on the captured (previous) hazards.
   Because simpy is single-threaded, the publish happens atomically at the
   call instant with no yield in between. This guarantees:
   - multiple concurrent waiters on the same prior event cannot clobber each
     other's release events (each published itself before yielding);
   - a late accessor always observes an already-published pending event, so
     a "pending writer + late reader" correctly resolves as RAW (reader
     waits for the writer).
   This mirrors `ShadowPipeline.acquire`, which reserves a slot before
   admitting.
5. **Three-phase event model**. Each access emits phases into
   `pipeline.events`:
   - `register`: logged immediately when `write`/`read` publishes the
     access (always at call time, in both modes).
   - `acquire`: logged when hazards are clear and the slot is safe to use.
     In auto mode this is logged inside `write`/`read` right before the slot
     is returned. In manual mode `write`/`read` return WITHOUT an acquire
     event; `wait()` logs `acquire` when it returns.
   - `release`: logged when `slot.release()` runs (once; double-release is a
     no-op and does not log a second event).
   The hazard invariant (T-H6.16) is defined over `acquire`/`release`
   times, which is well-defined in both modes.
6. **Acquire/release API**: `write(buf, chunk)` / `read(buf, chunk)` are
   generators that (register → [auto: wait hazards + log acquire]) and
   return a `BufferSlot`. The caller does its work, then calls
   `slot.release()` (idempotent; safe in `try/finally`).
   - WRITE release: succeeds the published `write_event`.
   - READ release: decrements `read_count`; when it hits 0, succeeds
     `reads_event`.
   - `produce`/`consume` are aliases for `write`/`read`.
   - `BufferSlot` public attributes: `buf_id`, `chunk`, `access`,
     `register_time`.
7. **Two modes**:
   - `auto_consume=True` (default): `write`/`read` automatically wait for
     hazards and emit `acquire` before returning the slot.
   - `auto_consume=False`: `write`/`read` only register+publish and return
     immediately (no wait, no `acquire` event). The caller must explicitly
     `yield pipeline.wait(buf, chunk, access)` to insert the sync, BEFORE
     calling `write`/`read` so the barrier observes the pre-registration
     scoreboard. Two manual callers that both `wait()` at the same instant
     pass together — same-mode serialization in manual mode is the caller's
     responsibility (matching "sync insertion is explicit").
8. **`wait()` semantics**: a one-off snapshot barrier. It captures the
   current scoreboard state for the key at call time and yields:
   - WRITE: `write_event & reads_event` (WAW + WAR); READ: `write_event`
     (RAW). It does NOT register/publish anything and does not return a
   slot. It is usable in both modes (redundant but legal in auto mode) and
   logs an `acquire` event when it returns (only relevant/needed in manual
   mode; to keep logs clean auto-mode callers should rely on write/read's
   own acquire). Invalid `access` raises `ValueError`.
9. **No fixed sync latency**: an inner sync is an event dependency. If the
   producer already finished, the consumer resumes at the very same cycle
   (0-cycle overhead); if not, it resumes exactly when the producer
   releases. No extra constant cycles are added (protocol-level sync cost
   is out of scope).
10. **Scope/limits**: one `Pipeline` instance models one PE/kernel pipeline
    domain and does not share state with other instances. It does not bound
    in-flight access count (occupancy/II belong to H4); hazard ordering is
    its only concern. Negative `chunk` or a non-int `chunk` raises
    `ValueError`. `work`/delays are the caller's responsibility (passed to
    `env.timeout`); the primitive does not validate them.
11. **Composition with H4 shadow**: recommended order is
    `slot = yield pipeline.write/read(...)` (resolve buffer hazard) BEFORE
    `yield shadow.enter/acquire(...)` (occupy the unit pipeline), so an
    access blocked on a buffer does not hold a shadow occupancy/II slot.
    Tests pin this ordering.

### Out of scope

- Auto-deriving hazards from high-level IO calls (`send_with_sync` etc.) —
  those wrappers belong to f11 outer sync and are not modelled here.
- Per-unit busy state, initiation interval, pipeline occupancy (H4).
- Protocol/timing cost of inner sync packets (zero-cost event only).
- Wiring `(buf_id, chunk)` onto DFG tasks / mappers.

### Concrete timing

A `worker(env, pipe, access, buf, chunk, work, start=None)` helper:
optionally waits until `start`, then `slot = yield pipe.write/read(...)`,
records `acquire_t = env.now`, `yield env.timeout(work)`, records
`release_t = env.now`, `slot.release()`.

- RAW: W work=10, R work=5 both launched at t=0 → R.acquire=10,
  R.release=15; W.release=10.
- WAW: two W work=10 at t=0 → acquires at 0 and 10; total 20.
- WAR: R work=10 and W work=5 at t=0 → W.acquire=10, W.release=15.
- R-R: two R work=10 at t=0 → both acquire at 0, both release at 10.
- 2 readers (work=10 each) + writer (work=5) at t=0 → writer acquires at
  10, releases at 15 (waits for *both* readers).
- Double buffering: W0→R0 and W1→R1 each work=10 launched together →
  makespan 20 (chunks independent), vs single-buffer serial 40.
- Ready path: W finishes at 10, R starts at 20 → R.acquire=20 (no extra
  cycles; sync overhead 0).

### Test cases

- **T-H6.1** construction & validation: `auto_consume=True` default,
  `events==[]`, fresh keys lazily created; negative `chunk` and non-int
  `chunk` (float/str/None) raise `ValueError`; `chunk=0` is accepted.
- **T-H6.2** write slot + three-phase log: fresh-buffer `write` registers at
  t=0 and auto-acquires at t=0 (no prior hazard), returns a BufferSlot with
  `access==WRITE`, `buf_id/chunk/register_time` exposed; release logs a
  `release` event; double-release is a no-op (no exception, no second
  release event, event not re-succeeded).
- **T-H6.3** read slot + aliases: `read` returns `access==READ`;
  `produce`/`consume` behave identically to `write`/`read`, including
  cross-alias hazards (`produce`→`consume` RAW, `produce`→`produce` WAW).
- **T-H6.4** independent keys/instances: W on (0,0) and R on (0,1) launched
  together do not stall (acquire==0); a different `buf_id` likewise; two
  `Pipeline` instances accessing the same `(buf_id, chunk)` do not
  interfere (instance isolation).
- **T-H6.5** RAW: R cannot acquire before W releases; R.acquire==W.release;
  event log `WRITE.release` time <= `READ.acquire` time.
- **T-H6.6** WAW: two concurrent writers serialize; second acquire == first
  release; makespan == work1+work2; their in-use intervals do not overlap.
- **T-H6.7** WAR: concurrent writer blocks until the reader drains;
  W.acquire == R.release.
- **T-H6.8** R-R parallel: two concurrent readers both acquire at t=0 and
  overlap; no serialization; `read_count` returns to 0 after both release.
- **T-H6.9** multiple readers + writer: writer waits for ALL readers (not
  just one); W.acquire == max(reader releases).
- **T-H6.10** hazard chain: W1→R→W2 on one buffer serializes in order;
  acquire times are W1=0, R=rel(W1), W2=rel(R).
- **T-H6.11** zero-cost ready path: when a producer finishes before the
  consumer asks, the consumer acquires at its own start cycle (sync adds 0
  cycles). Also covers `work=0`: acquire and release share a timestamp with
  no error.
- **T-H6.12** manual mode no auto-stall: `auto_consume=False`, concurrent W
  and R on the same buffer both `register` at t=0 and both run to
  completion concurrently (no implicit wait; no `acquire` event logged by
  write/read).
- **T-H6.13** manual explicit RAW barrier: in manual mode a reader calls
  `wait(..., READ)` before `read()`; it blocks until the writer releases,
  `wait` logs `acquire`, and the reader proceeds. Without `wait` the reader
  does not block (cross-checked against T-H6.12).
- **T-H6.14** manual explicit WAW+WAR barrier: in manual mode a writer calls
  `wait(..., WRITE)`, which waits for BOTH the prior in-flight writer (WAW)
  and in-flight readers (WAR). A second manual writer that does NOT wait
  runs concurrently (caller responsibility).
- **T-H6.15** double buffering: two chunks pipeline; W0/R0 and W1/R1 run
  concurrently with makespan equal to a single W+R chain (20), not the
  serial sum (40).
- **T-H6.16** event log fidelity: every slot emits register and (in auto
  mode, or after manual `wait`) acquire and release; phases/access labels
  correct; event timestamps non-decreasing; for every RAW/WAW/WAR pair the
  blocked access's `acquire` >= the hazard producer's `release`; double
  release emits no duplicate event.
- **T-H6.17** late waiter (release-before-wait): a slot released at t=5; a
  reader calling `read()` at t=20 registers and acquires immediately (event
  already succeeded) without blocking.
- **T-H6.18** N-concurrent-writer stress: N=32 writers with mixed start
  delays/work lengths on the same buffer all serialize; makespan equals the
  sum of their work; a scan of all slot intervals proves no two WRITE slots
  overlap and no READ overlaps an unreleased WRITE.
- **T-H6.19** combined WAW+WAR: at t=0 reader R1 (work=4) and writer W1
  (work=1) start; W1 waits on R1 (WAR), so W1.acquire=4, W1.release=5. A
  second reader R2 starts at t=0 after W1 registered; it sees W1's pending
  write (RAW) so R2.acquire=5, R2.release=15. Writer W2 arrives at t=1
  (work=5); W2 must wait for BOTH W1 (WAW, release 5) and R2 (WAR, release
  15) → W2.acquire == 15 (not 5), W2.release == 20. This proves WAW and WAR
  are waited on independently rather than only transitively.
- **T-H6.20** pending writer + late reader (register-before-wait): R1 starts
  at t=0 (work=10); W registers at t=0 and waits for R1; R2 arrives at t=2.
  Because W published itself first, R2 observes W's pending write event and
  waits (RAW): W.acquire == R1.release == 10; R2.acquire == W.release. This
  would fail under a wait-before-register design.
- **T-H6.21** `wait()` behaviour: valid READ/WRITE snapshot barriers work in
  both modes; invalid `access` ("BOGUS"/non-enum) raises `ValueError`;
  calling `wait()` does not register state or alter `read_count`.
- **T-H6.22** real-work + H4 composition: pipeline gates `env.timeout` /
  `TPU.occupy` regions; single-buffer download→compute makespan == D+C;
  double-buffer (compute chunk0 while downloading chunk1) makespan <
  2*(D+C). Hazard wait (`pipeline.write/read`) is issued BEFORE
  `ShadowPipeline.enter`, and a blocked hazard does not consume a shadow
  occupancy slot (verified via shadow `occupancy_log`).
- **T-H6.23** no DFG regression: a trivial single-core DFG (LOAD→COMP→STORE)
  built and executed without referencing any pipeline has identical
  makespan to the pre-H6 baseline (the module is opt-in and does not alter
  Core/Task behaviour).
- **T-H6.24** `buf_id` typing: an `IntEnum` `buf_id` and the equal plain
  int refer to the same scoreboard entry (hash/equal); distinct enum values
  do not.
- **T-H6.25** full regression gate: all pre-H6 suites (smoke/features/f6–
  f11/integration/h1–h5) still pass with zero failures.
