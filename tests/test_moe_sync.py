import unittest
from simulator_detailed.moe_model.sync import SyncNode,freeze_sync_plan,execute_sync_plan
from moe_network_fixtures import fixture

def plan(nodes):return freeze_sync_plan(protocol_id='p',fixture_only=True,nodes=tuple(nodes),source_refs=('fixture-only',))
def sig(i,k,c,t=0,deps=(),parts=('pe0',)):return SyncNode(node_id=i,stage='S',kind=k,counter_id=c,delta=1,start_cycles=t,depends_on=deps,participants=parts)
def wait(i,k,c,n,deps=(),parts=('pe0',)):return SyncNode(node_id=i,stage='S',kind=k,counter_id=c,threshold=n,depends_on=deps,participants=parts)

class SyncTests(unittest.TestCase):
 def binding(self):return fixture(False)[1:]
 def test_inner_release_acquire(self):
  p=plan([sig('release','inner_release','inner',5),wait('acquire','inner_acquire','inner',1)])
  b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);s={x.transaction_id:x for x in e.result.transactions}
  self.assertEqual((s['acquire'].start_cycles,s['acquire'].end_cycles),(0,5))
 def test_outer_release_acquire(self):
  p=plan([sig('release','outer_release','outer',7,parts=('pe0','gm0')),wait('acquire','outer_acquire','outer',1,parts=('pe0','gm0'))])
  b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);self.assertEqual({c.counter_id:c.value for c in e.result.counters}['outer'],1)
 def test_counted_after_all(self):
  nodes=[sig(f's{i}','count_signal','join',i+1,parts=(f'pe{i}',)) for i in range(4)]+[wait('join','count_wait','join',4,parts=('gm0',))]
  b,h=self.binding();e=execute_sync_plan(plan(nodes),b,h,production=False);span={x.transaction_id:x for x in e.result.transactions}['join'];self.assertEqual(span.end_cycles,4)
 def test_v0_participant_still_signals(self):
  nodes=[sig(f's{i}','count_signal','join',0,parts=(f'pe{i}',)) for i in range(32)]+[wait('join','count_wait','join',32,parts=('gm0',))]
  b,h=self.binding();e=execute_sync_plan(plan(nodes),b,h,production=False);self.assertEqual(e.result.counters[0].updates,32)
 def test_calc_fence_orders_consumer(self):
  p=plan([sig('producer','count_signal','done',5),wait('ready','count_wait','done',1),sig('fence','fence_calc','fence',deps=('ready',)),wait('consumer','inner_acquire','fence',1)])
  b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);s={x.transaction_id:x for x in e.result.transactions};self.assertEqual(s['consumer'].end_cycles,s['fence'].end_cycles)
 def test_io_fence_kind_preserved(self):
  p=plan([sig('f','fence_io','fence')]);b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);self.assertEqual(e.receipt['nodes'][0]['kind'],'fence_io')
 def test_whole_boundary_actual_closure(self):
  p=plan([sig('s','count_signal','c',9),wait('w','count_wait','c',1)]);b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);self.assertEqual(e.result.operation_envelopes[0]['end_cycles'],9)
 def test_no_sync_cost_fabricated(self):
  p=plan([sig('s','count_signal','c')]);b,h=self.binding();e=execute_sync_plan(p,b,h,production=False);self.assertEqual(e.receipt['service_duration_cycles'],0)
 def test_unreachable_wait_rejected(self):
  with self.assertRaises(ValueError):plan([sig('s','count_signal','c'),wait('w','count_wait','c',2)])
 def test_forward_dependency_rejected(self):
  with self.assertRaises(ValueError):plan([sig('s','count_signal','c',deps=('later',)),sig('later','count_signal','c')])
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):plan([sig('s','count_signal','c'),sig('s','count_signal','c')])
 def test_kind_fields(self):
  with self.assertRaises(ValueError):SyncNode(node_id='x',stage='s',kind='inner_acquire',counter_id='c',delta=1,participants=('p',))
 def test_fixture_production_gate(self):
  p=plan([sig('s','count_signal','c')]);b,h=self.binding()
  with self.assertRaises(ValueError):execute_sync_plan(p,b,h)
 def test_determinism(self):
  p=plan([sig('s','count_signal','c',3),wait('w','count_wait','c',1)]);b,h=self.binding();a=execute_sync_plan(p,b,h,production=False);z=execute_sync_plan(p,b,h,production=False);self.assertEqual(a.receipt,z.receipt)
 def test_receipt_dependencies_participants(self):
  p=plan([sig('s','count_signal','c',parts=('pe0','pe1')),wait('w','count_wait','c',1,deps=('s',),parts=('gm0',))]);b,h=self.binding();e=execute_sync_plan(p,b,h,production=False)
  self.assertEqual(e.receipt['nodes'][1]['depends_on'],['s']);self.assertEqual(e.receipt['nodes'][0]['participants'],['pe0','pe1'])
 def test_cleanup(self):
  p=plan([sig('s','count_signal','c',100),wait('w','count_wait','c',1)]);b,h=self.binding();e=execute_sync_plan(p,b,h,production=False,cancel=('w',5));self.assertTrue(e.receipt['cleanup'])
