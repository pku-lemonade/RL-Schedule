import logging
from typing import List
from .definitions import Slice, Message, OperatorType
from .dfg import DFGNode

logger = logging.getLogger("Task")

task_priority = {
    OperatorType.STORE.name: 0,
    OperatorType.SEND.name: 1,
    OperatorType.CONV.name: 2,
    OperatorType.POOL.name: 2,
    OperatorType.FC.name: 2,
    OperatorType.LOAD_FEAT.name: 3,
    OperatorType.LOAD_WGT.name: 3,
    OperatorType.RECV.name: 3,
}


class Task:
    def __init__(self, node: DFGNode):
        self.index = node.index
        self.operation = node.operation
        self.input_shape = node.input_size
        self.output_shape = node.output_size
        self.weight_shape = node.weight_size
        self.core_id = node.core_id
        self.dependencies: List[int] = node.father
        self.successors: List[int] = node.son
        self.received_input = node.received_input
        self.received_weight = node.received_weight
        self.ready = node.ready
        self.element_bytes = getattr(node, 'element_bytes', 1)

    def input_size(self) -> int:
        if not self.input_shape:
            return 0
        return Slice(tensor_slice=self.input_shape).size()

    def weight_size(self) -> int:
        if not self.weight_shape:
            return 0
        return Slice(tensor_slice=self.weight_shape).size()

    def output_size(self) -> int:
        if not self.output_shape:
            return 0
        return Slice(tensor_slice=self.output_shape).size()

    def check_ready(self):
        if not self.dependencies:
            self.ready = True
            return self.ready
        if self.input_shape:
            if self.received_input != Slice(tensor_slice=self.input_shape).size():
                return False
        if self.weight_shape:
            if self.received_weight != Slice(tensor_slice=self.weight_shape).size():
                return False
        self.ready = True
        return self.ready

    def execute(self, core):
        self.check_ready()
        if not self.ready:
            raise RuntimeError(f'Task {self.index} on core {core.id} not ready')

        env = core.env
        match self.operation:
            case OperatorType.LOAD_FEAT:
                with core.sram_write.request() as wr_req:
                    yield wr_req
                    yield env.process(core.spm.allocate(self.input_size(), self.index))
                    yield env.process(core.lsu.occupy(self.input_size()))

            case OperatorType.LOAD_WGT:
                with core.sram_write.request() as wr_req:
                    yield wr_req
                    yield env.process(core.spm.allocate(self.weight_size(), self.index))
                    yield env.process(core.lsu.occupy(self.weight_size()))

            case OperatorType.CONV:
                with core.sram_write.request() as wr_req:
                    yield wr_req
                    yield env.process(core.spm.allocate(self.output_size(), self.index))
                batch = self.input_shape[0].end - self.input_shape[0].start
                c_in = self.input_shape[1].end - self.input_shape[1].start
                c_out = self.weight_shape[1].end - self.weight_shape[1].start
                k = self.weight_shape[3].end - self.weight_shape[3].start
                h_out = self.output_shape[2].end - self.output_shape[2].start
                w_out = self.output_shape[3].end - self.output_shape[3].start
                flops = batch * c_in * c_out * k * h_out * w_out
                core.events[core.index2id[self.index]].flops = flops
                with core.sram_read.request() as rd_req:
                    yield rd_req
                    if core.shadow_enabled:
                        cal = flops // core.tpu.flops
                        yield core.enter_matrix(self.index, cal)
                    else:
                        yield env.process(core.tpu.occupy(flops))
                yield env.process(core.spm.release(self.input_size() + self.weight_size(), self.index))

            case OperatorType.POOL:
                with core.sram_write.request() as wr_req:
                    yield wr_req
                    yield env.process(core.spm.allocate(self.output_size(), self.index))
                batch = self.input_shape[0].end - self.input_shape[0].start
                h_in = self.input_shape[2].end - self.input_shape[2].start
                w_in = self.input_shape[3].end - self.input_shape[3].start
                h_out = self.output_shape[2].end - self.output_shape[2].start
                w_out = self.output_shape[3].end - self.output_shape[3].start
                kh, kw = h_in // h_out, w_in // w_out
                flops = batch * kh * kw * h_out * w_out
                core.events[core.index2id[self.index]].flops = flops
                with core.sram_read.request() as rd_req:
                    yield rd_req
                    if core.shadow_enabled:
                        cal = flops // core.tpu.flops
                        yield core.enter_vector(self.index, cal)
                    else:
                        yield env.process(core.tpu.occupy(flops))
                yield env.process(core.spm.release(self.input_size() + self.weight_size(), self.index))

            case OperatorType.FC:
                pass

            case OperatorType.STORE:
                with core.sram_read.request() as rd_req:
                    yield rd_req
                    yield env.process(core.lsu.occupy(self.output_size()))
                yield env.process(core.spm.release(self.output_size(), self.index))

            case OperatorType.SEND:
                ch = yield core.nmc.acquire()
                try:
                    with core.nmc.injection_ports.request() as inj_req, \
                            core.sram_read.request() as rd_req:
                        yield inj_req
                        yield rd_req
                        if core.shadow_enabled:
                            yield core.enter_sramc(ch, upload=True, tag=self.index)
                        yield env.timeout(core.nmc.start_up_time)
                        for son in self.successors:
                            node = core.mapper.dfg.get_node(son)
                            msg = Message(src=core.id, dst=node.core_id,
                                          index=self.index,
                                          data=node.input_slice().tensor_slice,
                                          element_bytes=self.element_bytes)
                            yield core.data_out.put(msg)
                    yield env.process(core.spm.release(self.output_size(), self.index))
                finally:
                    yield core.nmc.release(ch)

            case OperatorType.RECV:
                ch = yield core.nmc.acquire()
                try:
                    yield env.process(core.spm.allocate(self.input_size(), self.index))
                    yield core.data_in.get()
                    with core.sram_write.request() as wr_req:
                        yield wr_req
                        if core.shadow_enabled:
                            yield core.enter_sramc(ch, upload=False, tag=self.index)
                finally:
                    yield core.nmc.release(ch)

    def __lt__(self, other: "Task") -> bool:
        p_self = task_priority[self.operation.name]
        p_other = task_priority[other.operation.name]
        if p_self != p_other:
            return p_self < p_other
        return self.index < other.index
