"""CPU-only admission, accounting and deterministic public-interface proofs."""
from copy import deepcopy
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULES = Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'
sys.path.insert(0, str(MODULES))
import design_space as api
import design_space_schema as wire
import causal_timing_fixtures as fixtures


def point(result, alpha='1', beta='1'):
    return next(row for row in result['grid'] if row['rates'] == dict(alpha=alpha, beta=beta))


def objects(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from objects(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from objects(child, path + (index,))


class CausalTimingTests(unittest.TestCase):
    def setUp(self):
        self.spec = fixtures.model()
        self.model = api.DesignSpace(self.spec)
        self.request = fixtures.request(self.model)

    def reject(self, code=None, spec=None):
        with self.assertRaises(wire.Rejected) as caught:
            api.DesignSpace(self.spec if spec is None else spec)
        if code is not None:
            self.assertEqual(caught.exception.decision['code'], code)
        self.assertTrue(caught.exception.decision['path'])

    def test_recursive_model_and_request_schema_closure(self):
        for original, admit in ((self.spec, api.DesignSpace), (self.request, self.model.evaluate)):
            for path, source in objects(original):
                for operation in ('extra', 'missing'):
                    if operation == 'missing' and not source:
                        continue
                    changed = deepcopy(original)
                    target = changed
                    for key in path:
                        target = target[key]
                    if operation == 'extra':
                        target['unexpected'] = None
                    else:
                        del target[next(iter(source))]
                    with self.subTest(path=path, operation=operation), self.assertRaises(wire.Rejected):
                        admit(changed)

    def test_json_schema_matches_positive_contracts(self):
        import jsonschema
        for value, schema in ((self.spec, wire.model_schema('causal_timing')),
                (self.request, wire.request_schema('causal_timing'))):
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(value, schema)
            wire.validate(value, schema)

    def test_explicit_unknowns_and_candidates_survive(self):
        result = self.model.evaluate(self.request)
        self.assertEqual(result['causal_join'], self.spec['join'])
        self.assertIsNone(result['causal_latency_ticks'])
        self.assertIsNone(result['evidence_backed_baseline']['causal_latency_ticks'])
        self.assertFalse(result['unresolved_identities_promoted'])
        self.assertEqual(result['causal_order']['exact_edges'], [['left', 'right']])
        self.assertEqual(result['causal_order']['exact_order'], ['left', 'right'])
        self.assertEqual(result['unavailable']['numeric_mfu']['status'], 'unavailable')

    def test_no_unresolved_binding_promotion(self):
        self.spec['join']['bindings'][2]['identity'] = 'possible-action'
        self.reject('UNRESOLVED_IDENTITY')

    def test_unresolved_requires_missing_input(self):
        self.spec['join']['bindings'][2]['unknown'] = None
        self.reject('UNRESOLVED_IDENTITY')

    def test_exact_requires_identity_and_no_unknown(self):
        for field, value in (('identity', None), ('unknown', 'still-missing')):
            changed = deepcopy(self.spec)
            changed['join']['bindings'][0][field] = value
            self.reject('EXACT_IDENTITY', changed)

    def test_exact_binding_bijection(self):
        self.spec['join']['bindings'][1]['identity'] = 'left-identity'
        self.reject('IDENTITY_REUSE')

    def test_missing_and_duplicate_action_joins(self):
        self.spec['join']['bindings'].pop()
        self.reject('JOIN_COVERAGE')
        self.spec = fixtures.model()
        self.spec['join']['bindings'].append(deepcopy(self.spec['join']['bindings'][0]))
        self.reject('DUPLICATE_ID')

    def test_no_dependency_promotion_from_correlation(self):
        edge = self.spec['join']['dependencies'][1]
        edge.update(status='exact', identity='candidate-dependency', unknown=None)
        self.reject('EXACT_DEPENDENCY')
        edge['basis'] = 'happens_before'
        self.reject('UNRESOLVED_ENDPOINT')

    def test_no_unresolved_dependency_identity(self):
        self.spec['join']['dependencies'][1]['identity'] = 'guessed-dependency'
        self.reject('UNRESOLVED_IDENTITY')

    def test_reversed_same_clock_causality_rejected(self):
        self.spec['join']['dependencies'][0].update(before='right', after='left')
        self.reject('CAUSAL_ORDER')

    def test_unknown_dependency_endpoint_rejected(self):
        self.spec['join']['dependencies'][0]['before'] = 'absent'
        self.reject('DEPENDENCY_ENDPOINT')

    def test_logical_cycles_reject_without_timing_promotion(self):
        self.spec['join']['dependencies'] = [dict(id='logical-' + before, before=before, after=after,
            status='unresolved', basis='logical', identity=None, unknown='causality_missing')
            for before, after in [('left', 'right'), ('right', 'left')]]
        self.reject('CAUSAL_CYCLE')

    def test_cross_clock_edges_do_not_compare_timestamps(self):
        fixtures.add_clock(self.spec)
        self.spec['join']['dependencies'].append(dict(id='cross', before='second-right', after='left', status='exact',
            basis='happens_before', identity='cross-identity', unknown=None))
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(point(result)['latency_interval'], ['11', '11'])
        self.assertIsNone(result['causal_latency_ticks'])
        self.spec['join']['dependencies'].append(dict(id='cycle', before='right', after='second-right', status='exact',
            basis='happens_before', identity='cycle-identity', unknown=None))
        self.reject('CAUSAL_CYCLE')

    def test_same_rank_separate_clocks_never_sum_or_splice(self):
        fixtures.add_clock(self.spec)
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(point(result)['latency_interval'], ['11', '11'])
        self.assertEqual(len(point(result)['scenarios'][0]['clocks']), 2)
        self.spec['clocks'][1]['rank'] = 7
        shifted = api.DesignSpace(self.spec)
        self.assertEqual(point(shifted.evaluate(fixtures.request(shifted)))['latency_interval'], ['11', '11'])

    def test_rank_and_clock_admission(self):
        for value in (-1, True, 0.0, '0'):
            changed = deepcopy(self.spec)
            changed['clocks'][0]['rank'] = value
            self.reject('SCHEMA_INTEGER', changed)
        self.spec['clocks'].append(deepcopy(self.spec['clocks'][0]))
        self.reject('DUPLICATE_ID')
        self.spec = fixtures.model()
        self.spec['join']['actions'][0]['clock'] = 'unknown-clock'
        self.reject('ACTION_CLOCK_ORDER')

    def test_cross_clock_active_action_rejected(self):
        fixtures.add_clock(self.spec, offset=0)
        self.spec['partition']['atoms'][0]['active_actions'] = ['second-left']
        self.reject('ACTIVE_MEMBERSHIP')

    def test_family_masks_reject_noninteger_negative_and_unknown_bits(self):
        for field in ('family_mask_conservative', 'family_mask_inclusive'):
            for value in (-1, True, False, 1.0, '1', None, [], {}, 4, 1 << 23):
                changed = deepcopy(self.spec)
                changed['partition']['atoms'][0][field] = value
                with self.subTest(field=field, value=value):
                    self.reject(spec=changed)

    def test_dynamic_family_registry_not_fixed_width(self):
        self.spec['families'].append(dict(id='gamma', law='inverse_partition_response', authority='caller_hypothesis'))
        self.spec['partition']['atoms'][0].update(family_mask_conservative=4, family_mask_inclusive=4)
        model = api.DesignSpace(self.spec)
        query = fixtures.request(model)
        query['axes'] = [dict(query['axes'][0], id='gamma', family='gamma')]
        result = model.evaluate(query)
        self.assertEqual(result['grid'][1]['latency_interval'], ['10.0', '10.0'])

    def test_mask_subset_and_unobserved_residual_credit_rejected(self):
        self.spec['partition']['atoms'][0]['family_mask_conservative'] = 2
        self.reject('MASK_SUBSET')
        self.spec = fixtures.model()
        self.spec['partition']['atoms'][-1]['family_mask_inclusive'] = 1
        self.reject('RESIDUAL_MASK')

    def test_unresolved_costs_and_exclusive_owners_stay_null(self):
        for path in (('join', 'actions', 0, 'source_cost_ticks'),
                ('partition', 'atoms', 0, 'exclusive_causal_owner'), ('partition', 'causal_latency_ticks')):
            changed = deepcopy(self.spec)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = 0
            self.reject('SCHEMA_NULL', changed)

    def test_once_only_union_not_action_duration_sum(self):
        result = self.model.evaluate(self.request)
        self.assertEqual(point(result)['latency_interval'], ['11', '11'])
        self.assertEqual(point(result, '2', '2')['latency_interval'], ['6.5', '6.5'])
        clock = result['causal_order']['clocks'][0]
        self.assertEqual(clock['ownership_ticks'], dict(residual=2, exclusive_observation=5, shared_observation=4))
        for histogram in clock['histogram'].values():
            self.assertEqual(sum(row['duration_ticks'] for row in histogram), 11)
        self.assertFalse(result['observed_intervals_added_as_cost'])

    def test_explicit_shared_and_co_bottleneck_laws(self):
        rows = point(self.model.evaluate(self.request), '2', '1')['scenarios']
        by_name = {row['id']: list(map(Decimal, row['latency_interval'])) for row in rows}
        self.assertEqual(by_name['inclusive-shared_serial'], [9, 9])
        self.assertEqual(by_name['inclusive-co_bottleneck'], [10, 10])

    def test_exposure_box_and_unselected_families(self):
        self.request['axes'][0]['exposure']['interval'] = ['0', '1']
        result = self.model.evaluate(self.request)
        self.assertEqual(list(map(Decimal, point(result, '2', '1')['latency_interval'])), [9, 11])
        self.request['axes'].pop()
        result = self.model.evaluate(self.request)
        self.assertEqual(list(map(Decimal, result['grid'][1]['latency_interval'])), [9, 11])

    def test_mixed_slowdown_speedup_box_matches_independent_atom_oracle(self):
        self.request['grid_budget'] = 9
        for axis in self.request['axes']:
            axis.update(domain=['0.5', '2'], grid=['0.5', '1', '2'])
            axis['exposure']['interval'] = ['0', '1']
        result = self.model.evaluate(self.request)
        for candidate in result['grid']:
            for scenario, output in zip(self.spec['scenarios'], candidate['scenarios']):
                expected = []
                for exposures in itertools.product((Decimal(0), Decimal(1)), repeat=2):
                    rates = [Decimal(candidate['rates'][name]) for name in ('alpha', 'beta')]
                    factors = [1 - exposure + exposure / rate for exposure, rate in zip(exposures, rates)]
                    duration = Decimal(0)
                    for atom in self.spec['partition']['atoms']:
                        mask = atom['family_mask_' + scenario['membership']]
                        selected = [factors[index] for index in range(2) if mask & (1 << index)]
                        factor = Decimal(1) if not selected else max(selected) if scenario['overlap'] == 'co_bottleneck' else sum(selected) / len(selected)
                        duration += atom['duration_ticks'] * factor
                    expected.append(duration)
                self.assertEqual(list(map(Decimal, output['latency_interval'])), [min(expected), max(expected)])

    def test_numeric_peak_is_caller_owned_and_not_clipped(self):
        self.spec['peak'] = dict(kind='hypothesis', domain=deepcopy(self.spec['peak_domain']), parameter=None,
            interval=['0.01', '0.01'], evidence='invented-peak', missing_input=None)
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertGreater(Decimal(point(result)['mfu']['numeric_interval'][0]), 1)
        self.assertEqual(model.specification()['peak'], self.spec['peak'])

    def test_duplicate_charge_and_duplicate_active_membership_rejected(self):
        self.spec['partition']['atoms'][1]['accounting_owner'] = 'owner-0'
        self.reject('DUPLICATE_ID')
        self.spec = fixtures.model()
        self.spec['partition']['atoms'][0]['active_actions'].append('left')
        self.reject('SCHEMA_UNIQUE')

    def test_gaps_overlaps_and_duration_mismatch_rejected(self):
        for interval, code in (([1, 2], 'PARTITION_COVERAGE'), ([0, 3], 'MISSING_ENDPOINT'), ([0, 0], 'ATOM_DURATION')):
            changed = deepcopy(self.spec)
            changed['partition']['atoms'][0].update(interval=interval, duration_ticks=interval[1] - interval[0])
            self.reject(code, changed)
        self.spec['partition']['atoms'][0]['duration_ticks'] += 1
        self.reject('ATOM_DURATION')

    def test_active_set_not_merely_cardinality(self):
        self.spec['partition']['atoms'][0]['active_actions'] = ['right']
        self.reject('ACTIVE_MEMBERSHIP')

    def test_duplicate_atom_overlap_missing_terminal_and_wrong_owner_kind(self):
        extra = dict(self.spec['partition']['atoms'][0], id='duplicate-charge', accounting_owner='duplicate-owner')
        self.spec['partition']['atoms'].append(extra)
        self.reject('PARTITION_COVERAGE')
        self.spec = fixtures.model()
        self.spec['partition']['atoms'].pop()
        self.reject('PARTITION_COVERAGE')
        self.spec = fixtures.model()
        self.spec['partition']['atoms'][1]['owner_kind'] = 'exclusive_observation'
        self.reject('OWNER_KIND')

    def test_outside_and_cross_boundary_actions_are_clipped_not_recharged(self):
        self.spec['join']['actions'][0]['interval'][0] = 0
        self.spec['clocks'][0]['window'][0] = 1
        self.spec['partition']['atoms'][0].update(interval=[1, 2], duration_ticks=1)
        self.spec['join']['actions'].append(dict(id='outside', clock='clock-a', interval=[20, 23], source_cost_ticks=None))
        self.spec['join']['bindings'].append(dict(action='outside', status='unresolved', identity=None, candidates=[], unknown='identity_missing'))
        model = api.DesignSpace(self.spec)
        self.assertEqual(point(model.evaluate(fixtures.request(model)))['latency_interval'], ['10', '10'])

    def test_empty_and_zero_length_observations_do_not_add_cost(self):
        self.spec['join']['actions'].append(dict(id='marker', clock='clock-a', interval=[5, 5], source_cost_ticks=None))
        self.spec['join']['bindings'].append(dict(action='marker', status='unresolved', identity=None, candidates=[], unknown='identity_missing'))
        model = api.DesignSpace(self.spec)
        self.assertEqual(point(model.evaluate(fixtures.request(model)))['latency_interval'], ['11', '11'])
        self.spec['join'] = dict(actions=[], bindings=[], dependencies=[])
        self.spec['partition']['atoms'] = [dict(self.spec['partition']['atoms'][-1], interval=[0, 11], duration_ticks=11)]
        model = api.DesignSpace(self.spec)
        self.assertEqual(point(model.evaluate(fixtures.request(model)), '2', '2')['latency_interval'], ['11', '11'])

    def test_input_and_output_snapshot_isolation(self):
        expected = self.model.evaluate(self.request)
        self.spec['partition']['atoms'][0]['duration_ticks'] = 99
        for snapshot in (self.model.specification(), self.model.catalog(), self.model.evaluate(self.request), self.model.schema()):
            snapshot.clear()
        self.assertEqual(self.model.evaluate(self.request), expected)
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(self.model.evaluate(self.request), expected)

    def test_causal_mode_owns_decimal_rounding_and_traps(self):
        self.request['axes'][0].update(domain=['1', '3'], grid=['1', '3'])
        expected = self.model.evaluate(self.request)
        with localcontext() as context:
            context.prec = 4
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            model = api.DesignSpace(self.spec)
            self.assertEqual(model.evaluate(self.request), expected)

    def test_cli_api_byte_parity_replay_and_json_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, query = root / 'model.json', root / 'request.json'
            source.write_text(json.dumps(self.spec))
            query.write_text(json.dumps(self.request))
            command = [sys.executable, '-B', str(MODULES / 'design_space.py'), 'evaluate', '--model', str(source), '--request', str(query)]
            expected = api.formulas.encoded(self.model.evaluate(self.request))
            for seed in ('0', '19'):
                result = subprocess.run(command, capture_output=True, check=True, env=dict(os.environ, PYTHONHASHSEED=seed))
                self.assertEqual(result.stdout, expected)
                self.assertEqual(json.loads(result.stdout), self.model.evaluate(self.request))
            source.write_text('{"mode":"causal_timing","mode":"causal_timing"}')
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['code'], 'DUPLICATE_JSON_KEY')
            self.spec['partition']['atoms'][0]['family_mask_conservative'] = -1
            source.write_text(json.dumps(self.spec))
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['code'], 'SCHEMA_INTEGER')

    def test_schema_catalog_template_cli_api_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'model.json'
            source.write_text(json.dumps(self.spec))
            for operation in ('catalog', 'template'):
                result = subprocess.run([sys.executable, '-B', str(MODULES / 'design_space.py'), operation,
                    '--model', str(source)], capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout), getattr(self.model, operation)())
            for kind in ('model', 'request'):
                result = subprocess.run([sys.executable, '-B', str(MODULES / 'design_space.py'), 'schema',
                    '--kind', kind, '--mode', 'causal_timing'], capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout), getattr(wire, kind + '_schema')('causal_timing'))

    def test_large_synthetic_join_has_no_dense_mask_table_or_pairwise_join(self):
        self.spec['clocks'][0]['window'] = [0, 1200]
        self.spec['join'] = dict(actions=[], bindings=[], dependencies=[])
        self.spec['partition']['atoms'] = []
        for index in range(1200):
            name = 'action-' + str(index)
            self.spec['join']['actions'].append(dict(id=name, clock='clock-a', interval=[index, index + 1], source_cost_ticks=None))
            self.spec['join']['bindings'].append(dict(action=name, status='unresolved', identity=None, candidates=[], unknown='identity_missing'))
            self.spec['partition']['atoms'].append(dict(id='atom-' + str(index), clock='clock-a', interval=[index, index + 1],
                duration_ticks=1, active_actions=[name], accounting_owner='owner-' + str(index), owner_kind='exclusive_observation',
                exclusive_causal_owner=None, family_mask_conservative=1, family_mask_inclusive=1))
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(point(result, '2', '2')['latency_interval'], ['600.0', '600.0'])
        self.assertEqual(len(result['causal_order']['clocks'][0]['histogram']['inclusive']), 1)

    def test_modes_cannot_mix_contracts(self):
        for mode in ('hypothetical_joint', 'evidence_portfolio'):
            changed = deepcopy(self.spec)
            changed['mode'] = mode
            self.reject('SCHEMA_FIELDS', changed)
            query = deepcopy(self.request)
            query['mode'] = mode
            with self.assertRaises(wire.Rejected):
                self.model.evaluate(query)


if __name__ == '__main__':
    unittest.main()
