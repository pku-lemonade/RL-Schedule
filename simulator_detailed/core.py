import simpy
import heapq
import logging
from enum import IntEnum, auto
from typing import List, Dict, Optional

from .utils.dfg import *
from .utils.mapper import NetworkMapper
from .configs.schemas.arch_config import *
from .endpoint_registry import EndpointRegistry
from .noc import Link, Router
from .utils.task import Task
from .utils.definitions import EndpointAddress, Event, NodeType
from .utils.definitions import comp_operator, comm_operator


logger = logging.getLogger("Core")

        
class ScratchpadMemory:
    def __init__(self, env, id, config: SPMConfig):
        # basic parameters
        self.id = id
        self.env = env
        self.delay = config.delay
        self.container = simpy.Container(self.env, init=config.size, capacity=config.size)

    
    def allocate(self, size: int, task_index: int):
        logger.debug(f"Core #{self.id}: allocate {size} for Task {task_index}")
        logger.debug(f"before allocating space for Task {task_index}: {2147483648-self.container.level}/2147483648")

        if size < 0:
            raise RuntimeError("Allocation size must be non-negative.")
        yield self.env.timeout(self.delay)

        logger.debug(f"Task {task_index} delay ok, data size is {size}")

        yield self.container.get(size)

        logger.debug(f"after allocating space for Task {task_index}: {2147483648-self.container.level}/2147483648")


    def release(self, size: int, task_index: int):
        logger.debug(f"Core #{self.id}: release {size} for Task {task_index}")
        logger.debug(f"before releasing space for Task {task_index}: {2147483648-self.container.level}/2147483648")
        
        if size < 0:
            raise RuntimeError("Release size must be non-negative.")
        yield self.env.timeout(self.delay)

        logger.debug(f"Task {task_index} delay ok, data size is {size}")

        level = 2147483648-self.container.level
        if size > level:
            logger.debug(f"size({size}) > level({level})")
        #     yield self.container.get(size)
        #     logger.debug(f"finish get space for Task {task_index}")
        
        # logger.debug(f"after get space for Task {task_index}: {2147483648-self.container.level}/2147483648")
        
        yield self.container.put(size)

        logger.debug(f"after releasing space for Task {task_index}: {2147483648-self.container.level}/2147483648")


class LSU:
    def __init__(self, env, config: LSUConfig):
        # basic parameters
        self.env = env
        self.width = 1.0 * config.width
        self.resource = simpy.Resource(env=env, capacity=config.size)

    
    def occupy(self, data_size: int):
        with self.resource.request() as req:
            yield req
            latency = data_size // self.width
            yield self.env.timeout(latency)


class TPU:
    def __init__(self, env, config: TPUConfig):
        # basic parameters
        self.env = env
        self.flops: float = float(config.flops)
        self.resource = simpy.Resource(env=env, capacity=config.size)


    def occupy(self, flop: int):
        with self.resource.request() as req:
            yield req
            latency = flop // self.flops
            yield self.env.timeout(latency)

