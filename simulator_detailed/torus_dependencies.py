"""Static lane/owner/response dependency checks, independent of scheduling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from itertools import pairwise
from types import MappingProxyType
from typing import Literal, Self

from pydantic import model_validator

from .configs.schemas.topology import GraphRecord, Identifier, Index
from .configs.schemas.torus_replay import (
    LaneIdentity,
    ResourceHop,
    RouteRecord,
    TrafficClass,
)
from .topology import canonical_json, content_digest


class DependencyResource(GraphRecord):
    kind: Literal["packet_owner", "lane_storage", "response_descriptors"]
    fabric_id: Index
    traffic_class: TrafficClass
    lane: LaneIdentity | None
    endpoint_id: Identifier | None
    rank: Index

    @model_validator(mode="after")
    def namespace(self) -> Self:
        descriptor = self.kind == "response_descriptors"
        if descriptor != (self.lane is None) or descriptor != (self.endpoint_id is not None):
            raise ValueError("descriptor resources require an endpoint; owner/storage resources require a lane")
        if self.lane is not None and (
            self.lane.channel.fabric_id != self.fabric_id or self.lane.traffic_class != self.traffic_class
        ):
            raise ValueError("dependency resource and lane identities disagree")
        if descriptor and self.traffic_class != "request":
            raise ValueError("only a consumed request can retain a response descriptor")
        return self

    @property
    def identity(self) -> str:
        return canonical_json([self.kind, self.fabric_id, self.traffic_class,
                               None if self.lane is None else self.lane.model_dump(mode="json"), self.endpoint_id])


@dataclass(frozen=True)
class ResourceDependencies:
    resources: Mapping[str, DependencyResource]
    edges: tuple[tuple[str, str], ...]
    topological_order: tuple[str, ...]

    @classmethod
    def validate(cls, resources: Iterable[DependencyResource], edges: Iterable[tuple[str, str]]) -> ResourceDependencies:
        nodes: dict[str, DependencyResource] = {}
        for resource in resources:
            resource = DependencyResource.model_validate(resource.model_dump(mode="json"))
            if resource.identity in nodes:
                raise ValueError("duplicate dependency resource identity")
            nodes[resource.identity] = resource
        ordered_edges = tuple(sorted(set(edges)))
        predecessors: dict[str, set[str]] = {key: set() for key in nodes}
        for before, after in ordered_edges:
            if before not in nodes or after not in nodes:
                raise ValueError("dependency edge refers to an unknown resource")
            a, b = nodes[before], nodes[after]
            if a.fabric_id != b.fabric_id:
                raise ValueError("cross-fabric dependencies are unsupported")
            if a.traffic_class != b.traffic_class and not (
                a.kind == "response_descriptors" and a.traffic_class == "request"
                and b.kind == "packet_owner" and b.traffic_class == "response"
                and b.lane is not None and b.lane.channel.kind == "inject"
                and a.endpoint_id == b.lane.channel.identity
            ):
                raise ValueError("unsupported class dependency; responses cannot depend on requests")
            predecessors[after].add(before)
        try:
            order = tuple(TopologicalSorter({key: sorted(predecessors[key]) for key in sorted(nodes)}).static_order())
        except CycleError as exc:
            raise ValueError("torus resource dependency cycle") from exc
        for before, after in ordered_edges:
            if nodes[before].rank >= nodes[after].rank:
                raise ValueError("dependency does not increase the declared resource rank")
        return cls(MappingProxyType(dict(sorted(nodes.items()))), ordered_edges, order)

    @classmethod
    def from_paths(cls, paths: Iterable[tuple[ResourceHop, ...]],
                   response_pairs: tuple[tuple[RouteRecord, RouteRecord], ...] = ()) -> ResourceDependencies:
        """Include packet owners before their storage, and causal descriptors.

        Paths must additionally pass the torus route-policy validator. The graph
        checks consistency/order; it does not itself establish physical routes.
        """
        resources: dict[str, DependencyResource] = {}
        edges: set[tuple[str, str]] = set()
        lane_kinds: tuple[tuple[Literal["packet_owner", "lane_storage"], int],
                           tuple[Literal["packet_owner", "lane_storage"], int]] = (
            ("packet_owner", 0), ("lane_storage", 1))

        def register(resource: DependencyResource) -> str:
            key = resource.identity
            if key in resources and resources[key] != resource:
                raise ValueError("one dependency resource was assigned inconsistent ranks")
            resources[key] = resource
            return key

        def lane_resources(hop: ResourceHop) -> tuple[str, str]:
            identities = tuple(register(DependencyResource(
                kind=kind, fabric_id=hop.lane.channel.fabric_id, traffic_class=hop.lane.traffic_class,
                lane=hop.lane, endpoint_id=None, rank=3 * hop.rank + offset,
            )) for kind, offset in lane_kinds)
            return identities[0], identities[1]

        for hops in paths:
            path: list[str] = []
            for hop in hops:
                path.extend(lane_resources(hop))
            edges.update(pairwise(path))
        for request, response in response_pairs:
            if request.traffic_class != "request" or response.traffic_class != "response" or (
                request.fabric_id != response.fabric_id or request.source != response.destination
                or request.destination != response.source
            ):
                raise ValueError("causal response must reverse its request endpoints on the same fabric")
            request_storage = lane_resources(request.hops[-1])[1]
            response_owner = lane_resources(response.hops[0])[0]
            descriptor = register(DependencyResource(
                kind="response_descriptors", fabric_id=request.fabric_id, traffic_class="request",
                endpoint_id=request.destination, lane=None, rank=3 * (request.hops[-1].rank + 1),
            ))
            edges.update(((request_storage, descriptor), (descriptor, response_owner)))
        return cls.validate(resources.values(), edges)

    def export(self) -> dict[str, object]:
        graph = {"resources": [{"identity": key, **resource.model_dump(mode="json")}
                               for key, resource in self.resources.items()], "edges": self.edges}
        return {
            **graph, "sha256": content_digest(graph), "acyclic": True,
            "scope": "static_declared_resources", "runtime_verified": False,
            "assumptions": [
                "finite packets and service; enabled independent terminal sinks",
                "fair eligible arbitration; blocked or idle lanes hold no shared physical grant",
                "finite lane storage includes retained flits and delayed credit returns",
                "packet owners and next-hop storage follow the compiled increasing ranks",
                "request descriptors wait only on independently draining response resources",
            ],
        }
