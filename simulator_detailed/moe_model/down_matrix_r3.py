"""Source-exact down admission; R3 is blocked, never a production timing model."""
from pathlib import Path
from typing import Literal
import hashlib
import json
import math

from pydantic import Field, model_validator

from .component_service import ComponentAction, execute_actions
from .workload import FrozenRecord, stable_sha256

SOURCE = '094bcd25e985a4c836fd5521cccd61b14d911aac'
REVISION = 'source-gathered-down-r3'
ENGINES = ('MATRIX_FTR_READ', 'MATRIX_WGT_READ', 'MATRIX', 'MATRIX_WRITE')
BLOCKER = 'DOWN_R3_UNOBSERVABLE_REQUEST_ADMISSION_AND_TERMINAL_PREDICATE'


class DownWork(FrozenRecord):
    source_commit: Literal['094bcd25e985a4c836fd5521cccd61b14d911aac'] = SOURCE
    operation: Literal['gathered_down'] = 'gathered_down'
    profile: Literal['transformer-fused'] = 'transformer-fused'
    rank: Literal[0] = 0
    pe: int = Field(ge=0, lt=32)
    call_ordinal: Literal[0] = 0
    V: int = Field(gt=0, le=64)
    locality: Literal['repeated', 'spread', 'reverse', 'paired', 'relocated', 'coalesced', 'queued']
    expert_order: tuple[int, ...]
    shape: tuple[int, int, int] = (1, 256, 256)
    tile: tuple[int, int, int] = (1, 32, 256)
    feature: Literal[1048704] = 0x100080
    output: Literal[1310848] = 0x140080
    table: Literal[1179776] = 0x120080
    weight: int = 0x400000
    tiles: int
    activation_bytes: int
    weight_bytes: int
    table_bytes: int
    output_bytes: int

    @model_validator(mode='after')
    def exact(self):
        expected = tuple(0 if self.locality == 'repeated' else
                         (95 - ordinal * 17) % 96 if self.locality == 'reverse' else
                         ordinal // 2 % 96 if self.locality == 'paired' else ordinal * 17 % 96 for ordinal in range(self.V))
        if self.expert_order != expected or self.weight != (0x410000 if self.locality == 'relocated' else 0x400000):
            raise ValueError('DOWN_ORDER_LOCALITY_ADDRESS')
        if self.shape != (1, 256, 256) or self.tile != (1, 32, 256):
            raise ValueError('DOWN_SHAPE_TILE')
        if (self.tiles, self.activation_bytes, self.weight_bytes, self.table_bytes, self.output_bytes) != tuple(self.V * value for value in (8, 384, 36864, 24, 1024)):
            raise ValueError('DOWN_WORK_CONSERVATION')
        return self

    @property
    def predecessor(self):
        return f'E3_E4.handoff.pe{self.pe}' if self.pe < 28 else f'E2_E3.root{self.pe}.matrix.ready'

    @property
    def successor(self):
        return 'PE_DOWNLOAD_1' if self.pe % 4 == 3 else 'PE_UPLOAD_1'

    def addresses(self):
        return tuple(tuple(self.weight + expert * 36864 + entry * 4096 for entry in range(9)) for expert in self.expert_order)


def down_work(pe, count, locality='spread'):
    order = tuple(0 if locality == 'repeated' else (95 - ordinal * 17) % 96 if locality == 'reverse' else
                  ordinal // 2 % 96 if locality == 'paired' else ordinal * 17 % 96 for ordinal in range(count))
    return DownWork(pe=pe, V=count, locality=locality, expert_order=order,
                    weight=0x410000 if locality == 'relocated' else 0x400000,
                    tiles=8*count, activation_bytes=384*count, weight_bytes=36864*count, table_bytes=24*count, output_bytes=1024*count)


def reference_down(work, features, weights, feature_scales, weight_scales):
    work = DownWork.model_validate_json(work.model_dump_json())
    if len(features) != work.V or len(feature_scales) != work.V or any(len(row) != 256 for row in features):
        raise ValueError('DOWN_REFERENCE_FEATURE_SHAPE')
    if any(type(value) is not int or not -128 <= value <= 127 for row in features for value in row):
        raise ValueError('DOWN_REFERENCE_FEATURE_PRECISION')
    for expert in set(work.expert_order):
        if expert not in weights or expert not in weight_scales or len(weights[expert]) != 256 or len(weight_scales[expert]) != 256 or any(len(row) != 256 for row in weights[expert]):
            raise ValueError('DOWN_REFERENCE_WEIGHT_SHAPE')
        if any(type(value) is not int or not 0 <= value <= 15 for row in weights[expert] for value in row):
            raise ValueError('DOWN_REFERENCE_WEIGHT_PRECISION')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (*feature_scales, *(value for expert in work.expert_order for value in weight_scales[expert]))):
        raise ValueError('DOWN_REFERENCE_SCALE')
    return tuple(tuple(sum(feature * weight for feature, weight in zip(features[ordinal], row)) * feature_scales[ordinal] * weight_scales[expert][column]
                 for column, row in enumerate(weights[expert])) for ordinal, expert in enumerate(work.expert_order))


class DownIdentity(FrozenRecord):
    engine: Literal['MATRIX_FTR_READ', 'MATRIX_WGT_READ', 'MATRIX', 'MATRIX_WRITE']
    node: str
    unit: Literal['reported_ns'] = 'reported_ns'
    seconds_per_unit: Literal[1e-9] = 1e-9
    resolution_seconds: Literal[1e-12] = 1e-12
    model_sha256: Literal['c570f898733d379833f2349688bd81ed6e87ee4db71564d2dce36f74daa415ad']
    systemc_sha256: Literal['b293e9f1cc99263b192d39edbe8f755188826829d4210a4d6daec06f26e7b14a']
    observer_sha256: Literal['c3e930524daa47a12141b3695d1740e868f1eff11d2ec66174f173a78d8bf981']
    binary_sha256: Literal['aa4d368d0168bca4714b2dad6c93fc5ea0aef41aaf01ea2ffff5b09b641dd458']
    dependencies: tuple[str, ...]


def production_request(work, identity, workload_sha256):
    from .component_calibration import CalibrationResolutionRequest
    work = DownWork.model_validate_json(work.model_dump_json())
    identity = DownIdentity.model_validate_json(identity.model_dump_json())
    if identity.node != f'rank0.PE{work.pe}' or identity.dependencies != (work.predecessor, f'rank0.R9.pe{work.pe}.weight_table.ready', f'rank0.E4.pe{work.pe}.source_issue.ready'):
        raise ValueError('DOWN_NODE_DEPENDENCY')
    return CalibrationResolutionRequest(request_id=f'rank0.E4.pe{work.pe}.down.r3.{identity.engine}',
        action_id=f'rank0:E4:pe{work.pe}:call0:{identity.engine}', workload_sha256=workload_sha256,
        component_id='M05', mechanism=REVISION, operation='gathered_down', profile='transformer-fused', consumer_stage='E4',
        shape_key='transformer-fused', V=work.V, payload_bytes=work.activation_bytes, matrix_shape=work.shape,
        weight_locality=work.locality, feature_stride=384, feature_layout='source-V1K-strided-inline-scale',
        route_domain='source-down-r3-' + work.locality, requires_physical_time=False,
        compiled_path_sha256=stable_sha256(dict(work=work.model_dump(mode='json'), identity=identity.model_dump(mode='json'))),
        model_library_sha256=identity.model_sha256, systemc_library_sha256=identity.systemc_sha256,
        observer_sha256=identity.observer_sha256, counter_callsite=0x91c96d)


def resolve_production(bundle, work, identity, workload_sha256):
    from .component_calibration import resolve_calibration
    request = production_request(work, identity, workload_sha256)
    if any(entry.mechanism == REVISION for entry in bundle.entries):
        raise ValueError('DOWN_R3_UNAUTHORIZED_PROMOTION')
    try:
        resolve_calibration(bundle, request)
    except ValueError as error:
        raise ValueError(BLOCKER) from error
    raise ValueError('DOWN_R3_AMBIGUOUS_RESOLUTION')


def build_revision_bundle(root):
    from .component_calibration import ComponentCalibrationBundle, build_component_calibration_bundle
    root = Path(root)
    baseline = build_component_calibration_bundle(root).model_dump(mode='json')
    if (len(baseline['entries']), len(baseline['excluded_evidence']), len(baseline['timebases']['domains'])) != (151, 109, 42):
        raise ValueError('DOWN_R3_BASELINE')
    path = root / 'c05-down-matrix-r3/result.json'
    result = json.loads(path.read_text())
    if result['status'] != 'source_event_observability_blocker' or result['validation_launches'] != 0 or result['accepted']:
        raise ValueError('DOWN_R3_EXCLUSION_STATUS')
    baseline['excluded_evidence'].append(dict(component_id='M05', revision=REVISION, reason=BLOCKER,
        source_refs=('c05-down-matrix-r3/result.json', 'c05-down-matrix-r3/source-contract.json', 'c05-down-matrix-r3/REPORT.md'),
        evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    baseline['source_refs'].append('c05-down-matrix-r3/result.json')
    baseline['bundle_sha256'] = stable_sha256({key: value for key, value in baseline.items() if key != 'bundle_sha256'})
    return ComponentCalibrationBundle.model_validate_json(json.dumps(baseline))


def execute_topology_fixture(calls, binding, hardware, *, costs, cancel=None):
    """Exercise resource plumbing with explicit synthetic costs; never fit or publish."""
    if not binding.fixture_only or hardware.clock_domain != 'reported_ns':
        raise ValueError('DOWN_R3_FIXTURE_ONLY')
    calls = tuple(DownWork.model_validate_json(work.model_dump_json()) for work in calls)
    units = {unit['unit_id']: unit for unit in binding.graph['execution_units']}
    resources = {f'rank0.PE{work.pe}.{engine}' for work in calls for engine in (*ENGINES, 'WEIGHT_SRAM')}
    allowed = {resource + '.' + field for resource in resources for field in ('startup', 'per_work', 'drain', 'per_transition')}
    if set(costs) != allowed or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in costs.values()):
        raise ValueError('DOWN_FIXTURE_COST_IDENTITY')
    for resource in resources:
        if resource not in units or units[resource]['node_id'] != '.'.join(resource.split('.')[:2]):
            raise ValueError('DOWN_FIXTURE_NODE_ENGINE')
    actions = []
    previous = {}
    digest = stable_sha256(costs)
    for invocation, work in enumerate(calls):
        stem = f'down.fixture.pe{work.pe}.invocation{invocation}'
        ends = []
        for engine in (*ENGINES, 'WEIGHT_SRAM'):
            resource = f'rank0.PE{work.pe}.{engine}'
            amount = work.V * 9 if engine in ('WEIGHT_SRAM', 'MATRIX_WGT_READ') else work.tiles
            transitions = sum(left != right for left, right in zip(work.expert_order, work.expert_order[1:]))
            duration = costs[resource+'.startup'] + costs[resource+'.per_work']*amount + costs[resource+'.drain'] + costs[resource+'.per_transition']*transitions
            action_id = stem + '.' + engine
            dependencies = (previous[resource],) if resource in previous else ()
            actions.append(ComponentAction(action_id=action_id, stage='E4.fixture', component='M05', resource_id=resource,
                work=amount, work_unit='packed-entry' if engine in ('WEIGHT_SRAM', 'MATRIX_WGT_READ') else 'tile', duration_cycles=duration,
                duration_source='CPU topology only; no production service or boundary attribution', calibration_sha256=digest,
                dependencies=dependencies, participants=(f'rank0.PE{work.pe}',), status='calibrated'))
            previous[resource] = action_id
            ends.append(action_id)
        actions.append(ComponentAction(action_id=stem+'.after_all', stage='E4.fixture.join', component='M05',
            resource_id=f'rank0.PE{work.pe}.MATRIX', work=0, work_unit='after-all', duration_cycles=0,
            duration_source='CPU join only; NOT the real successor terminal', calibration_sha256=digest,
            dependencies=tuple(ends), participants=(f'rank0.PE{work.pe}',), status='calibrated'))
    run = execute_actions(actions, binding, hardware, production=False, cancel=cancel)
    run.receipt.update(revision=REVISION, accepted=False, production_timing_available=False,
                       source_event_blocker=BLOCKER, fixture_only=True, exact_timebase='reported_ns=1e-9 s; native tick=1e-12 s',
                       excluded_cost_owners=['gate/up', 'Scalar', 'GM', 'NoC', 'synchronization', 'successor'])
    return run
