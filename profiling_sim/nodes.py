"""Non-PE NoC nodes: DMA engines and AdaLink endpoints.

Node id allocation (global, chip-wide):
    PE        0..31
    GM_RDMA   32..35
    GM_WDMA   36..39
    DDR_RDMA  40..43
    DDR_WDMA  44..47
    ADALINK   48..65
"""
from enum import IntEnum
from typing import Dict, List, Optional, Tuple

import simpy

from .definitions import Message, AdaLinkOp
from .config import ShadowConfig
from .shadow import ShadowEntry, ShadowPipeline


class DataNocLocalId(IntEnum):
    PE = 0
    FABRIC_BRIDGE = 1
    DNOC2AXI = 2
    DDR_WDMA_CH0 = 3
    DDR_WDMA_CH1 = 4
    DDR_RDMA_LOCAL_SRAM = 5
    DDR_WDMA_LOCAL_SRAM = 6
    DDR_RDMA = 7
    DDR_WDMA_SINGLE = 8
    GM_WDMA_CH0 = 10
    GM_WDMA_CH1 = 11
    GM_RDMA_LOCAL_SRAM = 12
    GM_WDMA_LOCAL_SRAM = 13
    GM_RDMA = 14
    GM_WDMA_SINGLE = 15
    ADALINK_0 = 16
    ADALINK_1 = 17
    ADALINK_2 = 18
    ADALINK_3 = 19
    ADALINK_4 = 20
    ALL_ADALINK = 21
    MMU = 22


class NodeType(IntEnum):
    PE = 0
    GM_RDMA = 1
    GM_WDMA = 2
    DDR_RDMA = 3
    DDR_WDMA = 4
    ADALINK = 5


NODE_RANGES = {
    NodeType.PE: (0, 32),
    NodeType.GM_RDMA: (32, 4),
    NodeType.GM_WDMA: (36, 4),
    NodeType.DDR_RDMA: (40, 4),
    NodeType.DDR_WDMA: (44, 4),
    NodeType.ADALINK: (48, 18),
}

GM_RDMA_ROUTERS = [28, 29, 30, 31]
GM_WDMA_ROUTERS = [28, 29, 30, 31]
DDR_RDMA_ROUTERS = [0, 28, 3, 31]
DDR_WDMA_ROUTERS = [0, 28, 3, 31]

ADALINK_ATTACH = [
    (28, [16, 17, 18, 19, 20]),
    (29, [16, 17, 18, 19, 20]),
    (30, [16, 17, 18, 19]),
    (31, [16, 17, 18, 19]),
]


def node_type(global_id: int) -> NodeType:
    for nt, (base, count) in NODE_RANGES.items():
        if base <= global_id < base + count:
            return nt
    raise ValueError(f"unknown node id {global_id}")


def type_local_id(global_id: int) -> int:
    nt = node_type(global_id)
    return global_id - NODE_RANGES[nt][0]


def global_node_id(node_type: NodeType, local_id: int) -> int:
    base, count = NODE_RANGES[node_type]
    if not 0 <= local_id < count:
        raise ValueError(f"local id {local_id} out of range for {node_type.name}")
    return base + local_id


def is_pe(gid: int) -> bool:
    return node_type(gid) == NodeType.PE


def is_gm_rdma(gid: int) -> bool:
    return node_type(gid) == NodeType.GM_RDMA


def is_gm_wdma(gid: int) -> bool:
    return node_type(gid) == NodeType.GM_WDMA


def is_ddr_rdma(gid: int) -> bool:
    return node_type(gid) == NodeType.DDR_RDMA


def is_ddr_wdma(gid: int) -> bool:
    return node_type(gid) == NodeType.DDR_WDMA


def is_adalink(gid: int) -> bool:
    return node_type(gid) == NodeType.ADALINK


