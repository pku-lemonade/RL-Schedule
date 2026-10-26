"""Source-backed production input consolidation, without real-input substitution.

Source facts and accepted component evidence are formal partial inputs. A group
is acquired only when its exact contract value is independent of missing route
or binding. Current composition explicitly excludes static whole-operation costs.
"""
from pathlib import Path
from typing import Literal
import ast,hashlib,json,re,subprocess
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES,stable_sha256
from .input_acquisition import InputAcquisitionInventory
from .private_inputs import merge_static_binding,PhysicalBindingArtifact,WriteSumMappingArtifact,AdalinkBindingArtifact
from .component_calibration import ComponentCalibrationBundle

BASE='98bafeae5b4cff4eae6d16166bb629e88448b223'
KERNEL='094bcd25e985a4c836fd5521cccd61b14d911aac'
STAGES=tuple([f'R{i}' for i in range(13)]+[f'E{i}' for i in range(9)])

class SourceInputReference(FrozenRecord):
    path:str
    repository:Literal['T81','adas_lp_kernels']
    commit:str=Field(pattern=r'^[0-9a-f]{40}$')
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')

class NormalizedProductionInput(FrozenRecord):
    schema_version:Literal[1]=1
    input_id:str
    kind:Literal['system_version','source_profiles','logical_stage_contract','logical_resources','whole_operation_cost_policy','component_calibration_bundle','partial_physical_binding','partial_write_sum_mapping','partial_adalink_binding','timebase_inventory','peak_inventory']
    status:Literal['complete_contract_value','partial_source_input','accepted_evidence_limited']
    profile_scope:tuple[str,...]
    selection_commit:str=Field(pattern=r'^[0-9a-f]{40}$')
    kernel_commit:Literal[KERNEL]=KERNEL
    data:dict
    source_refs:tuple[SourceInputReference,...]
    production_complete:Literal[False]=False
    artifact_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def integrity(self):
        if not self.input_id or not self.source_refs or len(set(r.path for r in self.source_refs))!=len(self.source_refs):raise ValueError('normalized input identity/source references invalid')
        if set(self.profile_scope)!=set(PROFILES):raise ValueError('normalized input must explicitly cover both source profiles')
        if any(r.repository=='T81' and r.commit!=self.selection_commit or r.repository=='adas_lp_kernels' and r.commit!=KERNEL for r in self.source_refs):raise ValueError('stale source selection commit')
        if self.kind=='whole_operation_cost_policy' and self.data!=dict(operation_costs=[],allowed_transaction_kind='live_composed_transactions',whole_operation_black_box_allowed=False):raise ValueError('fixed whole-operation cost forbidden')
        if stable_sha256(self.model_dump(mode='json',exclude={'artifact_sha256'}))!=self.artifact_sha256:raise ValueError('normalized input hash drift')
        return self

def freeze_source_input(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(dict(schema_version=1,kernel_commit=KERNEL,production_complete=False,**kwargs),mode='json');raw['artifact_sha256']=stable_sha256(raw)
    return NormalizedProductionInput.model_validate_json(json.dumps(raw))

def checked_source(root,relative,commit,repository):
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root) or path.is_symlink():raise ValueError('source outside explicit repository')
    # Read exactly the requested fixed source, never referenced private paths.
    frozen=subprocess.check_output(['git','show',commit+':'+relative],cwd=root)
    current=path.read_bytes()
    if current!=frozen:raise ValueError('source content drift:'+relative)
    return SourceInputReference(path=str(path),repository=repository,commit=commit,sha256=hashlib.sha256(current).hexdigest())

def verify_source_input(artifact,t81_root,adas_root,expected_commit,*,historical=False):
    artifact=NormalizedProductionInput.model_validate_json(artifact.model_dump_json())
    if artifact.selection_commit!=expected_commit:raise ValueError('stale normalized input commit')
    for ref in artifact.source_refs:
        root=Path(t81_root if ref.repository=='T81' else adas_root).resolve();path=Path(ref.path).resolve()
        if not path.is_relative_to(root):raise ValueError('source reference outside authorized repository')
        if historical and ref.repository=='T81':
            value=subprocess.check_output(['git','show',ref.commit+':'+str(path.relative_to(root))],cwd=root)
            actual=SourceInputReference(path=ref.path,repository=ref.repository,commit=ref.commit,sha256=hashlib.sha256(value).hexdigest())
        else:actual=checked_source(root,str(path.relative_to(root)),ref.commit,ref.repository)
        if actual.sha256!=ref.sha256:raise ValueError('source hash drift')
    return artifact

