"""Supplied M03 packet/merge/root/broadcast runtime composition.

Native boundary affine evidence does not identify independent merge/issue costs.
Those costs must be supplied explicitly; CPU fixtures never become physical claims.
"""
from dataclasses import dataclass
import copy,json,math
from typing import Literal
from pydantic import Field,model_validator
from .binding import LogicalFlow,SuppliedBinding,freeze_network_workload
from .collective import PEChain
from .hardware import HardwareParameterSet
from .lowering import CompiledNetwork,compile_network
from .receipt import ResourceObserver,build_receipt
from .workload import FrozenRecord,stable_sha256
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..runtime_context import RuntimeContext
from ..system_compile import compile_system


class ChainService(FrozenRecord):
    pe: int = Field(ge=0,lt=32)
    issue_unit: str
    merge_unit: str
    issue_cycles: float | None = Field(default=None,ge=0)
    merge_cycles: float | None = Field(default=None,ge=0)
    cost_status: Literal['fixture_only','calibrated','unavailable']
    calibration_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    source_ref: str
    @model_validator(mode='after')
    def cost(self):
        if not self.source_ref or not self.issue_unit or not self.merge_unit:raise ValueError('chain service source/units required')
        if self.cost_status=='unavailable' and (self.issue_cycles is not None or self.merge_cycles is not None):raise ValueError('unavailable chain service has no duration')
        if self.cost_status!='unavailable' and (self.issue_cycles is None or self.merge_cycles is None):raise ValueError('chain service costs missing')
        if self.cost_status=='calibrated' and self.calibration_sha256 is None:raise ValueError('chain service calibration required')
        if any(v is not None and not math.isfinite(v) for v in (self.issue_cycles,self.merge_cycles)):raise ValueError('chain service must be finite')
        return self


class ChainParameterTarget(FrozenRecord):
    parameter_path: str
    network_id: str | None=None
    link_ids: tuple[str,...]=()
    pe: int | None = Field(default=None,ge=0,lt=32)
    effect: Literal['link_bandwidth','link_issue','fifo_slots','pe_issue','pe_merge']


class ChainRuntimeBinding(FrozenRecord):
    network: SuppliedBinding
    services: tuple[ChainService,...]
    parameters: tuple[ChainParameterTarget,...]=()
    fixture_only: bool
    source_refs: tuple[str,...]
    binding_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if {s.pe for s in self.services}!=set(range(32)) or len(self.services)!=32:raise ValueError('chain needs all 32 supplied PE services')
        if not self.source_refs or self.fixture_only!=self.network.fixture_only:raise ValueError('chain fixture/source identity mismatch')
        units={u['unit_id'] for u in self.network.graph['execution_units']}
        if any(s.issue_unit not in units or s.merge_unit not in units for s in self.services):raise ValueError('chain service unit missing')
        if not self.fixture_only and any(s.cost_status!='calibrated' for s in self.services):raise ValueError('production chain issue/merge calibration unavailable')
        paths=[p.parameter_path for p in self.parameters]
        if len(paths)!=len(set(paths)):raise ValueError('duplicate chain parameter path')
        links={(l['network_id'],l['link_id']) for l in self.network.graph['links']}
        for target in self.parameters:
            if target.effect.startswith('pe_'):
                if target.pe is None or target.link_ids:raise ValueError('PE service target invalid')
            elif not target.link_ids or any((target.network_id,l) not in links for l in target.link_ids):raise ValueError('chain parameter link missing')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('chain runtime binding hash mismatch')
        return self


def freeze_chain_runtime_binding(**kwargs):
    shape=ChainRuntimeBinding.model_construct(**kwargs,binding_sha256='0'*64).model_dump(mode='json',exclude={'binding_sha256'})
    return ChainRuntimeBinding(**kwargs,binding_sha256=stable_sha256(shape))


@dataclass(frozen=True)
class CompiledChain:
    network: CompiledNetwork
    actions: tuple[dict,...]
    binding_sha256: str
    formula_sha256: str | None
    publication_status: str
    supplied_workload_sha256: str | None
    service_cost_sha256: str


