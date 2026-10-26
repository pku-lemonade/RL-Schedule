from copy import deepcopy
from decimal import Decimal, localcontext
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
LOCATION = ROOT / 'simulator_detailed/moe_model/counterfactual.py'
SPEC = importlib.util.spec_from_file_location('public_counterfactual', LOCATION)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def fixture():
    domain = dict(timebase='TSim_ns', convention='synthetic-useful-flops', participants=['rank'],
        engine_scope='synthetic-compute', rate_unit='useful_flops/TSim_ns')
    def node(name, duration, resource):
        return dict(id=name, kind='point', interval=[str(duration)] * 2, dependencies=[],
            owner=name, resources=[resource], evidence='synthetic-source')
    nodes = [node('transfer', 10, 'port'), node('compute', 6, 'engine'),
        dict(id='join', kind='structural', interval=['0', '0'], dependencies=['transfer', 'compute'])]
    parameters = {name: dict(value=value, domain=['1', '100'], unit=unit, resource=resource, evidence='synthetic-binding')
        for name, value, unit, resource in [('bandwidth', '10', 'bytes/TSim_ns', 'port'),
            ('throughput', '8', 'useful_flops/TSim_ns', 'engine')]}
    bindings = {name: dict(evidence='synthetic-binding', workload_identity='fixed-fixture', timebase='TSim_ns',
        law=dict(fixed='0', terms=[dict(parameter=parameter, kind='inverse_rate', work=work)]))
        for name, parameter, work in [('transfer', 'bandwidth', '100'), ('compute', 'throughput', '48')]}
    return dict(scope='layer', workload_identity='fixed-fixture', timebase='TSim_ns', evidence_identity='fixture-only',
        nodes=nodes, resource_orders={}, parameters=parameters, bindings=bindings, admitted_bindings=['synthetic-binding'],
        useful_flops=200, peak_domain=domain)


def symbolic(specification):
    return dict(specification['peak_domain'], kind='symbolic', parameter='applicable_peak')


def profile():
    return dict(workload_identity='profile-fixture', evidence_identity='source-profile', timebase='TSim_ns',
        post_moe_terminals=[], useful_flops=200, peak_domain=fixture()['peak_domain'],
        ranks=[dict(rank='rank', full=['0', '12'], attention=['1', '8'], moe=['6', '12'])])


