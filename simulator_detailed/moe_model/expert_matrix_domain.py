"""Exact production binding and publication for gathered down Matrix service."""
import json
from pathlib import Path
from pydantic import Field, model_validator
from .expert_matrix import ExpertMatrixWork, ExpertMatrixFormula, ENGINES, BOUNDARY, expert_work, expert_actions, resource, ready, complete
from .workload import FrozenRecord, stable_sha256
from .timebase import TimebaseDomain, convert_time_value, freeze_timebase_bundle, resolve_timebase
from .component_service import ComponentAction, execute_actions


class ExpertMatrixDomain(FrozenRecord):
    work: ExpertMatrixWork
    formula: ExpertMatrixFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_contract_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_refs: tuple[str, ...]
    domain_sha256: str

    @model_validator(mode='after')
    def exact(self):
        work, timebase = self.work, self.timebase
        if work != expert_work(0, self.formula.locality) or not self.source_refs:
            raise ValueError('expert Matrix production domain is rank0 V64 source-exact')
        if (timebase.component_ids, timebase.operation, timebase.profile, timebase.boundary_id, timebase.source_unit, timebase.accepted_work_values, timebase.status) != (('M05',), work.operation, work.profile, BOUNDARY+':'+work.locality, 'reported_ns', (64,), 'validated_simulation'):
            raise ValueError('expert Matrix exact timebase')
        if timebase.calibration_scene_ids != self.formula.calibration_scene_ids:
            raise ValueError('expert Matrix timebase evidence')
        if (timebase.seconds_per_source_unit, timebase.active_default_unit_seconds, timebase.active_resolution_seconds, timebase.work_unit) != (1e-9, 1e-9, 1e-12, 'valid-selection'):
            raise ValueError('expert Matrix timebase conversion')
        if stable_sha256(self.model_dump(mode='json', exclude={'domain_sha256'})) != self.domain_sha256:
            raise ValueError('expert Matrix domain hash')
        return self


def freeze_expert_domain(**values):
    from pydantic import TypeAdapter
    raw = TypeAdapter(dict).dump_python(values, mode='json')
    raw['domain_sha256'] = stable_sha256(raw)
    return ExpertMatrixDomain.model_validate_json(json.dumps(raw))


def expert_request(domain, workload_sha256):
    from .component_calibration import CalibrationResolutionRequest
    domain = ExpertMatrixDomain.model_validate_json(domain.model_dump_json())
    work = domain.work
    return CalibrationResolutionRequest(request_id='rank0.E4.gathered_down', action_id='rank0:E4:gathered_down',
        workload_sha256=workload_sha256, component_id='M05', mechanism='expert_matrix_gathered',
        operation=work.operation, profile=work.profile, consumer_stage='E4', shape_key=work.profile,
        V=64, payload_bytes=work.activation_bytes, matrix_shape=work.shape, weight_locality=work.locality,
        feature_stride=384, feature_layout='source-V1K-strided-inline-scale', route_domain='source-gathered-down-rank0-'+work.locality,
        requires_physical_time=False, expert_matrix_work=work, expert_matrix_unit='reported_ns',
        model_library_sha256=domain.timebase.model_library_sha256, systemc_library_sha256=domain.timebase.systemc_library_sha256,
        observer_sha256=domain.observer_sha256, compiled_path_sha256=domain.source_contract_sha256,
        counter_callsite=0x91c96d)


def expert_request_matches(entry, request):
    domain = ExpertMatrixDomain.model_validate_json(json.dumps(entry.formula))
    expected = expert_request(domain, request.workload_sha256)
    fields = ('request_id','action_id','component_id','mechanism','operation','profile','consumer_stage','shape_key','V','payload_bytes',
        'matrix_shape','weight_locality','feature_stride','feature_layout','route_domain','requires_physical_time','expert_matrix_work',
        'expert_matrix_unit','model_library_sha256','systemc_library_sha256','observer_sha256','compiled_path_sha256','counter_callsite')
    return all(getattr(request, field) == getattr(expected, field) for field in fields)


