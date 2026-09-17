"""Lower finite compute jobs into one ordered memory plan and closed stage gates.

This module declares causality, not a compute scheduler. In particular it never
opens the math-completion gate or allocates a SimPy environment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .compute_plan import ComputePlan
from .configs.schemas.compute_workload import ComputeInput, ComputeJob, ComputeStream
from .configs.schemas.memory_replay import (
    MemoryOperation,
    MemoryRange,
    MemoryReplay,
    MemoryVersion,
)
from .configs.schemas.topology import GraphRecord, Identifier
from .memory_execution import MemoryExecutionPlan, MemoryRuntimeConfig
from .memory_ordering import MemoryWait
from .memory_plan import MemoryPlan
from .memory_session import MemoryGateDefinition, MemorySessionPlan
from .topology import content_digest


def compute_memory_id(job_id: str, role: str) -> str:
    """An injective namespace, including IDs containing punctuation/quotes."""
    return json.dumps(("compute", job_id, role), separators=(",", ":"), ensure_ascii=True)


class ComputeJobMemory(GraphRecord):
    job_id: Identifier
    reader_operations: tuple[Identifier, ...]
    operand_operations: tuple[Identifier, Identifier]
    result_operation: Identifier
    writer_operation: Identifier | None
    completion_operations: tuple[Identifier, ...]
    gate_ids: tuple[Identifier, ...]


@dataclass(frozen=True)
class ComputeMemoryPlan:
    workload: ComputePlan
    session: MemorySessionPlan
    jobs: tuple[ComputeJobMemory, ...]
    plan_sha256: str

    @classmethod
    def compile(cls, workload: ComputePlan, settings: MemoryRuntimeConfig) -> ComputeMemoryPlan:
        workload = workload.revalidate()
        lowered = _Lowering(workload)
        operations, gates, jobs = lowered.build()
        replay = MemoryReplay.model_validate({**workload.config.memory.model_dump(mode="python"),
                                             "kind": "memory_replay", "schema_version": 1,
                                             "model_revision": "addressed_memory_v1", "operations": operations})
        source: object = workload.graph.model_dump(mode="python")
        if workload.config.memory.source.kind == "hardware_profile":
            if workload.graph.origin.document_json is None:
                raise ValueError("lowering requires the admitted profile source")
            source = json.loads(workload.graph.origin.document_json)
        memory = MemoryExecutionPlan.compile(MemoryPlan.compile(replay, source), settings)
        session = MemorySessionPlan.compile(memory, owner_id=workload.record.plan_sha256, gates=gates)
        digest = content_digest({"workload_plan_sha256": workload.record.plan_sha256,
                                 "memory_session_sha256": session.plan_sha256,
                                 "jobs": [j.model_dump(mode="json") for j in jobs], "revision": "compute_memory_v1"})
        return cls(workload, session, jobs, digest)

    def revalidate(self) -> ComputeMemoryPlan:
        rebuilt = self.compile(self.workload, self.session.execution.settings)
        if (rebuilt.plan_sha256 != self.plan_sha256 or rebuilt.jobs != self.jobs
                or rebuilt.session.plan_sha256 != self.session.plan_sha256):
            raise ValueError("compute memory plan differs from its admitted lowering")
        self.session.revalidate()
        return rebuilt


class _Lowering:
    def __init__(self, workload: ComputePlan):
        self.workload = workload
        self.jobs = {j.job_id: j for s in workload.config.streams for j in s.jobs}
        self.costs = {j.job_id: j for j in workload.record.jobs}
        self.workers = {w.tile_id: w for w in workload.config.workers}
        self.endpoints = {e.endpoint_id: e for e in workload.config.memory.endpoints}
        self.records: dict[str, ComputeJobMemory] = {}
        for stream in workload.config.streams:
            previous_completion: tuple[str, ...] = ()
            for job in stream.jobs:
                result = compute_memory_id(job.job_id, "result")
                writer = None if job.output.mode == "local" else compute_memory_id(job.job_id, "write")
                # A local writer is a zero-network handoff. Its FIFO completion
                # also follows the preceding writer, without inventing a memory
                # transfer or treating an earlier posted effect as acknowledged.
                completion = (writer,) if writer is not None else (result, *previous_completion)
                record = ComputeJobMemory(
                    job_id=job.job_id,
                    reader_operations=tuple(compute_memory_id(job.job_id, f"read_{name}")
                                            for name, operand in (("a", job.a), ("b", job.b)) if operand.mode == "remote"),
                    operand_operations=(compute_memory_id(job.job_id, "operand_a"), compute_memory_id(job.job_id, "operand_b")),
                    result_operation=result, writer_operation=writer, completion_operations=completion,
                    gate_ids=tuple(compute_memory_id(job.job_id, phase) for phase in
                                   ("reader", "inputs_ready", "compute", "math_done", "output_ready", "writer", "done")))
                self.records[job.job_id] = record
                previous_completion = completion

    def _initiator(self, stream: ComputeStream, fabric: int | None = None) -> str:
        worker = self.workers[stream.worker_tile_id]
        if fabric is None:
            return worker.endpoint_ids[0]
        return next(e for e in worker.endpoint_ids if self.endpoints[e].fabric_id == fabric)

    def _version(self, binding: ComputeInput) -> MemoryVersion:
        producer = binding.version.producer_job_id
        if producer is None:
            return MemoryVersion(kind="initial")
        record = self.records[producer]
        return MemoryVersion(kind="producer", producer_id=record.writer_operation or record.result_operation)

    def _local_consumers(self, job: ComputeJob) -> tuple[str, ...]:
        if job.output.mode != "local":
            return ()
        return tuple(compute_memory_id(consumer.job_id, f"read_{name}" if binding.mode == "remote" else f"operand_{name}")
                     for consumer in self.jobs.values() for name, binding in (("a", consumer.a), ("b", consumer.b))
                     if binding.version.producer_job_id == job.job_id)

    def build(self) -> tuple[tuple[MemoryOperation, ...], tuple[MemoryGateDefinition, ...], tuple[ComputeJobMemory, ...]]:
        operations: list[MemoryOperation] = []
        gates: list[MemoryGateDefinition] = []
        for stream in self.workload.config.streams:
            for index, job in enumerate(stream.jobs):
                record = self.records[job.job_id]
                plan = self.costs[job.job_id]
                slot = stream.slots[index % len(stream.slots)]
                previous = self.records[stream.jobs[index - 1].job_id] if index else None
                prior_slot = stream.jobs[index - len(stream.slots)] if index >= len(stream.slots) else None
                predecessors = list(plan.predecessors)
                if prior_slot is not None:
                    predecessors.append(prior_slot.job_id)
                base = {op for p in predecessors for op in self.records[p].completion_operations}
                consumers = () if prior_slot is None else self._local_consumers(prior_slot)
                base.update(consumers)
                prior_reads = () if previous is None else previous.reader_operations
                local_ranges = tuple(MemoryRange(buffer_id=extent.buffer_id, offset_bytes=extent.offset_bytes,
                                                 size_bytes=cost.storage_bytes)
                                     for extent, cost in zip((slot.a, slot.b, slot.c), (plan.cost.a, plan.cost.b, plan.cost.c), strict=True))
                for name, binding, local_range, operand_op in zip(("a", "b"), (job.a, job.b), local_ranges[:2], record.operand_operations, strict=True):
                    version = self._version(binding)
                    if binding.mode == "remote":
                        read_id = compute_memory_id(job.job_id, f"read_{name}")
                        operations.append(MemoryOperation(operation_id=read_id, kind="read",
                                                          initiator_id=self._initiator(stream, binding.fabric_id), fabric_id=binding.fabric_id,
                                                          source=binding.source, destination=local_range, source_version=version,
                                                          depends_on=tuple(sorted(base | set(prior_reads)))))
                        version = MemoryVersion(kind="producer", producer_id=read_id)
                    dependencies = base | set(record.reader_operations)
                    if previous is not None:
                        dependencies.add(previous.result_operation)
                    operations.append(MemoryOperation(operation_id=operand_op, kind="local_read", initiator_id=self._initiator(stream),
                                                      source=local_range, source_version=version, depends_on=tuple(sorted(dependencies))))
                operations.append(MemoryOperation(operation_id=record.result_operation, kind="local_write",
                                                  initiator_id=self._initiator(stream), destination=local_ranges[2],
                                                  depends_on=record.operand_operations))
                if record.writer_operation is not None:
                    if job.output.mode == "local":
                        raise ValueError("local output cannot lower to a network writer")
                    operations.append(MemoryOperation(operation_id=record.writer_operation, kind=job.output.mode,
                                                      initiator_id=self._initiator(stream, job.output.fabric_id), fabric_id=job.output.fabric_id,
                                                      source=local_ranges[2], destination=job.output.destination,
                                                      source_version=MemoryVersion(kind="producer", producer_id=record.result_operation),
                                                      depends_on=(record.result_operation, *(() if previous is None else previous.completion_operations))))
                gates.extend(self._gates(stream, record, previous, tuple(dict.fromkeys(predecessors)), consumers))
        return tuple(operations), tuple(gates), tuple(self.records.values())

    def _gates(self, stream: ComputeStream, record: ComputeJobMemory, previous: ComputeJobMemory | None,
               predecessors: tuple[str, ...], consumers: tuple[str, ...]) -> tuple[MemoryGateDefinition, ...]:
        reader, inputs, compute, math_done, output, writer, done = record.gate_ids
        resource = self.workers[stream.worker_tile_id].l1_resource_id

        def gate(gate_id: str, operations: tuple[str, ...] = (), parents: tuple[str, ...] = (),
                 waits: tuple[str, ...] = ()) -> MemoryGateDefinition:
            return MemoryGateDefinition(gate_id=gate_id, resource_id=resource, operation_ids=operations,
                                        after_gates=tuple(dict.fromkeys(parents)),
                                        waits=tuple(MemoryWait(operation_id=op, event="complete") for op in dict.fromkeys(waits)))

        reader_parents = tuple(self.records[job].gate_ids[-1] for job in predecessors)
        if previous is not None:
            reader_parents += (previous.gate_ids[1],)
        return (
            gate(reader, record.reader_operations, reader_parents, consumers),
            gate(inputs, parents=(reader,), waits=record.reader_operations),
            gate(compute, record.operand_operations, (inputs, *(() if previous is None else (previous.gate_ids[4],)))),
            gate(math_done, (record.result_operation,), (compute,), record.operand_operations),
            gate(output, parents=(math_done,), waits=(record.result_operation,)),
            gate(writer, () if record.writer_operation is None else (record.writer_operation,),
                 (output, *(() if previous is None else (previous.gate_ids[-1],)))),
            gate(done, parents=(writer,), waits=(record.result_operation,) if record.writer_operation is None else (record.writer_operation,)),
        )
