"""Opt-in legacy lifecycle and exclusive scratchpad-capacity bridges.

Neither adapter moves legacy work into the addressed-memory transport/server.
The DMA facade observes native results. The scratchpad facade owns capacity
only, using the original container and delay without a second byte budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import simpy
from simpy.events import Event, Process, ProcessGenerator

from .configs.schemas.memory_replay import MemoryBuffer
from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .configs.schemas.torus_replay import Cycles
from .core import ScratchpadMemory
from .dma_endpoint import DMAEndpoint, DMAReceiveResult, DMATransmitResult
from .memory_resources import BufferHandle, MemoryOwnershipEvent
from .pe_channel import NMCChannel, NMCReceiveResult, NMCTransmitResult
from .utils.definitions import (
    DMA_RDMA_NODE_TYPES,
    DMA_WDMA_NODE_TYPES,
    Message,
    NMCShapeMode,
    NodeType,
)

LegacyNativeResult = DMATransmitResult | DMAReceiveResult | NMCTransmitResult | NMCReceiveResult


class LegacyDMALifecycle(GraphRecord):
    execution_policy: Literal["legacy_dma"] = "legacy_dma"
    message_index: int
    operation: Literal["send", "receive"]
    client: Literal["dma_endpoint", "nmc_channel"]
    submission_aci_cycles: Cycles
    descriptor_acceptance_aci_cycles: Cycles
    final_local_handoff_aci_cycles: Cycles | None
    tail_service_completion_aci_cycles: Cycles | None
    completion_aci_cycles: Cycles
    source_read_completion_aci_cycles: None = None
    destination_ready_aci_cycles: None = None
    response_receipt_aci_cycles: None = None


@dataclass(frozen=True)
class LegacyDMAResult:
    native: LegacyNativeResult
    lifecycle: LegacyDMALifecycle


class LegacyDMAAdapter:
    """Wrap one existing DMA endpoint or PE channel; preserve paired posting."""

    def __init__(self, client: object):
        if not isinstance(client, (DMAEndpoint, NMCChannel)):
            raise TypeError("legacy DMA adapter requires a DMA endpoint or NMC channel")
        self.client, self.env = client, client.env

    @staticmethod
    def _validate(message: Message) -> None:
        message.validate_transport()
        if not ((message.src.node_type in DMA_RDMA_NODE_TYPES and message.dst.node_type is NodeType.PE)
                or (message.src.node_type is NodeType.PE and message.dst.node_type in DMA_WDMA_NODE_TYPES)):
            raise NotImplementedError("legacy DMA adapter supports GM/DDR reads and writes only")

    def send(self, message: Message) -> Process:
        self._validate(message)
        native = self.client.send(message)
        return self.env.process(self._observe(native, message, "send"))

    def recv_message(self, message: Message, shape_mode: NMCShapeMode | None = None) -> Process:
        self._validate(message)
        if isinstance(self.client, NMCChannel):
            if shape_mode is None:
                raise ValueError("NMC receive requires an explicit shape mode")
            native = self.client.recv_message(message, shape_mode)
        else:
            if shape_mode is not None:
                raise ValueError("DMA endpoint receive has no NMC shape mode")
            native = self.client.recv_message(message)
        return self.env.process(self._observe(native, message, "receive"))

    def _observe(self, native: Process, message: Message, operation: Literal["send", "receive"]) -> ProcessGenerator:
        result: object = yield native
        if not isinstance(result, (DMATransmitResult, DMAReceiveResult, NMCTransmitResult, NMCReceiveResult)):
            raise TypeError("legacy client returned an unsupported lifecycle result")
        handoff = result.final_local_handoff_time_aci_cycles if isinstance(result, (DMATransmitResult, NMCTransmitResult)) else None
        tail = (result.tail_service_completion_time_aci_cycles if isinstance(result, DMAReceiveResult)
                else result.tail_rx_service_completion_time_aci_cycles if isinstance(result, NMCReceiveResult) else None)
        return LegacyDMAResult(result, LegacyDMALifecycle(
            message_index=message.index, operation=operation,
            client="dma_endpoint" if isinstance(self.client, DMAEndpoint) else "nmc_channel",
            submission_aci_cycles=result.submission_time_aci_cycles,
            descriptor_acceptance_aci_cycles=result.descriptor_acceptance_time_aci_cycles,
            final_local_handoff_aci_cycles=handoff, tail_service_completion_aci_cycles=tail,
            completion_aci_cycles=result.operation_completion_time_aci_cycles))


class ScratchpadReservation(GraphRecord):
    buffer: MemoryBuffer
    state: Literal["allocating", "reserved", "releasing"]


class ScratchpadCapacityState(GraphRecord):
    execution_policy: Literal["legacy_scratchpad_capacity"] = "legacy_scratchpad_capacity"
    resource_id: Identifier
    capacity_bytes: PositiveInt
    available_bytes: Index
    used_bytes: Index
    pending_operations: Index
    attached: bool
    reservations: tuple[ScratchpadReservation, ...]


class ScratchpadCapacityAdapter:
    """Exclusive addressed reservations over one legacy capacity container.

    Returned events cannot be interrupted; admitted allocation/release work
    runs to completion. This capacity-only bridge provides no memory service,
    readiness or DFG migration, and must not be paired with a shadow allocator.
    """

    def __init__(self, env: simpy.Environment, scratchpad: ScratchpadMemory, *, resource_id: str, capacity_bytes: int):
        # Validate all parameters before taking the exclusive owner token.
        ScratchpadCapacityState(resource_id=resource_id, capacity_bytes=capacity_bytes,
                                available_bytes=capacity_bytes, used_bytes=0, pending_operations=0,
                                attached=True, reservations=())
        if scratchpad.env is not env or scratchpad.container.capacity != capacity_bytes:
            raise ValueError("scratchpad adapter requires the same environment and matching capacity")
        if type(scratchpad.delay) is not int or scratchpad.delay < 0:
            raise ValueError("scratchpad adapter requires a non-negative integer delay")
        self.env, self.scratchpad, self.resource_id = env, scratchpad, resource_id
        self._token = object()
        self._attached = True
        self._handles: dict[str, BufferHandle] = {}
        self._states: dict[str, Literal["allocating", "reserved", "releasing"]] = {}
        self._events: list[MemoryOwnershipEvent] = []
        scratchpad.bind_capacity_owner(self._token)

    @property
    def available_bytes(self) -> int:
        return int(self.scratchpad.container.level)

    @property
    def events(self) -> tuple[MemoryOwnershipEvent, ...]:
        return tuple(self._events)

    def _require_attached(self) -> None:
        if not self._attached:
            raise ValueError("scratchpad capacity adapter is detached")

    def reserve(self, buffer: MemoryBuffer, *, task_index: int = 0) -> Event:
        self._require_attached()
        buffer = MemoryBuffer.model_validate(buffer.model_dump(mode="python"))
        if buffer.resource_id != self.resource_id or buffer.buffer_id in self._handles:
            raise ValueError("foreign resource or duplicate scratchpad reservation")
        end = buffer.base_address + buffer.size_bytes
        if end > self.scratchpad.container.capacity:
            raise ValueError("scratchpad reservation exceeds physical capacity")
        if any(buffer.base_address < h.buffer.base_address + h.buffer.size_bytes and h.buffer.base_address < end
               for h in self._handles.values()):
            raise ValueError("scratchpad reservations overlap, including pending operations")
        handle = BufferHandle(buffer)
        self._handles[buffer.buffer_id] = handle
        self._states[buffer.buffer_id] = "allocating"
        done = self.env.event()
        self.env.process(self._reserve(handle, task_index, done))
        return done

    def _reserve(self, handle: BufferHandle, task_index: int, done: Event) -> ProcessGenerator:
        yield from self.scratchpad.allocate(handle.buffer.size_bytes, task_index, owner=self._token)
        self._states[handle.buffer.buffer_id] = "reserved"
        self._log(handle, "reserve")
        done.succeed(handle)

    def release(self, handle: BufferHandle, *, task_index: int = 0) -> Event:
        self._require_attached()
        key = handle.buffer.buffer_id
        if self._handles.get(key) is not handle or self._states[key] != "reserved":
            raise ValueError("foreign, pending or already released scratchpad handle")
        self._states[key] = "releasing"
        done = self.env.event()
        self.env.process(self._release(handle, task_index, done))
        return done

    def _release(self, handle: BufferHandle, task_index: int, done: Event) -> ProcessGenerator:
        yield from self.scratchpad.release(handle.buffer.size_bytes, task_index, owner=self._token)
        del self._handles[handle.buffer.buffer_id], self._states[handle.buffer.buffer_id]
        self._log(handle, "release")
        done.succeed()

    def _log(self, handle: BufferHandle, action: Literal["reserve", "release"]) -> None:
        self._events.append(MemoryOwnershipEvent(time_aci_cycles=self.env.now, resource_id=self.resource_id,
                                                buffer_id=handle.buffer.buffer_id, action=action,
                                                address=handle.buffer.base_address, size_bytes=handle.buffer.size_bytes,
                                                available_bytes=self.available_bytes))

    def snapshot(self) -> ScratchpadCapacityState:
        return ScratchpadCapacityState(resource_id=self.resource_id, capacity_bytes=int(self.scratchpad.container.capacity),
                                       available_bytes=self.available_bytes, used_bytes=int(self.scratchpad.used_bytes),
                                       pending_operations=sum(s != "reserved" for s in self._states.values()),
                                       attached=self._attached,
                                       reservations=tuple(ScratchpadReservation(buffer=h.buffer, state=self._states[key])
                                                          for key, h in self._handles.items()))

    def detach(self) -> None:
        self._require_attached()
        if self._handles:
            raise ValueError("scratchpad reservations and pending operations must drain before detach")
        self.scratchpad.unbind_capacity_owner(self._token)
        self._attached = False