def readiness_actions(work, formula):
    actions = []
    for pe in range(32):
        predecessor = f'E3_E4.handoff.pe{pe}' if pe < 28 else f'E2_E3.root{pe}.matrix.ready'
        actions.append(ComponentAction(action_id=ready(work.rank, pe), stage='E4.binding', component='M05',
            resource_id=resource(work.rank, pe, 'MATRIX'), work=0, work_unit='after-all', duration_cycles=0,
            duration_source='zero-cost source activation/table/issue prerequisites; readiness owned elsewhere',
            calibration_sha256=formula.formula_sha256,
            dependencies=(predecessor, f'rank{work.rank}.R9.pe{pe}.weight_table.ready', f'rank{work.rank}.E4.pe{pe}.source_issue.ready'),
            participants=(f'rank{work.rank}.PE{pe}',), status='calibrated'))
    return tuple(actions)


def execute_resolved_expert(bundle, request, binding, hardware, *, changes=None, extra_actions=(), cancel=None):
    from .component_calibration import resolve_calibration
    selected = resolve_calibration(bundle, request)
    entry = next(item for item in bundle.entries if item.entry_id == selected.entry_id)
    domain = ExpertMatrixDomain.model_validate_json(json.dumps(entry.formula))
    if hardware.clock_domain != 'reported_ns' or binding.fixture_only:
        raise ValueError('expert Matrix production units/binding')
    timebase = resolve_timebase(bundle.timebases, component_id='M05', operation=domain.work.operation, profile=domain.work.profile,
        boundary_id=domain.timebase.boundary_id, source_unit=request.expert_matrix_unit, work=64,
        model_library_sha256=request.model_library_sha256, systemc_library_sha256=request.systemc_library_sha256, physical=request.requires_physical_time)
    if timebase != domain.timebase:
        raise ValueError('expert Matrix runtime timebase mismatch')
    actions = expert_actions(domain.work, domain.formula, binding, changes)
    adapters = readiness_actions(domain.work, domain.formula)
    for action in extra_actions:
        if action.component == 'M05' and (action.stage in ('E4','down','MATRIX') or action.duration_source == BOUNDARY):
            raise ValueError('expert Matrix adjacent domain duplicate owner')
        if action.action_id in {item.action_id for item in actions+adapters}:
            raise ValueError('expert Matrix duplicate prerequisite/owner')
    output = ComponentAction(action_id='E4.down.complete', stage='E4.binding', component='M05', resource_id=resource(0,0,'MATRIX'),
        work=0, work_unit='after-all', duration_cycles=0, duration_source='zero-cost real E4-E5 producer alias',
        calibration_sha256=domain.formula.formula_sha256, dependencies=('rank0.E4.complete',), participants=('rank0.PE0',), status='calibrated')
    run = execute_actions(adapters+actions+(output,)+tuple(extra_actions), binding, hardware, production=True, cancel=cancel)
    spans = {item.transaction_id:item for item in run.result.transactions}
    terminal = spans['E4.down.complete'].end_cycles if run.result.status == 'complete' else None
    origin = min(spans[ready(0,pe)].end_cycles for pe in range(32)) if run.result.status == 'complete' else None
    run.receipt.update(domain_sha256=domain.domain_sha256, production_request_id=request.request_id,
        production_action_id=request.action_id, production_request_consumptions=1, calibration_resolution=selected.model_dump(mode='json'),
        timing_owner=BOUNDARY, source_work=domain.work.model_dump(mode='json'), cost_count_once=True,
        excluded_cost_owners=['E3-E4 readiness','E2-E3 root readiness','Vector table generation','Scalar issue preparation','GM','NoC','E4-E5 synchronization','old nongathered M05'],
        simulation_seconds=convert_time_value(timebase,terminal-origin) if origin is not None else None, physical_publishable=False)
    return run


