"""Source-bulk Vector boundaries from direct engine/TPC calibration only.

One invocation consumes V records, distributed over four TPCs. READ completion
uses an observed work anchor plus steady full-round service and remainder drain;
it does not identify unobserved FIFO depth or physical standalone SRAM bandwidth.
Only an accepted bundle entry authorizes timing outside the calibration scenes.
"""
from typing import Literal
import math,json
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .vector import VectorWork
from .component_service import ComponentAction

ENGINES=('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE')

class VectorBulkWork(FrozenRecord):
    profile:Literal['standalone','transformer-fused']
    operation:Literal['table','silu_score_quant']
    route_V:int=Field(gt=0,le=256)
    bulk_invocations:Literal[1]
    record_input_entries:int=Field(gt=0)
    total_rounds:int=Field(gt=0)
    full_rounds:int=Field(ge=0)
    remainder:int=Field(ge=0,lt=4)
    buffer_binding:Literal['source-strides-private-fixed-addresses-v2']
    participants:tuple[int,...]
    @model_validator(mode='after')
    def valid(self):
        expected=tuple(range(32)) if self.operation=='table' else (28,29,30,31)
        entries=1 if self.operation=='table' else (20 if self.profile=='standalone' else 16)
        if self.participants!=expected or self.record_input_entries!=entries or self.total_rounds!=self.route_V or (self.full_rounds,self.remainder)!=divmod(self.route_V,4):raise ValueError('source bulk work/round/participant identity mismatch')
        return self
    def shape(self):
        w=VectorWork(work_id='source-bulk',profile=self.profile,operation=self.operation,work_items=self.route_V,pe_id=self.participants[0])
        return w.shape()
    def sram_bytes_per_pe(self):
        return VectorWork(work_id='source-bulk',profile=self.profile,operation=self.operation,work_items=self.route_V,pe_id=self.participants[0]).physical_sram_bytes()

class VectorBulkFormula(FrozenRecord):
    revision:Literal['M06-source-bulk-round-r4']='M06-source-bulk-round-r4'
    profile:Literal['standalone','transformer-fused']
    operation:Literal['table','silu_score_quant']
    calibration_V:tuple[int,...]
    anchor_full_rounds:int=Field(gt=0)
    read_startup:float=Field(ge=0)
    read_anchor_service:float=Field(gt=0)
    read_full_round_service:float=Field(gt=0)
    read_remainder_drain:dict[str,float]
    cal_startup:float=Field(ge=0)
    cal_first_round:float=Field(ge=0)
    cal_round_service:float=Field(gt=0)
    cal_tpc_skew:float=Field(ge=0)
    cal_terminal_service:dict[str,float]
    write_startup:float=Field(ge=0)
    write_completion_handoff:float=Field(ge=0)
    engine_tail:dict[str,float]
    calibration_scene_ids:tuple[str,...]
    source_refs:tuple[str,...]
    unit:Literal['reported_ns']='reported_ns'
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if len(set(self.calibration_V))<2 or tuple(sorted(set(self.calibration_V)))!=self.calibration_V or len(set(self.calibration_scene_ids))!=len(self.calibration_scene_ids) or not self.source_refs:raise ValueError('bulk calibration identity incomplete')
        if set(self.read_remainder_drain)!=set(self.cal_terminal_service) or '0' not in self.read_remainder_drain or not set(self.read_remainder_drain)<=set('0123') or set(self.engine_tail)!=set(ENGINES):raise ValueError('bulk boundary terms incomplete')
        values=[*self.read_remainder_drain.values(),*self.cal_terminal_service.values(),*self.engine_tail.values()]
        if any(not math.isfinite(v) or v<0 for v in values):raise ValueError('bulk boundary values invalid')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('bulk formula hash drift')
        return self
    def parameter_paths(self):
        prefix=f'vector.bulk.{self.profile}.{self.operation}.'
        return tuple(prefix+t for t in ('READ.startup','READ.anchor_service','READ.full_round_service','CAL.startup','CAL.first_round','CAL.round_service','CAL.tpc_skew','WRITE.startup','WRITE.completion_handoff'))+tuple(prefix+'READ.remainder'+r for r in self.read_remainder_drain)+tuple(prefix+'CAL.terminal'+r for r in self.cal_terminal_service)+tuple(prefix+e+'.tail' for e in ENGINES)
    def timing(self,work,changes=None):
        work=VectorBulkWork.model_validate_json(work.model_dump_json())
        if (work.profile,work.operation)!=(self.profile,self.operation) or not min(self.calibration_V)<=work.route_V<=max(self.calibration_V) or str(work.remainder) not in self.cal_terminal_service:raise ValueError('bulk work outside calibrated model bounds')
        changes=changes or {};prefix=f'vector.bulk.{self.profile}.{self.operation}.'
        if not set(changes)<=set(self.parameter_paths()) or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in changes.values()):raise ValueError('bulk mutation must name finite nonnegative local parameter')
        def param(term,base):return changes.get(prefix+term,base)
        rem=str(work.remainder);read=param('READ.startup',self.read_startup)+param('READ.anchor_service',self.read_anchor_service)+(work.full_rounds-self.anchor_full_rounds)*param('READ.full_round_service',self.read_full_round_service)+param('READ.remainder'+rem,self.read_remainder_drain[rem])
        cal_start=param('CAL.startup',self.cal_startup);period=param('CAL.round_service',self.cal_round_service);skew=param('CAL.tpc_skew',self.cal_tpc_skew);first=param('CAL.first_round',self.cal_first_round)
        rounds={}
        for tpc in range(4):
            rounds[str(tpc)]=[dict(round=i+1,time_ns=cal_start+first+i*period+(tpc*skew if i else 0)) for i in range(work.full_rounds)]
            if tpc<work.remainder:rounds[str(tpc)].append(dict(round=0,time_ns=cal_start+first+work.full_rounds*period+tpc*skew))
        cal_finish=max(r['time_ns'] for rows in rounds.values() for r in rows)+param('CAL.terminal'+rem,self.cal_terminal_service[rem]);write_start=param('WRITE.startup',self.write_startup)
        # Explicit producer completion controls final WRITE, not a MoE residual.
        write_finish=max(cal_finish,read,write_start)+param('WRITE.completion_handoff',self.write_completion_handoff)
        stages={}
        for e,start,finish in (('VECTOR_READ',0,read),('VECTOR_CAL',cal_start,cal_finish),('VECTOR_WRITE',write_start,write_finish)):
            if finish<start:raise ValueError('bulk invalid timing boundary')
            stages[e]=dict(start_ns=start,active_finish_ns=finish,end_ns=finish+param(e+'.tail',self.engine_tail[e]))
        return dict(stages=stages,rounds=rounds)
    def actions(self,work,pe_id,*,resource_ids,changes=None):
        if changes:raise ValueError('bulk conditional intervals do not identify hardware counterfactual response')
        if pe_id not in work.participants or set(resource_ids)!=set(ENGINES):raise ValueError('bulk supplied PE/engine binding incomplete')
        timing=self.timing(work,changes);actions=[];stage='R12' if self.operation=='table' else 'E2'
        for e in ENGINES:
            b=timing['stages'][e];prefix=f'{stage}.pe{pe_id}.{e}'
            for part,duration,deps,start in (('active',b['active_finish_ns']-b['start_ns'],(),b['start_ns']),('tail',b['end_ns']-b['active_finish_ns'],(prefix+'.active',),0)):
                actions.append(ComponentAction(action_id=prefix+'.'+part,stage=stage+'.'+e+'.'+part,component='M06',resource_id=resource_ids[e],work=float(work.total_rounds if part=='active' else 1),work_unit='bulk-source-rounds' if part=='active' else 'engine-drain',duration_cycles=float(duration),duration_source='direct bulk engine/TPC boundaries; reported units only',calibration_sha256=self.formula_sha256,dependencies=deps,earliest_start=float(start),participants=(f'pe{pe_id}',),status='calibrated',conditional_context=f'{stage}.pe{pe_id}.bulk',conditional_members=2*len(ENGINES),conditional_start=float(b['start_ns'] if part=='active' else b['active_finish_ns'])))
        return tuple(actions)

