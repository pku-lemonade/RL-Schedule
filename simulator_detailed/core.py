from __future__ import annotations

import heapq
import logging
from collections.abc import Mapping
from types import MappingProxyType

import simpy
from simpy.events import Process, ProcessGenerator
from simpy.resources.resource import Request

from .configs.schemas.arch_config import (
    CoreConfig,
    LSUConfig,
    SPMConfig,
    TPUConfig,
)
from .endpoint_registry import EndpointRegistry
from .pe_channel import NMCChannel, PEChannelBinding
from .utils.definitions import (
    Event,
    NoCChannel,
    NodeType,
    comm_operator,
    comp_operator,
)
from .utils.dfg import DFGNode
from .utils.mapper import NetworkMapper
from .utils.task import Task

logger = logging.getLogger("Core")


class ScratchpadMemory:
    def __init__(
        self,
        env: simpy.Environment,
        core_id: int,
        config: SPMConfig,
    ) -> None:
        # basic parameters
        self.id = core_id
        self.env = env
        self.delay = config.delay
        self.container = simpy.Container(self.env, init=config.size, capacity=config.size)

    @property
    def used_bytes(self) -> float:
        return float(self.container.capacity - self.container.level)

    def allocate(self, size: int, task_index: int) -> ProcessGenerator:
        logger.debug(f"Core #{self.id}: allocate {size} for Task {task_index}")
        logger.debug(
            "before allocating space for Task %s: %s/%s",
            task_index,
            self.used_bytes,
            self.container.capacity,
        )

        if size < 0:
            raise RuntimeError("Allocation size must be non-negative.")
        yield self.env.timeout(self.delay)

        logger.debug(f"Task {task_index} delay ok, data size is {size}")

        yield self.container.get(size)

        logger.debug(
            "after allocating space for Task %s: %s/%s",
            task_index,
            self.used_bytes,
            self.container.capacity,
        )

    def release(self, size: int, task_index: int) -> ProcessGenerator:
        logger.debug(f"Core #{self.id}: release {size} for Task {task_index}")
        logger.debug(
            "before releasing space for Task %s: %s/%s",
            task_index,
            self.used_bytes,
            self.container.capacity,
        )

        if size < 0:
            raise RuntimeError("Release size must be non-negative.")
        yield self.env.timeout(self.delay)

        logger.debug(f"Task {task_index} delay ok, data size is {size}")

        if size > self.used_bytes:
            logger.debug("release size %s exceeds used bytes %s", size, self.used_bytes)

        yield self.container.put(size)

        logger.debug(
            "after releasing space for Task %s: %s/%s",
            task_index,
            self.used_bytes,
            self.container.capacity,
        )


class LSU:
    def __init__(self, env: simpy.Environment, config: LSUConfig) -> None:
        # basic parameters
        self.env = env
        self.width = 1.0 * config.width
        self.resource = simpy.Resource(env=env, capacity=config.size)

    def occupy(self, data_size: int) -> ProcessGenerator:
        request: Request = self.resource.request()
        with request:
            yield request
            latency = data_size // self.width
            yield self.env.timeout(latency)


class TPU:
    def __init__(self, env: simpy.Environment, config: TPUConfig) -> None:
        # basic parameters
        self.env = env
        self.flops: float = float(config.flops)
        self.resource = simpy.Resource(env=env, capacity=config.size)

    def occupy(self, flop: int) -> ProcessGenerator:
        request: Request = self.resource.request()
        with request:
            yield request
            latency = flop // self.flops
            yield self.env.timeout(latency)

