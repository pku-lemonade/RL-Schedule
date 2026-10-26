"""Append-only publication and exact production resolution for the source bridge."""
import hashlib
import json
from pathlib import Path

from .bridge_commid_r3 import (
    BridgeDomain, BridgeWork, PRODUCERS, WAITERS, SyncGroup, checked,
    execute_bridge, parameters, resolve_bridge,
)
from .component_calibration import (
    ActionDomain, CalibrationSceneEvidence, ComponentCalibrationBundle,
    ErrorSummary, EvidenceArtifact, ExcludedEvidence, _freeze_entry,
)
from .timebase import freeze_timebase_bundle
from .workload import stable_sha256


ENTRY_ID = 'm07-source-attention-moe-bridge-commid17-C14-W4-r3'
REVISION = 'c07-bridge-commid-r3'


def append_validated_bridge(root):
    root = Path(root)
    base = root / REVISION
    acceptance = json.loads((base / 'acceptance-evidence.json').read_text())
    if not acceptance['pass_gate'] or acceptance['baseline_counts'] != [151, 109, 42]:
        raise ValueError('bridge publication acceptance gate')
    for name, digest in acceptance['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('bridge acceptance artifact drift: ' + name)
    baseline = root / 'component-calibration/component-calibration-bundle.json'
    if hashlib.sha256(baseline.read_bytes()).hexdigest() != acceptance['baseline_sha256']:
        raise ValueError('accepted baseline drift')
    bundle = ComponentCalibrationBundle.model_validate_json(baseline.read_text())
    if (len(bundle.entries), len(bundle.excluded_evidence), len(bundle.timebases.domains)) != (151, 109, 42):
        raise ValueError('wrong accepted baseline')
    domain = BridgeDomain.model_validate_json((base / 'domain.json').read_text())
    calibration = json.loads((base / 'tsim/calibration-summary.json').read_text())
    validation = json.loads((base / 'validation-summary.json').read_text())
    if not calibration['pass_gate'] or not validation['pass_gate'] or validation['launches'] != 4 or validation['retries'] != 0:
        raise ValueError('bridge calibration/once-only validation gate')
    def artifact(name):
        return EvidenceArtifact(path=name, sha256=hashlib.sha256((root / name).read_bytes()).hexdigest())
    calibration_scenes = tuple(CalibrationSceneEvidence(scene_id=scene_id, role='calibration',
        artifacts=(artifact(REVISION + '/tsim/calibration-summary.json'),), max_error=calibration['max_error_ns'],
        error_unit='reported_ns', passed=True) for scene_id in domain.formula.calibration_ids)
    validation_scenes = tuple(CalibrationSceneEvidence(scene_id=scene['scene']['run_id'], role='validation',
        artifacts=(artifact(REVISION + '/tsim/validation-' + scene['scene']['run_id'] + '.json'),),
        max_error=scene['max_error_ns'], error_unit='reported_ns', passed=True) for scene in validation['scenes'])
    group = SyncGroup(group_id='source', rank=0, comm_id=17, generation=0, producers=PRODUCERS, waiters=WAITERS,
        expected_count=14, producer_dependencies={producer: f'producer{producer}' for producer in PRODUCERS},
        acquire_dependencies={waiter: f'waiter{waiter}' for waiter in WAITERS})
    entry = _freeze_entry(entry_id=ENTRY_ID, component_id='M07', mechanism='source_bridge_commid',
        operation='source_bridge_commid17', profiles=('transformer-fused',), consumer_stages=(domain.work.stage,),
        parameter_paths=tuple(parameters(domain.formula, (group,))), formula_id=domain.formula.family,
        formula=domain.model_dump(mode='json'), code_artifacts=tuple(artifact('public-patch/t81-overlay/simulator_detailed/moe_model/' + name)
            for name in ('bridge_commid_r3.py', 'bridge_publication.py')),
        domain=ActionDomain(shape_keys=('transformer-fused',), input_dimensions={'bridge_work': BridgeWork().model_dump(mode='json')},
            producer_counts=(14,), waiter_counts=(4,), rank_counts=(1,), commid_slots=(17,),
            route_domains=('rank0-ADALINK4..17-ADL_FUNC-to-GM_RDMA0..3-DMA_CHANNEL_1',),
            applicability_text='Exact source comm17 synchronization control cut, boundary-conditioned on native source release and acquire issue. No collective, Scalar, Matrix, Vector, Data NoC, GM, receiver outer wait, host or whole-decode timing.'),
        duration_unit='reported_ns', observation_unit='reported_ns', timebase_status='local_reported_ns',
        calibration_scenes=calibration_scenes, validation_scenes=validation_scenes,
        error_summary=ErrorSummary(metric='maximum native acquire-terminal absolute error', max_error=validation['max_error_ns'],
            threshold=8., unit='reported_ns', validation_scene_count=4), status='validated_limited',
        publication_class='current_architecture_component_limited', source_commit=domain.work.source_commit,
        source_refs=(REVISION + '/source-contract.json', REVISION + '/compiled-contract.json', REVISION + '/domain.json'))
    quarantine = artifact(REVISION + '/quarantine.json')
    exclusions = tuple(ExcludedEvidence(component_id='M07', revision=f'C07-bridge-commid-r{revision}-quarantined',
        reason='Rejected before validation: frozen parser/candidate failure. No retry, refit, validation launch or accepted timing. Fresh R3 calibration alone supplies accepted coefficients.',
        source_refs=(quarantine.path,), evidence_sha256=quarantine.sha256) for revision in (1, 2))
    raw = bundle.model_dump(mode='json', exclude={'bundle_sha256'})
    raw['entries'] = [item.model_dump(mode='json') for item in bundle.entries + (entry,)]
    raw['excluded_evidence'] = [item.model_dump(mode='json') for item in bundle.excluded_evidence + exclusions]
    raw['timebases'] = freeze_timebase_bundle(bundle_id='T81-plus-source-bridge-commid17-r3', fixture_only=False,
        domains=bundle.timebases.domains + (domain.timebase,), source_refs=bundle.timebases.source_refs + (REVISION + '/acceptance-evidence.json',)).model_dump(mode='json')
    raw['bundle_id'] = 'T81-accepted-v26-source-bridge-commid17-r3'
    raw['source_refs'].append(REVISION + '/acceptance-evidence.json')
    raw['bundle_sha256'] = stable_sha256(raw)
    return ComponentCalibrationBundle.model_validate_json(json.dumps(raw))


def resolve_published_bridge(bundle, request):
    bundle = checked(bundle)
    entries = [entry for entry in bundle.entries if entry.operation == 'source_bridge_commid17']
    if len(entries) != 1 or entries[0].entry_id != ENTRY_ID or entries[0].status != 'validated_limited':
        raise ValueError('one accepted published bridge owner required')
    entry = entries[0]
    domain = resolve_bridge((BridgeDomain.model_validate_json(json.dumps(entry.formula)),), request)
    if tuple(scene.scene_id for scene in entry.validation_scenes) != domain.validation_ids:
        raise ValueError('bridge validation identity mismatch')
    return entry, domain


def execute_published_bridge(bundle, request, binding, groups, external_transactions, **kwargs):
    entry, domain = resolve_published_bridge(bundle, request)
    groups = tuple(groups)
    if len(groups) != 1 or groups[0].generation != 0:
        raise ValueError('accepted source cut has exactly one comm-id generation')
    if any(name in kwargs for name in ('production', 'domain')):
        raise ValueError('published bridge cannot bypass production binding')
    execution = execute_bridge(domain.formula, binding, groups, external_transactions, domain=domain, production=True, **kwargs)
    execution.receipt.update(component_entry_id=entry.entry_id, component_entry_sha256=entry.entry_sha256,
        component_bundle_sha256=bundle.bundle_sha256, resolution='exact_unique_source_bridge')
    return execution
