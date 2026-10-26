import unittest
from simulator_detailed.moe_model.gm_service import GMTransaction,GMFormula,conflict_groups,gm_actions
class GMTests(unittest.TestCase):
 def tx(self,id='x',addr=0,op='write_sum',controller='gm0'):
  return GMTransaction(transaction_id=id,source_pe=0,token_id=0,output_shard=0,address=addr,payload_bytes=128,operation=op,controller_id=controller)
 def formula(self):return GMFormula(formula_id='fixture-only-linear',startup_cycles=1.0,cycles_per_byte=0.01,rmw_cycles_per_contribution=2.0,status='calibrated',calibration_sha256='a'*64,applicability='fixture only',source_refs=('fixture only',))
 def test_same_address_group(self):self.assertEqual(conflict_groups((self.tx('a'),self.tx('b')))[0]['conflict_multiplicity'],2)
 def test_different_address(self):self.assertEqual(len(conflict_groups((self.tx('a'),self.tx('b',128)))),2)
 def test_different_controller(self):self.assertEqual(len(conflict_groups((self.tx('a'),self.tx('b',controller='gm1')))),2)
 def test_ordinary_no_rmw(self):self.assertAlmostEqual(self.formula().service(self.tx(op='write')),2.28)
 def test_atomic_increment(self):self.assertAlmostEqual(self.formula().service(self.tx()),4.28)
 def test_resource_bound(self):self.assertEqual(gm_actions((self.tx(),),self.formula())[0].resource_id,'gm0')
 def test_bad_address(self):
  with self.assertRaises(ValueError):self.tx(addr=3)
 def test_unknown_controller(self):
  with self.assertRaises(ValueError):self.tx(controller='')
 def test_unavailable(self):
  f=GMFormula(formula_id='unknown',startup_cycles=None,cycles_per_byte=None,rmw_cycles_per_contribution=None,status='unavailable',calibration_sha256='a'*64,applicability='unknown',source_refs=('missing',))
  with self.assertRaises(ValueError):f.service(self.tx())
