import json
import queue
from typing import List
from pydantic import BaseModel, ValidationError

from ...utils.definitions import DimSlice


class Core(BaseModel):
    x: int
    y: int


class Block(BaseModel):
    cores: List[Core]
    tensor_slice: List[DimSlice]


class IFeature(BaseModel):
    source: str
    source_layer_id: int
    blocks: List[Block]


class WFeature(BaseModel):
    source: str
    blocks: List[Block]


class OFeature(BaseModel):
    dest: str
    next: List[int]
    blocks: List[Block]


class Partition(BaseModel):
    dims: List[int]
    def num(self) -> int:
        res = 1
        for dim_part in self.dims:
            res = res * dim_part
        return res


class Layer(BaseModel):
    type: str
    layer_id: int
    layer_group_id: int
    group_num: int
    layer_batch_size: int
    output_partition: Partition
    input_fetch: Partition
    input_feature: List[IFeature]
    wgt_feature: List[WFeature]
    output_feature: List[OFeature]


class Network(BaseModel):
    name: str
    batch_size: int
    layers: List[Layer]