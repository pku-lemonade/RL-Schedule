import unittest
from simulator_detailed.moe_model.matrix import MatrixWork
from simulator_detailed.moe_model.engine import EngineFormula,EngineSlice
from simulator_detailed.moe_model.engine_transient import TransientEngineFormula,TransientEngineSlice
class MatrixTests(unittest.TestCase):
 def test_router_bf16(self):
  w=MatrixWork(work_id='r',profile='standalone',operation='router',V=0,pe_id=0)
  self.assertEqual(w.shape(),(8,384,384));self.assertEqual(w.precision(),'BF16')
 def test_gate_w4(self):
  w=MatrixWork(work_id='g',profile='standalone',operation='gate_up',V=1,pe_id=31,expert_sequence=(0,))
  self.assertEqual(w.issued_flops(),655360)
 def test_down_padding(self):
  w=MatrixWork(work_id='d',profile='standalone',operation='down',V=1,pe_id=0,expert_sequence=(1,))
  self.assertEqual(w.shape(),(1,384,512))
 def test_fused(self):
  w=MatrixWork(work_id='d',profile='transformer-fused',operation='gate_up',V=1,pe_id=0,expert_sequence=(95,))
  self.assertEqual(w.shape(),(1,512,256))
 def test_no_weight_noc(self):
  w=MatrixWork(work_id='d',profile='standalone',operation='down',V=0,pe_id=0)
  self.assertFalse(w.weight_gather()['data_noc']);self.assertEqual(w.issued_flops(),0)
 def test_expert_sequence(self):
  with self.assertRaises(ValueError):MatrixWork(work_id='d',profile='standalone',operation='down',V=1,pe_id=0)
 def test_expert_ownership(self):
  with self.assertRaises(ValueError):MatrixWork(work_id='d',profile='standalone',operation='down',V=1,pe_id=0,expert_sequence=(100,))
 def test_engine_warmup_and_steady_timing(self):
  s=EngineSlice(engine='MATRIX',start_offset_cycles=10,duration_cycles=20,
   repeat_start_offset_cycles=40,repeat_interval_cycles=30,repeat_duration_cycles=25,parameter_path='matrix.p.op.MATRIX')
  f=EngineFormula(component='M05',operation='router',shape_key='standalone',model_revision='r2',slices=(s,),
   calibration_sha256='a'*64,source_refs=('V1','V4'),applicability='test')
  actions=f.actions('w',0,3,('pe0',))
  self.assertEqual([(a.earliest_start,a.duration_cycles) for a in actions],[(10,20),(40,25),(70,25)])
 def test_engine_repeat_interval_must_be_positive(self):
  with self.assertRaises(ValueError):
   EngineSlice(engine='MATRIX',start_offset_cycles=0,duration_cycles=1,
    repeat_start_offset_cycles=1,repeat_interval_cycles=0,repeat_duration_cycles=1,parameter_path='p')
 def test_transient_prefix_and_steady_tail(self):
  s=TransientEngineSlice(engine='MATRIX',first_start_cycles=0,first_duration_cycles=8,
   transient_start_cycles=(10,21,33),transient_duration_cycles=(9,10,10),
   steady_interval_cycles=12,steady_duration_cycles=10,parameter_path='matrix.p.op.MATRIX')
  f=TransientEngineFormula(component='M05',operation='router',shape_key='standalone',model_revision='r3',slices=(s,),
   calibration_sha256='b'*64,source_refs=('V1','V4'),applicability='test')
  actions=f.actions('w',0,6,('pe0',))
  self.assertEqual([(a.earliest_start,a.duration_cycles) for a in actions],[(0,8),(10,9),(21,10),(33,10),(45,10),(57,10)])
