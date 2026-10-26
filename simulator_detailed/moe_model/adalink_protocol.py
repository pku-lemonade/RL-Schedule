"""M08 consuming counter generations on the existing unified runtime.

A supplied program binds each native wait/publication to a comm-id namespace,
rank and generation. No hardware decrement latency is assumed. Native packet
flows still use latency_fifo and local resource arbitration.
"""
from typing import Literal
from dataclasses import dataclass
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .adalink_execute import compile_adalink,_build_receipt,AdalinkExecution
from .receipt import ResourceObserver
from ..runtime_context import RuntimeContext
from ..quantity_resources import QuantityClaim
from ..runtime_cleanup import capture_failure
import simpy

class CommCounterAction(FrozenRecord):
 action_id:str
 rank:int|None=Field(default=None,ge=0)
 comm_id:int=Field(ge=0,lt=64)
 generation:int=Field(ge=0)
 counter_kind:Literal['producer','send','receive','credit']
 consume:int|None=Field(default=None,gt=0)
 increment:int|None=Field(default=None,gt=0)
 @model_validator(mode='after')
 def valid(self):
  if not self.action_id or (self.consume is None)==(self.increment is None):raise ValueError('counter action must publish or consume')
  return self
class CommCounterProgram(FrozenRecord):
 program_id:str
 fixture_only:bool
 schedule_sha256:str
 actions:tuple[CommCounterAction,...]
 source_refs:tuple[str,...]
 program_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if not self.program_id or not self.actions or not self.source_refs or len({a.action_id for a in self.actions})!=len(self.actions):raise ValueError('counter program incomplete/duplicate')
  if stable_sha256(self.model_dump(mode='json',exclude={'program_sha256'}))!=self.program_sha256:raise ValueError('counter program hash drift')
  return self
 def validate_schedule(self,schedule):
  if self.schedule_sha256!=schedule.schedule_sha256:raise ValueError('counter program schedule hash mismatch')
  native={a.action_id:a for a in schedule.actions if a.kind in ('signal','wait')}
  if set(native)!={a.action_id for a in self.actions}:raise ValueError('counter program must bind every publication/wait')
  ledger={};identities={};names={}
  if [p.action_id for p in self.actions]!=list(native):raise ValueError('comm actions must preserve schedule order')
  for p in self.actions:
   n=native[p.action_id];key=(p.rank,p.comm_id,p.generation,p.counter_kind)
   if n.kind=='signal' and p.increment!=n.delta or n.kind=='wait' and p.consume!=n.threshold:raise ValueError('counter program delta/consume mismatch')
   owner=names.setdefault(key,n.counter_id)
   if owner!=n.counter_id:raise ValueError('two counters alias one comm-id generation')
   old=identities.setdefault(n.counter_id,key)
   if old!=key:raise ValueError('counter generation/comm identity conflicts')
   if p.increment is not None:ledger[key]=ledger.get(key,0)+p.increment
   else:
    if ledger.get(key,0)<p.consume:raise ValueError('counter consumption unreachable')
    ledger[key]-=p.consume
  return native

def freeze_comm_counter_program(**kwargs):
 from pydantic import TypeAdapter
 raw=TypeAdapter(dict).dump_python(kwargs,mode='json');raw['program_sha256']=stable_sha256(raw)
 return CommCounterProgram.model_validate_json(json.dumps(raw))

