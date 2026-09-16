"""Canonical capacity, range versions and access ownership for memory clients.

No tensor values, packet scheduling or operation dependency policy live here.
The registry admits all reservations before constructing any runtime resource.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Self

import simpy
from pydantic import model_validator
from simpy.events import Event, ProcessGenerator

from .configs.schemas.memory_replay import MemoryBuffer, MemoryReplay
from .configs.schemas.topology import (
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
)
from .configs.schemas.torus_replay import Cycles
from .memory_plan import MemoryPlan
from .memory_service import (
    MemoryService,
    MemoryServiceState,
    ServiceChunk,
    ServiceTiming,
)


class MemoryVersion(GraphRecord):
    kind: Literal["initial", "producer"]
    producer_id: Identifier | None = None

    @model_validator(mode="after")
    def tagged(self) -> Self:
        if (self.kind == "producer") != (self.producer_id is not None):
            raise ValueError("only a producer version names an operation")
        return self


class MemoryAccess(GraphRecord):
    client_id: Identifier
    direction: Literal["read", "write"]
    offset_bytes: Index
    size_bytes: PositiveInt
    version: MemoryVersion

    @model_validator(mode="after")
    def write_version(self) -> Self:
        if self.direction == "write" and self.version.kind != "producer":
            raise ValueError("a write must name its producer version")
        return self


class ReadyRange(GraphRecord):
    address: Index
    size_bytes: PositiveInt
    version: MemoryVersion


class MemoryOwnershipEvent(GraphRecord):
    time_aci_cycles: Cycles
    resource_id: Identifier
    buffer_id: Identifier
    action: Literal["reserve", "release", "read_acquire", "write_acquire", "access_release", "invalidate", "publish"]
    address: Index
    size_bytes: PositiveInt
    available_bytes: Index
    access_id: Index | None = None
    version: MemoryVersion | None = None


class BufferOwnershipState(GraphRecord):
    buffer: MemoryBuffer
    ready_ranges: tuple[ReadyRange, ...]
    access_ids: tuple[Index, ...]


class MemoryResourceState(GraphRecord):
    resource_id: Identifier
    capacity_bytes: PositiveInt
    available_bytes: Index
    reserved_bytes: Index
    peak_reserved_bytes: Index
    buffers: tuple[BufferOwnershipState, ...]
    service: MemoryServiceState

    @model_validator(mode="after")
    def conserved(self) -> Self:
        if self.available_bytes + self.reserved_bytes != self.capacity_bytes:
            raise ValueError("memory capacity is not conserved")
        if not self.reserved_bytes <= self.peak_reserved_bytes <= self.capacity_bytes:
            raise ValueError("memory capacity high-water mark is invalid")
        if self.reserved_bytes != sum(b.buffer.size_bytes for b in self.buffers):
            raise ValueError("capacity ledger differs from reservations")
        return self


class MemoryResourceDefinition(GraphRecord):
    resource_id: Identifier
    capacity_bytes: PositiveInt
    timing: ServiceTiming


@dataclass(frozen=True)
class MemoryResourcePlan:
    plan_sha256: str
    resources: tuple[MemoryResourceDefinition, ...]
    buffers: tuple[MemoryBuffer, ...]
    aliases: Mapping[tuple[str, str], str]

    @classmethod
    def compile(cls, plan: MemoryPlan) -> MemoryResourcePlan:
        # Preserve structural admission as a separate, pure boundary. Earlier
        # wire-only plans need not satisfy an unused memory-service geometry.
        plan = MemoryPlan.compile(MemoryReplay.model_validate(plan.config.model_dump(mode="python")),
                                  plan.graph.model_dump(mode="python"))
        config = plan.config
        graph = {r.resource_id: r for r in plan.graph.resources}
        definitions: list[MemoryResourceDefinition] = []
        for r in config.resources:
            if graph[r.resource_id].kind not in {"local_sram", "dram"}:
                raise ValueError("memory service requires L1 or DRAM backing storage")
            service = r.service
            if config.packet.address_alignment_bytes % service.service_granule_bytes or config.packet.data_capacity_bytes % service.chunk_bytes:
                raise ValueError("packet alignment/data capacity and memory granule/chunk are incompatible")
            definition = MemoryResourceDefinition(resource_id=r.resource_id,
                                                  capacity_bytes=r.capacity_override_bytes or graph[r.resource_id].capacity_bytes,
                                                  timing=ServiceTiming(config=service, aci_clock_hz=config.aci_clock_hz))
            selected = sorted((b for b in config.buffers if b.resource_id == r.resource_id), key=lambda b: b.base_address)
            end = 0
            for buffer in selected:
                if buffer.base_address < end:
                    raise ValueError("distinct memory buffer reservations overlap")
                end = buffer.base_address + buffer.size_bytes
                if end > definition.capacity_bytes:
                    raise ValueError("buffer exceeds effective memory capacity")
            definitions.append(definition)
        aliases = {(e.endpoint_id, r): r for e in config.endpoints if e.enabled for r in e.resource_ids}
        return cls(plan.plan_sha256, tuple(definitions), config.buffers, MappingProxyType(aliases))


@dataclass(frozen=True, eq=False)
class BufferHandle:
    buffer: MemoryBuffer


@dataclass(frozen=True, eq=False)
class AccessLease:
    access_id: int
    handle: BufferHandle
    access: MemoryAccess


@dataclass
class _AccessState:
    lease: AccessLease
    pending: dict[str, tuple[int, int]] = field(default_factory=lambda: dict[str, tuple[int, int]]())
    completed: list[tuple[int, int]] = field(default_factory=lambda: list[tuple[int, int]]())


def _overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


class MemoryResource:
    def __init__(self, env: simpy.Environment, definition: MemoryResourceDefinition):
        self.definition = MemoryResourceDefinition.model_validate(definition.model_dump(mode="python"))
        self.env = env
        self.resource_id = self.definition.resource_id
        self.service = MemoryService(env, self.resource_id, self.definition.timing)
        self._capacity = simpy.Container(env, capacity=self.definition.capacity_bytes, init=self.definition.capacity_bytes)
        self._handles: dict[str, BufferHandle] = {}
        self._ready: dict[str, list[ReadyRange]] = {}
        self._accesses: dict[int, _AccessState] = {}
        self._next_access = 0
        self._peak = 0
        self._changed = env.event()
        self._events: list[MemoryOwnershipEvent] = []

    @property
    def available_bytes(self) -> int:
        return int(self._capacity.level)

    @property
    def events(self) -> tuple[MemoryOwnershipEvent, ...]:
        return tuple(self._events)

    @property
    def changed(self) -> Event:
        return self._changed

    @property
    def is_drained(self) -> bool:
        # Persistent reservations are not pending work; snapshot before teardown.
        return not self._accesses and self.service.is_drained

    def _notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def _log(self, handle: BufferHandle, action: Literal["reserve", "release", "read_acquire", "write_acquire", "access_release", "invalidate", "publish"],
             address: int, size: int, lease: AccessLease | None = None, version: MemoryVersion | None = None) -> None:
        self._events.append(MemoryOwnershipEvent(time_aci_cycles=self.env.now, resource_id=self.resource_id,
                                                buffer_id=handle.buffer.buffer_id, action=action, address=address,
                                                size_bytes=size, available_bytes=self.available_bytes,
                                                access_id=None if lease is None else lease.access_id, version=version))
        self._notify()

    def _handle(self, handle: BufferHandle) -> MemoryBuffer:
        if not self.owns(handle):
            raise ValueError("foreign or released memory buffer handle")
        return handle.buffer

    def owns(self, handle: BufferHandle) -> bool:
        return self._handles.get(handle.buffer.buffer_id) is handle

    def reserve(self, buffer: MemoryBuffer) -> BufferHandle:
        buffer = MemoryBuffer.model_validate(buffer.model_dump(mode="python"))
        if buffer.resource_id != self.resource_id or buffer.buffer_id in self._handles:
            raise ValueError("foreign resource or duplicate buffer reservation")
        end = buffer.base_address + buffer.size_bytes
        if end > self.definition.capacity_bytes or buffer.size_bytes > self.available_bytes:
            raise ValueError("buffer exceeds available physical memory capacity")
        if any(_overlap(buffer.base_address, end, h.buffer.base_address, h.buffer.base_address + h.buffer.size_bytes)
               for h in self._handles.values()):
            raise ValueError("distinct memory buffer reservations overlap")
        handle = BufferHandle(buffer)
        # All checks precede the one real capacity charge; no pending put/get.
        self._capacity.get(buffer.size_bytes)
        self._handles[buffer.buffer_id] = handle
        self._ready[buffer.buffer_id] = ([ReadyRange(address=buffer.base_address, size_bytes=buffer.size_bytes,
                                                   version=MemoryVersion(kind="initial"))] if buffer.initially_ready else [])
        self._peak = max(self._peak, self.definition.capacity_bytes - self.available_bytes)
        self._log(handle, "reserve", buffer.base_address, buffer.size_bytes)
        return handle

    def release(self, handle: BufferHandle) -> None:
        buffer = self._handle(handle)
        if any(s.lease.handle is handle for s in self._accesses.values()):
            raise ValueError("buffer still owns memory accesses")
        self._capacity.put(buffer.size_bytes)
        del self._handles[buffer.buffer_id], self._ready[buffer.buffer_id]
        self._log(handle, "release", buffer.base_address, buffer.size_bytes)

    def _validate_access(self, handle: BufferHandle, access: MemoryAccess) -> tuple[int, int]:
        buffer = self._handle(handle)
        if access.offset_bytes + access.size_bytes > buffer.size_bytes:
            raise ValueError("memory access exceeds its reservation")
        if not (buffer.readable if access.direction == "read" else buffer.writable):
            raise ValueError("memory access violates buffer permission")
        start = buffer.base_address + access.offset_bytes
        return start, start + access.size_bytes

    def is_ready(self, handle: BufferHandle, *, offset_bytes: int, size_bytes: int, version: MemoryVersion) -> bool:
        access = MemoryAccess(client_id="readiness", direction="read", offset_bytes=offset_bytes,
                              size_bytes=size_bytes, version=version)
        start, end = self._validate_access(handle, access)
        cursor = start
        for item in self._ready[handle.buffer.buffer_id]:
            if item.address + item.size_bytes <= cursor:
                continue
            if item.address > cursor or item.version != version:
                return False
            cursor = min(end, item.address + item.size_bytes)
            if cursor == end:
                return True
        return False

    def try_acquire(self, handle: BufferHandle, access: MemoryAccess) -> AccessLease | None:
        access = MemoryAccess.model_validate(access.model_dump(mode="python"))
        start, end = self._validate_access(handle, access)
        for state in self._accesses.values():
            other = state.lease
            other_start = other.handle.buffer.base_address + other.access.offset_bytes
            if _overlap(start, end, other_start, other_start + other.access.size_bytes) and (
                    access.direction == "write" or other.access.direction == "write"):
                return None
        if access.direction == "read" and not self.is_ready(handle, offset_bytes=access.offset_bytes,
                                                           size_bytes=access.size_bytes, version=access.version):
            return None
        lease = AccessLease(self._next_access, handle, access)
        self._next_access += 1
        self._accesses[lease.access_id] = _AccessState(lease)
        if access.direction == "write":
            self._replace_ready(handle, start, end, None)
            self._log(handle, "invalidate", start, access.size_bytes, lease)
        self._log(handle, "read_acquire" if access.direction == "read" else "write_acquire",
                  start, access.size_bytes, lease, access.version)
        return lease

    def _lease(self, lease: AccessLease) -> _AccessState:
        state = self._accesses.get(lease.access_id)
        if state is None or state.lease is not lease:
            raise ValueError("foreign or released memory access lease")
        return state

    def release_access(self, lease: AccessLease) -> None:
        state = self._lease(lease)
        if state.pending:
            raise ValueError("memory access still owns queued/active service")
        del self._accesses[lease.access_id]
        self._log(lease.handle, "access_release", lease.handle.buffer.base_address + lease.access.offset_bytes,
                  lease.access.size_bytes, lease, lease.access.version)

    def access_complete(self, lease: AccessLease) -> bool:
        state = self._lease(lease)
        return not state.pending and sum(end - start for start, end in state.completed) == lease.access.size_bytes

    def try_service(self, lease: AccessLease, *, service_id: str, offset_bytes: int, size_bytes: int) -> Event | None:
        state = self._lease(lease)
        # Offsets here are relative to the access, not the containing buffer.
        if type(offset_bytes) is not int or offset_bytes < 0 or type(size_bytes) is not int or size_bytes <= 0:
            raise ValueError("invalid memory service subrange")
        if offset_bytes + size_bytes > lease.access.size_bytes:
            raise ValueError("memory service exceeds its access lease")
        start = lease.handle.buffer.base_address + lease.access.offset_bytes + offset_bytes
        end = start + size_bytes
        if any(_overlap(start, end, a, b) for a, b in (*state.pending.values(), *state.completed)):
            raise ValueError("memory access chunk was already submitted or serviced")
        ticket = self.service.try_submit(ServiceChunk(service_id=service_id, client_id=lease.access.client_id,
                                                      direction=lease.access.direction, address=start, useful_bytes=size_bytes))
        if ticket is None:
            return None
        state.pending[service_id] = start, end
        done = self.env.event()
        self.env.process(self._finish(lease, service_id, ticket, done))
        return done

    def _finish(self, lease: AccessLease, service_id: str, ticket: Event, done: Event) -> ProcessGenerator:
        record: object = yield ticket
        state = self._lease(lease)
        start, end = state.pending.pop(service_id)
        state.completed.append((start, end))
        if lease.access.direction == "write":
            self._replace_ready(lease.handle, start, end, lease.access.version)
            self._log(lease.handle, "publish", start, end - start, lease, lease.access.version)
        self._notify()
        done.succeed(record)

    def _replace_ready(self, handle: BufferHandle, start: int, end: int, version: MemoryVersion | None) -> None:
        ranges: list[ReadyRange] = []
        for item in self._ready[handle.buffer.buffer_id]:
            item_end = item.address + item.size_bytes
            if not _overlap(start, end, item.address, item_end):
                ranges.append(item)
                continue
            if item.address < start:
                ranges.append(item.model_copy(update={"size_bytes": start - item.address}))
            if item_end > end:
                ranges.append(item.model_copy(update={"address": end, "size_bytes": item_end - end}))
        if version is not None:
            ranges.append(ReadyRange(address=start, size_bytes=end - start, version=version))
        merged: list[ReadyRange] = []
        for item in sorted(ranges, key=lambda r: r.address):
            if merged and merged[-1].address + merged[-1].size_bytes == item.address and merged[-1].version == item.version:
                merged[-1] = merged[-1].model_copy(update={"size_bytes": merged[-1].size_bytes + item.size_bytes})
            else:
                merged.append(item)
        self._ready[handle.buffer.buffer_id] = merged

    def snapshot(self) -> MemoryResourceState:
        return MemoryResourceState(
            resource_id=self.resource_id, capacity_bytes=self.definition.capacity_bytes,
            available_bytes=self.available_bytes, reserved_bytes=self.definition.capacity_bytes - self.available_bytes,
            peak_reserved_bytes=self._peak, service=self.service.snapshot(),
            buffers=tuple(BufferOwnershipState(buffer=h.buffer, ready_ranges=tuple(self._ready[key]),
                                                access_ids=tuple(k for k, s in self._accesses.items() if s.lease.handle is h))
                          for key, h in self._handles.items()))


class MemoryResources:
    """One registry per replay; all admitted aliases return the same owner."""

    def __init__(self, env: simpy.Environment, plan: MemoryResourcePlan):
        self.env, self.plan = env, plan
        self.resources: Mapping[str, MemoryResource] = MappingProxyType(
            {r.resource_id: MemoryResource(env, r) for r in plan.resources})
        self.handles: Mapping[str, BufferHandle] = MappingProxyType(
            {b.buffer_id: self.resources[b.resource_id].reserve(b) for b in plan.buffers})

    def via(self, endpoint_id: str, resource_id: str) -> MemoryResource:
        canonical = self.plan.aliases.get((endpoint_id, resource_id))
        if canonical is None:
            raise ValueError("unavailable memory attachment/resource alias")
        return self.resources[canonical]

    @property
    def is_drained(self) -> bool:
        return all(r.is_drained for r in self.resources.values())

    def teardown(self) -> None:
        if not self.is_drained:
            raise ValueError("memory ownership/service must drain before teardown")
        # Preflight all handles so a repeated or partially manual teardown fails
        # without releasing any additional reservation.
        for resource in self.resources.values():
            expected = tuple(h for h in self.handles.values() if h.buffer.resource_id == resource.resource_id)
            if len(resource.snapshot().buffers) != len(expected) or any(not resource.owns(h) for h in expected):
                raise ValueError("replay reservation was released or replaced outside teardown")
        for handle in self.handles.values():
            self.resources[handle.buffer.resource_id].release(handle)
