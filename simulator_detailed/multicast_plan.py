"""Deterministic planning for the opt-in multicast/synchronization subset."""

from __future__ import annotations

import math
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from .configs.schemas.multicast_sync import (
    MulticastSyncResult,
    MulticastSyncWorkload,
    MulticastWrite,
)
from .configs.schemas.topology import CanonicalTopology, GraphRecord, Identifier, Index
from .multicast_tree import RectangleTreePlan, compile_rectangle_tree
from .topology import content_digest


class MulticastSegmentPlan(GraphRecord):
    segment_index: int
    source_offset_bytes: int
    payload_bytes: int
    flit_count: int
    physical_bytes: int


class MulticastWritePlan(GraphRecord):
    operation_id: Identifier
    fabric_id: Index
    source_endpoint_id: Identifier
    completion: Literal["write_posted", "write_acknowledged"]
    source_useful_bytes: int
    destination_useful_bytes: int
    packet_physical_bytes: int
    tree: RectangleTreePlan
    segments: tuple[MulticastSegmentPlan, ...]


class ScalarCounterPlan(GraphRecord):
    counter_id: Identifier
    endpoint_id: Identifier
    buffer_id: Identifier
    offset_bytes: int
    width_bytes: int
    initial_value: int
    increment_count: int
    final_value: int


class MulticastSyncPlanRecord(GraphRecord):
    kind: Literal["multicast_sync_plan"] = "multicast_sync_plan"
    schema_version: int = 1
    model_revision: Literal["finite_multicast_sync_plan_v1"] = "finite_multicast_sync_plan_v1"
    workload_sha256: str
    topology_sha256: str
    plan_sha256: str
    operation_order: tuple[Identifier, ...]
    writes: tuple[MulticastWritePlan, ...]
    counters: tuple[ScalarCounterPlan, ...]
    waits: tuple[Identifier, ...]


