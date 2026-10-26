import unittest
from simulator_detailed.moe_model.collective import PEChain,CollectiveFormula
class CollectiveTests(unittest.TestCase):
 def test_column_members(self):
  p=PEChain(collective_id='col',axis='column',payload_bytes=2560,packet_bytes=128)
  self.assertEqual(p.groups()[0],(0,4,8,12,16,20,24,28));self.assertEqual(len(p.groups()),4)
 def test_row_members(self):
  p=PEChain(collective_id='row',axis='row',payload_bytes=1536,packet_bytes=128)
  self.assertEqual(p.groups()[7],(28,29,30,31));self.assertEqual(len(p.groups()),8)
 def test_root_self_send(self):
  p=PEChain(collective_id='c',axis='column',payload_bytes=128,packet_bytes=128).logical_plan()
  self.assertEqual(p['groups'][0]['root_self_send']['source'],28)
 def test_bytes_column(self):
  p=PEChain(collective_id='c',axis='column',payload_bytes=2560,packet_bytes=128).logical_plan()
  self.assertEqual(p['logical_reduce_link_bytes'],4*7*2560);self.assertEqual(p['broadcast_source_bytes'],4*2560)
 def test_bytes_row(self):
  p=PEChain(collective_id='r',axis='row',payload_bytes=1024,packet_bytes=128).logical_plan()
  self.assertEqual(p['logical_reduce_link_bytes'],8*3*1024)
 def test_streaming_not_whole_flow(self):
  p=PEChain(collective_id='r',axis='row',payload_bytes=256,packet_bytes=128)
  f=p.streaming_flows();self.assertEqual(f[3].depends_on,());self.assertEqual(len(f),8*(2*3+1))
 def test_broadcast_after_all_packets(self):
  f=PEChain(collective_id='r',axis='row',payload_bytes=256,packet_bytes=128).streaming_flows()
  self.assertEqual(len(f[6].depends_on),2)
 def test_invalid_alignment(self):
  with self.assertRaises(ValueError):PEChain(collective_id='r',axis='row',payload_bytes=3,packet_bytes=128)
 def test_unavailable_cost(self):
  f=CollectiveFormula(formula_id='unknown',source_refs=('missing C03',),status='unavailable',per_packet_merge_cycles=None,
   per_pe_issue_cycles=None,root_handoff_cycles=None,applicability='unknown')
  self.assertIsNone(f.root_handoff_cycles)
