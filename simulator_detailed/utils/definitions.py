from __future__ import annotations

from enum import Enum, IntEnum, auto
from itertools import pairwise
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FlitConfig(BaseModel):
    """Synthetic packet defaults; deployments supply their own format."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    physical_flit_bytes: int = Field(default=16, gt=0, strict=True)
    payload_capacity_bytes: int = Field(default=16, gt=0, strict=True)
    header_bytes: int = Field(default=1, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_capacity(self) -> FlitConfig:
        if self.payload_capacity_bytes > self.physical_flit_bytes:
            raise ValueError("payload capacity cannot exceed physical flit size")
        if self.header_bytes > self.physical_flit_bytes:
            raise ValueError("header cannot exceed physical flit size")
        return self


class OperatorType(Enum):
    """Supported operator types in the DFG"""

    LOAD_FEAT = auto()  # Load feature map
    LOAD_WGT = auto()  # Load weight
    CONV = auto()  # Convolution
    POOL = auto()  # Pooling
    FC = auto()  # Fully connected
    STORE = auto()  # Store output
    SEND = auto()  # Send data to another core
    RECV = auto()  # Receive data from another core


comp_operator = [OperatorType.CONV, OperatorType.POOL, OperatorType.FC]
comm_operator = [OperatorType.SEND, OperatorType.RECV]
io_operator = [OperatorType.LOAD_FEAT, OperatorType.LOAD_WGT, OperatorType.STORE]


class Direction(IntEnum):
    NORTH = 0
    SOUTH = 1
    EAST = 2
    WEST = 3


class NodeType(IntEnum):
    """Type of NoC endpoint node."""

    PE = 0  # Processing Element (compute core)
    GM_RDMA = 1  # Global Memory read DMA (data source: GM -> NoC)
    GM_WDMA = 2  # Global Memory write DMA (data sink: NoC -> GM)
    DDR_RDMA = 3  # DDR read DMA (data source: DDR -> NoC)
    DDR_WDMA = 4  # DDR write DMA (data sink: NoC -> DDR)


DMA_RDMA_NODE_TYPES: Final[frozenset[NodeType]] = frozenset(
    (NodeType.GM_RDMA, NodeType.DDR_RDMA)
)
DMA_WDMA_NODE_TYPES: Final[frozenset[NodeType]] = frozenset(
    (NodeType.GM_WDMA, NodeType.DDR_WDMA)
)
DDR_DMA_NODE_TYPES: Final[frozenset[NodeType]] = frozenset(
    (NodeType.DDR_RDMA, NodeType.DDR_WDMA)
)


class FlitType(IntEnum):
    """Flit type in wormhole packetization."""

    SINGLE = 0  # Single-flit packet: establishes and releases the path
    HEAD = 1  # First flit of a multi-flit packet
    BODY = 2  # Interior flit following an established path
    TAIL = 3  # Final flit releasing the path


class NoCChannel(IntEnum):
    """PE NMC channel and its corresponding physical NoC fabric."""

    CH0 = 0
    CH1 = 1

    @classmethod
    def _missing_(cls, value: object) -> NoCChannel | None:
        # JSON object keys are strings, including per-fabric configuration keys.
        if isinstance(value, str) and value.isdecimal():
            return cls(int(value))
        if type(value) is not int or value < 0:
            return None
        existing = cls._value2member_map_.get(value)
        if isinstance(existing, cls):
            return existing
        member = int.__new__(cls, value)
        member._name_ = f"CH{value}"
        member._value_ = value
        cls._value2member_map_[value] = member
        return member


class NMCShapeMode(Enum):
    """NMC endpoint setup path selected by transfer-shape construction."""

    STATIC = "static"
    DYNAMIC = "dynamic"


class NoCPlane(Enum):
    """Physical NoC plane; values are labels, not hardware encodings."""

    DATA = "data"
    SYNC = "sync"
    CFG = "cfg"


class DMAAttachmentMode(Enum):
    """Physical attachment mode selected for a DMA endpoint."""

    DUAL_SIDE = "dual_side"
    SINGLE_SIDE = "single_side"
    LOCAL = "local"


class DMACommandMode(Enum):
    """Software command protocol for a transfer involving a DMA endpoint."""

    DUAL_SIDE = "dual_side"
    SINGLE_SIDE = "single_side"


class FlitTrafficType(Enum):
    """Protocol role of a flit carried by a data NoC fabric."""

    PAYLOAD = "payload"
    DMA_REQUEST = "dma_request"
    DMA_RESPONSE = "dma_response"


class TransType(IntEnum):
    """Simulator transmission modes, without a hardware wire encoding."""

    SINGLECAST = 0  # Normal unicast to one destination (XY routing)
    FIXPATH = 1  # Source-specified fixed path
    MULTICAST = 2  # Multicast to a subset of destinations (in-router fan-out)
    BROADCAST = 3  # Broadcast to all PEs (multicast with full mask)


FixedPath = Annotated[
    tuple[Annotated[int, Field(strict=True, ge=0)], ...],
    Field(min_length=1),
]


def _validate_fixed_path(
    trans_type: TransType,
    fixed_path: FixedPath | None,
    src_router: int,
    dst_router: int,
    mesh_x: int,
    mesh_y: int,
) -> None:
    """Validate a simple path against the configured mesh geometry."""
    if trans_type is not TransType.FIXPATH:
        if fixed_path is not None:
            raise ValueError("fixed_path is valid only for FIXPATH transfers")
        return
    if not fixed_path:
        raise ValueError("FIXPATH requires an explicit nonempty fixed_path")
    if fixed_path[0] != src_router or fixed_path[-1] != dst_router:
        raise ValueError(
            "fixed_path must start at the source and end at the destination"
        )
    if len(set(fixed_path)) != len(fixed_path):
        raise ValueError("fixed_path must not repeat routers")
    if any(router >= mesh_x * mesh_y for router in fixed_path):
        raise ValueError("fixed_path router is outside the configured mesh")
    for src, dst in pairwise(fixed_path):
        src_y, src_x = divmod(src, mesh_x)
        dst_y, dst_x = divmod(dst, mesh_x)
        if abs(src_x - dst_x) + abs(src_y - dst_y) != 1:
            raise ValueError(
                f"fixed_path hop {src}->{dst} does not join mesh neighbors"
            )


def _require_singlecast(trans_type: TransType) -> None:
    if trans_type is not TransType.SINGLECAST:
        raise NotImplementedError(
            f"transport supports SINGLECAST only; does not implement {trans_type.name}"
        )


class BurstLenMode(IntEnum):
    """Arbitration quanta in flits; zero selects the configured default."""

    DEFAULT = 0
    FLITS_1 = 1
    FLITS_2 = 2
    FLITS_4 = 4
    FLITS_8 = 8

    @classmethod
    def _missing_(cls, value: object) -> BurstLenMode | None:
        if type(value) is not int or value <= 0:
            return None
        existing = cls._value2member_map_.get(value)
        if isinstance(existing, cls):
            return existing
        member = int.__new__(cls, value)
        member._name_ = f"FLITS_{value}"
        member._value_ = value
        cls._value2member_map_[value] = member
        return member

    def explicit_quantum_flits(self) -> int:
        if self is BurstLenMode.DEFAULT:
            raise ValueError("DEFAULT requires architecture resolution")
        return int(self.value)


# Negative IDs form an internal namespace separate from configurable local ports.
DIR_NORTH = -1  # direction port: North neighbor (out port = +Y)
DIR_SOUTH = -2  # direction port: South neighbor (out port = -Y)
DIR_EAST = -3  # direction port: East neighbor (out port = +X)
DIR_WEST = -4  # direction port: West neighbor (out port = -X)

_DIR_MAP = {
    Direction.NORTH: DIR_NORTH,
    Direction.SOUTH: DIR_SOUTH,
    Direction.EAST: DIR_EAST,
    Direction.WEST: DIR_WEST,
}


def direction_to_port(direction: Direction) -> int:
    """Map a Direction enum to its numeric port ID (negative IDs)."""
    return _DIR_MAP[direction]


def port_to_direction(port: int) -> Direction | None:
    """Map a numeric direction port ID back to Direction enum, or None if not a direction port."""
    for d, p in _DIR_MAP.items():
        if p == port:
            return d
    return None


def is_direction_port(port: int) -> bool:
    """Check if a port ID is a direction (inter-router) port (negative IDs)."""
    return port in _DIR_MAP.values()


def is_local_port(port: int) -> bool:
    """Check if a port ID is a local endpoint port (nonnegative IDs)."""
    return port >= 0


def compute_flit_count(payload_bytes: int, payload_capacity_bytes: int) -> int:
    """Round payload size up to configured capacity; control uses one flit."""
    if payload_bytes < 0 or payload_capacity_bytes <= 0:
        raise ValueError("payload must be nonnegative and capacity positive")
    return max(
        1, (payload_bytes + payload_capacity_bytes - 1) // payload_capacity_bytes
    )


class DimSlice(BaseModel):
    start: int
    end: int


class Slice(BaseModel):
    tensor_slice: list[DimSlice]

    def size(self) -> int:
        if self.tensor_slice == []:
            return 0

        res = 1
        for dim_slice in self.tensor_slice:
            dim_len = max(0, dim_slice.end - dim_slice.start)
            res = res * dim_len
        return res

    def max(self, other: Slice) -> Slice:
        res: list[DimSlice] = []
        for i in range(len(self.tensor_slice)):
            res.append(
                DimSlice(
                    start=min(self.tensor_slice[i].start, other.tensor_slice[i].start),
                    end=max(self.tensor_slice[i].end, other.tensor_slice[i].end),
                )
            )
        return Slice(tensor_slice=res)


class Flit(BaseModel):
    """A single flit (flow control digit) in the wormhole NoC."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: FlitConfig = Field(default_factory=FlitConfig)
    mesh_x: int = Field(default=3, gt=0)
    mesh_y: int = Field(default=2, gt=0)
    flit_type: FlitType  # HEAD/BODY/TAIL
    payload_bytes: int = Field(ge=0)  # B, actual payload carried
    msg_id: int  # message index for reordering/correlation
    fabric_id: NoCChannel  # physical NoC fabric carrying this flit
    dst_router: int  # destination router ID
    dst_local_port: int = 0  # destination local port on the dst router
    src_router: int  # source router ID
    src_local_port: int = 0  # source local port on the src router
    trans_type: TransType = TransType.SINGLECAST
    fixed_path: FixedPath | None = None
    is_broadcast: bool = False  # whether this is a broadcast flit (in-router fan-out)
    broadcast_dst_mask: int = 0  # bitmask of destination PE/router IDs for broadcast
    reduce_op: int = -1  # reduce operation type (-1 = none, >=0 = reduce mode)
    sync_mode: int = (
        0  # sync mode: 0=normal,1=bcast-incl-self,2=reduce-intermediate,3=reduce-last
    )
    burst_len_mode: BurstLenMode = BurstLenMode.DEFAULT
    traffic_type: FlitTrafficType = FlitTrafficType.PAYLOAD
    dma_header_bytes: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def validate_routing(self) -> Flit:
        if self.payload_bytes > self.format.payload_capacity_bytes:
            raise ValueError("flit payload exceeds configured capacity")
        if self.dma_header_bytes > self.format.physical_flit_bytes:
            raise ValueError("flit header exceeds configured size")
        _validate_fixed_path(
            self.trans_type,
            self.fixed_path,
            self.src_router,
            self.dst_router,
            self.mesh_x,
            self.mesh_y,
        )
        return self

    def validate_transport(self) -> None:
        """Reject unsupported routing before a link or router acquires resources."""
        _validate_fixed_path(
            self.trans_type,
            self.fixed_path,
            self.src_router,
            self.dst_router,
            self.mesh_x,
            self.mesh_y,
        )
        _require_singlecast(self.trans_type)

    @property
    def is_head(self) -> bool:
        return self.flit_type in (FlitType.SINGLE, FlitType.HEAD)

    @property
    def is_tail(self) -> bool:
        return self.flit_type in (FlitType.SINGLE, FlitType.TAIL)

    @property
    def transfer_bytes(self) -> int:
        """Configured physical transfer cost, including padding of partial flits."""
        return self.format.physical_flit_bytes


