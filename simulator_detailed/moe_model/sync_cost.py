"""C07 exact-domain adapter; reported units remain explicitly nonphysical."""
from .sync_resource import SyncLocalFormula,SyncResource

def sync_formula_resource(formula,*,resource_id,endpoint_role,native_unit_id,capacity,queue_slots,calibration_sha256):
 formula=SyncLocalFormula.model_validate_json(formula.model_dump_json())
 return SyncResource(resource_id=resource_id,endpoint_role=endpoint_role,native_unit_id=native_unit_id,
  latency_path='sync.resources.'+resource_id+'.latency',capacity_path='sync.resources.'+resource_id+'.capacity',queue_path='sync.resources.'+resource_id+'.queue_slots',
  latency=formula.service_latency,capacity=capacity,queue_slots=queue_slots,unit=formula.unit,status='validated_limited',calibration_sha256=calibration_sha256,physical_calibrated=False,applicability=formula.applicability)

def require_sync_formula_domain(formula,*,profile,V,operation,load,producers,waiters,rounds,frontier,route_domain):
 formula=SyncLocalFormula.model_validate_json(formula.model_dump_json())
 expected_frontier='producer_ready' if formula.operation=='inner_release_acquire' else 'engines_empty'
 if (profile,V,operation,load,producers,waiters)!=(formula.profile,formula.V,formula.operation,formula.load,formula.producers,formula.waiters):raise ValueError('C07 exact operation/profile/V/concurrency domain mismatch')
 if rounds!=8 or frontier!=expected_frontier or route_domain!='local-pe-eu-unbound':raise ValueError('C07 rounds/frontier/real resource binding outside validated domain')
 return formula

def execute_calibrated_empty_fence(bundle,request,binding,*,production=True,mutations=()):
 """Compose frozen scalar boundary overhead and per-fence queue service.

 `binding` supplies the native endpoint/unit and local capacity. The loop
 overhead belongs to this observer/harness boundary, not to a physical fence.
 Mutations change one exact local path while the scene and binding stay fixed.
 """
 from .component_calibration import resolve_calibration
 from .sync import SyncNode,freeze_sync_plan
 from .sync_resource import freeze_sync_resource_binding,execute_sync_resources,mutate_sync_resource
 resolution=resolve_calibration(bundle,request);entry=next(e for e in bundle.entries if e.entry_id==resolution.entry_id)
 if entry.formula_id!='M07-local-queue-r1' or request.operation not in ('fence_calc','fence_io'):raise ValueError('calibrated fence entry required')
 formula=SyncLocalFormula.model_validate_json(__import__('json').dumps(entry.formula))
 require_sync_formula_domain(formula,profile=request.profile,V=request.V,operation=request.operation,load=request.scenario_class,producers=request.producer_count,waiters=request.waiter_count,rounds=request.rounds,frontier=request.frontier,route_domain=request.route_domain)
 if len(binding.resources)!=1:raise ValueError('empty-fence supplied binding needs one exact local resource')
 supplied=binding.resources[0]
 service=sync_formula_resource(formula,resource_id=supplied.resource_id,endpoint_role=supplied.endpoint_role,native_unit_id=supplied.native_unit_id,capacity=supplied.capacity,queue_slots=supplied.queue_slots,calibration_sha256=entry.entry_sha256)
 nodes=[];resources=[service];mapping={}
 if formula.intercept>0:
  name=supplied.resource_id+'.observer_overhead'
  overhead=SyncResource(resource_id=name,endpoint_role=supplied.endpoint_role,native_unit_id=supplied.native_unit_id,latency_path='sync.resources.'+name+'.latency',capacity_path='sync.resources.'+name+'.capacity',queue_path='sync.resources.'+name+'.queue_slots',latency=formula.intercept,capacity=1,queue_slots=1,unit=formula.unit,status='validated_limited',calibration_sha256=entry.entry_sha256,physical_calibrated=False,applicability='frozen scalar-loop observation overhead; not physical sync cost')
  resources.append(overhead);nodes.append(SyncNode(node_id='observer.setup',stage=request.consumer_stage,kind='count_signal',counter_id='observer',delta=1,participants=(supplied.endpoint_role,)));mapping['observer.setup']=name
 for i in range(request.rounds):
  node=SyncNode(node_id='fence.'+str(i),stage=request.consumer_stage,kind=request.operation,counter_id='fence',delta=1,depends_on=() if not nodes else (nodes[-1].node_id,),participants=(supplied.endpoint_role,));nodes.append(node);mapping[node.node_id]=service.resource_id
 plan=freeze_sync_plan(protocol_id='c07-calibrated-empty-fence',fixture_only=binding.fixture_only,nodes=tuple(nodes),source_refs=entry.source_refs)
 frozen=freeze_sync_resource_binding(binding_id=binding.binding_id,fixture_only=binding.fixture_only,graph=binding.graph,timing=binding.timing,resources=tuple(resources),node_resources=mapping,source_refs=binding.source_refs+entry.source_refs)
 for path,value in mutations:
  if path not in {service.latency_path,service.capacity_path,service.queue_path}:raise ValueError('mutation may only target exact local sync service')
  frozen=mutate_sync_resource(frozen,path,value)
 result=execute_sync_resources(plan,frozen,production=production)
 result.receipt['resolution_sha256']=resolution.request_sha256;result.receipt['accepted_entry_sha256']=entry.entry_sha256;result.receipt['scalar_observer_overhead']=formula.intercept
 return result

