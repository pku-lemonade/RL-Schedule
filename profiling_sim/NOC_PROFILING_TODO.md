# NoC Profiling TODO

This document tracks all microarchitectural parameters and behavioral details
that are currently **assumed or estimated** in `profiling_sim/` and must be
confirmed by profiling on real hardware (ADA2S) or TSIM before the model can
be considered cycle-accurate.

Items are grouped by subsystem. Each entry lists the current assumption, where
it lives in the code, and what to measure.

---

## 1. NMC (Network-to-Memory Controller)

### 1.1 NMC startup latency
- **Assumption:** `NMCConfig.start_up_time = 1` cycle.
- **Reality:** NMC startup is believed to be ~1000 cycles (hardware boot /
  channel wake-up). This has a large impact on short transfers.
- **Where:** `config.NMCConfig.start_up_time`, wired into `core.NMC.__init__`.
- **Measure:** Issue a single 1-flit transfer from PE to PE and measure the
  first-flit injection delay on TSIM. Compare against the `start_up_time`.
- **Action after profiling:** Update default; consider modeling this as a
  one-time per-channel power-up cost rather than per-message overhead.

### 1.2 NMC injection port sharing (C1)
- **Assumption:** `NMCConfig.injection_ports = 0` (unlimited). Hardware has
  CH0 and CH1 sharing one physical injection port.
- **Where:** `config.NMCConfig.injection_ports`, `core.NMC.injection_ports`.
- **Measure:** Determine whether CH0 and CH1 can inject simultaneously or
  serialize. If serialized, measure the arbitration grant delay.
- **Action after profiling:** Set default to 1 if shared, or 2 if independent.

### 1.3 NMC channel count and width
- **Assumption:** 2 channels, width derived from link width (16 B/cycle).
- **Where:** `NMCConfig.channels`.
- **Measure:** Confirm channel count and per-channel injection bandwidth.

---

## 2. Router Pipeline

### 2.1 Pipeline stage latencies
- **Assumption:** Single `per_hop_time = 1` cycle models the entire router
  pipeline (BW + RC + VA + SA + ST) as one cycle per hop.
- **Reference (BookSim2):** Canonical 4-stage pipeline:
  - **BW** (Buffer Write): 1 cycle
  - **RC** (Route Compute): 1 cycle
  - **VA** (VC Allocation): 1 cycle
  - **SA** (Switch Allocation): 1 cycle
  - **ST** (Switch Traversal): 1 cycle
  - **LT** (Link Traversal): 1 cycle
- **Where:** `Router.per_hop_time` in `noc.py`.
- **Measure:** On TSIM, measure per-hop latency for a single-flit packet under
  no contention. Determine whether the pipeline is 4-stage or 5-stage and
  whether speculative VA/SA removes a cycle for the head flit.
- **Action after profiling:** Split `per_hop_time` into per-stage delays;
  model head-flit vs body-flit latency differences.

### 2.2 Speculative VA/SA
- **Assumption:** Not modeled; every flit pays the full per-hop cost.
- **BookSim2:** Speculative allocation can save 1 cycle per hop by performing
  VA and SA in parallel.
- **Measure:** Determine if the hardware router uses speculative allocation
  and under what conditions it succeeds/fails.
- **Action after profiling:** Add a `speculative` flag and model the 1-cycle
  savings on successful speculation.

### 2.3 Switch allocator algorithm
- **Assumption:** Round-robin separable allocator (implicit in SimPy
  resource ordering).
- **Where:** Switch allocation is implicitly handled by `simpy.Resource`
  request ordering in `routing()`.
- **Measure:** Confirm the allocator type (round-robin vs matrix vs
  wavefront). Measure priority modifier behavior (`shrBufPortPriority`,
  4-level priority).
- **Action after profiling:** If non-round-robin, implement a more faithful
  allocator.

### 2.4 Switch hold policy
- **Assumption:** Flit-level wormhole switching; once a switch output is
  allocated, it is held per-flit.
- **Risk:** Throughput modeling error can exceed 20% if the hardware uses
  burst-level hold (packet-level) instead of flit-level.
- **Measure:** Observe whether a multi-flit packet holds the crossbar for its
  entire duration or releases per-flit.
- **Action after profiling:** Add a configurable `switch_hold` policy
  (`flit` vs `burst`/`packet`).

---

## 3. Flow Control & Buffers

### 3.1 VC count and depth
- **Assumption:** `RouterConfig.vc = 2` VCs. FIFO depth is set per-port via
  `register_fifo()`; default depth not standardized.
- **Where:** `RouterConfig.vc`, `NoC.register_fifo()`.
- **Measure:** Confirm the number of VCs per port, buffer depth per VC, and
  which traffic classes map to which VCs.
- **Action after profiling:** Set correct defaults; verify credit-based
  flow control round-trip latency.

### 3.2 Credit return latency
- **Assumption:** Credits are returned instantly (same-cycle via
  `simpy.Container.put`).
