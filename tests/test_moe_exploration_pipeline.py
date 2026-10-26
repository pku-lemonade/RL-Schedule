"""Public synthetic pipeline regressions without historical artifact discovery."""
from unittest.mock import patch
from simulator_detailed.moe_model import exploration_pipeline
from test_moe_production_provenance import cpu_calibration_bundle

calibration_patch = patch.object(exploration_pipeline, 'build_component_calibration_bundle', side_effect=cpu_calibration_bundle)


def setUpModule():
    calibration_patch.start()


def tearDownModule():
    calibration_patch.stop()


import contextlib


import io


import json


from pathlib import Path


from tempfile import TemporaryDirectory


import unittest


from pydantic import ValidationError


from simulator_detailed.moe_model.exploration_pipeline import (
    BaselineFreezeManifest, SweepCase, SweepPlan, analyze, cli, freeze_baseline, freeze_sweep_plan,
    run_baseline, run_sweep, status, validate_inputs,
)


from simulator_detailed.moe_model.component_calibration import ComponentCalibrationBundle


from simulator_detailed.moe_model.hardware import HardwareMutation


def mutation(identity,path,operand=.5):
    return HardwareMutation(mutation_id=identity,path=path,operator='scale',operand=operand)


def fixture_plan(path,*,fixture=True,cases=None):
    cases=cases or (
      SweepCase(case_id='data-link',mutations=(mutation('data-slow','data_noc.links.data.central.bytes_per_cycle'),),rationale='fixture locality'),
      SweepCase(case_id='gm-service',mutations=(mutation('gm-slow','gm.controllers.gm0.bytes_per_cycle'),),rationale='fixture locality'))
    plan=freeze_sweep_plan(plan_id='fixture-first-sweep',fixture_only=fixture,cases=cases,source_refs=('fixture-only pipeline test',))
    path.write_text(plan.model_dump_json());return plan


class SweepSchemaTests(unittest.TestCase):
    def test_exact_first_round_paths(self):
        with TemporaryDirectory() as td:
            plan=fixture_plan(Path(td)/'plan.json');self.assertEqual(len(plan.cases),2)
            self.assertEqual(plan,SweepPlan.model_validate_json(plan.model_dump_json()))

    def test_wildcard_rejected(self):
        with self.assertRaises(ValidationError):
            SweepCase(case_id='bad',mutations=(mutation('bad','data_noc.links.*.bytes_per_cycle'),),rationale='bad')

    def test_adalink_rejected_from_first_round(self):
        with self.assertRaises(ValidationError):
            SweepCase(case_id='bad',mutations=(mutation('bad','adalink.links.a.bytes_per_cycle'),),rationale='bad')

    def test_multi_path_requires_explicit_coupling(self):
        rows=(mutation('a','data_noc.links.data.central.bytes_per_cycle'),mutation('b','gm.controllers.gm0.bytes_per_cycle'))
        with self.assertRaises(ValidationError):SweepCase(case_id='bad',mutations=rows,rationale='bad')
        self.assertTrue(SweepCase(case_id='coupled',mutations=rows,explicitly_coupled=True,rationale='declared').explicitly_coupled)

    def test_hash_tamper_rejected(self):
        with TemporaryDirectory() as td:
            plan=fixture_plan(Path(td)/'plan.json');raw=json.loads(plan.model_dump_json());raw['plan_id']='changed'
            with self.assertRaises(ValidationError):SweepPlan.model_validate(raw)


class FixtureInputGateTests(unittest.TestCase):
    def test_fixture_validation_is_explicitly_unpublishable(self):
        with TemporaryDirectory() as td:
            result=validate_inputs(Path(td)/'run',fixture_profile='transformer-fused')
            self.assertEqual(result['status'],'READY_FIXTURE_ONLY');self.assertFalse(result['publishable']);self.assertEqual(result['strict_complete_groups'],1)


class CompleteCliFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name);cls.run_dir=root/'run';cls.plan_path=root/'plan.json';fixture_plan(cls.plan_path)
        commands=(
          ['validate-inputs','--run-dir',str(cls.run_dir),'--fixture-profile','transformer-fused'],
          ['freeze-baseline','--run-dir',str(cls.run_dir),'--sweep-plan',str(cls.plan_path)],
          ['run-baseline','--run-dir',str(cls.run_dir)],
          ['run-sweep','--run-dir',str(cls.run_dir)],
          ['analyze','--run-dir',str(cls.run_dir)],
          ['status','--run-dir',str(cls.run_dir)])
        cls.outputs=[]
        for command in commands:
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):code=cli(command)
            if code!=0:raise AssertionError((command,stream.getvalue()))
            cls.outputs.append(json.loads(stream.getvalue()))
        cls.freeze=json.loads((cls.run_dir/'freeze/baseline-freeze.json').read_text());cls.baseline=json.loads((cls.run_dir/'baseline/result.json').read_text());cls.analysis=json.loads((cls.run_dir/'analysis/result.json').read_text())

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_command_state_sequence_finishes_analyzed(self):
        self.assertEqual(self.outputs[-1]['status'],'ANALYZED');self.assertEqual(self.outputs[-1]['state']['revision'],5)

    def test_freeze_binds_all_identities_and_forbids_refit(self):
        freeze=self.freeze
        self.assertEqual(freeze['mode'],'fixture_only');self.assertFalse(freeze['publishable'])
        self.assertTrue(freeze['identities']['workload_sha256']);self.assertTrue(freeze['identities']['binding_sha256']);self.assertTrue(freeze['identities']['component_sha256'])
        self.assertFalse(freeze['model_refit_allowed']);self.assertFalse(freeze['topology_change_allowed']);self.assertFalse(freeze['workload_change_allowed']);self.assertFalse(freeze['route_change_allowed'])
        self.assertEqual(freeze['timebase']['status'],'reported_units_only')
        bundle=ComponentCalibrationBundle.model_validate_json((self.run_dir/'freeze/frozen-inputs/component-calibration-bundle.json').read_text())
        self.assertTrue(bundle.fixture_only);self.assertEqual(freeze['identities']['component_sha256'],bundle.bundle_sha256)

    def test_freeze_schema_roundtrip_and_tamper_gate(self):
        parsed=BaselineFreezeManifest.model_validate_json((self.run_dir/'freeze/baseline-freeze.json').read_text());self.assertEqual(parsed.freeze_sha256,self.freeze['freeze_sha256'])
        raw=dict(self.freeze);raw['code_commit']='0'*40
        with self.assertRaises(ValidationError):BaselineFreezeManifest.model_validate(raw)

    def test_baseline_receipt_and_cleanup(self):
        result=self.baseline;receipt=result['receipt']
        self.assertEqual(result['status'],'COMPLETE_FIXTURE_ONLY');self.assertFalse(result['publishable'])
        self.assertEqual(result['four_metric_inputs']['status'],'not_publishable');self.assertIsNone(result['full_decode_latency']['seconds'])
        self.assertEqual(len(receipt['stage_boundaries']),22);self.assertTrue(receipt['resources']);self.assertTrue(receipt['event_resource_critical_path'])
        self.assertEqual(receipt['cleanup'],{'background_alive':0,'multicast_groups_empty':True,'resources_released':True})

    def test_cases_reuse_frozen_workload_binding(self):
        for case in ('data-link','gm-service'):
            result=json.loads((self.run_dir/'sweeps'/case/'result.json').read_text())
            self.assertEqual(result['workload_sha256'],self.freeze['identities']['workload_sha256'])
            self.assertEqual(result['binding_sha256'],self.freeze['identities']['binding_sha256'])
            self.assertEqual(result['base_hardware_sha256'],self.freeze['hardware_sha256'])
            self.assertEqual(len(result['applied_parameters']),1);self.assertFalse(result['causality']['unexpected_changed_actions'])

    def test_analysis_has_required_fields_but_no_fixture_mfu(self):
        analysis=self.analysis
        self.assertEqual(analysis['status'],'COMPLETE_FIXTURE_ONLY_NOT_PUBLISHABLE');self.assertFalse(analysis['publishable']);self.assertFalse(analysis['architecture_benefit_claim'])
        for row in (analysis['baseline'],*analysis['cases']):
            self.assertEqual(set(row['metrics']),{'useful_model_mfu','issued_engine_utilization','moe_local_mfu','full_decode_mfu'})
            self.assertTrue(all(metric['value'] is None for metric in row['metrics'].values()))
            self.assertEqual(row['latency']['status'],'reported_units_only');self.assertIsNone(row['full_decode_latency']['seconds'])
            self.assertTrue(row['stage_times']);self.assertTrue(row['component_times']);self.assertTrue(row['resource_waits']);self.assertTrue(row['critical_path'])

    def test_bottleneck_and_parameter_causal_chain(self):
        self.assertEqual(len(self.analysis['bottleneck_migration']),2)
        for row in self.analysis['cases']:
            self.assertEqual(len(row['applied_parameters']),1);self.assertIsNotNone(row['parameter_causality'])

    def test_no_temporary_artifact_directories_remain(self):
        self.assertFalse(any('.tmp-' in str(path) for path in self.run_dir.rglob('*')))


