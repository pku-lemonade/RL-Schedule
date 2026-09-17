"""Pure admission for a closed set of externally owned memory activation gates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Self

from pydantic import model_validator

from .configs.schemas.topology import GraphRecord, Identifier, unique
from .memory_execution import MemoryExecutionPlan
from .memory_ordering import MemoryWait
from .topology import content_digest


class MemoryGateDefinition(GraphRecord):
    gate_id: Identifier
    resource_id: Identifier
    operation_ids: tuple[Identifier, ...] = ()
    after_gates: tuple[Identifier, ...] = ()
    waits: tuple[MemoryWait, ...] = ()

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(self.operation_ids, "gated operation")
        unique(self.after_gates, "parent activation gate")
        unique(tuple((w.operation_id, w.event) for w in self.waits), "gate memory wait")
        return self


def check_acyclic(parents: dict[tuple[str, str], set[tuple[str, str]]]) -> None:
    """Iterative check for the combined memory-operation/stage dependency graph."""
    children: dict[tuple[str, str], list[tuple[str, str]]] = {node: [] for node in parents}
    counts = {node: len(dependencies) for node, dependencies in parents.items()}
    for node, dependencies in parents.items():
        for dependency in dependencies:
            if dependency not in children:
                raise ValueError(f"unknown combined dependency {dependency}")
            children[dependency].append(node)
    ready = deque(node for node, count in counts.items() if count == 0)
    visited = 0
    while ready:
        visited += 1
        for child in children[ready.popleft()]:
            counts[child] -= 1
            if counts[child] == 0:
                ready.append(child)
    if visited != len(parents):
        raise ValueError("combined memory/compute/FIFO gates contain a cycle")


@dataclass(frozen=True)
class MemorySessionPlan:
    execution: MemoryExecutionPlan
    owner_id: str
    gates: tuple[MemoryGateDefinition, ...]
    plan_sha256: str

    @classmethod
    def compile(cls, execution: MemoryExecutionPlan, *, owner_id: str,
                gates: tuple[MemoryGateDefinition, ...]) -> MemorySessionPlan:
        execution = MemoryExecutionPlan.compile(execution.memory, execution.settings)
        if not owner_id or any(c.isspace() for c in owner_id):
            raise ValueError("session requires an explicit enclosing owner identity")
        gates = tuple(MemoryGateDefinition.model_validate(g.model_dump(mode="python")) for g in gates)
        unique(tuple(g.gate_id for g in gates), "memory activation gate")
        assigned = tuple(op for g in gates for op in g.operation_ids)
        unique(assigned, "operation activation owner")
        operations = {o.operation_id: o for o in execution.memory.config.operations}
        if set(assigned) != set(operations):
            raise ValueError("every admitted memory operation requires exactly one activation gate")
        gate_map = {g.gate_id: g for g in gates}
        buffers = {b.buffer_id: b for b in execution.memory.config.buffers}
        resources = {r.resource_id: r for r in execution.memory.graph.resources}
        parents = {("operation", op): {("operation", w.operation_id) for w in order.waits}
                   for op, order in execution.ordering.operations.items()}
        for gate in gates:
            resource = resources.get(gate.resource_id)
            if (resource is None or resource.kind != "local_sram"
                    or resource.owner_tile_id not in execution.memory.graph.enabled_worker_ids):
                raise ValueError("activation stage requires an enabled worker's canonical L1")
            dependencies: set[tuple[str, str]] = set()
            for parent in gate.after_gates:
                if parent not in gate_map or gate_map[parent].resource_id != gate.resource_id:
                    raise ValueError("stage dependencies require the same canonical worker; remote notification is unsupported")
                dependencies.add(("gate", parent))
            for wait in gate.waits:
                if wait.operation_id not in operations:
                    raise ValueError("activation gate names an unadmitted operation")
                op = operations[wait.operation_id]
                if wait.event == "destination_ready":
                    if op.destination is None or buffers[op.destination.buffer_id].resource_id != gate.resource_id:
                        raise ValueError("gate cannot observe remote destination readiness")
                else:
                    if execution.ordering.operations[wait.operation_id].initiator_resource_id != gate.resource_id:
                        raise ValueError("gate completion waits require the same canonical initiator")
                    if wait.event == "request_handoff" and op.kind not in {"read", "write_posted", "write_acknowledged"}:
                        raise ValueError("local operations have no network handoff event")
                dependencies.add(("operation", wait.operation_id))
            parents["gate", gate.gate_id] = dependencies
            for op in gate.operation_ids:
                if execution.ordering.operations[op].initiator_resource_id != gate.resource_id:
                    raise ValueError("activation gate does not own this operation's initiator")
                parents["operation", op].add(("gate", gate.gate_id))
        check_acyclic(parents)
        digest = content_digest({"execution_plan_sha256": execution.plan_sha256, "owner_id": owner_id,
                                 "gates": [g.model_dump(mode="json") for g in gates], "policy": "finite_memory_session_v1"})
        return cls(execution, owner_id, gates, digest)

    def revalidate(self) -> MemorySessionPlan:
        rebuilt = self.compile(self.execution, owner_id=self.owner_id, gates=self.gates)
        if rebuilt.plan_sha256 != self.plan_sha256:
            raise ValueError("memory session plan differs from its admitted gates/settings")
        return rebuilt


@dataclass(frozen=True, eq=False)
class MemoryGateToken:
    """A capability checked by identity; reconstructing its fields grants nothing."""

    gate_id: str


@dataclass(frozen=True, eq=False)
class MemoryOwnerToken:
    owner_id: str


@dataclass(frozen=True, eq=False)
class MemoryOwnerComponentToken:
    component_id: str
