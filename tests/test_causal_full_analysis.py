"""CPU-only exact API/schema/CLI and bounded finite-study regressions."""
from copy import deepcopy
from contextlib import redirect_stdout
from decimal import DefaultContext, Inexact, ROUND_UP, getcontext, localcontext
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULES = Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'
sys.path.insert(0, str(MODULES))
import causal_full_analysis as full
import causal_full_fixtures as fixtures
import causal_timing_fixtures as legacy
import design_space as api
import design_space_schema as wire
import causal_full_schema as schema
import counterfactual as formulas

REPLAY_EVIDENCE = {}


class FullAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.spec = fixtures.model()
        self.model = api.DesignSpace(self.spec)
        self.request = fixtures.request(self.model)

    def reject_model(self, code):
        with self.assertRaises(wire.Rejected) as caught:
            api.DesignSpace(self.spec)
        self.assertEqual(caught.exception.decision['code'], code)

    def reject_request(self, code):
        with self.assertRaises(wire.Rejected) as caught:
            self.model.evaluate(self.request)
        self.assertEqual(caught.exception.decision['code'], code)

    def test_exact_response_policies(self):
        scenario = self.spec['scenarios'][0]
        factors = [Fraction(1, 2), Fraction(1, 4)]
        self.assertEqual(full._response(0, factors, scenario), 1)
        self.assertEqual(full._response(3, factors, scenario), Fraction(1, 4))
        scenario['sharing'] = 'shared_serial'
        self.assertEqual(full._response(3, factors, scenario), Fraction(3, 8))
        scenario['sharing'] = 'co_bottleneck'
        self.assertEqual(full._response(3, factors, scenario), Fraction(1, 2))

    def test_exact_rows_intervals_and_symbolic_mfu(self):
        result = self.model.evaluate(self.request)
        point = result['grid'][0]
        self.assertEqual(point['latency_ns']['interval'], [full.rational(Fraction(13, 3))] * 2)
        self.assertEqual(point['mfu']['coefficient']['interval'], [full.rational(Fraction(51, 13))] * 2)
        self.assertIsNone(point['mfu']['numeric_interval'])
        self.assertEqual(result['model'], self.spec)
        self.assertFalse(result['unavailable']['physical_promotion'])
        self.assertFalse(result['unavailable']['no_public_evaluation'])
        full.validate_result(result)

    def test_explicit_numeric_peak_conversion(self):
        self.spec['peak'].update(kind='accepted', parameter=None, interval=['5', '7'], evidence='caller-peak')
        for unit, conversion in [('tick', 3), ('ns', 1)]:
            self.spec['composition']['peak_rate_time_unit'] = unit
            model = api.DesignSpace(self.spec)
            result = model.evaluate(fixtures.request(model))
            mfu = result['grid'][0]['mfu']
            self.assertEqual(mfu['peak_to_per_ns'], full.rational(conversion))
            self.assertEqual(mfu['numeric_interval'], [full.rational(Fraction(51, 13 * 7 * conversion)),
                full.rational(Fraction(51, 13 * 5 * conversion))])

    def test_rehashed_output_semantic_rejection(self):
        result = self.model.evaluate(self.request)
        for mutate in (
                lambda value: value['grid'][0]['scenarios'][0].update(id='wrong'),
                lambda value: value['grid'][0]['scenarios'][0]['terminals'].clear(),
                lambda value: value['grid'][0]['latency_ns']['lower_scenarios'].reverse(),
                lambda value: value.update(baseline_index=1)):
            changed = deepcopy(result)
            mutate(changed)
            changed['output_identity'] = formulas.identity({key: value for key, value in changed.items() if key != 'output_identity'})
            with self.assertRaises(wire.Rejected):
                full.validate_result(changed)

    def test_fixed_exposure_and_unselected_family(self):
        self.request['axes'] = self.request['axes'][:1]
        self.request['surfaces'] = []
        result = self.model.evaluate(self.request)
        for point in result['grid']:
            self.assertEqual(point['scenarios'][-1]['latency_ns'], full.rational(Fraction(13, 3)))
        expected = full._scenario(self.spec, self.model._timing, self.spec['scenarios'][0], [Fraction(4), Fraction(1)])
        self.assertEqual(result['grid'][-1]['scenarios'][0], expected)

    def test_source_known_cost_is_metadata_only(self):
        source = self.spec['source_nodes'][0]
        source.update(expansion_status='known', invocation_status='known', cost_status='known',
            source_cost_ticks=23, cost_authority='caller-declaration')
        model = api.DesignSpace(self.spec)
        self.assertEqual(model.catalog()['baseline'], self.model.catalog()['baseline'])
        self.assertNotEqual(model.identity, self.model.identity)

    def test_source_unresolved_cost_rejected(self):
        self.spec['source_nodes'][0].update(cost_status='known', source_cost_ticks=23, cost_authority='caller')
        self.reject_model('SOURCE_COST')

    def test_source_cost_null_mismatch_rejected(self):
        self.spec['source_nodes'][0]['source_cost_ticks'] = 0
        self.reject_model('SOURCE_COST')

    def test_static_source_not_action_or_exact_edge(self):
        self.spec['join']['dependencies'][-1].update(status='exact', identity='new', unknown=None)
        self.reject_model('SOURCE_DEPENDENCY')
        self.spec = fixtures.model()
        self.spec['source_nodes'][0]['id'] = 'left'
        self.reject_model('SOURCE_ID')

    def test_source_null_clock_and_opaque_metadata(self):
        self.spec['source_nodes'][0]['metadata_json'] = '{"text":"__import__(anything)","nested":[null,3]}'
        model = api.DesignSpace(self.spec)
        self.assertEqual(model.specification()['source_nodes'], self.spec['source_nodes'])
        self.spec['source_nodes'][0]['clock'] = 'absent'
        self.reject_model('SOURCE_CLOCK')

    def test_source_logical_cycles_and_correlations(self):
        edge = deepcopy(self.spec['join']['dependencies'][-1])
        edge.update(id='return-source', before='left', after='static-unexpanded')
        self.spec['join']['dependencies'].append(edge)
        self.reject_model('CAUSAL_CYCLE')
        edge['basis'] = 'correlation'
        api.DesignSpace(self.spec)

    def test_source_metadata_json_rejected(self):
        for value in ('[]', '{"duplicate":1,"duplicate":2}', '{"bad":NaN}'):
            with self.subTest(value=value), self.assertRaises(wire.Rejected):
                self.spec['source_nodes'][0]['metadata_json'] = value
                api.DesignSpace(self.spec)

    def test_scenario_policy_and_priority_negatives(self):
        for key, value in [('sharing', 'run_code'), ('priority', [0]), ('priority', [0, 0]),
                ('priority', [False, 1]), ('exposure_mask', True), ('exposure_mask', -1), ('exposure_mask', 4)]:
            with self.subTest(key=key, value=value), self.assertRaises(wire.Rejected):
                spec = fixtures.model()
                spec['scenarios'][0][key] = value
                api.DesignSpace(spec)
        self.spec['scenarios'][1]['priority'] = [0, 1]
        self.reject_model('PRIORITY')

    def test_scenario_order_identity_and_mask_preservation(self):
        result = self.model.evaluate(self.request)
        self.assertEqual(result['model']['partition'], self.spec['partition'])
        for point in result['grid']:
            self.assertEqual([row['id'] for row in point['scenarios']], [row['id'] for row in self.spec['scenarios']])
        self.spec['scenarios'].reverse()
        self.assertNotEqual(api.DesignSpace(self.spec).identity, self.model.identity)

    def test_layer_baseline_peak_scope_and_conversion_negatives(self):
        for mutate in (
                lambda spec: spec['composition'].update(baseline_layer_ticks=14),
                lambda spec: spec['composition']['participants'][0].update(baseline_layer_ticks=14),
                lambda spec: spec['composition'].update(ticks_per_ns='0'),
                lambda spec: spec['composition']['participants'][0].update(scope='wrong'),
                lambda spec: spec['composition'].update(peak_participants=['absent'])):
            with self.subTest(mutate=mutate), self.assertRaises(wire.Rejected):
                spec = fixtures.model()
                mutate(spec)
                api.DesignSpace(spec)

    def test_standalone_equal_rank_clock_not_aggregated(self):
        legacy.add_clock(self.spec, offset=101, rank=0)
        self.spec['clocks'][1]['scope'] = 'standalone'
        self.spec['peak_domain']['participants'].remove('clock-b')
        self.spec['clocks'][1]['window'][1] = 129
        self.spec['partition']['atoms'].append(dict(id='standalone-tail', clock='clock-b', interval=[112, 129],
            duration_ticks=17, active_actions=[], accounting_owner='standalone-tail-owner', owner_kind='residual',
            exclusive_causal_owner=None, family_mask_conservative=0, family_mask_inclusive=0))
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(result['grid'][0]['latency_ns']['interval'], [full.rational(Fraction(13, 3))] * 2)
        self.assertIsNone(result['grid'][0]['scenarios'][0]['clocks'][1]['layer_ticks'])
        self.assertEqual(result['grid'][0]['moe_ns']['interval'], [full.rational(Fraction(11, 3))] * 2)
        self.assertEqual(len(result['model']['clocks']), 2)

    def test_layer_ties_and_migrations(self):
        legacy.add_clock(self.spec, offset=101, rank=0)
        self.spec['clocks'][1]['scope'] = 'other-layer'
        self.spec['peak']['domain'] = deepcopy(self.spec['peak_domain'])
        self.spec['composition']['peak_participants'].append('clock-b')
        self.spec['composition']['participants'].append(dict(clock='clock-b', scope='other-layer',
            release_offset_ticks=2, attention_offset_ticks=13, baseline_layer_ticks=13))
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(len(result['grid'][0]['scenarios'][0]['terminals']), 3)
        self.assertTrue(any(row['changed'] for row in result['migrations']))
        self.assertTrue(all(terminal['rank'] == 0 for row in result['migrations'] for terminal in row['before']))

    def test_once_only_and_mask_negatives(self):
        for key, value in [('accounting_owner', 'owner-1'), ('duration_ticks', 9),
                ('family_mask_conservative', 4), ('family_mask_inclusive', 0), ('active_actions', [])]:
            with self.subTest(key=key), self.assertRaises(wire.Rejected):
                spec = fixtures.model()
                spec['partition']['atoms'][0][key] = value
                api.DesignSpace(spec)

    def test_transitive_order_through_another_clock(self):
        legacy.add_clock(self.spec, offset=101, rank=0)
        self.spec['clocks'][1]['scope'] = 'standalone'
        self.spec['peak_domain']['participants'].remove('clock-b')
        self.spec['join']['dependencies'] = [dict(id=name, before=before, after=after,
            basis='happens_before', status='exact', identity=name, unknown=None)
            for name, before, after in [('out', 'right', 'second-left'), ('back', 'second-left', 'left')]]
        self.reject_model('CAUSAL_ORDER')

    def test_all_record_limits_and_validation_budget(self):
        for key, count in full._records(self.spec).items():
            if count:
                with self.subTest(key=key):
                    spec = deepcopy(self.spec)
                    spec['capacity']['records'][key] = count - 1
                    with self.assertRaises(wire.Rejected) as caught:
                        api.DesignSpace(spec)
                    self.assertEqual(caught.exception.decision['code'], 'RECORD_CAPACITY')
        self.spec['capacity']['validation_work'] = 0
        self.reject_model('VALIDATION_BUDGET')

    def test_all_evaluation_limits_precede_evaluation(self):
        expected = full._prepare(self.spec, self.model._timing, self.request)[1]
        for key, count in expected.items():
            with self.subTest(key=key):
                request = deepcopy(self.request)
                request['budget'][key] = count - 1
                with patch.object(full, '_scenario', side_effect=AssertionError('must not evaluate')):
                    with self.assertRaises(wire.Rejected) as caught:
                        self.model.evaluate(request)
                self.assertEqual(caught.exception.decision['code'], 'EVALUATION_BUDGET')
        self.request['budget'] = expected
        self.assertEqual(self.model.evaluate(self.request)['work'], expected)

    def test_invalid_budget_policy_grid_and_identity(self):
        for mutate in (
                lambda request: request['budget'].update(designs=True),
                lambda request: request['comparisons'].update(policy='automatic'),
                lambda request: request['comparisons'].update(pairs=[[0, 1]]),
                lambda request: request['comparisons'].update(policy='selected', pairs=[[0, 99]]),
                lambda request: request['axes'][0].update(grid=['1', '1', '4']),
                lambda request: request.update(input_identity='wrong')):
            with self.subTest(mutate=mutate), self.assertRaises(wire.Rejected):
                request = deepcopy(self.request)
                mutate(request)
                self.model.evaluate(request)

    def test_selected_pairs_do_not_claim_full_frontiers(self):
        self.request['comparisons'] = dict(policy='selected', pairs=[[1, 0]])
        result = self.model.evaluate(self.request)
        self.assertEqual(len(result['comparisons']), 1)
        self.assertEqual(result['pareto']['coverage'], 'selected_pairs')
        self.assertIsNone(result['pareto']['robust_frontier'])

    def test_shared_scenario_oat_and_joint_arithmetic(self):
        result = self.model.evaluate(self.request)
        self.assertEqual(len(result['oat']), 4)
        for scenario, row in enumerate(result['joint_minus_oat'][4]['scenarios']):
            latency = lambda index: full.number(result['grid'][index]['scenarios'][scenario]['latency_ns'])
            self.assertEqual(full.number(row['gain_ns']), latency(1) + latency(3) - latency(0) - latency(4))
        self.assertEqual(result['grid'][4]['rates'], [full.rational(2)] * 2)
        for pair in result['comparisons']:
            self.assertEqual(pair['faster'] + pair['equal'] + pair['slower'], len(self.spec['scenarios']))

    def test_pareto_quantifiers_minimize_latency_and_rates(self):
        def point(index, rate, latencies):
            return dict(index=index, rates=[full.rational(rate)], scenarios=[dict(latency_ns=full.rational(value)) for value in latencies])
        left, right = point(0, 1, [2, 3]), point(1, 1, [3, 3])
        relation = full._dominance(left, right)
        self.assertTrue(relation['robust'])
        self.assertTrue(relation['opportunity'])
        self.assertFalse(relation['guaranteed'])
        right['rates'] = [full.rational(2)]
        self.assertTrue(full._dominance(left, right)['guaranteed'])
        left['rates'] = [full.rational(3)]
        self.assertFalse(full._dominance(left, right)['opportunity'])

    def test_exact_surfaces_match_point_evaluation(self):
        result = self.model.evaluate(self.request)
        for surface in result['surfaces']:
            scenario = next(row for row in self.spec['scenarios'] if row['id'] == surface['scenario'])
            for segment in surface['segments']:
                first, last = map(full.number, segment['inverse_scale_interval'])
                for inverse in (first, (first + last) / 2, last):
                    rates = [1 / inverse if family['id'] in surface['axes'] else Fraction(1) for family in self.spec['families']]
                    row = full._scenario(self.spec, self.model._timing, scenario, rates)
                    self.assertEqual(full.number(row['latency_ns']), full.number(segment['layer_intercept']) + inverse * full.number(segment['layer_slope']))
                    self.assertEqual(full.number(row['moe_ns']), full.number(segment['moe_intercept']) + inverse * full.number(segment['moe_slope']))
            self.assertIsNotNone(surface['asymptotic_latency_ns'])

    def test_surface_no_extrapolation_and_invalid_domain(self):
        self.request['surfaces'][0]['extrapolation'] = 'forbidden'
        result = self.model.evaluate(self.request)
        self.assertIsNone(result['surfaces'][0]['asymptotic_latency_ns'])
        self.request['surfaces'][0]['domain'] = ['1', '5']
        self.reject_request('SURFACE_DOMAIN')

    def test_break_even_and_saturation(self):
        result = self.model.evaluate(self.request)
        surface = next(row for row in result['surfaces'] if row['id'] == 'diagonal' and row['scenario'] == 'scenario-0')
        self.assertEqual(surface['baseline_break_even_scale_intervals'], [[full.rational(1)] * 2])
        self.assertEqual(surface['finite_saturation_scale'], full.rational(3))
        self.assertEqual(surface['asymptotic_latency_ns'], full.rational(Fraction(7, 3)))
        self.assertTrue(any(len(row['terminals']) == 2 for row in surface['knots']))

    def test_asymptotic_only_saturation(self):
        self.spec['composition']['participants'][0].update(release_offset_ticks=0, attention_offset_ticks=0, baseline_layer_ticks=11)
        self.spec['composition']['baseline_layer_ticks'] = 11
        model = api.DesignSpace(self.spec)
        result = model.evaluate(fixtures.request(model))
        surface = next(row for row in result['surfaces'] if row['id'] == 'diagonal' and row['scenario'] == 'scenario-0')
        self.assertIsNone(surface['finite_saturation_scale'])
        self.assertEqual(surface['asymptotic_latency_ns'], full.rational(Fraction(2, 3)))

    def test_mixed_rates_against_independent_atom_oracle(self):
        for axis in self.request['axes']:
            axis.update(domain=['0.5', '4'], grid=['0.5', '1', '4'])
        for curve in self.request['surfaces']:
            curve['domain'] = ['0.5', '4']
        result = self.model.evaluate(self.request)
        for point in result['grid']:
            for scenario, row in zip(self.spec['scenarios'], point['scenarios']):
                factors = [1 / full.number(rate) if scenario['exposure_mask'] & (1 << index) else Fraction(1)
                    for index, rate in enumerate(point['rates'])]
                duration = Fraction(0)
                for atom in self.spec['partition']['atoms']:
                    mask = atom['family_mask_' + scenario['membership']]
                    selected = [index for index in range(2) if mask & (1 << index)]
                    response = Fraction(1)
                    if selected:
                        if scenario['sharing'] == 'priority':
                            response = factors[next(index for index in scenario['priority'] if index in selected)]
                        elif scenario['sharing'] == 'shared_serial':
                            response = sum(factors[index] for index in selected) / len(selected)
                        else:
                            response = max(factors[index] for index in selected)
                    duration += atom['duration_ticks'] * response
                self.assertEqual(row['latency_ns'], full.rational(max(Fraction(7), 2 + duration) / 3))
                self.assertEqual(row['moe_ns'], full.rational(duration / 3))
        self.assertEqual(result['baseline_index'], 4)
        self.test_exact_surfaces_match_point_evaluation()

    def test_decimal_context_independence_and_cleanup(self):
        reference = formulas.encoded(self.model.evaluate(self.request))
        original = DefaultContext.copy()
        try:
            DefaultContext.prec = 3
            DefaultContext.rounding = ROUND_UP
            DefaultContext.traps[Inexact] = True
            with localcontext() as context:
                context.prec = 2
                context.traps[Inexact] = True
                context.clear_flags()
                flags = dict(context.flags)
                model = api.DesignSpace(self.spec)
                self.assertEqual(formulas.encoded(model.evaluate(self.request)), reference)
                self.request['budget']['designs'] = 0
                self.reject_request('EVALUATION_BUDGET')
                self.assertEqual(dict(getcontext().flags), flags)
        finally:
            for field in ('prec', 'rounding', 'Emin', 'Emax', 'capitals', 'clamp'):
                setattr(DefaultContext, field, getattr(original, field))
            DefaultContext.traps = original.traps
            DefaultContext.flags = original.flags

    def test_defensive_snapshots_and_deterministic_replay(self):
        first = self.model.evaluate(self.request)
        second = self.model.evaluate(self.request)
        self.assertEqual(formulas.encoded(first), formulas.encoded(second))
        first['model']['partition']['atoms'][0]['duration_ticks'] = 999
        self.assertEqual(self.model.evaluate(self.request), second)
        self.model.specification()['source_nodes'].clear()
        self.assertTrue(self.model.specification()['source_nodes'])

    def test_closed_schemas_recursive_extra_and_missing_fields(self):
        def objects(value, path=()):
            if type(value) is dict:
                yield path, value
                for key, child in value.items():
                    yield from objects(child, path + (key,))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from objects(child, path + (index,))
        for original, contract in ((self.spec, schema.model_schema()), (self.request, schema.request_schema())):
            for path, source in objects(original):
                for missing in (False, True):
                    changed = deepcopy(original)
                    target = changed
                    for key in path:
                        target = target[key]
                    if missing:
                        target.pop(next(iter(source)))
                    else:
                        target['unexpected'] = None
                    with self.assertRaises(wire.Rejected):
                        wire.validate(changed, contract)

    def test_output_negatives(self):
        result = self.model.evaluate(self.request)
        for mutate in (
                lambda value: value.update(extra=None),
                lambda value: value['unavailable'].update(physical_promotion=True),
                lambda value: value['unavailable'].update(causal_mfu='promoted'),
                lambda value: value['grid'][0]['scenarios'][0]['latency_ns'].update(denominator=0),
                lambda value: value['grid'][0]['scenarios'][0]['latency_ns'].update(numerator=26, denominator=6),
                lambda value: value['grid'][0]['scenarios'][0].update(id='wrong')):
            with self.subTest(mutate=mutate), self.assertRaises(wire.Rejected):
                changed = deepcopy(result)
                mutate(changed)
                full.validate_result(changed)

    def test_json_schema_standard_and_result(self):
        import jsonschema
        for value, contract in ((self.spec, schema.model_schema()), (self.request, schema.request_schema()),
                (self.model.evaluate(self.request), schema.result_schema())):
            jsonschema.Draft202012Validator.check_schema(contract)
            jsonschema.validate(value, contract)

    def test_cli_determinism_schemas_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.json').write_text(json.dumps(self.spec))
            (root / 'request.json').write_text(json.dumps(self.request))
            command = [sys.executable, '-B', str(MODULES / 'design_space.py')]
            evaluate = command + ['evaluate', '--model', str(root / 'model.json'), '--request', str(root / 'request.json')]
            first = subprocess.run(evaluate, capture_output=True, check=True).stdout
            self.assertEqual(first, subprocess.run(evaluate, capture_output=True, check=True).stdout)
            self.assertEqual(first, formulas.encoded(self.model.evaluate(self.request)))
            for kind in ('model', 'request', 'result'):
                process = subprocess.run(command + ['schema', '--kind', kind, '--mode', schema.MODE], capture_output=True, check=True)
                self.assertIsInstance(json.loads(process.stdout), dict)
            self.request['budget']['designs'] = 0
            (root / 'request.json').write_text(json.dumps(self.request))
            process = subprocess.run(evaluate, capture_output=True)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads(process.stdout)['code'], 'EVALUATION_BUDGET')

    def test_package_and_exploration_dispatch_without_execution(self):
        from simulator_detailed.moe_model import exploration_pipeline
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.json').write_text(json.dumps(self.spec))
            (root / 'request.json').write_text(json.dumps(self.request))
            arguments = ['evaluate', '--model', str(root / 'model.json'), '--request', str(root / 'request.json')]
            output = io.StringIO()
            with redirect_stdout(output), patch.object(exploration_pipeline, 'run_baseline', side_effect=AssertionError('no execution')), \
                    patch.object(exploration_pipeline, 'run_sweep', side_effect=AssertionError('no execution')):
                status = exploration_pipeline.cli(['design-space', *arguments])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), self.model.evaluate(self.request))
            process = subprocess.run([sys.executable, '-B', '-m', 'simulator_detailed.moe_model.design_space', *arguments],
                capture_output=True, check=True, cwd=MODULES.parents[1])
            self.assertEqual(json.loads(process.stdout), self.model.evaluate(self.request))


