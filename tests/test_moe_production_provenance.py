"""CPU-only provenance/report regressions; no evidence bundle construction."""
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter

from simulator_detailed.moe_model import component_calibration as calibration
from simulator_detailed.moe_model import exploration_pipeline as pipeline
from simulator_detailed.moe_model.e7_join import E7JoinWork
from simulator_detailed.moe_model.hardware import HardwareMutation
from simulator_detailed.moe_model.phase3_fixture import build_phase3_fixture
from simulator_detailed.moe_model.phase3_fixture_execute import compile_phase3_fixture
from simulator_detailed.moe_model.production_inputs import require_production_action_provenance
from simulator_detailed.moe_model.workload import stable_sha256
from simulator_detailed.configs.schemas.system_spec import SystemSpec


IDENTITY=dict(model_library_sha256='1'*64,systemc_library_sha256='2'*64,
    observer_sha256='3'*64,counter_callsite=7)
PROVENANCE_ERROR='production_action_provenance_unavailable'


def cpu_calibration_bundle(*args,**kwargs):
    work=E7JoinWork();entries=[]
    for number in range(1,9):
        component=f'M{number:02d}';is_join=component=='M07'
        entries.append(calibration._freeze_entry(
            entry_id='cpu-'+component,component_id=component,
            mechanism='sync_e7_commid_join' if is_join else 'cpu_placeholder',
            operation=work.operation if is_join else 'cpu_placeholder',
            profiles=('transformer-fused',),consumer_stages=('E7',),
            parameter_paths=('cpu.placeholder',),formula_id='cpu-only-'+component,formula={},
            code_artifacts=(calibration.EvidenceArtifact(path='cpu-fixture-not-evidence',sha256='a'*64),),
            domain=calibration.ActionDomain(
                input_dimensions=dict(e7_join_work=work.model_dump(mode='json'),**IDENTITY) if is_join else {},
                route_domains=('source-fused-e7-fullmesh-ports4-17-commid16',),
                applicability_text='CPU-only synthetic resolver fixture; not accepted production evidence'),
            duration_unit='unavailable',observation_unit='unavailable',timebase_status='unavailable',
            calibration_scenes=(),validation_scenes=(),
            error_summary=calibration.ErrorSummary(metric='unavailable',unit='unavailable',validation_scene_count=0),
            status='cpu_only_binding_gate',publication_class='not_publishable',
            source_commit='a'*40,source_refs=('CPU fixture only',)))
    raw=dict(schema_version=1,bundle_id='cpu-provenance-tests',fixture_only=True,entries=tuple(entries),
        excluded_evidence=(),source_repository_commit='a'*40,source_refs=('CPU fixture only',))
    return calibration.ComponentCalibrationBundle(**raw,
        bundle_sha256=stable_sha256(TypeAdapter(dict).dump_python(raw,mode='json')))


class RequestIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=cpu_calibration_bundle()
        cls.workload=build_phase3_fixture('transformer-fused').full_workload
        cls.bound_requests=tuple(request for request in calibration.workload_action_requests(
            cls.workload,'b'*64,e7_join_identity=IDENTITY) if request.mechanism=='sync_e7_commid_join')

    def issues_for(self,request):
        with patch.object(calibration,'workload_action_requests',return_value=(request,)):
            return calibration.resolution_issues(self.bundle,self.workload,'b'*64)

    def test_explicit_matching_identity_resolves_cpu_entry(self):
        self.assertTrue(self.bound_requests)
        for request in self.bound_requests:
            self.assertEqual(self.issues_for(request),[])
            self.assertEqual(calibration.resolve_calibration(self.bundle,request).publication_class,'not_publishable')

    def test_missing_provider_or_observer_is_not_filled_from_candidate(self):
        for field in IDENTITY:
            with self.subTest(field=field):
                request=self.bound_requests[0].model_copy(update={field:None})
                issues=self.issues_for(request)
                self.assertEqual(len(issues),1)
                self.assertIn('found 0',issues[0]['error'])
                self.assertIsNone(getattr(request,field))

    def test_mismatched_provider_or_observer_is_not_overwritten(self):
        for field in IDENTITY:
            with self.subTest(field=field):
                value=99 if field=='counter_callsite' else 'f'*64
                issues=self.issues_for(self.bound_requests[0].model_copy(update={field:value}))
                self.assertEqual(len(issues),1)
                self.assertIn('found 0',issues[0]['error'])

    def test_workload_identity_is_forwarded_and_missing_identity_remains_unresolved(self):
        for identity in (None,IDENTITY):
            with self.subTest(identity=identity):
                kwargs={} if identity is None else dict(e7_join_identity=identity)
                issues=calibration.resolution_issues(self.bundle,self.workload,'b'*64,**kwargs)
                join_issues=[row for row in issues if row['operation']==E7JoinWork().operation]
                self.assertEqual(len(join_issues),len(self.bound_requests) if identity is None else 0)

    def test_all_optional_domain_bindings_are_forwarded_unchanged(self):
        options={name:object() for name,parameter in inspect.signature(calibration.workload_action_requests).parameters.items()
            if parameter.kind==inspect.Parameter.KEYWORD_ONLY}
        self.assertTrue({'expert_matrix_domains','gate_up_domains','vector_bulk_bindings','chain_scenarios'}<=options.keys())
        with patch.object(calibration,'workload_action_requests',return_value=()) as requests:
            self.assertEqual(calibration.resolution_issues(self.bundle,self.workload,'b'*64,**options),[])
        requests.assert_called_once_with(self.workload,'b'*64,**options)

    def test_unknown_domain_binding_is_not_silently_ignored(self):
        with self.assertRaises(TypeError):
            calibration.resolution_issues(self.bundle,self.workload,'b'*64,unknown_domain={})


class ProductionExecutionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=build_phase3_fixture('standalone')
        cls.spec=compile_phase3_fixture(cls.fixture,production=False).spec

    def values(self):
        peak=SimpleNamespace(peak_id='cpu-peak',model_dump=lambda **kwargs:dict(peak_id='cpu-peak'))
        return dict(inventory=SimpleNamespace(complete_group_count=33),
            route=SimpleNamespace(workload_sha256='a'*64,profile=SimpleNamespace(name='standalone')),
            binding=SimpleNamespace(system_spec=self.spec.model_dump(mode='json'),artifact_sha256='b'*64),
            baseline=SimpleNamespace(workload_sha256='a'*64,selected_peak_id=peak.peak_id,peak_candidates=(peak,)),
            write_sum=None,adalink=None,hardware=SimpleNamespace(parameters=()),
            calibrations=SimpleNamespace(fixture_only=False,excluded_evidence=(),timebases=None),
            peak_evidence=SimpleNamespace(precision='BF16',peak=peak))

    def test_abstract_resolution_success_cannot_authorize_raw_spec(self):
        values=self.values()
        with patch.object(pipeline,'phase3_production_gate',return_value=dict(ready=True)), \
             patch.object(pipeline,'resolution_issues',return_value=[]), \
             patch.object(pipeline,'_timebase',return_value=dict(status='physical')), \
             patch('simulator_detailed.moe_model.peak_evidence.require_selected_peak',return_value=values['peak_evidence'].peak):
            issues,_,_,resolution_issues=pipeline._production_issues(values)
        self.assertEqual(resolution_issues,[])
        self.assertEqual(len(issues),1)
        self.assertIn(PROVENANCE_ERROR,issues[0])

    def test_existing_fixture_spec_cannot_be_promoted_to_production(self):
        for spec in (self.spec,self.spec.model_dump(mode='json')):
            with self.subTest(typed=isinstance(spec,SystemSpec)), \
                 patch.object(pipeline,'compile_system') as compile_spy, \
                 patch.object(pipeline,'RuntimeContext') as runtime_spy:
                with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline._execute_spec(spec)
                compile_spy.assert_not_called();runtime_spy.assert_not_called()

    def test_preexisting_ready_validation_cannot_freeze_unproved_spec(self):
        values=self.values()
        with TemporaryDirectory() as directory:
            run=Path(directory)/'run';plan_path=Path(directory)/'plan.json'
            case=pipeline.SweepCase(case_id='cpu-case',mutations=(HardwareMutation(mutation_id='slow',
                path='data_noc.links.data.central.bytes_per_cycle',operator='scale',operand=.5),),rationale='CPU regression')
            plan=pipeline.freeze_sweep_plan(plan_id='cpu-plan',fixture_only=False,cases=(case,),source_refs=('CPU fixture',))
            plan_path.write_text(plan.model_dump_json())
            pipeline._atomic_json(run/'validation.json',dict(mode='production',ready=True,input_dir='unused'))
            with patch.object(pipeline,'_production_inputs',return_value=values), \
                 patch.object(pipeline,'phase3_production_gate',return_value=dict(ready=True)), \
                 patch.object(pipeline,'resolution_issues',return_value=[]), \
                 patch.object(pipeline,'_timebase',return_value=dict(status='physical')), \
                 patch('simulator_detailed.moe_model.peak_evidence.require_selected_peak',return_value=values['peak_evidence'].peak):
                with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline.freeze_baseline(run,plan_path)
            self.assertFalse((run/'freeze').exists())

    def test_schema_valid_mismatched_compute_duration_is_blocked_before_compilation(self):
        raw=self.spec.model_dump(mode='json')
        action=next(row for row in raw['batch']['transactions'] if row['kind']=='compute')
        action['duration_cycles']+=1000
        changed=SystemSpec.model_validate_json(json.dumps(raw))
        self.assertNotEqual(changed,self.spec)
        with patch.object(pipeline,'compile_system') as compile_spy:
            with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline._execute_spec(changed)
            compile_spy.assert_not_called()

    def test_self_asserted_provenance_and_arbitrary_action_changes_cannot_enable_execution(self):
        for field,value in (('formula_id','unaccepted'),('work',{}),('unit','ns'),
                            ('unit_id','unrelated-resource'),('depends_on',[])):
            with self.subTest(field=field):
                raw=self.spec.model_dump(mode='json')
                action=next(row for row in raw['batch']['transactions'] if row['kind']=='compute')
                action[field]=value
                raw['action_provenance']=dict(accepted=True,entry_sha256='a'*64,system_spec_sha256=stable_sha256(raw))
                with patch.object(pipeline,'compile_system') as compile_spy:
                    with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline._execute_spec(raw)
                    compile_spy.assert_not_called()

    def test_absent_or_empty_production_spec_is_blocked(self):
        for spec in (None,{},dict(batch=dict(transactions=[]))):
            with self.subTest(spec=spec),self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):
                require_production_action_provenance(spec)

    def test_preexisting_production_freeze_cannot_execute_raw_baseline(self):
        with TemporaryDirectory() as directory:
            run=Path(directory);freeze=dict(mode='production',freeze_sha256='f'*64)
            with patch.object(pipeline,'_verify_freeze',return_value=freeze), \
                 patch.object(pipeline,'_load_frozen',return_value=self.values()), \
                 patch.object(pipeline,'compile_system') as compile_spy:
                with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline.run_baseline(run)
                compile_spy.assert_not_called()
            failure=json.loads((run/'baseline/failure.json').read_text())
            self.assertIn(PROVENANCE_ERROR,failure['error'])
            self.assertEqual(pipeline.status(run)['status'],'BASELINE_FAILED')
            self.assertFalse((run/'baseline/result.json').exists())

    def test_preexisting_production_baseline_cannot_execute_raw_sweep(self):
        with TemporaryDirectory() as directory:
            run=Path(directory);freeze=dict(mode='production',freeze_sha256='f'*64)
            case=pipeline.SweepCase(case_id='cpu-case',mutations=(HardwareMutation(mutation_id='cpu-slow',
                path='data_noc.links.data.central.bytes_per_cycle',operator='scale',operand=.5),),rationale='CPU regression')
            plan=pipeline.freeze_sweep_plan(plan_id='cpu-plan',fixture_only=False,cases=(case,),source_refs=('CPU fixture',))
            pipeline._atomic_json(run/'freeze/frozen-inputs/sweep-plan.json',plan.model_dump(mode='json'))
            pipeline._atomic_json(run/'baseline/result.json',dict(freeze_sha256='f'*64,status='COMPLETE_PRODUCTION'))
            with patch.object(pipeline,'_verify_freeze',return_value=freeze), \
                 patch.object(pipeline,'_load_frozen',return_value=self.values()), \
                 patch.object(pipeline,'_apply_production_mutations',return_value=(self.spec,None,[])), \
                 patch.object(pipeline,'compile_system') as compile_spy:
                with self.assertRaisesRegex(ValueError,PROVENANCE_ERROR):pipeline.run_sweep(run)
                compile_spy.assert_not_called()
            self.assertEqual(pipeline.status(run)['status'],'SWEEP_FAILED')
            self.assertFalse((run/'sweeps/cpu-case/result.json').exists())
            self.assertIn(PROVENANCE_ERROR,json.loads((run/'sweeps/cpu-case/failure.json').read_text())['error'])