class CounterfactualTests(unittest.TestCase):
    def setUp(self):
        self.spec = fixture()
        self.model = api.CounterfactualModel(self.spec)

    def evaluate(self, **options):
        return self.model.evaluate('fixed-fixture', **options)

    def test_overlap_not_serial_addition(self):
        self.assertEqual(self.evaluate()['baseline']['operation_interval'], ['10', '10'])

    def test_identity_rejects_workload_and_mutation(self):
        with self.assertRaises(api.ContractError):
            self.model.evaluate('different')
        self.model.specification['nodes'][0]['interval'][0] = '9'
        with self.assertRaises(api.ContractError):
            self.evaluate()

    def test_constructor_copies_input(self):
        self.spec['nodes'][0]['interval'] = ['1', '1']
        self.assertEqual(self.evaluate()['baseline']['operation_interval'], ['10', '10'])

    def test_locality_and_bottleneck_migration(self):
        result = self.evaluate(changes={'bandwidth': '20'})
        self.assertEqual(result['counterfactual']['operation_interval'], ['6', '6'])
        self.assertEqual(result['changed_services'], ['transfer'])
        self.assertEqual(result['baseline']['services']['compute'], result['counterfactual']['services']['compute'])
        self.assertEqual(result['baseline']['bottlenecks']['definite'], ['transfer'])
        self.assertEqual(result['counterfactual']['bottlenecks']['definite'], ['compute'])
        self.assertEqual(result['delta_interval'], ['-4', '-4'])

    def test_monotonicity(self):
        results = [Decimal(self.evaluate(changes={'bandwidth': value})['counterfactual']['operation_interval'][1])
            for value in ('5', '10', '20', '40')]
        self.assertEqual(results, sorted(results, reverse=True))

    def test_interval_bounds_enclose_all_corner_responses(self):
        bounded = self.evaluate(hypotheses=[dict(id='extent', reason='synthetic bounded response', services=['transfer'],
            kind='multiplier', interval=['0.5', '2'])])
        for factor in ('0.5', '1', '1.5', '2'):
            sample = self.evaluate(hypotheses=[dict(id='sample', reason='same synthetic law', services=['transfer'],
                kind='multiplier', interval=[factor, factor])])
            for field, bounds in [('operation_interval', bounded['counterfactual']['operation_interval'])]:
                self.assertLessEqual(Decimal(bounds[0]), Decimal(sample['counterfactual'][field][0]))
                self.assertGreaterEqual(Decimal(bounds[1]), Decimal(sample['counterfactual'][field][1]))
            self.assertLessEqual(Decimal(bounded['delta_interval'][0]), Decimal(sample['delta_interval'][0]))
            self.assertGreaterEqual(Decimal(bounded['delta_interval'][1]), Decimal(sample['delta_interval'][1]))

    def test_explicit_sharing_changes_completion_not_service_work(self):
        source = fixture()
        source['nodes'][1]['resources'].append('port')
        source['resource_orders'] = {'port': ['transfer', 'compute']}
        shared = api.CounterfactualModel(source).evaluate('fixed-fixture')
        independent = self.evaluate()
        self.assertEqual(shared['baseline']['services'], independent['baseline']['services'])
        self.assertGreater(Decimal(shared['baseline']['operation_interval'][0]), Decimal(independent['baseline']['operation_interval'][0]))

    def test_resource_order_is_explicit(self):
        self.spec['nodes'][1]['resources'].append('port')
        self.spec['resource_orders'] = {'port': ['transfer', 'compute']}
        result = api.CounterfactualModel(self.spec).evaluate('fixed-fixture', changes={'bandwidth': '20'})
        self.assertEqual(result['baseline']['operation_interval'], ['16', '16'])
        self.assertEqual(result['counterfactual']['operation_interval'], ['11', '11'])

    def test_duplicate_owners_and_dependency_cycles_fail(self):
        for variant in ('owner', 'cycle'):
            changed = deepcopy(self.spec)
            if variant == 'owner':
                changed['nodes'][1]['owner'] = 'transfer'
            else:
                changed['nodes'][0]['dependencies'] = ['join']
            with self.assertRaises(api.ContractError):
                api.CounterfactualModel(changed)

    def test_binding_resource_and_evidence_fail_closed(self):
        for key, value in [('resource', 'other'), ('evidence', 'unaccepted'), ('unit', '')]:
            changed = deepcopy(self.spec)
            changed['parameters']['bandwidth'][key] = value
            with self.assertRaises(api.ContractError):
                api.CounterfactualModel(changed)

    def test_no_refit_or_unsupported_law(self):
        for kind, work in [('inverse_rate', '101'), ('guessed', '100')]:
            changed = deepcopy(self.spec)
            changed['bindings']['transfer']['law']['terms'][0].update(kind=kind, work=work)
            with self.assertRaises(api.ContractError):
                api.CounterfactualModel(changed)

    def test_unmapped_parameter_is_not_synthetic(self):
        with self.assertRaises(api.ContractError):
            self.evaluate(changes={'physical_link': '2'})
        result = self.evaluate(hypotheses=[dict(id='sharing', reason='unknown physical sharing', services=['transfer'],
            kind='multiplier', interval=['0.5', '2'])])
        self.assertEqual(result['counterfactual']['operation_interval'], ['6', '20'])
        self.assertEqual(result['classification'], 'hypothesis_dependent')

    def test_no_double_intervention(self):
        with self.assertRaises(api.ContractError):
            self.evaluate(changes={'bandwidth': '20'}, hypotheses=[dict(id='duplicate', reason='bad',
                services=['transfer'], kind='multiplier', interval=['1', '2'])])

    def test_interval_readiness_and_possible_membership(self):
        self.spec['nodes'].insert(0, dict(id='ready', kind='conditional', interval=['0', '8'], dependencies=[],
            owner='ready', resources=[], evidence='bounded-readiness'))
        self.spec['nodes'][2]['dependencies'] = ['ready']
        result = api.CounterfactualModel(self.spec).evaluate('fixed-fixture')
        self.assertEqual(result['baseline']['operation_interval'], ['10', '14'])
        self.assertEqual(result['baseline']['bottlenecks']['definite'], [])
        self.assertIn('compute', result['baseline']['bottlenecks']['possible'])
        self.assertEqual(result['delta_interval'], ['0', '0'])

    def test_correlated_anchor_not_additive_intercept(self):
        self.spec['nodes'].insert(0, dict(id='ready', kind='conditional', interval=['0', '9'], dependencies=[],
            owner='ready', resources=[], evidence='bounded-readiness'))
        self.spec['nodes'][2]['dependencies'] = ['ready']
        self.spec['correlation'] = dict(residual_order=['ready'], base_expression={'0': '10', '1': '6'},
            anchor_interval=['11', '13'], deltas=[['0', '2']])
        result = api.CounterfactualModel(self.spec).evaluate('fixed-fixture')
        self.assertEqual(result['baseline']['operation_interval'], ['11', '15'])
        self.assertEqual(result['delta_interval'], ['0', '0'])

    def test_correlated_completion_caps_must_be_safe(self):
        self.spec['nodes'].append(dict(id='ready', kind='conditional', interval=['0', '1'], dependencies=[],
            owner='ready', resources=[], evidence='bounded-readiness'))
        self.spec['correlation'] = dict(residual_order=['ready'], base_expression={'0': '10', '1': '0'},
            anchor_interval=['11', '13'], deltas=[['0', '2']])
        with self.assertRaises(api.ContractError):
            api.CounterfactualModel(self.spec)

    def test_correlated_prefix_mask_survives_later_residual(self):
        source = fixture()
        source['nodes'].append(dict(id='ready', kind='conditional', interval=['0', '20'],
            dependencies=['join'], owner='ready', resources=[], evidence='prefix-anchor'))
        source['correlation'] = dict(residual_order=['ready'], base_expression={'0': '10', '1': '10'},
            anchor_interval=['11', '13'], deltas=[['0', '2']])
        result = api.CounterfactualModel(source).evaluate('fixed-fixture')
        self.assertEqual(result['baseline']['operation_interval'], ['11', '15'])

    def test_derived_state_mutation_fails_closed(self):
        model = api.CounterfactualModel(self.spec)
        model.costs['transfer'] = (Decimal(1), Decimal(1))
        with self.assertRaises(api.ContractError):
            model.evaluate('fixed-fixture')

    def test_symbolic_mfu_and_peak_change(self):
        peak = dict(self.spec['peak_domain'], kind='accepted', interval=['20', '20'], evidence='fixture-peak')
        result = self.evaluate(peak=peak, counterfactual_peak=dict(peak, interval=['40', '40']))
        self.assertEqual(result['baseline']['mfu']['numeric_interval'], ['1', '1'])
        self.assertEqual(result['counterfactual']['mfu']['numeric_interval'], ['0.5', '0.5'])
        self.assertEqual(result['delta_interval'], ['0', '0'])
        self.assertEqual(self.evaluate(peak=symbolic(self.spec))['baseline']['mfu']['coefficients'], ['20', '20'])

    def test_peak_unit_participants_convention_mismatch(self):
        for key, value in [('timebase', 'physical_cycles'), ('participants', ['other']), ('convention', 'issued'),
                ('engine_scope', 'unrelated'), ('rate_unit', 'flops/physical_cycle')]:
            with self.assertRaises(api.ContractError):
                self.evaluate(peak=dict(symbolic(self.spec), **{key: value}))

    def test_numeric_mfu_interval_no_clipping(self):
        result = api.mfu(100, ['10', '20'], dict(self.spec['peak_domain'], kind='hypothesis', evidence='explicit',
            interval=['1', '2']), self.spec['peak_domain'])
        self.assertEqual(result['numeric_interval'], ['2.5', '10'])

    def test_explicit_pipeline_max_plus_and_flops(self):
        pipeline = dict(timebase='TSim_ns', description='explicit synthetic pipeline', model_depth=2,
            nodes=[dict(id='layer-a', kind='layer', dependencies=[]), dict(id='layer-b', kind='layer', dependencies=[])])
        result = self.evaluate(pipeline=pipeline, peak=symbolic(self.spec))
        self.assertEqual(result['baseline']['decode_interval'], ['10', '10'])
        self.assertEqual(result['baseline']['mfu']['coefficients'], ['40', '40'])
        pipeline['nodes'][1]['dependencies'] = ['layer-a']
        result = self.evaluate(pipeline=pipeline)
        self.assertEqual(result['baseline']['decode_interval'], ['20', '20'])
        pipeline['model_depth'] = 3
        with self.assertRaises(api.ContractError):
            self.evaluate(pipeline=pipeline)

    def test_no_silent_depth_or_decode(self):
        self.assertIsNone(self.evaluate()['baseline']['decode_interval'])

    def test_transition_threshold_is_analytic(self):
        result = self.evaluate(changes={'bandwidth': '20'})
        self.assertEqual(result['transition_thresholds']['values'], [str(Decimal(100) / 6)])

    def test_linear_latency(self):
        self.spec['bindings']['transfer']['law']['terms'][0].update(kind='linear_latency', work='1')
        result = api.CounterfactualModel(self.spec).evaluate('fixed-fixture', changes={'bandwidth': '20'})
        self.assertEqual(result['counterfactual']['operation_interval'], ['20', '20'])

    def test_sweeps_are_independent_and_selected(self):
        cases = [dict(id='one', changes={'bandwidth': '20'}), dict(id='combined', combined=True,
            changes={'bandwidth': '20', 'throughput': '16'}), dict(id='unknown', changes={'fifo_alias': '3'})]
        results = self.model.sweep('fixed-fixture', cases)
        self.assertEqual([row['status'] for row in results], ['evaluated', 'evaluated', 'not_evaluated'])
        self.assertEqual(results[1]['result']['counterfactual']['operation_interval'], ['5', '5'])
        self.assertEqual(self.evaluate()['baseline']['operation_interval'], ['10', '10'])
        with self.assertRaises(api.ContractError):
            self.model.sweep('fixed-fixture', [dict(id='bad', changes=cases[1]['changes'])])

    def test_byte_stable_replay_and_decimal_context(self):
        first = api.encoded(self.evaluate(changes={'bandwidth': '30'}))
        with localcontext() as context:
            context.prec = 6
            second = api.encoded(self.evaluate(changes={'bandwidth': '30'}))
        self.assertEqual(first, second)

    def test_invalid_intervals(self):
        for value in (['nan', '3'], ['4', '3'], ['-1', '2'], [1.1, 2]):
            with self.assertRaises(api.ContractError):
                api.interval(value)

    def test_profile_baseline_union_and_mfu(self):
        source = profile()
        result = api.profile_counterfactual(source, 'profile-fixture', peak=symbolic(self.spec))
        self.assertEqual(result['ranks'][0]['baseline']['overlap_ns'], '2')
        self.assertEqual(result['ranks'][0]['baseline']['residual_ns'], '1')
        self.assertEqual(result['baseline']['layer_interval'], ['12', '12'])
        self.assertEqual(result['new_point_owners'], 0)

    def test_profile_counterfactual_is_explicit_hypothesis(self):
        response = dict(status='hypothesis', workload_identity='profile-fixture', reason='explicit response bound', interval=['-2', '3'])
        result = api.profile_counterfactual(profile(), 'profile-fixture', response)
        self.assertEqual(result['counterfactual']['layer_interval'], ['10', '15'])
        self.assertEqual(result['classification'], 'hypothesis_dependent')
        with self.assertRaises(api.ContractError):
            api.profile_counterfactual(profile(), 'other', response)
        with self.assertRaises(api.ContractError):
            api.profile_counterfactual(profile(), 'profile-fixture', dict(response, status='measured'))

    def test_profile_missing_terminal_rejects(self):
        source = profile()
        source['ranks'][0]['full'][1] = '13'
        with self.assertRaises(api.ContractError):
            api.profile_counterfactual(source, 'profile-fixture')

    def test_profile_missing_participant_rejects(self):
        source = profile()
        source['peak_domain']['participants'].append('missing')
        with self.assertRaises(api.ContractError):
            api.profile_counterfactual(source, 'profile-fixture')

    def test_profile_terminal_transition_and_peak(self):
        source = profile()
        response = dict(status='hypothesis', workload_identity='profile-fixture', reason='synthetic', interval=['-5', '-3'])
        peak = dict(source['peak_domain'], kind='hypothesis', interval=['20', '20'], evidence='synthetic')
        result = api.profile_counterfactual(source, 'profile-fixture', response, peak=peak,
            counterfactual_peak=dict(peak, interval=['40', '40']))
        self.assertEqual(result['transition_thresholds']['moe_completion_offset'], '-4')
        self.assertEqual(result['bottlenecks']['definite'], [])
        self.assertEqual(result['bottlenecks']['possible'], ['attention_terminal', 'moe_terminal'])
        self.assertEqual(result['counterfactual']['layer_interval'], ['8', '9'])


if __name__ == '__main__':
    unittest.main()
