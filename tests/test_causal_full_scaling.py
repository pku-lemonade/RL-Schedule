"""Public-only exact producer equivalence and bounded-consumer regressions."""
from copy import deepcopy
from fractions import Fraction
import gc
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

MODULES = Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'
sys.path.insert(0, str(MODULES))
import causal_full_analysis as full
import causal_full_stream as stream
import causal_full_fixtures as fixtures
import causal_full_materialized_oracle as oracle
import design_space as api
import design_space_schema as wire
import counterfactual as formulas

SCALING_EVIDENCE = dict(equivalence=[], prefix=[], resource=None)


class IndexedPoints:
    def __init__(self, designs, scenarios, limit=None):
        self.designs, self.scenarios, self.limit = designs, scenarios, limit
        self.reads = 0

    def __len__(self):
        return self.designs

    def __getitem__(self, index):
        if index >= self.designs:
            raise IndexError(index)
        def rows():
            for scenario in range(self.scenarios):
                self.reads += 1
                if self.limit is not None and self.reads > self.limit:
                    raise AssertionError('scenario over-read before bounded first output')
                yield dict(id='scenario-' + str(scenario),
                    latency_ns=full.rational(Fraction(17 + (index + scenario) % 11, 7)))
        return dict(index=index, rates=[full.rational(Fraction(index + 1, 3))], scenarios=rows())


class HashSink:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.count = 0

    def write(self, data):
        self.digest.update(data)
        self.count += len(data)
        return len(data)


