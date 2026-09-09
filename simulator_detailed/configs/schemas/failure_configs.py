
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...utils.definitions import Direction, NoCChannel, NodeType


class RouterFail(BaseModel):
    start_time: int
    end_time: int
    fabric_id: NoCChannel = NoCChannel.CH0
    router_id: int
    times: int

class LinkFail(BaseModel):
    start_time: int
    end_time: int
    fabric_id: NoCChannel = NoCChannel.CH0
    router_id: int
    direction: Direction
    times: int

class LsuFail(BaseModel):
    start_time: int
    end_time: int
    pe_id: int
    times: int

class TpuFail(BaseModel):
    start_time: int
    end_time: int
    pe_id: int
    times: int


class DMAFail(BaseModel):
    """Slow one shared DMA payload engine during an ACI-cycle interval."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    start_time: float = Field(ge=0, allow_inf_nan=False)
    end_time: float = Field(gt=0, allow_inf_nan=False)
    node_type: Literal[
        NodeType.GM_RDMA, NodeType.GM_WDMA,
        NodeType.DDR_RDMA, NodeType.DDR_WDMA,
    ]
    instance_id: int = Field(ge=0, le=3)
    times: float = Field(ge=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_interval(self) -> "DMAFail":
        if self.end_time <= self.start_time:
            raise ValueError("DMA failure end_time must exceed start_time")
        return self


class FailSlow(BaseModel):
    router: list[RouterFail]
    link: list[LinkFail]
    lsu: list[LsuFail]
    tpu: list[TpuFail]
    dma: list[DMAFail] = Field(default_factory=list[DMAFail])
