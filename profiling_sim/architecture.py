import simpy

from .noc import NoC
from .core import Core
from .config import ArchConfig
from .nodes import attach_nodes
from .memory import build_memory_system


class Arch:
    def __init__(self, arch: ArchConfig, mapper, deterministic: bool = False,
                 env=None, rank: int = 0, atomic_latency: int = 0):
        self.env = env if env is not None else simpy.Environment()
        self.config = arch
        self.rank = rank
        self.x_size = arch.noc.x
        self.y_size = arch.noc.y
        self.mapper = mapper
        self.deterministic = deterministic
        self.clock = arch.clock

        self.noc = NoC(self.env, arch.noc, deterministic=deterministic,
                       rank=rank).build()
        self.cores = self._build_cores(arch.core, mapper, arch.shadow)
        ncfg = arch.nodes
        self.gm = self.ddr = None
        if ncfg.enable_dma:
            self.nodes, self.gm, self.ddr = build_memory_system(
                self.env, self.noc, arch.memory, arch.clock,
                include_adalink=ncfg.enable_adalink,
                adalink_latency=ncfg.adalink_latency,
                shadow_cfg=arch.shadow, rank=rank,
                atomic_latency=atomic_latency)
        elif ncfg.enable_adalink:
            self.nodes = attach_nodes(
                self.env, self.noc, include_dma=False,
                include_adalink=True,
                include_all_adalink_port=ncfg.include_all_adalink_port,
                adalink_latency=ncfg.adalink_latency,
                shadow_cfg=arch.shadow, rank=rank,
                atomic_latency=atomic_latency)
        else:
            self.nodes = {}

        ready_nodes = mapper.zero_degree()
        self._initialize(ready_nodes)

    def _build_cores(self, config, mapper, shadow_cfg=None):
        cores = []
        for cid in range(self.x_size * self.y_size):
            core = Core(self.env, cid, config, mapper, shadow_cfg=shadow_cfg)
            data_in, data_out = self.noc.attach_local(cid, 0, cid)
            core.bind_with_router(data_in, data_out, self.noc.routers[cid])
            cores.append(core)
        return cores

    def _initialize(self, operators):
        for core in self.cores:
            core.initialize(operators)
        for cid in range(self.x_size * self.y_size):
            core_list = []
            for c in self.cores:
                core_list.append(None if c.id == cid else c)
            self.cores[cid].scheduler.bind_with_core(core_list)

    def execute(self):
        self.env.run()
        return self.env