def compile_chain(chain,binding,hardware,*,broadcast=True,scenario_class='controlled_overlap',workload_sha256=None,formula_sha256=None,production=True,max_cycles=100000.0):
    binding=ChainRuntimeBinding.model_validate_json(binding.model_dump_json());hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json())
    chain=PEChain.model_validate_json(chain.model_dump_json())
    if production and (binding.fixture_only or workload_sha256 is None):raise ValueError('production chain requires real supplied binding and workload identity')
    import re
    for identity in (workload_sha256,formula_sha256):
        if identity is not None and re.fullmatch(r'[0-9a-f]{64}',identity) is None:raise ValueError('chain identity must be SHA256')
    if scenario_class not in ('isolated','controlled_overlap','sustained_backpressure'):raise ValueError('chain scenario unknown')
    rounds=2 if scenario_class=='sustained_backpressure' else 1;groups=chain.groups()[:1] if scenario_class=='isolated' else chain.groups()
    net_raw=binding.network.model_dump(mode='json');timing=copy.deepcopy(list(binding.network.timing));services={s.pe:s for s in binding.services};hp=hardware.by_path()
    for target in binding.parameters:
        parameter=hp.get(target.parameter_path)
        if parameter is None or parameter.quantity.value is None:raise ValueError('chain parameter unavailable: '+target.parameter_path)
        q=parameter.quantity;value=float(q.value)
        if production and q.calibration_status!='calibrated':raise ValueError('chain parameter uncalibrated')
        if target.effect in ('pe_issue','pe_merge'):
            if q.unit!='cycle' or value<0:raise ValueError('chain service unit invalid')
            s=services[target.pe];services[target.pe]=s.model_copy(update={('issue_cycles' if target.effect=='pe_issue' else 'merge_cycles'):value});continue
        network=next(n for n in timing if n['network_id']==target.network_id);overrides={o['link_id']:copy.deepcopy(o['timing']) for o in network.get('overrides',[])}
        for link in target.link_ids:
            t=copy.deepcopy(overrides.get(link,network['link']))
            if t.get('mode')!='latency_fifo':raise ValueError('chain requires latency_fifo')
            if target.effect=='link_bandwidth':
                if q.unit!='byte/cycle' or value<=0:raise ValueError('chain bandwidth unit invalid')
                t['issue_cycles']=max(1,math.ceil(t['packet_bytes']/value))
            elif target.effect=='link_issue':
                if q.unit!='cycle' or value<=0 or not value.is_integer():raise ValueError('chain link issue must be integral cycles')
                t['issue_cycles']=int(value)
            else:
                if q.unit!='packet slot' or value<=0 or not value.is_integer():raise ValueError('chain FIFO unit invalid')
                t['fifo_slots']=int(value)
            overrides[link]=t
        network['overrides']=[dict(link_id=k,timing=v) for k,v in sorted(overrides.items())]
    # Explicit caller routes, timing and service ownership; no graph generation.
    net_raw['timing']=timing;net_raw.pop('binding_sha256')
    net=SuppliedBinding.model_validate_json(json.dumps(dict(net_raw,binding_sha256=stable_sha256(net_raw))))
    flows=[];actions=[];service_tx=[];counters=[];previous_round={}
    def action(identity,pe,kind,deps,bytes=0):
        s=services[pe];duration=s.issue_cycles if kind=='issue' else s.merge_cycles
        if duration is None:raise ValueError('chain service cost unavailable')
        unit=s.issue_unit if kind=='issue' else s.merge_unit
        tx=dict(transaction_id=identity,depends_on=list(deps),start_cycles=0.0)
        if duration==0:
            counter=identity+'.counter';counters.append(dict(counter_id=counter,initial_value=0));tx.update(kind='signal',counter_id=counter,delta=1)
        else:tx.update(kind='compute',unit_id=unit,duration_cycles=duration)
        service_tx.append(tx);actions.append(dict(action_id=identity,pe=pe,kind=kind,resource_id=unit,bytes=bytes,dependencies=list(deps),cost_status=s.cost_status,calibration_sha256=s.calibration_sha256))
        return identity
    for ri in range(rounds):
        for gi,pes in enumerate(groups):
            tails=[]
            for pi,offset in enumerate(range(0,chain.payload_bytes,chain.packet_bytes)):
                payload=min(chain.packet_bytes,chain.payload_bytes-offset);deps=previous_round.get(gi,())
                for hi,(src,dst) in enumerate(zip(pes,pes[1:])):
                    base=f'{chain.collective_id}.r{ri}.g{gi}.p{pi}.h{hi}';issued=action(base+'.issue',src,'issue',deps)
                    fid=base+'.transfer';flows.append(LogicalFlow(flow_id=fid,source_role=f'pe{src}',destination_roles=(f'pe{dst}',),payload_bytes=payload,primitive='point_to_point'))
                    actions.append(dict(action_id=fid,pe=src,destination=dst,kind='route_reduce_transport',bytes=payload,dependencies=[issued]))
                    merged=action(base+'.merge',dst,'merge',(fid,),payload);deps=(merged,)
                tails.append(action(f'{chain.collective_id}.r{ri}.g{gi}.p{pi}.self',pes[-1],'merge',deps,payload))
            root=f'{chain.collective_id}.r{ri}.g{gi}.root';counter=root+'.counter';counters.append(dict(counter_id=counter,initial_value=0));service_tx.append(dict(kind='signal',transaction_id=root,counter_id=counter,delta=1,depends_on=tails))
            actions.append(dict(action_id=root,kind='root_materialized',bytes=chain.payload_bytes,dependencies=tails))
            if broadcast:
                fid=f'{chain.collective_id}.r{ri}.g{gi}.broadcast';flows.append(LogicalFlow(flow_id=fid,source_role=f'pe{pes[-1]}',destination_roles=tuple(f'pe{pe}' for pe in pes[:-1]),payload_bytes=chain.payload_bytes,primitive='masked_multicast'))
                actions.append(dict(action_id=fid,kind='broadcast',bytes=chain.payload_bytes,dependencies=[root]));previous_round[gi]=(fid,)
            else:previous_round[gi]=(root,)
    work=freeze_network_workload(workload_id=chain.collective_id,fixture_only=binding.fixture_only,flows=tuple(flows),source_refs=binding.source_refs)
    base=compile_network(work,net,hardware,production=production,max_cycles=max_cycles);raw=base.spec.model_dump(mode='json')
    by_id={a['action_id']:a for a in actions}
    for tx in raw['batch']['transactions']:
        owner=next((fid for fid,ids in base.native_members.items() if tx['transaction_id'] in ids),None)
        if owner:tx['depends_on']+=by_id[owner]['dependencies']
    raw['batch']['counters']+=counters;raw['batch']['transactions']=service_tx+raw['batch']['transactions']
    raw['batch']['operation_envelopes'][0]['member_transactions']+=[tx['transaction_id'] for tx in service_tx]
    batch=GenericTransactionBatch.model_validate_json(json.dumps(raw['batch']));raw['batch']=batch.model_dump(mode='json');spec=SystemSpec.model_validate_json(json.dumps(raw));plan=compile_system(spec)
    compiled=CompiledNetwork(work,net,hardware,spec,plan,base.native_members,timing)
    costs=stable_sha256([services[pe].model_dump(mode='json') for pe in sorted(services)])
    return CompiledChain(compiled,tuple(actions),binding.binding_sha256,formula_sha256,'CPU_ONLY' if binding.fixture_only else 'SUPPLIED_SERVICE_COSTS_REQUIRE_SEPARATE_NATIVE_VALIDATION',workload_sha256,costs)


