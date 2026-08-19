"""GM / DDR memory nodes with per-engine channels and aggregate bandwidth."""
import math
from typing import Dict, List, Optional

import simpy

from .definitions import Message, DimSlice, TransType, TransferMode
from .config import MemoryConfig, ShadowConfig
from .nodes import (
    NoCNode, NodeType, DataNocLocalId, attach_nodes,
)
from .shadow import (
    ShadowEntry, ShadowPipeline, mdma_channel_entry,
)


class Memory:
    """Shared memory (GM or DDR) with capacity and aggregate bandwidth.

    Aggregate bandwidth is modelled as *lanes*: the memory supports
    ``aggregate_bw // engine_width`` concurrent engine-width transfers.
    """

    def __init__(self, env, name: str, capacity: int, aggregate_bw: float):
        self.env = env
        self.name = name
        self.capacity = capacity
        self.used = 0
        self.atomic: Dict[int, int] = {}
        self.aggregate_bw = float(aggregate_bw)
        self._lane_resources: Dict[float, simpy.Resource] = {}
        self.events = []

    def lane_resource(self, engine_width: float) -> simpy.Resource:
        key = round(float(engine_width), 6)
        if key not in self._lane_resources:
            if self.aggregate_bw <= 0:
                cap = 10 ** 6
            else:
                cap = max(1, int(round(self.aggregate_bw / key)))
            self._lane_resources[key] = simpy.Resource(self.env, capacity=cap)
        return self._lane_resources[key]

    def allocate(self, n_bytes: int):
        if self.used + n_bytes > self.capacity:
            raise MemoryError(
                f"{self.name} out of capacity: "
                f"{self.used}+{n_bytes} > {self.capacity}")
        self.used += n_bytes

    def write(self, addr: int, value: int, write_sum: int = 0):
        if write_sum == 1:
            self.atomic[addr] = self.atomic.get(addr, 0) + value
        elif write_sum == 2:
            self.atomic[addr] = max(self.atomic.get(addr, 0), value)
        else:
            self.atomic[addr] = value

    def read(self, addr: int) -> int:
        return self.atomic.get(addr, 0)


