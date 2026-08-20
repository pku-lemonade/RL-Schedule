# ADA2S-32 NoC and Transport Subsystem Architecture (Single-Card Model)

> This document describes the on-chip NoC and data transport subsystem of the ADA2S-32 SoC, scoped to a single chip. All content is derived from hardware header files and driver source; parameters whose exact numerical values are not exposed in code are marked with **[TBD]** and require microbenchmarking or vendor microarchitecture documentation.

## 1. Chip Overview

The ADA2S-32 is a heterogeneous many-core AI accelerator SoC built around a 2D mesh Network-on-Chip (NoC). The chip employs an SPMD programming model where all Processing Elements (PEs) execute the same kernel on different data partitions.

### 1.1 Node Inventory

| Node Type | Count | Physical Node ID | Function | Compiler Attribute |
|-----------|-------|-----------------|----------|-------------------|
| PE | 32 | 0-31 | Compute (Scalar + Vector + Matrix) | `__adax_pe__` |
| GM_RDMA | 4 | 32-35 | Global Memory read DMA (GM -> NoC) | `__adax_gm_rdma__` |
| GM_WDMA | 4 | 36-39 | Global Memory write DMA (NoC -> GM) | `__adax_gm_wdma__` |
| DDR_RDMA | 4 | 40-43 | LPDDR read DMA (DDR -> NoC) | `__adax_ddr_rdma__` |
| DDR_WDMA | 4 | 44-47 | LPDDR write DMA (NoC -> DDR) | `__adax_ddr_wdma__` |

**Total on-chip NoC nodes**: 32 + 4 + 4 + 4 + 4 = **48 nodes**

**Multi-chip extension (ADALINK)**: 8-chip full-mesh configuration adds **18 ADALINK nodes** (IDs 0-3 inter-chassis, 4-17 intra-chassis full mesh), yielding a total launch configuration of 66 nodes. ADALINK provides 1-hop full-mesh connectivity between any two chips; no ring topology is needed. CommID allocation by firmware: Bridge (Attention→MoE inter-chip AllReduce) uses commid=18; MoE internal cross-chip reduction uses commid=16; slot isolation prevents conflicts between phases.

### 1.2 Memory Hierarchy

| Memory | Capacity | Aggregate Bandwidth | Visibility | Address Range |
|--------|----------|-------------------|------------|---------------|
| LPDDR (external) | 128 GB | 533 GB/s | All nodes (via DDR_DMA) | -- |
| Global Memory (GM, on-chip SRAM) | 32 MB | 576 GB/s | All nodes (via GM_DMA) | -- |
| PE Local SRAM | 3 MB per PE | [TBD: per-port BW] | Single PE only | 0x10_0000 - 0x3F_FFFF |
| PE Weight SRAM | 16 MB per PE | [TBD: per-port BW, Matrix-prioritized] | Single PE only | 0x40_0000 - 0x13F_FFFF |
| VMEM (Vector register file) | 128 entries x 128 B = 16 KB per PE | -- | Vector core only | Entry-indexed |
| AIU Download SRAM | 256 KB per DMA node | -- | Local DMA node only | 16 B-aligned |

> **Note (documented)**: SRAM bank conflicts do not need to be considered in software; the hardware SRAM controller handles banking transparently. Feature SRAM (Local) accesses should be 128 B-aligned; Weight SRAM accesses should be 4 KB-aligned.

### 1.3 Clock Domains

| Domain | Frequency | Nodes |
|--------|-----------|-------|
| ACI | **1125 MHz** | PE, GM_RDMA, GM_WDMA, Routers |
| DDR | **1150 MHz** | DDR_RDMA, DDR_WDMA |

> CDC async FIFO bubble penalty measured **<10 cyc fixed overhead** (2026-08-13 Round4): 512B ul base latency 193cyc (DDR) vs 188cyc (GM); negligible for bandwidth modeling.

---

## 2. NoC Mesh Topology

### 2.1 Physical Layout

The Data NoC is a **2D rectangular mesh** with dimensions defined in adas_base_info.h:

- **NOC_DIM_X = 4** (columns)
- **NOC_DIM_Y = 8** (rows)

This yields **32 mesh routers** (ID 0-31), one per PE grid position.

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

**Bandwidth per directional link**: 128 B/cycle × 1125 MHz = **144 GB/s per direction** (full-duplex: 288 GB/s bidirectional). Effective payload throughput ≈ 120 B/cycle = **135 GB/s** after inter-flit bubbles (~6%) and per-flit CRC overhead (~4 B/flit).

