"""Configuration-based simulator regressions with synthetic data."""

import unittest
from profiling_sim.config import load_arch
from profiling_sim.tests.helpers import device
from profiling_sim.run import simulate
from unittest.mock import Mock


class SmokeTests(unittest.TestCase):
    def test_example_builds_and_runs(self):
        config = load_arch("profiling_sim/configs/mesh_example.json")
        arch = device(config)
        arch.execute()
        self.assertEqual(len(arch.cores), config.noc.x * config.noc.y)

    def test_top_level_simulation(self):
        mapper = Mock()
        mapper.zero_degree.return_value = []
        mapper.all_tasks_completed.return_value = True
        duration, trace, arch = simulate(
            "profiling_sim/configs/mesh_example.json", mapper
        )
        self.assertEqual(duration, 0)
        self.assertEqual(len(arch.cores), 6)
