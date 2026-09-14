from enum import Enum, auto
from enum import IntEnum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProfilingSimError(Exception):
    pass


class UnboundLocalPortError(ProfilingSimError):
    pass


class UnsupportedTransferMode(ProfilingSimError):
    pass


class TransType(IntEnum):
    SINGLECAST = 0
    FIXPATH = 1
    MULTICAST = 2
    BROADCAST = 3
    REDUCE = 4


class TransferMode(IntEnum):
    DUAL_SIDE = 0
    SINGLE_SIDE = 1


class InterChipOp(IntEnum):
    WRITE = auto()
    WRITE_SUM = auto()
    WRITE_MAX = auto()
    WRITE_WITH_IMMEDIATE = auto()
    WRITE_SUM_WITH_IMMEDIATE = auto()
    WRITE_MAX_WITH_IMMEDIATE = auto()

    @property
    def has_immediate(self) -> bool:
        return self in (
            InterChipOp.WRITE_WITH_IMMEDIATE,
            InterChipOp.WRITE_SUM_WITH_IMMEDIATE,
            InterChipOp.WRITE_MAX_WITH_IMMEDIATE,
        )

    @property
    def is_atomic(self) -> bool:
        return self in (
            InterChipOp.WRITE_SUM,
            InterChipOp.WRITE_MAX,
            InterChipOp.WRITE_SUM_WITH_IMMEDIATE,
            InterChipOp.WRITE_MAX_WITH_IMMEDIATE,
        )

    @property
    def atomic_op(self) -> int:
        if self in (InterChipOp.WRITE_SUM, InterChipOp.WRITE_SUM_WITH_IMMEDIATE):
            return 1
        if self in (InterChipOp.WRITE_MAX, InterChipOp.WRITE_MAX_WITH_IMMEDIATE):
            return 2
        return 0


class OperatorType(Enum):
    LOAD_FEAT = auto()
    LOAD_WGT = auto()
    CONV = auto()
    POOL = auto()
    FC = auto()
    STORE = auto()
    SEND = auto()
    RECV = auto()


comp_operator = [OperatorType.CONV, OperatorType.POOL, OperatorType.FC]
comm_operator = [OperatorType.SEND, OperatorType.RECV]
io_operator = [OperatorType.LOAD_FEAT, OperatorType.LOAD_WGT, OperatorType.STORE]


def ceil(a: int, b: int):
    return (a + b - 1) // b


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
        res = None
        for dim_slice in self.tensor_slice:
            dim_len = max(0, dim_slice.end - dim_slice.start)
            if res is None:
                res = dim_len
            else:
                res = res * dim_len
        return res


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: int
    dst: int
    index: int
    data: List[DimSlice]
    element_bytes: int = Field(default=1, gt=0)
    src_local_port: int = 0
    dst_local_port: int = 0
    header_bytes: int = Field(default=0, ge=0)
    immediate_bytes: int = Field(default=0, ge=0)
    trans_type: int = TransType.SINGLECAST
    dst_mask: int = 0
    transfer_mode: int = TransferMode.DUAL_SIDE
    sync: bool = False
    ins_sync_mode: int = 0
    fixed_path: Optional[List[int]] = None
    addr: int = 0
    value: int = 0
    write_sum: int = 0
    is_local_memory: bool = False
    is_control: bool = False
    reduce_op: int = 0
    task_id: int = 0
    reduce_is_last: bool = False
    reduce_count: int = 1
    priority: int = 0
    burst_len_mode: int = 0
    fifo_hw_id: int = -1
    fifo_logic_id: int = -1
    fifo_check_type: int = -1
    layout: Optional["TensorLayout"] = None
    dst_rank: int = 0
    interchip_op: int = int(InterChipOp.WRITE)
    imm: int = 0

    @field_validator("dst_rank")
    @classmethod
    def _check_dst_rank(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"dst_rank {v} must be >= 0")
        return v

    @field_validator("imm")
    @classmethod
    def _check_imm(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"imm {v} must be >= 0")
        return v

    @field_validator("ins_sync_mode")
    @classmethod
    def _check_sync_mode(cls, v: int) -> int:
        if v not in (0, 1, 2, 3):
            raise ValueError(f"ins_sync_mode {v} not in (0,1,2,3)")
        return v

    @property
    def imm_bytes(self) -> int:
        try:
            return (
                self.immediate_bytes
                if InterChipOp(self.interchip_op).has_immediate
                else 0
            )
        except ValueError:
            return 0

    def total_bytes_with_imm(self) -> int:
        return self.byte_size() + self.header_bytes + self.imm_bytes

    @field_validator("src_local_port", "dst_local_port")
    @classmethod
    def _check_port(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"local port {v} must be nonnegative")
        return v

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, v: int) -> int:
        if v < 0:
            raise ValueError("priority must be nonnegative")
        return v

    @field_validator("burst_len_mode")
    @classmethod
    def _check_burst(cls, v: int) -> int:
        if v < 0:
            raise ValueError("burst quantum must be nonnegative; zero uses link config")
        return v

    @model_validator(mode="after")
    def _check_layout_trans_type(self) -> "Message":
        if self.layout is not None and self.trans_type not in (
            TransType.SINGLECAST,
            TransType.FIXPATH,
        ):
            raise ValueError(
                f"layout is only valid for SINGLECAST/FIXPATH, got "
                f"trans_type={self.trans_type}"
            )
        if self.fixed_path is not None and self.trans_type != TransType.FIXPATH:
            raise ValueError("fixed_path is only valid for FIXPATH trans_type")
        return self

    def __lt__(self, other: "Message") -> bool:
        return self.byte_size() < other.byte_size()

    def byte_size(self) -> int:
        if self.layout is not None:
            return self.layout.payload_bytes()
        return Slice(tensor_slice=self.data).size() * self.element_bytes

    def total_bytes(self) -> int:
        return self.byte_size() + self.header_bytes


class TraceItem(BaseModel):
    id: int
    slow: float = 0.0
    ultilization: float = 0.0
    op_num: int = 0


class TimeSlice(BaseModel):
    cores: List[TraceItem] = []
    links: List[TraceItem] = []
    dma_links: List[TraceItem] = []
    mem_bw: List[TraceItem] = []


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
    src_port: int = -1
    dst_port: int = -1
    flops: int = 0
    data_size: int = 0
    is_control: bool = False
    is_sync: bool = False
    reduce_count: int = 0
    priority: int = 0
    burst_index: int = 0
    burst_count: int = 1


from .layout import TensorLayout  # noqa: E402

Message.model_rebuild(_types_namespace={"TensorLayout": TensorLayout})