def source_static_inventory(t81_root,adas_root,selection_commit):
    t81_root=Path(t81_root);adas_root=Path(adas_root)
    # Existing T81 extraction supplies logical mappings; it does not supply routes.
    old=json.loads((t81_root/'phase3/private-input-acquisition/static-extraction.json').read_text())
    refs=[checked_source(t81_root,'phase3/private-input-acquisition/static-extraction.json',selection_commit,'T81')]
    for name in ('kernels/cpp/moe/define.hpp','kernels/cpp/moe/router_gemm.hpp','kernels/cpp/moe/moe_gemm.hpp','kernels/cpp/moe/test_moe.py','kernels/cpp/transformer_layer_pp_nlayer/define.hpp','kernels/cpp/transformer_layer_pp_nlayer/moe/router_gemm.hpp','kernels/cpp/transformer_layer_pp_nlayer/moe/moe_gemm.hpp'):
        refs.append(checked_source(adas_root,name,KERNEL,'adas_lp_kernels'))
    if old['adas_commit']!=KERNEL or old['simulator_commit']!=BASE:raise ValueError('static extraction identity conflict')
    for oldref in old['source_files']:
        if oldref['path'].startswith(str(adas_root.resolve())+'/'):
            actual=next((r.sha256 for r in refs if r.path==oldref['path']),None)
            if actual is None or actual!=oldref['sha256']:raise ValueError('old static source hash drift')
    profiles={p:v.model_dump(mode='json') for p,v in PROFILES.items()}
    if profiles!=old['profiles']:raise ValueError('profile conflict with frozen extraction')
    facts={k:old[k] for k in ('roles','pe_engine_roles','gsram','logical_multicast_groups','logical_chain_groups')}
    facts.update(schema_version=1,adas_commit=KERNEL,simulator_commit=BASE,profiles=profiles,logical_routes=[],physical_links=[],physical_routes=[],multicast_trees=[],complete_physical_binding=False,source_files=[dict(path=r.path,sha256=r.sha256) for r in refs],no_coordinate_route_inference=True)
    facts['static_facts_sha256']=stable_sha256(facts)
    return facts,tuple(refs)