- **Where:** `routing()` credit return path (~line 335).
- **Measure:** Measure the actual credit round-trip latency (receiver to
  sender). This affects effective throughput under load.
- **Action after modeling:** Add a `credit_latency` parameter.

### 3.3 Header size
- **Assumption:** 4-byte routing header (`Message.header_bytes = 4`),
  consistent with `build_info1`.
- **Where:** `definitions.Message.header_bytes`,
  `noc.Router.routing()` (adds header to byte count).
- **Measure:** Confirm the exact header format and size. Determine if
  multicast/reduce/sync headers are larger.
- **Action after profiling:** Adjust default if different; per-message-type
  header sizes if needed.

---

## 4. Concentrated Routers (C2)

### 4.1 Local injection/ejection capacity
- **Assumption:** `RouterConfig.local_injection_capacity = 1` for
  concentrated routers [0, 3, 28, 29, 30, 31]. All other routers unlimited.
- **Where:** `Router.local_inj` (simpy.Resource),
  `NoCConfig.concentrated_routers`.
- **Measure:** Determine how many concurrent local-port inject/eject
  operations each concentrated router can sustain. Routers 28-31 handle
  PE + DDR + GM traffic and are the primary bottlenecks.
- **Action after profiling:** Set capacity to measured value; verify whether
  injection and ejection share the same resource or have separate limits.

### 4.2 Concentrated router port map
- **Assumption:** Routers [0, 3, 28, 29, 30, 31] are concentrated.
- **Measure:** Confirm exactly which router IDs aggregate multiple local
  devices and which devices share each.

---

## 5. PE SRAM Ports (C3)

### 5.1 SRAM read/write port count
- **Assumption:** `CoreConfig.sram_read_ports = 0` and
  `sram_write_ports = 0` (unlimited). Real PE SRAM has a finite number of
  ports shared by ~7 masters (TPU, LSU, NMC, DMA, etc.).
- **Where:** `CoreConfig.sram_read_ports` / `sram_write_ports`,
  `core.Core.sram_read` / `sram_write`, wired into `task.py`.
- **Measure:** Count physical SRAM read and write ports. Determine which
  masters share which ports and the arbitration priority.
- **Action after profiling:** Set correct port counts; model per-port
  arbitration if masters have asymmetric access.

---

## 6. DMA Engine (C4)

### 6.1 Scalar core dispatch interval
- **Assumption:** `DMAEngineConfig.dispatch_interval = 0` (no serialization).
- **Where:** `DMAEngineConfig.dispatch_interval`,
  `dma.DMAEngine.dispatch`, `memory.DMANode.dispatch`.
- **Measure:** The DMA scalar core issues commands one at a time. Measure the
  gap between consecutive command issues (dispatch interval in cycles).
- **Action after profiling:** Set the measured interval. Determine whether it
  is fixed or depends on transfer size/type.

### 6.2 DMA engine width and channels
- **Assumption:** `DMAEngineConfig.width = 16`, `channels = 2`.
- **Where:** `DMAEngineConfig`.
- **Measure:** Confirm per-channel width and aggregate bandwidth.

---

## 7. Routing & Topology

### 7.1 XY routing enforcement
- **Status:** Fixed. X=4 columns, Y=8 rows, ID = y*4 + x. X-first dimension
  order routing is now correctly implemented.
- **Verify:** Confirm on hardware that XY (X-first) is indeed the routing
  algorithm and no adaptive routing is used.

### 7.2 FIXPATH routing
- **Status:** Implemented as `TransType.FIXPATH` with explicit
  `fixed_path: List[int]`.
- **Verify:** Confirm whether hardware supports source-directed explicit
  paths and the maximum path length.

---

## 8. Synchronization Modes (A4)

### 8.1 ins_sync_mode timing
- **Assumption:** 4 modes modeled as a field:
  - 0 = normal unicast
  - 1 = broadcast (including self)
  - 2 = reduce intermediate
  - 3 = reduce last (root)
- **Where:** `Message.ins_sync_mode`, `Router._reduce_run()`.
- **Measure:** Confirm that modes 2 and 3 have different timing/behavior at
  intermediate vs root nodes. Measure any barrier/fence stalls.
- **Action after profiling:** Add per-mode latency differences if hardware
  treats them differently beyond message merging.

---

## 9. Multicast

### 9.1 Replication strategy
- **Assumption:** Packet replication at routers via `route_multicast()` using
  a destination bitmask. The message is forked at the first divergent branch.
- **Where:** `Router.route_multicast()`, `_multicast_branches()`.
- **Measure:** Confirm whether hardware replicates at the router or uses a
  different mechanism (e.g., path-based multicast, virtual circuit).
- **Action after profiling:** Adjust replication model if needed.

### 9.2 Multicast header size
- **Assumption:** Same 4-byte header as unicast.
- **Measure:** Multicast may require a larger header (bitmask or ID list).

---

## 10. Reduce (Route Reduce)

