"""Source gate/up engine occupancy, not an independently measured peak FLOP rate."""
import json
import math
from typing import Literal

from pydantic import Field, model_validator
from .workload import FrozenRecord, stable_sha256
from .binding import SuppliedBinding
from .component_service import ComponentAction, execute_actions
from .expert_matrix import ENGINES, SOURCE_COMMIT, resource

BOUNDARY = 'source-gate-up-per-call-engine-start-active-end-drained-end'
BUFFERS = ('moe.activation_quant', 'moe.gate_up_weight', 'moe.topk_token_ids', 'moe.topk_idx', 'moe.gate_up_res')


class GateUpWork(FrozenRecord):
    source_commit: Literal[SOURCE_COMMIT] = SOURCE_COMMIT
    source_callsite: Literal['moe/moe_gemm.hpp:173-179:moe_gate_up_gemm'] = 'moe/moe_gemm.hpp:173-179:moe_gate_up_gemm'
    profile: Literal['transformer-fused'] = 'transformer-fused'
    operation: Literal['source_gate_up'] = 'source_gate_up'
    precision: Literal['W4A8-group256'] = 'W4A8-group256'
    rank: Literal[0] = 0
    V: int = Field(gt=0, le=64)
    locality: Literal['paired', 'spread']
    expert_order: tuple[int, ...]
    token_order: tuple[int, ...]
    shape: tuple[int, int, int] = (1, 512, 256)
    tile_shape: tuple[int, int, int] = (1, 32, 256)
    pe_grid: tuple[int, int] = (8, 4)
    tiles: int
    weight_entries: int
    activation_bytes: int
    weight_bytes: int
    output_bytes: int
    selection_bytes: int
    feature_stride: int = 384
    buffers: tuple[str, ...] = BUFFERS
    weight_level: Literal['PE-local-Weight-SRAM'] = 'PE-local-Weight-SRAM'
    boundary: Literal[BOUNDARY] = BOUNDARY

    @model_validator(mode='after')
    def exact(self):
        experts = tuple(ordinal % 2 if self.locality == 'paired' else ordinal * 17 % 96 for ordinal in range(self.V))
        if self.expert_order != experts or self.token_order != tuple(ordinal // 2 for ordinal in range(self.V)):
            raise ValueError('gate/up source selection order')
        if (self.shape, self.tile_shape, self.pe_grid, self.feature_stride, self.buffers) != ((1, 512, 256), (1, 32, 256), (8, 4), 384, BUFFERS):
            raise ValueError('gate/up source shape/layout/buffers')
        if (self.tiles, self.weight_entries, self.activation_bytes, self.weight_bytes, self.output_bytes, self.selection_bytes) != (16 * self.V, 17 * self.V, 384 * self.V, 69632 * self.V, 2048 * self.V, 8 * self.V):
            raise ValueError('gate/up tile/entry/byte conservation')
        return self


def gate_up_work(locality, count=64, *, hidden=2048, expert_dim=1024, pe_rows=8, pe_columns=4, weight_bits=4):
    if (hidden, expert_dim, pe_rows, pe_columns, weight_bits) != (2048, 1024, 8, 4, 4):
        raise ValueError('gate/up unsupported source profile')
    depth = ((hidden + pe_rows * 256 - 1) // (pe_rows * 256) * pe_rows * 256) // pe_rows
    width = ((expert_dim + pe_columns * 32 - 1) // (pe_columns * 32) * pe_columns * 32) * 2 // pe_columns
    tiles = (width // 32) * (depth // 256)
    entries = tiles + (tiles + 27) // 28
    return GateUpWork(V=count, locality=locality, expert_order=tuple(ordinal % 2 if locality == 'paired' else ordinal * 17 % 96 for ordinal in range(count)),
        token_order=tuple(ordinal // 2 for ordinal in range(count)), shape=(1, width, depth), tiles=count * tiles,
        weight_entries=count * entries, activation_bytes=count * (depth + 128), weight_bytes=count * entries * 4096,
        output_bytes=count * width * 4, selection_bytes=count * 8)


def reference_gate_up(work, features, weights, feature_scales, weight_scales):
    work = GateUpWork.model_validate_json(work.model_dump_json())
    if len(features) != 32 or len(feature_scales) != 32 or any(len(row) != 256 for row in features):
        raise ValueError('gate/up reference activation shape')
    if any(type(value) is not int or not -128 <= value <= 127 for row in features for value in row):
        raise ValueError('gate/up reference activation precision')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in feature_scales):
        raise ValueError('gate/up reference activation scale')
    for expert in set(work.expert_order):
        if expert not in weights or expert not in weight_scales or len(weights[expert]) != 512 or len(weight_scales[expert]) != 512 or any(len(row) != 256 for row in weights[expert]):
            raise ValueError('gate/up reference weight shape')
        if any(type(value) is not int or not 0 <= value <= 15 for row in weights[expert] for value in row):
            raise ValueError('gate/up reference weight precision')
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in weight_scales[expert]):
            raise ValueError('gate/up reference weight scale')
    return tuple(tuple(sum(feature * weight for feature, weight in zip(features[token], row)) * feature_scales[token] * weight_scales[expert][column]
        for column, row in enumerate(weights[expert])) for token, expert in zip(work.token_order, work.expert_order))


class GateUpEngine(FrozenRecord):
    engine: Literal['MATRIX_FTR_READ', 'MATRIX_WGT_READ', 'MATRIX', 'MATRIX_WRITE']
    startup_ns: float = Field(ge=0)
    active_ns_per_work: float = Field(ge=0)
    drain_ns: float = Field(ge=0)

    @model_validator(mode='after')
    def finite(self):
        if not all(math.isfinite(value) for value in (self.startup_ns, self.active_ns_per_work, self.drain_ns)):
            raise ValueError('gate/up nonfinite engine service')
        return self


class GateUpFormula(FrozenRecord):
    revision: Literal['source-gate-up-native-active-drain-work-r1'] = 'source-gate-up-native-active-drain-work-r1'
    locality: Literal['paired', 'spread']
    engines: tuple[GateUpEngine, ...]
    calibration_scene_ids: tuple[str, ...]
    formula_sha256: str

    @model_validator(mode='after')
    def exact(self):
        if tuple(item.engine for item in self.engines) != ENGINES or len(set(self.calibration_scene_ids)) < 2:
            raise ValueError('gate/up engines/calibration inventory')
        if stable_sha256(self.model_dump(mode='json', exclude={'formula_sha256'})) != self.formula_sha256:
            raise ValueError('gate/up formula hash')
        return self

    def parameters(self, pe):
        return tuple(f'gate_up.rank0.PE{pe}.{self.locality}.{item.engine}.{field}' for item in self.engines for field in ('startup_ns', 'active_ns_per_work', 'drain_ns'))

    def timing(self, work, pe, changes=None):
        changes = changes or {}
        allowed = {path for node in range(32) for path in self.parameters(node)}
        if self.locality != work.locality or type(pe) is not int or pe not in range(32) or len(changes) > 1 or not set(changes) <= allowed:
            raise ValueError('gate/up parameter resource/locality')
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in changes.values()):
            raise ValueError('gate/up parameter value')
        result = {}
        for item in self.engines:
            prefix = f'gate_up.rank0.PE{pe}.{self.locality}.{item.engine}.'
            startup = changes.get(prefix + 'startup_ns', item.startup_ns)
            rate = changes.get(prefix + 'active_ns_per_work', item.active_ns_per_work)
            drain = changes.get(prefix + 'drain_ns', item.drain_ns)
            amount = work.weight_entries // work.V if item.engine == 'MATRIX_WGT_READ' else work.tiles // work.V
            result[item.engine] = (startup, amount * rate, drain)
        return result


def freeze_gate_up_formula(**values):
    raw = dict(revision='source-gate-up-native-active-drain-work-r1', **values)
    raw['engines'] = [item.model_dump(mode='json') if isinstance(item, GateUpEngine) else item for item in raw['engines']]
    raw['formula_sha256'] = stable_sha256(raw)
    return GateUpFormula.model_validate_json(json.dumps(raw))


def issue_ready(pe, ordinal, engine):
    return f'rank0.E0.pe{pe}.call{ordinal}.{engine}.issue_ready'


def call_complete(pe, ordinal):
    return f'rank0.E0.pe{pe}.call{ordinal}.complete'


def gate_up_actions(work, formula, binding, changes=None):
    if changes:
        raise ValueError('gate/up conditional intervals do not identify hardware counterfactual response')
    work = GateUpWork.model_validate_json(work.model_dump_json())
    formula = GateUpFormula.model_validate_json(formula.model_dump_json())
    binding = SuppliedBinding.model_validate_json(binding.model_dump_json())
    units = {item['unit_id']: item for item in binding.graph['execution_units']}
    actions = []
    for pe in range(32):
        for engine in ENGINES:
            unit = units.get(resource(0, pe, engine))
            if unit is None or unit['node_id'] != f'rank0.PE{pe}':
                raise ValueError('gate/up engine/node binding')
        for ordinal in range(work.V):
            ends = []
            for engine, (startup, active, drain) in formula.timing(work, pe, changes).items():
                stem = f'rank0.E0.pe{pe}.call{ordinal}.{engine}'
                previous = () if ordinal == 0 else (f'rank0.E0.pe{pe}.call{ordinal - 1}.{engine}.end',)
                amount = work.weight_entries // work.V if engine == 'MATRIX_WGT_READ' else work.tiles // work.V
                byte_count = {'MATRIX_FTR_READ': work.activation_bytes // work.V, 'MATRIX_WGT_READ': work.weight_bytes // work.V, 'MATRIX': 0, 'MATRIX_WRITE': work.output_bytes // work.V}[engine]
                common = dict(stage='E0', component='M05', resource_id=resource(0, pe, engine), duration_source=BOUNDARY,
                    calibration_sha256=formula.formula_sha256, participants=(f'rank0.PE{pe}',), status='calibrated',
                    conditional_context=f'rank0.E0.pe{pe}', conditional_members=work.V * len(ENGINES),
                    simultaneous_ready=tuple(issue_ready(pe, ordinal, peer) for peer in ENGINES))
                actions.append(ComponentAction(action_id=stem + '.end', work=amount,
                    work_unit='packed-entry' if engine == 'MATRIX_WGT_READ' else 'source-N32-K256-tile', bytes=byte_count,
                    duration_cycles=startup + active + drain, dependencies=(issue_ready(pe, ordinal, engine),) + previous, **common))
                ends.append(stem + '.end')
            actions.append(ComponentAction(action_id=call_complete(pe, ordinal), stage='E0.join', component='M05',
                resource_id=resource(0, pe, 'MATRIX'), work=0, work_unit='after-all', duration_cycles=0,
                duration_source=BOUNDARY, calibration_sha256=formula.formula_sha256, dependencies=tuple(ends),
                participants=(f'rank0.PE{pe}',), status='calibrated'))
    actions.append(ComponentAction(action_id='E0.gate_up.complete', stage='E0.join', component='M05',
        resource_id=resource(0, 0, 'MATRIX'), work=0, work_unit='after-all', duration_cycles=0, duration_source=BOUNDARY,
        calibration_sha256=formula.formula_sha256, dependencies=tuple(call_complete(pe, work.V - 1) for pe in range(32)),
        participants=('rank0.PE0',), status='calibrated'))
    return tuple(actions)


def execute_gate_up(work, formula, binding, hardware, *, extra_actions, changes=None, cancel=None):
    if hardware.clock_domain != 'reported_ns' or binding.fixture_only:
        raise ValueError('gate/up production time/binding')
    actions = gate_up_actions(work, formula, binding, changes)
    if {action.action_id for action in actions} & {action.action_id for action in extra_actions}:
        raise ValueError('gate/up duplicate owner')
    if any(action.component == 'M05' and (action.stage in ('E0', 'gate_up', 'MATRIX') or action.duration_source == BOUNDARY) for action in extra_actions):
        raise ValueError('gate/up overlapping Matrix owner')
    supplied = {action.action_id: action for action in extra_actions}
    for pe in range(32):
        for ordinal in range(work.V):
            for index, engine in enumerate(ENGINES):
                issue = supplied.get(issue_ready(pe, ordinal, engine))
                dependencies = (f'rank0.E0.pe{pe}.activation_download1_ready', f'rank0.E0.pe{pe}.call{ordinal}.selection_ready')
                if index:
                    dependencies += (issue_ready(pe, ordinal, ENGINES[index - 1]),)
                if issue is None or issue.dependencies != dependencies or issue.duration_cycles != 0 or issue.work != 0 or issue.participants != (f'rank0.PE{pe}',):
                    raise ValueError('gate/up source issue dependency/buffer readiness')
    run = execute_actions(actions + tuple(extra_actions), binding, hardware, production=True, cancel=cancel)
    run.receipt.update(timing_owner=BOUNDARY, source_work=work.model_dump(mode='json'), cost_count_once=True,
        physical_publishable=False, scope='opaque current-context engine intervals, not independently composable service',
        excluded_cost_owners=['Scalar configuration and inter-call issue gaps', 'E0 input DOWNLOAD1 readiness', 'E0-E1 sync', 'E1 column reduction', 'E3-E4 readiness', 'GM', 'NoC', 'old Matrix envelope'])
    return run
