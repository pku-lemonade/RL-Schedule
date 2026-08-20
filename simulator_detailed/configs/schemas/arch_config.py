from typing import List
from enum import IntEnum
from pydantic import BaseModel


class DMAType(IntEnum):
    """DMA engine type: read (source) or write (sink) attached to GM or DDR."""
    GM_RDMA  = 0  # GM read DMA, injects data from GM into NoC
    GM_WDMA  = 1  # GM write DMA, drains data from NoC to GM
    DDR_RDMA = 2  # DDR read DMA, injects data from DDR into NoC
    DDR_WDMA = 3  # DDR write DMA, drains data from NoC to DDR


class MemType(IntEnum):
    """Memory controller type behind the DMA engines."""
    GM  = 0  # On-chip global memory
    DDR = 1  # Off-chip DDR memory


class SPMConfig(BaseModel):
    """Scratchpad memory (SRAM) config per memory bank."""
    size: int       # B, total capacity of the SRAM bank
    delay: int = 1  # cycles, fixed access latency per allocate/release


class TPUConfig(BaseModel):
    """Tensor Processing Unit (compute engine) config per core."""
    size: int = 1    # number of TPU instances (parallelism)
    flops: int = 4096  # FLOPs per cycle, peak compute throughput


class LSUConfig(BaseModel):
    """Load-Store Unit config for data movement between SRAM and NMC."""
    size: int = 1      # number of LSU instances
    width: float = 106.0  # B/cycle, LSU throughput (NMC SRAM port share)


class FlitConfig(BaseModel):
    """Flit-level NoC transport parameters defining how messages are packetized."""
    phit_width: int = 128            # B/cycle, physical link width (one phit per cycle)
    flit_size: int = 512             # B, size of one flit (1 flit = 4 phits on ADA2S)
    header_bytes: int = 12           # B, routing header overhead in HEAD flit
    body_overhead: int = 4           # B, per-flit overhead (seq/CRC) in BODY/TAIL flits
    buffer_depth_flits: int = 1      # flits, input buffer depth per router port (1-flit on ADA2S)
    serialization_cycles: float = 4.3  # cycles, time to serialize one flit onto the link
    credit_rtt_cycles: int = 17       # cycles, credit round-trip time for backpressure


class RouterPipelineConfig(BaseModel):
    """Per-hop router pipeline stage latencies for flit traversal."""
    rc_cycles: float = 1.0         # cycles, Route Compute stage (HEAD flit only)
    sa_cycles: float = 2.0         # cycles, Switch Allocation / arbitration stage
    st_cycles: float = 1.0         # cycles, Switch Traversal (crossbar crossing)
    lt_cycles: float = 0.5         # cycles, Link Traversal (wire propagation, half-cycle)
    credit_overhead: float = 1.0   # cycles, extra delay on credit return path
    head_hop_cycles: float = 4.5   # cycles, total HEAD flit per-hop latency (RC+SA+ST+LT)
    body_hop_cycles: float = 4.3   # cycles, BODY/TAIL flit per-hop latency (no RC, pipelined)
    x_fast_hop_cycles: float = 5.5  # cycles, X-direction fast-path hop on Y=0/7 rows
    y_hop_cycles: float = 8.5      # cycles, Y-direction hop latency (no fast path)


class RouterConfig(BaseModel):
    """Router microarchitecture config."""
    type: str = "XY"                  # routing algorithm, "XY" = X-first deterministic
    vc: int = 1                       # number of virtual channels per port
    arbitration: str = "round_robin"  # arbitration policy, "round_robin" on ADA2S
    flit: FlitConfig = FlitConfig()           # flit transport parameters
    pipeline: RouterPipelineConfig = RouterPipelineConfig()  # pipeline stage latencies


class LinkConfig(BaseModel):
    """Inter-router or core-to-router link config."""
    width: int = 128      # B/cycle, link bandwidth (phit width)
    delay: float = 0.5    # cycles, wire propagation delay (half-cycle)


