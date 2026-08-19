import copy
import json
import logging
import queue
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, overload

from pydantic import ValidationError

from .definitions import DimSlice, OperatorType, Slice, comp_operator
from .dfg import DFG, DFGNode
from ..configs.schemas.mapping_config import Block, Core, IFeature, Layer, Network, OFeature, Partition, WFeature


logger = logging.getLogger("Mapper")


@dataclass
class BlockBinding:
    core_id: int
    input_slice: List[DimSlice]
    output_slice: List[DimSlice]
    weight_slice: List[DimSlice]


@dataclass
class LayerView:
    layer_id: int
    layer_type: str
    layer_group_id: int
    layer_batch_size: int
    input_fetch: Partition
    input_source: str
    input_source_layer_id: int
    output_dest: str
    output_next: List[int]
    weight_source: Optional[str]
    input_follow_axes: Tuple[int, ...]
    bindings: List[BlockBinding] = field(default_factory=list)


@dataclass
class CertificationReport:
    accepted: bool
    action_type: str
    layer_id: int
    src_core: int
    dst_core: Optional[int]
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_mapping(filename: str) -> Network:
    with open(filename, "r") as file:
        data = json.load(file)
        try:
            return Network.model_validate(data)
        except ValidationError as exc:
            print(exc.json())
            raise


