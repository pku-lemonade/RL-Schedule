"""Frozen pre-graph runtime expectations; independent of the graph adapter."""

import json
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.configs.schemas.failure_configs import LinkFail
from simulator_detailed.noc import FlitAction
from simulator_detailed.tests.test_phase2_noc import build_runtime
from simulator_detailed.utils.definitions import (
    DimSlice,
    Direction,
    Message,
    NMCShapeMode,
)

FIXTURE = Path(__file__).parent / "fixtures/topology_baseline.json"


def legacy_observation(slowdown=False):
    path = Path(__file__).parents[1] / "configs/instances/mesh_example.json"
    config = ArchConfig.model_validate_json(path.read_text())
    env, arch, cores = build_runtime(config.noc, config.core)
    if slowdown:
        env.process(arch.link_fail(LinkFail(
            start_time=0, end_time=100, router_id=0,
            direction=Direction.EAST, times=3,
        )))
    completed = []
    early_scales = []

    def sample_failure():
        yield env.timeout(1)
        for fabric, noc in arch.nocs.items():
            for link in noc.r2r_links[:3]:
                early_scales.append([int(fabric), link.identity.link_id, link.delay_factor])

    env.process(sample_failure())

    def transfer(fabric):
        source = cores[0].nmc_channel_for(fabric)
        target = cores[-1].nmc_channel_for(fabric)
        message = Message(src=source.binding.address, dst=target.binding.address,
                          index=int(fabric), data=[DimSlice(start=0, end=29)])
        yield env.all_of([target.recv_message(message, NMCShapeMode.DYNAMIC), source.send(message)])
        completed.append([int(fabric), round(env.now, 9)])

    for fabric in config.noc.fabric_ids:
        env.process(transfer(fabric))
    env.run()
    return {
        "completion": sorted(completed),
        "early_link_scales": early_scales,
        "routes": [
            [int(fabric), event.router_id, event.out_port, round(event.time, 9)]
            for fabric, noc in arch.nocs.items()
            for event in noc.tracer.events if event.action is FlitAction.ROUTER_RC
        ],
        "links": [
            [int(fabric), link.identity.link_id,
             link.identity.src_router, link.identity.dst_router, link.delay_factor]
            for fabric, noc in arch.nocs.items() for link in noc.r2r_links
        ],
    }


class LegacyTopologyBaselineTests(unittest.TestCase):
    def test_custom_mesh_route_timing_and_slowdown_baseline(self):
        reference = json.loads(FIXTURE.read_text())
        for slowdown in (False, True):
            with self.subTest(slowdown=slowdown):
                self.assertEqual(legacy_observation(slowdown),
                                 reference["slowed" if slowdown else "normal"])
