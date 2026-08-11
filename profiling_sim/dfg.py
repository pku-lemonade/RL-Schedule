from typing import Dict, List, Optional
from .definitions import DimSlice, OperatorType, Slice


class DFGNode:
    def __init__(self, index: int, operation: OperatorType, core_id: int,
                 input_size: List[DimSlice] = None,
                 output_size: List[DimSlice] = None,
                 weight_size: List[DimSlice] = None,
                 element_bytes: int = 1):
        self.index = index
        self.operation = operation
        self.core_id = core_id
        self.input_size = input_size or []
        self.output_size = output_size or []
        self.weight_size = weight_size or []
        self.element_bytes = element_bytes
        self.father: List[int] = []
        self.son: List[int] = []
        self.received_input = 0
        self.received_weight = 0
        self.ready: bool = False
        self.executed: bool = False
        self.finished: bool = False

    def input_slice(self):
        return Slice(tensor_slice=self.input_size)

    def weight_slice(self):
        return Slice(tensor_slice=self.weight_size)

    def output_slice(self):
        return Slice(tensor_slice=self.output_size)

    def add_father(self, node_index: int):
        if node_index not in self.father:
            self.father.append(node_index)

    def add_son(self, node_index: int):
        if node_index not in self.son:
            self.son.append(node_index)


class DFG:
    def __init__(self):
        self.nodes: Dict[int, DFGNode] = {}

    def add_node(self, index, operation, core_id,
                 input_size=None, output_size=None, weight_size=None,
                 element_bytes: int = 1):
        if index in self.nodes:
            raise RuntimeError(f"Node {index} already exists")
        node = DFGNode(index, operation, core_id, input_size, output_size,
                       weight_size, element_bytes)
        self.nodes[index] = node
        return node

    def add_edge(self, from_index, to_index):
        if from_index not in self.nodes or to_index not in self.nodes:
            raise RuntimeError("Both nodes must exist")
        self.nodes[from_index].add_son(to_index)
        self.nodes[to_index].add_father(from_index)

    def get_node(self, index):
        return self.nodes.get(index)
