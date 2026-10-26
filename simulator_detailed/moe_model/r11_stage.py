"""Fused R11 data-readiness primitive and strict consumer publication gate.

GM internal read/admission and Data NoC/sync services were not independently
observed. The archived readiness interval is fixture-only: current TSim did not
complete the source weight_gather VECTOR_READ consumer, so no production entry
or first-consumer timing publication is allowed.
"""
import json,math
from typing import Literal
from pydantic import Field,model_validator,TypeAdapter
from .workload import FrozenRecord,stable_sha256
from .timebase import TimebaseDomain,TimebaseBundle,convert_time_value,freeze_timebase_bundle
from .component_service import ComponentAction,execute_actions
from .r10_stage import R10StageDomain,R10StageBinding,r10_actions

R11_BOUNDARY='GM_RDMA0 before valid-block fanout -> max PE after topk receive/download-to-vector/fence and downstream scalar input observation'
R11_OWNER='R11.valid_block_topk_upload_read_broadcast_consumer_combined'

class R11Work(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    operation:Literal['topk_allgather_combined']='topk_allgather_combined'
    consumer_stage:Literal['R11']='R11'
    V:int=Field(ge=0,le=256)
    per_pe_counts:tuple[int,...]
    prefix:tuple[int,...]
    controller_by_pe:tuple[int,...]
    metadata_buffer_bytes:Literal[3072]=3072
    valid_block_bytes:Literal[128]=128
    row_stride_elements:Literal[256]=256
    element_bytes:Literal[4]=4
    fields:tuple[str,...]=('local_expert','score_bits','token_id')
    channel:Literal[1]=1
    controller_count:Literal[4]=4
    buffer_binding:Literal['source-stride256-private-r11-addresses-r2']='source-stride256-private-r11-addresses-r2'
    counter_node:Literal['GM_RDMA0->max(PE0..31)']='GM_RDMA0->max(PE0..31)'
    boundary:Literal[R11_BOUNDARY]=R11_BOUNDARY
    timing_ownership:Literal[R11_OWNER]=R11_OWNER
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    @model_validator(mode='after')
    def semantics(self):
        running=0;prefix=[]
        for n in self.per_pe_counts:
            if isinstance(n,bool) or not 0<=n<=8:raise ValueError('R11 valid/sentinel top-k count outside source range')
            running+=n;prefix.append(running)
        if len(self.per_pe_counts)!=32 or self.prefix!=tuple(prefix) or self.V!=running or self.controller_by_pe!=tuple(p%4 for p in range(32)) or self.fields!=('local_expert','score_bits','token_id'):
            raise ValueError('R11 prefix/V/controller/field identity mismatch')
        return self
    @property
    def upload_bytes(self):return 12*self.V
    @property
    def upload_payloads(self):return tuple(12*n for n in self.per_pe_counts)
    @property
    def logical_source_bytes(self):return 4*128+self.upload_bytes+3072
    @property
    def delivered_bytes(self):return 4*128+self.upload_bytes+32*3072
    @property
    def native_transfer_count(self):return 4+sum(n>0 for n in self.per_pe_counts)+32
    def flow_inventory(self):
        rows=[dict(flow_id=f'R11.valid_block.c{c}',operation='gm_send_to_dma_local_sync',source='GM_RDMA0',destination=f'GM_WDMA{c}.AIU_DOWNLOAD',payload_bytes=128,channel=1) for c in range(4)]
        rows += [dict(flow_id=f'R11.topk.pe{pe}',operation='gm_receive_sync(with_sum=0)',source=f'PE{pe}.UPLOAD1',destination=f'GM_WDMA{pe%4}.CHANNEL1',payload_bytes=12*n,shape=[3,n],source_row_stride=256,target_row_stride=256,target_prefix_column=0 if pe==0 else self.prefix[pe-1],channel=1) for pe,n in enumerate(self.per_pe_counts) if n]
        rows += [dict(flow_id='R11.metadata.broadcast',operation='gm_broadcast',source='GM_RDMA0.CHANNEL1',destinations=[f'PE{i}.DOWNLOAD1' for i in range(32)],payload_bytes=3072,logical_source_once=True,channel=1)]
        return rows

class R11Formula(FrozenRecord):
    revision:Literal['M04-fused-R11-combined-r2']='M04-fused-R11-combined-r2'
    source_unit:Literal['tsim_counter_default_unit']='tsim_counter_default_unit'
    combined_startup_counter:float=Field(gt=0)
    combined_counter_per_valid_entry:float=Field(ge=0)
    calibration_V:tuple[int,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if not all(math.isfinite(v) for v in (self.combined_startup_counter,self.combined_counter_per_valid_entry)) or len(self.calibration_V)<2 or not self.calibration_scene_ids or not self.applicability:raise ValueError('R11 calibration formula incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('R11 formula hash drift')
        return self
    def parameter_paths(self):return ('gm.r11.topk_allgather_combined.startup_counter','gm.r11.topk_allgather_combined.counter_per_valid_entry')
    def duration(self,work,changes=None):
        work=R11Work.model_validate_json(work.model_dump_json());changes=changes or {}
        if not min(self.calibration_V)<=work.V<=max(self.calibration_V):raise ValueError('R11 work outside calibration bounds')
        if not set(changes)<=set(self.parameter_paths()) or any(isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in changes.values()):raise ValueError('R11 requires declared finite local combined parameter; independent GM/NoC bandwidth unavailable')
        a=changes.get(self.parameter_paths()[0],self.combined_startup_counter);b=changes.get(self.parameter_paths()[1],self.combined_counter_per_valid_entry)
        value=a+b*work.V
        if value<=0:raise ValueError('R11 combined duration must be positive')
        return value

class R11Domain(FrozenRecord):
    work:R11Work
    formula:R11Formula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    terminal_counter_nodes:tuple[str,...]
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        t=self.timebase
        if t.component_ids!=('M04',) or t.operation!=self.work.operation or t.profile!=self.work.profile or t.boundary_id!=self.work.boundary or t.accepted_work_values!=(self.work.V,) or t.source_unit!=self.formula.source_unit or t.status!='validated_simulation' or self.terminal_counter_nodes!=tuple(f'PE{i}' for i in range(32)) or not self.source_refs:raise ValueError('R11 component/work/timebase/terminal mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('R11 domain hash drift')
        return self

class R11Binding(FrozenRecord):
    r10_binding:R10StageBinding
    composite_resource_id:str
    work_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    workload_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    buffer_ids:dict[str,str]
    timing_owner:Literal[R11_OWNER]=R11_OWNER
    source_refs:tuple[str,...]
    binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        units={u['unit_id'] for u in self.r10_binding.binding.graph['execution_units']}
        if self.composite_resource_id not in units or self.composite_resource_id in self.r10_binding.resources.values():raise ValueError('R11 supplied composite resource absent/conflicts with R10')
        if set(self.buffer_ids)!={'gsram_prefix','gsram_topk',*(f'wdma{c}_prefix' for c in range(4)),*(f'pe{p}_topk' for p in range(32))} or any(not v for v in self.buffer_ids.values()) or not self.source_refs:raise ValueError('R11 supplied buffer identity incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('R11 binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_r11_formula(**kwargs):return _freeze(R11Formula,'formula_sha256',dict(revision='M04-fused-R11-combined-r2',source_unit='tsim_counter_default_unit',**kwargs))
def freeze_r11_domain(**kwargs):return _freeze(R11Domain,'domain_sha256',kwargs)
def freeze_r11_binding(**kwargs):return _freeze(R11Binding,'binding_sha256',dict(timing_owner=R11_OWNER,**kwargs))
def r11_work_from_row(row):return R11Work.model_validate_json(json.dumps({k:row[k] for k in R11Work.model_fields if k in row}))

def resolve_r11(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=R11Work.model_validate_json(work.model_dump_json());matches=[]
    for d in domains:
        d=R11Domain.model_validate_json(d.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError('R11 requires one exact simulation domain, found '+str(len(matches)))
    return matches[0]

def convert_r11_duration(domain,value,**identity):return convert_time_value(resolve_r11((domain,),**identity).timebase,value)

def r11_actions(domain,binding,*,before=('R10.complete',),changes=None):
    domain=R11Domain.model_validate_json(domain.model_dump_json());binding=R11Binding.model_validate_json(binding.model_dump_json())
    if binding.domain_sha256!=domain.domain_sha256 or binding.work_sha256!=stable_sha256(domain.work.model_dump(mode='json')):raise ValueError('R11 binding work/domain mismatch')
    duration=domain.formula.duration(domain.work,changes)
    main=ComponentAction(action_id='R11.topk_allgather.combined',stage='R11',component='M04',resource_id=binding.composite_resource_id,work=float(domain.work.upload_bytes),work_unit='combined-topk-source-byte',duration_cycles=duration,duration_source=R11_OWNER+'; GM read/link/sync internal service unseparated',calibration_sha256=domain.formula.formula_sha256,dependencies=before,bytes=domain.work.logical_source_bytes,participants=('GM_RDMA0',)+tuple(f'GM_WDMA{c}' for c in range(4))+tuple(f'PE{p}' for p in range(32)),status='calibrated')
    end=ComponentAction(action_id='R11.input_ready',stage='R11',component='M04',resource_id=binding.composite_resource_id,work=1,work_unit='all-PE-input-readiness',duration_cycles=0,duration_source='post-broadcast handoff/fence/readiness observation; source VECTOR_READ consumer not calibrated',calibration_sha256=domain.domain_sha256,dependencies=(main.action_id,),participants=main.participants,status='calibrated')
    return main,end

def execute_r10_r11(r10,domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    r10=R10StageDomain.model_validate_json(r10.model_dump_json());domain=R11Domain.model_validate_json(domain.model_dump_json());binding=R11Binding.model_validate_json(binding.model_dump_json())
    if production and binding.r10_binding.binding.fixture_only:raise ValueError('production R11 requires real supplied buffers/resources')
    old=r10.post
    if (old.timebase.model_library_sha256,old.timebase.systemc_library_sha256,old.observer_sha256,old.counter_callsite,old.timebase.active_default_unit_seconds)!=(domain.timebase.model_library_sha256,domain.timebase.systemc_library_sha256,domain.observer_sha256,domain.counter_callsite,domain.timebase.active_default_unit_seconds):raise ValueError('R10/R11 provider or unit identity conflict')
    if any(a.stage.startswith(('R10','R11')) for a in extra_actions):raise ValueError('duplicate R10/R11 GM/NoC/sync timing owner')
    actions=r10_actions(r10,binding.r10_binding)+r11_actions(domain,binding,changes=changes)
    e=execute_actions(actions+tuple(extra_actions),binding.r10_binding.binding,hardware,production=production,cancel=cancel)
    e.receipt.update(workload_sha256=binding.workload_sha256,r10_stage_sha256=r10.stage_sha256,r11_domain_sha256=domain.domain_sha256,r11_binding_sha256=binding.binding_sha256,r11_work=domain.work.model_dump(mode='json'),logical_flows=domain.work.flow_inventory(),logical_source_bytes=domain.work.logical_source_bytes,endpoint_delivered_bytes=domain.work.delivered_bytes,logical_transactions=4+sum(v>0 for v in domain.work.per_pe_counts)+1,native_transfer_count=domain.work.native_transfer_count,physical_transaction_count=None,packet_count=None,packet_unknown_reason='combined engine counter does not expose physical route/packet/FIFO boundaries',GM_internal_service=None,DataNoC_internal_service=None,input_readiness_only=True,first_consumer_included=False,consumer_blocker='32-PE source weight_gather VECTOR_READ stalled at configuration frontier under 300s and 900s limits',R12_table_vector_duration_excluded=True,source_unit=domain.formula.source_unit,simulation_seconds=convert_time_value(domain.timebase,actions[-2].duration_cycles),physical_publishable=False,model_cycle_mapping=False)
    if e.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        e.receipt['r11_event_resource_critical_path']=_critical_to_completion(e.receipt['causal_graph'],'R11.input_ready')
    return e

def append_r11_timebase(base,domain):
    base=TimebaseBundle.model_validate_json(base.model_dump_json());domain=R11Domain.model_validate_json(domain.model_dump_json())
    if domain.timebase.domain_id in {d.domain_id for d in base.domains}:raise ValueError('R11 duplicate timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-R11-exact-counter-domain',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
