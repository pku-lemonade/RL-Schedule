"""Exact simulated counter-boundary mapping, distinct from physical model cycles."""
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .timebase import TimebaseDomain,TimebaseBundle,freeze_timebase_bundle,resolve_timebase,convert_time_value

class ComponentCounterDomain(FrozenRecord):
    domain:TimebaseDomain
    component_id:Literal['M03','M04','M05']
    counter_node:str
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    primitive_binding:str
    shape:tuple[int,...] | None
    precision:str
    contributors:int | None
    channel:Literal[1]
    entry_reference:str
    @model_validator(mode='after')
    def valid(self):
        if self.domain.component_ids!=(self.component_id,) or not self.counter_node or not self.primitive_binding or not self.entry_reference:raise ValueError('component timebase boundary identity incomplete')
        if self.domain.status!='validated_simulation' or self.domain.source_unit!='tsim_counter_default_unit':raise ValueError('component timebase is simulation counter only')
        return self

class ComponentTimebaseSet(FrozenRecord):
    schema_version:Literal[1]=1
    revision:Literal['C09-component-counter-r7']='C09-component-counter-r7'
    domains:tuple[ComponentCounterDomain,...]
    source_refs:tuple[str,...]
    set_sha256:str
    @model_validator(mode='after')
    def valid(self):
        ids=[d.domain.domain_id for d in self.domains]
        if not ids or len(ids)!=len(set(ids)) or not self.source_refs:raise ValueError('component timebase set incomplete/duplicate')
        if stable_sha256(self.model_dump(mode='json',exclude={'set_sha256'}))!=self.set_sha256:raise ValueError('component timebase set hash drift')
        return self

def freeze_component_timebases(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(dict(schema_version=1,revision='C09-component-counter-r7',**kwargs),mode='json');raw['set_sha256']=stable_sha256(raw)
    return ComponentTimebaseSet.model_validate_json(json.dumps(raw))

def resolve_component_timebase(timebases,*,component_id,operation,profile,boundary_id,source_unit,work,model_library_sha256,systemc_library_sha256,counter_node,observer_sha256,counter_callsite,primitive_binding,shape,precision,contributors,channel,physical=False):
    timebases=ComponentTimebaseSet.model_validate_json(timebases.model_dump_json());matches=[]
    for d in timebases.domains:
        q=d.domain
        if (d.component_id,q.operation,q.profile,q.boundary_id,q.source_unit)!=(component_id,operation,profile,boundary_id,source_unit):continue
        if work not in q.accepted_work_values or (q.model_library_sha256,q.systemc_library_sha256)!=(model_library_sha256,systemc_library_sha256):continue
        if (d.counter_node,d.observer_sha256,d.counter_callsite,d.primitive_binding,d.shape,d.precision,d.contributors,d.channel)!=(counter_node,observer_sha256,counter_callsite,primitive_binding,shape,precision,contributors,channel):continue
        if physical:continue
        matches.append(d)
    if len(matches)!=1:raise ValueError('component timebase requires one exact validated identity, found '+str(len(matches)))
    return matches[0]

def convert_component_duration(timebases,request,raw_value,*,workload_sha256,binding_sha256,component_formula_sha256):
    d=resolve_component_timebase(timebases,**request);out=convert_time_value(d.domain,raw_value)
    return dict(out,workload_sha256=workload_sha256,binding_sha256=binding_sha256,component_formula_sha256=component_formula_sha256,component_timebase_set_sha256=timebases.set_sha256,timebase_refitted=False)

def append_component_timebases(base,extension):
    base=TimebaseBundle.model_validate_json(base.model_dump_json());extension=ComponentTimebaseSet.model_validate_json(extension.model_dump_json())
    domains=base.domains+tuple(d.domain for d in extension.domains)
    if len({d.domain_id for d in domains})!=len(domains):raise ValueError('timebase extension domain conflicts')
    return freeze_timebase_bundle(bundle_id='T81-C09-exact-simulation-counter-domains',fixture_only=False,domains=domains,source_refs=base.source_refs+extension.source_refs)