class DMANode(NoCNode):
    """DMA engine node (RDMA=read / WDMA=write) attached to a memory."""

    def __init__(self, env, node_id, nt, router_id, ports, noc,
                 memory: Memory, engine_width: float,
                 channels: int = 1, is_read: bool = True,
                 aiu_port: Optional[int] = None,
                 aiu_sram_size: int = 256 * 1024,
                 shadow_cfg: Optional[ShadowConfig] = None,
                 dispatch_interval: int = 0):
        super().__init__(env, node_id, nt, router_id, ports, noc)
        if aiu_port is None:
            aiu_port = _aiu_port_for(nt)
        self.memory = memory
        self.engine_width = float(engine_width)
        agg = memory.aggregate_bw
        if agg > 0:
            self.effective_width = min(self.engine_width, agg)
        else:
            self.effective_width = self.engine_width
        self.is_read = is_read
        self.channel_store = simpy.Store(env, capacity=channels)
        for i in range(channels):
            self.channel_store.put(i)
        self.dispatch_interval = dispatch_interval
        self.dispatch = simpy.Resource(env, capacity=1)
        self.aiu_port = aiu_port
        self.aiu_sram_size = aiu_sram_size
        self.aiu_used = 0
        self.aiu_events = []
        self.shadow_cfg = shadow_cfg if shadow_cfg is not None else ShadowConfig()
        self._mdma_pipelines = {}
        self._mdma_aiu_pipeline = None
        if self.shadow_cfg.enabled:
            d = self.shadow_cfg.occupancy
            for ch in range(channels):
                entry = mdma_channel_entry(ch)
                self._mdma_pipelines[ch] = ShadowPipeline(
                    env, f"dma{node_id}.ch{ch}", [int(entry)],
                    [self.shadow_cfg.mdma_channel],
                    occupancy=d, ii=self.shadow_cfg.ii)
            self._mdma_aiu_pipeline = ShadowPipeline(
                env, f"dma{node_id}.aiu",
                [int(ShadowEntry.ST_MDMA_AIU_DOWNLOAD)],
                [self.shadow_cfg.mdma_aiu],
                occupancy=d, ii=self.shadow_cfg.ii)
        if aiu_port is not None:
            self._attach_aiu(aiu_port)

    @property
    def shadow_events(self):
        if not self.shadow_cfg.enabled:
            return []
        evts = []
        for p in self._mdma_pipelines.values():
            evts.extend(p.events)
        if self._mdma_aiu_pipeline is not None:
            evts.extend(self._mdma_aiu_pipeline.events)
        return evts

    def _attach_aiu(self, port):
        din, dout = self.noc.attach_local(self.router_id, port, node_id=self.id)
        self.aiu_in = din
        self.data_in[port] = din
        self.data_out[port] = dout
        self.env.process(self._listen_aiu(port))

    def _listen_aiu(self, port):
        while True:
            msg = yield self.aiu_in.get()
            self.received.append((self.env.now, msg))
            n = msg.byte_size()
            if n % 16 != 0:
                raise ValueError(
                    f"AIU download must be 16B aligned, got {n}B")
            if self.aiu_used + n > self.aiu_sram_size:
                raise MemoryError("AIU SRAM capacity exceeded")
            if self._mdma_aiu_pipeline is not None:
                slot = yield self.env.process(
                    self._mdma_aiu_pipeline.acquire())
            else:
                slot = None
            try:
                self.aiu_used += n
                self.aiu_events.append((self.env.now, n, msg))
                scalar_sync = msg.model_copy(update={
                    'dst': 8, 'src': self.id,
                    'data': [DimSlice(start=0, end=16)],
                    'element_bytes': 1, 'header_bytes': 0,
                    'transfer_mode': TransferMode.DUAL_SIDE,
                    'is_control': True, 'sync': True, 'dst_mask': 0,
                    'trans_type': TransType.SINGLECAST,
                    'dst_local_port': 0, 'is_aiu': False,
                    'layout': None,
                })
                self.data_out[port].put(scalar_sync)
            finally:
                if slot is not None:
                    yield self.env.process(
                        self._mdma_aiu_pipeline.release(slot))

    def transfer(self, n_bytes: int, addr: int = 0, value: int = 0,
                 write_sum: int = 0):
        if self.dispatch_interval:
            with self.dispatch.request() as dreq:
                yield dreq
                yield self.env.timeout(self.dispatch_interval)
        ch = yield self.channel_store.get()
        start = self.env.now
        if not self.is_read:
            self.memory.allocate(n_bytes)
            self.memory.write(addr, value, write_sum)
        engine_time = (math.ceil(n_bytes / self.effective_width)
                       if self.effective_width > 0 else 0)
        lane = self.memory.lane_resource(self.effective_width)
        pipeline = self._mdma_pipelines.get(ch)
        if pipeline is not None:
            slot = yield self.env.process(pipeline.acquire())
        else:
            slot = None
        try:
            with lane.request() as req:
                yield req
                yield self.env.timeout(engine_time)
        finally:
            if slot is not None and pipeline is not None:
                yield self.env.process(pipeline.release(slot))
            yield self.channel_store.put(ch)
        self.memory.events.append((self.id, start, self.env.now, n_bytes))

    def handle(self, port, msg: Message):
        if msg.is_control or port == self.aiu_port:
            return
        n = msg.byte_size()
        yield self.env.process(self.transfer(
            n, addr=msg.addr, value=msg.value, write_sum=msg.write_sum))


def _aiu_port_for(nt: NodeType):
    if nt == NodeType.GM_RDMA:
        return DataNocLocalId.GM_RDMA_LOCAL_SRAM
    if nt == NodeType.GM_WDMA:
        return DataNocLocalId.GM_WDMA_LOCAL_SRAM
    if nt == NodeType.DDR_RDMA:
        return DataNocLocalId.DDR_RDMA_LOCAL_SRAM
    if nt == NodeType.DDR_WDMA:
        return DataNocLocalId.DDR_WDMA_LOCAL_SRAM
    return None


