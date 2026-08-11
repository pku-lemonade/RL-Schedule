"""Multi-chip AdaLink fabric: relocate table, inter-chip links, fabric."""
from typing import Dict, List, Optional, Tuple

import simpy

from .config import ArchConfig
from .nodes import AdaLinkNode, ADALINK_ATTACH


class AdaLinkRelocateTable:
    """adalink_relocate_table (DLCM + 0x303d8), 20 entries.

    Entries 0..17 select a local AdaLink node and store the peer
    (rank, port); entry 18 is reserved; entry 19 holds self (rank, port).
    port is 2 bits (0..3) per spec.
    """

    RESERVED = 18
    SELF = 19

    def __init__(self):
        self._entries: Dict[int, Tuple[int, int]] = {}

    @staticmethod
    def pack(rank: int, port: int) -> int:
        if not 0 <= port <= 3:
            raise ValueError(f"relocate port {port} out of 2-bit range [0,3]")
        if rank < 0:
            raise ValueError(f"rank {rank} must be >= 0")
        return (rank << 2) | port

    @staticmethod
    def unpack(value: int) -> Tuple[int, int]:
        return value >> 2, value & 0x3

    def set(self, local_id: int, remote_rank: int, remote_port: int):
        if not 0 <= local_id <= 17:
            raise ValueError(f"adalink local id {local_id} out of range [0,17]")
        if not 0 <= remote_port <= 3:
            raise ValueError(
                f"remote port {remote_port} out of 2-bit range [0,3]")
        if remote_rank < 0:
            raise ValueError(f"remote rank {remote_rank} must be >= 0")
        if local_id in self._entries:
            raise ValueError(f"adalink local id {local_id} already wired")
        self._entries[local_id] = (remote_rank, remote_port)

    def set_self(self, rank: int, port: int):
        if not 0 <= port <= 3:
            raise ValueError(f"self port {port} out of 2-bit range [0,3]")
        self._entries[self.SELF] = (rank, port)

    def lookup(self, local_id: int) -> Tuple[int, int]:
        if local_id == self.RESERVED:
            raise ValueError("relocate entry 18 is reserved")
        return self._entries[local_id]

    def __contains__(self, local_id: int) -> bool:
        return local_id in self._entries

    def items(self):
        return self._entries.items()


class InterChipLink:
    """One directed inter-chip wire with credit-based flow control."""

    def __init__(self, env: simpy.Environment, latency: int, credits: int = 64):
        if credits < 1:
            raise ValueError("inter-chip credits must be >= 1")
        self.env = env
        self.latency = latency
        self.capacity = credits
        self.credits = simpy.Container(env, capacity=credits, init=credits)
        self.peer: Optional[AdaLinkNode] = None

    def bind(self, peer: AdaLinkNode):
        self.peer = peer

    def send(self, msg):
        yield self.credits.get(1)
        delivered = False
        try:
            if self.latency:
                yield self.env.timeout(self.latency)
            if self.peer is None or self.peer.interchip_in is None:
                raise RuntimeError("InterChipLink peer not bound")
            yield self.peer.interchip_in.put(msg)
            delivered = True
        finally:
            if not delivered and self.credits.level < self.capacity:
                yield self.credits.put(1)

    def return_credit(self):
        if self.credits.level < self.capacity:
            self.credits.put(1)


def _adalink_router_port(local_id: int) -> Tuple[int, int]:
    idx = 0
    for router, ports in ADALINK_ATTACH:
        for p in ports:
            if idx == local_id:
                return router, p
            idx += 1
    raise ValueError(f"unknown adalink local id {local_id}")


class MultiChipFabric:
    """A collection of chips sharing one simpy environment, wired over AdaLink."""

    def __init__(self, env: simpy.Environment,
                 chip_configs: Dict[int, ArchConfig],
                 mappers: dict,
                 topology: List[Tuple[int, int, int, int]],
                 link_latency: int = 8,
                 credits: int = 64,
                 atomic_latency: int = 4,
                 deterministic: bool = False):
        self.env = env
        self.link_latency = link_latency
        self.credits = credits
        self.atomic_latency = atomic_latency
        self.deterministic = deterministic
        self.topology = list(topology)

        from .architecture import Arch
        self.chips: Dict[int, Arch] = {}
        self.relocate: Dict[int, AdaLinkRelocateTable] = {}
        self.links: List[InterChipLink] = []

        for rank in sorted(chip_configs):
            arch = Arch(chip_configs[rank], mappers[rank],
                        deterministic=deterministic, env=env, rank=rank,
                        atomic_latency=atomic_latency)
            self.chips[rank] = arch
            self.relocate[rank] = AdaLinkRelocateTable()

        for rank_a, idx_a, rank_b, idx_b in topology:
            self._wire(rank_a, idx_a, rank_b, idx_b)

    def _wire(self, rank_a, idx_a, rank_b, idx_b):
        if rank_a not in self.chips or rank_b not in self.chips:
            raise ValueError(f"topology references unknown rank "
                             f"{rank_a}->{rank_b}")
        node_a = self.chips[rank_a].nodes[48 + idx_a]
        node_b = self.chips[rank_b].nodes[48 + idx_b]
        if not isinstance(node_a, AdaLinkNode) or not isinstance(node_b, AdaLinkNode):
            raise ValueError("topology endpoints must be AdaLink nodes")
        if node_a._peer_link is not None or node_b._peer_link is not None:
            raise ValueError(
                f"adalink {rank_a}:{idx_a} or {rank_b}:{idx_b} already wired")

        link_ab = InterChipLink(self.env, self.link_latency, self.credits)
        link_ba = InterChipLink(self.env, self.link_latency, self.credits)
        link_ab.bind(node_b)
        link_ba.bind(node_a)
        node_a.bind_peer(link_ab, link_ba, rank_b, idx_b)
        node_b.bind_peer(link_ba, link_ab, rank_a, idx_a)
        self.links.extend([link_ab, link_ba])

        router_a, port_a = _adalink_router_port(idx_a)
        router_b, port_b = _adalink_router_port(idx_b)
        self._set_egress(rank_a, rank_b, router_a, port_a)
        self._set_egress(rank_b, rank_a, router_b, port_b)

        self.relocate[rank_a].set(idx_a, rank_b, idx_b & 0x3)
        self.relocate[rank_b].set(idx_b, rank_a, idx_a & 0x3)

    def _set_egress(self, rank, remote_rank, router_id, port):
        egress = self.chips[rank].noc.egress
        if remote_rank in egress:
            raise ValueError(
                f"rank {rank} has two egress ports for remote rank "
                f"{remote_rank}")
        egress[remote_rank] = (router_id, port)

    def execute(self):
        self.env.run()
        return self.env

    @property
    def shadow_events(self):
        evts = []
        for chip in self.chips.values():
            for node in chip.nodes.values():
                if isinstance(node, AdaLinkNode):
                    evts.extend(node.shadow_events)
        return evts

    @property
    def atomic_events(self):
        evts = []
        for chip in self.chips.values():
            for node in chip.nodes.values():
                if isinstance(node, AdaLinkNode):
                    evts.extend(node.atomic_events)
        return evts

    @property
    def interchip_events(self):
        evts = []
        for chip in self.chips.values():
            for node in chip.nodes.values():
                if isinstance(node, AdaLinkNode):
                    evts.extend(node.interchip_events)
        return evts