def add_counted_join_causality(execution):
 """Connect observed final required publications to native counter readiness.

 Native waits are non-consuming monotonic counters. Repeated hardware credit
 consumption needs separate generations/thresholds and remains outside C07.
 """
 from .adalink_execute import _critical_to_completion
 receipt=execution.receipt;graph=receipt['causal_graph'];nodes={n['node_id']:n for n in receipt['nodes']}
 if execution.result is None:return execution
 spans={s.transaction_id:s for s in execution.result.transactions};ids={n['id'] for n in graph['nodes']}
 for node in nodes.values():
  if node.get('threshold') is None:continue
  producers=[n for n in nodes.values() if n.get('delta') is not None and n['counter_id']==node['counter_id'] and n['node_id'] in spans and spans[n['node_id']].status=='complete']
  producers.sort(key=lambda n:(spans[n['node_id']].end_cycles,n['node_id']));value=0;required=None
  for producer in producers:
   value+=producer['delta']
   if value>=node['threshold']:required=producer;break
  if required is None:continue
  publications=[e for e in graph['nodes'] if e.get('transaction_id')==required['node_id'] and e.get('action')=='counter_publish']
  resumed=[e for e in graph['nodes'] if e.get('transaction_id')==node['node_id']+'.counter-ready' and e.get('action')=='wait_resume']
  if publications and resumed:graph['edges'].append(dict(source=publications[-1]['id'],target=resumed[-1]['id'],kind='counted-final-required-publication'))
 # Local resource release/grant events retain ordering even at equal timestamps.
 for event in receipt['local_services']:
  if event['grant'] is None:continue
  blockers=[p for p in receipt['local_services'] if p is not event and p['resource_id']==event['resource_id'] and p['end'] is not None and p['end']<=event['grant'] and p['end']>=(event['requested'] or 0)]
  if not blockers:continue
  blocker=max(blockers,key=lambda p:p['end'])
  release=[n for n in graph['nodes'] if n.get('transaction_id')==blocker['transaction_id'] and n.get('action')=='unit_release']
  grant=[n for n in graph['nodes'] if n.get('transaction_id')==event['transaction_id'] and n.get('action')=='unit_acquire']
  if release and grant:graph['edges'].append(dict(source=release[-1]['id'],target=grant[-1]['id'],kind='local-resource-causal-release'))
 receipt['counter_semantics']='monotonic once-per-generation threshold; no hardware decrement cost assumed'
 receipt['event_resource_critical_path']=_critical_to_completion(graph,receipt['nodes'][-1]['node_id'])
 return execution

def compare_sync_parameter(sync_plan,binding,path,value,*,production=True):
 from .sync_resource import execute_sync_resources,mutate_sync_resource
 from .workload import stable_sha256
 def structural(b):
  raw=b.model_dump(mode='json');raw.pop('binding_sha256')
  for r in raw['resources']:
   for field in ('latency','capacity','queue_slots','physical_calibrated'):r.pop(field)
  return stable_sha256(raw)
 changed=mutate_sync_resource(binding,path,value)
 a=add_counted_join_causality(execute_sync_resources(sync_plan,binding,production=production));z=add_counted_join_causality(execute_sync_resources(sync_plan,changed,production=production))
 target=next(r.resource_id for r in binding.resources if path in (r.latency_path,r.capacity_path,r.queue_path))
 ends=lambda e:{n['node_id']:None if n['completion'] is None else n['completion']['end_cycles'] for n in e.receipt['nodes']}
 before,after=ends(a),ends(z)
 return dict(workload_sha256=sync_plan.plan_sha256,binding_structure_sha256=structural(binding),changed_binding_structure_sha256=structural(changed),parameter_path=path,target_resource_id=target,
  base_hardware_sha256=a.receipt['hardware_sha256'],changed_hardware_sha256=z.receipt['hardware_sha256'],direct_action_ids=sorted(n for n,r in binding.node_resources.items() if r==target),
  completion_deltas={k:None if before[k] is None or after[k] is None else after[k]-before[k] for k in before},base_receipt=a.receipt,changed_receipt=z.receipt,physical_publishable=False)
