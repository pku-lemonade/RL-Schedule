from __future__ import annotations

import logging
from typing import List, Protocol

import simpy
from simpy.events import ProcessGenerator

from ..endpoint_registry import EndpointRegistry
from ..pe_channel import NMCChannel
from .definitions import Event, Message, NoCChannel, NodeType, OperatorType, Slice
from .dfg import DFGNode
from .mapper import NetworkMapper

logger = logging.getLogger("Task")


class _Scratchpad(Protocol):
    def allocate(self, size: int, task_index: int) -> ProcessGenerator: ...

    def release(self, size: int, task_index: int) -> ProcessGenerator: ...


class _LSU(Protocol):
    def occupy(self, data_size: int) -> ProcessGenerator: ...


class _TPU(Protocol):
    def occupy(self, flop: int) -> ProcessGenerator: ...


class TaskCore(Protocol):
    id: int
    env: simpy.Environment
    spm: _Scratchpad
    lsu: _LSU
    tpu: _TPU
    mapper: NetworkMapper
    endpoint_registry: EndpointRegistry
    events: List[Event]
    index2id: dict[int, int]

    def nmc_channel_for(self, fabric_id: NoCChannel) -> NMCChannel: ...

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
        self.fabric_id = node.fabric_id
        self.nmc_shape_mode = node.nmc_shape_mode

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


    def execute(self, core: TaskCore) -> ProcessGenerator:
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
                channel = core.nmc_channel_for(self.fabric_id)
                send_node = core.mapper.dfg.get_node(self.index)
                if send_node is None:
                    raise RuntimeError(f"missing SEND task {self.index} in DFG")
                for child in self.successors:
                    node = core.mapper.dfg.get_node(child)
                    if node is None:
                        raise RuntimeError(
                            f"SEND task {self.index} has missing successor {child}"
                        )
                    self._validate_send_receive_pair(send_node, node)
                    message = Message(
                        src=channel.binding.address,
                        dst=core.endpoint_registry.resolve(
                            NodeType.PE,
                            node.core_id,
                            fabric_id=self.fabric_id,
                        ),
                        index=node.index,
                        data=node.input_slice().tensor_slice,
                        nmc_shape_mode=self.nmc_shape_mode,
                    )
                    yield channel.send(message)
                    
                yield env.process(core.spm.release(size=self.output_size(), task_index=self.index))

            case OperatorType.RECV:
                send_node = self._paired_send_node(core)
                receive_node = core.mapper.dfg.get_node(self.index)
                if receive_node is None:
                    raise RuntimeError(f"missing RECV task {self.index} in DFG")
                self._validate_send_receive_pair(send_node, receive_node)
                channel = core.nmc_channel_for(self.fabric_id)
                yield env.process(core.spm.allocate(self.input_size(), task_index=self.index))
                message = Message(
                    src=core.endpoint_registry.resolve(
                        NodeType.PE,
                        send_node.core_id,
                        fabric_id=self.fabric_id,
                    ),
                    dst=channel.binding.address,
                    index=self.index,
                    data=self.input_shape,
                    nmc_shape_mode=send_node.nmc_shape_mode,
                )
                yield channel.recv_message(message, self.nmc_shape_mode)

    @staticmethod
    def _validate_send_receive_pair(
        send_node: DFGNode,
        receive_node: DFGNode,
    ) -> None:
        if send_node.operation is not OperatorType.SEND:
            raise ValueError(
                f"task {send_node.index} is not a SEND command"
            )
        if receive_node.operation is not OperatorType.RECV:
            raise NotImplementedError(
                f"SEND task {send_node.index} cannot execute successor "
                f"{receive_node.index} of type {receive_node.operation.name}"
            )
        if send_node.fabric_id is not receive_node.fabric_id:
            raise ValueError(
                f"SEND {send_node.index} uses {send_node.fabric_id.name} but "
                f"paired RECV {receive_node.index} uses "
                f"{receive_node.fabric_id.name}"
            )

    def _paired_send_node(self, core: TaskCore) -> DFGNode:
        if len(self.dependencies) != 1:
            raise ValueError(
                f"RECV task {self.index} requires exactly one paired SEND, "
                f"found {len(self.dependencies)}"
            )
        send_index = self.dependencies[0]
        send_node = core.mapper.dfg.get_node(send_index)
        if send_node is None:
            raise RuntimeError(
                f"RECV task {self.index} has missing parent {send_index}"
            )
        return send_node


    def __lt__(self, other: "Task") -> bool:
        # print(task_priority)
        if task_priority[self.operation.name] != task_priority[other.operation.name]:
            return task_priority[self.operation.name] < task_priority[other.operation.name]
        else:
            return self.index < other.index