# modify the global dfg, dispatch tasks for cores
class Scheduler:
    def __init__(self, core_id: int, mapper: NetworkMapper) -> None:
        # basic parameters.
        self.id = core_id
        self.mapper = mapper
        self.ready_tasks: list[Task] = []

        self.ready_comp_tasks: list[Task] = []
        self.ready_comm_tasks: list[Task] = []
        self.ready_io_tasks: list[Task] = []
        self.cores: list[Core | None] = []

    def bind_with_core(self, cores: list[Core | None]) -> None:
        self.cores = cores

    # update the global dfg
    def update(self, task: Task) -> None:
        logger.debug(f"Core {self.id} is updating task {task.index}")
        cur_node = self.mapper.dfg.get_node(task.index)
        assert cur_node is not None
        cur_node.finished = True

        for child_id in task.successors:
            child_node = self.mapper.dfg.get_node(child_id)
            assert child_node is not None
            self.mapper.update(src_node=cur_node, dst_node=child_node)

            if child_node.ready == True and child_node.executed == False:
                child_node.executed = True
                if child_node.core_id == self.id:
                    logger.debug(f"    push task {child_node.index} into Core {self.id}")
                    heapq.heappush(self.ready_tasks, Task(node=child_node))
                else:
                    logger.debug(f"    push task {child_node.index} into Core {child_node.core_id}")
                    target_core = self.cores[child_node.core_id]
                    assert target_core is not None
                    heapq.heappush(target_core.scheduler.ready_tasks, Task(node=child_node))


    def schedule(self) -> tuple[Task | None, Task | None, Task | None]:
        task_num = len(self.ready_tasks)
        task_num += len(self.ready_comp_tasks) + len(self.ready_comm_tasks) + len(self.ready_io_tasks)
        if task_num == 0:
            return None, None, None
        else:
            comp_task, comm_task, io_task = None, None, None
            while self.ready_tasks:
                ready_task = heapq.heappop(self.ready_tasks)

                if ready_task.operation in comp_operator:
                    heapq.heappush(self.ready_comp_tasks, ready_task)
                elif ready_task.operation in comm_operator:
                    heapq.heappush(self.ready_comm_tasks, ready_task)
                else:
                    heapq.heappush(self.ready_io_tasks, ready_task)
            
            if self.ready_comp_tasks:
                comp_task = heapq.heappop(self.ready_comp_tasks)
            if self.ready_comm_tasks:
                comm_task = heapq.heappop(self.ready_comm_tasks)
            if self.ready_io_tasks:
                io_task = heapq.heappop(self.ready_io_tasks)

            return comp_task, comm_task, io_task