**Physical layer vs logical ports (important conceptual model)**:
- All physical links are **unidirectional point-to-point**. There are no true bidirectional wires (half-duplex turnaround latency is avoided entirely in NoC designs).
- All **logical ports visible to software** (the 23 local ports in §2.5 plus the 4 directional neighbor ports, totaling 27 per router) are **bidirectional logical ports**. Each logical port is physically implemented as **two independent unidirectional wires**: one ingress (receiving flits into the router) and one egress (sending flits out of the router).
- There are no unidirectional logical ports. Even "write-only" WDMA ports need to receive credits/ACKs, and "read-only" RDMA ports need to receive read requests.
- Every bidirectional logical port has the same underlying physical capacity: 144 GB/s in + 144 GB/s out = **288 GB/s bidirectional wire capacity**. Actual achieved bandwidth depends on the endpoint device capability, not on router-imposed throttling.

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

> **DMA channel sharing rules** ★: Each DMA instance has its own command processor and independent physical NoC port (bandwidth stacks across instances). Within a CH0/CH1 pair (e.g., NMC CH0/CH1 on a PE, or GM_WDMA CH0/CH1), the pair shares one physical NoC injection port (bandwidth is NOT doubled). Different DMA types (GM_RDMA vs GM_WDMA vs DDR_RDMA vs PE NMC) are independent. Each of the 4 GM_WDMA nodes has an independent port to its router.

**DMA attachment and X fast path**:
- DDR DMAs attach to the four corner routers (0=top-left, 3=top-right, 28=bottom-left, 31=bottom-right).
- GM DMAs attach to the bottom edge (Y=7, routers 28-31), one per column. PE in column i accessing GM i travels straight down Y-direction with no turns.
- **Bottom/top row X fast path**: Dedicated crossbar on Y=0 (top row) and Y=7 (bottom row) reduces X-hop latency to **~11 cycles/X-hop** vs **~17 cycles/Y-hop**; the first 2-6 X-hops add nearly zero latency.

**Achieved bandwidth by port type** (large messages, silicon-measured):
| Port | Connected Device | Single-stream unidir BW | Full-duplex bidir aggregate BW | Bottleneck |
|------|-----------------|-------------------------|--------------------------------|------------|
| N/S/E/W directional ports | Adjacent router | ~135 GB/s (94% wire speed) | ~270 GB/s (135 each direction) | ~6% inter-flit bubbles + ~1% CRC only; near wire capacity |
| Port 0 (PE NMC) | PE DMA engine | ~118-120 GB/s | **~120 GB/s total** (~60 send + ~60 recv) | PE-internal SRAM access port shared by 7 masters (Matrix/Vector/NMC CH0/CH1/Scalar); read+write time-share a single ~120 GB/s port |
| Ports 3/4/10/11 (GM/DDR WDMA dual-side) | GM/DDR write DMA | ~104-122 GB/s | ~120 GB/s aggregate | DMA engine internal logic (same as NMC sharing) |
| Ports 7/14 (GM/DDR RDMA single/dual-side) | GM/DDR read DMA | GM: ~119 GB/s<br>DDR: ~103 GB/s | ~120 GB/s aggregate | DDR download bottleneck is DDR memory controller read pipeline (13% lower than GM), NOT NoC |
| Ports 2/22 (DNOC2AXI/MMU) | Config bus / MMU | <10 GB/s | Far below wire speed | Control-plane ports; register reads/writes and address translation only; not used for bulk data |

> Key insight: The bottleneck is always at the **endpoint DMA/SRAM port**, never at the router-to-router links. Four disjoint PE pairs simultaneously achieve ~119 GB/s each with zero interference, proving NoC fabric capacity is abundant.

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
- Multicast replication: implemented via in-router single-write-multi-read buffer (mentor confirmed); tree construction latency [TBD: measure via broadcast microbenchmark].

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

> [TBD: Per-hop reduction latency overhead in cycles; number of concurrent reduction trees supported; whether reduction is cut-through or store-and-forward; data type support for reduction.]

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

> [TBD: FIFO depths for each hw_id/logic_id; credit return latency.]

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

> [TBD: Exact single-side packet overhead (header size in bytes); request/response round-trip latency; maximum outstanding single-side transactions (maxOst default value, range 0-255); out-of-order response capability.]

---

## 4. PE-Internal NMC (Network Memory Controller)

### 4.1 Overview

