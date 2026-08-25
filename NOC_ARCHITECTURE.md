# ADA2S-32 NoC and Transport Subsystem Architecture (Single-Card Model)

> This document describes the on-chip NoC and data transport subsystem of the ADA2S-32 SoC, scoped to a single chip. The chip features **two physically independent 8×4 NoC meshes (NoC0 and NoC1)** with PE NMC channels mapped 1:1 to each mesh. All high-priority transport parameters have been empirically calibrated through silicon microbenchmarks (§9); remaining unmeasured parameters are listed in §10.

## 1. Chip Overview

The ADA2S-32 is a heterogeneous many-core AI accelerator SoC built around **dual 2D mesh Network-on-Chip (NoC)** fabrics. The chip employs an SPMD programming model where all Processing Elements (PEs) execute the same kernel on different data partitions. Each PE contains a scalar control core, a Matrix compute engine (fp8 GEMM), a 4-TPC Vector engine, and an NMC (Network Memory Controller) with two fully independent, full-duplex DMA channels connecting to the two NoC meshes.

### 1.1 Node Inventory

| Node Type | Count | Physical Node ID | Function | Compiler Attribute |
|-----------|-------|-----------------|----------|-------------------|
| PE | 32 | 0-31 | Compute (Scalar + Vector + Matrix) | `__adax_pe__` |
| GM_RDMA | 4 | 32-35 | Global Memory read DMA (GM -> NoC) | `__adax_gm_rdma__` |
| GM_WDMA | 4 | 36-39 | Global Memory write DMA (NoC -> GM) | `__adax_gm_wdma__` |
| DDR_RDMA | 4 | 40-43 | LPDDR read DMA (DDR -> NoC) | `__adax_ddr_rdma__` |
| DDR_WDMA | 4 | 44-47 | LPDDR write DMA (NoC -> DDR) | `__adax_ddr_wdma__` |

**Total on-chip NoC nodes**: 32 + 4 + 4 + 4 + 4 = **48 nodes**

### 1.2 Memory Hierarchy

The chip has **two physically independent NoC meshes (NoC0 and NoC1)**, each with its own routers and links. PE NMC exposes two fully independent channels (CH0→NoC0, CH1→NoC1), each with independent upload/download engines. PE↔PE traffic achieves full 2× bandwidth on dual-channel; GM/DDR nodes have a single internal DMA engine shared by both channels, capping aggregate at ~124 GB/s regardless of channels used.

| Memory | Capacity | Aggregate Bandwidth | Visibility | Address Range |
|--------|----------|-------------------|------------|---------------|
| LPDDR (external) | 128 GB | 533 GB/s (4×DDR) | All nodes (via DDR_DMA) | -- |
| Global Memory (GM, on-chip SRAM) | 32 MB | 4×124 = ~496 GB/s | All nodes (via GM_DMA) | -- |
| PE Local SRAM | 3 MB per PE | **~292 GB/s read / ~145 GB/s write** (vec DMA), ~134 GB/s/ch NMC | Single PE only | 0x10_0000 - 0x3F_FFFF |
| PE Weight SRAM | 16 MB per PE | ~283 GB/s read (vec DMA); Matrix read priority | Single PE only | 0x40_0000 - 0x13F_FFFF |
| VMEM (Vector register file) | **990 entries x 128 B ≈ 124 KB per PE** | -- | Vector core only | Byte-offset from VMEM base; 10-bit addr field (max 1023 entries) |
| AIU Download SRAM | 256 KB per DMA node | -- | Local DMA node only | 16 B-aligned |

> **Note (documented + measured)**: SRAM bank conflicts do not need to be considered in software; the hardware SRAM controller handles banking transparently. Feature SRAM (Local) accesses should be 128 B-aligned; Weight SRAM accesses should be 4 KB-aligned. Local SRAM and Weight SRAM are **independent banks with separate read/write ports** (§9.18); NMC TX (SRAM read) and NMC RX (SRAM write) plus Matrix and Vector DMA can run concurrently with ≤7% worst-case port contention (§9.19).

### 1.3 Clock Domains

| Domain | Frequency | Nodes |
|--------|-----------|-------|
| ACI | **1125 MHz** | PE, GM_RDMA, GM_WDMA, Routers |
| DDR | **1150 MHz** | DDR_RDMA, DDR_WDMA |

> CDC async FIFO bubble penalty measured **<10 cyc fixed overhead** (2026-08-13 Round4): 512B ul base latency 193cyc (DDR) vs 188cyc (GM); negligible for bandwidth modeling.

---

## 2. NoC Mesh Topology

### 2.1 Physical Layout

The chip contains **two physically independent Data NoC meshes (NoC0 and NoC1)**, each an identical 2D rectangular mesh. NMC CH0 connects to NoC0; NMC CH1 connects to NoC1. Both meshes share the same 8×4 logical grid (32 routers each), and PE NMC CH0/CH1 have separate physical ports to their respective NoC. GM/DDR DMA nodes attach to both meshes via CH0/CH1 ports but share a single internal DMA engine (§2.5, §9.9-9.10).

Each mesh has dimensions defined in adas_base_info.h:

- **NOC_DIM_X = 4** (columns)
- **NOC_DIM_Y = 8** (rows)

This yields **32 mesh routers per NoC** (ID 0-31), one per PE grid position. With two NoCs, the chip has 64 routers total. Inter-PE traffic on both NoCs can run in parallel at full line rate (§9.13, §9.19).

### 2.2 Router Coordinate System

Router coordinate from router ID:
```
x = route_id % NOC_DIM_X    // 0..3
y = route_id / NOC_DIM_X    // 0..7
```

The 8x4 mesh layout:
```
        X=0  X=1  X=2  X=3
Y=0  [  0 ][  1 ][  2 ][  3 ]
Y=1  [  4 ][  5 ][  6 ][  7 ]
Y=2  [  8 ][  9 ][ 10 ][ 11 ]
Y=3  [ 12 ][ 13 ][ 14 ][ 15 ]
Y=4  [ 16 ][ 17 ][ 18 ][ 19 ]
Y=5  [ 20 ][ 21 ][ 22 ][ 23 ]
Y=6  [ 24 ][ 25 ][ 26 ][ 27 ]
Y=7  [ 28 ][ 29 ][ 30 ][ 31 ]
```

### 2.3 Node-to-Router Attachment Map

Non-PE DMA nodes do not have dedicated routers; they attach to existing PE-position routers. Mapping from `get_route_id()`:

| Node Type | Instance | Router ID | Local Port ID | Grid Position |
|-----------|----------|-----------|---------------|---------------|
| PE | 0-31 | pe_id | DATA_NOC_LOCAL_ID_PE (0) | Grid (y=id/4, x=id%4) |
| DDR_RDMA | 0 | **0** | DATA_NOC_LOCAL_ID_DDR_RDMA (7) | (Y=0,X=0) |
| DDR_RDMA | 1 | **28** | DATA_NOC_LOCAL_ID_DDR_RDMA (7) | (Y=7,X=0) |
| DDR_RDMA | 2 | **3** | DATA_NOC_LOCAL_ID_DDR_RDMA (7) | (Y=0,X=3) |
| DDR_RDMA | 3 | **31** | DATA_NOC_LOCAL_ID_DDR_RDMA (7) | (Y=7,X=3) |
| DDR_WDMA | 0 | **0** | CH0=3, CH1=4 | (Y=0,X=0) |
| DDR_WDMA | 1 | **28** | CH0=3, CH1=4 | (Y=7,X=0) |
| DDR_WDMA | 2 | **3** | CH0=3, CH1=4 | (Y=0,X=3) |
| DDR_WDMA | 3 | **31** | CH0=3, CH1=4 | (Y=7,X=3) |
| GM_RDMA | 0 | **28** | DATA_NOC_LOCAL_ID_GM_RDMA (14) | (Y=7,X=0) |
| GM_RDMA | 1 | **29** | DATA_NOC_LOCAL_ID_GM_RDMA (14) | (Y=7,X=1) |
| GM_RDMA | 2 | **30** | DATA_NOC_LOCAL_ID_GM_RDMA (14) | (Y=7,X=2) |
| GM_RDMA | 3 | **31** | DATA_NOC_LOCAL_ID_GM_RDMA (14) | (Y=7,X=3) |
| GM_WDMA | 0 | **28** | CH0=10, CH1=11 | (Y=7,X=0) |
| GM_WDMA | 1 | **29** | CH0=10, CH1=11 | (Y=7,X=1) |
| GM_WDMA | 2 | **30** | CH0=10, CH1=11 | (Y=7,X=2) |
| GM_WDMA | 3 | **31** | CH0=10, CH1=11 | (Y=7,X=3) |

**Attachment summary**:
- All GM DMAs attach to the bottom edge (Y=7, routers 28-31).
- DDR DMAs attach to the four corner routers (0, 3, 28, 31).
- Routers 28-31 are the most heavily populated (PE + GM DMA + DDR DMA).

### 2.4 Router Microarchitecture

Each router implements a simple wormhole-cut-through design (mentor-confirmed parameters marked ★):

| Parameter | Value | Notes |
|-----------|-------|-------|
| Physical link (phit) width | **1024 bits (128 B) / cycle / direction** | Derived from measured data (see §9.3): sustained ~120 B/cyc injection across 1-10 hops, and per-hop 8.5 cyc with 4-phit flit yields 5-cycle router pipeline (consistent). Contradicts initial "512-bit" assumption; 512-bit link would cap at 64 B/cyc = 72 GB/s, but we measure 135 GB/s. |
| Flit size (flow-control unit) | **512 B = 4 phits** ★ | Mentor-confirmed. Buffer allocation and credit granularity is one flit. Single-flit serialization: 4 cycles ideal, ~4.3 cycles effective with inter-flit bubble. |
| NoC clock domain | **1125 MHz (ACI domain)** | Same as PE/GM scalar/vector cores. 5-cycle router pipeline (RC:1, SA:2, ST:1, LT:1) is reasonable at 1125 MHz; a 2× clock hypothesis would require 9+ pipeline stages with CDC overhead, for which there is no evidence. |
| Packet format | **Single-flit**: header+payload packed in one 512B flit; **Multi-flit**: 1 header flit + P payload flits + 1 tail flit (total P+2 flits) ★ | Mentor-confirmed. Header fits in first phit (~8-12 B); body flits carry ~4 B CRC/seq overhead; tail flit carries end-of-packet marker. For large P, overhead amortizes to <1%. |
| Virtual channels (VC) | **1 VC per port** ★ | No VC partitioning; deadlock freedom via XY dimension-order routing. No escape VC. |
| Input buffer depth | **1 flit (512 B) per port** ★ | Extremely shallow; throughput depends critically on credit round-trip latency (~17 cycles RTT). |
| Buffer organization | **Single-write, multi-read** ★ | Enables in-router multicast replication (same flit read out multiple output ports simultaneously). |
| Flow control | **Credit-based** ★ | Credits return after a flit departs the downstream input buffer. Credit RTT ≈ 17 cycles (1 hop) — measured. |
| Routing algorithm | **XY deterministic (dimension-order), X-first then Y** ★ | Measured: perfect X/Y symmetry, same-Manhattan-distance PEs have identical latency regardless of X/Y traversal order. |
| Arbitration | **Round-robin** | Confirmed by uniform BW distribution across 4 disjoint flows; no observed unfairness. |
| Multicast replication | **In-router packet copy** ★ | Single-write-multi-read buffer replicates flits to multiple output ports within a single router; no source-side repeated injection needed. |
| Pipeline stages | **~5 stages (RC:1, SA:2, ST:1, LT:1)**, total ≈ 8.5 cycles one-way per hop for a complete flit | Derived: head phit takes ~4 cycles through router+link; remaining 3 phits arrive at 1/cycle; plus ~1.5 cycles credit/sync overhead = 8.5 cyc. Measured via ping-pong across all 31 PEs. |
| Per-hop zero-load latency | **8.5 cycles one-way (17 cycles RTT)** | Linear fit: RTT = 250 + 17×hops (dynamic shape) / 159 + 17×hops (static shape). |

**Directional ports**: Each router has up to 4 neighbor ports (N/S/E/W) + local ports (see §2.5). Edge routers have fewer active neighbor ports.

**Bandwidth per directional link (per NoC)**: 128 B/cycle × 1125 MHz = **144 GB/s per direction per NoC** (full-duplex: 288 GB/s bidirectional per NoC). Effective payload throughput ≈ 120 B/cycle = **135 GB/s** after inter-flit bubbles (~6%) and per-flit CRC overhead (~4 B/flit). With two independent NoCs, a PE can achieve **~270 GB/s simplex aggregate (dual-ch same-direction)** or **~527 GB/s bulk bidirectional** (2 NoCs × 2 directions × ~117 B/cyc, §9.19).

### 2.5 Router Local Port Map (DataNocLocalId)

Each router has local ports connecting to attached modules, defined by the DataNocLocalId enum. Not all ports exist on every router (e.g., only routers 0/3/28/31 have DDR ports; only routers 28-31 have GM ports):

| Port ID | Enum Name | Connected Module | Transfer Mode | Present on Routers |
|---------|-----------|-----------------|---------------|-------------------|
| 0 | DATA_NOC_LOCAL_ID_PE | Local PE via NMC | Dual/Single | All (0-31) |
| 1 | DATA_NOC_LOCAL_ID_FABRIC_BRIDGE | Fabric bridge (control) | -- | [TBD] |
| 2 | DATA_NOC_LOCAL_ID_DNOC2AXI | NoC-to-AXI bridge | -- | [TBD] |
| 3 | DATA_NOC_LOCAL_ID_DDR_WDMA_CHANNEL0 | DDR WDMA CH0 (dual-side) | Dual-side | 0, 3, 28, 31 |
| 4 | DATA_NOC_LOCAL_ID_DDR_WDMA_CHANNEL1 | DDR WDMA CH1 (dual-side) | Dual-side | 0, 3, 28, 31 |
| 5 | DATA_NOC_LOCAL_ID_DDR_RDMA_LOCAL_SRAM | DDR RDMA AIU Download SRAM | AIU Download | 0, 3, 28, 31 |
| 6 | DATA_NOC_LOCAL_ID_DDR_WDMA_LOCAL_SRAM | DDR WDMA AIU Download SRAM | AIU Download | 0, 3, 28, 31 |
| 7 | DATA_NOC_LOCAL_ID_DDR_RDMA | DDR RDMA (single-side) | Single-side | 0, 3, 28, 31 |
| 8 | DATA_NOC_LOCAL_ID_DDR_WDMA | DDR WDMA (single-side) | Single-side | 0, 3, 28, 31 |
| 9 | (reserved) | -- | -- | -- |
| 10 | DATA_NOC_LOCAL_ID_GM_WDMA_CHANNEL0 | GM WDMA CH0 (dual-side) | Dual-side | 28-31 |
| 11 | DATA_NOC_LOCAL_ID_GM_WDMA_CHANNEL1 | GM WDMA CH1 (dual-side) | Dual-side | 28-31 |
| 12 | DATA_NOC_LOCAL_ID_GM_RDMA_LOCAL_SRAM | GM RDMA AIU Download SRAM | AIU Download | 28-31 |
| 13 | DATA_NOC_LOCAL_ID_GM_WDMA_LOCAL_SRAM | GM WDMA AIU Download SRAM | AIU Download | 28-31 |
| 14 | DATA_NOC_LOCAL_ID_GM_RDMA | GM RDMA (single-side) | Single-side | 28-31 |
| 15 | DATA_NOC_LOCAL_ID_GM_WDMA | GM WDMA (single-side) | Single-side | 28-31 |
| 22 | DATA_NOC_LOCAL_ID_MMU | Memory Management Unit | -- | [TBD] |

> **DMA channel sharing rules** ★ (updated 2026-08-24):
> - **PE NMC CH0/CH1 are fully independent and full-duplex**: Each channel has separate upload/download engines with independent SRAM read/write ports. Same-direction parallel (back-to-back `send_with_sync<0>`+`send_with_sync<1>` before fence) achieves full 2× bandwidth (~240 B/cyc = 270 GB/s PE↔PE, §9.13). Same-channel full-duplex (TX+RX on same CH) achieves 2× simplex BW (~234 B/cyc = 263 GB/s, §9.19). Dual-channel full-duplex (2ch × 2dir) peaks at ~468 B/cyc = 527 GB/s bulk / ~340 B/cyc = 382 GB/s at 32KB batch.
> - **GM_WDMA/GM_RDMA/DDR_WDMA/DDR_RDMA share a single internal DMA engine across CH0+CH1**: PE→GM/DDR dual-channel caps aggregate at ~114-126 GB/s (NOT 2×). The remote endpoint is the bottleneck, not NoC links.
> - **Different DMA types are independent**: GM_RDMA vs GM_WDMA vs DDR_RDMA vs PE NMC each have independent router ports. The 4 GM_WDMA instances aggregate to ~314 GB/s when PEs spread across different GMs.
> - **In-router ALU (reduce) adds zero bandwidth overhead**; reduction passes data through at line rate.

---

## 3. Router Behavior

### 3.1 Packet Header Encoding

Packet headers are constructed by the `build_info1` function at the sender NMC (software-visible routing word):

```cpp
unsigned int build_info1(unsigned int local_id, unsigned int route_id, unsigned int trans_type) {
    route_id  &= 0x3f;   // bits [5:0]   - destination router ID
    local_id  &= 0x1f;   // bits [16:12] - destination local port (DataNocLocalId)
    local_id  <<= 12;
    trans_type &= 0x3;   // bits [18:17] - transfer type
    trans_type <<= 17;
    return (route_id | local_id | trans_type);
}
```

**Software-visible routing word (build_info1, 32 bits)**:

| Bits | Field | Width | Description |
|------|-------|-------|-------------|
| [5:0] | route_id | 6 bits | Destination router ID (supports up to 64 routers) |
| [11:6] | reserved | 6 bits | Reserved/padding |
| [16:12] | local_id | 5 bits | Destination local port ID (DataNocLocalId) |
| [18:17] | trans_type | 2 bits | Transfer type (SINGLECAST/FIXPATH/MULTICAST/BROADCAST) |
| [31:19] | reserved | 13 bits | Reserved |

**Estimated full on-wire header (~8-12 bytes total, in first phit of header flit)**:

The software-visible 32-bit routing word is embedded in a larger hardware header that the NMC prepends. Estimated fields (from register interface and microarchitecture):

| Field | Est. Width | Purpose |
|-------|-----------|---------|
| Dest route_id + local_id | 11 bits | From build_info1 |
| Src route_id + local_id | ~11 bits | Return path for ACKs |
| TransType + pkt_type (H/B/T) | 4 bits | Packet type and flit-sequence marker |
| TaskID / sync tag | 8 bits | Completion/barrier matching |
| Packet length (flit count) | 8 bits | For multi-flit packet reassembly |
| Reduce opType + insSyncMode | 5 bits | Route-reduce control |
| Burst/vc/misc | ~5 bits | Burst length, VC ID, etc. |
| CRC/checksum | 16-32 bits | Per-flit or per-packet integrity |
| **Total** | **~70-90 bits ≈ 9-12 bytes** | Fits in first phit (128B) |

**Flit payload layout** (512B flit = 4 phits of 128B):
- **Single-flit packet**: phit0 = ~12B header + ~116B payload; phits 1-3 = 384B payload. Total payload ≈ 500B.
- **Header flit (multi-flit)**: phit0 = ~12B header + ~116B payload; phits 1-3 = 384B payload. Carries ~500B payload.
- **Body flit**: ~4B seq/CRC in phit0; rest = 508B payload.
- **Tail flit**: ~4B tail marker/CRC in last phit; rest = 508B payload.

For large packets (P ≫ 2), overhead is ~4B per 512B flit + (2×12B)/(P×512B) amortized → effective ~120 B/cyc sustained.

### 3.2 Transfer Types (TransType)

| Enum | Value | Description |
|------|-------|-------------|
| SINGLECAST | 0 | Point-to-point transfer to a single (router, local_port) |
| FIXPATH | 1 | Source-specified fixed path (for deterministic latency) |
| MULTICAST | 2 | Multi-destination transfer using dmaIdOrPeMask/nodeMask as bitmask |
| BROADCAST | 3 | Broadcast to all nodes of the target type |

For multicast, the upload register `dmaIdOrPeMask` serves as a destination bitmask (one bit per PE or per DMA instance), and `nodeMask` provides a broader node-type-level mask.

### 3.3 Routing

The routing mechanism uses the `route_id` and `routeLocalID` fields in the header to deliver packets. The FIXPATH transfer type allows software to specify a predetermined route for latency-sensitive paths.

**Confirmed via microbenchmark**: Default routing is **XY dimension-order (X first, then Y)**. Measurements across all 31 PEs from PE0 show that RTT latency depends purely on Manhattan distance `|Δx| + |Δy|`, with identical latency for:
- Same-hop X-only vs Y-only vs mixed-XY paths (e.g., PE1(Δx=1)=PE4(Δy=1)=267 cyc RTT)
- This proves X-first dimensional routing with no adaptive path selection.
- Adaptive routing: NOT supported (deterministic XY only).
- Multicast replication: implemented via in-router single-write-multi-read buffer (mentor confirmed); tree construction latency ~7 cyc (PE bcast) / ~155 cyc (GM bcast) measured (§9.7).

### 3.4 In-Router Reduction (Route Reduce)

Routers support in-flight element-wise reduction during packet traversal, configured via NMC upload registers.

**Reduce operation types**:

| Enum | Value | Operation |
|------|-------|-----------|
| NoCalc | 0 | Pass-through (no reduction) |
| Add | 1 | Element-wise addition |
| Max | 2 | Element-wise maximum |

**Control fields** (from NMC upload registers):
- `opType`: -1 = none, 0 = Add, 1 = Max
- `opTypeSrc0`, `opTypeSrc1`: Source operand selectors (which upstream ports provide operands)
- `insSyncMode` (inserted sync mode):
  - `0`: Normal transfer (no reduction)
  - `1`: Broadcast including self
  - `2`: Route reduce (intermediate hop - forward partial result)
  - `3`: Route reduce last (final hop - write reduced result to destination)
- `opTypeSrcNode0`, `opTypeSrcNode1`: Source node type identifiers
- `reduceIsLast`: Marks final reduction stage

**Mechanism**: When packets with matching metadata (same task ID, same reduction tree) arrive at a router, the router performs the specified arithmetic operation per element before forwarding. This creates a hardware reduction tree across the mesh.

**Measured characteristics** (§9.11, 2026-08-20 Round6):
- **Cut-through reduction**: The in-router ALU operates in cut-through mode (not store-and-forward). Reduction adds **~42 cycles per chain hop** of fixed overhead (tree-depth latency) but does not reduce payload bandwidth.
- **Zero incremental BW cost**: Add/Max ALU is fully pipelined; asymptotic bandwidth unchanged from unicast (~59 B/cyc = 66 GB/s end-to-end for reduce+bcast chain, 92% of theoretical).
- **Sum and Max have identical performance** (same latency, same bandwidth).
- **CH0 and CH1 are symmetric** for reduction (identical cycle counts).
- **Mandatory broadcast release**: After reduction reaches root, `pe_broadcast_sync` MUST be called to reset download units left in reduce-forward state; non-root PEs must call `recv_with_sync(view, NodeType::PE, root_pe)` to complete the release. Without broadcast release, subsequent NOC operations on the same channel deadlock.
- **Minimum transfer size**: ≥256B for reliable correctness across multi-iteration runs (sub-256B sizes can produce stale flit contamination).

### 3.5 Burst Length Control

Each transfer specifies a `burstLenMode` controlling the maximum burst size:

| Enum | Value | Max Burst Size |
|------|-------|---------------|
| BURST_LEN_DEFAULT | -1 | Hardware default [TBD: exact value] |
| BURST_LEN_0 | 0 | 1 beat |
| BURST_LEN_1 | 1 | 2 beats |
| BURST_LEN_3 | 3 | 4 beats |
| BURST_LEN_7 | 7 | 8 beats |

> [TBD: Beat size in bytes. The minimum transfer granularity for gather table entries is 128 B, which may correspond to one beat or flit.]

### 3.6 Shared Buffer Priority

Each transfer can set `shrBufPortPriority` (0-3) to control arbitration priority for shared router buffer allocation. Higher values indicate higher priority.

### 3.7 FIFO Flow Control