# task execution
class Core:
    def __init__(
        self,
        env: simpy.Environment,
        core_id: int,
        config: CoreConfig,
        mapper: NetworkMapper,
        endpoint_registry: EndpointRegistry,
    ) -> None:
        # basic parameters
        self.env = env
        self.id = core_id
        self.mapper = mapper
        self.endpoint_registry = endpoint_registry
        self._nmc_channels: dict[NoCChannel, NMCChannel] = {}
        self.nmc_channels: Mapping[NoCChannel, NMCChannel] = MappingProxyType(
            self._nmc_channels
        )
        self._channel_bindings: dict[NoCChannel, PEChannelBinding] = {}
        self.channel_bindings: Mapping[NoCChannel, PEChannelBinding] = (
            MappingProxyType(self._channel_bindings)
        )
        
        # other resources
        self.scheduler = Scheduler(core_id=self.id, mapper=mapper)
        self.spm = ScratchpadMemory(
            env=self.env,
            core_id=self.id,
            config=config.spm,
        )
        self.lsu = LSU(env=self.env, config=config.lsu)
        self.tpu = TPU(env=self.env, config=config.tpu)

        # data collection
        self.events: list[Event] = []
        self.index2id: dict[int, int] = {}

        # begin simulation
        self.env.process(self.execute())


    def tpu_fail(self, times: int) -> None:
        self.tpu.flops /= times

    def tpu_recover(self, times: int) -> None:
        self.tpu.flops *= times

    def lsu_fail(self, times: int) -> None:
        self.lsu.width /= times

    def lsu_recover(self, times: int) -> None:
        self.lsu.width *= times
        

    def bind_channel(self, channel: NMCChannel) -> None:
        fabric_id = channel.fabric_id
        if fabric_id in self._nmc_channels:
            raise ValueError(
                f"core {self.id} already has a {fabric_id.name} NMC channel"
            )
        if channel.env is not self.env:
            raise ValueError(
                f"core {self.id} and {fabric_id.name} NMC channel "
                "must use the same SimPy environment"
            )
        expected_address = self.endpoint_registry.resolve(
            NodeType.PE,
            self.id,
            fabric_id=fabric_id,
        )
        if channel.binding.address != expected_address:
            raise ValueError(
                f"core {self.id} received a mismatched {fabric_id.name} NMC channel"
            )
        self._nmc_channels[fabric_id] = channel
        self._channel_bindings[fabric_id] = channel.binding

    def nmc_channel_for(self, fabric_id: NoCChannel) -> NMCChannel:
        channel = self._nmc_channels.get(fabric_id)
        if channel is None:
            raise RuntimeError(
                f"core {self.id} has no {fabric_id.name} NMC channel"
            )
        return channel

    def binding_for(self, fabric_id: NoCChannel) -> PEChannelBinding:
        binding = self._channel_bindings.get(fabric_id)
        if binding is None:
            raise RuntimeError(
                f"core {self.id} has no {fabric_id.name} PE channel binding"
            )
        return binding

    def validate_channel_bindings(self) -> None:
        expected_fabrics = set(NoCChannel)
        actual_fabrics = set(self._nmc_channels)
        if actual_fabrics != expected_fabrics:
            missing = expected_fabrics - actual_fabrics
            unexpected = actual_fabrics - expected_fabrics
            raise ValueError(
                f"core {self.id} NMC channels are incomplete; "
                f"missing={missing}, unexpected={unexpected}"
            )
        if set(self._channel_bindings) != actual_fabrics:
            raise RuntimeError(
                f"core {self.id} NMC channels and bindings disagree"
            )


    def initialize(self, operators: list[DFGNode]) -> None:
        for operator in operators:
            if operator.core_id == self.id:
                # if operator.core_id == 0:
                #     print(f"Core {self.id} add node {operator.index}.")
                task = Task(node=operator)
                heapq.heappush(self.scheduler.ready_tasks, task)


    def execute(self) -> ProcessGenerator:
        self.running_event: list[Process] = []
        self.event2task: dict[Process, Task] = {}
        self.executed_task: dict[int, int] = {}

        while True:
            # fetch data from router and update
            # self.env.process(self.receive_data())

            # create processes for ready tasks
            # tasks that use different hardware components can execute parallelly
            comp_task, comm_task, io_task = self.scheduler.schedule()

            if comp_task:
                logger.info(f"Time {self.env.now: .2f}: CompTask {comp_task.index} running at Core {self.id}")
                self.events.append(Event(type = comp_task.operation, 
                                         index = comp_task.index, 
                                         start_time = round(self.env.now),
                                         pe_id = self.id))
                self.index2id[comp_task.index] = len(self.events) - 1

                comp_event = self.env.process(comp_task.execute(self))
                self.running_event.append(comp_event)
                self.event2task[comp_event] = comp_task

            if comm_task:
                logger.info(f"Time {self.env.now: .2f}: CommTask {comm_task.index} running at Core {self.id}")
                self.events.append(Event(type = comm_task.operation, 
                                         index = comm_task.index, 
                                         start_time = round(self.env.now),
                                         pe_id = self.id))
                self.index2id[comm_task.index] = len(self.events) - 1

                comm_event = self.env.process(comm_task.execute(self))
                self.running_event.append(comm_event)
                self.event2task[comm_event] = comm_task

            if io_task:
                logger.info(f"Time {self.env.now: .2f}: IOTask {io_task.index} running at Core {self.id}")
                self.events.append(Event(type = io_task.operation, 
                                         index = io_task.index, 
                                         start_time = round(self.env.now),
                                         pe_id = self.id))
                self.index2id[io_task.index] = len(self.events) - 1

                io_event = self.env.process(io_task.execute(self))
                self.running_event.append(io_event)
                self.event2task[io_event] = io_task

            # logger.info(f"SPM usage: {2147483648-self.spm.container.level}/2147483648")
            # yield the running tasks
            # print(f"Time {self.env.now}: Core {self.id} running_events: {self.running_event}")
            yield self.env.any_of(self.running_event)

            if self.id == 5:
                logger.debug("core5 running event:")
                for event in tuple(self.running_event):
                    logger.debug(self.event2task[event].index)

            if not self.running_event:
                if self.mapper.all_tasks_completed(self.id):
                    logger.info(f"Time {self.env.now: .2f}: Core {self.id} finished - all tasks completed")
                    logger.info(
                        "spm usage: %s/%s",
                        self.spm.used_bytes,
                        self.spm.container.capacity,
                    )
                    break

                # logger.debug(f"Time {self.env.now: .2f}: Core {self.id} has no running tasks, waiting...")
                yield self.env.timeout(10000)
                continue
            
            # update finished tasks
            for event in tuple(self.running_event):
                if event.triggered:
                    task = self.event2task[event]

                    logger.info(f"Time {self.env.now: .2f}: Task {task.index} finished at Core {self.id}")
                    self.events[self.index2id[task.index]].end_time = round(
                        self.env.now
                    )

                    self.scheduler.update(task=task)
                    self.running_event.remove(event)

                    if task.index not in self.executed_task:
                        self.executed_task[task.index] = 1
                    else:
                        raise RuntimeError(f"Repetitive Execution: Core {self.id} - task {task.index}")