Each PE contains one NMC that manages all data movement between the PE's local SRAM (Local + Weight) and the NoC. The NMC exposes **two independent DMA channels** (Channel 0 and Channel 1) to software.

### 4.2 Execution Unit Mapping

Within a PE, the NMC channels are exposed as execution units in the sync framework:

| Unit ID | Name | Direction | Channel |
|---------|------|-----------|---------|
| 3 | PE_DOWNLOAD_0 | NoC -> SRAM (receive) | Channel 0 |
| 4 | PE_UPLOAD_0 | SRAM -> NoC (send) | Channel 0 |
| 5 | PE_DOWNLOAD_1 | NoC -> SRAM (receive) | Channel 1 |
| 6 | PE_UPLOAD_1 | SRAM -> NoC (send) | Channel 1 |
| 7 | PE_VME | SRAM <-> VMEM (Vector Memory Engine) | Internal |

Other PE execution units:

| Unit ID | Name | Function |
|---------|------|----------|
| 1 | PE_MATRIX | Matrix compute engine |
| 2 | PE_VECTOR | Vector compute engine |
| 8 | SCALAR_CORE | Scalar control core (programs NMC/DMA registers) |

Channel-to-unit mapping is fixed: CH0 <-> {DOWNLOAD_0, UPLOAD_0}, CH1 <-> {DOWNLOAD_1, UPLOAD_1}.

**NMC SRAM port bandwidth sharing model (derived from silicon measurements):**
The two NMC channels (CH0/CH1) present 4 logical execution units (UPLOAD_0/DOWNLOAD_0/UPLOAD_1/DOWNLOAD_1), but all share **a single PE-internal SRAM read/write port** between NMC and Local/Weight SRAM. This is the root cause of the ~120 GB/s aggregate NMC bandwidth cap:
- 7 PE-internal bus masters compete for SRAM access: Matrix Core, Vector Core, NMC Upload CH0/CH1, NMC Download CH0/CH1, Scalar Core.
- The NMC-facing SRAM port has a total bandwidth of ~120 GB/s. Reads (upload) and writes (download) time-share this port.
- **Single direction alone** (either upload or download, one channel) achieves ~118-120 GB/s, near the port cap.
- **Same-direction CH0+CH1 concurrency** achieves only ~114-126 GB/s aggregate (3-15% gain over single channel, NOT 2×).
- **Full-duplex CH0 upload + CH1 download** achieves **~120 GB/s total aggregate** (~60 GB/s send + ~60 GB/s recv), NOT 240 GB/s.
- CH0 and CH1 are best understood as **two independent command queues** enabling pipeline overlap (e.g., upload current tile while downloading next tile while Matrix Core computes), not as bandwidth doublers.

This is an intentional bandwidth-matching design decision: the Matrix Core GEMM compute engine itself produces/consumes data at ~100-120 GB/s, so matching NMC bandwidth to compute bandwidth is sufficient to fully hide communication latency via double-buffering. Widening the NMC SRAM port to bidirectional 288 GB/s would waste area and power without providing real kernel benefit.

### 4.3 NMC Channel Capabilities

Each channel supports:
- **Download**: NoC -> Local/Weight SRAM (destination address configurable via register)
- **Upload**: Local/Weight SRAM -> NoC (source address configurable)
- **Independent operation**: CH0 and CH1 can run concurrently (e.g., CH0 uploading while CH1 downloading).
- **Double-buffering**: Software uses CH0/CH1 alternately to overlap compute with IO.

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

> [TBD: Per-channel GM/DDR bandwidth (4 RDMAs + 4 WDMAs share the 576 GB/s GM / 533 GB/s DDR aggregate -- per-channel allocation is not documented).]
>
> **Measured (2026-08-13)**: GM_WDMA CH0 and CH1 **share one physical NoC injection port** (confirmed by B7/C6 dual-channel tests: upload CH0+CH1 aggregate 114 GB/s vs single-ch 104 GB/s; download CH0+CH1 aggregate 124 GB/s vs single-ch 119 GB/s — NOT 2×). Same sharing rule applies to GM_RDMA CH0/CH1 as to PE NMC CH0/CH1. Each GM_WDMA single-channel receive hard-caps at ~100-120 GB/s under N-way incast; each GM_RDMA single-channel send hard-caps at ~120-125 GB/s under N-way outcast.

### 5.2 DMA Scalar Core

