from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional

import simpy
from simpy.resources.resource import Request as SimpyRequest

from .configs.schemas.arch_config import LinkConfig, NoCConfig, RouterConfig
from .utils.definitions import (
    DIR_EAST,
    DIR_NORTH,
    DIR_SOUTH,
    DIR_WEST,
    PORT_PE,
    Direction,
    Flit,
    direction_to_port,
)


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


@dataclass
class FlitEvent:
    time: float
    action: FlitAction
    router_id: int = -1
    port: int = -1
    msg_id: int = -1
    src_router: int = -1
    dst_router: int = -1
    out_port: int = -1
    link_name: str = ""


class NoCTracer:
    def __init__(self):
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
    ):
        if not self._enabled:
            return
        self.events.append(
            FlitEvent(
                time=time,
                action=action,
                router_id=router_id,
                port=port,
                msg_id=-1 if flit is None else flit.msg_id,
                src_router=-1 if flit is None else flit.src_router,
                dst_router=-1 if flit is None else flit.dst_router,
                out_port=out_port,
                link_name=link_name,
            )
        )

    def per_msg_latency(self) -> Dict[int, float]:
        injected: Dict[int, float] = {}
        ejected: Dict[int, float] = {}
        for event in self.events:
            if event.action == FlitAction.INJECT:
                injected.setdefault(event.msg_id, event.time)
            elif event.action == FlitAction.EJECT:
                ejected[event.msg_id] = event.time
        return {
            msg_id: ejected[msg_id] - start
            for msg_id, start in injected.items()
            if msg_id in ejected
        }

    def summary(self, end_time: float) -> str:
        counts = {
            action.name: sum(event.action == action for event in self.events)
            for action in FlitAction
        }
        latencies = self.per_msg_latency()
        avg_latency = (
            sum(latencies.values()) / len(latencies) if latencies else 0.0
        )
        return (
            f"cycles={end_time:.3f} events={len(self.events)} "
            f"messages={len(latencies)} avg_latency={avg_latency:.3f} "
            + " ".join(f"{name}={count}" for name, count in counts.items())
        )


class Link:
    """Unidirectional, credit-controlled phit pipeline."""

    def __init__(
        self,
        env: simpy.Environment,
        config: LinkConfig,
        physical_flit_bytes: int,
        tracer: NoCTracer,
        link_name: str = "",
    ):
        self.env = env
        self.config = config
        self.physical_flit_bytes = physical_flit_bytes
        self.tracer = tracer
        self.link_name = link_name
        self.serialization_cycles = config.serialization_cycles(physical_flit_bytes)
        if config.launch_interval_cycles < self.serialization_cycles:
            raise ValueError("launch interval cannot be shorter than serialization")
        self.launch_interval_cycles = config.launch_interval_cycles
        self.wire_delay_cycles = config.wire_delay_cycles
        self.delay_factor = 1.0

        self.flit_buffer = simpy.Store(
            env,
            capacity=config.input_buffer_depth_flits,
        )
        self.credits = simpy.Container(
            env,
            init=config.flow_control_window_flits,
            capacity=config.flow_control_window_flits,
        )
        self._out_queue = simpy.Store(env, capacity=1)
        self.env.process(self._transmit_loop())

    def send_flit(self, flit: Flit):
        return self.env.process(self._send_flit(flit))

    def recv_flit(self):
        return self.env.process(self._recv_flit())

    def ack_credit(self):
        return self.env.process(self._return_credit())

    def scale_link_delay(self, factor: float):
        if factor <= 0:
            raise ValueError("link delay scale factor must be positive")
        self.delay_factor *= factor

    def _send_flit(self, flit: Flit):
        if self.credits.level < 1:
            self.tracer.log(
                self.env.now,
                FlitAction.STALL_CREDIT,
                flit=flit,
                link_name=self.link_name,
            )
        yield self.credits.get(1)
        yield self._out_queue.put(flit)
        self.tracer.log(
            self.env.now,
            FlitAction.LINK_SEND,
            flit=flit,
            link_name=self.link_name,
        )

    def _recv_flit(self):
        flit = yield self.flit_buffer.get()
        return flit

    def _transmit_loop(self):
        while True:
            flit = yield self._out_queue.get()
            yield self.env.timeout(self.serialization_cycles * self.delay_factor)
            self.env.process(self._wire_deliver(flit))
            launch_gap = self.launch_interval_cycles - self.serialization_cycles
            if launch_gap > 0:
                yield self.env.timeout(launch_gap * self.delay_factor)

    def _wire_deliver(self, flit: Flit):
        yield self.env.timeout(self.wire_delay_cycles * self.delay_factor)
        yield self.flit_buffer.put(flit)
        self.tracer.log(
            self.env.now,
            FlitAction.LINK_RECV,
            flit=flit,
            link_name=self.link_name,
        )

    def _return_credit(self):
        yield self.credits.put(1)
        self.tracer.log(
            self.env.now,
            FlitAction.CREDIT_RETURN,
            link_name=self.link_name,
        )


