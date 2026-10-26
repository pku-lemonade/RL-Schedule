import unittest
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.configs.schemas.system_spec import ImmutablePlan
from simulator_detailed.configs.schemas.operation_envelope import OperationEnvelope
from simulator_detailed.topology import content_digest
from moe_fifo_fixtures import same_path_plan,parallel_plan

def envelope_plan(p,members):
 e=OperationEnvelope.model_validate(dict(operation_id='whole',member_transactions=members,phases=[dict(phase_id='tail',duration_cycles=3,depends_on=members,evidence_ref='cpu-event')],boundary_ref='cpu'))
 c=p.content.model_copy(update=dict(operation_envelopes=(e,)));return ImmutablePlan.model_validate(dict(kind='immutable_plan',schema_version=p.schema_version,spec_sha256=p.spec_sha256,content=c.model_dump(mode='json'),plan_sha256=content_digest(c.model_dump(mode='json'))))
class LiveFifoEnvelopeTests(unittest.TestCase):
 def test_shared_fifo_member_end_controls_whole(self):
  p=same_path_plan([24,8]);ids=[t.transaction_id for t in p.content.transactions];r=RuntimeContext(envelope_plan(p,ids)).run();self.assertEqual(r.operation_envelopes[0]['end_cycles'],max(s.end_cycles for s in r.transactions)+3);self.assertTrue(all(m['network_completion_cycles']==m['end_cycles']for m in r.operation_envelopes[0]['members']))
 def test_independent_fifo_groups_overlap(self):
  p=parallel_plan([dict(id='a',payload=24),dict(id='b',payload=24)]);ids=[t.transaction_id for t in p.content.transactions];r=RuntimeContext(envelope_plan(p,ids)).run();ends=[s.end_cycles for s in r.transactions];self.assertEqual(ends[0],ends[1]);self.assertEqual(r.operation_envelopes[0]['duration_cycles'],ends[0]+3)
 def test_no_envelope_preserves_original_result_identity(self):
  p=same_path_plan([24,8]);r=RuntimeContext(p).run();self.assertEqual(r.operation_envelopes,());self.assertNotIn('operation_envelopes',r.model_dump(mode='json'))