DMA nodes (GM_RDMA, GM_WDMA, etc.) each contain a scalar core that dispatches DMA commands. The scalar core executes DMA-initiated transfer programs.

> [TBD: DMA scalar core dispatch rate (commands per cycle); whether RDMA and WDMA on the same router share a scalar core. Documented behavior (from kv_io_overlap_issue.md): GM_RDMA scalar cores can become serialization bottlenecks when dispatching many small fan-out transfers, suggesting command issue is serial per DMA engine.]

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

> [TBD: Outer sync packet propagation latency (cycles from release on sender to acquire satisfied on receiver); whether sync packets have higher NoC priority than data packets.]

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

| Channel | BW @ 64KB (GB/s) | Configuration |
|---------|-------------------|---------------|
| CH0 only | 85.1 | Single channel |
| CH1 only | 82.8 | Single channel (symmetric to CH0) |
| CH0+CH1 parallel (aggregate) | **94.8** | Both channels issued back-to-back, 2×size bytes |

Key finding: CH0 and CH1 **share bandwidth within the NMC pair** (mentor confirmed; "CH0/CH1 pair内共享带宽"). Adding CH1 gives only ~11% aggregate throughput gain via command pipelining, not 2×. This means per-PE injection port is the bottleneck, not per-channel resources.

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
| Per-channel injection BW | **~120 B/cyc = 135 GB/s** | Asymptotic large-message slope |
| Per-direction link wire BW | **144 GB/s** (128 B/cyc × 1125 MHz) | Raw phit rate |
| CH0↔CH1 sharing | Shared port within pair ★ | Mentor-confirmed; B7 dual-channel test shows aggregate ≈95 GB/s; PE↔PE full-duplex bidir also ~120 GB/s (2026-08-13 Round3) |
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
| PE NMC receive cap (N-way incast) | **~125 GB/s aggregate** | N PE senders to 1 PE receiver; fair RR; ~125/N GB/s per flow |
| PE NMC send cap (N-way outcast) | **~130 GB/s aggregate** | 1 PE to N receivers; symmetric to incast; endpoint cap |
| PE↔PE CH0+CH1 full-duplex bidir | **~120 GB/s aggregate** | CH0 send + CH1 recv simultaneously; shared port caps total; NOT 2× |
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
| CH0+CH1 dual-channel (both directions) | **114-124 GB/s aggregate** | Shared physical port; NOT 2× single channel; gain ~4-15% |
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
| PE↔DDR CH0+CH1 dual-ch upload aggregate | **~126 GB/s** (512KB total) | Shared NMC port (NOT 2×); consistent with PE↔GM/PE↔PE finding; single-stream already at 122 GB/s so dual-ch adds <4% at large sizes |
| DDR↔NoC bottleneck location | **DDR_RDMA read + DDR_MC**, not NoC fabric | All 4 DDRs symmetric; disjoint flows expected to scale (4-disjoint-DDR aggregate TBD med-pri) |
| Scalar SRAM write caveat (DDR context) | **Scalar core writes crash** | Same as GM; must use ddr_to_sram for PE-side preload |
| **PE hardware broadcast (pe_broadcast_sync)** | **Zero-cost in-router fan-out** | Round5 2026-08-13: PE0->31 PEs @256KB min=2354cyc (vs 2347cyc N=1, +0.3%); BW per receiver 125 GB/s (~NMC cap); 512B latency 127cyc vs 120cyc (+7cyc spanning-tree); aggregate BW ~3.9 TB/s to 31 PEs |
| **GM hardware broadcast (gm_broadcast_sync)** | **Zero-cost in-router fan-out, ~135 GB/s/stream** | Round5 2026-08-13: GM1->32 PEs @512KB avg=4376cyc (vs 4262cyc N=1, +2.7%); BW per receiver ~135 GB/s; aggregate ~4.3 TB/s; command-issue latency 443cyc(N=1)/598cyc(N=32) fire-and-forget |
| Broadcast vs unicast N-way speedup | **Nx linear up to N*126 GB/s** | Round5: N=31 PE-bcast ~3887 GB/s agg vs ~130 GB/s unicast = **30x speedup**; in-router SWMR replication adds ~7cyc flat overhead independent of N |
| Full-chip spanning-tree setup | **~7cyc (PE bcast), ~155cyc (GM bcast)** | One-time router table programming; adding receivers beyond tree depth adds 0 per-receiver latency |

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

