"""Pure event-order, physical hazard and source-version admission.

An operation edge alone does not prove a memory effect: posted completion and
local-handoff fences finish before destination writes. This compiler checks
the applicable access-finish event and admits no implicit race serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType
from typing import Literal

from .configs.schemas.memory_replay import MemoryOperation, MemoryVersion
from .configs.schemas.topology import GraphRecord, Identifier, Index
from .memory_plan import MemoryPlan
from .memory_routes import MemoryRoutes

DependencyEvent = Literal["complete", "destination_ready", "request_handoff"]
EventNode = tuple[str, str]


class MemoryWait(GraphRecord):
    operation_id: Identifier
    event: DependencyEvent


class MemoryOperationOrder(GraphRecord):
    operation_id: Identifier
    initiator_resource_id: Identifier
    source_version: MemoryVersion | None
    waits: tuple[MemoryWait, ...]
    fence_fabrics: tuple[Index, ...] = ()
    policy: Literal["explicit_dependencies_v1"] = "explicit_dependencies_v1"


@dataclass(frozen=True)
class _Access:
    operation_id: str
    resource_id: str
    start: int
    end: int
    write: bool

    @property
    def finish(self) -> str:
        return "destination_ready" if self.write else "source_read_complete"

    def overlaps(self, other: _Access) -> bool:
        return self.resource_id == other.resource_id and self.start < other.end and other.start < self.end

    def contains(self, other: _Access) -> bool:
        return self.resource_id == other.resource_id and self.start <= other.start and self.end >= other.end


def _worker_resource(plan: MemoryPlan, operation: MemoryOperation) -> str:
    endpoint = next(e for e in plan.config.endpoints if e.endpoint_id == operation.initiator_id)
    router = next(r for r in plan.graph.routers if (r.fabric_id, r.router_id) == (endpoint.fabric_id, endpoint.router_id))
    if not endpoint.enabled or "initiator" not in endpoint.roles or router.tile_id not in plan.graph.enabled_worker_ids:
        raise ValueError("local operations and fences require an enabled worker initiator")
    resources = [r.resource_id for r in plan.graph.resources if r.resource_id in endpoint.resource_ids
                 and r.kind == "local_sram" and r.owner_tile_id == router.tile_id]
    if len(resources) != 1:
        raise ValueError("local operation/fence requires one canonical worker L1 resource")
    return resources[0]


@dataclass(frozen=True)
class MemoryOrderingPlan:
    operations: Mapping[str, MemoryOperationOrder]

    @classmethod
    def compile(cls, plan: MemoryPlan, routes: MemoryRoutes) -> MemoryOrderingPlan:
        config = plan.config
        operations = {o.operation_id: o for o in config.operations}
        position = {o.operation_id: index for index, o in enumerate(config.operations)}
        buffers = {b.buffer_id: b for b in config.buffers}
        capacity = {r.resource_id: r.capacity_bytes for r in plan.graph.resources}
        capacity.update({r.resource_id: r.capacity_override_bytes for r in config.resources if r.capacity_override_bytes is not None})
        owners = {r.operation_id: r.initiator_resource_id for r in routes.operations}
        for op in config.operations:
            if op.operation_id not in owners:
                owners[op.operation_id] = _worker_resource(plan, op)
        accesses: list[_Access] = []
        reads: dict[str, _Access] = {}
        writes: dict[str, _Access] = {}
        versions: dict[str, MemoryVersion] = {}
        for op in config.operations:
            for extent, write in ((op.source, False), (op.destination, True)):
                if extent is None:
                    continue
                buffer = buffers[extent.buffer_id]
                start = buffer.base_address + extent.offset_bytes
                end = start + extent.size_bytes
                if extent.offset_bytes + extent.size_bytes > buffer.size_bytes or end > capacity[buffer.resource_id]:
                    raise ValueError("memory operation range exceeds buffer/resource capacity")
                if start % config.packet.address_alignment_bytes:
                    raise ValueError("memory operation address is not aligned")
                if not (buffer.writable if write else buffer.readable):
                    raise ValueError("memory operation violates buffer permission")
                if op.kind in {"local_read", "local_write"} and buffer.resource_id != owners[op.operation_id]:
                    raise ValueError("local clients may access only their own L1 resource")
                access = _Access(op.operation_id, buffer.resource_id, start, end, write)
                accesses.append(access)
                (writes if write else reads)[op.operation_id] = access
                if not write:
                    version = op.source_version
                    if version is None:
                        if buffer.producer_operation_id is not None:
                            version = MemoryVersion(kind="producer", producer_id=buffer.producer_operation_id)
                        elif buffer.initially_ready:
                            version = MemoryVersion(kind="initial")
                        else:
                            raise ValueError("source requires an initial or explicit producer version")
                    if version.kind == "initial" and not buffer.initially_ready:
                        raise ValueError("source has no initialized version")
                    versions[op.operation_id] = version
        for buffer in config.buffers:
            if buffer.producer_operation_id is not None:
                producer = operations[buffer.producer_operation_id]
                if producer.destination is None or producer.destination.buffer_id != buffer.buffer_id:
                    raise ValueError("buffer producer does not write that buffer")

        orders: dict[str, MemoryOperationOrder] = {}
        for op in config.operations:
            waits: list[MemoryWait] = []
            for dependency in op.depends_on:
                if owners[dependency] != owners[op.operation_id]:
                    raise ValueError("completion dependencies require the same canonical initiator")
                waits.append(MemoryWait(operation_id=dependency, event="complete"))
            for dependency in op.destination_ready_after:
                target = writes.get(dependency)
                if target is None or target.resource_id != owners[op.operation_id]:
                    raise ValueError("destination readiness can be observed only at the destination's local resource")
                waits.append(MemoryWait(operation_id=dependency, event="destination_ready"))
            fabrics = op.fence_fabrics
            if op.kind == "fence":
                selected: set[int] = set()
                for dependency in op.fence_operations:
                    referenced = operations[dependency]
                    if referenced.kind not in {"read", "write_posted", "write_acknowledged"} or referenced.fabric_id is None:
                        raise ValueError("fences may select only network operations")
                    if position[dependency] >= position[op.operation_id]:
                        raise ValueError("fences cannot select forward operations")
                    if owners[dependency] != owners[op.operation_id]:
                        raise ValueError("fences require the same canonical initiator")
                    if fabrics and referenced.fabric_id not in fabrics:
                        raise ValueError("selected operation lies outside fence fabrics")
                    if op.fence_mode == "remote_completion" and referenced.kind == "write_posted":
                        raise ValueError("remote-completion fence cannot include posted writes")
                    selected.add(referenced.fabric_id)
                    waits.append(MemoryWait(operation_id=dependency,
                                            event="request_handoff" if op.fence_mode == "local_handoff" else "complete"))
                fabrics = fabrics or tuple(sorted(selected))
            orders[op.operation_id] = MemoryOperationOrder(
                operation_id=op.operation_id, initiator_resource_id=owners[op.operation_id],
                source_version=versions.get(op.operation_id), waits=tuple(dict.fromkeys(waits)), fence_fabrics=fabrics)

        # Use event-level ancestry; no timing estimates or list-order shortcuts.
        parents: dict[EventNode, set[EventNode]] = {}

        def node(operation_id: str, event: str) -> EventNode:
            kind = operations[operation_id].kind
            if event == "complete":
                event = {"write_posted": "request_handoff", "read": "destination_ready",
                         "local_read": "source_read_complete", "local_write": "destination_ready"}.get(kind, event)
            return operation_id, event

        def edge(before: EventNode, after: EventNode) -> None:
            parents.setdefault(after, set()).add(before)

        for op in config.operations:
            events = ("start", "source_read_complete", "request_handoff", "destination_ready")
            if op.kind == "read":
                events = ("start", "request_handoff", "source_read_complete", "destination_ready")
            elif op.kind == "local_read":
                events = ("start", "source_read_complete")
            elif op.kind == "local_write":
                events = ("start", "destination_ready")
            elif op.kind == "fence":
                events = ("start", "complete")
            if op.kind == "write_acknowledged":
                events += ("complete",)
            for earlier, later in pairwise(events):
                edge(node(op.operation_id, earlier), node(op.operation_id, later))
            for wait in orders[op.operation_id].waits:
                edge(node(wait.operation_id, wait.event), node(op.operation_id, "start"))
        ancestors: dict[EventNode, set[EventNode]] = {}
        visiting: set[EventNode] = set()

        def preceding(item: EventNode) -> set[EventNode]:
            if item in visiting:
                raise ValueError("memory event dependencies contain a cycle")
            if item not in ancestors:
                visiting.add(item)
                ancestors[item] = set()
                for parent in parents.get(item, set()):
                    ancestors[item].add(parent)
                    ancestors[item].update(preceding(parent))
                visiting.remove(item)
            return ancestors[item]

        def before(access: _Access, operation_id: str) -> bool:
            return node(access.operation_id, access.finish) in preceding(node(operation_id, "start"))

        for index, a in enumerate(accesses):
            for b in accesses[index + 1:]:
                if (a.overlaps(b) and (a.write or b.write)
                        and (a.operation_id == b.operation_id or not (before(a, b.operation_id) or before(b, a.operation_id)))):
                    raise ValueError("overlapping memory accesses lack a sufficient effect dependency")
        for operation_id, read in reads.items():
            version = versions[operation_id]
            producer = None if version.producer_id is None else writes.get(version.producer_id)
            if version.kind == "producer" and (producer is None or not producer.contains(read) or not before(producer, operation_id)):
                raise ValueError("source producer must cover the range and finish before the read")
            for writer in writes.values():
                if not writer.overlaps(read) or writer == producer:
                    continue
                if before(read, writer.operation_id):
                    continue
                if producer is not None and before(writer, producer.operation_id):
                    continue
                raise ValueError("source version can be overwritten before the read")
        return cls(MappingProxyType(orders))
