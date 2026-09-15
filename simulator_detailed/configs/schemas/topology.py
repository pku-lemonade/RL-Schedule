"""Immutable directed topology records, independent of runtime service defaults."""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Identifier = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
Index = Annotated[int, Field(strict=True, ge=0)]
PortIndex = Annotated[int, Field(strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Availability = StrictBool | None
TileRole = Literal["worker", "memory", "ethernet", "pcie", "management", "transit"]
K = TypeVar("K")


def unique(values: tuple[K, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")


class GraphRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Coordinate(GraphRecord):
    x: Index
    y: Index


class Extent(GraphRecord):
    width: Annotated[int, Field(strict=True, gt=0)]
    height: Annotated[int, Field(strict=True, gt=0)]


class TopologyTile(Coordinate):
    tile_id: Identifier
    role: TileRole


class TopologyFabric(GraphRecord):
    fabric_id: Index
    extent: Extent | None = None
    topology_policy: Identifier
    routing_policy: Identifier


class TopologyPort(GraphRecord):
    port_id: Identifier
    kind: Literal["network", "local"]
    runtime_index: PortIndex | None = None

    @model_validator(mode="after")
    def port_namespace(self) -> Self:
        if self.runtime_index is not None and (
            (self.runtime_index < 0) != (self.kind == "network")
        ):
            raise ValueError("network port indices are negative; local ports are nonnegative")
        return self


class TopologyRouter(GraphRecord):
    router_id: Identifier
    fabric_id: Index
    tile_id: Identifier
    enabled: Availability
    coordinate: Coordinate | None = None
    ports: tuple[TopologyPort, ...] = ()
    runtime_index: Index | None = None

    @property
    def key(self) -> tuple[int, str]:
        return self.fabric_id, self.router_id


class TopologyLink(GraphRecord):
    link_id: Identifier
    fabric_id: Index
    src_router: Identifier
    src_port: Identifier
    dst_router: Identifier
    dst_port: Identifier
    enabled: Availability
    direction: Identifier | None = None
    wrap: StrictBool = False
    runtime_index: Index | None = None

    @property
    def key(self) -> tuple[int, str]:
        return self.fabric_id, self.link_id


class LegacyEndpointBinding(GraphRecord):
    node_type: Literal["PE", "GM_RDMA", "GM_WDMA", "DDR_RDMA", "DDR_WDMA"]
    node_id: Index
    attachment_mode: Literal["single_side", "dual_side", "local"] | None = None

    @model_validator(mode="after")
    def dma_mode(self) -> Self:
        if (self.node_type == "PE") != (self.attachment_mode is None):
            raise ValueError("only DMA bindings require an attachment mode")
        return self


class TopologyAttachment(GraphRecord):
    endpoint_id: Identifier
    fabric_id: Index
    router_id: Identifier
    role: Literal["network", "compute", "dma"]
    enabled: Availability
    # None means no declared channel, not an implicit port zero.
    inject_port: Identifier | None = None
    eject_port: Identifier | None = None
    permissions_resolved: StrictBool = False
    replay_enabled: StrictBool = False
    resource_ids: tuple[Identifier, ...] = ()
    legacy_binding: LegacyEndpointBinding | None = None


class TopologyWorker(GraphRecord):
    worker_index: Index
    logical_x: Index
    logical_y: Index
    tile_id: Identifier


class TopologyResource(GraphRecord):
    resource_id: Identifier
    kind: Literal["local_sram", "dram"]
    capacity_bytes: Annotated[int, Field(strict=True, gt=0)]
    owner_tile_id: Identifier | None = None
    source_parameter: Identifier | None = None
    evidence_path: Identifier | None = None


class TopologyOrigin(GraphRecord):
    kind: Literal["synthetic", "legacy_mesh", "hardware_profile"]
    content_hash: Digest | None = None
    # Immutable canonical JSON retains source evidence without mutable nested dicts.
    document_json: str | None = None


class CanonicalTopology(GraphRecord):
    kind: Literal["canonical_topology"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    topology_id: Identifier
    asic_id: Identifier
    origin: TopologyOrigin
    connectivity_state: Literal["complete", "unresolved"]
    tiles: tuple[TopologyTile, ...] = Field(min_length=1)
    fabrics: tuple[TopologyFabric, ...] = Field(min_length=1)
    routers: tuple[TopologyRouter, ...]
    links: tuple[TopologyLink, ...]
    attachments: tuple[TopologyAttachment, ...]
    enabled_worker_ids: tuple[Identifier, ...]
    logical_workers: tuple[TopologyWorker, ...]
    resources: tuple[TopologyResource, ...]

    @model_validator(mode="after")
    def references(self) -> Self:
        unique(tuple(t.tile_id for t in self.tiles), "tile identity")
        unique(tuple((t.x, t.y) for t in self.tiles), "physical coordinate")
        unique(tuple(f.fabric_id for f in self.fabrics), "fabric identity")
        unique(tuple(r.key for r in self.routers), "router identity")
        unique(tuple((r.fabric_id, r.tile_id) for r in self.routers), "tile/fabric router")
        unique(tuple(l.key for l in self.links), "link identity")
        unique(tuple(a.endpoint_id for a in self.attachments), "endpoint identity")
        unique(tuple(r.resource_id for r in self.resources), "resource identity")
        tiles = {t.tile_id: t for t in self.tiles}
        fabrics = {f.fabric_id: f for f in self.fabrics}
        routers = {r.key: r for r in self.routers}
        resources = {r.resource_id: r for r in self.resources}
        for router in self.routers:
            if router.tile_id not in tiles or router.fabric_id not in fabrics:
                raise ValueError(f"router {router.key}: unknown tile/fabric")
            unique(tuple(p.port_id for p in router.ports), f"port on {router.key}")
            unique(tuple(p.runtime_index for p in router.ports if p.runtime_index is not None),
                   f"port index on {router.key}")
            extent = fabrics[router.fabric_id].extent
            if extent is not None and router.coordinate is not None and (
                router.coordinate.x >= extent.width or router.coordinate.y >= extent.height
            ):
                raise ValueError(f"router {router.key}: fabric coordinate out of bounds")
        for fabric in self.fabrics:
            members = tuple(r for r in self.routers if r.fabric_id == fabric.fabric_id)
            unique(tuple((r.coordinate.x, r.coordinate.y) for r in members
                         if r.coordinate is not None), "fabric coordinate")
            self._indices(tuple(r.runtime_index for r in members), "router")
            self._indices(tuple(l.runtime_index for l in self.links
                                if l.fabric_id == fabric.fabric_id), "link")
        unique(self.enabled_worker_ids, "enabled worker")
        for worker in self.enabled_worker_ids:
            if worker not in tiles or tiles[worker].role != "worker":
                raise ValueError(f"enabled worker {worker} is not a worker tile")
        unique(tuple(w.tile_id for w in self.logical_workers), "logical worker tile")
        unique(tuple(w.worker_index for w in self.logical_workers), "logical worker index")
        unique(tuple((w.logical_x, w.logical_y) for w in self.logical_workers), "logical coordinate")
        if set(self.enabled_worker_ids) != {w.tile_id for w in self.logical_workers}:
            raise ValueError("logical workers must bijectively map enabled workers")
        for resource in self.resources:
            if resource.owner_tile_id is not None and resource.owner_tile_id not in tiles:
                raise ValueError(f"resource {resource.resource_id}: unknown owner")
            if resource.kind == "local_sram" and resource.owner_tile_id is None:
                raise ValueError("local_sram requires an owner tile")
        occupied: set[tuple[int, str, str, str]] = set()

        def occupy(fabric: int, router_id: str, port_id: str, half: str, kind: str) -> None:
            router = routers.get((fabric, router_id))
            if router is None:
                raise ValueError(f"unknown router {(fabric, router_id)}")
            port = next((p for p in router.ports if p.port_id == port_id), None)
            if port is None or port.kind != kind:
                raise ValueError(f"invalid {kind} port {router.key}:{port_id}")
            key = (fabric, router_id, port_id, half)
            if key in occupied:
                raise ValueError(f"port occupancy collision: shared port {key}")
            occupied.add(key)

        for link in self.links:
            occupy(link.fabric_id, link.src_router, link.src_port, "out", "network")
            occupy(link.fabric_id, link.dst_router, link.dst_port, "in", "network")
        bindings: set[tuple[int, str, int]] = set()
        for endpoint in self.attachments:
            router = routers.get((endpoint.fabric_id, endpoint.router_id))
            if router is None:
                raise ValueError(f"endpoint {endpoint.endpoint_id}: unknown router")
            if not endpoint.permissions_resolved and (endpoint.inject_port or endpoint.eject_port):
                raise ValueError("unresolved attachment cannot declare directional ports")
            for port, half in ((endpoint.inject_port, "in"), (endpoint.eject_port, "out")):
                if port is not None:
                    occupy(endpoint.fabric_id, endpoint.router_id, port, half, "local")
            if endpoint.role == "compute" and router.tile_id not in self.enabled_worker_ids:
                raise ValueError("compute binding requires an enabled worker")
            if endpoint.replay_enabled and (
                endpoint.enabled is not True or not endpoint.permissions_resolved
                or not (endpoint.inject_port or endpoint.eject_port)
                or router.enabled is not True
            ):
                raise ValueError("replay endpoint requires an available resolved attachment/router")
            binding = endpoint.legacy_binding
            if binding is not None:
                if (binding.node_type == "PE") != (endpoint.role == "compute"):
                    raise ValueError("legacy binding role mismatch")
                if binding.node_type != "PE" and endpoint.role != "dma":
                    raise ValueError("legacy DMA binding requires dma role")
                key = (endpoint.fabric_id, binding.node_type, binding.node_id)
                if key in bindings:
                    raise ValueError("duplicate legacy endpoint binding")
                bindings.add(key)
            unique(endpoint.resource_ids, "endpoint resource reference")
            for resource_id in endpoint.resource_ids:
                resource = resources.get(resource_id)
                if resource is None:
                    raise ValueError(f"unknown resource {resource_id}")
                if resource.owner_tile_id is not None and resource.owner_tile_id != router.tile_id:
                    raise ValueError(f"resource {resource_id} belongs to another tile")
        return self

    @staticmethod
    def _indices(indices: tuple[int | None, ...], label: str) -> None:
        if any(i is not None for i in indices) and set(indices) != set(range(len(indices))):
            raise ValueError(f"{label} compatibility indices must be a complete dense bijection")
