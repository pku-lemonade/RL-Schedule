import json
import unittest
from copy import deepcopy
from simulator_detailed.moe_model.binding import *
from simulator_detailed.moe_model.execute import execute_network
from simulator_detailed.moe_model.lowering import compile_network
from simulator_detailed.moe_model.hardware import *
from moe_network_fixtures import fixture

class Phase1Tests(unittest.TestCase):
    def run_fixture(self,mc=True,payload=40,**kwargs):
        w,b,h=fixture(mc,payload)
        return execute_network(w,b,h,production=False,**kwargs)
    def test_p2p_exact_route(self):
        e=self.run_fixture(False)
        self.assertEqual(e.receipt['logical_flows'][0]['endpoints'][0]['route'],['f0h0','f0h1'])
        self.assertEqual(e.result.status,'complete')
    def test_multicast_routes(self):
        e=self.run_fixture()
        self.assertEqual([x['route'] for x in e.receipt['logical_flows'][0]['endpoints']],[['prefix','leaf-0'],['prefix','leaf-1']])
    def test_source_once(self):
        e=self.run_fixture()
        f=e.receipt['logical_flows'][0]
        self.assertEqual(f['source_packet_injections'],5)
        self.assertEqual(f['endpoint_delivered_bytes'],80)
    def test_partial_packet_byte_conservation(self):
        e=self.run_fixture(False,19)
        f=e.receipt['logical_flows'][0]
        self.assertEqual(f['expected_packets'],3)
        self.assertEqual(f['endpoint_delivered_bytes'],19)
    def test_fanout(self):
        self.assertEqual(len(self.run_fixture().receipt['logical_flows'][0]['endpoints']),2)
    def test_asymmetric_endpoint_completions(self):
        e=self.run_fixture();ep=e.receipt['logical_flows'][0]['endpoints']
        self.assertLess(ep[0]['end_cycles'],ep[1]['end_cycles'])
    def test_occupancy_capacity(self):
        e=self.run_fixture(payload=240)
        self.assertTrue(all(r['max_occupancy']<=r['capacity'] for r in e.receipt['resources']))
        self.assertTrue(any(r['wait_count']>0 for r in e.receipt['resources']))
    def test_whole_is_actual_completion(self):
        e=self.run_fixture()
        self.assertEqual(e.receipt['whole_boundary'][0]['end_cycles'],max(s.end_cycles for s in e.result.transactions))
        self.assertEqual(e.receipt['whole_boundary'][0]['network_coverage'],'live_event_composition')
    def test_cleanup(self):
        e=self.run_fixture()
        self.assertEqual(e.receipt['cleanup'],dict(resources_released=True,multicast_groups_empty=True,background_alive=0))
    def test_cancel_cleanup(self):
        e=self.run_fixture(payload=240,cancel=('mc.member.00',2.0),max_cycles=100.0)
        self.assertEqual(e.receipt['status'],'incomplete')
        self.assertTrue(e.receipt['cleanup']['resources_released'])
        self.assertTrue(e.receipt['cleanup']['multicast_groups_empty'])
    def test_failure_cleanup(self):
        e=self.run_fixture(inject_failure=True)
        self.assertEqual(e.receipt['status'],'failed')
        self.assertTrue(e.receipt['cleanup']['resources_released'])
    def test_determinism(self):
        self.assertEqual(self.run_fixture().receipt,self.run_fixture().receipt)
    def test_production_refuses_fixture(self):
        with self.assertRaises(ValueError):execute_network(*fixture())
    def test_unknown_endpoint_rejected(self):
        w,b,h=fixture()
        raw=b.model_dump();raw.pop('binding_sha256');raw['endpoints']=(EndpointBinding(role_id='bad',endpoint_id='missing',kind='pe'),)
        with self.assertRaises(ValueError):freeze_binding(**raw)
    def test_missing_route_rejected(self):
        w,b,h=fixture();raw=b.model_dump();raw.pop('binding_sha256');raw['graph']=deepcopy(b.graph);raw['graph']['static_routes']=[]
        replacement=freeze_binding(**raw)
        with self.assertRaises(ValueError):compile_network(w,replacement,h,production=False)
    def test_route_continuity_gate(self):
        w,b,h=fixture();raw=b.model_dump();raw.pop('binding_sha256');raw['graph']=deepcopy(b.graph)
        raw['graph']['static_routes'][0]['link_ids']=['leaf-1','prefix']
        with self.assertRaises(ValueError):freeze_binding(**raw)
    def test_hash_gate(self):
        w,b,h=fixture();raw=b.model_dump_json();obj=json.loads(raw);obj['fixture_only']=False
        with self.assertRaises(ValueError):SuppliedBinding.model_validate_json(json.dumps(obj))
    def test_bandwidth_target_is_local(self):
        w,b,h=fixture(payload=120)
        old=execute_network(w,b,h,production=False)
        path=h.parameters[0].path
        changed=apply_mutations(h,(HardwareMutation(mutation_id='twice',path=path,operator='scale',operand=0.5),),
            MutationScope(scope_id='only-prefix',allowed_paths=(path,)))
        new=execute_network(w,b,changed,production=False)
        a={s.transaction_id:s.end_cycles for s in old.result.transactions};c={s.transaction_id:s.end_cycles for s in new.result.transactions}
        self.assertGreater(c['mc'],a['mc']);self.assertEqual(c['solo'],a['solo'])
        self.assertEqual(old.compiled.workload,new.compiled.workload);self.assertEqual(old.compiled.binding,new.compiled.binding)
    def test_gm_bandwidth_path(self):
        w,b,h=fixture(False);raw=b.model_dump();raw.pop('binding_sha256');path='gm.wdma.0.bytes_per_cycle'
        raw['parameters']=(ParameterBinding(parameter_path=path,network_id='noc',link_ids=('f0h1',),allowed_component='M01'),)
        b=freeze_binding(**raw);hp=h.parameters[0].model_copy(update={'path':path})
        h=freeze_hardware_parameters('fixture','cycles',(hp,),('fixture',))
        self.assertEqual(execute_network(w,b,h,production=False).receipt['status'],'complete')
    def test_unavailable_bandwidth(self):
        w,b,h=fixture();hp=h.parameters[0].model_copy(update={'quantity':h.parameters[0].quantity.model_copy(update={'value':None})})
        h=freeze_hardware_parameters('fixture','cycles',(hp,),('fixture',))
        with self.assertRaises(ValueError):execute_network(w,b,h,production=False)
    def test_no_compute_adalink_parameter_target(self):
        w,b,h=fixture();raw=b.model_dump();raw.pop('binding_sha256')
        raw['parameters']=(ParameterBinding(parameter_path='adalink.link.bandwidth',network_id='noc',link_ids=('prefix',),allowed_component='M01'),)
        with self.assertRaises(ValueError):freeze_binding(**raw)
    def test_receipt_provenance(self):
        e=self.run_fixture()
        self.assertEqual(set(e.receipt['provenance']),{'workload_sha256','binding_sha256','hardware_sha256','calibration_sha256','plan_sha256'})
        self.assertFalse(e.receipt['architecture_benefit_claim'])
    def test_event_resource_critical_path(self):
        e=self.run_fixture(payload=120);g=e.receipt['causal_graph']
        self.assertTrue(g['critical_path'])
        self.assertTrue(any(x['kind']=='resource_blocker' for x in g['edges']))
        self.assertTrue(any(x['kind']=='packet_handoff' for x in g['edges']))
    def test_logical_dependencies(self):
        w,b,h=fixture();raw=w.model_dump();raw.pop('workload_sha256')
        raw['flows']=(w.flows[0],w.flows[1].model_copy(update={'depends_on':('mc',)}))
        w=freeze_network_workload(**raw);e=execute_network(w,b,h,production=False)
        rows={s.transaction_id:s for s in e.result.transactions}
        self.assertGreaterEqual(rows['solo.member.00'].start_cycles,rows['mc'].end_cycles)

class TailOwnershipTests(unittest.TestCase):
    def test_delayed_source_cancel_is_not_unhandled_interrupt(self):
        w,b,h=fixture();raw=w.model_dump();raw.pop('workload_sha256')
        raw['flows']=tuple(f.model_copy(update={'start_cycles':1000.0}) for f in w.flows)
        w=freeze_network_workload(**raw)
        e=execute_network(w,b,h,production=False,max_cycles=10.0)
        self.assertEqual(e.result.status,'incomplete')
        self.assertTrue(e.receipt['cleanup']['resources_released'])
    def test_multicast_tail_is_owned_background(self):
        w,b,h=fixture();e=execute_network(w,b,h,production=False)
        self.assertTrue(e.runtime._background)
        self.assertTrue(all(not p.is_alive for p in e.runtime._background))