def source_stage_contract(root,commit):
    root=Path(root);ref=checked_source(root,'public-patch/t81-overlay/simulator_detailed/moe_model/operation_plan.py',commit,'T81');text=Path(ref.path).read_text();tree=ast.parse(text);rows=[]
    for call in ast.walk(tree):
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or call.func.id!='add' or len(call.args)<4:continue
        stage=ast.literal_eval(call.args[0])
        if stage not in STAGES:continue
        rows.append(dict(stage_id=stage,label=ast.literal_eval(call.args[1]),component=ast.literal_eval(call.args[2]),resource_kind=ast.literal_eval(call.args[3]),dependencies=list(ast.literal_eval(call.args[4])) if len(call.args)>4 else [],participants_expression=ast.unparse(call.args[5]) if len(call.args)>5 else None,source_bytes_expression=ast.unparse(call.args[6]) if len(call.args)>6 else '0',endpoint_bytes_expression=ast.unparse(call.args[7]) if len(call.args)>7 else '0',attributes={k.arg:ast.unparse(k.value) for k in call.keywords}))
    rows.sort(key=lambda r:STAGES.index(r['stage_id']));known=set()
    if tuple(r['stage_id'] for r in rows)!=STAGES:raise ValueError('stage contract inventory mismatch')
    for row in rows:
        if not set(row['dependencies'])<=known:raise ValueError('stage dependency not topological')
        known.add(row['stage_id'])
    return dict(stages=rows,V=None,V_source='real post-EP-filter frozen route only',source_variables=[ast.unparse(n) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_operation_plan' for n in n.body if isinstance(n,ast.Assign)],physical_bindings=None),ref

def validate_normalized_set(artifacts,bundle):
    artifacts=tuple(NormalizedProductionInput.model_validate_json(a.model_dump_json()) for a in artifacts);bundle=ComponentCalibrationBundle.model_validate_json(bundle.model_dump_json())
    kinds=[a.kind for a in artifacts]
    if len(set(kinds))!=len(kinds) or len({a.input_id for a in artifacts})!=len(artifacts):raise ValueError('duplicate/conflicting normalized input')
    if len({a.selection_commit for a in artifacts})!=1:raise ValueError('inconsistent selection commit')
    by={a.kind:a for a in artifacts}
    if by['source_profiles'].data['profiles']!={p:v.model_dump(mode='json') for p,v in PROFILES.items()}:raise ValueError('source profile shape drift')
    rows=by['logical_stage_contract'].data['stages'];stages={r['stage_id']:r for r in rows}
    if tuple(stages)!=STAGES:raise ValueError('stage inventory inconsistent')
    known=set()
    for row in rows:
        if len(row['dependencies'])!=len(set(row['dependencies'])) or not set(row['dependencies'])<=known:raise ValueError('stage dependency conflict/cycle')
        known.add(row['stage_id'])
    for entry in bundle.entries:
        for p in entry.profiles:
            if p not in PROFILES:raise ValueError('bundle profile unknown')
        if not set(entry.consumer_stages)<=set(stages):raise ValueError('bundle stage outside source DAG')
        if any(entry.component_id not in stages[s]['component'].split('/') for s in entry.consumer_stages):
            # Source stage labels may name the dominant mechanism; the current
            # resolver's mapping explicitly includes co-resident GM/sync actions.
            if entry.component_id not in ('M04','M07'):raise ValueError('bundle component/stage mismatch')
    source_stage,source_ref=source_stage_contract(Path(by['logical_stage_contract'].source_refs[0].path).parents[4],by['logical_stage_contract'].selection_commit)
    if source_stage!=by['logical_stage_contract'].data:raise ValueError('stage formula/shape/payload drift from fixed source')
    selected=by['component_calibration_bundle'].data
    if selected['bundle_sha256']!=bundle.bundle_sha256 or selected['entry_ids']!=[e.entry_id for e in bundle.entries]:raise ValueError('component selection differs from current bundle')
    return True

def require_live_operation_costs(document):
    if document.get('operation_costs')!=[] or document.get('whole_operation_black_box_allowed') is not False:raise ValueError('explicit unused whole-operation cost policy required')
    def walk(value):
        if isinstance(value,dict):
            if value.get('kind')=='whole_operation' or 'operation_cost' in value:raise ValueError('production static whole-operation cost forbidden')
            for x in value.values():walk(x)
        elif isinstance(value,(list,tuple)):
            for x in value:walk(x)
    walk(document.get('system_spec',{}));return True

def require_production_action_provenance(system_spec):
    """Reject raw specs until deterministic, per-native-action lowering exists.

    Abstract calibration resolution and caller-authored metadata cannot prove
    that the executed formulas, work, units, resources and dependencies match
    accepted entries. No production provenance format is currently supported.
    """
    raise ValueError('production_action_provenance_unavailable: deterministic lowering with exact per-native-action formula/work/unit/resource/dependency proof is required')

def validate_partial_consistency(binding=None,write_sum=None,adalink=None,route=None,hardware=None,bundle=None):
    """Validate supplied schemas together; partial values never waive readiness."""
    from .route_capture import import_frozen_route
    if binding is not None:binding=PhysicalBindingArtifact.model_validate_json(binding.model_dump_json())
    if write_sum is not None:write_sum=WriteSumMappingArtifact.model_validate_json(write_sum.model_dump_json())
    if adalink is not None:adalink=AdalinkBindingArtifact.model_validate_json(adalink.model_dump_json())
    if bundle is not None:bundle=ComponentCalibrationBundle.model_validate_json(bundle.model_dump_json())
    if route is not None:
        route=import_frozen_route(route.model_dump_json(),require_real=True)
        if route.identity.source_commit!=KERNEL:raise ValueError('production route uses stale kernel source commit')
        if route.profile!=PROFILES[route.profile.name]:raise ValueError('production route profile differs from fixed source')
        if adalink is not None and adalink.profile!=route.profile.name:raise ValueError('ADALINK/workload profile mismatch')
    graph=None if binding is None or binding.system_spec is None else binding.system_spec['graph']
    if graph is not None:
        resource_ids={e['endpoint_id'] for e in graph.get('dma_endpoints',[])}|{e['unit_id'] for e in graph.get('execution_units',[])}|{e['resource_id'] for e in graph.get('memory_resources',[])}|{e['link_id'] for e in graph.get('links',[])}
        if hardware is not None:
            for p in hardware.parameters:
                if p.resource_id is not None and p.resource_id not in resource_ids:raise ValueError('hardware resource not present in supplied binding:'+p.path)
        if write_sum is not None and write_sum.complete:
            roles={r.logical_role:r.resource_id for r in binding.roles}
            if any(c.controller_id not in roles or roles[c.controller_id] not in resource_ids for c in write_sum.controllers):raise ValueError('write-sum controller identity differs from physical binding')
    return dict(consistency='PASS_PARTIAL_CONTRACTS',production_ready=False,physical_resource_check='performed' if graph is not None else 'unavailable')

def verify_consolidated_directory(directory,t81_root,adas_root,expected_selection_commit=None):
    """Current evidence identity is explicit; older selected files are rejected."""
    directory=Path(directory);manifest=json.loads((directory/'normalized-input-set.json').read_text())
    if expected_selection_commit is not None and manifest['selection_commit']!=expected_selection_commit:raise ValueError('stale input-set selection commit')
    if manifest['kernel_commit']!=KERNEL:raise ValueError('consolidation kernel commit mismatch')
    artifacts=[]
    for relative in manifest['artifact_paths']:
        p=(directory/relative).resolve()
        if not p.is_relative_to(directory.resolve()):raise ValueError('normalized artifact escapes input directory')
        artifact=NormalizedProductionInput.model_validate_json(p.read_text());verify_source_input(artifact,t81_root,adas_root,manifest['selection_commit'],historical=expected_selection_commit is None);artifacts.append(artifact)
    bundle=ComponentCalibrationBundle.model_validate_json((directory/'normalized-inputs/component-calibration-bundle.json').read_text())
    validate_normalized_set(artifacts,bundle)
    original_bundle=ComponentCalibrationBundle.model_validate_json(subprocess.check_output(['git','show',manifest['selection_commit']+':component-calibration/component-calibration-bundle.json'],cwd=t81_root))
    if bundle!=original_bundle:raise ValueError('normalized component bundle differs from selected committed bundle')
    by_kind={a.kind:a for a in artifacts}
    for name,kind,cls in (('physical-binding.json','partial_physical_binding',PhysicalBindingArtifact),('write-sum-mapping.json','partial_write_sum_mapping',WriteSumMappingArtifact)):
        parsed=cls.model_validate_json((directory/'normalized-inputs'/name).read_text())
        if parsed.model_dump(mode='json')!=by_kind[kind].data:raise ValueError('normalized imported binding differs from source artifact:'+name)
    inventory=InputAcquisitionInventory.model_validate_json((directory/'group-classification.json').read_text());groups={r.group_id:r for r in inventory.groups};by={a.kind:a for a in artifacts}
    expected={'SYSTEM_VERSION':by['system_version'].data['system_version'],'WHOLE_OPERATION_COST':[]}
    for key,value in expected.items():
        if groups[key].field_state!='ACQUIRED' or groups[key].acquired_value_sha256!=stable_sha256(value):raise ValueError('acquired group value differs from normalized evidence:'+key)
    normalized_inventory=InputAcquisitionInventory.model_validate_json((directory/'normalized-inputs/group-classification.json').read_text())
    if normalized_inventory!=inventory:raise ValueError('production inventory differs from normalized input-set')
    policy=json.loads((directory/'normalized-inputs/operation-cost-policy.json').read_text());require_live_operation_costs(policy)
    if policy.get('policy_artifact_sha256')!=by['whole_operation_cost_policy'].artifact_sha256:raise ValueError('whole-operation policy artifact identity drift')
    if set(r.group_id for r in inventory.groups if r.field_state=='ACQUIRED')!=set(expected):raise ValueError('unexpected complete group without actual production input')
    return dict(status='PASS_NORMALIZED_INPUT_CONSISTENCY',complete_groups=inventory.complete_group_count,artifacts=len(artifacts),source_selection_commit=manifest['selection_commit'],bundle_sha256=bundle.bundle_sha256)
