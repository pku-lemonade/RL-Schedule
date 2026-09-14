"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.definitions import TransType
from profiling_sim.tests.helpers import network, message


class MulticastTests(unittest.TestCase):
    def test_broadcast_and_sparse_multicast(self):
        for mode, mask, expected in (
            (TransType.BROADCAST, 0, set(range(6))),
            (TransType.MULTICAST, (1 << 1) | (1 << 4), {1, 4}),
        ):
            env, noc, pes = network()
            pes[0].send(7, message(trans_type=mode, dst_mask=mask))
            env.run()
            self.assertEqual({p.id for p in pes if p.received}, expected)
            self.assertTrue(all(len(p.received) == 1 for p in pes if p.received))
