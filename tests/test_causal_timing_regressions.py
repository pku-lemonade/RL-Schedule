"""Invented review repros and focused causal/decimal isolation controls."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import decimal
import io
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
import causal_timing
import design_space as api
import design_space_fixtures as legacy

FIXTURES = Path(__file__).resolve().parent / 'causal_timing_regressions'
CONTEXT_FIELDS = ('prec', 'rounding', 'Emin', 'Emax', 'capitals', 'clamp', 'flags', 'traps')
STANDALONE = ('import runpy; import sys; sys.path.insert(0, ' + repr(str(MODULES)) + '); runpy.run_path(' +
    repr(str(MODULES / 'design_space.py')) + ", run_name='__main__')")
POISON = """
import decimal
for context in (decimal.getcontext(), decimal.DefaultContext):
    context.prec = 2
    context.rounding = decimal.ROUND_UP
    context.Emin = -2
    context.Emax = 2
    context.capitals = 0
    context.clamp = 1
    for signal in context.traps:
        context.traps[signal] = True
        context.flags[signal] = True
"""


def fixture(case, kind):
    return json.loads((FIXTURES / ('repro-' + case + '-' + kind + '.json')).read_text())


def context_state(context):
    return [dict(getattr(context, field)) if field in ('flags', 'traps') else getattr(context, field)
            for field in CONTEXT_FIELDS]


@contextmanager
def hostile_context(target):
    caller = decimal.getcontext()
    saved_caller, saved_default = caller.copy(), decimal.DefaultContext.copy()
    contexts = [caller] if target == 'current' else [decimal.DefaultContext]
    if target == 'both':
        contexts = [caller, decimal.DefaultContext]
    try:
        for context in contexts:
            context.prec, context.rounding = 2, decimal.ROUND_UP
            context.Emin, context.Emax = -2, 2
            context.capitals, context.clamp = 0, 1
            for signal in context.traps:
                context.traps[signal] = True
                context.flags[signal] = True
        yield
    finally:
        for context, saved in ((caller, saved_caller), (decimal.DefaultContext, saved_default)):
            for field in CONTEXT_FIELDS:
                setattr(context, field, getattr(saved, field))
        decimal.setcontext(caller)


def snapshot(specification, request):
    model = api.DesignSpace(specification)
    api.wire.validate(specification, api.wire.model_schema(specification['mode']))
    api.wire.validate(request, model.schema())
    return api.formulas.encoded(dict(identity=model.identity, specification=model.specification(),
        catalog=model.catalog(), template=model.template(), schema=model.schema(),
        model_schema=api.wire.model_schema(specification['mode']), result=model.evaluate(request)))


class CausalTimingRegressionTests(unittest.TestCase):
    def test_review_transitive_same_clock_repro_rejected_api_and_cli(self):
        specification = fixture('causal-order', 'model')
        with self.assertRaises(api.Rejected) as caught:
            api.DesignSpace(specification)
        self.assertEqual(caught.exception.decision['code'], 'CAUSAL_ORDER')
        output = io.StringIO()
        with redirect_stdout(output):
            status = api.cli(['evaluate', '--model', str(FIXTURES / 'repro-causal-order-model.json'),
                '--request', str(FIXTURES / 'repro-causal-order-request.json')])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue()), caught.exception.decision)

    def test_valid_cross_clock_return_and_rank_separation(self):
        for rank in (2, 9):
            specification = fixture('causal-order', 'model')
            specification['clocks'][1]['rank'] = rank
            edges = specification['join']['dependencies']
            edges[0]['before'], edges[1]['after'] = 'early', 'later'
            model = api.DesignSpace(specification)
            request = fixture('causal-order', 'request')
            request['input_identity'] = model.identity
            result = model.evaluate(request)
            self.assertEqual(result['causal_order']['exact_order'], ['early', 'other-early', 'later'])
            self.assertIsNone(result['causal_latency_ticks'])
            self.assertEqual(result['causal_join'], specification['join'])

    def test_unresolved_edges_never_complete_an_exact_path(self):
        for basis in ('happens_before', 'logical', 'correlation'):
            specification = fixture('causal-order', 'model')
            specification['join']['dependencies'][1].update(status='unresolved', basis=basis,
                identity=None, unknown='missing_causal_binding')
            model = api.DesignSpace(specification)
            request = fixture('causal-order', 'request')
            request['input_identity'] = model.identity
            result = model.evaluate(request)
            self.assertEqual(result['causal_order']['exact_edges'], [['later', 'other-early']])
            self.assertEqual(result['causal_join'], specification['join'])
            self.assertFalse(result['unresolved_identities_promoted'])

    def test_all_small_dags_against_reachability_oracle(self):
        specification = fixture('causal-order', 'model')
        actions = {row['id']: row for row in specification['join']['actions'] if 'ambiguous' not in row['id']}
        accepted = rejected = 0
        for permutation in itertools.permutations(actions):
            possible = list(itertools.combinations(permutation, 2))
            for mask in range(1 << len(possible)):
                edges = [edge for index, edge in enumerate(possible) if mask & (1 << index)]
                invalid = False
                for source in actions:
                    reachable, pending = set(), [source]
                    while pending:
                        current = pending.pop()
                        for before, after in edges:
                            if before == current and after not in reachable:
                                reachable.add(after)
                                pending.append(after)
                    invalid |= any(actions[source]['clock'] == actions[target]['clock'] and
                        actions[source]['interval'][1] > actions[target]['interval'][0] for target in reachable)
                specification['join']['dependencies'] = [dict(id='edge-' + str(index), before=before, after=after,
                    status='exact', basis='happens_before', identity='edge-binding-' + str(index), unknown=None)
                    for index, (before, after) in enumerate(edges)]
                with self.subTest(edges=edges):
                    if invalid:
                        with self.assertRaises(api.Rejected) as caught:
                            api.DesignSpace(specification)
                        self.assertEqual(caught.exception.decision['code'], 'CAUSAL_ORDER')
                        rejected += 1
                    else:
                        model = api.DesignSpace(specification)
                        order = causal_timing.admit(model.specification())['exact_order']
                        self.assertTrue(all(order.index(before) < order.index(after) for before, after in edges))
                        accepted += 1
        self.assertGreater(accepted, 0)
        self.assertGreater(rejected, 0)

    def test_long_path_and_zero_duration_equality(self):
        actions = {str(index): dict(clock='clock-' + str(index), interval=[5, 6]) for index in range(200)}
        actions['199'] = dict(clock='clock-0', interval=[6, 6])
        edges = [(str(index), str(index + 1)) for index in range(199)]
        self.assertEqual(len(causal_timing._causal_order(actions, edges)), 200)
        actions['199']['interval'] = [5, 6]
        with self.assertRaises(api.Rejected) as caught:
            causal_timing._causal_order(actions, edges)
        self.assertEqual(caught.exception.decision['code'], 'CAUSAL_ORDER')
        actions = {name: dict(clock='one', interval=[5, 5]) for name in ('first', 'second')}
        self.assertEqual(causal_timing._causal_order(actions, [('first', 'second')]), ['first', 'second'])

    def test_review_decimal_repro_all_context_fields_and_no_leak(self):
        specification, request = fixture('decimal', 'model'), fixture('decimal', 'request')
        expected = snapshot(specification, request)
        existing = api.DesignSpace(specification)
        result = existing.evaluate(request)
        for target in ('current', 'default', 'both'):
            with self.subTest(target=target), hostile_context(target):
                caller = decimal.getcontext()
                before = context_state(caller), context_state(decimal.DefaultContext)
                self.assertEqual(snapshot(specification, request), expected)
                self.assertEqual(existing.evaluate(request), result)
                self.assertIs(decimal.getcontext(), caller)
                self.assertEqual((context_state(caller), context_state(decimal.DefaultContext)), before)

    def test_legacy_public_modes_are_context_independent(self):
        for factory in (legacy.joint_model, legacy.portfolio_model, legacy.scenario_model):
            specification = factory()
            model = api.DesignSpace(specification)
            request = legacy.request(model)
            request['axes'][0].update(domain=['1', '3'], grid=['1', '3'])
            expected = snapshot(specification, request)
            for target in ('current', 'default', 'both'):
                with self.subTest(factory=factory.__name__, target=target), hostile_context(target):
                    self.assertEqual(snapshot(specification, request), expected)

    def test_rejection_bytes_and_context_restoration_all_modes(self):
        for specification in [fixture('decimal', 'model'), legacy.joint_model(), legacy.portfolio_model()]:
            model = api.DesignSpace(specification)
            request = fixture('decimal', 'request') if specification['mode'] == 'causal_timing' else legacy.request(model)
            malformed = deepcopy(request)
            malformed['axes'][0]['domain'] = ['NaN', '3']
            outside = deepcopy(request)
            outside['axes'][0]['exposure']['interval'] = ['0', '2']
            wrong_mode = deepcopy(request)
            wrong_mode['input_identity'] = 'not-the-input'
            bad_model = deepcopy(specification)
            bad_model['useful_flops'] = 'not-a-decimal'
            calls = [(api.DesignSpace, bad_model), (model.evaluate, malformed),
                (model.evaluate, outside), (model.evaluate, wrong_mode)]
            if specification['mode'] == 'causal_timing':
                calls.append((api.DesignSpace, fixture('causal-order', 'model')))
            for admit, value in calls:
                with self.assertRaises(api.Rejected) as caught:
                    admit(value)
                expected = api.formulas.encoded(caught.exception.decision)
                with hostile_context('both'):
                    before = context_state(decimal.getcontext()), context_state(decimal.DefaultContext)
                    with self.assertRaises(api.Rejected) as hostile:
                        admit(value)
                    self.assertEqual(api.formulas.encoded(hostile.exception.decision), expected)
                    self.assertEqual((context_state(decimal.getcontext()), context_state(decimal.DefaultContext)), before)

    def test_cli_poison_before_import_api_parity_all_modes(self):
        entries = {
            'standalone': STANDALONE,
            'package': "import runpy; runpy.run_module('simulator_detailed.moe_model.design_space', run_name='__main__')",
            'exploration': "from simulator_detailed.moe_model import exploration_pipeline; "
                "raise SystemExit(exploration_pipeline.cli(['design-space', *sys.argv[1:]]))",
        }
        for specification in [fixture('decimal', 'model'), legacy.joint_model(), legacy.portfolio_model()]:
            model = api.DesignSpace(specification)
            request = fixture('decimal', 'request') if specification['mode'] == 'causal_timing' else legacy.request(model)
            with tempfile.TemporaryDirectory() as directory:
                source, query = Path(directory) / 'model.json', Path(directory) / 'request.json'
                source.write_bytes(api.formulas.encoded(specification))
                query.write_bytes(api.formulas.encoded(request))
                operations = [(['evaluate', '--model', str(source), '--request', str(query)], model.evaluate(request)),
                    (['catalog', '--model', str(source)], model.catalog()),
                    (['template', '--model', str(source)], model.template()),
                    (['schema', '--kind', 'model', '--mode', specification['mode']], api.wire.model_schema(specification['mode'])),
                    (['schema', '--kind', 'request', '--mode', specification['mode']], model.schema())]
                for entry, bootstrap in entries.items():
                    for arguments, expected in operations:
                        for poison, seed in (('', '0'), (POISON, '19')):
                            with self.subTest(mode=specification['mode'], entry=entry, operation=arguments[0], poison=bool(poison)):
                                result = subprocess.run([sys.executable, '-B', '-c', poison + '\nimport sys\n' + bootstrap,
                                    *arguments], cwd=MODULES.parents[1], capture_output=True,
                                    env=dict(os.environ, PYTHONHASHSEED=seed))
                                self.assertEqual(result.returncode, 0, result.stderr.decode())
                                self.assertEqual(result.stdout, api.formulas.encoded(expected))

    def test_cli_rejections_under_poison_before_import(self):
        for specification in [fixture('decimal', 'model'), legacy.joint_model(), legacy.portfolio_model(),
                fixture('causal-order', 'model')]:
            with tempfile.TemporaryDirectory() as directory:
                source, query = Path(directory) / 'model.json', Path(directory) / 'request.json'
                source.write_bytes(api.formulas.encoded(specification))
                try:
                    model = api.DesignSpace(specification)
                except api.Rejected:
                    request = fixture('causal-order', 'request')
                else:
                    request = fixture('decimal', 'request') if specification['mode'] == 'causal_timing' else legacy.request(model)
                    request['axes'][0]['exposure']['interval'] = ['0', '2']
                query.write_bytes(api.formulas.encoded(request))
                arguments = ['evaluate', '--model', str(source), '--request', str(query)]
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(api.cli(arguments), 2)
                for poison in ('', POISON):
                    bootstrap = poison + '\n' + STANDALONE
                    result = subprocess.run([sys.executable, '-B', '-c', bootstrap, *arguments], capture_output=True)
                    self.assertEqual(result.returncode, 2, result.stderr.decode())
                    self.assertEqual(result.stdout, output.getvalue().encode())


if __name__ == '__main__':
    unittest.main()
