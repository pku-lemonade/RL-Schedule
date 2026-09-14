"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.config import LinkConfig
from profiling_sim.noc import Link
from profiling_sim.tests.helpers import message
import simpy


class QoSTests(unittest.TestCase):
    def test_configured_and_per_message_burst_quanta(self):
        for default, override, expected in (
            (3, 0, [15, 15, 1]),
            (3, 2, [10, 10, 10, 1]),
            (0, 0, [31]),
        ):
            env = simpy.Environment()
            link = Link(
                env, LinkConfig(width=5, burst_beats=default), deterministic=True
            )
            msg = message(size=31, burst_len_mode=override)
            self.assertEqual(link._payload_chunks(msg), expected)
            done = link.put(msg)
            env.run(until=done)
            self.assertEqual([e.data_size for e in link.events], expected)

    def test_zero_payload_still_transmits_header(self):
        env = simpy.Environment()
        link = Link(env, LinkConfig(width=5, burst_beats=3), deterministic=True)
        env.run(until=link.put(message(size=0, header_bytes=2)))
        self.assertEqual(len(link.events), 1)
        self.assertEqual(env.now, 1)

    def test_single_side_rate_factor_is_configurable(self):
        from profiling_sim.definitions import TransferMode

        times = []
        for factor in (1.0, 0.5):
            env = simpy.Environment()
            link = Link(
                env,
                LinkConfig(width=5, single_side_bandwidth_factor=factor),
                deterministic=True,
            )
            env.run(
                until=link.put(message(size=20, transfer_mode=TransferMode.SINGLE_SIDE))
            )
            times.append(env.now)
        self.assertEqual(times, [4, 8])
