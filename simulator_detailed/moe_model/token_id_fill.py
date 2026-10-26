"""Exact source token-ID Vector fill, with local queue/CAL/WRITE ownership."""
from typing import Literal
import json,math,re
from pydantic import Field,TypeAdapter,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseDomain,freeze_timebase_bundle,convert_time_value
PARTICIPANTS=tuple(range(32))
CALLSITE='router_gemm.hpp:471-489;vector_fill.hpp:9-45'
OWNER='R9.token_ids_init.Vector_config_CAL_WRITE'
BOUNDARY='per PE token-ID fill API issue -> fifth named VECTOR_WRITE visible completion; config/CAL/WRITE source events only'

class TokenIdFillWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    ep_rank:Literal[0]=0
    V:Literal[64]=64
    operation:Literal['token_ids_fill']='token_ids_fill'
    consumer_stage:Literal['R9.token_ids_init']='R9.token_ids_init'
    elements:Literal[32]=32
    payload_bytes:Literal[128]=128
    dtype:Literal['int32']='int32'
    value_rule:Literal['each PE node_id broadcast into32entries']='each PE node_id broadcast into32entries'
    participants:tuple[int,...]=PARTICIPANTS
    invocations_per_pe:Literal[1]=1
    read_launches:Literal[0]=0
    cal_launches:Literal[1]=1
    write_launches:Literal[1]=1
    buffer:Literal['moe.topk_token_ids[0:32]']='moe.topk_token_ids[0:32]'
    predecessor_entry:Literal['m07-source-fused-count-token-local-handoff-rank0-V64']='m07-source-fused-count-token-local-handoff-rank0-V64'
    next_consumer:Literal['source scalar sentinel scan independent; later sync_vector_to_upload gates topk_allgather DMA']='source scalar sentinel scan independent; later sync_vector_to_upload gates topk_allgather DMA'
    source_callsite:Literal[CALLSITE]=CALLSITE
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    boundary:Literal[BOUNDARY]=BOUNDARY
    timing_owner:Literal[OWNER]=OWNER
    @model_validator(mode='after')
    def exact(self):
        if self.participants!=PARTICIPANTS:raise ValueError('token fill participant mismatch')
        return self

def extract_token_id_fill_source(router,fill):
    compact=lambda s:''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',s,flags=re.S).split())
    required=('tl_vector_fill::fill_value<int32_t>(topk_token_ids,pe_id);','Layout<Shape<Int<PE_NUM>>,Stride<Int<1>>>{}')
    f=compact(fill)
    if any(x not in compact(router) for x in required) or 'cute::ada::vec_launch(1,' not in f or 'cute::ada::vec_write(output,0);' not in f or 'broadcast_value(output,value)' not in f:raise ValueError('token fill source drift')
    return TokenIdFillWork()

