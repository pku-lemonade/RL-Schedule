"""Local controller/channel and supplied shared-address resources, CPU only.

No default physical cost exists. The caller supplies every service coefficient
and every atomic resource binding. Production execution requires independently
correlated service calibration, which the current observation gate lacks.
"""
import math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .component_service import ComponentAction,execute_actions
from .gm_source import GMSourceWork

class GMSourceCosts(FrozenRecord):
    controller_startup:dict[str,float]
    controller_bytes_per_unit:dict[str,float]
    atomic_per_contribution:dict[str,float]
    fixture_only:bool
    unit:str
    calibration_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        if not self.fixture_only or self.unit!='fixture_unit':raise ValueError('GM source physical service calibration unavailable')
        if set(self.controller_startup)!=set(self.controller_bytes_per_unit):raise ValueError('GM controller cost scope mismatch')
        if any(not math.isfinite(v) or v<0 for v in (*self.controller_startup.values(),*self.atomic_per_contribution.values())) or any(not math.isfinite(v) or v<=0 for v in self.controller_bytes_per_unit.values()):raise ValueError('GM explicit finite resource costs required')
        return self
    def mutate(self,path,value):
        raw=self.model_dump();parts=path.split('/',1)
        if len(parts)!=2 or parts[0] not in ('controller_startup','controller_bytes_per_unit','atomic_per_contribution') or parts[1] not in raw[parts[0]]:raise ValueError('GM mutation needs exact existing resource path')
        raw[parts[0]][parts[1]]=value
        return GMSourceCosts(**raw)

def source_gm_actions(work,costs,*,controller_units,atomic_address_units):
    work=GMSourceWork.model_validate_json(work.model_dump_json());costs=GMSourceCosts.model_validate_json(costs.model_dump_json());transactions,descriptor_ids=work.expand();result=[];atomic_groups={}
    for tx in transactions:
        key=tx.controller_id
        if key not in controller_units or key not in costs.controller_startup:raise ValueError('GM controller binding/cost missing')
        rid=controller_units[key];duration=costs.controller_startup[key]+tx.payload_bytes/costs.controller_bytes_per_unit[key]
        result.append(ComponentAction(action_id=tx.transaction_id+'.channel',stage=work.stage,component='M04',resource_id=rid,work=float(tx.payload_bytes),work_unit='bytes',duration_cycles=duration,duration_source='explicit fixture GM channel',calibration_sha256=costs.calibration_sha256,dependencies=tx.dependencies,bytes=tx.payload_bytes,participants=(f'pe{tx.source_pe}',key),status='calibrated'))
        if tx.operation=='write_sum':
            address_key=f'{tx.address}:{tx.payload_bytes}'
            if address_key not in atomic_address_units:raise ValueError('GM atomic address resource missing')
            atom=atomic_address_units[address_key]
            if atom not in costs.atomic_per_contribution:raise ValueError('GM atomic resource cost missing')
            atomic_groups.setdefault(address_key,[]).append(tx.transaction_id)
            result.append(ComponentAction(action_id=tx.transaction_id+'.atomic',stage=work.stage,component='M04',resource_id=atom,work=1.0,work_unit='contribution',duration_cycles=costs.atomic_per_contribution[atom],duration_source='explicit fixture atomic service, never calibrated from ordinary write',calibration_sha256=costs.calibration_sha256,dependencies=(tx.transaction_id+'.channel',),participants=(key,atom),status='calibrated'))
    return tuple(result),atomic_groups

def execute_source_gm(work,costs,binding,hardware,*,controller_units,atomic_address_units,extra_actions=(),production=True,cancel=None,max_cycles=100000.0):
    if production or not work.fixture_only or not binding.fixture_only:raise ValueError('GM source production requires independent correlated service calibration and real binding')
    actions,groups=source_gm_actions(work,costs,controller_units=controller_units,atomic_address_units=atomic_address_units)
    if not actions:raise ValueError('GM source empty execution')
    last=tuple(a.action_id for a in actions if a.action_id.endswith('.atomic') or (a.action_id.endswith('.channel') and a.action_id[:-8]+'.atomic' not in {x.action_id for x in actions}))
    terminal=ComponentAction(action_id=work.stage+'.complete',stage=work.stage,component='M04',resource_id=actions[0].resource_id,work=1.0,work_unit='completion-join',duration_cycles=0.0,duration_source='zero cost dependency join',calibration_sha256=costs.calibration_sha256,dependencies=last,participants=('source-gm',),status='calibrated')
    ex=execute_actions(actions+(terminal,)+tuple(extra_actions),binding,hardware,production=False,cancel=cancel,max_cycles=max_cycles)
    ex.receipt.update(source_work_sha256=work.identity(),source_work=work.inventory(),cost_sha256=stable_sha256(costs.model_dump(mode='json')),atomic_address_conflict_groups=groups,physical_publishable=False,reported_unit='fixture_unit',tsim_counterfactual_validated=False)
    if ex.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        ex.receipt['gm_event_resource_critical_path']=_critical_to_completion(ex.receipt['causal_graph'],work.stage+'.complete')
    return ex