class BottleneckReportTests(unittest.TestCase):
    def test_aggregate_wait_winner_is_not_a_causal_bottleneck(self):
        receipt=dict(resources=[dict(resource_id='busy-but-off-path',mode='credit',wait_cycles=999,wait_count=20)])
        result=pipeline._resource_critical(receipt)
        self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['resource_id'])

    def test_timestamp_path_without_resource_attribution_is_unavailable(self):
        receipt=dict(causal_graph=dict(critical_path=[dict(resource_id='on-path',time=10)]))
        self.assertEqual(pipeline._resource_critical(receipt)['status'],'unavailable')

    def migration(self,baseline,changed):
        return pipeline._bottleneck_migration(dict(bottleneck=baseline),dict(case_id='cpu-case',bottleneck=changed))

    def test_same_resource_wait_changes_are_not_migration(self):
        baseline=dict(status='available',resource_id='engine',mode='serializer',wait_cycles=1,wait_count=1)
        changed=dict(baseline,wait_cycles=10,wait_count=2)
        self.assertFalse(self.migration(baseline,changed)['changed'])

    def test_resource_or_mode_identity_change_is_migration(self):
        baseline=dict(status='available',resource_id='engine',mode='serializer')
        for changed in (dict(baseline,resource_id='other'),dict(baseline,mode='credit')):
            with self.subTest(changed=changed):self.assertTrue(self.migration(baseline,changed)['changed'])

    def test_unavailable_attribution_never_claims_migration_or_no_migration(self):
        unavailable=pipeline._resource_critical({})
        available=dict(status='available',resource_id='engine',mode='serializer')
        for baseline,changed in ((unavailable,unavailable),(available,unavailable),(None,available)):
            with self.subTest(baseline=baseline,changed=changed):
                result=self.migration(baseline,changed)
                self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['changed'])


class FixturePipelineRegressionTests(unittest.TestCase):
    def test_existing_fixture_execution_sweep_analysis_remain_unpublishable(self):
        with TemporaryDirectory() as directory:
            run=Path(directory)/'run';plan_path=Path(directory)/'plan.json'
            case=pipeline.SweepCase(case_id='data-link',mutations=(HardwareMutation(mutation_id='slow',
                path='data_noc.links.data.central.bytes_per_cycle',operator='scale',operand=.5),),rationale='CPU fixture')
            plan=pipeline.freeze_sweep_plan(plan_id='fixture-plan',fixture_only=True,cases=(case,),source_refs=('CPU fixture',))
            plan_path.write_text(plan.model_dump_json())
            with patch.object(pipeline,'build_component_calibration_bundle',side_effect=cpu_calibration_bundle), \
                 patch.object(pipeline,'require_production_action_provenance',side_effect=AssertionError('fixture entered production gate')):
                validation=pipeline.validate_inputs(run,fixture_profile='transformer-fused')
                pipeline.freeze_baseline(run,plan_path)
                baseline=pipeline.run_baseline(run)
                pipeline.run_sweep(run)
                analysis=pipeline.analyze(run)
            self.assertTrue(validation['ready']);self.assertFalse(validation['publishable'])
            self.assertEqual(baseline['status'],'COMPLETE_FIXTURE_ONLY');self.assertFalse(baseline['publishable'])
            self.assertEqual(baseline['receipt']['native_transaction_count'],534)
            self.assertEqual(analysis['status'],'COMPLETE_FIXTURE_ONLY_NOT_PUBLISHABLE')
            self.assertFalse(analysis['publishable']);self.assertFalse(analysis['architecture_benefit_claim'])
            self.assertIsNone(analysis['bottleneck_migration'][0]['changed'])


if __name__=='__main__':unittest.main()
