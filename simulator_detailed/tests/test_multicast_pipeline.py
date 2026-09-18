"""Representative two-round distribute/compute/collect composition."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_pipeline import (
    FinitePipelineExecutor,
    FinitePipelinePlan,
    FinitePipelineWorkload,
)
from simulator_detailed.tests.test_multicast_sync import workload_document


def pipeline_workload() -> tuple[dict[str, Any], dict[str, Any]]:
    document, graph = workload_document()
    second_write = copy.deepcopy(document["writes"][0])
    second_write["operation_id"] = "distribute1"
    second_write["depends_on"] = ["broadcast"]
    document["writes"].append(second_write)
    document["counters"] = [{
        "counter_id": "collector",
        "endpoint_id": "ep-t0_0",
        "buffer_id": "b-ep-t0_0",
        "offset_bytes": 256,
        "width_bytes": 1,
        "initial_value": 0,
    }]
    document["increments"] = [
        {
            "operation_id": "inc0", "source_endpoint_id": "ep-t0_0", "fabric_id": 0,
            "counter_id": "collector", "completion": "atomic_posted", "depends_on": ["broadcast"],
        },
        {
            "operation_id": "inc1", "source_endpoint_id": "ep-t0_0", "fabric_id": 0,
            "counter_id": "collector", "completion": "atomic_posted", "depends_on": ["inc0", "distribute1"],
        },
    ]
    document["waits"] = [
        {
            "wait_id": "wait0", "endpoint_id": "ep-t0_0", "counter_id": "collector", "threshold": 1,
            "producer_operations": ["inc0"], "data_ready_after": ["broadcast"],
        },
        {
            "wait_id": "wait1", "endpoint_id": "ep-t0_0", "counter_id": "collector", "threshold": 2,
            "producer_operations": ["inc1"], "data_ready_after": ["distribute1"],
        },
    ]
    return {
        "kind": "multicast_pipeline_workload",
        "schema_version": 1,
        "multicast": document,
        "stages": [
            {
                "stage_id": "compute0", "endpoint_id": "ep-t0_0",
                "input_buffer_ids": ["b-ep-t1_0"], "output_buffer_id": "b-ep-t0_0",
                "wait_ids": ["wait0"], "service_aci_cycles": 3, "slot_generation": 1,
            },
            {
                "stage_id": "compute1", "endpoint_id": "ep-t0_0",
                "input_buffer_ids": ["b-ep-t1_1"], "output_buffer_id": "b-ep-t0_0",
                "wait_ids": ["wait1"], "service_aci_cycles": 3, "slot_generation": 2,
            },
        ],
        "rounds": [
            {
                "round_id": "round0", "distribute_operation_ids": ["broadcast"],
                "stage_ids": ["compute0"], "wait_ids": ["wait0"], "threshold": 1,
            },
            {
                "round_id": "round1", "distribute_operation_ids": ["distribute1"],
                "stage_ids": ["compute1"], "wait_ids": ["wait1"], "threshold": 2,
            },
        ],
    }, graph


class MulticastPipelineTests(unittest.TestCase):
    def test_two_round_pipeline_reuses_slots_after_local_waits(self) -> None:
        document, graph = pipeline_workload()
        plan = FinitePipelinePlan.compile(
            FinitePipelineWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
        )
        result = FinitePipelineExecutor(plan).run()
        self.assertEqual(result.status, "complete")
        self.assertEqual([stage.stage_id for stage in result.stages], ["compute0", "compute1"])
        self.assertTrue(all(stage.status == "complete" for stage in result.stages))
        self.assertEqual(result.scalar.counters[0].value, 2)
        self.assertEqual([wait.reason for wait in result.scalar.waits], ["threshold_reached", "threshold_reached"])
        self.assertEqual(sum(event.action == "stage_complete" for event in result.events), 2)
        output = next(buffer for buffer in result.buffers if buffer.buffer_id == "b-ep-t0_0")
        self.assertEqual(output.version, 6)

    def test_pipeline_rejects_stage_cycles_and_nonincreasing_rounds(self) -> None:
        document, graph = pipeline_workload()
        bad_rounds = copy.deepcopy(document)
        bad_rounds["rounds"][1]["threshold"] = 1
        with self.assertRaises(ValueError):
            FinitePipelineWorkload.model_validate(bad_rounds)
        bad_stage = copy.deepcopy(document)
        bad_stage["rounds"][1]["stage_ids"] = ["compute0"]
        with self.assertRaises(ValueError):
            FinitePipelinePlan.compile(
                FinitePipelineWorkload.model_validate(bad_stage), CanonicalTopology.model_validate(graph)
            )


if __name__ == "__main__":
    unittest.main()
