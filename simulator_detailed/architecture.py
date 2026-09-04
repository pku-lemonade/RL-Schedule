import simpy

from .command_coordination import DMACommandCoordinator
from .configs.schemas.arch_config import ArchConfig, CoreConfig, NoCConfig
from .configs.schemas.failure_configs import (
    FailSlow,
    LinkFail,
    LsuFail,
    RouterFail,
    TpuFail,
)
from .core import Core
from .dma_endpoint import DMAChannelBinding, DMAEndpoint, DMAEndpoints
from .endpoint_registry import EndpointRegistry, dma_node_type
from .noc import Link, NoC, NoCTracer
from .pe_channel import NMCChannel, PEChannelBinding
from .utils.definitions import PORT_PE, NoCChannel, NodeType, direction_to_port
from .utils.dfg import DFGNode
from .utils.mapper import NetworkMapper

NoCFabrics = dict[NoCChannel, NoC]


class Arch:
    def __init__(self, arch: ArchConfig, mapper: NetworkMapper, failures: FailSlow):
        # basic parameters
        self.env = simpy.Environment()
        self.config = arch
        self.x_size = arch.noc.x
        self.y_size = arch.noc.y
        self.mapper = mapper
        self.fail_slow = failures
        self.endpoint_registry = EndpointRegistry(arch.noc)
        self.dma_commands = DMACommandCoordinator(self.env)
        
        # construction
        self.nocs = self.build_nocs(env=self.env, config=self.config.noc)
        self.dma_endpoints = self.build_dma_endpoints(
            env=self.env,
            noc_config=self.config.noc,
        )
        self.cores = self.build_cores(
            env=self.env,
            config=self.config.core,
            noc_config=self.config.noc,
            mapper=self.mapper,
        )

        # initialization
        ready_nodes = self.mapper.zero_degree()
        self.initialize(operators=ready_nodes)


    def build_cores(
        self,
        env: simpy.Environment,
        config: CoreConfig,
        noc_config: NoCConfig,
        mapper: NetworkMapper,
    ) -> list[Core]:
        dma_commands = self._dma_command_coordinator(env)
        cores: list[Core] = []
        for id in range(self.x_size * self.y_size):
            core = Core(
                env=env,
                core_id=id,
                config=config,
                mapper=mapper,
                endpoint_registry=self.endpoint_registry,
            )
            for fabric_id in NoCChannel:
                address = self.endpoint_registry.resolve(
                    NodeType.PE,
                    id,
                    fabric_id=fabric_id,
                )
                noc = self.nocs[fabric_id]
                tx_link = Link(
                    env=env,
                    config=noc_config.c2r_link,
                    fabric_id=fabric_id,
                    tracer=noc.tracer,
                    link_name=f"PE{id}->R{id}",
                    noc_cycles_per_aci_cycle=noc_config.noc_cycles_per_aci_cycle,
                )
                rx_link = Link(
                    env=env,
                    config=noc_config.c2r_link,
                    fabric_id=fabric_id,
                    tracer=noc.tracer,
                    link_name=f"R{id}->PE{id}",
                    noc_cycles_per_aci_cycle=noc_config.noc_cycles_per_aci_cycle,
                )
                router = noc.routers[id]
                binding = PEChannelBinding(
                    address=address,
                    tx_link=tx_link,
                    rx_link=rx_link,
                    router=router,
                )
                channel = NMCChannel(
                    env=env,
                    config=config.nmc.channel_config(fabric_id),
                    shape_timing=config.nmc.shape_timing,
                    binding=binding,
                    dma_commands=dma_commands,
                )

                core.bind_channel(channel)
                router.bind_link(PORT_PE, tx_link, rx_link)

            core.validate_channel_bindings()
            cores.append(core)
            
        return cores

    def build_dma_endpoints(
        self,
        env: simpy.Environment,
        noc_config: NoCConfig,
    ) -> DMAEndpoints:
        dma_commands = self._dma_command_coordinator(env)
        endpoints: DMAEndpoints = {}
        for dma_config in noc_config.dma_engines:
            node_type = dma_node_type(dma_config.dma_type)
            if node_type not in (NodeType.GM_RDMA, NodeType.GM_WDMA):
                raise NotImplementedError(
                    "Phase 3A runtime attachment supports GM DMA endpoints only"
                )
            endpoint_key = (node_type, dma_config.instance_id)
            endpoint = DMAEndpoint(
                env,
                dma_config,
                node_type,
                dma_commands=dma_commands,
            )
            for address in self.endpoint_registry.configured_addresses(
                node_type,
                dma_config.instance_id,
            ):
                fabric_id = address.fabric_id
                noc = self.nocs[fabric_id]
                router = noc.routers[address.router_id]
                endpoint_name = f"{node_type.name}{dma_config.instance_id}"
                tx_link = Link(
                    env=env,
                    config=noc_config.c2r_link,
                    fabric_id=fabric_id,
                    tracer=noc.tracer,
                    link_name=(
                        f"{endpoint_name}->{fabric_id.name}:"
                        f"R{address.router_id}:P{address.local_port}"
                    ),
                    noc_cycles_per_aci_cycle=(
                        noc_config.noc_cycles_per_aci_cycle
                    ),
                )
                rx_link = Link(
                    env=env,
                    config=noc_config.c2r_link,
                    fabric_id=fabric_id,
                    tracer=noc.tracer,
                    link_name=(
                        f"{fabric_id.name}:R{address.router_id}:"
                        f"P{address.local_port}->{endpoint_name}"
                    ),
                    noc_cycles_per_aci_cycle=(
                        noc_config.noc_cycles_per_aci_cycle
                    ),
                )
                binding = DMAChannelBinding(
                    address=address,
                    tx_link=tx_link,
                    rx_link=rx_link,
                    router=router,
                )
                endpoint.bind_channel(binding)
                router.bind_link(address.local_port, tx_link, rx_link)

            endpoint.validate_channel_bindings()
            endpoints[endpoint_key] = endpoint
        return endpoints

    def _dma_command_coordinator(
        self,
        env: simpy.Environment,
    ) -> DMACommandCoordinator:
        coordinator = getattr(self, "dma_commands", None)
        if coordinator is None:
            coordinator = DMACommandCoordinator(env)
            self.dma_commands = coordinator
        elif coordinator.env is not env:
            raise ValueError(
                "architecture and DMA command coordinator must use the same environment"
            )
        return coordinator


    @staticmethod
    def build_nocs(
        env: simpy.Environment,
        config: NoCConfig,
    ) -> NoCFabrics:
        return {
            fabric_id: NoC(
                env=env,
                config=config,
                fabric_id=fabric_id,
                tracer=NoCTracer(fabric_id),
            ).build_connection_mesh()
            for fabric_id in NoCChannel
        }
    

    def initialize(self, operators: list[DFGNode]):
        # initialize primary tasks
        for core in self.cores:
            core.initialize(operators=operators)
        
        # initialize each core's spm
        for id in range(self.x_size * self.y_size):
            core_list: list[Core | None] = []
            for core in self.cores:
                if core.id == id:
                    core_list.append(None)
                else:
                    core_list.append(core)

            self.cores[id].scheduler.bind_with_core(core_list)

    # change failslow times easily
    def preprocess_fail(self, times: int = 10):
        for link_fail in self.fail_slow.link:
            link_fail.times = times

        for router_fail in self.fail_slow.router:
            router_fail.times = times

        for lsu_fail in self.fail_slow.lsu:
            lsu_fail.times = times

        for tpu_fail in self.fail_slow.tpu:
            tpu_fail.times = times


    def link_fail(self, fail: LinkFail):
        noc = self._noc_for_fabric(fail.fabric_id)
        yield self.env.timeout(fail.start_time)
        port = direction_to_port(fail.direction)
        link_in = noc.routers[fail.router_id].port_in[port]
        link_out = noc.routers[fail.router_id].port_out[port]
        assert link_in is not None and link_out is not None
        link_in.scale_link_delay(fail.times)
        link_out.scale_link_delay(fail.times)
        
        yield self.env.timeout(fail.end_time-fail.start_time)
        link_in.scale_link_delay(1 / fail.times)
        link_out.scale_link_delay(1 / fail.times)


    def router_fail(self, fail: RouterFail):
        noc = self._noc_for_fabric(fail.fabric_id)
        yield self.env.timeout(fail.start_time)
        noc.routers[fail.router_id].scale_link_delay(fail.times)
        yield self.env.timeout(fail.end_time-fail.start_time)
        noc.routers[fail.router_id].scale_link_delay(1 / fail.times)

    def _noc_for_fabric(self, fabric_id: NoCChannel) -> NoC:
        return self.nocs[fabric_id]


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
