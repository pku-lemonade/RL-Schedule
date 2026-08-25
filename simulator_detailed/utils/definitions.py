from __future__ import annotations

import math
from enum import Enum, IntEnum, auto
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from ..configs.schemas.arch_config import FlitConfig


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
    return math.ceil(a / b)


class Direction(IntEnum):
    NORTH = 0
    SOUTH = 1
    EAST = 2
    WEST = 3


class NodeType(IntEnum):
    """Type of NoC endpoint node."""
    PE       = 0  # Processing Element (compute core)
    GM_RDMA  = 1  # Global Memory read DMA (data source: GM -> NoC)
    GM_WDMA  = 2  # Global Memory write DMA (data sink: NoC -> GM)
    DDR_RDMA = 3  # DDR read DMA (data source: DDR -> NoC)
    DDR_WDMA = 4  # DDR write DMA (data sink: NoC -> DDR)


class FlitType(IntEnum):
    """Flit type in wormhole packetization."""
    SINGLE = 0  # Single-flit packet: establishes and releases the path
    HEAD = 1    # First flit of a multi-flit packet
    BODY = 2    # Interior flit following an established path
    TAIL = 3    # Final flit releasing the path


class NoCChannel(IntEnum):
    """PE NMC channel and its corresponding physical NoC fabric."""

    CH0 = 0
    CH1 = 1


class TransType(IntEnum):
    """Hardware transmission encoding stored in routing-word bits [18:17]."""

    SINGLECAST = 0  # Normal unicast to one destination (XY routing)
    FIXPATH = 1     # Source-specified fixed path
    MULTICAST = 2   # Multicast to a subset of destinations (in-router fan-out)
    BROADCAST = 3   # Broadcast to all PEs (multicast with full mask)


PORT_PE           = 0   # local port: PE NMC
PORT_DDR_WDMA_CH0 = 3   # local port: DDR write DMA channel 0
PORT_DDR_WDMA_CH1 = 4   # local port: DDR write DMA channel 1
PORT_DDR_RDMA_LOC = 5   # local port: DDR read DMA (local/on-chip path)
PORT_DDR_WDMA_LOC = 6   # local port: DDR write DMA (local/on-chip path)
PORT_DDR_RDMA     = 7   # local port: DDR read DMA (off-chip path)
PORT_DDR_WDMA     = 8   # local port: DDR write DMA (off-chip path)
PORT_GM_WDMA_CH0  = 10  # local port: GM write DMA channel 0
PORT_GM_WDMA_CH1  = 11  # local port: GM write DMA channel 1
PORT_GM_RDMA_LOC  = 12  # local port: GM read DMA (local path)
PORT_GM_WDMA_LOC  = 13  # local port: GM write DMA (local path)
PORT_GM_RDMA      = 14  # local port: GM read DMA
PORT_GM_WDMA      = 15  # local port: GM write DMA

_ENDPOINT_ROUTERS = {
    NodeType.PE: tuple(range(32)),
    NodeType.GM_RDMA: (28, 29, 30, 31),
    NodeType.GM_WDMA: (28, 29, 30, 31),
    NodeType.DDR_RDMA: (0, 28, 3, 31),
    NodeType.DDR_WDMA: (0, 28, 3, 31),
}

_ENDPOINT_LOCAL_PORTS = {
    NodeType.PE: frozenset({PORT_PE}),
    NodeType.GM_RDMA: frozenset({PORT_GM_RDMA_LOC, PORT_GM_RDMA}),
    NodeType.GM_WDMA: frozenset(
        {PORT_GM_WDMA_CH0, PORT_GM_WDMA_CH1, PORT_GM_WDMA_LOC, PORT_GM_WDMA}
    ),
    NodeType.DDR_RDMA: frozenset({PORT_DDR_RDMA_LOC, PORT_DDR_RDMA}),
    NodeType.DDR_WDMA: frozenset(
        {PORT_DDR_WDMA_CH0, PORT_DDR_WDMA_CH1, PORT_DDR_WDMA_LOC, PORT_DDR_WDMA}
    ),
}

DIR_NORTH = 100  # direction port: North neighbor (out port = +Y)
DIR_SOUTH = 101  # direction port: South neighbor (out port = -Y)
DIR_EAST  = 102  # direction port: East neighbor (out port = +X)
DIR_WEST  = 103  # direction port: West neighbor (out port = -X)

_DIR_MAP = {
    Direction.NORTH: DIR_NORTH,
    Direction.SOUTH: DIR_SOUTH,
    Direction.EAST:  DIR_EAST,
    Direction.WEST:  DIR_WEST,
}


