"""Synthetic fixtures shared by profiling regressions."""

from unittest.mock import Mock

import simpy

from profiling_sim.architecture import Arch
from profiling_sim.config import (
    ArchConfig,
    CoreConfig,
    LinkConfig,
    MemoryConfig,
    NoCConfig,
    NodeAttachment,
    NodeType,
    SPMConfig,
)
from profiling_sim.definitions import DimSlice, Message
from profiling_sim.noc import NoC
from profiling_sim.nodes import NoCNode


def noc_config(**changes):
    fields = dict(x=3, y=2, pe_local_port=7, link=LinkConfig(width=5))
    fields.update(changes)
    return NoCConfig(**fields)


def network(config=None):
    env = simpy.Environment()
    noc = NoC(env, config or noc_config(), deterministic=True).build()
    pes = [
        NoCNode(env, i, NodeType.PE, i, [noc.config.pe_local_port], noc)
        for i in range(noc.x * noc.y)
    ]
    return env, noc, pes


def message(src=0, dst=5, index=1, size=13, **changes):
    fields = dict(
        src=src,
        dst=dst,
        index=index,
        data=[DimSlice(start=0, end=size)],
        src_local_port=7,
        dst_local_port=7,
    )
    fields.update(changes)
    return Message(**fields)


def device_config(**changes):
    attachments = [
        NodeAttachment(
            node_type=NodeType.GM_RDMA,
            instance_id=12,
            router_id=1,
            ports=[30],
            local_memory_port=31,
            channels=3,
        ),
        NodeAttachment(
            node_type=NodeType.GM_WDMA,
            instance_id=12,
            router_id=4,
            ports=[32, 33],
            channels=2,
        ),
        NodeAttachment(
            node_type=NodeType.DDR_RDMA,
            instance_id=9,
            router_id=3,
            ports=[34],
            channels=1,
        ),
        NodeAttachment(
            node_type=NodeType.DDR_WDMA,
            instance_id=9,
            router_id=2,
            ports=[35],
            channels=3,
        ),
        NodeAttachment(
            node_type=NodeType.INTERCHIP, instance_id=20, router_id=5, ports=[36]
        ),
    ]
    fields = dict(
        core=CoreConfig(x=3, y=2, spm=SPMConfig(size=1024)),
        noc=noc_config(
            attachments=attachments, commid_count=5, interchip_broadcast_port=37
        ),
        memory=MemoryConfig(
            gm_capacity=1000, ddr_capacity=2000, gm_engine_width=3, ddr_engine_width=5
        ),
    )
    fields.update(changes)
    return ArchConfig(**fields)


def device(config=None, **kwargs):
    mapper = Mock()
    mapper.zero_degree.return_value = []
    mapper.all_tasks_completed.return_value = True
    return Arch(config or device_config(), mapper, deterministic=True, **kwargs)