Conclusion: CH0+CH1 share the **same physical NMC injection port** on PE (same as PE↔GM/PE↔PE). Aggregate cap ~126 GB/s, NOT 2× single channel; dual-channel helps small/medium sizes (12-20% gain at 64-128KB) but adds <4% at 512KB where single-stream already saturates. DDR0 (PE0→DDR0) dual-ch gives identical 126.2 GB/s → all 4 DDRs symmetric.

**E3. Y-hop sweep @256KB (X=0 column → DDR1) confirms ul_min = 2337 = constant across 0–7 hops; dl_min increases 102cyc over 7 hops = 14.6 cyc/hop.** Cross-corner (PE0→4 DDRs) confirms X fast-path on top row (Y=0): X-only 3-hop = +34cyc (11.3 cyc/hop) vs Y-only 7-hop = +119cyc (17.0 cyc/hop), same asymmetry as GM/PE↔PE.

**Latency formulas (conservative model):**

- PE→DDR upload one-way (min, large ≥256KB): T_ul_min(sz) = 193 + sz/128 cyc (hop term = 0; pipeline hides all hop latency)
- PE→DDR upload one-way (avg): T_ul_avg(sz, h) = 220 + 17·h + sz/108 cyc
- DDR→PE download one-way (min): T_dl_min(sz, h) = 426 + 17·h + sz/120 cyc
- DDR→PE download one-way (avg): T_dl_avg(sz, h) = 442 + 17·h + sz/108 cyc

---

### 9.11 Network Bisection Bandwidth

Bisection bandwidth is the minimum total bandwidth crossing any cut that evenly divides all compute nodes (32 PEs) into two equal halves of 16 PEs each. For an 8×4 2D mesh:

| Cut | Links cut | Per-link physical unidir BW | Physical unidir bisection BW | Physical bidir full-duplex bisection BW | Router-level effective unidir BW (with ~6% bubble loss) |
|-----|----------|----------------------------|------------------------------|----------------------------------------|------------------------------------------------------|
| **Minimal bisection** (horizontal cut: Y=0-3 vs Y=4-7, cutting 4 vertical links between Y=3 and Y=4) | 4 | 144 GB/s | **576 GB/s** | **1.15 TB/s** | ~540 GB/s (4×135 GB/s) |
| Vertical cut (X=0-1 vs X=2-3, cutting 8 horizontal links between X=1 and X=2) | 8 | 144 GB/s | 1152 GB/s | 2.3 TB/s | ~1.08 TB/s |

The **minimum bisection bandwidth** (the architecturally significant number) is **576 GB/s unidirectional / 1.15 TB/s bidirectional physical**, or ~540 GB/s / ~1.08 TB/s effective at router level.

**Endpoint-constrained effective bisection**: When the number of concurrent flows crossing the bisection is ≤ 4 (e.g., 4 PEs sending to the other half), each flow achieves ~120 GB/s (NMC endpoint cap) for an aggregate of ~480 GB/s, and the links themselves are not yet saturated. Only when more than 4 concurrent flows cross the cut do the links become the bottleneck, and flows are fairly round-robin partitioned.

The bottom/top-row X fast path (~11 cyc/X-hop vs ~17 cyc/Y-hop) reduces latency for horizontal traffic on the edge rows but does not change the bisection bandwidth values.

---

### 9.12 Collective Communication Best Practices by Scale

The optimal collective communication algorithm depends on the number of participating PEs and the operation type. Based on hardware capabilities and measured performance:

| Participating PEs | Topology | Optimal Algorithm | Measured BW per endpoint | Typical kernel use case |
|-------------------|----------|-------------------|--------------------------|------------------------|
| 2 PEs (adjacent) | Direct neighbor | Single dual-side send/recv | ~120 GB/s | Pairwise neighbor exchange |
| 4 PEs (same row) | Linear chain along X | Neighbor-chain pipeline reduce + broadcast; long packets fill pipeline for ~100% link utilization | ~120 GB/s/PE | Attention SV reduction (4 PEs per row), QKV/Gate AllReduce, MoE MLP1 N-dim shard reduction |
| 8 PEs (same column) | Linear chain along Y | Same chain pipeline reduce + broadcast | ~120 GB/s/PE | Attention Gate GEMM row-wise reduce, MoE MLP0 K-dim shard reduction |
| 16 PEs (half-chip) | Either | Break-even point: chain pipeline latency ≈ GM-relay latency; choice depends on message size | Similar for both | Rarely used in current kernels |
| 32 PEs (full-chip global) | All PEs | **GM write_sum (reduce) + GM BROADCAST (distribute)**: all PEs write shards to GM simultaneously using atomic add, one GM BROADCAST distributes result to all PEs; zero-cost in-router fan-out | Reduce: ~120 GB/s aggregate<br>Broadcast: **~135 GB/s/PE independent**, aggregate ~4.3 TB/s | Attention LSE global merge, MoE expert task list broadcast |
| N PEs (scatter-add only, result stays in GM) | All PEs → GM | **GM write_sum atomic add directly**: no need for PE-to-PE reduce at all; each PE writes its contribution straight to GM, hardware sums atomically; result already in GM no return broadcast needed | 4×GM_WDMA aggregate **~314 GB/s** | MoE final output accumulation: different experts'/cards' contributions to same token sum directly in GM |
| 8 chips (multi-card) | 8-chip ADALINK fullmesh | ADALINK 1-hop AllReduce; no ring topology needed | 1-hop direct; commid 16/18 slot-isolated | Bridge stage inter-chip Attention O-projection AllReduce, MoE cross-card expert reduction |