class Router:
    """One-VC wormhole router with FIFO switch allocation."""

    def __init__(
        self,
        env: simpy.Environment,
        config: RouterConfig,
        router_id: int,
        x_dim: int,
        y_dim: int,
        tracer: NoCTracer,
    ):
        if config.type != "XY":
            raise ValueError("Phase 2 supports only deterministic XY routing")
        if config.vc != 1:
            raise ValueError("ADA2S-32 requires exactly one VC per port")

        self.env = env
        self.config = config
        self.id = router_id
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
        self.out_channels: Dict[int, simpy.Resource] = {}
        self.reservation: Dict[int, int] = {}
        self._sa_reqs: Dict[int, SimpyRequest] = {}
        self._forwarder_started: Dict[int, bool] = {}

    def bind_link(self, port: int, link_in: Link, link_out: Link):
        if self.port_in.get(port) is not None or self.port_out.get(port) is not None:
            raise ValueError(f"router {self.id} port {port} is already bound")
        self.port_in[port] = link_in
        self.port_out[port] = link_out
        self.out_channels[port] = simpy.Resource(self.env, capacity=1)
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

    def _port_forwarder(self, in_port: int):
        in_link = self.port_in[in_port]
        assert in_link is not None

        while True:
            flit = yield in_link.recv_flit()
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

            out_port = self._rc_compute(in_port, flit)
            if flit.is_head:
                self.tracer.log(
                    self.env.now,
                    FlitAction.ROUTER_RC,
                    router_id=self.id,
                    port=in_port,
                    flit=flit,
                    out_port=out_port,
                )
                yield self.env.timeout(self.config.pipeline.rc_cycles)

                out_channel = self.out_channels.get(out_port)
                if out_channel is None:
                    raise RuntimeError(
                        f"router {self.id} output port {out_port} is unbound"
                    )
                if out_channel.count >= out_channel.capacity:
                    self.tracer.log(
                        self.env.now,
                        FlitAction.STALL_SA,
                        router_id=self.id,
                        port=in_port,
                        flit=flit,
                        out_port=out_port,
                    )
                sa_req = out_channel.request()
                yield sa_req
                self.tracer.log(
                    self.env.now,
                    FlitAction.ROUTER_SA_GRANT,
                    router_id=self.id,
                    port=in_port,
                    flit=flit,
                    out_port=out_port,
                )
                self._sa_reqs[in_port] = sa_req
                yield self.env.timeout(self.config.pipeline.sa_cycles)

            in_link.ack_credit()
            self.tracer.log(
                self.env.now,
                FlitAction.ROUTER_ST,
                router_id=self.id,
                port=in_port,
                flit=flit,
                out_port=out_port,
            )
            yield self.env.timeout(self.config.pipeline.st_cycles)

            out_link = self.port_out.get(out_port)
            if out_link is None:
                raise RuntimeError(
                    f"router {self.id} output port {out_port} is unbound"
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

    def _rc_compute(self, in_port: int, flit: Flit) -> int:
        if not flit.is_head:
            if in_port not in self.reservation:
                raise RuntimeError(
                    f"router {self.id} received {flit.flit_type.name} without HEAD"
                )
            return self.reservation[in_port]

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
        self.reservation[in_port] = out_port
        return out_port

    def _post_send(self, in_port: int, out_port: int, flit: Flit):
        if not flit.is_tail:
            return
        request = self._sa_reqs.pop(in_port, None)
        if request is None:
            raise RuntimeError(
                f"router {self.id} tail flit has no switch reservation"
            )
        self.out_channels[out_port].release(request)
        del self.reservation[in_port]

    def to_id(self, x: int, y: int) -> int:
        return y * self.x_dim + x

    def to_xy(self, router_id: int):
        return router_id % self.x_dim, router_id // self.x_dim


class NoC:
    """ADA2S-32 deterministic 4-column by 8-row mesh."""

    def __init__(
        self,
        env: simpy.Environment,
        config: NoCConfig,
        tracer: NoCTracer,
    ):
        if config.type != "Mesh":
            raise ValueError("Phase 2 supports only the ADA2S-32 Mesh topology")
        self.env = env
        self.config = config
        self.x = config.x
        self.y = config.y
        self.tracer = tracer
        self.physical_flit_bytes = config.router.flit.physical_flit_bytes
        self.r2r_links: List[Link] = []
        self.routers: List[Router] = []

    def build_connection_mesh(self):
        if self.routers:
            raise RuntimeError("NoC mesh has already been built")
        for router_id in range(self.x * self.y):
            self.routers.append(
                Router(
                    self.env,
                    self.config.router,
                    router_id,
                    self.x,
                    self.y,
                    self.tracer,
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
            self.env,
            self.config.link,
            self.physical_flit_bytes,
            self.tracer,
            f"R{router_a}_{direction_a.name}->R{router_b}_{direction_b.name}",
        )
        link_ba = Link(
            self.env,
            self.config.link,
            self.physical_flit_bytes,
            self.tracer,
            f"R{router_b}_{direction_b.name}->R{router_a}_{direction_a.name}",
        )
        self.routers[router_a].bind_link(port_a, link_ba, link_ab)
        self.routers[router_b].bind_link(port_b, link_ab, link_ba)
        self.r2r_links.extend((link_ab, link_ba))


__all__ = [
    "FlitAction",
    "FlitEvent",
    "NoCTracer",
    "Link",
    "Router",
    "NoC",
    "PORT_PE",
]
