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

> [TBD: Async FIFO depth and bubble-cycle overhead for CDC between DDR (1150 MHz) and ACI (1125 MHz) domains.]

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

### 2.4 Router Local Port Map (DataNocLocalId)

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

Each router also has **4 directional ports** (North, South, East, West) connecting to mesh neighbors. Edge routers have fewer active directional ports (e.g., router 0 has no North or West neighbor).

> [TBD: Number of virtual channels; physical link width (bits/cycle/direction); flit size in bytes; per-hop router pipeline latency in cycles; input buffer depth per port in flits; total shared buffer size in bytes/flits; arbitration algorithm details beyond the 4-level port priority.]

---

## 3. Router Behavior

### 3.1 Packet Header Encoding

Packet headers are constructed by the `build_info1` function at the sender NMC:

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

| Bits | Field | Width | Description |
|------|-------|-------|-------------|
| [5:0] | route_id | 6 bits | Destination router ID (supports up to 64 routers) |
| [11:6] | -- | 6 bits | Reserved |
| [16:12] | local_id | 5 bits | Destination local port ID (DataNocLocalId) |
| [18:17] | trans_type | 2 bits | Transfer type |
| [31:19] | -- | 13 bits | Reserved |

> [TBD: Complete packet format (header size in bytes, payload flit count, tail flit, CRC/checksum, packet vs flit granularity). The above is the software-visible routing field; full microarchitectural packet format is not exposed.]

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

> [TBD: Default routing algorithm (dimension-order? XY vs YX?); adaptive routing support; multicast replication implementation details (tree-based vs path-based); multicast tree construction latency.]

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

> [TBD: Per-channel SRAM read/write bandwidth (bytes/cycle); contention model when Matrix/Vector/Scalar + both NMC channels access SRAM simultaneously (hardware handles bank conflicts, but port-level arbitration and bandwidth sharing is not documented).]

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

> [TBD: NMC setup latency -- cycles from scalar writing the final register (firing the transfer) to the first flit appearing on the NoC. Empirically PE EU instruction issue is ~0.9 us (~1000 cycles at 1125 MHz) but exact NMC launch latency needs measurement.]

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

> [TBD: Per-channel GM/DDR bandwidth (4 RDMAs + 4 WDMAs share the 576 GB/s GM / 533 GB/s DDR aggregate -- per-channel allocation is not documented). Whether WDMA CH0 and CH1 share a single physical NoC link or have independent links is not explicitly stated; the separate DataNocLocalId values suggest independent ports.]

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

> [TBD: Atomic throughput (contending writes to same GM address); supported data types for atomic operations.]

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

## 9. Measured Performance Data (Empirical)

These values are from real hardware profiling on ADA2S-32 and can be used to calibrate the model:

| Metric | Value | Conditions |
|--------|-------|------------|
| Single NMC channel steady-state bandwidth | ~18 GB/s | 128 B-aligned contiguous dual-side transfer |
| Transformer layer steady-state (no MoE) | ~109 us | 32 PE, 32 query tokens / 2048 KV context, TP |
| Transformer layer steady-state (with MoE) | ~161 us | Includes expert routing overhead |
| RMSNorm kernel (C++ hand-optimized) | ~40 us | hidden=5120, bf16, 32 PE |
| RMSNorm kernel (Triton JIT) | ~59 us | same problem size, ~1.5x slower than C++ |
| Cold start (kernel launch) | ~10 ms | One-time firmware/init overhead |
| PE EU instruction issue latency | ~0.9 us (~1000 cycles at 1125 MHz) | Scalar issue -> unit start |
| Single-side vs dual-side throughput ratio | ~10% degradation | Documented in ARCH.md |

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