def direction_to_port(direction: Direction) -> int:
    """Map a Direction enum to its numeric port ID (>= 100 range)."""
    return _DIR_MAP[direction]


def port_to_direction(port: int) -> Optional[Direction]:
    """Map a numeric direction port ID back to Direction enum, or None if not a direction port."""
    for d, p in _DIR_MAP.items():
        if p == port:
            return d
    return None


def is_direction_port(port: int) -> bool:
    """Check if a port ID is a direction (inter-router) port (>= 100)."""
    return port >= 100


def is_local_port(port: int) -> bool:
    """Check if a port ID is a local endpoint port (< 100)."""
    return port < 100


def expected_endpoint_router(node_type: NodeType, node_id: int) -> int:
    """Return the fixed ADA2S-32 router attachment for an endpoint."""
    routers = _ENDPOINT_ROUTERS[node_type]
    if not 0 <= node_id < len(routers):
        raise ValueError(
            f"{node_type.name} node_id must be between 0 and {len(routers) - 1}"
        )
    return routers[node_id]


def valid_endpoint_local_ports(node_type: NodeType) -> frozenset[int]:
    """Return hardware local ports that can address the given endpoint type."""
    return _ENDPOINT_LOCAL_PORTS[node_type]


def compute_flit_count(
    payload_bytes: int,
    payload_capacity_bytes: int = 512,
) -> int:
    """Return the measured logical flit count for a payload.

    Header and CRC sizes are estimated wire metadata. Hardware measurements
    expose 512 logical payload bytes per flit, so they are not deducted here.
    A zero-byte control message still occupies one flit.
    """
    if payload_bytes < 0:
        raise ValueError("payload size cannot be negative")
    if payload_capacity_bytes <= 0:
        raise ValueError("flit payload capacity must be positive")
    return max(1, ceil(payload_bytes, payload_capacity_bytes))


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
        res: list[DimSlice] = []
        for i in range(len(self.tensor_slice)):
            res.append(
                DimSlice(
                    start = min(self.tensor_slice[i].start, other.tensor_slice[i].start),
                    end = max(self.tensor_slice[i].end, other.tensor_slice[i].end)
                )
            )
        return Slice(tensor_slice=res)


class Flit(BaseModel):
    """A single flit (flow control digit) in the wormhole NoC."""
    flit_type: FlitType              # HEAD/BODY/TAIL
    payload_bytes: int               # B, payload carried by this flit
    msg_id: int                      # message index for reordering/correlation
    dst_router: int                  # destination router ID
    dst_local_port: int = PORT_PE    # destination local port on the dst router
    src_router: int                  # source router ID
    src_local_port: int = PORT_PE    # source local port on the src router
    is_broadcast: bool = False       # whether this is a broadcast flit (in-router fan-out)
    broadcast_dst_mask: int = 0      # bitmask of destination PE/router IDs for broadcast
    reduce_op: int = -1              # reduce operation type (-1 = none, >=0 = reduce mode)
    sync_mode: int = 0               # sync mode: 0=normal,1=bcast-incl-self,2=reduce-intermediate,3=reduce-last

    @property
    def is_head(self) -> bool:
        return self.flit_type in (FlitType.SINGLE, FlitType.HEAD)

    @property
    def is_tail(self) -> bool:
        return self.flit_type in (FlitType.SINGLE, FlitType.TAIL)


class EndpointAddress(BaseModel):
    """Resolved, immutable attachment of one type-qualified NoC endpoint."""

    model_config = ConfigDict(frozen=True)

    node_type: NodeType
    node_id: int = Field(ge=0)
    router_id: int = Field(ge=0, le=63)
    local_port: int = Field(ge=0, le=31)

    @model_validator(mode="after")
    def validate_attachment(self) -> "EndpointAddress":
        expected_router = expected_endpoint_router(self.node_type, self.node_id)
        if self.router_id != expected_router:
            raise ValueError(
                f"{self.node_type.name}[{self.node_id}] must attach to router "
                f"{expected_router}, not router {self.router_id}"
            )
        if self.local_port not in valid_endpoint_local_ports(self.node_type):
            raise ValueError(
                f"{self.node_type.name}[{self.node_id}] cannot use local port "
                f"{self.local_port}"
            )
        return self


