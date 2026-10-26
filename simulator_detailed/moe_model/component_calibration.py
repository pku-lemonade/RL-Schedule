"""Deterministic M01-M08 calibration bundle and action-level resolver.

Only final accepted frozen evidence is selected. Failed/diagnostic revisions
are recorded as exclusions and can never participate in resolution.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
import hashlib,json,re,subprocess
from pydantic import Field,model_validator

from .engine_transient import TransientEngineFormula
from .matrix import MatrixWork
from .matrix_v_domain import MatrixVDomainFormula
from .operation_plan import build_operation_plan
from .phase3_fixture import build_phase3_fixture
from .vector import VectorWork
from .vector_timing import LongRepeatedVectorFormula,RecurringTpcVectorFormula
from .vector_v_domain import VectorVDomainFormula
from .chain_v_domain import ChainPayloadFormula
from .gm_v_domain import GMServiceDomainFormula
from .gm_source import GMSourceWork
from .write_sum_primitive import (
    CombinedWriteSumFormula,
    WriteSumPrimitiveDomain,
    append_write_sum_timebase,
)
from .r10_stage import R10BoundaryWork, R10BoundaryDomain, append_r10_timebases
from .r11_stage import R11Work,R11Domain,append_r11_timebase
from .fused_preseed import FusedPreseedWork,FusedPreseedDomain,append_fused_preseed_timebase,work_from_operation_plan
from .e7_join import E7JoinWork,E7JoinDomain,append_e7_timebase
from .attn_output_norm import AttnOutputNormWork,AttnOutputNormDomain,append_attn_output_norm_timebase
from .e4_e5_handoff import E4E5HandoffWork,E4E5HandoffDomain,append_e4_e5_timebase
from .e0_e1_handoff import E0E1HandoffWork,E0E1HandoffDomain,append_e0_e1_timebase
from .e2_e3_handoff import E2E3HandoffWork,E2E3HandoffDomain,append_e2_e3_timebase
from .e1_e2_handoff import E1E2HandoffWork,E1E2HandoffDomain,append_e1_e2_timebase
from .e3_e4_handoff import E3E4HandoffWork,E3E4HandoffDomain,append_e3_e4_timebase
from .r7_r8_handoff import R7R8HandoffWork,R7R8HandoffDomain,append_r7_r8_timebase
from .r9_count_handoff import R9CountHandoffWork,R9CountHandoffDomain,append_r9_count_timebase
from .count_token_handoff import CountTokenHandoffWork,CountTokenHandoffDomain,append_count_token_timebase
from .token_id_fill import TokenIdFillWork,TokenIdFillDomain,append_token_id_fill_timebase
from .router_rank_domains import RouterRankWork,RouterRankDomain,rank_work,append_rank_timebase
from .count_fill import CountFillWork,CountFillDomain,count_work,append_count_timebase
from .scalar_tail import ScalarTailWork,ScalarTailDomain,tail_work,append_tail_timebase
from .count_scan import CountScanWork,CountScanDomain,scan_work,append_scan_timebase
from .expert_matrix import ExpertMatrixWork
from .gate_up_matrix import GateUpWork
from .datanoc_v_domain import DataNocDomainFormula
from .sync_resource import SyncLocalFormula
from .adalink_domain import AdalinkWqeFormula
from .timebase import TimebaseBundle
from .router_shard import RouterShardFormula
from .vector_bulk import VectorBulkWork,VectorBulkFormula,bulk_work_from_row,bulk_work_from_plan
from .workload import FrozenMoeWorkload,FrozenRecord,PROFILES,stable_sha256

ComponentId=Literal['M01','M02','M03','M04','M05','M06','M07','M08']
EntryStatus=Literal['validated_limited','cpu_only_binding_gate','unavailable']
PublicationClass=Literal['current_architecture_component_limited','protocol_only','not_publishable']
ProfileName=Literal['standalone','transformer-fused']


class EvidenceArtifact(FrozenRecord):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class CalibrationSceneEvidence(FrozenRecord):
    scene_id: str
    role: Literal['calibration','validation']
    artifacts: tuple[EvidenceArtifact,...]
    max_error: float | None = Field(default=None,ge=0)
    error_unit: str | None=None
    passed: bool


class ErrorSummary(FrozenRecord):
    metric: str
    max_error: float | None = Field(default=None,ge=0)
    threshold: float | None = Field(default=None,ge=0)
    unit: str
    validation_scene_count: int = Field(ge=0)


class ActionDomain(FrozenRecord):
    shape_keys: tuple[str,...]=()
    input_dimensions: dict
    v_min: int | None = Field(default=None,ge=0)
    v_max: int | None = Field(default=None,ge=0)
    excluded_v: tuple[int,...]=()
    payload_min_bytes: int | None = Field(default=None,ge=0)
    payload_max_bytes: int | None = Field(default=None,ge=0)
    fanout_values: tuple[int,...]=()
    scenario_classes: tuple[str,...]=()
    operation_variants: tuple[str,...]=()
    controller_counts: tuple[int,...]=()
    transaction_counts: tuple[int,...]=()
    address_distributions: tuple[str,...]=()
    flow_counts: tuple[int,...]=()
    source_counts: tuple[int,...]=()
    directions: tuple[str,...]=()
    producer_counts: tuple[int,...]=()
    waiter_counts: tuple[int,...]=()
    round_counts: tuple[int,...]=()
    frontiers: tuple[str,...]=()
    rank_counts: tuple[int,...]=()
    chunk_counts: tuple[int,...]=()
    commid_slots: tuple[int,...]=()
    schedule_domains: tuple[str,...]=()
    router_invocation_counts: tuple[int,...]=()
    matrix_shapes: tuple[tuple[int,int,int],...]=()
    weight_localities: tuple[str,...]=()
    feature_layouts: tuple[str,...]=()
    feature_strides: tuple[int,...]=()
    route_domains: tuple[str,...]
    applicability_text: str
    @model_validator(mode='after')
    def complete(self):
        if self.v_min is not None and self.v_max is not None and self.v_min>self.v_max:raise ValueError('V domain inverted')
        if self.payload_min_bytes is not None and self.payload_max_bytes is not None and self.payload_min_bytes>self.payload_max_bytes:raise ValueError('payload domain inverted')
        if len(set(self.excluded_v))!=len(self.excluded_v) or len(set(self.fanout_values))!=len(self.fanout_values) or not self.route_domains or not self.applicability_text:
            raise ValueError('action domain incomplete')
        return self


class ComponentCalibrationEntry(FrozenRecord):
    entry_id: str
    component_id: ComponentId
    mechanism: str
    operation: str
    profiles: tuple[ProfileName,...]
    consumer_stages: tuple[str,...]
    parameter_paths: tuple[str,...]
    formula_id: str
    formula: dict
    formula_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    code_artifacts: tuple[EvidenceArtifact,...]
    domain: ActionDomain
    duration_unit: str
    observation_unit: str
    timebase_status: Literal['tsim_component_ns','protocol_only','reported_units_only','local_reported_ns','local_reported_cycle','local_tsim_counter','unavailable']
    calibration_scenes: tuple[CalibrationSceneEvidence,...]
    validation_scenes: tuple[CalibrationSceneEvidence,...]
    error_summary: ErrorSummary
    status: EntryStatus
    publication_class: PublicationClass
    source_commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    source_refs: tuple[str,...]
    entry_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        if not self.entry_id or not self.mechanism or not self.operation or not self.profiles or len(set(self.profiles))!=len(self.profiles):raise ValueError('entry identity/profile invalid')
        if not self.consumer_stages or not self.parameter_paths or len(set(self.parameter_paths))!=len(self.parameter_paths):raise ValueError('entry stages/parameters invalid')
        if stable_sha256(self.formula)!=self.formula_sha256:raise ValueError('formula hash mismatch')
        cal={x.scene_id for x in self.calibration_scenes};val={x.scene_id for x in self.validation_scenes}
        if len(cal)!=len(self.calibration_scenes) or len(val)!=len(self.validation_scenes) or cal&val:raise ValueError('calibration/validation scene split invalid')
        if self.status=='validated_limited' and (not self.calibration_scenes or not self.validation_scenes or not all(x.passed for x in (*self.calibration_scenes,*self.validation_scenes))):raise ValueError('validated entry requires passing disjoint evidence')
        if self.status!='validated_limited' and self.publication_class=='current_architecture_component_limited':raise ValueError('nonvalidated entry cannot be timing-publishable')
        if self.status=='cpu_only_binding_gate' and (self.calibration_scenes or self.validation_scenes or self.publication_class!='not_publishable'):raise ValueError('CPU-only entry cannot carry calibration evidence')
        expected_units={
          'tsim_component_ns':('model_cycle','ns'),
          'protocol_only':('protocol_event','dependency_order'),
          'reported_units_only':('unavailable','reported_units'),
          'unavailable':('unavailable','unavailable'),
          'local_reported_ns':('reported_ns','reported_ns'),
          'local_reported_cycle':('reported_cycle','reported_cycle'),
          'local_tsim_counter':('tsim_counter_default_unit','simulation_seconds')}
        if (self.duration_unit,self.observation_unit)!=expected_units[self.timebase_status]:raise ValueError('entry unit/timebase contract mismatch')
        if not self.code_artifacts or not self.source_refs:raise ValueError('entry code/source evidence required')
        if self.error_summary.validation_scene_count!=len(self.validation_scenes):raise ValueError('error scene count mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'entry_sha256'}))!=self.entry_sha256:raise ValueError('entry hash mismatch')
        return self


class ExcludedEvidence(FrozenRecord):
    component_id: ComponentId
    revision: str
    reason: str
    source_refs: tuple[str,...]
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ComponentCalibrationBundle(FrozenRecord):
    schema_version: Literal[1]=1
    bundle_id: str
    fixture_only: bool
    entries: tuple[ComponentCalibrationEntry,...]
    excluded_evidence: tuple[ExcludedEvidence,...]
    source_repository_commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    timebases: TimebaseBundle | None=None
    source_refs: tuple[str,...]
    bundle_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        ids=[x.entry_id for x in self.entries]
        if len(ids)!=len(set(ids)) or {x.component_id for x in self.entries}!={f'M{i:02d}' for i in range(1,9)}:raise ValueError('bundle entries must uniquely cover M01-M08')
        signatures=[(x.component_id,x.operation,x.profiles,x.domain.shape_keys,x.domain.v_min,x.domain.v_max,x.domain.payload_min_bytes,x.domain.payload_max_bytes,x.domain.route_domains,x.domain.scenario_classes,x.domain.operation_variants,x.domain.controller_counts,x.domain.transaction_counts,x.domain.address_distributions,x.domain.flow_counts,x.domain.source_counts,x.domain.directions,x.domain.producer_counts,x.domain.waiter_counts,x.domain.round_counts,x.domain.frontiers,x.domain.rank_counts,x.domain.chunk_counts,x.domain.commid_slots,x.domain.schedule_domains,x.domain.router_invocation_counts,x.domain.matrix_shapes,x.domain.weight_localities,x.domain.feature_layouts,x.domain.feature_strides) for x in self.entries]
        if len(signatures)!=len(set(signatures)):raise ValueError('duplicate/conflicting calibration entry')
        if any(x.revision in {entry.formula_id for entry in self.entries} for x in self.excluded_evidence):raise ValueError('excluded revision selected as accepted')
        if not self.source_refs:raise ValueError('bundle sources required')
        if stable_sha256(self.model_dump(mode='json',exclude={'bundle_sha256'}|({'timebases'} if self.timebases is None else set())))!=self.bundle_sha256:raise ValueError('bundle hash mismatch')
        return self


class CalibrationResolutionRequest(FrozenRecord):
    request_id: str
    workload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    action_id: str
    component_id: ComponentId
    mechanism: str
    operation: str
    profile: ProfileName
    consumer_stage: str
    parameter_paths: tuple[str,...]=()
    shape_key: str | None=None
    V: int | None = Field(default=None,ge=0)
    payload_bytes: int | None = Field(default=None,ge=0)
    fanout: int | None = Field(default=None,ge=1)
    route_domain: str
    requires_physical_time: bool
    scenario_class: str | None=None
    operation_variant: str | None=None
    controller_count: int | None=Field(default=None,gt=0)
    transaction_count: int | None=Field(default=None,gt=0)
    address_distribution: str | None=None
    flow_count: int | None=Field(default=None,gt=0)
    source_count: int | None=Field(default=None,gt=0)
    direction: str | None=None
    producer_count: int | None=Field(default=None,gt=0)
    waiter_count: int | None=Field(default=None,gt=0)
    rounds: int | None=Field(default=None,gt=0)
    frontier: str | None=None
    rank_count: int | None=Field(default=None,ge=2)
    chunk_count: int | None=Field(default=None,gt=0)
    commid_slot_idx: int | None=Field(default=None,ge=0)
    ccl_config_sha256: str | None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    rank_map_sha256: str | None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    comm_program_sha256: str | None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    schedule_domain: str | None=None
    router_invocations: int | None=Field(default=None,gt=0)
    matrix_shape: tuple[int,int,int] | None=None
    weight_locality: str | None=None
    feature_layout: str | None=None
    feature_stride: int | None=Field(default=None,gt=0)
    vector_bulk_work: VectorBulkWork | None=None
    gm_source_work: GMSourceWork | None=None
    r10_boundary_work: R10BoundaryWork | None=None
    r11_work: R11Work | None=None
    fused_preseed_work: FusedPreseedWork | None=None
    e7_join_work: E7JoinWork | None=None
    attn_output_norm_work: AttnOutputNormWork | None=None
    e4_e5_handoff_work: E4E5HandoffWork | None=None
    e0_e1_handoff_work: E0E1HandoffWork | None=None
    e2_e3_handoff_work: E2E3HandoffWork | None=None
    e1_e2_handoff_work: E1E2HandoffWork | None=None
    e3_e4_handoff_work: E3E4HandoffWork | None=None
    r7_r8_handoff_work: R7R8HandoffWork | None=None
    r9_count_handoff_work: R9CountHandoffWork | None=None
    count_token_handoff_work: CountTokenHandoffWork | None=None
    token_id_fill_work: TokenIdFillWork | None=None
    router_rank_work: RouterRankWork | None=None
    router_rank_token_counts: tuple[int,...] | None=None
    count_fill_work: CountFillWork | None=None
    scalar_tail_work: ScalarTailWork | None=None
    count_scan_work: CountScanWork | None=None
    expert_matrix_work: ExpertMatrixWork | None=None
    expert_matrix_unit: str | None=None
    gate_up_work: GateUpWork | None=None
    gate_up_unit: str | None=None
    compiled_path_sha256: str | None=None
    model_library_sha256: str | None=None
    systemc_library_sha256: str | None=None
    observer_sha256: str | None=None
    counter_callsite: int | None=None
    counter_callsites: tuple[int,...]=()


class CalibrationResolution(FrozenRecord):
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    entry_id: str
    entry_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    formula_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    publication_class: PublicationClass


class _FreezeIndex:
    def __init__(self,root,freeze_path):
        self.root=Path(root).resolve();self.freeze_path=(self.root/freeze_path).resolve();self.document=json.loads(self.freeze_path.read_text());self.base=self.freeze_path.parent.parent
        rows=[*self.document['files'],*self.document.get('external_files',[])]
        self.rows={str((Path(row['path']) if Path(row['path']).is_absolute() else self.base/row['path']).resolve()):row['sha256'] for row in rows}
    def artifact(self,relative):
        path=(self.base/relative).resolve();key=str(path)
        if key not in self.rows:raise ValueError(f'accepted artifact absent from freeze: {relative}')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=self.rows[key]:raise ValueError(f'accepted artifact hash drift: {relative}')
        try:label=str(path.relative_to(self.root))
        except ValueError:label=str(path)
        return EvidenceArtifact(path=label,sha256=actual)


def _json(path):return json.loads(Path(path).read_text())
def _current_artifact(root,path):
    path=(Path(root)/path).resolve();return EvidenceArtifact(path=str(path.relative_to(Path(root).resolve())),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
def _commit(root,path):return subprocess.run(['git','log','-1','--format=%H','--',path],cwd=root,text=True,capture_output=True,check=True).stdout.strip()
def _freeze_entry(**kwargs):
    raw=dict(kwargs);raw['formula_sha256']=stable_sha256(raw['formula'])
    from pydantic import TypeAdapter
    shape=TypeAdapter(dict).dump_python(raw,mode='json');return ComponentCalibrationEntry(**raw,entry_sha256=stable_sha256(shape))
def _artifact_scene(index,run_id,role,error=None,unit='ns',registration='calibration-registration.json',prediction=None):
    refs=[index.artifact(registration),_current_artifact(index.root,index.base.relative_to(index.root)/'runs'/run_id/'result.json')]
    if prediction is not None:refs.append(index.artifact(prediction))
    if role=='validation':refs.append(_current_artifact(index.root,index.base.relative_to(index.root)/'runs'/run_id/'validation.json'))
    return CalibrationSceneEvidence(scene_id=run_id,role=role,artifacts=tuple(refs),max_error=error,error_unit=unit,passed=True)
def _max_validation_error(root,base,runs):
    errors={};maximum=0.0
    for run in runs:
        doc=_json(Path(root)/base/'runs'/run['run_id']/'validation.json');
        if not doc.get('pass_gate',False):raise ValueError('accepted validation is not PASS: '+run['run_id'])
        value=doc.get('max_error_ns')
        if value is None:value=max([row.get('error_ns',0) for row in doc.get('members',[])]+[0])
        errors[run['run_id']]=float(value);maximum=max(maximum,float(value))
    return errors,maximum
def _fits(index):
    rows=index.document.get('fits',index.document.get('calibration_fit',[]));return {row['run_id']:float(row.get('max_error_ns',row.get('max_engine_error_ns',0))) for row in rows}
def _threshold(index):
    values=[value for key,value in index.document.get('thresholds',{}).items() if key.endswith('_ns') and isinstance(value,(int,float))]
    return None if not values else float(max(values))
def _paths(prefix,value):
    result=[]
    def walk(name,item):
        if isinstance(item,dict):
            for key,child in item.items():walk(name+'.'+key,child)
        elif isinstance(item,(int,float)) and not isinstance(item,bool):result.append(name)
    walk(prefix,value);return tuple(sorted(result))
def _run_value(run,key,default=None):return run[key] if key in run else default
def _source_v(scene_id):
    match=re.search(r'-V(\d+)$',scene_id);return None if match is None else int(match.group(1))


def _simple_entries(root,phase,component,operations,stages,route_domains):
    tsim=Path(root)/phase/'tsim';index=_FreezeIndex(root,f'{phase}/tsim/freeze/pre-validation-freeze.json');result=_json(Path(root)/phase/'result.json')
    cal=_json(tsim/'calibration-registration.json')['runs'];val=_json(tsim/'validation-registration.json')['runs'];params=_json(tsim/'parameters.json');fits=_fits(index);errors,maximum=_max_validation_error(root,f'{phase}/tsim',val);commit=_commit(root,f'{phase}/result.json')
    code_name='component_model.py' if phase=='phase1' else 'model.py';code=(index.artifact(code_name),)
    out=[]
    for operation in operations:
        selector=('primitive' if component in ('M01','M02') else 'axis' if component=='M03' else 'mode')
        cruns=[row for row in cal if row[selector]==operation];vruns=[row for row in val if row[selector]==operation]
        payloads=[row['payload_bytes'] for row in (*cruns,*vruns)];fanouts=tuple(sorted({len(row.get('members',[])) for row in (*cruns,*vruns) if row.get('members')}))
        formula=dict(params);formula['operation']=operation
        if component=='M03':formula=dict(packet_service_cycles=params['packet_service_cycles'],**params[operation],operation=operation)
        calibration=tuple(_artifact_scene(index,row['run_id'],'calibration',fits[row['run_id']]) for row in cruns)
        validation=tuple(_artifact_scene(index,row['run_id'],'validation',errors[row['run_id']],registration='validation-registration.json',prediction='predictions.json') for row in vruns)
        out.append(_freeze_entry(entry_id=f'{component.lower()}-{operation}',component_id=component,mechanism={'M01':'point_to_point','M02':'masked_multicast','M03':'pe_chain','M04':'gm_service'}[component],operation=operation,
          profiles=('standalone','transformer-fused'),consumer_stages=tuple(stages[operation]),parameter_paths=_paths(component.lower(),formula),formula_id=f'{component}-{operation}-accepted',formula=formula,code_artifacts=code,
          domain=ActionDomain(input_dimensions=formula,v_min=None,v_max=None,excluded_v=(),payload_min_bytes=min(payloads),payload_max_bytes=max(payloads),fanout_values=fanouts,
            route_domains=tuple(route_domains[operation]),applicability_text=result.get('domain','; '.join(result.get('limitations',result.get('limits',[]))) or index.document.get('model_domain','limited accepted domain'))),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,
          error_summary=ErrorSummary(metric='component_boundary_error',max_error=maximum,threshold=_threshold(index),unit='ns',validation_scene_count=len(validation)),
          status='validated_limited',publication_class='current_architecture_component_limited',source_commit=commit,source_refs=(f'{phase}/result.json',str(index.freeze_path.relative_to(index.root)))))
    return out


def _m05_entries(root):
    base=Path(root)/'phase2c/tsim';index=_FreezeIndex(root,'phase2c/tsim/freeze-r3/pre-validation-freeze.json');result=_json(Path(root)/'phase2c/result.json')
    formulas=_json(base/'formulas-r3.json');cal=_json(base/'calibration-registration.json')['runs'];val=_json(base/'validation-registration-r3.json')['runs'];fits=_fits(index);errors,maximum=_max_validation_error(root,'phase2c/tsim',val);commit=_commit(root,'phase2c/result.json')
    code=(index.artifact('model.py'),index.artifact('../../public-patch/t81-overlay/simulator_detailed/moe_model/matrix.py'),index.artifact('../../public-patch/t81-overlay/simulator_detailed/moe_model/engine_transient.py'))
    stage={'router':('R6',),'gate_up':('E0',),'down':('E4',)};out=[]
    for key,formula in formulas.items():
        TransientEngineFormula.model_validate_json(json.dumps(formula));profile,operation=formula['shape_key'],formula['operation'];cruns=[row for row in cal if row['profile']==profile and row['operation']==operation];vruns=[row for row in val if row['profile']==profile and row['operation']==operation]
        v=[row['V'] for row in (*cruns,*vruns)];calibration=tuple(_artifact_scene(index,row['run_id'],'calibration',fits[row['run_id']]) for row in cruns);validation=tuple(_artifact_scene(index,row['run_id'],'validation',errors[row['run_id']],registration='validation-registration-r3.json',prediction='predictions-r3.json') for row in vruns)
        work=MatrixWork(work_id='bundle',profile=profile,operation=operation,V=1 if operation!='router' else 0,pe_id=0,expert_sequence=(0,) if operation!='router' else ())
        out.append(_freeze_entry(entry_id='m05-'+key,component_id='M05',mechanism='matrix',operation=operation,profiles=(profile,),consumer_stages=stage[operation],parameter_paths=tuple(row['parameter_path'] for row in formula['slices']),
          formula_id=formula['model_revision'],formula=formula,code_artifacts=code,domain=ActionDomain(shape_keys=(profile,),input_dimensions=dict(shape=work.shape(),precision=work.precision()),v_min=min(v),v_max=max(v),excluded_v=(),
          payload_min_bytes=None,payload_max_bytes=None,fanout_values=(),route_domains=('resident-weight-current-architecture',),applicability_text=formula['applicability']),duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',
          calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='engine_boundary_error',max_error=maximum,threshold=_threshold(index),unit='ns',validation_scene_count=len(validation)),
          status='validated_limited',publication_class='current_architecture_component_limited',source_commit=commit,source_refs=('phase2c/result.json','phase2c/tsim/formulas-r3.json','phase2c/tsim/validation-registration-r3.json')))
    return out


def _router_source_entries(root):
    base=Path(root)/'c05-router-domain-r3/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c05-router-domain-r3/tsim/freeze/final-evidence.json');accepted=_json(base/'accepted-domain.json')['accepted'];formulas=_json(base/'formulas.json');fits={r['run_id']:r for r in _json(base/'calibration-fit.json')};out=[]
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/router_shard.py').resolve())),final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/router_matrix.py').resolve())),final.artifact('prepare_freeze.py'),final.artifact('extract.py'))
    for row in accepted:
        f=formulas[row['profile']];formula=RouterShardFormula.model_validate_json(json.dumps(f))
        calibration=tuple(_artifact_scene(final,rid,'calibration',fits[rid]['max_error_ns']) for rid in formula.calibration_scene_ids)
        validation=(_artifact_scene(final,row['run_id'],'validation',row['max_error_ns'],registration='validation-registration.json',prediction='predictions.json'),)
        out.append(_freeze_entry(entry_id='m05-router-source-r3-'+row['profile'],component_id='M05',mechanism='matrix',operation='router',profiles=(row['profile'],),consumer_stages=('R6',),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=f,code_artifacts=code,
          domain=ActionDomain(shape_keys=(row['profile'],),input_dimensions=dict(route_V_is_expert_metadata=True,router_tasks_per_PE=1,PE_count=32,source_shard_expression='pe_id // 4',per_tile_throughput_calibrated=False),v_min=row['route_V'],v_max=row['route_V'],router_invocation_counts=(1,),matrix_shapes=(formula.shape,),weight_localities=(formula.weight_locality,),feature_layouts=(formula.feature_layout,),feature_strides=(formula.feature_stride,),route_domains=('source-router-current-address',),applicability_text='exact profile route_V, one source-shaped BF16 router on every PE with source-hidden stride and resident packed weights; per-shard engine boundary timing only'),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='router_source_shard_engine_start_active_finish_end',max_error=row['max_error_ns'],threshold=row['threshold_ns'],unit='ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=final.document['source_commits']['start_commit'],source_refs=('c05-router-domain-r3/tsim/freeze/pre-validation-freeze.json','c05-router-domain-r3/tsim/freeze/final-evidence.json')))
    return out


def _c0102_entries(root):
    base=Path(root)/'c01-c02-datanoc-domain/tsim';final=_FreezeIndex(root,'c01-c02-datanoc-domain/tsim/freeze/final-evidence.json');domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas-c0102.json');cal=_json(base/'calibration-registration-r4.json')['runs'];fits={r['run_id']:r['max_error_ns'] for r in _json(base/'calibration-fit-r3.json')}
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/datanoc_v_domain.py').resolve())),final.artifact('prepare_freeze.py'),final.artifact('finalize.py'))
    out=[]
    for accepted in domain['accepted']:
        prediction=next(r for r in _json(base/'predictions.json') if r['run_id']==accepted['run_id']);vrun=next(r for r in _json(base/'validation-registration-r4.json')['runs'] if r['run_id']==accepted['run_id']);formula=formulas[prediction['family']];DataNocDomainFormula.model_validate_json(json.dumps(formula));cruns=[r for r in cal if r['run_id'] in formula['source_refs']]
        calibration=tuple(_artifact_scene(final,r['run_id'],'calibration',fits[r['run_id']],registration='calibration-registration-r4.json') for r in cruns);validation=(_artifact_scene(final,accepted['run_id'],'validation',accepted['max_error_ns'],registration='validation-registration-r4.json',prediction='predictions.json'),)
        component='M01' if accepted['primitive']=='point_to_point' else 'M02';role=accepted.get('flow_role') or accepted['direction'];entry=f"{component.lower()}-c0102-{accepted['profile']}-{accepted['stage']}-{role}-B{accepted['payload_bytes']}"
        out.append(_freeze_entry(entry_id=entry,component_id=component,mechanism=accepted['primitive'],operation=accepted['primitive'],profiles=(accepted['profile'],),consumer_stages=(accepted['stage'],),parameter_paths=tuple(b['parameter_path'] for b in formula['boundaries']),formula_id=formula['model_revision'],formula=formula,code_artifacts=code,
          domain=ActionDomain(shape_keys=(accepted['profile'],),input_dimensions=dict(pattern=accepted['pattern'],observer_scope='TSim native topology; production physical route not attested'),payload_min_bytes=accepted['payload_bytes'],payload_max_bytes=accepted['payload_bytes'],fanout_values=(accepted['fanout'],),scenario_classes=(accepted['concurrency'],),operation_variants=(accepted['direction'],),flow_counts=(accepted['flow_count'],),source_counts=(vrun['source_count'],),directions=(accepted['direction'],),route_domains=(accepted['route_domain'],),applicability_text='validated logical pattern/payload/mask/concurrency; not physically publishable until supplied real route attestation'),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='packet_endpoint_boundary_error',max_error=accepted['max_error_ns'],threshold=accepted['threshold_ns'],unit='ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=final.document['source_commits']['start_commit'],source_refs=('c01-c02-datanoc-domain/tsim/freeze/pre-validation-freeze.json','c01-c02-datanoc-domain/tsim/freeze/final-evidence.json','c07-sync-domain/tsim/freeze/final-evidence.json')))
    return out


def _c04_entries(root):
    base=Path(root)/'c04-gm-production-domain/tsim';final=_FreezeIndex(root,'c04-gm-production-domain/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c04-gm-production-domain/tsim/freeze/pre-validation-freeze.json')
    domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas-c04.json');cal=_json(base/'calibration-registration.json')['runs'];fits={r['run_id']:r['max_error_ns'] for r in _json(base/'calibration-fit.json')}
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/gm_v_domain.py').resolve())),final.artifact('prepare_freeze.py'),final.artifact('finalize.py'))
    out=[]
    for accepted in domain['accepted']:
        family=next(r['family'] for r in _json(base/'predictions.json') if r['run_id']==accepted['run_id']);formula=formulas[family];GMServiceDomainFormula.model_validate_json(json.dumps(formula))
        cruns=[r for r in cal if r['run_id'] in formula['source_refs']];calibration=tuple(_artifact_scene(final,r['run_id'],'calibration',fits[r['run_id']]) for r in cruns)
        validation=(_artifact_scene(final,accepted['run_id'],'validation',accepted['max_error_ns'],registration='validation-registration.json',prediction='predictions.json'),)
        out.append(_freeze_entry(entry_id=f"m04-c04-{accepted['profile']}-{accepted['stage']}-B{accepted['payload_bytes']}",component_id='M04',mechanism='gm_service',operation='write',profiles=(accepted['profile'],),consumer_stages=(accepted['stage'],),
          parameter_paths=tuple(b['parameter_path'] for b in formula['boundaries']),formula_id=formula['model_revision'],formula=formula,code_artifacts=code,
          domain=ActionDomain(shape_keys=(accepted['profile'],),input_dimensions=dict(family=family,controller_protocol='four WDMA controllers, eight PE streams each'),v_min=accepted['V'],v_max=accepted['V'],
            payload_min_bytes=accepted['payload_bytes'],payload_max_bytes=accepted['payload_bytes'],controller_counts=(accepted['controllers'],),transaction_counts=(accepted['logical_transactions'],),address_distributions=(accepted['address_distribution'],),
            operation_variants=('ordinary-write',),route_domains=('production-write-mapping',),applicability_text='exact accepted C04 ordinary-write profile/stage/payload; controller boundary timing'),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,
          error_summary=ErrorSummary(metric='gm_controller_boundary_error',max_error=accepted['max_error_ns'],threshold=accepted['threshold_ns'],unit='ns',validation_scene_count=1),status='validated_limited',publication_class='current_architecture_component_limited',
          source_commit=final.document['source_commits']['start_commit'],source_refs=('c04-gm-production-domain/tsim/freeze/pre-validation-freeze.json','c04-gm-production-domain/tsim/freeze/final-evidence.json')))
    return out


def _c03_entries(root):
    base=Path(root)/'c03-chain-v-domain/tsim';final=_FreezeIndex(root,'c03-chain-v-domain/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c03-chain-v-domain/tsim/freeze/pre-validation-freeze.json')
    domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas-c03.json');cal=_json(base/'calibration-registration.json')['runs'];fits={r['run_id']:r['max_boundary_error_ns'] for r in _json(base/'calibration-fit.json')}
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/chain_v_domain.py').resolve())),final.artifact('prepare_freeze.py'),final.artifact('finalize.py'))
    out=[]
    for accepted in domain['accepted']:
        variant,scenario=accepted['variant'],accepted['scenario_class'];profile=accepted['profile'];formula=formulas[variant];ChainPayloadFormula.model_validate_json(json.dumps(formula))
        cruns=[r for r in cal if r['variant']==variant and r['scenario_class']==scenario]
        calibration=tuple(_artifact_scene(final,r['run_id'],'calibration',fits[r['run_id']]) for r in cruns)
        validation=(_artifact_scene(final,accepted['run_id'],'validation',accepted['max_error_ns'],registration='validation-registration.json',prediction='predictions.json'),)
        params=tuple(b['parameter_path'] for s in formula['scenarios'] if s['scenario_class']==scenario for b in s['boundaries'])
        out.append(_freeze_entry(entry_id=f"m03-c03-{profile}-{accepted['stage']}-{scenario}-B{accepted['payload_bytes']}",component_id='M03',mechanism='pe_chain',operation=accepted['axis'],profiles=(profile,),consumer_stages=(accepted['stage'],),
          parameter_paths=params,formula_id=formula['model_revision'],formula=formula,code_artifacts=code,
          domain=ActionDomain(shape_keys=(profile,),input_dimensions=dict(variant=variant,groups=accepted['active_groups'],rounds=2 if scenario=='sustained_backpressure' else 1,launched_pes=32),
            v_min=accepted['V'],v_max=accepted['V'],payload_min_bytes=accepted['payload_bytes'],payload_max_bytes=accepted['payload_bytes'],scenario_classes=(scenario,),operation_variants=(variant,),
            route_domains=('native-8x4-'+accepted['axis'],),applicability_text='exact C03 profile/stage/payload/scenario; native boundary timing only; no independently calibrated issue/merge/FIFO counterfactual'),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,
          error_summary=ErrorSummary(metric='every_native_chain_boundary_error',max_error=accepted['max_error_ns'],threshold=accepted['threshold_ns'],unit='ns',validation_scene_count=1),
          status='validated_limited',publication_class='current_architecture_component_limited',source_commit=final.document['source_commits']['start_commit'],
          source_refs=('c03-chain-v-domain/tsim/freeze/pre-validation-freeze.json','c03-chain-v-domain/tsim/freeze/final-evidence.json')))
    return out


def _c05_entries(root):
    base=Path(root)/'c05-matrix-v-domain/tsim';final=_FreezeIndex(root,'c05-matrix-v-domain/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c05-matrix-v-domain/tsim/freeze/pre-validation-freeze.json')
    domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas-c05.json');cal=_json(base/'calibration-registration.json')['runs'];val=_json(base/'validation-registration.json')['runs'];summary={row['run_id']:row for row in _json(base/'validation-summary.json')['scenes']};fits={row['run_id']:row['max_engine_error_ns'] for row in _json(base/'calibration-fit.json')}
    code=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/matrix_v_domain.py').resolve())),final.artifact('prepare_freeze.py'),final.artifact('finalize.py'))
    stage={'gate_up':('E0',),'down':('E4',)};out=[];commit=final.document['source_commits']['start_commit']
    for accepted in domain['accepted']:
        profile,operation,target=accepted['profile'],accepted['operation'],accepted['V'];key=profile+'-'+operation;formula=formulas[key];MatrixVDomainFormula.model_validate_json(json.dumps(formula))
        cruns=[row for row in cal if row['profile']==profile and row['operation']==operation];vrun=next(row for row in val if row['run_id']==accepted['run_id'])
        calibration=tuple(_artifact_scene(final,row['run_id'],'calibration',fits[row['run_id']]) for row in cruns)
        validation=(_artifact_scene(final,vrun['run_id'],'validation',summary[vrun['run_id']]['max_error_ns'],registration='validation-registration.json',prediction='predictions.json'),)
        work=MatrixWork(work_id='c05-bundle',profile=profile,operation=operation,V=1,pe_id=0,expert_sequence=(0,))
        out.append(_freeze_entry(entry_id=f'm05-c05-{profile}-{operation}-V{target}',component_id='M05',mechanism='matrix',operation=operation,profiles=(profile,),consumer_stages=stage[operation],
          parameter_paths=tuple(f'matrix.{profile}.{operation}.{engine}.v_anchor' for engine in ('MATRIX_FTR_READ','MATRIX_WGT_READ','MATRIX','MATRIX_WRITE')),
          formula_id=formula['model_revision'],formula=formula,code_artifacts=code,domain=ActionDomain(shape_keys=(profile,),input_dimensions=dict(shape=work.shape(),precision=work.precision()),
          v_min=min(row['V'] for row in cruns),v_max=target,excluded_v=(),payload_min_bytes=None,payload_max_bytes=None,fanout_values=(),route_domains=('resident-weight-current-architecture',),applicability_text=formula['applicability']),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,
          error_summary=ErrorSummary(metric='engine_boundary_error',max_error=summary[vrun['run_id']]['max_error_ns'],threshold=summary[vrun['run_id']]['threshold_ns'],unit='ns',validation_scene_count=1),
          status='validated_limited',publication_class='current_architecture_component_limited',source_commit=commit,
          source_refs=('c05-matrix-v-domain/tsim/freeze/pre-validation-freeze.json','c05-matrix-v-domain/tsim/freeze/final-evidence.json',f'c05-matrix-v-domain/tsim/runs/{vrun["run_id"]}/validation.json')))
    return out


def _m06_entries(root):
    base=Path(root)/'phase2d/tsim';index=_FreezeIndex(root,'phase2d/tsim/freeze-r5/pre-validation-freeze.json');result=_json(Path(root)/'phase2d/result.json');formulas=_json(base/'formulas-r4.json');val=_json(base/'validation-registration-r5.json')['runs'];fits=_fits(index);errors,maximum=_max_validation_error(root,'phase2d/tsim',val);commit=_commit(root,'phase2d/result.json')
    code=(index.artifact('model.py'),index.artifact('../../public-patch/t81-overlay/simulator_detailed/moe_model/vector.py'),index.artifact('../../public-patch/t81-overlay/simulator_detailed/moe_model/vector_timing.py'))
    stages={'rmsnorm':('R1',),'quant':('R3',),'softmax_topk':('R8','R9'),'table':('R12',),'silu_score_quant':('E2',)};out=[]
    for key,formula in formulas.items():
        cls=LongRepeatedVectorFormula if formula['kind']=='long_repeated' else RecurringTpcVectorFormula;cls.model_validate_json(json.dumps(formula));profile= formula['profile'];operation=formula['operation'];vruns=[row for row in val if row['profile']==profile and row['operation']==operation]
        calibration=tuple(_artifact_scene(index,scene,'calibration',fits[scene],registration='formulas-r4.json') for scene in formula['source_refs']);validation=tuple(_artifact_scene(index,row['run_id'],'validation',errors[row['run_id']],registration='validation-registration-r5.json',prediction='predictions-r5.json') for row in vruns)
        values=[_source_v(scene) for scene in formula['source_refs']]+[row['V'] for row in vruns];values=[x for x in values if x is not None]
        c06=Path(root)/'c06-vector-v-domain/tsim/accepted-domain.json'
        c06_values=() if not c06.is_file() else tuple(sorted({row['V'] for row in (*_json(c06)['accepted'],*_json(c06)['rejected']) if row['profile']==profile and row['operation']==operation and row['V']<=max(values)}))
        excluded=tuple(sorted(set((2,6,10,22) if operation in ('table','silu_score_quant') else ())|set(c06_values)))
        pe=28 if operation=='silu_score_quant' else 0;work=VectorWork(work_id='bundle',profile=profile,operation=operation,work_items=1,pe_id=pe)
        out.append(_freeze_entry(entry_id='m06-'+key,component_id='M06',mechanism='vector',operation=operation,profiles=(profile,),consumer_stages=stages[operation],parameter_paths=tuple(row['parameter_path'] for row in formula['slices']),formula_id=formula['model_revision'],formula=formula,code_artifacts=code,
          domain=ActionDomain(shape_keys=(profile,),input_dimensions=work.shape(),v_min=min(values),v_max=max(values),excluded_v=excluded,payload_min_bytes=None,payload_max_bytes=None,fanout_values=(),route_domains=('source-shaped-current-architecture',),applicability_text=formula['applicability']),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=calibration,validation_scenes=validation,
          error_summary=ErrorSummary(metric='engine_boundary_error',max_error=maximum,threshold=_threshold(index),unit='ns',validation_scene_count=len(validation)),status='validated_limited',publication_class='current_architecture_component_limited',source_commit=commit,
          source_refs=('phase2d/result.json','phase2d/tsim/formulas-r4.json','phase2d/tsim/validation-registration-r5.json')))
    return out


def _c06_entries(root):
    base=Path(root)/'c06-vector-v-domain/tsim';final=_FreezeIndex(root,'c06-vector-v-domain/tsim/freeze-r5/final-evidence.json');pre=_FreezeIndex(root,'c06-vector-v-domain/tsim/freeze-r5/pre-validation-freeze.json')
    old=_FreezeIndex(root,'phase2d/tsim/freeze-r5/pre-validation-freeze.json');domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas-c06.json')
    cal=_json(base/'calibration-registration-r3.json')['runs'];cal_status={row['run_id']:row['returncode'] for row in _json(base/'calibration-campaign-result-r3-final.json')['results']}
    val={row['run_id']:row for row in _json(base/'validation-registration-r5.json')['runs']};summary={row['run_id']:row for row in _json(base/'validation-summary.json')['scenes']}
    fits={row['run_id']:row['max_engine_error_ns'] for row in _json(base/'calibration-fit-r4.json')};old_fits=_fits(old)
    code=(old.artifact('../../public-patch/t81-overlay/simulator_detailed/moe_model/vector_timing.py'),
      pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/vector_v_domain.py').resolve())),final.artifact('finalize.py'))
    stages={'rmsnorm':('R1',),'quant':('R3',),'softmax_topk':('R8','R9'),'table':('R12',),'silu_score_quant':('E2',)};out=[]
    for accepted in domain['accepted']:
        profile,operation,target=accepted['profile'],accepted['operation'],accepted['V'];formula=formulas[profile+'-'+operation]
        cls=LongRepeatedVectorFormula if formula.get('kind')=='long_repeated' else VectorVDomainFormula;cls.model_validate_json(json.dumps(formula))
        new_cal=[row for row in cal if row['profile']==profile and row['operation']==operation and cal_status[row['run_id']]==0]
        calibration=[]
        for scene in formula['source_refs']:
            if scene.startswith('c06-r3-'):
                row=next(row for row in new_cal if row['run_id']==scene);calibration.append(_artifact_scene(final,row['run_id'],'calibration',fits[row['run_id']],registration='calibration-registration-r3.json'))
            elif scene in old_fits:
                calibration.append(_artifact_scene(old,scene,'calibration',old_fits[scene],registration='formulas-r4.json'))
        if not calibration:raise ValueError('C06 accepted entry has no selected calibration: '+accepted['run_id'])
        vrun=val[accepted['run_id']];validation=(_artifact_scene(final,vrun['run_id'],'validation',summary[vrun['run_id']]['max_error_ns'],registration='validation-registration-r5.json',prediction='predictions-r5.json'),)
        pe=28 if operation=='silu_score_quant' else 0;work=VectorWork(work_id='c06-bundle',profile=profile,operation=operation,work_items=target,pe_id=pe)
        parameters=(tuple(row['parameter_path'] for row in formula['slices']) if formula.get('kind')=='long_repeated' else tuple(f'vector.{profile}.{operation}.{engine}.v_anchor' for engine in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE')))
        out.append(_freeze_entry(entry_id=f'm06-c06-{profile}-{operation}-V{target}',component_id='M06',mechanism='vector',operation=operation,profiles=(profile,),consumer_stages=stages[operation],
          parameter_paths=parameters,formula_id=formula['model_revision'],formula=formula,code_artifacts=code,
          domain=ActionDomain(shape_keys=(profile,),input_dimensions=work.shape(),v_min=target,v_max=target,excluded_v=(),payload_min_bytes=None,payload_max_bytes=None,fanout_values=(),
            route_domains=('source-shaped-current-architecture',),applicability_text=formula['applicability']+f'; exact C06 heldout V={target} only'),
          duration_unit='model_cycle',observation_unit='ns',timebase_status='tsim_component_ns',calibration_scenes=tuple(calibration),validation_scenes=validation,
          error_summary=ErrorSummary(metric='engine_boundary_error',max_error=summary[vrun['run_id']]['max_error_ns'],threshold=summary[vrun['run_id']]['threshold_ns'],unit='ns',validation_scene_count=1),
          status='validated_limited',publication_class='current_architecture_component_limited',source_commit=final.document['source_commits']['start_commit'],
          source_refs=('c06-vector-v-domain/tsim/freeze-r5/pre-validation-freeze.json','c06-vector-v-domain/tsim/freeze-r5/final-evidence.json',f'c06-vector-v-domain/tsim/runs/{vrun["run_id"]}/validation.json')))
    return out


def _bulk_vector_entries(root):
    base=Path(root)/'c06-bulk-source-r2/tsim'
    if not (base/'freeze/final-evidence.json').exists():return []
    pre=_FreezeIndex(root,'c06-bulk-source-r2/tsim/freeze/pre-validation-freeze.json');final=_FreezeIndex(root,'c06-bulk-source-r2/tsim/freeze/final-evidence.json')
    for name in ('accepted-domain.json','formulas.json','calibration-fit.json','validation-summary.json'):final.artifact(name)
    domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas.json');fits={r['run_id']:r['max_error_ns'] for r in _json(base/'calibration-fit.json')};out=[]
    for row in domain['accepted']:
        f=VectorBulkFormula.model_validate_json(json.dumps(formulas[row['profile']+'-'+row['operation']]))
        def scene(identity,role,error):
            paths=[role+'-registration.json','runs/'+identity+'/result.json','runs/'+identity+'/business-result.json']
            result=_json(base/paths[1]);business=_json(base/paths[2])
            if result['returncode']!=0 or not result['cleanup_complete'] or not all(business.get(k) for k in ('payload_pass','guard_pass','source_pass','inactive_pass')):raise ValueError('bulk selected evidence failed function/cleanup')
            if role=='validation':
                verdict=_json(base/'runs'/identity/'validation.json')
                if not verdict['pass_gate'] or verdict['formula_sha256']!=f.formula_sha256 or verdict['max_error_ns']>verdict['threshold_ns']:raise ValueError('bulk selected validation not accepted')
                paths+=['predictions.json','runs/'+identity+'/validation.json']
            return CalibrationSceneEvidence(scene_id=identity,role=role,artifacts=tuple(final.artifact(p) for p in paths),max_error=error,error_unit='reported_ns',passed=True)
        work=bulk_work_from_row(row)
        out.append(_freeze_entry(entry_id=f"m06-source-bulk-{row['profile']}-{row['operation']}-V{row['V']}",component_id='M06',mechanism='vector',operation=row['operation'],profiles=(row['profile'],),consumer_stages=('R12',) if row['operation']=='table' else ('E2',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/vector_bulk.py').resolve())),final.artifact('extract.py')),
          domain=ActionDomain(shape_keys=(row['profile'],),input_dimensions=dict(vector_bulk_work=work.model_dump(mode='json'),shape=work.shape(),sram_bytes_per_pe=work.sram_bytes_per_pe()),v_min=row['V'],v_max=row['V'],route_domains=('source-bulk-private-current-buffer-v2',),applicability_text='one source bulk invocation; exact profile/V/work/round/remainder and source-strides private current buffers; READ backpressure and TPC boundaries only; no physical timebase, isolated SRAM bandwidth or queue capacity identification'),
          duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=tuple(scene(i,'calibration',fits[i]) for i in f.calibration_scene_ids),validation_scenes=(scene(row['run_id'],'validation',row['max_error_ns']),),error_summary=ErrorSummary(metric='engine_start_ALL_FINISH_end_and_TPC_round',max_error=row['max_error_ns'],threshold=row['threshold_ns'],unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=final.document['source_commits']['start_commit'],source_refs=('c06-bulk-source-r2/tsim/freeze/pre-validation-freeze.json','c06-bulk-source-r2/tsim/freeze/final-evidence.json')))
    return out


def _write_sum_primitive_entries(root):
    base=Path(root)/'c04-writesum-primitive-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c04-writesum-primitive-r1/tsim/freeze/final-evidence.json')
    pre=_FreezeIndex(root,'c04-writesum-primitive-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('write-sum-domain.json','formula.json','calibration-fits.json','validation-summary.json','accepted-domain.json'):final.artifact(name)
    domain=WriteSumPrimitiveDomain.model_validate_json((base/'write-sum-domain.json').read_text())
    formula=CombinedWriteSumFormula.model_validate_json((base/'formula.json').read_text())
    if domain.formula!=formula:raise ValueError('WRITE_SUM accepted formula/domain mismatch')
    fits={row['scene_id']:row for row in _json(base/'calibration-fits.json')}
    validation=_json(base/'validation-summary.json');accepted=[row for row in validation['rows'] if row['accepted']]
    if len(accepted)!=1 or accepted[0]['run_id'] not in domain.timebase.validation_scene_ids:raise ValueError('WRITE_SUM exact accepted validation identity mismatch')
    calibration=tuple(_artifact_scene(final,scene,'calibration',fits[scene]['error_counter'],unit='tsim_counter_default_unit') for scene in domain.formula.calibration_scene_ids)
    selected=accepted[0]
    heldout=(_artifact_scene(final,selected['run_id'],'validation',selected['counter_error'],unit='tsim_counter_default_unit',registration='validation-registration.json',prediction='predictions.json'),)
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/write_sum_primitive.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py'))
    work=domain.work
    entry=_freeze_entry(entry_id='m04-source-fused-R10-write_sum-C32-B128',component_id='M04',mechanism='gm_source_service',operation='write_sum',profiles=('transformer-fused',),consumer_stages=('R10',),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=code,
      domain=ActionDomain(input_dimensions=dict(primitive=work.primitive,controller=work.controller,channel=work.channel,buffer_binding=work.buffer_binding,contributor_order=list(work.contributor_order),counter_node=work.counter_node,boundary=work.boundary,zero_initialization_scope=work.zero_initialization_scope,atomic_internal_service=None),payload_min_bytes=work.payload_bytes,payload_max_bytes=work.payload_bytes,controller_counts=(1,),transaction_counts=(work.contributors,),address_distributions=(work.address_distribution,),operation_variants=('write_sum',),producer_counts=(work.contributors,),route_domains=('source-fused-r10-gm-wdma0-channel1-same-address',),applicability_text='exact source fused R10 combined GM_WDMA0 channel1 same-address WRITE_SUM, 32 ordered contributors x 128B; zero initialization outside boundary; atomic internal service not separable'),
      duration_unit='tsim_counter_default_unit',observation_unit='simulation_seconds',timebase_status='local_tsim_counter',calibration_scenes=calibration,validation_scenes=heldout,error_summary=ErrorSummary(metric='combined_WRITE_SUM_counter_prediction_error',max_error=selected['counter_error'],threshold=pre.document['thresholds']['counter_prediction_error'],unit='tsim_counter_default_unit',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=final.document['source_commits']['start_commit'],source_refs=('c04-writesum-primitive-r1/tsim/freeze/pre-validation-freeze.json','c04-writesum-primitive-r1/tsim/freeze/final-evidence.json','c04-writesum-primitive-r1/tsim/write-sum-domain.json'))
    return [entry]



def _r10_boundary_entries(root):
    base=Path(root)/'c04-r10-stage-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c04-r10-stage-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c04-r10-stage-r1/tsim/freeze/pre-validation-freeze.json')
    for path in ('accepted-domain.json','validation-summary.json','formulas.json','r10-stage-domain.json'):final.artifact(path)
    fits={r['scene_id']:r for r in _json(base/'calibration-fits.json')};summary=_json(base/'validation-summary.json');accepted={r['run_id']:r for r in summary['rows'] if r['accepted']};entries=[]
    for raw in _json(base/'accepted-domain.json')['domains']:
        d=R10BoundaryDomain.model_validate_json(json.dumps(raw));f=d.formula;w=d.work;scene_id=d.timebase.validation_scene_ids[0]
        if scene_id not in accepted:raise ValueError('R10 domain selects failed validation')
        r=accepted[scene_id]
        for i in f.calibration_scene_ids:final.artifact('runs/'+i+'/result.json')
        final.artifact('runs/'+scene_id+'/result.json');final.artifact('runs/'+scene_id+'/validation.json')
        cal=tuple(_artifact_scene(final,i,'calibration',fits[i]['error_counter'],unit='tsim_counter_default_unit') for i in f.calibration_scene_ids)
        val=(_artifact_scene(final,scene_id,'validation',r['counter_error'],unit='tsim_counter_default_unit',registration='validation-registration.json',prediction='predictions.json'),)
        entries.append(_freeze_entry(entry_id='m04-source-fused-R10-'+w.operation+'-B128',component_id='M04',mechanism='gm_r10_boundary',operation=w.operation,profiles=(w.profile,),consumer_stages=('R10',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/r10_stage.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
          domain=ActionDomain(input_dimensions=dict(r10_boundary_work=w.model_dump(mode='json'),model_library_sha256=d.timebase.model_library_sha256,systemc_library_sha256=d.timebase.systemc_library_sha256,observer_sha256=d.observer_sha256,counter_callsite=d.counter_callsite,timing_ownership=w.timing_ownership,terminal_counter_nodes=list(d.terminal_counter_nodes)),payload_min_bytes=128,payload_max_bytes=128,fanout_values=(32,) if w.operation=='reduce_done_broadcast_consumer' else (1,),route_domains=('source-fused-r10-'+w.operation+'-channel1',),applicability_text='exact fused B128 combined boundary, supplied source buffer and provider/counter identity; isolated current TSim simulation-counter only; independent link/sync/bandwidth costs not identified'),duration_unit='tsim_counter_default_unit',observation_unit='simulation_seconds',timebase_status='local_tsim_counter',calibration_scenes=cal,validation_scenes=val,error_summary=ErrorSummary(metric='combined_R10_counter_prediction_error',max_error=r['counter_error'],threshold=pre.document['thresholds']['counter_prediction_error'],unit='tsim_counter_default_unit',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c04-r10-stage-r1/tsim/freeze/pre-validation-freeze.json','c04-r10-stage-r1/tsim/freeze/final-evidence.json')))
    return entries



def _r11_entries(root):
    base=Path(root)/'c04-r11-stage-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c04-r11-stage-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c04-r11-stage-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','r11-domain.json','formula.json','validation-summary.json','calibration-fits.json'):final.artifact(name)
    d=R11Domain.model_validate_json((base/'r11-domain.json').read_text());f=d.formula;w=d.work;fits={r['scene_id']:r for r in _json(base/'calibration-fits.json')};v=next(r for r in _json(base/'validation-summary.json')['rows'] if r['run_id']==d.timebase.validation_scene_ids[0])
    if not v['accepted']:raise ValueError('R11 selected validation is not accepted')
    for scene in f.calibration_scene_ids:final.artifact('runs/'+scene+'/result.json')
    final.artifact('runs/'+v['run_id']+'/result.json');final.artifact('runs/'+v['run_id']+'/validation.json')
    cal=tuple(_artifact_scene(final,i,'calibration',fits[i]['error_counter'],unit='tsim_counter_default_unit') for i in f.calibration_scene_ids)
    val=(_artifact_scene(final,v['run_id'],'validation',v['counter_error'],unit='tsim_counter_default_unit',registration='validation-registration.json',prediction='predictions.json'),)
    return [_freeze_entry(entry_id='m04-source-fused-R11-combined-V64-32PE2',component_id='M04',mechanism='gm_r11_combined',operation=w.operation,profiles=(w.profile,),consumer_stages=('R11',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/r11_stage.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(input_dimensions=dict(r11_work=w.model_dump(mode='json'),model_library_sha256=d.timebase.model_library_sha256,systemc_library_sha256=d.timebase.systemc_library_sha256,observer_sha256=d.observer_sha256,counter_callsite=d.counter_callsite,timing_ownership=w.timing_ownership),v_min=64,v_max=64,payload_min_bytes=w.upload_bytes,payload_max_bytes=w.upload_bytes,fanout_values=(32,),route_domains=('source-fused-r11-stride256-four-wdma-private-r2',),applicability_text='exact supplied counts [2]*32 and prefix, V64, local expert/score/token fields, four source WDMA and current private buffer/provider identity; combined read/upload/broadcast/sync/first consumer simulation only; route trace not acquired'),duration_unit='tsim_counter_default_unit',observation_unit='simulation_seconds',timebase_status='local_tsim_counter',calibration_scenes=cal,validation_scenes=val,error_summary=ErrorSummary(metric='R11_combined_counter_prediction_error',max_error=v['counter_error'],threshold=2.0,unit='tsim_counter_default_unit',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c04-r11-stage-r1/tsim/freeze/pre-validation-freeze.json','c04-r11-stage-r1/tsim/freeze/final-evidence.json'))]


def _fused_preseed_entries(root):
    base=Path(root)/'c04-fused-preseed-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    if _json(base/'freeze/final-evidence.json')['status']!='PASS_EXACT_TRANSFORMER_FUSED_RESIDUAL_PRESEED':return []
    final=_FreezeIndex(root,'c04-fused-preseed-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c04-fused-preseed-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('fused-preseed-domain.json','formula.json','calibration-summary.json','validation-summary.json','accepted-domain.json'):final.artifact(name)
    domain=FusedPreseedDomain.model_validate_json((base/'fused-preseed-domain.json').read_text());formula=domain.formula;work=domain.work
    calibration_summary=_json(base/'calibration-summary.json');validation_summary=_json(base/'validation-summary.json')
    if validation_summary['accepted_domains']!=1 or validation_summary['validation_pass']!=1 or validation_summary['validation_fail']!=0:raise ValueError('preseed selected validation is not uniquely accepted')
    def scene(scene_id,role,error):
        names=('calibration-registration-r2.json','calibration-summary.json') if role=='calibration' else ('validation-registration-r2.json','predictions.json','validation-summary.json')
        return CalibrationSceneEvidence(scene_id=scene_id,role=role,artifacts=tuple(final.artifact(name) for name in names),max_error=error,error_unit='reported_ns',passed=True)
    calibration=tuple(scene(row['run_id'],'calibration',row['max_controller_error']) for row in calibration_summary['rows'])
    selected=validation_summary['rows'][0];validation=(scene(selected['run_id'],'validation',selected['max_controller_error']),)
    return [_freeze_entry(entry_id='m04-source-fused-R4-residual-preseed-B8192-C4',component_id='M04',mechanism='gm_fused_preseed',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/fused_preseed.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(input_dimensions=dict(fused_preseed_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsites=list(domain.counter_callsites),timing_ownership=work.timing_ownership,terminal_counter_nodes=list(domain.terminal_counter_nodes)),payload_min_bytes=8192,payload_max_bytes=8192,controller_counts=(4,),transaction_counts=(32,),address_distributions=('disjoint-output-rows',),flow_counts=(32,),source_counts=(32,),directions=('pe_to_gm',),operation_variants=('ordinary-write-source-exact',),route_domains=('source-fused-residual-preseed-four-wdma-channel1',),applicability_text='exact transformer-fused residual preseed: 32 PE x 8192B to four GM_WDMA channel1 controllers, eight disjoint rows per controller, source-exact sync/send/receive ownership; reported simulation ns only and no physical-clock claim'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='four_controller_and_max_terminal_reported_ns_error',max_error=max(selected['max_controller_error'],selected['terminal_error']),threshold=pre.document['thresholds']['reported_ns_prediction_error'],unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c04-fused-preseed-r1/tsim/freeze/pre-validation-freeze.json','c04-fused-preseed-r1/tsim/freeze/final-evidence.json'))]


def _m07_entries(root):
    index=_FreezeIndex(root,'phase2e/tsim/freeze/evidence-freeze.json');registration=_json(Path(root)/'phase2e/tsim/evidence-registration.json');result=_json(Path(root)/'phase2e/result.json');commit=_commit(root,'phase2e/result.json')
    def scene(row,role):
        artifacts=[]
        for key in ('result','business','verdict'):
            if key in row:artifacts.append(index.artifact('../../'+row[key]))
        return CalibrationSceneEvidence(scene_id=row['id'],role=role,artifacts=tuple(artifacts),max_error=None,error_unit=None,passed=True)
    calibration=tuple(scene(row,'calibration') for row in registration['calibration']);validation=tuple(scene(row,'validation') for row in registration['validation'])
    code=tuple(index.artifact(row['path'] if Path(row['path']).is_absolute() else '../../'+row['path']) for row in registration['source_evidence'])
    stages={'inner_release_acquire':('R1','R3','R8'),'outer_release_acquire':('R0','R2','R4','R5','R7','E1','E5'),'fence_calc':('R1','R8'),'fence_io':('R11',),'counted_after_all':('R10','E7')};out=[]
    for operation in result['protocols']:
        formula=dict(cost_model=registration['cost_model'],operation=operation)
        out.append(_freeze_entry(entry_id='m07-'+operation,component_id='M07',mechanism='sync_protocol',operation=operation,profiles=('standalone','transformer-fused'),consumer_stages=stages[operation],parameter_paths=('sync.protocol.'+operation,),formula_id='M07-protocol-only',formula=formula,code_artifacts=code,
          domain=ActionDomain(input_dimensions=dict(protocol=operation),route_domains=('protocol-ordering',),applicability_text='ordering and counted completion only; no independent sync duration'),duration_unit='protocol_event',observation_unit='dependency_order',timebase_status='protocol_only',
          calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='protocol_ordering',max_error=None,threshold=None,unit='protocol',validation_scene_count=len(validation)),status='validated_limited',publication_class='protocol_only',source_commit=commit,source_refs=('phase2e/result.json','phase2e/tsim/evidence-registration.json')))
    return out


def _c07_entries(root):
    base=Path(root)/'c07-sync-domain/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-sync-domain/tsim/freeze/final-evidence.json');domain=_json(base/'accepted-domain.json');formulas=_json(base/'formulas.json');cal=_json(base/'calibration-registration.json')['runs'];out=[]
    code=(final.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/sync_resource.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py'))
    fits={r['run_id']:r for r in _json(base/'calibration-fits.json')}
    for row in domain['accepted']:
        formula=formulas[row['profile']+'-'+row['operation']+'-'+row['load']];SyncLocalFormula.model_validate_json(json.dumps(formula))
        calibration=tuple(_artifact_scene(final,rid,'calibration',fits[rid]['max_error'],unit=formula['unit']) for rid in formula['source_refs'])
        validation=(_artifact_scene(final,row['run_id'],'validation',row['max_error'],unit=row['unit'],registration='validation-registration.json',prediction='predictions.json'),)
        frontier='producer_ready' if row['operation_id']==0 else 'engines_empty'
        out.append(_freeze_entry(entry_id='m07-c07-'+row['profile']+'-'+row['operation']+'-'+row['load'],component_id='M07',mechanism='sync_protocol',operation=row['operation'],profiles=(row['profile'],),consumer_stages=(row['source_stage'],),parameter_paths=('sync.resources.local.latency','sync.resources.local.capacity','sync.resources.local.queue_slots'),formula_id='M07-local-queue-r1',formula=formula,code_artifacts=code,
          domain=ActionDomain(input_dimensions=dict(scope='local EU empty/ready boundary; no physical sync binding',participants=32),v_min=row['V'],v_max=row['V'],scenario_classes=(row['load'],),producer_counts=(row['producers'],),waiter_counts=(row['waiters'],),round_counts=(row['rounds'],),frontiers=(frontier,),route_domains=('local-pe-eu-unbound',),applicability_text='exact independently passed ready-acquire or empty-fence action; capacity and queue are CPU hypotheses, not TSim physical calibration'),
          duration_unit='reported_ns' if row['unit']=='reported_ns' else 'reported_cycle',observation_unit=row['unit'],timebase_status='local_reported_ns' if row['unit']=='reported_ns' else 'local_reported_cycle',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='local_sync_boundary_error',max_error=row['max_error'],threshold=row['threshold'],unit=row['unit'],validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=final.document['source_commits']['start_commit'],source_refs=('c07-sync-domain/tsim/freeze/pre-validation-freeze.json','c07-sync-domain/tsim/freeze/final-evidence.json')))
    return out

def _e7_join_entries(root):
    base=Path(root)/'c07-e7-join-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e7-join-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e7-join-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e7-join-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E7JoinDomain.model_validate_json((base/'e7-join-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0]
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E7 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration-r4.json'),final.artifact('calibration-summary.json')),max_error=0,error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration-r4.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E7-commid16-C32-W14',component_id='M07',mechanism='sync_e7_commid_join',operation=work.operation,profiles=(work.profile,),consumer_stages=('E7',),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e7_join.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(input_dimensions=dict(e7_join_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,release_edges=448),producer_counts=(32,),waiter_counts=(14,),commid_slots=(16,),scenario_classes=('source-exact-counted-join',),operation_variants=('PE_UPLOAD1-to-ADL_FUNC',),route_domains=('source-fused-e7-fullmesh-ports4-17-commid16',),applicability_text='exact transformer-fused E7 count32 comm-id16, PE_UPLOAD1 releases to fullmesh ADALINK ports4..17, explicit 448 target issue events through 14 internal acquire completions; simulation only; E6 scatter and E8 collective excluded'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='explicit_release_edge_consume_waiter_terminal_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e7-join-r1/tsim/freeze/pre-validation-freeze.json','c07-e7-join-r1/tsim/freeze/final-evidence.json'))]


def _attn_output_norm_entries(root):
    base=Path(root)/'c06-attn-output-norm-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c06-attn-output-norm-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c06-attn-output-norm-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','attn-output-norm-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=AttnOutputNormDomain.model_validate_json((base/'attn-output-norm-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0]
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('attn-output-norm selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(item['max_segment_error_ns'] for item in _json(base/'calibration-summary.json')['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m06-source-fused-attn-output-norm-H2048-32PE',component_id='M06',mechanism='vector_attn_output_norm',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/attn_output_norm.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(attn_output_norm_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,hidden_elements_per_pe=2048,activation_bytes_per_pe=8192,weight_bytes_per_pe=4096,output_bytes_per_pe=8192,pe_count=32,in_place=True),payload_min_bytes=8192,payload_max_bytes=8192,scenario_classes=('source-exact-inplace-rmsnorm-terminal',),operation_variants=('rms_norm-2048-float-inplace',),route_domains=('source-fused-attn-output-norm-inplace-vector',),applicability_text='exact transformer-fused source callsite: 32 PE each execute one in-place rms_norm<2048,float> using private fp32 activation and bf16 Weight SRAM, with named VECTOR_READ/CAL/WRITE overlap and terminal=max(engine completion,sync API return); simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='named_vector_segment_and_terminal_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c06-attn-output-norm-r1/tsim/freeze/pre-validation-freeze.json','c06-attn-output-norm-r1/tsim/freeze/final-evidence.json'))]


def _e4_e5_handoff_entries(root):
    base=Path(root)/'c07-e4-e5-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e4-e5-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e4-e5-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e4-e5-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E4E5HandoffDomain.model_validate_json((base/'e4-e5-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E4/E5 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_tail_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E4-E5-local-handoff-V64',component_id='M07',mechanism='sync_e4_e5_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e4_e5_handoff.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(e4_e5_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,predecessor_entry_id=work.predecessor_entry_id,pe_count=32,upload_waiters=24,download_waiters=8,output_bytes_per_pe=65536,channel=1),v_min=64,v_max=64,payload_min_bytes=65536,payload_max_bytes=65536,scenario_classes=('source-exact-post-matrix-local-handoff',),operation_variants=('24-upload1-8-download1',),producer_counts=(32,),waiter_counts=(32,),route_domains=('source-fused-e4-e5-local-config-island',),applicability_text='exact transformer-fused E4 down V64: accepted Matrix predecessor owns through last MATRIX_WRITE; this domain owns only the per-PE local Matrix->UPLOAD1/DOWNLOAD1 post-write tail and 32-way max before E5 row reduce; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_matrix_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e4-e5-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-e4-e5-handoff-r1/tsim/freeze/final-evidence.json'))]

def _e0_e1_handoff_entries(root):
    base=Path(root)/'c07-e0-e1-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e0-e1-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e0-e1-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e0-e1-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E0E1HandoffDomain.model_validate_json((base/'e0-e1-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E0/E1 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_tail_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E0-E1-local-handoff-V64',component_id='M07',mechanism='sync_e0_e1_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e0_e1_handoff.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(e0_e1_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,predecessor_entry_id=work.predecessor_entry_id,pe_count=32,upload_waiters=28,download_waiters=4,output_bytes_per_pe=131072,channel=1),v_min=64,v_max=64,payload_min_bytes=131072,payload_max_bytes=131072,scenario_classes=('source-exact-post-matrix-local-handoff',),operation_variants=('28-upload1-4-download1',),producer_counts=(32,),waiter_counts=(32,),route_domains=('source-fused-e0-e1-local-config-island',),applicability_text='exact transformer-fused E0 gate-up V64: accepted Matrix predecessor owns through last MATRIX_WRITE; this domain owns only per-PE local Matrix->UPLOAD1/DOWNLOAD1 post-write tail and 32-way max before E1 column reduce; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_matrix_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e0-e1-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-e0-e1-handoff-r1/tsim/freeze/final-evidence.json'))]

def _e2_e3_handoff_entries(root):
    base=Path(root)/'c07-e2-e3-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e2-e3-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e2-e3-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e2-e3-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E2E3HandoffDomain.model_validate_json((base/'e2-e3-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E2/E3 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E2-E3-local-handoff-V64',component_id='M07',mechanism='sync_e2_e3_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e2_e3_handoff.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(e2_e3_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,predecessor_entry_id=work.predecessor_entry_id,pe_count=32,vector_participants=4,payload_bytes_per_root=24576,total_payload_bytes=98304,root_dual_sync_count=4,nonroot_ready_sync_count=28,channel=1,e3_physical_route_resolved=False),v_min=64,v_max=64,payload_min_bytes=24576,payload_max_bytes=24576,scenario_classes=('source-exact-post-vector-local-handoff',),operation_variants=('4-vector-roots-dual-sync-28-nonroot-ready',),producer_counts=(4,),waiter_counts=(32,),route_domains=('source-fused-e2-e3-local-config-island',),applicability_text='exact transformer-fused E2 V64: accepted Vector predecessor owns named READ/CAL/WRITE; this domain owns only post-global-last-Vector root dual-sync/nonroot readiness and 32-PE after-all before E3; E3 physical multicast/Data NoC excluded; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_vector_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e2-e3-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-e2-e3-handoff-r1/tsim/freeze/final-evidence.json'))]


def _e1_e2_handoff_entries(root):
    base=Path(root)/'c07-e1-e2-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e1-e2-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e1-e2-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e1-e2-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E1E2HandoffDomain.model_validate_json((base/'e1-e2-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E1/E2 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E1-E2-local-handoff-V64',component_id='M07',mechanism='sync_e1_e2_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e1_e2_handoff.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(e1_e2_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,predecessor_entry_id=work.predecessor_entry_id,pe_count=32,roots=[28,29,30,31],payload_bytes_per_root=131072,contributors_per_root=8,column_count=4,channel=1,input_buffer=work.input_buffer,local_resource=work.local_resource),v_min=64,v_max=64,payload_min_bytes=131072,payload_max_bytes=131072,scenario_classes=('source-exact-post-column-reduce-local-handoff',),operation_variants=('4-download1-to-vector-roots',),producer_counts=(4,),waiter_counts=(4,),route_domains=('source-fused-e1-e2-local-config-island',),applicability_text='exact transformer-fused E1 V64 four column roots: accepted chain owns through root materialization; this domain owns only per-root DOWNLOAD1-to-Vector readiness tail before E2 Vector service; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_vector_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e1-e2-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-e1-e2-handoff-r1/tsim/freeze/final-evidence.json'))]


def _e3_e4_handoff_entries(root):
    base=Path(root)/'c07-e3-e4-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-e3-e4-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-e3-e4-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e3-e4-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=E3E4HandoffDomain.model_validate_json((base/'e3-e4-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('E3/E4 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-E3-E4-local-handoff-V64',component_id='M07',mechanism='sync_e3_e4_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/e3_e4_handoff.py').resolve())),final.artifact('extract_r2.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(e3_e4_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,root_readiness_owner=work.root_readiness_owner,pe_count=32,owned_pes=list(work.owned_pes),roots=list(work.roots),payload_bytes_per_endpoint=24576,channel=1,input_buffer=work.buffer,local_resource=work.local_resource,terminal_kind=work.terminal_kind),v_min=64,v_max=64,payload_min_bytes=24576,payload_max_bytes=24576,scenario_classes=('source-exact-post-multicast-local-matrix-readiness',),operation_variants=('28-download1-to-matrix-nonroots-4-already-ready-roots',),producer_counts=(4,),waiter_counts=(28,),route_domains=('source-fused-e3-e4-local-config-island',),applicability_text='exact fused V64 E3 source native DOWNLOAD1 endpoint visible completion to 28 nonroot local Matrix acquire; multicast and E4 Matrix excluded; four root readiness costs owned by E2/E3; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_vector_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-e3-e4-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-e3-e4-handoff-r1/tsim/freeze/final-evidence.json'))]


def _r7_r8_handoff_entries(root):
    base=Path(root)/'c07-r7-r8-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-r7-r8-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-r7-r8-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','e1-e2-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    publication=_FreezeIndex(root,'c07-r7-r8-handoff-r1/tsim/freeze/publication-identity-freeze.json');publication.artifact('r7-r8-handoff-domain.json')
    domain=R7R8HandoffDomain.model_validate_json((base/'r7-r8-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('R7/R8 selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-R7-R8-local-handoff-V64',component_id='M07',mechanism='sync_r7_r8_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/r7_r8_handoff.py').resolve())),final.artifact('extract_r2.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(r7_r8_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,predecessor_entry_id=work.predecessor_entry_id,pe_count=32,owned_pes=list(work.owned_pes),roots=list(work.roots),payload_bytes_per_endpoint=12288,channel=1,input_buffer=work.buffer,local_resource=work.local_resource,terminal_kind=work.terminal_kind,source_callsite=work.source_callsite),v_min=64,v_max=64,payload_min_bytes=12288,payload_max_bytes=12288,scenario_classes=('source-exact-post-router-broadcast-local-vector-readiness',),operation_variants=('28-download1-and-4-upload1-to-vector',),producer_counts=(4,),waiter_counts=(32,),route_domains=('source-fused-r7-r8-local-config-island',),applicability_text='exact fused V64 fixed router8x384 FP32: per-PE named R7 broadcast DOWNLOAD1/nonroot or last UPLOAD1/root completion to local Vector acquire; R7 chain and R8 softmax service excluded; simulation only'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_router_broadcast_local_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-r7-r8-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-r7-r8-handoff-r1/tsim/freeze/final-evidence.json','c07-r7-r8-handoff-r1/tsim/freeze/publication-identity-freeze.json'))]


def _r9_count_handoff_entries(root):
    base=Path(root)/'c07-r9-count-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-r9-count-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-r9-count-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','r9-count-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=R9CountHandoffDomain.model_validate_json((base/'r9-count-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('R9/count selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-R9-count-local-handoff-rank0-V64',component_id='M07',mechanism='sync_r9_count_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/r9_count_handoff.py').resolve())),final.artifact('extract_r2.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(r9_count_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,ep_rank=work.ep_rank,pe_count=32,owned_pes=list(work.owned_pes),payload_bytes_per_endpoint=256,input_buffer=work.buffer,local_resource=work.local_resource,terminal_kind=work.terminal_kind,source_callsite=work.source_callsite),v_min=64,v_max=64,payload_min_bytes=256,payload_max_bytes=256,scenario_classes=('source-exact-post-filter-local-count-readiness',),operation_variants=('32-vector-self-acquire',),producer_counts=(32,),waiter_counts=(32,),route_domains=('source-fused-r9-count-local-config-island',),applicability_text='exact fused rank0 V64: last named EP filter WRITE completion to third local Vector self acquire before count fill; no filter/fill/softmax/topk service cost; rank1..3 unverified'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_filter_local_count_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-r9-count-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-r9-count-handoff-r1/tsim/freeze/final-evidence.json','c07-r9-count-handoff-r1/tsim/freeze/provenance-metadata-correction.json'))]


def _count_token_handoff_entries(root):
    base=Path(root)/'c07-count-token-handoff-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c07-count-token-handoff-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c07-count-token-handoff-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','count-token-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=CountTokenHandoffDomain.model_validate_json((base/'count-token-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('count/token selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_pe_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m07-source-fused-count-token-local-handoff-rank0-V64',component_id='M07',mechanism='sync_count_token_local_handoff',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/count_token_handoff.py').resolve())),final.artifact('extract.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(count_token_handoff_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_ownership,ep_rank=work.ep_rank,pe_count=32,owned_pes=list(work.owned_pes),payload_bytes_per_endpoint=128,input_buffer=work.buffer,local_resource=work.local_resource,terminal_kind=work.terminal_kind,source_callsite=work.source_callsite),v_min=64,v_max=64,payload_min_bytes=128,payload_max_bytes=128,scenario_classes=('source-exact-post-count-local-token-readiness',),operation_variants=('32-vector-self-acquire-calc-fence',),producer_counts=(32,),waiter_counts=(32,),route_domains=('source-fused-count-token-local-config-island',),applicability_text='exact fused rank0 V64: count-fill last WRITE to local fourth self acquire and scalar calc fence return before token-ID fill; count/token-fill and scalar/GM service excluded'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='post_count_local_token_handoff_tail_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c07-count-token-handoff-r1/tsim/freeze/pre-validation-freeze.json','c07-count-token-handoff-r1/tsim/freeze/final-evidence.json'))]


def _token_id_fill_handoff_entries(root):
    base=Path(root)/'c06-token-id-fill-r1/tsim'
    if not (base/'freeze/final-evidence.json').is_file():return []
    final=_FreezeIndex(root,'c06-token-id-fill-r1/tsim/freeze/final-evidence.json');pre=_FreezeIndex(root,'c06-token-id-fill-r1/tsim/freeze/pre-validation-freeze.json')
    for name in ('accepted-domain.json','token-id-fill-handoff-domain.json','formula.json','calibration-summary.json','validation-summary.json'):final.artifact(name)
    domain=TokenIdFillDomain.model_validate_json((base/'token-id-fill-handoff-domain.json').read_text());formula=domain.formula;work=domain.work;summary=_json(base/'validation-summary.json');row=summary['rows'][0];cal_summary=_json(base/'calibration-summary.json')
    if summary['accepted_domains']!=1 or not row['pass_gate']:raise ValueError('token fill selected validation not accepted')
    calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact('calibration-summary.json')),max_error=max(max(item['max_event_error_ns'],item['terminal_error_ns']) for item in cal_summary['rows']),error_unit='reported_ns',passed=True) for scene_id in formula.calibration_scene_ids)
    validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact('predictions.json'),final.artifact('validation-summary.json')),max_error=row['max_error_ns'],error_unit='reported_ns',passed=True),)
    return [_freeze_entry(entry_id='m06-source-fused-token-id-fill-rank0-V64',component_id='M06',mechanism='vector_token_id_fill',operation=work.operation,profiles=(work.profile,),consumer_stages=(work.consumer_stage,),parameter_paths=formula.parameter_paths(),formula_id=formula.revision,formula=formula.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/token_id_fill.py').resolve())),final.artifact('extract_r2.py'),final.artifact('prepare_freeze.py'),final.artifact('validate.py')),
      domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(token_id_fill_work=work.model_dump(mode='json'),model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite,timing_ownership=work.timing_owner,ep_rank=work.ep_rank,pe_count=32,owned_pes=list(work.participants),payload_bytes_per_endpoint=128,input_buffer=work.buffer,local_resource='PE_CONFIG+VECTOR_CAL+VECTOR_WRITE',terminal_kind='named_VECTOR_WRITE_ALL_FINISH',source_callsite=work.source_callsite),v_min=64,v_max=64,payload_min_bytes=128,payload_max_bytes=128,scenario_classes=('source-exact-token-id-fill-body',),operation_variants=('32PE-int32-CAL-WRITE-noREAD',),producer_counts=(32,),waiter_counts=(32,),route_domains=('source-fused-token-id-fill-local-vector',),applicability_text='exact fused rank0 V64 token-ID fill body: source API/configuration startup,CAL and WRITE visible completion; prior49ns handoff,scalar,observer fence anddispatch excluded'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='token_fill_engine_boundary_error',max_error=row['max_error_ns'],threshold=8.0,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=('c06-token-id-fill-r1/tsim/freeze/pre-validation-freeze.json','c06-token-id-fill-r1/tsim/freeze/final-evidence.json'))]


def _router_rank_entries(root):
    base=Path(root)/'c07-router-ranks123-r1/tsim';out=[]
    from .router_rank_domains import COMPONENTS,STAGES
    for rank in (1,2,3):
        if not (base/f'freeze/final-rank{rank}.json').is_file():continue
        final=_FreezeIndex(root,f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json');pre=_FreezeIndex(root,f'c07-router-ranks123-r1/tsim/freeze/pre-validation-rank{rank}.json')
        summary=_json(base/f'validation-summary-rank{rank}.json');cal=_json(base/f'calibration-summary-rank{rank}.json')
        if summary['validation_pass']!=1 or summary['accepted_domains']!=3 or summary['retries']!=0:raise ValueError('rank validation domains not accepted')
        for segment in ('filter_count','count_token','token_fill'):
            final.artifact(f'domain-rank{rank}-{segment}.json')
            domain=RouterRankDomain.model_validate_json((base/f'domain-rank{rank}-{segment}.json').read_text());w=domain.work;f=domain.formula
            vr=next(r for r in summary['rows'] if r['segment']==segment);cr=next(r for r in cal['segments'] if r['segment']==segment)
            calibration=tuple(CalibrationSceneEvidence(scene_id=scene_id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact(f'calibration-summary-rank{rank}.json')),max_error=max(max(r['max_event_error_ns'],r['terminal_error_ns']) for r in cr['rows']),error_unit='reported_ns',passed=True) for scene_id in f.calibration_scene_ids)
            validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact(f'predictions-rank{rank}.json'),final.artifact(f'validation-summary-rank{rank}.json')),max_error=vr['max_error_ns'],error_unit='reported_ns',passed=True),)
            out.append(_freeze_entry(entry_id=f'router-rank{rank}-{segment}-V64',component_id=COMPONENTS[segment],mechanism='router_rank_local',operation=w.operation,profiles=(w.profile,),consumer_stages=(STAGES[segment],),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/router_rank_domains.py').resolve())),final.artifact('extract.py'),final.artifact('fit_calibration.py'),final.artifact('validate.py')),
                domain=ActionDomain(shape_keys=('transformer-fused',),input_dimensions=dict(router_rank_work=w.model_dump(mode='json'),ep_rank=rank,segment=segment,model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite),v_min=64,v_max=64,payload_min_bytes=w.payload_bytes,payload_max_bytes=w.payload_bytes,scenario_classes=('source-exact-rank-router-local',),operation_variants=(segment,),producer_counts=(32,),waiter_counts=(32,),route_domains=(f'source-rank{rank}-{segment}-local',),applicability_text=f'exact independently calibrated rank{rank} V64 {segment};rank0 entries/formulas unchanged;no body/gap/dispatch double cost'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='source_rank_segment_event_error',max_error=vr['max_error_ns'],threshold=8.,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=(f'c07-router-ranks123-r1/tsim/freeze/pre-validation-rank{rank}.json',f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json')))
    return out


def _count_fill_entries(root):
    base=Path(root)/'c06-count-fill-ranks-r1/tsim';entries=[]
    for rank in range(4):
        if not (base/f'freeze/final-rank{rank}.json').is_file():continue
        final=_FreezeIndex(root,f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json');pre=_FreezeIndex(root,f'c06-count-fill-ranks-r1/tsim/freeze/pre-validation-rank{rank}.json')
        final.artifact(f'domain-rank{rank}.json');domain=CountFillDomain.model_validate_json((base/f'domain-rank{rank}.json').read_text());f=domain.formula;w=domain.work
        val=_json(base/f'validation-summary-rank{rank}.json');cal=_json(base/f'calibration-summary-rank{rank}.json');v=val['rows'][0]
        if val['validation_pass']!=1 or val['accepted_domains']!=1 or val['retries']!=0 or not v['pass_gate']:raise ValueError('count body validation not accepted')
        calibration=tuple(CalibrationSceneEvidence(scene_id=id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact(f'calibration-summary-rank{rank}.json')),max_error=max(max(r['max_event_error_ns'],r['terminal_error_ns']) for r in cal['rows']),error_unit='reported_ns',passed=True) for id in f.calibration_scene_ids)
        validation=(CalibrationSceneEvidence(scene_id=domain.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact(f'predictions-rank{rank}.json'),final.artifact(f'validation-summary-rank{rank}.json')),max_error=v['max_error_ns'],error_unit='reported_ns',passed=True),)
        entries.append(_freeze_entry(entry_id=f'm06-count-fill-rank{rank}-V64',component_id='M06',mechanism='vector_count_fill',operation=w.operation,profiles=(w.profile,),consumer_stages=('R9.valid_count_init',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/count_fill.py').resolve())),final.artifact('extract.py'),final.artifact('fit_calibration.py'),final.artifact('validate.py')),
            domain=ActionDomain(shape_keys=(w.profile,),input_dimensions=dict(count_fill_work=w.model_dump(mode='json'),rank=rank,model_library_sha256=domain.timebase.model_library_sha256,systemc_library_sha256=domain.timebase.systemc_library_sha256,observer_sha256=domain.observer_sha256,counter_callsite=domain.counter_callsite),v_min=64,v_max=64,payload_min_bytes=128,payload_max_bytes=128,scenario_classes=('source-count-zero-fill-ready-to-visible',),operation_variants=('oneCAL-oneWRITE-zeroREAD',),producer_counts=(32,),waiter_counts=(32,),route_domains=(f'source-count-fill-rank{rank}-local',),applicability_text=f'exact rank{rank}32int32count-zero body between accepted pre-readiness and post WRITE terminal;everytoken2valid;pre/posthandoff andprequeued config excluded'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='count_fill_engine_boundaries',max_error=v['max_error_ns'],threshold=8.,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=(f'c06-count-fill-ranks-r1/tsim/freeze/pre-validation-rank{rank}.json',f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json')))
    return entries


def _scalar_tail_entries(root):
    base=Path(root)/'c09-scalar-local-r1/tsim';entries=[]
    for rank in range(4):
        if not (base/f'freeze/final-rank{rank}.json').is_file():continue
        final=_FreezeIndex(root,f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json');pre=_FreezeIndex(root,f'c09-scalar-local-r1/tsim/freeze/pre-validation-rank{rank}.json')
        final.artifact(f'domain-rank{rank}.json');d=ScalarTailDomain.model_validate_json((base/f'domain-rank{rank}.json').read_text());f=d.formula;w=d.work;cal=_json(base/f'calibration-summary-rank{rank}.json');val=_json(base/f'validation-summary-rank{rank}.json');v=val['rows'][0]
        if val['validation_pass']!=1 or val['accepted_domains']!=1 or val['retries'] or not v['pass_gate']:raise ValueError('scalar tail validation not accepted')
        calibration=tuple(CalibrationSceneEvidence(scene_id=id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact(f'calibration-summary-rank{rank}.json')),max_error=max(r['max_event_error_ns'] for r in cal['rows']),error_unit='reported_ns',passed=True) for id in f.calibration_scene_ids)
        validation=(CalibrationSceneEvidence(scene_id=d.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact(f'predictions-rank{rank}.json'),final.artifact(f'validation-summary-rank{rank}.json')),max_error=v['max_error_ns'],error_unit='reported_ns',passed=True),)
        entries.append(_freeze_entry(entry_id=f'm07-scalar-tail-rank{rank}-V64',component_id='M07',mechanism='scalar_tail_service',operation=w.operation,profiles=(w.profile,),consumer_stages=('R9.scalar_count_tail',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/scalar_tail.py').resolve())),final.artifact('extract.py'),final.artifact('fit_calibration.py'),final.artifact('validate.py')),
            domain=ActionDomain(shape_keys=(w.profile,),input_dimensions=dict(scalar_tail_work=w.model_dump(mode='json'),rank=rank,model_library_sha256=d.timebase.model_library_sha256,systemc_library_sha256=d.timebase.systemc_library_sha256,observer_sha256=d.observer_sha256,counter_callsite=d.counter_callsite),v_min=64,v_max=64,payload_min_bytes=128,payload_max_bytes=128,scenario_classes=('source-scalar-dlcm-tail-store',),operation_variants=('32-minus-pe-int32-stores',),producer_counts=(32,),waiter_counts=(32,),route_domains=(f'source-scalar-tail-rank{rank}-local',),applicability_text=f'exact rank{rank}V64/2validper-token sourceScalarDLCMcumulativeTail32-pe stores;combinedcounterprimitiveonly,notphysicalstoretruth/scalarprep'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='scalar_tail_counter_duration_error',max_error=v['max_error_ns'],threshold=8.,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=(f'c09-scalar-local-r1/tsim/freeze/pre-validation-rank{rank}.json',f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json')))
    return entries


def _count_scan_entries(root):
    base=Path(root)/'c09-count-scan-r1/tsim';entries=[]
    for rank in range(4):
        if not (base/f'freeze/final-rank{rank}.json').is_file():continue
        final=_FreezeIndex(root,f'c09-count-scan-r1/tsim/freeze/final-rank{rank}.json');pre=_FreezeIndex(root,f'c09-count-scan-r1/tsim/freeze/pre-validation-rank{rank}.json')
        pub=_FreezeIndex(root,f'c09-count-scan-r1/tsim/freeze/publication-rank{rank}.json');pub.artifact(f'publication-domain-rank{rank}.json');d=CountScanDomain.model_validate_json((base/f'publication-domain-rank{rank}.json').read_text());f=d.formula;w=d.work;cal=_json(base/f'calibration-summary-rank{rank}.json');val=_json(base/f'validation-summary-rank{rank}.json');v=val['rows'][0]
        if val['validation_pass']!=1 or val['accepted_domains']!=1 or val['retries'] or not v['pass_gate']:raise ValueError('count scan validation not accepted')
        calibration=tuple(CalibrationSceneEvidence(scene_id=id,role='calibration',artifacts=(final.artifact('calibration-registration.json'),final.artifact(f'calibration-summary-rank{rank}.json')),max_error=max(r['max_event_error_ns'] for r in cal['rows']),error_unit='reported_ns',passed=True) for id in f.calibration_scene_ids)
        validation=(CalibrationSceneEvidence(scene_id=d.timebase.validation_scene_ids[0],role='validation',artifacts=(final.artifact('validation-registration.json'),final.artifact(f'predictions-rank{rank}.json'),final.artifact(f'validation-summary-rank{rank}.json')),max_error=v['max_error_ns'],error_unit='reported_ns',passed=True),)
        entries.append(_freeze_entry(entry_id=f'm07-count-scan-rank{rank}-V64',component_id='M07',mechanism='scalar_count_scan',operation=w.operation,profiles=(w.profile,),consumer_stages=('R9.scalar_count_scan',),parameter_paths=f.parameter_paths(),formula_id=f.revision,formula=f.model_dump(mode='json'),code_artifacts=(pre.artifact(str((Path(root)/'public-patch/t81-overlay/simulator_detailed/moe_model/count_scan.py').resolve())),final.artifact('extract.py'),final.artifact('fit_calibration.py'),final.artifact('validate.py')),
            domain=ActionDomain(shape_keys=(w.profile,),input_dimensions=dict(count_scan_work=w.model_dump(mode='json'),rank=rank,model_library_sha256=d.timebase.model_library_sha256,systemc_library_sha256=d.timebase.systemc_library_sha256,observer_sha256=d.observer_sha256,counter_callsite=d.counter_callsite,compiled_path_sha256=d.compiled_path_sha256),v_min=64,v_max=64,payload_min_bytes=12,payload_max_bytes=12,scenario_classes=('source-scalar-sentinel-count-scan',),operation_variants=('compiled-valid2-path-13instructions-3loads',),producer_counts=(32,),waiter_counts=(32,),route_domains=(f'source-count-scan-rank{rank}-local',),applicability_text=f'exact rank{rank}V64/2validper-token sourceScalarDLCM3loads/sentinel96/13instructionpath;combinedcounterprimitiveonly,notphysicalloadtruth/scalarprep'),duration_unit='reported_ns',observation_unit='reported_ns',timebase_status='local_reported_ns',calibration_scenes=calibration,validation_scenes=validation,error_summary=ErrorSummary(metric='count_scan_counter_duration_error',max_error=v['max_error_ns'],threshold=8.,unit='reported_ns',validation_scene_count=1),status='validated_limited',publication_class='not_publishable',source_commit=pre.document['source_commits']['start_commit'],source_refs=(f'c09-count-scan-r1/tsim/freeze/pre-validation-rank{rank}.json',f'c09-count-scan-r1/tsim/freeze/final-rank{rank}.json')))
    return entries


def _m08_entries(root):
    manifest=_json(Path(root)/'m08-adalink/freeze-manifest.json');rows={row['path']:row['sha256'] for row in manifest['files']};selected=('public-patch/t81-overlay/simulator_detailed/moe_model/adalink.py','public-patch/t81-overlay/simulator_detailed/moe_model/adalink_binding.py','public-patch/t81-overlay/simulator_detailed/moe_model/adalink_execute.py')
    code=[]
    for path in selected:
        actual=hashlib.sha256((Path(root)/path).read_bytes()).hexdigest()
        if rows.get(path)!=actual:raise ValueError('M08 frozen code drift: '+path)
        code.append(EvidenceArtifact(path=path,sha256=actual))
    result=_json(Path(root)/'m08-adalink/result.json');commit=_commit(root,'m08-adalink/result.json');out=[]
    for profile in ('standalone','transformer-fused'):
        formula=dict(implementation=result['implementation'],tsim=result['tsim'],profile=profile)
        out.append(_freeze_entry(entry_id='m08-'+profile+'-allreduce',component_id='M08',mechanism='adalink_collective',operation='allreduce',profiles=(profile,),consumer_stages=('E8',),
          parameter_paths=('adalink.links.<supplied-link>.bytes_per_cycle','adalink.links.<supplied-link>.channel_latency_cycles'),formula_id='M08-schedule-driven-cpu-only',formula=formula,code_artifacts=tuple(code),
          domain=ActionDomain(shape_keys=(profile,),input_dimensions=dict(rank_count=PROFILES[profile].ep_size,payload_bytes_per_rank=PROFILES[profile].output_buffer_bytes_per_rank),rank_counts=(PROFILES[profile].ep_size,),
            payload_min_bytes=PROFILES[profile].output_buffer_bytes_per_rank,payload_max_bytes=PROFILES[profile].output_buffer_bytes_per_rank,route_domains=('supplied-binding-required',),applicability_text='CPU mechanism only; real CclConfig/rank/link/port calibration absent'),
          duration_unit='unavailable',observation_unit='reported_units',timebase_status='reported_units_only',calibration_scenes=(),validation_scenes=(),error_summary=ErrorSummary(metric='unavailable',max_error=None,threshold=None,unit='unavailable',validation_scene_count=0),
          status='cpu_only_binding_gate',publication_class='not_publishable',source_commit=commit,source_refs=('m08-adalink/result.json','m08-adalink/freeze-manifest.json')))
    return out


def _exclusions(root):
    m05=_json(Path(root)/'phase2c/result.json')['prior_failed_revision'];m06=_json(Path(root)/'phase2d/result.json')['prior_validation_revisions'];rows=[]
    def add(component,revision,reason,refs):rows.append(ExcludedEvidence(component_id=component,revision=revision,reason=reason,source_refs=tuple(refs),evidence_sha256=stable_sha256(dict(component=component,revision=revision,reason=reason,refs=refs))))
    add('M05',m05['revision'],f"failed {m05['failed_run_id']}; {m05['passed']}/{m05['validation_scenes']} passed; never rerun or used",['phase2c/result.json','phase2c/tsim/validation-registration.json'])
    add('M05','M05-diagnostic-pre-weight-address-fix','diagnostic runs are not formal calibration or validation',['phase2c/result.json','phase2c/tsim/diagnostic-registration.json'])
    for row in m06:add('M06',row['revision'],f"prior heldout not accepted: {json.dumps(row,sort_keys=True)}",['phase2d/result.json',f"phase2d/tsim/validation-registration-{row['revision']}.json" if row['revision']!='r1' else 'phase2d/tsim/validation-registration.json'])
    add('M06','M06-diagnostic-scenes','diagnostic PASS/FAIL history is excluded from accepted formulas',['phase2d/result.json','phase2d/tsim/diagnostic-registration.json'])
    c05=Path(root)/'c05-matrix-v-domain/tsim'
    if (c05/'accepted-domain.json').is_file():
        audit=_json(c05/'r3-extension-audit.json');add('M05','M05-r3-direct-C05-extension',f"calibration gate failed with max error {audit['max_error_ns']} ns",['c05-matrix-v-domain/tsim/r3-extension-audit.json'])
        for row in _json(c05/'accepted-domain.json')['rejected']:
            add('M05',f"M05-engine-v-anchor-steady-r4-{row['profile']}-{row['operation']}-V{row['V']}-failed",f"frozen heldout failed at {row['max_error_ns']} ns; never rerun or used for fitting",
              ['c05-matrix-v-domain/tsim/freeze/pre-validation-freeze.json',f"c05-matrix-v-domain/tsim/runs/{row['run_id']}/validation.json"])
    c06=Path(root)/'c06-vector-v-domain/tsim'
    if (c06/'accepted-domain.json').is_file():
        final=_json(c06/'freeze-r5/final-evidence.json')
        for row in final['excluded_revisions']:
            add('M06',row['revision'],row['reason'],['c06-vector-v-domain/tsim/freeze-r5/final-evidence.json'])
        for run_id in _json(c06/'freeze-r5/pre-validation-freeze.json')['failed_calibration_ids']:
            add('M06','C06-calibration-failed-'+run_id,'functional calibration failure; excluded from every fit and accepted entry',
              ['c06-vector-v-domain/tsim/calibration-campaign-result-r3-final.json',f'c06-vector-v-domain/tsim/runs/{run_id}/result.json'])
        for row in _json(c06/'accepted-domain.json')['rejected']:
            add('M06',f"C06-{row['profile']}-{row['operation']}-V{row['V']}-failed",f"frozen heldout failed at {row['max_error_ns']} ns; never rerun or used for fitting",
              ['c06-vector-v-domain/tsim/freeze-r5/pre-validation-freeze.json',f"c06-vector-v-domain/tsim/runs/{row['run_id']}/validation.json"])
    c03=Path(root)/'c03-chain-v-domain/tsim/accepted-domain.json'
    if c03.is_file():
        for row in _json(c03)['rejected']:
            add('M03','C03-failed-'+row['run_id'],'frozen validation failed; no fitting or retry',[f"c03-chain-v-domain/tsim/runs/{row['run_id']}/validation.json"])
    c04=Path(root)/'c04-gm-production-domain/tsim/accepted-domain.json'
    if c04.is_file():
        for row in _json(c04)['rejected']:
            add('M04','C04-failed-'+row['run_id'],f"frozen validation failed at {row['max_error_ns']} ns; never refit or retried",[f"c04-gm-production-domain/tsim/runs/{row['run_id']}/validation.json"])
    c0102=Path(root)/'c01-c02-datanoc-domain/tsim/accepted-domain.json'
    if c0102.is_file():
        for revision,reason in (('C0102-production-r1','guard oracle failure'),('C0102-production-r2','observer tag mismatch'),('C0102-validation-r3','metadata registration superseded before launch')):add('M01',revision,reason,['c01-c02-datanoc-domain/tsim/freeze/final-evidence.json'])
        for row in _json(c0102)['rejected']:
            add('M01' if row['primitive']=='point_to_point' else 'M02','C0102-excluded-'+row['run_id'],'timing or metadata quality gate failed; never refit/retried',[f"c01-c02-datanoc-domain/tsim/runs/{row['run_id']}/validation.json"])
    c07=Path(root)/'c07-sync-domain/tsim/accepted-domain.json'
    if c07.is_file():
        add('M07','C07-r1-harness','unused inner consumer counter; calibration diagnostic only',['c07-sync-domain/tsim/harness-exclusion.json'])
        for row in _json(c07)['rejected']:
            add('M07','C07-excluded-'+row['run_id'],'validation timing, missing model or quality exclusion; no retry/refit',['c07-sync-domain/tsim/runs/'+row['run_id']+'/validation.json'])
        for row in _json(Path(root)/'c07-sync-domain/tsim/calibration-exclusions.json'):
            add('M07','C07-calibration-excluded-'+row['run_id'],row['reason'],['c07-sync-domain/tsim/calibration-exclusions.json'])
    c08=Path(root)/'c08-adalink-domain/tsim/accepted-domain.json'
    if c08.is_file():
        for row in _json(c08)['rejected']:
            add('M08','C08-failed-'+row['run_id'],'complete registered WQE validation failed; no passing-member promotion',['c08-adalink-domain/tsim/runs/'+row['run_id']+'/validation.json'])
    current=Path(root)/'c08-accumulation-r2/tsim/identification-result.json'
    if current.is_file():
        index=_FreezeIndex(root,'c08-accumulation-r2/tsim/freeze/calibration-gate-freeze.json')
        index.artifact('identification-result.json')
        gate=_json(current)
        if gate['accepted_domains']!=0 or gate['fit_performed']:raise ValueError('C08 r2 requires independently identified component before timing entry')
        add('M08','C08-accumulation-r2-observability-blocked','matched new WRITE/WRITE_SUM calibration has no independent link/accumulation boundary, shared rank timebase or lane binding',['c08-accumulation-r2/tsim/freeze/calibration-gate-freeze.json','c08-accumulation-r2/tsim/identification-result.json'])
    router=Path(root)/'c05-router-domain-r2/tsim/accepted-domain.json'
    if router.is_file():
        for row in _json(router)['rejected']:
            add('M05','C05-router-r2-excluded-'+row['run_id'],'frozen source-task median boundary failed or repeated diagnostic not applicable; never refit from validation',['c05-router-domain-r2/tsim/runs/'+row['run_id']+'/validation.json'])
    bulk=Path(root)/'c06-bulk-source-r2/tsim/accepted-domain.json'
    if bulk.exists():
        for row in _json(bulk)['rejected']:
            add('M06','C06-bulk-excluded-'+row['run_id'],'frozen new bulk validation failed; never fit or rerun',['c06-bulk-source-r2/tsim/freeze/final-evidence.json'])
        for row in _json(bulk.parent/'staging-exclusion.json')['excluded']:
            add('M06','C06-bulk-staging-'+row['revision'],row['reason'],['c06-bulk-source-r2/tsim/staging-exclusion.json'])

    gm_source=Path(root)/'c04-source-service-r3/tsim/observability-result.json'
    if gm_source.exists():
        add('M04','M04-source-service-r4-observability-blocked','new source primitive qualification has no independently correlated memory/atomic service; no fitted formula or validation',['c04-source-service-r3/tsim/freeze/identification-freeze.json'])
        add('M04','M04-source-service-r3-staging','metadata host copy rejected before kernel launch; not calibration evidence',['c04-source-service-r3/tsim/staging-exclusion.json'])
    write_sum=Path(root)/'c04-writesum-primitive-r1/tsim/accepted-domain.json'
    if write_sum.exists():
        for row in _json(write_sum)['rejected']:
            add('M04','M04-WRITE_SUM-excluded-'+row['run_id'],'frozen combined WRITE_SUM validation failed; never used for fitting or rerun',['c04-writesum-primitive-r1/tsim/freeze/final-evidence.json','c04-writesum-primitive-r1/tsim/runs/'+row['run_id']+'/validation.json'])

    r10=Path(root)/'c04-r10-stage-r1/tsim/staging-exclusion.json'
    if r10.exists():
        for row in _json(r10)['excluded']:add('M04','R10-staging-'+row['revision'],row['reason'],['c04-r10-stage-r1/tsim/staging-exclusion.json'])
    r11=Path(root)/'c04-r11-stage-r1/tsim/staging-exclusion.json'
    if r11.exists():
        for row in _json(r11)['excluded']:add('M04','R11-staging-'+row['revision'],row['reason'],['c04-r11-stage-r1/tsim/staging-exclusion.json'])
    r10full=Path(root)/'c04-r10-full-r1/tsim/composition-evidence.json'
    if r10full.exists():
        evidence=_json(r10full)
        if evidence['status']!='FAILED_COMPONENT_COMPOSITION_GATE' or evidence['validation_executed'] or evidence['black_box_cost_added'] or evidence['component_formula_changed']:raise ValueError('R10 full composition failure contract drift')
        add('M04','R10-full-composition-r1-failed',f"full source calibration actual {evidence['actual_component_counter']} / total {evidence['actual_total_counter']} differs from frozen component prediction {evidence['component_prediction_counter']} / total {evidence['predicted_total_counter']}; validation not run and no refit",['c04-r10-full-r1/tsim/freeze/calibration-gate.json','c04-r10-full-r1/tsim/composition-evidence.json'])
    r10r2=Path(root)/'c04-r10-composition-r2/tsim/evidence.json'
    if r10r2.exists():
        evidence=_json(r10r2);observability=_json(r10r2.parent/'observability.json')
        if evidence['status']!='CALIBRATION_FAILED' or evidence['validation_executed'] or evidence['black_box_cost_added'] or evidence['component_formula_changed'] or observability['formula_refit']:raise ValueError('R10 R2 failed evidence contract drift')
        add('M04','R10-context-overlap-r2-calibration-failed',f"mechanistic max/overlap rule predicted {evidence['predicted_terminal']} but source-exact calibration observed {evidence['observed_terminal']} (error {evidence['error_counter']} > {evidence['threshold_counter']}); asynchronous handoff events remain insufficient; validation not run and no refit",['c04-r10-composition-r2/tsim/freeze/pre-calibration-freeze.json','c04-r10-composition-r2/tsim/freeze/calibration-evidence.json','c04-r10-composition-r2/tsim/observability.json'])
    r10r3=Path(root)/'c04-r10-handoff-r3/result.json'
    if r10r3.exists():
        result=_json(r10r3);audit=_json(r10r3.parent/'source-audit.json')
        calibration=result['r3_calibration'];validation=result['r3_validation'];calibration_active=any(calibration.values()) if isinstance(calibration,dict) else bool(calibration);validation_active=any(validation.values()) if isinstance(validation,dict) else bool(validation)
        if result['status']!='HOOK_REQUIRED' or calibration_active or validation_active or result['r3_entries'] or audit['r3_tsim_executions']:raise ValueError('R10 R3 hook gate drift')
        add('M04','R10-handoff-r3-hook-required','four asynchronous handoff classes cannot all be uniquely identity-bound: sync trigger/finish pairs have partial FIFO-backed ordinal evidence, but GM broadcast queue and component-counter ownership lack generation/operation/buffer/transaction/admission identity; R3 TSim forbidden',['c04-r10-handoff-r3/source-audit.json','c04-r10-handoff-r3/hook-contract.json','c04-r10-handoff-r3/result.json'])
    producer=Path(root)/'c04-r10-hook-producer-r1/capability-status.json'
    if producer.exists():
        capability=_json(producer);symbol=_json(producer.parent/'symbol-audit.json');passivity=_json(producer.parent/'passivity-manifest.json')
        if capability['status']!='UPSTREAM_TSIM_HOOK_UNAVAILABLE' or capability['capability_probe_executed'] or capability['r3_calibration'] or capability['r3_validation'] or symbol['tsim_executions'] or not passivity['status']=='PASS_STATIC_PASSIVITY':raise ValueError('R10 private hook producer gate drift')
        add('M04','R10-hook-producer-r1-upstream-unavailable','task-private passive producer and matcher are ready, but installed TSim exports no internal outer-sync/GM-queue/component-counter registration API and no exact interposable queue symbol; capability probe and R3 timing runs forbidden',['c04-r10-hook-producer-r1/symbol-audit.json','c04-r10-hook-producer-r1/capability-status.json','c04-r10-hook-producer-r1/hook-contract.json','c04-r10-hook-producer-r1/passivity-manifest.json'])
    preseed=Path(root)/'c04-fused-preseed-r1/tsim/freeze/final-evidence.json'
    if preseed.exists():
        evidence=_json(preseed)
        if evidence['status']=='FAILED_ONCE_ONLY_VALIDATION':
            add('M04','M04-fused-residual-preseed-r2-validation-failed',f"source-exact production B8192 validation max controller error {evidence['max_controller_error']} reported ns and terminal error {evidence['terminal_error']} exceeded frozen {evidence['threshold_reported_ns']} ns threshold; retries=0 and no refit",['c04-fused-preseed-r1/tsim/freeze/pre-validation-freeze.json','c04-fused-preseed-r1/tsim/freeze/final-evidence.json','c04-fused-preseed-r1/tsim/rejected-domain.json'])
    preseed_r3=Path(root)/'c04-fused-preseed-r3/tsim/freeze/final-evidence.json'
    if preseed_r3.exists():
        evidence=_json(preseed_r3)
        if evidence['status']=='FAILED_ONCE_ONLY_VALIDATION':
            add('M04','M04-fused-residual-preseed-packet-r3-validation-failed',f"source-backed discrete 128B packet/tail/controller-queue production B8192 validation max controller error {evidence['max_controller_error']} reported ns and terminal error {evidence['terminal_error']} exceeded frozen {evidence['threshold_reported_ns']} ns threshold; zero R2-validation numeric reads, retries=0 and no refit",['c04-fused-preseed-r3/tsim/freeze/pre-validation-freeze.json','c04-fused-preseed-r3/tsim/freeze/final-evidence.json','c04-fused-preseed-r3/tsim/zero-read-audit.json','c04-fused-preseed-r3/tsim/rejected-domain.json'])
    e7_staging=Path(root)/'c07-e7-join-r1/tsim/staging-exclusion-r4.json'
    if e7_staging.exists():
        for row in _json(e7_staging)['excluded']:
            add('M07','M07-fused-E7-join-'+row['revision']+'-excluded',row['error'],['c07-e7-join-r1/tsim/staging-exclusion-r4.json'])
    return tuple(rows)


def build_component_calibration_bundle(root,*,fixture_only=False):
    root=Path(root).resolve();entries=[]
    entries+=_simple_entries(root,'phase1','M01',('point_to_point',),{'point_to_point':('R0','R2','R4','R11')},{'point_to_point':('gm0-channel1-pe0',)})
    entries+=_simple_entries(root,'phase1','M02',('masked_multicast',),{'masked_multicast':('R5','R11','E3')},{'masked_multicast':('gm0-channel1-pe0..3',)})
    entries+=_c0102_entries(root)
    entries+=_simple_entries(root,'phase2a','M03',('column','row'),{'column':('R7','E1'),'row':('E5',)},{'column':('native-8x4-column',),'row':('native-8x4-row',)})
    entries+=_simple_entries(root,'phase2b','M04',('write','write_sum'),{'write':('R2','R4','R11'),'write_sum':('R10','E6')},{'write':('gm-wdma0-pe0..3-disjoint',),'write_sum':('gm-wdma0-pe0..3-same-address',)})
    entries+=_c03_entries(root);entries+=_c04_entries(root);entries+=_write_sum_primitive_entries(root);entries+=_r10_boundary_entries(root);entries+=_r11_entries(root);entries+=_fused_preseed_entries(root);entries+=_m05_entries(root);entries+=_c05_entries(root);entries+=_router_source_entries(root);entries+=_m06_entries(root);entries+=_c06_entries(root);entries+=_bulk_vector_entries(root);entries+=_attn_output_norm_entries(root);entries+=_m07_entries(root);entries+=_c07_entries(root);entries+=_e7_join_entries(root);entries+=_e4_e5_handoff_entries(root);entries+=_e0_e1_handoff_entries(root);entries+=_e2_e3_handoff_entries(root);entries+=_e1_e2_handoff_entries(root);entries+=_e3_e4_handoff_entries(root);entries+=_r7_r8_handoff_entries(root);entries+=_r9_count_handoff_entries(root);entries+=_count_token_handoff_entries(root);entries+=_token_id_fill_handoff_entries(root);entries+=_router_rank_entries(root);entries+=_count_fill_entries(root);entries+=_scalar_tail_entries(root);entries+=_count_scan_entries(root);entries+=_m08_entries(root)
    latest_commit=(_json(root/'c06-vector-v-domain/tsim/freeze-r5/final-evidence.json')['source_commits']['start_commit'] if (root/'c06-vector-v-domain/tsim/freeze-r5/final-evidence.json').is_file() else _json(root/'c05-matrix-v-domain/tsim/freeze/final-evidence.json')['source_commits']['start_commit'] if (root/'c05-matrix-v-domain/tsim/freeze/final-evidence.json').is_file() else _commit(root,'m08-adalink/result.json'))
    latest_commit=_json(root/'c01-c02-datanoc-domain/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    if (root/'c07-sync-domain/tsim/freeze/final-evidence.json').is_file():latest_commit=_json(root/'c07-sync-domain/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    preseed_accepted=(root/'c04-fused-preseed-r1/tsim/fused-preseed-domain.json').is_file()
    if preseed_accepted:latest_commit=_json(root/'c04-fused-preseed-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e7_accepted=(root/'c07-e7-join-r1/tsim/e7-join-domain.json').is_file()
    if e7_accepted:latest_commit=_json(root/'c07-e7-join-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    attn_norm_accepted=(root/'c06-attn-output-norm-r1/tsim/attn-output-norm-domain.json').is_file()
    if attn_norm_accepted:latest_commit=_json(root/'c06-attn-output-norm-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e4_e5_accepted=(root/'c07-e4-e5-handoff-r1/tsim/e4-e5-handoff-domain.json').is_file()
    if e4_e5_accepted:latest_commit=_json(root/'c07-e4-e5-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e0_e1_accepted=(root/'c07-e0-e1-handoff-r1/tsim/e0-e1-handoff-domain.json').is_file()
    if e0_e1_accepted:latest_commit=_json(root/'c07-e0-e1-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e2_e3_accepted=(root/'c07-e2-e3-handoff-r1/tsim/e2-e3-handoff-domain.json').is_file()
    if e2_e3_accepted:latest_commit=_json(root/'c07-e2-e3-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e1_e2_accepted=(root/'c07-e1-e2-handoff-r1/tsim/e1-e2-handoff-domain.json').is_file()
    if e1_e2_accepted:latest_commit=_json(root/'c07-e1-e2-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    e3_e4_accepted=(root/'c07-e3-e4-handoff-r1/tsim/e3-e4-handoff-domain.json').is_file()
    if e3_e4_accepted:latest_commit=_json(root/'c07-e3-e4-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    r7_r8_accepted=(root/'c07-r7-r8-handoff-r1/tsim/r7-r8-handoff-domain.json').is_file()
    if r7_r8_accepted:latest_commit=_json(root/'c07-r7-r8-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    r9_count_accepted=(root/'c07-r9-count-handoff-r1/tsim/r9-count-handoff-domain.json').is_file()
    if r9_count_accepted:latest_commit=_json(root/'c07-r9-count-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    count_token_accepted=(root/'c07-count-token-handoff-r1/tsim/count-token-handoff-domain.json').is_file()
    if count_token_accepted:latest_commit=_json(root/'c07-count-token-handoff-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    token_fill_accepted=(root/'c06-token-id-fill-r1/tsim/token-id-fill-handoff-domain.json').is_file()
    if token_fill_accepted:latest_commit=_json(root/'c06-token-id-fill-r1/tsim/freeze/final-evidence.json')['source_commits']['start_commit']
    rank_extension_accepted=any((root/f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json').is_file() for rank in (1,2,3))
    if rank_extension_accepted:latest_commit='dfe35682e5c383637e6bd6acae37a59bdaeaa31b'
    count_body_accepted=any((root/f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json').is_file() for rank in range(4))
    if count_body_accepted:latest_commit='5b11878dde9a0d30ca98ff56d34ad573290a755f'
    scalar_tail_accepted=any((root/f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json').is_file() for rank in range(4))
    if scalar_tail_accepted:latest_commit='766df60f8bf6fc9982a8132aa37c9c84b6959662'
    count_scan_accepted=any((root/f'c09-count-scan-r1/tsim/freeze/final-rank{rank}.json').is_file() for rank in range(4))
    if count_scan_accepted:latest_commit='aa54a61d05eb61530c9933f49dde263b737c001e'
    raw=dict(schema_version=1,bundle_id='T81-M01-M08-final-accepted-v24-count-scan-ranks' if count_scan_accepted else 'T81-M01-M08-final-accepted-v23-scalar-tail-ranks' if scalar_tail_accepted else 'T81-M01-M08-final-accepted-v22-count-fill-ranks' if count_body_accepted else 'T81-M01-M08-final-accepted-v21-router-ranks123' if rank_extension_accepted else 'T81-M01-M08-final-accepted-v20-fused-token-id-fill' if token_fill_accepted else 'T81-M01-M08-final-accepted-v19-fused-count-token-handoff' if count_token_accepted else 'T81-M01-M08-final-accepted-v18-fused-R9-count-handoff' if r9_count_accepted else 'T81-M01-M08-final-accepted-v17-fused-R7-R8-handoff' if r7_r8_accepted else 'T81-M01-M08-final-accepted-v16-fused-E3-E4-handoff' if e3_e4_accepted else 'T81-M01-M08-final-accepted-v15-fused-E1-E2-handoff' if e1_e2_accepted else 'T81-M01-M08-final-accepted-v14-fused-E2-E3-handoff' if e2_e3_accepted else 'T81-M01-M08-final-accepted-v13-fused-E0-E1-handoff' if e0_e1_accepted else 'T81-M01-M08-final-accepted-v12-fused-E4-E5-handoff' if e4_e5_accepted else 'T81-M01-M08-final-accepted-v11-fused-attn-output-norm' if attn_norm_accepted else 'T81-M01-M08-final-accepted-v10-fused-E7-join' if e7_accepted else 'T81-M01-M08-final-accepted-v9-fused-preseed' if preseed_accepted else 'T81-M01-M08-final-accepted-v8-c08-observability',fixture_only=fixture_only,entries=tuple(entries),excluded_evidence=_exclusions(root),
      source_repository_commit=latest_commit,
      source_refs=('phase1/result.json','phase2a/result.json','phase2b/result.json','phase2c/result.json','phase2d/result.json','phase2e/result.json','m08-adalink/result.json','c05-matrix-v-domain/result.json','c06-vector-v-domain/tsim/freeze-r5/final-evidence.json','c03-chain-v-domain/tsim/freeze/final-evidence.json','c04-gm-production-domain/tsim/freeze/final-evidence.json','c01-c02-datanoc-domain/tsim/freeze/final-evidence.json','c07-sync-domain/tsim/freeze/final-evidence.json'))
    timebase_path=root/'c09-timebase-domain/tsim/timebase-bundle.json'
    if timebase_path.is_file():
        index=_FreezeIndex(root,'c09-timebase-domain/tsim/freeze/final-evidence.json');index.artifact('timebase-bundle.json')
        raw['timebases']=TimebaseBundle.model_validate_json(timebase_path.read_text())
        raw['source_refs']+=('c09-timebase-domain/tsim/freeze/final-evidence.json',)
    extension=root/'c09-component-timebase-r4/tsim/component-timebase-set.json'
    if extension.is_file():
        from .component_timebase import ComponentTimebaseSet,append_component_timebases
        index=_FreezeIndex(root,'c09-component-timebase-r4/tsim/freeze/final-evidence.json');index.artifact('component-timebase-set.json')
        ext=ComponentTimebaseSet.model_validate_json(extension.read_text());raw['timebases']=append_component_timebases(raw['timebases'],ext)
        raw['source_refs']+=('c09-component-timebase-r4/tsim/freeze/final-evidence.json',)
    write_sum_timebase=root/'c04-writesum-primitive-r1/tsim/write-sum-domain.json'
    if write_sum_timebase.is_file():
        index=_FreezeIndex(root,'c04-writesum-primitive-r1/tsim/freeze/final-evidence.json');index.artifact('write-sum-domain.json')
        domain=WriteSumPrimitiveDomain.model_validate_json(write_sum_timebase.read_text());raw['timebases']=append_write_sum_timebase(raw['timebases'],domain)
        raw['source_refs']+=('c04-writesum-primitive-r1/tsim/freeze/final-evidence.json',)
    r10_path=root/'c04-r10-stage-r1/tsim/accepted-domain.json'
    if r10_path.is_file():
        index=_FreezeIndex(root,'c04-r10-stage-r1/tsim/freeze/final-evidence.json');index.artifact('accepted-domain.json')
        domains=tuple(R10BoundaryDomain.model_validate_json(json.dumps(d)) for d in _json(r10_path)['domains'])
        raw['timebases']=append_r10_timebases(raw['timebases'],domains)
        raw['source_refs']+=('c04-r10-stage-r1/tsim/freeze/final-evidence.json',)
    r11=root/'c04-r11-stage-r1/tsim/r11-domain.json'
    if r11.is_file():
        index=_FreezeIndex(root,'c04-r11-stage-r1/tsim/freeze/final-evidence.json');index.artifact('r11-domain.json')
        raw['timebases']=append_r11_timebase(raw['timebases'],R11Domain.model_validate_json(r11.read_text()));raw['source_refs']+=('c04-r11-stage-r1/tsim/freeze/final-evidence.json',)
    preseed=root/'c04-fused-preseed-r1/tsim/fused-preseed-domain.json'
    if preseed.is_file():
        index=_FreezeIndex(root,'c04-fused-preseed-r1/tsim/freeze/final-evidence.json');index.artifact('fused-preseed-domain.json')
        raw['timebases']=append_fused_preseed_timebase(raw['timebases'],FusedPreseedDomain.model_validate_json(preseed.read_text()));raw['source_refs']+=('c04-fused-preseed-r1/tsim/freeze/final-evidence.json',)
    e7=root/'c07-e7-join-r1/tsim/e7-join-domain.json'
    if e7.is_file():
        index=_FreezeIndex(root,'c07-e7-join-r1/tsim/freeze/final-evidence.json');index.artifact('e7-join-domain.json')
        raw['timebases']=append_e7_timebase(raw['timebases'],E7JoinDomain.model_validate_json(e7.read_text()));raw['source_refs']+=('c07-e7-join-r1/tsim/freeze/final-evidence.json',)
    attn_norm=root/'c06-attn-output-norm-r1/tsim/attn-output-norm-domain.json'
    if attn_norm.is_file():
        index=_FreezeIndex(root,'c06-attn-output-norm-r1/tsim/freeze/final-evidence.json');index.artifact('attn-output-norm-domain.json')
        raw['timebases']=append_attn_output_norm_timebase(raw['timebases'],AttnOutputNormDomain.model_validate_json(attn_norm.read_text()));raw['source_refs']+=('c06-attn-output-norm-r1/tsim/freeze/final-evidence.json',)
    e4_e5=root/'c07-e4-e5-handoff-r1/tsim/e4-e5-handoff-domain.json'
    if e4_e5.is_file():
        index=_FreezeIndex(root,'c07-e4-e5-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('e4-e5-handoff-domain.json')
        raw['timebases']=append_e4_e5_timebase(raw['timebases'],E4E5HandoffDomain.model_validate_json(e4_e5.read_text()));raw['source_refs']+=('c07-e4-e5-handoff-r1/tsim/freeze/final-evidence.json',)
    e0_e1=root/'c07-e0-e1-handoff-r1/tsim/e0-e1-handoff-domain.json'
    if e0_e1.is_file():
        index=_FreezeIndex(root,'c07-e0-e1-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('e0-e1-handoff-domain.json')
        raw['timebases']=append_e0_e1_timebase(raw['timebases'],E0E1HandoffDomain.model_validate_json(e0_e1.read_text()));raw['source_refs']+=('c07-e0-e1-handoff-r1/tsim/freeze/final-evidence.json',)
    e2_e3=root/'c07-e2-e3-handoff-r1/tsim/e2-e3-handoff-domain.json'
    if e2_e3.is_file():
        index=_FreezeIndex(root,'c07-e2-e3-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('e2-e3-handoff-domain.json')
        raw['timebases']=append_e2_e3_timebase(raw['timebases'],E2E3HandoffDomain.model_validate_json(e2_e3.read_text()));raw['source_refs']+=('c07-e2-e3-handoff-r1/tsim/freeze/final-evidence.json',)
    e1_e2=root/'c07-e1-e2-handoff-r1/tsim/e1-e2-handoff-domain.json'
    if e1_e2.is_file():
        index=_FreezeIndex(root,'c07-e1-e2-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('e1-e2-handoff-domain.json')
        raw['timebases']=append_e1_e2_timebase(raw['timebases'],E1E2HandoffDomain.model_validate_json(e1_e2.read_text()));raw['source_refs']+=('c07-e1-e2-handoff-r1/tsim/freeze/final-evidence.json',)
    e3_e4=root/'c07-e3-e4-handoff-r1/tsim/e3-e4-handoff-domain.json'
    if e3_e4.is_file():
        index=_FreezeIndex(root,'c07-e3-e4-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('e3-e4-handoff-domain.json')
        raw['timebases']=append_e3_e4_timebase(raw['timebases'],E3E4HandoffDomain.model_validate_json(e3_e4.read_text()));raw['source_refs']+=('c07-e3-e4-handoff-r1/tsim/freeze/final-evidence.json',)
    r7_r8=root/'c07-r7-r8-handoff-r1/tsim/r7-r8-handoff-domain.json'
    if r7_r8.is_file():
        index=_FreezeIndex(root,'c07-r7-r8-handoff-r1/tsim/freeze/publication-identity-freeze.json');index.artifact('r7-r8-handoff-domain.json')
        raw['timebases']=append_r7_r8_timebase(raw['timebases'],R7R8HandoffDomain.model_validate_json(r7_r8.read_text()));raw['source_refs']+=('c07-r7-r8-handoff-r1/tsim/freeze/final-evidence.json','c07-r7-r8-handoff-r1/tsim/freeze/publication-identity-freeze.json')
    r9_count=root/'c07-r9-count-handoff-r1/tsim/r9-count-handoff-domain.json'
    if r9_count.is_file():
        index=_FreezeIndex(root,'c07-r9-count-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('r9-count-handoff-domain.json')
        raw['timebases']=append_r9_count_timebase(raw['timebases'],R9CountHandoffDomain.model_validate_json(r9_count.read_text()));raw['source_refs']+=('c07-r9-count-handoff-r1/tsim/freeze/final-evidence.json',)
    count_token=root/'c07-count-token-handoff-r1/tsim/count-token-handoff-domain.json'
    if count_token.is_file():
        index=_FreezeIndex(root,'c07-count-token-handoff-r1/tsim/freeze/final-evidence.json');index.artifact('count-token-handoff-domain.json')
        raw['timebases']=append_count_token_timebase(raw['timebases'],CountTokenHandoffDomain.model_validate_json(count_token.read_text()));raw['source_refs']+=('c07-count-token-handoff-r1/tsim/freeze/final-evidence.json',)
    token_fill=root/'c06-token-id-fill-r1/tsim/token-id-fill-handoff-domain.json'
    if token_fill.is_file():
        index=_FreezeIndex(root,'c06-token-id-fill-r1/tsim/freeze/final-evidence.json');index.artifact('token-id-fill-handoff-domain.json')
        raw['timebases']=append_token_id_fill_timebase(raw['timebases'],TokenIdFillDomain.model_validate_json(token_fill.read_text()));raw['source_refs']+=('c06-token-id-fill-r1/tsim/freeze/final-evidence.json',)
    for rank in (1,2,3):
        final_path=root/f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json'
        if final_path.is_file():
            index=_FreezeIndex(root,f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json')
            for segment in ('filter_count','count_token','token_fill'):
                filename=f'domain-rank{rank}-{segment}.json';index.artifact(filename)
                raw['timebases']=append_rank_timebase(raw['timebases'],RouterRankDomain.model_validate_json((final_path.parent.parent/filename).read_text()))
            raw['source_refs']+=(f'c07-router-ranks123-r1/tsim/freeze/final-rank{rank}.json',)
    for rank in range(4):
        final_path=root/f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json'
        if final_path.is_file():
            index=_FreezeIndex(root,f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json');index.artifact(f'domain-rank{rank}.json')
            raw['timebases']=append_count_timebase(raw['timebases'],CountFillDomain.model_validate_json((final_path.parent.parent/f'domain-rank{rank}.json').read_text()))
            raw['source_refs']+=(f'c06-count-fill-ranks-r1/tsim/freeze/final-rank{rank}.json',)
    for rank in range(4):
        final_path=root/f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json'
        if final_path.is_file():
            index=_FreezeIndex(root,f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json');index.artifact(f'domain-rank{rank}.json')
            raw['timebases']=append_tail_timebase(raw['timebases'],ScalarTailDomain.model_validate_json((final_path.parent.parent/f'domain-rank{rank}.json').read_text()))
            raw['source_refs']+=(f'c09-scalar-local-r1/tsim/freeze/final-rank{rank}.json',)
    for rank in range(4):
        final_path=root/f'c09-count-scan-r1/tsim/freeze/final-rank{rank}.json'
        if final_path.is_file():
            index=_FreezeIndex(root,f'c09-count-scan-r1/tsim/freeze/publication-rank{rank}.json');index.artifact(f'publication-domain-rank{rank}.json')
            raw['timebases']=append_scan_timebase(raw['timebases'],CountScanDomain.model_validate_json((final_path.parent.parent/f'publication-domain-rank{rank}.json').read_text()))
            raw['source_refs']+=(f'c09-count-scan-r1/tsim/freeze/final-rank{rank}.json',)
    from pydantic import TypeAdapter
    from .expert_matrix_domain import append_expert_publication
    raw=append_expert_publication(root,raw)
    from .gate_up_domain import append_gate_up_publication
    raw=append_gate_up_publication(root,raw)
    shape=TypeAdapter(dict).dump_python(raw,mode='json');return ComponentCalibrationBundle(**raw,bundle_sha256=stable_sha256(shape))


def resolve_calibration(bundle,request):
    bundle=ComponentCalibrationBundle.model_validate_json(bundle.model_dump_json());request=CalibrationResolutionRequest.model_validate_json(request.model_dump_json())
    matches=[]
    for entry in bundle.entries:
        d=entry.domain
        if entry.component_id!=request.component_id or entry.mechanism!=request.mechanism or entry.operation!=request.operation or request.profile not in entry.profiles or request.consumer_stage not in entry.consumer_stages:continue
        if request.mechanism=='source_gate_up_engines':
            from .gate_up_domain import gate_up_request_matches
            if not gate_up_request_matches(entry,request):continue
        if request.mechanism=='expert_matrix_gathered':
            from .expert_matrix_domain import expert_request_matches
            if not expert_request_matches(entry,request):continue
        if request.mechanism=='gm_source_service' and entry.formula_id!='M04-source-write-sum-combined-r1':continue
        if request.gm_source_work is not None:continue  # no accepted source-service formula yet
        if request.parameter_paths and not set(request.parameter_paths)<=set(entry.parameter_paths):continue
        if request.shape_key is not None and d.shape_keys and request.shape_key not in d.shape_keys:continue
        if request.V is not None and ((d.v_min is not None and request.V<d.v_min) or (d.v_max is not None and request.V>d.v_max) or request.V in d.excluded_v):continue
        if request.payload_bytes is not None and ((d.payload_min_bytes is not None and request.payload_bytes<d.payload_min_bytes) or (d.payload_max_bytes is not None and request.payload_bytes>d.payload_max_bytes)):continue
        if request.fanout is not None and d.fanout_values and request.fanout not in d.fanout_values:continue
        if d.scenario_classes and request.scenario_class not in d.scenario_classes:continue
        if d.operation_variants and request.operation_variant not in d.operation_variants:continue
        if d.operation_variants and request.payload_bytes is None and entry.mechanism!='sync_e7_commid_join':continue
        if d.controller_counts and request.controller_count not in d.controller_counts:continue
        if d.transaction_counts and request.transaction_count not in d.transaction_counts:continue
        if d.address_distributions and request.address_distribution not in d.address_distributions:continue
        if d.flow_counts and request.flow_count not in d.flow_counts:continue
        if d.source_counts and request.source_count not in d.source_counts:continue
        if d.directions and request.direction not in d.directions:continue
        if d.producer_counts and request.producer_count not in d.producer_counts:continue
        if d.waiter_counts and request.waiter_count not in d.waiter_counts:continue
        if d.round_counts and request.rounds not in d.round_counts:continue
        if d.frontiers and request.frontier not in d.frontiers:continue
        if d.rank_counts and request.rank_count not in d.rank_counts:continue
        if d.chunk_counts and request.chunk_count not in d.chunk_counts:continue
        if d.commid_slots and request.commid_slot_idx not in d.commid_slots:continue
        if d.schedule_domains and request.schedule_domain not in d.schedule_domains:continue
        if d.router_invocation_counts and request.router_invocations not in d.router_invocation_counts:continue
        if d.matrix_shapes and request.matrix_shape not in d.matrix_shapes:continue
        if d.weight_localities and request.weight_locality not in d.weight_localities:continue
        if d.feature_layouts and request.feature_layout not in d.feature_layouts:continue
        if d.feature_strides and request.feature_stride not in d.feature_strides:continue
        if entry.mechanism=='gm_r11_combined':
            w=request.r11_work
            if w is None or request.V!=w.V or request.payload_bytes!=w.upload_bytes or request.profile!=w.profile or request.operation!=w.operation or w.model_dump(mode='json')!=d.input_dimensions['r11_work']:continue
            if any(getattr(request,k)!=d.input_dimensions[k] for k in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='gm_r10_boundary':
            w=request.r10_boundary_work
            if w is None or request.payload_bytes!=w.payload_bytes or request.profile!=w.profile or request.operation!=w.operation or w.model_dump(mode='json')!=d.input_dimensions['r10_boundary_work']:continue
            if any(getattr(request,k)!=d.input_dimensions[k] for k in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='gm_fused_preseed':
            w=request.fused_preseed_work
            if w is None or request.payload_bytes!=w.payload_bytes_per_pe or request.profile!=w.profile or request.operation!=w.operation or w.model_dump(mode='json')!=d.input_dimensions['fused_preseed_work']:continue
            if request.model_library_sha256!=d.input_dimensions['model_library_sha256'] or request.systemc_library_sha256!=d.input_dimensions['systemc_library_sha256'] or request.observer_sha256!=d.input_dimensions['observer_sha256'] or tuple(request.counter_callsites)!=tuple(d.input_dimensions['counter_callsites']):continue
        if entry.mechanism=='sync_e7_commid_join':
            w=request.e7_join_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e7_join_work'] or request.producer_count!=32 or request.waiter_count!=14 or request.commid_slot_idx!=16:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='vector_attn_output_norm':
            w=request.attn_output_norm_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['attn_output_norm_work'] or request.payload_bytes!=8192 or request.shape_key!='transformer-fused':continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_e4_e5_local_handoff':
            w=request.e4_e5_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e4_e5_handoff_work'] or request.V!=64 or request.payload_bytes!=65536 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_e0_e1_local_handoff':
            w=request.e0_e1_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e0_e1_handoff_work'] or request.V!=64 or request.payload_bytes!=131072 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_e2_e3_local_handoff':
            w=request.e2_e3_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e2_e3_handoff_work'] or request.V!=64 or request.payload_bytes!=24576 or request.shape_key!='transformer-fused' or request.producer_count!=4 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_e1_e2_local_handoff':
            w=request.e1_e2_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e1_e2_handoff_work'] or request.V!=64 or request.payload_bytes!=131072 or request.shape_key!='transformer-fused' or request.producer_count!=4 or request.waiter_count!=4:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_e3_e4_local_handoff':
            w=request.e3_e4_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['e3_e4_handoff_work'] or request.V!=64 or request.payload_bytes!=24576 or request.shape_key!='transformer-fused' or request.producer_count!=4 or request.waiter_count!=28:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_r7_r8_local_handoff':
            w=request.r7_r8_handoff_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['r7_r8_handoff_work'] or request.V!=64 or request.payload_bytes!=12288 or request.shape_key!='transformer-fused' or request.producer_count!=4 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_r9_count_local_handoff':
            if request.request_id!='rank0.R9_COUNT.local_handoff' or request.action_id!='rank0:R9_COUNT.local_handoff':continue
            w=request.r9_count_handoff_work
            if w is None or w.ep_rank!=0 or w.model_dump(mode='json')!=d.input_dimensions['r9_count_handoff_work'] or request.V!=64 or request.payload_bytes!=256 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='sync_count_token_local_handoff':
            if request.request_id!='rank0.COUNT_TOKEN.local_handoff' or request.action_id!='rank0:COUNT_TOKEN.local_handoff':continue
            w=request.count_token_handoff_work
            if w is None or w.ep_rank!=0 or w.model_dump(mode='json')!=d.input_dimensions['count_token_handoff_work'] or request.V!=64 or request.payload_bytes!=128 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='vector_token_id_fill':
            if request.request_id!='rank0.TOKEN_ID.fill' or request.action_id!='rank0:TOKEN_ID.fill':continue
            w=request.token_id_fill_work
            if w is None or w.ep_rank!=0 or w.model_dump(mode='json')!=d.input_dimensions['token_id_fill_work'] or request.V!=64 or request.payload_bytes!=128 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='router_rank_local':
            if request.router_rank_token_counts!=(2,)*32:continue
            w=request.router_rank_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['router_rank_work'] or request.V!=64 or request.payload_bytes!=w.payload_bytes or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if request.request_id!=f'rank{w.ep_rank}.router.{w.segment}' or request.action_id!=f'rank{w.ep_rank}:router.{w.segment}':continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='vector_count_fill':
            w=request.count_fill_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['count_fill_work'] or request.V!=64 or request.payload_bytes!=128 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if request.request_id!=f'rank{w.ep_rank}.count_fill' or request.action_id!=f'rank{w.ep_rank}:count_fill':continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='scalar_count_scan':
            w=request.count_scan_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['count_scan_work'] or request.V!=64 or request.payload_bytes!=12 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if request.request_id!=f'rank{w.ep_rank}.count_scan' or request.action_id!=f'rank{w.ep_rank}:count_scan' or request.compiled_path_sha256!=d.input_dimensions['compiled_path_sha256']:continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.mechanism=='scalar_tail_service':
            w=request.scalar_tail_work
            if w is None or w.model_dump(mode='json')!=d.input_dimensions['scalar_tail_work'] or request.V!=64 or request.payload_bytes!=128 or request.shape_key!='transformer-fused' or request.producer_count!=32 or request.waiter_count!=32:continue
            if request.request_id!=f'rank{w.ep_rank}.scalar_tail' or request.action_id!=f'rank{w.ep_rank}:scalar_tail':continue
            if any(getattr(request,key)!=d.input_dimensions[key] for key in ('model_library_sha256','systemc_library_sha256','observer_sha256','counter_callsite')):continue
        if entry.formula_id=='M06-source-bulk-round-r4':
            w=request.vector_bulk_work
            if w is None or request.shape_key!=w.profile or request.profile!=w.profile or request.operation!=w.operation or request.V!=w.route_V or w.model_dump(mode='json')!=d.input_dimensions['vector_bulk_work']:continue
        if entry.formula_id=='M05-router-source-shard-boundaries-r3' and request.V is None:continue
        if entry.component_id=='M08' and any(v is None for v in (request.rank_count,request.chunk_count,request.commid_slot_idx,request.ccl_config_sha256,request.rank_map_sha256,request.comm_program_sha256)):continue
        if entry.formula_id=='M07-local-queue-r1' and request.V is None:continue
        if request.route_domain not in d.route_domains:continue
        if request.requires_physical_time and entry.publication_class!='current_architecture_component_limited':continue
        matches.append(entry)
    if len(matches)!=1:raise ValueError(f'calibration resolution requires exactly one accepted entry, found {len(matches)} for {request.action_id}')
    entry=matches[0];return CalibrationResolution(request_sha256=stable_sha256(request.model_dump(mode='json')),entry_id=entry.entry_id,entry_sha256=entry.entry_sha256,formula_sha256=entry.formula_sha256,publication_class=entry.publication_class)


def execute_resolved_r7_r8_handoff(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    """Consume the exact production request before native local execution."""
    from .r7_r8_handoff import resolve_r7_r8_handoff,execute_r7_r8_handoff
    from .timebase import resolve_timebase
    selected=resolve_calibration(bundle,request)
    if selected.entry_id!='m07-source-fused-R7-R8-local-handoff-V64':raise ValueError('R7/R8 wrong production action owner')
    resolved=resolve_r7_r8_handoff((domain,),work=request.r7_r8_handoff_work,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,
        observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    entry=next(e for e in bundle.entries if e.entry_id==selected.entry_id)
    if entry.formula!=resolved.formula.model_dump(mode='json'):raise ValueError('R7/R8 selected formula differs from runtime domain')
    timebase=resolve_timebase(bundle.timebases,component_id='M07',operation=resolved.work.operation,
        profile=request.profile,boundary_id=resolved.work.boundary,source_unit='reported_ns',work=request.V,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,physical=False)
    if timebase!=resolved.timebase:raise ValueError('R7/R8 exact runtime timebase mismatch')
    units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
    for pe in range(32):
        unit=units.get(binding.resources[f'PE{pe}.local_vector_ready'])
        if unit is None or unit['node_id']!=f'PE{pe}':raise ValueError('R7/R8 local resource PE identity mismatch')
    expected={f'R7.pe{pe}.broadcast.complete' for pe in range(32)}
    if not expected<={a.action_id for a in extra_actions}:raise ValueError('R7/R8 predecessor completion dependencies missing')
    result=execute_r7_r8_handoff(resolved,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    result.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,
        calibration_resolution=selected.model_dump(mode='json'),production_request_consumptions=1,
        validated_source_unit='reported_ns',timebase_domain_id=timebase.domain_id)
    return result


def execute_resolved_r9_count_handoff(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    """Consume the exact production request before native local execution."""
    from .r9_count_handoff import resolve_r9_count_handoff,execute_r9_count_handoff
    from .timebase import resolve_timebase
    if hardware.clock_domain!='reported_ns':raise ValueError('R9/count clock unit mismatch')
    selected=resolve_calibration(bundle,request)
    if selected.entry_id!='m07-source-fused-R9-count-local-handoff-rank0-V64':raise ValueError('R9/count wrong production action owner')
    resolved=resolve_r9_count_handoff((domain,),work=request.r9_count_handoff_work,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,
        observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    entry=next(e for e in bundle.entries if e.entry_id==selected.entry_id)
    if entry.formula!=resolved.formula.model_dump(mode='json'):raise ValueError('R9/count selected formula differs from runtime domain')
    timebase=resolve_timebase(bundle.timebases,component_id='M07',operation=resolved.work.operation,
        profile=request.profile,boundary_id=resolved.work.boundary,source_unit='reported_ns',work=request.V,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,physical=False)
    if timebase!=resolved.timebase:raise ValueError('R9/count exact runtime timebase mismatch')
    units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
    for pe in range(32):
        unit=units.get(binding.resources[f'PE{pe}.local_vector_ready'])
        if unit is None or unit['node_id']!=f'PE{pe}':raise ValueError('R9/count local resource PE identity mismatch')
    expected={f'R9.rank0.pe{pe}.filter_write.complete' for pe in range(32)}
    if not expected<={a.action_id for a in extra_actions}:raise ValueError('R9/count predecessor completion dependencies missing')
    result=execute_r9_count_handoff(resolved,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    result.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,
        calibration_resolution=selected.model_dump(mode='json'),production_request_consumptions=1,
        validated_source_unit='reported_ns',timebase_domain_id=timebase.domain_id)
    return result


def execute_resolved_count_token_handoff(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    """Consume the exact production request before native local execution."""
    from .count_token_handoff import resolve_count_token_handoff,execute_count_token_handoff
    from .timebase import resolve_timebase
    if hardware.clock_domain!='reported_ns':raise ValueError('count/token clock unit mismatch')
    selected=resolve_calibration(bundle,request)
    if selected.entry_id!='m07-source-fused-count-token-local-handoff-rank0-V64':raise ValueError('count/token wrong production action owner')
    resolved=resolve_count_token_handoff((domain,),work=request.count_token_handoff_work,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,
        observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    entry=next(e for e in bundle.entries if e.entry_id==selected.entry_id)
    if entry.formula!=resolved.formula.model_dump(mode='json'):raise ValueError('count/token selected formula differs from runtime domain')
    timebase=resolve_timebase(bundle.timebases,component_id='M07',operation=resolved.work.operation,
        profile=request.profile,boundary_id=resolved.work.boundary,source_unit='reported_ns',work=request.V,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,physical=False)
    if timebase!=resolved.timebase:raise ValueError('count/token exact runtime timebase mismatch')
    units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
    for pe in range(32):
        unit=units.get(binding.resources[f'PE{pe}.local_vector_ready'])
        if unit is None or unit['node_id']!=f'PE{pe}':raise ValueError('count/token local resource PE identity mismatch')
    expected={f'R9.rank0.pe{pe}.count_fill.write.complete' for pe in range(32)}
    if not expected<={a.action_id for a in extra_actions}:raise ValueError('count/token predecessor completion dependencies missing')
    result=execute_count_token_handoff(resolved,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    result.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,
        calibration_resolution=selected.model_dump(mode='json'),production_request_consumptions=1,
        validated_source_unit='reported_ns',timebase_domain_id=timebase.domain_id)
    return result


def execute_resolved_token_id_fill(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    from .token_id_fill import resolve_token_id_fill,execute_token_id_fill
    from .timebase import resolve_timebase
    if hardware.clock_domain!='reported_ns':raise ValueError('token fill clock unit mismatch')
    selected=resolve_calibration(bundle,request)
    if selected.entry_id!='m06-source-fused-token-id-fill-rank0-V64':raise ValueError('token fill wrong action owner')
    domain=resolve_token_id_fill((domain,),work=request.token_id_fill_work,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,
        observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    entry=next(e for e in bundle.entries if e.entry_id==selected.entry_id)
    if entry.formula!=domain.formula.model_dump(mode='json'):raise ValueError('token fill formula mismatch')
    timebase=resolve_timebase(bundle.timebases,component_id='M06',operation=domain.work.operation,profile=request.profile,
        boundary_id=domain.work.boundary,source_unit='reported_ns',work=request.V,
        model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,physical=False)
    if timebase!=domain.timebase:raise ValueError('token fill timebase mismatch')
    units={u['unit_id']:u for u in binding.binding.graph['execution_units']}
    for pe in range(32):
        for engine in ('CONFIG','VECTOR_CAL','VECTOR_WRITE'):
            resource=f'PE{pe}.{engine}';unit=units.get(binding.resources[resource])
            if unit is None or unit['node_id']!=f'PE{pe}' or unit['unit_id']!=resource:raise ValueError('token fill resource engine/PE mismatch')
    if not {f'COUNT_TOKEN.handoff.pe{pe}' for pe in range(32)}<={a.action_id for a in extra_actions}:raise ValueError('token fill source predecessor readiness missing')
    run=execute_token_id_fill(domain,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    run.receipt.update(production_request_consumptions=1,production_request_id=request.request_id,production_action_id=request.action_id,
        calibration_resolution=selected.model_dump(mode='json'),timebase_domain_id=timebase.domain_id,validated_source_unit='reported_ns')
    return run


def execute_resolved_router_rank(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    from .router_rank_domains import resolve_rank_domain,execute_rank_domain,predecessor
    from .timebase import resolve_timebase
    resolution=resolve_calibration(bundle,request);w=request.router_rank_work
    if w is None or resolution.entry_id!=f'router-rank{w.ep_rank}-{w.segment}-V64':raise ValueError('rank request owner mismatch')
    d=resolve_rank_domain((domain,),work=w,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    entry=next(e for e in bundle.entries if e.entry_id==resolution.entry_id)
    if entry.formula!=d.formula.model_dump(mode='json'):raise ValueError('rank runtime formula mismatch')
    t=resolve_timebase(bundle.timebases,component_id=request.component_id,operation=w.operation,profile=w.profile,boundary_id=w.boundary,source_unit='reported_ns',work=64,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256)
    if t!=d.timebase:raise ValueError('rank runtime timebase mismatch')
    if not {predecessor(w.ep_rank,w.segment,p) for p in range(32)}<={a.action_id for a in extra_actions}:raise ValueError('rank runtime predecessor identity missing')
    run=execute_rank_domain(d,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    run.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,production_request_consumptions=1,calibration_resolution=resolution.model_dump(mode='json'))
    return run


def execute_resolved_count_fill(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    from .count_fill import resolve_count_fill,execute_count_fill,count_start
    from .timebase import resolve_timebase
    resolution=resolve_calibration(bundle,request);w=request.count_fill_work
    if w is None or resolution.entry_id!=f'm06-count-fill-rank{w.ep_rank}-V64':raise ValueError('count fill runtime request owner mismatch')
    d=resolve_count_fill((domain,),work=w,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    if next(e for e in bundle.entries if e.entry_id==resolution.entry_id).formula!=d.formula.model_dump(mode='json'):raise ValueError('count fill runtime formula mismatch')
    t=resolve_timebase(bundle.timebases,component_id='M06',operation=w.operation,profile=w.profile,boundary_id=w.boundary,source_unit='reported_ns',work=64,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256)
    if t!=d.timebase:raise ValueError('count fill runtime timebase mismatch')
    if not {count_start(w.ep_rank,p) for p in range(32)}<={a.action_id for a in extra_actions}:raise ValueError('count fill preceding readiness identity missing')
    run=execute_count_fill(d,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    run.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,production_request_consumptions=1,calibration_resolution=resolution.model_dump(mode='json'))
    return run


def execute_resolved_scalar_tail(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    from .scalar_tail import resolve_scalar_tail,execute_scalar_tail,tail_pred
    from .timebase import resolve_timebase
    r=resolve_calibration(bundle,request);w=request.scalar_tail_work
    if w is None or r.entry_id!=f'm07-scalar-tail-rank{w.ep_rank}-V64':raise ValueError('scalar tail request owner mismatch')
    d=resolve_scalar_tail((domain,),work=w,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,physical=request.requires_physical_time)
    if next(e for e in bundle.entries if e.entry_id==r.entry_id).formula!=d.formula.model_dump(mode='json'):raise ValueError('scalar tail runtime formula mismatch')
    t=resolve_timebase(bundle.timebases,component_id='M07',operation=w.operation,profile=w.profile,boundary_id=w.boundary,source_unit='reported_ns',work=64,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256)
    if t!=d.timebase:raise ValueError('scalar tail runtime timebase mismatch')
    if not {tail_pred(w.ep_rank,p) for p in range(32)}<={a.action_id for a in extra_actions}:raise ValueError('scalar tail count-scan completion identity missing')
    run=execute_scalar_tail(d,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    run.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,production_request_consumptions=1,calibration_resolution=r.model_dump(mode='json'))
    return run


def execute_resolved_count_scan(bundle,request,domain,binding,hardware,*,changes=None,extra_actions=(),cancel=None):
    from .count_scan import resolve_count_scan,execute_count_scan
    from .timebase import resolve_timebase
    selected=resolve_calibration(bundle,request);w=request.count_scan_work
    if w is None or selected.entry_id!=f'm07-count-scan-rank{w.ep_rank}-V64':raise ValueError('count scan request owner mismatch')
    d=resolve_count_scan((domain,),work=w,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256,observer_sha256=request.observer_sha256,counter_callsite=request.counter_callsite,compiled_path_sha256=request.compiled_path_sha256,physical=request.requires_physical_time)
    if next(e for e in bundle.entries if e.entry_id==selected.entry_id).formula!=d.formula.model_dump(mode='json'):raise ValueError('count scan runtime formula mismatch')
    t=resolve_timebase(bundle.timebases,component_id='M07',operation=w.operation,profile=w.profile,boundary_id=w.boundary,source_unit='reported_ns',work=64,model_library_sha256=request.model_library_sha256,systemc_library_sha256=request.systemc_library_sha256)
    if t!=d.timebase:raise ValueError('count scan runtime timebase mismatch')
    run=execute_count_scan(d,binding,hardware,changes=changes,extra_actions=extra_actions,production=True,cancel=cancel)
    run.receipt.update(production_request_id=request.request_id,production_action_id=request.action_id,production_request_consumptions=1,calibration_resolution=selected.model_dump(mode='json'))
    return run


def workload_action_requests(workload,binding_sha256,*,chain_scenarios=None,vector_bulk_bindings=None,fused_preseed_identity=None,e7_join_identity=None,attn_output_norm_identity=None,e4_e5_handoff_identity=None,e0_e1_handoff_identity=None,e2_e3_handoff_identity=None,e1_e2_handoff_identity=None,e3_e4_handoff_identity=None,r7_r8_handoff_identity=None,r9_count_handoff_identity=None,count_token_handoff_identity=None,token_id_fill_identity=None,router_rank_identities=None,count_fill_identities=None,scalar_tail_identities=None,count_scan_identities=None,expert_matrix_domains=None,gate_up_domains=None):
    workload=FrozenMoeWorkload.model_validate_json(workload.model_dump_json());requests=[]
    if any(type(rank) is not int or rank not in workload.observed_ep_ranks for rank in (expert_matrix_domains or {})):
        raise ValueError('expert Matrix supplied domain rank is absent from workload')
    if any(type(rank) is not int or rank not in workload.observed_ep_ranks for rank in (gate_up_domains or {})):
        raise ValueError('gate/up supplied domain rank is absent from workload')
    mappings={
      'R0':[('M01','point_to_point','point_to_point','production-bound-route',True),('M04','gm_service','read','production-read-mapping',True)],'R1':[('M06','vector','rmsnorm','source-shaped-current-architecture',True),('M07','sync_protocol','inner_release_acquire','protocol-ordering',False),('M07','sync_protocol','fence_calc','protocol-ordering',False)],
      'R2':[('M01','point_to_point','point_to_point','production-bound-route',True),('M04','gm_service','write','production-write-mapping',True)],'R3':[('M06','vector','quant','source-shaped-current-architecture',True),('M07','sync_protocol','inner_release_acquire','protocol-ordering',False)],
      'R4':[('M01','point_to_point','point_to_point','production-bound-route',True),('M04','gm_service','write','production-write-mapping',True)],'R5':[('M02','masked_multicast','masked_multicast','production-bound-route',True),('M07','sync_protocol','outer_release_acquire','protocol-ordering',False)],
      'R6':[('M05','matrix','router','resident-weight-current-architecture',True)],'R7':[('M03','pe_chain','column','native-8x4-column',True),('M07','sync_protocol','outer_release_acquire','protocol-ordering',False)],
      'R8':[('M06','vector','softmax_topk','source-shaped-current-architecture',True),('M07','sync_protocol','fence_calc','protocol-ordering',False)],'R9':[('M06','vector','softmax_topk','source-shaped-current-architecture',True)],
      'R10':[('M04','gm_service','write_sum','production-write-sum-mapping',True),('M07','sync_protocol','counted_after_all','protocol-ordering',False)],
      'R11':[('M01','point_to_point','point_to_point','production-bound-route',True),('M02','masked_multicast','masked_multicast','production-bound-route',True),('M04','gm_service','write','production-write-mapping',True),('M07','sync_protocol','fence_io','protocol-ordering',False)],
      'R12':[('M06','vector','table','source-shaped-current-architecture',True)],'E0':[('M05','matrix','gate_up','resident-weight-current-architecture',True)],
      'E1':[('M03','pe_chain','column','native-8x4-column',True)],'E2':[('M06','vector','silu_score_quant','source-shaped-current-architecture',True)],'E3':[('M02','masked_multicast','masked_multicast','production-bound-route',True)],
      'E4':[('M05','matrix','down','resident-weight-current-architecture',True)],'E5':[('M03','pe_chain','row','native-8x4-row',True)],'E6':[('M04','gm_service','write_sum','production-write-sum-mapping',True)],
      'E7':[('M07','sync_protocol','counted_after_all','protocol-ordering',False)],'E8':[('M08','adalink_collective','allreduce','supplied-binding-required',True)]}
    for rank in workload.observed_ep_ranks:
        plan=build_operation_plan(workload,rank,binding_sha256=binding_sha256,include_allreduce=True,allow_fixture=workload.trace_kind!='real_decode')
        for stage in plan.stages:
            if not stage.active:continue
            if stage.stage_id=='E0' and rank in (gate_up_domains or {}):
                from .gate_up_domain import GateUpDomain, gate_up_request, gate_up_work_from_workload
                domain=GateUpDomain.model_validate_json(gate_up_domains[rank].model_dump_json())
                if domain.work!=gate_up_work_from_workload(workload,rank):raise ValueError('gate/up workload/domain mismatch')
                requests.append(gate_up_request(domain,workload.workload_sha256))
                continue
            if stage.stage_id=='E4' and rank in (expert_matrix_domains or {}):
                from .expert_matrix_input import work_from_workload
                from .expert_matrix_domain import ExpertMatrixDomain, expert_request
                domain=ExpertMatrixDomain.model_validate_json(expert_matrix_domains[rank].model_dump_json())
                if domain.work!=work_from_workload(workload,rank):raise ValueError('expert Matrix workload/domain mismatch')
                requests.append(expert_request(domain,workload.workload_sha256))
                continue
            for index,(component,mechanism,operation,route,physical) in enumerate(mappings[stage.stage_id]):
                if stage.stage_id=='E7' and workload.profile.name=='transformer-fused':continue
                if component in ('M01','M02') and stage.attributes.get('datanoc_flows'):
                    selected=[flow for flow in stage.attributes['datanoc_flows'] if flow['primitive']==operation]
                    for flow_index,flow in enumerate(selected):
                        requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.{stage.stage_id}.{index}.{flow_index}',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:{stage.stage_id}:{flow["flow_role"]}',component_id=component,mechanism=mechanism,operation=operation,profile=workload.profile.name,consumer_stage=stage.stage_id,
                          V=plan.V,payload_bytes=flow['payload_per_flow'],fanout=flow['fanout'],route_domain='production-route-unbound:'+flow['route_group'],requires_physical_time=physical,scenario_class=flow['concurrency'],operation_variant=flow['direction'],flow_count=flow['flow_count'],source_count=flow['source_count'],direction=flow['direction']))
                    continue
                bulk_work=None
                if component=='M06' and operation in ('table','silu_score_quant'):
                    label=(vector_bulk_bindings or {}).get(f'rank{rank}:{stage.stage_id}')
                    route='production-vector-bulk-unbound'
                    if label is not None:
                        bulk_work=bulk_work_from_plan(workload,plan,operation,buffer_binding=label)
                        route='source-bulk-private-current-buffer-v2'
                fanout=stage.attributes.get('fanout_per_root',stage.attributes.get('quant_fanout',stage.attributes.get('rms_fanout')))
                chain_variant=stage.attributes.get('chain_variant') if component=='M03' else None
                # All source chains have multiple concurrently active groups.
                # An external overlap schedule must override this for other domains.
                scenario=(chain_scenarios or {}).get(f'rank{rank}:{stage.stage_id}','controlled_overlap') if component=='M03' else None
                payload=stage.attributes['chain_payload_bytes'] if component=='M03' else stage.logical_source_bytes or None
                gm=stage.attributes if component=='M04' else {}
                if component=='M04' and stage.stage_id in ('R0','R10','R11','E6'):
                    mechanism='gm_source_service'
                    route='production-gm-source-unbound'
                    if stage.stage_id=='R10' and workload.profile.name=='transformer-fused':
                        route='source-fused-r10-gm-wdma0-channel1-same-address'
                if component=='M04':payload=gm.get('gm_payload_per_transaction')
                requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.{stage.stage_id}.{index}',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:{stage.stage_id}',component_id=component,
                  mechanism=mechanism,operation=operation,profile=workload.profile.name,consumer_stage=stage.stage_id,shape_key=workload.profile.name if component in ('M05','M06','M08') else None,V=plan.V,
                  vector_bulk_work=bulk_work,payload_bytes=payload,fanout=fanout,route_domain='production-sync-unbound:'+operation if component=='M07' else 'source-router-current-address' if component=='M05' and operation=='router' else route,requires_physical_time=True if component=='M07' else physical,scenario_class=scenario,
                  operation_variant=chain_variant or ('ordinary-write' if component=='M04' and operation=='write' else gm.get('gm_operation')),
                  controller_count=gm.get('gm_controller_count'),transaction_count=gm.get('gm_transaction_count'),address_distribution=gm.get('gm_address_distribution'),producer_count=gm.get('gm_transaction_count') if component=='M04' and operation=='write_sum' else None,rank_count=workload.profile.ep_size if component=='M08' else None,router_invocations=1 if component=='M05' and operation=='router' else None,matrix_shape=(8,384,workload.profile.hidden_gating//8) if component=='M05' and operation=='router' else None,feature_stride=workload.profile.hidden_gating if component=='M05' and operation=='router' else None,weight_locality='resident-source-packed-current-address' if component=='M05' and operation=='router' else None,feature_layout='source-hidden-stride-pe-row-shard' if component=='M05' and operation=='router' else None))
    if workload.profile.name=='transformer-fused':
        for rank in workload.observed_ep_ranks:
            for op,fanout in (('zero_init',1),('reduce_done_broadcast_consumer',32)):
                requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R10.{op}',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R10.{op}',component_id='M04',mechanism='gm_r10_boundary',operation=op,profile='transformer-fused',consumer_stage='R10',payload_bytes=128,fanout=fanout,route_domain='source-fused-r10-'+op+'-channel1',requires_physical_time=True))
    if workload.profile.name=='transformer-fused':
        for rank in workload.observed_ep_ranks:
            V=next(r.valid_selection_count for r in workload.rank_valid_counts if r.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R11.combined',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R11.combined',component_id='M04',mechanism='gm_r11_combined',operation='topk_allgather_combined',profile='transformer-fused',consumer_stage='R11',V=V,payload_bytes=12*V,fanout=32,route_domain='source-fused-r11-stride256-four-wdma-private-r2',requires_physical_time=True))
    if workload.profile.name=='transformer-fused':
        identity=fused_preseed_identity or {}
        for rank in workload.observed_ep_ranks:
            plan=build_operation_plan(workload,rank,binding_sha256=binding_sha256,include_allreduce=True,allow_fixture=workload.trace_kind!='real_decode');work=work_from_operation_plan(plan)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R4.residual_preseed',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R4.residual_preseed',component_id='M04',mechanism='gm_fused_preseed',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,payload_bytes=work.payload_bytes_per_pe,route_domain='source-fused-residual-preseed-four-wdma-channel1',requires_physical_time=False,operation_variant='ordinary-write-source-exact',controller_count=4,transaction_count=32,address_distribution='disjoint-output-rows',flow_count=32,source_count=32,direction='pe_to_gm',fused_preseed_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsites=tuple(identity.get('counter_callsites',()))))
    if workload.profile.name=='transformer-fused':
        identity=e7_join_identity or {};work=E7JoinWork()
        for rank in workload.observed_ep_ranks:
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E7.output_completion_join',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E7.output_completion_join',component_id='M07',mechanism='sync_e7_commid_join',operation=work.operation,profile=work.profile,consumer_stage='E7',route_domain='source-fused-e7-fullmesh-ports4-17-commid16',requires_physical_time=False,scenario_class='source-exact-counted-join',operation_variant='PE_UPLOAD1-to-ADL_FUNC',producer_count=32,waiter_count=14,commid_slot_idx=16,e7_join_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=attn_output_norm_identity or {};work=AttnOutputNormWork()
        for rank in workload.observed_ep_ranks:
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R0.attn_output_norm',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R0.attn_output_norm',component_id='M06',mechanism='vector_attn_output_norm',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',payload_bytes=8192,route_domain='source-fused-attn-output-norm-inplace-vector',requires_physical_time=False,scenario_class='source-exact-inplace-rmsnorm-terminal',operation_variant='rms_norm-2048-float-inplace',attn_output_norm_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=e4_e5_handoff_identity or {};work=E4E5HandoffWork()
        for rank in workload.observed_ep_ranks:
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E4_E5.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E4_E5.local_handoff',component_id='M07',mechanism='sync_e4_e5_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=64,payload_bytes=65536,route_domain='source-fused-e4-e5-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-matrix-local-handoff',operation_variant='24-upload1-8-download1',producer_count=32,waiter_count=32,e4_e5_handoff_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=e0_e1_handoff_identity or {};work=E0E1HandoffWork()
        for rank in workload.observed_ep_ranks:
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E0_E1.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E0_E1.local_handoff',component_id='M07',mechanism='sync_e0_e1_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=64,payload_bytes=131072,route_domain='source-fused-e0-e1-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-matrix-local-handoff',operation_variant='28-upload1-4-download1',producer_count=32,waiter_count=32,e0_e1_handoff_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=e2_e3_handoff_identity or {};work=E2E3HandoffWork()
        for rank in workload.observed_ep_ranks:
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E2_E3.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E2_E3.local_handoff',component_id='M07',mechanism='sync_e2_e3_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=64,payload_bytes=24576,route_domain='source-fused-e2-e3-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-vector-local-handoff',operation_variant='4-vector-roots-dual-sync-28-nonroot-ready',producer_count=4,waiter_count=32,e2_e3_handoff_work=work,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=e1_e2_handoff_identity or {};work=E1E2HandoffWork()
        for rank in workload.observed_ep_ranks:
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E1_E2.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E1_E2.local_handoff',component_id='M07',mechanism='sync_e1_e2_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=131072,route_domain='source-fused-e1-e2-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-column-reduce-local-handoff',operation_variant='4-download1-to-vector-roots',producer_count=4,waiter_count=4,e1_e2_handoff_work=work if rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=e3_e4_handoff_identity or {};work=E3E4HandoffWork()
        for rank in workload.observed_ep_ranks:
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.E3_E4.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:E3_E4.local_handoff',component_id='M07',mechanism='sync_e3_e4_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=24576,route_domain='source-fused-e3-e4-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-multicast-local-matrix-readiness',operation_variant='28-download1-to-matrix-nonroots-4-already-ready-roots',producer_count=4,waiter_count=28,e3_e4_handoff_work=work if rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=r7_r8_handoff_identity or {};work=R7R8HandoffWork()
        for rank in workload.observed_ep_ranks:
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R7_R8.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R7_R8.local_handoff',component_id='M07',mechanism='sync_r7_r8_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=12288,route_domain='source-fused-r7-r8-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-router-broadcast-local-vector-readiness',operation_variant='28-download1-and-4-upload1-to-vector',producer_count=4,waiter_count=32,r7_r8_handoff_work=work if rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=r9_count_handoff_identity or {};work=R9CountHandoffWork(ep_rank=0)
        for rank in workload.observed_ep_ranks:
            if rank!=0:continue
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.R9_COUNT.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:R9_COUNT.local_handoff',component_id='M07',mechanism='sync_r9_count_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=256,route_domain='source-fused-r9-count-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-filter-local-count-readiness',operation_variant='32-vector-self-acquire',producer_count=32,waiter_count=32,r9_count_handoff_work=R9CountHandoffWork(ep_rank=rank) if rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=count_token_handoff_identity or {};work=CountTokenHandoffWork(ep_rank=0)
        for rank in workload.observed_ep_ranks:
            if rank!=0:continue
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.COUNT_TOKEN.local_handoff',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:COUNT_TOKEN.local_handoff',component_id='M07',mechanism='sync_count_token_local_handoff',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=128,route_domain='source-fused-count-token-local-config-island',requires_physical_time=False,scenario_class='source-exact-post-count-local-token-readiness',operation_variant='32-vector-self-acquire-calc-fence',producer_count=32,waiter_count=32,count_token_handoff_work=work if rank==0 and rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        identity=token_id_fill_identity or {};work=TokenIdFillWork()
        for rank in workload.observed_ep_ranks:
            if rank!=0:continue
            rank_V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.TOKEN_ID.fill',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:TOKEN_ID.fill',component_id='M06',mechanism='vector_token_id_fill',operation=work.operation,profile=work.profile,consumer_stage=work.consumer_stage,shape_key='transformer-fused',V=rank_V,payload_bytes=128,route_domain='source-fused-token-id-fill-local-vector',requires_physical_time=False,scenario_class='source-exact-token-id-fill-body',operation_variant='32PE-int32-CAL-WRITE-noREAD',producer_count=32,waiter_count=32,token_id_fill_work=work if rank==0 and rank_V==64 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        from .router_rank_domains import COMPONENTS,STAGES
        for rank in workload.observed_ep_ranks:
            if rank not in (1,2,3):continue
            V=next(x.valid_selection_count for x in workload.rank_valid_counts if x.ep_rank==rank)
            from collections import Counter
            counts=Counter(x.token_id for x in workload.records if x.ep_rank==rank and x.status=='valid')
            token_counts=tuple(counts.get(token,0) for token in range(32))
            for segment in ('filter_count','count_token','token_fill'):
                w=rank_work(rank,segment);identity=(router_rank_identities or {}).get(f'rank{rank}.{segment}',{})
                requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.router.{segment}',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:router.{segment}',component_id=COMPONENTS[segment],mechanism='router_rank_local',operation=w.operation,profile=w.profile,consumer_stage=STAGES[segment],shape_key=w.profile,V=V,payload_bytes=w.payload_bytes,route_domain=f'source-rank{rank}-{segment}-local',requires_physical_time=False,scenario_class='source-exact-rank-router-local',operation_variant=segment,producer_count=32,waiter_count=32,router_rank_work=w if V==64 and token_counts==(2,)*32 else None,router_rank_token_counts=token_counts,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        from collections import Counter
        for rank in workload.observed_ep_ranks:
            V=next(c.valid_selection_count for c in workload.rank_valid_counts if c.ep_rank==rank)
            counts=Counter(x.token_id for x in workload.records if x.ep_rank==rank and x.status=='valid')
            per_token=tuple(counts.get(p,0) for p in range(32));w=count_work(rank);identity=(count_fill_identities or {}).get(f'rank{rank}',{})
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.count_fill',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:count_fill',component_id='M06',mechanism='vector_count_fill',operation=w.operation,profile=w.profile,consumer_stage='R9.valid_count_init',shape_key=w.profile,V=V,payload_bytes=128,route_domain=f'source-count-fill-rank{rank}-local',requires_physical_time=False,scenario_class='source-count-zero-fill-ready-to-visible',operation_variant='oneCAL-oneWRITE-zeroREAD',producer_count=32,waiter_count=32,count_fill_work=w if V==64 and per_token==(2,)*32 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
    if workload.profile.name=='transformer-fused':
        from collections import Counter
        for rank in workload.observed_ep_ranks:
            V=next(c.valid_selection_count for c in workload.rank_valid_counts if c.ep_rank==rank)
            counts=Counter(x.token_id for x in workload.records if x.ep_rank==rank and x.status=='valid');per_token=tuple(counts.get(p,0) for p in range(32));w=tail_work(rank);identity=(scalar_tail_identities or {}).get(f'rank{rank}',{})
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.scalar_tail',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:scalar_tail',component_id='M07',mechanism='scalar_tail_service',operation=w.operation,profile=w.profile,consumer_stage='R9.scalar_count_tail',shape_key=w.profile,V=V,payload_bytes=128,route_domain=f'source-scalar-tail-rank{rank}-local',requires_physical_time=False,scenario_class='source-scalar-dlcm-tail-store',operation_variant='32-minus-pe-int32-stores',producer_count=32,waiter_count=32,scalar_tail_work=w if V==64 and per_token==(2,)*32 else None,model_library_sha256=identity.get('model_library_sha256'),systemc_library_sha256=identity.get('systemc_library_sha256'),observer_sha256=identity.get('observer_sha256'),counter_callsite=identity.get('counter_callsite')))
            scan=scan_work(rank);scan_identity=(count_scan_identities or {}).get(f'rank{rank}',{})
            requests.append(CalibrationResolutionRequest(request_id=f'rank{rank}.count_scan',workload_sha256=workload.workload_sha256,action_id=f'rank{rank}:count_scan',component_id='M07',mechanism='scalar_count_scan',operation=scan.operation,profile=scan.profile,consumer_stage='R9.scalar_count_scan',shape_key=scan.profile,V=V,payload_bytes=12,route_domain=f'source-count-scan-rank{rank}-local',requires_physical_time=False,scenario_class='source-scalar-sentinel-count-scan',operation_variant='compiled-valid2-path-13instructions-3loads',producer_count=32,waiter_count=32,count_scan_work=scan if V==64 and per_token==(2,)*32 else None,model_library_sha256=scan_identity.get('model_library_sha256'),systemc_library_sha256=scan_identity.get('systemc_library_sha256'),observer_sha256=scan_identity.get('observer_sha256'),counter_callsite=scan_identity.get('counter_callsite'),compiled_path_sha256=scan_identity.get('compiled_path_sha256')))


    return tuple(requests)


def resolution_issues(bundle,workload,binding_sha256,**action_bindings):
    """Resolve caller-bound requests without borrowing candidate identities.

    Optional domains and provider/observer identities are forwarded unchanged
    to workload_action_requests. Resolution is not native execution provenance.
    """
    issues=[]
    for request in workload_action_requests(workload,binding_sha256,**action_bindings):
        try:resolve_calibration(bundle,request)
        except ValueError as exc:issues.append(dict(request_id=request.request_id,action_id=request.action_id,component_id=request.component_id,operation=request.operation,error=str(exc)))
    return issues


def coverage_matrix(bundle):
    rows=[]
    for component in [f'M{i:02d}' for i in range(1,9)]:
        entries=[x for x in bundle.entries if x.component_id==component]
        rows.append(dict(component_id=component,entry_count=len(entries),operations=sorted({x.operation for x in entries}),profiles=sorted({p for x in entries for p in x.profiles}),
          consumer_stages=sorted({s for x in entries for s in x.consumer_stages}),statuses={status:sum(x.status==status for x in entries) for status in ('validated_limited','cpu_only_binding_gate','unavailable')},
          publication_classes=sorted({x.publication_class for x in entries}),duration_units=sorted({x.duration_unit for x in entries}),timebase_statuses=sorted({x.timebase_status for x in entries})))
    return dict(schema_version=1,bundle_sha256=bundle.bundle_sha256,total_entries=len(bundle.entries),components=rows,
      vector_bulk_domains=[dict(entry_id=e.entry_id,work=e.domain.input_dimensions['vector_bulk_work'],consumer_stages=e.consumer_stages,parameter_paths=e.parameter_paths,status=e.status,unit=e.duration_unit,publication_class=e.publication_class) for e in bundle.entries if e.formula_id=='M06-source-bulk-round-r4'],
      production_complete=False,timebase_bundle_sha256=None if bundle.timebases is None else bundle.timebases.bundle_sha256,timebase_domains=[] if bundle.timebases is None else [d.model_dump(mode='json') for d in bundle.timebases.domains],blocking_summary=['accepted domains are limited and do not cover the full real plan','transformer-fused residual preseed R2 and packet/queue R3 once-only validations failed; zero accepted entry','M08 has no calibrated physical timing','physical global timebase and real binding remain external','M07 now includes exact fused E0-to-E1, E2-to-E3 and E4-to-E5 local handoffs plus E1-to-E2 column-root readiness, E3-to-E4 local Matrix readiness, R7-to-R8 local Vector readiness, rank0 R9-filter-to-count-init and count-init-to-token-ID readiness,exact token-ID fill body,independent ranks1..3 router local domains and E7 join; E3 physical multicast remains unbound'])


def gap_contracts(bundle):
    specs=[
      ('C01-C02-full-routes','M01/M02','nine logical profile/stage payload/mask/concurrency domains passed; R0 and standalone R4 failed, R11 excluded by metadata quality gate; all physical production routes remain unbound and therefore zero-match','complete physical Data NoC binding and frozen real route','attest each logical group against supplied link routes, then validate exact bandwidth/latency/FIFO intervention','packet/FIFO/link bandwidth and latency; NO_DIRECT_DATANOC_HARDWARE_INTERVENTION','first source injection to every endpoint completion plus queue waits'),
      ('C03-chain-domain','M03','R7 12288B, E1 81920/131072B and E5 49152/65536B boundary timing validated; native issue/merge/FIFO cost separation, cross-stage contention and real binding remain unavailable','32-PE physical chain/broadcast routes','new independently registered issue/merge/FIFO separation and real cross-stage overlap','per-hop merge, issue, FIFO and root handoff; NO_DIRECT_CHAIN_HARDWARE_INTERVENTION','source issue, every reduce hop, root materialization and broadcast completion'),
      ('C04-gm-domain','M04','Fused R10 exact domains remain accepted. R11 data-readiness calibration/validation passed but is excluded because its terminal was not the source consumer. Two new consumer-completion calibrations both stalled at the same 32-PE weight_gather VECTOR_READ configuration frontier under 300s and 900s limits; no consumer formula, validation, bundle entry or timebase was published. Independent GM/NoC/sync service also remains unavailable.','production GM/write-sum/R11 mapping','expose a completing source weight_gather VECTOR_READ boundary or an authorized equivalent event before any new validation','R11 combined consumer coefficients and independent controller/link/sync parameters unavailable','R10 terminal through R11 transfer and actual first VECTOR_READ consumer completion'),
      ('C04-fused-preseed-r2','M04','source-exact transformer-fused residual preseed calibration separated API return from the eighth CHANNEL1 engine completion, but the frozen production B8192 validation missed by 166 reported ns versus an 8 ns threshold; no accepted entry or timebase was added','no external binding required for the exact logical action; a new model revision would require a new calibration and validation','model the observed non-linear queue/service behavior using calibration-only evidence, then register a new untouched validation scene','four controller-local startup/queue/packet-service parameters; current r2 linear packet rule rejected','four GM_WDMA active begins through max of the eighth CHANNEL1 engine completions'),
      ('C04-fused-preseed-r3','M04','source-backed discrete 128B packet/tail and eight-transaction controller queue stages passed three calibration scenes with zero R2 validation reads, but the frozen new B8192 validation missed by 183.5 reported ns versus an 8 ns threshold; no accepted entry or timebase was added','no external binding required for the exact logical action; a future revision must explain the unobserved production-only delay with new calibration-only evidence','expose the missing packet-internal or cross-controller contention event before any new model revision; do not reuse either failed validation','controller-local admission, full/tail packet service, drain and FIFO gap are modeled; a further production-only delay remains unidentified','matched controller begin, 8 CHANNEL1 transactions and 8 PE UPLOAD1 stages through four-controller max'),
      ('C05-matrix-domain','M05','router source single-invocation/source-feature-shard domains evaluated at route V32/V64; repeated-call V is not real router work. Physical engine/locality/timebase and independent tile service remain unbound; expert gather locality/throughput intervention still absent','real Weight-SRAM/expert binding','router high-V plus expert locality and hardware intervention scenes','named engine throughput/latency and weight locality','four named engine boundaries and output completion'),
      ('C06-vector-domain','M06','historical C06 failed domains stay excluded; source bulk current-buffer V32/V64 entries remain accepted and the exact transformer-fused attn_output_norm H2048/32PE in-place primitive now has named READ/CAL/WRITE overlap plus terminal validation. Other remainders, physical timebase, real buffer binding and independent physical bandwidth remain unavailable','real Vector buffer/resource binding and attested physical timebase','new calibration/validation only for rejected table/SiLU domains or any different attn_output_norm profile/work/provider boundary','exact per-PE attn_output_norm issue/READ/CAL/WRITE/sync-tail reported-ns controls; physical VECTOR bandwidth remains unbound','named engine boundaries, in-place functional output, counter/API handoff, guards and cleanup'),
      ('C07-sync-intervention','M07','transformer-fused V64 has exact local handoffs for E0->E1, E1->E2, E2->E3, E3->E4 and E4->E5. E1->E2 owns only the per-root post-materialization DOWNLOAD1/Vector readiness tail. E2->E3 owns only the post-global-last-Vector local sync/readiness tail; E3->E4 owns only 28 nonroot DOWNLOAD1-completion-to-Matrix-acquire tails; four roots reuse E2/E3 readiness. E3 physical multicast is explicitly excluded. Generic ready/empty evidence and other production outer/comm-id/busy-fence domains remain limited; physical capacity/timebase is uncalibrated','real synchronization resource binding and attested physical timebase; exact E3 physical multicast route remains separate','reuse only the exact E0/E1, E1/E2, E2/E3, E3/E4 or E4/E5 V64/provider/role-map boundary; any other profile/V/handoff needs new evidence','per-PE local handoff tail parameters in reported ns; no physical queue-capacity or E3 Data NoC claim','last named Matrix/Vector engine completion -> matching local acquire/API completion -> 32-PE after-all'),
      ('C07-E7-counted-join','M07','exact transformer-fused E7 count32 comm-id16 domain validated with 448 explicit release-target edges and 14 internal acquire completions; physical clock and E8 collective remain outside this domain','source logical resources are exact; future physical publication still requires attested sync resource/timebase binding','reuse the frozen exact C32/W14/ports4..17/comm-id16 domain only; any different producer count, mask, port set or comm-id needs new evidence','per-ADALINK counted-ready and acquire-consume parameters, simulation reported ns only','PE_UPLOAD1 internal release issues -> per-waiter counted max/consume -> 14-way max completion'),
      ('C08-adalink','M08','prior C08 rank2 validation remains failed; new matched WRITE/WRITE_SUM calibration also lacks link arrival/service-complete, correlated accumulation admission/start/end/work and shared rank timebase/lane mapping; zero timing domain, no formula fit','real CclConfig, rank map, links, ports and chunk schedule','provide correlated receive arrival, link-complete and accumulation start/end/work from the current TSim or an authorized trace API before additional scenes','per-link bandwidth/latency/FIFO and independent accumulation resource','rank/chunk link-complete -> accumulation admission/start/end -> receive counter update'),
      ('C09-timebase','GLOBAL','nine exact simulation-counter domains passed, including new M03 row/M04 write/M05 gate-up selected-node boundaries; global model_cycle/silicon/ADALINK timebase and selected W4A8/mixed peak remain unbound','authorized model-cycle/physical-clock and peak convention','new model-cycle per-resource and cross-rank boundary correspondence with fixed model','physical clock and authoritative BF16/W4A8 peaks; no nominal clock substitution','model-cycle runtime duration to actual primitive boundaries and full decode')]
    contracts=[]
    for identity,component,gap,binding,scene,parameters,boundary in specs:
        status='PASS_LOGICAL_DOMAIN_PHYSICAL_ROUTE_GATE' if identity=='C01-C02-full-routes' else 'PASS_SOURCE_BACKED_EXACT_DOMAIN' if identity in ('C03-chain-domain','C07-E7-counted-join') else 'FAILED_ONCE_ONLY_VALIDATION' if identity in ('C04-fused-preseed-r2','C04-fused-preseed-r3') else 'PARTIAL_PASS_EXACT_DOMAIN' if identity in ('C04-gm-domain','C06-vector-domain') else 'BLOCKED_SEPARATE_SERVICE_OBSERVATION' if identity=='C08-adalink' else 'PLANNED_NOT_RUN'
        raw=dict(contract_id=identity,component=component,status=status,gap=gap,required_real_binding=binding,scene_inputs=scene,parameter_requirements=parameters,
          observation_boundary=boundary,calibration_requirement='at least two disjoint workload points selected before validation',validation_requirement='new frozen scene IDs not used for fitting; each launched once',
          success_conditions=['pre-validation freeze hash intact','functional/guard/byte/participant checks pass','declared component error threshold passes','cleanup complete','no whole-MoE or per-case refit'])
        raw['contract_sha256']=stable_sha256(raw);contracts.append(raw)
    return dict(schema_version=1,bundle_sha256=bundle.bundle_sha256,status='C07_EXPERT_LOCAL_HANDOFFS_ACCEPTED',contracts=contracts,new_tsim_runs=89,new_tsim_runs_scope='prior accounted77 plus fourrankCountScan newcalibration8/once-onlyvalidation4;no oldvalidation reused',
      count_scan_evidence=dict(calibration=8,validation=4,validation_pass=4,accepted=4,retries=0,ranks=[0,1,2,3],old_validation_reused=False,compiledpath=True,physical_publishable=False,status='PASS_EXACT_FOUR_RANK_COUNT_SCAN'),
      scalar_tail_evidence=dict(calibration=8,validation=4,validation_pass=4,accepted=4,retries=0,ranks=[0,1,2,3],old_validation_reused=False,combined_scalar_dlcm_only=True,physical_publishable=False,status='PASS_EXACT_FOUR_RANK_SCALAR_TAIL'),
      scalar_preparation_blocker=dict(status='SCALAR_PREPARATION_UNIQUE_PC_BOUNDARY_UNAVAILABLE',calibration=0,validation=0,accepted=0,missing_event='retired PC uniquely bound to source setup instructions',evidence='c09-scalar-local-r1/freeze/scalar-preparation-blocker.json'),
      e3_e4_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,owned_pe_count=28,root_new_cost=0,payload_bytes=24576,stage_tail_reported_ns=2.,max_error_reported_ns=0.,threshold_reported_ns=8.,simulation_seconds_only=True,physical_publishable=False,physical_route_resolved=False,status='PASS_EXACT_FUSED_E3_E4_LOCAL_HANDOFF'),
      r7_r8_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,payload_bytes=12288,shape=[8,384],stage_tail_reported_ns=2.,max_error_reported_ns=0.,threshold_reported_ns=8.,simulation_seconds_only=True,physical_publishable=False,status='PASS_EXACT_FUSED_R7_R8_LOCAL_HANDOFF'),
      r9_count_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,accepted=1,retries=0,rank_scope=[0],V=64,pe_count=32,payload_bytes=256,stage_tail_reported_ns=2.,max_error_reported_ns=0.,threshold_reported_ns=8.,simulation_seconds_only=True,status='PASS_EXACT_FUSED_R9_COUNT_LOCAL_HANDOFF'),
      count_token_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,accepted=1,retries=0,rank_scope=[0],V=64,pe_count=32,payload_bytes=128,stage_tail_reported_ns=49.00000000000091,max_error_reported_ns=9.094947017729282e-13,threshold_reported_ns=8.,simulation_seconds_only=True,status='PASS_EXACT_FUSED_COUNT_TOKEN_LOCAL_HANDOFF'),
      token_id_fill_evidence=dict(calibration=2,validation=1,validation_pass=1,accepted=1,retries=0,rank_scope=[0],V=64,pe_count=32,elements=32,payload_bytes=128,stage_reported_ns=121.,max_error_reported_ns=9.094947017729282e-13,threshold_reported_ns=8.,simulation_seconds_only=True,status='PASS_EXACT_FUSED_TOKEN_ID_FILL_LOCAL_HANDOFF'),
      e1_e2_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,roots=[28,29,30,31],payload_bytes_per_root=131072,stage_tail_reported_ns=3.5,max_error_reported_ns=9.094947017729282e-13,threshold_reported_ns=8.,simulation_seconds_only=True,physical_publishable=False,status='PASS_EXACT_FUSED_E1_E2_LOCAL_HANDOFF'),
      e2_e3_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,vector_participants=[28,29,30,31],payload_bytes_per_root=24576,total_payload_bytes=98304,stage_tail_reported_ns=2.0,max_error_reported_ns=0.0,threshold_reported_ns=8.0,simulation_seconds_only=True,physical_publishable=False,e3_physical_route_resolved=False,status='PASS_EXACT_FUSED_E2_E3_LOCAL_HANDOFF'),
      e0_e1_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,upload_waiters=28,download_waiters=4,output_bytes_per_pe=131072,max_tail_reported_ns=44.00000000000091,max_error_reported_ns=1.8189894035458565e-12,threshold_reported_ns=8.0,simulation_seconds_only=True,physical_publishable=False,status='PASS_EXACT_FUSED_E0_E1_HANDOFF'),
      e4_e5_handoff_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',V=64,pe_count=32,upload_waiters=24,download_waiters=8,output_bytes_per_pe=65536,max_tail_reported_ns=54.00000000000091,max_error_reported_ns=1.8189894035458565e-12,threshold_reported_ns=8.0,simulation_seconds_only=True,physical_publishable=False,status='PASS_EXACT_FUSED_E4_E5_HANDOFF'),
      attn_output_norm_evidence=dict(calibration=2,validation=1,validation_pass=1,validation_fail=0,accepted=1,retries=0,profile='transformer-fused',hidden_elements_per_pe=2048,pe_count=32,activation_bytes_per_pe=8192,weight_bytes_per_pe=4096,output_bytes_per_pe=8192,max_error_reported_ns=4.547473508864641e-13,threshold_reported_ns=8.0,simulation_seconds_only=True,physical_publishable=False,status='PASS_EXACT_FUSED_ATTN_OUTPUT_NORM'),
      e7_join_evidence=dict(staging_prelaunch_failures=4,r3_diagnostic_calibration=2,r4_calibration=2,r4_validation=1,r4_validation_pass=1,r4_validation_fail=0,accepted=1,retries=0,producer_count=32,waiter_count=14,release_edges=448,comm_id=16,max_error_reported_ns=4.547473508864641e-13,threshold_reported_ns=8.0,physical_publishable=False,status='PASS_EXACT_FUSED_E7_JOIN'),
      fused_preseed_r3_evidence=dict(calibration=3,validation=1,validation_pass=0,validation_fail=1,accepted=0,retries=0,packet_bytes=128,calibration_payloads=[8064,8068,8320],production_payload=8192,max_controller_error_reported_ns=183.49999999999955,terminal_error_reported_ns=179.99999999999955,threshold_reported_ns=8.0,r2_validation_numeric_values_read=False,function_guard_source_cleanup_identity_timebase_pass=True,status='FAILED_ONCE_ONLY_VALIDATION'),
      fused_preseed_evidence=dict(r1_calibration=2,r1_validation=0,r1_excluded=True,r2_calibration=2,r2_validation=1,r2_validation_pass=0,r2_validation_fail=1,accepted=0,retries=0,max_controller_error_reported_ns=166.0,terminal_error_reported_ns=160.99999999999977,threshold_reported_ns=8.0,function_guard_cleanup_identity_timebase_pass=True,existing_entries_modified=0,status='FAILED_ONCE_ONLY_VALIDATION'),
      r10_hook_producer_evidence=dict(producer_implemented=True,matcher_implemented=True,static_passivity=True,required_upstream_callbacks=3,available_upstream_callbacks=0,registration_export=False,exact_interposer=False,capability_probe=0,calibration=0,validation=0,accepted=0,status='UPSTREAM_TSIM_HOOK_UNAVAILABLE'),
      r10_r3_handoff_evidence=dict(required_classes=4,fully_identity_bound=0,partial_fifo_ordinal=2,hook_required=2,calibration_registered=0,calibration_executed=0,validation_registered=0,validation_executed=0,accepted=0,component_refit=False,black_box_cost=False,status='HOOK_REQUIRED'),
      r10_r2_composition_evidence=dict(calibration=1,function_pass=1,predicted_terminal=3900,observed_terminal=3847,error_counter=53,threshold_counter=6,validation_registered=1,validation_executed=0,accepted=0,component_refit=False,black_box_cost=False,missing_async_handoff_events=4,status='CALIBRATION_FAILED'),
      r10_full_composition_evidence=dict(calibration=1,function_pass=1,predicted_component_counter=[96,3718,697],actual_component_counter=[127,3633,217],predicted_total_counter=4511,actual_total_counter=3994,total_error_counter=517,total_error_threshold=6,validation_registered=1,validation_executed=0,accepted=0,component_refit=False,black_box_cost=False,status='FAILED_COMPONENT_COMPOSITION_GATE'),
      r11_stage_evidence=dict(readiness_calibration=2,readiness_validation=1,readiness_validation_pass=1,readiness_accepted=0,consumer_calibration_attempted=2,consumer_completion_pass=0,consumer_timeouts=[300,900],consumer_validation=0,accepted=0,retries=0,physical_publishable=False,independent_GM_NoC_service=False,status='BLOCKED_SOURCE_VECTOR_READ_CONSUMER'),
      r10_stage_evidence=dict(calibration=4,validation=2,validation_pass=2,validation_fail=0,accepted=2,retries=0,physical_publishable=False,internal_cost_separation=False),
      c04_write_sum_evidence=dict(calibration=4,validation=2,validation_pass=1,validation_fail=1,accepted=1,retries=0,combined_primitive_only=True,atomic_internal_service_separable=False,physical_publishable=False),
      gm_source_evidence=dict(calibration_registered=14,calibration_executed=10,function_pass=10,validation_registered=4,validation_executed=0,accepted=0,formula_fit=False,status='BLOCKED_INDEPENDENT_GM_SERVICE_OBSERVABILITY'),
      c06_bulk_evidence=dict(calibration=16,validation=4,accepted=sum(e.formula_id=='M06-source-bulk-round-r4' for e in bundle.entries),physical_publishable=False),
      router_source_evidence=dict(r2_calibration=8,r2_validation=4,r2_production_pass=0,r3_calibration=4,r3_validation=2,physical_publishable=False),
      c09_evidence=dict(calibration=4,validation=2,validation_pass=2,accepted_timebase_domains=2,physical_publishable=False,global_model_cycle_bound=False),
      c08_r2_evidence=dict(calibration_registered=4,calibration_attempted=2,function_pass=2,identification_pass=0,validation_registered=2,validation_attempted=0,accepted=0,formula_fit=False,status='BLOCKED_LINK_ACCUMULATION_OBSERVABILITY'),
      c08_evidence=dict(calibration_attempted=4,calibration_pass=4,validation_attempted=2,validation_pass=0,validation_fail=2,accepted=0,retries=0,max_observed_error_reported_ns=3.7142857142857153),
      c07_evidence=dict(calibration_attempted=52,calibration_pass=36,calibration_fail=16,validation_attempted=26,raw_validation_pass=14,raw_validation_fail=12,accepted=8,excluded=18,retries=0,physical_publishable=False),
      c0102_evidence=dict(calibration_attempted=24,validation_attempted=13,raw_validation_pass=11,accepted=9,excluded=4,retries=0,physical_route_ready=False),
      c03_evidence=dict(calibration_attempted=18,validation_attempted=18,validation_pass=18,validation_fail=0,retries=0),
      c04_evidence=dict(calibration_attempted=19,validation_attempted=10,validation_pass=3,validation_fail=7,retries=0),
      c06_evidence=dict(calibration_attempted=40,calibration_pass=38,calibration_fail=2,validation_attempted=50,validation_pass=37,validation_fail=13,retries=0))


__all__=['ActionDomain','CalibrationResolution','CalibrationResolutionRequest','ComponentCalibrationBundle','ComponentCalibrationEntry','coverage_matrix','build_component_calibration_bundle','gap_contracts','resolution_issues','resolve_calibration']
