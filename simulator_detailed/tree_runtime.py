"""Live, bounded cut-through multicast on the shared physical transport.

Each forwarder holds its incoming credit until every output accepts the flit.
Copies exist only in charged downstream slots. The replication budget bounds
active forwarders, and router grants never wait for downstream capacity.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Literal, Protocol

import simpy
from simpy.events import Event, ProcessGenerator

from .configs.schemas.topology import GraphRecord, Identifier, Index
from .configs.schemas.torus_replay import ChannelIdentity, Cycles
from .multicast_network import CompositeLinkContract, MulticastNetworkPlan, TreePacket
from .packet_runtime import PhysicalTransportRegistry
from .torus_records import TransportTraceEvent
from .tree_wire import (
    SharedResourceState,
    TreeFlit,
    TreeLaneIdentity,
    TreePacketIdentity,
    TreeTraceEvent,
)
from .virtual_channel import VirtualChannelLink


class TreeHooks(Protocol):
    @property
    def env(self) -> simpy.Environment: ...
    def produce(self, flit: TreeFlit) -> ProcessGenerator: ...
    def consume(self, flit: TreeFlit, endpoint_id: str) -> ProcessGenerator: ...


class TreeRuntimeEvent(GraphRecord):
    time_aci_cycles: Cycles
    action: Literal["reservation_queue", "reservation_acquire", "reservation_ready", "reservation_release",
                    "budget_acquire", "budget_release", "branch_pending", "branch_accept", "terminal",
                    "handoff", "recipient_complete", "tree_drain"]
    packet: TreePacketIdentity
    resource_id: Identifier
    occupied: Index
    flit_index: Index | None = None
    pending_outputs: tuple[ChannelIdentity, ...] = ()


class TreeDelivery(GraphRecord):
    packet: TreePacketIdentity
    endpoint_id: Identifier
    received_flits: Index
    useful_bytes: Index
    complete_aci_cycles: Cycles | None = None


class TreeTransportSnapshot(GraphRecord):
    status: Literal["complete", "incomplete"]
    reason: Literal["drained", "cycle_limit", "idle_with_pending"]
    elapsed_aci_cycles: Cycles
    submitted: tuple[TreePacketIdentity, ...]
    pending: tuple[TreePacketIdentity, ...]
    deliveries: tuple[TreeDelivery, ...]
    resources: tuple[SharedResourceState, ...]
    trace: tuple[TransportTraceEvent | TreeTraceEvent, ...]
    events: tuple[TreeRuntimeEvent, ...]
    physical_channel_bytes: Index


class _Budget:
    def __init__(self, runtime: TreeTransport, identity: str, fabric: int, capacity: int,
                 kind: Literal["replication", "tx_descriptors", "tx_staging", "rx_staging"]):
        self.runtime, self.identity, self.fabric, self.capacity = runtime, identity, fabric, capacity
        self.kind: Literal["replication", "tx_descriptors", "tx_staging", "rx_staging"] = kind
        self.owners: dict[int, TreePacketIdentity] = {}
        self.sequence, self.peak = 0, 0

    def acquire(self, packet: TreePacketIdentity) -> int | None:
        if len(self.owners) == self.capacity:
            return None
        self.sequence += 1
        self.owners[self.sequence] = packet
        self.peak = max(self.peak, len(self.owners))
        self.runtime.log("budget_acquire", packet, self.identity, len(self.owners))
        self.runtime.notify()
        return self.sequence

    def release(self, lease: int) -> None:
        packet = self.owners.pop(lease)
        self.runtime.log("budget_release", packet, self.identity, len(self.owners))
        self.runtime.notify()

    def snapshot(self) -> SharedResourceState:
        return SharedResourceState(resource_id=self.identity, kind=self.kind, fabric_id=self.fabric,
                                   capacity=self.capacity, available=self.capacity - len(self.owners),
                                   occupied=len(self.owners), peak_occupied=self.peak,
                                   owners=tuple(dict.fromkeys(self.owners.values())))


class AtomicTreeReservations:
    """Finite FIFO control admission, atomic grants, and credit-drain release."""

    def __init__(self, runtime: TreeTransport):
        self.runtime = runtime
        self.capacity = runtime.plan.admitted.workload.control.reservation_capacity
        self.pending: deque[TreePacketIdentity] = deque()
        self.owners: dict[ChannelIdentity, TreePacketIdentity] = {}
        self.active: set[TreePacketIdentity] = set()
        self.admitted: set[TreePacketIdentity] = set()
        self.peak = 0
        self.ready = {packet: runtime.env.event() for packet in runtime.plan.trees}
        runtime.env.process(self._serve())

    def request(self, packet: TreePacketIdentity) -> bool:
        if packet not in self.ready or packet in self.admitted:
            raise ValueError("tree reservation is unknown or already admitted")
        if len(self.pending) + len(self.active) == self.capacity:
            return False
        self.admitted.add(packet)
        self.pending.append(packet)
        self.peak = max(self.peak, len(self.pending) + len(self.active))
        self.runtime.log("reservation_queue", packet, "tree-controller", len(self.pending) + len(self.active))
        self.runtime.notify()
        return True

    def _serve(self) -> ProcessGenerator:
        runtime = self.runtime
        control = runtime.plan.admitted.workload.control
        while True:
            while not self.pending:
                yield runtime.changed
            packet = self.pending[0]
            tree = runtime.plan.trees[packet]
            if any(channel in self.owners for channel in tree.channels):
                yield runtime.changed
                continue
            # No yield or partial grant between the capacity check and all claims.
            self.pending.popleft()
            self.active.add(packet)
            self.owners.update(dict.fromkeys(tree.channels, packet))
            runtime.log("reservation_acquire", packet, "tree-controller", len(self.active) + len(self.pending))
            yield runtime.env.timeout(control.reservation_setup_aci_cycles + len(tree.write.tree.edges) * control.reservation_edge_aci_cycles)
            runtime.log("reservation_ready", packet, "tree-controller", len(self.active) + len(self.pending))
            self.ready[packet].succeed()
            runtime.notify()

    def release(self, packet: TreePacketIdentity) -> None:
        if packet not in self.active:
            raise ValueError("foreign or duplicate tree release")
        channels = self.runtime.plan.trees[packet].channels
        if any(not self.runtime.link(channel).lane_drained(TreeLaneIdentity(channel=channel)) for channel in channels):
            raise ValueError("tree grant cannot release before all delayed credits drain")
        if not self.runtime.effects[packet].triggered:
            raise ValueError("tree grant cannot release before terminal/ejection completion")
        for channel in channels:
            if self.owners.pop(channel) != packet:
                raise AssertionError("tree reservation ownership changed")
        self.active.remove(packet)
        self.runtime.log("reservation_release", packet, "tree-controller", len(self.active) + len(self.pending))
        self.runtime.notify()

    def snapshot(self) -> SharedResourceState:
        owners = tuple(p for p in self.runtime.plan.trees if p in self.active or p in self.pending)
        return SharedResourceState(resource_id="tree-controller", kind="tree_reservations", fabric_id=0,
                                   capacity=self.capacity, available=self.capacity - len(owners), occupied=len(owners),
                                   peak_occupied=self.peak, owners=owners)


class TreeTransport:
    def __init__(self, env: simpy.Environment, plan: MulticastNetworkPlan, registry: PhysicalTransportRegistry,
                 hooks: TreeHooks):
        if hooks.env is not env:
            raise ValueError("tree hooks require the shared environment")
        registry.attach("multicast", env, plan.network)
        self.env, self.plan, self.registry, self.hooks = env, plan, registry, hooks
        self._changed = env.event()
        self.events: list[TreeRuntimeEvent] = []
        self.submitted: set[TreePacketIdentity] = set()
        self.handoffs = {p: env.event() for p in plan.trees}
        self.effects = {p: env.event() for p in plan.trees}
        self.drains = {p: env.event() for p in plan.trees}
        self._completed_nodes: dict[TreePacketIdentity, set[str]] = {p: set() for p in plan.trees}
        self.deliveries = {(p, r.endpoint_id): TreeDelivery(packet=p, endpoint_id=r.endpoint_id, received_flits=0, useful_bytes=0)
                           for p, t in plan.trees.items() for r in t.write.tree.recipients}
        memory, control = plan.admitted.workload.memory, plan.admitted.workload.control
        sources = {(t.write.fabric_id, t.write.source_endpoint_id) for t in plan.trees.values()}
        targets = {(t.write.fabric_id, r.endpoint_id) for t in plan.trees.values() for r in t.write.tree.recipients}
        self.tx_descriptors = {k: _Budget(self, f"tree-tx-descriptors:{k[1]}", k[0], memory.endpoint_queue_capacity_packets, "tx_descriptors") for k in sorted(sources)}
        self.tx_staging = {k: _Budget(self, f"tree-tx-staging:{k[1]}", k[0], memory.endpoint_staging_capacity_flits, "tx_staging") for k in sorted(sources)}
        self.rx_staging = {k: _Budget(self, f"tree-rx-staging:{k[1]}", k[0], memory.endpoint_staging_capacity_flits, "rx_staging") for k in sorted(targets)}
        self.replication = {k: _Budget(self, f"replication:{k[0]}:{k[1]}", k[0], control.replication_capacity_flits, "replication") for k in registry.pipelines}
        self.reservations = AtomicTreeReservations(self)
        # Exactly one consumer per physical multicast lane, shared by the finite
        # packet inventory. Unicast consumers attach only to request/response lanes.
        incoming: set[ChannelIdentity] = set()
        for tree in plan.trees.values():
            incoming.update(c for c in tree.channels if c.kind != "eject")
        for channel in sorted(incoming, key=lambda c: c.model_dump_json()):
            env.process(self._forward(channel))
        for fabric, endpoint in sorted(targets):
            env.process(self._receive(ChannelIdentity(fabric_id=fabric, kind="eject", identity=endpoint)))

    @property
    def changed(self) -> Event:
        return self._changed

    def notify(self) -> None:
        self._changed.succeed()
        self._changed = self.env.event()

    def log(self, action: Literal["reservation_queue", "reservation_acquire", "reservation_ready", "reservation_release",
                                 "budget_acquire", "budget_release", "branch_pending", "branch_accept", "terminal",
                                 "handoff", "recipient_complete", "tree_drain"],
            packet: TreePacketIdentity, resource: str, occupied: int, *, flit: int | None = None,
            pending: tuple[ChannelIdentity, ...] = ()) -> None:
        self.events.append(TreeRuntimeEvent(time_aci_cycles=self.env.now, action=action, packet=packet,
                                            resource_id=resource, occupied=occupied, flit_index=flit, pending_outputs=pending))

    def link(self, channel: ChannelIdentity) -> VirtualChannelLink:
        return self.registry.links[channel.model_dump_json()]

    def envelope(self, channel: ChannelIdentity, packet: TreePacketIdentity, index: int) -> TreeFlit:
        contract = self.link(channel).contract
        if not isinstance(contract, CompositeLinkContract):
            raise TypeError("tree requires a composite physical contract")
        return contract.tree_envelope(packet, index)

    def try_submit(self, packet: TreePacketIdentity) -> bool:
        if packet not in self.plan.trees or packet in self.submitted:
            raise ValueError("tree packet is unknown or already submitted")
        tree = self.plan.trees[packet]
        key = tree.write.fabric_id, tree.write.source_endpoint_id
        descriptor = self.tx_descriptors[key]
        if len(descriptor.owners) == descriptor.capacity or not self.reservations.request(packet):
            return False
        lease = descriptor.acquire(packet)
        assert lease is not None
        self.submitted.add(packet)
        self.env.process(self._inject(tree, lease))
        self.env.process(self._drain(tree))
        return True

    def _acquire(self, budget: _Budget, packet: TreePacketIdentity) -> ProcessGenerator:
        lease = budget.acquire(packet)
        while lease is None:
            yield self.changed
            lease = budget.acquire(packet)
        return lease

    def _inject(self, tree: TreePacket, descriptor: int) -> ProcessGenerator:
        packet = tree.identity
        yield self.reservations.ready[packet]
        key = tree.write.fabric_id, tree.write.source_endpoint_id
        channel = ChannelIdentity(fabric_id=key[0], kind="inject", identity=key[1])
        link, staging = self.link(channel), self.tx_staging[key]
        for index in range(tree.layout.flit_count):
            lease = yield from self._acquire(staging, packet)
            flit = self.envelope(channel, packet, index)
            yield from self.hooks.produce(flit)
            token = link.try_reserve(flit)
            while token is None:
                yield link.changed
                token = link.try_reserve(flit)
            link.make_ready(token)
            staging.release(lease)
            del token, flit
        self.tx_descriptors[key].release(descriptor)
        self.handoffs[packet].succeed()
        self.log("handoff", packet, key[1], 0)
        self.notify()

    def _forward(self, channel: ChannelIdentity) -> ProcessGenerator:
        link, lane = self.link(channel), TreeLaneIdentity(channel=channel)
        while True:
            token = link.take(lane)
            while token is None:
                yield link.changed
                token = link.take(lane)
            flit = token.envelope
            assert isinstance(flit, TreeFlit)
            tree = self.plan.trees[flit.packet]
            incoming_edge = next((e for e in tree.write.tree.edges if e.link_id == channel.identity), None)
            router = tree.write.tree.source_router_id if channel.kind == "inject" else incoming_edge.dst_router if incoming_edge else None
            assert router is not None
            node = next(n for n in tree.write.tree.nodes if n.router_id == router)
            pipeline = self.registry.pipelines[(channel.fabric_id, router)]
            budget = self.replication[(channel.fabric_id, router)]
            lease = yield from self._acquire(budget, flit.packet)
            outputs = [ChannelIdentity(fabric_id=channel.fabric_id, kind="network", identity=e.link_id)
                       for e in tree.write.tree.edges if e.edge_id in node.outgoing_edge_ids]
            if node.recipient_endpoint_id is not None:
                outputs.append(ChannelIdentity(fabric_id=channel.fabric_id, kind="eject", identity=node.recipient_endpoint_id))
            self.log("branch_pending", flit.packet, router, len(budget.owners), flit=flit.flit_index, pending=tuple(outputs))
            while outputs:
                target = outputs[0]
                outgoing = self.link(target)
                out_token = outgoing.try_reserve(self.envelope(target, flit.packet, flit.flit_index))
                while out_token is None:
                    yield outgoing.changed
                    out_token = outgoing.try_reserve(self.envelope(target, flit.packet, flit.flit_index))
                while not pipeline.try_start(target, flit.packet):
                    yield pipeline.changed
                outgoing.log_event("transfer_start", out_token, duration=pipeline.transfer_aci_cycles, router_id=router)
                yield self.env.timeout(pipeline.transfer_aci_cycles)
                outgoing.log_event("transfer_end", out_token, duration=pipeline.transfer_aci_cycles, router_id=router)
                outgoing.make_ready(out_token)
                pipeline.finish(target, flit.packet)
                outputs.pop(0)
                self.log("branch_accept", flit.packet, router, len(budget.owners), flit=flit.flit_index, pending=tuple(outputs))
                del out_token
            if node.terminal and node.recipient_endpoint_id is None:
                while not pipeline.try_start(channel, flit.packet):
                    yield pipeline.changed
                yield self.env.timeout(pipeline.transfer_aci_cycles)
                pipeline.finish(channel, flit.packet)
                self.log("terminal", flit.packet, router, len(budget.owners), flit=flit.flit_index)
            link.release(token)
            budget.release(lease)
            if flit.is_tail:
                self._completed_nodes[flit.packet].add(router)
                self._effect_check(flit.packet)
            del token, flit

    def _receive(self, channel: ChannelIdentity) -> ProcessGenerator:
        link, lane = self.link(channel), TreeLaneIdentity(channel=channel)
        budget = self.rx_staging[(channel.fabric_id, channel.identity)]
        while True:
            token = link.take(lane)
            while token is None:
                yield link.changed
                token = link.take(lane)
            flit = token.envelope
            assert isinstance(flit, TreeFlit)
            lease = yield from self._acquire(budget, flit.packet)
            state = self.deliveries[(flit.packet, channel.identity)]
            if state.received_flits != flit.flit_index:
                raise ValueError("duplicate or out-of-order tree ejection")
            yield from self.hooks.consume(flit, channel.identity)
            self.deliveries[(flit.packet, channel.identity)] = state.model_copy(update={
                "received_flits": state.received_flits + 1, "useful_bytes": state.useful_bytes + flit.payload_bytes,
                "complete_aci_cycles": float(self.env.now) if flit.is_tail else None})
            link.log_event("sink_complete", token)
            link.release(token)
            budget.release(lease)
            if flit.is_tail:
                self.log("recipient_complete", flit.packet, channel.identity, 0)
                self._effect_check(flit.packet)
            del token, flit

    def _effect_check(self, packet: TreePacketIdentity) -> None:
        tree = self.plan.trees[packet]
        if (len(self._completed_nodes[packet]) == len(tree.write.tree.nodes)
                and all(self.deliveries[(packet, r.endpoint_id)].complete_aci_cycles is not None for r in tree.write.tree.recipients)
                and not self.effects[packet].triggered):
            self.effects[packet].succeed()
            self.notify()

    def _drain(self, tree: TreePacket) -> ProcessGenerator:
        yield self.effects[tree.identity]
        while not all(self.link(c).lane_drained(TreeLaneIdentity(channel=c)) for c in tree.channels):
            yield self.env.any_of([self.link(c).changed for c in tree.channels])
        self.reservations.release(tree.identity)
        self.drains[tree.identity].succeed()
        self.log("tree_drain", tree.identity, "tree-controller", 0)
        self.notify()

    def snapshot(self) -> TreeTransportSnapshot:
        resources = tuple(r for link in self.registry.links.values() for r in link.shared_resources()) + tuple(
            p.shared_resources() for p in self.registry.pipelines.values()) + tuple(
            b.snapshot() for pools in (self.tx_descriptors, self.tx_staging, self.rx_staging, self.replication)
            for b in pools.values()) + (self.reservations.snapshot(),)
        trace = tuple(sorted((e for link in self.registry.links.values() for e in link.all_events),
                             key=lambda e: (e.time_aci_cycles, e.action, e.fabric_id, e.token_id or "")))
        pending = tuple(p for p in self.plan.trees if not self.drains[p].triggered)
        complete = not pending and self.registry.is_drained and all(r.is_drained for r in resources)
        return TreeTransportSnapshot(status="complete" if complete else "incomplete",
            reason="drained" if complete else "idle_with_pending" if self.env.peek() == float("inf") else "cycle_limit",
            elapsed_aci_cycles=self.env.now, submitted=tuple(p for p in self.plan.trees if p in self.submitted), pending=pending,
            deliveries=tuple(self.deliveries.values()), resources=resources, trace=trace, events=tuple(self.events),
            physical_channel_bytes=sum(e.physical_bytes for e in trace if e.action == "link_launch"))

    def run(self, *, max_aci_cycles: float) -> TreeTransportSnapshot:
        if not math.isfinite(max_aci_cycles) or max_aci_cycles <= self.env.now:
            raise ValueError("tree horizon must be finite and later than current time")
        while self.env.peek() != float("inf") and self.env.peek() <= max_aci_cycles:
            self.env.step()
        return self.snapshot()
