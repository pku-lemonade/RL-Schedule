"""Independently designed synthetic adapter input format.

This vocabulary (sites/planes/sockets/wires/movers/workers/stores/paths/
gauges/jobs) was invented for the public example adapter and deliberately
differs from the `generic_system_graph`/`generic_transaction_batch` field
names. It describes exactly the same neutral systems; conversion is validated
again by the generic schemas, so this layer only checks local consistency.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .generic_graph import NeutralId
from .generic_transactions import GenericLinkTiming
from .topology import Cycles, GraphRecord, Index, PositiveInt, PositiveTime, unique


class SyntheticSite(GraphRecord):
    site: NeutralId
    col: Index
    row: Index
    nature: Literal["logic", "storehouse", "relay"]


class SyntheticPlane(GraphRecord):
    plane: NeutralId


class SyntheticSocket(GraphRecord):
    socket: NeutralId
    site: NeutralId
    plane: NeutralId
    facing: Literal["wire", "house"]


class SyntheticWire(GraphRecord):
    wire: NeutralId
    plane: NeutralId
    from_site: NeutralId
    from_socket: NeutralId
    to_site: NeutralId
    to_socket: NeutralId


class SyntheticMover(GraphRecord):
    """A DMA-style agent that can move bytes to any reachable agent."""

    mover: NeutralId
    site: NeutralId
    plane: NeutralId
    socket: NeutralId


class SyntheticWorker(GraphRecord):
    worker: NeutralId
    site: NeutralId
    plane: NeutralId
    socket: NeutralId


class SyntheticStore(GraphRecord):
    store: NeutralId
    site: NeutralId
    room_bytes: PositiveInt
    service: NeutralId | None = None
    plane: NeutralId | None = None
    socket: NeutralId | None = None


class SyntheticPath(GraphRecord):
    plane: NeutralId
    from_agent: NeutralId
    to_agent: NeutralId
    wires: tuple[NeutralId, ...] = Field(min_length=1)


class SyntheticGauge(GraphRecord):
    gauge: NeutralId
    start: Index


class SyntheticTweak(GraphRecord):
    wire: NeutralId
    rate: GenericLinkTiming


class SyntheticRate(GraphRecord):
    plane: NeutralId
    rate: GenericLinkTiming
    tweaks: tuple[SyntheticTweak, ...] = ()


class SyntheticJobBase(GraphRecord):
    job: NeutralId
    after: tuple[NeutralId, ...] = ()
    at_cycle: Cycles = 0.0


class SyntheticMove(SyntheticJobBase):
    act: Literal["move"]
    plane: NeutralId
    from_agent: NeutralId
    to_agent: NeutralId
    load_bytes: PositiveInt


class SyntheticWork(SyntheticJobBase):
    act: Literal["work"]
    worker: NeutralId
    span_cycles: PositiveTime


class SyntheticRaise(SyntheticJobBase):
    act: Literal["raise"]
    gauge: NeutralId
    by: PositiveInt


class SyntheticAwait(SyntheticJobBase):
    act: Literal["await"]
    gauge: NeutralId
    until: PositiveInt


SyntheticJob = Annotated[
    SyntheticMove | SyntheticWork | SyntheticRaise | SyntheticAwait,
    Field(discriminator="act"),
]


class SyntheticWorld(GraphRecord):
    """A complete synthetic adapter input: structure plus jobs."""

    kind: Literal["synthetic_grid_world"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    world_id: NeutralId
    sites: tuple[SyntheticSite, ...] = Field(min_length=1)
    planes: tuple[SyntheticPlane, ...] = Field(min_length=1)
    sockets: tuple[SyntheticSocket, ...] = ()
    wires: tuple[SyntheticWire, ...] = ()
    movers: tuple[SyntheticMover, ...] = ()
    workers: tuple[SyntheticWorker, ...] = ()
    stores: tuple[SyntheticStore, ...] = ()
    paths: tuple[SyntheticPath, ...] = ()
    gauges: tuple[SyntheticGauge, ...] = ()
    rates: tuple[SyntheticRate, ...] = Field(min_length=1)
    jobs: tuple[SyntheticJob, ...] = Field(min_length=1)
    limit_cycles: PositiveTime

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(s.site for s in self.sites), "site identity")
        unique(tuple(p.plane for p in self.planes), "plane identity")
        unique(
            tuple((s.site, s.plane, s.socket) for s in self.sockets), "socket identity"
        )
        unique(tuple((w.plane, w.wire) for w in self.wires), "wire identity")
        unique(tuple(g.gauge for g in self.gauges), "gauge identity")
        unique(tuple(r.plane for r in self.rates), "rate plane")
        unique(tuple(j.job for j in self.jobs), "job identity")
        agents = tuple(
            [m.mover for m in self.movers]
            + [w.worker for w in self.workers]
            + [s.service for s in self.stores if s.service is not None]
        )
        unique(agents, "agent identity")
        sites = {s.site for s in self.sites}
        planes = {p.plane for p in self.planes}
        sockets = {(s.site, s.plane, s.socket): s for s in self.sockets}
        gauges = {g.gauge for g in self.gauges}
        for socket in self.sockets:
            if socket.site not in sites or socket.plane not in planes:
                raise ValueError(f"socket {socket.socket}: unknown site or plane")
        for wire in self.wires:
            if wire.plane not in planes:
                raise ValueError(f"wire {wire.wire}: unknown plane {wire.plane}")
            for site, socket_id in (
                (wire.from_site, wire.from_socket),
                (wire.to_site, wire.to_socket),
            ):
                socket = sockets.get((site, wire.plane, socket_id))
                if socket is None or socket.facing != "wire":
                    raise ValueError(
                        f"wire {wire.wire}: invalid wire socket {site}:{socket_id}"
                    )
        house_keys: set[tuple[str, str, str]] = set()
        for label, owner, site, plane, socket_id in (
            [("mover", m.mover, m.site, m.plane, m.socket) for m in self.movers]
            + [("worker", w.worker, w.site, w.plane, w.socket) for w in self.workers]
        ):
            if site not in sites or plane not in planes:
                raise ValueError(f"{label} {owner}: unknown site or plane")
            socket = sockets.get((site, plane, socket_id))
            if socket is None or socket.facing != "house":
                raise ValueError(f"{label} {owner}: invalid house socket {socket_id}")
            key = (site, plane, socket_id)
            if key in house_keys:
                raise ValueError(f"{label} {owner}: house socket {socket_id} shared")
            house_keys.add(key)
        agent_sites: dict[str, str] = {m.mover: m.site for m in self.movers}
        agent_sites.update({w.worker: w.site for w in self.workers})
        for worker in self.workers:
            site = next(s for s in self.sites if s.site == worker.site)
            if site.nature != "logic":
                raise ValueError(f"worker {worker.worker}: site {worker.site} is not logic")
        for store in self.stores:
            if store.site not in sites:
                raise ValueError(f"store {store.store}: unknown site {store.site}")
            declared = (
                store.service is not None,
                store.plane is not None,
                store.socket is not None,
            )
            if any(declared) and not all(declared):
                raise ValueError(f"store {store.store}: service attachment is all-or-nothing")
            if (
                store.service is not None
                and store.plane is not None
                and store.socket is not None
            ):
                if store.plane not in planes:
                    raise ValueError(f"store {store.store}: unknown plane {store.plane}")
                socket = sockets.get((store.site, store.plane, store.socket))
                if socket is None or socket.facing != "house":
                    raise ValueError(f"store {store.store}: invalid house socket")
                key = (store.site, store.plane, store.socket)
                if key in house_keys:
                    raise ValueError(f"store {store.store}: house socket shared")
                house_keys.add(key)
                agent_sites[store.service] = store.site
        rate_planes = {r.plane for r in self.rates}
        for rate in self.rates:
            if rate.plane not in planes:
                raise ValueError(f"rate declared for unknown plane {rate.plane}")
            member_wires = {w.wire for w in self.wires if w.plane == rate.plane}
            for tweak in rate.tweaks:
                if tweak.wire not in member_wires:
                    raise ValueError(f"rate tweak for unknown wire {tweak.wire}")
        jobs = {j.job for j in self.jobs}
        for job in self.jobs:
            for dependency in job.after:
                if dependency not in jobs or dependency == job.job:
                    raise ValueError(f"job {job.job}: unknown or self dependency")
            if isinstance(job, SyntheticMove):
                if job.plane not in planes:
                    raise ValueError(f"job {job.job}: unknown plane {job.plane}")
                if job.plane not in rate_planes:
                    raise ValueError(f"job {job.job}: plane {job.plane} has no rate")
                if job.from_agent not in agent_sites or job.to_agent not in agent_sites:
                    raise ValueError(f"job {job.job}: unknown agent")
            elif isinstance(job, SyntheticWork):
                if job.worker not in agent_sites:
                    raise ValueError(f"job {job.job}: unknown worker")
            else:
                if job.gauge not in gauges:
                    raise ValueError(f"job {job.job}: unknown gauge")
        incoming = {j.job: set(j.after) for j in self.jobs}
        resolved: set[str] = set()
        pending = dict(incoming)
        while pending:
            ready = sorted(k for k, deps in pending.items() if deps <= resolved)
            if not ready:
                raise ValueError("dependency cycle involving " + ", ".join(sorted(pending)))
            resolved.update(ready)
            for key in ready:
                del pending[key]
        for path in self.paths:
            if path.plane not in planes:
                raise ValueError(f"path {path.from_agent}->{path.to_agent}: unknown plane")
            if path.from_agent not in agent_sites or path.to_agent not in agent_sites:
                raise ValueError(f"path {path.from_agent}->{path.to_agent}: unknown agent")
            plane_wires = {w.wire for w in self.wires if w.plane == path.plane}
            for wire_id in path.wires:
                if wire_id not in plane_wires:
                    raise ValueError(
                        f"path {path.from_agent}->{path.to_agent}: unknown wire {wire_id}"
                    )
        return self