**Core decision rule**:
- ≤ 8 PEs (row or column): use **neighbor-chain pipelining** — avoids GM endpoint setup overhead (~250+ cyc), fills pipeline for near-100% bandwidth utilization, result lands directly at the end PE ready for subsequent ops (SiLU, quantization, etc.)
- ≥ 16 PEs (full/half chip): use **GM relay + hardware broadcast** — GM BROADCAST zero-cost fan-out gives every receiver ~135 GB/s independent bandwidth, 30× faster than N-way unicast; the GM endpoint setup overhead (~155 cyc) is amortized across large fan-out
- Pure scatter-add (reduction result consumed by next layer in GM, not needed back on PEs): use **GM write_sum directly** — skips an entire communication phase (no return broadcast needed)

---

## 10. Remaining TBD Parameters (What We Still Lack)

Parameters marked **[TBD]** in this document that require additional measurement or vendor disclosure for a high-fidelity (<20% error) event-driven model:

### 10.1 High Priority (Directly Affects End-to-End NoC Timing)

| # | Parameter | Why It Matters | How to Measure |
|---|-----------|---------------|----------------|
| 1 | ~~**PE↔GM bandwidth** (single GM_RDMA/WDMA channel)~~ | **DONE (§9.9, updated 2026-08-13 Round2)**: Single-PE upload ~104 GB/s (0-hop) to ~90 GB/s (7-hop), 138cyc/257cyc fixed latency; 4-GM_WDMA aggregate ~314 GB/s; download ~119 GB/s (0-hop 256KB), 246cyc/365cyc fixed latency; CH0+CH1 share port (114-124 GB/s dual-ch aggregate); N-way incast caps at ~100-120 GB/s/GM, outcast ~120-125 GB/s/GM; bottom-row X fast path confirmed for both directions; atomic with_sum=1 ~120 GB/s aggregate. | Extend bench with `sram_to_gm` / `gm_to_sram` using `__global_sram__` GM allocation from Python host; sweep message sizes to/from GM0 (router 28). |
| 2 | ~~**PE↔DDR bandwidth**~~ | **DONE (§9.10, 2026-08-13 Round4)**: Upload ~122 GB/s/ch (0-hop, PE NMC-limited, hop latency fully hidden at ≥256KB), download ~103 GB/s/ch (DDR_RDMA/MC-limited, ~13% lower than GM); 512B base latency: ul 193cyc (+5cyc CDC), dl 426cyc (+180cyc DDR PHY); hop cost dl ~15-17cyc/hop, ul ~0cyc/hop (large msg); CH0+CH1 shared port (126 GB/s dual-ch agg, NOT 2×); X fast path confirmed for DDR on rows 0/7; all 4 DDRs symmetric. CDC penalty <10cyc. | Fatbin split-kernel with __global_ddr__ buffers (adaMemoryType.GDDR), ddr_send_sync/ddr_receive_sync/ddr_to_sram APIs, same launch config as GM. |
| 3 | ~~**Multicast/broadcast fanout latency** (1-to-N)~~ | **DONE (§9.7, Round5 2026-08-13)**: Zero-cost in-router SWMR replication; min_cyc 2347->2354 (+7cyc = +0.3%) from N=1 to N=31; each receiver gets full sender BW (~126 GB/s PE, ~135 GB/s GM); PE bcast 512B latency 120->127cyc (+7cyc spanning-tree); GM bcast 512B 443->598cyc (+155cyc); agg BW 3.9 TB/s (PE) / 4.3 TB/s (GM) to full chip; **30x speedup vs unicast N-way outcast** (130 GB/s cap). Use pe_broadcast_sync/gm_broadcast_sync with dst_mask bitmask; receivers call standard recv_with_sync. | `pe_broadcast_sync<CH>(view, int64_t dst_mask)` / `gm_broadcast_sync<CH>(view, NodeType::PE, int64_t dst_mask)`; measure N=1..31. |
| 4 | **In-router reduction overhead** (Add/Max per-hop) | Allreduce / reduce-scatter for MoE router-logit aggregation uses `opType=Add`; need per-reduce-hop cycle cost and whether reduction is cut-through or store-and-forward. Note: GM-side atomic (with_sum=1) measured fully pipelined (~0 overhead); PE-side in-router reduction chain still TBD. | Use `send_with_sync(..., ReduceOp::Add, ...)` in a reduction chain across PEs; compare RTT with reduction enabled vs disabled. |
| 5 | ~~**Incast/outcast/full-duplex/contention**~~ | **ALL DONE (2026-08-13 Round2+Round3)**: N-way incast/outcast caps at ~100-130 GB/s aggregate for all endpoints (GM_WDMA rx ~100-120, GM_RDMA tx ~120-125, PE rx ~125, PE tx ~130 GB/s) with fair RR; CH0+CH1 full-duplex bidir ~120 GB/s (shared NMC port, NOT 2x); 4 disjoint pairs ~119 GB/s each with zero interference → **endpoint NMC ports (~120 GB/s/ch) are the bottleneck, not NoC fabric**. Root cause: PE-internal NMC-to-SRAM port is shared (~120 GB/s total aggregate, read+write time-shared), an intentional bandwidth match to Matrix Core compute (see §4.2). | noc_pe_incast/outcast/fulldup/contend + GM fatbin kernels. |
| 6 | ~~**SRAM port structure / NMC bandwidth sharing**~~ | **RESOLVED (§4.2, 2026-08-20 analysis)**: All 4 NMC logical units (UPLOAD_0/DOWNLOAD_0/UPLOAD_1/DOWNLOAD_1) share a single PE-internal SRAM read/write port of total ~120 GB/s; single direction achieves ~118-120 GB/s; same-direction CH0+CH1 concurrency gains only 3-15% (NOT 2×); full-duplex CH0+CH1 aggregate is also ~120 GB/s (~60 send + ~60 recv). CH0/CH1 are independent command queues for pipeline overlap, not bandwidth doublers. 7 PE-internal bus masters (Matrix, Vector, NMC x4, Scalar) compete for SRAM access; hardware handles bank conflicts transparently. Remaining TBD: exact SRAM macro port count (1R1W vs 2R1W) at the circuit level, but irrelevant for kernel programming. | Run concurrent upload (CH0) + download (CH1) + matrix compute; measure achieved NMC BW vs isolated. |

