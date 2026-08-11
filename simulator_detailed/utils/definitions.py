import json
from enum import Enum, auto
from enum import IntEnum
from typing import List
from pydantic import BaseModel
from pydantic import ValidationError


class OperatorType(Enum):
    """Supported operator types in the DFG"""
    LOAD_FEAT = auto()  # Load feature map
    LOAD_WGT  = auto()  # Load weight
    CONV      = auto()  # Convolution
    POOL      = auto()  # Pooling
    FC        = auto()  # Fully connected
    STORE     = auto()  # Store output
    SEND      = auto()  # Send data to another core
    RECV      = auto()  # Receive data from another core

comp_operator = [OperatorType.CONV, OperatorType.POOL, OperatorType.FC]
comm_operator = [OperatorType.SEND, OperatorType.RECV]
io_operator = [OperatorType.LOAD_FEAT, OperatorType.LOAD_WGT, OperatorType.STORE]


def ceil(a: int, b: float) -> int:
    return int((a + b - 1) // b)

class Direction(IntEnum):
    NORTH = 0
    SOUTH = 1
    EAST = 2
    WEST = 3


class DimSlice(BaseModel):
    start: int
    end: int
    

class Slice(BaseModel):
    tensor_slice: List[DimSlice]
    def size(self) -> int:
        if self.tensor_slice == []:
            return 0

        res = 1
        for dim_slice in self.tensor_slice:
            dim_len = max(0, dim_slice.end - dim_slice.start)
            res = res * dim_len
        return res
    
    def max(self, other: "Slice") -> "Slice":
        res = []
        for i in range(len(self.tensor_slice)):
            res.append(
                DimSlice(
                    start = min(self.tensor_slice[i].start, other.tensor_slice[i].start),
                    end = max(self.tensor_slice[i].end, other.tensor_slice[i].end)
                )
            )
        return Slice(tensor_slice=res)


class Message(BaseModel):
    src: int
    dst: int
    index: int
    data: List[DimSlice]

    def __lt__(self, other: "Message") -> bool:
        slice = Slice(tensor_slice=self.data)
        other_slice = Slice(tensor_slice=other.data)
        
        return slice.size() < other_slice.size()
    

class TraceItem(BaseModel):
    id: int
    slow: float
    ultilization: float
    op_num: int


class TimeSlice(BaseModel):
    cores: List[TraceItem] = []
    links: List[TraceItem] = []


class Trace(BaseModel):
    time_slices: List[TimeSlice] = []


class Event(BaseModel):
    type: OperatorType = OperatorType.SEND
    index: int = -1
    start_time: int = -1
    end_time: int = -1
    pe_id: int = -1
    src_id: int = -1
    dst_id: int = -1
    flops: int = 0
    data_size: int = 0