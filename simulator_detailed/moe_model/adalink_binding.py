"""Strict supplied CclConfig/rank/link/port binding for M08."""
from __future__ import annotations
from typing import Literal
import copy,json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .hardware import HardwareParameterSet

class RankCclConfig(FrozenRecord):
    rank: int = Field(ge=0)
    commid_slot_idx: int = Field(ge=0)
    rank_info_offset: int = Field(ge=0)
    reduce_ordered: bool
    port_num: int = Field(gt=0)
    send_ranks: tuple[int,...]
    recv_ranks: tuple[int,...]
    fullmesh_node_ids: tuple[str,...]
    source_ref: str = Field(min_length=1)

class AdalinkRankBinding(FrozenRecord):
    rank: int = Field(ge=0)
    endpoint_id: str = Field(min_length=1)
    reduce_unit_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)

class AdalinkRouteBinding(FrozenRecord):
    source_rank: int = Field(ge=0)
    destination_rank: int = Field(ge=0)
    network_id: str = Field(min_length=1)
    link_ids: tuple[str,...]
    port_link_ids: tuple[str,...]
    source_ref: str = Field(min_length=1)
    @model_validator(mode='after')
    def route(self):
        if self.source_rank==self.destination_rank or not self.link_ids or not self.port_link_ids or not set(self.port_link_ids)<=set(self.link_ids):raise ValueError('ADALINK route/port links invalid')
        return self

class AdalinkParameterBinding(FrozenRecord):
    parameter_path: str = Field(min_length=1)
    network_id: str = Field(min_length=1)
    link_id: str = Field(min_length=1)
    effect: Literal['bandwidth','channel_latency']

class SuppliedAdalinkBinding(FrozenRecord):
    schema_version: int=1
    binding_id: str
    fixture_only: bool
    graph: dict
    timing: tuple[dict,...]
    ccl_configs: tuple[RankCclConfig,...]
    ranks: tuple[AdalinkRankBinding,...]
    routes: tuple[AdalinkRouteBinding,...]
    parameters: tuple[AdalinkParameterBinding,...]
    source_refs: tuple[str,...]
    calibration_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        from ..configs.schemas.generic_graph import GenericSystemGraph
        graph=GenericSystemGraph.model_validate_json(json.dumps(self.graph));rank_ids=[x.rank for x in self.ranks];configs=[x.rank for x in self.ccl_configs]
        if not self.binding_id or not self.source_refs or len(rank_ids)<2 or rank_ids!=list(range(len(rank_ids))) or configs!=rank_ids:raise ValueError('rank map/CclConfig must cover contiguous ranks')
        expected=set(rank_ids)
        for config in self.ccl_configs:
            if set(config.send_ranks)!=expected or set(config.recv_ranks)!=expected or len(config.send_ranks)!=len(rank_ids) or len(config.recv_ranks)!=len(rank_ids) or not config.fullmesh_node_ids:
                raise ValueError('CclConfig rank_info/fullmesh nodes incomplete')
        endpoints={x.endpoint_id for x in graph.dma_endpoints};units={x.unit_id for x in graph.execution_units}
        if any(x.endpoint_id not in endpoints or x.reduce_unit_id not in units or not x.device_id or not x.source_ref for x in self.ranks):raise ValueError('rank endpoint/reduce unit absent')
        graph_routes={(x.network_id,x.source,x.destination):tuple(x.link_ids) for x in graph.static_routes};links={(x.network_id,x.link_id) for x in graph.links};rank_ep={x.rank:x.endpoint_id for x in self.ranks};pairs=set()
        for route in self.routes:
            pair=(route.source_rank,route.destination_rank)
            if route.source_rank not in expected or route.destination_rank not in expected or pair in pairs:raise ValueError('route rank pair invalid')
            pairs.add(pair);key=(route.network_id,rank_ep[route.source_rank],rank_ep[route.destination_rank])
            if graph_routes.get(key)!=route.link_ids or any((route.network_id,x) not in links for x in route.link_ids):raise ValueError('route differs from supplied graph')
        required_pairs={(source,destination) for source in expected for destination in expected if source!=destination}
        if pairs!=required_pairs:raise ValueError('supplied ADALINK routes must cover every ordered rank pair')
        paths=[x.parameter_path for x in self.parameters]
        if len(paths)!=len(set(paths)):raise ValueError('parameter paths must be unique')
        for parameter in self.parameters:
            if (parameter.network_id,parameter.link_id) not in links:raise ValueError('parameter targets unknown exact link')
            prefix=f'adalink.links.{parameter.link_id}.'
            if not parameter.parameter_path.startswith(prefix):raise ValueError('ADALINK parameter path/link mismatch')
        if not self.fixture_only:
            required={(route.network_id,link,effect) for route in self.routes for link in route.link_ids for effect in ('bandwidth','channel_latency')}
            supplied={(x.network_id,x.link_id,x.effect) for x in self.parameters}
            if required!=supplied:raise ValueError('production binding requires bandwidth and latency for every routed link')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('ADALINK binding hash mismatch')
        return self
    def rank(self,rank):return next(x for x in self.ranks if x.rank==rank)
    def route(self,source,destination):
        rows=[x for x in self.routes if x.source_rank==source and x.destination_rank==destination]
        if len(rows)!=1:raise ValueError(f'missing exact ADALINK route {source}->{destination}')
        return rows[0]
    def effective_timing(self,hardware):
        hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json());values=hardware.by_path();result=copy.deepcopy(list(self.timing));bound={x.parameter_path for x in self.parameters}
        extra={x.path for x in hardware.parameters if x.component=='M08'}-bound
        if extra:raise ValueError('M08 hardware parameter has no exact link binding')
        for target in self.parameters:
            if target.parameter_path not in values:raise ValueError('missing ADALINK hardware parameter '+target.parameter_path)
            quantity=values[target.parameter_path].quantity
            if quantity.value is None:raise ValueError('ADALINK parameter unavailable')
            if not self.fixture_only and quantity.calibration_status!='calibrated':raise ValueError('production ADALINK parameter must be calibrated')
            network=next((x for x in result if x['network_id']==target.network_id),None)
            if network is None:raise ValueError('ADALINK timing network missing')
            overrides={x['link_id']:x['timing'] for x in network.get('overrides',[])};timing=copy.deepcopy(overrides.get(target.link_id,network['link']))
            if timing.get('mode')!='latency_fifo':raise ValueError('M08 requires packet/FIFO timing')
            if target.effect=='bandwidth':
                if quantity.unit!='byte/cycle' or quantity.value<=0:raise ValueError('ADALINK bandwidth unit/value invalid')
                timing['issue_cycles']=max(1,math.ceil(timing['packet_bytes']/quantity.value))
            else:
                if quantity.unit!='cycle' or quantity.value<0:raise ValueError('ADALINK latency unit/value invalid')
                timing['channel_latency_cycles']=quantity.value
            overrides[target.link_id]=timing;network['overrides']=[dict(link_id=k,timing=v) for k,v in sorted(overrides.items())]
        return result