class LargeShapeTests(unittest.TestCase):
    def test_large_bounded_shape_and_budget_contract(self):
        spec = fixtures.large_model()
        model = api.DesignSpace(spec)
        request = fixtures.large_request(model)
        result = model.evaluate(request)
        self.assertEqual(len(result['model']['partition']['atoms']), 50021)
        self.assertEqual(len(result['grid']), 361)
        self.assertEqual(len(result['grid'][0]['scenarios']), 31)
        self.assertEqual(len(result['comparisons']), 2)
        first_digest = result['output_identity']
        encoded = formulas.encoded(result)
        first_sha256 = hashlib.sha256(encoded).hexdigest()
        REPLAY_EVIDENCE.update(bytes=len(encoded), first_sha256=first_sha256,
            work=result['work'], budget=request['budget'], atoms=len(spec['partition']['atoms']),
            actions=len(spec['join']['actions']), bindings=len(spec['join']['bindings']), designs=361, scenarios=31)
        del encoded
        del result
        second = model.evaluate(request)
        self.assertEqual(second['output_identity'], first_digest)
        second_sha256 = hashlib.sha256(formulas.encoded(second)).hexdigest()
        self.assertEqual(second_sha256, first_sha256)
        REPLAY_EVIDENCE.update(second_sha256=second_sha256, byte_digest_equal=True, output_identity=first_digest)
        del second
        request['comparisons'] = dict(policy='all', pairs=[])
        with self.assertRaises(wire.Rejected) as caught:
            model.evaluate(request)
        self.assertEqual(caught.exception.decision['code'], 'EVALUATION_BUDGET')