Hardware FIFO checking prevents buffer overflow/underflow between endpoints:
- `hw_id` (0-63): Hardware FIFO identifier
- `logic_id` (0-31): Logical FIFO ID for multiplexed streams
- `check_type`:
  - `TYPE_DEFAULT (-1)`: No FIFO check
  - `WRITE_FULL (0)`: Check write-side FIFO not full before sending
  - `READ_EMPTY (1)`: Check read-side FIFO not empty before receiving
  - `UPDATE (2)`: Update FIFO credit counter after transfer

> Credit return latency ≈ 8.5 cyc/hop one-way (same as data propagation, §9.16). FIFO depths per hw_id remain [TBD] for detailed flow-control modeling.

### 3.8 Single-Side vs Dual-Side Transfer Modes

The NoC supports two software configuration models:

**Dual-Side (Paired Configuration)**:
- Both sender and receiver pre-configure their DMA/NMC descriptors with matching parameters.
- Synchronized by a hardware outer-sync handshake (release/acquire pair).
- Packets carry data only; no address header needed (receiver pre-programmed).
- Best throughput.
- Supported paths: PE<->PE, PE<->DMA, DMA->PE (WDMA dual-channel ports), DMA<->DMA.

**Single-Side (Request/Response)**:
- Only the initiator configures descriptors; target address is embedded in the packet header.
- Bidirectional request/response traffic on the NoC.
- Documented performance impact: approximately **10% throughput degradation** (from ARCH.md).
- Supported paths: PE<->GM/DDR DMA (single-side ports 7/8/14/15), DMA->PE.

**Configuration support matrix**:

| Src \\ Dst | PE | DMA (GM/DDR) |
|-----------|-----|-------------|
| PE | Dual-side | Dual + Single |
| DMA | Dual + Single | Dual-side |

> Single-side vs dual-side throughput difference <3% (equivalent performance, §9.8). Header size ≈12 B (first phit of header flit). maxOst default and out-of-order capability remain [TBD].

---

## 4. PE-Internal NMC (Network Memory Controller)

### 4.1 Overview

Each PE contains one NMC that manages all data movement between the PE's local SRAM (Local + Weight) and the two on-chip NoC meshes. The NMC exposes **two fully independent DMA channels** (Channel 0 → NoC0, Channel 1 → NoC1). Each channel is **physically full-duplex** with separate upload (TX) and download (RX) engines that can operate simultaneously at full line rate (§9.19).

### 4.2 Execution Unit Mapping

Within a PE, the NMC channels are exposed as execution units in the sync framework:

| Unit ID | Name | Direction | Channel |
|---------|------|-----------|---------|
| 3 | PE_DOWNLOAD_0 | NoC -> SRAM (receive) | Channel 0 (NoC0) |
| 4 | PE_UPLOAD_0 | SRAM -> NoC (send) | Channel 0 (NoC0) |
| 5 | PE_DOWNLOAD_1 | NoC -> SRAM (receive) | Channel 1 (NoC1) |
| 6 | PE_UPLOAD_1 | SRAM -> NoC (send) | Channel 1 (NoC1) |
| 7 | PE_VME | SRAM <-> VMEM (Vector Memory Engine) | Internal |

Other PE execution units:

| Unit ID | Name | Function |
|---------|------|----------|
| 1 | PE_MATRIX | Matrix compute engine (fp8 GEMM, ~2120 cyc/256×256×128 tile, queue depth ~4 tiles) |
| 2 | PE_VECTOR | Vector compute engine (4 TPC, ~16 fp32/cyc/TPC, 990×128B VMEM) |
| 8 | SCALAR_CORE | Scalar control core (programs NMC/DMA registers, no HW FP) |

Channel-to-unit mapping is fixed: CH0 ↔ {DOWNLOAD_0, UPLOAD_0}, CH1 ↔ {DOWNLOAD_1, UPLOAD_1}.

**Measured SRAM port contention (§9.18-9.19)**: Local SRAM and Weight SRAM are independent banks with separate read/write ports. NMC TX (read) and RX (write) can run concurrently with Matrix and Vector DMA. Worst-case contention (dual-ch fulldup NMC + Matrix) is ≤7% in NMC-bound regime; in matrix-bound regime ≤3%. Optimal command ordering is **mtx-first**: fire bmm tiles first (up to queue depth of 4), then post NMC DMA descriptors.

### 4.3 NMC Channel Capabilities

Each channel supports:
- **Download**: NoC → Local/Weight SRAM (destination address configurable via register)
- **Upload**: Local/Weight SRAM → NoC (source address configurable)
- **Independent operation**: CH0 and CH1 can run concurrently (e.g., CH0 uploading while CH1 downloading).
- **Full-duplex per channel**: TX and RX on the same channel operate simultaneously at full line rate with no duplex penalty (§9.19).
- **Double-buffering**: Software uses CH0/CH1 alternately to overlap compute with IO. With full-duplex, a single channel can simultaneously TX previous results and RX next inputs.

**Measured bandwidth per channel (N=32 batched 32KB, MoE tile size)**:
- Simplex TX or RX: ~87 B/cyc = **98 GB/s**
- Same-channel full-duplex (TX+RX): ~172 B/cyc = **194 GB/s** (99% of 2× simplex)
- Dual-channel same-direction: ~171 B/cyc = **192 GB/s** (98% of 2× simplex)
- **Dual-channel full-duplex (max)**: ~340 B/cyc = **382 GB/s** (97.5% of 4× simplex)

**Bulk asymptotic bandwidth (≥256KB contiguous)**:
- Per channel per direction: ~119 B/cyc = **134 GB/s**
- Dual-channel full-duplex: ~468 B/cyc = **527 GB/s**

### 4.4 Multi-Dimensional Strided DMA

The NMC supports up to **10 levels of nested loop strided access**, enabling direct reads/writes of multi-dimensional tensor layouts without software tiling:

- **Dual-side upload**: 10 source dimensions (loopCnt[0..9], loopStride[0..9])
- **Dual-side download**: 10 destination dimensions (loopCnt[0..9], loopStride[0..9])
- **Single-side**: SRAM side has 10 dimensions; GM/DDR side has 6 dimensions

Loop dimension packing (innermost to outermost): `x0 (contiguous), x1, x2, x3, x4, x5, x6, x7, x8, x9`
- `loopCnt[i]`: Element count at nesting level i
- `loopStride[i]`: Byte stride between iterations at level i (in **bytes**, not elements)

Strides are in bytes to support sub-byte packed types (UINT4) and arbitrary tensor layouts.

### 4.5 Data Type Support (dtype)

The NMC handles pack/unpack for the following element types:

| AdaType | Value | Element Size |
|---------|-------|-------------|
| ADA_INT8 | 0b000 | 1 B |
| ADA_INT16 | 0b001 | 2 B |
| ADA_INT32 | 0b010 | 4 B |
| ADA_FP8_E4M3 | 0b011 | 1 B |
| ADA_FP16 | 0b100 | 2 B |
| ADA_BF16 | 0b101 | 2 B |
| ADA_FP32 | 0b110 | 4 B |
| ADA_TF32 | 0b111 | 4 B |
| ADA_INT64 | 0b1000 | 8 B |
| ADA_FP8_E5M2 | 0b1001 | 1 B |
| ADA_UINT4 | 0b1010 | 0.5 B (packed) |
| ADA_UINT4x2 | 0b1011 | 1 B (2 x UINT4) |

### 4.6 BAUA (Base-Axis-UnAlign)

BAUA provides hardware support for transfers where tensor boundaries do not align with the preferred NoC packet/segment size. Two independent BAUA groups can be programmed:

- **First group** (`bauaf`): `baseAxisFirst`, `unalignAxisFirst`, `bauaNumFirst`
- **Second group** (`bauas`): `baseAxisSecond`, `unalignAxisSecond`, `bauaNumSecond`

Used for edge-tile handling in convolutions and attention masking.

> [TBD: Cycle overhead of BAUA processing; alignment requirements it relaxes.]

### 4.7 Mask and Padding

Hardware boundary masking:
- `maskType`: Masking mode
- `zeroPoint`/`maskValue`: Pad value (stored as uint32, bit-cast for fp16/bf16/fp32)
- `maskFaxis`/`maskFnum`: First-axis masking (leading edge)
- `maskLaxis`/`maskLnum`: Last-axis masking (trailing edge)

Masked positions are filled with `zeroPoint` on download, or read as `zeroPoint` on upload.

### 4.8 Hardware Transpose and Reorder

The NMC can perform in-flight transpose and structured reorder during DMA:

- `enTrans`: Enable N-dimensional transpose
- `enReorder` (ReorderMode):

| Value | Mode | Description |
|-------|------|-------------|
| 0 | NoReorder | No reorder |
| 1 | BmmReorder | Attention: [seq, head_dim] -> [head_dim, seq] |
| 2 | Con2dReorder | Conv2d weight reorder |
| 3 | BisaReorder | Binary-weight reorder |
| 4 | Con3dReorder | Conv3d weight reorder |

Transpose descriptor (`transParam`): `transEn`, `trsSize`, `trsUnalignIn/Out`, `trsLoop0/1`, `trsIn/Out`, `trsInUnalign/trsOutUnalign`.

Single-side download also supports a legacy `ctrans` mode selected via `DnldSingleSide` mode parameter.

> [TBD: Throughput impact of hardware transpose (zero-cost vs extra cycles); tile size constraints; supported transpose dimensions.]

### 4.9 Gather/Scatter (Indirect DMA)

The NMC supports table-based indirect addressing:
- **Gather (upload/send)**: Reads an index table in local SRAM and sends non-contiguous rows as a contiguous NoC stream.
  - `gatherTableAddr`: Byte address of the index table in Local SRAM
  - `gatherAddrOffset`: Base offset added to each entry
  - `gatherTableNum`: Number of entries
- **Scatter (download/receive)**: Receives a contiguous stream and writes to non-contiguous SRAM addresses using a scatter table (same parameter structure).

**Table entry format**:
- Native (SRAM-side) gather table entries (64-bit packed): `{addr[24:0], jump[8:0], len[3:0]}` -- len is in units of **128 B**
- GM-side gather table entries (64-bit packed): `{addr[39:0], len[15:0]}` -- len is in units of **128 B**

Table entries are `unsigned long long` arrays in SRAM.

> [TBD: Per-entry table walk overhead in cycles; maximum gather/scatter table size.]

### 4.10 Work Mode

- `workMode = 0`: Local copy (SRAM-to-SRAM within same PE via NMC loopback)
- `workMode = 1`: Remote transfer (NoC send/receive) -- default

### 4.11 Task ID

`taskID` (default 0) identifies a transfer for:
- Sync framework completion tracking
- Request/response matching in single-side mode
- Grouping packets for in-router reduction (same tree)

### 4.12 MMU Support

Download transfers can enable MMU address translation via `mmuEn` and `mmuAddr` registers.

> [TBD: MMU page size, translation cache details.]

### 4.13 NMC Register Interface

The NMC is programmed by the scalar core writing to a memory-mapped register file, then fired via intrinsics.

#### Upload (SRAM -> NoC) -- 58 Arguments

The `__adas_nmc_upload` intrinsic fires a send:

| Pos | Register | Description |
|-----|----------|-------------|
| 0 | nativeAddr | Source SRAM base pointer |
| 1-10 | loopCnt[0..9] | 10D source element counts |
| 11-20 | loopStride[0..9] | 10D source byte strides |
| 21 | dtype | AdaType element type |
| 22 | routeID | Destination router ID |
| 23 | routeLocalID | Destination DataNocLocalId |
| 24 | channelID | NMC channel (0 or 1) |
| 25 | taskID | Transfer task ID |
| 26 | dmaIdOrPeMask | Target DMA ID or PE bitmask (multicast) |
| 27 | transType | TransType enum |
| 28 | opType | Route reduce (-1=none, 0=Add, 1=Max) |
| 29 | opTypeSrc0 | Reduction source 0 selector |
| 30 | opTypeSrc1 | Reduction source 1 selector |
| 31 | workMode | 0=local, 1=remote |
| 32 | burstLenMode | Burst length control |
| 33 | zeroPoint | Pad/mask value |
| 34-37 | maskFaxis/Fnum/Laxis/Lnum | Mask parameters |
| 38-43 | BAUA first/second groups | Non-aligned transfer descriptors |
| 44 | shrBufPortPriority | Shared buffer priority (0-3) |
| 45 | enTrans | Hardware transpose enable |
| 46-48 | gatherTableAddr/AddrOffset/Num | Gather table parameters |
| 49 | nodeType | Destination node type |
| 50 | nodeMask | Destination node mask |
| 51 | isAIUDownload | AIU download flag |
| 52 | insSyncMode | Inserted sync mode (0-3) |
| 53 | (reserved) | 0 |
| 54 | opTypeSrcNode0 | Reduction source node type 0 |
| 55 | opTypeSrcNode1 | Reduction source node type 1 |
| 56-58 | (reserved) | 0 |

> **Measured NMC endpoint latency (single endpoint, including ACQUIRE wait and first-flit injection)**:
> - **Static shape** (compile-time `Int<N>{}` size, e.g., `make_shape(Int<128>{})`): **~80 cycles** per endpoint (send or receive). Derived from 176-cycle RTT ÷ 2 − 8.5 hop latency.
> - **Dynamic shape** (runtime integer size, e.g., `make_shape(n_bytes)`): **~125 cycles** per endpoint. Derived from 267-cycle RTT ÷ 2 − 8.5 hop latency.
> - Dynamic dispatch overhead: **~45 cycles** (extra NMC descriptor programming / loop-dimension setup when size is not a compile-time constant).
> - Note: this includes the scalar-core register programming overhead + NMC command processor decode + outer-sync ACQUIRE wait, not just DMA engine start. The NMC fires the first flit ~80 cycles after the scalar core writes the final register (in the static-shape fast path).

#### Download (NoC -> SRAM) -- 91 Arguments

The `__adas_nmc_download` intrinsic fires a receive. It has more registers than upload because it carries both source and destination stride descriptors (for transpose where layouts differ), a transpose parameter block, and MMU fields. Key register groups:

| Pos | Register Group | Description |
|-----|---------------|-------------|
| 0 | nativeAddr | Destination SRAM base pointer |
| 1 | srcPtr | Source pointer (0 for dual-side; single-side uses this) |
| 2-11 | loopCnt[0..9] | 10D destination element counts |
| 12-21 | loopStride[0..9] | 10D destination byte strides |
| 22-31 | srcLoopCnt[0..9] | 10D source loop counts (for transpose) |
| 32-41 | srcLoopStride[0..9] | 10D source byte strides (for transpose) |
| 42-51 | transParam[0..9] | Transpose descriptor block |
| 52 | mmuEn | MMU enable |
| 53 | mmuAddr | MMU address |
| 54 | (reserved) | 0 |
| 55 | routeID | Source router ID (or 0 when matched by outer sync) |
| 56 | routeLocalID | Source local port ID |
| 57-59 | gatherScatter | Gather/scatter parameters |
| 62 | insSyncMode | Inserted sync mode |
| 63 | isAIUDownload | AIU download flag |
| 64 | nodeType | Source node type |
| 65 | nodeMask | Source node mask |
| 74 | dtype | Element type |
| 76 | dmaIdOrPeMask | Source DMA ID or PE mask |
| 77 | taskID | Task ID |
| 78 | channelID | NMC channel (0 or 1) |
| 79 | workMode | 0=local, 1=remote |
| 80 | maskType | Mask type |
| 81 | zeroPoint | Pad value |
| 82-85 | maskFaxis/Fnum/Laxis/Lnum | Mask parameters |
| 86-91 | BAUA, priority, transpose, reorder | Same as upload |

---

## 5. DMA Engine Architecture

### 5.1 DMA Node Types

Four DMA types, 4 instances each:

| DMA Type | Direction | Clock | Router Attachment | Dual-Side Channels | Single-Side Port |
|----------|-----------|-------|------------------|-------------------|-----------------|
| GM_RDMA | GM -> NoC | 1125 MHz | 28-31 | -- | 14 |
| GM_WDMA | NoC -> GM | 1125 MHz | 28-31 | CH0=10, CH1=11 | 15 |
| DDR_RDMA | DDR -> NoC | 1150 MHz | 0, 3, 28, 31 | -- | 7 |
| DDR_WDMA | NoC -> DDR | 1150 MHz | 0, 3, 28, 31 | CH0=3, CH1=4 | 8 |

Read DMAs (RDMA) have one port plus an AIU Download SRAM port. Write DMAs (WDMA) have two dual-side channels plus one single-side port plus an AIU Download SRAM port.

> **Measured (2026-08-13, updated 2026-08-24)**: GM_WDMA/GM_RDMA share a single internal DMA engine across CH0+CH1, capping aggregate at ~124 GB/s per GM node regardless of channels used (NOT 2×). 4×GM_WDMA aggregate write BW ~314 GB/s when PEs spread across different GMs. DDR_WDMA/DDR_RDMA similarly share one engine, capping at ~126 GB/s per DDR node. PE NMC CH0/CH1 achieve full 2× bandwidth for PE↔PE traffic (~270 GB/s same-direction, ~527 GB/s fulldup bulk, §9.13/§9.19), proving the two NoC meshes are physically independent. Each GM_WDMA single-channel receive hard-caps at ~100-120 GB/s under N-way incast; each GM_RDMA single-channel send hard-caps at ~120-125 GB/s under N-way outcast.

### 5.2 DMA Scalar Core

DMA nodes (GM_RDMA, GM_WDMA, etc.) each contain a scalar core that dispatches DMA commands. The scalar core executes DMA-initiated transfer programs.

