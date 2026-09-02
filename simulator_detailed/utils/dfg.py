
from .definitions import (
    DimSlice,
    NMCShapeMode,
    NoCChannel,
    OperatorType,
    Slice,
)


class DFGNode:
    def __init__(
        self,
        index: int,
        operation: OperatorType,
        core_id: int,
        input_size: list[DimSlice] | None = None,
        output_size: list[DimSlice] | None = None,
        weight_size: list[DimSlice] | None = None,
        *,
        fabric_id: NoCChannel = NoCChannel.CH0,
        nmc_shape_mode: NMCShapeMode = NMCShapeMode.DYNAMIC,
    ):
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
        self.fabric_id = fabric_id
        self.nmc_shape_mode = nmc_shape_mode

        self.input_size = list(input_size or ())
        self.output_size = list(output_size or ())
        self.weight_size = list(weight_size or ())

        self.parent: list[int] = []
        self.child: list[int] = []

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
        self.nodes: dict[int, DFGNode] = {}

    def add_node(
        self,
        index: int,
        operation: OperatorType,
        core_id: int,
        input_size: list[DimSlice] | None = None,
        output_size: list[DimSlice] | None = None,
        weight_size: list[DimSlice] | None = None,
        *,
        fabric_id: NoCChannel = NoCChannel.CH0,
        nmc_shape_mode: NMCShapeMode = NMCShapeMode.DYNAMIC,
    ) -> DFGNode:
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
        
        node = DFGNode(
            index,
            operation,
            core_id,
            input_size,
            output_size,
            weight_size,
            fabric_id=fabric_id,
            nmc_shape_mode=nmc_shape_mode,
        )
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
        
        source = self.nodes[from_index]
        destination = self.nodes[to_index]
        if (
            source.operation is OperatorType.SEND
            and destination.operation is OperatorType.RECV
            and source.fabric_id is not destination.fabric_id
        ):
            raise ValueError(
                f"SEND {from_index} uses {source.fabric_id.name} but paired "
                f"RECV {to_index} uses {destination.fabric_id.name}"
            )
        source.add_child(to_index)
        destination.add_parent(from_index)

    def get_node(self, index: int) -> DFGNode | None:
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
