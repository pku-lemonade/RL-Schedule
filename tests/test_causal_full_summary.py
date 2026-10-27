"""Public lossless-summary, exact byte-capacity and pre-evaluation gate proofs."""
from copy import deepcopy
from contextlib import ExitStack
from fractions import Fraction
import hashlib
import io
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
import causal_full_schema as schema
import causal_full_stream as stream
import causal_full_fixtures as fixtures
import causal_full_materialized_oracle as oracle
import counterfactual as formulas
import design_space as api
import design_space_schema as wire
from test_causal_full_scaling import IndexedPoints

EVIDENCE = dict(equivalence=[], large_capacity=None, live_state=[], cli=[])


def limits(**changes):
    directory = Path(tempfile.gettempdir())
    value = dict(version=capacity.LIMIT_VERSION, filesystem=str(directory), device=directory.stat().st_dev,
        frozen_available_bytes=1 << 38, output_limits_bytes=[], reserve_bytes=capacity.RESERVE,
        logs_and_gate_bytes=1048576, additional_temporary_bytes=0)
    value.update(changes)
    return value


def study(specification=None):
    model = api.DesignSpace(fixtures.model() if specification is None else specification, admission_only=True)
    request = fixtures.request(model)
    request['version'] = schema.SUMMARY_VERSION
    return model, request


def rehash(result):
    result['request_identity'] = formulas.identity(result['request'])
    result.pop('output_identity', None)
    result['output_identity'] = formulas.identity(result)
    return result


def summary_reference(result):
    result = deepcopy(result)
    result['version'] = result['request']['version'] = schema.SUMMARY_VERSION
    for pair in result['comparisons']:
        del pair['deltas']
    return rehash(result)


def reconstruct(result):
    for pair in result['comparisons']:
        left, right = result['grid'][pair['left']], result['grid'][pair['right']]
        yield [dict(scenario=row['id'], delta_ns=full.rational(full.number(row['latency_ns']) - full.number(other['latency_ns'])))
            for row, other in zip(left['scenarios'], right['scenarios'])]


def sink_result(model, request, certificate, block=1):
    with tempfile.TemporaryFile('w+b') as sink:
        receipt = model.evaluate_to(request, sink, block_size=block, capacity=certificate)
        sink.seek(0)
        return sink.read(), receipt


