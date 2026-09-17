"""Finite compute-to-memory lowering contracts."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from simulator_detailed.compute_memory import ComputeMemoryPlan, compute_memory_id
from simulator_detailed.compute_plan import ComputePlan
from simulator_detailed.configs.schemas.compute_workload import ComputeWorkload
from simulator_detailed.memory_execution import MemoryRuntimeConfig
from simulator_detailed.tests.test_compute_contracts import compute_documents
from simulator_detailed.tests.test_memory_contracts import EVIDENCE
from simulator_detailed.tests.test_packet_runtime import transport_config


def compile_lowered(*, jobs: int = 3, slots: int = 2, local: bool = False) -> ComputeMemoryPlan:
    document, graph = compute_documents(jobs=jobs, slots=slots, local=local)
    workload = ComputePlan.compile(ComputeWorkload.model_validate(document), graph)
    memory = document["memory"]
    transport = transport_config(physical=32, capacity=2, staging=2).model_copy(update={
        "endpoint_queue_capacity_packets": memory["endpoint_queue_capacity_packets"],
        "endpoint_staging_capacity_flits": memory["endpoint_staging_capacity_flits"],
    })
    settings = MemoryRuntimeConfig(transport=transport, responder_capacity_packets=1,
                                   request_control_aci_cycles=1, response_control_aci_cycles=1,
                                   evidence=EVIDENCE)
    return ComputeMemoryPlan.compile(workload, settings)


class ComputeMemoryLoweringTests(unittest.TestCase):
    def test_remote_jobs_declare_reader_operands_result_and_writer(self) -> None:
        with patch("simpy.Environment", side_effect=AssertionError("runtime allocated")):
            plan = compile_lowered(jobs=3, slots=2)

        self.assertEqual([job.slot_generation for job in plan.workload.record.jobs], [0, 0, 1])
        self.assertEqual([job.slot_id for job in plan.workload.record.jobs], ["slot0", "slot1", "slot0"])
        operations = {operation.operation_id: operation for operation in plan.session.execution.memory.config.operations}
        self.assertEqual(len(operations), 18)
        self.assertEqual({operation.kind for operation in operations.values()},
                         {"read", "local_read", "local_write", "write_acknowledged"})

        for index, job in enumerate(plan.jobs):
            def operation_id(role: str, job_id: str = job.job_id) -> str:
                return compute_memory_id(job_id, role)

            self.assertEqual(len(job.reader_operations), 2)
            self.assertEqual(tuple(operations[operation].kind for operation in job.reader_operations), ("read", "read"))
            self.assertEqual(tuple(operations[operation].kind for operation in job.operand_operations),
                             ("local_read", "local_read"))
            self.assertEqual(operations[job.result_operation].kind, "local_write")
            self.assertIsNotNone(job.writer_operation)
            assert job.writer_operation is not None
            self.assertEqual(operations[job.writer_operation].kind, "write_acknowledged")
            self.assertEqual(operations[job.writer_operation].source_version.producer_id, job.result_operation)
            self.assertEqual(operations[job.writer_operation].depends_on[0], job.result_operation)
            self.assertEqual(job.reader_operations, (operation_id("read_a"), operation_id("read_b")))
            self.assertEqual(job.gate_ids,
                             tuple(operation_id(phase) for phase in
                                   ("reader", "inputs_ready", "compute", "math_done", "output_ready", "writer", "done")))
            if index:
                previous = plan.jobs[index - 1]
                self.assertIn(previous.writer_operation, operations[job.writer_operation].depends_on)

        gated = {operation for gate in plan.session.gates for operation in gate.operation_ids}
        self.assertEqual(gated, set(operations))
        self.assertEqual(len(plan.session.gates), 21)
        self.assertEqual(plan.revalidate().plan_sha256, plan.plan_sha256)

    def test_local_jobs_omit_network_operations_but_keep_local_service(self) -> None:
        plan = compile_lowered(jobs=4, slots=2, local=True)
        operations = plan.session.execution.memory.config.operations
        self.assertEqual(len(operations), 12)
        self.assertTrue(all(operation.kind in {"local_read", "local_write"} for operation in operations))
        self.assertTrue(all(operation.fabric_id is None for operation in operations))
        for job in plan.jobs:
            self.assertEqual(job.reader_operations, ())
            self.assertIsNone(job.writer_operation)
            self.assertEqual(len(job.operand_operations), 2)
            self.assertEqual(job.completion_operations[0], job.result_operation)

    def test_lowering_preserves_version_and_slot_reuse_order(self) -> None:
        plan = compile_lowered(jobs=3, slots=2)
        operations = {operation.operation_id: operation for operation in plan.session.execution.memory.config.operations}
        first, second, third = plan.jobs
        third_reader = operations[third.reader_operations[0]]
        self.assertIn(first.writer_operation, third_reader.depends_on)
        self.assertIn(second.reader_operations[0], third_reader.depends_on)
        self.assertIn(second.reader_operations[1], third_reader.depends_on)
        self.assertEqual(third_reader.source_version.kind, "initial")
        second_operand = operations[second.operand_operations[0]]
        self.assertIn(first.result_operation, second_operand.depends_on)
        self.assertEqual(second_operand.source_version.producer_id, second.reader_operations[0])


if __name__ == "__main__":
    unittest.main()
