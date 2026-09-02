from collections import defaultdict, deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, cast

import simpy
from simpy.events import Event as SimpyEvent
from simpy.events import Process, ProcessGenerator

from .configs.schemas.arch_config import LinkConfig, NoCConfig, RouterConfig
from .utils.definitions import (
    DIR_EAST,
    DIR_NORTH,
    DIR_SOUTH,
    DIR_WEST,
    PORT_PE,
    BurstLenMode,
    Direction,
    Event,
    Flit,
    NoCChannel,
    NoCPlane,
    direction_to_port,
)

TraceMessageKey = tuple[NoCChannel, int]


class FlitAction(IntEnum):
    INJECT = 0
    EJECT = 1
    ROUTER_FLIT_ARR = 2
    ROUTER_RC = 3
    ROUTER_SA_GRANT = 4
    STALL_SA = 5
    ROUTER_ST = 6
    LINK_SEND = 7
    LINK_RECV = 8
    STALL_CREDIT = 9
    CREDIT_RETURN = 10
    ROUTER_SA_RELEASE = 11


@dataclass(frozen=True)
class FlitEvent:
    """One tracer event whose `time` is always an ACI-cycle timestamp."""

    time: float
    action: FlitAction
    fabric_id: NoCChannel
    plane: NoCPlane
    router_id: int = -1
    port: int = -1
    msg_id: int = -1
    payload_bytes: int = 0
    src_router: int = -1
    dst_router: int = -1
    out_port: int = -1
    link_name: str = ""
    grant_flits: int = 0
    is_tail: bool = False


@dataclass(frozen=True)
class MessageFabricTiming:
    """Router-boundary DATA-plane timing for one completed message packet."""

    fabric_id: NoCChannel
    msg_id: int
    first_injection_time_aci_cycles: float
    first_ejection_time_aci_cycles: float
    final_ejection_time_aci_cycles: float

    @property
    def first_flit_fabric_latency_aci_cycles(self) -> float:
        return (
            self.first_ejection_time_aci_cycles
            - self.first_injection_time_aci_cycles
        )

    @property
    def packet_fabric_completion_latency_aci_cycles(self) -> float:
        return (
            self.final_ejection_time_aci_cycles
            - self.first_injection_time_aci_cycles
        )


@dataclass(frozen=True)
class NoCLinkIdentity:
    """Fabric-qualified identity of one directional inter-router link."""

    fabric_id: NoCChannel
    link_id: int
    src_router: int
    dst_router: int


