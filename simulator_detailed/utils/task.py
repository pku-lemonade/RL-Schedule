import logging
from typing import List

from .definitions import Message, NodeType, OperatorType, Slice
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
    OperatorType.RECV.name: 3
}

class Task:
    def __init__(self, node: DFGNode):
        # basic parameters
        self.index = node.index
        self.operation = node.operation
        self.input_shape = node.input_size
        self.output_shape = node.output_size
        self.weight_shape = node.weight_size
        self.core_id = node.core_id

        # dependencies
        self.dependencies: List[int] = node.parent
        self.successors: List[int] = node.child

        # state parameters
        self.received_input = node.received_input
        self.received_weight = node.received_weight
        self.ready = node.ready


    def input_size(self) -> int:
        if self.input_shape == []:
            return 0
        input_slice = Slice(tensor_slice=self.input_shape)
        # if input_slice.size() == None:
        #     return 0
        return input_slice.size()
    
    def weight_size(self) -> int:
        if self.weight_shape == []:
            return 0
        weight_slice = Slice(tensor_slice=self.weight_shape)
        # if weight_slice.size() == None:
        #     return 0
        return weight_slice.size()

    def output_size(self) -> int:
        if self.output_shape == []:
            return 0
        output_slice = Slice(tensor_slice=self.output_shape)
        # if output_slice.size() == None:
        #     return 0
        return output_slice.size()


    def check_ready(self):
        if len(self.dependencies) == 0:
            self.ready = True
            return self.ready
        
        # print(self.core_id, self.index, self.input_shape)
        if self.input_shape:
            input_slice = Slice(tensor_slice=self.input_shape)
            if self.received_input != input_slice.size():
                return False
        
        if self.weight_shape:
            weight_slice = Slice(tensor_slice=self.weight_shape)
            if self.received_weight != weight_slice.size():
                return False
        
        self.ready = True
        return self.ready


    def execute(self, core):
        # print(self.input_size())
        self.check_ready()
        if not self.ready:
            raise RuntimeError(f'Task {self.index} on core {core.id} is not ready for execution.')
        
        env = core.env
        # print(self.operation)
        match self.operation:
            case OperatorType.LOAD_FEAT:
                # spare space for executing task
                yield env.process(core.spm.allocate(size=self.input_size(), task_index=self.index))
                # execute the task
                yield env.process(core.lsu.occupy(data_size=self.input_size()))

            case OperatorType.LOAD_WGT:
                yield env.process(core.spm.allocate(size=self.weight_size(), task_index=self.index))
                yield env.process(core.lsu.occupy(data_size=self.weight_size()))

            case OperatorType.CONV:
                logger.debug(f"start running task {self.index}")

                yield env.process(core.spm.allocate(size=self.output_size(), task_index=self.index))

                logger.debug(f"successfully allocating space for task {self.index}")
                # compute the flops
                # input:  B:batch  C: channel_in  H: height_in  W: weight_in
                # weight: B:    1  C:channel_out  H:channel_in  W:       RxS
                # output: B:batch  C:channel_out  H:height_out  W:weight_out
                batch_size = self.input_shape[0].end - self.input_shape[0].start

                channel_in = self.input_shape[1].end - self.input_shape[1].start
                channel_out = self.weight_shape[1].end - self.weight_shape[1].start
                
                kernal_size = self.weight_shape[3].end - self.weight_shape[3].start

                height_out = self.output_shape[2].end - self.output_shape[2].start
                weight_out = self.output_shape[3].end - self.output_shape[3].start

                flops = batch_size * channel_in * channel_out * kernal_size * height_out * weight_out
                core.events[core.index2id[self.index]].flops = flops
                
                logger.debug(f"task {self.index}'s flops is {flops}")
                
                yield env.process(core.tpu.occupy(flop=flops))

                logger.debug(f"finish running task {self.index}")

                yield env.process(core.spm.release(size=self.input_size()+self.weight_size(), task_index=self.index))

                logger.debug(f"successfully release space for task {self.index}")

            case OperatorType.POOL:
                logger.debug(f"start running task {self.index}")

                yield env.process(core.spm.allocate(size=self.output_size(), task_index=self.index))

                logger.debug(f"successfully allocating space for task {self.index}")
                # input:  B:batch  C: channel_in  H: height_in  W: weight_in
                # weight: None
                # output: B:batch  C:channel_out  H:height_out  W:weight_out
                batch_size = self.input_shape[0].end - self.input_shape[0].start

                height_in = self.input_shape[2].end - self.input_shape[2].start
                weight_in = self.input_shape[3].end - self.input_shape[3].start

                height_out = self.output_shape[2].end - self.output_shape[2].start
                weight_out = self.output_shape[3].end - self.output_shape[3].start

                kernal_height = height_in // height_out
                kernal_weight = weight_in // weight_out
                flops = batch_size * kernal_height * kernal_weight * height_out * weight_out
                core.events[core.index2id[self.index]].flops = flops
                
                logger.debug(f"task {self.index}'s flops is {flops}")

                yield env.process(core.tpu.occupy(flop=flops))

                logger.debug(f"finish running task {self.index}")

                yield env.process(core.spm.release(size=self.input_size()+self.weight_size(), task_index=self.index))

                logger.debug(f"successfully release space for task {self.index}")

            case OperatorType.FC:
                pass

            case OperatorType.STORE:
                logger.debug(f"start running task {self.index}")

                # write feature back into dram
                yield env.process(core.lsu.occupy(data_size=self.output_size()))

                logger.debug(f"successfully writing task {self.index}")

                # release the space
                yield env.process(core.spm.release(size=self.output_size(), task_index=self.index))

                logger.debug(f"successfully release space for task {self.index}")

            case OperatorType.SEND:
                # start up time
                yield env.timeout(core.router.start_up_time)
                # put the feature into the corresponding router
                ### there should be only one successor, I think (Wrong, maybe more successors)
                for child in self.successors:
                    node = core.mapper.dfg.get_node(child)
                    # print("-" * 20)
                    # print(f"DEBUG: Attempting to create Message for child '{child}'")
                    # print(f"DEBUG: src = {core.id} (type: {type(core.id)})")
                    # print(f"DEBUG: dst = {node.core_id} (type: {type(node.core_id)})")
                    # print(f"DEBUG: data = {node.input_slice()} (type: {type(node.input_slice())})")
                    # print("-" * 20)
                    message = Message(
                        src=core.address,
                        dst=core.endpoint_registry.resolve(NodeType.PE, node.core_id),
                        index=self.index,
                        data=node.input_slice().tensor_slice,
                    )

                    yield core.data_out.put(message)
                    # yield env.process(core.spm.release(size=node.input_slice().size(), task_index=self.index))
                    
                yield env.process(core.spm.release(size=self.output_size(), task_index=self.index))

            case OperatorType.RECV:
                # allocate space for coming data
                yield env.process(core.spm.allocate(self.input_size(), task_index=self.index))
                # receive data from noc
                yield core.data_in.get()


    def __lt__(self, other: "Task") -> bool:
        # print(task_priority)
        if task_priority[self.operation.name] != task_priority[other.operation.name]:
            return task_priority[self.operation.name] < task_priority[other.operation.name]
        else:
            return self.index < other.index
