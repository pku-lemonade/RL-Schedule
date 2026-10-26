import unittest
from simulator_detailed.moe_model.vector import VectorWork
from simulator_detailed.moe_model.vector_timing import RepeatedVectorFormula,RepeatedVectorSlice,BulkVectorFormula,BulkVectorSlice,PiecewiseBulkVectorFormula,PiecewiseBulkVectorSlice,TpcRoundAnchor,TpcBatchVectorSlice,TpcBatchVectorFormula,LongRepeatedVectorFormula,LongRepeatedVectorSlice,RecurringTpcVectorFormula,RecurringTpcVectorSlice

class VectorTests(unittest.TestCase):
 def test_rms_profiles(self):
  self.assertEqual(VectorWork(work_id='r',profile='standalone',operation='rmsnorm',work_items=1,pe_id=0).shape()['input_elements'],3072)
  self.assertEqual(VectorWork(work_id='r',profile='transformer-fused',operation='rmsnorm',work_items=1,pe_id=0).shape()['input_elements'],2048)
 def test_quant_padded_profiles(self):
  a=VectorWork(work_id='q',profile='standalone',operation='quant',work_items=1,pe_id=0).shape()
  b=VectorWork(work_id='q',profile='transformer-fused',operation='quant',work_items=1,pe_id=0).shape()
  self.assertEqual((a['input_elements'],a['quant_elements_per_shard']),(4096,512));self.assertEqual((b['input_elements'],b['quant_elements_per_shard']),(2048,256))
 def test_softmax_topk_filter_domain(self):
  s=VectorWork(work_id='s',profile='transformer-fused',operation='softmax_topk',work_items=1,pe_id=7).shape()
  self.assertEqual((s['input_elements'],s['top_k'],s['experts_per_rank']),(384,8,96))
 def test_silu_padding(self):
  a=VectorWork(work_id='s',profile='standalone',operation='silu_score_quant',work_items=3,pe_id=28).shape()
  b=VectorWork(work_id='s',profile='transformer-fused',operation='silu_score_quant',work_items=3,pe_id=31).shape()
  self.assertEqual((a['valid_output_elements'],a['padded_output_elements'],a['zero_pad_elements']),(320,512,192))
  self.assertEqual((b['valid_output_elements'],b['padded_output_elements'],b['zero_pad_elements']),(256,256,0))
 def test_silu_only_roots(self):
  with self.assertRaises(ValueError):VectorWork(work_id='s',profile='standalone',operation='silu_score_quant',work_items=1,pe_id=27)
 def test_zero_silu_skips_nonroot(self):
  self.assertEqual(VectorWork(work_id='s',profile='standalone',operation='silu_score_quant',work_items=0,pe_id=0).work_items,0)
 def test_table_does_not_move_weight(self):
  a=VectorWork(work_id='t',profile='standalone',operation='table',work_items=4,pe_id=0).shape()
  b=VectorWork(work_id='t',profile='transformer-fused',operation='table',work_items=4,pe_id=0).shape()
  self.assertFalse(a['weight_payload_moved']);self.assertEqual((a['mlp1_table_entries_per_record'],b['mlp1_table_entries_per_record']),(4,3))
 def test_named_stages(self):
  self.assertEqual(VectorWork(work_id='x',profile='standalone',operation='softmax_topk',work_items=1,pe_id=0).real_stage(),'R8/R9')
  self.assertEqual(VectorWork(work_id='x',profile='standalone',operation='table',work_items=1,pe_id=0).real_stage(),'R12')
 def test_bulk_semantics(self):
  self.assertTrue(VectorWork(work_id='x',profile='standalone',operation='table',work_items=4,pe_id=0).bulk_v())
  self.assertFalse(VectorWork(work_id='x',profile='standalone',operation='quant',work_items=4,pe_id=0).bulk_v())
 def test_physical_bytes(self):
  q=VectorWork(work_id='q',profile='standalone',operation='quant',work_items=1,pe_id=0)
  self.assertEqual(q.physical_sram_bytes(),4096*2+8*(512+128))
 def test_input_gates(self):
  with self.assertRaises(ValueError):VectorWork(work_id='',profile='standalone',operation='rmsnorm',work_items=1,pe_id=0)
  with self.assertRaises(ValueError):VectorWork(work_id='x',profile='standalone',operation='rmsnorm',work_items=257,pe_id=0)
 def test_repeated_timing_actions(self):
  slices=tuple(RepeatedVectorSlice(engine=e,subevent=0,first_start_cycles=s,first_duration_cycles=5,
   transient_start_cycles=(20,30,40),transient_duration_cycles=(6,6,6),steady_interval_cycles=10,steady_duration_cycles=6,parameter_path=e)
   for e,s in (('VECTOR_READ',0),('VECTOR_CAL',2),('VECTOR_WRITE',4)))
  f=RepeatedVectorFormula(profile='standalone',operation='quant',model_revision='r1',events_per_item=1,slices=slices,
   calibration_sha256='a'*64,source_refs=('V1','V4'),applicability='test')
  self.assertEqual([a.earliest_start for a in f.actions('w',0,2,('pe0',))],[0,2,4,20,20,20])
 def test_bulk_affine_and_zero_skip(self):
  slices=tuple(BulkVectorSlice(engine=e,start_base_cycles=0,start_per_item_cycles=1,duration_base_cycles=10,duration_per_item_cycles=2,parameter_path=e)
   for e in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))
  f=BulkVectorFormula(profile='standalone',operation='table',model_revision='r1',slices=slices,
   calibration_sha256='b'*64,source_refs=('V1','V4'),applicability='test')
  self.assertEqual(f.actions('w',0,0,('pe0',)),());self.assertEqual({a.duration_cycles for a in f.actions('w',0,3,('pe0',))},{16})
 def test_piecewise_bulk_branch(self):
  slices=tuple(PiecewiseBulkVectorSlice(engine=e,singleton_start_cycles=0,singleton_duration_cycles=5,
   multi_start_base_cycles=1,multi_start_per_item_cycles=2,multi_duration_base_cycles=10,multi_duration_per_item_cycles=3,parameter_path=e)
   for e in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))
  f=PiecewiseBulkVectorFormula(profile='standalone',model_revision='r2',slices=slices,calibration_sha256='c'*64,
   source_refs=('V1','V3','V4'),applicability='test')
  self.assertEqual({a.duration_cycles for a in f.actions('w',28,1,('pe28',))},{5})
  self.assertEqual({a.duration_cycles for a in f.actions('w',28,6,('pe28',))},{28})
 def test_tpc_round_remainder_interpolation_and_domain(self):
  rounds=(TpcRoundAnchor(round_index=1,first_start_cycles=0,first_duration_cycles=10,third_start_cycles=2,third_duration_cycles=14,full_start_cycles=3,full_duration_cycles=16),)
  slices=tuple(TpcBatchVectorSlice(engine=e,rounds=rounds,parameter_path=e) for e in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))
  f=TpcBatchVectorFormula(profile='standalone',operation='table',model_revision='r3',slices=slices,calibration_sha256='d'*64,source_refs=('V1','V3','V4'),applicability='V1..4')
  self.assertEqual({a.duration_cycles for a in f.actions('w',0,2,('pe0',))},{12})
  with self.assertRaises(ValueError):f.actions('w',0,5,('pe0',))
 def test_long_repeated_average_tail(self):
  slices=tuple(LongRepeatedVectorSlice(engine=e,subevent=0,first_start_cycles=0,first_duration_cycles=2,prefix_start_cycles=(5,10),prefix_duration_cycles=(3,3),
   tail_interval_cycles=5.5,tail_duration_cycles=3,parameter_path=e) for e in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))
  f=LongRepeatedVectorFormula(profile='standalone',operation='quant',model_revision='r4',events_per_item=1,slices=slices,calibration_sha256='e'*64,source_refs=('V1','V16'),applicability='test')
  self.assertEqual({a.earliest_start for a in f.actions('w',0,4,('pe0',))},{0,5,10,15.5})
 def test_recurring_tpc_round(self):
  prefix=tuple(TpcRoundAnchor(round_index=i,first_start_cycles=0,first_duration_cycles=i,third_start_cycles=0,third_duration_cycles=i+2,full_start_cycles=0,full_duration_cycles=i+3) for i in (1,2,3))
  slices=tuple(RecurringTpcVectorSlice(engine=e,prefix_rounds=prefix,round4_start_by_remainder=(0,0,0,0),round4_duration_by_remainder=(10,11,12,13),
   start_delta_per_round=(0,0,0,0),duration_delta_per_round=(4,4,4,4),parameter_path=e) for e in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'))
  f=RecurringTpcVectorFormula(profile='standalone',operation='table',model_revision='r4',slices=slices,calibration_sha256='f'*64,source_refs=('V13','V20'),applicability='test')
  self.assertEqual({a.duration_cycles for a in f.actions('w',0,22,('pe0',))},{19})
  with self.assertRaises(ValueError):f.actions('w',0,10,('pe0',))
