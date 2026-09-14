"""GM / DDR memory nodes with per-engine channels and aggregate bandwidth."""

import math
from typing import Dict, Optional

import simpy

from .definitions import Message, DimSlice, TransType, TransferMode
from .config import MemoryConfig, ShadowConfig
from .nodes import (
    NoCNode,
    NodeType,
    attach_nodes,
)
from .shadow import (
    ShadowEntry,
    ShadowPipeline,
    mdma_channel_entry,
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
                cap = 10**6
            else:
                cap = max(1, int(round(self.aggregate_bw / key)))
            self._lane_resources[key] = simpy.Resource(self.env, capacity=cap)
        return self._lane_resources[key]

    def allocate(self, n_bytes: int):
        if self.used + n_bytes > self.capacity:
            raise MemoryError(
                f"{self.name} out of capacity: {self.used}+{n_bytes} > {self.capacity}"
            )
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

    def __init__(
        self,
        env,
        node_id,
        nt,
        router_id,
        ports,
        noc,
        memory: Memory,
        engine_width: float,
        channels: int = 1,
        is_read: bool = True,
        local_memory_port: Optional[int] = None,
        local_memory_sram_size: int = 1024,
        local_memory_alignment: int = 1,
        local_memory_sync_target: int = 0,
        local_memory_sync_bytes: int = 1,
        shadow_cfg: Optional[ShadowConfig] = None,
        dispatch_interval: int = 0,
    ):
        super().__init__(env, node_id, nt, router_id, ports, noc)
        self.local_memory_alignment = local_memory_alignment
        self.local_memory_sync_target = local_memory_sync_target
        self.local_memory_sync_bytes = local_memory_sync_bytes
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
        self.local_memory_port = local_memory_port
        self.local_memory_sram_size = local_memory_sram_size
        self.local_memory_used = 0
        self.local_memory_events = []
        self.shadow_cfg = shadow_cfg if shadow_cfg is not None else ShadowConfig()
        self._mdma_pipelines = {}
        self._mdma_local_memory_pipeline = None
        if self.shadow_cfg.enabled:
            d = self.shadow_cfg.occupancy
            for ch in range(channels):
                entry = mdma_channel_entry(ch)
                self._mdma_pipelines[ch] = ShadowPipeline(
                    env,
                    f"dma{node_id}.ch{ch}",
                    [entry],
                    [self.shadow_cfg.mdma_channel],
                    occupancy=d,
                    ii=self.shadow_cfg.ii,
                )
            self._mdma_local_memory_pipeline = ShadowPipeline(
                env,
                f"dma{node_id}.local_memory",
                [ShadowEntry.ST_MDMA_LOCAL_MEMORY_DOWNLOAD.value],
                [self.shadow_cfg.mdma_local_memory],
                occupancy=d,
                ii=self.shadow_cfg.ii,
            )
        if local_memory_port is not None:
            self._attach_local_memory(local_memory_port)

    @property
    def shadow_events(self):
        if not self.shadow_cfg.enabled:
            return []
        evts = []
        for p in self._mdma_pipelines.values():
            evts.extend(p.events)
        if self._mdma_local_memory_pipeline is not None:
            evts.extend(self._mdma_local_memory_pipeline.events)
        return evts

    def _attach_local_memory(self, port):
        din, dout = self.noc.attach_local(self.router_id, port, node_id=self.id)
        self.local_memory_in = din
        self.data_in[port] = din
        self.data_out[port] = dout
        self.env.process(self._listen_local_memory(port))

    def _listen_local_memory(self, port):
        while True:
            msg = yield self.local_memory_in.get()
            self.received.append((self.env.now, msg))
            n = msg.byte_size()
            if n % self.local_memory_alignment != 0:
                raise ValueError(
                    f"download must align to {self.local_memory_alignment} bytes, got {n}"
                )
            if self.local_memory_used + n > self.local_memory_sram_size:
                raise MemoryError("local memory SRAM capacity exceeded")
            if self._mdma_local_memory_pipeline is not None:
                slot = yield self.env.process(
                    self._mdma_local_memory_pipeline.acquire()
                )
            else:
                slot = None
            try:
                self.local_memory_used += n
                self.local_memory_events.append((self.env.now, n, msg))
                scalar_sync = msg.model_copy(
                    update={
                        "dst": self.local_memory_sync_target,
                        "src": self.id,
                        "data": [DimSlice(start=0, end=self.local_memory_sync_bytes)],
                        "element_bytes": 1,
                        "header_bytes": 0,
                        "transfer_mode": TransferMode.DUAL_SIDE,
                        "is_control": True,
                        "sync": True,
                        "dst_mask": 0,
                        "trans_type": TransType.SINGLECAST,
                        "dst_local_port": self.noc.config.pe_local_port,
                        "is_local_memory": False,
                        "layout": None,
                    }
                )
                self.data_out[port].put(scalar_sync)
            finally:
                if slot is not None:
                    yield self.env.process(
                        self._mdma_local_memory_pipeline.release(slot)
                    )

    def transfer(self, n_bytes: int, addr: int = 0, value: int = 0, write_sum: int = 0):
        if self.dispatch_interval:
            with self.dispatch.request() as dreq:
                yield dreq
                yield self.env.timeout(self.dispatch_interval)
        ch = yield self.channel_store.get()
        start = self.env.now
        if not self.is_read:
            self.memory.allocate(n_bytes)
            self.memory.write(addr, value, write_sum)
        engine_time = (
            math.ceil(n_bytes / self.effective_width) if self.effective_width > 0 else 0
        )
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
        if msg.is_control or port == self.local_memory_port:
            return
        n = msg.byte_size()
        yield self.env.process(
            self.transfer(n, addr=msg.addr, value=msg.value, write_sum=msg.write_sum)
        )


def build_memory_system(
    env,
    noc,
    mcfg: MemoryConfig,
    clock,
    include_interchip: bool = True,
    interchip_latency: int = 0,
    shadow_cfg: Optional[ShadowConfig] = None,
    rank: int = 0,
    atomic_latency: int = 0,
    include_all_interchip_port: bool = True,
):
    """Construct the configured endpoints and their memory services."""
    gm = Memory(env, "GM", mcfg.gm_capacity, mcfg.gm_aggregate_bw)
    ddr = Memory(env, "DDR", mcfg.ddr_capacity, mcfg.ddr_aggregate_bw * clock.ddr_scale)
    nodes = attach_nodes(
        env,
        noc,
        include_dma=False,
        include_interchip=include_interchip,
        include_all_interchip_port=include_all_interchip_port,
        interchip_latency=interchip_latency,
        shadow_cfg=shadow_cfg,
        rank=rank,
        atomic_latency=atomic_latency,
    )
    for item in noc.config.attachments:
        nt = item.node_type
        if nt == NodeType.INTERCHIP:
            continue
        is_ddr = nt in (NodeType.DDR_RDMA, NodeType.DDR_WDMA)
        gid = noc.layout.global_node_id(nt, item.instance_id)
        nodes[gid] = DMANode(
            env,
            gid,
            nt,
            item.router_id,
            item.ports,
            noc,
            memory=ddr if is_ddr else gm,
            engine_width=(
                mcfg.ddr_engine_width * clock.ddr_scale
                if is_ddr
                else mcfg.gm_engine_width
            ),
            channels=item.channels,
            is_read=nt in (NodeType.GM_RDMA, NodeType.DDR_RDMA),
            local_memory_port=item.local_memory_port,
            local_memory_sram_size=mcfg.local_memory_size,
            local_memory_alignment=mcfg.local_memory_alignment,
            local_memory_sync_target=mcfg.local_memory_sync_target,
            local_memory_sync_bytes=mcfg.local_memory_sync_bytes,
            shadow_cfg=shadow_cfg,
            dispatch_interval=mcfg.dma.dispatch_interval,
        )
    return nodes, gm, ddr