class _ConsumingRuntime(RuntimeContext):
 def __init__(self,plan,program):
  super().__init__(plan);self.program={a.action_id:a for a in program.actions};self.counter_events=[]
  for t in plan.content.transactions:
   if t.transaction_id in self.program and self.program[t.transaction_id].consume is not None:
    resource='comm-counter.'+t.counter_id
    if resource not in self.registry._quantity_pools:self.registry.register_quantity(resource,1,'waiter')
 def _signal(self,t):
  p=self.program.get(t.transaction_id)
  if p is None:return (yield from super()._signal(t))
  yield from super()._signal(t)
  before=self.bus.counter(t.counter_id).value-p.increment
  self.counter_events.append(dict(action_id=t.transaction_id,counter_id=t.counter_id,rank=p.rank,comm_id=p.comm_id,generation=p.generation,counter_kind=p.counter_kind,time=float(self.env.now),operation='publish',amount=p.increment,before=before,after=self.bus.counter(t.counter_id).value))
 def _wait(self,t):
  p=self.program.get(t.transaction_id)
  if p is None:return (yield from super()._wait(t))
  lease=None
  try:
   yield from self._start(t.depends_on,t.start_cycles);started=float(self.env.now)
   lease=yield from self.registry.acquire_quantities((QuantityClaim('comm-counter.'+t.counter_id,1,'waiter'),),t.transaction_id)
   c=self.bus.counter(t.counter_id)
   while c.value<p.consume:yield c.changed
   before=c.value;c.value-=p.consume;c.changed.succeed();c.changed=self.env.event()
   self.counter_events.append(dict(action_id=t.transaction_id,counter_id=t.counter_id,rank=p.rank,comm_id=p.comm_id,generation=p.generation,counter_kind=p.counter_kind,requested=started,time=float(self.env.now),operation='consume',amount=p.consume,before=before,after=c.value))
   self._emit('wait_resume',t.transaction_id,t.counter_id);self._finish(t.transaction_id,'wait',started,float(self.env.now)-started)
  except simpy.Interrupt:pass
  except Exception as exc:capture_failure(self,exc);raise
  finally:
   if lease:self.registry.release_quantities(lease)

def execute_consuming_adalink(schedule,binding,hardware,costs,program,*,production=True,max_cycles=1000000,cancel=None,inject_failure=False,link_parameters=None):
 program=CommCounterProgram.model_validate_json(program.model_dump_json());program.validate_schedule(schedule)
 if production and program.fixture_only:raise ValueError('production forbids fixture counter program')
 compiled=compile_adalink(schedule,binding,hardware,costs,production=production,max_cycles=max_cycles)
 if link_parameters is not None:compiled=apply_adalink_link_parameters(compiled,link_parameters,production=production)
 runtime=_ConsumingRuntime(compiled.plan,program);observer=ResourceObserver(runtime)
 if cancel:
  if cancel[0] not in {a.action_id for a in schedule.actions}|{schedule.schedule_id+'.collective'}:raise ValueError('unknown cancellation target')
  def cancelling():yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
  runtime.env.process(cancelling())
 if inject_failure:
  original=runtime._emit
  def emit(action,*args,**kwargs):
   if action=='fifo_event' and 'packet_forward' in str(kwargs.get('detail',args[-1] if args else '')):raise RuntimeError('C08_PACKET_FAILURE')
   return original(action,*args,**kwargs)
  runtime._emit=emit
 result=None;error=None
 try:result=runtime.run()
 except RuntimeError as exc:error=str(exc)
 receipt=_build_receipt(compiled,runtime,result,observer,error);receipt['comm_program_sha256']=program.program_sha256;receipt['consuming_counter_events']=runtime.counter_events;receipt['counter_semantics']='explicit generation consume; no decrement latency calibration assumed'
 receipt['link_parameter_sha256']=None if link_parameters is None else link_parameters.parameters_sha256
 receipt['hardware_with_link_parameters_sha256']=stable_sha256(dict(hardware_sha256=hardware.parameter_sha256,link_parameters_sha256=receipt['link_parameter_sha256']))
 return AdalinkExecution(compiled,runtime,result,receipt,error)

def mutate_local_reduce(costs,rank,*,startup=None,cycles_per_byte=None):
 from .adalink_binding import AdalinkCostTable
 raw=costs.model_dump(mode='json');raw.pop('table_sha256');rows=[r for r in raw['costs'] if r['rank']==rank]
 if len(rows)!=1:raise ValueError('local reduce rank must uniquely match')
 row=rows[0]
 if startup is not None:
  if not math.isfinite(startup) or startup<0:raise ValueError('invalid reduce startup')
  row['startup_cycles']=float(startup)
 if cycles_per_byte is not None:
  if not math.isfinite(cycles_per_byte) or cycles_per_byte<0:raise ValueError('invalid reduce throughput')
  row['cycles_per_byte']=float(cycles_per_byte)
 raw['table_sha256']=stable_sha256(raw)
 return AdalinkCostTable.model_validate_json(json.dumps(raw))

