import json
from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator


class SPMConfig(BaseModel):
    size: int
    delay: int = 0


class TPUConfig(BaseModel):
    size: int = 1
    flops: int = 1024


class LSUConfig(BaseModel):
    size: int = 4
    width: int = 4


class NMCConfig(BaseModel):
    channels: int = 2
    start_up_time: int = 1


class CoreConfig(BaseModel):
    type: str = "Simple"
    x: int = 8
    y: int = 4
    width: int = 8
    blk_size: int = 10
    spm: SPMConfig
    tpu: TPUConfig = TPUConfig()
    lsu: LSUConfig = LSUConfig()
    nmc: NMCConfig = NMCConfig()
    element_bytes: int = 1


class RouterConfig(BaseModel):
    type: str = "XY"
    vc: int = 2
    reduce_latency: int = 2
    burst_bubble: int = 1


class LinkConfig(BaseModel):
    width: int = 16
    delay: int = 0


class NoCConfig(BaseModel):
    type: str = "Mesh"
    x: int = 8
    y: int = 4
    router: RouterConfig = RouterConfig()
    link: LinkConfig = LinkConfig()


class ClockConfig(BaseModel):
    aci_mhz: int = 1125
    ddr_mhz: int = 1150

    @property
    def ddr_scale(self) -> float:
        return self.ddr_mhz / self.aci_mhz


class DMAEngineConfig(BaseModel):
    channels: int = 2
    width: int = 16


class MemoryConfig(BaseModel):
    gm_capacity: int = 32 * 1024 * 1024
    ddr_capacity: int = 128 * 1024 * 1024 * 1024
    gm_aggregate_bw: int = 0
    ddr_aggregate_bw: int = 0
    gm_engine_width: int = 16
    ddr_engine_width: int = 16
    aiu_sram_size: int = 256 * 1024


class NodeConfig(BaseModel):
    enable_dma: bool = True
    enable_adalink: bool = True
    adalink_latency: int = 8
    include_all_adalink_port: bool = True


class ShadowConfig(BaseModel):
    enabled: bool = False
    occupancy: int = 4
    ii: int = 1
    matrix: List[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    vector: List[int] = Field(default_factory=lambda: [0, 0, 0])
    sramc_dnld: int = 0
    sramc_upld: int = 0
    mdma_channel: int = 0
    mdma_aiu: int = 0
    aci_func: int = 0
    aci_aiu: int = 0

    @field_validator("occupancy", "ii")
    @classmethod
    def _pos(cls, v: int) -> int:
        if v < 1:
            raise ValueError("occupancy and ii must be >= 1")
        return v

    @field_validator(
        "sramc_dnld", "sramc_upld", "mdma_channel", "mdma_aiu",
        "aci_func", "aci_aiu",
    )
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("latencies must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_lists(self) -> "ShadowConfig":
        if len(self.matrix) != 4:
            raise ValueError("matrix must have 4 stage latencies")
        if len(self.vector) != 3:
            raise ValueError("vector must have 3 stage latencies")
        for v in self.matrix + self.vector:
            if v < 0:
                raise ValueError("stage latencies must be >= 0")
        return self


class ArchConfig(BaseModel):
    core: CoreConfig
    noc: NoCConfig
    clock: ClockConfig = ClockConfig()
    nodes: NodeConfig = NodeConfig()
    memory: MemoryConfig = MemoryConfig()
    shadow: ShadowConfig = ShadowConfig()


def load_arch(path: str) -> ArchConfig:
    with open(path) as f:
        return ArchConfig.model_validate(json.load(f))
