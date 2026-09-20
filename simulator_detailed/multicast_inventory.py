"""Pure mixed-operation admission; no environments, events or output files.

The reservation policy is a model assumption. Its dependency proof is separate
from the unicast dateline ranks and does not claim a hardware multicast VC map.
"""

from __future__ import annotations

import json
import math
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, Literal

from .configs.schemas.memory_replay import MemoryRange, MemoryVersion
from .configs.schemas.multicast_sync import LocalDataPrerequisite, MulticastSyncWorkload
from .configs.schemas.topology import (
    CanonicalTopology,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
)
from .configs.schemas.torus_replay import ChannelIdentity, RouteRecord
from .memory_routes import MemoryOperationRoutes, operation_routes
from .memory_transport import validate_transport_settings
from .torus import TorusRouting
from .torus_dependencies import ResourceDependencies

if TYPE_CHECKING:
    from .multicast_plan import MulticastWritePlan


def inventory_id(*parts: object) -> str:
    """Tuple encoding avoids collisions with user IDs and separator characters."""
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


class MixedAccess(GraphRecord):
    operation_id: Identifier
    resource_id: Identifier
    buffer_id: Identifier
    offset_bytes: Index
    address_bytes: Index
    size_bytes: PositiveInt
    direction: Literal["read", "write", "atomic"]
    version: MemoryVersion | None = None


class ControlPacketPlan(GraphRecord):
    packet_id: Identifier
    operation_id: Identifier
    segment_index: Index
    purpose: Literal["multicast_ack", "atomic_request", "atomic_return"]
    route: RouteRecord
    inline_bytes: Index
    flit_count: PositiveInt = 1
    physical_bytes: PositiveInt
    planned_channel_bytes: PositiveInt
    after_effect: Identifier | None = None


