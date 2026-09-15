"""Version-two transport contracts; parsing does not admit a graph or run traffic."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from .topology import (
    Coordinate,
    Digest,
    GraphRecord,
    Identifier,
    Index,
    PositiveInt,
    unique,
)

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Cycles = Annotated[float, Field(strict=True, ge=0)]
PositiveTime = Annotated[float, Field(strict=True, gt=0)]
Number = StrictInt | StrictFloat
Unit = Literal["Hz", "bytes", "bits_per_cycle"]
TrafficClass = Literal["request", "response"]
Phase = Annotated[int, Field(strict=True, ge=0, le=1)]
Policy = Literal["dimension_dateline_v1"]


class EvidenceSource(GraphRecord):
    url: Annotated[str, Field(pattern=r"^https?://\S+$")]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")] | None = None
    sha256: Digest | None = None

    @model_validator(mode="after")
    def pinned(self) -> Self:
        if self.revision is None and self.sha256 is None:
            raise ValueError("evidence source requires an immutable revision or digest")
        return self


class TransportEvidence(GraphRecord):
    """New model choices or documented values; inherited evidence stays intact."""

    status: Literal["assumed", "documented"]
    description: Text
    sources: tuple[EvidenceSource, ...] = ()

    @model_validator(mode="after")
    def documented_source(self) -> Self:
        unique(tuple((s.url, s.revision, s.sha256) for s in self.sources), "evidence source")
        if self.status == "documented" and not self.sources:
            raise ValueError("documented transport evidence requires a pinned source")
        return self


def check_quantity(value: float, unit: Unit) -> None:
    if value <= 0 or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("hardware quantity must be finite and positive")
    if unit != "Hz" and type(value) is not int:
        raise ValueError(f"{unit} requires an integer value")


class LiteralQuantity(GraphRecord):
    kind: Literal["literal"]
    value: Number
    unit: Unit
    evidence: TransportEvidence

    @model_validator(mode="after")
    def quantity(self) -> Self:
        check_quantity(self.value, self.unit)
        return self


class QuantityOverride(GraphRecord):
    value: Number
    reason: Text
    evidence: TransportEvidence


class ProfileQuantity(GraphRecord):
    kind: Literal["profile_parameter"]
    parameter: Identifier
    unit: Unit
    override: QuantityOverride | None = None

    @model_validator(mode="after")
    def quantity(self) -> Self:
        if self.override is not None:
            check_quantity(self.override.value, self.unit)
        return self


Quantity = Annotated[LiteralQuantity | ProfileQuantity, Field(discriminator="kind")]


def require_unit(quantity: Quantity, unit: Unit) -> None:
    if quantity.unit != unit:
        raise ValueError(f"expected {unit}, received {quantity.unit}")


def known_value(quantity: Quantity) -> int | float | None:
    if isinstance(quantity, LiteralQuantity):
        return quantity.value
    return None if quantity.override is None else quantity.override.value


class GraphSource(GraphRecord):
    kind: Literal["canonical_graph"]
    graph_path: Text


class ProfileSource(GraphRecord):
    kind: Literal["hardware_profile"]
    profile_path: Text


ReplaySource = Annotated[GraphSource | ProfileSource, Field(discriminator="kind")]


class AvailabilitySetting(GraphRecord):
    enabled: StrictBool
    evidence: TransportEvidence


class RouterAvailability(AvailabilitySetting):
    fabric_id: Index
    router_id: Identifier


class LinkAvailability(AvailabilitySetting):
    fabric_id: Index
    link_id: Identifier


class TorusFabricBinding(GraphRecord):
    fabric_id: Index
    topology_policy: Literal["torus_2d_positive"]
    routing_policy: Literal["dimension_order_xy", "dimension_order_yx"]
    dateline: Coordinate
    router_default: AvailabilitySetting
    link_default: AvailabilitySetting
    evidence: TransportEvidence


class ServiceTime(GraphRecord):
    cycles: Cycles
    timebase: Literal["aci", "noc"]
    evidence: TransportEvidence


EndpointRole = Literal["request_source", "request_sink", "responder", "response_sink"]


class TorusEndpointBinding(GraphRecord):
    endpoint_id: Identifier
    fabric_id: Index
    roles: tuple[EndpointRole, ...] = Field(min_length=1)
    inject_port: Identifier | None
    eject_port: Identifier | None
    injection_queue_capacity_packets: PositiveInt | None
    sink_service: ServiceTime | None
    response_queue_capacity_packets: PositiveInt | None
    response_service: ServiceTime | None
    evidence: TransportEvidence

    @model_validator(mode="after")
    def role_resources(self) -> Self:
        unique(self.roles, "endpoint role")
        injects = "request_source" in self.roles or "responder" in self.roles
        ejects = any(r in self.roles for r in ("request_sink", "responder", "response_sink"))
        if injects != (self.inject_port is not None) or injects != (
            self.injection_queue_capacity_packets is not None
        ):
            raise ValueError("injecting roles require an injection port and bounded queue")
        if ejects != (self.eject_port is not None) or ejects != (self.sink_service is not None):
            raise ValueError("receiving roles require an ejection port and sink service")
        if self.sink_service is not None and self.sink_service.cycles <= 0:
            raise ValueError("sink service must be positive")
        responds = "responder" in self.roles
        if responds != (self.response_queue_capacity_packets is not None) or responds != (
            self.response_service is not None
        ):
            raise ValueError("only responders require bounded response storage and service")
        return self


class TorusBinding(GraphRecord):
    fabrics: tuple[TorusFabricBinding, ...] = Field(min_length=1)
    endpoints: tuple[TorusEndpointBinding, ...] = Field(min_length=1)
    router_overrides: tuple[RouterAvailability, ...] = ()
    link_overrides: tuple[LinkAvailability, ...] = ()

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(f.fabric_id for f in self.fabrics), "binding fabric")
        unique(tuple(e.endpoint_id for e in self.endpoints), "binding endpoint")
        unique(tuple((r.fabric_id, r.router_id) for r in self.router_overrides), "router override")
        unique(tuple((e.fabric_id, e.link_id) for e in self.link_overrides), "link override")
        fabrics = {f.fabric_id for f in self.fabrics}
        for item in (*self.endpoints, *self.router_overrides, *self.link_overrides):
            if item.fabric_id not in fabrics:
                raise ValueError("binding refers to an unknown fabric")
        return self


class TorusFlitFormat(GraphRecord):
    physical_flit_bytes: Quantity
    payload_capacity_bytes: PositiveInt
    header_bytes: Index
    evidence: TransportEvidence

    @model_validator(mode="after")
    def capacity(self) -> Self:
        require_unit(self.physical_flit_bytes, "bytes")
        physical = known_value(self.physical_flit_bytes)
        if physical is not None and max(self.payload_capacity_bytes, self.header_bytes) > physical:
            raise ValueError("flit payload/header exceeds physical size")
        return self


class TorusLinkSettings(GraphRecord):
    wire_bits_per_noc_cycle: Quantity
    payload_bits_per_noc_cycle: Quantity
    launch_interval_noc_cycles: PositiveTime
    propagation_noc_cycles: Cycles
    credit_return_noc_cycles: Cycles
    lane_capacity_flits: PositiveInt
    staging_capacity_flits: PositiveInt
    arbitration_quantum_flits: PositiveInt
    evidence: TransportEvidence

    @model_validator(mode="after")
    def widths(self) -> Self:
        require_unit(self.wire_bits_per_noc_cycle, "bits_per_cycle")
        require_unit(self.payload_bits_per_noc_cycle, "bits_per_cycle")
        wire, payload = known_value(self.wire_bits_per_noc_cycle), known_value(self.payload_bits_per_noc_cycle)
        if wire is not None and payload is not None and payload > wire:
            raise ValueError("usable link width exceeds wire width")
        return self

    def validate_flit(self, flit: TorusFlitFormat) -> None:
        physical, payload = known_value(flit.physical_flit_bytes), known_value(self.payload_bits_per_noc_cycle)
        if type(physical) is int and type(payload) is int:
            serialization = (8 * physical + payload - 1) // payload
            if self.launch_interval_noc_cycles < serialization:
                raise ValueError("physical launch interval is shorter than serialization")


class TorusRouterSettings(GraphRecord):
    rc_noc_cycles: Cycles
    sa_noc_cycles: Cycles
    transfer_noc_cycles: Cycles
    transfer_initiation_interval_noc_cycles: PositiveTime
    transfer_capacity_flits: PositiveInt
    arbitration_quantum_flits: PositiveInt
    evidence: TransportEvidence


class TorusFabricSettings(GraphRecord):
    fabric_id: Index
    noc_clock: Quantity
    flit: TorusFlitFormat
    router: TorusRouterSettings
    network_link: TorusLinkSettings
    local_link: TorusLinkSettings

    @model_validator(mode="after")
    def clock_and_serialization(self) -> Self:
        require_unit(self.noc_clock, "Hz")
        for link in (self.network_link, self.local_link):
            link.validate_flit(self.flit)
        return self


class NetworkSettingsOverride(GraphRecord):
    fabric_id: Index
    link_id: Identifier
    settings: TorusLinkSettings


class LocalSettingsOverride(GraphRecord):
    endpoint_id: Identifier
    direction: Literal["inject", "eject"]
    settings: TorusLinkSettings


class PacketTraffic(GraphRecord):
    transfer_id: Identifier
    fabric_id: Index
    source: Identifier
    destination: Identifier
    payload_bytes: PositiveInt
    start_aci_cycles: Cycles
    burst_quantum_flits: PositiveInt


class OneWayTraffic(PacketTraffic):
    kind: Literal["one_way"]


class RequestResponseTraffic(PacketTraffic):
    kind: Literal["request_response"]
    response_payload_bytes: PositiveInt
    response_burst_quantum_flits: PositiveInt


Traffic = Annotated[OneWayTraffic | RequestResponseTraffic, Field(discriminator="kind")]


class DirectedSlowdown(GraphRecord):
    failure_id: Identifier
    fabric_id: Index
    link_id: Identifier
    start_aci_cycles: Cycles
    end_aci_cycles: PositiveTime
    factor: Annotated[float, Field(strict=True, ge=1)]

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.end_aci_cycles <= self.start_aci_cycles:
            raise ValueError("slowdown end must exceed start")
        return self


class TorusReplay(GraphRecord):
    kind: Literal["topology_replay"]
    schema_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    policy: Policy
    source: ReplaySource
    aci_clock: LiteralQuantity
    binding: TorusBinding
    fabrics: tuple[TorusFabricSettings, ...] = Field(min_length=1)
    network_overrides: tuple[NetworkSettingsOverride, ...] = ()
    local_overrides: tuple[LocalSettingsOverride, ...] = ()
    traffic: tuple[Traffic, ...] = Field(min_length=1)
    slowdowns: tuple[DirectedSlowdown, ...] = ()
    max_aci_cycles: PositiveTime

    @model_validator(mode="after")
    def admission(self) -> Self:
        require_unit(self.aci_clock, "Hz")
        unique(tuple(f.fabric_id for f in self.fabrics), "settings fabric")
        unique(tuple(t.transfer_id for t in self.traffic), "traffic identity")
        unique(tuple(s.failure_id for s in self.slowdowns), "slowdown identity")
        unique(tuple((o.fabric_id, o.link_id) for o in self.network_overrides), "network settings override")
        unique(tuple((o.endpoint_id, o.direction) for o in self.local_overrides), "local settings override")
        fabrics = {f.fabric_id: f for f in self.fabrics}
        if set(fabrics) != {f.fabric_id for f in self.binding.fabrics}:
            raise ValueError("settings must match binding fabrics exactly")
        endpoints = {e.endpoint_id: e for e in self.binding.endpoints}
        for item in self.traffic:
            src, dst = endpoints.get(item.source), endpoints.get(item.destination)
            if src is None or dst is None or src.fabric_id != item.fabric_id or dst.fabric_id != item.fabric_id:
                raise ValueError(f"traffic {item.transfer_id}: missing or cross-fabric endpoint")
            if "request_source" not in src.roles:
                raise ValueError(f"traffic {item.transfer_id}: source cannot initiate requests")
            if isinstance(item, RequestResponseTraffic):
                if "responder" not in dst.roles or "response_sink" not in src.roles:
                    raise ValueError("request/response requires a responder and return sink")
            elif "request_sink" not in dst.roles:
                raise ValueError("one-way traffic requires an explicit request sink")
        for override in self.network_overrides:
            if override.fabric_id not in fabrics:
                raise ValueError("network settings override refers to an unknown fabric")
            override.settings.validate_flit(fabrics[override.fabric_id].flit)
        for override in self.local_overrides:
            endpoint = endpoints.get(override.endpoint_id)
            if endpoint is None or (
                endpoint.inject_port if override.direction == "inject" else endpoint.eject_port
            ) is None:
                raise ValueError("local settings override requires an admitted endpoint channel")
            override.settings.validate_flit(fabrics[endpoint.fabric_id].flit)
        last_end: dict[tuple[int, str], float] = {}
        for failure in sorted(self.slowdowns, key=lambda f: (f.fabric_id, f.link_id, f.start_aci_cycles)):
            if failure.fabric_id not in fabrics:
                raise ValueError("slowdown refers to an unknown fabric")
            key = (failure.fabric_id, failure.link_id)
            if failure.start_aci_cycles < last_end.get(key, 0):
                raise ValueError("overlapping slowdowns on the same directed link")
            last_end[key] = failure.end_aci_cycles
        return self


class PacketIdentity(GraphRecord):
    """Structural namespace: response IDs cannot collide with user transfer IDs."""

    transfer_id: Identifier
    traffic_class: TrafficClass


class ChannelIdentity(GraphRecord):
    fabric_id: Index
    kind: Literal["network", "inject", "eject"]
    identity: Identifier


class LaneIdentity(GraphRecord):
    channel: ChannelIdentity
    traffic_class: TrafficClass
    dateline_phase: Phase | None

    @model_validator(mode="after")
    def phase_namespace(self) -> Self:
        if (self.channel.kind == "network") != (self.dateline_phase is not None):
            raise ValueError("only network lanes require a dateline phase")
        return self


class ResourceHop(GraphRecord):
    lane: LaneIdentity
    src_router: Identifier | None
    dst_router: Identifier | None
    src_port: Identifier
    dst_port: Identifier
    axis: Literal["x", "y"] | None
    rank: Index

    @model_validator(mode="after")
    def channel_endpoints(self) -> Self:
        kind = self.lane.channel.kind
        if (kind == "network") != (self.axis is not None):
            raise ValueError("only network hops have an axis")
        if (self.src_router is not None) != (kind != "inject") or (
            self.dst_router is not None
        ) != (kind != "eject"):
            raise ValueError("hop router endpoints disagree with its channel kind")
        return self


class RouteRecord(GraphRecord):
    fabric_id: Index
    source: Identifier
    destination: Identifier
    traffic_class: TrafficClass
    hops: tuple[ResourceHop, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def path_shape(self) -> Self:
        if self.hops[0].lane.channel.kind != "inject" or self.hops[-1].lane.channel.kind != "eject":
            raise ValueError("route must begin at injection and end at ejection")
        if self.hops[0].lane.channel.identity != self.source or self.hops[-1].lane.channel.identity != self.destination:
            raise ValueError("route endpoints disagree with local channels")
        unique(tuple(h.lane for h in self.hops), "route lane")
        for index, hop in enumerate(self.hops):
            if hop.lane.channel.fabric_id != self.fabric_id or hop.lane.traffic_class != self.traffic_class:
                raise ValueError("route cannot change fabric or traffic class")
            if 0 < index < len(self.hops) - 1 and hop.lane.channel.kind != "network":
                raise ValueError("route interior must contain network hops")
            if index and (self.hops[index - 1].dst_router != hop.src_router or self.hops[index - 1].rank >= hop.rank):
                raise ValueError("route must have continuous routers and increasing resource ranks")
        return self


class TransportEnvelope(GraphRecord):
    """Immutable byte-flit metadata, not a hardware header or runtime admission."""

    plan_sha256: Digest
    packet: PacketIdentity
    fabric_id: Index
    source: Identifier
    destination: Identifier
    hop_index: Index
    lane: LaneIdentity
    flit_index: Index
    flit_count: PositiveInt
    payload_bytes: PositiveInt
    physical_bytes: PositiveInt
    burst_quantum_flits: PositiveInt

    @model_validator(mode="after")
    def packet_shape(self) -> Self:
        if self.flit_index >= self.flit_count or self.payload_bytes > self.physical_bytes:
            raise ValueError("invalid byte-flit bounds")
        if self.lane.channel.fabric_id != self.fabric_id or self.lane.traffic_class != self.packet.traffic_class:
            raise ValueError("envelope and lane disagree on fabric/class")
        return self

    @property
    def is_head(self) -> bool:
        return self.flit_index == 0

    @property
    def is_tail(self) -> bool:
        return self.flit_index + 1 == self.flit_count