def freeze_adalink_binding(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(kwargs);raw.setdefault('schema_version',1);shape=TypeAdapter(dict).dump_python(raw,mode='json')
    return SuppliedAdalinkBinding(**raw,binding_sha256=stable_sha256(shape))

class LocalReduceCost(FrozenRecord):
    rank: int = Field(ge=0)
    status: Literal['calibrated','uncalibrated','unavailable']
    startup_cycles: float | None = Field(default=None,ge=0)
    cycles_per_byte: float | None = Field(default=None,ge=0)
    source_refs: tuple[str,...]
    @model_validator(mode='after')
    def cost(self):
        available=self.status=='calibrated'
        if available!=(self.startup_cycles is not None and self.cycles_per_byte is not None):raise ValueError('calibrated reduce cost fields mismatch')
        if not self.source_refs:raise ValueError('reduce cost source required')
        return self
    def duration(self,work_bytes):
        if self.status!='calibrated':raise ValueError('ADALINK local reduce cost unavailable')
        return self.startup_cycles+self.cycles_per_byte*work_bytes

class AdalinkCostTable(FrozenRecord):
    table_id: str = Field(min_length=1)
    costs: tuple[LocalReduceCost,...]
    source_refs: tuple[str,...]
    table_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        ranks=[x.rank for x in self.costs]
        if not self.table_id or not self.source_refs or len(ranks)!=len(set(ranks)):raise ValueError('cost table identity invalid')
        if stable_sha256(self.model_dump(mode='json',exclude={'table_sha256'}))!=self.table_sha256:raise ValueError('cost table hash mismatch')
        return self
    def cost(self,rank):
        rows=[x for x in self.costs if x.rank==rank]
        if len(rows)!=1:raise ValueError('rank local reduce cost missing')
        return rows[0]

def freeze_adalink_cost_table(**kwargs):
    from pydantic import TypeAdapter
    shape=TypeAdapter(dict).dump_python(kwargs,mode='json')
    return AdalinkCostTable(**kwargs,table_sha256=stable_sha256(shape))