class NoCTracer:
    def __init__(self, fabric_id: NoCChannel):
        self.fabric_id = fabric_id
        self.events: List[FlitEvent] = []
        self._enabled = True

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def clear(self):
        self.events.clear()

    def log(
        self,
        time: float,
        action: FlitAction,
        *,
        router_id: int = -1,
        port: int = -1,
        flit: Optional[Flit] = None,
        out_port: int = -1,
        link_name: str = "",
        plane: NoCPlane = NoCPlane.DATA,
        grant_flits: int = 0,
    ):
        if not self._enabled:
            return
        if flit is not None and flit.fabric_id is not self.fabric_id:
            raise ValueError(
                f"{flit.fabric_id.name} flit cannot be recorded by "
                f"{self.fabric_id.name} tracer"
            )
        self.events.append(
            FlitEvent(
                time=time,
                action=action,
                fabric_id=self.fabric_id,
                plane=plane,
                router_id=router_id,
                port=port,
                msg_id=-1 if flit is None else flit.msg_id,
                payload_bytes=0 if flit is None else flit.payload_bytes,
                src_router=-1 if flit is None else flit.src_router,
                dst_router=-1 if flit is None else flit.dst_router,
                out_port=out_port,
                link_name=link_name,
                grant_flits=grant_flits,
                is_tail=False if flit is None else flit.is_tail,
            )
        )

    def message_fabric_timings(
        self,
    ) -> Dict[TraceMessageKey, MessageFabricTiming]:
        """Return completed packet timings at source/destination router edges."""
        injected: Dict[TraceMessageKey, float] = {}
        first_ejected: Dict[TraceMessageKey, float] = {}
        final_ejected: Dict[TraceMessageKey, float] = {}
        for event in self.events:
            if event.plane is not NoCPlane.DATA:
                continue
            message_key = (event.fabric_id, event.msg_id)
            if event.action is FlitAction.INJECT:
                injected.setdefault(message_key, event.time)
            elif event.action is FlitAction.EJECT:
                first_ejected.setdefault(message_key, event.time)
                if event.is_tail:
                    final_ejected[message_key] = event.time
        return {
            message_key: MessageFabricTiming(
                fabric_id=message_key[0],
                msg_id=message_key[1],
                first_injection_time_aci_cycles=start,
                first_ejection_time_aci_cycles=first_ejected[message_key],
                final_ejection_time_aci_cycles=final_ejected[message_key],
            )
            for message_key, start in injected.items()
            if message_key in first_ejected and message_key in final_ejected
        }

    def first_flit_fabric_latencies(self) -> Dict[TraceMessageKey, float]:
        """Return first INJECT-to-first EJECT latency in ACI cycles."""
        return {
            message_key: timing.first_flit_fabric_latency_aci_cycles
            for message_key, timing in self.message_fabric_timings().items()
        }

    def packet_fabric_completion_latencies(
        self,
    ) -> Dict[TraceMessageKey, float]:
        """Return first INJECT-to-final EJECT latency in ACI cycles."""
        return {
            message_key: timing.packet_fabric_completion_latency_aci_cycles
            for message_key, timing in self.message_fabric_timings().items()
        }

    def per_msg_latency(self) -> Dict[TraceMessageKey, float]:
        """Compatibility alias for packet fabric completion latency."""
        return self.packet_fabric_completion_latencies()

    def summary(self, end_time: float) -> str:
        counts = {
            action.name: sum(event.action == action for event in self.events)
            for action in FlitAction
        }
        timings = self.message_fabric_timings()
        avg_first_flit_latency = (
            sum(
                timing.first_flit_fabric_latency_aci_cycles
                for timing in timings.values()
            )
            / len(timings)
            if timings
            else 0.0
        )
        avg_packet_completion_latency = (
            sum(
                timing.packet_fabric_completion_latency_aci_cycles
                for timing in timings.values()
            )
            / len(timings)
            if timings
            else 0.0
        )
        return (
            f"fabric={self.fabric_id.name} "
            f"timebase=aci_cycles cycles={end_time:.3f} "
            f"events={len(self.events)} "
            f"messages={len(timings)} "
            "avg_first_flit_fabric_latency_aci_cycles="
            f"{avg_first_flit_latency:.3f} "
            "avg_packet_fabric_completion_latency_aci_cycles="
            f"{avg_packet_completion_latency:.3f} "
            + " ".join(f"{name}={count}" for name, count in counts.items())
        )


class RoundRobinArbiter:
    """One rotating grant owner with at most one request per input port."""

    def __init__(self, env: simpy.Environment):
        self.env = env
        self._owner: int | None = None
        self._last_owner: int | None = None
        self._pending: dict[int, SimpyEvent] = {}

    @property
    def owner(self) -> int | None:
        return self._owner

    @property
    def pending_ports(self) -> tuple[int, ...]:
        return tuple(sorted(self._pending))

    def request(self, input_port: int) -> SimpyEvent:
        if self._owner == input_port or input_port in self._pending:
            raise RuntimeError(
                f"input port {input_port} already owns or awaits this arbiter"
            )
        request = self.env.event()
        self._pending[input_port] = request
        self._grant_next()
        return request

    def release(self, input_port: int) -> None:
        if self._owner != input_port:
            raise RuntimeError(
                f"input port {input_port} cannot release owner {self._owner}"
            )
        self._owner = None
        self._last_owner = input_port
        self._grant_next()

    def _grant_next(self) -> None:
        if self._owner is not None or not self._pending:
            return
        ordered_ports = sorted(self._pending)
        next_port = ordered_ports[0]
        if self._last_owner is not None:
            next_port = next(
                (
                    port
                    for port in ordered_ports
                    if port > self._last_owner
                ),
                next_port,
            )
        request = self._pending.pop(next_port)
        self._owner = next_port
        request.succeed()


