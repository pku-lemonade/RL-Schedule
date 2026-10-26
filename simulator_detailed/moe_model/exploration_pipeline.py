"""Frozen production orchestration for MoE baseline and exact-resource sweeps.

Production input schemas are imported from the existing Phase-0/3 contracts.
New schemas here describe only orchestration state, freeze identity and sweep
intent.  A separate fixture mode exercises the pipeline but is never
publishable.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
import argparse, copy, hashlib, inspect, json, math, os, re, shutil, subprocess, uuid

from pydantic import Field, model_validator

from .component_calibration import ComponentCalibrationBundle,build_component_calibration_bundle,resolution_issues
from .hardware import HardwareMutation, HardwareParameterSet, MutationScope, apply_mutations
from .input_acquisition import InputAcquisitionInventory
from .mfu import (
    DecodeBaselineManifest, EngineUtilizationRequest, TimeBoundary,
    compute_full_decode_mfu, compute_issued_engine_utilization,
    compute_moe_local_mfu, compute_useful_model_mfu, freeze_decode_baseline,
)
from .phase3_fixture import Phase3FixtureBundle, build_phase3_fixture
from .phase3_fixture_execute import compare_fixture_mutation, execute_phase3_fixture
from .private_inputs import (
    AdalinkBindingArtifact, PhysicalBindingArtifact, WriteSumMappingArtifact,
    import_decode_baseline, phase3_production_gate,
)
from .receipt import ResourceObserver
from .production_inputs import require_production_action_provenance
from .route_capture import import_frozen_route
from .workload import FrozenRecord, stable_sha256
from ..configs.schemas.system_spec import SystemSpec
from ..runtime_context import RuntimeContext
from ..system_compile import compile_system


Mode=Literal['production','fixture_only']
PipelinePhase=Literal['VALIDATED','BLOCKED','FROZEN','BASELINE_COMPLETE','BASELINE_FAILED','SWEEP_COMPLETE','SWEEP_FAILED','ANALYZED','ANALYSIS_BLOCKED']

PRODUCTION_INPUT_FILES={
    'inventory':'group-classification.json',
    'route':'frozen-route.json',
    'binding':'physical-binding.json',
    'write_sum':'write-sum-mapping.json',
    'decode_baseline':'decode-baseline.json',
    'adalink':'adalink-binding.json',
    'hardware':'hardware-parameters.json',
    'calibrations':'component-calibration-bundle.json',
    'peak_evidence':'peak-evidence.json',
    'operation_policy':'operation-cost-policy.json',
}
ALLOWED_SWEEP_PREFIXES=('data_noc.links.','data_noc.endpoints.','gm.rdma.','gm.wdma.','gm.controllers.','gsram.')


class SweepCase(FrozenRecord):
    case_id: str = Field(min_length=1,pattern=r'^\S+$')
    mutations: tuple[HardwareMutation,...]
    explicitly_coupled: bool=False
    rationale: str = Field(min_length=1)
    @model_validator(mode='after')
    def valid(self):
        if not self.mutations or len({row.path for row in self.mutations})!=len(self.mutations):raise ValueError('sweep case requires unique mutations')
        if len(self.mutations)>1 and not self.explicitly_coupled:raise ValueError('multi-parameter case must be explicitly coupled')
        for row in self.mutations:
            if '*' in row.path or not row.path.endswith(('bytes_per_cycle','payload_bytes_per_cycle')) or not row.path.startswith(ALLOWED_SWEEP_PREFIXES):
                raise ValueError('first sweep permits only exact Data NoC or ordinary GM bandwidth paths')
        return self


class SweepPlan(FrozenRecord):
    schema_version: Literal[1]=1
    plan_id: str = Field(min_length=1)
    fixture_only: bool
    cases: tuple[SweepCase,...]
    source_refs: tuple[str,...]
    plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        if not self.cases or len({row.case_id for row in self.cases})!=len(self.cases) or not self.source_refs:raise ValueError('sweep plan identity/cases/sources invalid')
        if stable_sha256(self.model_dump(mode='json',exclude={'plan_sha256'}))!=self.plan_sha256:raise ValueError('sweep plan hash mismatch')
        return self


class BaselineFreezeManifest(FrozenRecord):
    schema_version: Literal[1]=1
    freeze_id: str = Field(min_length=1)
    mode: Mode
    publishable: bool
    validation_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strict_complete_groups: int = Field(ge=0,le=33)
    total_groups: Literal[33]=33
    identities: dict
    hardware_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    sweep_plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    code_commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    code_files: dict[str,str]
    timebase: dict
    input_files: dict[str,str]
    model_refit_allowed: Literal[False]=False
    topology_change_allowed: Literal[False]=False
    workload_change_allowed: Literal[False]=False
    route_change_allowed: Literal[False]=False
    freeze_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        required={'workload_sha256','binding_sha256','component_sha256','decode_baseline_sha256','adalink_sha256','write_sum_sha256'}
        if set(self.identities)!=required or any(value is not None and not re.fullmatch(r'[0-9a-f]{64}',value) for value in self.identities.values()):raise ValueError('freeze identities invalid')
        if not self.code_files or not self.input_files or any(not re.fullmatch(r'[0-9a-f]{64}',value) for value in (*self.code_files.values(),*self.input_files.values())):raise ValueError('freeze file hashes invalid')
        if self.mode=='production' and (not self.publishable or self.strict_complete_groups!=33):raise ValueError('production freeze requires 33/33 publishable inputs')
        if self.mode=='fixture_only' and self.publishable:raise ValueError('fixture freeze cannot be publishable')
        if stable_sha256(self.model_dump(mode='json',exclude={'freeze_sha256'}))!=self.freeze_sha256:raise ValueError('baseline freeze hash mismatch')
        return self


def freeze_sweep_plan(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(schema_version=1,**kwargs);shape=TypeAdapter(dict).dump_python(raw,mode='json')
    return SweepPlan(**raw,plan_sha256=stable_sha256(shape))


def _sha_bytes(data):return hashlib.sha256(data).hexdigest()
def _sha_file(path):return _sha_bytes(Path(path).read_bytes())
def _read(path):return json.loads(Path(path).read_text())


def _atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');os.replace(temporary,path)


def _state(run_dir):
    path=Path(run_dir)/'state.json'
    return None if not path.is_file() else _read(path)


def _set_state(run_dir,phase: PipelinePhase,mode: Mode,detail):
    previous=_state(run_dir);revision=1 if previous is None else previous['revision']+1
    value=dict(schema_version=1,phase=phase,mode=mode,revision=revision,detail=detail)
    value['state_sha256']=stable_sha256(value);_atomic_json(Path(run_dir)/'state.json',value);return value


def status(run_dir):
    run_dir=Path(run_dir);state=_state(run_dir)
    return dict(status='ABSENT' if state is None else state['phase'],state=state,artifacts={name:(run_dir/name).exists() for name in ('validation.json','freeze','baseline','sweeps','analysis')})


def _timebase(hardware,timebases=None,components=(),*,profile="full-production-workload"):
    from .timebase import model_timebase_gate
    gate=model_timebase_gate(timebases,components or tuple(f'M{i:02d}' for i in range(1,9)),operation='full_moe_decode',profile=profile,boundary_id='R0-E8-full-moe',physical=True)
    if gate['status']!='physical':return dict(status='reported_units_only',parameter_path=None,seconds_per_cycle=None,missing_fields=gate['missing_fields'],active_timebase_bundle_sha256=None if timebases is None else timebases.bundle_sha256)
    candidates=[]
    for row in hardware.parameters:
        if not row.path.startswith('clock.') or row.quantity.value is None or row.quantity.calibration_status!='calibrated':continue
        if row.quantity.unit=='Hz' and row.quantity.value>0:candidates.append((row.path,1.0/row.quantity.value))
        elif row.quantity.unit=='second/cycle' and row.quantity.value>0:candidates.append((row.path,row.quantity.value))
    if len(candidates)==1 and math.isclose(candidates[0][1],gate['seconds_per_model_cycle'],rel_tol=1e-12):return dict(status='physical',parameter_path=candidates[0][0],seconds_per_cycle=candidates[0][1],active_timebase_bundle_sha256=timebases.bundle_sha256)
    return dict(status='reported_units_only',parameter_path=None,seconds_per_cycle=None,missing_fields=['calibrated hardware clock disagrees with validated full-model timebase'])


def _production_inputs(input_dir,allow_missing=False):
    root=Path(input_dir);missing=[name for name in PRODUCTION_INPUT_FILES.values() if not (root/name).is_file()]
    if missing and not allow_missing:raise ValueError('missing production input files: '+','.join(missing))
    result=dict(root=root,missing=missing)
    if missing:
        result['partial_inputs']={}
        partial={'inventory':InputAcquisitionInventory,'binding':PhysicalBindingArtifact,'write_sum':WriteSumMappingArtifact,'adalink':AdalinkBindingArtifact,'calibrations':ComponentCalibrationBundle,'hardware':HardwareParameterSet}
        for key,cls in partial.items():
            path=root/PRODUCTION_INPUT_FILES[key]
            if path.is_file():result['partial_inputs'][key]=cls.model_validate_json(path.read_text())
        from .production_inputs import validate_partial_consistency
        result['partial_consistency']=validate_partial_consistency(binding=result['partial_inputs'].get('binding'),write_sum=result['partial_inputs'].get('write_sum'),adalink=result['partial_inputs'].get('adalink'),hardware=result['partial_inputs'].get('hardware'),bundle=result['partial_inputs'].get('calibrations'))
        policy_path=root/PRODUCTION_INPUT_FILES['operation_policy']
        if policy_path.is_file():
            from .production_inputs import require_live_operation_costs
            policy=_read(policy_path);require_live_operation_costs(policy);result['partial_inputs']['operation_policy']=policy
        return result
    from .production_inputs import require_live_operation_costs
    result['operation_policy']=_read(root/PRODUCTION_INPUT_FILES['operation_policy']);require_live_operation_costs(dict(result['operation_policy'],system_spec=_read(root/PRODUCTION_INPUT_FILES['binding']).get('system_spec')))
    result['inventory']=InputAcquisitionInventory.model_validate_json((root/PRODUCTION_INPUT_FILES['inventory']).read_text())
    result['route']=import_frozen_route(_read(root/PRODUCTION_INPUT_FILES['route']),require_real=True)
    result['binding']=PhysicalBindingArtifact.model_validate_json((root/PRODUCTION_INPUT_FILES['binding']).read_text())
    result['write_sum']=WriteSumMappingArtifact.model_validate_json((root/PRODUCTION_INPUT_FILES['write_sum']).read_text())
    result['baseline']=import_decode_baseline(_read(root/PRODUCTION_INPUT_FILES['decode_baseline']),require_complete=True)
    result['adalink']=AdalinkBindingArtifact.model_validate_json((root/PRODUCTION_INPUT_FILES['adalink']).read_text())
    result['hardware']=HardwareParameterSet.model_validate_json((root/PRODUCTION_INPUT_FILES['hardware']).read_text())
    result['calibrations']=ComponentCalibrationBundle.model_validate_json((root/PRODUCTION_INPUT_FILES['calibrations']).read_text())
    from .peak_evidence import PeakEvidence
    from .production_inputs import validate_partial_consistency
    validate_partial_consistency(binding=result['binding'],write_sum=result['write_sum'],adalink=result['adalink'],route=result['route'],hardware=result['hardware'],bundle=result['calibrations'])
    result['peak_evidence']=PeakEvidence.model_validate_json((root/PRODUCTION_INPUT_FILES['peak_evidence']).read_text())
    return result


def _production_issues(values):
    issues=[]
    try:require_production_action_provenance(values['binding'].system_spec)
    except ValueError as exc:issues.append(str(exc))
    if values['inventory'].complete_group_count!=33:issues.append(f"strict_input_groups={values['inventory'].complete_group_count}/33")
    gate=phase3_production_gate(values['route'],values['binding'],values['write_sum'],values['baseline'],values['adalink'],include_adalink=True)
    if not gate['ready']:issues.extend(gate['missing_fields'])
    if values['baseline'].workload_sha256!=values['route'].workload_sha256:issues.append('decode_baseline_workload_hash_mismatch')
    calibration_issues=[]
    if values['calibrations'].fixture_only:issues.append('fixture_component_bundle_forbidden')
    else:calibration_issues=resolution_issues(values['calibrations'],values['route'],values['binding'].artifact_sha256)
    if calibration_issues:issues.append('component_action_calibration_unresolved:'+str(len(calibration_issues)))
    if any(row.revision=='R10-full-composition-r1-failed' for row in values['calibrations'].excluded_evidence):issues.append('r10_full_component_composition_unvalidated')
    if any(row.revision=='R10-context-overlap-r2-calibration-failed' for row in values['calibrations'].excluded_evidence):issues.append('r10_r2_mechanistic_composition_unvalidated')
    if any(row.revision=='R10-handoff-r3-hook-required' for row in values['calibrations'].excluded_evidence):issues.append('r10_r3_async_handoff_hook_required')
    if any(row.revision=='R10-hook-producer-r1-upstream-unavailable' for row in values['calibrations'].excluded_evidence):issues.append('r10_upstream_tsim_hook_unavailable')
    if any(row.revision=='M04-fused-residual-preseed-r2-validation-failed' for row in values['calibrations'].excluded_evidence):issues.append('fused_residual_preseed_timing_unvalidated')
    if any(row.revision=='M04-fused-residual-preseed-packet-r3-validation-failed' for row in values['calibrations'].excluded_evidence):issues.append('fused_residual_preseed_r3_packet_timing_unvalidated')
    if values['route'].profile.name=='transformer-fused':
        if any(row.valid_selection_count for row in values['route'].rank_valid_counts):
            from .scatter_handoff_contract import E5_E6_BLOCK,E6_E7_BLOCK
            issues.extend((E5_E6_BLOCK,E6_E7_BLOCK))
        from .r10_lowering import require_r10_spec_contract
        try:require_r10_spec_contract(values['binding'].system_spec,values['route'],values['calibrations'])
        except ValueError as exc:issues.append('r10_source_stage_contract:'+str(exc))
        from .r11_lowering import require_r11_spec_contract
        try:require_r11_spec_contract(values['binding'].system_spec,values['route'],values['calibrations'])
        except ValueError as exc:issues.append('r11_source_stage_contract:'+str(exc))
    for row in values['hardware'].parameters:
        q=row.quantity
        if q.value is None or q.calibration_status!='calibrated' or not q.unit or not q.source_ref or not q.applicability.description:issues.append('hardware_parameter_not_calibrated:'+row.path)
    from .peak_evidence import require_selected_peak
    try:
        selected=values['peak_evidence'];peak=require_selected_peak(selected,profile=values['route'].profile.name,precision=selected.precision,flop_convention='useful_model')
        if peak.peak_id!=values['baseline'].selected_peak_id:issues.append('baseline_selected_peak_identity_mismatch')
        candidates=[p for p in values['baseline'].peak_candidates if p.peak_id==peak.peak_id]
        if len(candidates)!=1 or candidates[0].model_dump(mode='json')!=peak.model_dump(mode='json'):issues.append('baseline_peak_value_or_precision_evidence_mismatch')
    except (ValueError,KeyError) as exc:issues.append('mfu_peak_convention_unbound:'+str(exc))
    timebase=_timebase(values['hardware'],values['calibrations'].timebases,profile=values['route'].profile.name)
    if timebase['status']!='physical':issues.append('physical_timebase_missing')
    return tuple(dict.fromkeys(issues)),gate,timebase,calibration_issues


def validate_inputs(run_dir, *, input_dir=None, fixture_profile=None):
    run_dir=Path(run_dir)
    if (run_dir/'state.json').exists():raise ValueError('pipeline directory already initialized')
    run_dir.mkdir(parents=True,exist_ok=True);inputs=run_dir/'inputs';inputs.mkdir(exist_ok=False)
    if fixture_profile is not None:
        bundle=build_phase3_fixture(fixture_profile);calibrations=build_component_calibration_bundle(Path.cwd(),fixture_only=True)
        _atomic_json(inputs/'fixture-bundle.json',bundle.model_dump(mode='json'));_atomic_json(inputs/'component-calibration-bundle.json',calibrations.model_dump(mode='json'))
        raw=dict(schema_version=1,mode='fixture_only',status='READY_FIXTURE_ONLY',ready=True,publishable=False,fixture_profile=fixture_profile,
          strict_complete_groups=1,total_groups=33,bundle_sha256=bundle.bundle_sha256,issues=['fixture inputs are never production-publishable'])
        raw['validation_sha256']=stable_sha256(raw);_atomic_json(run_dir/'validation.json',raw);_set_state(run_dir,'VALIDATED','fixture_only',raw['status']);return raw
    if input_dir is None:raise ValueError('production validation requires input_dir')
    source=Path(input_dir);consolidation=source.parent/'normalized-input-set.json'
    if consolidation.is_file():
        from .production_inputs import verify_consolidated_directory
        selection=_read(consolidation)['selection_commit']
        repo_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=Path.cwd(),text=True,capture_output=True,check=True).stdout.strip()
        if selection!=repo_commit:raise ValueError('stale production input selection commit')
        verify_consolidated_directory(source.parent,Path.cwd(),'/home/tiger/adas_lp_kernels',expected_selection_commit=repo_commit)
    inventory_path=source/PRODUCTION_INPUT_FILES['inventory'];issues=[];count=0;inventory_sha=None
    if inventory_path.is_file():
        try:
            inventory=InputAcquisitionInventory.model_validate_json(inventory_path.read_text());count=inventory.complete_group_count;inventory_sha=inventory.inventory_sha256
        except Exception as exc:issues.append('inventory_invalid:'+str(exc))
    else:issues.append('inventory_missing')
    values=_production_inputs(source,allow_missing=True)
    partial=values.get('partial_inputs',{})
    partial_status={key:('parsed_inventory' if key=='inventory' else 'accepted_evidence_limited' if key=='calibrations' else 'complete_contract_value' if key=='operation_policy' else 'parsed_incomplete') for key in partial}
    if values['missing'] and 'binding' in partial:
        issues.extend(partial['binding'].missing_fields)
    if count!=33:issues.append(f'strict_input_groups={count}/33')
    issues.extend('file_missing:'+name for name in values['missing']);gate=None;timebase=dict(status='unknown');calibration_issues=[]
    if not values['missing'] and not issues:
        more,gate,timebase,calibration_issues=_production_issues(values);issues.extend(more)
    raw=dict(schema_version=1,mode='production',status='READY_PRODUCTION' if not issues else 'BLOCKED_PRODUCTION_INPUTS',ready=not issues,publishable=not issues,
      partial_input_status=partial_status,input_dir=str(source.resolve()),strict_complete_groups=count,total_groups=33,inventory_sha256=inventory_sha,gate=gate,timebase=timebase,component_resolution_issues=calibration_issues,issues=issues)
    raw['validation_sha256']=stable_sha256(raw);_atomic_json(run_dir/'validation.json',raw);_set_state(run_dir,'VALIDATED' if not issues else 'BLOCKED','production',raw['status']);return raw


def _validate_sweep(plan,hardware,mode):
    if plan.fixture_only!=(mode=='fixture_only'):raise ValueError('sweep plan fixture/production mode mismatch')
    known=hardware.by_path()
    for case in plan.cases:
        for mutation in case.mutations:
            if mutation.path not in known:raise ValueError('sweep path absent from frozen hardware: '+mutation.path)
            parameter=known[mutation.path]
            if parameter.quantity.unit!='byte/cycle':raise ValueError('sweep bandwidth unit must be byte/cycle')
            if mode=='production' and parameter.quantity.calibration_status!='calibrated':raise ValueError('production sweep parameter must be calibrated')


def _code_identity(repo_root):
    import simulator_detailed.moe_model.phase3_fixture as fixture_module
    import simulator_detailed.moe_model.component_calibration as calibration_module
    from . import timebase as timebase_module,peak_evidence as peak_module, r10_stage as r10_stage_module, r10_lowering as r10_lowering_module, r11_stage as r11_stage_module, r11_lowering as r11_lowering_module
    files=(Path(__file__).resolve(),Path(inspect.getsourcefile(fixture_module)).resolve(),Path(inspect.getsourcefile(calibration_module)).resolve(),Path(inspect.getsourcefile(timebase_module)).resolve(),Path(inspect.getsourcefile(peak_module)).resolve(),Path(inspect.getsourcefile(r10_stage_module)).resolve(),Path(inspect.getsourcefile(r10_lowering_module)).resolve(),Path(inspect.getsourcefile(r11_stage_module)).resolve(),Path(inspect.getsourcefile(r11_lowering_module)).resolve())
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=repo_root,text=True,capture_output=True,check=True).stdout.strip()
    return commit,{str(path):_sha_file(path) for path in files}


def freeze_baseline(run_dir,sweep_plan_path,*,repo_root=None):
    run_dir=Path(run_dir);validation=_read(run_dir/'validation.json');mode=validation['mode']
    if not validation['ready']:raise ValueError('input validation is not ready')
    target=run_dir/'freeze'
    if target.exists():raise ValueError('baseline freeze already exists')
    plan=SweepPlan.model_validate_json(Path(sweep_plan_path).read_text());bundle=None
    if mode=='fixture_only':
        bundle=Phase3FixtureBundle.model_validate_json((run_dir/'inputs/fixture-bundle.json').read_text());calibrations=ComponentCalibrationBundle.model_validate_json((run_dir/'inputs/component-calibration-bundle.json').read_text());hardware=bundle.hardware;timebase=dict(status='reported_units_only',seconds_per_cycle=None,missing_fields=['fixture has no physical timebase'])
        identities=dict(workload_sha256=bundle.bounded_workload.workload_sha256,binding_sha256=bundle.combined_binding_sha256,component_sha256=calibrations.bundle_sha256,
          decode_baseline_sha256=None,adalink_sha256=bundle.adalink_binding.binding_sha256,write_sum_sha256=stable_sha256([row.model_dump(mode='json') for row in bundle.gm_transactions]))
    else:
        values=_production_inputs(validation['input_dir']);issues,gate,timebase,calibration_issues=_production_issues(values)
        if issues:raise ValueError('production inputs changed or incomplete: '+','.join(issues))
        hardware=values['hardware'];identities=dict(workload_sha256=values['route'].workload_sha256,binding_sha256=values['binding'].artifact_sha256,
          component_sha256=values['calibrations'].bundle_sha256,decode_baseline_sha256=values['baseline'].manifest_sha256,
          adalink_sha256=values['adalink'].artifact_sha256,write_sum_sha256=values['write_sum'].artifact_sha256)
    _validate_sweep(plan,hardware,mode);repo_root=Path.cwd() if repo_root is None else Path(repo_root);commit,code_files=_code_identity(repo_root)
    temporary=run_dir/('.freeze.tmp-'+uuid.uuid4().hex);temporary.mkdir();frozen=temporary/'frozen-inputs';frozen.mkdir()
    if mode=='fixture_only':
        shutil.copyfile(run_dir/'inputs/fixture-bundle.json',frozen/'fixture-bundle.json');shutil.copyfile(run_dir/'inputs/component-calibration-bundle.json',frozen/'component-calibration-bundle.json')
    else:
        for name in PRODUCTION_INPUT_FILES.values():shutil.copyfile(Path(validation['input_dir'])/name,frozen/name)
    shutil.copyfile(sweep_plan_path,frozen/'sweep-plan.json')
    files={str(path.relative_to(temporary)):_sha_file(path) for path in sorted(frozen.iterdir())}
    raw=dict(schema_version=1,freeze_id='t81-moe-baseline-freeze-v1',mode=mode,publishable=mode=='production',validation_sha256=validation['validation_sha256'],
      strict_complete_groups=validation['strict_complete_groups'],total_groups=33,identities=identities,hardware_sha256=hardware.parameter_sha256,
      sweep_plan_sha256=plan.plan_sha256,code_commit=commit,code_files=code_files,timebase=timebase,input_files=files,
      model_refit_allowed=False,topology_change_allowed=False,workload_change_allowed=False,route_change_allowed=False)
    manifest=BaselineFreezeManifest(**raw,freeze_sha256=stable_sha256(raw));document=manifest.model_dump(mode='json')
    _atomic_json(temporary/'baseline-freeze.json',document);os.replace(temporary,target)
    _set_state(run_dir,'FROZEN',mode,manifest.freeze_sha256);return document


def _verify_freeze(run_dir,repo_root=None):
    run_dir=Path(run_dir);manifest=BaselineFreezeManifest.model_validate_json((run_dir/'freeze/baseline-freeze.json').read_text());freeze=manifest.model_dump(mode='json')
    for relative,digest in freeze['input_files'].items():
        path=run_dir/'freeze'/relative
        if not path.is_file() or _sha_file(path)!=digest:raise ValueError('frozen input changed: '+relative)
    repo_root=Path.cwd() if repo_root is None else Path(repo_root);commit,files=_code_identity(repo_root)
    if commit!=freeze['code_commit'] or files!=freeze['code_files']:raise ValueError('code/commit changed after baseline freeze')
    return freeze


def _resource_critical(receipt):
    """Timestamp paths and aggregate waits do not prove resource attribution."""
    return dict(status='unavailable',resource_id=None,mode=None,reason='causal_resource_attribution_unavailable')


def _bottleneck_migration(baseline,case):
    base=baseline['bottleneck'];changed=case['bottleneck']
    available=all(row is not None and row.get('status')=='available' and row.get('resource_id') is not None and row.get('mode') is not None for row in (base,changed))
    return dict(case_id=case['case_id'],baseline=base,case=changed,status='available' if available else 'unavailable',
      changed=(base['resource_id'],base['mode'])!=(changed['resource_id'],changed['mode']) if available else None)


def _execute_spec(spec):
    require_production_action_provenance(spec)
    spec=spec if isinstance(spec,SystemSpec) else SystemSpec.model_validate_json(json.dumps(spec));plan=compile_system(spec);runtime=RuntimeContext(plan);observer=ResourceObserver(runtime);result=runtime.run();spans={row.transaction_id:row for row in result.transactions};resources=observer.summary()
    native=[dict(row.model_dump(mode='json'),resource_wait_cycles=sum(req['grant']-req['requested'] for req in observer.requests if req.get('transaction_id')==row.transaction_id and req['grant'] is not None)) for row in result.transactions]
    stages=[]
    for stage in [*[f'R{i}' for i in range(13)],*[f'E{i}' for i in range(9)]]:
        selected=[row for row in result.transactions if re.match(r'^'+stage+r'(?:\.|$)',row.transaction_id) and row.start_cycles is not None and row.end_cycles is not None]
        stages.append(dict(stage_id=stage,start_cycles=min((row.start_cycles for row in selected),default=None),end_cycles=max((row.end_cycles for row in selected),default=None),transaction_count=len(selected)))
    causal=observer.causal_graph(SimpleNamespace(plan=plan),spans)
    return dict(schema='t81.production-runtime-receipt.v1',status=result.status,plan_sha256=plan.plan_sha256,native_transactions=native,stage_boundaries=stages,
      component_times={kind:sum((row.end_cycles or 0)-(row.start_cycles or 0) for row in result.transactions if row.kind==kind) for kind in ('transfer','compute','wait','signal','storage')},
      resources=resources,resource_requests=observer.requests,causal_graph=causal,event_resource_critical_path=causal['critical_path'],whole_boundary=list(result.operation_envelopes),
      cleanup=dict(resources_released=runtime.registry.all_released(),multicast_groups_empty=not bool(getattr(runtime,'_fifo_multicast_groups',{})),background_alive=sum(p.is_alive for p in runtime._background)))


def _load_frozen(run_dir,freeze):
    frozen=Path(run_dir)/'freeze/frozen-inputs'
    if freeze['mode']=='fixture_only':
        bundle=Phase3FixtureBundle.model_validate_json((frozen/'fixture-bundle.json').read_text());calibrations=ComponentCalibrationBundle.model_validate_json((frozen/'component-calibration-bundle.json').read_text());return dict(bundle=bundle,hardware=bundle.hardware,calibrations=calibrations)
    values=_production_inputs(frozen);return values


def _whole_cycles(receipt):
    if receipt.get('whole_boundary'):return receipt['whole_boundary'][0]['end_cycles']-receipt['whole_boundary'][0]['start_cycles']
    ends=[row.get('end_cycles') for row in receipt.get('native_transactions',[]) if row.get('end_cycles') is not None]
    starts=[row.get('start_cycles') for row in receipt.get('native_transactions',[]) if row.get('start_cycles') is not None]
    return None if not ends or not starts else max(ends)-min(starts)


def run_baseline(run_dir,*,repo_root=None,inject_failure=False):
    run_dir=Path(run_dir);freeze=_verify_freeze(run_dir,repo_root);target=run_dir/'baseline'
    if target.exists():raise ValueError('baseline result already exists; overwrite/retry forbidden')
    temporary=run_dir/('.baseline.tmp-'+uuid.uuid4().hex);temporary.mkdir();mode=freeze['mode']
    try:
        values=_load_frozen(run_dir,freeze)
        if mode=='fixture_only':execution=execute_phase3_fixture(values['bundle'],production=False,inject_failure=inject_failure);receipt=execution.receipt
        else:
            if inject_failure:raise ValueError('failure injection is fixture-only')
            receipt=_execute_spec(values['binding'].system_spec)
        if receipt['status']!='complete' or not receipt['cleanup']['resources_released'] or not receipt['cleanup']['multicast_groups_empty'] or receipt['cleanup']['background_alive']:
            raise RuntimeError('baseline execution or cleanup incomplete')
        cycles=_whole_cycles(receipt);seconds=None if freeze['timebase']['seconds_per_cycle'] is None else cycles*freeze['timebase']['seconds_per_cycle']
        if mode=='fixture_only':
            metric_inputs=dict(status='not_publishable',moe_useful_flops=None,moe_issued_flops=None,non_moe_useful_flops=None,full_decode_useful_flops=None,
              device_count=None,selected_peak_id=None,non_moe_elapsed_seconds=None,overlap_seconds=None,missing_fields=['real full-decode/MFU baseline'])
            full_decode=dict(status='not_publishable',seconds=None,missing_fields=['real non-MoE time and physical timebase'])
        else:
            manifest=values['baseline'];metric_inputs=dict(status='complete',moe_useful_flops=manifest.moe_useful_flops,
              moe_issued_flops=values['route'].flops.group_issued_flops,non_moe_useful_flops=manifest.non_moe_useful_flops,
              full_decode_useful_flops=manifest.full_decode_useful_flops,device_count=manifest.device_count,selected_peak_id=manifest.selected_peak_id,
              non_moe_elapsed_seconds=manifest.non_moe_elapsed_seconds,overlap_seconds=manifest.moe_non_moe_overlap_seconds,missing_fields=[])
            full_decode=(dict(status='reported_units_only',seconds=None,missing_fields=['physical timebase']) if seconds is None else
              dict(status='physical',seconds=seconds+manifest.non_moe_elapsed_seconds-manifest.moe_non_moe_overlap_seconds,missing_fields=[]))
        raw=dict(schema_version=1,status='COMPLETE_PRODUCTION' if mode=='production' else 'COMPLETE_FIXTURE_ONLY',mode=mode,publishable=mode=='production' and freeze['timebase']['status']=='physical',
          freeze_sha256=freeze['freeze_sha256'],workload_sha256=freeze['identities']['workload_sha256'],binding_sha256=freeze['identities']['binding_sha256'],
          hardware_sha256=freeze['hardware_sha256'],reported_moe_cycles=cycles,moe_seconds=seconds,full_decode_latency=full_decode,
          four_metric_inputs=metric_inputs,timebase=freeze['timebase'],receipt=receipt)
        raw['result_sha256']=stable_sha256(raw);_atomic_json(temporary/'result.json',raw);os.replace(temporary,target);_set_state(run_dir,'BASELINE_COMPLETE',mode,raw['status']);return raw
    except Exception as exc:
        failure=dict(schema_version=1,status='FAILED',mode=mode,freeze_sha256=freeze['freeze_sha256'],error=str(exc),retry_same_case_forbidden=True)
        failure['failure_sha256']=stable_sha256(failure);_atomic_json(temporary/'failure.json',failure);os.replace(temporary,target);_set_state(run_dir,'BASELINE_FAILED',mode,str(exc));raise


def _apply_production_mutations(spec_document,hardware,mutations,scope):
    updated=apply_mutations(hardware,mutations,scope);raw=copy.deepcopy(spec_document);values=updated.by_path();applied=[]
    for mutation in mutations:
        parameter=values[mutation.path];value=parameter.quantity.value;resource=parameter.resource_id;matched=0
        if mutation.path.startswith(('data_noc.links.','data_noc.endpoints.')):
            for network in raw['batch']['timing']:
                rows=[dict(link_id=None,timing=network['link']),*network.get('overrides',[])]
                for row in rows:
                    timing=row['timing'];link_match=resource in (row.get('link_id'),f"{network['network_id']}/{row.get('link_id')}")
                    resource_match=resource in (timing.get('input_resource_id'),timing.get('output_resource_id'),timing.get('channel_resource_id'))
                    if link_match or resource_match:
                        if timing.get('mode')!='latency_fifo':raise ValueError('sweep target is not latency_fifo')
                        timing['issue_cycles']=max(1,math.ceil(timing['packet_bytes']/value));matched+=1
        else:
            for memory in raw['graph'].get('memory_resources',[]):
                hierarchy=memory.get('hierarchy')
                if not hierarchy:continue
                for channel in hierarchy['channels']:
                    if resource in (memory['resource_id'],channel['channel_id']):channel['bytes_per_cycle']=max(1,round(value));matched+=1
        if matched==0:raise ValueError('exact sweep resource not found in frozen SystemSpec: '+mutation.path)
        applied.append(dict(path=mutation.path,resource_id=resource,matched_resources=matched,value=value,unit=parameter.quantity.unit))
    return SystemSpec.model_validate_json(json.dumps(raw)),updated,applied


def run_sweep(run_dir,*,repo_root=None,fail_case=None):
    run_dir=Path(run_dir);freeze=_verify_freeze(run_dir,repo_root);baseline=_read(run_dir/'baseline/result.json')
    if baseline['freeze_sha256']!=freeze['freeze_sha256'] or not baseline['status'].startswith('COMPLETE'):raise ValueError('complete frozen baseline required')
    plan=SweepPlan.model_validate_json((run_dir/'freeze/frozen-inputs/sweep-plan.json').read_text());values=_load_frozen(run_dir,freeze);root=run_dir/'sweeps';root.mkdir(exist_ok=True);completed=[]
    for case in plan.cases:
        target=root/case.case_id
        if target.exists():raise ValueError('sweep case already attempted; overwrite/retry forbidden: '+case.case_id)
        temporary=root/('.'+case.case_id+'.tmp-'+uuid.uuid4().hex);temporary.mkdir()
        try:
            if fail_case==case.case_id:raise RuntimeError('INJECTED_SWEEP_CASE_FAILURE')
            scope=MutationScope(scope_id=case.case_id,allowed_paths=tuple(row.path for row in case.mutations),allowed_coupled_path_sets=(tuple(row.path for row in case.mutations),) if len(case.mutations)>1 else (),allow_multiple_independent=False)
            if freeze['mode']=='fixture_only':
                if len(case.mutations)==1:
                    comparison=compare_fixture_mutation(values['bundle'],case.mutations[0],scope);receipt=comparison['changed_receipt'];causality={key:comparison[key] for key in ('directly_targeted_actions','declared_downstream_actions','resource_downstream_actions','changed_actions','unexpected_changed_actions')}
                    changed_hash=comparison['changed_hardware_sha256']
                else:
                    changed=apply_mutations(values['hardware'],case.mutations,scope);execution=execute_phase3_fixture(values['bundle'],hardware=changed,production=False);receipt=execution.receipt;changed_hash=changed.parameter_sha256;causality=dict(status='explicit_coupled_case',unexpected_changed_actions=[])
                applied=[dict(path=row.path,operator=row.operator,operand=row.operand) for row in case.mutations]
            else:
                spec,changed,applied=_apply_production_mutations(values['binding'].system_spec,values['hardware'],case.mutations,scope);receipt=_execute_spec(spec);changed_hash=changed.parameter_sha256;causality=dict(status='exact_resource_binding',unexpected_changed_actions=[])
            if receipt['status']!='complete' or not receipt['cleanup']['resources_released']:raise RuntimeError('sweep execution or cleanup incomplete')
            raw=dict(schema_version=1,status='COMPLETE_PRODUCTION' if freeze['mode']=='production' else 'COMPLETE_FIXTURE_ONLY',mode=freeze['mode'],publishable=freeze['mode']=='production' and freeze['timebase']['status']=='physical',
              case_id=case.case_id,freeze_sha256=freeze['freeze_sha256'],sweep_plan_sha256=plan.plan_sha256,base_hardware_sha256=values['hardware'].parameter_sha256,
              changed_hardware_sha256=changed_hash,workload_sha256=freeze['identities']['workload_sha256'],binding_sha256=freeze['identities']['binding_sha256'],applied_parameters=applied,
              reported_moe_cycles=_whole_cycles(receipt),causality=causality,receipt=receipt)
            raw['result_sha256']=stable_sha256(raw);_atomic_json(temporary/'result.json',raw);os.replace(temporary,target);completed.append(case.case_id)
        except Exception as exc:
            failure=dict(schema_version=1,status='FAILED',case_id=case.case_id,freeze_sha256=freeze['freeze_sha256'],error=str(exc),retry_same_case_forbidden=True)
            failure['failure_sha256']=stable_sha256(failure);_atomic_json(temporary/'failure.json',failure);os.replace(temporary,target);_set_state(run_dir,'SWEEP_FAILED',freeze['mode'],dict(failed_case=case.case_id,completed_cases=completed));raise
    _set_state(run_dir,'SWEEP_COMPLETE',freeze['mode'],dict(completed_cases=completed));return dict(status='SWEEP_COMPLETE',completed_cases=completed,fixture_only=freeze['mode']=='fixture_only')


def _metric_unavailable(name,reason):return dict(metric_name=name,status='not_publishable',value=None,unit='ratio',missing_fields=[reason])
def _stage_times(receipt):return [dict(stage_id=row['stage_id'],duration_reported_units=None if row.get('start_cycles') is None or row.get('end_cycles') is None else row['end_cycles']-row['start_cycles']) for row in receipt.get('stage_boundaries',[])]


def _analyze_result(run,freeze,baseline_manifest=None,hardware=None):
    receipt=run['receipt'];cycles=run['reported_moe_cycles'];seconds=None if freeze['timebase']['seconds_per_cycle'] is None else cycles*freeze['timebase']['seconds_per_cycle']
    latency=dict(status='physical' if seconds is not None else 'reported_units_only',reported_value=cycles,reported_unit='runtime_cycle',seconds=seconds)
    metrics={}
    if run['mode']=='fixture_only':
        for name in ('useful_model_mfu','issued_engine_utilization','moe_local_mfu','full_decode_mfu'):metrics[name]=_metric_unavailable(name,'fixture-only inputs')
        full_decode=dict(status='not_publishable',seconds=None,missing_fields=['real non-MoE/full-decode baseline'])
    elif seconds is None:
        for name in ('useful_model_mfu','issued_engine_utilization','moe_local_mfu','full_decode_mfu'):metrics[name]=_metric_unavailable(name,'physical timebase missing')
        full_decode=dict(status='reported_units_only',seconds=None,missing_fields=['physical timebase'])
    else:
        peak=next(row for row in baseline_manifest.peak_candidates if row.peak_id==baseline_manifest.selected_peak_id);devices=baseline_manifest.device_count
        boundary=TimeBoundary(boundary_id='simulated-moe',start_event='R0',end_event='E8',scope='moe_local',elapsed_seconds=seconds,elapsed_cycles=None,clock_hz=None,includes_adalink=True,device_count=devices)
        metrics['useful_model_mfu']=compute_useful_model_mfu(baseline_manifest.moe_useful_flops,boundary,peak,devices).model_dump(mode='json')
        metrics['moe_local_mfu']=compute_moe_local_mfu(baseline_manifest.moe_useful_flops,boundary,peak,devices).model_dump(mode='json')
        metrics['issued_engine_utilization']=_metric_unavailable('issued_engine_utilization','precision-specific issued padded work, active engine boundary/timebase and W4A8 peak evidence are required')
        full_seconds=seconds+baseline_manifest.non_moe_elapsed_seconds-baseline_manifest.moe_non_moe_overlap_seconds
        full_boundary=TimeBoundary(boundary_id='simulated-full-decode',start_event=baseline_manifest.full_decode_boundary.start_event,end_event=baseline_manifest.full_decode_boundary.end_event,
          scope='full_decode',elapsed_seconds=full_seconds,elapsed_cycles=None,clock_hz=None,includes_adalink=True,device_count=devices)
        updated=freeze_decode_baseline(manifest_id=baseline_manifest.manifest_id+'-simulated',workload_sha256=baseline_manifest.workload_sha256,moe_boundary=boundary,full_decode_boundary=full_boundary,
          moe_useful_flops=baseline_manifest.moe_useful_flops,non_moe_useful_flops=baseline_manifest.non_moe_useful_flops,full_decode_useful_flops=baseline_manifest.full_decode_useful_flops,
          non_moe_elapsed_seconds=baseline_manifest.non_moe_elapsed_seconds,moe_non_moe_overlap_seconds=baseline_manifest.moe_non_moe_overlap_seconds,device_count=devices,
          peak_candidates=baseline_manifest.peak_candidates,selected_peak_id=baseline_manifest.selected_peak_id,source_refs=baseline_manifest.source_refs,completeness='complete',missing_fields=())
        metrics['full_decode_mfu']=compute_full_decode_mfu(updated).model_dump(mode='json');full_decode=dict(status='physical',seconds=full_seconds,manifest_sha256=updated.manifest_sha256)
    component_times=receipt.get('component_times')
    if component_times is None:
        grouped=defaultdict(float)
        for row in receipt.get('native_transactions',[]):
            if row.get('start_cycles') is not None and row.get('end_cycles') is not None:grouped[row.get('category','unknown')]+=row['end_cycles']-row['start_cycles']
        component_times=dict(grouped)
    return dict(case_id=run.get('case_id','baseline'),latency=latency,full_decode_latency=full_decode,metrics=metrics,stage_times=_stage_times(receipt),component_times=component_times,
      resource_waits=sorted(receipt.get('resources',[]),key=lambda row:row.get('wait_cycles',0),reverse=True),critical_path=receipt.get('event_resource_critical_path',[]),bottleneck=_resource_critical(receipt),
      parameter_causality=run.get('causality'),applied_parameters=run.get('applied_parameters',[]))


def analyze(run_dir,*,repo_root=None):
    run_dir=Path(run_dir);freeze=_verify_freeze(run_dir,repo_root);target=run_dir/'analysis'
    if target.exists():raise ValueError('analysis already exists')
    temporary=run_dir/('.analysis.tmp-'+uuid.uuid4().hex);temporary.mkdir()
    plan=SweepPlan.model_validate_json((run_dir/'freeze/frozen-inputs/sweep-plan.json').read_text());baseline_path=run_dir/'baseline/result.json'
    missing=[] if baseline_path.is_file() else ['baseline'];cases=[]
    for case in plan.cases:
        path=run_dir/'sweeps'/case.case_id/'result.json'
        if not path.is_file():missing.append(case.case_id)
        else:cases.append(_read(path))
    if missing:
        raw=dict(schema_version=1,status='PARTIAL_NOT_PUBLISHABLE',freeze_sha256=freeze['freeze_sha256'],missing_or_failed=missing,formal_summary_created=False)
        raw['analysis_sha256']=stable_sha256(raw);_atomic_json(temporary/'blocked.json',raw);os.replace(temporary,target);_set_state(run_dir,'ANALYSIS_BLOCKED',freeze['mode'],missing);return raw
    baseline=_read(baseline_path);values=_load_frozen(run_dir,freeze);manifest=None if freeze['mode']=='fixture_only' else values['baseline'];hardware=values['hardware']
    base_analysis=_analyze_result(baseline,freeze,manifest,hardware);case_analysis=[_analyze_result(row,freeze,manifest,hardware) for row in cases]
    analyses=[base_analysis,*case_analysis]
    publishable=freeze['mode']=='production' and all(row['latency']['status']=='physical' and all(metric['status']=='complete' for metric in row['metrics'].values()) for row in analyses)
    raw=dict(schema_version=1,status=('COMPLETE_PRODUCTION' if publishable else 'COMPLETE_PRODUCTION_METRICS_INCOMPLETE') if freeze['mode']=='production' else 'COMPLETE_FIXTURE_ONLY_NOT_PUBLISHABLE',mode=freeze['mode'],publishable=publishable,
      freeze_sha256=freeze['freeze_sha256'],sweep_plan_sha256=plan.plan_sha256,baseline=base_analysis,cases=case_analysis,
      bottleneck_migration=[_bottleneck_migration(base_analysis,row) for row in case_analysis],
      whole_moe_refit=False,per_case_refit=False,workload_route_topology_fixed=True,architecture_benefit_claim=publishable)
    raw['analysis_sha256']=stable_sha256(raw);_atomic_json(temporary/'result.json',raw);os.replace(temporary,target);_set_state(run_dir,'ANALYZED',freeze['mode'],raw['status']);return raw


def _parser():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('validate-inputs');p.add_argument('--run-dir',required=True);p.add_argument('--input-dir');p.add_argument('--fixture-profile',choices=('standalone','transformer-fused'))
    p=sub.add_parser('freeze-baseline');p.add_argument('--run-dir',required=True);p.add_argument('--sweep-plan',required=True)
    p=sub.add_parser('run-baseline');p.add_argument('--run-dir',required=True);p.add_argument('--inject-failure',action='store_true')
    p=sub.add_parser('run-sweep');p.add_argument('--run-dir',required=True);p.add_argument('--fail-case')
    p=sub.add_parser('analyze');p.add_argument('--run-dir',required=True)
    p=sub.add_parser('status');p.add_argument('--run-dir',required=True)
    p=sub.add_parser('design-space');p.add_argument('arguments',nargs=argparse.REMAINDER)
    return parser


def cli(argv=None):
    args=_parser().parse_args(argv)
    try:
        if args.command=='design-space':
            from .design_space import cli as design_space_cli
            return design_space_cli(args.arguments)
        if args.command=='validate-inputs':result=validate_inputs(args.run_dir,input_dir=args.input_dir,fixture_profile=args.fixture_profile)
        elif args.command=='freeze-baseline':result=freeze_baseline(args.run_dir,args.sweep_plan)
        elif args.command=='run-baseline':result=run_baseline(args.run_dir,inject_failure=args.inject_failure)
        elif args.command=='run-sweep':result=run_sweep(args.run_dir,fail_case=args.fail_case)
        elif args.command=='analyze':result=analyze(args.run_dir)
        else:result=status(args.run_dir)
        print(json.dumps(result,sort_keys=True));return 0 if result.get('ready',True) and not str(result.get('status','')).startswith(('BLOCKED','FAILED','PARTIAL')) else 2
    except Exception as exc:
        print(json.dumps(dict(status='ERROR',error=str(exc)),sort_keys=True));return 2


def main():raise SystemExit(cli())


__all__=['BaselineFreezeManifest','SweepCase','SweepPlan','analyze','cli','freeze_baseline','freeze_sweep_plan','run_baseline','run_sweep','status','validate_inputs']
