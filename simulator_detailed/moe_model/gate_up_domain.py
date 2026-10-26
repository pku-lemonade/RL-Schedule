"""Exact source gate/up production admission and evidence-gated publication."""
import json
from pathlib import Path
from pydantic import Field, TypeAdapter, model_validator

from .gate_up_matrix import BOUNDARY, GateUpWork, GateUpFormula, gate_up_work, execute_gate_up
from .workload import FrozenRecord, FrozenMoeWorkload, PROFILES, stable_sha256
from .timebase import TimebaseDomain, resolve_timebase, convert_time_value


class GateUpDomain(FrozenRecord):
    work: GateUpWork
    formula: GateUpFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_contract_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_refs: tuple[str, ...]
    domain_sha256: str

    @model_validator(mode='after')
    def exact(self):
        work, timebase = self.work, self.timebase
        if work != gate_up_work(self.formula.locality) or not self.source_refs:
            raise ValueError('gate/up production domain is exact rank0 V64')
        if (timebase.component_ids, timebase.operation, timebase.profile, timebase.boundary_id, timebase.source_unit, timebase.accepted_work_values, timebase.status) != (('M05',), work.operation, work.profile, BOUNDARY + ':' + work.locality, 'reported_ns', (64,), 'validated_simulation'):
            raise ValueError('gate/up exact timebase')
        if timebase.calibration_scene_ids != self.formula.calibration_scene_ids or (timebase.seconds_per_source_unit, timebase.active_default_unit_seconds, timebase.active_resolution_seconds, timebase.work_unit) != (1e-9, 1e-9, 1e-12, 'valid-selection'):
            raise ValueError('gate/up timebase evidence/conversion')
        if stable_sha256(self.model_dump(mode='json', exclude={'domain_sha256'})) != self.domain_sha256:
            raise ValueError('gate/up domain hash')
        return self


def freeze_gate_up_domain(**values):
    raw = TypeAdapter(dict).dump_python(values, mode='json')
    raw['domain_sha256'] = stable_sha256(raw)
    return GateUpDomain.model_validate_json(json.dumps(raw))


def gate_up_request(domain, workload_sha256):
    from .component_calibration import CalibrationResolutionRequest
    domain = GateUpDomain.model_validate_json(domain.model_dump_json())
    work = domain.work
    return CalibrationResolutionRequest(request_id='rank0.E0.source_gate_up', action_id='rank0:E0:source_gate_up',
        workload_sha256=workload_sha256, component_id='M05', mechanism='source_gate_up_engines', operation=work.operation,
        profile=work.profile, consumer_stage='E0', shape_key=work.profile, V=64, payload_bytes=work.activation_bytes,
        matrix_shape=work.shape, weight_locality=work.locality, feature_stride=384, feature_layout='source-selected-token-inline-scale',
        route_domain='source-gate-up-rank0-' + work.locality, requires_physical_time=False, gate_up_work=work, gate_up_unit='reported_ns',
        model_library_sha256=domain.timebase.model_library_sha256, systemc_library_sha256=domain.timebase.systemc_library_sha256,
        observer_sha256=domain.observer_sha256, compiled_path_sha256=domain.source_contract_sha256, counter_callsite=0x91c96d)


def gate_up_request_matches(entry, request):
    domain = GateUpDomain.model_validate_json(json.dumps(entry.formula))
    expected = gate_up_request(domain, request.workload_sha256)
    return request.model_dump(exclude={'parameter_paths'}) == expected.model_dump(exclude={'parameter_paths'})


def gate_up_work_from_workload(workload, rank):
    workload = FrozenMoeWorkload.model_validate_json(workload.model_dump_json())
    if type(rank) is not int or rank != 0 or rank not in workload.observed_ep_ranks or workload.profile != PROFILES['transformer-fused'] or workload.identity.source_commit != '094bcd25e985a4c836fd5521cccd61b14d911aac':
        raise ValueError('gate/up workload source/profile/rank')
    records = tuple(record for record in workload.records if record.ep_rank == rank and record.status == 'valid')
    for locality in ('paired', 'spread'):
        work = gate_up_work(locality, len(records))
        if work.expert_order == tuple(record.local_expert_id for record in records) and work.token_order == tuple(record.token_id for record in records):
            return work
    raise ValueError('gate/up uncalibrated selection sequence')


def execute_resolved_gate_up(bundle, request, binding, hardware, *, extra_actions, changes=None, cancel=None):
    from .component_calibration import resolve_calibration
    selected = resolve_calibration(bundle, request)
    entry = next(item for item in bundle.entries if item.entry_id == selected.entry_id)
    domain = GateUpDomain.model_validate_json(json.dumps(entry.formula))
    timebase = resolve_timebase(bundle.timebases, component_id='M05', operation=domain.work.operation, profile=domain.work.profile,
        boundary_id=domain.timebase.boundary_id, source_unit=request.gate_up_unit, work=64,
        model_library_sha256=request.model_library_sha256, systemc_library_sha256=request.systemc_library_sha256, physical=request.requires_physical_time)
    if timebase != domain.timebase:
        raise ValueError('gate/up runtime timebase mismatch')
    run = execute_gate_up(domain.work, domain.formula, binding, hardware, extra_actions=extra_actions, changes=changes, cancel=cancel)
    run.receipt.update(domain_sha256=domain.domain_sha256, production_request_id=request.request_id, production_action_id=request.action_id,
        production_request_consumptions=1, calibration_resolution=selected.model_dump(mode='json'), timebase=timebase.model_dump(mode='json'),
        engine_service_seconds={item.engine: convert_time_value(timebase, sum(domain.formula.timing(domain.work, 0)[item.engine])) for item in domain.formula.engines})
    return run


def append_gate_up_publication(root, raw):
    root = Path(root)
    manifest = root / 'c05-gate-up-matrix-r1/tsim/freeze/final-evidence.json'
    if not manifest.exists():
        return raw
    from .gate_up_publication import append_validated_gate_up
    return append_validated_gate_up(root, raw)
