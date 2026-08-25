from pydantic import BaseModel
from enum import Enum, auto
from typing import List, Dict, Optional, Tuple

from .definitions import DimSlice, Slice, OperatorType


class DFGNode:
    def __init__(self, index: int, operation: OperatorType, core_id: int,
                 input_size: List[DimSlice] = [], output_size: List[DimSlice] = [],
                 weight_size: List[DimSlice] = []):
        """
        Initialize a DFG node
        :param index: unique node index
        :param operation: operation type, e.g., 'conv', 'load', 'send'
        :param core_id: the core ID this node is mapped to
        :param input_size: input size (N,C,H,W)
        :param output_size: output size (N,C,H,W)
        :param weight_size: weight size (N,C,H,W)
        """
        self.index = index
        self.operation = operation
        self.core_id = core_id

        self.input_size = input_size
        self.output_size = output_size
        self.weight_size = weight_size

        self.parent: List[int] = []
        self.child: List[int] = []

        # state variables
        self.received_input = 0
        self.received_weight = 0
        self.ready: bool = False
        self.executed: bool = False
        self.finished: bool = False


    def input_slice(self) -> Slice:
        input_slice = Slice(tensor_slice=self.input_size)
        return input_slice
    
    def weight_slice(self) -> Slice:
        weight_slice = Slice(tensor_slice=self.weight_size)
        return weight_slice

    def output_slice(self) -> Slice:
        output_slice = Slice(tensor_slice=self.output_size)
        return output_slice


    def add_parent(self, node_index: int):
        """Add a parent (upstream) node"""
        if node_index not in self.parent:
            self.parent.append(node_index)


    def add_child(self, node_index: int):
        """Add a child (downstream) node"""
        if node_index not in self.child:
            self.child.append(node_index)


class DFG:
    def __init__(self):
        self.nodes: Dict[int, DFGNode] = {}

    def add_node(self, index: int, operation: OperatorType, core_id: int,
                 input_size: List[DimSlice] = [], output_size: List[DimSlice] = [],
                 weight_size: List[DimSlice] = []) -> DFGNode:
        """
        Add a node to the DFG
        :param index: unique node index
        :param operation: operation type
        :param core_id: core mapping
        :param input_size: input size
        :param output_size: output size
        :param weight_size: weight size
        """
        if index in self.nodes:
            raise RuntimeError(f"Node with index {index} already exists")
        
        node = DFGNode(index, operation, core_id, input_size, output_size, weight_size)
        self.nodes[index] = node
        return node

    def add_edge(self, from_index: int, to_index: int):
        """
        Add a dependency edge between nodes
        :param from_index: upstream node index
        :param to_index: downstream node index
        """
        if from_index not in self.nodes or to_index not in self.nodes:
            raise RuntimeError("Both nodes must exist in DFG before adding an edge")
        
        self.nodes[from_index].add_child(to_index)
        self.nodes[to_index].add_parent(from_index)

    def get_node(self, index: int) -> Optional[DFGNode]:
        """Retrieve a node by index"""
        return self.nodes.get(index)
    
    def print(self, filename: str):
        with open(filename, 'w') as file:
            for index, node in self.nodes.items():
                print(f"Node {index}: Operation={node.operation.name}, Core={node.core_id}", file=file)
                print(f"    Input:{node.input_size}", file=file)
                print(f"    Weight:{node.weight_size}", file=file)
                print(f"    Output:{node.output_size}", file=file)
                print(f"    Link: {node.child}", file=file)
                print(f"    Received input/weight:{node.received_input} {node.received_weight}", file=file)
                print(f"    Ready: {node.ready}", file=file)
