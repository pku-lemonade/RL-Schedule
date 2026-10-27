"""Exact public differential tests for summary arithmetic and canonical encoding."""
from copy import deepcopy
from fractions import Fraction
import itertools
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model')]
import causal_full_analysis as full
import causal_full_fixtures as fixtures
import causal_full_materialized_oracle as oracle
import causal_full_schema as schema
import causal_full_stream as stream
import counterfactual as formulas
from test_causal_full_summary import study, limits, sink_result, summary_reference


def points(designs=19, scenarios=43, seed=19):
    generator = random.Random(seed)
    return [dict(index=index, rates=[full.rational(Fraction(generator.randrange(1, 13), divisor)) for divisor in (3, 5, 7)],
        scenarios=[dict(id='public-' + str(scenario), latency_ns=full.rational(Fraction(generator.randrange(1, 101),
            generator.choice((2, 3, 5, 7, 11))))) for scenario in range(scenarios)]) for index in range(designs)]


class SummaryComputeTests(unittest.TestCase):
    def check_pairs(self, rows, selection):
        indices = list(itertools.permutations(range(len(rows)), 2)) if selection['policy'] == 'all' else selection['pairs']
        expected = []
        for left, right in indices:
            paired, edge = full._pair(rows[left], rows[right], deltas=None)
            del paired['deltas']
            expected.append((paired, edge))
        for block_size in (1, 7, 129):
            for reverse in (False, True):
                with patch.object(full, '_pair', side_effect=AssertionError('no Python pair-by-scenario loop')):
                    actual = [deepcopy(row) for block in full._comparison_blocks(rows, selection, block_size,
                        reverse=reverse, summary=True) for row in block]
                self.assertEqual(actual, expected)
                self.assertEqual(formulas.encoded(actual), formulas.encoded(expected))

    def test_all_pairs_exact_random_rationals(self):
        for seed in (0, 11, 97):
            self.check_pairs(points(seed=seed), dict(policy='all', pairs=[]))

    def test_selected_order_duplicates_and_empty(self):
        rows = points()
        for selected in ([], [[4, 1], [0, 18], [1, 4], [4, 1], [18, 0], [4, 2]]):
            self.check_pairs(rows, dict(policy='selected', pairs=selected))

    def test_ties_rate_quantifiers_and_constant_scenarios(self):
        rows = points(7, 13)
        for index, point in enumerate(rows):
            point['rates'] = [full.rational(index % 3), full.rational(2 - index % 3)]
            for scenario, row in enumerate(point['scenarios']):
                row['latency_ns'] = full.rational(7 if scenario % 2 else index % 3 + 1)
        self.check_pairs(rows, dict(policy='all', pairs=[]))

    def test_int64_subtraction_boundary_and_big_integer_fallback(self):
        selection = dict(policy='all', pairs=[])
        for magnitude, dtype in (((1 << 62) - 1, 'int64'), (1 << 62, 'object'), (10**180, 'object')):
            rows = points(3, 3)
            for index, point in enumerate(rows):
                point['scenarios'] = [dict(id='public-' + str(scenario), latency_ns=full.rational(value))
                    for scenario, value in enumerate((2 * magnitude + (0, -magnitude, magnitude)[index], magnitude + 1, 3 * magnitude))]
            self.assertEqual(str(full._SummaryPairs(rows, selection).values.dtype), dtype)
            self.check_pairs(rows, selection)

    def test_coprime_denominators_and_large_common_offset(self):
        rows = points(11, 17)
        for index, point in enumerate(rows):
            for scenario, row in enumerate(point['scenarios']):
                row['latency_ns'] = full.rational(Fraction(10**120 + index, 2**(scenario + 67) + index * 2 + 1))
        with patch.object(Fraction, '__float__', side_effect=AssertionError('no float conversion')):
            self.check_pairs(rows, dict(policy='all', pairs=[]))

    def test_lattice_is_cached_by_left_not_by_pair_or_scenario(self):
        rows = points(137, 29)
        exact = full._SummaryPairs(rows, dict(policy='selected', pairs=[[0, 1], [0, 136], [1, 0]]))
        exact.pair(0, 1)
        first = exact.aggregates
        exact.pair(0, 136)
        self.assertIs(exact.aggregates, first)
        self.assertEqual(set(first), {1, 136})
        exact.pair(1, 0)
        self.assertIsNot(exact.aggregates, first)
        self.assertEqual(set(exact.aggregates), {0})

    def test_full_semantics_and_canonical_bytes_varied_public_models(self):
        for seed in (3, 17, 41):
            spec = fixtures.model()
            spec['partition']['atoms'][-1].update(family_mask_conservative=seed % 3, family_mask_inclusive=seed % 3)
            spec['scenarios'] = [dict(spec['scenarios'][index % 5], id='public-varied-' + str(index)) for index in range(11)]
            model, request = study(spec)
            for axis in request['axes']:
                axis.update(domain=['0.5', '2.5'], grid=['0.5', '1', '2.5'])
            request['surfaces'] = [dict(surface, domain=['1', '2.5']) for surface in request['surfaces']]
            expected = summary_reference(oracle.evaluate(model, dict(request, version=schema.VERSION)))
            certificate = model.summary_capacity(request, limits())
            self.assertEqual(model.evaluate(request, capacity=certificate), expected)
            for block_size in (1, 5, 127):
                encoded, receipt = sink_result(model, request, certificate, block_size)
                self.assertEqual(encoded, formulas.encoded(expected))
                self.assertEqual(json.loads(encoded), expected)
                self.assertEqual(receipt['output_identity'], expected['output_identity'])

    def test_canonical_native_encoder_escaping_and_unbounded_integers(self):
        value = dict(z='\u2028\U00012345\ud800\x00\b/<>\\"\n', a=[10**400, -10**300, True, False, None, {}, []])
        for depth in (2, 3, 7):
            self.assertEqual(b''.join(stream._summary_tokens(value, depth)), b''.join(stream._tokens(value, depth)))

    def test_four_active_axes_high_residual_bits_and_all_response_laws(self):
        from summary_scale_probe import specification
        spec = specification()
        spec['scenarios'] = spec['scenarios'][:16] + spec['scenarios'][384:416] + spec['scenarios'][-16:]
        spec['partition']['atoms'][-1].update(family_mask_conservative=1 << 25, family_mask_inclusive=1 << 25)
        model, request = study(spec)
        request['axes'] = [dict(id=row['id'], family=row['id'], unit='dimensionless_service_scale',
            domain=['1', '2'], grid=['1', '2']) for row in spec['families'][:4]]
        request['surfaces'] = [dict(id='public-joint', axes=[row['id'] for row in request['axes']],
            domain=['1', '2'], extrapolation='inverse_scale_to_infinity')]
        request['budget'] = dict.fromkeys(request['budget'], 10**12)
        expected = summary_reference(oracle.evaluate(model, dict(request, version=schema.VERSION)))
        certificate = model.summary_capacity(request, limits())
        for block in (1, 17):
            encoded, receipt = sink_result(model, request, certificate, block)
            self.assertEqual(encoded, formulas.encoded(expected))

    def test_memoized_validation_checks_detached_and_shared_mutations(self):
        model, request = study()
        result = model.evaluate(request, capacity=model.summary_capacity(request, limits()))
        from test_causal_full_summary import rehash
        import design_space_schema as wire
        for detached in (False, True):
            changed = deepcopy(result)
            row = changed['grid'][-1]['scenarios'][-1]
            if detached:
                row['clocks'] = deepcopy(row['clocks'])
            row['clocks'][0]['moe_ticks'] = full.rational(999)
            with self.assertRaises(wire.Rejected):
                full.validate_result(rehash(changed))

    def test_compiled_schema_success_and_failure_match_original(self):
        import design_space_schema as wire
        nodes = {}
        def collect(node):
            nodes[json.dumps(node, sort_keys=True)] = node
            for child in node.get('properties', {}).values():
                collect(child)
            if 'items' in node:
                collect(node['items'])
        collect(schema.result_schema(schema.SUMMARY_VERSION))
        collect(schema.model_schema())
        values = [None, False, True, -1, 0, 1, 10**100, 1.25, '', 'public', '1', [], {}, [1],
            {'numerator': 1, 'denominator': 3}, {'numerator': True, 'denominator': 0}]
        def outcome(check, value):
            try:
                check(value)
                return None
            except wire.Rejected as error:
                return error.decision
        for node in nodes.values():
            for value in values:
                expected = outcome(lambda item: wire.validate(item, node), value)
                self.assertEqual(outcome(wire.compile_validator(node), value), expected)
                self.assertEqual(outcome(wire.compile_validator(node, memo=set()), value), expected)


if __name__ == '__main__':
    unittest.main()
