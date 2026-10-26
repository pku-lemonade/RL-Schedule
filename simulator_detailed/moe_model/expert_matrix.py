"""Source-shaped gathered down engine service; no whole-operation residual."""
import json, math
from typing import Literal
from pydantic import Field, model_validator
from .workload import FrozenRecord, stable_sha256
from .component_service import ComponentAction, execute_actions
from .binding import SuppliedBinding
from .timebase import TimebaseDomain, convert_time_value

SOURCE_COMMIT = '094bcd25e985a4c836fd5521cccd61b14d911aac'
ENGINES = ('MATRIX_FTR_READ', 'MATRIX_WGT_READ', 'MATRIX', 'MATRIX_WRITE')
LOCALITIES = ('repeated', 'spread')
BOUNDARY = 'source-gathered-down-engine-start-active-finish-write-end'


class ExpertMatrixWork(FrozenRecord):
    source_commit: Literal[SOURCE_COMMIT] = SOURCE_COMMIT
    source_callsite: Literal['moe/moe_gemm.hpp:compute_down_gemm'] = 'moe/moe_gemm.hpp:compute_down_gemm'
    profile: Literal['transformer-fused'] = 'transformer-fused'
    operation: Literal['gathered_down'] = 'gathered_down'
    precision: Literal['W4A8-group256'] = 'W4A8-group256'
    rank: int = Field(ge=0, le=3)
    V: int = Field(gt=0, le=64)
    locality: Literal['repeated', 'spread']
    expert_order: tuple[int, ...]
    shape: tuple[int, int, int] = (1, 256, 256)
    tile_shape: tuple[int, int, int] = (1, 32, 256)
    tiles: int
    activation_bytes: int
    weight_bytes: int
    table_bytes: int
    output_bytes: int
    feature_stride: int = 384
    buffers: tuple[str, ...] = ('moe.down_activation', 'moe.down_weight', 'moe.mlp1_weight_table', 'moe.down_res')
    weight_level: Literal['PE-local-Weight-SRAM'] = 'PE-local-Weight-SRAM'
    boundary: Literal[BOUNDARY] = BOUNDARY

    @model_validator(mode='after')
    def exact(self):
        expected = tuple(0 if self.locality == 'repeated' else index * 17 % 96 for index in range(self.V))
        if self.expert_order != expected or self.shape != (1, 256, 256) or self.tile_shape != (1, 32, 256):
            raise ValueError('expert Matrix source shape/order/tile mismatch')
        if (self.tiles, self.activation_bytes, self.weight_bytes, self.table_bytes, self.output_bytes, self.feature_stride) != (self.V * 8, self.V * 384, self.V * 36864, self.V * 24, self.V * 1024, 384):
            raise ValueError('expert Matrix work conservation')
        if self.buffers != ('moe.down_activation', 'moe.down_weight', 'moe.mlp1_weight_table', 'moe.down_res'):
            raise ValueError('expert Matrix buffer identity')
        return self


def expert_work(rank, locality, count=64):
    return ExpertMatrixWork(rank=rank, locality=locality, V=count,
        expert_order=tuple(0 if locality == 'repeated' else index * 17 % 96 for index in range(count)),
        tiles=count * 8, activation_bytes=count * 384, weight_bytes=count * 36864,
        table_bytes=count * 24, output_bytes=count * 1024)


class EngineService(FrozenRecord):
    engine: Literal['MATRIX_FTR_READ', 'MATRIX_WGT_READ', 'MATRIX', 'MATRIX_WRITE']
    startup_ns: float = Field(ge=0)
    ns_per_work: float = Field(ge=0)
    issue_startup_ns: float = Field(ge=0)
    issue_ns_per_work: float = Field(ge=0)

    @model_validator(mode='after')
    def finite(self):
        if not all(math.isfinite(value) for key, value in self.model_dump().items() if key != 'engine'):
            raise ValueError('nonfinite Matrix coefficient')
        return self


class ExpertMatrixFormula(FrozenRecord):
    revision: Literal['source-gathered-down-engine-work-r1'] = 'source-gathered-down-engine-work-r1'
    locality: Literal['repeated', 'spread']
    engines: tuple[EngineService, ...]
    calibration_scene_ids: tuple[str, ...]
    formula_sha256: str

    @model_validator(mode='after')
    def exact(self):
        if tuple(item.engine for item in self.engines) != ENGINES or len(set(self.calibration_scene_ids)) < 2:
            raise ValueError('expert Matrix engines/calibration inventory')
        if stable_sha256(self.model_dump(mode='json', exclude={'formula_sha256'})) != self.formula_sha256:
            raise ValueError('expert Matrix formula hash')
        return self

    def parameters(self, rank, pe):
        return tuple(f'expert.rank{rank}.PE{pe}.{self.locality}.{item.engine}.{field}'
            for item in self.engines for field in ('startup_ns', 'ns_per_work', 'issue_startup_ns', 'issue_ns_per_work'))

    def timing(self, work, pe, changes=None):
        changes = changes or {}
        allowed = {path for node in range(32) for path in self.parameters(work.rank, node)}
        if self.locality != work.locality or type(pe) is not int or pe not in range(32) or len(changes) > 1 or not set(changes) <= allowed:
            raise ValueError('expert Matrix parameter locality/node mismatch')
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in changes.values()):
            raise ValueError('expert Matrix parameter value')
        out = {}
        for item in self.engines:
            values = item.model_dump()
            for field in ('startup_ns', 'ns_per_work', 'issue_startup_ns', 'issue_ns_per_work'):
                path = f'expert.rank{work.rank}.PE{pe}.{work.locality}.{item.engine}.{field}'
                values[field] = changes.get(path, values[field])
            amount = work.V * 9 if item.engine == 'MATRIX_WGT_READ' else work.tiles
            out[item.engine] = (values['issue_startup_ns'] + amount * values['issue_ns_per_work'], values['startup_ns'] + amount * values['ns_per_work'])
        return out


