"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.definitions import TransType
from profiling_sim.tests.helpers import network, message


class ReduceTests(unittest.TestCase):
    def test_reduce_tree_reaches_selected_root(self):
        env, noc, pes = network()
        noc.register_reduce(11, [0, 2], 5, op=1)
        for src in (0, 2):
            pes[src].send(
                7,
                message(
                    src=src,
                    index=src,
                    trans_type=TransType.REDUCE,
                    task_id=11,
                    reduce_op=1,
                    value=src + 1,
                ),
            )
        env.run()
        self.assertEqual(len(pes[5].received), 1)
        result = pes[5].received[0][1]
        self.assertEqual(result.value, 4)

    def test_invalid_reduce_sources(self):
        _, noc, _ = network()
        for sources in ([], [0, 0], [99]):
            with self.assertRaises(ValueError):
                noc.register_reduce(11, sources, 5)