class EndpointAddress(BaseModel):
    """Resolved, immutable attachment of one type-qualified NoC endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_type: NodeType
    node_id: int = Field(ge=0)
    fabric_id: NoCChannel
    router_id: int = Field(ge=0)
    local_port: int = Field(ge=0)
    attachment_mode: DMAAttachmentMode | None = None
    format: FlitConfig = Field(default_factory=FlitConfig)
    mesh_x: int = Field(default=3, gt=0)
    mesh_y: int = Field(default=2, gt=0)

    @model_validator(mode="after")
    def validate_attachment(self) -> EndpointAddress:
        if self.router_id >= self.mesh_x * self.mesh_y:
            raise ValueError("endpoint router is outside the configured mesh")
        if (self.node_type is NodeType.PE) != (self.attachment_mode is None):
            raise ValueError("DMA endpoints require an explicit attachment mode")
        return self


class Message(BaseModel):
    """A message to be injected into the NoC, packetized into flits."""

    model_config = ConfigDict(extra="forbid")

    src: EndpointAddress  # resolved source endpoint attachment
    dst: EndpointAddress  # resolved destination endpoint attachment
    index: int  # unique message index (DFG task index)
    data: list[DimSlice]  # tensor slice(s) describing payload
    nmc_shape_mode: NMCShapeMode = NMCShapeMode.DYNAMIC
    dma_command_mode: DMACommandMode | None = None
    trans_type: TransType = TransType.SINGLECAST  # transmission type
    fixed_path: FixedPath | None = Field(default=None, frozen=True)
    burst_len_mode: BurstLenMode = BurstLenMode.DEFAULT
    is_broadcast: bool = False  # whether this is a broadcast message
    broadcast_dst_mask: int = 0  # destination bitmask for broadcast/multicast
    reduce_op: int = -1  # reduce operation (-1 = none)
    sync_mode: int = 0  # sync mode field

    @property
    def header_bytes(self) -> int:
        return self.src.format.header_bytes

    @property
    def format(self) -> FlitConfig:
        return self.src.format

    @model_validator(mode="after")
    def validate_endpoint_roles(self) -> Message:
        if (self.src.format, self.src.mesh_x, self.src.mesh_y) != (
            self.dst.format,
            self.dst.mesh_x,
            self.dst.mesh_y,
        ):
            raise ValueError("message endpoints must share transport configuration")
        if self.src.node_type in DMA_WDMA_NODE_TYPES:
            raise ValueError(f"{self.src.node_type.name} cannot inject payload data")
        if self.dst.node_type in DMA_RDMA_NODE_TYPES:
            raise ValueError(f"{self.dst.node_type.name} cannot consume payload data")
        if self.src.fabric_id is not self.dst.fabric_id:
            raise ValueError("message endpoints must use the same NoC fabric")
        involves_dma = (
            self.src.node_type is not NodeType.PE
            or self.dst.node_type is not NodeType.PE
        )
        if involves_dma and self.dma_command_mode is None:
            raise ValueError(
                "transfers involving DMA endpoints require dma_command_mode"
            )
        if not involves_dma and self.dma_command_mode is not None:
            raise ValueError(
                "dma_command_mode is valid only for transfers involving DMA endpoints"
            )
        dma_endpoints = tuple(
            endpoint
            for endpoint in (self.src, self.dst)
            if endpoint.node_type is not NodeType.PE
        )
        if self.dma_command_mode is DMACommandMode.SINGLE_SIDE:
            if self.header_bytes == 0:
                raise ValueError("single-side commands require a nonzero header")
            if (
                self.src.node_type is not NodeType.PE
                and self.dst.node_type is not NodeType.PE
            ):
                raise ValueError("single-side commands require one PE endpoint")
            if any(
                endpoint.attachment_mode
                not in (
                    DMAAttachmentMode.SINGLE_SIDE,
                    DMAAttachmentMode.LOCAL,
                )
                for endpoint in dma_endpoints
            ):
                raise ValueError(
                    "single-side commands require a single-side DMA attachment"
                )
        if self.dma_command_mode is DMACommandMode.DUAL_SIDE and any(
            endpoint.node_type in DMA_WDMA_NODE_TYPES
            and endpoint.attachment_mode is not DMAAttachmentMode.DUAL_SIDE
            for endpoint in dma_endpoints
        ):
            raise ValueError("dual-side commands require a dual-side WDMA attachment")
        return self

    @model_validator(mode="after")
    def validate_routing(self) -> Message:
        _validate_fixed_path(
            self.trans_type,
            self.fixed_path,
            self.src.router_id,
            self.dst.router_id,
            self.src.mesh_x,
            self.src.mesh_y,
        )
        return self

    def flit_count(self) -> int:
        """Number of flits this message is packetized into."""
        return compute_flit_count(
            self.payload_bytes(), self.format.payload_capacity_bytes
        )

    def payload_bytes(self) -> int:
        """Total payload size in bytes."""
        return Slice(tensor_slice=self.data).size()

    @staticmethod
    def _flit_type_at(flit_index: int, flit_count: int) -> FlitType:
        if flit_count == 1:
            return FlitType.SINGLE
        if flit_index == 0:
            return FlitType.HEAD
        if flit_index == flit_count - 1:
            return FlitType.TAIL
        return FlitType.BODY

    def validate_transport(self) -> None:
        """Check the common packetization and PE/DMA command admission boundary."""
        _validate_fixed_path(
            self.trans_type,
            self.fixed_path,
            self.src.router_id,
            self.dst.router_id,
            self.src.mesh_x,
            self.src.mesh_y,
        )
        _require_singlecast(self.trans_type)
        if (
            self.src.attachment_mode is DMAAttachmentMode.LOCAL
            or self.dst.attachment_mode is DMAAttachmentMode.LOCAL
        ):
            raise NotImplementedError(
                "Phase 2 transport does not implement local-memory DMA endpoints"
            )

    def packetize(self) -> list[Flit]:
        """Split this addressed message into configured flits."""
        self.validate_transport()
        payload_bytes = self.payload_bytes()
        flit_count = compute_flit_count(
            payload_bytes, self.format.payload_capacity_bytes
        )
        remaining_bytes = payload_bytes
        flits: list[Flit] = []

        for flit_index in range(flit_count):
            flit_payload_bytes = min(
                remaining_bytes, self.format.payload_capacity_bytes
            )
            remaining_bytes -= flit_payload_bytes
            flits.append(
                Flit(
                    format=self.format,
                    mesh_x=self.src.mesh_x,
                    mesh_y=self.src.mesh_y,
                    flit_type=self._flit_type_at(flit_index, flit_count),
                    payload_bytes=flit_payload_bytes,
                    msg_id=self.index,
                    fabric_id=self.src.fabric_id,
                    dst_router=self.dst.router_id,
                    dst_local_port=self.dst.local_port,
                    src_router=self.src.router_id,
                    src_local_port=self.src.local_port,
                    trans_type=self.trans_type,
                    fixed_path=self.fixed_path,
                    is_broadcast=self.is_broadcast,
                    broadcast_dst_mask=self.broadcast_dst_mask,
                    reduce_op=self.reduce_op,
                    sync_mode=self.sync_mode,
                    burst_len_mode=self.burst_len_mode,
                    traffic_type=FlitTrafficType.PAYLOAD,
                    dma_header_bytes=(
                        self.header_bytes
                        if flit_index == 0
                        and self.dma_command_mode is DMACommandMode.SINGLE_SIDE
                        else 0
                    ),
                )
            )

        return flits

    def validate_flit(
        self,
        flit: Flit,
        flit_index: int,
        flit_count: int,
    ) -> None:
        """Validate one received flit against this message's wire identity."""
        expected_payload_bytes = min(
            self.format.payload_capacity_bytes,
            max(
                0,
                self.payload_bytes() - flit_index * self.format.payload_capacity_bytes,
            ),
        )
        if (
            flit.format != self.format
            or flit.flit_type is not self._flit_type_at(flit_index, flit_count)
            or flit.payload_bytes != expected_payload_bytes
            or flit.msg_id != self.index
            or flit.fabric_id is not self.src.fabric_id
            or flit.src_router != self.src.router_id
            or flit.src_local_port != self.src.local_port
            or flit.dst_router != self.dst.router_id
            or flit.dst_local_port != self.dst.local_port
            or flit.trans_type is not self.trans_type
            or flit.fixed_path != self.fixed_path
            or flit.is_broadcast != self.is_broadcast
            or flit.broadcast_dst_mask != self.broadcast_dst_mask
            or flit.reduce_op != self.reduce_op
            or flit.sync_mode != self.sync_mode
            or flit.burst_len_mode is not self.burst_len_mode
            or flit.traffic_type is not FlitTrafficType.PAYLOAD
            or flit.dma_header_bytes
            != (
                self.header_bytes
                if flit_index == 0
                and self.dma_command_mode is DMACommandMode.SINGLE_SIDE
                else 0
            )
        ):
            raise RuntimeError(
                f"message {self.index} received an invalid flit at index {flit_index}"
            )

    def __lt__(self, other: Message) -> bool:
        return self.payload_bytes() < other.payload_bytes()


