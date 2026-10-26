"""Synthetic public CPU proofs; no runtime or private evidence access."""
from copy import deepcopy
from decimal import Decimal, localcontext
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULES = Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'
sys.path.insert(0, str(MODULES))
import design_space as api
import design_space_schema as wire
import design_space_fixtures as fixtures


def numbers(values):
    return list(map(Decimal, values))


def point(result, rates):
    return next(row for row in result['grid'] if numbers(row['rates'].values()) == list(map(Decimal, rates)))


def containers(value):
    found = []
    if type(value) in (dict, list):
        found.append(value)
        for child in list(value.values()) if type(value) is dict else value:
            found.extend(containers(child))
    return found


class DesignSpaceTests(unittest.TestCase):
    def setUp(self):
        self.spec = fixtures.joint_model()
        self.model = api.DesignSpace(self.spec)
        self.request = fixtures.request(self.model)

    def rejected(self, callback, code):
        with self.assertRaises(api.Rejected) as caught:
            callback()
        self.assertEqual(caught.exception.decision['code'], code)
        self.assertTrue(caught.exception.decision['path'])
        self.assertTrue(caught.exception.decision['reason'])

    def test_endpoint_arithmetic_fixed_baseline_and_peak(self):
        result = self.model.evaluate(self.request)
        self.assertEqual(numbers(point(result, ['1', '1'])['latency_interval']), [30, 36])
        fast = point(result, ['2', '2'])
        self.assertEqual(numbers(fast['latency_interval']), [15, 21])
        self.assertEqual(numbers(fast['mfu']['numeric_interval']), [Decimal(120) / 21 / 6, Decimal(120) / 15 / 6])
        self.assertEqual(result['evidence_backed_baseline']['latency_interval'], ['30', '30'])
        self.assertTrue(result['frozen_peak_unchanged'])
        self.assertFalse(result['observed_intervals_added_as_cost'])
        self.assertTrue(fast['improvement']['guaranteed_strict'])

    def test_monotonicity_all_coordinates(self):
        rows = self.model.evaluate(self.request)['grid']
        for left, right in itertools.product(rows, rows):
            if all(Decimal(left['rates'][name]) >= Decimal(right['rates'][name]) for name in left['rates']):
                self.assertTrue(all(lower <= upper for lower, upper in zip(numbers(left['latency_interval']), numbers(right['latency_interval']))))

    def test_widening_shared_box_does_not_tighten(self):
        original = self.model.evaluate(self.request)
        self.spec['nodes'][0]['residual']['interval'][1] = '9'
        model = api.DesignSpace(self.spec)
        widened = model.evaluate(fixtures.request(model))
        for before, after in zip(original['grid'], widened['grid']):
            self.assertLessEqual(Decimal(after['latency_interval'][0]), Decimal(before['latency_interval'][0]))
            self.assertGreaterEqual(Decimal(after['latency_interval'][1]), Decimal(before['latency_interval'][1]))

    def test_zero_exposure_is_opportunity_not_guaranteed(self):
        for axis in self.request['axes']:
            axis['exposure']['interval'] = ['0', '1']
        row = point(self.model.evaluate(self.request), ['2', '2'])
        self.assertFalse(row['improvement']['guaranteed_strict'])
        self.assertTrue(row['improvement']['guaranteed_nonregression'])
        self.assertTrue(row['improvement']['opportunity_with_fixed_shared_witness'])

    def test_scenario_reference_and_robust_quantifier(self):
        model = api.DesignSpace(fixtures.scenario_model())
        result = model.evaluate(fixtures.request(model))
        baseline = point(result, ['1', '1'])
        self.assertEqual([numbers(row['latency_interval']) for row in baseline['scenarios']], [[30, 36], [30, 30]])
        for comparison in result['comparisons']:
            left = next(row for row in result['grid'] if row['id'] == comparison['left'])
            right = next(row for row in result['grid'] if row['id'] == comparison['right'])
            expected = all(Decimal(first['latency_interval'][1]) < Decimal(second['latency_interval'][0])
                for first, second in zip(left['scenarios'], right['scenarios']))
            self.assertEqual(comparison['guaranteed_strict'], expected)

    def test_no_double_count_event_and_axis(self):
        self.spec['event_partition'].append(deepcopy(self.spec['event_partition'][0]))
        self.rejected(lambda: api.DesignSpace(self.spec), 'DUPLICATE_ID')
        self.spec = fixtures.joint_model()
        self.spec['families'][1]['nodes'] = ['stage-a']
        self.rejected(lambda: api.DesignSpace(self.spec), 'NO_DOUBLE_COUNT')
        self.request['axes'][1]['family'] = self.request['axes'][0]['family']
        self.rejected(lambda: self.model.evaluate(self.request), 'DUPLICATE_ID')

    def test_context_is_not_a_cost_owner(self):
        self.spec['event_partition'][0]['role'] = 'context_only'
        self.rejected(lambda: api.DesignSpace(self.spec), 'NO_DOUBLE_COUNT')
        self.spec['event_partition'][0]['node'] = None
        model = api.DesignSpace(self.spec)
        self.assertEqual(numbers(point(model.evaluate(fixtures.request(model)), ['1', '1'])['latency_interval']), [30, 36])

    def test_reference_mismatch_cannot_fit(self):
        self.spec['nodes'][0]['service'] = dict(interval=['11', '12'], reference='11')
        self.rejected(lambda: api.DesignSpace(self.spec), 'BASELINE_REPRODUCTION')

    def test_source_boundary_and_event_ownership(self):
        self.spec['event_inventory'][0]['interval'] = ['0', '31']
        self.rejected(lambda: api.DesignSpace(self.spec), 'SOURCE_BOUNDARY')
        self.spec = fixtures.joint_model()
        self.spec['event_partition'][0]['node'] = 'stage-b'
        self.rejected(lambda: api.DesignSpace(self.spec), 'EVENT_OWNER')

    def test_sharing_coverage_cycle_and_cross_clock(self):
        self.spec['scenarios'][0]['route_contention'][0]['node_order'].reverse()
        self.rejected(lambda: api.DesignSpace(self.spec), 'CYCLE')
        self.spec = fixtures.joint_model()
        self.spec['scenarios'][0]['assignments'].pop()
        self.rejected(lambda: api.DesignSpace(self.spec), 'RESOURCE_COVERAGE')
        self.spec = fixtures.joint_model()
        self.spec['nodes'][1]['clock'] = 'different-clock'
        self.rejected(lambda: api.DesignSpace(self.spec), 'CLOCK_COVERAGE')

    def test_exact_mode_specific_fields(self):
        del self.request['mode']
        self.rejected(lambda: self.model.evaluate(self.request), 'MODE')
        self.request = fixtures.request(self.model)
        self.request['uncertainty']['readiness'] = dict(mode='shared', radius='0', path_count=0)
        self.rejected(lambda: self.model.evaluate(self.request), 'SCHEMA_FIELDS')
        self.spec['terminals'] = []
        self.rejected(lambda: api.DesignSpace(self.spec), 'SCHEMA_FIELDS')

    def test_strict_decimal_and_domain_rejection(self):
        for value in (' 1', '1_0', 'NaN', 1.0, True):
            query = deepcopy(self.request)
            query['axes'][0]['grid'][1] = value
            self.rejected(lambda: self.model.evaluate(query), 'SCHEMA_STRING')
        self.request['axes'][0]['grid'] = ['0.5', '2']
        self.rejected(lambda: self.model.evaluate(self.request), 'GRID_DOMAIN')
        self.request = fixtures.request(self.model)
        self.request['axes'][0]['exposure']['interval'] = ['0', '2']
        self.rejected(lambda: self.model.evaluate(self.request), 'INTERVAL')

    def test_identity_budget_and_unsupported_claim(self):
        self.request['input_identity'] = 'different-workload'
        self.rejected(lambda: self.model.evaluate(self.request), 'IDENTITY')
        self.request = fixtures.request(self.model)
        self.request['grid_budget'] = 8
        self.rejected(lambda: self.model.evaluate(self.request), 'GRID_BUDGET')
        self.request = fixtures.request(self.model)
        self.request['claim'] = 'physical_truth'
        self.rejected(lambda: self.model.evaluate(self.request), 'SCHEMA_ENUM')

    def test_unsupported_family_and_explicit_unavailable(self):
        self.request['axes'][0]['family'] = 'unregistered-capacity'
        self.rejected(lambda: self.model.evaluate(self.request), 'UNSUPPORTED_FAMILY')
        unavailable = self.model.catalog()['unavailable']
        self.assertEqual(unavailable['physical_capacity']['status'], 'unavailable')
        self.spec['peak'] = dict(kind='unavailable', domain=None, parameter=None, interval=None, evidence=None, missing_input='applicable-peak-authority')
        model = api.DesignSpace(self.spec)
        self.assertEqual(model.evaluate(fixtures.request(model))['grid'][0]['mfu']['minimal_missing_input'], 'applicable-peak-authority')

    def test_peak_domain_and_evidence_kind(self):
        self.spec['peak']['domain']['timebase'] = 'other-unit'
        self.rejected(lambda: api.DesignSpace(self.spec), 'PEAK_DOMAIN')
        self.spec = fixtures.joint_model()
        self.spec['peak']['parameter'] = 'injected'
        self.rejected(lambda: api.DesignSpace(self.spec), 'PEAK_FIELDS')

    def test_all_returned_nested_containers_are_isolated(self):
        baseline = self.model.evaluate(self.request)
        identity = self.model.identity
        public = [self.model.catalog(), self.model.specification(), self.model.schema(), self.model.template(),
            self.model.evaluate(self.request), wire.model_schema('hypothetical_joint'), wire.request_schema('hypothetical_joint')]
        nested = [container for value in public for container in containers(value)]
        self.assertGreater(len(nested), 100)
        for container in reversed(nested):
            if type(container) is dict:
                container.clear()
                container.update(injected_numeric_mfu=['999', '999'], authority='caller_evidence')
            else:
                container[:] = ['injected']
        self.spec.clear()
        self.assertEqual(self.model.identity, identity)
        self.assertEqual(self.model.evaluate(self.request), baseline)
        self.assertEqual(self.model.catalog()['baseline'], baseline['evidence_backed_baseline'])

    def test_request_and_numeric_mfu_alias_isolation(self):
        query = deepcopy(self.request)
        result = self.model.evaluate(query)
        result['evidence_backed_baseline']['mfu']['numeric_interval'][:] = ['999', '999']
        result['identity_hooks']['workload'] = 'injected'
        query['axes'].clear()
        self.assertNotEqual(self.model.catalog()['baseline']['mfu']['numeric_interval'], ['999', '999'])
        self.assertEqual(self.model.evaluate(self.request)['identity_hooks']['workload'], 'synthetic-workload')

    def test_decimal_context_is_fixed(self):
        normal = self.model.evaluate(self.request)
        with localcontext() as context:
            context.prec = 7
            self.assertEqual(self.model.evaluate(self.request), normal)

    def test_surfaces_and_conservative_pareto(self):
        result = self.model.evaluate(self.request)
        self.assertIn('symbolic max-plus', result['break_even_surfaces']['representation'])
        self.assertEqual(result['break_even_surfaces']['scenario_dependencies']['synthetic-serial']['stage-b'], ['stage-a'])
        self.assertFalse(set(result['pareto']['conservative_outer_set']) & set(result['pareto']['guaranteed_dominated']))
        self.assertEqual(set(result['pareto']['conservative_outer_set']) | set(result['pareto']['guaranteed_dominated']), {row['id'] for row in result['grid']})

    def test_duplicate_json_rejected(self):
        self.rejected(lambda: wire.strict_json('{"mode":"one","mode":"two"}'), 'DUPLICATE_JSON_KEY')
        self.rejected(lambda: wire.strict_json('{"number":NaN}'), 'NONFINITE_JSON')

    def test_cli_determinism_and_api_equality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.json').write_text(json.dumps(self.model.specification()))
            (root / 'request.json').write_text(json.dumps(self.request))
            command = [sys.executable, '-B', str(MODULES / 'design_space.py'), 'evaluate', '--model', str(root / 'model.json'), '--request', str(root / 'request.json')]
            first = subprocess.check_output(command)
            second = subprocess.check_output(command)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first), self.model.evaluate(self.request))
            invalid = deepcopy(self.request)
            del invalid['mode']
            (root / 'request.json').write_text(json.dumps(invalid))
            failed = subprocess.run(command, capture_output=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stdout)['code'], 'MODE')


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.model = api.DesignSpace(fixtures.portfolio_model())
        self.request = fixtures.request(self.model)

    def test_profile_endpoint_arithmetic_and_nonaddition(self):
        result = self.model.evaluate(self.request)
        fastest = point(result, ['2', '2'])
        self.assertEqual(numbers(fastest['conditional_views']['transfer']['latency_interval']), [24, 24])
        self.assertEqual(numbers(fastest['conditional_views']['compute']['latency_interval']), [27, 27])
        self.assertIsNone(fastest['joint_architecture_latency'])
        self.assertIsNone(result['joint_pareto'])
        self.assertEqual(result['unavailable']['joint_latency']['status'], 'unavailable')
        for facet in result['conditional_facets'].values():
            baseline = next(row for row in facet['results'] if Decimal(row['rate']) == 1)
            self.assertEqual(baseline['latency_interval'], result['evidence_backed_baseline']['latency_interval'])

    def test_readiness_outer_not_feasible_witness(self):
        self.request['uncertainty']['readiness'] = dict(mode='bounded', radius='1', path_count=2)
        result = self.model.evaluate(self.request)
        row = point(result, ['2', '2'])['conditional_views']['transfer']
        self.assertEqual(numbers(row['latency_interval']), [22, 26])
        self.assertFalse(row['improvement']['opportunity_with_box_endpoint_witness'])
        self.assertTrue(row['improvement']['possible_outer_not_witness'])
        self.assertEqual(Decimal(row['break_even']['sufficient_radius_upper_exclusive']), 3)

    def test_portfolio_nested_returns_and_mfu_evidence_are_isolated(self):
        expected = self.model.evaluate(self.request)
        identity = self.model.identity
        outputs = [self.model.catalog(), self.model.specification(), self.model.template(), self.model.schema(), expected]
        frozen = deepcopy(expected)
        for container in reversed([nested for output in outputs for nested in containers(output)]):
            if type(container) is dict:
                container.clear()
                container.update(kind='accepted', numeric_interval=['999', '999'])
            else:
                container[:] = ['injected']
        self.assertEqual(self.model.evaluate(self.request), frozen)
        self.assertEqual(self.model.identity, identity)

    def test_negative_duration_rejected(self):
        self.request['uncertainty']['readiness'] = dict(mode='bounded', radius='100', path_count=1)
        with self.assertRaisesRegex(api.Rejected, 'NEGATIVE_DURATION'):
            self.model.evaluate(self.request)

    def test_no_default_readiness_or_physical_fields(self):
        del self.request['uncertainty']['readiness']['radius']
        with self.assertRaisesRegex(api.Rejected, 'SCHEMA_FIELDS'):
            self.model.evaluate(self.request)
        request = fixtures.request(self.model)
        request['per_configuration_costs'] = {}
        with self.assertRaisesRegex(api.Rejected, 'SCHEMA_FIELDS'):
            self.model.evaluate(request)


if __name__ == '__main__':
    unittest.main()
