from typing import List
from enum import IntEnum
from pydantic import BaseModel, Field


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
    """Packet format parameters."""
    flit_size: int = 512
    header_bytes: int = 12
    body_overhead: int = 4


class RouterPipelineConfig(BaseModel):
    """Router pipeline stages, in ACI cycles."""
    rc_cycles: float = 1.0
    sa_cycles: float = 2.0
    st_cycles: float = 1.0


class RouterConfig(BaseModel):
    """Router microarchitecture config."""
    type: str = "XY"                  # routing algorithm, "XY" = X-first deterministic
    vc: int = 1                       # number of virtual channels per port
    arbitration: str = "round_robin"  # arbitration policy, "round_robin" on ADA2S
    flit: FlitConfig = Field(default_factory=FlitConfig)
    pipeline: RouterPipelineConfig = Field(default_factory=RouterPipelineConfig)


class LinkConfig(BaseModel):
    """Unidirectional phit link and downstream input-buffer parameters."""
    phit_width: int = 128
    wire_delay: float = 0.5
    buffer_depth: int = 1
    credit_return_cycles: float = 0.3


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
    spm: SPMConfig = Field(default_factory=lambda: SPMConfig(size=3145728, delay=1))
    weight_spm: SPMConfig = Field(default_factory=lambda: SPMConfig(size=16777216, delay=1))
    tpu: TPUConfig = Field(default_factory=TPUConfig)
    lsu: LSUConfig = Field(default_factory=LSUConfig)
    nmc: NMCConfig = Field(default_factory=NMCConfig)


class DMAEngineConfig(BaseModel):
    """DMA engine config: GM/DDR read/write endpoints attached to router local ports."""
    dma_type: DMAType          # DMA direction: GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA
    instance_id: int           # instance index within dma_type (0-3 for 4 GM/DDR controllers)
    router_id: int             # router ID this DMA is attached to
    channels: int = 1          # number of independent DMA channels (WDMA=2, RDMA=1)
    local_ports: List[int] = Field(default_factory=list)
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
    router: RouterConfig = Field(default_factory=RouterConfig)
    link: LinkConfig = Field(default_factory=LinkConfig)
    c2r_link: LinkConfig = Field(default_factory=LinkConfig)
    dma_engines: List[DMAEngineConfig] = Field(default_factory=list)
    mem_controllers: List[MemoryControllerConfig] = Field(default_factory=list)


class MemConfig(BaseModel):
    """Off-chip / global memory interface config (top-level)."""
    width: int = 128  # B/cycle, memory interface width
    delay: int = 5    # cycles, fixed memory access latency


class ArchConfig(BaseModel):
    """Top-level architecture config combining core, NoC, and memory subsystems."""
    core: CoreConfig = Field(default_factory=CoreConfig)
    noc: NoCConfig = Field(default_factory=NoCConfig)
    mem: MemConfig = Field(default_factory=MemConfig)


class ScratchpadConfig(BaseModel):
    """Legacy scratchpad config (backward compatibility alias)."""
    size: int   # B, SRAM capacity
    delay: int  # cycles, access latency
