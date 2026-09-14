"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.definitions import TransferMode, UnsupportedTransferMode
from profiling_sim.tests.helpers import network, message


class TransferTests(unittest.TestCase):
    def test_single_side_pe_transfer_is_rejected(self):
        env, noc, pes = network()
        pes[0].send(7, message(transfer_mode=TransferMode.SINGLE_SIDE))
        with self.assertRaises(UnsupportedTransferMode):
            env.run()

    def test_payload_and_header_are_preserved(self):
        env, noc, pes = network()
        msg = message(size=17, header_bytes=3)
        pes[0].send(7, msg)
        env.run()
        self.assertEqual(pes[5].received[-1][1].total_bytes(), 20)
        self.assertTrue(noc.r2r_links)