@dataclass(frozen=True)
class PacketRouteState:
    """HEAD-established route metadata retained through TAIL."""

    msg_id: int
    out_port: int
    burst_len_mode: BurstLenMode
    burst_quantum_flits: int


@dataclass
class SwitchGrantState:
    """Temporary output ownership for one packet burst."""

    out_port: int
    transmitted_flits: int = 0


class Link:
    """Unidirectional data path with credit return on the sync plane."""

    def __init__(
        self,
        env: simpy.Environment,
        config: LinkConfig,
        fabric_id: NoCChannel,
        tracer: NoCTracer,
        link_name: str = "",
        *,
        noc_cycles_per_aci_cycle: float,
        link_id: int | None = None,
        src_router: int | None = None,
        dst_router: int | None = None,
    ):
        if tracer.fabric_id is not fabric_id:
            raise ValueError(
                f"{fabric_id.name} link cannot use {tracer.fabric_id.name} tracer"
            )
        self.env = env
        self.config = config
        self.fabric_id = fabric_id
        self.tracer = tracer
        self.link_name = f"{fabric_id.name}:{link_name or 'unnamed-link'}"
        identity_fields = (link_id, src_router, dst_router)
        if any(field is None for field in identity_fields) and not all(
            field is None for field in identity_fields
        ):
            raise ValueError(
                "inter-router link identity requires link, source, and destination IDs"
            )
        self._identity = (
            None
            if link_id is None or src_router is None or dst_router is None
            else NoCLinkIdentity(
                fabric_id=fabric_id,
                link_id=link_id,
                src_router=src_router,
                dst_router=dst_router,
            )
        )
        self.serialization_noc_cycles = config.serialization_noc_cycles()
        self.serialization_aci_cycles = config.serialization_aci_cycles(
            noc_cycles_per_aci_cycle,
        )
        if config.launch_interval_aci_cycles < self.serialization_aci_cycles:
            raise ValueError("launch interval cannot be shorter than serialization")
        required_window = config.required_in_flight_window_flits(
            noc_cycles_per_aci_cycle
        )
        if config.effective_in_flight_window_flits < required_window:
            raise ValueError(
                "effective in-flight window must contain at least "
                f"{required_window} flits for the configured zero-load timing"
            )
        self.launch_interval_aci_cycles = config.launch_interval_aci_cycles
        self.effective_link_stage_aci_cycles = (
            config.effective_link_stage_aci_cycles
        )
        self.sync_credit_return_aci_cycles = config.sync_credit_return_aci_cycles
        self.delay_factor = 1.0

        self.flit_buffer = simpy.Store(
            env,
            capacity=config.input_buffer_depth_flits,
        )
        self.in_flight_credits = simpy.Container(
            env,
            init=config.effective_in_flight_window_flits,
            capacity=config.effective_in_flight_window_flits,
        )
        self._out_queue = simpy.Store(env, capacity=1)
        self.env.process(self._transmit_loop())

    def send_flit(self, flit: Flit) -> Process:
        self._validate_flit_fabric(flit)
        return self.env.process(self._send_flit(flit))

    def recv_flit(self) -> Process:
        return self.env.process(self._recv_flit())

    def ack_credit(self) -> Process:
        return self.env.process(self._return_credit())

    @property
    def identity(self) -> NoCLinkIdentity:
        """Return the identity of an inter-router link."""
        if self._identity is None:
            raise ValueError(f"{self.link_name} is not an inter-router link")
        return self._identity

    @property
    def in_flight_flits(self) -> int:
        """Return flits holding effective pipeline/window capacity."""
        return int(self.in_flight_credits.capacity - self.in_flight_credits.level)

    def utilization_events(self) -> list[Event]:
        """Build non-duplicated link occupancy intervals from tracer events."""
        pending: dict[int, deque[FlitEvent]] = defaultdict(deque)
        intervals: list[Event] = []
        for trace_event in self.tracer.events:
            if trace_event.link_name != self.link_name:
                continue
            if trace_event.action is FlitAction.LINK_SEND:
                pending[trace_event.msg_id].append(trace_event)
                continue
            if trace_event.action is not FlitAction.LINK_RECV:
                continue
            starts = pending.get(trace_event.msg_id)
            if not starts:
                raise RuntimeError(
                    f"{self.link_name} received message {trace_event.msg_id} "
                    "without a matching send event"
                )
            start_event = starts.popleft()
            intervals.append(
                Event(
                    index=trace_event.msg_id,
                    start_time=start_event.time,
                    end_time=trace_event.time,
                    src_id=self.identity.src_router,
                    dst_id=self.identity.dst_router,
                    data_size=trace_event.payload_bytes,
                    flit_count=1,
                    fabric_id=self.fabric_id,
                )
            )
        return intervals

    def scale_link_delay(self, factor: float):
        if factor <= 0:
            raise ValueError("link delay scale factor must be positive")
        self.delay_factor *= factor

    def _validate_flit_fabric(self, flit: Flit) -> None:
        if flit.fabric_id is not self.fabric_id:
            raise ValueError(
                f"{flit.fabric_id.name} flit cannot enter {self.link_name}"
            )

    def _send_flit(self, flit: Flit) -> ProcessGenerator:
        credit_request = self.in_flight_credits.get(1)
        if not credit_request.triggered:
            self.tracer.log(
                self.env.now,
                FlitAction.STALL_CREDIT,
                flit=flit,
                link_name=self.link_name,
            )
        yield credit_request
        yield self._out_queue.put(flit)

    def _recv_flit(self) -> ProcessGenerator:
        flit = cast(Flit, (yield self.flit_buffer.get()))
        return flit

    def _transmit_loop(self) -> ProcessGenerator:
        while True:
            flit = cast(Flit, (yield self._out_queue.get()))
            self.tracer.log(
                self.env.now,
                FlitAction.LINK_SEND,
                flit=flit,
                link_name=self.link_name,
            )
            yield self.env.timeout(
                self.serialization_aci_cycles * self.delay_factor
            )
            self.env.process(self._wire_deliver(flit))
            launch_gap = (
                self.launch_interval_aci_cycles - self.serialization_aci_cycles
            )
            if launch_gap > 0:
                yield self.env.timeout(launch_gap * self.delay_factor)

    def _wire_deliver(self, flit: Flit) -> ProcessGenerator:
        yield self.env.timeout(
            self.effective_link_stage_aci_cycles * self.delay_factor
        )
        yield self.flit_buffer.put(flit)
        self.tracer.log(
            self.env.now,
            FlitAction.LINK_RECV,
            flit=flit,
            link_name=self.link_name,
        )

    def _return_credit(self) -> ProcessGenerator:
        if self.sync_credit_return_aci_cycles > 0:
            yield self.env.timeout(self.sync_credit_return_aci_cycles)
        yield self.in_flight_credits.put(1)
        self.tracer.log(
            self.env.now,
            FlitAction.CREDIT_RETURN,
            link_name=self.link_name,
            plane=NoCPlane.SYNC,
        )


