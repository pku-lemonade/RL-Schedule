"""Endpoints attached to the configured local ports of each mesh."""

from typing import Dict, List, Optional, Tuple

import simpy

from .definitions import Message, InterChipOp
from .config import NoCConfig, NodeType, ShadowConfig
from .shadow import ShadowEntry, ShadowPipeline


class NodeLayout:
    """Per-network node identities; no process-global attachment state."""

    def __init__(self, config: NoCConfig):
        self.config = config
        self.pe_count = config.x * config.y
        self.attachments = {
            (item.node_type, item.instance_id): item for item in config.attachments
        }
        self.ids = {
            key: self.pe_count + index for index, key in enumerate(self.attachments)
        }

    def global_node_id(self, kind: NodeType, local_id: int) -> int:
        if kind == NodeType.PE:
            if not 0 <= local_id < self.pe_count:
                raise ValueError("PE ID is outside the configured mesh")
            return local_id
        return self.ids[kind, local_id]

    def node_type(self, global_id: int) -> NodeType:
        if 0 <= global_id < self.pe_count:
            return NodeType.PE
        for key, gid in self.ids.items():
            if gid == global_id:
                return key[0]
        raise ValueError(f"unknown node ID {global_id}")

    def type_local_id(self, global_id: int) -> int:
        if self.node_type(global_id) == NodeType.PE:
            return global_id
        return next(key[1] for key, gid in self.ids.items() if gid == global_id)

    def address(self, kind: NodeType, local_id: int, channel: int = 0):
        if kind == NodeType.PE:
            return self.global_node_id(kind, local_id), self.config.pe_local_port
        item = self.attachments[kind, local_id]
        return item.router_id, item.ports[channel]


class NoCNode:
    """Base endpoint attached to one or more local ports of a router."""

    def __init__(
        self, env, node_id: int, nt: NodeType, router_id: int, ports: List[int], noc
    ):
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


class InterChipNode(NoCNode):
    """Single-chip InterChip stub and multi-chip egress/ingress endpoint."""

    def __init__(
        self,
        env,
        node_id,
        router_id,
        ports,
        noc,
        latency: int = 0,
        shadow_cfg: Optional[ShadowConfig] = None,
        chip_rank: int = 0,
        atomic_latency: int = 0,
    ):
        super().__init__(env, node_id, NodeType.INTERCHIP, router_id, ports, noc)
        self.latency = latency
        self.chip_rank = chip_rank
        self.atomic_latency = atomic_latency
        self.commids = self._make_commids()
        self._commid_events: Dict[Tuple[str, int], object] = {}
        self.shadow_cfg = shadow_cfg if shadow_cfg is not None else ShadowConfig()
        self._aci_func_pipeline = None
        self._aci_local_memory_pipeline = None
        if self.shadow_cfg.enabled:
            d = self.shadow_cfg.occupancy
            self._aci_func_pipeline = ShadowPipeline(
                env,
                f"aci{node_id}.func",
                [ShadowEntry.ST_ACI_FUNC.value],
                [self.shadow_cfg.aci_func],
                occupancy=d,
                ii=self.shadow_cfg.ii,
            )
            self._aci_local_memory_pipeline = ShadowPipeline(
                env,
                f"aci{node_id}.local_memory",
                [ShadowEntry.ST_ACI_LOCAL_MEMORY_DOWNLOAD.value],
                [self.shadow_cfg.aci_local_memory],
                occupancy=d,
                ii=self.shadow_cfg.ii,
            )
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
        evts.extend(self._aci_local_memory_pipeline.events)
        return evts

    def _make_commids(self):
        counters = ["PRODUCE", "SEND", "RECEIVE", "CREDIT", "RNIC_PRODUCE"]
        return {c: [0] * self.noc.config.commid_count for c in counters}

    def _pipeline_for(self, msg):
        if not self.shadow_cfg.enabled:
            return None
        return (
            self._aci_local_memory_pipeline
            if getattr(msg, "is_local_memory", False)
            else self._aci_func_pipeline
        )

    def handle(self, port, msg):
        broadcast = port == self.noc.config.interchip_broadcast_port
        if self._peer_link is not None and (
            broadcast or msg.dst_rank != self.chip_rank
        ):
            yield self.env.process(self._egress(msg))
            return
        self.commids["RECEIVE"][msg.index % self.noc.config.commid_count] += 1
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
            self.commids["SEND"][msg.index % self.noc.config.commid_count] += 1
            yield self.env.process(self._peer_link.send(msg))
        finally:
            if slot is not None:
                yield self.env.process(pipeline.release(slot))

    def _listen_interchip(self):
        while True:
            msg = yield self.interchip_in.get()
            self.received.append((self.env.now, msg))
            self.interchip_events.append((self.env.now, msg))
            self.commids["RECEIVE"][msg.index % self.noc.config.commid_count] += 1
            pipeline = self._pipeline_for(msg)
            slot = None
            if pipeline is not None:
                slot = yield self.env.process(pipeline.acquire())
            try:
                if self.latency:
                    yield self.env.timeout(self.latency)
                if not msg.is_local_memory:
                    op = InterChipOp(msg.interchip_op)
                    if op.is_atomic and self.atomic_latency:
                        self.atomic_events.append(
                            {
                                "time": self.env.now,
                                "op": int(op),
                                "value": msg.value,
                                "index": msg.index,
                                "src_rank": msg.dst_rank,
                            }
                        )
                        yield self.env.timeout(self.atomic_latency)
                    out = self.data_out[self.ports[0]]
                    yield out.put(msg)
            finally:
                if slot is not None:
                    yield self.env.process(pipeline.release(slot))
                if self._return_link is not None:
                    self._return_link.return_credit()

    def release_comm_id(self, comm_id: int, counter_type: str = "PRODUCE"):
        cid = comm_id % self.noc.config.commid_count
        self.commids[counter_type][cid] += 1
        ev = self._commid_events.get((counter_type, cid))
        if ev is not None and not ev.triggered:
            ev.succeed()

    def acquire_comm_id(self, comm_id: int, counter_type: str = "CREDIT"):
        self.commids[counter_type][comm_id % self.noc.config.commid_count] += 1
        return self.commids[counter_type][comm_id % self.noc.config.commid_count]

    def wait_comm_id(
        self, comm_id: int, counter_type: str = "CREDIT", expected: int = 1
    ):
        cid = comm_id % self.noc.config.commid_count
        key = (counter_type, cid)
        while self.commids[counter_type][cid] < expected:
            ev = self._commid_events.get(key)
            if ev is None or ev.triggered:
                ev = self.env.event()
                self._commid_events[key] = ev
            yield ev


