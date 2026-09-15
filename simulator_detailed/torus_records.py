"""Immutable effective-plan/result data; these records are not runtime capabilities."""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import Field, model_validator

from .configs.schemas.topology import (
    CanonicalTopology,
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
    unique,
)
from .configs.schemas.torus_replay import (
    ChannelIdentity,
    Cycles,
    LaneIdentity,
    PacketIdentity,
    Policy,
    RouteRecord,
    Text,
)
from .topology import content_digest, normalize_topology
from .torus_contract import ResolvedQuantity, require_canonical_object


class EffectivePlanRecord(GraphRecord):
    policy: Policy
    source_sha256: Digest
    contract_sha256: Digest
    source_json: str
    configuration_json: str
    quantities: tuple[ResolvedQuantity, ...]
    graph: CanonicalTopology
    routes: tuple[RouteRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self) -> Self:
        for snapshot in (self.source_json, self.configuration_json):
            require_canonical_object(snapshot)
        if content_digest(json.loads(self.source_json)) != self.source_sha256:
            raise ValueError("source snapshot does not match source digest")
        if json.loads(self.configuration_json).get("policy") != self.policy:
            raise ValueError("plan and configuration policies disagree")
        unique(tuple(q.field_path for q in self.quantities), "resolved quantity")
        unique(tuple((r.fabric_id, r.source, r.destination, r.traffic_class) for r in self.routes), "plan route")
        # Normalize unordered tables, preserving order within each route.
        object.__setattr__(self, "graph", normalize_topology(self.graph))
        object.__setattr__(self, "quantities", tuple(sorted(self.quantities, key=lambda q: q.field_path)))
        object.__setattr__(self, "routes", tuple(sorted(self.routes, key=lambda r: (
            r.fabric_id, r.source, r.destination, r.traffic_class))))
        expected = content_digest({
            "contract_version": 1, "source_sha256": self.source_sha256,
            "configuration": json.loads(self.configuration_json),
            "quantities": [q.model_dump(mode="json") for q in self.quantities],
        })
        if expected != self.contract_sha256:
            raise ValueError("effective configuration does not match contract digest")
        return self

    @property
    def plan_sha256(self) -> str:
        return content_digest(self.model_dump(mode="json"))


class RuntimeIndexRecord(GraphRecord):
    kind: Literal["router", "link", "port"]
    fabric_id: Index
    identity: Identifier
    router_id: Identifier | None
    runtime_index: int = Field(strict=True)

    @model_validator(mode="after")
    def namespace(self) -> Self:
        if (self.kind == "port") != (self.router_id is not None):
            raise ValueError("only port indices require an owning router")
        if self.kind != "port" and self.runtime_index < 0:
            raise ValueError("router/link indices must be nonnegative")
        return self


class PacketResult(GraphRecord):
    packet: PacketIdentity
    fabric_id: Index
    source: Identifier
    destination: Identifier
    expected_payload_bytes: PositiveInt
    received_payload_bytes: Index
    expected_flits: PositiveInt
    received_flits: Index
    physical_bytes: PositiveInt
    first_injection_aci_cycles: Cycles | None
    first_ejection_aci_cycles: Cycles | None
    completion_aci_cycles: Cycles | None

    @model_validator(mode="after")
    def counts_and_times(self) -> Self:
        if self.received_payload_bytes > self.expected_payload_bytes or self.received_flits > self.expected_flits:
            raise ValueError("packet receipt exceeds expected counts")
        if self.physical_bytes < self.expected_payload_bytes:
            raise ValueError("physical packet bytes cannot be smaller than payload")
        times = (self.first_injection_aci_cycles, self.first_ejection_aci_cycles, self.completion_aci_cycles)
        for index, time in enumerate(times):
            if time is not None and any(t is None for t in times[:index]):
                raise ValueError("packet timing cannot skip preceding boundaries")
            if time is not None and any(t is not None and time < t for t in times[:index]):
                raise ValueError("packet timestamps must be ordered")
        if self.completion_aci_cycles is not None and (
            self.received_payload_bytes != self.expected_payload_bytes or self.received_flits != self.expected_flits
        ):
            raise ValueError("completed packet must have all expected bytes and flits")
        return self


class ResourceState(GraphRecord):
    resource_id: Text
    kind: Literal["lane", "injection_queue", "response_descriptors", "router_pipeline"]
    fabric_id: Index
    unit: Literal["flits", "packets"]
    lane: LaneIdentity | None
    capacity: PositiveInt
    available: Index
    occupied: Index
    pending_returns: Index
    peak_occupied: Index
    owners: tuple[PacketIdentity, ...]

    @model_validator(mode="after")
    def conservation(self) -> Self:
        unique(self.owners, "resource owner")
        if self.available + self.occupied + self.pending_returns != self.capacity:
            raise ValueError("resource capacity is not conserved")
        if not self.occupied <= self.peak_occupied <= self.capacity:
            raise ValueError("resource occupancy exceeds its bound or peak")
        if (self.kind == "lane") != (self.lane is not None):
            raise ValueError("only lane resources require a lane identity")
        if self.lane is not None and self.lane.channel.fabric_id != self.fabric_id:
            raise ValueError("resource and lane fabrics disagree")
        if self.kind != "lane" and self.pending_returns:
            raise ValueError("only lane capacity has delayed credit returns")
        expected_unit = "packets" if self.kind in {"injection_queue", "response_descriptors"} else "flits"
        if self.unit != expected_unit:
            raise ValueError("resource capacity unit disagrees with its kind")
        return self

    @property
    def is_drained(self) -> bool:
        return not (self.occupied or self.pending_returns or self.owners)