class Message(BaseModel):
    """A message to be injected into the NoC, packetized into flits."""
    src: EndpointAddress              # resolved source endpoint attachment
    dst: EndpointAddress              # resolved destination endpoint attachment
    index: int                        # unique message index (DFG task index)
    data: List[DimSlice]              # tensor slice(s) describing payload
    trans_type: TransType = TransType.SINGLECAST  # transmission type
    is_broadcast: bool = False        # whether this is a broadcast message
    broadcast_dst_mask: int = 0       # destination bitmask for broadcast/multicast
    reduce_op: int = -1               # reduce operation (-1 = none)
    sync_mode: int = 0                # sync mode field
    header_bytes: int = 12            # B, estimated metadata; not deducted from logical payload

    @model_validator(mode="after")
    def validate_endpoint_roles(self) -> "Message":
        if self.src.node_type in (NodeType.GM_WDMA, NodeType.DDR_WDMA):
            raise ValueError(f"{self.src.node_type.name} cannot inject payload data")
        if self.dst.node_type in (NodeType.GM_RDMA, NodeType.DDR_RDMA):
            raise ValueError(f"{self.dst.node_type.name} cannot consume payload data")
        return self

    def flit_count(self, payload_capacity_bytes: int = 512) -> int:
        """Number of flits this message is packetized into."""
        return compute_flit_count(self.payload_bytes(), payload_capacity_bytes)

    def payload_bytes(self) -> int:
        """Total payload size in bytes."""
        return Slice(tensor_slice=self.data).size()

    def packetize(
        self,
        flit_config: "FlitConfig",
    ) -> list[Flit]:
        """Split this addressed message into payload-bearing flits."""
        if self.trans_type is not TransType.SINGLECAST:
            raise NotImplementedError(
                f"Phase 2 transport does not implement {self.trans_type.name}"
            )

        payload_bytes = self.payload_bytes()
        payload_capacity_bytes = flit_config.payload_capacity_bytes
        flit_count = compute_flit_count(payload_bytes, payload_capacity_bytes)
        remaining_bytes = payload_bytes
        flits: list[Flit] = []

        for flit_index in range(flit_count):
            if flit_count == 1:
                flit_type = FlitType.SINGLE
            elif flit_index == 0:
                flit_type = FlitType.HEAD
            elif flit_index == flit_count - 1:
                flit_type = FlitType.TAIL
            else:
                flit_type = FlitType.BODY

            flit_payload_bytes = min(remaining_bytes, payload_capacity_bytes)
            remaining_bytes -= flit_payload_bytes
            flits.append(
                Flit(
                    flit_type=flit_type,
                    payload_bytes=flit_payload_bytes,
                    msg_id=self.index,
                    dst_router=self.dst.router_id,
                    dst_local_port=self.dst.local_port,
                    src_router=self.src.router_id,
                    src_local_port=self.src.local_port,
                    is_broadcast=self.is_broadcast,
                    broadcast_dst_mask=self.broadcast_dst_mask,
                    reduce_op=self.reduce_op,
                    sync_mode=self.sync_mode,
                )
            )

        return flits

    def __lt__(self, other: "Message") -> bool:
        return self.payload_bytes() < other.payload_bytes()


class TraceItem(BaseModel):
    """Per-resource utilization trace entry for one time slice."""
    id: int            # resource ID
    slow: float        # slowdown factor
    ultilization: float  # utilization ratio [0,1]
    op_num: int        # number of operations in this slice
    node_type: int = NodeType.PE  # node type: PE/GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA


class TimeSlice(BaseModel):
    """Aggregated trace data for one time window."""
    cores: List[TraceItem] = []  # per-core utilization
    links: List[TraceItem] = []  # per-link utilization
    dmas: List[TraceItem] = []   # per-DMA utilization


class Trace(BaseModel):
    """Full simulation trace: list of time slices."""
    time_slices: List[TimeSlice] = []


class Event(BaseModel):
    """A single recorded event (message transfer or compute operation) for tracing."""
    type: OperatorType = OperatorType.SEND  # operator type
    index: int = -1        # message/task index
    start_time: int = -1   # cycles, event start time
    end_time: int = -1     # cycles, event end time
    pe_id: int = -1        # PE/core ID where event occurred
    src_id: int = -1       # source node ID
    dst_id: int = -1       # destination node ID
    flops: int = 0         # FLOPs consumed (for compute events)
    data_size: int = 0     # B, data transferred
    flit_count: int = 0    # number of flits in this message
    is_dma: bool = False   # whether this is a DMA (non-PE) event
    node_type: int = NodeType.PE  # source/destination node type
