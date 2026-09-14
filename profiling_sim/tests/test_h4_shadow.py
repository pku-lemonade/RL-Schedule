"""Configuration-based simulator regressions with synthetic data."""

import unittest
import simpy
from pydantic import ValidationError
from profiling_sim.config import ShadowConfig, CoreConfig, NMCConfig, SPMConfig
from profiling_sim.shadow import ShadowPipeline, sramc_entry, mdma_channel_entry
from profiling_sim.tests.helpers import device, device_config


class ShadowTests(unittest.TestCase):
    def test_variable_compute_stages_and_channels(self):
        for stages, channels in ((2, 3), (5, 4)):
            cfg = device_config(
                core=CoreConfig(
                    x=3, y=2, spm=SPMConfig(size=1024), nmc=NMCConfig(channels=channels)
                ),
                shadow=ShadowConfig(
                    enabled=True,
                    matrix=[1] * stages,
                    vector=[2] * stages,
                    matrix_compute_stage=stages - 1,
                    vector_compute_stage=0,
                ),
            )
            arch = device(cfg)
            core = arch.cores[0]
            self.assertEqual(len(core.matrix_pipeline.entry_ids), stages)
            self.assertEqual(len(core.sramc_pipelines), channels * 2)
            task = core.enter_matrix(cal_cycles=3)
            arch.env.run(until=task)
            self.assertEqual(arch.env.now, stages - 1 + 3)
            self.assertEqual(sramc_entry(channels, True), f"sram.{channels}.upload")
            self.assertEqual(mdma_channel_entry(channels), f"dma.channel.{channels}")

    def test_pipeline_capacity_and_initiation_interval(self):
        env = simpy.Environment()
        pipeline = ShadowPipeline(env, "test", ["a", "b"], [2, 3], occupancy=2, ii=3)
        tasks = [env.process(pipeline.enter(tag=i)) for i in range(4)]
        env.run(until=env.all_of(tasks))
        starts = [
            event["enter"] for event in pipeline.events if event["entry_id"] == "a"
        ]
        self.assertEqual(starts, [0, 3, 6, 9])
        self.assertEqual(pipeline.slots.level, 2)

    def test_invalid_compute_stage(self):
        with self.assertRaises(ValidationError):
            ShadowConfig(matrix=[1, 2], matrix_compute_stage=2)