class SummaryTests(unittest.TestCase):
    def check_equivalence(self, spec, alter=None):
        model, request = study(spec)
        if alter:
            alter(request)
        full_request = dict(request, version=schema.VERSION)
        reference = oracle.evaluate(model, full_request)
        self.assertEqual(formulas.encoded(model.evaluate(full_request)), formulas.encoded(reference))
        expected = summary_reference(reference)
        certificate = model.summary_capacity(request, limits())
        self.assertEqual(formulas.encoded(model.evaluate(request, capacity=certificate)), formulas.encoded(expected))
        for block in (1, 3, 7):
            actual, receipt = sink_result(model, request, certificate, block)
            self.assertEqual(actual, formulas.encoded(expected))
            self.assertEqual(receipt['bytes_written'], len(actual))
            self.assertEqual(receipt['sha256'], hashlib.sha256(actual).hexdigest())
            self.assertEqual(receipt['capacity'], certificate)
            capacity.validate_receipt(model, request, receipt)
        for original, recovered in zip(reference['comparisons'], reconstruct(expected)):
            self.assertEqual(recovered, original['deltas'])
            values = [full.number(row['delta_ns']) for row in recovered]
            self.assertEqual(full.hull(values), original['delta_ns'])
            self.assertEqual([sum(value < 0 for value in values), sum(value == 0 for value in values), sum(value > 0 for value in values)],
                [original['faster'], original['equal'], original['slower']])
        self.assertLessEqual(len(actual), certificate['bounds']['result_bytes'])
        for key, value in expected.items():
            size = sum(len(token) for token in stream._tokens(value, 1))
            self.assertLessEqual(size, certificate['bounds']['groups'][key], key)
        self.assertLessEqual(sum(len(token) for token in stream._tokens(expected['pareto']['edges'], 2)),
            certificate['bounds']['edge_spool_bytes'])
        full.validate_result(expected)
        def check_width(value, kind):
            bound = certificate['digit_bounds'][kind]
            self.assertLessEqual(len(str(abs(value['numerator']))), bound['numerator_digits'])
            self.assertLessEqual(len(str(value['denominator'])), bound['denominator_digits'])
        for point in expected['grid']:
            for row in point['scenarios']:
                for field, kind in (('latency_ns', 'latency'), ('moe_ns', 'latency'), ('mfu_coefficient', 'coefficient')):
                    check_width(row[field], kind)
                for clock in row['clocks']:
                    check_width(clock['moe_ticks'], 'local')
                    if clock['layer_ticks'] is not None:
                        check_width(clock['layer_ticks'], 'local')
            for value in point['mfu']['numeric_interval'] or []:
                check_width(value, 'numeric')
        for pair in expected['comparisons'] + expected['oat']:
            for value in pair['delta_ns']:
                check_width(value, 'delta')
            for row in pair.get('deltas', []):
                check_width(row['delta_ns'], 'delta')
        for joint in expected['joint_minus_oat']:
            for row in joint['scenarios']:
                check_width(row['gain_ns'], 'joint')
        for surface in expected['surfaces']:
            for segment in surface['segments']:
                for field in ('layer_intercept', 'layer_slope', 'moe_intercept', 'moe_slope'):
                    check_width(segment[field], 'curve')
                for value in segment['inverse_scale_interval']:
                    check_width(value, 'endpoint')
            for knot in surface['knots']:
                check_width(knot['latency_ns'], 'knot_latency')
            for interval in surface['baseline_break_even_scale_intervals']:
                for value in interval:
                    check_width(value, 'scale')
            if surface['finite_saturation_scale'] is not None:
                check_width(surface['finite_saturation_scale'], 'scale')
            if surface['asymptotic_latency_ns'] is not None:
                check_width(surface['asymptotic_latency_ns'], 'curve')
        EVIDENCE['equivalence'].append(dict(bytes=len(actual), sha256=receipt['sha256'], bound=certificate['bounds']['result_bytes'],
            proof_identity=certificate['proof_identity'], pairs=len(expected['comparisons']), reconstructed=True))
        return expected, certificate

    def test_closed_versions_schemas_and_templates(self):
        model, request = study()
        self.assertEqual(model.template()['version'], schema.VERSION)
        self.assertEqual(model.template(output_detail='summary')['version'], schema.SUMMARY_VERSION)
        self.assertEqual(model.schema(output_detail='summary'), schema.request_schema(schema.SUMMARY_VERSION))
        summary = schema.result_schema(schema.SUMMARY_VERSION)['properties']
        self.assertNotIn('deltas', summary['comparisons']['items']['properties'])
        self.assertIn('deltas', summary['oat']['items']['properties'])
        self.assertEqual(set(summary), set(schema.result_schema()['properties']))
        for value in (None, '', False, 1, [], 'unknown'):
            with self.assertRaises(wire.Rejected):
                schema.result_schema(value)
        with self.assertRaises(wire.Rejected):
            model.template(output_detail='unknown')

    def test_retained_fields_and_delta_reconstruction_all_laws(self):
        for sharing in ('priority', 'shared_serial', 'co_bottleneck'):
            for membership in ('conservative', 'inclusive'):
                spec = fixtures.model()
                for row in spec['scenarios']:
                    row.update(sharing=sharing, membership=membership, priority=[0, 1] if sharing == 'priority' else None)
                self.check_equivalence(spec)

    def test_peak_null_ties_and_residual_equivalence(self):
        for mask in (0, 1, 2):
            spec = fixtures.model()
            spec['partition']['atoms'][-1].update(family_mask_conservative=mask, family_mask_inclusive=mask)
            self.check_equivalence(spec)
        for kind in ('accepted', 'hypothesis', 'unavailable'):
            spec = fixtures.model()
            spec['peak'].update(kind=kind, interval=['7', '11'] if kind != 'unavailable' else None,
                parameter=None, evidence='synthetic-peak-evidence' if kind != 'unavailable' else None,
                domain=None if kind == 'unavailable' else spec['peak']['domain'],
                missing_input='synthetic-missing-peak' if kind == 'unavailable' else None)
            self.check_equivalence(spec)

    def test_selected_empty_order_and_nonmonotonic_grids(self):
        for pairs in ([], [[2, 0], [0, 2], [8, 1]]):
            self.check_equivalence(fixtures.model(), lambda request: request.update(comparisons=dict(policy='selected', pairs=pairs)))
        def change(request):
            for axis in request['axes']:
                axis.update(domain=['0.5', '4'], grid=['0.5', '1', '4'])
        self.check_equivalence(fixtures.model(), change)

    def test_exact_escaped_identifier_framing_and_large_exponents(self):
        for exponent in (0, 75, wire._decimal()['maxLength'] - 4):
            spec = fixtures.model()
            spec['scenarios'][0]['id'] = 'public-scenario-with-longer-identifier'
            spec['source_nodes'][0]['metadata_json'] = json.dumps(dict(escaped='quote-"-slash-\\-snowman-☃'))
            small = '0.' + '0' * (exponent - 1) + '1' if exponent else '1'
            large = '1' + '0' * exponent
            spec['composition']['ticks_per_ns'] = small
            def change(request):
                if exponent:
                    for axis in request['axes']:
                        axis.update(domain=[small, large], grid=[small, '1', large])
            result, certificate = self.check_equivalence(spec, change)
            exact = sum(len(json.dumps(row['id']).encode()) for row in spec['scenarios'])
            self.assertEqual(certificate['identifier_lengths']['scenario_bytes'], exact)

    def test_compositional_byte_formulas_independent_serializer(self):
        for value in ({}, [], {'escaped-"☃': [0, -123456, {'denominator': 1000001, 'numerator': -10000}]}, ['x'] * 11):
            for depth in (0, 1, 2, 4):
                actual = sum(len(token) for token in stream._tokens(value, depth))
                if type(value) is dict:
                    computed = capacity.object_size({key: sum(len(token) for token in stream._tokens(child, depth + 1))
                        for key, child in value.items()}, depth)
                else:
                    computed = capacity.array_size(len(value), sum(sum(len(token) for token in stream._tokens(child, depth + 1))
                        for child in value), depth)
                self.assertEqual(actual, computed)
        for exponent in (0, 1, 9, 20, 200, 1000):
            for value in (10 ** exponent, 10 ** exponent - 1, 10 ** exponent + 1):
                self.assertEqual(capacity.digits(value), len(str(value)))

    def test_missing_unknown_cross_mode_and_budget_rejections_before_evaluation(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        mutations = [lambda value: value.pop('version'), lambda value: value.update(version='unknown'),
            lambda value: value.update(version=False), lambda value: value.update(version=None),
            lambda value: value.update(mode='causal_timing'), lambda value: value.update(output_detail='summary'),
            lambda value: value['comparisons'].update(pairs=[[0, 0]], policy='selected'),
            lambda value: value['comparisons'].update(pairs=[[0, 1], [0, 1]], policy='selected')]
        mutations += [lambda value, key=key: value['budget'].update({key: 0}) for key in schema.WORK]
        with patch.object(full, '_scenario', side_effect=AssertionError('evaluation before rejection')), \
                patch.object(full, '_build_result', side_effect=AssertionError('evaluation before rejection')):
            for mutation in mutations:
                bad = deepcopy(request)
                mutation(bad)
                with self.assertRaises(wire.Rejected):
                    model.evaluate(bad, capacity=certificate)
            with self.assertRaises(wire.Rejected):
                model.evaluate(request)
            with self.assertRaises(wire.Rejected):
                model.evaluate(dict(request, version=schema.VERSION), capacity=certificate)

    def test_result_closed_schema_mode_oat_and_required_outputs(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        result = model.evaluate(request, capacity=certificate)
        mutations = [lambda value: value.update(version=schema.VERSION),
            lambda value: value['request'].update(version=schema.VERSION), lambda value: value.update(extra=1),
            lambda value: value['oat'][0].pop('deltas')]
        mutations += [lambda value, payload=payload: value['comparisons'][0].update(deltas=payload) for payload in ([], None, [0])]
        mutations += [lambda value, key=key: value.pop(key) for key in ('grid', 'joint_minus_oat', 'migrations', 'surfaces', 'pareto')]
        for mutation in mutations:
            bad = deepcopy(result)
            mutation(bad)
            with self.assertRaises(wire.Rejected):
                full.validate_result(rehash(bad))
        detail = model.evaluate(dict(request, version=schema.VERSION))
        del detail['comparisons'][0]['deltas']
        with self.assertRaises(wire.Rejected):
            full.validate_result(rehash(detail))

    def test_identity_grid_rational_and_null_tampering(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        result = model.evaluate(request, capacity=certificate)
        mutations = [lambda value: value.update(output_identity='stale'),
            lambda value: value['grid'][0]['scenarios'].reverse(),
            lambda value: value['grid'][0]['scenarios'][0].update(id='wrong'),
            lambda value: value['grid'][0]['scenarios'][0]['latency_ns'].update(numerator=True),
            lambda value: value['grid'][0]['scenarios'][0]['latency_ns'].update(denominator=0),
            lambda value: value['grid'][0]['scenarios'][0]['latency_ns'].update(numerator=2, denominator=2),
            lambda value: value['unavailable'].update(physical_promotion=True)]
        for mutation in mutations:
            bad = deepcopy(result)
            mutation(bad)
            with self.assertRaises(wire.Rejected):
                full.validate_result(bad)

    def test_s1_rehashed_derived_omissions_remain_outside_validator(self):
        model, request = study()
        result = model.evaluate(request, capacity=model.summary_capacity(request, limits()))
        changed = deepcopy(result)
        changed['comparisons'].pop()
        changed['pareto']['edges'].pop()
        changed['pareto']['robust_frontier'] = []
        changed['comparisons'][0]['faster'] += 1
        full.validate_result(rehash(changed))
        self.assertNotEqual(formulas.encoded(changed), formulas.encoded(result))
        detail = model.evaluate(dict(request, version=schema.VERSION))
        detail['comparisons'][0]['deltas'][0]['delta_ns'] = dict(numerator=12345, denominator=7)
        full.validate_result(rehash(detail))

    def test_summary_prefix_has_no_delta_lists_or_spools(self):
        for scenarios in (8, 64, 512, 4096):
            for block in (1, 3):
                points = IndexedPoints(19, scenarios, limit=2 * block * scenarios)
                with patch.object(stream.ArraySpool, '__init__', side_effect=AssertionError('no summary delta spool')):
                    producer = full._comparison_blocks(points, dict(policy='all', pairs=[]), block, reverse=True, summary=True)
                    records = next(producer)
                    self.assertEqual(len(records), block)
                    self.assertTrue(all(set(pair) == {'left', 'right', 'delta_ns', 'faster', 'equal', 'slower'} for pair, edge in records))
                    self.assertTrue(all(len(pair['delta_ns']) == 2 for pair, edge in records))
                    self.assertEqual(points.reads, 2 * block * scenarios)
                    producer.close()
                    self.assertEqual(records, [])
                EVIDENCE['live_state'].append(dict(scenarios=scenarios, block_size=block, pair_records=block,
                    extrema_records=2 * block, delta_rows=0, delta_spools=0))

    def test_summary_public_path_skips_delta_spools_and_keeps_oat(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        initialize = stream.ArraySpool.__init__
        depths = []
        def observe(spool, depth, *args):
            self.assertNotEqual(depth, 3)
            depths.append(depth)
            initialize(spool, depth, *args)
        with patch.object(stream.ArraySpool, '__init__', observe):
            output, receipt = sink_result(model, request, certificate, 3)
        self.assertEqual(depths, [1, 2])
        self.assertTrue(all(row['deltas'] for row in json.loads(output)['oat']))

    def test_short_writes_failure_and_cleanup(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        with tempfile.TemporaryFile('w+b') as target:
            class Short:
                def fileno(self):
                    return target.fileno()
                def write(self, data):
                    return target.write(data[:13])
            receipt = model.evaluate_to(request, Short(), capacity=certificate)
            self.assertEqual(receipt['bytes_written'], target.tell())
        initialized, append = stream.ArraySpool.__init__, stream.ArraySpool.append
        opened = []
        def observe(spool, *args):
            initialized(spool, *args)
            opened.append(spool)
        def fail(spool, item):
            if spool.depth == 1:
                raise OSError('injected comparison write failure')
            return append(spool, item)
        with tempfile.TemporaryFile('w+b') as target, patch.object(stream.ArraySpool, '__init__', observe), patch.object(stream.ArraySpool, 'append', fail):
            with self.assertRaises(OSError):
                model.evaluate_to(request, target, capacity=certificate)
            self.assertEqual(target.tell(), 0)
        self.assertTrue(all(spool.file.closed for spool in opened))

    def test_output_and_disk_capacity_exact_fit_boundaries(self):
        model, request = study()
        initial = model.summary_capacity(request, limits())
        maximum = initial['bounds']['result_bytes']
        for offset in (-1, 0, 1):
            declared = limits(output_limits_bytes=[maximum + offset])
            with patch.object(full, '_scenario', side_effect=AssertionError('gate evaluated scenarios')):
                if offset < 0:
                    with self.assertRaises(wire.Rejected):
                        model.summary_capacity(request, declared)
                else:
                    model.summary_capacity(request, declared)
        peak = initial['bounds']['peak_additional_disk_bytes']
        with patch.object(capacity, '_available', return_value=peak - 1):
            with self.assertRaises(wire.Rejected):
                model.summary_capacity(request, limits())
        with patch.object(capacity, '_available', return_value=peak):
            model.summary_capacity(request, limits())

    def test_reserve_retention_other_storage_and_monotonicity(self):
        model, request = study()
        first = model.summary_capacity(request, limits())
        other = model.summary_capacity(request, limits(additional_temporary_bytes=9999))
        self.assertEqual(other['bounds']['peak_additional_disk_bytes'] - first['bounds']['peak_additional_disk_bytes'], 9999)
        bounds = first['bounds']
        self.assertEqual(bounds['peak_additional_disk_bytes'], 2 * bounds['result_bytes'] + bounds['comparison_spool_bytes']
            + bounds['edge_spool_bytes'] + capacity.RESERVE + 1048576)
        more = fixtures.model()
        more['scenarios'] += [dict(deepcopy(row), id=row['id'] + '-extra') for row in more['scenarios']]
        larger_model, larger_request = study(more)
        larger = larger_model.summary_capacity(larger_request, limits())
        self.assertGreater(larger['bounds']['result_bytes'], bounds['result_bytes'])
        for key, value in (('reserve_bytes', 0), ('logs_and_gate_bytes', 0), ('frozen_available_bytes', False), ('unknown', 1)):
            with self.assertRaises(wire.Rejected):
                model.summary_capacity(request, limits(**{key: value}))

    def test_capacity_certificate_tampering_and_frozen_study_mismatch(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        mutations = [lambda value: value.update(proof_identity='stale'), lambda value: value.update(extra=1),
            lambda value: value.update(output_version=schema.VERSION), lambda value: value.update(source_identity='stale'),
            lambda value: value['bounds'].update(result_bytes=1), lambda value: value['bounds'].update(other_spool_bytes=False),
            lambda value: value['identifier_lengths'].update(scenario_bytes=1), lambda value: value['digit_bounds'].clear(),
            lambda value: value['limits'].update(frozen_available_bytes=1), lambda value: value['work'].update(designs=1)]
        with patch.object(full, '_scenario', side_effect=AssertionError('gate failure evaluated scenarios')), \
                patch.object(full, '_build_result', side_effect=AssertionError('gate failure evaluated results')):
            for mutation in mutations:
                bad = deepcopy(certificate)
                mutation(bad)
                with self.assertRaises(wire.Rejected):
                    model.evaluate(request, capacity=bad)
            smaller = deepcopy(request)
            smaller['comparisons'] = dict(policy='selected', pairs=[])
            with self.assertRaises(wire.Rejected):
                model.evaluate(smaller, capacity=certificate)
            with patch.object(capacity, '_available', return_value=0):
                with self.assertRaises(wire.Rejected):
                    model.evaluate(request, capacity=certificate)
        model.summary_capacity(smaller, limits())

    def test_unknown_width_and_nonfile_or_wrong_filesystem_fail_closed(self):
        with patch.object(full, '_scenario', side_effect=AssertionError('admission must not evaluate')):
            model, request = study()
            certificate = model.summary_capacity(request, limits())
            with patch.object(capacity, '_widths', side_effect=wire.Rejected('CAPACITY_WIDTH', '$', 'unproved')):
                with self.assertRaises(wire.Rejected):
                    model.evaluate(request, capacity=certificate)
            with self.assertRaises(wire.Rejected):
                model.evaluate_to(request, io.BytesIO(), capacity=certificate)
            with self.assertRaises(wire.Rejected):
                model.summary_capacity(request, limits(device=limits()['device'] + 1))

    def test_hash_seed_block_and_cli_api_byte_identity(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        expected, initial = sink_result(model, request, certificate)
        producer = full._comparison_blocks
        def reversed_blocks(*args, **kwargs):
            return producer(*args, **dict(kwargs, reverse=True))
        for block in (1, 3, 7):
            with patch.object(full, '_comparison_blocks', reversed_blocks):
                actual, receipt = sink_result(model, request, certificate, block)
            self.assertEqual(actual, expected)
            self.assertEqual(receipt, initial)
        entry = Path(api.__file__)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (('model', model.specification()), ('request', request), ('capacity', certificate), ('limits', limits())):
                (root / (name + '.json')).write_bytes(formulas.encoded(value))
            for seed in ('5', '97'):
                with (root / ('result-' + seed)).open('wb') as output:
                    result = subprocess.run([sys.executable, '-B', str(entry), 'evaluate', '--model', str(root / 'model.json'),
                        '--request', str(root / 'request.json'), '--capacity', str(root / 'capacity.json'),
                        '--receipt', str(root / ('receipt-' + seed))], stdout=output, stderr=subprocess.PIPE,
                        env=dict(os.environ, PYTHONHASHSEED=seed), check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / ('result-' + seed)).read_bytes(), expected)
                self.assertEqual(json.loads((root / ('receipt-' + seed)).read_bytes()), initial)
                EVIDENCE['cli'].append(dict(seed=seed, sha256=initial['sha256'], exact_bytes=True))
            result = subprocess.run([sys.executable, '-B', str(entry), 'capacity', '--model', str(root / 'model.json'),
                '--request', str(root / 'request.json'), '--limits', str(root / 'limits.json')], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), certificate)

    def test_summary_failure_stages_never_complete_and_close_spools(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        initialize, append, finish = stream.ArraySpool.__init__, stream.ArraySpool.append, stream.ArraySpool.finish
        for stage in ('edge', 'hash', 'read', 'validation', 'corruption', 'emission'):
            opened = []
            def observe(spool, *args):
                initialize(spool, *args)
                opened.append(spool)
            def fail_edge(spool, item):
                if spool.depth == 2:
                    raise OSError('injected edge write')
                return append(spool, item)
            def corrupt(spool):
                finish(spool)
                spool.file.seek(0)
                spool.file.write(b'!')
            with tempfile.TemporaryFile('w+b') as target, ExitStack() as stack:
                stack.enter_context(patch.object(stream.ArraySpool, '__init__', observe))
                if stage == 'edge':
                    stack.enter_context(patch.object(stream.ArraySpool, 'append', fail_edge))
                elif stage == 'hash':
                    stack.enter_context(patch.object(stream, 'identity', side_effect=OSError('injected hash')))
                elif stage == 'read':
                    stack.enter_context(patch.object(stream.ArraySpool, 'chunks', side_effect=OSError('injected read')))
                elif stage == 'validation':
                    stack.enter_context(patch.object(full, 'validate_result', side_effect=wire.Rejected('OUTPUT', '$', 'injected validation')))
                elif stage == 'corruption':
                    stack.enter_context(patch.object(stream.ArraySpool, 'finish', corrupt))
                class FailedSink:
                    def fileno(self):
                        return target.fileno()
                    def write(self, data):
                        raise OSError('injected final emission')
                with self.assertRaises((OSError, wire.Rejected)):
                    model.evaluate_to(request, FailedSink() if stage == 'emission' else target, capacity=certificate)
            self.assertTrue(all(spool.file.closed for spool in opened))

    def test_cli_closed_summary_selectors_and_mode_rejections(self):
        entry = Path(api.__file__)
        for kind in ('request', 'result'):
            result = subprocess.run([sys.executable, '-B', str(entry), 'schema', '--kind', kind,
                '--mode', schema.MODE, '--output-detail', 'summary'], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = schema.request_schema(schema.SUMMARY_VERSION) if kind == 'request' else schema.result_schema(schema.SUMMARY_VERSION)
            self.assertEqual(result.stdout, formulas.encoded(expected))
        for kind, mode in (('model', schema.MODE), ('request', 'causal_timing')):
            result = subprocess.run([sys.executable, '-B', str(entry), 'schema', '--kind', kind,
                '--mode', mode, '--output-detail', 'summary'], capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)

    def test_completion_receipt_identity_binds_bytes_and_capacity(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        output, receipt = sink_result(model, request, certificate)
        capacity.validate_receipt(model, request, receipt)
        for key, value in (('bytes_written', len(output) - 1), ('sha256', '0' * 64), ('output_identity', '0' * 64), ('extra', 1)):
            changed = deepcopy(receipt)
            changed[key] = value
            with self.assertRaises(wire.Rejected):
                capacity.validate_receipt(model, request, changed)
        changed = deepcopy(receipt)
        changed['capacity']['bounds']['result_bytes'] += 1
        changed['receipt_identity'] = formulas.identity({key: value for key, value in changed.items() if key != 'receipt_identity'})
        with self.assertRaises(wire.Rejected):
            capacity.validate_receipt(model, request, changed)

    def test_large_synthetic_static_capacity_without_grid_or_numerical_work(self):
        spec = fixtures.model()
        spec['families'].extend([dict(id=name, law='inverse_partition_response', authority='caller_hypothesis') for name in ('gamma', 'delta')])
        original = spec['scenarios']
        spec['scenarios'] = [dict(deepcopy(original[index % len(original)]), id='public-synthetic-scenario-' + str(index)) for index in range(832)]
        for row in spec['scenarios']:
            if row['priority'] is not None:
                row['priority'] = [1, 0, 2, 3]
        spec['capacity']['records'] = {key: 1000000 for key in spec['capacity']['records']}
        with patch.object(full, '_scenario', side_effect=AssertionError('no numerical evaluation')), \
                patch.object(full, '_build_result', side_effect=AssertionError('no result evaluation')), \
                patch.object(full.itertools, 'product', side_effect=AssertionError('no Cartesian expansion')):
            model, request = study(spec)
            request['axes'] = [dict(id=row['id'], family=row['id'], unit='dimensionless_service_scale', domain=['1', '7'],
                grid=[str(value) for value in range(1, 8)]) for row in spec['families']]
            request['budget'] = {key: 10 ** 30 for key in schema.WORK}
            certificate = model.summary_capacity(request, limits())
        self.assertEqual(certificate['bounds']['cardinalities']['designs'], 2401)
        self.assertEqual(certificate['bounds']['cardinalities']['scenarios'], 832)
        self.assertLess(certificate['bounds']['peak_additional_disk_bytes'], 1 << 38)
        EVIDENCE['large_capacity'] = certificate


if __name__ == '__main__':
    unittest.main()
