"""M07 supplied local synchronization queues; no protocol-wide fixed duration.

Native counters preserve dependency readiness. Resource admission and service
are local quantity leases in the existing RuntimeContext environment. Reported
TSim units never imply silicon clock calibration.
"""
from dataclasses import dataclass
from typing import Literal
from types import SimpleNamespace
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .sync import SyncPlan
from .receipt import ResourceObserver
from .adalink_execute import _critical_to_completion
from ..quantity_resources import QuantityClaim
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system
from ..runtime_context import RuntimeContext

class SyncResource(FrozenRecord):
 resource_id:str=Field(min_length=1)
 endpoint_role:str=Field(min_length=1)
 native_unit_id:str=Field(min_length=1)
 latency_path:str=Field(min_length=1)
 capacity_path:str=Field(min_length=1)
 queue_path:str=Field(min_length=1)
 latency:float|None=Field(default=None,gt=0)
 capacity:int=Field(gt=0)
 queue_slots:int=Field(gt=0)
 unit:Literal['reported_ns','reported_cycle','fixture_cycle','cycle']
 status:Literal['validated_limited','fixture_only','unavailable']
 calibration_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
 physical_calibrated:bool=False
 applicability:str=Field(min_length=1)
 @model_validator(mode='after')
 def valid(self):
  if self.queue_slots<self.capacity:raise ValueError('sync queue must hold all servers')
  if self.status=='unavailable' and self.latency is not None:raise ValueError('unavailable sync latency must be null')
  if self.status!='unavailable' and (self.latency is None or not math.isfinite(self.latency)):raise ValueError('sync latency missing/nonfinite')
  if len({self.latency_path,self.capacity_path,self.queue_path})!=3 or any(not p.startswith('sync.resources.') for p in (self.latency_path,self.capacity_path,self.queue_path)):raise ValueError('sync parameter paths must be exact and distinct')
  if self.physical_calibrated and (self.unit!='cycle' or self.status!='validated_limited'):raise ValueError('physical sync requires cycle calibration')
  return self

class SyncResourceBinding(FrozenRecord):
 binding_id:str
 fixture_only:bool
 graph:dict
 timing:tuple[dict,...]
 resources:tuple[SyncResource,...]
 node_resources:dict[str,str]
 source_refs:tuple[str,...]
 binding_sha256:str
 @model_validator(mode='after')
 def valid(self):
  from ..configs.schemas.generic_graph import GenericSystemGraph
  graph=GenericSystemGraph.model_validate_json(json.dumps(self.graph));units={x.unit_id for x in graph.execution_units}
  ids=[r.resource_id for r in self.resources];paths=[p for r in self.resources for p in (r.latency_path,r.capacity_path,r.queue_path)]
  if not self.binding_id or not self.resources or len(ids)!=len(set(ids)) or len(paths)!=len(set(paths)) or not self.source_refs:raise ValueError('sync binding incomplete/duplicate')
  if any(r.native_unit_id not in units for r in self.resources) or not set(self.node_resources.values())<=set(ids):raise ValueError('sync resource absent from supplied binding')
  if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('sync binding hash mismatch')
  return self

def freeze_sync_resource_binding(**kwargs):
 from pydantic import TypeAdapter
 raw=TypeAdapter(dict).dump_python(kwargs,mode='json');raw['binding_sha256']=stable_sha256(raw)
 return SyncResourceBinding.model_validate_json(json.dumps(raw))

class SyncLocalFormula(FrozenRecord):
 revision:Literal['M07-local-queue-r1']='M07-local-queue-r1'
 operation:str
 profile:Literal['standalone','transformer-fused']
 V:int=Field(ge=0,le=256)
 load:str
 producers:int=Field(gt=0,le=32)
 waiters:int=Field(gt=0,le=32)
 rounds:tuple[int,...]
 service_latency:float=Field(ge=0)
 unit:Literal['reported_ns','reported_cycle']='reported_ns'
 measurement_mode:str
 intercept:float=Field(ge=0)
 service_count_per_round:int=Field(gt=0)
 source_refs:tuple[str,...]
 applicability:str
 formula_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if not math.isfinite(self.service_latency) or not self.rounds or not self.source_refs or not self.applicability:raise ValueError('sync formula incomplete')
  if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('sync formula hash mismatch')
  return self

