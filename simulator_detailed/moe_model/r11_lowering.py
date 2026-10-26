"""Supplied R11 combined primitive in the existing SystemSpec runtime path."""
import json
from .r11_stage import R11Domain,R11Binding,r11_actions
from ..configs.schemas.system_spec import SystemSpec

def lower_r11_into_system_spec(spec,domain,binding,*,prefix='rank0',downstream=(),changes=None):
    spec=SystemSpec.model_validate_json(spec.model_dump_json());domain=R11Domain.model_validate_json(domain.model_dump_json());binding=R11Binding.model_validate_json(binding.model_dump_json())
    raw=spec.model_dump(mode='json');tx=raw['batch']['transactions'];ids={t['transaction_id'] for t in tx};before=prefix+'.R10.complete'
    if before not in ids or not downstream or not set(downstream)<=ids:raise ValueError('R11 needs exact R10 terminal and downstream consumer')
    if spec.graph.model_dump(mode='json')!=SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec',schema_version=1,spec_id='r11-binding-check',graph=binding.r10_binding.binding.graph,batch=raw['batch']))).graph.model_dump(mode='json'):raise ValueError('R11 supplied graph differs')
    if any(t['transaction_id'].startswith(prefix+'.R11.') for t in tx):raise ValueError('R11 already timed; duplicate GM/NoC/sync costs rejected')
    a,z=r11_actions(domain,binding,before=(before,),changes=changes);main=prefix+'.'+a.action_id;end=prefix+'.'+z.action_id;counter=end+'.counter'
    new=[dict(transaction_id=main,kind='compute',unit_id=a.resource_id,duration_cycles=a.duration_cycles,depends_on=[before],start_cycles=0),dict(transaction_id=end,kind='signal',counter_id=counter,delta=1,depends_on=[main],start_cycles=0)]
    raw['batch']['counters'].append(dict(counter_id=counter,initial_value=0))
    for t in tx:
        if t['transaction_id'] in downstream:t['depends_on']=list(dict.fromkeys(t['depends_on']+[end]))
    raw['batch']['transactions']=tx+new
    for envelope in raw['batch'].get('operation_envelopes',[]):
        if before in envelope['member_transactions'] or set(downstream)&set(envelope['member_transactions']):envelope['member_transactions']+=[main,end]
    return SystemSpec.model_validate_json(json.dumps(raw))

def build_r11_operation_plan(workload,ep_rank,**kwargs):
    from .r10_lowering import build_r10_operation_plan
    from .operation_plan import MoeOperationPlan
    from .workload import stable_sha256
    p=build_r10_operation_plan(workload,ep_rank,**kwargs)
    if workload.profile.name!='transformer-fused':return p
    raw=p.model_dump(mode='json',exclude={'plan_sha256'})
    s=next(s for s in raw['stages'] if s['stage_id']=='R11')
    s['attributes'].update(r11_combined_owner='R11.valid_block_topk_upload_read_broadcast_input_readiness_combined',r11_prefix_fanout_bytes=512,r11_dynamic_upload_bytes=12*p.V,r11_metadata_broadcast_bytes=3072,r11_metadata_delivered_bytes=3072*32,r11_counts_require_real_route=True,r11_controller_by_pe=[i%4 for i in range(32)],r11_input_readiness_only=True,r11_first_consumer='source weight_gather VECTOR_READ completion not available: TSim stalled at configuration frontier',r11_independent_read_noc_sync_costs=False)
    raw['plan_sha256']=stable_sha256(raw);return MoeOperationPlan.model_validate_json(json.dumps(raw))

def require_r11_spec_contract(spec,workload,bundle):
    if workload.profile.name!='transformer-fused':return
    spec=SystemSpec.model_validate_json(json.dumps(spec) if isinstance(spec,dict) else spec.model_dump_json());tx={t.transaction_id:t for t in spec.batch.transactions}
    entries=[e for e in bundle.entries if e.mechanism=='gm_r11_combined']
    for rank in workload.observed_ep_ranks:
        prefix=f'rank{rank}';main=prefix+'.R11.topk_allgather.combined';end=prefix+'.R11.input_ready';before=prefix+'.R10.complete'
        candidates=[e for e in entries if e.domain.v_min==next(r.valid_selection_count for r in workload.rank_valid_counts if r.ep_rank==rank)]
        if len(candidates)!=1:raise ValueError('R11 source VECTOR_READ consumer timing unavailable')
        if main not in tx or end not in tx or before not in tx:raise ValueError('R11 exact source stage/action domain missing')
        e=candidates[0];f=e.formula
        if tx[main].kind!='compute' or tx[main].duration_cycles!=f['combined_startup_counter']+f['combined_counter_per_valid_entry']*e.domain.v_min or before not in tx[main].depends_on or tx[end].kind!='signal' or main not in tx[end].depends_on:raise ValueError('R11 duration/order/terminal identity mismatch')
        if any(n.startswith(prefix+'.R11.') and n not in (main,end) for n in tx):raise ValueError('R11 duplicate GM/NoC/sync costs')
        if not any(n.startswith(prefix+'.R12.') and end in t.depends_on for n,t in tx.items()):raise ValueError('R11 first downstream R12 dependency missing')