# modify the global dfg, dispatch tasks for cores
class Scheduler:
    def __init__(self, id: int, mapper: NetworkMapper):
        # basic parameters.
        self.id = id
        self.mapper = mapper
        self.ready_tasks: List[Task] = []

        self.ready_comp_tasks: List[Task] = []
        self.ready_comm_tasks: List[Task] = []
        self.ready_io_tasks: List[Task] = []
    

    def bind_with_core(self, cores: List['Core | None']):
        self.cores = cores

    # update the global dfg
    def update(self, task: Task):
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


    def schedule(self):
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
        env,
        core_id: int,
        config: CoreConfig,
        mapper: NetworkMapper,
        address: EndpointAddress,
        endpoint_registry: EndpointRegistry,
    ):
        # basic parameters
        self.env = env
        self.id = core_id
        self.mapper = mapper
        if address.node_type != NodeType.PE or address.node_id != core_id:
            raise ValueError(f"core {core_id} received a mismatched endpoint address")
        self.address = address
        self.endpoint_registry = endpoint_registry
        
        # other resources
        self.scheduler = Scheduler(id=self.id, mapper=mapper)
        self.spm = ScratchpadMemory(env=self.env, id=self.id, config=config.spm)
        self.lsu = LSU(env=self.env, config=config.lsu)
        self.tpu = TPU(env=self.env, config=config.tpu)

        # data collection
        self.events: List[Event] = []
        self.index2id: Dict[int, int] = {}

        # begin simulation
        self.env.process(self.execute())


    def tpu_fail(self, times: int):
        self.tpu.flops /= times

    def tpu_recover(self, times: int):
        self.tpu.flops *= times

    def lsu_fail(self, times: int):
        self.lsu.width /= times

    def lsu_recover(self, times: int):
        self.lsu.width *= times
        

    def bind_with_router(self, data_in: Link, data_out: Link, router: Router):
        if router.fabric_id is not self.address.fabric_id:
            raise ValueError(
                f"core {self.id} address uses {self.address.fabric_id.name}, "
                f"not {router.fabric_id.name}"
            )
        for link in (data_in, data_out):
            if link.fabric_id is not self.address.fabric_id:
                raise ValueError(
                    f"core {self.id} cannot bind {link.link_name} from another fabric"
                )
        if router.id != self.address.router_id:
            raise ValueError(
                f"core {self.id} address maps to router {self.address.router_id}, "
                f"not router {router.id}"
            )
        self.data_in = data_in
        self.data_out = data_out
        self.router = router


    def initialize(self, operators: List[DFGNode]):
        for operator in operators:
            if operator.core_id == self.id:
                # if operator.core_id == 0:
                #     print(f"Core {self.id} add node {operator.index}.")
                task = Task(node=operator)
                heapq.heappush(self.scheduler.ready_tasks, task)


    def execute(self):
        self.running_event = []
        self.event2task = {}
        self.executed_task = {}

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
                                         start_time = int(round(self.env.now)),
                                         pe_id = self.id))
                self.index2id[comp_task.index] = len(self.events) - 1

                comp_event = self.env.process(comp_task.execute(self))
                self.running_event.append(comp_event)
                self.event2task[comp_event] = comp_task

            if comm_task:
                logger.info(f"Time {self.env.now: .2f}: CommTask {comm_task.index} running at Core {self.id}")
                self.events.append(Event(type = comm_task.operation, 
                                         index = comm_task.index, 
                                         start_time = int(round(self.env.now)),
                                         pe_id = self.id))
                self.index2id[comm_task.index] = len(self.events) - 1

                comm_event = self.env.process(comm_task.execute(self))
                self.running_event.append(comm_event)
                self.event2task[comm_event] = comm_task

            if io_task:
                logger.info(f"Time {self.env.now: .2f}: IOTask {io_task.index} running at Core {self.id}")
                self.events.append(Event(type = io_task.operation, 
                                         index = io_task.index, 
                                         start_time = int(round(self.env.now)),
                                         pe_id = self.id))
                self.index2id[io_task.index] = len(self.events) - 1

                io_event = self.env.process(io_task.execute(self))
                self.running_event.append(io_event)
                self.event2task[io_event] = io_task

            # logger.info(f"SPM usage: {2147483648-self.spm.container.level}/2147483648")
            # yield the running tasks
            # print(f"Time {self.env.now}: Core {self.id} running_events: {self.running_event}")
            result = yield self.env.any_of(self.running_event)

            if self.id == 5:
                logger.debug("core5 running event:")
                for event in self.running_event:
                    logger.debug(self.event2task[event].index)

            if not self.running_event:
                if self.mapper.all_tasks_completed(self.id):
                    logger.info(f"Time {self.env.now: .2f}: Core {self.id} finished - all tasks completed")
                    logger.info(f"spm usage: {2147483648-self.spm.container.level}/2147483648")
                    break

                # logger.debug(f"Time {self.env.now: .2f}: Core {self.id} has no running tasks, waiting...")
                yield self.env.timeout(10000)
                continue
            
            # update finished tasks
            for event in self.running_event:
                if event.triggered:
                    task = self.event2task[event]

                    logger.info(f"Time {self.env.now: .2f}: Task {task.index} finished at Core {self.id}")
                    self.events[self.index2id[task.index]].end_time = int(round(self.env.now))

                    self.scheduler.update(task=task)
                    self.running_event.remove(event)

                    if task.index not in self.executed_task:
                        self.executed_task[task.index] = 1
                    else:
                        raise RuntimeError(f"Repetitive Execution: Core {self.id} - task {task.index}")
