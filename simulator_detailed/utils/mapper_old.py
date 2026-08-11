import copy
import math
import itertools

import json
import queue
import logging
from typing import List, Tuple, overload, Literal
from pydantic import BaseModel, ValidationError

from .dfg import *
from ..configs.schemas.mapping_config import *
from .definitions import comp_operator, DimSlice, Slice


logger = logging.getLogger("Mapper")


def parse_mapping(filename: str) -> Network:
    with open(filename, 'r') as file:
        data = json.load(file)
        try:
            net = Network.model_validate(data)
            return net
        except ValidationError as e:
            print(e.json())
            raise


class NetworkMapper:
    def __init__(self, network, x_size=4, y_size=4):
        self.network = network
        self.x_size = x_size
        self.y_size = y_size
        self.dfg = DFG()
        self.node_counter = 0
        self.all_tasks_completed_ntask = -1
        self.all_tasks_completed_counter = 0


    def clear(self):
        self.dfg = DFG()
        self.node_counter = 0
        self.all_tasks_completed_ntask = -1
        self.all_tasks_completed_counter = 0

    def shift(self, layer_id: int, src: int, dst: int):
        pass

    def split(self, layer_id: int, src: int, dst: int):
        pass

    def replace(self, layer_id: int, src: int, dst: int):
        pass

    def remove(self, layer_id: int, src: int, dst: int):
        pass

    def apply_local_remap(self, layer_id: int, action_type: str, src_core: int, dst_core: int, trace = None):
        pass

    def xy2x(self, x: int, y: int) -> int:
        return x * self.y_size + y


    def x2xy(self, x: int) -> Tuple[int, int]:
        return x // self.y_size, x % self.y_size
    

    def get_core_ids(self, layer: Layer) -> List[int]:
        core_ids = []
        for feature in layer.input_feature:
            for block in feature.blocks:
                core_ids.extend([self.xy2x(core.x, core.y) for core in block.cores])
        return core_ids

    
    @overload
    def get_slice(self, core_id: int, features, type: Literal[0]) -> Tuple[str, int, List[DimSlice]]: ...
    @overload
    def get_slice(self, core_id: int, features, type: Literal[2]) -> Tuple[str, List[int], List[DimSlice]]: ...
    @overload
    def get_slice(self, core_id: int, features, type: Literal[1]) -> Tuple[str, List[DimSlice]]: ...
    def get_slice(self, core_id: int, features, type: int):
        for feature in features:
            for block in feature.blocks:
                for core in block.cores:
                    if self.xy2x(core.x, core.y) == core_id:
                        if type == 2:
                            return feature.dest, feature.next, block.tensor_slice
                        elif type == 0:
                            return feature.source, feature.source_layer_id, block.tensor_slice
                        else:
                            return feature.source, block.tensor_slice
                        
        raise RuntimeError(f"Core_id {core_id} not found")


    def fetch(self, slice: List[DimSlice], fetch: Partition, time: int, type: int) -> List[DimSlice]:
        fetch_copy = Partition(dims=[dim for dim in fetch.dims])
        if type == 1:
            fetch_copy.dims[2] = fetch_copy.dims[1]
            fetch_copy.dims[0] = fetch_copy.dims[1] = fetch_copy.dims[3] = 1
        elif type == 2:
            fetch_copy.dims[1] = 1

        fetch_slice = []
        dim_times = []
        tot_num = fetch_copy.num()
        for num in fetch_copy.dims:
            tot_num /= num
            dim_times.append(time // tot_num % num)

        for idx, dim_slice in enumerate(slice):
            interval_length = (dim_slice.end - dim_slice.start) // fetch_copy.dims[idx]
            remain_length = (dim_slice.end - dim_slice.start) % fetch_copy.dims[idx]

            dim_start = dim_slice.start + dim_times[idx] * interval_length + min(dim_times[idx], remain_length)
            dim_end = dim_start + interval_length + (1 if dim_times[idx] < remain_length else 0)
            fetch_slice.append(DimSlice(start=dim_start, end=dim_end))
        return fetch_slice
    

    def get_group(self, network: Network) -> dict[int, int]:
        group_dict = {}
        for layer in network.layers:
            group_dict[layer.layer_id] = layer.layer_group_id
        return group_dict
    

    def intersect(self, x: List[DimSlice], y: List[DimSlice]) -> Tuple[bool, List[DimSlice]]:
        res = []
        intersect_flag = True

        for i in range(len(x)):
            start = max(x[i].start, y[i].start)
            end = min(x[i].end, y[i].end)

            if start >= end:
                intersect_flag = False
                res.append(DimSlice(start=0, end=0))
            else:
                res.append(DimSlice(start=start, end=end))
        return intersect_flag, res


    def batch_offset(self, slice: List[DimSlice], time: int) -> List[DimSlice]:
        res = []
        new_start = slice[0].start + time * (slice[0].end - slice[0].start)
        new_end = slice[0].end + time * (slice[0].end - slice[0].start)
        res.append(DimSlice(start=new_start, end=new_end))
        for dim_id in range(1, len(slice)):
            res.append(slice[dim_id])
        return res
    

    def zero_degree(self) -> List[DFGNode]:
        nodes = []
        for node_id in range(1, self.node_counter+1):
            cur_node = self.dfg.get_node(node_id)
            assert cur_node is not None
            if len(cur_node.parent) == 0:
                cur_node.ready = True
                cur_node.executed = True
                nodes.append(cur_node)
        return nodes
        

    def toposort(self) -> List[int]:
        self.topo_order = []
        my_queue = queue.Queue()
        node_in_degree = [0 for _ in range(self.node_counter+1)]

        for node_id in range(1, self.node_counter+1):
            cur_node = self.dfg.get_node(node_id)
            assert cur_node is not None
            node_in_degree[node_id] = len(cur_node.parent)
            if node_in_degree[node_id] == 0:
                my_queue.put(cur_node)

        while not my_queue.empty():
            cur_node = my_queue.get()
            self.topo_order.append(cur_node.index)

            for child_id in cur_node.child:
                node_in_degree[child_id] -= 1
                if node_in_degree[child_id] == 0:
                    child_node = self.dfg.get_node(child_id)
                    assert child_node is not None
                    my_queue.put(child_node)

        return self.topo_order
    

    def update(self, src_node: DFGNode, dst_node: DFGNode):
        logger.debug(f"updating: {src_node.index} -> {dst_node.index}")

        if dst_node.index == 3369:
            logger.debug(f"before update: {dst_node.received_input} / {dst_node.input_slice().size()}")

        match src_node.operation:
            # update received input size 
            # LOAD_FEAT -> CONV/POOL
            # RECV -> CONV/POOL
            case OperatorType.LOAD_FEAT | OperatorType.RECV:
                flag, intersection = self.intersect(src_node.input_size, dst_node.input_size)
                dst_node.received_input += Slice(tensor_slice=intersection).size()
            # STORE -> LOAD_FEAT
            # SEND -> RECV
            case OperatorType.STORE | OperatorType.SEND:
                flag, intersection = self.intersect(src_node.output_size, dst_node.input_size)
                dst_node.received_input += Slice(tensor_slice=intersection).size()  
                # if flag:
                #     dst_node.received_input += Slice(tensor_slice=intersection).size()  
                # else:
                #     if len(dst_node.parent) == 1:
                #         dst_node.received_input += dst_node.input_slice().size()

            # update recerved weight size
            # LOAD_WGT -> CONV/POOL
            case OperatorType.LOAD_WGT:
                dst_node.received_weight += src_node.weight_slice().size()

            # only SEND and STORE can be triggered
            # no need to update received size
            # COMP -> STORE/SEND
            case OperatorType.CONV | OperatorType.POOL | OperatorType.FC:
                dst_node.ready = True

        if dst_node.index == 3369:
            logger.debug(f"after update: {dst_node.received_input} / {dst_node.input_slice().size()}")

        # update dst_node, STORE/SEND have been updated above, LOAD_WGT doesn't need updating
        # CONV/POOL/FC
        if dst_node.operation in comp_operator:
            if dst_node.input_slice().size() != None:
                if dst_node.received_input != dst_node.input_slice().size():
                    return
            if dst_node.weight_slice().size() != None:
                if dst_node.received_weight != dst_node.weight_slice().size():
                    return
            dst_node.ready = True

        # LOAD_FEAT
        if dst_node.operation == OperatorType.LOAD_FEAT:
            if dst_node.input_slice().size() != None:
                if dst_node.received_input != dst_node.input_slice().size():
                    return
            dst_node.ready = True

        # RECV
        if dst_node.operation == OperatorType.RECV:
            if dst_node.input_slice().size() != None:
                if dst_node.received_input != dst_node.input_slice().size():
                    return
            dst_node.ready = True

    
    def all_tasks_completed(self, core_id: int):
        for index, node in self.dfg.nodes.items():
            if not node.finished:
                if index != self.all_tasks_completed_ntask:
                    self.all_tasks_completed_ntask = index
                    self.all_tasks_completed_counter = 1
                    # print(f"task {index} has not been executed. [{self.all_tasks_completed_counter} times by core {core_id}]")
                else:
                    self.all_tasks_completed_counter += 1
                    # print(f"task {index} has not been executed. [{self.all_tasks_completed_counter} times by core {core_id}]")
                return False
        return True


    def gen_dfg(self):
        self.clear()

        layer_group = self.get_group(self.network)
        send_node_ids = [[] for _ in range(len(self.network.layers))]

        for layer in self.network.layers:
            # if layer.layer_id >= 12:
            #     return
            core_num = layer.output_partition.num()
            repeat_time = layer.input_fetch.num()
            core_ids = self.get_core_ids(layer)

            for b_time in range(self.network.batch_size // layer.layer_batch_size):
                for index in range(core_num):
                    i_src, i_dst, input_slice = self.get_slice(core_ids[index], layer.input_feature, 0)
                    o_src, o_dst, output_slice = self.get_slice(core_ids[index], layer.output_feature, 2)

                    input_slice = self.batch_offset(input_slice, b_time)
                    output_slice = self.batch_offset(output_slice, b_time)

                    wgt_slice: List[DimSlice] = []
                    if layer.type != "pool":
                        w_src, wgt_slice = self.get_slice(core_ids[index], layer.wgt_feature, 1)

                    last_node = None
                    for time in range(repeat_time):
                        input_nodes = []
                        compute_nodes = []
                        output_nodes = []

                        # input node
                        input_slice_fetch = self.fetch(input_slice, layer.input_fetch, time, 0)

                        if i_src == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(index=self.node_counter, operation=OperatorType.LOAD_FEAT, 
                                            core_id=core_ids[index], input_size=input_slice_fetch)
                            input_nodes.append(self.node_counter)

                            for sender in send_node_ids[layer.layer_id-1]:
                                flag, intersection = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                if flag:
                                    self.dfg.add_edge(sender, self.node_counter)

                        else:
                            for sender in send_node_ids[i_dst]:
                                flag, intersection = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                if not flag: 
                                    continue
                                if i_dst not in layer_group:
                                    raise RuntimeError(f"Destination layer {i_dst} not in layer_group map")
                                
                                if layer_group[layer.layer_id] == layer_group[i_dst]:
                                    # intra-group cross-layer
                                    self.node_counter += 1
                                    self.dfg.add_node(index=self.node_counter, operation=OperatorType.RECV,
                                                    core_id=core_ids[index], input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)
                                else:
                                    # inter-group cross-layer
                                    self.node_counter += 1
                                    self.dfg.add_node(index=self.node_counter, operation=OperatorType.LOAD_FEAT, 
                                                    core_id=core_ids[index], input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)
                        
                        if layer.type != "pool":
                            wgt_slice_fetch = self.fetch(wgt_slice, layer.input_fetch, time, 1)
                            self.node_counter += 1
                            self.dfg.add_node(index=self.node_counter, operation=OperatorType.LOAD_WGT,
                                            core_id=core_ids[index], weight_size=wgt_slice_fetch)
                            input_nodes.append(self.node_counter)
                        else:
                            wgt_slice_fetch = []
                        
                        # compute node
                        output_slice_fetch = self.fetch(output_slice, layer.input_fetch, time, 2)
                        self.node_counter += 1
                        match layer.type:
                            case "conv": operation = OperatorType.CONV
                            case "pool": operation = OperatorType.POOL
                            case "fc": operation = OperatorType.FC
                            case _: raise ValueError(f"Unknown layer type: {layer.type}")

                        self.dfg.add_node(index=self.node_counter, operation=operation,
                                        core_id=core_ids[index], input_size=input_slice_fetch,
                                        weight_size=wgt_slice_fetch, output_size=output_slice_fetch)
                        compute_nodes.append(self.node_counter)
                        
                        for input_node in input_nodes:
                            # last_node -> input_nodes(not wgt nodes)
                            # if last_node is not None:                                
                            #     if len(self.dfg.nodes[input_node].weight_size) == 0:
                            #         self.dfg.add_edge(last_node, input_node)
                            # input_nodes -> compute_node
                            self.dfg.add_edge(input_node, self.node_counter)
                        
                        # store node
                        if o_src == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(index=self.node_counter, operation=OperatorType.STORE,
                                            core_id=core_ids[index], output_size=output_slice_fetch)
                            send_node_ids[layer.layer_id].append(self.node_counter)
                            output_nodes.append(self.node_counter)
                        else: 
                            for dst in o_dst:
                                if layer_group[layer.layer_id] == layer_group[dst]:
                                    # intra-group cross-layer
                                    self.node_counter += 1
                                    self.dfg.add_node(index=self.node_counter, operation=OperatorType.SEND,
                                                    core_id=core_ids[index], output_size=output_slice_fetch)
                                    send_node_ids[layer.layer_id].append(self.node_counter)
                                    output_nodes.append(self.node_counter)
                                else:
                                    # inter-group cross-layer
                                    self.node_counter += 1
                                    self.dfg.add_node(index=self.node_counter, operation=OperatorType.STORE,
                                                    core_id=core_ids[index], output_size=output_slice_fetch)
                                    send_node_ids[layer.layer_id].append(self.node_counter)
                                    output_nodes.append(self.node_counter)
                        last_node = self.node_counter

                        # compute_node -> store_node
                        for output_node in output_nodes:
                            self.dfg.add_edge(compute_nodes[0], output_node)

        # print(send_node_ids)