class Router:
    """One-VC wormhole router with burst-level round-robin allocation."""

    def __init__(
        self,
        env: simpy.Environment,
        config: RouterConfig,
        router_id: int,
        x_dim: int,
        y_dim: int,
        fabric_id: NoCChannel,
        tracer: NoCTracer,
    ):
        self.id = router_id
        self.fabric_id = fabric_id
        self.name = f"{fabric_id.name}:R{router_id}"
        if config.type != "XY":
            raise ValueError(
                f"{self.name} supports only deterministic XY routing"
            )
        if config.vc != 1:
            raise ValueError(f"{self.name} requires exactly one VC per port")
        if config.arbitration != "round_robin":
            raise ValueError(
                f"{self.name} supports only round-robin output arbitration"
            )
        if tracer.fabric_id is not fabric_id:
            raise ValueError(
                f"{self.name} cannot use {tracer.fabric_id.name} tracer"
            )

        self.env = env
        self.config = config
        self.x_dim = x_dim
        self.y_dim = y_dim
        self.tracer = tracer

        rx, ry = self.to_xy(router_id)
        self.is_edge: Dict[Direction, bool] = {
            Direction.NORTH: ry == y_dim - 1,
            Direction.SOUTH: ry == 0,
            Direction.EAST: rx == x_dim - 1,
            Direction.WEST: rx == 0,
        }
        direction_ports = (DIR_NORTH, DIR_SOUTH, DIR_EAST, DIR_WEST)
        self.port_in: Dict[int, Optional[Link]] = {
            port: None for port in direction_ports
        }
        self.port_out: Dict[int, Optional[Link]] = {
            port: None for port in direction_ports
        }
        self.output_arbiters: Dict[int, RoundRobinArbiter] = {}
        self.reservation: Dict[int, PacketRouteState] = {}
        self._switch_grants: Dict[int, SwitchGrantState] = {}
        self._forwarder_started: Dict[int, bool] = {}

    def bind_link(self, port: int, link_in: Link, link_out: Link):
        for link in (link_in, link_out):
            if link.fabric_id is not self.fabric_id:
                raise ValueError(
                    f"{self.name} cannot bind {link.link_name} from another fabric"
                )
            if link.tracer is not self.tracer:
                raise ValueError(
                    f"{self.name} cannot bind {link.link_name} with a different tracer"
                )
        if self.port_in.get(port) is not None or self.port_out.get(port) is not None:
            raise ValueError(f"{self.name} port {port} is already bound")
        self.port_in[port] = link_in
        self.port_out[port] = link_out
        self.output_arbiters[port] = RoundRobinArbiter(self.env)
        self._forwarder_started[port] = True
        self.env.process(self._port_forwarder(port))

    def scale_link_delay(self, factor: float):
        for port in self.port_in:
            link_in = self.port_in[port]
            link_out = self.port_out[port]
            if link_in is not None:
                link_in.scale_link_delay(factor)
            if link_out is not None:
                link_out.scale_link_delay(factor)

    def _port_forwarder(self, in_port: int) -> ProcessGenerator:
        in_link = self.port_in[in_port]
        assert in_link is not None

        while True:
            flit = cast(Flit, (yield in_link.recv_flit()))
            self._validate_flit_fabric(flit)
            self.tracer.log(
                self.env.now,
                FlitAction.ROUTER_FLIT_ARR,
                router_id=self.id,
                port=in_port,
                flit=flit,
            )
            if in_port == flit.src_local_port and self.id == flit.src_router:
                self.tracer.log(
                    self.env.now,
                    FlitAction.INJECT,
                    router_id=self.id,
                    port=in_port,
                    flit=flit,
                )

            route_state = self._rc_compute(in_port, flit)
            out_port = route_state.out_port
            if flit.is_head:
                self.tracer.log(
                    self.env.now,
                    FlitAction.ROUTER_RC,
                    router_id=self.id,
                    port=in_port,
                    flit=flit,
                    out_port=out_port,
                )
                yield self.env.timeout(
                    self.config.pipeline.effective_rc_aci_cycles
                )

            if in_port not in self._switch_grants:
                yield from self._acquire_switch_grant(
                    in_port,
                    out_port,
                    flit,
                )

            in_link.ack_credit()
            self.tracer.log(
                self.env.now,
                FlitAction.ROUTER_ST,
                router_id=self.id,
                port=in_port,
                flit=flit,
                out_port=out_port,
            )
            yield self.env.timeout(
                self.config.pipeline.effective_st_aci_cycles
            )

            out_link = self.port_out.get(out_port)
            if out_link is None:
                raise RuntimeError(
                    f"{self.name} output port {out_port} is unbound"
                )
            yield out_link.send_flit(flit)

            if self.id == flit.dst_router and out_port == flit.dst_local_port:
                self.tracer.log(
                    self.env.now,
                    FlitAction.EJECT,
                    router_id=self.id,
                    port=out_port,
                    flit=flit,
                )
            self._post_send(in_port, out_port, flit)

    def _rc_compute(self, in_port: int, flit: Flit) -> PacketRouteState:
        if not flit.is_head:
            route_state = self.reservation.get(in_port)
            if route_state is None:
                raise RuntimeError(
                    f"{self.name} received {flit.flit_type.name} without HEAD"
                )
            if route_state.msg_id != flit.msg_id:
                raise RuntimeError(
                    f"{self.name} received message {flit.msg_id} before "
                    f"message {route_state.msg_id} reached TAIL"
                )
            if route_state.burst_len_mode is not flit.burst_len_mode:
                raise RuntimeError(
                    f"{self.name} message {flit.msg_id} changed burst mode"
                )
            return route_state

        if in_port in self.reservation:
            active_msg_id = self.reservation[in_port].msg_id
            raise RuntimeError(
                f"{self.name} received message {flit.msg_id} HEAD before "
                f"message {active_msg_id} reached TAIL"
            )

        if not 0 <= flit.dst_router < self.x_dim * self.y_dim:
            raise ValueError(f"destination router {flit.dst_router} is out of range")
        rx, ry = self.to_xy(self.id)
        tx, ty = self.to_xy(flit.dst_router)
        if tx != rx:
            direction = Direction.EAST if tx > rx else Direction.WEST
            out_port = direction_to_port(direction)
        elif ty != ry:
            direction = Direction.NORTH if ty > ry else Direction.SOUTH
            out_port = direction_to_port(direction)
        else:
            out_port = flit.dst_local_port
        route_state = PacketRouteState(
            msg_id=flit.msg_id,
            out_port=out_port,
            burst_len_mode=flit.burst_len_mode,
            burst_quantum_flits=self.config.resolve_burst_quantum_flits(
                flit.burst_len_mode
            ),
        )
        self.reservation[in_port] = route_state
        return route_state

    def _acquire_switch_grant(
        self,
        in_port: int,
        out_port: int,
        flit: Flit,
    ) -> ProcessGenerator:
        arbiter = self.output_arbiters.get(out_port)
        if arbiter is None:
            raise RuntimeError(
                f"{self.name} output port {out_port} is unbound"
            )
        request = arbiter.request(in_port)
        granted_immediately = request.triggered
        if not granted_immediately:
            self.tracer.log(
                self.env.now,
                FlitAction.STALL_SA,
                router_id=self.id,
                port=in_port,
                flit=flit,
                out_port=out_port,
            )
        yield request
        self._switch_grants[in_port] = SwitchGrantState(out_port=out_port)
        self.tracer.log(
            self.env.now,
            FlitAction.ROUTER_SA_GRANT,
            router_id=self.id,
            port=in_port,
            flit=flit,
            out_port=out_port,
        )
        if flit.is_head or not granted_immediately:
            yield self.env.timeout(
                self.config.pipeline.effective_sa_aci_cycles
            )

    def _post_send(self, in_port: int, out_port: int, flit: Flit):
        route_state = self.reservation.get(in_port)
        if route_state is None:
            raise RuntimeError(
                f"{self.name} transmitted message {flit.msg_id} without a route"
            )
        grant_state = self._switch_grants.get(in_port)
        if grant_state is None or grant_state.out_port != out_port:
            raise RuntimeError(
                f"{self.name} transmitted message {flit.msg_id} without a grant"
            )
        grant_state.transmitted_flits += 1
        release_grant = (
            flit.is_tail
            or grant_state.transmitted_flits
            >= route_state.burst_quantum_flits
        )
        if not release_grant:
            return

        grant_flits = grant_state.transmitted_flits
        del self._switch_grants[in_port]
        arbiter = self.output_arbiters.get(out_port)
        if arbiter is None:
            raise RuntimeError(
                f"{self.name} output port {out_port} is unbound"
            )
        arbiter.release(in_port)
        self.tracer.log(
            self.env.now,
            FlitAction.ROUTER_SA_RELEASE,
            router_id=self.id,
            port=in_port,
            flit=flit,
            out_port=out_port,
            grant_flits=grant_flits,
        )
        if flit.is_tail:
            del self.reservation[in_port]

    def to_id(self, x: int, y: int) -> int:
        return y * self.x_dim + x

    def to_xy(self, router_id: int):
        return router_id % self.x_dim, router_id // self.x_dim

    def _validate_flit_fabric(self, flit: Flit) -> None:
        if flit.fabric_id is not self.fabric_id:
            raise ValueError(
                f"{flit.fabric_id.name} flit cannot enter {self.name}"
            )


