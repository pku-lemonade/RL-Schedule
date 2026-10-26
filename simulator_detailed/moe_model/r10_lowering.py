"""Compose accepted R10 services into a caller-supplied SystemSpec.

No route is generated. Composite service units must be bound explicitly, and
R10 may have no pre-existing timed primitive that would charge the work twice.
"""
import json
from .r10_stage import R10StageDomain,R10StageBinding,r10_actions
from ..configs.schemas.system_spec import SystemSpec

def lower_r10_into_system_spec(spec,domain,binding,*,prefix='rank0',before=(),downstream=(),changes=None):
    spec=SystemSpec.model_validate_json(spec.model_dump_json())
    domain=R10StageDomain.model_validate_json(domain.model_dump_json())
    binding=R10StageBinding.model_validate_json(binding.model_dump_json())
    if spec.graph.model_dump(mode='json')!=SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec',schema_version=1,spec_id='r10-graph-check',graph=binding.binding.graph,batch=spec.batch.model_dump(mode='json')))).graph.model_dump(mode='json'):
        raise ValueError('R10 supplied graph differs from SystemSpec graph')
    raw=spec.model_dump(mode='json');batch=raw['batch'];tx=batch['transactions'];ids={t['transaction_id'] for t in tx}
    if not prefix or not set(before)<=ids or not set(downstream)<=ids:raise ValueError('R10 prefix/prior/downstream identity missing')
    if any(t['transaction_id'].startswith(prefix+'.R10.') for t in tx):raise ValueError('R10 already timed; duplicate composite charging rejected')
    actions=r10_actions(domain,binding,changes=changes);mapping={a.action_id:prefix+'.'+a.action_id for a in actions};new=[]
    for a in actions:
        deps=[mapping[d] for d in a.dependencies] if a.dependencies else list(before)
        row=dict(transaction_id=mapping[a.action_id],depends_on=deps,start_cycles=0)
        if a.duration_cycles:
            row.update(kind='compute',unit_id=a.resource_id,duration_cycles=a.duration_cycles)
        else:
            counter=row['transaction_id']+'.counter';batch['counters'].append(dict(counter_id=counter,initial_value=0));row.update(kind='signal',counter_id=counter,delta=1)
        new.append(row)
    for t in tx:
        if t['transaction_id'] in downstream:t['depends_on']=list(dict.fromkeys(t['depends_on']+[mapping['R10.complete']]))
    batch['transactions']=tx+new
    for envelope in batch.get('operation_envelopes',[]):
        if set(before+downstream)&set(envelope['member_transactions']):envelope['member_transactions']+=list(mapping.values())
    if not batch.get('operation_envelopes'):
        batch['operation_envelopes']=[dict(operation_id=prefix+'.R10.whole',member_transactions=list(mapping.values()),boundary_ref='three-nonoverlapping-source-counter-primitives')]
    return SystemSpec.model_validate_json(json.dumps(raw))

def require_r10_spec_contract(spec,workload,bundle):
    if workload.profile.name!='transformer-fused':return
    spec=SystemSpec.model_validate_json(json.dumps(spec) if isinstance(spec,dict) else spec.model_dump_json())
    tx={t.transaction_id:t for t in spec.batch.transactions}
    expected_operations=('zero_init','write_sum','reduce_done_broadcast_consumer')
    for rank in workload.observed_ep_ranks:
        prefix=f'rank{rank}.R10.';previous=None
        for op in expected_operations:
            name=prefix+op+'.combined';t=tx.get(name)
            entry_id='m04-source-fused-R10-'+op+('-C32-B128' if op=='write_sum' else '-B128')
            matches=[e for e in bundle.entries if e.entry_id==entry_id]
            if t is None or len(matches)!=1 or t.kind!='compute':raise ValueError('R10 production spec missing exact source segment: '+name)
            e=matches[0]
            if op=='write_sum':
                from .write_sum_primitive import CombinedWriteSumFormula,WriteSumPrimitiveWork
                f=CombinedWriteSumFormula.model_validate_json(json.dumps(e.formula));duration=f.duration(WriteSumPrimitiveWork(payload_bytes=128,contributors=32,contributor_order=tuple(range(32))))
            else:
                from .r10_stage import R10BoundaryFormula,R10BoundaryWork
                f=R10BoundaryFormula.model_validate_json(json.dumps(e.formula));duration=f.duration(R10BoundaryWork.model_validate_json(json.dumps(e.domain.input_dimensions['r10_boundary_work'])))
            if t.duration_cycles!=duration or (previous is not None and previous not in t.depends_on):raise ValueError('R10 segment duration or dependency identity mismatch: '+name)
            previous=name
        terminal=tx.get(prefix+'complete')
        if terminal is None or terminal.kind!='signal' or previous not in terminal.depends_on:raise ValueError('R10 all-consumer terminal dependency missing')
        if any(name.startswith(prefix) and name not in {prefix+op+'.combined' for op in expected_operations}|{prefix+'complete'} for name in tx):raise ValueError('R10 duplicate timed primitive in production SystemSpec')
        consumers=[t for name,t in tx.items() if name.startswith(f'rank{rank}.R11.')]
        if not consumers or not any(prefix+'complete' in t.depends_on for t in consumers):raise ValueError('R10 lacks real R11 downstream dependency')

def build_r10_operation_plan(workload,ep_rank,**kwargs):
    """Add current R10 ownership to a plan without changing historical builder."""
    from .operation_plan import build_operation_plan,MoeOperationPlan
    from .workload import stable_sha256
    plan=build_operation_plan(workload,ep_rank,**kwargs)
    if workload.profile.name!='transformer-fused':return plan
    raw=plan.model_dump(mode='json',exclude={'plan_sha256'})
    for s in raw['stages']:
        if s['stage_id']=='R10':s['attributes'].update(r10_source_sequence=['zero_init','combined_write_sum','reduce_done_broadcast_first_consumer'],r10_source_payloads=[128,4096,128],r10_timing_owners=['R10.zero_init_transport_and_visibility','R10.combined_write_sum','R10.reduce_done_valid_count_broadcast_first_consumer'],first_consumer='receive_valid_expert_num_from_gm_rdma/native_receive/release/fence_download1',separate_counted_join_timing=False)
        if s['stage_id']=='R11':s['attributes']['r10_valid_count_broadcast_already_owned']=True
    raw['plan_sha256']=stable_sha256(raw)
    return MoeOperationPlan.model_validate_json(json.dumps(raw))
