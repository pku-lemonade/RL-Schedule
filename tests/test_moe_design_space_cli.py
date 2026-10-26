"""Pure CPU CLI integration with invented contracts and isolated temporary files."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from simulator_detailed.moe_model import design_space, exploration_pipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import design_space_fixtures as fixtures


class DesignSpaceCliTests(unittest.TestCase):
    def call(self, arguments):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), patch.object(exploration_pipeline, 'run_baseline', side_effect=AssertionError('no execution')), \
                patch.object(exploration_pipeline, 'run_sweep', side_effect=AssertionError('no execution')):
            code = exploration_pipeline.cli(['design-space', *arguments])
        return code, json.loads(stream.getvalue())

    def test_explicit_mode_schema_dispatch(self):
        for mode in ('hypothetical_joint', 'evidence_portfolio'):
            code, result = self.call(['schema', '--kind', 'request', '--mode', mode])
            self.assertEqual(code, 0)
            self.assertEqual(result, design_space.wire.request_schema(mode))
            self.assertIn('mode', result['required'])

    def test_evaluate_matches_isolated_api(self):
        for fixture in (fixtures.joint_model, fixtures.portfolio_model):
            spec = fixture()
            model = design_space.DesignSpace(spec)
            query = fixtures.request(model)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'model.json').write_text(json.dumps(spec))
                (root / 'query.json').write_text(json.dumps(query))
                args = ['evaluate', '--model', str(root / 'model.json'), '--request', str(root / 'query.json')]
                code, result = self.call(args)
                self.assertEqual(code, 0)
                self.assertEqual(result, model.evaluate(query))
                del query['mode']
                (root / 'query.json').write_text(json.dumps(query))
                code, result = self.call(args)
                self.assertEqual(code, 2)
                self.assertEqual(result['code'], 'MODE')

    def test_existing_command_namespace_unchanged(self):
        parser = exploration_pipeline._parser()
        self.assertEqual(parser.parse_args(['status', '--run-dir', 'synthetic']).command, 'status')
        self.assertEqual(parser.parse_args(['analyze', '--run-dir', 'synthetic']).command, 'analyze')


if __name__ == '__main__':
    unittest.main()
