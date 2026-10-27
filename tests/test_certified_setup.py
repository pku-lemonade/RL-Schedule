from contextlib import ExitStack
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'))
import causal_full_analysis as full
import causal_full_capacity as capacity
import causal_full_fixtures as fixtures
import certified_setup as setup
import counterfactual as formulas
import design_space as api
import design_space_schema as wire
from test_causal_full_summary import limits
import certified_setup_fixture as complex_fixture


def forbidden(*arguments, **keywords):
    raise AssertionError('duplicate setup or premature numerical evaluation')


def numerical_guard(stack):
    for name in ('_scenario', '_build_result', '_surface'):
        stack.enter_context(patch.object(full, name, forbidden))
    stack.enter_context(patch.object(full._SummaryScenarios, 'rows', forbidden))


def setup_guard(stack):
    for module, name in ((full, 'admit'), (full, '_prepare'), (capacity, 'estimate'), (capacity, '_proof'),
            (wire, 'validate')):
        stack.enter_context(patch.object(module, name, forbidden))


class CertifiedSetupTests(unittest.TestCase):
    def setUp(self):
        self.authority = setup.SetupAuthority()
        self.spec = fixtures.model()
        self.model = api.DesignSpace.certified(self.spec)
        self.request = fixtures.request(self.model)
        self.request['version'] = full.schema.SUMMARY_VERSION
        self.limits = limits()
        self.prepared = self.model.prepare_summary(self.request, self.limits, authority=self.authority)

    def execute(self, prepared=None, model=None, receipt=None, declared=None):
        prepared = self.prepared if prepared is None else prepared
        with tempfile.TemporaryFile('w+b') as sink:
            receipt = (self.model if model is None else model).evaluate_to(prepared, sink,
                setup=prepared.receipt if receipt is None else receipt,
                limits=self.limits if declared is None else declared)
            prepared.validate_receipt(receipt)
            sink.seek(0)
            return sink.read(), receipt

    def import_local(self, payload=None, receipt=None, **changes):
        read_fd, write_fd = os.pipe()
        try:
            self.authority.inherit_key(write_fd)
            os.close(write_fd)
            arguments = dict(key_fd=read_fd, input_identity=self.model.identity,
                request_identity=self.prepared.identity, limits=self.limits)
            arguments.update(changes)
            return setup.import_setup(self.prepared.payload if payload is None else payload,
                self.prepared.receipt if receipt is None else receipt, **arguments)
        finally:
            os.close(read_fd)

    def test_first_time_exact_proofs_zero_evaluations(self):
        with ExitStack() as stack:
            numerical_guard(stack)
            admit = stack.enter_context(patch.object(full, 'admit', wraps=full.admit))
            prepare = stack.enter_context(patch.object(full, '_prepare', wraps=full._prepare))
            estimate = stack.enter_context(patch.object(capacity, 'estimate', wraps=capacity.estimate))
            model = api.DesignSpace.certified(self.spec)
            prepared = model.prepare_summary(self.request, self.limits, authority=self.authority)
            self.assertEqual((admit.call_count, prepare.call_count, estimate.call_count), (1, 1, 1))
        expected = api.DesignSpace(self.spec, admission_only=True).summary_capacity(self.request, self.limits)
        self.assertEqual(setup.thaw(prepared.certificate), expected)

    def test_first_time_gates_still_reject(self):
        for mutate in ('record', 'unknown', 'budget', 'output'):
            with self.subTest(mutate=mutate), ExitStack() as stack:
                numerical_guard(stack)
                spec, request, declared = deepcopy(self.spec), deepcopy(self.request), deepcopy(self.limits)
                if mutate == 'record':
                    spec['capacity']['records']['atoms'] = 0
                elif mutate == 'unknown':
                    spec['source_nodes'][0]['source_cost_ticks'] = 7
                elif mutate == 'budget':
                    request['budget']['designs'] = 0
                else:
                    declared['output_limits_bytes'] = [1]
                with self.assertRaises(wire.Rejected):
                    model = api.DesignSpace.certified(spec)
                    request['input_identity'] = model.identity
                    model.prepare_summary(request, declared, authority=self.authority)

    def test_immutable_and_defensive_snapshots(self):
        with self.assertRaises(TypeError):
            self.model._specification['families'][0]['id'] = 'altered'
        with self.assertRaises(FrozenInstanceError):
            self.model.identity = '0' * 64
        with self.assertRaises(TypeError):
            self.prepared.data['budget']['designs'] = 0
        self.spec['families'][0]['id'] = 'changed-original'
        self.request['study_id'] = 'changed-original'
        self.prepared.receipt['capacity']['bounds']['result_bytes'] = 0
        self.execute()

    def test_same_identity_reuse_no_duplicate_setup_or_input_identity(self):
        other = api.DesignSpace.certified(self.spec)
        identity = formulas.identity
        def checked(value):
            if type(value) is dict and value.get('version') in (self.spec['version'], self.request['version']):
                if 'grid' not in value and ('source_nodes' in value or 'axes' in value):
                    forbidden()
            return identity(value)
        with ExitStack() as stack:
            setup_guard(stack)
            stack.enter_context(patch.object(formulas, 'identity', checked))
            first, receipt = self.execute()
            second, other_receipt = self.execute(model=other)
        self.assertEqual(first, second)
        self.assertEqual(receipt, other_receipt)

    def test_all_receipt_tampering_rejects_before_numerical_work(self):
        paths = [('version',), ('mode',), ('input_identity',), ('request_identity',), ('timing_identity',),
            ('code', 'schema_identity'), ('code', 'source_identity'), ('code', 'serializer_identity'),
            ('capacity', 'bounds', 'result_bytes'), ('capacity', 'bounds', 'peak_additional_disk_bytes'),
            ('capacity', 'work', 'designs'), ('capacity', 'limits', 'frozen_available_bytes'),
            ('records', 'atoms'), ('capacity', 'identifier_lengths', 'ordered_lengths_sha256'),
            ('capacity', 'limits_identity'), ('capacity', 'proof_identity'), ('payload_sha256',), ('payload_bytes',), ('authenticator',)]
        for path in paths:
            with self.subTest(path=path), ExitStack() as stack:
                numerical_guard(stack)
                receipt = self.prepared.receipt
                target = receipt
                for part in path[:-1]:
                    target = target[part]
                value = target[path[-1]]
                target[path[-1]] = value + 1 if type(value) is int else 'altered'
                with self.assertRaises(wire.Rejected):
                    self.execute(receipt=receipt)
                with self.assertRaises(wire.Rejected):
                    self.import_local(receipt=receipt)

    def test_missing_extra_and_caller_boolean_reject(self):
        with ExitStack() as stack:
            numerical_guard(stack)
            for receipt in (None, {}, True, dict(self.prepared.receipt, accepted=True)):
                with self.subTest(receipt=type(receipt)), tempfile.TemporaryFile() as sink, self.assertRaises(wire.Rejected):
                    self.model.evaluate_to(self.prepared, sink, setup=receipt, limits=self.limits)
            with tempfile.TemporaryFile() as sink, self.assertRaises(TypeError):
                self.model.evaluate_to(self.prepared, sink, limits=self.limits)

    def test_cross_model_request_limit_and_stale_handoff(self):
        changed = deepcopy(self.spec)
        changed['source_nodes'][0]['metadata_json'] = '{"public":"other"}'
        other_model = api.DesignSpace.certified(changed)
        other_request = dict(self.request, study_id='other-request')
        other = self.model.prepare_summary(other_request, self.limits, authority=self.authority)
        with ExitStack() as stack:
            numerical_guard(stack)
            with self.assertRaises(wire.Rejected):
                self.execute(model=other_model)
            with self.assertRaises(wire.Rejected):
                self.execute(prepared=other, receipt=self.prepared.receipt)
            with self.assertRaises(wire.Rejected):
                self.execute(declared=dict(self.limits, output_limits_bytes=[1]))
            for changes in (dict(input_identity='0' * 64), dict(request_identity='0' * 64),
                    dict(limits=dict(self.limits, additional_temporary_bytes=1))):
                with self.assertRaises(wire.Rejected):
                    self.import_local(**changes)
            with patch.object(setup, 'VERSION', 'stale'), self.assertRaises(wire.Rejected):
                self.execute()

    def test_forged_capabilities_and_payload_reject(self):
        with ExitStack() as stack:
            numerical_guard(stack)
            with self.assertRaises(wire.Rejected):
                self.execute(prepared=replace(self.prepared))
            with self.assertRaises(wire.Rejected):
                self.execute(model=replace(self.model))
            with self.assertRaises(wire.Rejected):
                self.import_local(payload=self.prepared.payload + b' ')
            with patch.object(self.authority, 'inherit_key', lambda descriptor: os.write(descriptor, b'0' * 32)):
                with self.assertRaises(wire.Rejected):
                    self.import_local()
            with tempfile.TemporaryFile() as sink, self.assertRaises(wire.Rejected):
                full.evaluate_to(self.model, self.request, sink, _prepared=True)

    def test_import_reuses_proof_without_traversal(self):
        with ExitStack() as stack:
            setup_guard(stack)
            imported = self.import_local()
            actual, receipt = self.execute(imported, imported.model)
        expected, expected_receipt = self.execute()
        self.assertEqual(actual, expected)
        self.assertEqual(receipt, expected_receipt)

    def test_completion_receipt_tampering(self):
        data, receipt = self.execute()
        self.assertEqual(hashlib.sha256(data).hexdigest(), receipt['sha256'])
        for key, value in (('bytes_written', -1), ('sha256', '0' * 64), ('receipt_identity', '0' * 64),
                ('capacity', {}), ('extra', True)):
            with self.subTest(key=key), self.assertRaises(wire.Rejected):
                self.prepared.validate_receipt(dict(receipt, **{key: value}))

    def test_cross_process_inherited_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'payload').write_bytes(self.prepared.payload)
            (directory / 'receipt.json').write_text(json.dumps(self.prepared.receipt))
            (directory / 'expected.json').write_text(json.dumps(dict(input_identity=self.model.identity,
                request_identity=self.prepared.identity, limits=self.limits)))
            read_fd, write_fd = os.pipe()
            self.authority.inherit_key(write_fd)
            os.close(write_fd)
            try:
                subprocess.run([sys.executable, '-B', __file__, str(directory), str(read_fd)],
                    pass_fds=(read_fd,), check=True, capture_output=True)
            finally:
                os.close(read_fd)
            expected, receipt = self.execute()
            self.assertEqual((directory / 'output').read_bytes(), expected)
            self.assertEqual(json.loads((directory / 'completion.json').read_text()), receipt)

    def test_structurally_complex_fixture_matches_legacy_bytes(self):
        spec = complex_fixture.specification(scenarios=9, sources=31, splits=4)
        model = api.DesignSpace.certified(spec)
        request = complex_fixture.request(model, 2)
        prepared = model.prepare_summary(request, self.limits, authority=self.authority)
        data, receipt = self.execute(prepared, model)
        legacy = api.DesignSpace(spec, admission_only=True)
        certificate = legacy.summary_capacity(request, self.limits)
        with tempfile.TemporaryFile() as sink:
            legacy.evaluate_to(request, sink, capacity=certificate)
            sink.seek(0)
            self.assertEqual(data, sink.read())

    def test_reuse_checks_current_disk_and_regular_sink(self):
        import io
        with ExitStack() as stack:
            numerical_guard(stack)
            with patch.object(capacity, '_available', return_value=0), self.assertRaises(wire.Rejected):
                self.execute()
            with self.assertRaises(wire.Rejected):
                self.model.evaluate_to(self.prepared, io.BytesIO(), setup=self.prepared.receipt, limits=self.limits)

    def test_reuse_sink_failure_cleans_spools(self):
        before = set(Path(tempfile.gettempdir()).iterdir())
        with tempfile.TemporaryFile() as output:
            class FailingSink:
                def fileno(self):
                    return output.fileno()

                def write(self, data):
                    raise OSError('public-synthetic-sink-failure')
            with self.assertRaises(OSError):
                self.model.evaluate_to(self.prepared, FailingSink(), setup=self.prepared.receipt, limits=self.limits)
        self.assertEqual(set(Path(tempfile.gettempdir()).iterdir()), before)


if __name__ == '__main__':
    directory, descriptor = Path(sys.argv[1]), int(sys.argv[2])
    with ExitStack() as stack:
        setup_guard(stack)
        prepared = setup.import_setup((directory / 'payload').read_bytes(),
            json.loads((directory / 'receipt.json').read_text()), key_fd=descriptor,
            **json.loads((directory / 'expected.json').read_text()))
        os.close(descriptor)
        with (directory / 'output').open('x+b') as sink:
            receipt = prepared.model.evaluate_to(prepared, sink, setup=prepared.receipt,
                limits=setup.thaw(prepared.certificate['limits']))
        prepared.validate_receipt(receipt)
        (directory / 'completion.json').write_text(json.dumps(receipt))
