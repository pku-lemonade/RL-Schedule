import simpy
from typing import List, Dict, Optional

from utils.mapper import *
from utils.dfg import DFGNode
from .noc import Link, NoC
from .core import Core
from configs.schemas.arch_config import *
from configs.schemas.failure_configs import *


class Arch:
    def __init__(self, arch: ArchConfig, mapper: NetworkMapper, failures: FailSlow):
        # basic parameters
        self.env = simpy.Environment()
        self.config = arch
        self.x_size = arch.noc.x
        self.y_size = arch.noc.y
        self.mapper = mapper
        self.fail_slow = failures
        
        # construction
        self.noc = self.build_noc(env=self.env, config=self.config.noc)
        self.cores = self.build_cores(env=self.env, config=self.config.core, mapper=self.mapper)

        # initialization
        ready_nodes = self.mapper.zero_degree()
        self.initialize(operators=ready_nodes)


    def build_cores(self, env, config: CoreConfig, mapper: NetworkMapper, c2r_width=128, c2r_delay=1) -> List[Core]:
        cores = []
        for id in range(self.x_size * self.y_size):
            core = Core(env=self.env, core_id=id, config=config, mapper=mapper)
            link1 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))
            link2 = Link(env=self.env, config=LinkConfig(width=c2r_width, delay=c2r_delay))

            link1.bind(0, 0, 0, 0, True)
            link2.bind(0, 0, 0, 0, True)
            
            core.bind_with_router(link2, link1, self.noc.routers[id])
            self.noc.routers[id].bind_link(direction='core', link_in=link1, link_out=link2)
            cores.append(core)
            
        return cores


    def build_noc(self, env, config: NoCConfig) -> NoC:
        # print("Building NoC architecture.")
        if config.type == "Mesh":
            return NoC(env = env, config = config).build_connection_mesh()
        elif config.type == "Torus":
            return NoC(env = env, config = config).build_connection_torus()
        elif config.type == "RingRoad":
            return NoC(env = env, config = config).build_connection_ring_road()
        elif config.type == "Dragonfly":
            return NoC(env = env, config = config).build_connection_dragonfly()
    

    def initialize(self, operators: List[DFGNode]):
        # initialize primary tasks
        for core in self.cores:
            core.initialize(operators=operators)
        
        # initialize each core's spm
        for id in range(self.x_size * self.y_size):
            core_list = []
            for core in self.cores:
                if core.id == id:
                    core_list.append(None)
                else:
                    core_list.append(core)

            self.cores[id].scheduler.bind_with_core(core_list)

    # change failslow times easily
    def preprocess_fail(self, times=10):
        for link_fail in self.fail_slow.link:
            link_fail.times = times

        for router_fail in self.fail_slow.router:
            router_fail.times = times

        for lsu_fail in self.fail_slow.lsu:
            lsu_fail.times = times

        for tpu_fail in self.fail_slow.tpu:
            tpu_fail.times = times


    def link_fail(self, fail: LinkFail):
        yield self.env.timeout(fail.start_time)
        self.noc.routers[fail.router_id].links[fail.direction]['in'].change_delay(fail.times)
        self.noc.routers[fail.router_id].links[fail.direction]['out'].change_delay(fail.times)
        
        yield self.env.timeout(fail.end_time-fail.start_time)
        self.noc.routers[fail.router_id].links[fail.direction]['in'].recover_delay(fail.times)
        self.noc.routers[fail.router_id].links[fail.direction]['out'].recover_delay(fail.times)


    def router_fail(self, fail: RouterFail):
        yield self.env.timeout(fail.start_time)
        self.noc.routers[fail.router_id].router_fail(fail.times)
        yield self.env.timeout(fail.end_time-fail.start_time)
        self.noc.routers[fail.router_id].router_recover(fail.times)


    def lsu_fail(self, fail: LsuFail):
        yield self.env.timeout(fail.start_time)
        self.cores[fail.pe_id].lsu_fail(fail.times)
        yield self.env.timeout(fail.end_time-fail.start_time)
        self.cores[fail.pe_id].lsu_recover(fail.times)


    def tpu_fail(self, fail: TpuFail):
        yield self.env.timeout(fail.start_time)
        self.cores[fail.pe_id].tpu_fail(fail.times)
        yield self.env.timeout(fail.end_time-fail.start_time)
        self.cores[fail.pe_id].tpu_recover(fail.times)
    

    def run_fail_slow(self):
        for link_fail in self.fail_slow.link:
            self.env.process(self.link_fail(link_fail))
        
        for router_fail in self.fail_slow.router:
            self.env.process(self.router_fail(router_fail))

        for lsu_fail in self.fail_slow.lsu:
            self.env.process(self.lsu_fail(lsu_fail))

        for tpu_fail in self.fail_slow.tpu:
            self.env.process(self.tpu_fail(tpu_fail))
    

    def execute(self):
        # self.preprocess_fail(times=10)
        self.run_fail_slow()
        self.env.run()

        return self.env