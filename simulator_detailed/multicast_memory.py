"""Addressed-memory effects and completion records for planned multicast writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .configs.schemas.multicast_sync import MulticastSyncWorkload
from .configs.schemas.topology import GraphRecord, Identifier, Index
from .multicast_plan import MulticastSyncPlan, MulticastWritePlan
from .multicast_transport import (
    TreeForwardingEngine,
    TreeTransportPlan,
    TreeTransportResult,
)

MemoryEventAction = Literal[
    "source_read_start", "source_read_complete", "source_release",
    "destination_write_start", "destination_write_complete", "buffer_ready",
    "ack_emit", "ack_receive", "operation_complete",
]
OperationStatus = Literal["complete", "incomplete", "rejected"]
OperationReason = Literal["drained", "source_not_ready", "cycle_limit", "resource_capacity", "invalid"]


class MulticastBufferState(GraphRecord):
    buffer_id: Identifier
    ready: bool
    version: Index
    useful_bytes: Index
    service_bytes: Index
    producer_operation_id: Identifier | None


class MulticastMemoryEvent(GraphRecord):
    sequence: Index
    time_aci_cycles: float
    action: MemoryEventAction
    operation_id: Identifier
    endpoint_id: Identifier | None
    buffer_id: Identifier | None
    useful_bytes: Index
    service_bytes: Index
    physical_bytes: Index


class MulticastMemoryOperationRecord(GraphRecord):
    operation_id: Identifier
    completion: Literal["write_posted", "write_acknowledged"]
    status: OperationStatus
    source_read_useful_bytes: Index
    source_read_service_bytes: Index
    destination_write_useful_bytes: Index
    destination_write_service_bytes: Index
    acknowledgement_physical_bytes: Index
    source_reusable_aci_cycles: float | None
    destination_ready_aci_cycles: float | None
    completion_aci_cycles: float | None
    recipient_count: Index
    reason: OperationReason


class MulticastMemorySnapshot(GraphRecord):
    next_operation_index: Index
    elapsed_aci_cycles: float
    completed_operation_ids: tuple[Identifier, ...]
    pending_operation_ids: tuple[Identifier, ...]
    buffer_states: tuple[MulticastBufferState, ...]


class MulticastMemoryResult(GraphRecord):
    kind: Literal["multicast_memory_result"] = "multicast_memory_result"
    schema_version: Literal[1] = 1
    status: Literal["complete", "incomplete"]
    elapsed_aci_cycles: float
    operations: tuple[MulticastMemoryOperationRecord, ...]
    buffers: tuple[MulticastBufferState, ...]
    transport: TreeTransportResult
    events: tuple[MulticastMemoryEvent, ...]
    snapshot: MulticastMemorySnapshot


@dataclass(frozen=True)
class MulticastMemoryExecutor:
    """A bounded, side-effect-free execution projection over a multicast plan."""

    plan: MulticastSyncPlan
    transport: TreeTransportPlan

    @classmethod
    def compile(cls, plan: MulticastSyncPlan) -> MulticastMemoryExecutor:
        return cls(plan=plan, transport=TreeTransportPlan.compile(plan))

    def run(
        self,
        *,
        cycle_limit: float | None = None,
        snapshot: MulticastMemorySnapshot | None = None,
    ) -> MulticastMemoryResult:
        if cycle_limit is not None and cycle_limit < 0:
            raise ValueError("cycle limit must be non-negative")
        workload = self.plan.workload
        initial_buffers = self._initial_buffers(workload)
        completed: set[str] = set(snapshot.completed_operation_ids) if snapshot is not None else set()
        if snapshot is not None:
            initial_buffers = {state.buffer_id: state for state in snapshot.buffer_states}
        pending_writes = [write for write in self.plan.record.writes if write.operation_id not in completed]
        if not pending_writes:
            transport = TreeTransportResult(
                status="complete", reason="drained", elapsed_aci_cycles=snapshot.elapsed_aci_cycles if snapshot else 0,
                source_read_useful_bytes=0, destination_write_useful_bytes=0, packet_physical_bytes=0,
                launched_channel_bytes=0, reservations=(), events=(), pending_operations=(),
                snapshot=self.transport_snapshot(snapshot, 0, ()),
            )
            return MulticastMemoryResult(
                status="complete", elapsed_aci_cycles=transport.elapsed_aci_cycles, operations=(),
                buffers=tuple(initial_buffers.values()), transport=transport, events=(), snapshot=self._snapshot(
                    0, transport.elapsed_aci_cycles, set(), (), initial_buffers
                ),
            )
        write = pending_writes[0]
        operation_index = self.plan.record.operation_order.index(write.operation_id)
        source_buffer_id = next(
            item.source.buffer_id for item in workload.writes if item.operation_id == write.operation_id
        )
        source_state = initial_buffers[source_buffer_id]
        if not source_state.ready:
            record = self._operation(
                write, "incomplete", 0, 0, 0, 0, 0, None, None, None, "source_not_ready"
            )
            transport = self._empty_transport("source_not_ready", write.operation_id)
            return MulticastMemoryResult(
                status="incomplete", elapsed_aci_cycles=transport.elapsed_aci_cycles,
                operations=(record,), buffers=tuple(initial_buffers.values()), transport=transport,
                events=(), snapshot=self._snapshot(operation_index, 0, set(), (write.operation_id,), initial_buffers),
            )
        if not self._capacity_available(write):
            record = self._operation(
                write, "rejected", 0, 0, 0, 0, 0, None, None, None, "resource_capacity"
            )
            transport = self._empty_transport("resource_capacity", write.operation_id)
            return MulticastMemoryResult(
                status="incomplete", elapsed_aci_cycles=0, operations=(record,),
                buffers=tuple(initial_buffers.values()), transport=transport, events=(),
                snapshot=self._snapshot(operation_index, 0, set(), (write.operation_id,), initial_buffers),
            )
        engine = TreeForwardingEngine(
            self.transport,
            branch_capacity_flits=workload.control.replication_capacity_flits,
        )
        tree_result = engine.run(cycle_limit=cycle_limit, operation_order=(write.operation_id,))
        if tree_result.status != "complete":
            record = self._operation(
                write, "incomplete", 0, 0, 0, 0, 0, None, None, None, "cycle_limit"
            )
            return MulticastMemoryResult(
                status="incomplete", elapsed_aci_cycles=tree_result.elapsed_aci_cycles,
                operations=(record,), buffers=tuple(initial_buffers.values()), transport=tree_result,
                events=(), snapshot=self._snapshot(
                    operation_index, tree_result.elapsed_aci_cycles, set(), (write.operation_id,), initial_buffers
                ),
            )

        memory_events: list[MulticastMemoryEvent] = []
        elapsed = tree_result.elapsed_aci_cycles
        source_service = self._service_bytes(workload, source_buffer_id, write.source_useful_bytes)
        self._event(memory_events, "source_read_start", write, elapsed, write.source_useful_bytes, source_service,
                    source_buffer_id, write.source_endpoint_id)
        elapsed += self._service_cycles(workload, source_buffer_id, write.source_useful_bytes)
        self._event(memory_events, "source_read_complete", write, elapsed, write.source_useful_bytes, source_service,
                    source_buffer_id, write.source_endpoint_id)
        self._event(memory_events, "source_release", write, elapsed, 0, 0, source_buffer_id, write.source_endpoint_id)
        source_state = source_state.model_copy(update={"version": source_state.version + 1})
        initial_buffers[source_buffer_id] = source_state

        destination_service = 0
        destination_useful = 0
        for recipient in write.tree.recipients:
            service = self._service_bytes(workload, recipient.buffer_id, write.source_useful_bytes)
            cycles = self._service_cycles(workload, recipient.buffer_id, write.source_useful_bytes)
            destination_service += service
            destination_useful += write.source_useful_bytes
            self._event(memory_events, "destination_write_start", write, elapsed, write.source_useful_bytes, service,
                        recipient.buffer_id, recipient.endpoint_id)
            elapsed += cycles
            self._event(memory_events, "destination_write_complete", write, elapsed, write.source_useful_bytes, service,
                        recipient.buffer_id, recipient.endpoint_id)
            old = initial_buffers[recipient.buffer_id]
            initial_buffers[recipient.buffer_id] = old.model_copy(update={
                "ready": True,
                "version": old.version + 1,
                "useful_bytes": write.source_useful_bytes,
                "service_bytes": old.service_bytes + service,
                "producer_operation_id": write.operation_id,
            })
        for recipient in write.tree.recipients:
            self._event(memory_events, "buffer_ready", write, elapsed, write.source_useful_bytes, 0,
                        recipient.buffer_id, recipient.endpoint_id)
        destination_ready_time = elapsed

        acknowledgement_bytes = 0
        if write.completion == "write_acknowledged":
            acknowledgement_bytes = (
                len(write.tree.recipients)
                * len(write.segments)
                * workload.memory.packet.header_flits
                * workload.memory.packet.physical_flit_bytes
            )
            for recipient in write.tree.recipients:
                self._event(memory_events, "ack_emit", write, elapsed, 0,
                            workload.memory.packet.header_flits * workload.memory.packet.physical_flit_bytes,
                            None, recipient.endpoint_id)
                elapsed += workload.control.local_observation_aci_cycles
                self._event(memory_events, "ack_receive", write, elapsed, 0,
                            workload.memory.packet.header_flits * workload.memory.packet.physical_flit_bytes,
                            None, write.source_endpoint_id)
        self._event(memory_events, "operation_complete", write, elapsed, 0, 0, None, write.source_endpoint_id)
        record = self._operation(
            write, "complete", write.source_useful_bytes, source_service, destination_useful,
            destination_service, acknowledgement_bytes, elapsed - tree_result.elapsed_aci_cycles,
            destination_ready_time, elapsed, "drained"
        )
        completed.add(write.operation_id)
        pending = tuple(item.operation_id for item in self.plan.record.writes if item.operation_id not in completed)
        return MulticastMemoryResult(
            status="complete", elapsed_aci_cycles=elapsed, operations=(record,),
            buffers=tuple(initial_buffers.values()), transport=tree_result, events=tuple(memory_events),
            snapshot=self._snapshot(operation_index + 1, elapsed, completed, pending, initial_buffers),
        )

    def resume(self, result: MulticastMemoryResult) -> MulticastMemoryResult:
        return self.run(snapshot=result.snapshot)

    def _initial_buffers(self, workload: MulticastSyncWorkload) -> dict[str, MulticastBufferState]:
        return {
            buffer.buffer_id: MulticastBufferState(
                buffer_id=buffer.buffer_id,
                ready=buffer.initially_ready,
                version=0,
                useful_bytes=0,
                service_bytes=0,
                producer_operation_id=buffer.producer_operation_id,
            )
            for buffer in workload.memory.buffers
        }

    def _capacity_available(self, write: MulticastWritePlan) -> bool:
        resources = {resource.resource_id: resource for resource in self.plan.workload.memory.resources}
        buffers = {buffer.buffer_id: buffer for buffer in self.plan.workload.memory.buffers}
        needed: dict[str, int] = {}
        source = next(item.source for item in self.plan.workload.writes if item.operation_id == write.operation_id)
        needed[buffers[source.buffer_id].resource_id] = 1
        for recipient in write.tree.recipients:
            resource_id = buffers[recipient.buffer_id].resource_id
            needed[resource_id] = needed.get(resource_id, 0) + 1
        return all(needed[resource_id] <= resources[resource_id].service.queue_capacity for resource_id in needed)

    def _service_bytes(self, workload: MulticastSyncWorkload, buffer_id: str, useful: int) -> int:
        buffer = next(item for item in workload.memory.buffers if item.buffer_id == buffer_id)
        resource_id = buffer.resource_id
        resource = next(item for item in workload.memory.resources if item.resource_id == resource_id)
        granule = resource.service.service_granule_bytes
        return ((useful + granule - 1) // granule) * granule

    def _service_cycles(self, workload: MulticastSyncWorkload, buffer_id: str, useful: int) -> float:
        buffer = next(item for item in workload.memory.buffers if item.buffer_id == buffer_id)
        resource = next(item for item in workload.memory.resources if item.resource_id == buffer.resource_id)
        return resource.service.fixed_latency_cycles + useful / resource.service.bytes_per_cycle

    @staticmethod
    def _event(events: list[MulticastMemoryEvent], action: MemoryEventAction, write: MulticastWritePlan, elapsed: float,
               useful: int, service: int, buffer_id: str | None, endpoint_id: str | None) -> None:
        events.append(MulticastMemoryEvent(
            sequence=len(events), time_aci_cycles=elapsed, action=action, operation_id=write.operation_id,
            endpoint_id=endpoint_id, buffer_id=buffer_id, useful_bytes=useful, service_bytes=service,
            physical_bytes=0,
        ))

    @staticmethod
    def _operation(write: MulticastWritePlan, status: OperationStatus, source_useful: int, source_service: int,
                   destination_useful: int, destination_service: int, acknowledgement: int,
                   source_reusable: float | None, ready: float | None, complete: float | None,
                   reason: OperationReason) -> MulticastMemoryOperationRecord:
        return MulticastMemoryOperationRecord(
            operation_id=write.operation_id, completion=write.completion, status=status,
            source_read_useful_bytes=source_useful, source_read_service_bytes=source_service,
            destination_write_useful_bytes=destination_useful, destination_write_service_bytes=destination_service,
            acknowledgement_physical_bytes=acknowledgement, source_reusable_aci_cycles=source_reusable,
            destination_ready_aci_cycles=ready, completion_aci_cycles=complete,
            recipient_count=len(write.tree.recipients), reason=reason,
        )

    def _snapshot(self, index: int, elapsed: float, completed: set[str], pending: tuple[str, ...],
                  buffers: dict[str, MulticastBufferState]) -> MulticastMemorySnapshot:
        return MulticastMemorySnapshot(
            next_operation_index=index, elapsed_aci_cycles=elapsed,
            completed_operation_ids=tuple(sorted(completed)), pending_operation_ids=pending,
            buffer_states=tuple(buffers.values()),
        )

    def transport_snapshot(self, snapshot: MulticastMemorySnapshot | None, index: int,
                           pending: tuple[str, ...]):
        return self.transport_snapshot_type(snapshot, index, pending)

    @staticmethod
    def transport_snapshot_type(snapshot: MulticastMemorySnapshot | None, index: int, pending: tuple[str, ...]):
        from .multicast_transport import TreeTransportSnapshot

        return TreeTransportSnapshot(
            next_operation_index=index,
            next_segment_index=0,
            next_flit_index=0,
            elapsed_aci_cycles=snapshot.elapsed_aci_cycles if snapshot else 0,
            pending_operations=pending,
            active_reservation_ids=(),
        )

    @staticmethod
    def _empty_transport(reason: str, operation_id: str) -> TreeTransportResult:
        from .multicast_transport import TreeTransportSnapshot

        return TreeTransportResult(
            status="incomplete", reason="cycle_limit", elapsed_aci_cycles=0,
            source_read_useful_bytes=0, destination_write_useful_bytes=0,
            packet_physical_bytes=0, launched_channel_bytes=0, reservations=(), events=(),
            pending_operations=(operation_id,), snapshot=TreeTransportSnapshot(
                next_operation_index=0, next_segment_index=0, next_flit_index=0,
                elapsed_aci_cycles=0, pending_operations=(operation_id,), active_reservation_ids=(),
            )
        )