class TokenIdFillEvent(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    ep_rank:Literal[0]=0
    issue_ns:float=Field(ge=0)
    api_return_ns:float=Field(ge=0)
    cal_start_ns:float=Field(ge=0)
    cal_end_ns:float=Field(ge=0)
    write_start_ns:float=Field(ge=0)
    write_end_ns:float=Field(ge=0)
    observer_terminal_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def exact(self):
        vals=(self.issue_ns,self.api_return_ns,self.cal_start_ns,self.cal_end_ns,self.write_start_ns,self.write_end_ns,self.observer_terminal_ns)
        if not all(math.isfinite(x) for x in vals) or self.api_return_ns<self.issue_ns or self.cal_start_ns<self.issue_ns or self.cal_end_ns<self.cal_start_ns or self.write_start_ns<self.cal_start_ns or self.write_end_ns<max(self.write_start_ns,self.cal_end_ns) or self.observer_terminal_ns<self.write_end_ns:raise ValueError('token fill boundary/order mismatch')
        return self
    def params(self):return dict(pe=self.pe,config_startup_ns=self.cal_start_ns-self.issue_ns,cal_ns=self.cal_end_ns-self.cal_start_ns,write_issue_offset_ns=self.write_start_ns-self.cal_start_ns,write_drain_ns=self.write_end_ns-max(self.write_start_ns,self.cal_end_ns))

def validate_token_id_fill_events(events):
    rows=tuple(TokenIdFillEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(rows)!=32 or {x.pe for x in rows}!=set(PARTICIPANTS):raise ValueError('token fill duplicate/missing event')
    return tuple(sorted(rows,key=lambda x:x.pe))

class TokenIdPeFormula(FrozenRecord):
    pe:int=Field(ge=0,lt=32)
    config_startup_ns:float=Field(ge=0)
    cal_ns:float=Field(ge=0)
    write_issue_offset_ns:float=Field(ge=0)
    write_drain_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def finite(self):
        if not all(math.isfinite(x) for x in (self.config_startup_ns,self.cal_ns,self.write_issue_offset_ns,self.write_drain_ns)):raise ValueError('nonfinite token fill parameter')
        return self

class TokenIdFillFormula(FrozenRecord):
    revision:Literal['M06-fused-token-id-fill-local-events-r1']='M06-fused-token-id-fill-local-events-r1'
    source_unit:Literal['reported_ns']='reported_ns'
    pe_formulas:tuple[TokenIdPeFormula,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        if tuple(x.pe for x in self.pe_formulas)!=PARTICIPANTS or len(self.calibration_scene_ids)<2 or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids):raise ValueError('token fill formula inventory')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('token fill formula hash drift')
        return self
    def parameter_paths(self):return tuple(f'vector.token_ids.rank0.pe{pe}.{name}.reported_ns' for pe in PARTICIPANTS for name in ('config_startup','cal_service','write_issue_offset','write_drain'))
    def timing(self,pe,changes=None):
        changes=changes or {};paths=self.parameter_paths()
        if type(pe) is not int or pe not in PARTICIPANTS or len(changes)>1 or not set(changes)<=set(paths):raise ValueError('token fill exact local parameter required')
        q=self.pe_formulas[pe];values=[q.config_startup_ns,q.cal_ns,q.write_issue_offset_ns,q.write_drain_ns]
        for i in range(4):
            v=changes.get(paths[pe*4+i],values[i])
            if isinstance(v,bool) or type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('invalid token fill local parameter')
            values[i]=float(v)
        startup,cal,offset,drain=values
        return dict(config_startup=startup,cal_service=cal,write_issue_offset=offset,write_wait_for_cal=max(0.,cal-offset),write_drain=drain,write_occupancy=max(0.,cal-offset)+drain,terminal=startup+max(cal,offset)+drain)
    def terminal(self,changes=None):return max(self.timing(pe,changes)['terminal'] for pe in PARTICIPANTS)

class TokenIdFillDomain(FrozenRecord):
    work:TokenIdFillWork
    formula:TokenIdFillFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def exact(self):
        t=self.timebase
        if (t.component_ids,t.operation,t.profile,t.boundary_id,t.source_unit,t.accepted_work_values,t.status)!=(('M06',),self.work.operation,self.work.profile,BOUNDARY,'reported_ns',(64,),'validated_simulation'):raise ValueError('token fill timebase mismatch')
        if not self.source_refs or stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('token fill domain hash drift')
        return self

class TokenIdFillBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str
    source_refs:tuple[str,...]
    binding_sha256:str
    @model_validator(mode='after')
    def exact(self):
        required={f'PE{p}.{engine}' for p in PARTICIPANTS for engine in ('CONFIG','VECTOR_CAL','VECTOR_WRITE')};units={u['unit_id'] for u in self.binding.graph['execution_units']}
        if set(self.resources)!=required or len(set(self.resources.values()))!=96 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('token fill resources incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('token fill binding drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))
def freeze_token_id_fill_formula(**kw):return _freeze(TokenIdFillFormula,'formula_sha256',dict(revision='M06-fused-token-id-fill-local-events-r1',source_unit='reported_ns',**kw))
def freeze_token_id_fill_domain(**kw):return _freeze(TokenIdFillDomain,'domain_sha256',kw)
def freeze_token_id_fill_binding(**kw):return _freeze(TokenIdFillBinding,'binding_sha256',kw)

def resolve_token_id_fill(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=TokenIdFillWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        d=TokenIdFillDomain.model_validate_json(raw.model_dump_json())
        if not physical and d.work==work and (d.timebase.model_library_sha256,d.timebase.systemc_library_sha256,d.observer_sha256,d.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(d)
    if len(matches)!=1:raise ValueError('token fill requires one exact domain, found '+str(len(matches)))
    return matches[0]

def token_id_fill_actions(domain,binding,changes=None):
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('token fill binding/domain mismatch')
    out=[]
    def add(i,pe,engine,duration,deps,work=1):out.append(ComponentAction(action_id=i,stage='TOKEN_ID.fill',component='M06',resource_id=binding.resources[f'PE{pe}.{engine}'],work=work,work_unit='source-token-fill',duration_cycles=duration,duration_source=OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=deps,participants=(f'PE{pe}',),bytes=128 if engine=='VECTOR_WRITE' else 0,status='calibrated'))
    for pe in PARTICIPANTS:
        t=domain.formula.timing(pe,changes);stem=f'TOKEN_ID.fill.pe{pe}'
        add(stem+'.config',pe,'CONFIG',t['config_startup'],(f'COUNT_TOKEN.handoff.pe{pe}',))
        add(stem+'.cal',pe,'VECTOR_CAL',t['cal_service'],(stem+'.config',),32)
        add(stem+'.write_issue',pe,'CONFIG',t['write_issue_offset'],(stem+'.config',))
        add(stem+'.write',pe,'VECTOR_WRITE',t['write_occupancy'],(stem+'.write_issue',),32)
        add(stem+'.complete',pe,'CONFIG',0.,(stem+'.cal',stem+'.write'))
    deps=tuple(f'TOKEN_ID.fill.pe{p}.complete' for p in PARTICIPANTS)
    out.append(ComponentAction(action_id='TOKEN_ID.fill.complete',stage='TOKEN_ID.fill',component='M06',resource_id=binding.resources['PE0.CONFIG'],work=32,work_unit='token-fill-after-all',duration_cycles=0,duration_source='zero-cost32way max',calibration_sha256=domain.domain_sha256,dependencies=deps,participants=tuple(f'PE{p}' for p in PARTICIPANTS),status='calibrated'))
    return tuple(out)

def execute_token_id_fill(domain,binding,hardware,*,changes=None,extra_actions=(),production=True,cancel=None):
    domain=TokenIdFillDomain.model_validate_json(domain.model_dump_json());binding=TokenIdFillBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('token fill production supplied binding required')
    if any(x.stage=='TOKEN_ID.fill' or x.action_id.startswith('TOKEN_ID.fill') for x in extra_actions):raise ValueError('token fill duplicate timing owner')
    run=execute_actions(token_id_fill_actions(domain,binding,changes)+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    run.receipt.update(token_id_fill_domain_sha256=domain.domain_sha256,timing_owner=OWNER,profile='transformer-fused',rank=0,V=64,engine_rule='WRITE occupancy=max(CAL service-write offset,0)+write drain; terminal=max(CAL,WRITE)',per_pe_timing=[dict(pe=p,**domain.formula.timing(p,changes)) for p in PARTICIPANTS],excluded_cost_owners=['49ns.count_token_handoff','count_fill','scalar_scan','R10','R11','GM','DataNoC','Matrix'],parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=convert_time_value(domain.timebase,domain.formula.terminal(changes)),physical_publishable=False,dispatch_identity_closed=False)
    if run.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        run.receipt['event_resource_critical_path']=_critical_to_completion(run.receipt['causal_graph'],'TOKEN_ID.fill.complete')
    return run

def append_token_id_fill_timebase(base,domain):
    if domain.timebase.domain_id in {d.domain_id for d in base.domains}:raise ValueError('duplicate token fill timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-rank0-token-id-fill',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)
