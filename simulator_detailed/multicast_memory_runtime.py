"""Addressed multicast and ordinary memory execution on one retained session."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Self

import simpy
from pydantic import Field, model_validator
from simpy.events import Event, ProcessGenerator

from .configs.schemas.memory_replay import MemoryOperation, MemoryRange, MemoryVersion
from .configs.schemas.multicast_sync import LocalDataPrerequisite, MulticastSyncWorkload
from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .configs.schemas.torus_replay import Cycles, PacketIdentity
from .memory_resources import (
    AccessLease,
    MemoryAccess,
    MemoryOwnershipEvent,
    MemoryResourcePlan,
    MemoryResources,
    MemoryResourceState,
)
from .memory_service import MemoryChunkRecord, MemoryServiceEvent
from .mixed_compute_runtime import (
    LocalOperationCapability,
    MixedComputeComponent,
    MixedComputeSnapshot,
)
from .multicast_compute_plan import MixedComputePlan
from .multicast_inventory import inventory_id
from .multicast_network import MulticastNetworkPlan
from .multicast_plan import MulticastSyncPlan, MulticastSyncPlanRecord
from .packet_runtime import (
    PacketTransport,
    PacketTransportResult,
    PhysicalTransportRegistry,
)
from .packet_transport import PacketFlit
from .scalar_service import (
    AtomicChunk,
    CounterDefinition,
    CounterHandle,
    CounterState,
    ObservationChunk,
    ScalarServiceRecord,
)
from .tree_runtime import TreeTransport, TreeTransportSnapshot
from .tree_wire import TreeFlit, TreePacketIdentity

SegmentKey = tuple[str, int]
Fact = Literal["submission", "acceptance", "source_read_complete", "handoff", "recipient_effect", "acknowledgement", "complete", "all_effects", "wait_release"]


class MulticastLifecycleEvent(GraphRecord):
    time_aci_cycles: Cycles
    operation_id: Identifier
    segment_index: Index | None
    action: Fact
    resource_id: Identifier | None = None


class MixedDescriptorState(GraphRecord):
    resource_id: Identifier
    kind: Literal["issue", "responder", "local"]
    capacity: PositiveInt
    occupied: Index
    peak_occupied: Index
    owners: tuple[Identifier, ...]

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.occupied != len(self.owners) or not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("mixed descriptor ownership is not conserved")
        return self


class MixedDescriptorEvent(GraphRecord):
    time_aci_cycles: Cycles
    resource_id: Identifier
    kind: Literal["issue", "responder", "local"]
    action: Literal["acquire", "release"]
    owner: Identifier
    occupied: Index


class MulticastExecutionResult(GraphRecord):
    kind: Literal["multicast_sync_result"] = "multicast_sync_result"
    schema_version: int = Field(strict=True, ge=1, le=1, default=1)
    execution_supported: Literal[True] = True
    plan: MulticastSyncPlanRecord
    configuration: MulticastSyncWorkload
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending"]
    elapsed_aci_cycles: Cycles
    pending_operations: tuple[Identifier, ...]
    lifecycle: tuple[MulticastLifecycleEvent, ...]
    tree_transport: TreeTransportSnapshot
    unicast_transport: PacketTransportResult
    memory_resources: tuple[MemoryResourceState, ...]
    released_resources: tuple[MemoryResourceState, ...]
    ownership_trace: tuple[MemoryOwnershipEvent, ...]
    service_trace: tuple[MemoryServiceEvent, ...]
    chunks: tuple[MemoryChunkRecord, ...]
    descriptors: tuple[MixedDescriptorState, ...]
    descriptor_trace: tuple[MixedDescriptorEvent, ...]
    teardown_complete: bool
    source_useful_bytes: Index
    destination_useful_bytes: Index
    physical_channel_bytes: Index
    counters: tuple[CounterState, ...] = ()
    scalar_service: tuple[ScalarServiceRecord, ...] = ()
    inbox_values: tuple[tuple[Identifier, int], ...] = ()
    compute: MixedComputeSnapshot | None = None

    @model_validator(mode="after")
    def conserved(self) -> Self:
        if self.source_useful_bytes != sum(c.useful_bytes for c in self.chunks if c.direction == "read"):
            raise ValueError("source service byte total mismatch")
        if self.destination_useful_bytes != sum(c.useful_bytes for c in self.chunks if c.direction == "write"):
            raise ValueError("destination service byte total mismatch")
        if self.physical_channel_bytes != self.tree_transport.physical_channel_bytes:
            raise ValueError("mixed physical bytes must count the shared serializer launches")
        if (self.status == "complete") != (self.reason == "drained"):
            raise ValueError("only drained mixed work can complete")
        if self.status == "complete" and (self.pending_operations or not self.teardown_complete
                or self.tree_transport.status != "complete" or self.unicast_transport.status != "complete"
                or (self.compute is not None and self.compute.pending)
                or any(d.occupied for d in self.descriptors) or any(r.reserved_bytes for r in self.released_resources)):
            raise ValueError("mixed execution completed before all effects and ownership drained")
        return self


class _Descriptors:
    def __init__(self, session: MulticastMemoryRuntime, resource: str, capacity: int, kind: Literal["issue", "responder", "local"]):
        self.session, self.resource, self.capacity = session, resource, capacity
        self.kind: Literal["issue", "responder", "local"] = kind
        self.owners: set[str] = set()
        self.peak = 0

    @property
    def full(self) -> bool:
        return len(self.owners) == self.capacity

    def acquire(self, owner: str) -> None:
        if self.full or owner in self.owners:
            raise ValueError("descriptor is full or already owned")
        self.owners.add(owner)
        self.peak = max(self.peak, len(self.owners))
        self.log("acquire", owner)

    def release(self, owner: str) -> None:
        self.owners.remove(owner)
        self.log("release", owner)

    def log(self, action: Literal["acquire", "release"], owner: str) -> None:
        self.session.descriptor_events.append(MixedDescriptorEvent(time_aci_cycles=self.session.env.now,
            resource_id=self.resource, kind=self.kind, action=action, owner=owner, occupied=len(self.owners)))
        self.session.notify()

    def snapshot(self) -> MixedDescriptorState:
        return MixedDescriptorState(resource_id=self.resource, kind=self.kind, capacity=self.capacity,
                                    occupied=len(self.owners), peak_occupied=self.peak, owners=tuple(sorted(self.owners)))


@dataclass
class _Segment:
    key: SegmentKey
    source: AccessLease | None = None
    destinations: dict[str, AccessLease] = field(default_factory=lambda: dict[str, AccessLease]())
    completed_destinations: set[str] = field(default_factory=lambda: set[str]())
    source_done: bool = False
    handoff: bool = False
    complete: bool = False


class MulticastMemoryRuntime:
    """One environment, transport registry, memory registry and finalizer.

    Snapshot and advance retain live processes. Completed run() is idempotent;
    only finalization releases admitted buffer reservations.
    """

    def __init__(self, plan: MulticastSyncPlan):
        network = MulticastNetworkPlan.compile(plan)
        self.plan, self.network = plan, network
        self.config = plan.execution_workload
        assert self.config.runtime is not None
        self.settings = self.config.runtime
        resource_plan = MemoryResourcePlan.for_system(self.config.memory, plan.topology, plan_sha256=plan.record.plan_sha256)
        self.env = simpy.Environment()
        self._changed = self.env.event()
        self.memory = MemoryResources(self.env, resource_plan)
        self.registry = PhysicalTransportRegistry(self.env, network.network)
        self.lifecycle: list[MulticastLifecycleEvent] = []
        self.descriptor_events: list[MixedDescriptorEvent] = []
        self._writes = {w.operation_id: w for w in self.config.writes}
        self._ordinary = {o.operation_id: o for o in self.config.operations}
        self._increments = {i.operation_id: i for i in self.config.increments}
        self._waits = {w.wait_id: w for w in self.config.waits}
        self._owners = {w.operation_id: self._endpoint_owner(w.source_endpoint_id) for w in self.config.writes}
        self._owners.update({o.operation_id: self._endpoint_owner(o.initiator_id) for o in self.config.operations})
        self._owners.update({i.operation_id: self._endpoint_owner(i.source_endpoint_id) for i in self.config.increments})
        self._owners.update({w.wait_id: self._endpoint_owner(w.endpoint_id) for w in self.config.waits})
        self._facts = {(operation, action): self.env.event() for operation in self._owners
                       for action in (("complete", "all_effects") if operation in self._waits else
                                      ("handoff", "complete", "all_effects") if operation in self._increments else
                                      ("source_read_complete", "handoff", "complete", "all_effects"))}
        self._segments: dict[SegmentKey, _Segment] = {(p.operation_id, p.segment_index): _Segment((p.operation_id, p.segment_index)) for p in network.trees}
        for item in network.packets.values():
            if item.memory is not None:
                p = item.memory.identity
                self._segments.setdefault((p.operation_id, p.segment_index), _Segment((p.operation_id, p.segment_index)))
        for operation in self._increments:
            self._segments[(operation, 0)] = _Segment((operation, 0))
        self._counter_handles: dict[str, CounterHandle] = {}
        self._counter_owners: dict[str, str] = {}
        self._previous_values: dict[str, int] = {}
        self.inbox_values: list[tuple[str, int]] = []
        for counter in self.config.counters:
            buffer = self.memory.handles[counter.buffer_id].buffer
            granule = self.config.control.atomic_granule_bytes
            assert granule is not None
            owner = self.memory.resources[buffer.resource_id]
            self._counter_owners[counter.counter_id] = buffer.resource_id
            self._counter_handles[counter.counter_id] = owner.service.register_counter(CounterDefinition(
                counter_id=counter.counter_id, address=buffer.base_address + counter.offset_bytes,
                width_bytes=counter.width_bytes, granule_bytes=granule, initial_value=counter.initial_value))
        self._segment_counts = {op: sum(k[0] == op for k in self._segments) for op in self._owners}
        self._counts: dict[tuple[str, str], int] = {}
        inventory = plan.record.inventory
        assert inventory is not None
        self._controls = {c.packet_id: c for c in inventory.controls}
        self._issues = {owner: _Descriptors(self, owner, self.config.memory.max_outstanding_segments, "issue") for owner in sorted(set(self._owners.values()))}
        self._responders = {r.endpoint_id: _Descriptors(self, r.endpoint_id, r.capacity_packets, "responder") for r in inventory.responders}
        self._locals = {owner: _Descriptors(self, owner, self.settings.local_capacity_operations, "local") for owner in sorted(set(self._owners.values()))}
        self.tree = TreeTransport(self.env, network, self.registry, self)
        self.unicast = PacketTransport(self.env, network.network, _UnicastHooks(self), registry=self.registry)
        self._final: MulticastExecutionResult | None = None
        self._before_teardown: tuple[MemoryResourceState, ...] | None = None
        self._activation: dict[str, Event] = {o.operation_id: self.env.event() for o in plan.record.compute.operations} if plan.record.compute else {}
        self._capabilities: dict[tuple[str, str], LocalOperationCapability] = {}
        self._compute_attached = False
        self.compute = MixedComputeComponent(self, plan.record.compute) if plan.record.compute else None
        for packet in network.trees:
            self.env.process(self._multicast(packet))
        for item in network.packets.values():
            if item.memory is not None and item.definition.packet.traffic_class == "request":
                self.env.process(self._ordinary_segment(item.memory.identity.operation_id, item.memory.identity.segment_index))
        for operation in self.config.operations:
            if operation.kind in {"local_read", "local_write", "fence"}:
                self.env.process(self._local(operation))
        for operation in self._increments:
            self.env.process(self._increment(operation))
        for wait in self._waits:
            self.env.process(self._wait(wait))

    @property
    def changed(self) -> Event:
        return self._changed

    def notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def _endpoint_owner(self, endpoint: str) -> str:
        binding = next(e for e in self.config.memory.endpoints if e.endpoint_id == endpoint)
        resources = {r.resource_id: r for r in self.plan.topology.resources}
        return next(r for r in binding.resource_ids if resources[r].kind == "local_sram")

    def _version(self, version: MemoryVersion | None, buffer_id: str) -> MemoryVersion:
        buffer = self.memory.handles[buffer_id].buffer
        if version is None:
            version = MemoryVersion(kind="producer", producer_id=buffer.producer_operation_id) if buffer.producer_operation_id else MemoryVersion(kind="initial")
        if version.kind == "initial":
            return version
        return MemoryVersion(kind="producer", producer_id=inventory_id(version.producer_id, buffer.resource_id))

    def _access(self, operation: str, access: MemoryRange, direction: Literal["read", "write"],
                *, offset: int = 0, size: int | None = None, version: MemoryVersion | None = None) -> tuple[str, MemoryAccess]:
        selected = self._version(MemoryVersion(kind="producer", producer_id=operation) if direction == "write" else version, access.buffer_id)
        return access.buffer_id, MemoryAccess(client_id=operation, direction=direction, offset_bytes=access.offset_bytes + offset,
                                              size_bytes=size if size is not None else access.size_bytes, version=selected)

    def _log(self, operation: str, segment: int | None, action: Fact, resource: str | None = None) -> None:
        self.lifecycle.append(MulticastLifecycleEvent(time_aci_cycles=self.env.now, operation_id=operation,
                                                     segment_index=segment, action=action, resource_id=resource))
        self.notify()

    def _fact(self, key: SegmentKey, action: Literal["source_read_complete", "handoff", "complete", "all_effects"]) -> None:
        self._log(key[0], key[1], action)
        counter = key[0], action
        self._counts[counter] = self._counts.get(counter, 0) + 1
        if self._counts[counter] == self._segment_counts[key[0]]:
            self._facts[counter].succeed()
            self._log(key[0], None, action)

    def _local_ready(self, prerequisite: LocalDataPrerequisite) -> ProcessGenerator:
        access = prerequisite.access
        handle = self.memory.handles[access.buffer_id]
        resource = self.memory.resources[handle.buffer.resource_id]
        version = self._version(prerequisite.version, access.buffer_id)
        while not resource.is_ready(handle, offset_bytes=access.offset_bytes, size_bytes=access.size_bytes, version=version):
            yield resource.changed

    def attach_compute(self, plan: MixedComputePlan) -> dict[str, LocalOperationCapability]:
        if self._compute_attached or plan != self.plan.record.compute:
            raise ValueError("duplicate or unadmitted compute capacity attachment")
        self._compute_attached = True
        return {o.operation_id: self.local_operation(self._owners[o.operation_id], o.operation_id) for o in plan.operations}

    def local_operation(self, resource_id: str, operation: str) -> LocalOperationCapability:
        if self._owners.get(operation) != resource_id:
            raise ValueError("local capability cannot expose remote completion")
        key = resource_id, operation
        if key not in self._capabilities:
            self._capabilities[key] = LocalOperationCapability(operation, resource_id)
        return self._capabilities[key]

    def _check_capability(self, capability: LocalOperationCapability) -> str:
        if self._capabilities.get((capability.resource_id, capability.operation_id)) is not capability:
            raise ValueError("foreign or forged local lifecycle capability")
        return capability.operation_id

    def activate_local(self, capability: LocalOperationCapability) -> None:
        operation = self._check_capability(capability)
        gate = self._activation.get(operation)
        if gate is None or gate.triggered:
            raise ValueError("operation is not an inactive admitted compute stage")
        gate.succeed()

    def local_completion(self, capability: LocalOperationCapability) -> Event:
        return self._facts[self._check_capability(capability), "complete"]

    def wait_local_prerequisites(self, capability: LocalOperationCapability) -> ProcessGenerator:
        yield from self._prerequisites(self._check_capability(capability))

    def _dependencies(self, operation: str) -> ProcessGenerator:
        if operation in self._activation:
            yield self._activation[operation]
        yield from self._prerequisites(operation)

    def _prerequisites(self, operation: str) -> ProcessGenerator:
        declaration = self._writes.get(operation) or self._increments.get(operation) or self._ordinary[operation]
        for parent in declaration.depends_on:
            yield self._facts[parent, "complete"]
        if isinstance(declaration, MemoryOperation):
            if declaration.start_aci_cycles > self.env.now:
                yield self.env.timeout(declaration.start_aci_cycles - self.env.now)
            for parent in declaration.destination_ready_after:
                # Check only publications at the admitted local resource.
                inventory = self.plan.record.inventory
                assert inventory is not None
                for access in inventory.accesses:
                    if access.operation_id == parent and access.direction == "write" and access.resource_id == self._owners[operation]:
                        yield from self._local_ready(LocalDataPrerequisite(access=MemoryRange(buffer_id=access.buffer_id,
                            offset_bytes=access.offset_bytes, size_bytes=access.size_bytes), version=MemoryVersion(kind="producer", producer_id=parent)))
        for gate in self.config.gates:
            if gate.operation_id == operation:
                for wait in gate.after_waits:
                    yield self._facts[wait, "complete"]
                for prerequisite in gate.local_data:
                    yield from self._local_ready(prerequisite)

    def _bundle(self, key: SegmentKey, requests: tuple[tuple[str, MemoryAccess], ...], responders: tuple[str, ...],
                *, local: bool = False) -> ProcessGenerator:
        pool = (self._locals if local else self._issues)[self._owners[key[0]]]
        responder_pools = tuple(self._responders[e] for e in responders)
        while True:
            leases = None
            if not pool.full and all(not p.full for p in responder_pools):
                leases = self.memory.try_acquire_bundle(requests)
            if leases is not None:
                token = inventory_id(*key)
                pool.acquire(token)
                for response in responder_pools:
                    response.acquire(token)
                self._log(key[0], key[1], "acceptance")
                return leases
            yield self.env.any_of([self.changed, *(r.changed for r in self.memory.resources.values())])

    def _multicast(self, packet: TreePacketIdentity) -> ProcessGenerator:
        key = packet.operation_id, packet.segment_index
        state, definition = self._segments[key], self.network.trees[packet]
        declaration = self._writes[packet.operation_id]
        segment = definition.write.segments[packet.segment_index]
        offset = segment.source_offset_bytes - declaration.source.offset_bytes
        self._log(key[0], key[1], "submission")
        yield from self._dependencies(key[0])
        requests = (self._access(key[0], declaration.source, "read", offset=offset, size=segment.payload_bytes, version=declaration.source_version),
                    *(self._access(key[0], MemoryRange(buffer_id=r.buffer_id, offset_bytes=r.offset_bytes, size_bytes=declaration.size_bytes),
                                   "write", offset=offset, size=segment.payload_bytes) for r in definition.write.tree.recipients))
        responders = tuple(r.endpoint_id for r in definition.write.tree.recipients) if declaration.completion == "write_acknowledged" else ()
        leases = yield from self._bundle(key, requests, responders)
        state.source = leases[0]
        state.destinations = {r.endpoint_id: lease for r, lease in zip(definition.write.tree.recipients, leases[1:], strict=True)}
        yield self.env.timeout(self.config.memory.issue_latency_aci_cycles)
        while not self.tree.try_submit(packet):
            yield self.tree.changed
        yield self.tree.handoffs[packet]
        state.handoff = True
        self._fact(key, "handoff")
        if responders:
            for control in self._controls.values():
                if control.operation_id == key[0] and control.segment_index == key[1]:
                    yield self.unicast.receipt(PacketIdentity(transfer_id=control.packet_id, traffic_class="response"))
        state.complete = True
        self._fact(key, "complete")
        self._issues[self._owners[key[0]]].release(inventory_id(*key))

    def produce(self, flit: TreeFlit) -> ProcessGenerator:
        if flit.payload_bytes:
            key = flit.packet.operation_id, flit.packet.segment_index
            layout = self.network.trees[flit.packet].layout
            yield from self._service(key, "source", (flit.flit_index - layout.header_flits) * layout.data_capacity_bytes, flit.payload_bytes)

    def consume(self, flit: TreeFlit, endpoint_id: str) -> ProcessGenerator:
        key = flit.packet.operation_id, flit.packet.segment_index
        if flit.flit_index == 0:
            yield self.env.timeout(self.settings.request_control_aci_cycles)
        if flit.payload_bytes:
            layout = self.network.trees[flit.packet].layout
            yield from self._service(key, endpoint_id, (flit.flit_index - layout.header_flits) * layout.data_capacity_bytes, flit.payload_bytes)
        if flit.is_tail:
            control = next((c for c in self._controls.values() if c.operation_id == key[0] and c.segment_index == key[1]
                            and c.route.source == endpoint_id), None)
            if control is not None:
                self.env.process(self._respond(key, PacketIdentity(transfer_id=control.packet_id, traffic_class="response"), endpoint_id))

    def _service(self, key: SegmentKey, side: str, offset: int, size: int) -> ProcessGenerator:
        state = self._segments[key]
        lease = state.source if side == "source" else state.destinations[side]
        assert lease is not None
        owner = self.memory.resources[lease.handle.buffer.resource_id]
        chunk = owner.definition.timing.config.chunk_bytes
        for start in range(offset, offset + size, chunk):
            done: Event | None = None
            while done is None:
                done = owner.try_service(lease, service_id=inventory_id(*key, side, start), offset_bytes=start,
                                         size_bytes=min(chunk, offset + size - start))
                if done is None:
                    yield owner.service.changed
            yield done
        if owner.access_complete(lease):
            owner.release_access(lease)
            if side == "source":
                state.source, state.source_done = None, True
                self._fact(key, "source_read_complete")
            else:
                del state.destinations[side]
                state.completed_destinations.add(side)
                self._log(key[0], key[1], "recipient_effect", owner.resource_id)
                if not state.destinations and key[0] not in self._increments:
                    self._fact(key, "all_effects")

    def _respond(self, key: SegmentKey, packet: PacketIdentity, endpoint: str) -> ProcessGenerator:
        # The caller starts this process after its final service; its receive
        # frame returns and releases request credits before this process runs.
        while not self.unicast.try_submit(packet):
            yield self.unicast.changed
        yield self.unicast.handoff(packet)
        self._responders[endpoint].release(inventory_id(*key))

    def _ordinary_segment(self, operation: str, segment: int) -> ProcessGenerator:
        key, declaration = (operation, segment), self._ordinary[operation]
        items = [i for i in self.network.packets.values() if i.memory is not None
                 and (i.memory.identity.operation_id, i.memory.identity.segment_index) == key]
        request = next(i for i in items if i.definition.packet.traffic_class == "request")
        response = next((i for i in items if i.definition.packet.traffic_class == "response"), None)
        assert request.memory is not None and declaration.source is not None and declaration.destination is not None
        part, state = request.memory.segment, self._segments[key]
        self._log(operation, segment, "submission")
        yield from self._dependencies(operation)
        requests = (self._access(operation, declaration.source, "read", offset=part.offset_bytes, size=part.logical_bytes, version=declaration.source_version),
                    self._access(operation, declaration.destination, "write", offset=part.offset_bytes, size=part.logical_bytes))
        responders = (request.definition.route.destination,) if response is not None else ()
        leases = yield from self._bundle(key, requests, responders)
        state.source, state.destinations = leases[0], {"destination": leases[1]}
        yield self.env.timeout(self.config.memory.issue_latency_aci_cycles)
        while not self.unicast.try_submit(request.definition.packet):
            yield self.unicast.changed
        yield self.unicast.handoff(request.definition.packet)
        state.handoff = True
        self._fact(key, "handoff")
        if response is not None:
            yield self.unicast.receipt(response.definition.packet)
        state.complete = True
        self._fact(key, "complete")
        self._issues[self._owners[operation]].release(inventory_id(*key))

    def unicast_produce(self, flit: PacketFlit) -> ProcessGenerator:
        item = self.network.packets[flit.packet]
        if flit.payload_bytes:
            assert item.memory is not None
            key = item.memory.identity.operation_id, item.memory.identity.segment_index
            yield from self._service(key, "source", (flit.flit_index - item.layout.header_flits) * item.layout.data_capacity_bytes, flit.payload_bytes)

    def unicast_consume(self, flit: PacketFlit) -> ProcessGenerator:
        item = self.network.packets[flit.packet]
        if flit.flit_index == 0:
            yield self.env.timeout(self.settings.request_control_aci_cycles if flit.packet.traffic_class == "request" else self.settings.response_control_aci_cycles)
        if item.control_id is not None:
            control = self._controls[item.control_id]
            if flit.is_tail:
                if control.purpose == "multicast_ack":
                    self._log(control.operation_id, control.segment_index, "acknowledgement", self._owners[control.operation_id])
                elif control.purpose == "atomic_request":
                    yield from self._atomic_effect(control.operation_id)
                else:
                    increment = self._increments[control.operation_id]
                    assert increment.return_inbox is not None
                    yield from self._service((increment.operation_id, 0), "inbox", 0, increment.return_inbox.size_bytes)
                    self.inbox_values.append((increment.operation_id, self._previous_values[increment.operation_id]))
                    self._log(increment.operation_id, 0, "acknowledgement", self._owners[increment.operation_id])
            return
        assert item.memory is not None
        key = item.memory.identity.operation_id, item.memory.identity.segment_index
        if flit.payload_bytes:
            yield from self._service(key, "destination", (flit.flit_index - item.layout.header_flits) * item.layout.data_capacity_bytes, flit.payload_bytes)
        if flit.is_tail and flit.packet.traffic_class == "request":
            response = next((p for p, i in self.network.packets.items() if i.definition.after_packet == flit.packet), None)
            if response is not None:
                self.env.process(self._respond_ordinary(key, flit.packet, response, flit.destination))

    def _respond_ordinary(self, key: SegmentKey, request: PacketIdentity, response: PacketIdentity, endpoint: str) -> ProcessGenerator:
        yield self.unicast.receipt(request)
        yield from self._respond(key, response, endpoint)

    def _local(self, operation: MemoryOperation) -> ProcessGenerator:
        key = operation.operation_id, 0
        self._log(key[0], None, "submission")
        yield from self._dependencies(key[0])
        if operation.kind == "fence":
            for parent in operation.fence_operations:
                yield self._facts[parent, "handoff" if operation.fence_mode == "local_handoff" else "complete"]
        else:
            access = operation.source if operation.kind == "local_read" else operation.destination
            assert access is not None
            direction = "read" if operation.kind == "local_read" else "write"
            leases = yield from self._bundle(key, (self._access(key[0], access, direction, version=operation.source_version),), (), local=True)
            lease = leases[0]
            owner = self.memory.resources[lease.handle.buffer.resource_id]
            yield self.env.timeout(self.settings.local_control_aci_cycles)
            for offset in range(0, access.size_bytes, owner.definition.timing.config.chunk_bytes):
                done = owner.try_service(lease, service_id=inventory_id(*key, "local", offset), offset_bytes=offset,
                                         size_bytes=min(owner.definition.timing.config.chunk_bytes, access.size_bytes - offset))
                while done is None:
                    yield owner.service.changed
                    done = owner.try_service(lease, service_id=inventory_id(*key, "local", offset), offset_bytes=offset,
                                             size_bytes=min(owner.definition.timing.config.chunk_bytes, access.size_bytes - offset))
                yield done
            owner.release_access(lease)
            self._locals[self._owners[key[0]]].release(inventory_id(*key))
        for action in ("source_read_complete", "handoff", "all_effects", "complete"):
            self._facts[key[0], action].succeed()
        self._log(key[0], None, "complete")

    def _increment(self, operation: str) -> ProcessGenerator:
        increment, key = self._increments[operation], (operation, 0)
        self._log(operation, 0, "submission")
        yield from self._dependencies(operation)
        request = next(c for c in self._controls.values() if c.operation_id == operation and c.purpose == "atomic_request")
        response = next((c for c in self._controls.values() if c.operation_id == operation and c.purpose == "atomic_return"), None)
        requests = () if increment.return_inbox is None else (self._access(operation, increment.return_inbox, "write"),)
        leases = yield from self._bundle(key, requests, (request.route.destination,) if response is not None else ())
        if leases:
            self._segments[key].destinations["inbox"] = leases[0]
        yield self.env.timeout(self.config.memory.issue_latency_aci_cycles)
        packet = PacketIdentity(transfer_id=request.packet_id, traffic_class="request")
        while not self.unicast.try_submit(packet):
            yield self.unicast.changed
        yield self.unicast.handoff(packet)
        self._fact(key, "handoff")
        if response is not None:
            yield self.unicast.receipt(PacketIdentity(transfer_id=response.packet_id, traffic_class="response"))
        self._fact(key, "complete")
        self._issues[self._owners[operation]].release(inventory_id(*key))

    def _atomic_effect(self, operation: str) -> ProcessGenerator:
        increment = self._increments[operation]
        owner = self.memory.resources[self._counter_owners[increment.counter_id]]
        handle = self._counter_handles[increment.counter_id]
        job = AtomicChunk(service_id=inventory_id(operation, "atomic"), client_id=operation,
                          counter_id=increment.counter_id, native_cycles=self.config.control.atomic_native_cycles)
        done = owner.service.try_scalar(handle, job)
        while done is None:
            yield owner.service.changed
            done = owner.service.try_scalar(handle, job)
        record: object = yield done
        assert isinstance(record, ScalarServiceRecord)
        self._previous_values[operation] = record.old_value
        self._log(operation, 0, "recipient_effect", owner.resource_id)
        self._fact((operation, 0), "all_effects")
        response = next((c for c in self._controls.values() if c.operation_id == operation and c.purpose == "atomic_return"), None)
        if response is not None:
            self.env.process(self._respond((operation, 0), PacketIdentity(transfer_id=response.packet_id, traffic_class="response"), response.route.source))

    def _wait(self, wait_id: str) -> ProcessGenerator:
        wait = self._waits[wait_id]
        owner = self.memory.resources[self._counter_owners[wait.counter_id]]
        handle = self._counter_handles[wait.counter_id]
        observation = 0
        while True:
            # Subscribe before queued observation service. An update during that
            # service cannot be lost; the returned value is sampled at its end.
            changed = owner.service.counter_changed(handle)
            job = ObservationChunk(service_id=inventory_id(wait_id, "observation", observation), client_id=wait_id,
                                   counter_id=wait.counter_id, control_aci_cycles=self.config.control.local_observation_aci_cycles)
            done = owner.service.try_scalar(handle, job)
            while done is None:
                yield owner.service.changed
                done = owner.service.try_scalar(handle, job)
            record: object = yield done
            assert isinstance(record, ScalarServiceRecord)
            observation += 1
            if record.new_value >= wait.threshold:
                for prerequisite in wait.local_data:
                    yield from self._local_ready(prerequisite)
                self._facts[wait_id, "complete"].succeed()
                self._facts[wait_id, "all_effects"].succeed()
                self._log(wait_id, None, "wait_release", owner.resource_id)
                return
            if not changed.triggered:
                yield changed

    def _resources(self) -> tuple[MemoryResourceState, ...]:
        return tuple(r.snapshot() for r in self.memory.resources.values())

    def is_drained(self) -> bool:
        return ((self.compute is None or self.compute.is_drained) and all(e.triggered for e in self._facts.values()) and self.memory.is_drained and self.registry.is_drained
                and not self.tree.snapshot().pending and not self.unicast.snapshot(require_idle_environment=False).pending_packets
                and all(not p.owners for pools in (self._issues, self._responders, self._locals) for p in pools.values()))

    def advance(self, *, max_aci_cycles: float | None = None) -> MulticastExecutionResult:
        if self._final is not None:
            return self._final
        horizon = self.config.memory.max_aci_cycles if max_aci_cycles is None else max_aci_cycles
        if not math.isfinite(horizon) or horizon <= self.env.now:
            raise ValueError("mixed horizon must be finite and later than current time")
        while self.env.peek() != float("inf") and self.env.peek() <= horizon:
            self.env.step()
        return self.snapshot()

    def run(self, *, max_aci_cycles: float | None = None) -> MulticastExecutionResult:
        if self._final is not None:
            return self._final
        self.advance(max_aci_cycles=max_aci_cycles)
        return self.finalize() if self.is_drained() else self.snapshot()

    def finalize(self) -> MulticastExecutionResult:
        if self._final is not None:
            return self._final
        if not self.is_drained():
            raise ValueError("mixed effects and resources have not drained")
        self._before_teardown = self._resources()
        self.memory.teardown()
        self._final = self.snapshot()
        return self._final

    def snapshot(self) -> MulticastExecutionResult:
        if self._final is not None:
            return self._final
        tree = self.tree.snapshot()
        unicast = self.unicast.snapshot(memory_service="external_hooks", require_idle_environment=False)
        pending = tuple(op for op in self._owners if not (self._facts[op, "complete"].triggered and self._facts[op, "all_effects"].triggered))
        final = self._before_teardown is not None
        complete = final and not pending and self.registry.is_drained
        chunks = tuple(c for r in self.memory.resources.values() for c in r.service.records)
        return MulticastExecutionResult(plan=self.plan.record, configuration=self.plan.workload, compute=self.compute.snapshot() if self.compute else None, status="complete" if complete else "incomplete",
            counters=tuple(self.memory.resources[self._counter_owners[c]].service.counter_state(h) for c, h in self._counter_handles.items()),
            scalar_service=tuple(r for owner in self.memory.resources.values() for r in owner.service.scalar_records),
            inbox_values=tuple(self.inbox_values),
            reason="drained" if complete else "idle_with_pending" if self.env.peek() == float("inf") else "cycle_limit",
            elapsed_aci_cycles=self.env.now, pending_operations=pending, lifecycle=tuple(self.lifecycle), tree_transport=tree,
            unicast_transport=unicast, memory_resources=self._before_teardown or self._resources(),
            released_resources=self._resources() if final else (), ownership_trace=tuple(e for r in self.memory.resources.values() for e in r.events),
            service_trace=tuple(e for r in self.memory.resources.values() for e in r.service.events), chunks=chunks,
            descriptors=tuple(p.snapshot() for pools in (self._issues, self._responders, self._locals) for p in pools.values()),
            descriptor_trace=tuple(self.descriptor_events), teardown_complete=final,
            source_useful_bytes=sum(c.useful_bytes for c in chunks if c.direction == "read"),
            destination_useful_bytes=sum(c.useful_bytes for c in chunks if c.direction == "write"), physical_channel_bytes=tree.physical_channel_bytes)


class _UnicastHooks:
    def __init__(self, session: MulticastMemoryRuntime):
        self.session, self.env = session, session.env

    def produce(self, flit: PacketFlit) -> ProcessGenerator:
        yield from self.session.unicast_produce(flit)

    def consume(self, flit: PacketFlit) -> ProcessGenerator:
        yield from self.session.unicast_consume(flit)