def execute_chain(chain,binding,hardware,*,cancel=None,inject_failure=False,**kwargs):
    compiled=compile_chain(chain,binding,hardware,**kwargs);runtime=RuntimeContext(compiled.network.plan);observer=ResourceObserver(runtime)
    if cancel:
        if cancel[0] not in {t.transaction_id for t in compiled.network.plan.content.transactions}|{'network.whole'}:raise ValueError('unknown chain cancellation target')
        def cancelling():
            yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
        runtime.env.process(cancelling())
    if inject_failure:
        original=runtime._emit
        def emit(action,transaction_id=None,resource_id=None,detail=None):
            if action=='fifo_event' and detail and 'packet_forward' in detail:raise RuntimeError('C03_INJECTED_PACKET_FAILURE')
            return original(action,transaction_id,resource_id,detail)
        runtime._emit=emit
    result=None;error=None
    try:result=runtime.run()
    except RuntimeError as exc:error=str(exc)
    receipt=build_receipt(compiled.network,runtime,result,observer,error);receipt.update(chain_actions=list(compiled.actions),chain_binding_sha256=compiled.binding_sha256,
      formula_sha256=compiled.formula_sha256,publication_status=compiled.publication_status,native_route_reduce_semantics='packet transport plus explicit supplied local merges',
      supplied_workload_sha256=compiled.supplied_workload_sha256,service_cost_sha256=compiled.service_cost_sha256,physical_counterfactual_validated=False)
    return dict(compiled=compiled,runtime=runtime,result=result,receipt=receipt)


__all__=['ChainService','ChainParameterTarget','ChainRuntimeBinding','CompiledChain','freeze_chain_runtime_binding','compile_chain','execute_chain']
