"""Phase-3 private-input acquisition contracts and deterministic static facts."""
from __future__ import annotations
from pathlib import Path
from typing import Literal
import hashlib,json,re,subprocess
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES,stable_sha256

AcquisitionClass=Literal['EXISTING_MATERIAL','DETERMINISTIC_EXTRACTION','RUNTIME_CAPTURE_REQUIRED','EXTERNAL_REQUIRED']
FieldState=Literal['ACQUIRED','PROVEN_PARTIAL','TOOL_READY_VALUE_MISSING','MISSING']

class InputGroupAssessment(FrozenRecord):
    index: int = Field(ge=1,le=33)
    group_id: str
    category: str
    schema_field: str
    field_type: str
    unit: str
    owner: str
    acquisition_class: AcquisitionClass
    field_state: FieldState
    exact_fields: tuple[str,...]
    source_refs: tuple[str,...]
    acquisition_method: str
    completeness_rule: str
    closure_impact: str
    acquired_value_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def evidence(self):
        if not self.group_id or not self.source_refs or not self.exact_fields:raise ValueError('group evidence and exact fields required')
        if (self.field_state=='ACQUIRED')!=(self.acquired_value_sha256 is not None):raise ValueError('acquired group requires value hash only')
        return self

class InputAcquisitionInventory(FrozenRecord):
    schema_version: int=1
    inventory_id: str
    groups: tuple[InputGroupAssessment,...]
    complete_group_count: int
    tools_ready_group_count: int
    inventory_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if len(self.groups)!=33 or tuple(x.index for x in self.groups)!=tuple(range(1,34)) or len({x.group_id for x in self.groups})!=33:raise ValueError('exact 33-group inventory required')
        if self.complete_group_count!=sum(x.field_state=='ACQUIRED' for x in self.groups):raise ValueError('complete count mismatch')
        if self.tools_ready_group_count!=sum(x.field_state in ('ACQUIRED','PROVEN_PARTIAL','TOOL_READY_VALUE_MISSING') for x in self.groups):raise ValueError('tool-ready count mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'inventory_sha256'}))!=self.inventory_sha256:raise ValueError('inventory hash mismatch')
        return self

EXTERNAL={'AUTH_COMPLETE','AUTH_REAL','SYSTEM_INVENTORY','PARAMETER_SOURCE_MAP','NETWORK_TIMING','BYTES_PER_CYCLE','HOP_CYCLES','CREDIT_RETURN_CYCLES','BUFFER_SLOTS','MAX_CYCLES','TIMEBASE','CAPACITY','MEMORY_TIMING','HOST_BUDGET'}
RUNTIME={'WORKLOAD_VERSION','WORKLOAD_INVENTORY','TRANSACTIONS','START_CYCLES','PAYLOAD_BYTES'}
EXISTING={'SYSTEM_VERSION','DURATION_CYCLES'}

def _sources(group,simulator_commit):
    if group=='SYSTEM_VERSION':return ('/home/tiger/progress/local-git/ada-simulator-patch@'+simulator_commit,)
    if group in {'WORKLOAD_VERSION','WORKLOAD_INVENTORY','TRANSACTIONS','START_CYCLES','PAYLOAD_BYTES'}:
        return ('/home/tiger/adas_lp_kernels@094bcd25e985a4c836fd5521cccd61b14d911aac','phase3/private-input-acquisition/capture_route_trace.py')
    if group in {'DEPENDENCIES','COMPLETION','FENCES','SYNC'}:
        return ('public-patch/t81-overlay/simulator_detailed/moe_model/operation_plan.py','public-patch/t81-overlay/simulator_detailed/moe_model/sync.py','/home/tiger/progress/T79-moe-operation-decomposition-20261019/moe_operation_decomposition.json')
    if group=='DURATION_CYCLES':return ('phase2a/result.json','phase2b/result.json','phase2c/result.json','phase2d/result.json')
    if group in {'BUFFERS','ADDRESS_MAPS','ALIASES','STORAGE_EDGES','QUEUES','LIFECYCLE','ORDER_VISIBILITY'}:
        return ('/home/tiger/adas_lp_kernels/kernels/cpp/moe/define.hpp','/home/tiger/adas_lp_kernels/kernels/cpp/transformer_layer_pp_nlayer/define.hpp','public-patch/t81-overlay/simulator_detailed/moe_model/private_inputs.py')
    if group=='WHOLE_OPERATION_COST':return ('public-patch/t81-overlay/simulator_detailed/moe_model/costs.py','phase3/private-input-acquisition/import_inputs.py')
    return ('/home/tiger/progress/adas-simulator-private-input-binding/MISSING-INPUT-CONTRACT.json','phase3/private-input-acquisition/import_inputs.py')

def _impact(group):
    if group in {'AUTH_COMPLETE','AUTH_REAL','SYSTEM_VERSION','SYSTEM_INVENTORY','PARAMETER_SOURCE_MAP'}:return 'Blocks admission of an authorized current physical SystemSpec.'
    if group in {'WORKLOAD_VERSION','WORKLOAD_INVENTORY','TRANSACTIONS','DEPENDENCIES','START_CYCLES','PAYLOAD_BYTES','DURATION_CYCLES','MAX_CYCLES'}:return 'Blocks freezing and executing the exact real MoE transaction DAG.'
    if group in {'NETWORK_TIMING','BYTES_PER_CYCLE','HOP_CYCLES','CREDIT_RETURN_CYCLES','BUFFER_SLOTS','TIMEBASE'}:return 'Blocks current-architecture Data NoC timing and parameter counterfactuals.'
    if group in {'CAPACITY','MEMORY_TIMING','BUFFERS','ADDRESS_MAPS','ALIASES','STORAGE_EDGES','QUEUES','LIFECYCLE','ORDER_VISIBILITY'}:return 'Blocks physical GSRAM/GM service, address-conflict and lifecycle closure.'
    if group in {'COMPLETION','FENCES','SYNC'}:return 'Blocks causal completion and critical-path correctness.'
    if group=='HOST_BUDGET':return 'Blocks a bounded production execution contract.'
    return 'Blocks explicit component cost composition without a black-box total.'

def classify_groups(contract,simulator_commit):
    rows=[]
    for index,row in enumerate(contract['missing'],1):
        group=row['missing_id']
        cls='EXTERNAL_REQUIRED' if group in EXTERNAL else 'RUNTIME_CAPTURE_REQUIRED' if group in RUNTIME else 'EXISTING_MATERIAL' if group in EXISTING else 'DETERMINISTIC_EXTRACTION'
        state='ACQUIRED' if group=='SYSTEM_VERSION' else 'PROVEN_PARTIAL' if group in {'DURATION_CYCLES','DEPENDENCIES','BUFFERS','ADDRESS_MAPS','LIFECYCLE','COMPLETION','FENCES','SYNC','WHOLE_OPERATION_COST'} else 'TOOL_READY_VALUE_MISSING'
        source=_sources(group,simulator_commit)
        method={
          'EXTERNAL_REQUIRED':'Import an authorized source-owner document; no default or inference is permitted.',
          'RUNTIME_CAPTURE_REQUIRED':'Run or import the bound real decode workload, then derive this group from frozen records.',
          'DETERMINISTIC_EXTRACTION':'Derive from the frozen workload, operation DAG and supplied physical graph; reject unresolved references.',
          'EXISTING_MATERIAL':'Bind the existing fixed source/component evidence to the final imported document.'}[cls]
        value_hash=stable_sha256('ada-simulator-patch@'+simulator_commit) if group=='SYSTEM_VERSION' else None
        rows.append(InputGroupAssessment(index=index,group_id=group,category=row['category'],schema_field=row['field'],field_type=row['type'],unit=row['unit'],owner=row['source'],
          acquisition_class=cls,field_state=state,exact_fields=(row['field'],),source_refs=source,acquisition_method=method,
          completeness_rule=row['minimum_provision']+' '+row['validator'],closure_impact=_impact(group),acquired_value_sha256=value_hash))
    raw=dict(schema_version=1,inventory_id='T81-phase3-private-input-acquisition-v1',groups=tuple(rows),complete_group_count=sum(x.field_state=='ACQUIRED' for x in rows),
      tools_ready_group_count=sum(x.field_state in ('ACQUIRED','PROVEN_PARTIAL','TOOL_READY_VALUE_MISSING') for x in rows))
    from pydantic import TypeAdapter
    frozen=TypeAdapter(dict).dump_python(raw,mode='json')
    return InputAcquisitionInventory(**raw,inventory_sha256=stable_sha256(frozen))

def _head(path):
    result=subprocess.run(['git','rev-parse','HEAD'],cwd=path,text=True,capture_output=True,check=True)
    return result.stdout.strip()

def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _constant(text,name):
    match=re.search(rf'constexpr\s+int\s+{re.escape(name)}\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;',text)
    if not match:raise ValueError('missing fixed source constant '+name)
    return int(match.group(1),0)

def extract_static_moe_facts(adas_root,simulator_root,t79_json):
    adas_root=Path(adas_root);simulator_root=Path(simulator_root);stand=adas_root/'kernels/cpp/moe/define.hpp';test=adas_root/'kernels/cpp/moe/test_moe.py'
    fused=adas_root/'kernels/cpp/transformer_layer_pp_nlayer/define.hpp';router=adas_root/'kernels/cpp/moe/router_gemm.hpp';t79=Path(t79_json)
    adas_head=_head(adas_root);sim_head=_head(simulator_root)
    if adas_head!='094bcd25e985a4c836fd5521cccd61b14d911aac':raise ValueError('adas source commit mismatch')
    if sim_head!='98bafeae5b4cff4eae6d16166bb629e88448b223':raise ValueError('simulator source commit mismatch')
    text=stand.read_text();launch=test.read_text();profile={k:v.model_dump(mode='json') for k,v in PROFILES.items()}
    if (_constant(text,'PE_NUM'),_constant(text,'TOKEN_NUM'),_constant(text,'EXPERT_NUM'),_constant(text,'TOPK'))!=(32,32,384,8):raise ValueError('source profile constants changed')
    for token,count in (('adaUnitType.PE, 0, 32',1),('adaUnitType.GSRM_LD, 0, 4',1),('adaUnitType.GSRM_ST, 0, 4',1)):
        if launch.count(token)<count:raise ValueError('launch role evidence missing')
    roles=[dict(logical_role=f'PE{i}',kind='pe',source_scope='source logical node_id',physical_endpoint_id=None) for i in range(32)]
    roles += [dict(logical_role=f'GM_RDMA{i}',kind='gm_rdma',source_scope='GSRM_LD logical node_id',physical_endpoint_id=None) for i in range(4)]
    roles += [dict(logical_role=f'GM_WDMA{i}',kind='gm_wdma',source_scope='GSRM_ST logical node_id',physical_endpoint_id=None) for i in range(4)]
    roles += [dict(logical_role='GSRAM',kind='gsram',source_scope='GSRAM_ADDR and GlobalSramLayout',physical_endpoint_id=None)]
    pe_engine_roles=[dict(pe=f'PE{i}',engines=[f'PE{i}.MATRIX',f'PE{i}.VECTOR',f'PE{i}.UPLOAD1',f'PE{i}.DOWNLOAD1'],physical_resource_ids=None,
      source_scope='fixed PE engine enum roles; no physical capacity identity') for i in range(32)]
    t79_doc=json.loads(t79.read_text());logical_routes=[]
    for stage in t79_doc.get('routing_stages',t79_doc.get('stages',[])):
        movement=stage.get('movement')
        if movement:logical_routes.append(dict(stage_id=stage.get('id'),mapping=movement,physical_link_ids=None,status='LOGICAL_MAPPING_ONLY'))
    logical_multicast=[]
    for column in range(4):logical_multicast.append(dict(group_id=f'R5.rms.column{column}',source_role=f'GM_RDMA{column}',destination_roles=[f'PE{i}' for i in range(column,32,4)],physical_tree=None))
    for node in range(4):
        logical_multicast.append(dict(group_id=f'R5.quant.group{2*node}',source_role=f'GM_RDMA{node}',destination_roles=[f'PE{i}' for i in range(8*node,8*node+4)],physical_tree=None))
        logical_multicast.append(dict(group_id=f'R5.quant.group{2*node+1}',source_role=f'GM_RDMA{node}',destination_roles=[f'PE{i}' for i in range(8*node+4,8*node+8)],physical_tree=None))
    logical_multicast.append(dict(group_id='R11.metadata',source_role='GM_RDMA0',destination_roles=[f'PE{i}' for i in range(32)],physical_tree=None))
    for column in range(4):logical_multicast.append(dict(group_id=f'E3.down.column{column}',source_role=f'PE{28+column}',destination_roles=[f'PE{i}' for i in range(column,28,4)],physical_tree=None))
    chain_groups=dict(columns=[[f'PE{i}' for i in range(c,32,4)] for c in range(4)],rows=[[f'PE{i}' for i in range(r*4,r*4+4)] for r in range(8)],physical_link_ids=None)
    facts=dict(schema_version=1,adas_commit=adas_head,simulator_commit=sim_head,profiles=profile,roles=roles,pe_engine_roles=pe_engine_roles,
      gsram=dict(base_address=_constant(text,'GSRAM_ADDR'),service_identity='logical GSRAM only',capacity_bytes=None,timing=None),
      logical_routes=logical_routes,logical_multicast_groups=logical_multicast,logical_chain_groups=chain_groups,physical_links=[],physical_routes=[],multicast_trees=[],complete_physical_binding=False,
      source_files=[dict(path=str(p),sha256=_sha(p)) for p in (stand,fused,test,router,t79)],no_coordinate_route_inference=True)
    facts['static_facts_sha256']=stable_sha256(facts)
    return facts