def attach_nodes(
    env,
    noc,
    include_dma: bool = True,
    include_interchip: bool = True,
    include_all_interchip_port: bool = True,
    interchip_latency: int = 0,
    shadow_cfg: Optional[ShadowConfig] = None,
    rank: int = 0,
    atomic_latency: int = 0,
):
    """Build all non-PE nodes from the attachment table.

    Returns dict global_id -> NoCNode.
    """
    nodes: Dict[int, NoCNode] = {}
    by_router: Dict[int, List["InterChipNode"]] = {}
    for item in noc.config.attachments:
        nt, local_id, router_id, ports = (
            item.node_type,
            item.instance_id,
            item.router_id,
            item.ports,
        )
        if nt == NodeType.INTERCHIP and not include_interchip:
            continue
        if nt != NodeType.INTERCHIP and not include_dma:
            continue
        gid = noc.layout.global_node_id(nt, local_id)
        if gid in nodes:
            raise RuntimeError(f"duplicate node id {gid}")
        if nt == NodeType.INTERCHIP:
            node = InterChipNode(
                env,
                gid,
                router_id,
                ports,
                noc,
                latency=interchip_latency,
                shadow_cfg=shadow_cfg,
                chip_rank=rank,
                atomic_latency=atomic_latency,
            )
            by_router.setdefault(router_id, []).append(node)
        else:
            node = NoCNode(env, gid, nt, router_id, ports, noc)
        nodes[gid] = node

    if (
        include_interchip
        and include_all_interchip_port
        and noc.config.interchip_broadcast_port is not None
    ):
        for router_id, interchips in by_router.items():
            din, _dout = noc.attach_local(
                router_id, noc.config.interchip_broadcast_port, node_id=-1
            )

            def fanout(link=din, targets=interchips):
                while True:
                    msg = yield link.get()
                    for t in targets:
                        t.received.append((env.now, msg))
                        t.events.append(
                            (env.now, noc.config.interchip_broadcast_port, msg)
                        )
                        env.process(
                            t.handle(
                                noc.config.interchip_broadcast_port, msg.model_copy()
                            )
                        )

            env.process(fanout())

    return nodes
