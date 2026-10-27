"""Exact scalar-oracle coverage for the bounded response reduction."""
from copy import deepcopy
from fractions import Fraction
import itertools
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
import counterfactual as formulas
import design_space as api
import design_space_schema as wire
import response_scale_fixture as entropy
from test_causal_full_summary import limits, sink_result, summary_reference, rehash


class ResponseLatticeTests(unittest.TestCase):
    def check_rows(self, spec, rate_vectors, blocks=(1, 7, 128)):
        model = api.DesignSpace(spec, admission_only=True)
        expected = [[full._scenario(spec, model._timing, scenario, rates) for scenario in spec['scenarios']]
            for rates in rate_vectors]
        for block in blocks:
            grouped = full._SummaryScenarios(spec, model._timing, block_size=block)
            for rates, reference in zip(rate_vectors, expected):
                with patch.object(full, '_scenario', side_effect=AssertionError('scalar response recomputation')):
                    actual = grouped.rows(rates)
                self.assertEqual(actual, reference)
                self.assertEqual(formulas.encoded(actual), formulas.encoded(reference))
                self.assertEqual(len({id(row) for row in actual}), len(actual))
                self.assertLessEqual(len(grouped.cache), 4096)

    def test_high_entropy_all_families_varied_rationals(self):
        generator = random.Random(6177)
        spec = entropy.specification(scenarios=97)
        rates = [[Fraction(generator.randrange(1, 18), generator.randrange(1, 12)) for family in spec['families']]
            for sample in range(7)]
        with patch.object(Fraction, '__float__', side_effect=AssertionError('approximation')):
            self.check_rows(spec, rates)

    def test_request_derived_cardinalities_and_blocks(self):
        for clocks, count in ((1, 1), (2, 17), (4, 129)):
            spec = entropy.specification(clocks=clocks, scenarios=count)
            self.check_rows(spec, [[Fraction(index % 7 + 1, index % 3 + 1) for index in range(26)]], (1, 16, 127, 1024))

    def test_integer_overflow_fallback(self):
        import numpy as np
        spec = entropy.specification(scenarios=17)
        rates = [[Fraction(1)] * 26, [Fraction(1, 10**100)] * 26,
            [Fraction(10**60 + index * 2 + 1, index + 1) for index in range(26)]]
        kinds = []
        original = np.array
        def observe(*args, **kwargs):
            if 'dtype' in kwargs:
                kinds.append(kwargs['dtype'])
            return original(*args, **kwargs)
        with patch.object(np, 'array', side_effect=observe):
            self.check_rows(spec, rates, (3,))
        self.assertIn(np.int64, kinds)
        self.assertIn(object, kinds)

    def test_ties_all_branches_and_standalone_clock(self):
        spec = entropy.specification(scenarios=23)
        for participant in spec['composition']['participants']:
            duration = next(clock['window'][1] for clock in spec['clocks'] if clock['id'] == participant['clock'])
            participant.update(attention_offset_ticks=10000, release_offset_ticks=10000 - duration, baseline_layer_ticks=10000)
        spec['composition']['baseline_layer_ticks'] = 10000
        spec['composition']['participants'].pop()
        spec['composition']['peak_participants'].pop()
        spec['peak_domain']['participants'].pop()
        spec['peak']['domain'] = deepcopy(spec['peak_domain'])
        self.check_rows(spec, [[Fraction(1)] * 26, [Fraction(3, 2)] * 26])

    def test_residual_zero_and_high_family_bits(self):
        for mask in (0, 1, 1 << 25):
            spec = entropy.specification(scenarios=31)
            for atom in spec['partition']['atoms']:
                if not atom['active_actions']:
                    atom.update(family_mask_conservative=mask, family_mask_inclusive=mask)
            self.check_rows(spec, [[Fraction(index % 4 + 1) for index in range(26)]])

    def test_peak_kinds_summary_full_oracle_and_canonical_bytes(self):
        for kind in ('symbolic', 'accepted', 'hypothesis', 'unavailable'):
            spec = entropy.specification(clocks=2, scenarios=7)
            if kind != 'symbolic':
                spec['peak'].update(kind=kind, interval=['2.3', '3.8'] if kind != 'unavailable' else None,
                    parameter=None, evidence='public-peak' if kind != 'unavailable' else None,
                    domain=None if kind == 'unavailable' else spec['peak']['domain'],
                    missing_input='public-unknown' if kind == 'unavailable' else None)
            model = api.DesignSpace(spec, admission_only=True)
            request = entropy.request(model, 2)
            reference = oracle.evaluate(model, dict(request, version=schema.VERSION))
            self.assertEqual(model.evaluate(dict(request, version=schema.VERSION)), reference)
            expected = summary_reference(reference)
            certificate = model.summary_capacity(request, limits())
            result = model.evaluate(request, capacity=certificate)
            self.assertEqual(result, expected)
            self.assertIsNone(result['model']['source_nodes'][0]['source_cost_ticks'])
            self.assertIsNone(result['unavailable']['exclusive_causal_owner'])
            for block in (1, 7, 129):
                encoded, receipt = sink_result(model, request, certificate, block)
                self.assertEqual(encoded, formulas.encoded(expected))

    def test_bounds_nonreduced_values_and_ordered_ties(self):
        rows = [dict(id=str(index), value=dict(numerator=numerator, denominator=denominator))
            for index, (numerator, denominator) in enumerate(((2, 6), (7, 5), (1, 3), (14, 10), (-9, 2)))]
        values = [full.number(row['value']) for row in rows]
        expected = dict(interval=full.hull(values), lower_scenarios=[rows[index]['id'] for index, value in enumerate(values) if value == min(values)],
            upper_scenarios=[rows[index]['id'] for index, value in enumerate(values) if value == max(values)])
        self.assertEqual(full._bounds(rows, 'value'), expected)

    def test_shared_local_mutation_rejected_and_next_evaluation_clean(self):
        spec = entropy.specification(scenarios=7)
        model = api.DesignSpace(spec, admission_only=True)
        request = entropy.request(model, 2)
        certificate = model.summary_capacity(request, limits())
        original = model.evaluate(request, capacity=certificate)
        changed = deepcopy(original)
        changed['grid'][-1]['scenarios'][-1]['clocks'][0]['moe_ticks'] = full.rational(123456)
        with self.assertRaises(wire.Rejected):
            full.validate_result(rehash(changed))
        self.assertEqual(model.evaluate(request, capacity=certificate), original)

    def test_s1_boundary_not_expanded(self):
        model = api.DesignSpace(fixtures.model(), admission_only=True)
        for version in (schema.VERSION, schema.SUMMARY_VERSION):
            request = dict(fixtures.request(model), version=version)
            result = model.evaluate(request, capacity=model.summary_capacity(request, limits()) if version == schema.SUMMARY_VERSION else None)
            result['joint_minus_oat'] = []
            full.validate_result(rehash(result))

    def test_schema_reuse_preserves_different_constraints(self):
        positive = wire._object(dict(value=dict(type='integer', minimum=1)))
        larger = wire._object(dict(value=dict(type='integer', minimum=4)))
        shape = wire._object(dict(first=positive, again=deepcopy(positive), last=larger))
        shared = dict(value=2)
        value = dict(first=shared, again=shared, last=shared)
        failures = []
        for check in (wire.compile_validator(shape, memo=set()), lambda item: wire.validate(item, shape)):
            with self.assertRaises(wire.Rejected) as caught:
                check(value)
            failures.append(caught.exception.decision)
        self.assertEqual(*failures)
