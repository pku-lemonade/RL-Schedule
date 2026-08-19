import simpy
import heapq
import logging
from typing import List, Optional

from .dfg import DFGNode
from .definitions import Event, comp_operator, comm_operator
from .noc import Link, Router
from .task import Task
from .config import SPMConfig, LSUConfig, TPUConfig, NMCConfig, ShadowConfig
from .shadow import (
    ShadowEntry,
    ShadowPipeline,
    sramc_entry,
)

logger = logging.getLogger("Core")


class ScratchpadMemory:
    def __init__(self, env, id, config: SPMConfig):
        self.id = id
        self.env = env
        self.delay = config.delay
        self.capacity = config.size
        self.container = simpy.Container(env, init=config.size, capacity=config.size)

    def allocate(self, size: int, task_index: int):
        if size < 0:
            raise RuntimeError("Allocation size must be non-negative")
        yield self.env.timeout(self.delay)
        yield self.container.get(size)

    def release(self, size: int, task_index: int):
        if size < 0:
            raise RuntimeError("Release size must be non-negative")
        yield self.env.timeout(self.delay)
        yield self.container.put(size)


class LSU:
    def __init__(self, env, config: LSUConfig):
        self.env = env
        self.width = 1.0 * config.width
        self.resource = simpy.Resource(env, capacity=config.size)

    def occupy(self, data_size: int):
        with self.resource.request() as req:
            yield req
            yield self.env.timeout(data_size // self.width)


class TPU:
    def __init__(self, env, config: TPUConfig):
        self.env = env
        self.flops = config.flops
        self.resource = simpy.Resource(env, capacity=config.size)

    def occupy(self, flop: int):
        with self.resource.request() as req:
            yield req
            yield self.env.timeout(flop // self.flops)


class NMC:
    """NoC-Memory Controller with multiple independent DMA channels.

    Each channel can perform an upload (SEND) or download (RECV)
    concurrently.  The physical core-to-router link is shared, so
    concurrent transfers in the same direction still serialize on the
    link's capacity-1 store.
    """

    def __init__(self, env, config: NMCConfig):
        self.env = env
        self.start_up_time = config.start_up_time
        self.channel_store = simpy.Store(env, capacity=config.channels)
        for i in range(config.channels):
            self.channel_store.put(i)
        inj_cap = config.injection_ports
        self.injection_ports = simpy.Resource(
            env, capacity=inj_cap if inj_cap and inj_cap > 0 else 10 ** 6)

    def acquire(self):
        return self.channel_store.get()

    def release(self, channel_id):
        return self.channel_store.put(channel_id)


class Scheduler:
    def __init__(self, id: int, mapper, comm_slots: int = 1, comp_slots: int = 1):
        self.id = id
        self.mapper = mapper
        self.comm_slots = comm_slots
        self.comp_slots = comp_slots
        self.ready_tasks: List[Task] = []
        self.ready_comp_tasks: List[Task] = []
        self.ready_comm_tasks: List[Task] = []
        self.ready_io_tasks: List[Task] = []

    def bind_with_core(self, cores: List['Core']):
        self.cores = cores

    def update(self, task: Task):
        cur_node = self.mapper.dfg.get_node(task.index)
        cur_node.finished = True
        for son_id in task.successors:
            son_node = self.mapper.dfg.get_node(son_id)
            self.mapper.update(src_node=cur_node, dst_node=son_node)
            if son_node.ready and not son_node.executed:
                son_node.executed = True
                new_task = Task(node=son_node)
                if son_node.core_id == self.id:
                    heapq.heappush(self.ready_tasks, new_task)
                else:
                    heapq.heappush(
                        self.cores[son_node.core_id].scheduler.ready_tasks,
                        new_task)

    def schedule(self):
        total = (len(self.ready_tasks) + len(self.ready_comp_tasks)
                 + len(self.ready_comm_tasks) + len(self.ready_io_tasks))
        if total == 0:
            return [], [], None

        while self.ready_tasks:
            t = heapq.heappop(self.ready_tasks)
            if t.operation in comp_operator:
                heapq.heappush(self.ready_comp_tasks, t)
            elif t.operation in comm_operator:
                heapq.heappush(self.ready_comm_tasks, t)
            else:
                heapq.heappush(self.ready_io_tasks, t)

        comp_tasks = []
        while self.ready_comp_tasks and len(comp_tasks) < self.comp_slots:
            comp_tasks.append(heapq.heappop(self.ready_comp_tasks))
        comm_tasks = []
        while self.ready_comm_tasks and len(comm_tasks) < self.comm_slots:
            comm_tasks.append(heapq.heappop(self.ready_comm_tasks))
        io = heapq.heappop(self.ready_io_tasks) if self.ready_io_tasks else None
        return comp_tasks, comm_tasks, io


class Core:
    def __init__(self, env, core_id: int, config, mapper,
                 shadow_cfg: Optional[ShadowConfig] = None):
        self.env = env
        self.id = core_id
        self.mapper = mapper
        self.element_bytes = config.element_bytes
        self.shadow_cfg = shadow_cfg if shadow_cfg is not None else ShadowConfig()
        if self.shadow_cfg.enabled and config.nmc.channels > 2:
            raise ValueError("shadow pipelines only support up to 2 NMC channels")
        comp_slots = self.shadow_cfg.occupancy if self.shadow_cfg.enabled else 1
        self.scheduler = Scheduler(id=core_id, mapper=mapper,
                                   comm_slots=config.nmc.channels,
                                   comp_slots=comp_slots)
        self.spm = ScratchpadMemory(env, core_id, config.spm)
        self.lsu = LSU(env, config.lsu)
        self.tpu = TPU(env, config.tpu)
        self.nmc = NMC(env, config.nmc)
        rp = config.sram_read_ports
        wp = config.sram_write_ports
        self.sram_read = simpy.Resource(
            env, capacity=rp if rp and rp > 0 else 10 ** 6)
        self.sram_write = simpy.Resource(
            env, capacity=wp if wp and wp > 0 else 10 ** 6)
        self.events = []
        self.index2id = {}
        self._build_shadow_pipelines()
        self.env.process(self.execute())

    def _build_shadow_pipelines(self):
        self.shadow_enabled = self.shadow_cfg.enabled
        self.matrix_pipeline = None
        self.vector_pipeline = None
        self.sramc_pipelines = {}
        if not self.shadow_enabled:
            return
        cfg = self.shadow_cfg
        d = cfg.occupancy
        self.matrix_pipeline = ShadowPipeline(
            self.env, f"core{self.id}.matrix",
            [int(e) for e in (
                ShadowEntry.ST_PE_MATRIX_READ_F,
                ShadowEntry.ST_PE_MATRIX_READ_W,
                ShadowEntry.ST_PE_MATRIX_CAL,
                ShadowEntry.ST_PE_MATRIX_WRITE,
            )],
            list(cfg.matrix), occupancy=d, ii=cfg.ii,
        )
        self.vector_pipeline = ShadowPipeline(
            self.env, f"core{self.id}.vector",
            [int(e) for e in (
                ShadowEntry.ST_PE_VECTOR_READ,
                ShadowEntry.ST_PE_VECTOR_CAL,
                ShadowEntry.ST_PE_VECTOR_WRITE,
            )],
            list(cfg.vector), occupancy=d, ii=cfg.ii,
        )
        for ch in range(min(2, self.nmc.channel_store.capacity)):
            self.sramc_pipelines[(ch, True)] = ShadowPipeline(
                self.env, f"core{self.id}.sramc_upld{ch}",
                [int(sramc_entry(ch, True))], [cfg.sramc_upld],
                occupancy=d, ii=cfg.ii,
            )
            self.sramc_pipelines[(ch, False)] = ShadowPipeline(
                self.env, f"core{self.id}.sramc_dnld{ch}",
                [int(sramc_entry(ch, False))], [cfg.sramc_dnld],
                occupancy=d, ii=cfg.ii,
            )

    def enter_matrix(self, tag=None, cal_cycles: int = 0):
        return self.env.process(
            self.matrix_pipeline.enter(
                tag, {int(ShadowEntry.ST_PE_MATRIX_CAL): cal_cycles}))

    def enter_vector(self, tag=None, cal_cycles: int = 0):
        return self.env.process(
            self.vector_pipeline.enter(
                tag, {int(ShadowEntry.ST_PE_VECTOR_CAL): cal_cycles}))

    def enter_sramc(self, channel: int, upload: bool, tag=None):
        return self.env.process(
            self.sramc_pipelines[(channel, upload)].enter(tag))

    @property
    def shadow_events(self):
        if not self.shadow_enabled:
            return []
        evts = []
        evts.extend(self.matrix_pipeline.events)
        evts.extend(self.vector_pipeline.events)
        for p in self.sramc_pipelines.values():
            evts.extend(p.events)
        return evts

    def bind_with_router(self, data_in: Link, data_out: Link, router: Router):
        self.data_in = data_in
        self.data_out = data_out
        self.router = router

    def initialize(self, operators: List[DFGNode]):
        for op in operators:
            if op.core_id == self.id:
                heapq.heappush(self.scheduler.ready_tasks, Task(node=op))

    def execute(self):
        self.running_event = []
        self.event2task = {}
        self.executed_task = {}

        while True:
            comp_tasks, comm_tasks, io_task = self.scheduler.schedule()

            pending = []
            for ct in comp_tasks:
                pending.append((ct, "Comp"))
            for ct in comm_tasks:
                pending.append((ct, "Comm"))
            if io_task is not None:
                pending.append((io_task, "IO"))

            for task, tag in pending:
                logger.info(f"Time {self.env.now:.2f}: {tag}Task {task.index} at Core {self.id}")
                self.events.append(Event(
                    type=task.operation, index=task.index,
                    start_time=int(round(self.env.now)), pe_id=self.id))
                self.index2id[task.index] = len(self.events) - 1
                ev = self.env.process(task.execute(self))
                self.running_event.append(ev)
                self.event2task[ev] = task

            if not self.running_event:
                if self.mapper.all_tasks_completed(self.id):
                    logger.info(f"Time {self.env.now:.2f}: Core {self.id} finished")
                    break
                yield self.env.timeout(10000)
                continue

            yield simpy.events.AnyOf(self.env, self.running_event)

            for event in list(self.running_event):
                if event.triggered:
                    task = self.event2task[event]
                    logger.info(f"Time {self.env.now:.2f}: Task {task.index} finished at Core {self.id}")
                    self.events[self.index2id[task.index]].end_time = int(round(self.env.now))
                    self.scheduler.update(task)
                    self.running_event.remove(event)
                    if task.index in self.executed_task:
                        raise RuntimeError(f"Repetitive execution: Core {self.id} task {task.index}")
                    self.executed_task[task.index] = 1