class NoC:
    """ADA2S-32 deterministic 4-column by 8-row mesh."""

    def __init__(
        self,
        env: simpy.Environment,
        config: NoCConfig,
        fabric_id: NoCChannel,
        tracer: NoCTracer,
    ):
        if config.type != "Mesh":
            raise ValueError("Phase 2 supports only the ADA2S-32 Mesh topology")
        self.env = env
        self.config = config
        self.fabric_id = fabric_id
        self.name = fabric_id.name
        if tracer.fabric_id is not fabric_id:
            raise ValueError(
                f"{self.name} NoC cannot use {tracer.fabric_id.name} tracer"
            )
        self.x = config.x
        self.y = config.y
        self.tracer = tracer
        self.r2r_links: List[Link] = []
        self.routers: List[Router] = []

    def build_connection_mesh(self):
        if self.routers:
            raise RuntimeError("NoC mesh has already been built")
        for router_id in range(self.x * self.y):
            self.routers.append(
                Router(
                    env=self.env,
                    config=self.config.router,
                    router_id=router_id,
                    x_dim=self.x,
                    y_dim=self.y,
                    fabric_id=self.fabric_id,
                    tracer=self.tracer,
                )
            )

        for y in range(self.y):
            for x in range(self.x):
                router_id = y * self.x + x
                if x < self.x - 1:
                    self._connect(
                        router_id,
                        Direction.EAST,
                        router_id + 1,
                        Direction.WEST,
                    )
                if y < self.y - 1:
                    self._connect(
                        router_id,
                        Direction.NORTH,
                        router_id + self.x,
                        Direction.SOUTH,
                    )
        return self

    def _connect(
        self,
        router_a: int,
        direction_a: Direction,
        router_b: int,
        direction_b: Direction,
    ):
        port_a = direction_to_port(direction_a)
        port_b = direction_to_port(direction_b)
        link_ab = Link(
            env=self.env,
            config=self.config.link,
            fabric_id=self.fabric_id,
            tracer=self.tracer,
            link_name=f"R{router_a}_{direction_a.name}->R{router_b}_{direction_b.name}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
            link_id=len(self.r2r_links),
            src_router=router_a,
            dst_router=router_b,
        )
        link_ba = Link(
            env=self.env,
            config=self.config.link,
            fabric_id=self.fabric_id,
            tracer=self.tracer,
            link_name=f"R{router_b}_{direction_b.name}->R{router_a}_{direction_a.name}",
            noc_cycles_per_aci_cycle=self.config.noc_cycles_per_aci_cycle,
            link_id=len(self.r2r_links) + 1,
            src_router=router_b,
            dst_router=router_a,
        )
        self.routers[router_a].bind_link(port_a, link_ba, link_ab)
        self.routers[router_b].bind_link(port_b, link_ab, link_ba)
        self.r2r_links.extend((link_ab, link_ba))


__all__ = [
    "FlitAction",
    "FlitEvent",
    "NoCLinkIdentity",
    "NoCTracer",
    "RoundRobinArbiter",
    "TraceMessageKey",
    "Link",
    "Router",
    "NoC",
    "PORT_PE",
]
