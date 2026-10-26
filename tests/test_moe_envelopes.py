import unittest,sys
from pathlib import Path

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.system_compile import compile_system
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.tests.test_unified_runtime import ring_graph,ring_batch

def spec(txs,envelopes,limit=1000):
 b=ring_batch(txs,max_cycles=limit);b['operation_envelopes']=envelopes
 return SystemSpec.model_validate(dict(kind='system_spec',schema_version=1,spec_id='envelope-test',graph=ring_graph(),batch=b))
def env(member=('x',),phases=()):return dict(operation_id='whole',member_transactions=list(member),phases=list(phases),boundary_ref='cpu-boundary')
def phase(key,duration,deps=(),coverage='non_network'):return dict(phase_id=key,duration_cycles=duration,depends_on=list(deps),evidence_ref='cpu-phase',coverage=coverage)
def compute(key,duration,deps=(),unit='eu0'):return dict(kind='compute',transaction_id=key,unit_id=unit,duration_cycles=duration,depends_on=list(deps),start_cycles=0)
class Envelopes(unittest.TestCase):
 def test_multicast_owner_prefix_and_after_all_envelope(self):
  from moe_fifo_fixtures import multicast_plan
  from simulator_detailed.configs.schemas.operation_envelope import OperationEnvelope
  from simulator_detailed.configs.schemas.system_spec import ImmutablePlan
  from simulator_detailed.topology import content_digest
  p=multicast_plan();e=OperationEnvelope.model_validate(env(('m0','m1'),[phase('tail',3,('m0','m1'))]));c=p.content.model_copy(update=dict(operation_envelopes=(e,)));p=ImmutablePlan.model_validate(dict(kind='immutable_plan',schema_version=p.schema_version,spec_sha256=p.spec_sha256,content=c.model_dump(mode='json'),plan_sha256=content_digest(c.model_dump(mode='json'))));ctx=RuntimeContext(p);r=ctx.run();ends={s.transaction_id:s.end_cycles for s in r.transactions};self.assertEqual(r.operation_envelopes[0]['end_cycles'],max(ends['m0'],ends['m1'])+3);self.assertNotEqual(ends['m0'],ends['m1'])
  events=[e for e in ctx._fifo_events if e['action']=='fifo_admit'and e['resource_id']=='prefix-in'];self.assertEqual(len({(e['packet_index'],e['owner'])for e in events}),3)
 def test_exception_prevents_envelope_and_successor(self):
  c=RuntimeContext(compile_system(spec([compute('x',10),compute('after',1,('whole',))],[env(phases=[phase('tail',3,('x',))])])));original=c._emit
  def fail(action,*args,**kwargs):
   if action=='unit_acquire':raise ValueError('CPU_INJECTED_FAILURE')
   return original(action,*args,**kwargs)
  c._emit=fail
  with self.assertRaises(RuntimeError):c.run()
  self.assertFalse(c._done['whole'].triggered);self.assertFalse(c._done['after'].triggered);self.assertTrue(c.registry.all_released())

 def test_no_double_count_network_member(self):
  r=RuntimeContext(compile_system(spec([compute('x',10)],[env(phases=[phase('tail',3,('x',))])]))).run();self.assertEqual(r.operation_envelopes[0]['duration_cycles'],13)
 def test_parallel_phase_overlaps_member(self):
  r=RuntimeContext(compile_system(spec([compute('x',10)],[env(phases=[phase('control',8)])]))).run();self.assertEqual(r.completion_cycles,10)
 def test_independent_parallel_members(self):
  r=RuntimeContext(compile_system(spec([compute('x',10),compute('y',12,unit='eu1')],[env(('x','y'))]))).run();self.assertEqual(r.completion_cycles,12)
 def test_shared_local_resource_serializes(self):
  r=RuntimeContext(compile_system(spec([compute('x',10),compute('y',12)],[env(('x','y'))]))).run();self.assertEqual(r.completion_cycles,22)
 def test_successor_waits_for_envelope(self):
  r=RuntimeContext(compile_system(spec([compute('x',10),compute('after',1,('whole',))],[env(phases=[phase('tail',3,('x',))])]))).run();sp={s.transaction_id:s for s in r.transactions};self.assertEqual(sp['after'].start_cycles,13)
 def test_no_network_existing_operation_unchanged(self):
  r=RuntimeContext(compile_system(spec([compute('x',10)],[env((),[phase('control',8)])]))).run();self.assertEqual(r.operation_envelopes[0]['duration_cycles'],8);self.assertEqual(r.completion_cycles,10)
 def test_covered_whole_quote_not_added_to_network(self):
  r=RuntimeContext(compile_system(spec([compute('x',10)],[env(phases=[phase('preserved',12,coverage='preserved_whole_operation')])]))).run();self.assertEqual(r.completion_cycles,12)
 def test_duplicate_network_cover_rejected(self):
  with self.assertRaises(Exception):spec([compute('x',10)],[env(phases=[phase('preserved',12,coverage='preserved_whole_operation'),phase('again',3,('x',))])])
 def test_cancel_no_completion_successor_and_cleanup(self):
  c=RuntimeContext(compile_system(spec([compute('x',10),compute('after',1,('whole',))],[env(phases=[phase('tail',3,('x',))])],limit=5)));r=c.run();self.assertEqual(r.status,'incomplete');self.assertIsNone(r.operation_envelopes[0]['end_cycles']);self.assertFalse(c._done['whole'].triggered);self.assertFalse(c._done['after'].triggered);self.assertTrue(c.registry.all_released())
if __name__=='__main__':unittest.main()