class AdalinkLinkParameter(FrozenRecord):
 parameter_path:str
 network_id:str
 link_id:str
 effect:Literal['fifo_slots','channel_slots','input_latency']
 value:int|float
 unit:Literal['packet slot','cycle']
 calibration_status:Literal['fixture_only','uncalibrated','calibrated']
 source_ref:str
 @model_validator(mode='after')
 def valid(self):
  if self.parameter_path!=f'adalink.links.{self.link_id}.{self.effect}' or not self.source_ref:raise ValueError('exact ADALINK link path/source required')
  if isinstance(self.value,bool) or not math.isfinite(self.value):raise ValueError('nonfinite link value')
  if self.effect in ('fifo_slots','channel_slots'):
   if self.unit!='packet slot' or self.value<=0 or int(self.value)!=self.value:raise ValueError('invalid link capacity unit/value')
  elif self.unit!='cycle' or self.value<0:raise ValueError('invalid input latency unit/value')
  return self

class AdalinkLinkParameterSet(FrozenRecord):
 fixture_only:bool
 parameters:tuple[AdalinkLinkParameter,...]
 parameters_sha256:str
 @model_validator(mode='after')
 def valid(self):
  if not self.parameters or len({p.parameter_path for p in self.parameters})!=len(self.parameters):raise ValueError('link params incomplete/duplicate')
  if stable_sha256(self.model_dump(mode='json',exclude={'parameters_sha256'}))!=self.parameters_sha256:raise ValueError('link params hash drift')
  return self

def freeze_adalink_link_parameters(**kwargs):
 from pydantic import TypeAdapter
 raw=TypeAdapter(dict).dump_python(kwargs,mode='json');raw['parameters_sha256']=stable_sha256(raw)
 return AdalinkLinkParameterSet.model_validate_json(json.dumps(raw))

def apply_adalink_link_parameters(compiled,parameters,*,production):
 from .adalink_execute import CompiledAdalink
 from ..configs.schemas.generic_transactions import GenericTransactionBatch
 from ..configs.schemas.system_spec import SystemSpec
 from ..system_compile import compile_system
 import copy
 parameters=AdalinkLinkParameterSet.model_validate_json(parameters.model_dump_json())
 if production and (parameters.fixture_only or any(p.calibration_status!='calibrated' for p in parameters.parameters)):raise ValueError('physical link parameters uncalibrated/fixture')
 raw=compiled.spec.model_dump(mode='json');links={(l['network_id'],l['link_id']) for l in raw['graph']['links']}
 for p in parameters.parameters:
  if (p.network_id,p.link_id) not in links:raise ValueError('exact parameter link absent from binding')
  n=next(n for n in raw['batch']['timing'] if n['network_id']==p.network_id);overrides={r['link_id']:r['timing'] for r in n.get('overrides',[])};t=copy.deepcopy(overrides.get(p.link_id,n['link']))
  if p.effect=='input_latency' and t['stage']=='egress':raise ValueError('egress has no input visibility latency; bind an ingress/transit resource')
  t['input_latency_cycles' if p.effect=='input_latency' else p.effect]=p.value if p.effect=='input_latency' else int(p.value)
  overrides[p.link_id]=t;n['overrides']=[dict(link_id=k,timing=v) for k,v in sorted(overrides.items())]
 batch=GenericTransactionBatch.model_validate_json(json.dumps(raw['batch']));raw['batch']=batch.model_dump(mode='json');spec=SystemSpec.model_validate_json(json.dumps(raw));plan=compile_system(spec)
 return CompiledAdalink(compiled.schedule,compiled.binding,compiled.hardware,compiled.costs,spec,plan,raw['batch']['timing'])
