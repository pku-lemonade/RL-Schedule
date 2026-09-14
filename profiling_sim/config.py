import json
from typing import List
from enum import IntEnum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigModel(BaseModel):
    """Reject unknown settings and non-finite hardware parameters."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SPMConfig(ConfigModel):
    size: int = Field(gt=0)
    delay: int = Field(default=0, ge=0)


class TPUConfig(ConfigModel):
    size: int = Field(default=1, gt=0)
    flops: int = Field(default=1, gt=0)


class LSUConfig(ConfigModel):
    size: int = Field(default=4, gt=0)
    width: int = Field(default=4, gt=0)


class NMCConfig(ConfigModel):
    channels: int = Field(default=2, gt=0)
    start_up_time: int = Field(default=1, ge=0)
    injection_ports: int = Field(default=0, ge=0)


class CoreConfig(ConfigModel):
    type: str = "Simple"
    x: int = Field(default=3, gt=0)
    y: int = Field(default=2, gt=0)
    width: int = 8
    blk_size: int = 10
    spm: SPMConfig
    tpu: TPUConfig = TPUConfig()
    lsu: LSUConfig = LSUConfig()
    nmc: NMCConfig = NMCConfig()
    element_bytes: int = 1
    sram_read_ports: int = 0
    sram_write_ports: int = 0


class RouterConfig(ConfigModel):
    control_bytes: int = Field(default=1, ge=0)
    sync_bytes: int = Field(default=1, ge=0)
    pe_local_port: int = Field(default=0, ge=0)
    type: str = "XY"
    vc: int = 2
    reduce_latency: int = 2
    burst_bubble: int = 1
    local_injection_capacity: int = 0


class LinkConfig(ConfigModel):
    burst_beats: int = Field(default=0, ge=0)
    single_side_bandwidth_factor: float = Field(default=1.0, gt=0, le=1)
    width: int = Field(default=16, gt=0)
    delay: int = Field(default=0, ge=0)


class NodeType(IntEnum):
    PE = 0
    GM_RDMA = 1
    GM_WDMA = 2
    DDR_RDMA = 3
    DDR_WDMA = 4
    INTERCHIP = 5


class NodeAttachment(ConfigModel):
    node_type: NodeType
    instance_id: int = Field(ge=0)
    router_id: int = Field(ge=0)
    ports: list[int] = Field(min_length=1)
    channels: int = Field(default=1, gt=0)
    local_memory_port: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ports(self) -> "NodeAttachment":
        ports = self.ports + (
            [] if self.local_memory_port is None else [self.local_memory_port]
        )
        if self.node_type == NodeType.PE:
            raise ValueError("PE attachments are generated from the mesh")
        if any(p < 0 for p in ports) or len(set(ports)) != len(ports):
            raise ValueError("attachment ports must be nonnegative and unique")
        return self


class NoCConfig(ConfigModel):
    type: str = "Mesh"
    x: int = Field(default=3, gt=0)
    y: int = Field(default=2, gt=0)
    router: RouterConfig = RouterConfig()
    link: LinkConfig = LinkConfig()
    concentrated_routers: List[int] = Field(default_factory=list)
    pe_local_port: int = Field(default=0, ge=0)
    interchip_broadcast_port: int | None = Field(default=None, ge=0)
    attachments: list[NodeAttachment] = Field(default_factory=list)
    commid_count: int = Field(default=1, gt=0)
    fifo_hardware_count: int = Field(default=1, gt=0)
    fifo_logical_count: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_attachments(self) -> "NoCConfig":
        keys = set()
        ports = {(r, self.pe_local_port) for r in range(self.x * self.y)}
        for item in self.attachments:
            key = (item.node_type, item.instance_id)
            if key in keys:
                raise ValueError("duplicate endpoint identity")
            keys.add(key)
            if item.router_id >= self.x * self.y:
                raise ValueError("attachment router is outside the mesh")
            local = item.ports + (
                [] if item.local_memory_port is None else [item.local_memory_port]
            )
            for port in local:
                physical = (item.router_id, port)
                if physical in ports:
                    raise ValueError("local port has more than one owner")
                if port == self.interchip_broadcast_port:
                    raise ValueError("endpoint collides with the broadcast port")
                ports.add(physical)
        if self.interchip_broadcast_port == self.pe_local_port:
            raise ValueError("broadcast and PE ports must differ")
        if any(
            not 0 <= router < self.x * self.y for router in self.concentrated_routers
        ):
            raise ValueError("concentrated router is outside the mesh")
        return self


class ClockConfig(ConfigModel):
    aci_mhz: float = Field(default=1.0, gt=0)
    ddr_mhz: float = Field(default=1.0, gt=0)

    @property
    def ddr_scale(self) -> float:
        return self.ddr_mhz / self.aci_mhz


class DMAEngineConfig(ConfigModel):
    channels: int = Field(default=2, gt=0)
    width: int = Field(default=16, gt=0)
    dispatch_interval: int = Field(default=0, ge=0)


class MemoryConfig(ConfigModel):
    gm_capacity: int = Field(default=1024, gt=0)
    ddr_capacity: int = Field(default=1024, gt=0)
    gm_aggregate_bw: int = Field(default=0, ge=0)
    ddr_aggregate_bw: int = Field(default=0, ge=0)
    gm_engine_width: int = Field(default=16, gt=0)
    ddr_engine_width: int = Field(default=16, gt=0)
    local_memory_size: int = Field(default=1024, gt=0)
    local_memory_alignment: int = Field(default=1, gt=0)
    local_memory_sync_target: int = Field(default=0, ge=0)
    local_memory_sync_bytes: int = Field(default=1, ge=0)
    dma: DMAEngineConfig = DMAEngineConfig()


class NodeConfig(ConfigModel):
    enable_dma: bool = True
    enable_interchip: bool = True
    interchip_latency: int = Field(default=0, ge=0)
    include_all_interchip_port: bool = True


class ShadowConfig(ConfigModel):
    matrix_compute_stage: int = Field(default=0, ge=0)
    vector_compute_stage: int = Field(default=0, ge=0)
    enabled: bool = False
    occupancy: int = 1
    ii: int = 1
    matrix: List[int] = Field(default_factory=lambda: [0])
    vector: List[int] = Field(default_factory=lambda: [0])
    sramc_dnld: int = 0
    sramc_upld: int = 0
    mdma_channel: int = 0
    mdma_local_memory: int = 0
    aci_func: int = 0
    aci_local_memory: int = 0

    @field_validator("occupancy", "ii")
    @classmethod
    def _pos(cls, v: int) -> int:
        if v < 1:
            raise ValueError("occupancy and ii must be >= 1")
        return v

    @field_validator(
        "sramc_dnld",
        "sramc_upld",
        "mdma_channel",
        "mdma_local_memory",
        "aci_func",
        "aci_local_memory",
    )
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("latencies must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_lists(self) -> "ShadowConfig":
        if not self.matrix or not self.vector:
            raise ValueError("compute pipelines need at least one stage")
        if self.matrix_compute_stage >= len(
            self.matrix
        ) or self.vector_compute_stage >= len(self.vector):
            raise ValueError("compute stage is outside the configured pipeline")
        for v in self.matrix + self.vector:
            if v < 0:
                raise ValueError("stage latencies must be >= 0")
        return self


class ArchConfig(ConfigModel):
    core: CoreConfig
    noc: NoCConfig
    clock: ClockConfig = ClockConfig()
    nodes: NodeConfig = NodeConfig()
    memory: MemoryConfig = MemoryConfig()
    shadow: ShadowConfig = ShadowConfig()

    @model_validator(mode="after")
    def validate_memory_target(self) -> "ArchConfig":
        if self.memory.local_memory_sync_target >= self.noc.x * self.noc.y:
            raise ValueError("local memory sync target is outside the mesh")
        return self


def load_arch(path: str) -> ArchConfig:
    with open(path) as f:
        return ArchConfig.model_validate(json.load(f))
