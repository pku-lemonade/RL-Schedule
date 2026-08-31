import math
from enum import IntEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from ...utils.definitions import (
    FLIT_BYTES,
    BurstLenMode,
    NMCShapeMode,
    NoCChannel,
)


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
    """Fixed physical transfer size and logical payload capacity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_flit_bytes: int = Field(default=FLIT_BYTES, strict=True)
    payload_capacity_bytes: int = Field(default=FLIT_BYTES, strict=True)

    @model_validator(mode="after")
    def validate_fixed_hardware_flit(self) -> "FlitConfig":
        if (
            self.physical_flit_bytes != FLIT_BYTES
            or self.payload_capacity_bytes != FLIT_BYTES
        ):
            raise ValueError(
                f"physical flit size and payload capacity must both be {FLIT_BYTES} B"
            )
        return self


class RouterPipelineConfig(BaseModel):
    """Effective router-stage timing in the simulator's ACI-cycle domain."""

    model_config = ConfigDict(extra="forbid")

    effective_rc_aci_cycles: float = Field(default=1.0, ge=0)
    effective_sa_aci_cycles: float = Field(default=2.0, ge=0)
    effective_st_aci_cycles: float = Field(default=1.0, ge=0)


class RouterConfig(BaseModel):
    """Router microarchitecture config."""

    type: str = "XY"                  # routing algorithm, "XY" = X-first deterministic
    vc: int = 1                       # number of virtual channels per port
    arbitration: str = "round_robin"  # arbitration policy, "round_robin" on ADA2S
    default_burst_len_mode: BurstLenMode = BurstLenMode.BURST_LEN_7
    flit: FlitConfig = Field(default_factory=FlitConfig)
    pipeline: RouterPipelineConfig = Field(default_factory=RouterPipelineConfig)

    @field_validator("default_burst_len_mode")
    @classmethod
    def validate_default_burst_len_mode(
        cls,
        mode: BurstLenMode,
    ) -> BurstLenMode:
        if mode is not BurstLenMode.BURST_LEN_7:
            raise ValueError(
                "hardware BURST_LEN_DEFAULT must resolve to BURST_LEN_7"
            )
        return mode

    def resolve_burst_quantum_flits(self, mode: BurstLenMode) -> int:
        """Resolve a transfer mode to an explicit arbitration quantum."""
        resolved_mode = mode
        if mode is BurstLenMode.BURST_LEN_DEFAULT:
            resolved_mode = self.default_burst_len_mode
        return resolved_mode.explicit_quantum_flits()