def freeze_sync_local_formula(**kwargs):
 from pydantic import TypeAdapter
 raw=dict(revision='M07-local-queue-r1',**kwargs)
 return SyncLocalFormula(**raw,formula_sha256=stable_sha256(TypeAdapter(dict).dump_python(raw,mode='json')))

class _LocalSyncRuntime(RuntimeContext):
 def __init__(self,plan,services):
  super().__init__(plan);self.services=services;self.sync_events=[]
  for r in {r.resource_id:r for r in services.values()}.values():
   self.registry.register_quantity(r.resource_id+'.queue',r.queue_slots,'entry')
   self.registry.register_quantity(r.resource_id+'.servers',r.capacity,'entry')
 def _compute(self,t):
  if t.transaction_id not in self.services:return (yield from super()._compute(t))
  r=self.services[t.transaction_id];queue=None;server=None;record=dict(transaction_id=t.transaction_id,resource_id=r.resource_id,requested=None,admitted=None,grant=None,end=None,capacity=r.capacity,queue_slots=r.queue_slots,unit=r.unit)
  self.sync_events.append(record)
  try:
   yield from self._start(t.depends_on,t.start_cycles);record['requested']=float(self.env.now)
   queue=yield from self.registry.acquire_quantities((QuantityClaim(r.resource_id+'.queue',1,'entry'),),t.transaction_id)
   record['admitted']=float(self.env.now)
   server=yield from self.registry.acquire_quantities((QuantityClaim(r.resource_id+'.servers',1,'entry'),),t.transaction_id)
   record['grant']=float(self.env.now);self._emit('unit_acquire',t.transaction_id,r.resource_id)
   yield self.env.timeout(t.duration_cycles);record['end']=float(self.env.now)
   self._emit('unit_release',t.transaction_id,r.resource_id)
   self._finish(t.transaction_id,'compute',record['grant'],record['grant']-record['requested'])
  finally:
   for lease in (server,queue):
    if lease:self.registry.release_quantities(lease)

@dataclass(frozen=True)
class SyncResourceExecution:
 result:object
 runtime:object
 receipt:dict
 error:str|None