class TransportTraceEvent(GraphRecord):
    action: Literal[
        "inject", "eject", "router_arrive", "route", "grant", "release",
        "transfer_start", "transfer_end", "link_launch", "serialization_end",
        "link_arrive", "credit_wait", "credit_return", "arbitration_wait",
        "arrival_order_wait", "sink_complete", "response_ready", "failure_start", "failure_end",
        "owner_acquire", "owner_release", "credit_reserve", "link_ready", "link_take",
        "credit_release", "propagation_start",
    ]
    time_aci_cycles: Cycles
    fabric_id: Index
    router_id: Identifier | None
    port_id: Identifier | None
    channel: ChannelIdentity | None
    lane: LaneIdentity | None
    packet: PacketIdentity | None
    flit_index: Index | None
    token_id: Text | None
    failure_id: Identifier | None
    physical_bytes: Index
    payload_bytes: Index
    duration_aci_cycles: Cycles | None
    launch_factor: float | None = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def event_identity(self) -> Self:
        if self.channel is not None and self.channel.fabric_id != self.fabric_id:
            raise ValueError("trace channel has the wrong fabric")
        if self.lane is not None and self.lane.channel != self.channel:
            raise ValueError("trace lane does not belong to its channel")
        if self.packet is not None and self.lane is not None and self.packet.traffic_class != self.lane.traffic_class:
            raise ValueError("trace packet and lane classes disagree")
        if self.action in {"failure_start", "failure_end"}:
            if self.failure_id is None or self.channel is None or self.channel.kind != "network":
                raise ValueError("failure event requires a directed network channel and failure ID")
        elif self.action == "credit_return" and (self.lane is None or self.token_id is None):
            raise ValueError("credit return requires a lane and token")
        if self.action == "link_launch" and (
            self.lane is None or self.packet is None or self.flit_index is None
            or self.launch_factor is None or self.physical_bytes == 0
        ):
            raise ValueError("physical launch requires lane, packet, sequence, factor and byte cost")
        if self.payload_bytes > self.physical_bytes:
            raise ValueError("trace payload cannot exceed physical bytes")
        return self


class TorusReplayResult(GraphRecord):
    kind: Literal["topology_replay_result"]
    schema_version: int = Field(strict=True, ge=2, le=2)
    plan: EffectivePlanRecord
    plan_sha256: Digest
    runtime_indices: tuple[RuntimeIndexRecord, ...]
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending"]
    elapsed_aci_cycles: Cycles
    expected_payload_bytes: Index
    received_payload_bytes: Index
    packet_physical_bytes: Index
    transmitted_channel_bytes: Index
    packets: tuple[PacketResult, ...] = Field(min_length=1)
    resources: tuple[ResourceState, ...]
    trace: tuple[TransportTraceEvent, ...]
    execution: Literal["torus_unicast_byte_transport"]
    niu_transactions: Literal["unsupported"]
    memory_service: Literal["unsupported"]
    compute_execution: Literal["unsupported"]
    silicon_timing: Literal["unvalidated"]

    @model_validator(mode="after")
    def completion(self) -> Self:
        if self.plan_sha256 != self.plan.plan_sha256:
            raise ValueError("result plan digest disagrees with effective plan")
        unique(tuple((p.fabric_id, p.packet) for p in self.packets), "result packet")
        unique(tuple((r.fabric_id, r.resource_id) for r in self.resources), "result resource")
        unique(tuple((i.kind, i.fabric_id, i.router_id, i.identity) for i in self.runtime_indices), "runtime identity")
        unique(tuple((i.kind, i.fabric_id, i.router_id, i.runtime_index) for i in self.runtime_indices), "runtime index")
        if self.expected_payload_bytes != sum(p.expected_payload_bytes for p in self.packets) or (
            self.received_payload_bytes != sum(p.received_payload_bytes for p in self.packets)
        ) or self.packet_physical_bytes != sum(p.physical_bytes for p in self.packets):
            raise ValueError("result byte totals disagree with packet records")
        if self.transmitted_channel_bytes != sum(e.physical_bytes for e in self.trace if e.action == "link_launch"):
            raise ValueError("transmitted bytes must count actual physical launches once")
        if any(e.time_aci_cycles > self.elapsed_aci_cycles for e in self.trace) or any(
            time is not None and time > self.elapsed_aci_cycles for p in self.packets
            for time in (p.first_injection_aci_cycles, p.first_ejection_aci_cycles, p.completion_aci_cycles)
        ):
            raise ValueError("result contains events beyond elapsed simulation time")
        if (self.status == "complete") != (self.reason == "drained"):
            raise ValueError("only a drained result is complete")
        if self.status == "complete" and (
            any(p.completion_aci_cycles is None for p in self.packets)
            or any(not r.is_drained for r in self.resources)
        ):
            raise ValueError("complete result still has packets or resources pending")
        return self
