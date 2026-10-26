"""Append source gate/up evidence without altering any previously accepted record."""
import hashlib
import json

from .gate_up_domain import GateUpDomain
from .timebase import freeze_timebase_bundle


def append_validated_gate_up(root, raw):
    from .component_calibration import _freeze_entry, EvidenceArtifact, CalibrationSceneEvidence, ErrorSummary, ActionDomain, ExcludedEvidence
    base = root / 'c05-gate-up-matrix-r1/tsim'
    final = json.loads((base / 'freeze/final-evidence.json').read_text())
    if not final['pass_gate'] or final['append_only_baseline'] != [149,107,40]:
        raise ValueError('gate/up publication acceptance')
    for row in final['files']:
        if hashlib.sha256((root / row['path']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('gate/up publication evidence drift')
    def artifact(path):
        return EvidenceArtifact(path=path, sha256=hashlib.sha256((root / path).read_bytes()).hexdigest())
    entries, timebases = [], []
    for locality in ('paired','spread'):
        domain = GateUpDomain.model_validate_json((base / f'domain-{locality}.json').read_text())
        calibration = json.loads((base / f'calibration-summary-{locality}.json').read_text())
        summary = json.loads((base / f'validation-summary-{locality}.json').read_text())
        if not calibration['pass_gate'] or not summary['pass_gate'] or (summary['launches'], summary['retries']) != (1,0):
            raise ValueError('gate/up unaccepted evidence')
        scenes = tuple(CalibrationSceneEvidence(scene_id=row['run_id'], role='calibration', artifacts=(artifact(f'c05-gate-up-matrix-r1/tsim/calibration-summary-{locality}.json'),),
            max_error=row['max_error_ns'], error_unit='reported_ns', passed=True) for row in calibration['rows'])
        validation = (CalibrationSceneEvidence(scene_id=summary['run_id'], role='validation', artifacts=(artifact(f'c05-gate-up-matrix-r1/tsim/validation-summary-{locality}.json'),),
            max_error=summary['max_error_ns'], error_unit='reported_ns', passed=True),)
        entries.append(_freeze_entry(entry_id='m05-source-gate-up-rank0-' + locality + '-V64', component_id='M05', mechanism='source_gate_up_engines', operation='source_gate_up',
            profiles=('transformer-fused',), consumer_stages=('E0',), parameter_paths=tuple(path for pe in range(32) for path in domain.formula.parameters(pe)),
            formula_id=domain.formula.revision + '-' + locality, formula=domain.model_dump(mode='json'),
            code_artifacts=tuple(artifact('public-patch/t81-overlay/simulator_detailed/moe_model/' + name) for name in ('gate_up_matrix.py','gate_up_domain.py','gate_up_publication.py')),
            domain=ActionDomain(shape_keys=('transformer-fused',), input_dimensions={'gate_up_work':domain.work.model_dump(mode='json')},
                v_min=64, v_max=64, payload_min_bytes=24576, payload_max_bytes=24576, matrix_shapes=((1,512,256),), weight_localities=(locality,), feature_strides=(384,),
                route_domains=('source-gate-up-rank0-' + locality,), applicability_text='Exact source-selected per-call local engine service, conditional on separately owned source issue readiness; no physical peak compute/bandwidth or complete E0 envelope claim'),
            duration_unit='reported_ns', observation_unit='reported_ns', timebase_status='local_reported_ns', calibration_scenes=scenes, validation_scenes=validation,
            error_summary=ErrorSummary(metric='max native engine boundary absolute error', max_error=summary['max_error_ns'], threshold=8., unit='reported_ns', validation_scene_count=1),
            status='validated_limited', publication_class='current_architecture_component_limited', source_commit=domain.work.source_commit,
            source_refs=('c05-gate-up-matrix-r1/source-contract.json', f'c05-gate-up-matrix-r1/tsim/domain-{locality}.json')))
        timebases.append(domain.timebase)
    exclusions = []
    for locality in ('repeated','spread'):
        path = f'c05-expert-matrix-r1/tsim/validation-summary-{locality}.json'
        failed = json.loads((root / path).read_text())
        if failed['pass_gate'] or (failed['launches'], failed['retries']) != (1,0):
            raise ValueError('down exclusion seal drift')
        exclusions.append(ExcludedEvidence(component_id='M05', revision='source-gathered-down-engine-work-r1-' + locality,
            reason='Preregistered once-only all-engine boundary gate failed; no partial promotion, refit, rerun or reuse for gate/up modeling.',
            source_refs=(path, 'c05-expert-matrix-r1/tsim/freeze/validation-outcome.json'), evidence_sha256=artifact(path).sha256))
    raw['entries'] = tuple(raw['entries']) + tuple(entries)
    raw['excluded_evidence'] = tuple(raw['excluded_evidence']) + tuple(exclusions)
    raw['timebases'] = freeze_timebase_bundle(bundle_id='T81-C09-plus-source-gate-up', fixture_only=False,
        domains=raw['timebases'].domains + tuple(timebases), source_refs=raw['timebases'].source_refs + ('c05-gate-up-matrix-r1/tsim/freeze/final-evidence.json',))
    raw['bundle_id'] = 'T81-M01-M08-final-accepted-v25-source-gate-up'
    raw['source_refs'] += ('c05-gate-up-matrix-r1/tsim/freeze/final-evidence.json',)
    return raw