class NMCConfig(BaseModel):
    """Network Memory Controller config: PE-side NoC-to-SRAM interface."""
    channels: int = 2            # number of NMC channels (CH0/CH1), shared SRAM port
    sram_port_bw: float = 106.0  # B/cycle, aggregate SRAM port bandwidth shared by both channels
    startup_static: int = 80     # cycles, static (hot) startup latency for first flit injection
    startup_dynamic: int = 125   # cycles, dynamic (cold/wakeup) additional startup latency


class CoreConfig(BaseModel):
    """Processing Element (PE core) config, one per mesh node."""
    type: str = "Simple"      # core type identifier
    x: int = 4                # number of columns in the core mesh
    y: int = 8                # number of rows in the core mesh
    width: int = 128          # B/cycle, core-to-router link width
    blk_size: int = 128       # B, default block/tile size for tensor partitioning
    spm: SPMConfig = SPMConfig(size=3145728, delay=1)        # local scratchpad (3 MB)
    weight_spm: SPMConfig = SPMConfig(size=16777216, delay=1)  # weight scratchpad (16 MB)
    tpu: TPUConfig = TPUConfig()        # compute unit config
    lsu: LSUConfig = LSUConfig()        # load-store unit config
    nmc: NMCConfig = NMCConfig()        # network memory controller config


class DMAEngineConfig(BaseModel):
    """DMA engine config: GM/DDR read/write endpoints attached to router local ports."""
    dma_type: DMAType          # DMA direction: GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA
    instance_id: int           # instance index within dma_type (0-3 for 4 GM/DDR controllers)
    router_id: int             # router ID this DMA is attached to
    channels: int = 1          # number of independent DMA channels (WDMA=2, RDMA=1)
    local_ports: List[int] = []  # router local port numbers occupied by this DMA
    port_bw: float = 106.0     # B/cycle, per-port bandwidth (GM=106, DDR~=91.5)
    clock_scale: float = 1.0   # clock domain ratio relative to NoC (DDR=1.022 for 1150MHz)
    cdc_penalty: int = 0       # cycles, clock-domain-crossing penalty (DDR=5, GM=0)
    dispatch_interval: int = 1  # cycles, scalar-core dispatch serialization between channels


class MemoryControllerConfig(BaseModel):
    """Memory controller (GM/DDR) aggregate bandwidth model behind DMA engines."""
    mem_type: MemType          # memory type: GM (on-chip) or DDR (off-chip)
    aggregate_bw: float        # B/cycle, total bandwidth across all instances of this type
    instances: List[int]       # instance IDs covered by this controller group
    atomic_supported: bool = True  # whether atomic read-modify-write ops are supported
    atomic_bw: float = 106.0   # B/cycle, bandwidth available for atomic (reduce) operations


class NoCConfig(BaseModel):
    """Network-on-Chip topology and component config."""
    type: str = "Mesh"                                    # topology: Mesh/Torus/RingRoad/Dragonfly
    x: int = 4                                            # mesh columns
    y: int = 8                                            # mesh rows
    router: RouterConfig = RouterConfig()                  # router microarchitecture
    link: LinkConfig = LinkConfig()                        # inter-router link parameters
    dma_engines: List[DMAEngineConfig] = []                # DMA endpoints attached to routers
    mem_controllers: List[MemoryControllerConfig] = []     # memory controller bandwidth limits


class MemConfig(BaseModel):
    """Off-chip / global memory interface config (top-level)."""
    width: int = 128  # B/cycle, memory interface width
    delay: int = 5    # cycles, fixed memory access latency


class ArchConfig(BaseModel):
    """Top-level architecture config combining core, NoC, and memory subsystems."""
    core: CoreConfig = CoreConfig()     # PE core array config
    noc: NoCConfig = NoCConfig()        # Network-on-Chip config
    mem: MemConfig = MemConfig()        # memory interface config


class ScratchpadConfig(BaseModel):
    """Legacy scratchpad config (backward compatibility alias)."""
    size: int   # B, SRAM capacity
    delay: int  # cycles, access latency