class TreeChannelPlan(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    channels: tuple[ChannelIdentity, ...]
    planned_channel_bytes: PositiveInt


class ResponseReservationPlan(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    endpoint_id: Identifier
    capacity_packets: PositiveInt


class MixedDependency(GraphRecord):
    before: Identifier
    after: Identifier
    kind: Literal["completion", "local_data", "local_wait", "producer_proof"]
    resource_id: Identifier


class MixedInventory(GraphRecord):
    operation_order: tuple[Identifier, ...]
    controls: tuple[ControlPacketPlan, ...]
    tree_channels: tuple[TreeChannelPlan, ...]
    responders: tuple[ResponseReservationPlan, ...]
    ordinary_routes: tuple[MemoryOperationRoutes, ...]
    accesses: tuple[MixedAccess, ...]
    dependencies: tuple[MixedDependency, ...]
    assumptions: tuple[str, ...] = (
        "atomic_tree_reservation_v1 grants all tree lanes or none; waiters own no tree lanes",
        "response descriptors and all access leases are provisioned before tree acquisition",
        "acyclic tree grants drain all delayed credits before release; response traffic uses separate lanes",
        "shared physical grants provide finite service and never wait for downstream storage",
        "finite admitted endpoint service does not wait for a later signal or network request",
    )


def compile_inventory(workload: MulticastSyncWorkload, graph: CanonicalTopology,
                      writes: tuple[MulticastWritePlan, ...]) -> MixedInventory:
    return _Admission(workload, graph, writes).compile()


class _Admission:
    def __init__(self, workload: MulticastSyncWorkload, graph: CanonicalTopology,
                 writes: tuple[MulticastWritePlan, ...]):
        self.workload, self.graph, self.writes = workload, graph, writes
        self.memory = workload.memory
        self.buffers = {b.buffer_id: b for b in self.memory.buffers}
        self.resources = {r.resource_id: r for r in graph.resources}
        self.endpoints = {e.endpoint_id: e for e in self.memory.endpoints}
        self.attachments = {e.endpoint_id: e for e in graph.attachments}
        self.routers = {r.key: r for r in graph.routers}
        self.owners: dict[str, str] = {}
        self.parents: dict[str, set[str]] = {}
        self.dependencies: list[MixedDependency] = []
        self.accesses: list[MixedAccess] = []
        self.controls: list[ControlPacketPlan] = []
        self.responders: list[ResponseReservationPlan] = []
        self.routes: list[MemoryOperationRoutes] = []
        self.tree_channels: list[TreeChannelPlan] = []
        self.routing = TorusRouting.from_graph(graph, self.memory.routing)

    def compile(self) -> MixedInventory:
        runtime = self.workload.runtime
        if runtime is None:
            raise ValueError("mixed execution admission requires explicit runtime settings")
        if (self.memory.endpoint_queue_capacity_packets, self.memory.endpoint_staging_capacity_flits) != (
                runtime.transport.endpoint_queue_capacity_packets, runtime.transport.endpoint_staging_capacity_flits):
            raise ValueError("mixed memory and transport endpoint capacities must agree")
        validate_transport_settings(self.graph, runtime.transport,
                                    physical_flit_bytes=self.memory.packet.physical_flit_bytes,
                                    aci_clock_hz=self.memory.aci_clock_hz)
        for write in self.workload.writes:
            self.register(write.operation_id, write.source_endpoint_id)
        for increment in self.workload.increments:
            self.register(increment.operation_id, increment.source_endpoint_id)
        for operation in self.workload.operations:
            self.register(operation.operation_id, operation.initiator_id)
        for wait in self.workload.waits:
            self.register(wait.wait_id, wait.endpoint_id, initiate=False)
        self.multicast()
        self.ordinary()
        self.scalar()
        self.dependency_graph()
        try:
            order = tuple(TopologicalSorter({k: sorted(v) for k, v in self.parents.items()}).static_order())
        except CycleError as error:
            raise ValueError("mixed operation/wait/data dependencies contain a cycle") from error
        ancestors: dict[str, set[str]] = {}
        for node in order:
            ancestors[node] = set(self.parents[node])
            for parent in self.parents[node]:
                ancestors[node].update(ancestors[parent])
        self.conflicts(ancestors)
        self.read_versions(ancestors)
        self.phase_proofs(ancestors)
        paths = [packet.route.hops for packet in self.controls]
        for operation in self.routes:
            paths.append(operation.request.hops)
            if operation.response is not None:
                paths.append(operation.response.hops)
        ResourceDependencies.from_paths(paths)
        return MixedInventory(operation_order=order, controls=tuple(self.controls),
                              tree_channels=tuple(self.tree_channels), responders=tuple(self.responders),
                              ordinary_routes=tuple(self.routes), accesses=tuple(self.accesses),
                              dependencies=tuple(self.dependencies))

    def owner(self, endpoint_id: str, *, initiate: bool = False) -> str:
        endpoint = self.endpoints.get(endpoint_id)
        attachment = self.attachments.get(endpoint_id)
        if endpoint is None or attachment is None or not endpoint.enabled or not attachment.permissions_resolved:
            raise ValueError("mixed operation has an unavailable endpoint")
        router = self.routers[(endpoint.fabric_id, endpoint.router_id)]
        if (router.enabled is not True or router.tile_id not in self.graph.enabled_worker_ids
                or not attachment.enabled or not attachment.replay_enabled):
            raise ValueError("mixed control must belong to an enabled worker")
        if initiate and ("initiator" not in endpoint.roles or attachment.inject_port is None):
            raise ValueError("mixed operation requires an enabled initiator")
        candidates = [r for r in endpoint.resource_ids if self.resources[r].kind == "local_sram"
                      and self.resources[r].owner_tile_id == router.tile_id]
        if len(candidates) != 1:
            raise ValueError("mixed control endpoint must expose exactly one owned L1")
        return candidates[0]

    def register(self, operation: str, endpoint: str, *, initiate: bool = True) -> None:
        self.owners[operation] = self.owner(endpoint, initiate=initiate)
        self.parents[operation] = set()

    def access(self, operation: str, access: MemoryRange, direction: Literal["read", "write", "atomic"],
               version: MemoryVersion | None = None) -> MixedAccess:
        buffer = self.buffers.get(access.buffer_id)
        if buffer is None or access.offset_bytes + access.size_bytes > buffer.size_bytes:
            raise ValueError("mixed access exceeds a declared buffer")
        if direction in {"read", "atomic"} and not buffer.readable:
            raise ValueError("mixed access requires read permission")
        if direction in {"write", "atomic"} and not buffer.writable:
            raise ValueError("mixed access requires write permission")
        result = MixedAccess(operation_id=operation, resource_id=buffer.resource_id, buffer_id=buffer.buffer_id,
                             offset_bytes=access.offset_bytes, address_bytes=buffer.base_address + access.offset_bytes,
                             size_bytes=access.size_bytes, direction=direction, version=version)
        self.accesses.append(result)
        return result

    def responder(self, operation: str, segment: int, endpoint: str) -> None:
        runtime = self.workload.runtime
        assert runtime is not None
        self.responders.append(ResponseReservationPlan(operation_id=operation, segment_index=segment,
                                                       endpoint_id=endpoint,
                                                       capacity_packets=runtime.responder_capacity_packets))

    def control(self, operation: str, segment: int, purpose: Literal["multicast_ack", "atomic_request", "atomic_return"],
                route: RouteRecord, inline: int, effect: str | None = None) -> None:
        flits = self.memory.packet.header_flits if purpose == "multicast_ack" else 1
        physical = self.memory.packet.physical_flit_bytes * flits
        self.controls.append(ControlPacketPlan(
            packet_id=inventory_id(operation, segment, purpose, route.source, route.destination),
            operation_id=operation, segment_index=segment, purpose=purpose, route=route,
            inline_bytes=inline, flit_count=flits, physical_bytes=physical, planned_channel_bytes=len(route.hops) * physical,
            after_effect=effect))

    def multicast(self) -> None:
        for declaration, plan in zip(self.workload.writes, self.writes, strict=True):
            source = self.access(declaration.operation_id, declaration.source, "read", declaration.source_version)
            if source.resource_id != self.owners[declaration.operation_id]:
                raise ValueError("multicast source must be local")
            for recipient in plan.tree.recipients:
                self.access(declaration.operation_id, MemoryRange(buffer_id=recipient.buffer_id,
                            offset_bytes=recipient.offset_bytes, size_bytes=declaration.size_bytes), "write")
            channels = (ChannelIdentity(fabric_id=plan.fabric_id, kind="inject", identity=plan.source_endpoint_id),
                        *(ChannelIdentity(fabric_id=plan.fabric_id, kind="network", identity=e.link_id) for e in plan.tree.edges),
                        *(ChannelIdentity(fabric_id=plan.fabric_id, kind="eject", identity=r.endpoint_id)
                          for r in plan.tree.recipients))
            for segment in plan.segments:
                self.tree_channels.append(TreeChannelPlan(operation_id=plan.operation_id, segment_index=segment.segment_index,
                    channels=channels, planned_channel_bytes=len(channels) * segment.physical_bytes))
                if declaration.completion == "write_acknowledged":
                    if "response_sink" not in self.endpoints[plan.source_endpoint_id].roles:
                        raise ValueError("acknowledged multicast requires a source response sink")
                    for recipient in plan.tree.recipients:
                        route = self.routing.endpoint_path(plan.fabric_id, recipient.endpoint_id, plan.source_endpoint_id, "response")
                        self.control(plan.operation_id, segment.segment_index, "multicast_ack", route, 0,
                                     inventory_id(plan.operation_id, segment.segment_index, recipient.endpoint_id, "effect"))
                        self.responder(plan.operation_id, segment.segment_index, recipient.endpoint_id)

    def ordinary(self) -> None:
        for operation in self.workload.operations:
            if not math.isfinite(operation.start_aci_cycles + self.memory.issue_latency_aci_cycles):
                raise ValueError("unrepresentable mixed issue duration")
            if operation.kind == "fence":
                # Fence legality is resolved against the complete mixed inventory below.
                continue
            accesses: list[MixedAccess] = []
            if operation.source is not None:
                accesses.append(self.access(operation.operation_id, operation.source, "read", operation.source_version))
            if operation.destination is not None:
                accesses.append(self.access(operation.operation_id, operation.destination, "write"))
            if operation.kind.startswith("local_"):
                if any(a.resource_id != self.owners[operation.operation_id] for a in accesses):
                    raise ValueError("local memory operation cannot access remote memory")
            else:
                route = operation_routes(self.memory, self.graph, self.routing, operation)
                self.routes.append(route)
                if route.response is not None:
                    assert operation.source is not None
                    count = (operation.source.size_bytes + self.memory.packet.max_segment_payload_bytes - 1) // self.memory.packet.max_segment_payload_bytes
                    for segment in range(count):
                        self.responder(operation.operation_id, segment, route.request.destination)

    def scalar(self) -> None:
        if not (self.workload.counters or self.workload.increments or self.workload.waits):
            return
        control = self.workload.control
        granule, inline = control.atomic_granule_bytes, control.inline_control_bytes
        if granule is None or inline is None or inline > self.memory.packet.physical_flit_bytes:
            raise ValueError("scalar execution requires explicit atomic granule and inline control geometry")
        counters = {c.counter_id: c for c in self.workload.counters}
        payload_buffers = {a.buffer_id for a in self.accesses}
        counter_resources: dict[str, str] = {}
        counter_buffers: set[str] = set()
        for counter in counters.values():
            buffer = self.buffers[counter.buffer_id]
            address = buffer.base_address + counter.offset_bytes
            owner = self.owner(counter.endpoint_id)
            if (buffer.resource_id != owner or buffer.base_address % granule or buffer.size_bytes != granule
                    or counter.offset_bytes + counter.width_bytes > granule or address % counter.width_bytes
                    or granule % counter.width_bytes or counter.width_bytes > inline):
                raise ValueError("counter requires a dedicated aligned atomic granule and fitting inline word")
            if counter.buffer_id in payload_buffers or counter.buffer_id in counter_buffers:
                raise ValueError("atomic granules must be disjoint from payload buffers and other counters")
            counter_resources[counter.counter_id] = owner
            counter_buffers.add(counter.buffer_id)
        inboxes: list[MixedAccess] = []
        for increment in self.workload.increments:
            counter = counters[increment.counter_id]
            owner = counter_resources[counter.counter_id]
            targets = [e for e in self.memory.endpoints if e.fabric_id == increment.fabric_id and e.enabled
                       and "target" in e.roles and owner in e.resource_ids]
            if len(targets) != 1:
                raise ValueError("atomic request requires exactly one target alias on its fabric")
            target = targets[0]
            request = self.routing.endpoint_path(increment.fabric_id, increment.source_endpoint_id, target.endpoint_id, "request")
            self.control(increment.operation_id, 0, "atomic_request", request, counter.width_bytes)
            self.access(increment.operation_id, MemoryRange(buffer_id=counter.buffer_id, offset_bytes=0, size_bytes=granule), "atomic")
            if increment.completion == "atomic_returning":
                inbox = increment.return_inbox
                if inbox is None or inbox.size_bytes != counter.width_bytes:
                    raise ValueError("returning increment requires an explicit word-sized inbox")
                access = self.access(increment.operation_id, inbox, "write")
                if (access.resource_id != self.owners[increment.operation_id] or access.address_bytes % counter.width_bytes
                        or inbox.buffer_id in counter_buffers or inbox.buffer_id in payload_buffers
                        or any(_overlap(access, old) for old in inboxes)):
                    raise ValueError("atomic return inbox must be local, aligned and disjoint")
                inboxes.append(access)
                response = self.routing.endpoint_path(increment.fabric_id, target.endpoint_id, increment.source_endpoint_id, "response")
                self.control(increment.operation_id, 0, "atomic_return", response, counter.width_bytes,
                             inventory_id(increment.operation_id, "linearization"))
                self.responder(increment.operation_id, 0, target.endpoint_id)
        for wait in self.workload.waits:
            if self.owners[wait.wait_id] != counter_resources[wait.counter_id]:
                raise ValueError("threshold wait cannot peek at a remote counter")
            if wait.data_ready_after:
                raise ValueError("execution waits require explicit local_data extents and versions")

    def edge(self, before: str, after: str, kind: Literal["completion", "local_data", "local_wait", "producer_proof"],
             resource: str) -> None:
        if before not in self.parents:
            raise ValueError("unknown mixed dependency")
        self.parents[after].add(before)
        self.dependencies.append(MixedDependency(before=before, after=after, kind=kind, resource_id=resource))

    def local_data(self, operation: str, prerequisite: LocalDataPrerequisite) -> None:
        buffer = self.buffers.get(prerequisite.access.buffer_id)
        if buffer is None or buffer.resource_id != self.owners[operation] or not buffer.readable:
            raise ValueError("data readiness must observe readable local L1")
        access = prerequisite.access
        if access.offset_bytes + access.size_bytes > buffer.size_bytes:
            raise ValueError("local data prerequisite exceeds its buffer")
        version = prerequisite.version
        if version.kind == "initial":
            if not buffer.initially_ready:
                raise ValueError("local initial version is not initialized")
            return
        producer = version.producer_id
        assert producer is not None
        if not any(a.operation_id == producer and a.direction == "write" and a.buffer_id == access.buffer_id
                   and a.offset_bytes <= access.offset_bytes and access.offset_bytes + access.size_bytes <= a.offset_bytes + a.size_bytes
                   for a in self.accesses):
            raise ValueError("local producer does not publish the full declared data extent")
        self.edge(producer, operation, "local_data", buffer.resource_id)

    def dependency_graph(self) -> None:
        for operation in (*self.workload.writes, *self.workload.increments, *self.workload.operations):
            for parent in operation.depends_on:
                if self.owners.get(parent) != self.owners[operation.operation_id]:
                    raise ValueError("source completion dependency cannot observe a remote diagnostic")
                self.edge(parent, operation.operation_id, "completion", self.owners[operation.operation_id])
        for operation in self.workload.operations:
            for parent in operation.destination_ready_after:
                effects = [a for a in self.accesses if a.operation_id == parent and a.direction == "write"
                           and a.resource_id == self.owners[operation.operation_id]]
                if not effects:
                    raise ValueError("destination-ready dependency requires a local publication")
                self.edge(parent, operation.operation_id, "local_data", self.owners[operation.operation_id])
            for parent in operation.fence_operations:
                if self.owners.get(parent) != self.owners[operation.operation_id]:
                    raise ValueError("fence cannot observe another initiator")
                if operation.fence_mode == "remote_completion" and self.posted(parent):
                    raise ValueError("posted operations do not provide remote-completion fences")
                self.edge(parent, operation.operation_id, "completion", self.owners[operation.operation_id])
        for gate in self.workload.gates:
            for parent in gate.after_waits:
                if self.owners[parent] != self.owners[gate.operation_id]:
                    raise ValueError("wait activation requires the same physical L1")
                self.edge(parent, gate.operation_id, "local_wait", self.owners[gate.operation_id])
            for prerequisite in gate.local_data:
                self.local_data(gate.operation_id, prerequisite)
        for wait in self.workload.waits:
            for producer in wait.producer_operations:
                self.edge(producer, wait.wait_id, "producer_proof", self.owners[wait.wait_id])
            for prerequisite in wait.local_data:
                self.local_data(wait.wait_id, prerequisite)
        # Every source has an explicit or inferable version. Producer versions
        # require local publication evidence, never just a remote summary fence.
        for access in self.accesses:
            if access.direction != "read":
                continue
            buffer = self.buffers[access.buffer_id]
            version = access.version
            if version is None:
                version = (MemoryVersion(kind="producer", producer_id=buffer.producer_operation_id)
                           if buffer.producer_operation_id else MemoryVersion(kind="initial"))
            if version.kind == "initial":
                if not buffer.initially_ready:
                    raise ValueError("source read has no initialized or producer version")
            else:
                producer = version.producer_id
                if not any(a.operation_id == producer and a.direction == "write" and a.resource_id == access.resource_id
                           and a.address_bytes <= access.address_bytes
                           and access.address_bytes + access.size_bytes <= a.address_bytes + a.size_bytes for a in self.accesses):
                    raise ValueError("source producer does not publish the full read extent")
                if access.resource_id != self.owners[access.operation_id]:
                    # A remote read has real request/response transport, but must
                    # already be ordered after an acknowledged producer.
                    if producer not in self.parents[access.operation_id] or self.posted(producer):
                        raise ValueError("remote source version requires acknowledged producer completion")
                elif producer not in self.parents[access.operation_id]:
                    raise ValueError("producer source version requires an explicit local data/completion dependency")

    def posted(self, operation: str) -> bool:
        return (any(w.operation_id == operation and w.completion == "write_posted" for w in self.workload.writes)
                or any(i.operation_id == operation and i.completion == "atomic_posted" for i in self.workload.increments)
                or any(o.operation_id == operation and o.kind == "write_posted" for o in self.workload.operations))

    def conflicts(self, ancestors: dict[str, set[str]]) -> None:
        for index, left in enumerate(self.accesses):
            for right in self.accesses[index + 1:]:
                if not _overlap(left, right) or (left.direction == right.direction and left.direction in {"read", "atomic"}):
                    continue
                if left.operation_id == right.operation_id:
                    raise ValueError("operation source and destination ranges overlap")
                if left.operation_id in ancestors[right.operation_id]:
                    before, after = left, right
                elif right.operation_id in ancestors[left.operation_id]:
                    before, after = right, left
                else:
                    raise ValueError("conflicting mixed ranges require an explicit dependency")
                if (before.direction == "write" and self.posted(before.operation_id)
                        and before.resource_id != self.owners[before.operation_id]
                        and not any(d.before == before.operation_id and d.kind == "local_data" and d.resource_id == before.resource_id
                                    and (d.after == after.operation_id or d.after in ancestors[after.operation_id])
                                    for d in self.dependencies)):
                    raise ValueError("posted source handoff cannot order remote target reuse")

    def phase_proofs(self, ancestors: dict[str, set[str]]) -> None:
        counters = {c.counter_id: c for c in self.workload.counters}
        increments = {i.operation_id: i for i in self.workload.increments}
        for wait in self.workload.waits:
            counter = counters[wait.counter_id]
            producers = set(wait.producer_operations)
            if any(p not in increments or increments[p].counter_id != wait.counter_id for p in producers):
                raise ValueError("wait producers must increment its declared counter")
            if wait.threshold > counter.initial_value + len(producers):
                raise ValueError("wait threshold is unreachable by its finite producers")
            # All unnamed increments must occur after release of this barrier;
            # named producers do not filter the physical counter at runtime.
            for operation, increment in increments.items():
                if increment.counter_id == wait.counter_id and operation not in producers and wait.wait_id not in ancestors[operation]:
                    raise ValueError("unrelated or later-phase increments can overtake this threshold")

    def read_versions(self, ancestors: dict[str, set[str]]) -> None:
        for access in self.accesses:
            if access.direction != "read":
                continue
            version = access.version
            producer = (version.producer_id if version is not None
                        else self.buffers[access.buffer_id].producer_operation_id)
            for writer in self.accesses:
                if (writer.direction != "write" or not _overlap(access, writer)
                        or writer.operation_id not in ancestors[access.operation_id]):
                    continue
                if producer is None or (writer.operation_id != producer and writer.operation_id not in ancestors[producer]):
                    raise ValueError("source version is stale after an ordered overlapping write")


def _overlap(left: MixedAccess, right: MixedAccess) -> bool:
    return (left.resource_id == right.resource_id and left.address_bytes < right.address_bytes + right.size_bytes
            and right.address_bytes < left.address_bytes + left.size_bytes)