class ScalingTests(unittest.TestCase):
    def check_fixture(self, spec, configure=None):
        model = api.DesignSpace(spec)
        request = fixtures.request(model)
        if configure:
            configure(request)
        with patch.object(full, '_scenario', wraps=full._scenario) as baseline_scenario:
            expected = formulas.encoded(oracle.evaluate(model, deepcopy(request)))
        scenario_calls = baseline_scenario.call_count + len(model._specification['scenarios']) * len(json.loads(expected)['grid'])
        self.assertEqual(formulas.encoded(model.evaluate(request)), expected)
        for block_size in (1, 5, 7):
            for reverse in (False, True):
                producer = full._comparison_blocks
                def scheduled(*args, **kwargs):
                    return producer(*args, **dict(kwargs, reverse=reverse))
                sink = io.BytesIO()
                with patch.object(full, '_comparison_blocks', scheduled), patch.object(full, '_scenario', wraps=full._scenario) as scenario:
                    receipt = model.evaluate_to(request, sink, block_size=block_size)
                self.assertEqual(sink.getvalue(), expected)
                result = json.loads(expected)
                self.assertEqual(scenario.call_count, scenario_calls)
                self.assertEqual(receipt, dict(output_identity=result['output_identity'],
                    bytes_written=len(expected), sha256=hashlib.sha256(expected).hexdigest()))
        SCALING_EVIDENCE['equivalence'].append(dict(input_identity=model.identity,
            request_identity=formulas.identity(request), output_sha256=hashlib.sha256(expected).hexdigest(),
            bytes=len(expected), blocks=[1, 5, 7], reversed_and_forward=True, scenario_calls=scenario_calls))
        return json.loads(expected)

    def test_complete_oracle_laws_residuals_and_unknowns(self):
        for mask in (0, 1, 2):
            spec = fixtures.model()
            spec['partition']['atoms'][-1].update(family_mask_conservative=mask, family_mask_inclusive=mask)
            def mixed(request):
                for axis in request['axes']:
                    axis.update(domain=['0.5', '4'], grid=['0.5', '1', '1.5', '4'])
            result = self.check_fixture(spec, mixed)
            self.assertIsNone(result['unavailable']['causal_latency_ns'])
            self.assertFalse(result['unavailable']['physical_promotion'])
            self.assertIsNone(result['model']['source_nodes'][0]['source_cost_ticks'])

    def test_peak_scenario_order_and_tied_terminals(self):
        for kind in ('symbolic', 'accepted', 'unavailable'):
            spec = fixtures.model()
            spec['scenarios'].reverse()
            spec['scenarios'][-1]['priority'] = [0, 1]
            if kind == 'accepted':
                spec['peak'].update(kind=kind, parameter=None, interval=['5', '7'], evidence='caller-peak')
            elif kind == 'unavailable':
                spec['peak'].update(kind=kind, parameter=None, domain=None, missing_input='not-provided')
            spec['composition']['participants'][0]['attention_offset_ticks'] = 13
            result = self.check_fixture(spec)
            self.assertTrue(any(len(row['terminals']) > 1 for point in result['grid'] for row in point['scenarios']))

    def test_selected_empty_complete_and_single_design(self):
        for pairs in ([], [[8, 0], [0, 8], [3, 1]], list(itertools.permutations(range(9), 2))):
            def select(request):
                request['comparisons'] = dict(policy='selected', pairs=[list(pair) for pair in pairs])
            result = self.check_fixture(fixtures.model(), select)
            self.assertEqual([(row['left'], row['right']) for row in result['comparisons']], list(map(tuple, pairs)))
            self.assertEqual(result['pareto']['coverage'], 'complete' if len(pairs) == 72 else 'selected_pairs')
        comparisons, edges = [], []
        pareto = full._comparisons(IndexedPoints(1, 3), dict(policy='all', pairs=[]), comparisons, edges)
        self.assertEqual(pareto['robust_frontier'], [0])
        self.assertEqual(comparisons, [])
        self.assertEqual(edges, [])

    def test_pair_quantifiers_transitions_and_duplicate_order(self):
        points = [dict(index=index, rates=list(map(full.rational, rates)),
            scenarios=[dict(id='case-' + str(position), latency_ns=full.rational(value))
                for position, value in enumerate(values)])
            for index, (rates, values) in enumerate([
                ([1, 1], [Fraction(5, 3)] * 3), ([1, 1], [Fraction(5, 3)] * 3),
                ([2, 2], [1, 2, Fraction(5, 3)]), ([2, 1], [2, 2, 2]),
                ([1, 2], [1, 1, 1]), ([2, 2], [2, 2, 2])])]
        selection = dict(policy='selected', pairs=[[0, 1], [0, 2], [2, 0], [0, 5], [0, 1], [3, 4]])
        expected = [(oracle._paired(points[left], points[right]), oracle._dominance(points[left], points[right]))
            for left, right in selection['pairs']]
        for size in (1, 4, 5):
            actual = []
            for block in full._comparison_blocks(points, selection, size, reverse=True):
                actual.extend((dict(comparison, deltas=json.loads(b''.join(comparison['deltas'].chunks()))), edge)
                    for comparison, edge in block)
            self.assertEqual(formulas.encoded(actual), formulas.encoded(expected))
        self.assertEqual(expected[0][0]['equal'], 3)
        self.assertTrue(expected[3][1]['robust'])
        self.assertFalse(expected[1][1]['robust'])

    def test_large_shape_prefix_lifetime_cancel_and_retry(self):
        original = full._pair
        live = dict(records=0, high=0)
        class Record(dict):
            pass
        def release():
            live['records'] -= 1
        def tracked(value):
            record = Record(value)
            live['records'] += 1
            live['high'] = max(live['high'], live['records'])
            weakref.finalize(record, release)
            return record
        def pair(left, right, **kwargs):
            comparison, edge = original(left, right, **kwargs)
            return tracked(comparison), tracked(edge)
        def block_hash(block):
            return stream.identity(dict(comparisons=[dict(comparison) for comparison, edge in block],
                pareto=dict(edges=[dict(edge) for comparison, edge in block])))
        for size in (1, 3):
            points = IndexedPoints(2401, 832, limit=2 * size * 832)
            with patch.object(full, '_pair', pair):
                producer = full._comparison_blocks(points, dict(policy='all', pairs=[]), size)
                block = next(producer)
                self.assertEqual([(row['left'], row['right']) for row, edge in block], [(0, right) for right in range(1, size + 1)])
                self.assertEqual(block[0][0]['deltas'].count, 832)
                self.assertIn(b'"scenario": "scenario-0"', next(block[0][0]['deltas'].chunks()))
                first = block_hash(block)
                producer.close()
                del block, producer
            gc.collect()
            self.assertEqual(live['records'], 0)
            self.assertLessEqual(live['high'], size * 2)
            retry = full._comparison_blocks(IndexedPoints(2401, 832, limit=2 * size * 832), dict(policy='all', pairs=[]), size)
            self.assertEqual(block_hash(next(retry)), first)
            retry.close()
            SCALING_EVIDENCE['prefix'].append(dict(designs=2401, scenarios=832, block_size=size,
                scenario_reads=points.reads, maximum_live_records=live['high'], live_after_cancel=live['records'],
                prefix_sha256=first, retry_equal=True))
            eager = full._comparison_blocks(IndexedPoints(2401, 832, limit=2 * size * 832), dict(policy='all', pairs=[]), size)
            with self.assertRaisesRegex(AssertionError, 'over-read'):
                list(eager)
            old_points = IndexedPoints(2401, 832, limit=2 * size * 832)
            with self.assertRaisesRegex(AssertionError, 'over-read'):
                retained = []
                for left, right in itertools.permutations(range(len(old_points)), 2):
                    retained.append(oracle._paired(old_points[left], old_points[right]))

    def test_large_shape_public_sink_prefix_without_scenario_work(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        baseline = oracle.evaluate(model, request)
        captured = []
        class Cancelled(Exception):
            pass
        def synthetic(instance, owned, comparisons, edges, block_size):
            pareto = full._comparisons(IndexedPoints(2401, 832, limit=2 * block_size * 832),
                dict(policy='all', pairs=[]), comparisons, edges, block_size)
            return dict(baseline, comparisons=comparisons, pareto=pareto)
        append = stream.ArraySpool.append
        def prefix(sink, item):
            append(sink, item)
            if not any(previous is sink for previous, count in captured):
                captured.append((sink, sink.count))
            if sink.depth == 2:
                raise Cancelled()
        sink = HashSink()
        with patch.object(full, '_build_result', synthetic), patch.object(stream.ArraySpool, 'append', prefix), \
                patch.object(full, '_scenario', side_effect=AssertionError('no scenario evaluation in large-shape probe')):
            with self.assertRaises(Cancelled):
                model.evaluate_to(request, sink)
        self.assertEqual(sink.count, 0)
        self.assertTrue(all(spool.file.closed for spool, count in captured))

    def test_every_budget_rejects_before_work_or_output(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        required = full._prepare(model._specification, model._timing, request)[1]
        request['budget'] = required
        sink = io.BytesIO()
        model.evaluate_to(request, sink)
        self.assertEqual(json.loads(sink.getvalue())['work'], required)
        for field in required:
            bad = deepcopy(request)
            bad['budget'][field] -= 1
            sink = HashSink()
            with patch.object(full, '_scenario', side_effect=AssertionError('scenario on rejection')), patch.object(full, '_comparison_blocks', side_effect=AssertionError('pairs on rejection')):
                with self.assertRaises(wire.Rejected) as caught:
                    model.evaluate_to(bad, sink)
            self.assertEqual(caught.exception.decision['code'], 'EVALUATION_BUDGET')
            self.assertEqual(sink.count, 0)

    def test_short_sink_failure_validation_and_hash_failure(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        expected = formulas.encoded(oracle.evaluate(model, request))
        class Short(io.BytesIO):
            def write(self, data):
                return super().write(data[:11])
        sink = Short()
        model.evaluate_to(request, sink)
        self.assertEqual(sink.getvalue(), expected)
        for returned in (0, None, -1):
            with self.assertRaises(OSError):
                model.evaluate_to(request, type('BadSink', (), {'write': lambda self, data: returned})())
        with patch.object(full, 'validate_result', side_effect=wire.Rejected('OUTPUT_IDENTITY', '$', 'injected failure')):
            sink = HashSink()
            with self.assertRaises(wire.Rejected):
                model.evaluate_to(request, sink)
            self.assertEqual(sink.count, 0)
        finish = stream.ArraySpool.finish
        def corrupt(spool):
            finish(spool)
            spool.file.seek(0)
            spool.file.write(b'!')
        with patch.object(stream.ArraySpool, 'finish', corrupt):
            sink = HashSink()
            with self.assertRaisesRegex(OSError, 'integrity'):
                model.evaluate_to(request, sink)
            self.assertEqual(sink.count, 0)

    def test_sink_identity_and_rational_failure_before_external_output(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        original = full._output_identity
        calls = []
        def stale(result):
            calls.append(True)
            return '0' * 64 if len(calls) == 1 else original(result)
        sink = HashSink()
        with patch.object(full, '_output_identity', stale):
            with self.assertRaises(wire.Rejected) as caught:
                model.evaluate_to(request, sink)
        self.assertEqual(caught.exception.decision['code'], 'OUTPUT_IDENTITY')
        self.assertEqual(sink.count, 0)
        append = stream.ArraySpool.append
        def unreduced(spool, item):
            if spool.depth == 3:
                item = dict(item, delta_ns=dict(numerator=2, denominator=2))
            return append(spool, item)
        with patch.object(stream.ArraySpool, 'append', unreduced):
            with self.assertRaises(wire.Rejected) as caught:
                model.evaluate_to(request, sink)
        self.assertEqual(caught.exception.decision['code'], 'OUTPUT_RATIONAL')
        self.assertEqual(sink.count, 0)

    def test_request_identity_types_duplicates_and_block_negatives(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        invalid = []
        for budget in ({}, None, dict(request['budget'], designs=True), dict(request['budget'], extra=1)):
            invalid.append(dict(request, budget=budget))
        invalid.extend([dict(request, input_identity='wrong'), dict(request, extra=None),
            dict(request, comparisons=dict(policy='selected', pairs=[[0, 1], [0, 1]])),
            dict(request, comparisons=dict(policy='selected', pairs=[[0, 0]])),
            dict(request, comparisons=dict(policy='selected', pairs=[[0, 900]]))])
        for bad in invalid:
            sink = HashSink()
            with patch.object(full, '_scenario', side_effect=AssertionError('early rejection')), \
                    patch.object(full, '_comparison_blocks', side_effect=AssertionError('early rejection')):
                with self.assertRaises(wire.Rejected) as eager:
                    model.evaluate(bad)
                with self.assertRaises(wire.Rejected) as bounded:
                    model.evaluate_to(bad, sink)
            self.assertEqual(eager.exception.decision, bounded.exception.decision)
            self.assertEqual(sink.count, 0)
        for block_size in (0, -1, True, 1.5, None):
            with self.assertRaises(ValueError):
                model.evaluate_to(request, HashSink(), block_size=block_size)

    def test_model_capacity_masks_priority_and_source_negatives(self):
        spec = fixtures.model()
        invalid = []
        for field, count in full._records(spec).items():
            bad = deepcopy(spec)
            bad['capacity']['records'][field] = count - 1
            invalid.append(bad)
        bad = deepcopy(spec)
        bad['capacity']['validation_work'] = 0
        invalid.append(bad)
        for field in ('family_mask_conservative', 'family_mask_inclusive'):
            for value in (-1, True, 1.5, '1', None, 4, 3):
                bad = deepcopy(spec)
                bad['partition']['atoms'][-1][field] = value
                invalid.append(bad)
        for key, value in (('priority', [0, 0]), ('priority', [True, 1]), ('priority', None),
                ('exposure_mask', -1), ('exposure_mask', 4), ('sharing', 'unknown')):
            bad = deepcopy(spec)
            bad['scenarios'][0][key] = value
            invalid.append(bad)
        bad = deepcopy(spec)
        bad['source_nodes'][0]['source_cost_ticks'] = 1
        invalid.append(bad)
        bad = deepcopy(spec)
        bad['join']['dependencies'][-1].update(status='exact', identity='invented-promotion', unknown=None)
        invalid.append(bad)
        for bad in invalid:
            with patch.object(full, '_scenario', side_effect=AssertionError('invalid model must reject before baseline')), \
                    patch.object(full, '_comparison_blocks', side_effect=AssertionError('no comparisons on admission')):
                with self.assertRaises(wire.Rejected):
                    api.DesignSpace(bad)
        for mask in (0, 1, 2):
            valid = deepcopy(spec)
            valid['partition']['atoms'][-1].update(family_mask_conservative=mask, family_mask_inclusive=mask)
            with patch.object(full, '_scenario', wraps=full._scenario) as baseline, \
                    patch.object(full, '_build_result', side_effect=AssertionError('no full evaluation on admission')):
                api.DesignSpace(valid)
                self.assertEqual(baseline.call_count, 1)

    def test_rational_identity_precedence_and_s1(self):
        model = api.DesignSpace(fixtures.model())
        result = model.evaluate(fixtures.request(model))
        for rational, code in ((dict(numerator=2, denominator=2), 'OUTPUT_RATIONAL'), (full.rational(999), 'OUTPUT_IDENTITY')):
            bad = deepcopy(result)
            bad['comparisons'][0]['deltas'][0]['delta_ns'] = rational
            with self.assertRaises(wire.Rejected) as caught:
                full.validate_result(bad)
            self.assertEqual(caught.exception.decision['code'], code)
        bad['output_identity'] = formulas.identity({key: value for key, value in bad.items() if key != 'output_identity'})
        full.validate_result(bad)
        self.assertNotEqual(formulas.encoded(bad), formulas.encoded(result))
        for field in ('oat', 'comparisons', 'joint_minus_oat', 'migrations', 'surfaces'):
            bad[field] = []
        bad['output_identity'] = formulas.identity({key: value for key, value in bad.items() if key != 'output_identity'})
        full.validate_result(bad)

    def test_cli_hash_seeds_and_public_api_identity(self):
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        expected = formulas.encoded(oracle.evaluate(model, request))
        with tempfile.TemporaryDirectory() as directory:
            model_path, request_path = Path(directory) / 'model.json', Path(directory) / 'request.json'
            model_path.write_bytes(formulas.encoded(model.specification()))
            request_path.write_bytes(formulas.encoded(request))
            for seed in ('5', '97'):
                process = subprocess.run([sys.executable, '-B', str(MODULES / 'design_space.py'), 'evaluate',
                    '--model', str(model_path), '--request', str(request_path)], capture_output=True,
                    env=dict(os.environ, PYTHONHASHSEED=seed), check=False)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stdout, expected)

    def test_sink_subprocess_hash_seed_block_order_matrix(self):
        model = api.DesignSpace(fixtures.model())
        expected = formulas.encoded(oracle.evaluate(model, fixtures.request(model)))
        for seed in ('5', '97'):
            for block in ('1', '5'):
                for order in ('forward', 'reverse'):
                    process = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('causal_full_sink_probe.py')), block, order],
                        capture_output=True, check=False, env=dict(os.environ, PYTHONHASHSEED=seed))
                    self.assertEqual(process.returncode, 0, process.stderr.decode())
                    self.assertEqual(process.stdout, expected)

    def test_resource_subprocess_complete_independent_sweeps(self):
        process = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('causal_full_resource_probe.py'))],
            capture_output=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        result = json.loads(process.stdout)
        SCALING_EVIDENCE['resource'] = result
        self.assertTrue(result['bounded'])
        self.assertEqual(len(result['cases']), 6)


if __name__ == '__main__':
    unittest.main()