### 10.1 Reduce merge latency
- **Assumption:** `RouterConfig.reduce_latency = 2` cycles per merge.
- **Where:** `RouterConfig.reduce_latency`, `Router._reduce_run()`.
- **Measure:** Measure the actual ALU/merge latency at each reduce hop.
- **Action after profiling:** Update default.

### 10.2 Reduce operator behavior
- **Assumption:** Sum/MAX atomic operations modeled at WDMA.
- **Measure:** Confirm which reduce operators are supported in-network vs at
  endpoints.

---

## 11. Inter-Chip Link (AdaLink)

### 11.1 Link latency
- **Assumption:** `NodeConfig.adalink_latency = 8` cycles; test fixtures use
  `link_latency = 5`.
- **Where:** `NodeConfig.adalink_latency`, `fabric.InterChipLink`.
- **Measure:** Measure actual inter-chip serdes + protocol latency.

### 11.2 Credit count and bandwidth
- **Assumption:** `InterChipLink` credits = 64; width = 16 B/cycle.
- **Measure:** Confirm credit count, effective bandwidth, and whether
  backpressure propagates correctly.

### 11.3 Atomic operation latency
- **Assumption:** `atomic_latency = 4` cycles.
- **Measure:** Measure WRITE_SUM / WRITE_MAX completion latency at the
  bridge/AIU.

---

## 12. Shadow Pipeline (H4)

### 12.1 Stage latencies
- **Assumption:** All shadow stage latencies default to 0; must be configured
  via `ShadowConfig.matrix` / `vector`.
- **Where:** `config.ShadowConfig`.
- **Measure:** Profile SRAM download/upload, MDMA channel/AIU, ACI func/AIU
  stage latencies on hardware.
- **Action after profiling:** Populate defaults in the JSON config.

### 12.2 Occupancy and initiation interval
- **Assumption:** `occupancy = 4`, `ii = 1`.
- **Measure:** Confirm pipeline depth and throughput.

---

## 13. Clock & Bandwidth

### 13.1 Clock frequencies
- **Assumption:** ACI = 1125 MHz, DDR = 1150 MHz.
- **Where:** `ClockConfig`.
- **Measure:** Confirm production clock frequencies and any dynamic scaling.

### 13.2 Effective link bandwidth
- **Assumption:** 16 B/cycle (128-bit link), giving ~18 GB/s at 1125 MHz.
- **Measure:** Confirm link width and protocol efficiency (header overhead,
  credit stalls, bubble cycles).
- **Note:** `RouterConfig.burst_bubble = 1` models a bubble cycle between
  bursts. Verify this matches hardware.

---

## 14. QoS / Priority (H2)

### 14.1 Priority levels and arbitration
- **Assumption:** 4-level priority via `shrBufPortPriority`; modeled through
  SimPy resource ordering.
- **Where:** QoS logic in `noc.py` and `test_h2_qos.py`.
- **Measure:** Confirm priority encoding, preemption behavior, and whether
  lower-priority packets can be starved.

### 14.2 FIFO depth and backpressure
- **Assumption:** Per-port FIFO depth set via `register_fifo()`.
- **Measure:** Confirm hardware FIFO depths and whether they differ by port
  type (local vs r2r).

---

## Summary Table

| ID  | Parameter                    | Current Default | Confidence | Impact |
|-----|------------------------------|-----------------|------------|--------|
| 1.1 | NMC startup latency          | 1 cyc           | Low (~1000)| High   |
| 1.2 | NMC injection ports          | 0 (unlim)       | Medium     | Medium |
| 2.1 | Router pipeline stages       | 1 cyc/hop       | Low        | High   |
| 2.2 | Speculative VA/SA            | Not modeled     | Unknown    | Medium |
| 2.3 | Switch allocator algorithm   | Round-robin     | Medium     | Medium |
| 2.4 | Switch hold policy           | Flit-level      | Low        | High   |
| 3.1 | VC count / depth             | 2 / TBD         | Medium     | Medium |
| 3.2 | Credit return latency        | 0 (instant)     | Low        | Medium |
| 3.3 | Header size                  | 4 B             | High       | Low    |
| 4.1 | Concentrated local inj cap   | 1               | Low        | High   |
| 5.1 | SRAM read/write ports        | 0 (unlim)       | Low        | High   |
| 6.1 | DMA dispatch interval        | 0 (none)        | Low        | Medium |
| 8.1 | ins_sync_mode timing diffs   | Field only      | Medium     | Low    |
| 10.1| Reduce merge latency         | 2 cyc           | Medium     | Low    |
| 11.1| AdaLink latency              | 8 cyc           | Medium     | Medium |
| 11.3| Atomic latency               | 4 cyc           | Medium     | Low    |
| 12.1| Shadow stage latencies       | 0               | Low        | Medium |
| 13.2| Burst bubble cycles          | 1               | Medium     | Medium |
| 14.1| Priority arbitration         | 4-level RR      | Medium     | Medium |

**High-impact items to profile first:** 1.1, 2.1, 2.4, 4.1, 5.1.
