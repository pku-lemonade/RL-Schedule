from enum import Enum, auto
from enum import IntEnum
import math
from typing import List, Optional
from pydantic import BaseModel


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


class TransType(IntEnum):
    """Transmission type for a message."""
    SINGLECAST = 0  # Normal unicast to one destination (XY routing)
    MULTICAST  = 1  # Multicast to a subset of destinations (in-router fan-out)
    BROADCAST  = 2  # Broadcast to all PEs (multicast with full mask)


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


def compute_flit_count(payload_bytes: int, header_bytes: int = 12,
                       body_overhead: int = 4, flit_size: int = 512) -> int:
    """Compute number of flits needed for a given payload size.
    Single-flit (payload <= flit_size - header_bytes): 1 flit (header+payload inline).
    Multi-flit: 1 HEAD + N payload flits (last is TAIL which tears down the reservation).
    Each BODY/TAIL flit carries (flit_size - body_overhead) bytes of payload.
    """
    head_payload = flit_size - header_bytes
    if payload_bytes <= head_payload:
        return 1
    remaining = payload_bytes - head_payload
    body_payload = flit_size - body_overhead
    tail_plus_body = ceil(remaining, body_payload)
    return 1 + tail_plus_body


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


class Message(BaseModel):
    """A message to be injected into the NoC, packetized into flits."""
    src: int                          # source node ID (PE or DMA)
    dst: int                          # destination node ID (PE or DMA)
    index: int                        # unique message index (DFG task index)
    data: List[DimSlice]              # tensor slice(s) describing payload
    src_type: NodeType = NodeType.PE  # source node type
    dst_type: NodeType = NodeType.PE  # destination node type
    src_local_port: int = PORT_PE     # source local port on source router
    dst_local_port: int = PORT_PE     # destination local port on destination router
    trans_type: TransType = TransType.SINGLECAST  # transmission type
    is_broadcast: bool = False        # whether this is a broadcast message
    broadcast_dst_mask: int = 0       # destination bitmask for broadcast/multicast
    reduce_op: int = -1               # reduce operation (-1 = none)
    sync_mode: int = 0                # sync mode field
    header_bytes: int = 12            # B, routing header overhead

    def flit_count(self, flit_size: int = 512, body_overhead: int = 4) -> int:
        """Number of flits this message is packetized into."""
        return compute_flit_count(self.payload_bytes(), self.header_bytes, body_overhead, flit_size)

    def payload_bytes(self) -> int:
        """Total payload size in bytes."""
        return Slice(tensor_slice=self.data).size()

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