def build_memory_system(env, noc, mcfg: MemoryConfig, clock,
                        include_adalink: bool = True,
                        adalink_latency: int = 8,
                        shadow_cfg: Optional[ShadowConfig] = None,
                        rank: int = 0, atomic_latency: int = 0):
    """Build DMA nodes wired to GM/DDR memories plus AdaLink nodes."""
    gm = Memory(env, "GM", mcfg.gm_capacity, mcfg.gm_aggregate_bw)
    ddr = Memory(env, "DDR", mcfg.ddr_capacity,
                 mcfg.ddr_aggregate_bw * clock.ddr_scale)

    nodes: Dict[int, NoCNode] = {}

    def add_dma(nt: NodeType, local_id: int, router_id: int,
                ports: List[int], memory: Memory, width: float,
                channels: int, is_read: bool):
        gid = 32 + {
            NodeType.GM_RDMA: 0, NodeType.GM_WDMA: 4,
            NodeType.DDR_RDMA: 8, NodeType.DDR_WDMA: 12,
        }[nt] + local_id
        nodes[gid] = DMANode(
            env, gid, nt, router_id, ports, noc,
            memory=memory, engine_width=width, channels=channels,
            is_read=is_read, aiu_port=_aiu_port_for(nt),
            aiu_sram_size=mcfg.aiu_sram_size, shadow_cfg=shadow_cfg,
            dispatch_interval=mcfg.dma.dispatch_interval)

    from .nodes import (
        GM_RDMA_ROUTERS, GM_WDMA_ROUTERS, DDR_RDMA_ROUTERS, DDR_WDMA_ROUTERS,
        ADALINK_ATTACH, global_node_id, AdaLinkNode,
    )

    gm_engine = mcfg.gm_engine_width
    ddr_engine = mcfg.ddr_engine_width * clock.ddr_scale
    for i, r in enumerate(GM_RDMA_ROUTERS):
        add_dma(NodeType.GM_RDMA, i, r, [DataNocLocalId.GM_RDMA],
                gm, gm_engine, 1, True)
    for i, r in enumerate(GM_WDMA_ROUTERS):
        add_dma(NodeType.GM_WDMA, i, r,
                [DataNocLocalId.GM_WDMA_CH0, DataNocLocalId.GM_WDMA_CH1],
                gm, gm_engine, 2, False)
    for i, r in enumerate(DDR_RDMA_ROUTERS):
        add_dma(NodeType.DDR_RDMA, i, r, [DataNocLocalId.DDR_RDMA],
                ddr, ddr_engine, 1, True)
    for i, r in enumerate(DDR_WDMA_ROUTERS):
        add_dma(NodeType.DDR_WDMA, i, r,
                [DataNocLocalId.DDR_WDMA_CH0, DataNocLocalId.DDR_WDMA_CH1],
                ddr, ddr_engine, 2, False)

    if include_adalink:
        local_id = 0
        by_router: Dict[int, List[AdaLinkNode]] = {}
        for router, ports in ADALINK_ATTACH:
            for p in ports:
                gid = global_node_id(NodeType.ADALINK, local_id)
                node = AdaLinkNode(env, gid, router, [p], noc,
                                   latency=adalink_latency,
                                   shadow_cfg=shadow_cfg,
                                   chip_rank=rank,
                                   atomic_latency=atomic_latency)
                nodes[gid] = node
                by_router.setdefault(router, []).append(node)
                local_id += 1
        for router_id, adalinks in by_router.items():
            din, _dout = noc.attach_local(
                router_id, DataNocLocalId.ALL_ADALINK, node_id=-1)

            def fanout(link=din, targets=adalinks):
                while True:
                    msg = yield link.get()
                    for t in targets:
                        t.received.append((env.now, msg))
                        t.events.append(
                            (env.now, DataNocLocalId.ALL_ADALINK, msg))
                        env.process(t.handle(DataNocLocalId.ALL_ADALINK,
                                             msg.model_copy()))
            env.process(fanout())

    return nodes, gm, ddr
