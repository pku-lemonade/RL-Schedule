"""Bounded addressed execution for initialized, nonconflicting network accesses.

One environment contains shared links, memory servers and canonical issue
budgets. Segment coroutines retain control metadata only; all data service runs
under the packet kernel's counted TX/RX staging leases.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Literal

import simpy
from simpy.events import Event, ProcessGenerator

from .configs.schemas.torus_replay import PacketIdentity
from .memory_execution import (
    MemoryDescriptorEvent,
    MemoryDescriptorState,
    MemoryExecutionPlan,
    MemoryExecutionResult,
    MemoryLifecycleEvent,
    MemorySegmentDefinition,
    MemorySegmentRecord,
    SegmentKey,
)
from .memory_packets import MemoryPacket
from .memory_records import (
    MemoryBufferRecord,
    MemoryOperationRecord,
    MemoryServiceRecord,
)
from .memory_resources import (
    AccessLease,
    MemoryAccess,
    MemoryResources,
    MemoryResourceState,
    MemoryVersion,
)
from .packet_runtime import PacketTransport, PacketTransportResult
from .packet_transport import PacketFlit

Fact = Literal["submission", "acceptance", "source_read_complete", "request_handoff",
               "response_wire_receipt", "destination_ready", "complete"]
_FIELDS: dict[Fact, str] = {
    "submission": "submission_aci_cycles", "acceptance": "descriptor_acceptance_aci_cycles",
    "source_read_complete": "source_read_completion_aci_cycles", "request_handoff": "final_request_handoff_aci_cycles",
    "response_wire_receipt": "response_receipt_aci_cycles", "destination_ready": "destination_ready_aci_cycles",
    "complete": "completion_aci_cycles",
}


def _identity(*values: object) -> str:
    return json.dumps(values, separators=(",", ":"), ensure_ascii=True)


@dataclass
class _Segment:
    definition: MemorySegmentDefinition
    facts: dict[Fact, float] = field(default_factory=lambda: dict[Fact, float]())
    source: AccessLease | None = None
    destination: AccessLease | None = None


class _Descriptors:
    def __init__(self, env: simpy.Environment, kind: Literal["issue", "responder"], owner: str, capacity: int,
                 events: list[MemoryDescriptorEvent]):
        self.env, self.owner, self.capacity = env, owner, capacity
        self.kind: Literal["issue", "responder"] = kind
        self.events = events
        self.owners: dict[SegmentKey, None] = {}
        self.peak = 0
        self.changed = env.event()

    @property
    def full(self) -> bool:
        return len(self.owners) == self.capacity

    def acquire(self, key: SegmentKey) -> None:
        if self.full or key in self.owners:
            raise ValueError("duplicate or exhausted memory descriptor")
        self.owners[key] = None
        self.peak = max(self.peak, len(self.owners))
        self._log("acquire", key)

    def release(self, key: SegmentKey) -> None:
        if key not in self.owners:
            raise ValueError("foreign or already retired memory descriptor")
        del self.owners[key]
        self._log("release", key)

    def _log(self, action: Literal["acquire", "release"], key: SegmentKey) -> None:
        self.events.append(MemoryDescriptorEvent(time_aci_cycles=self.env.now, kind=self.kind, owner_id=self.owner,
                                                action=action, operation_id=key[0], segment_index=key[1],
                                                occupied=len(self.owners)))
        self.changed.succeed()
        self.changed = self.env.event()

    def snapshot(self) -> MemoryDescriptorState:
        return MemoryDescriptorState(kind=self.kind, owner_id=self.owner, capacity=self.capacity,
                                     occupied=len(self.owners), peak_occupied=self.peak, owners=tuple(self.owners))


class MemoryRuntime:
    def __init__(self, plan: MemoryExecutionPlan):
        # Recompile the opt-in boundary before allocating any runtime resources.
        self.plan = MemoryExecutionPlan.compile(plan.memory, plan.settings)
        self.env = simpy.Environment()
        self.memory = MemoryResources(self.env, self.plan.resources)
        self._segments = {d.key: _Segment(d) for d in self.plan.segments}
        self._packets = {p.packet.identity.transport_identity: p.packet for d in self.plan.segments
                         for p in (d.request, d.response) if p is not None}
        self._descriptor_events: list[MemoryDescriptorEvent] = []
        self._lifecycle: list[MemoryLifecycleEvent] = []
        self._service_segments: dict[str, SegmentKey] = {}
        self._issues = {owner: _Descriptors(self.env, "issue", owner, self.plan.memory.config.max_outstanding_segments,
                                            self._descriptor_events)
                        for owner in sorted({d.initiator_resource_id for d in self.plan.segments})}
        self._responders = {owner: _Descriptors(self.env, "responder", owner, self.plan.settings.responder_capacity_packets,
                                                self._descriptor_events)
                            for owner in sorted({d.request.route.destination for d in self.plan.segments if d.response is not None})}
        self.transport = PacketTransport(self.env, self.plan.transport.network, self)
        self._before_teardown: tuple[MemoryResourceState, ...] | None = None
        self._final: MemoryExecutionResult | None = None
        for state in self._segments.values():
            self.env.process(self._issue(state))

    def _mark(self, state: _Segment, action: Fact) -> None:
        if action in state.facts:
            raise ValueError("memory lifecycle fact published twice")
        state.facts[action] = float(self.env.now)
        self._lifecycle.append(MemoryLifecycleEvent(time_aci_cycles=self.env.now, operation_id=state.definition.key[0],
                                                    segment_index=state.definition.key[1], action=action))

    def _delay(self, cycles: float) -> ProcessGenerator:
        if cycles:
            if not math.isfinite(self.env.now + cycles) or self.env.now + cycles <= self.env.now:
                raise ValueError("unrepresentable memory control duration")
            yield self.env.timeout(cycles)

    def _issue(self, state: _Segment) -> ProcessGenerator:
        definition, operation = state.definition, state.definition.operation
        segment = definition.segment
        if operation.source is None or operation.destination is None:
            raise ValueError("admitted network operation has no memory range")
        yield from self._delay(operation.start_aci_cycles)
        self._mark(state, "submission")
        source_handle = self.memory.handles[operation.source.buffer_id]
        destination_handle = self.memory.handles[operation.destination.buffer_id]
        source = self.memory.resources[source_handle.buffer.resource_id]
        destination = self.memory.resources[destination_handle.buffer.resource_id]
        source_access = MemoryAccess(client_id=operation.operation_id, direction="read",
                                     offset_bytes=operation.source.offset_bytes + segment.offset_bytes,
                                     size_bytes=segment.logical_bytes, version=MemoryVersion(kind="initial"))
        destination_access = MemoryAccess(client_id=operation.operation_id, direction="write",
                                          offset_bytes=operation.destination.offset_bytes + segment.offset_bytes,
                                          size_bytes=segment.logical_bytes,
                                          version=MemoryVersion(kind="producer", producer_id=operation.operation_id))
        pool = self._issues[definition.initiator_resource_id]
        while True:
            if not source.is_ready(source_handle, offset_bytes=source_access.offset_bytes,
                                   size_bytes=source_access.size_bytes, version=source_access.version):
                yield source.changed
                continue
            if pool.full:
                yield pool.changed
                continue
            state.source = source.try_acquire(source_handle, source_access)
            if state.source is None:
                yield source.changed
                continue
            state.destination = destination.try_acquire(destination_handle, destination_access)
            if state.destination is not None:
                break
            # Never retain a partial pair while waiting for another access.
            source.release_access(state.source)
            state.source = None
            yield destination.changed
        pool.acquire(definition.key)
        self._mark(state, "acceptance")
        yield from self._delay(self.plan.memory.config.issue_latency_aci_cycles)
        request = definition.request.packet.identity.transport_identity
        yield from self._submit(request)
        yield self.transport.handoff(request)
        self._mark(state, "request_handoff")
        if definition.response is not None:
            yield self.transport.receipt(definition.response.packet.identity.transport_identity)
        self._mark(state, "complete")
        pool.release(definition.key)

    def _submit(self, packet: PacketIdentity) -> ProcessGenerator:
        while not self.transport.try_submit(packet):
            yield self.transport.changed

    def _respond(self, state: _Segment, pool: _Descriptors) -> ProcessGenerator:
        definition = state.definition
        if definition.response is None:
            raise ValueError("posted write cannot generate a response")
        yield self.transport.receipt(definition.request.packet.identity.transport_identity)
        response = definition.response.packet.identity.transport_identity
        yield from self._submit(response)
        yield self.transport.handoff(response)
        pool.release(definition.key)

    def produce(self, flit: PacketFlit) -> ProcessGenerator:
        if flit.payload_bytes:
            yield from self._data_service(flit, "source")

    def consume(self, flit: PacketFlit) -> ProcessGenerator:
        packet = self._packets[flit.packet]
        state = self._segments[(packet.identity.operation_id, packet.identity.segment_index)]
        if flit.flit_index == 0:
            request = packet.identity.traffic_class == "request"
            yield from self._delay(self.plan.settings.request_control_aci_cycles if request
                                   else self.plan.settings.response_control_aci_cycles)
        if flit.payload_bytes:
            yield from self._data_service(flit, "destination")
        if flit.is_tail and packet.identity.traffic_class == "request" and state.definition.response is not None:
            # Reserve response work only after request consumption. This tail
            # retains counted RX staging/credit while waiting; the current
            # responder never needs further request ingress to finish. This
            # ordering does not rely on the kernel's per-lane packet ownership.
            pool = self._responders[state.definition.request.route.destination]
            while pool.full:
                yield pool.changed
            pool.acquire(state.definition.key)
            self.env.process(self._respond(state, pool))

    def _data_service(self, flit: PacketFlit, side: Literal["source", "destination"]) -> ProcessGenerator:
        packet: MemoryPacket = self._packets[flit.packet]
        key = packet.identity.operation_id, packet.identity.segment_index
        state = self._segments[key]
        lease = state.source if side == "source" else state.destination
        if lease is None:
            raise ValueError("data flit has no live memory access")
        owner = self.memory.resources[lease.handle.buffer.resource_id]
        start = (flit.flit_index - packet.layout.header_flits) * packet.layout.data_capacity_bytes
        end = start + flit.payload_bytes
        chunk_bytes = owner.definition.timing.config.chunk_bytes
        for offset in range(start, end, chunk_bytes):
            service_id = _identity(key[0], key[1], side, offset)
            done: Event | None = None
            while done is None:
                done = owner.try_service(lease, service_id=service_id, offset_bytes=offset,
                                         size_bytes=min(chunk_bytes, end - offset))
                if done is None:
                    yield owner.service.changed
            self._service_segments[service_id] = key
            yield done
        if owner.access_complete(lease):
            owner.release_access(lease)
            if side == "source":
                state.source = None
                self._mark(state, "source_read_complete")
            else:
                state.destination = None
                self._mark(state, "destination_ready")

    def _resource_states(self) -> tuple[MemoryResourceState, ...]:
        return tuple(r.snapshot() for r in self.memory.resources.values())

    def _work_drained(self, transport: PacketTransportResult) -> bool:
        return (transport.status == "complete" and self.memory.is_drained
                and all(not p.owners for p in (*self._issues.values(), *self._responders.values()))
                and all("complete" in s.facts and "destination_ready" in s.facts for s in self._segments.values()))

    def run(self, *, max_aci_cycles: float | None = None) -> MemoryExecutionResult:
        if self._final is not None:
            return self._final
        horizon = self.plan.memory.config.max_aci_cycles if max_aci_cycles is None else max_aci_cycles
        if isinstance(horizon, bool) or not math.isfinite(horizon) or horizon <= self.env.now:
            raise ValueError("memory horizon must be finite and later than current time")
        while self.env.peek() != float("inf") and self.env.peek() <= horizon:
            self.env.step()
        transport = self.transport.snapshot(memory_service="external_hooks")
        if self._work_drained(transport):
            self._before_teardown = self._resource_states()
            self.memory.teardown()
            # Only synchronous release notifications/container bookkeeping remain.
            while self.env.peek() == self.env.now:
                self.env.step()
            self._final = self.snapshot()
            return self._final
        return self.snapshot()

    def snapshot(self) -> MemoryExecutionResult:
        if self._final is not None:
            return self._final
        transport = self.transport.snapshot(memory_service="external_hooks")
        resources = self._resource_states() if self._before_teardown is None else self._before_teardown
        chunks = tuple(sorted((c for r in self.memory.resources.values() for c in r.service.records),
                              key=lambda c: (c.end_aci_cycles, c.resource_id, c.service_id)))
        facts = {key: dict(state.facts) for key, state in self._segments.items()}
        lifecycle = list(self._lifecycle)
        packet_bytes = dict.fromkeys(self._segments, 0)
        channel_bytes = dict.fromkeys(self._segments, 0)
        service_bytes = dict.fromkeys(self._segments, 0)
        for event in transport.trace:
            if event.packet is None or event.channel is None:
                continue
            packet = self._packets[event.packet]
            key = packet.identity.operation_id, packet.identity.segment_index
            if event.action == "link_launch":
                channel_bytes[key] += event.physical_bytes
                if event.channel.kind == "inject":
                    packet_bytes[key] += event.physical_bytes
            if (event.action == "link_arrive" and event.channel.kind == "eject"
                    and packet.identity.traffic_class == "response" and event.flit_index == packet.layout.flit_count - 1):
                # Actual tail arrival precedes RX/local memory service. Do not
                # relabel the kernel's post-consumption receipt as wire arrival.
                if "response_wire_receipt" in facts[key]:
                    raise ValueError("response tail arrived twice")
                facts[key]["response_wire_receipt"] = event.time_aci_cycles
                lifecycle.append(MemoryLifecycleEvent(time_aci_cycles=event.time_aci_cycles, operation_id=key[0],
                                                      segment_index=key[1], action="response_wire_receipt"))
        for chunk in chunks:
            service_bytes[self._service_segments[chunk.service_id]] += chunk.serviced_bytes
        segments = tuple(MemorySegmentRecord.model_validate({
            "operation_id": key[0], "kind": state.definition.operation.kind,
            "status": "complete" if "complete" in facts[key] else "incomplete",
            **{name: facts[key].get(action) for action, name in _FIELDS.items()},
            "descriptor_retirement_aci_cycles": facts[key].get("complete"), "segment": state.definition.segment,
            "logical_bytes": state.definition.segment.logical_bytes, "packet_bytes": packet_bytes[key],
            "channel_bytes": channel_bytes[key], "memory_service_bytes": service_bytes[key],
        }) for key, state in self._segments.items())
        operations: list[MemoryOperationRecord] = []
        for operation in self.plan.memory.config.operations:
            selected = [s for s in segments if s.operation_id == operation.operation_id]
            times: dict[str, float | None] = {}
            for action, name in _FIELDS.items():
                values = [facts[s.operation_id, s.segment.segment_index].get(action) for s in selected]
                present = [v for v in values if v is not None]
                times[name] = (min(present) if action in {"submission", "acceptance"} else
                               max(present) if len(present) == len(values) else None) if present else None
            operations.append(MemoryOperationRecord.model_validate({
                "operation_id": operation.operation_id, "kind": operation.kind, **times,
                "status": "complete" if times["completion_aci_cycles"] is not None else "incomplete",
                **{name: sum(getattr(s, name) for s in selected)
                   for name in ("logical_bytes", "packet_bytes", "channel_bytes", "memory_service_bytes")},
            }))
        descriptors = tuple(p.snapshot() for p in (*self._issues.values(), *self._responders.values()))
        complete = self._before_teardown is not None and self._work_drained(transport)
        pending: list[str] = []
        if not complete:
            for key, state in self._segments.items():
                for action in ("complete", "destination_ready"):
                    if action not in state.facts:
                        pending.append(_identity("segment", *key, action))
            pending.extend(_identity(d.kind, d.owner_id, *key) for d in descriptors for key in d.owners)
            pending.extend(_identity("packet", p.transfer_id, p.traffic_class) for p in transport.pending_packets)
            pending.extend(_identity("network", r.fabric_id, r.resource_id,
                                     None if r.lane is None else r.lane.model_dump(mode="json"))
                           for r in transport.resources if not r.is_drained)
            for resource in resources:
                pending.extend(_identity("service", resource.resource_id, sid) for sid in resource.service.pending_service_ids)
                pending.extend(_identity("access", resource.resource_id, b.buffer.buffer_id, aid)
                               for b in resource.buffers for aid in b.access_ids)
            if not pending:
                pending.append(_identity("teardown"))
        return MemoryExecutionResult(
            status="complete" if complete else "incomplete",
            reason="drained" if complete else "idle_with_pending" if self.env.peek() == float("inf") else "cycle_limit",
            plan=self.plan.memory.record, execution_plan_sha256=self.plan.plan_sha256, settings=self.plan.settings,
            elapsed_aci_cycles=self.env.now, operations=tuple(operations), segments=segments,
            buffers=tuple(MemoryBufferRecord(buffer_id=b.buffer.buffer_id, resource_id=r.resource_id,
                                            base_address=b.buffer.base_address, size_bytes=b.buffer.size_bytes,
                                            reserved_bytes=b.buffer.size_bytes,
                                            ready=sum(v.size_bytes for v in b.ready_ranges) == b.buffer.size_bytes)
                          for r in resources for b in r.buffers),
            service=tuple(MemoryServiceRecord.model_validate(c.model_dump(include=set(MemoryServiceRecord.model_fields)))
                          for c in chunks),
            chunks=chunks, pending=tuple(pending), logical_bytes=sum(o.logical_bytes for o in operations),
            packet_bytes=sum(o.packet_bytes for o in operations), channel_bytes=transport.transmitted_channel_bytes,
            memory_service_bytes=sum(c.serviced_bytes for c in chunks), execution="addressed_memory_disjoint_v1",
            transport=transport, memory_resources=resources, descriptors=descriptors,
            released_resources=self._resource_states() if complete else (), teardown_complete=complete,
            lifecycle=tuple(sorted(lifecycle, key=lambda e: e.time_aci_cycles)), descriptor_trace=tuple(self._descriptor_events),
            ownership_trace=tuple(sorted((e for r in self.memory.resources.values() for e in r.events), key=lambda e: e.time_aci_cycles)),
            service_trace=tuple(sorted((e for r in self.memory.resources.values() for e in r.service.events), key=lambda e: e.time_aci_cycles)))
