"""Configuration-based simulator regressions with synthetic data."""

import unittest
import simpy
from pydantic import ValidationError
from profiling_sim.config import ClockConfig, DMAEngineConfig, LinkConfig, NMCConfig
from profiling_sim.dma import DMAEngine
from profiling_sim.core import NMC


class FeatureTests(unittest.TestCase):
    def test_invalid_configuration_fails_before_simulation(self):
        for model, values in (
            (DMAEngineConfig, {"channels": 0}),
            (LinkConfig, {"width": 0}),
            (NMCConfig, {"unknown_option": 1}),
            (ClockConfig, {"aci_mhz": float("inf")}),
        ):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                model.model_validate(values)

    def test_dma_parallel_channels_follow_config(self):
        env = simpy.Environment()
        dma = DMAEngine(env, DMAEngineConfig(channels=3, width=5), clock_scale=2)
        tasks = [env.process(dma.transfer(21)) for _ in range(4)]
        env.run(until=env.all_of(tasks))
        self.assertEqual(env.now, 6)
        self.assertEqual(len(dma.channel_store.items), 3)

    def test_nmc_channel_capacity(self):
        env = simpy.Environment()
        nmc = NMC(env, NMCConfig(channels=5))
        self.assertEqual(nmc.channel_store.capacity, 5)