def route_pos(route_id: int) -> int:
    """Encode a router/position id into the packet header route_pos field."""
    return ((route_id // 4) << 3) | (route_id % 4)


def get_route_id(nt: NodeType, local_id: int) -> int:
    if nt == NodeType.GM_RDMA:
        return GM_RDMA_ROUTERS[local_id]
    if nt == NodeType.GM_WDMA:
        return GM_WDMA_ROUTERS[local_id]
    if nt == NodeType.DDR_RDMA:
        return DDR_RDMA_ROUTERS[local_id]
    if nt == NodeType.DDR_WDMA:
        return DDR_WDMA_ROUTERS[local_id]
    if nt == NodeType.PE:
        return local_id
    raise ValueError(f"no single route for {nt.name}")


def get_data_noc_local_id(nt: NodeType, channel: int = 0,
                          side: Optional[str] = None) -> int:
    if nt == NodeType.PE:
        return DataNocLocalId.PE
    if nt == NodeType.GM_RDMA:
        return DataNocLocalId.GM_RDMA
    if nt == NodeType.DDR_RDMA:
        return DataNocLocalId.DDR_RDMA
    if nt == NodeType.GM_WDMA:
        return ([DataNocLocalId.GM_WDMA_CH0,
                 DataNocLocalId.GM_WDMA_CH1])[channel]
    if nt == NodeType.DDR_WDMA:
        return ([DataNocLocalId.DDR_WDMA_CH0,
                 DataNocLocalId.DDR_WDMA_CH1])[channel]
    if nt == NodeType.ADALINK:
        return DataNocLocalId.ADALINK_0 + channel
    raise ValueError(f"no local id for {nt.name}")


def build_attachment(include_adalink: bool = True,
                     include_dma: bool = True
                     ) -> List[Tuple[NodeType, int, int, List[int]]]:
    """Return list of (node_type, local_id, router_id, ports).

    Ports is a list of DataNocLocalId values the node owns.  DMA nodes with
    two channels own both channel ports; AdaLink nodes own one port each.
    """
    att: List[Tuple[NodeType, int, int, List[int]]] = []
    if include_dma:
        for i, r in enumerate(GM_RDMA_ROUTERS):
            att.append((NodeType.GM_RDMA, i, r, [DataNocLocalId.GM_RDMA]))
        for i, r in enumerate(GM_WDMA_ROUTERS):
            att.append((NodeType.GM_WDMA, i, r,
                        [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1]))
        for i, r in enumerate(DDR_RDMA_ROUTERS):
            att.append((NodeType.DDR_RDMA, i, r, [DataNocLocalId.DDR_RDMA]))
        for i, r in enumerate(DDR_WDMA_ROUTERS):
            att.append((NodeType.DDR_WDMA, i, r,
                        [DataNocLocalId.DDR_WDMA_CH0, DataNocLocalId.DDR_WDMA_CH1]))
    if include_adalink:
        local_id = 0
        for router, ports in ADALINK_ATTACH:
            for p in ports:
                att.append((NodeType.ADALINK, local_id, router, [p]))
                local_id += 1
    return att


class NoCNode:
    """Base endpoint attached to one or more local ports of a router."""

    def __init__(self, env, node_id: int, nt: NodeType, router_id: int,
                 ports: List[int], noc):
        self.env = env
        self.id = node_id
        self.node_type = nt
        self.router_id = router_id
        self.ports = list(ports)
        self.noc = noc
        self.received: List[Tuple[int, Message]] = []
        self.events = []
        self.data_in: Dict[int, object] = {}
        self.data_out: Dict[int, object] = {}
        for p in self.ports:
            din, dout = noc.attach_local(router_id, p, node_id)
            self.data_in[p] = din
            self.data_out[p] = dout
            self.env.process(self._listen(p))

    def _listen(self, port: int):
        link = self.data_in[port]
        while True:
            msg = yield link.get()
            self.received.append((self.env.now, msg))
            self.events.append((self.env.now, port, msg))
            yield self.env.process(self.handle(port, msg))

    def handle(self, port: int, msg: Message):
        yield self.env.timeout(0)

    def send(self, port: int, msg: Message):
        return self.data_out[port].put(msg)


class AdaLinkNode(NoCNode):
    """Single-chip AdaLink stub and multi-chip egress/ingress endpoint."""

    def __init__(self, env, node_id, router_id, ports, noc, latency: int = 8,
                 shadow_cfg: Optional[ShadowConfig] = None,
                 chip_rank: int = 0, atomic_latency: int = 0):
        super().__init__(env, node_id, NodeType.ADALINK, router_id, ports, noc)
        self.latency = latency
        self.chip_rank = chip_rank
        self.atomic_latency = atomic_latency
        self.commids = self._make_commids()
        self._commid_events: Dict[Tuple[str, int], object] = {}
        self.shadow_cfg = shadow_cfg if shadow_cfg is not None else ShadowConfig()
        self._aci_func_pipeline = None
        self._aci_aiu_pipeline = None
        if self.shadow_cfg.enabled:
            d = self.shadow_cfg.occupancy
            self._aci_func_pipeline = ShadowPipeline(
                env, f"aci{node_id}.func",
                [int(ShadowEntry.ST_ACI_FUNC)], [self.shadow_cfg.aci_func],
                occupancy=d, ii=self.shadow_cfg.ii)
            self._aci_aiu_pipeline = ShadowPipeline(
                env, f"aci{node_id}.aiu",
                [int(ShadowEntry.ST_ACI_AIU_DOWNLOAD)], [self.shadow_cfg.aci_aiu],
                occupancy=d, ii=self.shadow_cfg.ii)
        self.interchip_in: Optional[simpy.Store] = None
        self.interchip_events: List[Tuple[int, Message]] = []
        self.atomic_events: List[dict] = []
        self._peer_link = None
        self._return_link = None
        self._remote_rank = None
        self._remote_port = None

    def bind_peer(self, link, return_link, remote_rank: int, remote_port: int):
        self._peer_link = link
        self._return_link = return_link
        self._remote_rank = remote_rank
        self._remote_port = remote_port
        self.interchip_in = simpy.Store(self.env)
        self.env.process(self._listen_interchip())

    @property
    def shadow_events(self):
        if not self.shadow_cfg.enabled:
            return []
        evts = []
        evts.extend(self._aci_func_pipeline.events)
        evts.extend(self._aci_aiu_pipeline.events)
        return evts

    @staticmethod
    def _make_commids():
        counters = ["PRODUCE", "SEND", "RECEIVE", "CREDIT", "RNIC_PRODUCE"]
        return {c: [0] * 64 for c in counters}

    def _pipeline_for(self, msg):
        if not self.shadow_cfg.enabled:
            return None
        return (self._aci_aiu_pipeline
                if getattr(msg, "is_aiu", False)
                else self._aci_func_pipeline)

    def handle(self, port, msg):
        broadcast = port == DataNocLocalId.ALL_ADALINK
        if self._peer_link is not None and (
                broadcast or msg.dst_rank != self.chip_rank):
            yield self.env.process(self._egress(msg))
            return
        self.commids["RECEIVE"][msg.index & 63] += 1
        pipeline = self._pipeline_for(msg)
        if pipeline is not None:
            slot = yield self.env.process(pipeline.acquire())
            try:
                yield self.env.timeout(self.latency)
            finally:
                yield self.env.process(pipeline.release(slot))
        else:
            yield self.env.timeout(self.latency)

    def _egress(self, msg):
        pipeline = self._pipeline_for(msg)
        slot = None
        if pipeline is not None:
            slot = yield self.env.process(pipeline.acquire())
        try:
            if self.latency:
                yield self.env.timeout(self.latency)
            self.commids["SEND"][msg.index & 63] += 1
            yield self.env.process(self._peer_link.send(msg))
        finally:
            if slot is not None:
                yield self.env.process(pipeline.release(slot))

    def _listen_interchip(self):
        while True:
            msg = yield self.interchip_in.get()
            self.received.append((self.env.now, msg))
            self.interchip_events.append((self.env.now, msg))
            self.commids["RECEIVE"][msg.index & 63] += 1
            pipeline = self._pipeline_for(msg)
            slot = None
            if pipeline is not None:
                slot = yield self.env.process(pipeline.acquire())
            try:
                if self.latency:
                    yield self.env.timeout(self.latency)
                if not msg.is_aiu:
                    op = AdaLinkOp(msg.adalink_op)
                    if op.is_atomic and self.atomic_latency:
                        self.atomic_events.append({
                            "time": self.env.now,
                            "op": int(op), "value": msg.value,
                            "index": msg.index, "src_rank": msg.dst_rank,
                        })
                        yield self.env.timeout(self.atomic_latency)
                    out = self.data_out[self.ports[0]]
                    yield out.put(msg)
            finally:
                if slot is not None:
                    yield self.env.process(pipeline.release(slot))
                if self._return_link is not None:
                    self._return_link.return_credit()

    def release_comm_id(self, comm_id: int, counter_type: str = "PRODUCE"):
        cid = comm_id & 63
        self.commids[counter_type][cid] += 1
        ev = self._commid_events.get((counter_type, cid))
        if ev is not None and not ev.triggered:
            ev.succeed()

    def acquire_comm_id(self, comm_id: int, counter_type: str = "CREDIT"):
        self.commids[counter_type][comm_id & 63] += 1
        return self.commids[counter_type][comm_id & 63]

    def wait_comm_id(self, comm_id: int, counter_type: str = "CREDIT",
                     expected: int = 1):
        cid = comm_id & 63
        key = (counter_type, cid)
        while self.commids[counter_type][cid] < expected:
            ev = self._commid_events.get(key)
            if ev is None or ev.triggered:
                ev = self.env.event()
                self._commid_events[key] = ev
            yield ev


def attach_nodes(env, noc, include_dma: bool = True,
                 include_adalink: bool = True,
                 include_all_adalink_port: bool = True,
                 adalink_latency: int = 8,
                 shadow_cfg: Optional[ShadowConfig] = None,
                 rank: int = 0, atomic_latency: int = 0):
    """Build all non-PE nodes from the attachment table.

    Returns dict global_id -> NoCNode.
    """
    nodes: Dict[int, NoCNode] = {}
    by_router: Dict[int, List["AdaLinkNode"]] = {}
    for nt, local_id, router_id, ports in build_attachment(
            include_adalink, include_dma):
        gid = global_node_id(nt, local_id)
        if gid in nodes:
            raise RuntimeError(f"duplicate node id {gid}")
        if nt == NodeType.ADALINK:
            node = AdaLinkNode(env, gid, router_id, ports, noc,
                               latency=adalink_latency,
                               shadow_cfg=shadow_cfg,
                               chip_rank=rank,
                               atomic_latency=atomic_latency)
            by_router.setdefault(router_id, []).append(node)
        else:
            node = NoCNode(env, gid, nt, router_id, ports, noc)
        nodes[gid] = node

    if include_adalink and include_all_adalink_port:
        for router_id, adalinks in by_router.items():
            din, _dout = noc.attach_local(
                router_id, DataNocLocalId.ALL_ADALINK, node_id=-1)

            def fanout(link=din, targets=adalinks):
                while True:
                    msg = yield link.get()
                    for t in targets:
                        t.received.append((env.now, msg))
                        t.events.append((env.now, DataNocLocalId.ALL_ADALINK, msg))
                        env.process(t.handle(DataNocLocalId.ALL_ADALINK,
                                             msg.model_copy()))
            env.process(fanout())

    return nodes