def freeze_vector_bulk_formula(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(dict(revision='M06-source-bulk-round-r4',unit='reported_ns',**kwargs),mode='json');raw['formula_sha256']=stable_sha256(raw)
    return VectorBulkFormula.model_validate_json(json.dumps(raw))

def bulk_work_from_row(row):
    fields=VectorBulkWork.model_fields
    return VectorBulkWork.model_validate_json(json.dumps({k:row[k] for k in fields}))

def bulk_work_from_plan(workload,plan,operation,*,buffer_binding):
    if plan.workload_sha256!=workload.workload_sha256:raise ValueError('bulk workload/plan mismatch')
    stage='R12' if operation=='table' else 'E2'
    if not next(s for s in plan.stages if s.stage_id==stage).active:raise ValueError('bulk inactive stage')
    v=plan.V
    return VectorBulkWork(profile=workload.profile.name,operation=operation,route_V=v,bulk_invocations=1,record_input_entries=1 if operation=='table' else (20 if workload.profile.name=='standalone' else 16),total_rounds=v,full_rounds=v//4,remainder=v%4,buffer_binding=buffer_binding,participants=tuple(range(32)) if operation=='table' else (28,29,30,31))

def execute_vector_bulk(formula,work,binding,hardware,*,resource_ids,pe_id,changes=None,extra_actions=(),production=True,cancel=None):
    from .component_service import execute_actions
    from .adalink_execute import _critical_to_completion
    if production:raise ValueError('bulk physical timebase and real buffer binding unavailable')
    stage='R12' if work.operation=='table' else 'E2';actions=formula.actions(work,pe_id,resource_ids=resource_ids,changes=changes)
    terminal=ComponentAction(action_id=stage+'.complete',stage=stage,component='M06',resource_id=resource_ids['VECTOR_WRITE'],work=1.0,work_unit='all-engine-complete',duration_cycles=0.0,duration_source='terminal engine join',calibration_sha256=formula.formula_sha256,dependencies=tuple(a.action_id for a in actions if a.action_id.endswith('.tail')),participants=(f'pe{pe_id}',),status='calibrated')
    ex=execute_actions(actions+(terminal,)+tuple(extra_actions),binding,hardware,production=False,cancel=cancel)
    ex.receipt.update(workload_identity_sha256=stable_sha256(work.model_dump(mode='json')),bulk_formula_sha256=formula.formula_sha256,work=work.model_dump(mode='json'),sram_bytes_per_pe=work.sram_bytes_per_pe(),reported_unit='reported_ns',physical_publishable=False,bulk_round_projection=formula.timing(work,changes)['rounds'],counterfactual_tsim_verified=False)
    if ex.result.status=='complete':ex.receipt['bulk_event_resource_critical_path']=_critical_to_completion(ex.receipt['causal_graph'],stage+'.complete')
    return ex
