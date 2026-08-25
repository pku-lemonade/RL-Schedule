from enum import IntEnum
from typing import List

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...utils.definitions import NoCChannel


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
    """Physical transfer size and confirmed logical payload capacity."""

    model_config = ConfigDict(extra="forbid")

    physical_flit_bytes: int = Field(default=512, gt=0)
    payload_capacity_bytes: int = Field(default=512, gt=0)

    @model_validator(mode="after")
    def validate_payload_capacity(self) -> "FlitConfig":
        if self.payload_capacity_bytes > self.physical_flit_bytes:
            raise ValueError("payload capacity cannot exceed physical flit size")
        return self


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
    """Physical link timing and bounded flow-control parameters."""

    model_config = ConfigDict(extra="forbid")

    phit_bytes: int = Field(default=128, gt=0)
    launch_interval_cycles: float = Field(default=512.0 / 120.0, gt=0)
    wire_delay_cycles: float = Field(default=0.5, ge=0)
    input_buffer_depth_flits: int = Field(default=1, gt=0)
    flow_control_window_flits: int = Field(default=1, gt=0)

    def serialization_cycles(self, physical_flit_bytes: int) -> float:
        """Return ideal physical serialization time for one flit."""
        if physical_flit_bytes <= 0:
            raise ValueError("physical flit size must be positive")
        if physical_flit_bytes % self.phit_bytes != 0:
            raise ValueError("physical flit size must contain a whole number of phits")
        return physical_flit_bytes / self.phit_bytes


class NMCChannelConfig(BaseModel):
    """Configuration of one independent, full-duplex PE NMC channel."""

    model_config = ConfigDict(extra="forbid")

    tx_bytes_per_cycle: float = Field(default=120.0, gt=0)
    rx_bytes_per_cycle: float = Field(default=120.0, gt=0)
    descriptor_issue_cycles: float = Field(default=57.0, ge=0)
    max_outstanding_descriptors: int = Field(default=24, gt=0)


class NMCConfig(BaseModel):
    """Two independent PE NMC channels; runtime resources are added in Fix 9."""

    model_config = ConfigDict(extra="forbid")

    ch0: NMCChannelConfig = Field(default_factory=NMCChannelConfig)
    ch1: NMCChannelConfig = Field(default_factory=NMCChannelConfig)

    def channel_config(self, channel: NoCChannel) -> NMCChannelConfig:
        """Return the configuration for a validated hardware channel."""
        if channel is NoCChannel.CH0:
            return self.ch0
        if channel is NoCChannel.CH1:
            return self.ch1
        raise ValueError(f"unsupported NoC channel: {channel!r}")


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
    local_ports: List[int] = Field(default_factory=list[int])
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
    clock_mhz: float = Field(default=1125.0, gt=0)         # ACI/NoC clock
    router: RouterConfig = Field(default_factory=RouterConfig)
    link: LinkConfig = Field(default_factory=LinkConfig)
    c2r_link: LinkConfig = Field(default_factory=LinkConfig)
    dma_engines: List[DMAEngineConfig] = Field(
        default_factory=list[DMAEngineConfig]
    )
    mem_controllers: List[MemoryControllerConfig] = Field(
        default_factory=list[MemoryControllerConfig]
    )


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