class TraceItem(BaseModel):
    """Per-resource utilization trace entry for one time slice."""

    id: int  # resource ID
    slow: float  # slowdown factor
    ultilization: float  # utilization ratio [0,1]
    op_num: int  # number of operations in this slice
    node_type: int = NodeType.PE  # node type: PE/GM_RDMA/GM_WDMA/DDR_RDMA/DDR_WDMA
    fabric_id: NoCChannel | None = None


class TimeSlice(BaseModel):
    """Aggregated trace data for one time window."""

    cores: list[TraceItem] = []  # per-core utilization
    links: list[TraceItem] = []  # per-link utilization
    dmas: list[TraceItem] = []  # per-DMA utilization


class Trace(BaseModel):
    """Full simulation trace: list of time slices."""

    time_slices: list[TimeSlice] = []


class Event(BaseModel):
    """A single recorded event (message transfer or compute operation) for tracing."""

    type: OperatorType = OperatorType.SEND  # operator type
    index: int = -1  # message/task index
    start_time: float = -1.0  # ACI cycles, event start time
    end_time: float = -1.0  # ACI cycles, event end time
    pe_id: int = -1  # PE/core ID where event occurred
    src_id: int = -1  # source node ID
    dst_id: int = -1  # destination node ID
    flops: int = 0  # FLOPs consumed (for compute events)
    data_size: int = 0  # B, data transferred
    flit_count: int = 0  # number of flits in this message
    is_dma: bool = False  # whether this is a DMA (non-PE) event
    node_type: int = NodeType.PE  # source/destination node type
    fabric_id: NoCChannel = NoCChannel.CH0
