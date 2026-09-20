"""Pure memory-wire adaptation to shared transport, without memory execution."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Self

from pydantic import Field, model_validator

from .configs.schemas.topology import (
    CanonicalTopology,
    GraphRecord,
    Index,
    PositiveInt,
    unique,
)
from .configs.schemas.torus_replay import (
    ChannelIdentity,
    LaneIdentity,
    PacketIdentity,
    TransportEvidence,
)
from .memory_packets import MemoryWireEnvelope, MemoryWirePlan, RoutedMemoryPacket
from .packet_runtime import PacketDefinition, PacketNetworkPlan, RouterServiceConfig
from .packet_transport import LinkService, PacketFlit, WireFlit
from .topology import content_digest
from .virtual_channel import ResolvedLinkConfig


class MemoryFabricTransport(GraphRecord):
    fabric_id: Index
    network_link: ResolvedLinkConfig
    local_link: ResolvedLinkConfig
    router: RouterServiceConfig


class MemoryChannelOverride(GraphRecord):
    channel: ChannelIdentity
    settings: ResolvedLinkConfig


class MemoryTransportConfig(GraphRecord):
    """Explicit effective service settings; wire-only execution is opt-in.

    No callbacks are represented here. Memory service/operation completion is
    not enabled by admitting this internal transport configuration.
    """

    fabrics: tuple[MemoryFabricTransport, ...] = Field(min_length=1)
    overrides: tuple[MemoryChannelOverride, ...] = ()
    endpoint_queue_capacity_packets: PositiveInt
    endpoint_staging_capacity_flits: PositiveInt
    burst_quantum_flits: PositiveInt
    evidence: TransportEvidence

    @model_validator(mode="after")
    def identities(self) -> Self:
        unique(tuple(f.fabric_id for f in self.fabrics), "memory transport fabric")
        unique(tuple(o.channel for o in self.overrides), "memory channel override")
        return self


@dataclass(frozen=True)
class MemoryLinkContract:
    plan_sha256: str
    channel: ChannelIdentity
    config: ResolvedLinkConfig
    wire: MemoryWirePlan
    quantum: int
    _templates: Mapping[PacketIdentity, tuple[RoutedMemoryPacket, int]]

    @property
    def lanes(self) -> tuple[LaneIdentity, ...]:
        phases = (0, 1) if self.channel.kind == "network" else (None,)
        return tuple(LaneIdentity(channel=self.channel, traffic_class=kind, dateline_phase=phase)
                     for kind in ("request", "response") for phase in phases)

    def envelope(self, packet: PacketIdentity, flit_index: int) -> PacketFlit:
        template = self._templates.get(packet)
        if template is None:
            raise ValueError("packet has no admitted memory path on this channel")
        item, hop_index = template
        envelope = self.wire.envelope(item.packet.identity, flit_index, hop_index)
        return PacketFlit(plan_sha256=self.plan_sha256, packet=packet,
                          fabric_id=item.route.fabric_id, source=item.route.source, destination=item.route.destination,
                          hop_index=hop_index, lane=envelope.lane, flit_index=flit_index,
                          flit_count=envelope.layout.flit_count, payload_bytes=envelope.useful_bytes,
                          physical_bytes=envelope.physical_bytes, burst_quantum_flits=self.quantum)

    def admit_memory(self, envelope: MemoryWireEnvelope) -> PacketFlit:
        self.wire.validate_envelope(envelope)
        if envelope.lane.channel != self.channel:
            raise ValueError("memory envelope belongs to another channel")
        return self.envelope(envelope.packet.transport_identity, envelope.flit_index)

    def validate(self, envelope: WireFlit) -> PacketFlit:
        if not isinstance(envelope, PacketFlit):
            raise TypeError("memory link requires an admitted internal wire flit")
        checked = PacketFlit.model_validate(envelope.model_dump(mode="json"))
        if checked != self.envelope(checked.packet, checked.flit_index):
            raise ValueError("memory flit differs from admitted plan/layout/path")
        return checked


@dataclass(frozen=True)
class MemoryTransportPlan:
    wire: MemoryWirePlan
    config: MemoryTransportConfig
    network: PacketNetworkPlan

    @classmethod
    def compile(cls, wire: MemoryWirePlan, config: MemoryTransportConfig, *, physical_flit_bytes: int | None = None) -> MemoryTransportPlan:
        # Revalidate copy/construct inputs before callers allocate a network.
        config = MemoryTransportConfig.model_validate(config.model_dump(mode="python"))
        if not wire.packets and physical_flit_bytes is None:
            raise ValueError("memory wire transport requires network packets")
        physical = {p.packet.layout.physical_flit_bytes for p in wire.packets}
        if physical_flit_bytes is not None:
            if type(physical_flit_bytes) is not int or physical_flit_bytes <= 0 or (physical and physical != {physical_flit_bytes}):
                raise ValueError("declared physical width differs from memory packet layout")
            physical = {physical_flit_bytes}
        if len(physical) != 1:
            raise ValueError("memory packets must use one declared physical width")
        graph = wire.routes.routing.topology.graph
        validate_transport_settings(graph, config, physical_flit_bytes=next(iter(physical)), aci_clock_hz=wire.aci_clock_hz)
        fabrics = {f.fabric_id: f for f in config.fabrics}
        overrides = {o.channel: o.settings for o in config.overrides}
        digest = content_digest({"wire_plan_sha256": wire.plan_sha256, "transport": config.model_dump(mode="json")})
        definitions: dict[PacketIdentity, PacketDefinition] = {}
        templates: dict[ChannelIdentity, dict[PacketIdentity, tuple[RoutedMemoryPacket, int]]] = {}
        routers: dict[tuple[int, str], RouterServiceConfig] = {}
        for item in wire.packets:
            packet = item.packet.identity.transport_identity
            purpose = item.packet.identity.purpose
            request = item.packet.identity.model_copy(update={
                "purpose": "read_request" if purpose == "read_response" else "write_request"})
            definitions[packet] = PacketDefinition(packet=packet, route=item.route,
                                                   flit_count=item.packet.layout.flit_count,
                                                   useful_bytes=item.packet.layout.useful_bytes,
                                                   physical_bytes=item.packet.layout.packet_bytes,
                                                   after_packet=request.transport_identity if packet.traffic_class == "response" else None)
            for index, hop in enumerate(item.route.hops):
                templates.setdefault(hop.lane.channel, {})[packet] = (item, index)
                if hop.src_router is not None:
                    routers[(item.route.fabric_id, hop.src_router)] = fabrics[item.route.fabric_id].router
        if any(d.after_packet is not None and d.after_packet not in definitions for d in definitions.values()):
            raise ValueError("response is missing its causal request")
        links: dict[str, LinkService] = {}
        for channel in sorted(templates, key=lambda c: c.model_dump_json()):
            f = fabrics[channel.fabric_id]
            settings = overrides.get(channel, f.network_link if channel.kind == "network" else f.local_link)
            links[channel.model_dump_json()] = MemoryLinkContract(
                digest, channel, settings, wire, config.burst_quantum_flits, MappingProxyType(templates[channel]))
        network = PacketNetworkPlan(digest, MappingProxyType(definitions), MappingProxyType(links),
                                    MappingProxyType(routers), config.endpoint_queue_capacity_packets,
                                    config.endpoint_staging_capacity_flits)
        return cls(wire, config, network)


def validate_transport_settings(graph: CanonicalTopology, config: MemoryTransportConfig, *,
                                physical_flit_bytes: int, aci_clock_hz: float) -> None:
    """Validate shared physical settings before allocating any runtime objects."""
    config = MemoryTransportConfig.model_validate(config.model_dump(mode="python"))
    fabrics = {f.fabric_id: f for f in config.fabrics}
    if set(fabrics) != {f.fabric_id for f in graph.fabrics}:
        raise ValueError("transport settings must cover exactly the graph fabrics")
    for f in config.fabrics:
        for link in (f.network_link, f.local_link):
            if link.slowdowns:
                raise ValueError("slowdowns require an explicit directed channel override")
            if physical_flit_bytes != link.physical_flit_bytes:
                raise ValueError("link physical width differs from memory packet layout")
            if link.aci_clock_hz != aci_clock_hz:
                raise ValueError("transport ACI clock differs from the admitted memory plan")
        if (f.network_link.aci_clock_hz, f.network_link.noc_clock_hz) != (
                f.local_link.aci_clock_hz, f.local_link.noc_clock_hz):
            raise ValueError("local/network clocks disagree")
    if len({f.network_link.aci_clock_hz for f in config.fabrics}) != 1:
        raise ValueError("all fabrics must use one shared ACI clock")
    overrides = {o.channel: o.settings for o in config.overrides}
    graph_links = {(e.fabric_id, e.link_id): e for e in graph.links}
    attachments = {e.endpoint_id: e for e in graph.attachments}
    for channel, settings in overrides.items():
        if channel.fabric_id not in fabrics:
            raise ValueError("override uses an unknown fabric")
        base = fabrics[channel.fabric_id].network_link
        if physical_flit_bytes != settings.physical_flit_bytes or (settings.aci_clock_hz, settings.noc_clock_hz) != (
                base.aci_clock_hz, base.noc_clock_hz):
            raise ValueError("override changes admitted clock or physical width")
        if channel.kind == "network":
            edge = graph_links.get((channel.fabric_id, channel.identity))
            if edge is None or edge.enabled is not True:
                raise ValueError("slowdown/override requires an enabled directed network link")
        else:
            endpoint = attachments.get(channel.identity)
            if endpoint is None or endpoint.fabric_id != channel.fabric_id or not endpoint.replay_enabled or (
                endpoint.inject_port if channel.kind == "inject" else endpoint.eject_port
            ) is None:
                raise ValueError("override requires an admitted local channel")
            if settings.slowdowns:
                raise ValueError("slowdown cannot target a local channel")
        previous = 0.0
        unique(tuple(s[0] for s in settings.slowdowns), "directed slowdown identity")
        for _, start, end, factor in settings.slowdowns:
            if not all(math.isfinite(x) for x in (start, end, factor)) or start < previous or end <= start or factor < 1:
                raise ValueError("invalid or overlapping directed slowdown interval")
            previous = end
            if any(not math.isfinite(settings.aci(native) * factor) for native in (
                    settings.serialization_noc_cycles, settings.launch_interval_noc_cycles,
                    settings.propagation_noc_cycles)):
                raise ValueError("slowdown produces an unrepresentable service duration")