def freeze_expert_formula(**values):
    raw = dict(revision='source-gathered-down-engine-work-r1', **values)
    raw['engines'] = [item.model_dump(mode='json') if isinstance(item, EngineService) else item for item in raw['engines']]
    raw['formula_sha256'] = stable_sha256(raw)
    return ExpertMatrixFormula.model_validate_json(json.dumps(raw))


def resource(rank, pe, engine):
    return f'rank{rank}.PE{pe}.{engine}'


def ready(rank, pe):
    return f'rank{rank}.E4.pe{pe}.matrix_input_ready'


def complete(rank, pe):
    return f'rank{rank}.E4.pe{pe}.gathered_down_complete'


def expert_actions(work, formula, binding, changes=None, instance='E4'):
    work = ExpertMatrixWork.model_validate(work.model_dump())
    formula = ExpertMatrixFormula.model_validate(formula.model_dump())
    binding = SuppliedBinding.model_validate(binding.model_dump())
    units = {item['unit_id']: item for item in binding.graph['execution_units']}
    for pe in range(32):
        for engine in ENGINES:
            unit = units.get(resource(work.rank, pe, engine))
            if unit is None or unit['node_id'] != f'rank{work.rank}.PE{pe}':
                raise ValueError('expert Matrix engine/node binding')
    actions = []
    def add(identifier, pe, engine, duration, dependencies, amount=0, byte_count=0):
        actions.append(ComponentAction(action_id=identifier, stage=instance, component='M05',
            resource_id=resource(work.rank, pe, engine), work=amount,
            work_unit='packed-entry' if engine == 'MATRIX_WGT_READ' else 'source-N32-K256-tile',
            duration_cycles=duration, duration_source=BOUNDARY, calibration_sha256=formula.formula_sha256,
            dependencies=dependencies, participants=(f'rank{work.rank}.PE{pe}',), bytes=byte_count, status='calibrated'))
    for pe in range(32):
        stem = f'rank{work.rank}.{instance}.pe{pe}'
        timing = formula.timing(work, pe, changes)
        predecessor = ready(work.rank, pe)
        ends = []
        for engine in ENGINES:
            issue, duration = timing[engine]
            issue_id = stem + '.' + engine + '.issue'
            body_id = stem + '.' + engine + '.service'
            add(issue_id, pe, engine, issue, (predecessor,))
            amount = work.V * 9 if engine == 'MATRIX_WGT_READ' else work.tiles
            byte_count = {'MATRIX_FTR_READ': work.activation_bytes, 'MATRIX_WGT_READ': work.weight_bytes, 'MATRIX': 0, 'MATRIX_WRITE': work.output_bytes}[engine]
            add(body_id, pe, engine, duration, (issue_id,), amount, byte_count)
            ends.append(body_id)
            predecessor = issue_id
        end_id = complete(work.rank, pe) if instance == 'E4' else stem + '.gathered_down_complete'
        add(end_id, pe, 'MATRIX', 0, tuple(ends))
    add(f'rank{work.rank}.{instance}.complete', 0, 'MATRIX', 0,
        tuple(complete(work.rank, pe) if instance == 'E4' else f'rank{work.rank}.{instance}.pe{pe}.gathered_down_complete' for pe in range(32)))
    return tuple(actions)


def execute_expert(work, formula, binding, hardware, *, changes=None, extra_actions=(), production=True, cancel=None):
    if hardware.clock_domain != 'reported_ns':
        raise ValueError('expert Matrix requires reported_ns')
    actions = expert_actions(work, formula, binding, changes)
    if {item.action_id for item in actions} & {item.action_id for item in extra_actions}:
        raise ValueError('expert Matrix duplicate owner')
    run = execute_actions(actions + tuple(extra_actions), binding, hardware, production=production, cancel=cancel)
    run.receipt.update(timing_owner=BOUNDARY, formula_sha256=formula.formula_sha256,
        source_work=work.model_dump(mode='json'), cost_count_once=True, physical_publishable=False,
        excluded_cost_owners=['router', 'E3 NoC', 'E3-E4 readiness', 'weight_table_generation', 'GM', 'synchronization', 'nongathered-M05'])
    return run