class NetworkMapper:
    def __init__(self, network: Network, x_size: int = 4, y_size: int = 4, bootstrap: bool = True):
        self.network = self._clone_network(network)
        self.x_size = x_size
        self.y_size = y_size
        self.dfg = DFG()
        self.node_counter = 0
        self.layer_node_ranges: Dict[int, Tuple[int, int]] = {}
        self.all_tasks_completed_ntask = -1
        self.all_tasks_completed_counter = 0
        self.last_certification: Optional[CertificationReport] = None
        self.layer_views: Dict[int, LayerView] = {}
        self.original_layer_views: Dict[int, LayerView] = {}
        if bootstrap:
            self.layer_views = self._build_layer_views()
            self.original_layer_views = copy.deepcopy(self.layer_views)

    def _clone_network(self, network: Optional[Network] = None) -> Network:
        target = self.network if network is None else network
        if hasattr(target, "model_copy"):
            return target.model_copy(deep=True)
        return copy.deepcopy(target)

    def snapshot_network(self) -> Network:
        return self._clone_network()

    def _make_candidate(self) -> "NetworkMapper":
        candidate = NetworkMapper(self.snapshot_network(), self.x_size, self.y_size, bootstrap=False)
        candidate.layer_views = copy.deepcopy(self.layer_views)
        candidate.original_layer_views = copy.deepcopy(self.original_layer_views)
        return candidate

    def clear(self):
        self.dfg = DFG()
        self.node_counter = 0
        self.layer_node_ranges = {}
        self.all_tasks_completed_ntask = -1
        self.all_tasks_completed_counter = 0

    def xy2x(self, x: int, y: int) -> int:
        return y * self.x_size + x

    def x2xy(self, x: int) -> Tuple[int, int]:
        return x % self.x_size, x // self.x_size

    def _clone_slice(self, tensor_slice: List[DimSlice]) -> List[DimSlice]:
        return [DimSlice(start=dim.start, end=dim.end) for dim in tensor_slice]

    def _slice_size(self, tensor_slice: List[DimSlice]) -> int:
        if not tensor_slice:
            return 0
        return Slice(tensor_slice=tensor_slice).size()

    def _bbox(self, slices: List[List[DimSlice]]) -> List[DimSlice]:
        if not slices:
            return []
        bbox = self._clone_slice(slices[0])
        for tensor_slice in slices[1:]:
            for axis, dim in enumerate(tensor_slice):
                bbox[axis].start = min(bbox[axis].start, dim.start)
                bbox[axis].end = max(bbox[axis].end, dim.end)
        return bbox

    def _slice_key(self, tensor_slice: List[DimSlice]) -> Tuple[Tuple[int, int], ...]:
        return tuple((dim.start, dim.end) for dim in tensor_slice)

    def _slices_equal(self, lhs: List[DimSlice], rhs: List[DimSlice]) -> bool:
        return self._slice_key(lhs) == self._slice_key(rhs)

    def _merge_slices(self, lhs: List[DimSlice], rhs: List[DimSlice]) -> List[DimSlice]:
        merged = []
        for left_dim, right_dim in zip(lhs, rhs):
            merged.append(
                DimSlice(
                    start=min(left_dim.start, right_dim.start),
                    end=max(left_dim.end, right_dim.end),
                )
            )
        return merged

    def _get_core_ids_from_network(self, layer: Layer) -> List[int]:
        core_ids = []
        for feature in layer.input_feature:
            for block in feature.blocks:
                core_ids.extend([self.xy2x(core.x, core.y) for core in block.cores])
        return core_ids

    def get_core_ids(self, layer: Layer) -> List[int]:
        view = self.layer_views.get(layer.layer_id)
        if view is not None:
            return [binding.core_id for binding in view.bindings]
        return self._get_core_ids_from_network(layer)

    @overload
    def get_slice(self, core_id: int, features, type_id: Literal[0]) -> Tuple[str, int, List[DimSlice]]: ...
    @overload
    def get_slice(self, core_id: int, features, type_id: Literal[2]) -> Tuple[str, List[int], List[DimSlice]]: ...
    @overload
    def get_slice(self, core_id: int, features, type_id: Literal[1]) -> Tuple[str, List[DimSlice]]: ...
    def get_slice(self, core_id: int, features, type_id: int):
        for feature in features:
            for block in feature.blocks:
                for core in block.cores:
                    if self.xy2x(core.x, core.y) == core_id:
                        if type_id == 2:
                            return feature.dest, feature.next, self._clone_slice(block.tensor_slice)
                        if type_id == 0:
                            return feature.source, feature.source_layer_id, self._clone_slice(block.tensor_slice)
                        return feature.source, self._clone_slice(block.tensor_slice)
        raise RuntimeError(f"Core_id {core_id} not found")

    def fetch(self, tensor_slice: List[DimSlice], fetch: Partition, time: int, type_id: int) -> List[DimSlice]:
        fetch_copy = Partition(dims=[dim for dim in fetch.dims])
        if type_id == 1:
            fetch_copy.dims[2] = fetch_copy.dims[1]
            fetch_copy.dims[0] = fetch_copy.dims[1] = fetch_copy.dims[3] = 1
        elif type_id == 2:
            fetch_copy.dims[1] = 1

        fetch_slice = []
        dim_times = []
        total = fetch_copy.num()
        for num in fetch_copy.dims:
            total /= num
            dim_times.append(time // total % num)

        for axis, dim_slice in enumerate(tensor_slice):
            interval_length = (dim_slice.end - dim_slice.start) // fetch_copy.dims[axis]
            remain_length = (dim_slice.end - dim_slice.start) % fetch_copy.dims[axis]
            dim_start = dim_slice.start + dim_times[axis] * interval_length + min(dim_times[axis], remain_length)
            dim_end = dim_start + interval_length + (1 if dim_times[axis] < remain_length else 0)
            fetch_slice.append(DimSlice(start=dim_start, end=dim_end))
        return fetch_slice

    def get_group(self, network: Network) -> Dict[int, int]:
        return {layer.layer_id: layer.layer_group_id for layer in network.layers}

    def intersect(self, x: List[DimSlice], y: List[DimSlice]) -> Tuple[bool, List[DimSlice]]:
        result = []
        flag = True
        for axis in range(len(x)):
            start = max(x[axis].start, y[axis].start)
            end = min(x[axis].end, y[axis].end)
            if start >= end:
                flag = False
                result.append(DimSlice(start=0, end=0))
            else:
                result.append(DimSlice(start=start, end=end))
        return flag, result

    def batch_offset(self, tensor_slice: List[DimSlice], time: int) -> List[DimSlice]:
        result = []
        new_start = tensor_slice[0].start + time * (tensor_slice[0].end - tensor_slice[0].start)
        new_end = tensor_slice[0].end + time * (tensor_slice[0].end - tensor_slice[0].start)
        result.append(DimSlice(start=new_start, end=new_end))
        for axis in range(1, len(tensor_slice)):
            result.append(DimSlice(start=tensor_slice[axis].start, end=tensor_slice[axis].end))
        return result

    def _slice_overlap(self, lhs: List[DimSlice], rhs: List[DimSlice]) -> bool:
        flag, _ = self.intersect(lhs, rhs)
        return flag

    def _select_sender_cover(
        self, sender_ids: List[int], target_slice: List[DimSlice]
    ) -> List[Tuple[int, List[DimSlice]]]:
        target_size = Slice(tensor_slice=target_slice).size()
        candidates: List[Tuple[int, List[DimSlice]]] = []
        seen_intersections = set()

        for sender in sender_ids:
            flag, intersection = self.intersect(self.dfg.nodes[sender].output_size, target_slice)
            if not flag:
                continue
            key = self._slice_key(intersection)
            if key in seen_intersections:
                continue
            seen_intersections.add(key)
            candidates.append((sender, intersection))

        if not candidates:
            return []

        def greedy_cover() -> List[Tuple[int, List[DimSlice]]]:
            chosen: List[Tuple[int, List[DimSlice]]] = []
            covered = 0
            for sender, intersection in candidates:
                if any(self._slice_overlap(intersection, existing) for _, existing in chosen):
                    continue
                chosen.append((sender, intersection))
                covered += Slice(tensor_slice=intersection).size()
                if covered == target_size:
                    return chosen
            return []

        greedy = greedy_cover()
        if greedy:
            return greedy

        sizes = [Slice(tensor_slice=intersection).size() for _, intersection in candidates]
        suffix_max = [0 for _ in range(len(candidates) + 1)]
        for idx in range(len(candidates) - 1, -1, -1):
            suffix_max[idx] = suffix_max[idx + 1] + sizes[idx]

        def dfs(index: int, chosen: List[Tuple[int, List[DimSlice]]], covered: int) -> Optional[List[Tuple[int, List[DimSlice]]]]:
            if covered == target_size:
                return list(chosen)
            if index >= len(candidates) or covered > target_size:
                return None
            if covered + suffix_max[index] < target_size:
                return None

            sender, intersection = candidates[index]
            inter_size = sizes[index]
            if not any(self._slice_overlap(intersection, existing) for _, existing in chosen):
                chosen.append((sender, intersection))
                result = dfs(index + 1, chosen, covered + inter_size)
                if result is not None:
                    return result
                chosen.pop()

            return dfs(index + 1, chosen, covered)

        exact = dfs(0, [], 0)
        return exact or []

    def zero_degree(self) -> List[DFGNode]:
        nodes = []
        for node_id in range(1, self.node_counter + 1):
            node = self.dfg.get_node(node_id)
            assert node is not None
            if len(node.parent) == 0:
                node.ready = True
                node.executed = True
                nodes.append(node)
        return nodes

    def toposort(self) -> List[int]:
        self.topo_order = []
        node_in_degree = [0 for _ in range(self.node_counter + 1)]
        work_queue = queue.Queue()

        for node_id in range(1, self.node_counter + 1):
            node = self.dfg.get_node(node_id)
            assert node is not None
            node_in_degree[node_id] = len(node.parent)
            if node_in_degree[node_id] == 0:
                work_queue.put(node)

        while not work_queue.empty():
            node = work_queue.get()
            self.topo_order.append(node.index)
            for child_id in node.child:
                node_in_degree[child_id] -= 1
                if node_in_degree[child_id] == 0:
                    work_queue.put(self.dfg.get_node(child_id))

        return self.topo_order

    def update(self, src_node: DFGNode, dst_node: DFGNode):
        match src_node.operation:
            case OperatorType.LOAD_FEAT | OperatorType.RECV:
                _, intersection = self.intersect(src_node.input_size, dst_node.input_size)
                dst_node.received_input += Slice(tensor_slice=intersection).size()
            case OperatorType.STORE | OperatorType.SEND:
                _, intersection = self.intersect(src_node.output_size, dst_node.input_size)
                dst_node.received_input += Slice(tensor_slice=intersection).size()
            case OperatorType.LOAD_WGT:
                dst_node.received_weight += src_node.weight_slice().size()
            case OperatorType.CONV | OperatorType.POOL | OperatorType.FC:
                dst_node.ready = True

        if dst_node.operation in comp_operator:
            if dst_node.input_slice().size() is not None and dst_node.received_input != dst_node.input_slice().size():
                return
            if dst_node.weight_slice().size() is not None and dst_node.received_weight != dst_node.weight_slice().size():
                return
            dst_node.ready = True

        if dst_node.operation == OperatorType.LOAD_FEAT:
            if dst_node.input_slice().size() is not None and dst_node.received_input != dst_node.input_slice().size():
                return
            dst_node.ready = True

        if dst_node.operation == OperatorType.RECV:
            if dst_node.input_slice().size() is not None and dst_node.received_input != dst_node.input_slice().size():
                return
            dst_node.ready = True

    def all_tasks_completed(self, core_id: int):
        for index, node in self.dfg.nodes.items():
            if not node.finished:
                if index != self.all_tasks_completed_ntask:
                    self.all_tasks_completed_ntask = index
                    self.all_tasks_completed_counter = 1
                else:
                    self.all_tasks_completed_counter += 1
                return False
        return True

    def _build_layer_views(self) -> Dict[int, LayerView]:
        views: Dict[int, LayerView] = {}
        for layer in self.network.layers:
            core_ids = self._get_core_ids_from_network(layer)
            bindings: List[BlockBinding] = []
            input_source: str = ""
            input_source_layer_id: int = 0
            output_dest: str = ""
            output_next: List[int] = []
            weight_source: Optional[str] = None
            input_slices = []

            for core_id in core_ids:
                input_source, input_source_layer_id, input_slice = self.get_slice(core_id, layer.input_feature, 0)
                output_dest, output_next, output_slice = self.get_slice(core_id, layer.output_feature, 2)
                weight_slice: List[DimSlice] = []
                if layer.type != "pool":
                    weight_source, weight_slice = self.get_slice(core_id, layer.wgt_feature, 1)

                bindings.append(
                    BlockBinding(
                        core_id=core_id,
                        input_slice=input_slice,
                        output_slice=output_slice,
                        weight_slice=weight_slice,
                    )
                )
                input_slices.append(input_slice)

            input_follow_axes = []
            for axis in range(len(bindings[0].input_slice)):
                values = {(tensor_slice[axis].start, tensor_slice[axis].end) for tensor_slice in input_slices}
                if len(values) > 1:
                    input_follow_axes.append(axis)

            views[layer.layer_id] = LayerView(
                layer_id=layer.layer_id,
                layer_type=layer.type,
                layer_group_id=layer.layer_group_id,
                layer_batch_size=layer.layer_batch_size,
                input_fetch=Partition(dims=list(layer.input_fetch.dims)),
                input_source=input_source,
                input_source_layer_id=input_source_layer_id,
                output_dest=output_dest,
                output_next=list(output_next),
                weight_source=weight_source,
                input_follow_axes=tuple(input_follow_axes),
                bindings=bindings,
            )
        return views

    def normalize_layer(self, layer_id: int) -> LayerView:
        return copy.deepcopy(self.layer_views[layer_id])

    def _make_core(self, core_id: int) -> Core:
        x, y = self.x2xy(core_id)
        return Core(x=x, y=y)

    def _sync_layer_to_network(self, layer_id: int):
        layer = self.network.layers[layer_id]
        view = self.layer_views[layer_id]

        input_feature = IFeature(
            source=view.input_source,
            source_layer_id=view.input_source_layer_id,
            blocks=[
                Block(cores=[self._make_core(binding.core_id)], tensor_slice=self._clone_slice(binding.input_slice))
                for binding in view.bindings
            ],
        )
        output_feature = OFeature(
            dest=view.output_dest,
            next=list(view.output_next),
            blocks=[
                Block(cores=[self._make_core(binding.core_id)], tensor_slice=self._clone_slice(binding.output_slice))
                for binding in view.bindings
            ],
        )
        layer.input_feature = [input_feature]
        layer.output_feature = [output_feature]
        if view.weight_source is not None and layer.type != "pool":
            weight_feature = WFeature(
                source=view.weight_source,
                blocks=[
                    Block(cores=[self._make_core(binding.core_id)], tensor_slice=self._clone_slice(binding.weight_slice))
                    for binding in view.bindings
                ],
            )
            layer.wgt_feature = [weight_feature]
        layer.output_partition = Partition(dims=[len(view.bindings), 1, 1, 1])

    def _layer_is_remapped(self, layer_id: int) -> bool:
        original = self.original_layer_views.get(layer_id)
        current = self.layer_views.get(layer_id)
        if original is None or current is None:
            return False
        if len(original.bindings) != len(current.bindings):
            return True
        for old_binding, new_binding in zip(original.bindings, current.bindings):
            if old_binding.core_id != new_binding.core_id:
                return True
            if self._slice_key(old_binding.input_slice) != self._slice_key(new_binding.input_slice):
                return True
            if self._slice_key(old_binding.output_slice) != self._slice_key(new_binding.output_slice):
                return True
            if self._slice_key(old_binding.weight_slice) != self._slice_key(new_binding.weight_slice):
                return True
        return False

    def gen_dfg_reference(self):
        self.clear()
        layer_group = self.get_group(self.network)
        send_node_ids = [[] for _ in range(len(self.network.layers))]

        for layer in self.network.layers:
            layer_begin = self.node_counter + 1
            core_num = layer.output_partition.num()
            repeat_time = layer.input_fetch.num()
            core_ids = self._get_core_ids_from_network(layer)

            for b_time in range(self.network.batch_size // layer.layer_batch_size):
                for index in range(core_num):
                    i_src, i_dst, input_slice = self.get_slice(core_ids[index], layer.input_feature, 0)
                    o_src, o_dst, output_slice = self.get_slice(core_ids[index], layer.output_feature, 2)

                    input_slice = self.batch_offset(input_slice, b_time)
                    output_slice = self.batch_offset(output_slice, b_time)

                    wgt_slice: List[DimSlice] = []
                    if layer.type != "pool":
                        _, wgt_slice = self.get_slice(core_ids[index], layer.wgt_feature, 1)

                    for time_id in range(repeat_time):
                        input_nodes = []
                        output_nodes = []

                        input_slice_fetch = self.fetch(input_slice, layer.input_fetch, time_id, 0)

                        if i_src == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.LOAD_FEAT, core_ids[index], input_size=input_slice_fetch)
                            input_nodes.append(self.node_counter)
                            for sender in send_node_ids[layer.layer_id - 1]:
                                flag, _ = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                if flag:
                                    self.dfg.add_edge(sender, self.node_counter)
                        else:
                            for sender in send_node_ids[i_dst]:
                                flag, intersection = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                if not flag:
                                    continue
                                if layer_group[layer.layer_id] == layer_group[i_dst]:
                                    self.node_counter += 1
                                    self.dfg.add_node(self.node_counter, OperatorType.RECV, core_ids[index], input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)
                                else:
                                    self.node_counter += 1
                                    self.dfg.add_node(self.node_counter, OperatorType.LOAD_FEAT, core_ids[index], input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)

                        if layer.type != "pool":
                            wgt_slice_fetch = self.fetch(wgt_slice, layer.input_fetch, time_id, 1)
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.LOAD_WGT, core_ids[index], weight_size=wgt_slice_fetch)
                            input_nodes.append(self.node_counter)
                        else:
                            wgt_slice_fetch = []

                        output_slice_fetch = self.fetch(output_slice, layer.input_fetch, time_id, 2)
                        self.node_counter += 1
                        operation = OperatorType.CONV if layer.type == "conv" else OperatorType.POOL if layer.type == "pool" else OperatorType.FC
                        self.dfg.add_node(self.node_counter, operation, core_ids[index], input_size=input_slice_fetch, weight_size=wgt_slice_fetch, output_size=output_slice_fetch)
                        compute_node = self.node_counter

                        for input_node in input_nodes:
                            self.dfg.add_edge(input_node, compute_node)

                        if o_src == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.STORE, core_ids[index], output_size=output_slice_fetch)
                            send_node_ids[layer.layer_id].append(self.node_counter)
                            output_nodes.append(self.node_counter)
                        else:
                            for dst in o_dst:
                                self.node_counter += 1
                                operation = OperatorType.SEND if layer_group[layer.layer_id] == layer_group[dst] else OperatorType.STORE
                                self.dfg.add_node(self.node_counter, operation, core_ids[index], output_size=output_slice_fetch)
                                send_node_ids[layer.layer_id].append(self.node_counter)
                                output_nodes.append(self.node_counter)

                        for output_node in output_nodes:
                            self.dfg.add_edge(compute_node, output_node)
            self.layer_node_ranges[layer.layer_id] = (layer_begin, self.node_counter)

    def gen_dfg(self):
        self.clear()
        layer_group = self.get_group(self.network)
        send_node_ids = [[] for _ in range(len(self.network.layers))]

        for layer in self.network.layers:
            layer_begin = self.node_counter + 1
            view = self.layer_views[layer.layer_id]
            repeat_time = view.input_fetch.num()
            bindings = view.bindings

            for b_time in range(self.network.batch_size // view.layer_batch_size):
                for binding in bindings:
                    input_slice = self.batch_offset(binding.input_slice, b_time)
                    output_slice = self.batch_offset(binding.output_slice, b_time)
                    weight_slice = self._clone_slice(binding.weight_slice)

                    for time_id in range(repeat_time):
                        input_nodes = []
                        output_nodes = []

                        input_slice_fetch = self.fetch(input_slice, view.input_fetch, time_id, 0)
                        source_layer_is_remapped = view.input_source_layer_id >= 0 and self._layer_is_remapped(view.input_source_layer_id)
                        if view.input_source == "dram" and source_layer_is_remapped:
                            matched_senders = self._select_sender_cover(send_node_ids[view.input_source_layer_id], input_slice_fetch)
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.LOAD_FEAT, binding.core_id, input_size=input_slice_fetch)
                            input_nodes.append(self.node_counter)
                            if matched_senders:
                                for sender, _ in matched_senders:
                                    self.dfg.add_edge(sender, self.node_counter)
                        elif view.input_source == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.LOAD_FEAT, binding.core_id, input_size=input_slice_fetch)
                            input_nodes.append(self.node_counter)
                            if layer.layer_id > 0:
                                for sender in send_node_ids[layer.layer_id - 1]:
                                    flag, _ = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                    if flag:
                                        self.dfg.add_edge(sender, self.node_counter)
                        else:
                            for sender in send_node_ids[view.input_source_layer_id]:
                                flag, intersection = self.intersect(self.dfg.nodes[sender].output_size, input_slice_fetch)
                                if not flag:
                                    continue
                                if layer_group[layer.layer_id] == layer_group[view.input_source_layer_id]:
                                    self.node_counter += 1
                                    self.dfg.add_node(self.node_counter, OperatorType.RECV, binding.core_id, input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)
                                else:
                                    self.node_counter += 1
                                    self.dfg.add_node(self.node_counter, OperatorType.LOAD_FEAT, binding.core_id, input_size=intersection)
                                    self.dfg.add_edge(sender, self.node_counter)
                                    input_nodes.append(self.node_counter)

                        if view.layer_type != "pool":
                            wgt_slice_fetch = self.fetch(weight_slice, view.input_fetch, time_id, 1)
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.LOAD_WGT, binding.core_id, weight_size=wgt_slice_fetch)
                            input_nodes.append(self.node_counter)
                        else:
                            wgt_slice_fetch = []

                        output_slice_fetch = self.fetch(output_slice, view.input_fetch, time_id, 2)
                        self.node_counter += 1
                        operation = OperatorType.CONV if view.layer_type == "conv" else OperatorType.POOL if view.layer_type == "pool" else OperatorType.FC
                        self.dfg.add_node(self.node_counter, operation, binding.core_id, input_size=input_slice_fetch, weight_size=wgt_slice_fetch, output_size=output_slice_fetch)
                        compute_node = self.node_counter

                        for input_node in input_nodes:
                            self.dfg.add_edge(input_node, compute_node)

                        if view.output_dest == "dram":
                            self.node_counter += 1
                            self.dfg.add_node(self.node_counter, OperatorType.STORE, binding.core_id, output_size=output_slice_fetch)
                            send_node_ids[layer.layer_id].append(self.node_counter)
                            output_nodes.append(self.node_counter)
                        else:
                            for dst in view.output_next:
                                self.node_counter += 1
                                operation = OperatorType.SEND if layer_group[layer.layer_id] == layer_group[dst] else OperatorType.STORE
                                self.dfg.add_node(self.node_counter, operation, binding.core_id, output_size=output_slice_fetch)
                                send_node_ids[layer.layer_id].append(self.node_counter)
                                output_nodes.append(self.node_counter)

                        for output_node in output_nodes:
                            self.dfg.add_edge(compute_node, output_node)
            self.layer_node_ranges[layer.layer_id] = (layer_begin, self.node_counter)

    def _compare_reference(self) -> Tuple[bool, Dict[str, Any]]:
        current_views = copy.deepcopy(self.layer_views)
        current_network = self.snapshot_network()
        self.gen_dfg_reference()
        ref_node_count = self.node_counter
        ref_topo = len(self.toposort())
        self.network = current_network
        self.layer_views = current_views
        self.gen_dfg()
        new_node_count = self.node_counter
        new_topo = len(self.toposort())
        return (
            ref_node_count == new_node_count and ref_topo == new_topo,
            {
                "reference_node_count": ref_node_count,
                "new_node_count": new_node_count,
                "reference_topo": ref_topo,
                "new_topo": new_topo,
            },
        )

    def _find_binding(self, view: LayerView, core_id: int) -> Optional[BlockBinding]:
        for binding in view.bindings:
            if binding.core_id == core_id:
                return binding
        return None

    def _changed_axes(self, lhs: List[DimSlice], rhs: List[DimSlice]) -> List[int]:
        axes = []
        for axis in range(len(lhs)):
            if lhs[axis].start != rhs[axis].start or lhs[axis].end != rhs[axis].end:
                axes.append(axis)
        return axes

    def _adjacent_axis(self, lhs: List[DimSlice], rhs: List[DimSlice]) -> Optional[int]:
        axes = self._changed_axes(lhs, rhs)
        if len(axes) != 1:
            return None
        axis = axes[0]
        for other_axis in range(len(lhs)):
            if other_axis == axis:
                continue
            if lhs[other_axis].start != rhs[other_axis].start or lhs[other_axis].end != rhs[other_axis].end:
                return None
        if lhs[axis].end == rhs[axis].start or rhs[axis].end == lhs[axis].start:
            return axis
        return None

    def _capacity(self, trace: Optional[Any], core_id: int) -> float:
        if trace is None or not getattr(trace, "time_slices", None):
            return 1.0
        slow_values = []
        util_values = []
        for time_slice in trace.time_slices:
            for core in time_slice.cores:
                if core.id == core_id:
                    slow_values.append(float(core.slow))
                    util_values.append(float(core.ultilization))
                    break
        if not slow_values:
            return 1.0
        slow = sum(slow_values) / len(slow_values)
        util = sum(util_values) / len(util_values)
        return max(0.05, (1.0 - slow) * (1.0 + max(0.0, 1.0 - util)))

    def _balanced_length(self, total_length: int, src_capacity: float, dst_capacity: float) -> int:
        if total_length <= 1:
            return total_length
        target = int(round(total_length * (src_capacity / max(src_capacity + dst_capacity, 1e-6))))
        return max(1, min(total_length - 1, target))

    def _estimated_block_cost(self, output_slice: List[DimSlice], weight_slice: List[DimSlice], capacity: float) -> float:
        output_volume = max(1, self._slice_size(output_slice))
        weight_volume = max(1, self._slice_size(weight_slice))
        return (output_volume * weight_volume) / max(capacity, 1e-6)

    def _split_slice(self, tensor_slice: List[DimSlice], axis: int, left_length: int) -> Tuple[List[DimSlice], List[DimSlice]]:
        left = self._clone_slice(tensor_slice)
        right = self._clone_slice(tensor_slice)
        cut = tensor_slice[axis].start + left_length
        left[axis] = DimSlice(start=tensor_slice[axis].start, end=cut)
        right[axis] = DimSlice(start=cut, end=tensor_slice[axis].end)
        return left, right

    def _choose_split_axis(self, binding: BlockBinding) -> Optional[int]:
        best_axis = None
        best_length = 0
        for axis, dim in enumerate(binding.output_slice):
            length = dim.end - dim.start
            if length > best_length and length > 1:
                best_axis = axis
                best_length = length
        return best_axis

    def shift(self, layer_id: int, src: int, dst: int, trace=None) -> Tuple[bool, str]:
        view = self.layer_views[layer_id]
        src_binding = self._find_binding(view, src)
        dst_binding = self._find_binding(view, dst)
        if src_binding is None or dst_binding is None:
            return False, "SHIFT requires both src and dst to be active in the layer"

        axis = self._adjacent_axis(src_binding.output_slice, dst_binding.output_slice)
        if axis is None:
            return False, "SHIFT requires adjacent blocks that differ on exactly one axis"

        src_current_length = src_binding.output_slice[axis].end - src_binding.output_slice[axis].start
        dst_current_length = dst_binding.output_slice[axis].end - dst_binding.output_slice[axis].start
        total_length = src_current_length + dst_current_length
        src_capacity = self._capacity(trace, src)
        dst_capacity = self._capacity(trace, dst)

        if src_binding.output_slice[axis].start < dst_binding.output_slice[axis].start:
            union_slice = [
                DimSlice(start=min(src_binding.output_slice[idx].start, dst_binding.output_slice[idx].start),
                         end=max(src_binding.output_slice[idx].end, dst_binding.output_slice[idx].end))
                if idx == axis else DimSlice(start=src_binding.output_slice[idx].start, end=src_binding.output_slice[idx].end)
                for idx in range(len(src_binding.output_slice))
            ]
        else:
            union_slice = [
                DimSlice(start=min(src_binding.output_slice[idx].start, dst_binding.output_slice[idx].start),
                         end=max(src_binding.output_slice[idx].end, dst_binding.output_slice[idx].end))
                if idx == axis else DimSlice(start=src_binding.output_slice[idx].start, end=src_binding.output_slice[idx].end)
                for idx in range(len(src_binding.output_slice))
            ]

        baseline_cost = max(
            self._estimated_block_cost(src_binding.output_slice, src_binding.weight_slice, src_capacity),
            self._estimated_block_cost(dst_binding.output_slice, dst_binding.weight_slice, dst_capacity),
        )

        best = None
        for candidate_src_length in range(1, src_current_length):
            if src_binding.output_slice[axis].start < dst_binding.output_slice[axis].start:
                cand_src, cand_dst = self._split_slice(union_slice, axis, candidate_src_length)
            else:
                cand_dst, cand_src = self._split_slice(union_slice, axis, total_length - candidate_src_length)

            merged_dst_weight = self._clone_slice(dst_binding.weight_slice)
            if not self._slices_equal(src_binding.weight_slice, dst_binding.weight_slice):
                merged_dst_weight = self._merge_slices(src_binding.weight_slice, dst_binding.weight_slice)
            cost = max(
                self._estimated_block_cost(cand_src, src_binding.weight_slice, src_capacity),
                self._estimated_block_cost(cand_dst, merged_dst_weight, dst_capacity),
            )
            if best is None or cost < best[0]:
                best = (cost, cand_src, cand_dst)

        if best is None or best[0] >= baseline_cost:
            return False, "SHIFT has no beneficial move from src to dst"

        new_src, new_dst = best[1], best[2]

        src_binding.output_slice = new_src
        dst_binding.output_slice = new_dst
        if axis in view.input_follow_axes:
            src_binding.input_slice[axis] = DimSlice(start=new_src[axis].start, end=new_src[axis].end)
            dst_binding.input_slice[axis] = DimSlice(start=new_dst[axis].start, end=new_dst[axis].end)
        if not self._slices_equal(src_binding.weight_slice, dst_binding.weight_slice):
            dst_binding.weight_slice = self._merge_slices(src_binding.weight_slice, dst_binding.weight_slice)
        return True, ""

    def split(self, layer_id: int, src: int, dst: int, trace=None) -> Tuple[bool, str]:
        view = self.layer_views[layer_id]
        src_binding = self._find_binding(view, src)
        if src_binding is None:
            return False, "SPLIT requires src to be active in the layer"
        if self._find_binding(view, dst) is not None:
            return False, "SPLIT requires dst to be inactive for the layer"

        axis = self._choose_split_axis(src_binding)
        if axis is None:
            return False, "Source block is too small to split"

        total_length = src_binding.output_slice[axis].end - src_binding.output_slice[axis].start
        src_length = self._balanced_length(total_length, self._capacity(trace, src), self._capacity(trace, dst))
        left_slice, right_slice = self._split_slice(src_binding.output_slice, axis, src_length)

        new_binding = copy.deepcopy(src_binding)
        src_binding.output_slice = left_slice
        new_binding.core_id = dst
        new_binding.output_slice = right_slice
        if axis in view.input_follow_axes:
            src_binding.input_slice[axis] = DimSlice(start=left_slice[axis].start, end=left_slice[axis].end)
            new_binding.input_slice[axis] = DimSlice(start=right_slice[axis].start, end=right_slice[axis].end)
        insert_index = view.bindings.index(src_binding) + 1
        view.bindings.insert(insert_index, new_binding)
        return True, ""

    def replace(self, layer_id: int, src: int, dst: int, trace=None) -> Tuple[bool, str]:
        view = self.layer_views[layer_id]
        src_binding = self._find_binding(view, src)
        dst_binding = self._find_binding(view, dst)
        if src_binding is None or dst_binding is None:
            return False, "REPLACE requires both src and dst to be active in the layer"
        src_binding.core_id, dst_binding.core_id = dst_binding.core_id, src_binding.core_id
        return True, ""

    def remove(self, layer_id: int, src: int, dst: int, trace=None) -> Tuple[bool, str]:
        view = self.layer_views[layer_id]
        src_binding = self._find_binding(view, src)
        dst_binding = self._find_binding(view, dst)
        if src_binding is None or dst_binding is None:
            return False, "REMOVE requires both src and dst to be active in the layer"

        axis = self._adjacent_axis(src_binding.output_slice, dst_binding.output_slice)
        if axis is None:
            return False, "REMOVE requires adjacent blocks that differ on exactly one axis"

        dst_binding.output_slice[axis] = DimSlice(
            start=min(src_binding.output_slice[axis].start, dst_binding.output_slice[axis].start),
            end=max(src_binding.output_slice[axis].end, dst_binding.output_slice[axis].end),
        )
        if axis in view.input_follow_axes:
            dst_binding.input_slice[axis] = DimSlice(
                start=min(src_binding.input_slice[axis].start, dst_binding.input_slice[axis].start),
                end=max(src_binding.input_slice[axis].end, dst_binding.input_slice[axis].end),
            )
        if not self._slices_equal(src_binding.weight_slice, dst_binding.weight_slice):
            dst_binding.weight_slice = self._merge_slices(src_binding.weight_slice, dst_binding.weight_slice)
        view.bindings = [binding for binding in view.bindings if binding.core_id != src]
        return True, ""

    def _validate_view(self, view: LayerView) -> Tuple[bool, str]:
        seen_cores = set()
        for binding in view.bindings:
            if binding.core_id in seen_cores:
                return False, f"Duplicate core {binding.core_id} in layer {view.layer_id}"
            seen_cores.add(binding.core_id)
            if self._slice_size(binding.output_slice) <= 0:
                return False, f"Empty output slice in layer {view.layer_id}"
            if self._slice_size(binding.input_slice) <= 0:
                return False, f"Empty input slice in layer {view.layer_id}"
            if view.layer_type != "pool" and self._slice_size(binding.weight_slice) <= 0:
                return False, f"Empty weight slice in layer {view.layer_id}"
            repeat_time = view.input_fetch.num()
            for time_id in range(repeat_time):
                if self._slice_size(self.fetch(binding.input_slice, view.input_fetch, time_id, 0)) <= 0:
                    return False, f"Input fetch produced an empty slice in layer {view.layer_id}"
                if self._slice_size(self.fetch(binding.output_slice, view.input_fetch, time_id, 2)) <= 0:
                    return False, f"Output fetch produced an empty slice in layer {view.layer_id}"
                if view.layer_type != "pool" and self._slice_size(self.fetch(binding.weight_slice, view.input_fetch, time_id, 1)) <= 0:
                    return False, f"Weight fetch produced an empty slice in layer {view.layer_id}"
        return True, ""

    def apply_local_remap(self, layer_id: int, action_type: str, src_core: int, dst_core: int, trace=None):
        if layer_id not in self.layer_views:
            report = CertificationReport(False, action_type, layer_id, src_core, dst_core, "layer out of range")
            self.last_certification = report
            return report

        candidate = self._make_candidate()
        action_name = action_type.lower()
        if action_name not in {"shift", "split", "replace", "remove"}:
            report = CertificationReport(False, action_name, layer_id, src_core, dst_core, "unsupported action")
            self.last_certification = report
            return report

        ok, reason = getattr(candidate, action_name)(layer_id, src_core, dst_core, trace)
        if not ok:
            report = CertificationReport(False, action_name, layer_id, src_core, dst_core, reason)
            self.last_certification = report
            return report

        ok, reason = candidate._validate_view(candidate.layer_views[layer_id])
        if not ok:
            report = CertificationReport(False, action_name, layer_id, src_core, dst_core, reason)
            self.last_certification = report
            return report

        candidate._sync_layer_to_network(layer_id)
        try:
            candidate.gen_dfg()
            topo_len = len(candidate.toposort())
        except Exception as exc:
            report = CertificationReport(False, action_name, layer_id, src_core, dst_core, f"gen_dfg failed: {exc}")
            self.last_certification = report
            return report

        report = CertificationReport(
            True,
            action_name,
            layer_id,
            src_core,
            dst_core,
            "accepted",
            details={
                "node_count": candidate.node_counter,
                "topo_count": topo_len,
                "active_cores": [binding.core_id for binding in candidate.layer_views[layer_id].bindings],
            },
        )

        self.network = candidate.network
        self.layer_views = candidate.layer_views
        self.dfg = candidate.dfg
        self.node_counter = candidate.node_counter
        self.layer_node_ranges = candidate.layer_node_ranges
        self.all_tasks_completed_ntask = candidate.all_tasks_completed_ntask
        self.all_tasks_completed_counter = candidate.all_tasks_completed_counter
        self.last_certification = report
        return report