def execute_sync_resources(sync_plan,binding,*,production=True,max_time=100000,cancel=None,inject_failure=False):
 sync_plan=SyncPlan.model_validate_json(sync_plan.model_dump_json());binding=SyncResourceBinding.model_validate_json(binding.model_dump_json())
 if production and (sync_plan.fixture_only or binding.fixture_only):raise ValueError('production sync rejects fixtures')
 if production and any(not r.physical_calibrated for r in binding.resources):raise ValueError('physical sync parameters uncalibrated')
 if set(binding.node_resources)!={n.node_id for n in sync_plan.nodes}:raise ValueError('every sync action needs an exact resource binding')
 resources={r.resource_id:r for r in binding.resources};tx=[];services={};counters=sorted({n.counter_id for n in sync_plan.nodes})
 units={r.unit for r in binding.resources}
 if len(units)!=1:raise ValueError('sync units conflict')
 for n in sync_plan.nodes:
  r=resources[binding.node_resources[n.node_id]]
  if r.latency is None:raise ValueError('sync latency unavailable')
  deps=list(n.depends_on)
  if n.threshold is not None:
   token=n.node_id+'.counter-ready';tx.append(dict(kind='wait',transaction_id=token,counter_id=n.counter_id,threshold=n.threshold,depends_on=deps,start_cycles=n.start_cycles));deps=[token]
  service=n.node_id+'.service';services[service]=r
  tx.append(dict(kind='compute',transaction_id=service,unit_id=r.native_unit_id,duration_cycles=r.latency,depends_on=deps,start_cycles=n.start_cycles))
  if n.delta is not None:tx.append(dict(kind='signal',transaction_id=n.node_id,counter_id=n.counter_id,delta=n.delta,depends_on=[service],start_cycles=0))
  else:
   done=n.node_id+'.done';counters.append(done);tx.append(dict(kind='signal',transaction_id=n.node_id,counter_id=done,delta=1,depends_on=[service],start_cycles=0))
 batch=GenericTransactionBatch.model_validate_json(json.dumps(dict(kind='generic_transaction_batch',schema_version=1,batch_id=sync_plan.protocol_id,timing=binding.timing,counters=[dict(counter_id=c,initial_value=0) for c in sorted(set(counters))],transactions=tx,max_cycles=max_time,operation_envelopes=[dict(operation_id=sync_plan.protocol_id+'.whole',member_transactions=[n.node_id for n in sync_plan.nodes],boundary_ref='local-sync-member-closure')])))
 spec=SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec',schema_version=1,spec_id=sync_plan.protocol_id,graph=binding.graph,batch=batch.model_dump(mode='json'))));plan=compile_system(spec)
 runtime=_LocalSyncRuntime(plan,services);observer=ResourceObserver(runtime)
 if cancel:
  if cancel[0] not in {t['transaction_id'] for t in tx}|{sync_plan.protocol_id+'.whole'}:raise ValueError('unknown sync cancellation target')
  def cancelling():yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
  runtime.env.process(cancelling())
 if inject_failure:
  original=runtime._emit
  def emit(action,*args,**kwargs):
   if action=='unit_acquire':raise RuntimeError('C07_INJECTED_SERVICE_FAILURE')
   return original(action,*args,**kwargs)
  runtime._emit=emit
 result=None;error=None
 try:result=runtime.run()
 except RuntimeError as exc:error=str(exc)
 spans={} if result is None else {s.transaction_id:s for s in result.transactions};graph=observer.causal_graph(SimpleNamespace(plan=plan),spans)
 # Add local queue causality to the observed graph, retaining exact resource identity.
 for event in runtime.sync_events:
  if event['grant'] is None:continue
  previous=[e for e in runtime.sync_events if e is not event and e['resource_id']==event['resource_id'] and e['end'] is not None and e['end']<=event['grant'] and e['end']>=(event['requested'] or 0)]
  if previous:
   p=max(previous,key=lambda e:e['end']);graph['edges'].append(dict(source=p['transaction_id']+'.complete',target=event['transaction_id']+'.start',kind='sync-resource-queue'))
 receipt=dict(schema='t81.m07.local-resource.v1',status='failed' if error else result.status,error=error,workload_sha256=sync_plan.plan_sha256,binding_sha256=binding.binding_sha256,hardware_sha256=stable_sha256([r.model_dump(mode='json') for r in binding.resources]),calibration_sha256=stable_sha256([r.calibration_sha256 for r in binding.resources]),native_plan_sha256=plan.plan_sha256,reported_unit=next(iter(units)),physical_publishable=production and error is None,
  nodes=[dict(n.model_dump(mode='json'),completion=None if n.node_id not in spans else spans[n.node_id].model_dump(mode='json')) for n in sync_plan.nodes],local_services=runtime.sync_events,
  resources=[dict(resource_id=r.resource_id,capacity=r.capacity,queue_slots=r.queue_slots,latency=r.latency,unit=r.unit,wait=sum((e['grant']-e['requested']) for e in runtime.sync_events if e['resource_id']==r.resource_id and e['grant'] is not None),occupancy=sum((e['end']-e['grant']) for e in runtime.sync_events if e['resource_id']==r.resource_id and e['end'] is not None)) for r in binding.resources],
  counters=[] if result is None else [c.model_dump(mode='json') for c in result.counters],whole_boundary=[] if result is None else list(result.operation_envelopes),causal_graph=graph,event_resource_critical_path=_critical_to_completion(graph,sync_plan.nodes[-1].node_id),cleanup=runtime.registry.all_released(),fixture_only=sync_plan.fixture_only or binding.fixture_only,architecture_benefit_claim=False)
 return SyncResourceExecution(result,runtime,receipt,error)

def mutate_sync_resource(binding,path,value):
 raw=binding.model_dump(mode='json');raw.pop('binding_sha256');matches=[]
 for row in raw['resources']:
  for field,key in [('latency','latency_path'),('capacity','capacity_path'),('queue_slots','queue_path')]:
   if row[key]==path:row[field]=float(value) if field=='latency' else value;row['physical_calibrated']=False;matches.append(row['resource_id'])
 if len(matches)!=1:raise ValueError('sync mutation needs one exact declared resource parameter')
 return freeze_sync_resource_binding(**raw)