@dataclass(frozen=True)
class MulticastSyncPlan:
    """Immutable planning output; no SimPy objects or runtime side effects."""

    workload: MulticastSyncWorkload
    topology: CanonicalTopology
    record: MulticastSyncPlanRecord

    @classmethod
    def compile(
        cls,
        workload: MulticastSyncWorkload,
        topology: CanonicalTopology,
    ) -> MulticastSyncPlan:
        workload = MulticastSyncWorkload.model_validate(workload.model_dump(mode="json"))
        topology = CanonicalTopology.model_validate(topology.model_dump(mode="json"))
        if topology.connectivity_state != "complete":
            raise ValueError("multicast/synchronization planning requires a complete topology")
        cls._validate_control(workload)
        cls._validate_memory_fabrics(workload, topology)
        cls._validate_dependencies(workload)
        write_plans = tuple(
            cls._write_plan(workload, topology, write) for write in workload.writes
        )
        counter_plans = cls._counter_plans(workload, topology)
        operation_order = cls._operation_order(workload)
        workload_json = workload.model_dump(mode="json")
        topology_json = topology.model_dump(mode="json")
        workload_sha = content_digest(workload_json)
        topology_sha = content_digest(topology_json)
        plan_sha = content_digest(
            {
                "model_revision": "finite_multicast_sync_plan_v1",
                "workload_sha256": workload_sha,
                "topology_sha256": topology_sha,
                "operation_order": operation_order,
                "writes": [item.model_dump(mode="json") for item in write_plans],
                "counters": [item.model_dump(mode="json") for item in counter_plans],
                "waits": [item.wait_id for item in workload.waits],
            }
        )
        record = MulticastSyncPlanRecord(
            workload_sha256=workload_sha,
            topology_sha256=topology_sha,
            plan_sha256=plan_sha,
            operation_order=operation_order,
            writes=write_plans,
            counters=counter_plans,
            waits=tuple(wait.wait_id for wait in workload.waits),
        )
        return cls(workload=workload, topology=topology, record=record)

    def revalidate(self) -> None:
        rebuilt = self.compile(self.workload, self.topology)
        if rebuilt.record.plan_sha256 != self.record.plan_sha256:
            raise ValueError("multicast/synchronization plan digest is not reproducible")

    def planning_result(self) -> MulticastSyncResult:
        return MulticastSyncResult(
            plan_sha256=self.record.plan_sha256,
            pending_operations=self.record.operation_order,
        )

    def require_executable(self) -> None:
        raise RuntimeError(
            "multicast/synchronization planning is admission-only in this child; "
            "runtime execution is intentionally not enabled"
        )

    @staticmethod
    def _validate_control(workload: MulticastSyncWorkload) -> None:
        control = workload.control
        for name in (
            "reservation_capacity",
            "replication_capacity_flits",
            "atomic_native_cycles",
        ):
            if getattr(control, name) <= 0:
                raise ValueError(f"control.{name} must be positive")
        for name in ("reservation_setup_aci_cycles", "reservation_edge_aci_cycles", "local_observation_aci_cycles"):
            value = getattr(control, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"control.{name} must be finite and non-negative")

    @staticmethod
    def _validate_memory_fabrics(workload: MulticastSyncWorkload, topology: CanonicalTopology) -> None:
        graph_fabrics = {fabric.fabric_id for fabric in topology.fabrics}
        if not set(workload.memory.fabrics) <= graph_fabrics:
            raise ValueError("memory configuration refers to an unknown topology fabric")
        if workload.writes and not set(workload.memory.fabrics) & {
            write.fabric_id for write in workload.writes
        }:
            raise ValueError("memory configuration exposes no multicast fabric")

    @staticmethod
    def _validate_dependencies(workload: MulticastSyncWorkload) -> None:
        operations = {
            *(write.operation_id for write in workload.writes),
            *(increment.operation_id for increment in workload.increments),
        }
        edges: dict[str, set[str]] = {operation: set() for operation in operations}
        for write in workload.writes:
            edges[write.operation_id].update(write.depends_on)
        for increment in workload.increments:
            edges[increment.operation_id].update(increment.depends_on)
        for wait in workload.waits:
            if any(item not in operations for item in wait.producer_operations + wait.data_ready_after):
                raise ValueError(f"wait {wait.wait_id}: unknown operation dependency")
        try:
            tuple(TopologicalSorter(edges).static_order())
        except CycleError as error:
            raise ValueError("multicast/synchronization dependencies contain a cycle") from error

    @staticmethod
    def _write_plan(
        workload: MulticastSyncWorkload,
        topology: CanonicalTopology,
        write: MulticastWrite,
    ) -> MulticastWritePlan:
        tree = compile_rectangle_tree(workload, topology, write)
        packet = workload.memory.packet
        remaining = write.size_bytes
        offset = write.source.offset_bytes
        segments: list[MulticastSegmentPlan] = []
        index = 0
        while remaining:
            payload = min(remaining, packet.max_segment_payload_bytes)
            flits = packet.header_flits + math.ceil(payload / packet.data_capacity_bytes)
            segments.append(
                MulticastSegmentPlan(
                    segment_index=index,
                    source_offset_bytes=offset,
                    payload_bytes=payload,
                    flit_count=flits,
                    physical_bytes=flits * packet.physical_flit_bytes,
                )
            )
            index += 1
            offset += payload
            remaining -= payload
        packet_bytes = sum(segment.physical_bytes for segment in segments)
        return MulticastWritePlan(
            operation_id=write.operation_id,
            fabric_id=write.fabric_id,
            source_endpoint_id=write.source_endpoint_id,
            completion=write.completion,
            source_useful_bytes=write.size_bytes,
            destination_useful_bytes=write.size_bytes * len(tree.recipients),
            packet_physical_bytes=packet_bytes,
            tree=tree,
            segments=tuple(segments),
        )

    @staticmethod
    def _counter_plans(
        workload: MulticastSyncWorkload,
        topology: CanonicalTopology,
    ) -> tuple[ScalarCounterPlan, ...]:
        resources = {resource.resource_id: resource for resource in topology.resources}
        attachments = {attachment.endpoint_id: attachment for attachment in topology.attachments}
        bindings = {endpoint.endpoint_id: endpoint for endpoint in workload.memory.endpoints}
        buffers = {buffer.buffer_id: buffer for buffer in workload.memory.buffers}
        increments_by_counter: dict[str, int] = {}
        for increment in workload.increments:
            increments_by_counter[increment.counter_id] = increments_by_counter.get(increment.counter_id, 0) + 1
            binding = bindings.get(increment.source_endpoint_id)
            attachment = attachments.get(increment.source_endpoint_id)
            if binding is None or attachment is None or "initiator" not in binding.roles:
                raise ValueError(f"atomic {increment.operation_id}: source endpoint is not an initiator")
            if binding.fabric_id != increment.fabric_id or attachment.fabric_id != increment.fabric_id:
                raise ValueError(f"atomic {increment.operation_id}: endpoint fabric mismatch")
        result: list[ScalarCounterPlan] = []
        for counter in workload.counters:
            buffer = buffers.get(counter.buffer_id)
            attachment = attachments.get(counter.endpoint_id)
            binding = bindings.get(counter.endpoint_id)
            resource = resources.get(buffer.resource_id) if buffer is not None else None
            if buffer is None or attachment is None or binding is None or resource is None:
                raise ValueError(f"counter {counter.counter_id}: endpoint, buffer, or resource is missing")
            if attachment.role != "compute" or attachment.enabled is not True or attachment.replay_enabled is not True:
                raise ValueError(f"counter {counter.counter_id}: endpoint is unavailable")
            if counter.buffer_id not in buffers or not buffer.writable or not buffer.readable:
                raise ValueError(f"counter {counter.counter_id}: buffer must be readable and writable")
            if counter.offset_bytes % counter.width_bytes:
                raise ValueError(f"counter {counter.counter_id}: offset is not naturally aligned")
            if counter.offset_bytes + counter.width_bytes > buffer.size_bytes:
                raise ValueError(f"counter {counter.counter_id}: width exceeds buffer")
            router = next(
                (
                    router
                    for router in topology.routers
                    if router.fabric_id == attachment.fabric_id and router.router_id == attachment.router_id
                ),
                None,
            )
            if router is None or resource.owner_tile_id != router.tile_id:
                raise ValueError(f"counter {counter.counter_id}: resource is owned by another tile")
            count = increments_by_counter.get(counter.counter_id, 0)
            final = counter.initial_value + count
            max_value = (1 << (8 * counter.width_bytes)) - 1
            if final > max_value:
                raise ValueError(f"counter {counter.counter_id}: monotonic increment overflows its admitted width")
            result.append(
                ScalarCounterPlan(
                    counter_id=counter.counter_id,
                    endpoint_id=counter.endpoint_id,
                    buffer_id=counter.buffer_id,
                    offset_bytes=counter.offset_bytes,
                    width_bytes=counter.width_bytes,
                    initial_value=counter.initial_value,
                    increment_count=count,
                    final_value=final,
                )
            )
        if not set(increments_by_counter) <= {counter.counter_id for counter in workload.counters}:
            raise ValueError("every atomic increment must refer to a declared counter")
        return tuple(result)

    @staticmethod
    def _operation_order(workload: MulticastSyncWorkload) -> tuple[Identifier, ...]:
        edges: dict[str, set[str]] = {
            operation_id: set()
            for operation_id in (
                *(write.operation_id for write in workload.writes),
                *(increment.operation_id for increment in workload.increments),
            )
        }
        for write in workload.writes:
            edges[write.operation_id].update(write.depends_on)
        for increment in workload.increments:
            edges[increment.operation_id].update(increment.depends_on)
        return tuple(TopologicalSorter(edges).static_order())