class LinkConfig(BaseModel):
    """Native data-NoC width and effective ACI-domain link timing."""

    model_config = ConfigDict(extra="forbid")

    wire_bits_per_noc_cycle: int = Field(default=579, gt=0)
    payload_bits_per_noc_cycle: int = Field(default=512, gt=0)
    launch_interval_aci_cycles: float = Field(default=512.0 / 120.0, gt=0)
    effective_link_stage_aci_cycles: float = Field(default=0.5, ge=0)
    # Zero preserves measured ACI hop timing until sync_noc latency is isolated.
    sync_credit_return_aci_cycles: float = Field(default=0.0, ge=0)
    input_buffer_depth_flits: int = Field(default=1, gt=0, strict=True)
    effective_in_flight_window_flits: int = Field(default=2, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_native_width(self) -> "LinkConfig":
        if self.payload_bits_per_noc_cycle > self.wire_bits_per_noc_cycle:
            raise ValueError("payload bits cannot exceed physical wire bits")
        if self.payload_bits_per_noc_cycle % 8 != 0:
            raise ValueError("payload width must contain a whole number of bytes")
        if self.input_buffer_depth_flits != 1:
            raise ValueError("hardware downstream input buffer depth must be one flit")
        return self

    def serialization_noc_cycles(self) -> int:
        """Return native NoC cycles needed to serialize one logical flit."""
        flit_bits = FLIT_BYTES * 8
        if flit_bits % self.payload_bits_per_noc_cycle != 0:
            raise ValueError(
                "physical flit size must contain a whole number of native NoC beats"
            )
        return flit_bits // self.payload_bits_per_noc_cycle

    def serialization_aci_cycles(
        self,
        noc_cycles_per_aci_cycle: float,
    ) -> float:
        """Convert native serialization time to the simulator's ACI timebase."""
        if noc_cycles_per_aci_cycle <= 0:
            raise ValueError("NoC-to-ACI clock ratio must be positive")
        return (
            self.serialization_noc_cycles()
            / noc_cycles_per_aci_cycle
        )

    def required_in_flight_window_flits(
        self,
        noc_cycles_per_aci_cycle: float,
    ) -> int:
        """Return the window needed to sustain the calibrated launch interval."""
        zero_load_residence = (
            self.serialization_aci_cycles(noc_cycles_per_aci_cycle)
            + self.effective_link_stage_aci_cycles
            + self.sync_credit_return_aci_cycles
        )
        return max(
            1,
            math.ceil(zero_load_residence / self.launch_interval_aci_cycles),
        )


class NMCShapeTimingConfig(BaseModel):
    """Measured total setup targets for one NMC endpoint in ACI cycles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    static_endpoint_setup_aci_cycles: float = Field(
        default=79.5,
        ge=0,
        allow_inf_nan=False,
    )
    dynamic_endpoint_setup_aci_cycles: float = Field(
        default=125.0,
        ge=0,
        allow_inf_nan=False,
    )

    def endpoint_setup_target_aci_cycles(
        self,
        shape_mode: NMCShapeMode,
    ) -> float:
        """Return the measured total target for one endpoint setup path."""
        if shape_mode is NMCShapeMode.STATIC:
            return self.static_endpoint_setup_aci_cycles
        if shape_mode is NMCShapeMode.DYNAMIC:
            return self.dynamic_endpoint_setup_aci_cycles
        raise ValueError(f"unsupported NMC shape mode: {shape_mode!r}")


class NMCChannelConfig(BaseModel):
    """Configuration of one independent, full-duplex PE NMC channel."""

    model_config = ConfigDict(extra="forbid")

    tx_bytes_per_cycle: float = Field(default=120.0, gt=0)
    rx_bytes_per_cycle: float = Field(default=120.0, gt=0)
    descriptor_issue_cycles: float = Field(default=57.0, ge=0)
    max_outstanding_descriptors: int = Field(default=24, gt=0)


class NMCConfig(BaseModel):
    """Two independent PE NMC channels and measured shape timing targets."""

    model_config = ConfigDict(extra="forbid")

    ch0: NMCChannelConfig = Field(default_factory=NMCChannelConfig)
    ch1: NMCChannelConfig = Field(default_factory=NMCChannelConfig)
    shape_timing: NMCShapeTimingConfig = Field(
        default_factory=NMCShapeTimingConfig
    )

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
    width: int = 128          # effective B/ACI-cycle at the PE-to-router interface
    blk_size: int = 128       # B, default block/tile size for tensor partitioning
    spm: SPMConfig = Field(default_factory=lambda: SPMConfig(size=4194304, delay=1))
    weight_spm: SPMConfig = Field(default_factory=lambda: SPMConfig(size=16777216, delay=1))
    tpu: TPUConfig = Field(default_factory=TPUConfig)
    lsu: LSUConfig = Field(default_factory=LSUConfig)
    nmc: NMCConfig = Field(default_factory=NMCConfig)


class DMAEngineConfig(BaseModel):
    """DMA engine config: GM/DDR read/write endpoints attached to router local ports."""

    model_config = ConfigDict(extra="forbid")

    dma_type: DMAType          # DMA direction: GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA
    instance_id: int           # instance index within dma_type (0-3 for 4 GM/DDR controllers)
    router_id: int             # router ID this DMA is attached to
    channels: int = 1          # number of independent DMA channels (WDMA=2, RDMA=1)
    local_ports: list[int] = Field(default_factory=list[int])
    port_bw: float = 106.0     # effective B/ACI-cycle (GM=106, DDR~=91.5)
    cdc_penalty: int = 0       # effective ACI cycles (DDR=5, GM=0)
    dispatch_interval: int = 1  # ACI cycles between scalar-core dispatches

    @computed_field
    @property
    def endpoint_clock_mhz(self) -> float:
        """Return the fixed hardware clock for the endpoint's memory domain."""
        if self.dma_type in (DMAType.GM_RDMA, DMAType.GM_WDMA):
            return 900.0
        return 1200.0


class MemoryControllerConfig(BaseModel):
    """Memory controller (GM/DDR) aggregate bandwidth model behind DMA engines."""
    mem_type: MemType          # memory type: GM (on-chip) or DDR (off-chip)
    aggregate_bw: float        # B/cycle, total bandwidth across all instances of this type
    instances: list[int]       # instance IDs covered by this controller group
    atomic_supported: bool = True  # whether atomic read-modify-write ops are supported
    atomic_bw: float = 106.0   # B/cycle, bandwidth available for atomic (reduce) operations


class NoCConfig(BaseModel):
    """Network-on-Chip topology and component config."""

    model_config = ConfigDict(extra="forbid")

    type: str = "Mesh"                                    # topology: Mesh/Torus/RingRoad/Dragonfly
    x: int = 4                                            # mesh columns
    y: int = 8                                            # mesh rows
    aci_clock_mhz: float = Field(default=1125.0, gt=0)
    noc_clock_mhz: float = Field(default=2250.0, gt=0)
    router: RouterConfig = Field(default_factory=RouterConfig)
    link: LinkConfig = Field(default_factory=LinkConfig)
    c2r_link: LinkConfig = Field(default_factory=LinkConfig)
    dma_engines: list[DMAEngineConfig] = Field(
        default_factory=list[DMAEngineConfig]
    )
    mem_controllers: list[MemoryControllerConfig] = Field(
        default_factory=list[MemoryControllerConfig]
    )

    @model_validator(mode="after")
    def validate_clock_ratio(self) -> "NoCConfig":
        if not math.isclose(
            self.noc_cycles_per_aci_cycle,
            2.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("ADA2S-32 requires noc_clk to be exactly 2x aci_clk")
        return self

    @property
    def noc_cycles_per_aci_cycle(self) -> float:
        """Return native NoC cycles elapsed during one ACI simulation cycle."""
        return self.noc_clock_mhz / self.aci_clock_mhz


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