def append_expert_publication(root, raw):
    import hashlib
    from .component_calibration import _freeze_entry, EvidenceArtifact, CalibrationSceneEvidence, ErrorSummary, ActionDomain
    root=Path(root);base=root/'c05-expert-matrix-r1/tsim';manifest=base/'freeze/final-evidence.json'
    if not manifest.exists(): return raw
    final=json.loads(manifest.read_text())
    if not final['pass_gate']: return raw
    for row in final['files']:
        if hashlib.sha256((root/row['path']).read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('expert Matrix publication evidence drift')
    entries=[];timebases=[]
    def artifact(path):
        return EvidenceArtifact(path=path,sha256=hashlib.sha256((root/path).read_bytes()).hexdigest())
    for locality in ('repeated','spread'):
        domain=ExpertMatrixDomain.model_validate_json((base/f'domain-{locality}.json').read_text())
        summary=json.loads((base/f'validation-summary-{locality}.json').read_text())
        calibration=json.loads((base/f'calibration-summary-{locality}.json').read_text())
        if not summary['pass_gate'] or not calibration['pass_gate']:raise ValueError('expert Matrix evidence not accepted')
        refs=(f'c05-expert-matrix-r1/tsim/domain-{locality}.json','c05-expert-matrix-r1/source-contract.json')
        scenes=tuple(CalibrationSceneEvidence(scene_id=row['run_id'],role='calibration',artifacts=(artifact(f'c05-expert-matrix-r1/tsim/calibration-summary-{locality}.json'),),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True) for row in calibration['errors'])
        validation=(CalibrationSceneEvidence(scene_id=summary['run_id'],role='validation',artifacts=(artifact(f'c05-expert-matrix-r1/tsim/validation-summary-{locality}.json'),),max_error=summary['max_error_ns'],error_unit='reported_ns',passed=True),)
        entries.append(_freeze_entry(entry_id='m05-source-gathered-down-rank0-'+locality+'-V64',component_id='M05',mechanism='expert_matrix_gathered',operation='gathered_down',profiles=('transformer-fused',),consumer_stages=('E4',),
            parameter_paths=tuple(path for pe in range(32) for path in domain.formula.parameters(0,pe)),formula_id=domain.formula.revision+'-'+locality,formula=domain.model_dump(mode='json'),
            code_artifacts=tuple(artifact('public-patch/t81-overlay/simulator_detailed/moe_model/'+name) for name in ('expert_matrix.py','expert_matrix_domain.py','expert_matrix_input.py')),
            domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions={'expert_matrix_work':domain.work.model_dump(mode='json')},v_min=64,v_max=64,payload_min_bytes=24576,payload_max_bytes=24576,matrix_shapes=((1,256,256),),weight_localities=(locality,),feature_strides=(384,),route_domains=('source-gathered-down-rank0-'+locality,),applicability_text='Exact source gathered batch and local engine occupancy, simulation only; no physical independent bandwidth or off-chip gather claim'),
            duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=scenes,validation_scenes=validation,error_summary=ErrorSummary(metric='max engine boundary absolute error',max_error=summary['max_error_ns'],threshold=8.,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='current_architecture_component_limited',source_commit=domain.work.source_commit,source_refs=refs))
        timebases.append(domain.timebase)
    raw['entries']=tuple(raw['entries'])+tuple(entries)
    raw['timebases']=freeze_timebase_bundle(bundle_id='T81-C09-plus-gathered-down',fixture_only=False,domains=raw['timebases'].domains+tuple(timebases),source_refs=raw['timebases'].source_refs+('c05-expert-matrix-r1/tsim/freeze/final-evidence.json',))
    raw['bundle_id']='T81-M01-M08-final-accepted-v25-gathered-down'
    raw['source_refs']+=('c05-expert-matrix-r1/tsim/freeze/final-evidence.json',)
    return raw
