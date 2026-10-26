"""Fail-closed identity matching for passive asynchronous handoff events."""
from typing import Literal
import json
from pydantic import Field,model_validator,TypeAdapter
from .workload import FrozenRecord,stable_sha256

Phase=Literal['api_begin','api_return','sync_release','sync_consume','queue_admission','engine_start','engine_end','consumer_complete','counter_begin','counter_end']
Operation=Literal['zero_outer_release','reduce_done','zero_send','write_sum_contribution','result_broadcast','pe_result_consumer']

class AsyncHandoffEvent(FrozenRecord):
    event_id:str
    run_id:str
    stage:Literal['R10']='R10'
    generation:int=Field(ge=0)
    operation:Operation
    source:str
    destination:str
    buffer_id:str
    channel:int=Field(ge=0)
    contributor:int|None=Field(default=None,ge=0)
    ordinal:int=Field(ge=0)
    phase:Phase
    sequence:int=Field(ge=0)
    timestamp_counter:int=Field(ge=0)
    transaction_id:str
    callsite:str
    @model_validator(mode='after')
    def complete(self):
        if not all((self.event_id,self.run_id,self.source,self.destination,self.buffer_id,self.transaction_id,self.callsite)):raise ValueError('async handoff identity field missing')
        if self.operation=='write_sum_contribution' and self.contributor is None:raise ValueError('WRITE_SUM event requires contributor')
        if self.operation!='write_sum_contribution' and self.contributor is not None:raise ValueError('noncontribution event cannot carry contributor')
        return self

class HandoffRequirement(FrozenRecord):
    requirement_id:str
    run_id:str
    stage:Literal['R10']='R10'
    generation:int=Field(ge=0)
    operation:Operation
    source:str
    destination:str
    buffer_id:str
    channel:int=Field(ge=0)
    contributor:int|None=Field(default=None,ge=0)
    ordinal:int=Field(ge=0)
    transaction_id:str
    required_phases:tuple[Phase,...]
    happens_before:tuple[tuple[Phase,Phase],...]
    ordinal_mapping:bool=False
    strict_fifo_source_ref:str|None=None
    @model_validator(mode='after')
    def complete(self):
        if not self.requirement_id or not self.run_id or not self.source or not self.destination or not self.buffer_id or not self.transaction_id or len(set(self.required_phases))!=len(self.required_phases):raise ValueError('handoff requirement incomplete')
        if any(a not in self.required_phases or b not in self.required_phases or a==b for a,b in self.happens_before):raise ValueError('handoff partial order invalid')
        if self.ordinal_mapping!=(self.strict_fifo_source_ref is not None):raise ValueError('ordinal mapping requires explicit strict FIFO proof')
        if self.operation=='write_sum_contribution' and self.contributor is None:raise ValueError('WRITE_SUM requirement needs contributor')
        return self

class HandoffMatch(FrozenRecord):
    requirement_id:str
    event_ids:tuple[str,...]
    transaction_id:str
    generation:int
    phases:tuple[Phase,...]
    start_counter:int
    end_counter:int

class HandoffMatchSet(FrozenRecord):
    schema_version:Literal[1]=1
    matches:tuple[HandoffMatch,...]
    event_count:int
    requirement_count:int
    source_refs:tuple[str,...]
    match_sha256:str
    @model_validator(mode='after')
    def valid(self):
        if len(self.matches)!=self.requirement_count or not self.source_refs:raise ValueError('handoff match set incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'match_sha256'}))!=self.match_sha256:raise ValueError('handoff match hash drift')
        return self

def _identity(value):
    return tuple(getattr(value,k) for k in ('run_id','stage','generation','operation','source','destination','buffer_id','channel','contributor','ordinal','transaction_id'))

def match_async_handoffs(requirements,events,*,source_refs):
    requirements=tuple(HandoffRequirement.model_validate_json(r.model_dump_json()) for r in requirements);events=tuple(AsyncHandoffEvent.model_validate_json(e.model_dump_json()) for e in events)
    if not requirements or not events or len({r.requirement_id for r in requirements})!=len(requirements) or len({e.event_id for e in events})!=len(events):raise ValueError('handoff input empty or duplicate identity')
    known={_identity(r) for r in requirements}
    if any(_identity(e) not in known for e in events):raise ValueError('unexpected or cross-generation handoff candidate')
    used=set();matches=[]
    for req in requirements:
        rows=[e for e in events if _identity(e)==_identity(req)]
        phases={}
        for e in rows:phases.setdefault(e.phase,[]).append(e)
        if set(phases)!=set(req.required_phases):raise ValueError('missing or extra handoff phase: '+req.requirement_id)
        if any(len(v)!=1 for v in phases.values()):raise ValueError('ambiguous duplicate handoff phase: '+req.requirement_id)
        for before,after in req.happens_before:
            a,b=phases[before][0],phases[after][0]
            if a.sequence>=b.sequence or a.timestamp_counter>b.timestamp_counter:raise ValueError('handoff partial order violation: '+req.requirement_id)
        ordered=sorted(rows,key=lambda e:e.sequence)
        if any(e.event_id in used for e in ordered):raise ValueError('handoff event matched more than once')
        used.update(e.event_id for e in ordered);matches.append(HandoffMatch(requirement_id=req.requirement_id,event_ids=tuple(e.event_id for e in ordered),transaction_id=req.transaction_id,generation=req.generation,phases=tuple(e.phase for e in ordered),start_counter=min(e.timestamp_counter for e in ordered),end_counter=max(e.timestamp_counter for e in ordered)))
    if len(used)!=len(events):raise ValueError('unmatched handoff event')
    raw=TypeAdapter(dict).dump_python(dict(schema_version=1,matches=tuple(matches),event_count=len(events),requirement_count=len(requirements),source_refs=tuple(source_refs)),mode='json');raw['match_sha256']=stable_sha256(raw);return HandoffMatchSet.model_validate_json(json.dumps(raw))