class IntegrityAndFailureTests(unittest.TestCase):
    def initialize(self,root):
        run=root/'run';plan=root/'plan.json';fixture_plan(plan);validate_inputs(run,fixture_profile='transformer-fused');freeze_baseline(run,plan);return run,plan

    def test_frozen_input_tamper_rejected(self):
        with TemporaryDirectory() as td:
            run,_=self.initialize(Path(td));path=run/'freeze/frozen-inputs/fixture-bundle.json';raw=json.loads(path.read_text());raw['fixture_id']='tampered';path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'frozen input changed'):run_baseline(run)

    def test_baseline_failure_is_preserved_and_not_retryable(self):
        with TemporaryDirectory() as td:
            run,_=self.initialize(Path(td))
            with self.assertRaises(RuntimeError):run_baseline(run,inject_failure=True)
            self.assertTrue((run/'baseline/failure.json').is_file());self.assertEqual(status(run)['status'],'BASELINE_FAILED')
            with self.assertRaises(ValueError):run_baseline(run)

    def test_successful_baseline_cannot_be_overwritten(self):
        with TemporaryDirectory() as td:
            run,_=self.initialize(Path(td));run_baseline(run)
            with self.assertRaises(ValueError):run_baseline(run)

    def test_failed_sweep_case_stops_and_partial_analysis_is_blocked(self):
        with TemporaryDirectory() as td:
            run,_=self.initialize(Path(td));run_baseline(run)
            with self.assertRaises(RuntimeError):run_sweep(run,fail_case='gm-service')
            self.assertTrue((run/'sweeps/data-link/result.json').is_file());self.assertTrue((run/'sweeps/gm-service/failure.json').is_file())
            blocked=analyze(run);self.assertEqual(blocked['status'],'PARTIAL_NOT_PUBLISHABLE');self.assertFalse(blocked['formal_summary_created'])
            self.assertFalse((run/'analysis/result.json').exists())

    def test_failed_case_cannot_be_hidden_by_rerun(self):
        with TemporaryDirectory() as td:
            run,_=self.initialize(Path(td));run_baseline(run)
            with self.assertRaises(RuntimeError):run_sweep(run,fail_case='data-link')
            with self.assertRaises(ValueError):run_sweep(run)

    def test_freeze_rejects_mode_mismatch_and_unknown_path(self):
        with TemporaryDirectory() as td:
            root=Path(td);run=root/'run';validate_inputs(run,fixture_profile='transformer-fused');fixture_plan(root/'prod.json',fixture=False)
            with self.assertRaises(ValueError):freeze_baseline(run,root/'prod.json')
        with TemporaryDirectory() as td:
            root=Path(td);run=root/'run';validate_inputs(run,fixture_profile='transformer-fused')
            bad=freeze_sweep_plan(plan_id='bad',fixture_only=True,cases=(SweepCase(case_id='bad',mutations=(mutation('bad','gm.rdma.missing.bytes_per_cycle'),),rationale='bad'),),source_refs=('bad',));path=root/'bad.json';path.write_text(bad.model_dump_json())
            with self.assertRaisesRegex(ValueError,'absent from frozen hardware'):freeze_baseline(run,path)