> [CHARACTERIZED (§9.17, 2026-08-24): GM_WDMA scalar core dispatches at ~40 cyc/descriptor when queue has space, with only ~4 outstanding entries per channel (much shallower than PE NMC's 24); after queue fills, small-message completion rate ~273 cyc/msg, creating a well-documented serialization bottleneck for many small PE→GM transfers. Large bulk transfers reach full 110 B/cyc (124 GB/s) line rate. GM_RDMA dispatch rate not yet measured but expected to be similar; GM_WDMA/RDMA share separate scalar cores per channel per column.]

### 5.3 Single-Side DMA Register Interface

Single-side transfers use `NMCBaseSingle` with both source and destination descriptors.

**Download (GM/DDR -> PE) -- `DnldSingleSide`**:
- Native (SRAM) side: 10D loop/stride (dstLoopCnt[0..9], dstLoopStride[0..9])
- Global (GM/DDR) side: 6D loop/stride (srcLoopCnt[0..5], srcLoopStride[0..5])
- DMA selection via `setDmaPos(nodeType, dmaId)`
- `isToDDR`: 0 = GM, 1 = DDR
- `maxOst`: Maximum outstanding transactions (0-255) -- credit control
- Multicast mode (`setMulticastMode`): `Normal(0)`, `Master(1)`, `Slave(2)`
- `multicastPeMask`: 32-bit PE bitmask for multicast
- `writeSum`: Atomic accumulation (0=none, 1=add, 2=max)
- DMA-side gather/scatter support (`dmaGatherTable*`)

**Upload (PE -> GM/DDR) -- `UpldSingleSide`**:
- Native side: 10D loop/stride
- Global side: 6D loop/stride
- `setWriteSum(write_sum)`: 0=overwrite, 1=atomic add, 2=atomic max (GM_WDMA Reduce-on-Write)

**WorkModeSingle**:

| Value | Name | Description |
|-------|------|-------------|
| 0 | BroadCastSlv | Broadcast slave (receives via multicast) |
| 1 | SingleDDR | Single DDR transfer |
| 2 | SingleGM | Single GM transfer |

### 5.4 DMA Initiator-Side API

DMA nodes can initiate transfers (not only respond to PE requests) via:
- `__adas_dma_send` (71 args): DMA reads from DDR/GM and injects into NoC (RDMA function). Supports precomputed route masks for repeated broadcasts.
- `__adas_dma_receive` (49 args): DMA writes NoC data to DDR/GM (WDMA function). Supports `withSum` for atomic accumulation.

### 5.5 AIU Download Path

Each DMA node has a dedicated **256 KB local SRAM** for receiving small control/metadata payloads:
- Data written to AIU SRAM must be 16 B-aligned (`loopCnt` multiple of 16).
- After download completes, an outer sync to `SCALAR_CORE (unit 8)` signals the DMA scalar core to process the received data.
- Uses dedicated local ports: 5 (DDR_RDMA), 6 (DDR_WDMA), 12 (GM_RDMA), 13 (GM_WDMA).
- Used for kernel parameters, command lists, and control messages.

### 5.6 GM Reduce-on-Write (writeSum)

GM_WDMA supports hardware atomic accumulation directly at the GM memory interface:
- `writeSum = 0`: Standard overwrite
- `writeSum = 1`: Atomic add (accumulate incoming NoC data into existing GM value)
- `writeSum = 2`: Atomic max (element-wise maximum)

This enables gradient accumulation and partial-sum aggregation without read-modify-write sequences.

**Measured (2026-08-13 Round2)**: Atomic add (`with_sum=1`/`writeSum=1`) is fully pipelined at the GM_WDMA receiver. N-way atomic incast to a single GM address achieves **~120 GB/s aggregate bandwidth** (same as non-atomic overwrite). The RMW operation adds negligible serialization overhead beyond normal RR fair sharing. Confirmed for float32 (int32 fails backend assertion "Unsupported atomic type" — atomic is float-only in current compiler). First sender uses `with_sum=0` (overwrite to init), subsequent senders use `with_sum=1` (accumulate). MoE router-style 32-PE reduce to 1 GM_WDMA is expected to sustain ~100 GB/s (same as non-atomic incast cap).

---

## 6. Synchronization Framework

### 6.1 Two-Level Sync Hierarchy (Single-Card)

| Level | Scope | Mechanism | Function |
|-------|-------|-----------|----------|
| Inner Sync | Intra-PE (between units) | Unit-to-unit counter | Order DMA/Vector/Matrix ops within one PE |
| Outer Sync | Inter-node (PE<->PE, PE<->DMA, DMA<->DMA) | NoC sync packets | Dual-side transfer handshake across NoC |

Both levels use a counter-based release/acquire model.

### 6.2 Inner Sync

Each PE unit has a sync entry tracking resource availability:
- `ada_sync_release_inner(from_unit, dest_mask)`: Signal completion of `from_unit`; set bits in `dest_mask` to notify waiters.
- `ada_sync_acquire_inner(to_unit, src_mask)`: Block until all units in `src_mask` have released; then acquire `to_unit`.

**DEST_ENTRY_MASK**: `(0x1u << (unit_id - 1))` produces the one-hot bit for a unit.

**Standard sync patterns**:

| Pattern | Release -> Acquire | Use Case |
|---------|-------------------|----------|
| sync_download_to_vector | PE_DOWNLOAD_x -> PE_VECTOR | Data ready for vector compute |
| sync_vector_to_upload | PE_VECTOR -> PE_UPLOAD_x | Vector done, upload result |
| sync_download_to_upload | PE_DOWNLOAD_x -> PE_UPLOAD_x | Forward/passthrough (relay) |
| sync_upload_to_download | PE_UPLOAD_x -> PE_DOWNLOAD_x | Upload done, reuse download buffer |
| sync_download_to_matrix | PE_DOWNLOAD_x -> PE_MATRIX | Data ready for GEMM |
| sync_matrix_to_upload | PE_MATRIX -> PE_UPLOAD_x | GEMM done, send result |
| sync_matrix_to_download | PE_MATRIX -> PE_DOWNLOAD_x | GEMM done, start next download |
| sync_vector_to_matrix | PE_VECTOR -> PE_MATRIX | Vector preprocess done |
| sync_download_self | PE_DOWNLOAD_x -> PE_DOWNLOAD_x | Self-barrier (channel serialization) |
| sync_dma_channel_self | DMA_CHANNEL_x -> DMA_CHANNEL_x | DMA channel barrier |

> [TBD: Inner sync propagation latency (cycles from release to acquire visible on same PE).]

### 6.3 Outer Sync

Outer sync sends counter sync packets across the NoC between nodes. Parameters:
- `dest_type`: Target node type (NodeType enum)
- `dest_mask`: Bitmask of target node IDs (one bit per instance within type group)
- `dest_entry_mask`: One-hot mask of the target unit entry (e.g., DEST_ENTRY_MASK(PE_UPLOAD_0))

**Auto outer-sync wrappers** (cute::ada layer) derive sync parameters from IO calls:

| Wrapper | Pattern | Nodes |
|---------|---------|-------|
| `send_with_sync` | PE -> PE/DMA upload | PE upload unit -> dest unit |
| `recv_with_sync` | PE download | Dest unit -> download unit -> PE |
| `gm_send_sync` | GM RDMA -> PE/DMA | GM_RDMA -> PE (dual-side) |
| `gm_receive_sync` | PE -> GM WDMA | PE upload -> GM_WDMA |
| `gm_broadcast_sync` | GM -> PE multicast | GM_RDMA -> multiple PEs |
| `dma_local_receive_sync` | DMA self-sync | DMA channel barrier |
| `wdma_signal_rdma` | WDMA -> RDMA | WDMA completion -> RDMA start |
| `rdma_wait_wdma` | RDMA wait WDMA | RDMA blocks until WDMA done |
| `sync_aiu_download_to_scalar` | DMA -> Scalar | AIU download handoff |

> Sync packets have NO priority over data packets (§9.15); they are regular flits arbitrated via round-robin. Propagation latency follows standard NoC hop latency (8.5 cyc/hop one-way).

### 6.4 Pipeline Auto-Sync Framework

The `cute::ada::Pipeline` template provides software-level automatic inner-sync insertion:
- **Resource model**: Buffers identified by `(buf_id, chunk)` tuples.
- **Conflict detection**: RAW, WAR, WAW hazards across pipeline stages.
- **Modes**: `AUTO_CONSUME=false` (manual tracking); `AUTO_CONSUME=true` (automatic release/acquire insertion).
- **Limitations**: Does not model protocol-level sync inside IO helpers or per-unit busy state; only tracks buffer readiness.

### 6.5 Shadow Entry Types (Pipeline Stage Tracking)

The hardware exposes shadow (pipeline stage) sync entries that track occupancy at sub-unit granularity for performance modeling. Each unit occupies 4 consecutive shadow entries:

**PE shadow stages**:

| Shadow Entry | Value | Pipeline Stage |
|-------------|-------|---------------|
| ST_PE_MATRIX_READ_F | 4 | Matrix feature read |
| ST_PE_MATRIX_READ_W | 5 | Matrix weight read |
| ST_PE_MATRIX_CAL | 6 | Matrix compute |
| ST_PE_MATRIX_WRITE | 7 | Matrix writeback |
| ST_PE_VECTOR_READ | 8 | Vector SRAM read |
| ST_PE_VECTOR_CAL | 9 | Vector compute |
| ST_PE_VECTOR_WRITE | 10 | Vector writeback |
| ST_PE_SRAMC_DNLD_0 | 12 | Download CH0 SRAM controller |
| ST_PE_SRAMC_UPLD_0 | 16 | Upload CH0 SRAM controller |
| ST_PE_SRAMC_DNLD_1 | 20 | Download CH1 SRAM controller |
| ST_PE_SRAMC_UPLD_1 | 24 | Upload CH1 SRAM controller |

**MDMA (DMA node) shadow stages**:

| Shadow Entry | Value |
|-------------|-------|
| ST_MDMA_SHADOW_START | 32 |
| ST_MDMA_CHANNEL_0 | 36 |
| ST_MDMA_CHANNEL_1 | 40 |
| ST_MDMA_AIU_DOWNLOAD | 44 |

> The 4-entry spacing per unit (e.g., DNLD_0=12, UPLD_0=16, DNLD_1=20, UPLD_1=24) indicates multiple internal pipeline stages per SRAM controller, but exact stage semantics are not documented. [TBD: Latency in cycles per pipeline stage; initiation interval for back-to-back transfers.]

---

## 7. DLCM (Device-Level Configuration Memory)

Memory-mapped region at base address `0x20000000` containing chip-level configuration:

| Address Offset | Content | Description |
|---------------|---------|-------------|
| 0x30000 | ada_rank_list | Rank/topology information |

The `dlcm_ptr<T>(addr)` helper provides scalar-core access to DLCM.

---

## 8. Cycle Counter

Hardware cycle counter accessible from all ACI-clock nodes:

```cpp
__device__ inline uint64_t ada_get_cycle() {
    volatile uint64_t *addr = (uint64_t *)(0x40000000 + 0x21D8);
    return *addr;
}
```

Runs at 1125 MHz on PE/GM nodes, 1150 MHz on DDR nodes. Use `ada_get_default_frequency()` for the correct frequency on the current node type.

---

## 9. Measured Performance Data (Empirical, from Silicon Microbenchmark)

### 9.0 Data Provenance & Legend

**Three categories of parameters in this document, marked as follows:**

| Marker | Category | Description |
|--------|----------|-------------|
| **★** | **Mentor ground truth** | Directly provided by hardware team in response to NOC_MENTOR_QUESTIONS.md. Treat as authoritative; not measured. |
| **(measured)** | **Silicon measurement** | From microbenchmark runs on real ADA2S-32 silicon. Source files and methodology documented below. |
| **(derived)** | **Derived/inferred** | Computed from measured data or consistency arguments. Derivation logic documented. |

Unmarked items are either well-known software-visible facts (e.g., register map from SDK headers) or items where provenance is given inline.

### 9.0.1 Benchmark Source Files

| Benchmark | Source File | Mode | Section |
|-----------|-------------|------|---------|
| PE↔PE latency (RTT) & unidirectional BW | [noc_bench.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_bench.cpp) | .out / launchKernel | §9.1–9.6 |
| PE↔PE full sweep runner | [run_all.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/run_all.py) | launches .out | §9.1–9.6 |
| PE→GM single-PE upload bandwidth | [noc_gm_ul_bench.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_gm_ul_bench.cpp) | fatbin .adafb / launchFatbinKernel | §9.9 |
| PE→GM 32-PE all-column upload | [noc_gm_ul_allpe.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_gm_ul_allpe.cpp) | fatbin .adafb / launchFatbinKernel | §9.9 |
| PE→GM upload minimal no-scalar-write proof | [noc_gm_ul_nosw.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_gm_ul_nosw.cpp) | fatbin | §9.9 (root cause) |
| GM→PE download (single-sided) | [noc_gm_download.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_gm_download.cpp) (and noc_gm_bench.cpp) | .out | §9.9 |
| Upload benchmark runner | [test_ul_bench.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/test_ul_bench.py) | launches .adafb | §9.9 |
| All-PE upload runner | [test_allpe.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/test_allpe.py) and [sweep_allpe.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/sweep_allpe.py) | launches .adafb | §9.9 |
| In-router PE-side reduce+broadcast (X-chain 4-PE, Y-chain 8-PE) | [noc_rb39.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb39.cpp) (X-row Sum) / [noc_rb40.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb40.cpp) (Max) / [noc_rb41.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb41.cpp) (CH=1) / [noc_rb42.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb42.cpp) (Y-column) | fatbin .adafb / launchFatbinKernel | §9.11 |
| In-router 2-PE X/Y pair reduce | [noc_rb43.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb43.cpp) (X-pairs) / [noc_rb45.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb45.cpp) (Y-pairs) | fatbin .adafb / launchFatbinKernel | §9.11 |
| PE↔PE unicast ping-pong (baseline) | [noc_rb44.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb44.cpp) | fatbin .adafb / launchFatbinKernel | §9.11 |
| 32-PE full-chip 2D allreduce (X→Y→Y→X) | [noc_rb46.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb46.cpp) (CH0 Sum) / noc_rb47.cpp (CH1) / noc_rb48.cpp (Max) | fatbin .adafb / launchFatbinKernel | §9.12 |
| Dual-channel 32-PE allreduce (CH0+CH1 parallel) | [noc_rb49.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb49.cpp) | fatbin .adafb / launchFatbinKernel | §9.13 |
| Dual-channel PE↔PE same-dir parallel echo | [noc_rb50.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb50.cpp) | fatbin .adafb / launchFatbinKernel | §9.13 |
| PE↔PE full-duplex cross-channel | noc_pe_fulldup.cpp | fatbin .adafb | §9.13 |
| Reduce benchmark runner | [test_rb39.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/test_rb39.py) / test_rb40.py / test_rb41.py / test_rb42.py / test_rb43.py / test_rb44.py / test_rb45.py / test_rb46.py / test_rb47.py / test_rb48.py / test_rb49.py / test_rb50.py | launches .adafb | §9.11-13 |
| NMC DMA engine microarchitecture (cmd rate, queue depth) | [noc_rb51.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb51.cpp) / noc_rb52.cpp | fatbin .adafb | §9.14 |
| Link contention & RR arbitration | [noc_rb53.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb53.cpp) | fatbin .adafb | §9.15 |
| Packetization, per-hop latency, credit return | [noc_rb54.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb54.cpp) | fatbin .adafb | §9.16 |
| GM_WDMA DMA engine microarchitecture | [noc_rb55.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb55.cpp) | fatbin .adafb | §9.17 |
| SRAM port contention (NMC + Matrix, mtx-first) | [noc_rb56.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb56.cpp) (mtx-first) / [noc_rb57.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb57.cpp) (NMC-first) | fatbin .adafb | §9.18 |
| Full-duplex NMC + Matrix compute overlap | [noc_rb58.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb58.cpp) | fatbin .adafb | §9.19 |
| Vector engine BW/profile (read/write/vadd/vmul/gather) | [noc_rb59.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb59.cpp) / [test_rb59.py](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/test_rb59.py) | fatbin .adafb | §9.20 |
| Device reset kernel (for state cleanup between tests) | [noc_rb46.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb46.cpp) | fatbin .adafb | used between all tests |

### 9.0.2 Test Methodology (Common to All Benchmarks)

**Hardware**: ADA2S-32 silicon (8×4 mesh = 32 PEs + 4 GM_RDMA + 4 GM_WDMA + 4 DDR_RDMA + 4 DDR_WDMA = 48 nodes).
**Clock**: ACI domain (PE/GM/NoC routers) = **1125 MHz**; DDR domain = 1150 MHz (not used in these tests). All cycle counts reported are PE-side cycles from `ada_get_cycle()`.
**Compiler**: ADA SDK `ada_cc`, -O3 optimization (-O0 causes compiler crash for built-ins).
**Timing function**: `ada_get_cycle()` reads a 64-bit free-running cycle counter at MMIO `0x40000000 + 0x21D8` (see §8).

**Two compilation/launch modes**:

1. **`.out` mode** (`--offload-object-only` → `launchKernel()`): Single PE type only (PE nodes). DMA is handled implicitly by runtime. Used for PE↔PE benchmarks (noc_bench.cpp). Launch config: adaUnitType=PE(32).
2. **Fatbin `.adafb` mode** (`--offload-device-only -adas-const-node-type -adas-split-kernel-new` → `launchFatbinKernel()`): Split-kernel with multiple node types; PE code and DMA node code coexist in one binary. GM_WDMA code runs on the GM_WDMA scalar core. Launch config: 3-unit = PE(32) + GLPD_LD(4) + GSRM_ST(4) for GM dual-side transfers.

**Timing methodology**:
- 20 warmup iterations (untimed) to warm caches, establish credits, train branch predictors.
- 10–20 measured iterations; `min_cyc` reported (preferred, since zero-load jitter is <1 cycle on deterministic NoC) and `avg_cyc`.
- For ping-pong RTT (§9.1): PE0 sends 512B to dst, dst echoes back; PE0 fences before each timing read.
- For unidirectional BW (§9.3): PE0 sends `size_bytes` to dst, fences, receives 128B ACK; dst receives payload, fences, sends ACK. Time is one-way send + ACK return.
- For PE→GM upload (§9.9): PE0 does `send_with_sync` in a loop; GM_WDMA does `gm_receive_sync` matching. SRAM initialized via GM→SRAM DMA pre-load (NOT scalar writes, which coredump).

**Formula for bandwidth**:
```
BW (bytes/sec)  = size_bytes / (cycles / FREQ)
BW (GB/s)       = size_bytes × FREQ / cycles / 1e9     (FREQ = 1125e6)
BW (B/cyc)      = size_bytes / cycles
```
For ping-pong RTT patterns, payload BW = size_bytes × 2 / RTT_cycles (bidirectional), but we report one-way injection rate for large messages where ACK (128B) overhead is negligible.

**Linear fit for per-hop latency** (§9.1):
```
RTT(dynamic) = 250 + 17 × Manhattan_hops     (R² = 1.0)
One_way      = 125 + 8.5 × hops
RTT(static)  = 159 + 17 × hops               (compile-time constant sizes)
```
Endpoint setup = intercept/2; per-hop cost = slope/2 (one-way). The 17-cycle RTT slope = 2 × 8.5-cycle one-way per-hop latency.

### 9.1 NoC Latency (RTT, PE0 ↔ dst PE, 512B = 1 flit payload, dual-side send_with_sync/recv_with_sync, measured)

| Hops | Destinations | RTT min (cyc) | RTT avg (cyc) | RTT avg (µs) | One-way (µs) |
|------|-------------|---------------|---------------|--------------|--------------|
| 1 | PE1, PE4 | 267 | 267 | 0.237 | 0.119 |
| 2 | PE2, PE5, PE8 | 284 | 284 | 0.252 | 0.126 |
| 3 | PE3, PE6, PE9, PE12 | 301 | 301 | 0.268 | 0.134 |
| 4 | PE7, PE10, PE13, PE16 | 318 | 318 | 0.283 | 0.141 |
| 5 | PE11, PE14, PE17, PE20 | 335 | 335 | 0.298 | 0.149 |
| 6 | PE15, PE18, PE21, PE24 | 352 | 352 | 0.313 | 0.156 |
| 7 | PE19, PE22, PE25, PE28 | 369 | 369 | 0.328 | 0.164 |
| 8 | PE23, PE26, PE29 | 386 | 386 | 0.343 | 0.172 |
| 9 | PE27, PE30 | 403 | 403 | 0.358 | 0.179 |
| 10 | PE31 (corner-to-corner) | 420 | 420 | 0.373 | 0.187 |

**Linear model (dynamic shape dispatch):**
```
RTT_cycles  = 250 + 17 × Manhattan_hops        (R² ≈ 1.0, perfect linearity)
One_way_cyc = 125 + 8.5 × hops
```
- X/Y symmetry: **perfect** (same-Manhattan-distance PEs have identical latency regardless of X-first or Y-first path → confirms XY routing).
- Jitter: essentially zero (min=avg for all points at 512B; deterministic wormhole network under zero load).

**Static-shape baseline** (`Int<128>{}`, compile-time size, PE0↔PE1 128B): RTT=176 cyc (0.156 µs)
- Model: `RTT_static = 159 + 17 × hops`
- Dynamic-shape overhead: ~45 cycles per endpoint (NMC descriptor programming for runtime-variable sizes).

### 9.2 Latency vs Payload Size (1-hop, PE0↔PE1, RTT, measured)

| Size (B) | Payload flits (⌈size/512⌉) | min_cyc | avg_cyc | avg_us | Notes |
|----------|---------------------------|---------|---------|--------|-------|
| 16-512 | 1 | 265 | 265 | 0.236 | Single-flit (header+payload packed)★; fully latency-bound |
| 1024 | 2 | 265 | 265 | 0.236 | 2 flits (H+T, no body); still latency-bound (T pipelined immediately after H) |
| 2048 | 4 | 275 | 281 | 0.249 | Multi-flit (H+~2P+T); ~10 cyc incremental serialization visible in min_cyc |
| 4096 | 8 | 292 | 298 | 0.265 | Steady serialization slope |
| 8192 | 16 | 326 | 332 | 0.295 | |
| 16384 | 32 | 394 | 400 | 0.356 | |

**Observations**:
- Payloads ≤512B are single-flit packets (header+payload packed in one 512B flit)★; latency is constant at 265 cyc RTT.
- Payloads of ~1024B fit in a 2-flit packet (header flit + tail flit, no body flits). The second flit follows the first in a fully pipelined cut-through, adding no measurable latency over single-flit.
- Serialization becomes clearly visible at ≥2048B (3+ flits: H+body+T). Above 4KB, incremental bandwidth converges to **~120 B/cyc** (32KB→64KB slope = 32768B/274cyc = 119.6 B/cyc).
- The apparent "latency-bound" region extends to ~1KB (2 raw flits) because tail flits carry payload and cut-through routing hides the serialization of one trailing flit.

### 9.3 Unidirectional Bandwidth (PE0→dst, large send + 128B ACK return, CH0, send-fence-recv pattern)

| Size (B) | PE0→PE1 (1-hop) | PE0→PE28 (7-hop Y) | PE0→PE31 (10-hop diag) |
|----------|-----------------|--------------------|------------------------|
| | cyc / GB/s | cyc / GB/s | cyc / GB/s |
| 512 | 320 / 1.80 | 422 / 1.36 | 473 / 1.22 |
| 2048 | 322 / 7.16 | 431 / 5.35 | 482 / 4.78 |
| 8192 | 388 / 23.8 | 490 / 18.8 | 541 / 17.0 |
| 16384 | 456 / 40.4 | 558 / 33.0 | 609 / 30.3 |
| 32768 | 592 / 62.3 | 694 / 53.1 | 747 / 49.3 |
| 65536 | 866 / 85.1 | 972 / 75.9 | 1028 / 71.7 |

Asymptotic injection bandwidth (single NMC channel, 1-hop, large message, measured):
- **~120 B/cycle = 135 GB/s** (slope from 32KB→64KB: 32768 B ÷ 274 cyc = 119.6 B/cyc)
- This is consistent with a 1024-bit (128 B/cyc) physical datapath operating at ~94% efficiency (inter-flit bubbles + per-flit CRC/seq overhead); see Appendix A for derivation.

### 9.4 Bandwidth vs Hop Distance

| Hops | dst | BW @ 32KB (GB/s) | BW @ 64KB (GB/s) |
|------|-----|-------------------|-------------------|
| 1 | PE1 | 62.3 | 85.1 |
| 2 | PE2/PE8 | 60.6 | 83.8 |
| 3 | PE3/PE12 | 59.0 | 82.2 |
| 4 | PE16 | 57.4 | -- |
| 5 | PE20 | 55.9 | -- |
| 6 | PE24 | 54.5 | -- |
| 7 | PE28 | 53.1 | 75.9 |
| 10 | PE31 | 49.3 | 71.7 |

Per-hop BW degradation at 32KB: **~1.3 GB/s per additional hop** (~2% per hop).
Per-hop latency overhead for large messages: ~17 cycles RTT (same as zero-load, wormhole cut-through).
At 64KB, BW drops only ~16% from 1-hop to 10-hop — the NoC is highly pipelined.

### 9.5 Channel Independence (CH0 vs CH1) and Aggregation

**Cross-channel bidirectional (sequential) — noc_pe_fulldup mode=1 (2026-08-13 Round3)**:

| Channel | BW @ 64KB (GB/s) | Configuration |
|---------|-------------------|---------------|
| CH0 only | 85.1 | Single channel echo (send + 128B ACK) |
| CH1 only | 82.8 | Single channel (symmetric to CH0) |
| CH0→/→CH1 cross-duplex | **94.8** | Sequential: CH0 send size → fence → CH1 recv size; bidirectional aggregate |

**Same-direction parallel (back-to-back) — noc_rb50 mode=1 (2026-08-23)**:

| Size (B) | Single-ch echo BW (GB/s) | Dual-ch parallel BW (GB/s) | Ratio |
|----------|--------------------------|----------------------------|-------|
| 16384 | 45.3 | 56.9 | 1.26× |
| 32768 | 67.5 | 80.1 | 1.19× |
| 65536 | 88.9 | 99.6 | 1.12× |

Note: rb50 is an RTT echo (send→fence→recv), so one-way BW is ~2× the reported echo BW. Incremental per-channel one-way BW (32KB→64KB slope): **~117 B/cyc per channel** ≈ single-channel asymptotic (120 B/cyc), confirming channels operate independently for PE↔PE traffic.

**Clarification on channel sharing**: The earlier conclusion that "CH0/CH1 share bandwidth within an NMC pair" was based on (a) sequential cross-channel patterns and (b) PE→GM/DDR tests where the remote endpoint (GM_RDMA/DDR_RDMA) is the bottleneck, not the NoC link. For **PE↔PE same-direction parallel** (back-to-back `send_with_sync<0>` + `send_with_sync<1>` before a single fence), and for **PE↔PE collectives like allreduce**, both channels achieve full independent bandwidth (~120 B/cyc/channel), giving **~2× aggregate throughput** on NoC links. The 2× speedup is confirmed by dual-channel allreduce (§9.13) achieving 58 B/cyc vs 29.4 B/cyc single-channel = 1.97×.

| Channel mode | Aggregate injection BW | Notes |
|-------------|----------------------|-------|
| Single channel one-way (bulk) | ~119 B/cyc = **134 GB/s** | One NMC upload engine active; 93% PHY efficiency (§9.3, §9.19) |
| Single channel one-way (32KB batched) | ~87 B/cyc = 98 GB/s | 32KB × N messages; per-message overhead dominates (§9.18) |
| Single channel fulldup (bulk, TX+RX) | ~234 B/cyc = 263 GB/s | Upload+download independent; ~117 B/cyc/dir |
| Dual-channel same-direction parallel (PE↔PE bulk) | ~234 B/cyc = 263 GB/s | Both channels back-to-back, independent engines (§9.13) |
| Dual-channel fulldup (bulk, 2ch×2dir) | **~468 B/cyc = 527 GB/s** | Max per-PE aggregate N↔S + E↔W (§9.19) |
| Dual-channel PE→GM/DDR | ~114-126 GB/s | Remote DMA receiver is bottleneck; NOT 2× (§9.9-9.10) |

### 9.6 Contention (4 Disjoint Flows)

Pattern: PEs 0↔1, 2↔3, 4↔5, 6↔7 all send simultaneously (same row, disjoint links).

| Size (B) | Contended BW (GB/s) | Uncontended BW (GB/s) | Ratio |
|----------|---------------------|-----------------------|-------|
| 512 | 2.19 | 1.80 | 1.22× |
| 2048 | 8.44 | 7.16 | 1.18× |
| 8192 | 28.5 | 23.8 | 1.20× |
| 32768 | 68.9 | 62.3 | 1.11× |
| 65536 | 92.0 | 85.1 | 1.08× |

Non-overlapping flows show **no congestion degradation** (slightly faster likely due to NMC scheduling effects). Credit-based flow control with 1-flit buffers and RR arbitration isolates disjoint flows effectively. Many-to-one incast (flows sharing a link) was not tested and requires a different pattern.

### 9.7 Calibrated Model Parameters Summary

| Parameter | Value | Source |
|-----------|-------|--------|
| **Physical link (phit) width** | **1024 bits = 128 B/cyc** | Derived from 120 B/cyc asymptotic BW + 8.5 cyc/hop consistency |
| **Flit size** | **512 B = 4 phits** | Mentor-confirmed |
| **Packet format** | **Single-flit: inline; Multi-flit: 1H + P + 1T** ★ | Mentor-confirmed |
| **NoC clock** | **1125 MHz (ACI domain)** | Same as PE/GM; 5-stage pipeline self-consistent |
| Per-hop router latency (one-way) | **8.5 cycles** | Linear fit across 31 PEs |
| NMC endpoint setup (static shape) | **~80 cycles/endpoint** | 176 cyc/2 − 8.5 |
| NMC endpoint setup (dynamic/runtime size) | **~125 cycles/endpoint** | 267 cyc/2 − 8.5 |
| Dynamic-shape dispatch overhead | **~45 cycles** | Δ(static, dynamic) |
| Credit round-trip latency | **~17 cycles** (RTT hop = 2×per-hop) | Flit departs → credit returns |
| Per-channel injection BW | **~120 B/cyc = 135 GB/s** | Asymptotic large-message slope (single channel) |
| Per-channel raw phit BW | **144 GB/s** (128 B/cyc × 1125 MHz) | 1024-bit phit per channel, 94% efficiency gives 135 GB/s |
| Aggregate dual-ch per-direction BW | **~240 B/cyc = 270 GB/s** (PE↔PE) | CH0+CH1 independent for PE↔PE (§9.13); caps at ~126 GB/s to GM/DDR due to remote endpoint |
| CH0↔CH1 independence (PE↔PE) | **Independent physical datapaths, 2× aggregate for same-direction** | Same-direction parallel (back-to-back send<0>+send<1>) achieves ~240 B/cyc (270 GB/s); dual-channel allreduce 58 B/cyc = 1.97× single-channel (§9.13) |
| CH0↔CH1 to GM/DDR | Shared receiver bottleneck (NOT 2×) | PE→GM/DDR dual-ch caps at ~114-126 GB/s aggregate (§9.9-9.10); remote DMA endpoint is the limit, not NoC links |
| DMA instance independence | Independent ports, BW stacks ★ | Mentor-confirmed; 4×GM_WDMA measured 314 GB/s |
| Multicast replication | In-router single-write multi-read ★ | Mentor-confirmed |
| XY routing symmetry | Perfect (Manhattan distance only) | All 31 PE pairs tested |
| Single-flit payload capacity | **~500B** (derived) | Head flit: ~12B header + ~500B payload |
| Serialization-visible threshold | **≥2048B** (measured) | ≤1024B is latency-bound (2-flit H+T hides serialization) |
| Routing algorithm | XY dimension-order (X-first), deterministic, no adaptive ★ | Mentor-confirmed; verified by symmetry data |
| Arbitration | Round-robin (uniform BW, no unfairness) | Confirmed by N-way incast/outcast fair sharing (PE↔PE and PE↔GM) |
| VC per port | **1** ★ | Mentor-confirmed |
| Input buffer depth | **1 flit (512 B) per port** ★ | Mentor-confirmed |
| Flow control | Credit-based ★ | Mentor-confirmed; credit RTT ≈17 cyc measured |
| Jitter under zero load | <1 cycle (min=avg) | All measurements show deterministic timing |
| PE↔PE RTT latency (dynamic) | **250 + 17×hops cyc** | Linear fit across 31 PEs, R²=1.0; 8.5 cyc/hop one-way |
| PE↔PE asymptote BW (single ch, 1-hop) | **~118-120 GB/s** | 256KB ping-pong, CH0; matches PE↔GM download BW |
| PE NMC receive cap (N-way incast, single-ch) | **~125 GB/s per channel** | N PE senders to 1 PE receiver; fair RR; ~125/N GB/s per flow; dual-channel untested for incast |
| PE NMC send cap (N-way outcast, single-ch) | **~130 GB/s per channel** | 1 PE to N receivers; symmetric to incast; endpoint cap per channel |
| PE↔PE same-direction dual-ch (parallel) | **~270 GB/s aggregate** (asymptotic) | Back-to-back send<0>+send<1>; 2× single-channel (§9.13); chain reduce patterns achieve this |
| Disjoint-flow contention | **Zero interference** | 4 non-overlapping pairs each achieve ~119 GB/s independently; NoC fabric is not the bottleneck |
| Bottleneck location | **Endpoint NMC ports (PE/GM), not NoC fabric** | All endpoint types cap at ~120-130 GB/s/ch; disjoint flows scale linearly |
| PE→GM_WDMA fixed latency (single PE) | **138 cycles** one-way (0-hop PE28) / **257 cycles** one-way (7-hop PE0) | RTT = 2×138+17h = 276+119 = 395; measured 257cy one-way @7h |
| PE→GM_WDMA asymptote BW (single PE) | **~104 GB/s per channel** (0-hop) / ~90 GB/s (7-hop avg) | 256KB transfer, CH0 (104 GB/s = 262144/2462×1.125) |
| Per-GM_WDMA sustained BW (8 PEs sharing) | **~79 GB/s** | 32KB transfers, column-of-8 pattern |
| 4×GM_WDMA aggregate write BW | **~314 GB/s** | All 32 PEs, 32KB each, round-robin arbitration |
| N-way incast to 1 GM_WDMA (large msg) | **~100-120 GB/s aggregate cap** | Fair RR sharing; each PE gets ~100/N GB/s |
| Atomic add (with_sum=1) BW | **~120 GB/s aggregate** | RMW fully pipelined, same as non-atomic incast; float32 only |
| GM_RDMA→PE download fixed latency | **246 cycles** one-way (0-hop PE28) / **365 cycles** one-way (7-hop PE0) | GM_RDMA setup heavier than NMC upload (246 vs 138) |
| GM→PE download asymptote BW (single PE) | **~119 GB/s per channel** (0-hop 256KB) / ~113 GB/s (7-hop) | Download BW slightly higher than upload at large sizes |
| N-way outcast from 1 GM_RDMA (large msg) | **~120-125 GB/s aggregate cap** | Symmetric to incast; fair RR sharing |
| CH0+CH1 dual-channel to GM (both directions) | **114-124 GB/s aggregate** | GM receiver caps throughput; NOT 2×; gain ~4-15% (PE NMC itself can do 2× for PE↔PE, §9.13) |
| Bottom-row X fast path (upload+download) | **0 cyc for first 2-6 X-hops** | Dedicated GM-side crossbar; PEs closer to GM benefit most |
| GM→PE download (single PE, 4KB) | **~400 cycles ≈ 11.5 GB/s** | Dual-side or single-sided pull (equivalent, <3% diff) |
| 4×GM_RDMA aggregate broadcast BW | **projected ~500 GB/s** (4×125 GB/s) | Outcast pattern, MoE dispatch |
| Scalar SRAM write caveat (GM context) | **Scalar core writes crash** | Use GM→SRAM DMA init or vector writes; no scalar stores to SRAM when GM DMA functions are referenced |
| DDR clock | **1150 MHz** (vs ACI/NoC 1125 MHz) | Vendor spec; CDC async FIFO between domains |
| CDC async FIFO bubble penalty (DDR↔ACI) | **<10 cyc fixed overhead (negligible)** | Measured 2026-08-13 Round4: 512B ul base lat 193cyc vs GM 188cyc (+5cyc); BW modeling unaffected |
| DDR controller attachment | 4 DDRs at 4 corner routers (0,28,3,31) | list_ddr[4]={0,28,3,31} in adas_base_info.h; 1 DDR per corner, symmetric to GM placement |
| PE→DDR_WDMA 0-hop upload fixed lat | **193 cyc** one-way (512B min) | vs GM 138cyc (+55cyc: CDC + DDR WDMA write-posting setup) |
| PE→DDR_WDMA upload asymptote BW | **~122 GB/s/ch** (0-hop 512KB) | Close to PE NMC cap ~126 GB/s; ul_min@256K+ constant (2335cyc) for all distances — pipeline fully hides hop latency |
| DDR_RDMA→PE 0-hop download fixed lat | **426 cyc** one-way (512B min) | vs GM 246cyc (+180cyc: DDR PHY read activation + row-buffer overhead is the dominant cost) |
| DDR_RDMA→PE download asymptote BW | **~103 GB/s/ch** (0-hop 512KB) | ~13% lower than GM (119 GB/s) due to DDR memory controller read-side limitation; NOT NoC-limited |
| PE↔DDR hop cost (download, per hop) | **~15-17 cyc/hop** (dl_min) | Same as PE↔GM/PE↔PE; X fast path on rows 0/7 also applies (~11 cyc/hop X vs ~17 cyc/hop Y) |
| PE↔DDR hop cost (upload, per hop) | **0 cyc/hop (min, large msg)** | Deep DDR_WDMA write-posting pipeline completely hides hop latency for ≥256KB messages |
| PE↔DDR CH0+CH1 dual-ch upload aggregate | **~126 GB/s** (512KB total) | DDR_WDMA receiver caps throughput (NOT 2×); single-stream already at 122 GB/s so dual-ch adds <4% at large sizes; contrast with PE↔PE which achieves 2× (§9.13) |
| DDR↔NoC bottleneck location | **DDR_RDMA read + DDR_MC**, not NoC fabric | All 4 DDRs symmetric; disjoint flows expected to scale (4-disjoint-DDR aggregate TBD med-pri) |
| Scalar SRAM write caveat (DDR context) | **Scalar core writes crash** | Same as GM; must use ddr_to_sram for PE-side preload |
| Scalar SRAM read alias | **0x20000000 + 0x100000 + offset** (DLCM_BASE + LOCAL_SRAM_ADDR) | Scalar core must use alias to read NOC-populated data; physical address 0x100000+offset causes coredump |
| **In-router reduce (X 4-PE row)** | **335 cyc base + sz/59 cyc** (post-warmup) | Reduce+bcast allreduce; 335cyc=298ns; 59 B/cyc = 66 GB/s; §9.11 |
| **In-router reduce (Y 8-PE col)** | **502 cyc base + sz/60 cyc** (post-warmup) | Reduce+bcast allreduce; 502cyc=446ns; 60 B/cyc = 67 GB/s; §9.11 |
| **In-router reduce per-hop cost** | **~42 cyc/hop** (chain depth overhead) | Includes ALU + reverse-chain release; BW unchanged per hop |
| **In-router reduce BW** | **~59 B/cyc = 66 GB/s (92% of theoretical)** | ALU fully pipelined; same BW as unicast for large payloads |
| **In-router Sum vs Max** | **Identical performance** | Same latency, same BW |
| **In-router CH0 vs CH1** | **Symmetric** | Same latency, same BW |
| **Reduce mandatory broadcast release** | Root MUST pe_broadcast_sync after reduce | Download units left in reduce-forward state; non-roots must recv_with_sync(root) |
| **Minimum reduce transfer size** | **≥256B** (multi-iteration) | Sub-256B may have stale flit contamination |
| **PE↔PE unicast one-way BW** | **~120 B/cyc = 135 GB/s** (asymptotic, all distances) | Hop cost fully hidden for ≥32KB; small-msg ~17 cyc/hop; RTT base 222 cyc (1-hop) |
| **PE↔PE unicast vs reduce BW ratio** | **0.49× (reduce is ~half unicast BW)** | Two sequential payload passes (reduce→bcast); ALU adds zero BW cost |
| **32-PE full-chip allreduce base latency** | **1090 cyc = 969 ns** (post-warmup, ≤1KB) | 4-phase (X-red→Y-red→Y-bcast→X-bcast); root PE31 |
| **32-PE allreduce asymptotic BW** | **29.4 B/cyc = 33 GB/s** (≥8 KB) | = 120/4 = link BW / 4 phases; 98% efficiency |
| **32-PE allreduce Sum vs Max** | **Identical within 1%** | Same latency, same BW |
| **32-PE allreduce CH0 vs CH1** | **Identical (exact match)** | Channels symmetric for collective operations |
| **Dual-channel allreduce programming** | **Back-to-back post ch0+ch1 descriptors per phase, one fence_io per phase** | ReduceOp::Sum works in parallel on both channels; no inter-channel fence needed within a phase; §9.13 |
| **Dual-ch 32-PE allreduce base latency** | **~1450 cyc = 1289 ns** (post-warmup, ≤2KB) | 4× (ch0+ch1 parallel) phases; extra ~360 cyc from programming 8 NMC descriptors vs 4; §9.13 |
| **Dual-ch 32-PE allreduce asymptotic BW** | **~58 B/cyc = 65 GB/s** (≥96 KB) | = 2× single-channel (29.4 B/cyc); 98% of theoretical 2×; §9.13 |
| **Dual-ch allreduce crossover point** | **~32 KB** (dual-ch faster above, slower below) | Below 32KB: extra NMC programming overhead dominates; above: 2× BW dominates; §9.13 |
| **Dual-ch PE↔PE same-dir parallel BW** | **~120 B/cyc agg (60 B/cyc/ch) asymptotic** (≥32KB) | Both channels inject simultaneously; aggregate 2× single-channel; §9.13 |
| **PE hardware broadcast (pe_broadcast_sync)** | **Zero-cost in-router fan-out** | Round5 2026-08-13: PE0->31 PEs @256KB min=2354cyc (vs 2347cyc N=1, +0.3%); BW per receiver 125 GB/s (~NMC cap); 512B latency 127cyc vs 120cyc (+7cyc spanning-tree); aggregate BW ~3.9 TB/s to 31 PEs |
| **GM hardware broadcast (gm_broadcast_sync)** | **Zero-cost in-router fan-out, ~135 GB/s/stream** | Round5 2026-08-13: GM1->32 PEs @512KB avg=4376cyc (vs 4262cyc N=1, +2.7%); BW per receiver ~135 GB/s; aggregate ~4.3 TB/s; command-issue latency 443cyc(N=1)/598cyc(N=32) fire-and-forget |
| Broadcast vs unicast N-way speedup | **Nx linear up to N*126 GB/s** | Round5: N=31 PE-bcast ~3887 GB/s agg vs ~130 GB/s unicast = **30x speedup**; in-router SWMR replication adds ~7cyc flat overhead independent of N |
| Full-chip spanning-tree setup | **~7cyc (PE bcast), ~155cyc (GM bcast)** | One-time router table programming; adding receivers beyond tree depth adds 0 per-receiver latency |
| **NMC descriptor post cost** | **~57 cyc/cmd** (send or recv) | Independent of msg size (512B-8KB); N=1 baseline 61 cyc; §9.14 |
| **NMC outstanding queue depth** | **~24 entries/channel** | Linear 56.2 cyc/msg up to N=24; backpressure at N=28; §9.14 |
| **GM_WDMA descriptor post cost** | **~40 cyc/cmd** (unblocked) | Faster than NMC (57 cyc); §9.17 |
| **GM_WDMA outstanding queue depth** | **~4 entries/channel** | 6x shallower than NMC's 24; §9.17 |
| **GM_WDMA small-msg completion rate** | **~273 cyc/msg** (after queue fills) | Limits PE→GM small-msg throughput to ~1.9 GB/s/ch/PE; batching ≥32KB critical for GM BW; §9.17 |
| **Link contention (≤8 KB messages)** | **Zero interference** | NMC queue absorbs transient backpressure; MoE traffic (<8KB/expert) sees no contention; §9.15 |
| **Link contention (≥16 KB, saturating)** | **Fair RR, ~92% aggregate link utilization** | Each flow gets fair share; ~110 B/cyc aggregate on a 120 B/cyc link; §9.15 |
| **Flit serialization** | **4 phits × 1 cyc/phit = 4 cyc/flit** | Hidden for ≤4KB messages; MTU=512B (1 flit); §9.16 |
| **Per-flit overhead** | **~57 cyc/post + credit bubble** | Causes small-msg (32KB) effective BW ~70% of asymptote; §9.14-9.16 |
| **Same-channel full-duplex (NMC)** | **~99% efficiency (2× simplex BW)** | TX+RX on same CH achieve 2× rate; independent TX/RX datapaths; §9.19 |
| **Cross-channel duplex (CH0 TX + CH1 RX)** | **~98.5% efficiency (2× simplex BW)** | Identical to same-channel fulldup; confirms independent NoC meshes; §9.19 |
| **Dual-channel full-duplex (2ch × 2dir) bulk** | **~468 B/cyc = 527 GB/s** | 2 NoCs × 2 dir × ~117 B/cyc = 97.5% efficiency; §9.19 |
| **Dual-channel full-duplex @ 32KB batch** | **~340 B/cyc = 382 GB/s** | MoE tile granularity; 97.5% of 4×87 B/cyc; §9.19 |
| **SRAM port contention (NMC + Matrix, mtx-first)** | **≤7% worst-case (NMC-bound), ≤3% (matrix-bound)** | Local & Weight SRAM are independent banks; §9.18-9.19 |
| **SRAM port contention (Matrix + Vector)** | **~19% wall slowdown, Vec @80% BW** | Write-port bottleneck (Matrix C writes + vec_write); §9.21 |
| **SRAM port contention (NMC + Vector)** | **~26-29% wall slowdown, both @77% BW** | Both read+write ports shared; fair RR; §9.21 |
| **SRAM port contention (NMC + Matrix + Vector)** | **~44-48% wall slowdown, NMC @66%, Vec @65%** | Severe three-way contention; avoid full three-way overlap; §9.21 |
| **Local SRAM write port aggregate BW** | **~190-200 B/cyc (sustainable)** | Combined NMC RX + vec_write + Matrix C writes saturate at ~194 B/cyc; §9.21 |
| **Local SRAM read port aggregate BW** | **~260-290 B/cyc (sustainable)** | vec_read alone achieves 260 B/cyc; §9.20, §9.21 |
| **Weight SRAM independence** | **Fully separate ports, zero contention** | Matrix B reads from Weight SRAM don't interfere with Local SRAM traffic; §9.18, §9.21 |
| **bmm command queue depth** | **~4 tiles** | Issuing >4 bmm() back-to-back stalls scalar core; single tile ~2120 cyc; §9.18 |
| **Optimal command ordering** | **mtx-first**: fire bmm → post DMA → continue bmm → fence | NMC-first ordering 34% slower at mtx=8,N=32; §9.18 |
| **Vector vec_read BW (Local SRAM, bulk)** | **~260 B/cyc = 292 GB/s**, ~88 cyc overhead | Single TPC DMA; 146 B/cyc @ 32KB (56% of asymptote); §9.20 |
| **Vector vec_write BW (Local SRAM, bulk)** | **~129 B/cyc = 145 GB/s**, ~95 cyc overhead | 2× slower than read; 91 B/cyc @ 32KB; §9.20 |
| **Vector vadd/vmul throughput** | **~16 fp32/cyc/TPC** (512-bit SIMD) | 4-TPC aggregate ~64 fp32/cyc = 72 GFLOP/s theoretical; §9.20 |
| **Vector gather BW (BAUA, bulk)** | **~128 B/cyc = 144 GB/s**, ~100 cyc overhead | Identical for seq/rand indices (no random-access penalty); 90 B/cyc @ 32KB; §9.20 |
| **DLCM alias rule** | **64-bit scalar writes MUST use 0x20xxxxxx** | Non-DLCM 64-bit writes cause silent corruption/coredump (exit 134); applies to gather tables; §C.7, §9.20 |
| **PE scalar core FP support** | **None** (no HW FP) | float/double arithmetic causes linker errors (__mulsf3 etc.); all BW computed on host; §C.8 |

### 9.8 Pre-existing End-to-End Kernel Measurements (Reference)

| Metric | Value | Conditions |
|--------|-------|------------|
| Transformer layer steady-state (no MoE) | ~109 µs | 32 PE, 32 query tokens / 2048 KV context, TP |
| Transformer layer steady-state (with MoE) | ~161 µs | Includes expert routing overhead |
| RMSNorm kernel (C++ hand-optimized) | ~40 µs | hidden=5120, bf16, 32 PE |
| Cold start (kernel launch) | ~10 ms | One-time firmware/init overhead |
| Single-side vs dual-side throughput ratio | **<3% difference** (equivalent) | Re-measured 2026-08-13; earlier "10% degradation" claim was incorrect (misconfigured test) |

### 9.9 PE ↔ GM Bandwidth (Measured)

GM DMA nodes attach to the bottom-edge routers (Y=7, routers 28-31). PE→GM path uses `send_with_sync<CH>(sram_view, NodeType::GM_WDMA, col_id)` on PE side paired with `gm_receive_sync<CH>(gm_view, NodeType::PE, pe_id)` on GM_WDMA side, running in fatbin split-kernel mode (`--offload-device-only -adas-const-node-type -adas-split-kernel-new`).

**Critical programming note**: Scalar core writes to PE local SRAM cause device coredump when the kernel references GM DMA functions (`gm_to_sram`, `sram_to_gm`, `send_with_sync` to GM_WDMA, etc.). DMA-initiated writes (GM→SRAM download) and vector engine writes work correctly. To initialize SRAM for upload, use a GM→SRAM download pre-load. This appears to be a compiler/MMU-configuration artifact when GM DMA descriptors are present in the PE binary.

**PE0 → GM_WDMA0 (single PE, single GM channel, CH0, routers: PE0=0, GM_WDMA0=28, hopdist=7 down-Y)**:

| Size (B) | min_cyc | avg_cyc | BW (MB/s) | Notes |
|----------|---------|---------|-----------|-------|
| 64 | 263 | 272 | 265 | Latency-bound (~263 cyc fixed overhead) |
| 128 | 263 | 271 | 531 | |
| 256 | 263 | 274 | 1,051 | |
| 512 | 263 | 276 | 2,087 | |
| 1024 | 280 | 284 | 4,067 | |
| 2048 | 280 | 292 | 7,900 | |
| 4096 | 297 | 310 | 14,868 | Compare: GM→PE download 4096B ≈ 402 cyc = 11.5 GB/s |
| 8192 | 331 | 344 | 26,784 | |
| 16384 | 373 | 409 | 45,110 | |
| 32768 | 373 | 535 | 68,888 | |
| 65536 | 662 | 816 | 90,386 | Approaching single-NMC-channel limit |

- **Fixed upload latency (PE→GM, single PE)**: ~263 cycles one-way (vs ~125+8.5×7=185 cyc predicted from PE↔PE linear model, ~78 cyc extra for GM_WDMA endpoint setup/handshake)
- **Asymptotic single-PE upload bandwidth**: ~90 GB/s at 64KB per channel (similar to PE↔PE asymptotic ~85-135 GB/s range)
- **GM→PE (download) single PE**: 4096B @ 402 cyc ≈ 11.5 GB/s (measured with single-sided `gm_to_sram` + `sync_download_self`, .out mode); download direction has higher fixed overhead than upload for small sizes but converges similarly for large messages.

**All 32 PEs → 4 GM_WDMAs (MoE column pattern, CH0)**:
PEs mapped column-wise: `col_id = pe_id % 4`; each GM_WDMA serves 8 PEs in its column (row 0..7 = pe_id = col_id, 4+col_id, ..., 28+col_id). All 4 columns operate in parallel. PE0's measured time includes contention with 7 other PEs in column 0.

| Size (B) | PE0 min_cyc | PE0 avg_cyc | Per-GM_WDMA BW (GB/s) | Aggregate BW (4 ch × 8 PEs, GB/s) |
|----------|-------------|-------------|----------------------|----------------------------------|
| 64 | 570 | 1,817 | 0.32 | 1.3 |
| 256 | 383 | 1,827 | 1.26 | 5.0 |
| 1024 | 332 | 1,893 | 4.8 | 19.3 |
| 4096 | 162 | 2,054 | 17.9 | 71.7 |
| 8192 | 213 | 2,233 | 33.1 | 132 |
| 16384 | 281 | 2,527 | 46.6 | 187 |
| 32768 | 417 | 3,362 | 78.5 | 314 |

- **Per-GM_WDMA sustained bandwidth**: ~79 GB/s per channel at 32KB (8 PEs sharing, ~9.8 GB/s per PE when fully loaded)
- **Aggregate GM write bandwidth (all 32 PEs to 4 GM_WDMAs)**: ~314 GB/s at 32KB; approaching 4 × ~90 GB/s = 360 GB/s theoretical for 4 parallel GM_WDMA channels.
- **Latency under contention**: PE0 sees ~1,800+ cycles for small messages when contending with 7 other PEs in its column (≈ 8× single-PE latency, confirming round-robin arbitration at GM_WDMA receive).

### 9.10 PE ↔ DDR Bandwidth (Measured, Round4 2026-08-13)

Kernels: [noc_ddr_ul_generic.cpp](file:///home/tiger/adas_lp_kernels_new/kernels/cpp/noc_microbench/noc_ddr_ul_generic.cpp), [noc_ddr_dl_generic.cpp](file:///home/tiger/adas_lp_kernels_new/kernels/cpp/noc_microbench/noc_ddr_dl_generic.cpp), [noc_ddr_ul_dualch.cpp](file:///home/tiger/adas_lp_kernels_new/kernels/cpp/noc_microbench/noc_ddr_ul_dualch.cpp). Runner: [sweep_ddr_round4.py](file:///home/tiger/adas_lp_kernels_new/kernels/cpp/noc_microbench/sweep_ddr_round4.py).

DDR attachment: 4 controllers at the 4 corner routers — DDR0 at router 0 (PE0, top-left), DDR1 at router 28 (PE28, bottom-left), DDR2 at router 3 (PE3, top-right), DDR3 at router 31 (PE31, bottom-right). Each DDR has one RDMA (read) and one WDMA (write) engine, analogous to GM but connecting to off-chip DRAM (1150 MHz CDC domain).

**E1. PE28 ↔ DDR1 size sweep (0-hop baseline)**:

| Size (B) | ul_min (cyc) | ul_avg (cyc) | ul BW (GB/s) | dl_min (cyc) | dl_avg (cyc) | dl BW (GB/s) |
|----------|-------------|-------------|-------------|-------------|-------------|-------------|
| 512      | 193         | 224         | 2.6         | 426         | 442         | 1.3         |
| 4096     | 346         | 380         | 12.1        | 574         | 590         | 7.8         |
| 16384    | 465         | 480         | 38.4        | 693         | 769         | 24.0        |
| 65536    | 873         | 912         | 80.8        | 1169        | 1276        | 57.8        |
| 262144   | 2335        | 2582        | 114.2       | 2852        | 3147        | 93.8        |
| 524288   | 4579        | 4828        | **122.0**   | 5334        | 5728        | **102.9**   |

Key observations:
- **Upload asymptote ≈122 GB/s @512KB**, nearly identical to PE↔GM upload (127 GB/s) and PE↔PE (118 GB/s), confirming upload is PE NMC-port limited (not DDR).
- **Download asymptote ≈103 GB/s @512KB**, ~13% lower than GM download (119 GB/s). Bottleneck is DDR_RDMA read / DDR memory controller (row activation, PHY, tRCD/tRP), NOT the NoC fabric.
- **Small-message base latency**: upload 193 cyc (+5cyc vs GM's 188cyc = CDC overhead); download 426 cyc (+180cyc vs GM's 246cyc = DDR PHY read pipeline setup, the dominant asymmetry).
- **ul_min = 2335–2337 cyc at 256KB for ALL hop distances 0–10** (see E2/E3): DDR_WDMA write-posting FIFO completely hides hop latency for large uploads.
- **dl_min scales with hop count** at ~15–17 cyc/hop (same RTT model as GM/PE↔PE), with X fast path on rows 0/7 also confirmed (~11 cyc/hop X vs ~17 cyc/hop Y).

**E2. CH0+CH1 dual-channel (PE28→DDR1 0-hop)**:

| Per-ch size | Total | min_cyc | agg BW (GB/s) | vs single-ch |
|------------|-------|---------|--------------|-------------|
| 32KB       | 64KB  | 757     | 97.3         | +20%        |
| 64KB       | 128KB | 1301    | 113.2        | +12%        |
| 128KB      | 256KB | 2423    | 121.6        | +6%         |
| 256KB      | 512KB | 4667    | **126.3**    | +3%         |

Conclusion: When sending to DDR, the DDR_WDMA receiver is the bottleneck (not the PE NMC or NoC links). Aggregate cap ~126 GB/s, NOT 2× single channel; dual-channel helps small/medium sizes (12-20% gain at 64-128KB) but adds <4% at 512KB where single-stream already saturates the DDR path. Note: This does NOT mean PE NMC channels share bandwidth — for PE↔PE traffic (where both endpoints have dual-channel NMC engines), same-direction parallel achieves ~2× BW (§9.13). DDR0 (PE0→DDR0) dual-ch gives identical 126.2 GB/s → all 4 DDRs symmetric.

**E3. Y-hop sweep @256KB (X=0 column → DDR1) confirms ul_min = 2337 = constant across 0–7 hops; dl_min increases 102cyc over 7 hops = 14.6 cyc/hop.** Cross-corner (PE0→4 DDRs) confirms X fast-path on top row (Y=0): X-only 3-hop = +34cyc (11.3 cyc/hop) vs Y-only 7-hop = +119cyc (17.0 cyc/hop), same asymmetry as GM/PE↔PE.

**Latency formulas (conservative model):**

- PE→DDR upload one-way (min, large ≥256KB): T_ul_min(sz) = 193 + sz/128 cyc (hop term = 0; pipeline hides all hop latency)
- PE→DDR upload one-way (avg): T_ul_avg(sz, h) = 220 + 17·h + sz/108 cyc
- DDR→PE download one-way (min): T_dl_min(sz, h) = 426 + 17·h + sz/120 cyc
- DDR→PE download one-way (avg): T_dl_avg(sz, h) = 442 + 17·h + sz/108 cyc

---

### 9.11 In-Router PE-Side Reduction (Measured, Round6 2026-08-20)

In-router reduction uses the hardware Sum/Max ALU in each NoC router to perform element-wise reduction along a chain of PEs. The pattern is: leaf PE sends East/South with `(RouteMode::Remote, reduce_op, RouteMode::Local)`, intermediate PEs passthrough+reduce with `(Remote, reduce_op, Remote, prev_pe)`, root PE receives then sends reverse-chain release, then root broadcasts result back to all participants via `pe_broadcast_sync`.

**Benchmark kernels**: [noc_rb39.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb39.cpp) (X-row 4-PE Sum, CH0), [noc_rb40.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb40.cpp) (Max), [noc_rb41.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb41.cpp) (CH=1), [noc_rb42.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb42.cpp) (Y-column 8-PE). All 32 PEs run simultaneously in 8 independent X-row chains or 4 independent Y-column chains; timing measured on one root PE (PE3 for X, PE31 for Y). Methodology: 5 warmup + 20 measured iterations; post-warmup cycle counts are deterministic (zero jitter); t0 before first send, t1 after `ada_sync_fence_io()` completes all transfers.

**API pattern (from production `pe_rowwise_reduce`/`pe_rowwise_broadcast` in [rowwise_pe_allreduce.hpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/m13_attention_tp4dp8/rowwise_pe_allreduce.hpp))**:
```cpp
// Leaf (col 0)
send_with_sync<CH>(view, PE, pe_id+1, Remote, Sum, Local);
// Mid (col 1..N-2)
send_with_sync<CH>(view, PE, pe_id+1, Remote, Sum, Remote, pe_id-1);
// Root (col N-1): recv reduce result + release reverse chain
recv_with_sync<CH>(view, PE, pe_id, Local, Remote);
send_with_sync<CH>(view, PE, pe_id, Local, Sum, Remote, pe_id-1);
// Root broadcasts to all
pe_broadcast_sync<CH>(view, dst_mask_sync);
// Non-root receives broadcast release
recv_with_sync<CH>(view, PE, root_pe);
```

**Critical programming note for result verification**: To read NOC-populated data from the scalar core after `ada_sync_fence_io()`, the scalar core MUST use the DLCM-aliased address `0x20000000 + 0x100000 + offset` (i.e., `DLCM_BASE + LOCAL_SRAM_ADDR + offset`). Direct reads from the physical address `0x100000 + offset` cause device coredump. Writing to SRAM from scalar core in a fatbin context that references GM/DMA DMA functions also causes coredump (same caveat as §C.1).

**X-direction row reduce (4 PEs/row, 3-hop chain, 8 rows running in parallel)**:

| Size (B) | min_cyc (post-warmup) | Payload cyc | Effective BW (B/cyc) | Effective BW (GB/s) |
|----------|----------------------|-------------|---------------------|---------------------|
| 256–512 | 348–365 | 0–17 | 0.7–1.4 | 0.8–1.6 |
| 1024 | 365 | 17 | 2.8 | 3.2 |
| 2048 | 365 | 17 | 5.6 | 6.3 |
| 4096 | 399 | 51 | 10.3 | 11.5 |
| 8192 | 467 | 119 | 17.5 | 19.7 |
| 16384 | 603 | 255 | 27.2 | 30.6 |
| 32768 | 892 | 544 | 36.7 | 41.3 |
| 65536 | 1453 | 1105 | 45.1 | 50.7 |
| 98304 | 2014 | 1666 | 48.8 | 54.9 |

**Y-direction column reduce (8 PEs/column, 7-hop chain, 4 columns running in parallel)**:

| Size (B) | min_cyc (post-warmup) | Payload cyc | Effective BW (B/cyc) | Effective BW (GB/s) |
|----------|----------------------|-------------|---------------------|---------------------|
| 512 | 519 | -- | 1.0 | 1.1 |
| 4096 | 570 | 68 | 7.2 | 8.1 |
| 8192 | 638 | 136 | 12.8 | 14.4 |
| 16384 | 774 | 272 | 21.2 | 23.8 |
| 32768 | 1046 | 544 | 31.3 | 35.2 |
| 65536 | 1607 | 1105 | 40.8 | 45.9 |

**Linear model (post-warmup, ≥4KB where payload dominates)**:

```
X-reduce (4 PE, 3-hop):  cycles = 335 + 0.0170 × size_bytes     (R² ≈ 1.0)
                         base = 335 cyc (298 ns), slope = 0.0170 cyc/B → 59 B/cyc = 66 GB/s
Y-reduce (8 PE, 7-hop):  cycles = 502 + 0.0168 × size_bytes     (R² ≈ 1.0)
                         base = 502 cyc (446 ns), slope = 0.0168 cyc/B → 60 B/cyc = 67 GB/s
```

**X-direction 2-PE pair reduce (1-hop chain, 16 independent pairs running in parallel)**:

| Size (B) | min_cyc (post-warmup) | Payload cyc | Effective BW (B/cyc) | Effective BW (GB/s) |
|----------|----------------------|-------------|---------------------|---------------------|
| 256–1024 | 273 | ~23 | 0.9–3.8 | 1.0–4.2 |
| 2048 | 290 | 40 | 7.1 | 7.9 |
| 4096 | 307 | 57 | 13.3 | 15.0 |
| 8192 | 375 | 125 | 21.8 | 24.6 |
| 16384 | 528 | 278 | 31.0 | 34.9 |
| 32768 | 803 | 553 | 40.8 | 45.9 |
| 65536 | 1361 | 1111 | 48.2 | 54.2 |
| 98304 | 1922 | 1672 | 51.1 | 57.5 |

**Linear model verification (per-hop cost)**:

```
2-PE X-pair (1-hop East, 16 parallel):   cycles = 250 + 0.0170 × size_bytes  → 59 B/cyc = 66 GB/s, base=222ns
2-PE Y-pair (1-hop South, 16 parallel):  cycles = 263 + 0.0172 × size_bytes  → 58 B/cyc = 65 GB/s, base=234ns
4-PE X-row  (3-hop East, 8 parallel):    cycles = 335 + 0.0170 × size_bytes  → 59 B/cyc = 66 GB/s, base=298ns
8-PE Y-col  (7-hop South, 4 parallel):   cycles = 502 + 0.0168 × size_bytes  → 60 B/cyc = 67 GB/s, base=446ns

Per-chain-hop overhead: ~42 cyc/hop (includes reverse release + broadcast fan-out setup)
X vs Y direction: within ~5% — essentially symmetric for both latency and BW
```

**Cost decomposition vs plain unicast** (measured via separate ping-pong benchmark [noc_rb44.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb44.cpp) on same hardware):

| Operation | Base latency (1-hop, post-warmup) | Asymptotic BW | Payload passes |
|-----------|-----------------------------------|---------------|----------------|
| PE↔PE unicast RTT (send + small ack) | ~150 cyc (large msg floor), 222 cyc (small msg min) | **120 B/cyc = 135 GB/s** | 1 (forward dominates) |
| Reduce+bcast allreduce (2 PE) | 250–263 cyc | **59 B/cyc = 66 GB/s** | 2 (forward reduce + reverse bcast) |
| Ratio: allreduce/unicast | 1.7× (base) | 0.49× (BW) | 2× payload, sequential |

The ~50% bandwidth ratio confirms that in-router allreduce is **bandwidth-limited by two sequential full-payload traversals** (reduce toward root + broadcast back to leaves). The in-router ALU itself adds **zero measurable bandwidth penalty** — BW is exactly half of single-stream unicast, as expected from the two-pass nature of ring/reduce-then-broadcast.

The base latency difference (250 cyc allreduce vs 150 cyc unicast RTT) is ~100 cyc, which accounts for: (a) one additional NMC descriptor programming (reverse-chain `send_with_sync`), (b) `pe_broadcast_sync` spanning-tree setup, (c) one additional hop of reverse-chain traversal at each intermediate PE. The per-chain-hop cost of ~42 cyc is consistent with the ~17 cyc/hop unicast forward cost plus the ~25 cyc/hop reverse release cost.

**Plain unicast hop cost (for reference)**: 1-hop East small-RTT min=222 cyc, 3-hop East=256 cyc, 7-hop South=324 cyc → **~17 cyc/hop for small messages** (XY deterministic, cut-through). For large messages (≥32 KB), hop cost is fully hidden by pipelining; BW is identical (120 B/cyc) at all distances from 1 to 10 hops.

**Key findings**:
- **Per-hop overhead**: (502 − 335) / (7 − 3) = **~42 cycles per additional chain hop** (includes router ALU + reverse-chain release setup). This is the fixed per-hop latency for the reduce-tree depth, independent of payload size.
- **Bandwidth scaling**: Incremental bandwidth is ~59 B/cyc for both X and Y chains, independent of chain length. The Sum/Max ALU is fully pipelined and does not create a serialization bottleneck.
- **Theoretical comparison**: A 3-hop reduce + 3-hop reverse bcast sequentially traverses 6 links; with cut-through pipelining, asymptotic throughput approaches one direction of the link (128 B/cyc) divided by 2 for round-trip = 64 B/cyc. Measured 59 B/cyc = **92% efficiency** (residual 8% from NMC programming overhead and per-flit bubbles).
- **Cold-start (first iteration)**: ~573 cycles for 512B (vs 348 post-warmup), representing ~225 cycles of credit warmup / NMC cold-cache overhead.
- **Sum vs Max**: identical cycle counts for all sizes (hardware ALU handles both ops at same throughput).
- **CH0 vs CH1**: identical cycle counts (channels are symmetric).
- **Correctness**: v0=10 confirmed for X-reduce (1+2+3+4), v0=36 for Y-reduce (1+2+...+8), v0=4 for Max reduce (max(1,2,3,4)).
- **Allreduce total cost**: The measured cost is the **complete reduce + broadcast** (allreduce on 4 or 8 PEs). This is the pattern used in production `pe_rowwise_reduce` immediately followed by `pe_rowwise_broadcast` (see [attention.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/m13_attention_tp4dp8/attention.cpp)).

---

### 9.12 32-PE Full-Chip 2D Allreduce (Measured, Round6 2026-08-20)

The production attention kernel performs allreduce across all 32 PEs using a 2D recursive halving & doubling pattern over the 8×4 mesh. The sequence (exactly as used in [attention.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/m13_attention_tp4dp8/attention.cpp) lines 251-272) is:
1. **Phase 1 — X-reduce**: Each row independently reduces 4 PEs (col0→col3) via `pe_rowwise_reduce<Sum>`. Result stays at col3 PEs (8 PEs). No broadcast yet.
2. **Phase 2 — Y-reduce**: Column 3 reduces 8 PEs (row0→row7) via `pe_outer_cp_reduce<Sum>`. Result arrives at PE31 (global root). No broadcast yet.
3. **Phase 3 — Y-broadcast**: PE31 broadcasts the fully-reduced result north to all 7 col3 PEs via `pe_outer_cp_broadcast`.
4. **Phase 4 — X-broadcast**: Each col3 PE broadcasts west to its 3 row peers via `pe_rowwise_broadcast`. All 32 PEs now hold the allreduced result.

**Benchmark kernel**: [noc_rb46.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb46.cpp) (CH0 Sum), noc_rb47.cpp (CH1), noc_rb48.cpp (Max). All 32 PEs participate; timing measured at global root PE31; correctness verified at all 32 PEs. Methodology: 5 warmup + 20 measured iterations; t0 before Phase 1, t1 after Phase 4 fence.

**All 4 phases are sequential** (no pipelining between phases in current production code — each phase is separated by a pipeline barrier/completion fence).

**Measured data (CH0, Sum, post-warmup, deterministic zero jitter)**:

| Size (B) | min_cyc | Payload cyc | Effective BW (B/cyc) | Effective BW (GB/s) |
|----------|---------|-------------|---------------------|---------------------|
| 256–1024 | 1097 | 0–7 | 0.2–0.9 | 0.3–1.0 |
| 2048 | 1131 | 41 | 1.8 | 2.0 |
| 4096 | 1216 | 126 | 3.4 | 3.8 |
| 6144 | 1284 | 194 | 4.8 | 5.4 |
| 8192 | 1369 | 279 | 6.0 | 6.7 |
| 12288 | 1505 | 415 | 8.2 | 9.2 |
| 16384 | 1641 | 551 | 10.0 | 11.2 |
| 24576 | 1913 | 823 | 12.8 | 14.5 |
| 32768 | 2185 | 1095 | 15.0 | 16.9 |
| 49152 | 2729 | 1639 | 18.0 | 20.3 |
| 65536 | 3307 | 2217 | 19.8 | 22.3 |
| 98304 | 4429 | 3339 | 22.2 | 25.0 |
| 110000 | 4837 | 3747 | 22.7 | 25.6 |

**Linear model (post-warmup, ≥8 KB)**:

```
32-PE full-chip allreduce (Sum, CH0):  cycles = 1090 + 0.0340 × size_bytes     (R² ≈ 0.999)
                                       base = 1090 cyc (969 ns), slope = 0.0340 cyc/B → 29.4 B/cyc = 33.1 GB/s
```

**Phase cost decomposition**:

The 29.4 B/cyc asymptotic BW corresponds exactly to **link BW / 4 = 120/4 = 30 B/cyc**, confirming that the full-chip allreduce is bandwidth-limited by **four sequential full-payload traversals** of the critical path (X-forward, Y-forward, Y-bcast, X-bcast), with no ALU overhead:
- Phase 1 (X-reduce): payload east 3 hops, 8 rows in parallel → 120 B/cyc (one direction)
- Phase 2 (Y-reduce): payload south 7 hops, 1 chain serial → 120 B/cyc
- Phase 3 (Y-bcast): payload north, SWMR bcast → 120 B/cyc
- Phase 4 (X-bcast): payload west, 8 rows in parallel SWMR → 120 B/cyc
- **Efficiency**: 29.4/30.0 = **98%** of theoretical (residual 2% from NMC programming overhead between phases)

**Base latency decomposition** (1090 cyc for small messages):
- Phase 1 X-reduce (3-hop, 8 rows parallel): ~300 cyc (reduce + reverse ack, no bcast)
- Phase 2 Y-reduce (7-hop, 1 chain): ~460 cyc (reduce + reverse ack, no bcast)
- Phase 3 Y-bcast (7-hop, SWMR): ~160 cyc (spanning-tree setup + propagation)
- Phase 4 X-bcast (3-hop, 8 rows parallel): ~140 cyc (spanning-tree setup + propagation)
- Four inter-phase `ada_sync_fence_io()` barriers: ~30 cyc total

**Comparison with reduce+bcast sub-phases**:
- X 4-PE reduce+bcast (rb39): 335 cyc base, 59 B/cyc (2 passes)
- Y 8-PE reduce+bcast (rb42): 502 cyc base, 60 B/cyc (2 passes)
- Sum of independent X+Y reduce+bcast: 335+502 = 837 cyc (hypothetical, if bcasts were fused into reduce phases)
- Measured full allreduce (X-reduce + Y-reduce + Y-bcast + X-bcast): 1090 cyc
- Overhead of sequential 4-phase vs fused 2-phase: ~250 cyc (25%), from four fence barriers + separate spanning-tree setups for Y-bcast and X-bcast

**Key findings**:
- **Sum vs Max**: identical performance within 1% (1097 vs 1105 cyc at 512B; 1369 vs 1377 cyc at 8KB)
- **CH0 vs CH1**: identical cycle counts (exact match at all tested sizes)
- **Correctness**: v0=32 (sum of 1×32 PEs) verified on all 32 PEs; v0=32 (max of 1..32) verified for Max op.
- **Bandwidth bottleneck**: The full-chip allreduce is **NoC link BW limited**, not ALU or NMC limited. Each of the 4 phases runs at ~120 B/cyc effective throughput, consistent with unicast link bandwidth.
- **Algorithm note**: The 4-phase 2D allreduce achieves 120/4 = 30 B/cyc. A ring allreduce around the chip perimeter would be 31 hops and potentially slower; the 2D recursive halving & doubling minimizes the maximum hop count (3+7=10 sequential hops) and maximizes parallelism (8 rows in parallel for X phases, 1 chain for Y phase).
- **Allreduce aggregate throughput**: Since all 32 PEs receive the result, the effective aggregate bandwidth is 32 × 33 GB/s ≈ 1 TB/s of useful data delivered (though this counts SWMR fan-out duplication).

### 9.13 Dual-Channel NOC Operations (CH0+CH1 Parallel) (2026-08-23)

#### 9.13.1 Programming Model

Each PE's NMC contains two independent DMA channels (CH0 and CH1), each with its own upload engine, download engine, and NOC descriptor queue. The channels can post descriptors **back-to-back within the same phase** without inter-channel `ada_sync_fence_io()`. Only one `ada_sync_fence_io()` is needed per collective phase (after both channels' descriptors are posted) to ensure both channels' operations complete before the next phase.

**Correct programming pattern for dual-channel parallel allreduce** (noc_rb49.cpp):

```cpp
// Phase 1: X-reduce (CH0 + CH1 parallel, back-to-back)
send_with_sync<0>(half0, ...);
recv_with_sync<0>(half0, ..., ReduceOp::Sum);
send_with_sync<1>(half1, ...);
recv_with_sync<1>(half1, ..., ReduceOp::Sum);
ada_sync_fence_io();   // ONE fence after BOTH channels posted

// Phase 2: Y-reduce (same pattern)
send_with_sync<0>(half0, ...);
recv_with_sync<0>(half0, ..., ReduceOp::Sum);
send_with_sync<1>(half1, ...);
recv_with_sync<1>(half1, ..., ReduceOp::Sum);
ada_sync_fence_io();

// Phase 3: Y-bcast
pe_broadcast_sync<0>(half0, ...);
pe_broadcast_sync<1>(half1, ...);
ada_sync_fence_io();

// Phase 4: X-bcast
pe_broadcast_sync<0>(half0, ...);
pe_broadcast_sync<1>(half1, ...);
ada_sync_fence_io();
```

Key points:
1. **Buffer split**: Total payload `size_bytes` split into two halves (`half = size_bytes/2`); CH0 carries first half, CH1 carries second half. Buffers must be ≥64KB apart to avoid overlap at large sizes.
2. **Back-to-back posting**: Post ch0 descriptor(s), then ch1 descriptor(s) immediately, then ONE `ada_sync_fence_io()`. No fence between channels within a phase.
3. **ReduceOp::Sum works on both channels simultaneously**: In-router ALU processes each channel's flits independently; no crosstalk or serialization.
4. **Init loops**: Separate initialization loops per buffer (`for(i<size)buf[i]=1; for(i<half)buf1[i]=1;`) — combined loops may cause device state corruption.
5. **Device state reset**: After any coredump, reset device by running a known-good kernel (e.g., rb46) before subsequent tests; otherwise corrupted state causes unpredictable failures.

**Important correction to earlier finding**: An initial round of testing incorrectly concluded that parallel dual-channel reduce operations cause coredumps. This was due to a combination of buffer overlap at large sizes, combined init loops, and device state corruption from prior failures — not a hardware limitation. With proper buffer layout and device reset, fully parallel dual-channel reduce+broadcast works correctly at all tested sizes (512B–256KB), producing correct results (v0=32, v1=32 for Sum).

#### 9.13.2 Dual-Channel 32-PE Allreduce Measurements (noc_rb49.cpp, DUAL-FULL-PAR)

Same 4-phase 2D allreduce algorithm as §9.12, but with both CH0 and CH1 carrying half the payload in parallel within each phase. Buffer layout: buf0=LOCAL_SRAM+0x4000, buf1=LOCAL_SRAM+0x84000 (64KB separation), cyc_arr=DLCM+0x100000.

| Size (B) | half/ch (B) | min cyc | avg cyc | BW (B/cyc) | BW (GB/s) | Single-ch cyc | Single-ch BW | Speedup |
|----------|-------------|---------|---------|------------|-----------|---------------|--------------|---------|
| 512      | 256         | 1449    | 1449    | 0.35       | 0.40      | 1097          | 0.47         | 0.76×   |
| 1024     | 512         | 1449    | 1449    | 0.71       | 0.80      | 1097          | 0.93         | 0.76×   |
| 2048     | 1024        | 1449    | 1454    | 1.41       | 1.59      | 1129          | 1.81         | 0.78×   |
| 4096     | 2048        | 1500    | 1500    | 2.73       | 3.07      | 1199          | 3.42         | 0.80×   |
| 8192     | 4096        | 1585    | 1585    | 5.17       | 5.82      | 1367          | 5.99         | 0.86×   |
| 16384    | 8192        | 1721    | 1727    | 9.52       | 10.7      | 1641          | 9.98         | 0.95×   |
| 32768    | 16384       | 1993    | 1998    | 16.40      | 18.5      | 2185          | 15.00        | 1.09×   |
| 49152    | 24576       | 2282    | 2283    | 21.53      | 24.2      | 2741          | 17.93        | 1.20×   |
| 65536    | 32768       | 2554    | 2554    | 25.66      | 28.9      | 3302          | 19.85        | 1.29×   |
| 98304    | 49152       | 3115    | 3121    | 31.45      | 35.4      | 4429          | 22.20        | 1.42×   |
| 131072   | 65536       | 3687    | 3687    | 35.55      | 40.0      | —             | —            | —       |
| 196608   | 98304       | 4798    | 4798    | 40.98      | 46.1      | —             | —            | —       |
| 262144   | 131072      | 5920    | 5933    | 44.24      | 49.8      | —             | —            | —       |

**Linear model** (fit from large-size region 64KB–256KB):

```
cyc ≈ 1435 + size_bytes / 58
Asymptotic BW = 58 B/cyc = 65 GB/s
```

Comparison with single-channel model (cyc ≈ 1090 + N/29.4):
- **Base latency**: 1435 cyc vs 1090 cyc → 345 cyc overhead (≈32%) from programming 8 NMC descriptors instead of 4, plus doubled fence overhead.
- **Asymptotic BW**: 58 B/cyc vs 29.4 B/cyc → **1.97× ≈ 2× bandwidth**. 98% of theoretical 2× speedup.
- **Crossover point**: ~32 KB. Below 32 KB, the extra base latency dominates and dual-channel is slower (0.76–0.95×). Above 32 KB, the bandwidth advantage dominates, reaching 1.42× at 96 KB and trending toward 2× as size increases.

**Key findings**:
1. **Dual-channel allreduce achieves near-perfect 2× bandwidth** (98% efficiency). The NMC upload/download engines for CH0 and CH1 are fully independent and can inject/extract flits simultaneously.
2. **In-router ReduceOp::Sum operates independently per channel**. Both channels can have in-flight reduce operations concurrently with no serialization or crosstalk.
3. **Small-message penalty**: Dual-channel adds ~350 cyc fixed overhead (8 vs 4 NMC descriptor posts). For payloads <16 KB this overhead outweighs the bandwidth benefit.
4. **Independent channel datapaths**: PE NMC CH0+CH1 have independent DMA engines and NoC datapaths, achieving full 2× bandwidth for PE↔PE traffic. The <2× gain observed for PE→GM/DDR is due to GM/DDR receiver bottlenecks, not NoC link sharing.

#### 9.13.3 Dual-Channel PE↔PE Same-Direction Parallel Echo (noc_rb50.cpp)

Two PEs (PE0, PE1) send payload on BOTH CH0 and CH1 simultaneously in same direction (PE0→PE1 ch0+ch1 parallel, then PE1→PE0 ch0+ch1 parallel echo). Buffer layout: sb=0x101000, rb=0x102000, sb2=0x122000, rb2=0x142000.

| Size (B) | min cyc | Aggregate BW (B/cyc) | Aggregate BW (GB/s) |
|----------|---------|---------------------|---------------------|
| 512      | 376     | 2.7                 | 3.1                 |
| 1024     | 376     | 5.4                 | 6.1                 |
| 2048     | 393     | 10.4                | 11.7                |
| 4096     | 427     | 19.2                | 21.6                |
| 8192     | 512     | 32.0                | 36.0                |
| 16384    | 648     | 50.6                | 56.9                |
| 32768    | 920     | 71.2                | 80.1                |
| 65536    | 1481    | 88.5                | 99.6                |

This is an RTT echo pattern (PE0 sends both-ch → fence → PE0 recvs both-ch echo), so the reported BW uses `2*size/RTT` (aggregate both channels, one direction). Incremental one-way per-channel BW (32KB→64KB slope): ~117 B/cyc ≈ single-channel asymptotic (120 B/cyc), confirming independent channel operation. One-way aggregate dual-channel throughput trends toward ~240 B/cyc = 270 GB/s for large messages.

### 9.14 NMC DMA Engine Microarchitecture (noc_rb51.cpp, noc_rb52.cpp)

The NMC (Network Memory Controller) is the PE-local DMA engine that moves data between local SRAM and the NoC. Each PE has two independent NMC channels (CH0, CH1). This section characterizes the NMC's command processing throughput and descriptor buffering.

#### 9.14.1 Command Issue Rate (noc_rb51)

Measures how fast the scalar core can fire `send_with_sync<CH>` DMA descriptors back-to-back in a tight loop, without waiting for completion. Pattern: PE0 posts N sends to PE1 back-to-back, then posts 1 ACK-recv; PE1 posts N recvs back-to-back, then posts 1 ACK-send; single global fence per iteration. Post time = cycles between first `send_with_sync` call and Nth `send_with_sync` call return (scalar core time only, not data transfer).

**Key finding: command issue rate is ~57 cycles/descriptor, independent of message size (512B–16KB tested).**

| msg size | N=1 | N=2 | N=4 | N=8 | N=16 | N=32 | N=64 | N=128 |
|----------|-----|-----|-----|-----|------|------|------|-------|
| 512 B    | 61  | 117 | 229 | 453 | 901  | 2035 | 5041 | 11281 |
| 1 KB     | 61  | 117 | 229 | 453 | 901  | 2147 | —    | —     |
| 2 KB     | 61  | 117 | 229 | 453 | 901  | 2319 | —    | —     |
| 4 KB     | 61  | 117 | 229 | 453 | 901  | —    | —    | —     |
| 8 KB     | 61  | 117 | 229 | 453 | —    | —    | —    | —     |
| 16 KB    | 245 | 488 | 940 | 1861| —    | —    | —    | —     |

Post time (cyc) for posting N sends. Cyc/msg = post/N:
- For msg_size ≤ 8 KB: ~56–61 cyc/msg baseline at small N, flat across msg sizes.
- For msg_size = 16 KB: N=1 already at 245 cyc (includes data transfer start overlap), indicating the 16 KB transfer begins before the N=1 send call returns.
- N=1 baseline: 61 cyc for messages ≤8 KB (descriptor setup + channel arbitration).

This 57 cyc/descriptor rate is the fundamental NMC descriptor post overhead — it's independent of payload size because the scalar core is writing descriptors to the NMC command queue, not moving data. The NMC DMA engine transfers data asynchronously after the descriptor is queued.

For the event-driven model: **programming cost = 57 cyc per send/recv descriptor per channel** (scalar core stall time while posting). This is paid once per `send_with_sync`/`recv_with_sync` call regardless of transfer size.

#### 9.14.2 NMC Outstanding Depth (noc_rb52 + rb51 fine sweep)

Measures how many DMA descriptors the NMC can queue (absorb) per channel before backpressure stalls the scalar core. When N descriptors are posted back-to-back and the receiver is concurrently posting matching recvs (rb51 concurrent pattern), the post time per descriptor stays flat as long as the NMC descriptor queue has free entries. When the queue fills, the scalar core stalls waiting for the NMC to process descriptors and free entries.

Fine-grained N sweep at msg=512B (concurrent recv posting):

| N | post (cyc) | cyc/msg | Notes |
|---|-----------|---------|-------|
| 1 | 61        | 61.0    | baseline |
| 2 | 117       | 58.5    | |
| 4 | 229       | 57.2    | |
| 8 | 453       | 56.6    | |
| 10| 565       | 56.5    | |
| 12| 677       | 56.4    | |
| 14| 789       | 56.3    | |
| 16| 901       | 56.3    | |
| 18| 1013      | 56.2    | |
| 20| 1125      | 56.2    | |
| 22| 1237      | 56.2    | |
| 24| 1349      | 56.2    | |
| **28** | **1683** | **60.1** | **backpressure onset** |
| 32| 2029      | 63.4    | |
| 48| 3551      | 73.9    | |
| 64| 5139      | 80.2    | |

**Key finding: NMC outstanding descriptor depth ≈ 24–28 entries per channel.**

- For N ≤ 24: post time is perfectly linear (56.2 cyc/msg), indicating the NMC absorbs all descriptors without stalling the scalar core.
- At N = 28: cyc/msg jumps from 56.2 to 60.1 (+7%), first visible backpressure.
- At N = 32: 63.4 cyc/msg (+13%); at N = 48: 73.9 (+31%); at N = 64: 80.2 (+43%).
- The backpressure grows as N exceeds the queue depth, because the scalar core must wait for the NMC to drain earlier descriptors (data transfer completion) before accepting new ones.

For the event-driven model:
- **NMC descriptor queue depth = 24 entries per channel** (can be modeled as 24 outstanding DMA commands).
- Posting more than 24 descriptors back-to-back incurs additional stall cycles proportional to (N - 24) × data_drain_rate.
- The 24-entry depth allows ~24 × 512B = 12 KB of payload to be in-flight per channel without sender stall, which is more than enough to cover the round-trip credit latency (1 hop RTT ≈ 228 cyc at 120 B/cyc ≈ 27 KB, but credit flow control limits in-flight data to ~1-flit buffer per router port).

#### 9.14.3 Implications for Event-Driven Performance Model

| NMC Parameter | Value | Model Impact |
|---------------|-------|--------------|
| Descriptor post cost | 57 cyc/cmd | Programming latency per send/recv |
| Outstanding queue depth | ~24 entries/channel | Max in-flight DMA per channel before sender stalls |
| Channel independence | CH0, CH1 fully independent (§9.13) | Dual-channel doubles post throughput (57 cyc total for 2 descriptors if back-to-back across channels) |
| Post rate × BW product | 57 cyc × 120 B/cyc = 6840 B ≈ 6.8 KB | For messages smaller than ~7 KB, programming overhead dominates; for larger messages, BW dominates |

### 9.15 Link Contention and Arbitration (noc_rb53.cpp)

Measures NoC behavior when two flows share a common router output port. Pattern: PE0→PE2 (2-hop X, route 0→1→2) and PE1→PE3 (2-hop X, route 1→2→3) share the eastbound link from router 1 to router 2.

**9.15.1 Baseline (isolated flows, 4KB × 16 msgs)**

| Flow | Route | Hops | post (cyc) | cyc/msg | total (cyc) | BW (B/cyc) |
|------|-------|------|-----------|---------|-------------|------------|
| PE0→PE2 | east 2 hops | 2 | 818 | 51.1 | 2403 | 27.2 |
| PE1→PE3 | east 2 hops | 2 | 818 | 51.1 | 2404 | 27.2 |
| PE0→PE1 | east 1 hop | 1 | 720 | 45.0 | 2119 | 30.9 |
| PE0→PE31 | 3 east + 7 south | 10 | 1484 | 92.7 | 4546 | 14.4 |

**9.15.2 Contention (shared link 1→2)**

| msg size | Flow | Isolated BW | Contending BW | Aggregate BW | Post overhead |
|----------|------|-------------|---------------|--------------|---------------|
| 4 KB | PE0→PE2 | 27.2 | 27.2 | 54.4 | 0% |
| 4 KB | PE1→PE3 | 27.2 | 27.2 | 54.4 | 0% |
| 8 KB | PE0→PE2 | 44.5 | 44.7 | 89.2 | 0% |
| 8 KB | PE1→PE3 | 44.4 | 44.5 | 89.2 | 0% |
| 16 KB | PE0→PE2 | 64.1 | 55.5 | 110.5 | +21% post |
| 16 KB | PE1→PE3 | 64.2 | 55.0 | 110.5 | +22% post |

**Key findings**:
1. **Small messages (≤8 KB) show zero contention** because neither flow saturates the shared 120 B/cyc link. The NMC descriptor queue (24 entries) absorbs any transient backpressure.
2. **At 16 KB (saturating the link), fair round-robin arbitration splits bandwidth**: each flow achieves ~55 B/cyc (86% of isolated rate), aggregate ~110 B/cyc ≈ 92% of the 120 B/cyc link capacity.
3. **NMC post rate degrades under sustained backpressure**: cyc/msg increases from 83.6 to ~101 (+21%) because the descriptor queue fills when the network can't drain data fast enough, stalling the scalar core.
4. **Post times are identical for flows that DO share links but DON'T saturate them**, confirming the NMC queue depth provides significant buffering headroom.

**Implications for event-driven model**:
- Link sharing only causes measurable throughput degradation when the aggregate offered load exceeds ~90% of link capacity.
- For MoE traffic patterns (small messages ≤8 KB per expert transfer), link contention is negligible — the NoC is overprovisioned for this workload.
- Router arbitration policy: round-robin across competing input ports for a given output, producing fair bandwidth division.
- There is no evidence of QoS/priority for any packet type; ACK/sync packets are regular data flits and are arbitrated equally with bulk data.
- Contention backpressure propagates back to the NMC descriptor queue, increasing post latency once the queue fills (≥24 outstanding descriptors), but does NOT cause deadlock or unfair starvation.

### 9.16 Packetization, Per-Hop Latency, and Credit Return (noc_rb54.cpp)

#### 9.16.1 Minimum Transfer Unit and Serialization (noc_rb54)

Measures RTT for single messages from 64B to 32KB over 1 hop (PE0→PE1), 20 iterations per size.

| Size (B) | min RTT (cyc) | Δ vs 512B | Notes |
|----------|--------------|-----------|-------|
| 64 | 221 | 0 | minimum size, no penalty |
| 128 | 208 | −13 | within noise |
| 192 | 221 | 0 | |
| 256 | 221 | 0 | |
| 384 | 221 | 0 | |
| 512 | 221 | 0 | 1 flit |
| 1024 | 221 | 0 | 2 flits |
| 2048 | 221 | 0 | 4 flits |
| 4096 | 221 | 0 | 8 flits |
| 8192 | 255 | +34 | serialization visible |
| 16384 | 323 | +102 | |
| 32768 | 476 | +255 | |

**Key findings**:
- **Minimum transfer unit = 512B (1 flit)**. Sub-flit messages (64–384B) have identical latency to 512B, indicating zero-padding to a full flit.
- **Serialization is fully hidden for messages ≤4KB**: the NMC/NoC pipeline absorbs 8 flits of data without increasing end-to-end latency, because NMC SRAM read, header processing, and switch arbitration overlap with data transmission.
- **Asymptotic serialization rate**: Δ from 8KB→32KB = 221 cyc for 24KB ≈ **~110 B/cyc one-way** (close to 120 B/cyc link rate); Δ from 8KB→16KB = 68 cyc for 8KB = **~120 B/cyc one-way**.
- **The `BurstLen` API enum (BURST_LEN_0/1/3/7)** controls how many flits are sent as a continuous burst before releasing the output port for arbitration, not the phit/flit size. Beat (phit) = 128B, flit = 512B are fixed.

#### 9.16.2 Per-Hop Latency Decomposition (512B, single-message RTT)

Hop-count sweep at fixed 512B (minimum latency case), 20 iterations:

| Hops | Route | min RTT (cyc) | Δ per hop |
|------|-------|--------------|-----------|
| 1 (X) | PE0→PE1 | 221 | — |
| 2 (X) | PE0→PE2 | 238 | +17 |
| 3 (X) | PE0→PE3 | 255 | +17 |
| 1 (Y) | PE0→PE4 | 221 | — |
| 2 (Y) | PE0→PE8 | 238 | +17 |
| 4 (Y) | PE0→PE16 | 272 | +17 |
| 5 (Y) | PE0→PE20 | 289 | +17 |
| 6 (Y) | PE0→PE24 | 306 | +17 |
| 7 (Y) | PE0→PE28 | 323 | +17 |
| 4 (3X+1Y) | PE0→PE7 | 272 | +17 |
| 5 (3X+2Y) | PE0→PE11 | 289 | +17 |
| 7 (3X+4Y) | PE0→PE19 | 323 | +17 |
| 8 (3X+5Y) | PE0→PE23 | 340 | +17 |
| 9 (3X+6Y) | PE0→PE27 | 357 | +17 |
| 10 (3X+7Y) | PE0→PE31 | 374 | +17 |

**Linear model (512B RTT)**:
```
RTT_cyc = 204 + 17 × hops
```

- **RTT intercept = 204 cyc**: NMC programming overhead, independent of distance. Decomposition (approximate):
  - `send_with_sync` post: ~61 cyc (NMC descriptor enqueue, §9.14.1)
  - `recv_with_sync` post for ACK: ~61 cyc
  - NMC DMA startup + SRAM read setup + completion detection: ~82 cyc
- **Per-hop RTT cost = 17 cyc = 8.5 cyc one-way**. X and Y directions are identical; XY corner turn does not add extra latency.
- For large messages (32KB), per-hop RTT cost is also ~17 cyc (476 at hop=1, 612 at hop=10, Δ=136 over 9 hops ≈ 15 cyc/hop within pipeline fill noise).

**Estimated one-way router pipeline breakdown (8.5 cyc/hop)**:

| Stage | Cycles | Description |
|-------|--------|-------------|
| Route Compute (RC) | 1 | Output port lookup from dst PE |
| Switch Allocation (SA) | 2 | VC + crossbar arbitration |
| Switch Traversal (ST) | 1 | Crossbar + link buffer transfer |
| Link Traversal (LT) | 4.5 | Wire propagation + repeated pipeline registers across tile |
| **Total** | **8.5** | One-way per router hop |

Consistent with a 4-stage wormhole router (RC/SA/ST/LT) with multi-cycle link wires.

#### 9.16.3 Credit Return Latency (inferred)

Using the NMC outstanding depth measurement (§9.14.2) and per-hop latency:

- NMC TX queue: 24 descriptors (each ≥1 flit)
- Bandwidth-delay product (BDP) at 1 hop: 120 B/cyc × 17 cyc RTT ≈ 2 KB ≈ 4 flits
- BDP at 10 hops: 120 × 170 ≈ 20 KB ≈ 40 flits
- Router buffer per port: 1-flit input buffer × 5 ports; total buffering along a 10-hop path ≈ 50 flits
- The 24-entry NMC TX queue (24 flits minimum) covers BDP for RTT ≤ ~6 hops without sender stall

**Credit return latency ≈ 8.5 cyc/hop one-way** — credits return on reverse-direction links and do not incur extra processing beyond normal link traversal. This is consistent with measured BDP and NMC queue sizing.

**Model implications**:
- Per-hop one-way latency: 8.5 cyc; RTT: 17 cyc; X/Y symmetric
- Minimum packet: 512B (1 flit); sub-flit messages zero-padded
- Serialization: size/120 cyc one-way, hidden for ≤4KB
- Credit return: 8.5 cyc/hop; NMC queue absorbs BDP for RTT ≤6 hops
- Distances >6 hops: NMC TX queue (24 entries) limits outstanding transfers before backpressure

### 9.17 GM_WDMA DMA Engine Microarchitecture (noc_rb55.cpp)

The GM_WDMA (Global Memory Write DMA) engines handle PE→GM data transfers. There are 4 GM_WDMA nodes (one per column), each with two channels (CH0, CH1). This section characterizes the GM_WDMA scalar core's descriptor programming rate and buffering, analogous to §9.14 for PE NMC.

#### 9.17.1 GM_WDMA Descriptor Post Rate and Queue Depth (noc_rb55)

Measures how fast the GM_WDMA scalar core can post `gm_receive_sync<CH>` descriptors back-to-back, and the resulting PE→GM throughput for multi-message transfers. Pattern: PE0 posts N `send_with_sync` to GM_WDMA0 back-to-back; GM_WDMA0 posts N `gm_receive_sync` back-to-back; single global fence. Post time measured on both PE and GM sides.

**PE-side NMC post rate to GM (CH0, 512B messages):**

| N msgs | PE post (cyc) | PE cyc/msg | GM rx-post (cyc) | GM cyc/msg | Total (cyc) | BW (B/cyc) |
|--------|--------------|-----------|-----------------|-----------|-------------|------------|
| 1 | 55 | 55.0 | 41 | 41.0 | 292 | 1.7 |
| 2 | 104 | 52.0 | 92 | 46.0 | 596 | 1.7 |
| 4 | 202 | 50.5 | 142 | 35.5 | 1187 | 1.7 |
| 8 | 398 | 49.7 | 2186 | 273.2 | 2386 | 1.7 |
| 10 | 496 | 49.6 | 2733 | 273.3 | 2977 | 1.7 |
| 12 | 594 | 49.5 | 3276 | 273.0 | 3568 | 1.7 |
| 16 | 1466 | 91.6 | 4369 | 273.0 | 4763 | 1.7 |
| 32 | 6244 | 195.1 | 8742 | 273.1 | 9541 | 1.7 |

CH1 results are identical to CH0 (41 cyc N=1, 142 cyc N=4, 2185 cyc N=8 → 273.1 cyc/msg).

**Key findings**:
1. **GM_WDMA descriptor post cost (unblocked)**: ~40 cyc/descriptor for N≤4 (faster than PE NMC's 57 cyc), independent of message size (512B–4KB). N=1 baseline: 41 cyc.
2. **GM_WDMA outstanding queue depth ≈ 4 entries per channel** — much shallower than PE NMC's 24 entries. For N>4, the GM_WDMA scalar core stalls waiting for earlier receives to complete before accepting new descriptors.
3. **After queue fills, sustained receive rate ≈ 273 cyc/msg for 512B messages** (~300 cyc/msg for 4KB). This is the GM_WDMA's per-message completion rate, dominated by fixed per-message GM processing overhead (~230 cyc), not wire serialization (512B at 120 B/cyc = 4.3 cyc).
4. **For small messages (≤4KB), PE→GM throughput is GM-processing-limited at ~1.7 B/cyc (1.9 GB/s) per channel per PE**, far below the 120 B/cyc link capacity. This is the documented "GM serialization bottleneck for many small transfers."
5. **PE-side NMC starts back-pressuring at N≈16** (91.6 cyc/msg vs 50 baseline), because the GM_WDMA's slow drain rate causes NMC descriptors to pile up and fill the PE's 24-entry TX queue.
6. **Large-message asymptotic BW is unaffected**: single-message PE→GM reaches ~110 B/cyc (124 GB/s) at 512KB, confirming GM_WDMA can sustain full line rate for bulk transfers (post overhead amortized over large payload).

**Single-message PE→GM latency and BW (N=1):**

| Size (B) | Total (cyc) | BW (B/cyc) | BW (GB/s) |
|----------|------------|------------|-----------|
| 512 | 292 | 1.7 | 2.0 |
| 4 KB | 326 | 12.5 | 14.1 |
| 8 KB | 360 | 22.7 | 25.6 |
| 16 KB | 445 | 36.8 | 41.4 |
| 32 KB | 564 | 58.0 | 65.3 |
| 64 KB | 853 | 76.8 | 86.4 |
| 128 KB | 1397 | 93.8 | 105.5 |
| 256 KB | 2519 | 104.0 | 117.0 |
| 512 KB | 4763 | 110.0 | 123.8 |

Base latency (512B): 292 cyc one-way PE0→GM0 (≈65 cyc PE post + 41 cyc GM post + ~186 cyc wire+processing). Asymptotic slope: ~110 B/cyc = 124 GB/s (consistent with §9.9).

#### 9.17.2 Implications for Event-Driven Performance Model

| GM_WDMA Parameter | Value | Model Impact |
|-------------------|-------|--------------|
| Descriptor post cost (unblocked) | ~40 cyc/cmd | Faster than PE NMC (57 cyc) when queue has space |
| Outstanding queue depth | **~4 entries/channel** | Critical: only 4 in-flight receives before scalar core stalls; 6× shallower than PE NMC (24) |
| Small-message completion rate | ~273 cyc/msg (≤4KB) | Bottleneck for many small GM transfers; aggregate small-msg BW capped at ~1.9 GB/s/ch/PE |
| Large-message asymptotic BW | ~110 B/cyc (124 GB/s) | Bulk transfers reach full line rate; post overhead amortized |
| CH0/CH1 symmetry | Identical | Dual-channel to GM still receiver-limited (§9.9); cannot 2× small-msg throughput due to shared GM processing bottleneck |
| Post rate × BW product (small msg) | 273 cyc × 120 B/cyc ≈ 33 KB | Messages <33 KB are GM-processing-limited when posting individually; batched/large transfers amortize fixed cost |

**Critical model implication for MoE**: When many PEs upload small activation/expert-weight chunks to GM simultaneously, the GM_WDMA's 4-entry queue and ~273 cyc/msg processing rate become the dominant bottleneck. Batching small transfers into larger messages (≥32 KB) is essential to achieve good GM bandwidth utilization. This contrasts with PE↔PE transfers where NMC queue depth (24) and 57 cyc/msg post rate allow efficient fine-grained communication.

---

### 9.18 SRAM Port Contention (NMC + Matrix Core Overlap) (2026-08-24)

**Benchmark**: [noc_rb56.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb56.cpp) (mtx-first ordering), [noc_rb57.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb57.cpp) (NMC-first ordering for comparison). PE0 sends to PE1 via NMC CH0/CH1 while simultaneously running fp8e4m3 bmm tiles (BM=256, BN=256, BK=128) that read A from Local SRAM, B from Weight SRAM, and write C to Local SRAM.

**Buffer layout** (non-overlapping): bmm A @0x4000, B @Weight+0, C @0xC000, A_scale @Weight+0x20000; CH0 send @0x50000, recv @0x90000; CH1 send @0xD0000, recv @0x110000; Weight SRAM DMA buffers at 0x40000/0x80000/0xC0000/0x100000.

#### 9.18.1 Matrix Core bmm Characteristics

| Parameter | Value | Notes |
|-----------|-------|-------|
| Single bmm tile latency | **~2120 cyc** | 256×256×128 fp8e4m3 GEMM, measured across 2–16 tiles |
| bmm command queue depth | **~4 tiles** | Fire ≤4 bmm() calls back-to-back without stalling; 5th call blocks until a tile completes |
| bmm is non-blocking | Yes | Scalar core can post DMA descriptors after filling bmm queue |

#### 9.18.2 Optimal Command Ordering: mtx-first

Two ordering strategies compared:

1. **mtx-first (optimal)**: Fire 4 bmm tiles first (fill matrix queue) → post NMC DMA descriptors → continue bmm. This hides NMC descriptor post latency behind bmm startup.
2. **NMC-first (slower)**: Post all NMC descriptors first → then fire bmm. NMC queue (24 entries) fills faster than NMC can drain when competing with scalar posting, causing stalls.

At N=32, mtx=8 (both workloads substantial): mtx-first achieves 20388 cyc vs NMC-first 27327 cyc (**34% faster**).

#### 9.18.3 SRAM Port Contention Measurements (N=32, 32 KB msgs, mtx-first)

**Note on bandwidth regimes**: There are two relevant BW numbers for PE↔PE NMC transfers (see §9.3):
- **Bulk wire rate (asymptotic, ≥256KB contiguous)**: ~119 B/cyc = 134 GB/s per channel one-way (93% of 128 B/cyc PHY limit). Achieved when a single large message saturates the link.
- **Batched 32KB effective rate (N=32 back-to-back)**: ~87 B/cyc = 98 GB/s per channel. The ~27% gap comes from per-message overhead: ~57 cyc descriptor post + inter-message credit bubbles (32 msgs × ~60 cyc ≈ 1900 cyc of overhead).
- MoE tile-sized transfers (32-128KB) typically achieve 60-100 GB/s/ch depending on batch depth; the batched 32KB rate (87 B/cyc) is used in the contention tables below as the MoE-relevant baseline.

| Scenario | Mode | Streams | BW (B/cyc) | vs baseline |
|----------|------|---------|------------|-------------|
| CH0 Local TX only (32KB batched) | 1 | 1 | 87.4 | baseline (98 GB/s) |
| CH0 Weight TX only | 2 | 1 | 84.6 | -3.2% |
| Dual-ch Local TX (CH0+CH1) | 9 | 2 | 171.0 | 97.8% of 2×87.4 |
| CH0 Local TX + bmm (mtx=4, NMC-bound) | 5 | 1 | 84.8 | **-3.0%** (~2% contention) |
| CH0 Weight TX + bmm (mtx=4) | 6 | 1 | 82.9 | -2.0% |
| Dual-ch Local TX + bmm (mtx=4) | 13 | 2 | 168.3 | **-1.6%** (~2% contention) |
| CH0 Local TX + bmm (mtx=8, matrix-bound) | 5 | 1 | 51.4 | apparent; NMC fully hidden behind compute |
| 4-stream (CH0 L+W + CH1 L+W) + bmm (mtx=4) | 31 | 4 | ~157 | -7% worst case |

**Key findings**:
- **Local SRAM and Weight SRAM are independent banks** with separate ports. NMC on Weight SRAM does not contend with Matrix reading Local SRAM, and vice versa.
- **SRAM port contention is minimal (~2% overhead)** when using mtx-first ordering, even with dual-channel NMC + matrix running simultaneously.
- When matrix computation time exceeds NMC transfer time (matrix-bound regime), NMC is **fully hidden** behind compute — total time = matrix time.
- When NMC transfer time exceeds matrix time (NMC-bound regime), total time ≈ NMC time + ~2% contention overhead.

#### 9.18.4 Overlap Model

For MoE tile pipeline double-buffering, with mtx-first ordering:

```
total_cycles ≈ max(T_nmc_post + T_nmc_drain, T_matrix) + T_contention
T_contention ≈ 0.02 × T_nmc_drain   (NMC-bound regime)
T_contention ≈ 0                     (matrix-bound regime, NMC fully hidden)
```

---

### 9.19 Full-Duplex NMC and Compute Overlap (2026-08-24)

**Benchmark**: [noc_rb58.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb58.cpp). Extends rb56 with simultaneous TX+RX on same or different channels. PE0 sends to PE1 on some channel(s) while PE1 simultaneously sends to PE0 on same or different channel(s), optionally with matrix compute on PE0.

#### 9.19.1 Full-Duplex Capability

**Bulk wire rate (N=1 large message, slope 128KB→256KB)**:

| Configuration | per-dir BW (B/cyc) | Aggregate (B/cyc) | Aggregate (GB/s) |
|--------------|---------------------|---------------------|-------------------|
| Single-ch simplex | ~119 | 119 | **134** |
| Single-ch fulldup (TX+RX same ch) | ~117 | 234 | **263** |
| Dual-ch same-dir TX | ~234 (117/ch) | 234 | **263** |
| **Dual-ch fulldup (2ch × 2dir)** | ~234 TX + 234 RX | **468** | **527** |

**Batched 32KB rate (N=32 back-to-back × 32KB, MoE tile-sized)**:

| Configuration | Mode | TX BW (B/cyc) | RX BW (B/cyc) | Aggregate (B/cyc) | vs simplex sum |
|--------------|------|---------------|---------------|-------------------|----------------|
| Simplex TX CH0 | 1 | 87.4 | — | 87.4 | baseline |
| Simplex RX CH0 | 32 | — | 86.1 | 86.1 | RX symmetric |
| Same-ch fulldup CH0 (TX+RX same channel) | 33 | 86.0 | 86.0 | 172.0 | 98.6% of 87.4+86.1 |
| Cross-duplex (CH0 TX + CH1 RX) | 65 | 85.9 | 85.9 | 171.9 | 98.5% |
| Dual same-dir TX (CH0+CH1) | 9 | 171.0 | — | 171.0 | 97.8% of 2×87.4 |
| **Dual-channel fulldup (2ch × 2dir)** | **105** | **169.8** | **169.8** | **339.7** | **97.5% of 4×87.4** |

**Critical finding: each NMC channel is physically full-duplex.** The same channel can transmit and receive simultaneously at full line rate, with zero per-channel duplex penalty. Cross-channel and same-channel duplex perform identically, confirming:
1. Independent TX and RX datapaths within each channel (separate upload/download engines).
2. SRAM read (TX source) and write (RX destination) ports operate independently.
3. Two NoC meshes (NoC0/NoC1) are completely independent, each supporting full-duplex.
4. Single PE maximum aggregate bidirectional bandwidth: **~468 B/cyc ≈ 527 GB/s** (2 channels × 2 directions × ~117 B/cyc bulk wire rate); **~340 B/cyc ≈ 382 GB/s** for 32KB batched traffic (MoE tile granularity).

#### 9.19.2 Full-Duplex + Matrix Compute (mtx-first)

| Configuration | Mode | mtx=4 cyc (NMC-bound) | mtx=8 cyc (matrix-bound) | Overhead vs NMC-only |
|--------------|------|----------------------|------------------------|---------------------|
| Matrix only | 4 | 8567 | 16991 | — |
| TX CH0 only | 1 | 11993 | 11993 | baseline |
| Fulldup CH0 (TX+RX) | 33 | 12187 | 12187 | baseline |
| Dual fulldup (4 streams) | 105 | 12347 | 12347 | baseline |
| TX+mtx | 5 | 12320 | 20376 | +2.7% (NMC-bd), +0.4% (mtx-bd) |
| Fulldup CH0+mtx | 37 | 12603 | 20538 | +3.4% (NMC-bd), +0.9% (mtx-bd) |
| Cross-duplex+mtx | 69 | 12664 | 20560 | +3.8% (NMC-bd), +1.0% (mtx-bd) |
| **Dual fulldup+mtx** (max contention) | **109** | **13205** | **20815** | **+7.0%** (NMC-bd), +3.0% (mtx-bd) |

**Key findings**:
- Full-duplex NMC + matrix compute works efficiently. Even the worst case (4 simultaneous NMC streams + matrix: CH0 TX, CH1 TX, CH0 RX, CH1 RX, plus bmm reading A/B and writing C) adds only **7% overhead** in the NMC-bound regime.
- In the matrix-bound regime (compute > communication), overhead is 0–3%, meaning **NMC TX+RX are almost entirely hidden behind matrix compute**.
- Same-channel fulldup (CH0 bidirectional) performs identically to cross-duplex (CH0 TX + CH1 RX), confirming no extra penalty for using the same channel in both directions vs different channels.

#### 9.19.3 Implications for MoE Double-Buffering

The validated double-buffering pattern for MoE tile pipeline is:

```
// mtx-first + full-duplex pattern per tile:
fire 4 bmm tiles (current tile compute)        // fill matrix queue
post CH1 recv descriptors (prefetch next tile inputs)  // overlaps with compute
post CH0 send descriptors (send previous tile outputs) // overlaps with compute
continue bmm tiles                              // ongoing compute
fence_io                                        // ensure all DMA complete before next phase
```

This achieves:
- **Compute-communication overlap**: Both prefetch (RX) and send (TX) hide behind matrix compute.
- **Aggregate BW available**: Up to ~170 B/cyc TX + ~170 B/cyc RX = ~382 GB/s bidirectional per PE during compute.
- **Minimal SRAM port contention**: ≤7% overhead in worst case, ≤3% in typical matrix-bound MoE tiles.
- **Batching requirement**: GM/DDR transfers (if used) must still be batched to ≥32 KB to avoid GM_WDMA queue stalls.

---

### 9.20 Vector Engine (TPC) Bandwidth and Compute Profile (2026-08-24)

**Microbenchmark**: `noc_rb59.cpp` / `test_rb59.py` (fatbin, PE0-timed, all 32 PEs in sync fences).

**Vector engine architecture**: 4 TPC (Tensor Processing Core) units per PE. VMEM capacity = 990 entries x 128 B/entry = ~124 KB per PE. Each entry holds 32 fp32 elements. Pipeline: SRAM <-> vec_read/vec_write DMA <-> VMEM <-> vector_launch (vkernel) <-> VMEM.

**VMEM layout conventions**:
- VMEM addresses are in **byte offsets** from VMEM base: `(float*)0` = entry 0, `(float*)256` = entry 2, `(float*)512` = entry 4.
- Address field is 10 bits -> max entry offset = 1023. For 3-tensor ops (e.g., vadd: in0, in1, out), 3*N <= 990 -> N <= 256 entries (32 KB) per launch.

**DMA/TPC mode notes**: `ada_native_to_vmem` / `ada_vmem_to_native` helpers launch a single-TPC DMA channel. Using multiple TPCs for a single contiguous transfer does not increase bandwidth because the SRAM port is the bottleneck. Compute kernels via direct `vadd/vmul` intrinsics with absolute VMEM addresses also execute on a single effective TPC (others do redundant work without hardware tiling); multi-TPC throughput requires the streaming interface (`vecLaunchTensor` descriptors), not profiled here.

#### 9.20.1 vec_read (SRAM -> VMEM DMA)

| N (entries) | Size (B) | Cyc (Local) | B/cyc | Cyc (Weight) | B/cyc |
|-------------|----------|-------------|-------|--------------|-------|
| 8  | 1K  | 88  | 11.6 | 105 | 9.8  |
| 16 | 2K  | 105 | 19.5 | 105 | 19.5 |
| 32 | 4K  | 105 | 39.0 | 122 | 33.6 |
| 64 | 8K  | 122 | 67.1 | 139 | 58.9 |
| 128| 16K | 156 | 105.0| 156 | 105.0|
| 256| 32K | 224 | 146.3| 224 | 146.3|
| 512| 64K | 343 | 191.1| 360 | 182.0|
| 960| 120K| 564 | 217.9| 581 | 211.5|

- **Asymptotic bandwidth (Local)**: ~260 B/cyc = **292 GB/s**, fixed overhead ~88 cyc
- At MoE tile size (32 KB / N=256): **146 B/cyc = 165 GB/s** (56% of asymptotic)
- Weight SRAM: ~252 B/cyc asymptote, ~105 cyc overhead (~3% slower than Local)

#### 9.20.2 vec_write (VMEM -> SRAM DMA)

| N (entries) | Size (B) | Cyc | B/cyc |
|-------------|----------|-----|-------|
| 8  | 1K  | 105 | 9.8   |
| 16 | 2K  | 122 | 16.8  |
| 32 | 4K  | 139 | 29.5  |
| 64 | 8K  | 173 | 47.4  |
| 128| 16K | 224 | 73.1  |
| 256| 32K | 360 | 91.0  |
| 512| 64K | 615 | 106.6 |
| 960| 120K| 1057| 116.3 |

- **Asymptotic bandwidth**: ~129 B/cyc = **145 GB/s**, fixed overhead ~95 cyc
- Write is **~2x slower** than read (no burst doubling on write port)
- At 32 KB: **91 B/cyc = 102 GB/s** (71% of asymptotic)

#### 9.20.3 Element-wise Compute (vadd / vmul, single TPC, inputs in VMEM)

| N (entries) | FLOPs (fp32) | vadd cyc | vmul cyc | FLOPs/cyc |
|-------------|-------------|----------|----------|-----------|
| 8  | 256   | 213 | 213 | 1.2  |
| 16 | 512   | 213 | 213 | 2.4  |
| 32 | 1024  | 230 | 230 | 4.5  |
| 64 | 2048  | 281 | 298 | 7.0  |
| 128| 4096  | 417 | 417 | 9.8  |
| 256| 8192  | 672 | 672 | 12.2 |

- **Asymptotic throughput**: ~16 fp32 FLOPs/cyc per TPC = **18 GFLOPS per TPC** (512-bit SIMD)
- Fixed overhead: ~200 cyc (vkernel dispatch + pipeline fill)
- vadd and vmul have identical throughput
- 4-TPC theoretical aggregate (streaming API): ~64 FLOPs/cyc = 72 GFLOPS
- At 32 KB (N=256): **12.2 FLOPs/cyc** per TPC (76% of asymptotic)

#### 9.20.4 vec_read_gather (Indexed SRAM -> VMEM, BAUA unit)

| N (entries) | Size (B) | cyc (seq) | B/cyc | cyc (rand) | B/cyc |
|-------------|----------|-----------|-------|------------|-------|
| 8  | 1K  | 109 | 9.4  | 109 | 9.4  |
| 16 | 2K  | 126 | 16.3 | 126 | 16.3 |
| 32 | 4K  | 143 | 28.6 | 143 | 28.6 |
| 64 | 8K  | 163 | 50.3 | 177 | 46.3 |
| 128| 16K | 228 | 71.9 | 228 | 71.9 |
| 256| 32K | 364 | 89.9 | 364 | 89.9 |

- **Asymptotic bandwidth**: ~128 B/cyc = **144 GB/s** (identical to vec_write), ~100 cyc overhead
- **CRITICAL**: Random/strided indices perform **identically** to sequential (NO random-access penalty). BAUA throughput is limited by VMEM write port (1 entry/cyc), not SRAM access pattern.
- At 32 KB: **90 B/cyc = 101 GB/s** (70% of asymptotic)

**Gather programming notes**:
- Table entries are **64-bit absolute physical SRAM addresses**; `addrOffset` can be `(float*)0`.
- 64-bit table writes from scalar core MUST use DLCM alias (0x20xxxxxx); non-DLCM writes cause coredump (exit 134).
- Table address passed to DMA must be physical address (non-DLCM).

#### 9.20.5 MoE Kernel Design Implications

1. **Activation gather**: ~90 B/cyc (101 GB/s) at 32 KB, comparable to NMC bandwidth (~98 GB/s/ch at 32 KB). Gather is not the bottleneck when overlapped with NMC/Matrix.
2. **Element-wise ops (SiLU/gating)**: ~670 cyc (1-TPC) or ~170 cyc (4-TPC) for 32 KB, negligible vs BMM (~2120 cyc/tile) and NMC (~2000+ cyc).
3. **Pipeline stage costs** (32 KB batch): gather ~364 cyc -> compute ~670 cyc -> write ~360 cyc = ~1400 cyc total (1-TPC) or ~900 cyc (4-TPC).
4. **Port contention**: Vector, NMC, and Matrix DO share Local SRAM read/write ports. Three-way contention is significant (see §9.21); Vector+NMC two-way contention is ~25% wall slowdown (77% per-engine BW retention), not ≤7% as previously estimated from NMC+Matrix-only measurements.

---

### 9.21 Three-Way SRAM Port Contention (Vector + NMC + Matrix) (2026-08-25)

**Benchmark**: [noc_rb60.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_rb60.cpp). PE0 runs three workloads concurrently (mtx-first ordering): (1) fp8e4m3 bmm tiles reading A from Local SRAM, B from Weight SRAM, writing C to Local SRAM; (2) NMC fulldup echo with PE1 (32 KB batched messages, CH0 and/or CH1); (3) Vector rd+wr DMA (ada_native_to_vmem from Local SRAM → VMEM, then ada_vmem_to_native from VMEM → Local SRAM, 60 KB/iter = 480 VMEM entries). PE1 acts as echo partner. Workloads are balanced to ~6200-6450 cyc alone (mtx=3 tiles, n=16 msgs, vec=8 iters) so all engines finish near-simultaneously, enabling accurate per-engine BW measurement.

**Buffer layout** (non-overlapping, verified): bmm A @0x4000, C @0xC000; CH0 send @0x50000 (512KB), recv @0xD0000 (512KB); CH1 send @0x150000 (512KB), recv @0x1D0000 (512KB); Vector read src @0x250000 (64KB), write dst @0x270000 (64KB); bmm B+A_scale in Weight SRAM @0/0x20000.

#### 9.21.1 Isolated Baselines (32 KB batched, PE0↔PE1 single-hop)

| Workload | Cycles | NMC agg B/cyc | Vec agg B/cyc | GB/s |
|----------|--------|---------------|---------------|------|
| NMC CH0 fulldup (16×32KB TX+RX) | 6218 | 168.6 | — | 190 |
| NMC dual-ch fulldup (16×32KB × 2ch) | 6355 | 330.0 | — | 371 |
| Matrix bmm (3 tiles, 256×256×128 fp8) | 6454 | — | — | — |
| Vector rd+wr (8 iters × 60KB ea. dir) | 6126 | — | 160.4 | 180 |

Alone times are balanced within ±5% (6126-6454 cyc), enabling fair overlap measurement.

#### 9.21.2 Two-Way Contention Results (32 KB batched, mtx-first)

| Combination | Mode | Wall cyc | Wall slowdown | NMC agg B/cyc | NMC vs alone | Vec agg B/cyc | Vec vs alone |
|-------------|------|----------|---------------|---------------|-------------|---------------|-------------|
| M+N0 (Matrix + CH0 fulldup) | 5 | 6699 | **+3.8%** | 156.5 | 92.8% | — | — |
| M+N0+N1 (Matrix + dual-ch fulldup) | 7 | 7144 | **+10.7%** | 293.5 | 88.9% | — | — |
| M+V (Matrix + Vector rd+wr) | 12 | 7691 | **+19.2%** | — | — | 127.8 | 79.7% |
| N0+V (CH0 fulldup + Vector) | 9 | 8010 | **+28.8%** | 130.9 | 77.6% | 122.7 | 76.5% |
| N0+N1+V (dual-ch fulldup + Vector) | 11 | 8018 | **+26.2%** | 261.5 | 79.2% | 122.6 | 76.4% |

#### 9.21.3 Three-Way Contention Results (32 KB batched, mtx-first)

| Combination | Mode | Wall cyc | Wall slowdown | NMC agg B/cyc | NMC vs alone | Vec agg B/cyc | Vec vs alone | Aggregate vs ideal sum |
|-------------|------|----------|---------------|---------------|-------------|---------------|-------------|----------------------|
| M+N0+V (all three, single-ch) | 13 | 9274 | **+43.7%** | 113.0 | 67.0% | 105.9 | 66.0% | **66.6%** |
| M+N0+N1+V (all three, dual-ch) | 15 | 9570 | **+48.3%** | 219.1 | 66.4% | 102.7 | 64.0% | **65.6%** |

Ideal sum for three-way single-ch = N0_alone(168.6) + V_alone(160.4) = 329 B/cyc; achieved 218.9 B/cyc.

#### 9.21.4 Analysis and Microarchitectural Interpretation

The three engines **share Local SRAM read and write ports** (or a common interconnect to SRAM banks), causing measurable throughput degradation when running concurrently. The dominant bottleneck varies by combination:

1. **M+N (Matrix + NMC) — minimal contention (4-11%)**: Matrix consumes very little SRAM read bandwidth (~15 B/cyc for A matrix, with B fetched from independent Weight SRAM) but writes C at ~122 B/cyc. NMC reads ~84 B/cyc (TX) and writes ~84 B/cyc (RX). The write port (Matrix C + NMC RX) is the main contention point, but Matrix C writes are bursty (one 256KB tile every ~2120 cyc), allowing NMC to fill gaps. This confirms §9.18 findings of ≤7% wall overhead in NMC-bound regimes.

2. **M+V (Matrix + Vector) — moderate contention (19%)**: Vector vec_write is a continuous SRAM write stream at ~80-129 B/cyc average, which directly competes with Matrix C writes (~122 B/cyc) on the write port. The write port is the bottleneck; vec_read (~80 B/cyc average) barely competes with Matrix A reads (~15 B/cyc) on the read port. Vector throughput drops to ~80% of alone.

3. **N+V (NMC + Vector) — significant contention (26-29%)**: Both engines access both read and write ports continuously. NMC TX (~84 B/cyc read) competes with vec_read (~80 B/cyc read); NMC RX (~84 B/cyc write) competes with vec_write (~80 B/cyc write). Both ports are shared roughly equally, giving ~77% throughput retention per engine — consistent with fair round-robin arbitration on SRAM ports.

4. **M+N+V (three-way) — severe contention (44-48%)**: All three engines compete for both SRAM ports simultaneously. NMC aggregate BW drops to ~66% of alone, Vec BW drops to ~65% of alone, and wall time increases by 44-48% over the longest alone baseline. Aggregate SRAM throughput is ~66% of the ideal zero-contention sum. Dual-channel NMC makes contention slightly worse (3-5% additional) due to doubled NMC SRAM traffic.

5. **Dual-channel vs single-ch NMC**: Adding a second NMC channel adds marginally more contention (~3-5% additional wall slowdown) because the second channel's TX/RX doubles the SRAM read/write demand from NMC. However, the incremental penalty is sub-linear because the port bandwidth isn't fully saturated at single-ch NMC+V loads.

#### 9.21.5 MoE Kernel Design Implications

This finding **revises** the earlier §9.20.5 assumption that Vector uses independent SRAM ports with ≤7% overhead. The corrected model:

1. **Avoid three-way overlap**: Schedule NMC transfers and Vector compute/gather to NOT run simultaneously with Matrix bmm writes, or accept ~44-48% wall slowdown.
2. **Two-way overlaps are cheaper**: NMC+Matrix (4-11%) and Matrix+Vector (19%) are viable; NMC+Vector (26-29%) is acceptable but not free.
3. **Write port is the critical resource**: Matrix C writes (256 KB per tile at ~122 B/cyc) dominate SRAM write traffic. Overlapping vec_write or NMC RX with active bmm tiles will be write-port-limited.
4. **Activation pipeline scheduling**: For MoE token dispatch/gather + compute + reduce pipeline, the optimal double-buffering strategy should:
   - Overlap NMC dispatch (TX) with current-tile bmm (cheap: ~4% overhead)
   - Serialize Vector activation post-processing (gather+SiLU+write) AFTER bmm completes, or overlap only NMC TX (not RX) with vec_write to avoid write-port saturation
   - Avoid overlapping NMC fulldup (both TX+RX) + Vector rd+wr + Matrix bmm simultaneously (48% slowdown)
5. **Overlap model** (32 KB batched, mtx-first, rough):
   ```
   T_total ≈ max(T_nmc, T_mtx, T_vec) × (1 + α)
   α ≈ 0.04  for M+N
   α ≈ 0.19  for M+V
   α ≈ 0.28  for N+V
   α ≈ 0.46  for M+N+V (three-way)
   ```
   Add 0.03 for dual-ch NMC vs single-ch in all cases.

#### 9.21.6 Revised SRAM Port Model

Based on contention data, the Local SRAM likely provides:
- **Read port**: ~260-290 B/cyc (2048-2304 bits) sustainable aggregate; vec_read achieves 260 B/cyc alone, and combined NMC TX + vec_read + Matrix A achieves ~120 B/cyc (below capacity due to write-port backpressure and burst patterns)
- **Write port**: ~190-200 B/cyc (1536-1600 bits) sustainable aggregate; combined NMC RX + vec_write + Matrix C writes achieve ~194 B/cyc in three-way tests (near saturation)
- Weight SRAM is fully independent (separate physical memory), with its own ports; Matrix B reads from Weight SRAM do not contend with anything on Local SRAM
- Arbitration appears to be round-robin (fair) across engines when ports are contended

---

## 10. Remaining TBD Parameters (What We Still Lack)

Parameters marked **[TBD]** in this document that require additional measurement or vendor disclosure for a high-fidelity (<20% error) event-driven model. Parameters marked ~~strikethrough~~ are **DONE**.

### 10.1 High Priority (Directly Affects End-to-End MoE Kernel Timing)

| # | Parameter | Status & Notes |
|---|-----------|----------------|
| 1 | ~~**PE↔GM bandwidth**~~ | **DONE (§9.9)** |
| 2 | ~~**PE↔DDR bandwidth**~~ | **DONE (§9.10)** |
| 3 | ~~**Multicast/broadcast fanout latency**~~ | **DONE (§9.11)** |
| 4 | ~~**In-router reduction overhead**~~ | **DONE (§9.11)** |
| 5 | ~~**Incast/outcast/full-duplex/contention**~~ | **DONE (§9.13-9.19)** |
| 6 | ~~**SRAM port structure & NMC+Matrix contention**~~ | **DONE (§9.18-9.19)** |
| 7 | ~~**NMC command issue rate & outstanding depth**~~ | **DONE (§9.14)** |
| 8 | ~~**GM_WDMA dispatch rate & queue depth**~~ | **DONE (§9.17)** |
| 9 | ~~**Link contention & arbitration**~~ | **DONE (§9.15)** |
| 10 | ~~**Packetization/per-hop latency/credit return**~~ | **DONE (§9.16)** |
| 11 | ~~**Vector engine load/store/gather/compute BW**~~ | **DONE (§9.20)** |
| 12 | ~~**Vector + NMC + Matrix three-way SRAM contention**~~ | **DONE (§9.21)**: NMC+M 4-11% wall, M+V 19%, N+V 26-29%, three-way 44-48%. SRAM write port is the critical bottleneck. |
| 13 | **Matrix bmm with small M latency profile** (M=8/16/32/64/128/256) | MoE expert GEMMs have small M dimensions; single-tile latency ~2120 cyc at M=256, but smaller M may have different efficiency curves. Critical for MFU estimation. |
| 14 | **Vector scatter (VMEM→SRAM indexed write)** BAUA performance | vec_write_scatter bandwidth for MoE combine/scatter phase; symmetric to gather expected but unmeasured. |

### 10.2 Medium Priority (Affects Contention Accuracy & Non-Critical Paths)

| # | Parameter | Notes |
|---|-----------|-------|
| 15 | ~~**Sync packet priority / QoS**~~ | **DONE (§9.15)**: No priority, RR arbitration. |
| 16 | ~~**Credit return latency**~~ | **DONE (§9.16)**: 8.5 cyc/hop. |
| 17 | ~~**Burst length / beat size**~~ | **DONE (§9.16)**: 128B phit, 512B flit. |
| 18 | ~~**Per-hop latency breakdown**~~ | **DONE (§9.16)**: 8.5 cyc/hop one-way. |
| 19 | **GM internal DMA↔DMA** (WDMA→RDMA without PE) | `wdma_signal_rdma`/`rdma_wait_wdma` path for MoE pipeline data movement within GM. |
| 20 | **DDR N-way incast/outcast** (N PEs↔1 DDR) | Expected ~100-126 GB/s cap (DDR_RDMA MC-limited); 4-disjoint-DDR aggregate BW. |
| 21 | **Vector element-wise ops** (SiLU/residual/cast/reduction) | Throughput of vexp, vdiv, vcopy, vreduce in vkernel pipeline; needed for activation stage timing. |
| 22 | **BAUA / mask / transpose throughput impact** | Used in attention/conv but not MoE critical path. |
| 23 | **Vector multi-TPC streaming throughput** (vecLaunchTensor API) | 4-TPC partitioned DMA/compute to achieve ~4× single-TPC throughput for activation pipelines. |
| 24 | **Gather/scatter table-walk overhead** (NMC-side) | Per-entry cycle cost for NMC gather/scatter (different from Vector gather §9.20). |

### 10.3 Lower Priority (Corner Cases / Future Features)

| # | Parameter | Notes |
|---|-----------|-------|
| 25 | ~~Single-side vs dual-side overhead~~ | **DONE**: <3% difference. |
| 26 | ~~DDR↔ACI CDC penalty~~ | **DONE**: <10 cyc. |
| 27 | ~~Atomic GM writeSum throughput~~ | **DONE**: ~120 GB/s. |
| 28 | MMU page size / translation latency | If MMU is used in practice. |
| 29 | AdaLink cross-chip NoC extension latency | Multi-card (2/4/8-card) modeling. |
| 30 | FIFO credit depths per hw_id/logic_id | Detailed flow-control modeling. |
| 31 | Reduction tree concurrency (max simultaneous trees) | Overlapping collectives. |
| 32 | maxOst default value | Max outstanding single-side transactions. |

### 10.4 Recommended Next Benchmark Sequence

To complete the MoE kernel performance model:

1. ~~**Vector + NMC + Matrix three-way SRAM contention** (#12)~~: **DONE (§9.21)**
2. **Matrix bmm small-M profiling** (#13): Sweep M=8/16/32/64/128/256 for fp8e4m3 bmm tiles to get latency vs M curves.
3. **Vector scatter + element-wise ops** (#14, #21): vec_write_scatter bandwidth; SiLU (vexp+vadd+vdiv+vmul) pipeline throughput on real activation shapes.
4. **GM internal DMA↔DMA** (#19): wdma_signal_rdma path for direct GM→GM data movement bypassing PEs.
5. **DDR N-way incast/outcast** (#20): Confirm DDR endpoint caps under multi-PE load.

**Current calibration status (updated 2026-08-25)**: All core NoC transport parameters are calibrated:
- **Latency**: RTT = 204 + 17×hops cyc; 8.5 cyc/hop one-way; 57 cyc/NMC-descriptor; 40 cyc/GM-descriptor; ~100 cyc fixed overhead per vector op.
- **Bandwidth**: PE↔PE ~134 GB/s/ch/dir bulk, ~98 GB/s @32KB; dual-ch same-dir ~270 GB/s; dual-ch fulldup ~527 GB/s bulk / ~382 GB/s @32KB; GM/DDR caps ~124 GB/s aggregate (receiver-limited); Vector read ~292 GB/s, write ~145 GB/s, gather ~144 GB/s, compute ~18 GFLOPS/TPC; Matrix bmm ~2120 cyc/256×256×128 tile.
- **Contention**: NMC+Matrix 4-11% wall (§9.18); NMC+Vector 26-29% wall; Matrix+Vector 19% wall; **three-way 44-48% wall** (§9.21); RR arbitration with 92% link utilization under saturation; zero contention for ≤8 KB messages. SRAM write port is the critical bottleneck when Matrix C writes overlap with vec_write/NMC RX.
- **Programming model**: Dual NoC (NoC0/NoC1) fully independent; per-channel full-duplex; DLCM alias required for 64-bit scalar writes; all PEs must participate in global fences; mtx-first command ordering optimal.

---

## Appendix A: Enums and Constants Quick Reference

### A.1 Transfer Enums

```cpp
enum TransType       { SINGLECAST = 0, FIXPATH = 1, MULTICAST = 2, BROADCAST = 3 };
enum AdaNocReduceType { NoCalc = 0, Add = 1, Max = 2 };
enum BurstLenMode    { BURST_LEN_DEFAULT = -1, BURST_LEN_0 = 0, BURST_LEN_1, BURST_LEN_3, BURST_LEN_7 };
enum BroadCastMode   { Normal = 0, Master, Slave };
enum ReorderMode     { NoReorder = 0, BmmReorder, Con2dReorder, BisaReorder, Con3dReorder };
enum WorkModeSingle  { BroadCastSlv = 0, SingleDDR, SingleGM };
enum FifoCheckType   { TYPE_DEFAULT = -1, WRITE_FULL = 0, READ_EMPTY = 1, UPDATE = 2 };
```

### A.2 Node Type Helpers (Intrinsics)

```cpp
int  node_type();             // Current NodeType enum value
int  node_id();               // Type-local ID (0-based within type)
int  global_node_id();        // Global physical ID (0-47 on single chip)
int  physical_node_id(int type, int id);  // (type, local_id) -> global ID
int  pe_nums();               // 32
int  gm_rdma_nums();          // 4
int  gm_wdma_nums();          // 4
int  ddr_rdma_nums();         // 4
int  ddr_wdma_nums();         // 4
bool is_pe_node();
bool is_gm_rdma_node();
bool is_gm_wdma_node();
bool is_ddr_rdma_node();
bool is_ddr_wdma_node();
```

### A.3 Route Computation

```cpp
int get_route_id(int type, int id);       // (NodeType, local_id) -> router ID
int get_data_noc_local_id(int type, int channel, int side,
                          bool isAIUDownload, int adalinkId);
int get_route_pos(int nodeId);            // Encoded position
int get_local_id(int type, int id = 0);   // DataNocLocalId for local end
```

### A.4 Memory Base Addresses

```cpp
void* local_sram_base_addr();    // 0x10_0000
int   local_sram_size();         // 3 MB (3 * 1024 * 1024)
void* weight_sram_base_addr();   // 0x40_0000
int   weight_sram_size();        // 16 MB
int   vector_core_num();         // 4 (TPC clusters per PE vector engine)
int   vector_entry_size_byte();  // 128 (bytes per VMEM entry)
int   vmem_entries();            // 128 (vector register entries)
```

### A.5 DMA Entry Encoding (for Outer Sync)

```cpp
DMA_CHANNEL_0 = 1
DMA_CHANNEL_1 = 2
AIU_DOWNLOAD  = 3
```

---

## Appendix B: Phit Width & NoC Clock Derivation (Derived)

This appendix documents the mathematical reasoning used to infer phit width (physical link width) and NoC clock frequency from measured silicon data, given the mentor-confirmed ground truth (★):
- Flit size = 512 B★
- Packet format: single-flit = header+payload inline; multi-flit = 1 header flit + P payload flits + 1 tail flit★
- 1 VC per port, 1-flit input buffer, credit-based flow control★
- XY deterministic routing★
- Wormhole cut-through switching (standard for this class of NoC)

### B.1 Key Measured Data Points

All measurements use PE-side `ada_get_cycle()` @ 1125 MHz ACI clock.

| Measurement | Value | Source |
|-------------|-------|--------|
| Per-hop zero-load RTT slope | 17 cycles/hop | §9.1 linear fit (R²=1.0) |
| Per-hop one-way latency | 8.5 cycles | 17/2 |
| Asymptotic injection bandwidth (1-hop, large msg) | 120 B/cyc = 135 GB/s | §9.3 slope: 32768B/274cyc |
| Asymptotic injection bandwidth (7-hop, large msg) | 119.6 B/cyc | §9.3 |
| Asymptotic injection bandwidth (10-hop, large msg) | 116.6 B/cyc | §9.3 |
| Single-flit serialization (document) | ~4.3 cycles | Project memory / prior analysis |

### B.2 Phit Width Hypothesis Testing

A flit is the flow-control unit (512 B★). A phit is the physical unit transferred per cycle across a link (phit width = W bits). Number of phits per flit: N_phits = 512 / W_bytes.

For wormhole cut-through, the one-way per-hop latency for a complete flit decomposes as:
```
T_hop = T_router_pipeline + (N_phits - 1)    [in cycles]
```
where:
- `T_router_pipeline` = head-phit latency through one router (route-compute + switch-arbitration + switch-traversal + link-traversal)
- After the head phit establishes the path, remaining (N_phits - 1) phits follow at 1 phit/cycle in a pipeline
- Additional credit/synchronization overhead ~1 cycle

Constraints:
1. `T_router_pipeline` must be reasonable for 1125 MHz: 3–7 cycles (typical 4–5 stage router pipeline: RC/VA/SA/ST/LT)
2. Steady-state throughput must match measured ~120 B/cyc
3. `N_phits` must be power-of-2 (standard for NoC serialization)

**Test for W = 512 bits (64 B/cyc):**
- N_phits = 512/64 = 8
- Required T_router = 8.5 − (8−1) − 1 = 0.5 cycles → **IMPOSSIBLE** at 1125 MHz (combinational path would need to be <1 cycle)
- Throughput cap: 64 B/cyc = 72 GB/s, far below measured 135 GB/s
- **REJECTED**

**Test for W = 1024 bits (128 B/cyc):**
- N_phits = 512/128 = 4
- Required T_router = 8.5 − (4−1) − 1 = 4.5 cycles
- Router pipeline breakdown: RC(1) + SA(2) + ST(1) + LT(0.5) ≈ 4.5 cycles → **CONSISTENT** with a 4–5 stage wormhole router at 1125 MHz
- Wire rate: 128 B/cyc = 144 GB/s; measured ~120 B/cyc = 94% efficiency
- Efficiency loss: ~6% from inter-flit bubbles (arbitration gaps, credit return wait) + ~3 B/flit CRC overhead (508/512 ≈ 0.6%)
- **ACCEPTED as self-consistent model**

**Test for W = 2048 bits (256 B/cyc):**
- N_phits = 512/256 = 2
- Required T_router = 8.5 − (2−1) − 1 = 6.5 cycles → possible but deep pipeline
- Wire rate: 256 B/cyc = 288 GB/s; measured 120 B/cyc would imply 47% efficiency → unrealistically low (credit bubble limited to ~94% typically)
- **REJECTED** (no evidence for over-provisioned links)

### B.3 NoC Clock Domain Test

Hypothesis: NoC runs at 2× PE frequency (2250 MHz) with 512-bit phits.
- PE cycle = 2 NoC cycles
- Per-hop = 8.5 PE cyc = 17 NoC cycles
- N_phits = 512/64 = 8 NoC-phits
- Required T_router = 17 − 7 = 10 NoC cycles = 5 PE cycles → a very deep 10-stage pipeline at 2.25 GHz
- Would require CDC (clock-domain crossing) FIFOs between PE/GM (ACI 1125 MHz) and NoC (2250 MHz), adding latency and area
- No evidence of CDC in register documentation or performance counters
- **REJECTED** as unnecessarily complex; the 1024-bit @ 1125 MHz model is simpler and self-consistent.

### B.4 Per-Flit Overhead Calculation

Given phit = 128 B and wire rate = 128 B/cyc:
- Ideal flit serialization: 4 cycles (4 phits)
- Measured effective serialization: ~4.3 cycles (from document: 512B/4.3cyc ≈ 119 B/cyc)
- Inter-flit bubble: ~0.3 cycles per flit (from arbitration/credit)
- Payload per body flit: ~508 B (128B - 12B first phit header for H flit; 128B - 4B CRC for body/tail flits)
- Effective steady-state: 508B / (4 + 0.3)cyc = 118 B/cyc ≈ 120 B/cyc (matches measurement)

### B.5 Header Size Estimate

The software-visible `build_info1` routing word is 32 bits (see §3.1). The full on-wire header must also contain:

| Field | Est. bits |
|-------|-----------|
| Dest route_id (6b) + local_id (5b) | 11 |
| Src route_id (6b) + local_id (5b) (for ACK return) | 11 |
| TransType (2b) + pkt_type H/B/T (2b) | 4 |
| TaskID/sync tag | 8 |
| Payload length (flit count) | 8 |
| Reduce opType + insSyncMode | 5 |
| BurstLen/VC/QoS | 5 |
| CRC-16 or CRC-32 | 16–32 |
| MMU/PID/barrier flags | 8 |
| **Total** | **76–92 bits ≈ 10–12 bytes** |

Header fits entirely in first phit (128B), leaving ~116B for payload in the head flit.

### B.6 Conclusion (Derived Parameters)

| Derived Parameter | Value | Confidence |
|-------------------|-------|------------|
| Physical link (phit) width | 1024 bits = 128 B/cyc | High (only value consistent with both latency and BW) |
| NoC clock | 1125 MHz (ACI domain, same as PE/GM) | High (simplest consistent model) |
| Phits per flit | 4 | High |
| Router pipeline depth | ~4.5 cycles (RC:1, SA:2, ST:1, LT:0.5) | Medium (not directly measurable, but consistent with 8.5 cyc/hop) |
| On-wire header size | ~10–12 bytes in first phit | Medium (estimated from field list) |
| Inter-flit bubble | ~0.3 cycles/flit (~6% overhead) | High (from 4.0 vs 4.3 cyc serialization) |
| Link wire efficiency | ~94% (large messages) | High (120/128 B/cyc) |

These derived values are used in §2.4 and §9.7. They would be confirmed if hardware documentation provides the exact microarchitecture specification.

---

## Appendix C: Debug Lessons Learned (Caveats & Pitfalls)

### C.1 Scalar SRAM Write Coredump in GM DMA Context

**Symptom**: Any kernel referencing GM DMA functions (`gm_to_sram`, `sram_to_gm`, `send_with_sync` to GM_WDMA, etc.) that also performs scalar core writes to PE local SRAM causes immediate device coredump.

**Root cause**: Appears to be a compiler/MMU-configuration artifact. When GM DMA descriptors are present in the PE binary, the scalar core's SRAM write path conflicts with the DMA engine's MMU/SMU configuration.

**Workaround**: Use GM→SRAM DMA download (`gm_to_sram<CH>(sram_view, gm_view) + sync_download_self<CH>() + ada_sync_fence_io()/fence_calc()`) to pre-initialize SRAM data, or use vector engine writes. Do NOT use scalar `for`-loops to write SRAM in GM DMA kernels.

**Minimal proof**: [noc_gm_ul_nosw.cpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/noc_microbench/noc_gm_ul_nosw.cpp) — no scalar SRAM writes, sends uninitialized data, works correctly; adding any scalar SRAM write causes crash.

### C.2 Split-Kernel Fatbin Programming Model for GM DMA

GM_WDMA nodes (and other DMA types) are **not** active in `.out` / `launchKernel()` mode. To send/receive from GM:
1. Compile with `--offload-device-only -adas-const-node-type -adas-split-kernel-new` to produce `.adafb` fatbin
2. Launch with `launchFatbinKernel()` using a 3-unit config: `adaUnitType=PE(32) + GLPD_LD(4) + GSRM_ST(4)`
3. Both PE and GM_WDMA scalar cores run code from the same binary; dispatch via `is_pe_node()` / `is_gm_wdma_node()`
4. In split-kernel mode, `node_id()` for GM_WDMA returns logical lane ID (0-3), not physical ID (36-39)

### C.3 Compilation Notes

- **-O0 crashes the compiler**: "ada built-in function must has constant data_type UNREACHABLE". Use -O3.
- **No floating-point on scalar core**: `__floatsisf`, `__floatundidf` intrinsics are undefined. All bandwidth calculations must be done on the Python host side using integer arithmetic.
- **Iteration count must match**: Both PE and GM_WDMA sides must execute matching numbers of send/receive calls; mismatch causes deadlock (SIGTERM/exitcode -6).

### C.4 Node Type and ID Mapping (Split-Kernel Mode)

In fatbin split-kernel mode:
- `is_pe_node()` → true for PE cores; `node_id()` returns PE ID (0-31)
- `is_gm_wdma_node()` → true for GM_WDMA cores; `node_id()` returns logical lane (0-3), NOT physical global ID (36-39)
- PE→GM column mapping for MoE: `col_id = pe_id % 4` maps PE to one of 4 GM_WDMA lanes; each column has 8 PEs at rows 0..7 (pe_id = col_id + 4*row)

### C.5 Scalar SRAM Read Alias for NOC-Populated Data

**Symptom**: After NOC receive operations (recv_with_sync, pe_broadcast_sync, in-router reduce), reading PE local SRAM via the physical address `0x100000 + offset` from the scalar core causes device coredump, even when fence_io/fence_calc have been called.

**Root cause**: The scalar core must use the DLCM-mapped alias address to access SRAM after NMC/DMA writes have populated the buffer. The physical address works for scalar writes and for NMC view construction, but post-DMA reads require the alias.

**Workaround**: Always use `DLCM_BASE + LOCAL_SRAM_ADDR + offset = 0x20000000 + 0x100000 + offset` (i.e., `0x20100000 + offset`) when reading NOC-populated data from the scalar core. Example:
```cpp
int8_t *buf_noc = (int8_t*)(0x100000 + 0x4000);       // for NOC views
int8_t *buf_scalar = (int8_t*)(0x20000000 + 0x100000 + 0x4000); // for scalar reads after fence
```
This is consistent with how production code accesses SRAM via `dlcm_sram` pointers (e.g., `dlcm_sram->neg_infinity[0]` in [mask.hpp](file:///opt/tiger/adas_lp_kernels/kernels/cpp/m13_attention_tp4dp8/mask.hpp)).

### C.6 printf Serialization Requirement for Microbenchmarks

**Symptom**: Benchmark kernels without per-step printf crash with simultaneous 32-PE NOC programming burst (coredump), even though NOC operations are correctly programmed.

**Root cause**: In microbenchmarks with no computation between NOC calls, all 32 PEs hit NOC programming in perfect lockstep after `ada_sync_fence_io()`, overwhelming the NOC control plane. Production kernels are safe because real computation between NOC calls naturally desynchronizes PEs.

**Workaround**: Insert `printf("[PE%d] step\n", pe_id);` (with `\n` to flush UART) before each NOC call. The shared UART naturally serializes execution across PEs. Note: `printf(".")` without `\n` does NOT flush and provides no serialization. After all PEs synchronize at a global fence, avoid simultaneous printf from multiple PEs (post-fence UART contention causes coredump).

### C.7 DLCM Alias Required for 64-bit Scalar Writes

**Symptom**: Writing `unsigned long long` (64-bit) values to SRAM via the non-DLCM physical address (`0x10xxxxxx`) causes silent data corruption or immediate coredump (exit 134). This is critical for gather/scatter table construction, where table entries are 64-bit absolute physical addresses.

**Root cause**: The scalar core's 64-bit store path requires the DLCM coherence manager alias to correctly handle 8-byte writes to SRAM. 8-bit writes work through physical addresses, but 64-bit writes must go through DLCM.

**Workaround**: Write 64-bit table entries through the DLCM alias (`0x20xxxxxx`), but pass the physical (non-DLCM) address as the table pointer to DMA engines like `ada_native_to_vmem_gather`:
```cpp
unsigned long long *table_dl = (unsigned long long*)(0x20000000 + 0x100000 + table_offset);
unsigned long long *table    = (unsigned long long*)(0x100000 + table_offset);
for (int i = 0; i < N; i++) table_dl[i] = (unsigned long long)(src_addr + offset);
ada_native_to_vmem_gather(table, (float*)0, N, (float*)0, N, 1);
```
This also applies to reading NMC-written data (§C.5).

### C.8 All-PE Fence Participation Requirement

**Symptom**: Kernels where some PEs return early (before reaching a global `ada_sync_fence_io()`/`ada_sync_fence_calc()`) cause host segfault (exit 139). Idle PEs that do not participate in compute but still hit the fence are fine; PEs that skip the fence entirely cause runtime crash.

**Rule**: In fatbin split-kernel mode, ALL 32 PEs MUST enter every iteration of a loop containing a global fence. Conditional work must be guarded by `if (pe_id == 0)` INSIDE the fence-participating code path, not by early return. Non-participating PEs may return early ONLY if they do not need to hit the fence (i.e., the fence has mode bits excluding their execution unit).

### C.9 Additional Microbenchmark Pitfalls

- **Scalar core stack size is very limited**: Declaring multiple large arrays (e.g., `uint64_t ts[20]`) across multiple functions causes host segfault. Use single-iteration timing or reduce array sizes.
- **Name collision with `cute::R`**: Using `R` as a constant name causes "reference to 'R' is ambiguous" due to `cute::R` in cute-ada headers. Use `N_REP`, `N_WARM` etc.
- **Fatbin launch requires ALL 32 PEs**: PE(0,1) config is rejected with "invalid unitNum"; PE(0,32)+GLPD+GSRM is mandatory.
- **PE-only .out binary size limit ~112KB**: Templates instantiating many N values cause bloat; use `.adafb` fatbin which has no such limit.
- **`fp8e4m3` constant assignment in scalar core**: Causes backend "Cannot select" error. Use `int8_t` byte fill: `(int8_t)0x38` cast to `fp8e4m3*`.