### 10.2 Medium Priority (Affects Contention Accuracy)

| # | Parameter | How to Measure |
|---|-----------|----------------|
| 7 | **Sync packet priority vs data packets** | Do sync/ACK packets preempt data flits? Measure RTT under heavy background BW load. |
| 8 | **NMC command issue rate** (back-to-back `native_send` gap) | How many cycles between two independent send fires? Fire N sends in a tight loop without waiting; measure total time vs N. |
| 9 | **NMC outstanding depth** (max in-flight DMA commands per channel) | How many transfers can be queued before scalar core stalls? |
| 10 | **Credit return latency breakdown** | The 17-cycle RTT-hop includes credit return path latency; model needs this for buffer sizing. |
| 11 | **GM scalar core dispatch rate** | Documented serialization bottleneck for many small GM transfers; measure commands/cycle. |
| 12 | **Burst length (beat size)** | Confirm beat = 128 B (gather table entry unit) vs 512 B (flit). |
| 13 | **Per-hop packet processing latency breakdown** | How much is route-compute vs switch-arbitration vs link-traversal within the 8.5 cyc budget. |

### 10.3 Lower Priority (Corner Cases / Features Not in Current Critical Path)

| # | Parameter | Notes |
|---|-----------|-------|
| 14 | ~~Single-side (request/response) exact overhead~~ | **DONE (2026-08-13)**: <3% difference vs dual-side; performance equivalent. Earlier "10% degradation" claim was incorrect. |
| 15 | ~~DDR↔ACI async FIFO bubble penalty~~ | **DONE (Round4, §9.10)**: <10 cyc fixed overhead; negligible for BW modeling (512B ul base 193cyc vs GM 188cyc → +5cyc). | CDC between 1150 MHz DDR and 1125 MHz ACI domains. |
| 16 | BAUA / mask / transpose throughput impact | Used in attention/conv but not in MoE critical path; zero-cost vs extra cycles. |
| 17 | Gather/scatter per-entry table-walk overhead | For MoE expert dispatch (may be used). |
| 18 | maxOst default value | Max outstanding single-side transactions (0-255). |
| 19 | MMU page size / translation latency | If MMU is used in practice. |
| 20 | AdaLink cross-chip NoC extension latency | Multi-chip (2-card/4-card/8-card) AdaLink bridge latency; needed for multi-card modeling. |
| 21 | FIFO credit depths per hw_id/logic_id | For detailed flow-control modeling. |
| 22 | Reduction tree concurrency (max simultaneous trees) | For modeling overlapping collectives. |
| 23 | ~~Atomic GM writeSum throughput~~ | **DONE (2026-08-13 Round2 B8)**: ~120 GB/s aggregate, same as non-atomic incast; RMW fully pipelined; float32 only (int32 unsupported in compiler). |