class ResidualMaskTests(unittest.TestCase):
    def specification(self, mask=1):
        specification = fixtures.model()
        specification['partition']['atoms'][-1].update(
            family_mask_conservative=mask, family_mask_inclusive=mask)
        return specification

    def reject_before_scenario(self, specification, code=None):
        with patch.object(full, '_scenario', side_effect=AssertionError('baseline evaluated')) as scenario, \
                patch.object(full, 'evaluate', side_effect=AssertionError('response evaluated')) as evaluate:
            with self.assertRaises(wire.Rejected) as caught:
                api.DesignSpace(specification)
            if code is not None:
                self.assertEqual(caught.exception.decision['code'], code)
            scenario.assert_not_called()
            evaluate.assert_not_called()

    def test_zero_and_every_registered_singleton_including_high_bit(self):
        specification = self.specification(0)
        specification['families'].extend(dict(id='family-' + str(index),
            law='inverse_partition_response', authority='caller_hypothesis') for index in range(2, 67))
        specification['capacity']['records']['priority_entries'] = 1000
        for scenario in specification['scenarios']:
            scenario['exposure_mask'] = (1 << len(specification['families'])) - 1
            if scenario['sharing'] == 'priority':
                scenario['priority'] = list(reversed(range(len(specification['families']))))
        for mask in [0] + [1 << index for index in range(len(specification['families']))]:
            with self.subTest(mask=mask):
                specification['partition']['atoms'][-1].update(
                    family_mask_conservative=mask, family_mask_inclusive=mask)
                model = api.DesignSpace(specification)
                self.assertEqual(model.specification(), specification)
                clock = model._timing['clocks'][0]
                self.assertEqual(clock['ownership_ticks']['residual'], 2)
                self.assertEqual(clock['duration_ticks'], 11)

    def test_singleton_conditional_sensitivity_exact_oracle_and_once_only(self):
        for family_index, family_name in enumerate(('alpha', 'beta')):
            specification = self.specification(1 << family_index)
            specification['scenarios'] = [dict(id=membership + '-' + sharing + '-' + str(exposure),
                membership=membership, sharing=sharing,
                priority=[1, 0] if sharing == 'priority' else None, exposure_mask=exposure << family_index)
                for membership in ('conservative', 'inclusive')
                for sharing in ('priority', 'shared_serial', 'co_bottleneck') for exposure in (0, 1)]
            zeroed = deepcopy(specification)
            zeroed['partition']['atoms'][-1].update(family_mask_conservative=0, family_mask_inclusive=0)
            masked_model, zero_model = api.DesignSpace(specification), api.DesignSpace(zeroed)
            for model in (masked_model, zero_model):
                self.assertEqual(model._timing['clocks'][0]['ownership_ticks'],
                    dict(residual=2, exclusive_observation=5, shared_observation=4))
                for histogram in model._timing['clocks'][0]['histogram'].values():
                    self.assertEqual(sum(row['duration_ticks'] for row in histogram), 11)
            for selected in (True, False):
                results = []
                for model in (masked_model, zero_model):
                    request = fixtures.request(model)
                    name = family_name if selected else ('beta' if family_name == 'alpha' else 'alpha')
                    request.update(axes=[dict(id=name, family=name, unit='dimensionless_service_scale',
                        domain=['0.5', '2'], grid=['0.5', '1', '2'])], surfaces=[])
                    results.append(model.evaluate(request))
                for masked_point, zero_point in zip(results[0]['grid'], results[1]['grid']):
                    rate = full.number(masked_point['rates'][0]) if selected else Fraction(1)
                    for scenario, masked_row, zero_row in zip(specification['scenarios'],
                            masked_point['scenarios'], zero_point['scenarios']):
                        with self.subTest(family=family_name, selected=selected, rate=rate, scenario=scenario['id']):
                            exposure = int(bool(scenario['exposure_mask'] & (1 << family_index)))
                            factor = 1 - exposure + exposure / rate
                            factors = [Fraction(1), Fraction(1)]
                            factors[family_index] = factor
                            self.assertEqual(full._response(1 << family_index, factors, scenario), factor)
                            difference = full.number(masked_row['clocks'][0]['moe_ticks']) - full.number(zero_row['clocks'][0]['moe_ticks'])
                            self.assertEqual(difference, 2 * exposure * (1 / rate - 1))
                            self.assertEqual(full.number(masked_row['moe_ns']) - full.number(zero_row['moe_ns']), difference / 3)
                            if selected and exposure and rate != 1:
                                self.assertNotEqual(difference, 0)
                self.assertEqual(results[0]['model']['join'], results[1]['model']['join'])
                self.assertEqual(results[0]['model']['source_nodes'], results[1]['model']['source_nodes'])

    def test_singleton_preserves_unresolved_identities_costs_and_nulls(self):
        specification = self.specification()
        model = api.DesignSpace(specification)
        result = model.evaluate(fixtures.request(model))
        self.assertEqual(result['model'], specification)
        self.assertIsNone(result['model']['partition']['causal_latency_ticks'])
        self.assertTrue(all(atom['exclusive_causal_owner'] is None for atom in result['model']['partition']['atoms']))
        self.assertTrue(all(action['source_cost_ticks'] is None for action in result['model']['join']['actions']))
        self.assertEqual(result['model']['join']['bindings'][-1]['status'], 'unresolved')
        self.assertIsNone(result['model']['join']['bindings'][-1]['identity'])
        self.assertTrue(all(edge['status'] == 'unresolved' and edge['identity'] is None
            for edge in result['model']['join']['dependencies'][1:]))
        source = result['model']['source_nodes'][0]
        self.assertEqual(source['cost_status'], 'unresolved')
        self.assertIsNone(source['source_cost_ticks'])
        self.assertIsNone(source['cost_authority'])
        self.assertIsNone(source['exclusive_causal_owner'])
        self.assertIsNone(result['unavailable']['causal_latency_ns'])
        self.assertIsNone(result['unavailable']['causal_mfu'])
        self.assertFalse(result['unavailable']['physical_promotion'])

    def test_both_residual_fields_reject_noninteger_negative_and_unknown_bits(self):
        for field in ('family_mask_conservative', 'family_mask_inclusive'):
            for value in (-1, -2, True, False, 1.0, 1.5, '1', None, [], {}, 4, 5):
                with self.subTest(field=field, value=value):
                    specification = self.specification()
                    specification['partition']['atoms'][-1][field] = value
                    self.reject_before_scenario(wire.strict_json(json.dumps(specification)))

    def test_dynamic_registry_still_rejects_unknown_high_bits(self):
        for field in ('family_mask_conservative', 'family_mask_inclusive'):
            for value in (1 << 67, (1 << 67) | 1):
                with self.subTest(field=field, value=value):
                    specification = self.specification()
                    specification['families'].extend(dict(id='family-' + str(index),
                        law='inverse_partition_response', authority='caller_hypothesis') for index in range(2, 67))
                    specification['scenarios'] = [dict(id='high-bit', membership='inclusive', sharing='shared_serial',
                        priority=None, exposure_mask=1 << 66)]
                    specification['partition']['atoms'][-1][field] = value
                    self.reject_before_scenario(specification, 'FAMILY_MASK')

    def test_unequal_inclusive_only_and_multibit_residuals_reject(self):
        for conservative, inclusive in ((0, 1), (1, 0), (1, 2), (2, 1), (3, 3), (1, 3), (2, 3), (0, 3)):
            with self.subTest(conservative=conservative, inclusive=inclusive):
                specification = self.specification()
                specification['partition']['atoms'][-1].update(
                    family_mask_conservative=conservative, family_mask_inclusive=inclusive)
                self.reject_before_scenario(specification,
                    'MASK_SUBSET' if conservative & inclusive != conservative else 'RESIDUAL_MASK')

    def test_nonresidual_owner_kind_and_hidden_observations_reject(self):
        for owner_kind in ('exclusive_observation', 'shared_observation'):
            specification = self.specification()
            specification['partition']['atoms'][-1]['owner_kind'] = owner_kind
            self.reject_before_scenario(specification, 'OWNER_KIND')
        specification = self.specification()
        specification['partition']['atoms'][0].update(active_actions=[], owner_kind='residual')
        self.reject_before_scenario(specification, 'ACTIVE_MEMBERSHIP')
        specification = self.specification()
        specification['partition']['atoms'][0]['owner_kind'] = 'residual'
        self.reject_before_scenario(specification, 'OWNER_KIND')

    def test_cross_clock_and_incomplete_active_membership_reject(self):
        specification = self.specification()
        legacy.add_clock(specification)
        specification['clocks'][1]['scope'] = 'standalone'
        specification['peak_domain']['participants'].remove('clock-b')
        specification['partition']['atoms'][0]['active_actions'] = ['second-left']
        self.reject_before_scenario(specification, 'ACTIVE_MEMBERSHIP')
        specification = self.specification()
        specification['partition']['atoms'][1].update(active_actions=['left'], owner_kind='exclusive_observation')
        self.reject_before_scenario(specification, 'ACTIVE_MEMBERSHIP')

    def test_partition_coverage_duration_and_accounting_ownership_reject(self):
        for field, value in (('accounting_owner', 'owner-0'), ('id', 'atom-0'), ('duration_ticks', 0),
                ('duration_ticks', 3), ('interval', [9, 9]), ('interval', [11, 9])):
            with self.subTest(field=field, value=value):
                specification = self.specification()
                specification['partition']['atoms'][-1][field] = value
                self.reject_before_scenario(specification)
        for interval in ([10, 11], [8, 11]):
            specification = self.specification()
            specification['partition']['atoms'][-1].update(interval=interval, duration_ticks=interval[1] - interval[0])
            self.reject_before_scenario(specification, 'PARTITION_COVERAGE')
        specification = self.specification()
        specification['partition']['atoms'].pop()
        self.reject_before_scenario(specification, 'PARTITION_COVERAGE')
        specification = self.specification()
        specification['partition']['atoms'].append(deepcopy(specification['partition']['atoms'][-1]))
        self.reject_before_scenario(specification)

    def test_singleton_does_not_authorize_identity_or_cost_promotion(self):
        for mutate in (
                lambda spec: spec['partition']['atoms'][-1].update(exclusive_causal_owner='left'),
                lambda spec: spec['partition'].update(causal_latency_ticks=2),
                lambda spec: spec['join']['actions'][0].update(source_cost_ticks=2),
                lambda spec: spec['join']['bindings'][-1].update(identity='promoted'),
                lambda spec: spec['join']['dependencies'][1].update(identity='promoted'),
                lambda spec: spec['join']['dependencies'][1].update(status='exact', basis='happens_before', identity='promoted', unknown=None),
                lambda spec: spec['join']['dependencies'][-1].update(status='exact', identity='promoted', unknown=None),
                lambda spec: spec['source_nodes'][0].update(source_cost_ticks=2),
                lambda spec: spec['source_nodes'][0].update(cost_status='known', source_cost_ticks=2, cost_authority='caller'),
                lambda spec: spec['source_nodes'][0].update(exclusive_causal_owner='left')):
            with self.subTest(mutation=mutate):
                specification = self.specification()
                mutate(specification)
                self.reject_before_scenario(specification)

    def test_rehashed_singleton_results_cannot_promote_identity(self):
        model = api.DesignSpace(self.specification())
        original = model.evaluate(fixtures.request(model))
        for field, value in (('physical_promotion', True), ('exclusive_causal_owner', 'left'),
                ('causal_latency_ns', full.rational(2)), ('causal_mfu', full.rational(2))):
            with self.subTest(field=field):
                result = deepcopy(original)
                result['unavailable'][field] = value
                result.pop('output_identity')
                result['output_identity'] = formulas.identity(result)
                with self.assertRaises(wire.Rejected):
                    full.validate_result(result)

    def test_legacy_residuals_remain_zero_only(self):
        for conservative in range(4):
            for inclusive in range(4):
                specification = legacy.model()
                specification['partition']['atoms'][-1].update(
                    family_mask_conservative=conservative, family_mask_inclusive=inclusive)
                if conservative == inclusive == 0:
                    api.DesignSpace(specification)
                else:
                    self.reject_before_scenario(specification)

    def test_existing_observed_multibit_context_remains_valid(self):
        specification = self.specification()
        specification['partition']['atoms'][1].update(family_mask_conservative=3, family_mask_inclusive=3)
        model = api.DesignSpace(specification)
        self.assertEqual(model.specification(), specification)


if __name__ == '__main__':
    unittest.main()
