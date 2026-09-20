"""Immutable composite packet/tree contracts on the existing physical kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .configs.schemas.torus_replay import ChannelIdentity, LaneIdentity, PacketIdentity
from .memory_packets import MemoryPacket, MemoryPacketLayout, packetize
from .multicast_plan import MulticastSyncPlan, MulticastWritePlan
from .packet_runtime import PacketDefinition, PacketNetworkPlan, RouterServiceConfig
from .packet_transport import LinkService, PacketFlit, WireFlit
from .tree_wire import KernelLane, TreeFlit, TreeLaneIdentity, TreePacketIdentity
from .virtual_channel import ResolvedLinkConfig


@dataclass(frozen=True)
class UnicastPacket:
    definition: PacketDefinition
    layout: MemoryPacketLayout
    memory: MemoryPacket | None
    control_id: str | None


@dataclass(frozen=True)
class TreePacket:
    identity: TreePacketIdentity
    write: MulticastWritePlan
    layout: MemoryPacketLayout
    channels: tuple[ChannelIdentity, ...]


@dataclass(frozen=True)
class CompositeLinkContract:
    plan_sha256: str
    channel: ChannelIdentity
    config: ResolvedLinkConfig
    quantum: int
    packets: Mapping[PacketIdentity, tuple[UnicastPacket, int]]
    trees: Mapping[TreePacketIdentity, TreePacket]

    @property
    def lanes(self) -> tuple[KernelLane, ...]:
        phases = (0, 1) if self.channel.kind == "network" else (None,)
        return (*(LaneIdentity(channel=self.channel, traffic_class=kind, dateline_phase=phase)
                  for kind in ("request", "response") for phase in phases), TreeLaneIdentity(channel=self.channel))

    def envelope(self, packet: PacketIdentity, flit_index: int) -> PacketFlit:
        template = self.packets.get(packet)
        if template is None:
            raise ValueError("unicast packet is not admitted on this physical channel")
        item, hop = template
        route = item.definition.route
        return PacketFlit(plan_sha256=self.plan_sha256, packet=packet, fabric_id=route.fabric_id,
                          source=route.source, destination=route.destination, hop_index=hop,
                          lane=route.hops[hop].lane, flit_index=flit_index, flit_count=item.layout.flit_count,
                          payload_bytes=item.layout.flit_useful_bytes(flit_index),
                          physical_bytes=item.layout.physical_flit_bytes, burst_quantum_flits=self.quantum)

    def tree_envelope(self, packet: TreePacketIdentity, flit_index: int) -> TreeFlit:
        item = self.trees.get(packet)
        if item is None:
            raise ValueError("tree packet is not admitted on this physical channel")
        return TreeFlit(plan_sha256=self.plan_sha256, packet=packet, fabric_id=item.write.fabric_id,
                        source=item.write.source_endpoint_id, branch_id=self.channel.identity,
                        lane=TreeLaneIdentity(channel=self.channel), flit_index=flit_index,
                        flit_count=item.layout.flit_count, payload_bytes=item.layout.flit_useful_bytes(flit_index),
                        physical_bytes=item.layout.physical_flit_bytes, burst_quantum_flits=self.quantum)

    def validate(self, envelope: WireFlit) -> PacketFlit | TreeFlit:
        if isinstance(envelope, TreeFlit):
            checked = TreeFlit.model_validate(envelope.model_dump(mode="json"))
            if checked != self.tree_envelope(checked.packet, checked.flit_index):
                raise ValueError("tree flit differs from admitted branch/segment/layout")
            return checked
        if not isinstance(envelope, PacketFlit):
            raise TypeError("composite link requires admitted internal wire metadata")
        checked_packet = PacketFlit.model_validate(envelope.model_dump(mode="json"))
        if checked_packet != self.envelope(checked_packet.packet, checked_packet.flit_index):
            raise ValueError("unicast flit differs from admitted layout/path")
        return checked_packet


@dataclass(frozen=True)
class MulticastNetworkPlan:
    admitted: MulticastSyncPlan
    network: PacketNetworkPlan
    packets: Mapping[PacketIdentity, UnicastPacket]
    trees: Mapping[TreePacketIdentity, TreePacket]

    @classmethod
    def compile(cls, admitted: MulticastSyncPlan) -> MulticastNetworkPlan:
        admitted.revalidate()
        inventory, runtime = admitted.record.inventory, admitted.workload.runtime
        if inventory is None or runtime is None:
            raise ValueError("shared multicast transport requires execution admission")
        memory, settings = admitted.workload.memory, runtime.transport
        geometry = memory.packet
        packets: dict[PacketIdentity, UnicastPacket] = {}
        for control in inventory.controls:
            packet = PacketIdentity(transfer_id=control.packet_id, traffic_class=control.route.traffic_class)
            layout = MemoryPacketLayout(physical_flit_bytes=geometry.physical_flit_bytes,
                                        data_capacity_bytes=geometry.data_capacity_bytes, header_flits=control.flit_count, useful_bytes=0)
            definition = PacketDefinition(packet=packet, route=control.route, flit_count=control.flit_count, useful_bytes=0,
                                          physical_bytes=control.physical_bytes)
            packets[packet] = UnicastPacket(definition, layout, None, control.packet_id)
        routes = {r.operation_id: r for r in inventory.ordinary_routes}
        buffers = {b.buffer_id: b for b in memory.buffers}
        for operation in admitted.execution_workload.operations:
            if operation.operation_id not in routes:
                continue
            route = routes[operation.operation_id]
            assert operation.source is not None and operation.destination is not None
            request: PacketIdentity | None = None
            for packet in packetize(operation, geometry, source_base_address=buffers[operation.source.buffer_id].base_address,
                                    destination_base_address=buffers[operation.destination.buffer_id].base_address):
                identity = packet.identity.transport_identity
                path = route.request if identity.traffic_class == "request" else route.response
                assert path is not None
                if identity.traffic_class == "request":
                    request = identity
                definition = PacketDefinition(packet=identity, route=path, flit_count=packet.layout.flit_count,
                    useful_bytes=packet.layout.useful_bytes, physical_bytes=packet.layout.packet_bytes,
                    after_packet=request if identity.traffic_class == "response" else None)
                if identity in packets:
                    raise ValueError("mixed packet identity collision")
                packets[identity] = UnicastPacket(definition, packet.layout, packet, None)
        writes = {w.operation_id: w for w in admitted.record.writes}
        trees: dict[TreePacketIdentity, TreePacket] = {}
        for tree in inventory.tree_channels:
            write = writes[tree.operation_id]
            segment = write.segments[tree.segment_index]
            identity = TreePacketIdentity(operation_id=tree.operation_id, segment_index=tree.segment_index)
            layout = MemoryPacketLayout(physical_flit_bytes=geometry.physical_flit_bytes,
                                        data_capacity_bytes=geometry.data_capacity_bytes, header_flits=geometry.header_flits,
                                        useful_bytes=segment.payload_bytes)
            trees[identity] = TreePacket(identity, write, layout, tree.channels)
        templates: dict[ChannelIdentity, dict[PacketIdentity, tuple[UnicastPacket, int]]] = {}
        branches: dict[ChannelIdentity, dict[TreePacketIdentity, TreePacket]] = {}
        routers: dict[tuple[int, str], RouterServiceConfig] = {}
        fabrics = {f.fabric_id: f for f in settings.fabrics}
        for packet, item in packets.items():
            for index, hop in enumerate(item.definition.route.hops):
                templates.setdefault(hop.lane.channel, {})[packet] = item, index
                if hop.src_router is not None:
                    routers[(item.definition.route.fabric_id, hop.src_router)] = fabrics[item.definition.route.fabric_id].router
        for identity, tree in trees.items():
            for channel in tree.channels:
                branches.setdefault(channel, {})[identity] = tree
            for node in tree.write.tree.nodes:
                routers[(tree.write.fabric_id, node.router_id)] = fabrics[tree.write.fabric_id].router
        overrides = {o.channel: o.settings for o in settings.overrides}
        links: dict[str, LinkService] = {}
        for channel in sorted(templates.keys() | branches.keys(), key=lambda c: c.model_dump_json()):
            fabric = fabrics[channel.fabric_id]
            config = overrides.get(channel, fabric.network_link if channel.kind == "network" else fabric.local_link)
            links[channel.model_dump_json()] = CompositeLinkContract(admitted.record.plan_sha256, channel, config,
                settings.burst_quantum_flits, MappingProxyType(templates.get(channel, {})), MappingProxyType(branches.get(channel, {})))
        network = PacketNetworkPlan(admitted.record.plan_sha256,
                                    MappingProxyType({p: i.definition for p, i in packets.items()}), MappingProxyType(links),
                                    MappingProxyType(routers), settings.endpoint_queue_capacity_packets, settings.endpoint_staging_capacity_flits)
        return cls(admitted, network, MappingProxyType(packets), MappingProxyType(trees))