### 10.4 Recommended Next Benchmark Sequence

To get from current state to <20% end-to-end error for MoE workloads:

1. ~~**GM↔PE BW** (#1)~~: **DONE (§9.9, Round1+Round2 2026-08-13)**. Upload/download latency, BW, CH sharing, incast/outcast, hop-sweep, X fast-path, atomic, symmetricity all measured.
2. ~~**PE↔DDR bandwidth** (#2)~~: **DONE (§9.10, Round4 2026-08-13)**. Upload 122 GB/s (NMC-limited, hop-free for large msg), download 103 GB/s (DDR_RDMA/MC-limited, 17cyc/hop), CH0+CH1 shared port (126 GB/s agg), CDC penalty <10cyc, 4-DDR symmetry confirmed.
3. **Multicast/broadcast fanout** (#3): Hardware MULTICAST/BROADCAST transType — needed for EP weight dispatch (MoE all-gather). Note: unicast outcast from GM already gives ~125 GB/s/GM baseline.
4. **In-router PE-side reduction** (#4): `ReduceOp::Add` in send_with_sync for reduce-scatter chains (note: GM-side atomic with_sum=1 already confirmed zero-overhead).
5. ~~**Dual-stream full-duplex**~~: **DONE (Round3 A10, 2026-08-13)**. Full-duplex bidirectional BW ~120 GB/s aggregate, same as single-channel — CH0/CH1 share port in all directions.
6. **GM internal DMA↔DMA** (WDMA→RDMA without PE involvement): `wdma_signal_rdma`/`rdma_wait_wdma` path for MoE pipeline data movement within GM.
7. **DDR N-way incast/outcast** (med-pri): N PEs → 1 DDR_WDMA / 1 DDR_RDMA → N PEs; expected to cap at same ~120-130 GB/s aggregate as other endpoints (RR at NMC), but DDR_RDMA read-side cap (103 GB/s/ch) may lower effective ceiling.

The current benchmark infrastructure in `kernels/cpp/noc_microbench/` provides a working template (build system, Python runner, both .out PE-only and fatbin split-kernel modes, integer/float timing, send_with_sync/recv_with_sync/gm/ddr send/receive_sync handshake, atomic with_sum, parameterized PE/GM/DDR selection) that can be extended for these additional measurements. **All core PE↔PE, PE↔GM, and PE↔DDR NoC parameters required for MoE modeling are now calibrated** (2026-08-13 Round1+2+3+4): RTT latency (8.5 cyc/hop one-way, 250+17h), single-stream BW (PE↔PE ~118, PE→GM ~127, GM→PE ~119, PE→DDR ~122, DDR→PE ~103 GB/s/ch), N-way incast/outcast fair RR sharing (~120-130 GB/s aggregate cap at PE/GM endpoints), CH0/CH1 shared port across all directions and endpoints (aggregate ~114-126 GB/s, NOT 2×), full-duplex bidirectional, atomic reduce (zero-overhead pipelined), X fast path on edge rows, disjoint-flow zero interference, 4-GM and 4-DDR perfect symmetry, DDR CDC penalty <10cyc, and DDR download being DDR-memory-controller-limited (not NoC-limited).

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
