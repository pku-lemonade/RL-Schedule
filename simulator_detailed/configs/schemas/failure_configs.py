
from pydantic import BaseModel

from ...utils.definitions import Direction, NoCChannel


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

class FailSlow(BaseModel):
    router: list[RouterFail]
    link: list[LinkFail]
    lsu: list[LsuFail]
    tpu: list[TpuFail]
