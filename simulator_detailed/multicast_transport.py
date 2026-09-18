"""Bounded shared transport primitives for planned multicast trees.

The transport is deliberately separate from the legacy unicast runtime.  It
models the finite reservation and forwarding accounting needed by the child
without changing public request/response envelopes or allocating a second
physical graph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .multicast_plan import MulticastSegmentPlan, MulticastSyncPlan, MulticastWritePlan


class TreeLaneIdentity(GraphRecord):
    fabric_id: Index
    link_id: Identifier
    lane_kind: Literal["multicast_tree"] = "multicast_tree"
    lane_index: Index


class CompositeLaneIdentity(GraphRecord):
    """A physical link lane shared by legacy and child traffic classes."""

    fabric_id: Index
    link_id: Identifier
    lane_kind: Literal["request", "response", "multicast_tree"]
    lane_index: Index


class CompositeChannelRecord(GraphRecord):
    lane: CompositeLaneIdentity
    physical_flit_bytes: PositiveInt
    capacity_flits: PositiveInt
    owner: Identifier | None


class SharedPhysicalTransportRegistry:
    """One registry for ordinary request/response and multicast lanes."""

    def __init__(self, *, physical_flit_bytes: int, capacity_flits: int):
        if type(physical_flit_bytes) is not int or physical_flit_bytes <= 0:
            raise ValueError("physical flit width must be positive")
        if type(capacity_flits) is not int or capacity_flits <= 0:
            raise ValueError("physical lane capacity must be positive")
        self.physical_flit_bytes = physical_flit_bytes
        self.capacity_flits = capacity_flits
        self._channels: dict[tuple[int, str, str, int], CompositeChannelRecord] = {}
        self._owners: dict[tuple[int, str, str, int], str] = {}

    def register(self, lane: CompositeLaneIdentity) -> CompositeChannelRecord:
        key = self._key(lane)
        if key in self._channels:
            return self._channels[key]
        record = CompositeChannelRecord(
            lane=lane,
            physical_flit_bytes=self.physical_flit_bytes,
            capacity_flits=self.capacity_flits,
            owner=None,
        )
        self._channels[key] = record
        return record

    def claim(self, lane: CompositeLaneIdentity, owner: str) -> CompositeChannelRecord:
        record = self.register(lane)
        key = self._key(lane)
        if key in self._owners and self._owners[key] != owner:
            raise ValueError(f"physical lane {key} is owned by {self._owners[key]}")
        self._owners[key] = owner
        return record.model_copy(update={"owner": owner})

    def release(self, lane: CompositeLaneIdentity, owner: str) -> None:
        key = self._key(lane)
        if self._owners.get(key) != owner:
            raise ValueError(f"physical lane {key} is not owned by {owner}")
        del self._owners[key]

    def export(self) -> tuple[CompositeChannelRecord, ...]:
        return tuple(
            record.model_copy(update={"owner": self._owners.get(self._key(record.lane))})
            for record in self._channels.values()
        )

    @staticmethod
    def _key(lane: CompositeLaneIdentity) -> tuple[int, str, str, int]:
        return lane.fabric_id, lane.link_id, lane.lane_kind, lane.lane_index


class TreeFlitIdentity(GraphRecord):
    operation_id: Identifier
    segment_index: Index
    flit_index: Index
    flit_count: PositiveInt
    physical_bytes: PositiveInt
    useful_bytes: Index


class TreeReservationRecord(GraphRecord):
    reservation_id: Identifier
    operation_id: Identifier
    lane_ids: tuple[Identifier, ...] = Field(min_length=1)
    setup_aci_cycles: float
    edge_aci_cycles: float
    acquired_aci_cycles: float
    released_aci_cycles: float | None


class TreeTransportEvent(GraphRecord):
    sequence: Index
    time_aci_cycles: float
    action: Literal[
        "reservation_wait",
        "reservation_acquire",
        "tree_inject",
        "tree_forward",
        "tree_replicate",
        "recipient_deliver",
        "tree_drain",
        "reservation_release",
    ]
    operation_id: Identifier
    segment_index: Index | None
    flit_index: Index | None
    fabric_id: Index
    link_id: Identifier | None
    recipient_endpoint_id: Identifier | None
    physical_bytes: Index


class TreeTransportSnapshot(GraphRecord):
    next_operation_index: Index
    next_segment_index: Index
    next_flit_index: Index
    elapsed_aci_cycles: float
    pending_operations: tuple[Identifier, ...]
    active_reservation_ids: tuple[Identifier, ...]


class TreeTransportResult(GraphRecord):
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit"]
    elapsed_aci_cycles: float
    source_read_useful_bytes: Index
    destination_write_useful_bytes: Index
    packet_physical_bytes: Index
    launched_channel_bytes: Index
    reservations: tuple[TreeReservationRecord, ...]
    events: tuple[TreeTransportEvent, ...]
    pending_operations: tuple[Identifier, ...]
    snapshot: TreeTransportSnapshot


@dataclass(frozen=True)
class TreeTransportPlan:
    plan: MulticastSyncPlan
    writes: tuple[MulticastWritePlan, ...]
    lanes: tuple[TreeLaneIdentity, ...]
    lane_by_link: dict[tuple[int, str], TreeLaneIdentity]

    @classmethod
    def compile(cls, plan: MulticastSyncPlan) -> TreeTransportPlan:
        lanes: list[TreeLaneIdentity] = []
        lane_by_link: dict[tuple[int, str], TreeLaneIdentity] = {}
        writes = plan.record.writes
        for write in writes:
            for edge in write.tree.edges:
                key = (edge.fabric_id, edge.link_id)
                lane = lane_by_link.get(key)
                if lane is None:
                    lane = TreeLaneIdentity(
                        fabric_id=edge.fabric_id,
                        link_id=edge.link_id,
                        lane_index=len(lanes),
                    )
                    lanes.append(lane)
                    lane_by_link[key] = lane
        return cls(plan=plan, writes=writes, lanes=tuple(lanes), lane_by_link=lane_by_link)

    def flits(self, write: MulticastWritePlan, segment: MulticastSegmentPlan) -> tuple[TreeFlitIdentity, ...]:
        return tuple(
            TreeFlitIdentity(
                operation_id=write.operation_id,
                segment_index=segment.segment_index,
                flit_index=index,
                flit_count=segment.flit_count,
                physical_bytes=self.plan.workload.memory.packet.physical_flit_bytes,
                useful_bytes=(
                    0
                    if index < self.plan.workload.memory.packet.header_flits
                    else min(
                        self.plan.workload.memory.packet.data_capacity_bytes,
                        segment.payload_bytes
                        - (index - self.plan.workload.memory.packet.header_flits)
                        * self.plan.workload.memory.packet.data_capacity_bytes,
                    )
                ),
            )
            for index in range(segment.flit_count)
        )


@dataclass(frozen=True)
class _ReservationRequest:
    reservation_id: str
    operation_id: str
    lane_ids: tuple[str, ...]
    setup_aci_cycles: float
    edge_aci_cycles: float


class AtomicTreeReservation:
    """FIFO all-or-none ownership of every lane in a tree."""

    def __init__(self, *, capacity: int):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("reservation capacity must be a positive integer")
        self.capacity = capacity
        self._owners: dict[str, str] = {}
        self._active: dict[str, _ReservationRequest] = {}
        self._waiting: deque[_ReservationRequest] = deque()

    @property
    def waiting(self) -> tuple[str, ...]:
        return tuple(request.reservation_id for request in self._waiting)

    @property
    def active(self) -> tuple[str, ...]:
        return tuple(self._active)

    def submit(
        self,
        *,
        reservation_id: str,
        operation_id: str,
        lane_ids: tuple[str, ...],
        setup_aci_cycles: float,
        edge_aci_cycles: float,
    ) -> TreeReservationRecord | None:
        if not lane_ids or len(set(lane_ids)) != len(lane_ids):
            raise ValueError("tree reservation requires unique lanes")
        if reservation_id in self._active or any(item.reservation_id == reservation_id for item in self._waiting):
            raise ValueError(f"reservation {reservation_id} is already submitted")
        if setup_aci_cycles < 0 or edge_aci_cycles < 0:
            raise ValueError("reservation costs cannot be negative")
        request = _ReservationRequest(
            reservation_id=reservation_id,
            operation_id=operation_id,
            lane_ids=lane_ids,
            setup_aci_cycles=setup_aci_cycles,
            edge_aci_cycles=edge_aci_cycles,
        )
        self._waiting.append(request)
        return self._grant_head()

    def _grant_head(self) -> TreeReservationRecord | None:
        if not self._waiting or len(self._active) >= self.capacity:
            return None
        request = self._waiting[0]
        if any(lane in self._owners for lane in request.lane_ids):
            return None
        self._waiting.popleft()
        self._active[request.reservation_id] = request
        for lane in request.lane_ids:
            self._owners[lane] = request.reservation_id
        return TreeReservationRecord(
            reservation_id=request.reservation_id,
            operation_id=request.operation_id,
            lane_ids=request.lane_ids,
            setup_aci_cycles=request.setup_aci_cycles,
            edge_aci_cycles=request.edge_aci_cycles,
            acquired_aci_cycles=request.setup_aci_cycles + request.edge_aci_cycles * len(request.lane_ids),
            released_aci_cycles=None,
        )

    def release(self, reservation_id: str, *, released_aci_cycles: float) -> tuple[TreeReservationRecord, ...]:
        request = self._active.pop(reservation_id, None)
        if request is None:
            raise ValueError(f"reservation {reservation_id} is not active")
        for lane in request.lane_ids:
            owner = self._owners.pop(lane, None)
            if owner != reservation_id:
                raise RuntimeError("tree reservation lane ownership was corrupted")
        released = TreeReservationRecord(
            reservation_id=request.reservation_id,
            operation_id=request.operation_id,
            lane_ids=request.lane_ids,
            setup_aci_cycles=request.setup_aci_cycles,
            edge_aci_cycles=request.edge_aci_cycles,
            acquired_aci_cycles=request.setup_aci_cycles + request.edge_aci_cycles * len(request.lane_ids),
            released_aci_cycles=released_aci_cycles,
        )
        grants: list[TreeReservationRecord] = [released]
        while True:
            granted = self._grant_head()
            if granted is None:
                return tuple(grants)
            grants.append(granted)


class TreeForwardingEngine:
    """Deterministic cut-through accounting for one shared tree transport."""

    def __init__(self, transport: TreeTransportPlan, *, branch_capacity_flits: int):
        if type(branch_capacity_flits) is not int or branch_capacity_flits <= 0:
            raise ValueError("branch capacity must be a positive integer")
        self.transport = transport
        self.branch_capacity_flits = branch_capacity_flits

    def run(
        self,
        *,
        cycle_limit: float | None = None,
        operation_order: tuple[str, ...] | None = None,
    ) -> TreeTransportResult:
        workload = self.transport.plan.workload
        control = workload.control
        manager = AtomicTreeReservation(capacity=control.reservation_capacity)
        events: list[TreeTransportEvent] = []
        records: list[TreeReservationRecord] = []
        elapsed = 0.0
        source_bytes = destination_bytes = packet_bytes = channel_bytes = 0
        writes = list(self.transport.writes)
        if operation_order is not None:
            positions = {operation_id: index for index, operation_id in enumerate(operation_order)}
            writes.sort(key=lambda item: positions.get(item.operation_id, len(positions)))
        pending: list[str] = []
        for write_index, write in enumerate(writes):
            pending.append(write.operation_id)
            lane_ids = tuple(
                self.transport.lane_by_link[(edge.fabric_id, edge.link_id)].model_dump_json()
                for edge in write.tree.edges
            )
            reservation_id = f"{write.operation_id}:reservation"
            events.append(
                self._event(
                    len(events), elapsed, "reservation_wait", write.operation_id, write.fabric_id, None,
                    None, None, 0,
                )
            )
            acquired = manager.submit(
                reservation_id=reservation_id,
                operation_id=write.operation_id,
                lane_ids=lane_ids,
                setup_aci_cycles=control.reservation_setup_aci_cycles,
                edge_aci_cycles=control.reservation_edge_aci_cycles,
            )
            if acquired is None:
                # The analytical runner serializes operations; an unavailable
                # grant becomes a bounded incomplete result rather than a partial tree.
                return self._result(
                    status="incomplete",
                    elapsed=elapsed,
                    source_bytes=source_bytes,
                    destination_bytes=destination_bytes,
                    packet_bytes=packet_bytes,
                    channel_bytes=channel_bytes,
                    records=records,
                    events=events,
                    pending=tuple(pending),
                    active_reservation_ids=manager.active,
                    operation_index=write_index,
                    segment_index=0,
                    flit_index=0,
                )
            records.append(acquired)
            elapsed += acquired.acquired_aci_cycles
            events.append(
                self._event(
                    len(events), elapsed, "reservation_acquire", write.operation_id, write.fabric_id,
                    None, None, None, 0,
                )
            )
            for segment in write.segments:
                for flit in self.transport.flits(write, segment):
                    if cycle_limit is not None and elapsed >= cycle_limit:
                        return self._result(
                            status="incomplete",
                            elapsed=elapsed,
                            source_bytes=source_bytes,
                            destination_bytes=destination_bytes,
                            packet_bytes=packet_bytes,
                            channel_bytes=channel_bytes,
                            records=records,
                            events=events,
                            pending=tuple(pending),
                            active_reservation_ids=manager.active,
                            operation_index=write_index,
                            segment_index=segment.segment_index,
                            flit_index=flit.flit_index,
                        )
                    for edge_index, edge in enumerate(write.tree.edges):
                        elapsed += control.reservation_edge_aci_cycles
                        event_action = "tree_replicate" if edge.kind == "branch" and edge_index + 1 < len(write.tree.edges) else (
                            "tree_inject" if edge_index == 0 else "tree_forward"
                        )
                        events.append(
                            self._event(
                                len(events), elapsed, event_action, write.operation_id, write.fabric_id,
                                edge.link_id, segment.segment_index, flit.flit_index, flit.physical_bytes,
                            )
                        )
                        channel_bytes += flit.physical_bytes
                    for recipient in write.tree.recipients:
                        events.append(
                            self._event(
                                len(events), elapsed, "recipient_deliver", write.operation_id,
                                write.fabric_id, None, segment.segment_index, flit.flit_index, 0,
                                recipient.endpoint_id,
                            )
                        )
                    packet_bytes += flit.physical_bytes
                    source_bytes += flit.useful_bytes
                    destination_bytes += flit.useful_bytes * len(write.tree.recipients)
                    elapsed += control.local_observation_aci_cycles / max(1, self.branch_capacity_flits)
            events.append(
                self._event(
                    len(events), elapsed, "tree_drain", write.operation_id, write.fabric_id, None,
                    None, None, 0,
                )
            )
            released = manager.release(reservation_id, released_aci_cycles=elapsed)
            records[-1] = released[0]
            events.append(
                self._event(
                    len(events), elapsed, "reservation_release", write.operation_id, write.fabric_id,
                    None, None, None, 0,
                )
            )
            pending.remove(write.operation_id)
        return self._result(
            status="complete", elapsed=elapsed, source_bytes=source_bytes,
            destination_bytes=destination_bytes, packet_bytes=packet_bytes,
            channel_bytes=channel_bytes, records=records, events=events,
            pending=(), active_reservation_ids=manager.active,
            operation_index=len(writes), segment_index=0, flit_index=0,
        )

    @staticmethod
    def _event(
        sequence: int,
        time: float,
        action: Literal[
            "reservation_wait", "reservation_acquire", "tree_inject", "tree_forward",
            "tree_replicate", "recipient_deliver", "tree_drain", "reservation_release",
        ],
        operation_id: str,
        fabric_id: int,
        link_id: str | None,
        segment_index: int | None,
        flit_index: int | None,
        physical_bytes: int,
        recipient: str | None = None,
    ) -> TreeTransportEvent:
        return TreeTransportEvent(
            sequence=sequence,
            time_aci_cycles=time,
            action=action,
            operation_id=operation_id,
            segment_index=segment_index,
            flit_index=flit_index,
            fabric_id=fabric_id,
            link_id=link_id,
            recipient_endpoint_id=recipient,
            physical_bytes=physical_bytes,
        )

    @staticmethod
    def _result(
        *,
        status: Literal["complete", "incomplete"],
        elapsed: float,
        source_bytes: int,
        destination_bytes: int,
        packet_bytes: int,
        channel_bytes: int,
        records: list[TreeReservationRecord],
        events: list[TreeTransportEvent],
        pending: tuple[str, ...],
        active_reservation_ids: tuple[str, ...],
        operation_index: int,
        segment_index: int,
        flit_index: int,
    ) -> TreeTransportResult:
        return TreeTransportResult(
            status=status,
            reason="drained" if status == "complete" else "cycle_limit",
            elapsed_aci_cycles=elapsed,
            source_read_useful_bytes=source_bytes,
            destination_write_useful_bytes=destination_bytes,
            packet_physical_bytes=packet_bytes,
            launched_channel_bytes=channel_bytes,
            reservations=tuple(records),
            events=tuple(events),
            pending_operations=pending,
            snapshot=TreeTransportSnapshot(
                next_operation_index=operation_index,
                next_segment_index=segment_index,
                next_flit_index=flit_index,
                elapsed_aci_cycles=elapsed,
                pending_operations=pending,
                active_reservation_ids=active_reservation_ids,
            ),
        )
