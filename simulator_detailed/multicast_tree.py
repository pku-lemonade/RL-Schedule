"""Pure admission and route construction for finite rectangle multicast writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .configs.schemas.memory_replay import MemoryBuffer, MemoryEndpointBinding
from .configs.schemas.multicast_sync import MulticastSyncWorkload, MulticastWrite
from .configs.schemas.topology import (
    CanonicalTopology,
    Coordinate,
    GraphRecord,
    Identifier,
    Index,
    TopologyAttachment,
    TopologyLink,
    TopologyResource,
    TopologyRouter,
    TopologyTile,
)


class TreeEdge(GraphRecord):
    """One directed hop in the deterministic corner-rectangle tree."""

    edge_id: Identifier
    kind: Literal["approach", "spine", "branch"]
    fabric_id: Index
    src_router: Identifier
    dst_router: Identifier
    link_id: Identifier


class TreeRecipient(GraphRecord):
    """A worker endpoint admitted as an exact multicast recipient."""

    endpoint_id: Identifier
    tile_id: Identifier
    router_id: Identifier
    coordinate: Coordinate
    buffer_id: Identifier
    offset_bytes: int
    source_included: bool


class RectangleTreePlan(GraphRecord):
    """Canonical route and recipient set for one multicast write."""

    policy: Literal["corner_rectangle_tree_v1"]
    operation_id: Identifier
    fabric_id: Index
    source_endpoint_id: Identifier
    source_router_id: Identifier
    entry_router_id: Identifier
    recipients: tuple[TreeRecipient, ...]
    edges: tuple[TreeEdge, ...]


@dataclass(frozen=True)
class _GraphIndex:
    tiles: dict[str, TopologyTile]
    routers: dict[tuple[int, str], TopologyRouter]
    routers_by_coordinate: dict[tuple[int, int, int], TopologyRouter]
    links: tuple[TopologyLink, ...]
    attachments: dict[str, TopologyAttachment]
    resources: dict[str, TopologyResource]


def _index_graph(graph: CanonicalTopology) -> _GraphIndex:
    return _GraphIndex(
        tiles={tile.tile_id: tile for tile in graph.tiles},
        routers={(router.fabric_id, router.router_id): router for router in graph.routers},
        routers_by_coordinate={
            (router.fabric_id, router.coordinate.x, router.coordinate.y): router
            for router in graph.routers
            if router.coordinate is not None
        },
        links=graph.links,
        attachments={attachment.endpoint_id: attachment for attachment in graph.attachments},
        resources={resource.resource_id: resource for resource in graph.resources},
    )


def _endpoint(
    endpoint_id: str,
    fabric_id: int,
    graph_index: _GraphIndex,
    bindings: dict[str, MemoryEndpointBinding],
    *,
    role: Literal["source", "target"],
) -> tuple[TopologyAttachment, MemoryEndpointBinding, TopologyRouter]:
    attachment = graph_index.attachments.get(endpoint_id)
    if attachment is None:
        raise ValueError(f"{role} endpoint {endpoint_id}: graph attachment is missing")
    if attachment.fabric_id != fabric_id or attachment.role != "compute":
        raise ValueError(f"{role} endpoint {endpoint_id}: must be a compute attachment on fabric {fabric_id}")
    if attachment.enabled is not True or attachment.replay_enabled is not True:
        raise ValueError(f"{role} endpoint {endpoint_id}: attachment is unavailable")
    if attachment.permissions_resolved is not True or not attachment.inject_port or not attachment.eject_port:
        raise ValueError(f"{role} endpoint {endpoint_id}: directional permissions are unresolved")
    router = graph_index.routers.get((fabric_id, attachment.router_id))
    if router is None or router.enabled is not True or router.coordinate is None:
        raise ValueError(f"{role} endpoint {endpoint_id}: router is unavailable")
    binding = bindings.get(endpoint_id)
    if binding is None:
        raise ValueError(f"{role} endpoint {endpoint_id}: memory binding is missing")
    if binding.fabric_id != fabric_id or binding.router_id != attachment.router_id or not binding.enabled:
        raise ValueError(f"{role} endpoint {endpoint_id}: memory binding does not match graph")
    if role == "source" and "initiator" not in binding.roles:
        raise ValueError(f"source endpoint {endpoint_id}: initiator role is required")
    if role == "target" and "target" not in binding.roles:
        raise ValueError(f"target endpoint {endpoint_id}: target role is required")
    return attachment, binding, router


def _buffer(
    buffer_id: str,
    offset: int,
    size: int,
    *,
    endpoint_id: str,
    binding: MemoryEndpointBinding,
    graph_index: _GraphIndex,
    buffers: dict[str, MemoryBuffer],
    writable: bool,
) -> MemoryBuffer:
    buffer = buffers.get(buffer_id)
    if buffer is None:
        raise ValueError(f"buffer {buffer_id}: is missing")
    if writable and not buffer.writable:
        raise ValueError(f"buffer {buffer_id}: target is not writable")
    if not writable and not buffer.readable:
        raise ValueError(f"buffer {buffer_id}: source is not readable")
    if offset + size > buffer.size_bytes:
        raise ValueError(f"buffer {buffer_id}: range exceeds its declared size")
    resource = graph_index.resources.get(buffer.resource_id)
    if resource is None:
        raise ValueError(f"buffer {buffer_id}: resource {buffer.resource_id} is missing")
    attachment = graph_index.attachments[endpoint_id]
    router = graph_index.routers[(attachment.fabric_id, attachment.router_id)]
    if resource.owner_tile_id != router.tile_id:
        raise ValueError(f"buffer {buffer_id}: resource is not owned by endpoint {endpoint_id}")
    if buffer.resource_id not in binding.resource_ids:
        raise ValueError(f"buffer {buffer_id}: endpoint {endpoint_id} does not expose its resource")
    return buffer


def _link(
    fabric_id: int,
    src: TopologyRouter,
    dst: TopologyRouter,
    graph_index: _GraphIndex,
) -> tuple[str, TopologyLink]:
    if src.coordinate is None or dst.coordinate is None:
        raise ValueError("multicast tree requires coordinates on every router")
    if src.fabric_id != fabric_id or dst.fabric_id != fabric_id:
        raise ValueError("multicast tree cannot cross fabrics")
    link = next(
        (
            item
            for item in graph_index.links
            if item.fabric_id == fabric_id
            and item.src_router == src.router_id
            and item.dst_router == dst.router_id
            and item.enabled is True
            and item.wrap is False
        ),
        None,
    )
    if link is None:
        raise ValueError(f"missing enabled non-wrapping link {src.router_id}->{dst.router_id}")
    return link.link_id, link


def _walk(
    *,
    operation_id: str,
    fabric_id: int,
    start: Coordinate,
    end: Coordinate,
    kind: Literal["approach", "spine", "branch"],
    graph_index: _GraphIndex,
    edges: list[TreeEdge],
    seen_links: set[str],
) -> None:
    current = graph_index.routers_by_coordinate.get((fabric_id, start.x, start.y))
    if current is None:
        raise ValueError(f"fabric {fabric_id}: no router at ({start.x}, {start.y})")
    x_step = 0 if start.x == end.x else (1 if end.x > start.x else -1)
    y_step = 0 if start.y == end.y else (1 if end.y > start.y else -1)
    if x_step and y_step:
        raise ValueError("tree walks must vary one coordinate at a time")
    coordinate = start
    while coordinate.x != end.x or coordinate.y != end.y:
        next_coordinate = Coordinate(x=coordinate.x + x_step, y=coordinate.y + y_step)
        destination = graph_index.routers_by_coordinate.get(
            (fabric_id, next_coordinate.x, next_coordinate.y)
        )
        if destination is None:
            raise ValueError(f"fabric {fabric_id}: no router at ({next_coordinate.x}, {next_coordinate.y})")
        link_id, _ = _link(fabric_id, current, destination, graph_index)
        if link_id in seen_links:
            raise ValueError(f"tree repeats link {link_id}")
        seen_links.add(link_id)
        edges.append(
            TreeEdge(
                edge_id=f"{operation_id}:tree:{len(edges)}",
                kind=kind,
                fabric_id=fabric_id,
                src_router=current.router_id,
                dst_router=destination.router_id,
                link_id=link_id,
            )
        )
        current = destination
        coordinate = next_coordinate


def compile_rectangle_tree(
    workload: MulticastSyncWorkload,
    graph: CanonicalTopology,
    write: MulticastWrite,
) -> RectangleTreePlan:
    """Validate bindings and compile the documented corner tree.

    This function is intentionally pure.  It only admits the finite subset
    represented by the child schema and does not allocate transport resources.
    """

    graph = CanonicalTopology.model_validate(graph.model_dump(mode="json"))
    if graph.connectivity_state != "complete":
        raise ValueError("multicast tree requires a complete canonical graph")
    if write not in workload.writes:
        raise ValueError(f"write {write.operation_id}: is not part of workload")
    if write.fabric_id not in workload.memory.fabrics:
        raise ValueError(f"write {write.operation_id}: fabric is not enabled for memory")
    fabric = next((item for item in graph.fabrics if item.fabric_id == write.fabric_id), None)
    if fabric is None or fabric.extent is None:
        raise ValueError(f"write {write.operation_id}: fabric extent is required")
    if write.rectangle.end.x >= fabric.extent.width or write.rectangle.end.y >= fabric.extent.height:
        raise ValueError(f"write {write.operation_id}: rectangle exceeds fabric extent")

    graph_index = _index_graph(graph)
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in workload.memory.endpoints}
    buffers = {buffer.buffer_id: buffer for buffer in workload.memory.buffers}
    _, source_binding, source_router = _endpoint(
        write.source_endpoint_id, write.fabric_id, graph_index, endpoints, role="source"
    )
    source_buffer = _buffer(
        write.source.buffer_id,
        write.source.offset_bytes,
        write.size_bytes,
        endpoint_id=write.source_endpoint_id,
        binding=source_binding,
        graph_index=graph_index,
        buffers=buffers,
        writable=False,
    )
    if write.completion == "write_acknowledged" and "response_sink" not in source_binding.roles:
        raise ValueError("acknowledged multicast source requires a bounded response sink")
    source_coordinate = source_router.coordinate
    if source_coordinate is None:
        raise ValueError("source router must have a coordinate")
    start, end = write.rectangle.start, write.rectangle.end
    if write.rectangle.major_axis == "x":
        if source_coordinate.y != start.y or source_coordinate.x > start.x:
            raise ValueError("x-major tree source must be on the start row at or before the corner")
    else:
        if source_coordinate.x != start.x or source_coordinate.y > start.y:
            raise ValueError("y-major tree source must be on the start column at or before the corner")

    worker_tiles = {
        tile.tile_id: tile
        for tile in graph.tiles
        if tile.tile_id in graph.enabled_worker_ids
        and start.x <= tile.x <= end.x
        and start.y <= tile.y <= end.y
    }
    if not worker_tiles:
        raise ValueError("multicast rectangle contains no enabled workers")
    expected: list[tuple[TopologyTile, TopologyAttachment]] = []
    for tile in sorted(worker_tiles.values(), key=lambda item: (item.y, item.x, item.tile_id)):
        router = graph_index.routers_by_coordinate.get((write.fabric_id, tile.x, tile.y))
        if router is None or router.tile_id != tile.tile_id:
            raise ValueError(f"worker {tile.tile_id}: fabric router is missing or mismatched")
        attachment = next(
            (
                item
                for item in graph.attachments
                if item.fabric_id == write.fabric_id
                and item.router_id == router.router_id
                and item.role == "compute"
            ),
            None,
        )
        if attachment is None:
            raise ValueError(f"worker {tile.tile_id}: compute attachment is missing")
        expected.append((tile, attachment))
    expected_endpoint_ids = {attachment.endpoint_id for _, attachment in expected}
    if not write.rectangle.include_source:
        expected_endpoint_ids.discard(write.source_endpoint_id)
    expected_recipients = [
        (tile, attachment)
        for tile, attachment in expected
        if attachment.endpoint_id in expected_endpoint_ids
    ]
    declared_endpoint_ids = {destination.endpoint_id for destination in write.destinations}
    if declared_endpoint_ids != expected_endpoint_ids:
        missing = sorted(expected_endpoint_ids - declared_endpoint_ids)
        extra = sorted(declared_endpoint_ids - expected_endpoint_ids)
        raise ValueError(f"multicast recipients do not match rectangle (missing={missing}, extra={extra})")
    if write.source_endpoint_id not in declared_endpoint_ids and write.rectangle.include_source:
        raise ValueError("rectangle includes source but source endpoint is absent from recipients")
    if write.source_endpoint_id in declared_endpoint_ids and not write.rectangle.include_source:
        raise ValueError("rectangle excludes source but source endpoint is listed as a recipient")

    destination_map = {destination.endpoint_id: destination for destination in write.destinations}
    recipients: list[TreeRecipient] = []
    for tile, attachment in expected_recipients:
        destination = destination_map[attachment.endpoint_id]
        _, target_binding, _ = _endpoint(
            destination.endpoint_id, write.fabric_id, graph_index, endpoints, role="target"
        )
        if destination.offset_bytes != write.target_offset_bytes:
            raise ValueError(f"destination {destination.endpoint_id}: offset differs from target_offset_bytes")
        target_buffer = _buffer(
            destination.buffer_id,
            destination.offset_bytes,
            write.size_bytes,
            endpoint_id=destination.endpoint_id,
            binding=target_binding,
            graph_index=graph_index,
            buffers=buffers,
            writable=True,
        )
        if target_buffer.buffer_id == source_buffer.buffer_id:
            source_end = write.source.offset_bytes + write.size_bytes
            target_end = destination.offset_bytes + write.size_bytes
            if write.source.offset_bytes < target_end and destination.offset_bytes < source_end:
                raise ValueError("multicast source and destination ranges overlap")
        router = graph_index.routers[(write.fabric_id, attachment.router_id)]
        recipients.append(
            TreeRecipient(
                endpoint_id=destination.endpoint_id,
                tile_id=tile.tile_id,
                router_id=router.router_id,
                coordinate=Coordinate(x=tile.x, y=tile.y),
                buffer_id=target_buffer.buffer_id,
                offset_bytes=destination.offset_bytes,
                source_included=destination.endpoint_id == write.source_endpoint_id,
            )
        )

    entry_router = graph_index.routers_by_coordinate.get((write.fabric_id, start.x, start.y))
    if entry_router is None:
        raise ValueError("rectangle start has no fabric router")
    edges: list[TreeEdge] = []
    seen_links: set[str] = set()
    if write.rectangle.major_axis == "x":
        _walk(
            operation_id=write.operation_id,
            fabric_id=write.fabric_id,
            start=source_coordinate,
            end=Coordinate(x=start.x, y=start.y),
            kind="approach",
            graph_index=graph_index,
            edges=edges,
            seen_links=seen_links,
        )
        _walk(
            operation_id=write.operation_id,
            fabric_id=write.fabric_id,
            start=Coordinate(x=start.x, y=start.y),
            end=Coordinate(x=start.x, y=end.y),
            kind="spine",
            graph_index=graph_index,
            edges=edges,
            seen_links=seen_links,
        )
        for y in range(start.y, end.y + 1):
            _walk(
                operation_id=write.operation_id,
                fabric_id=write.fabric_id,
                start=Coordinate(x=start.x, y=y),
                end=Coordinate(x=end.x, y=y),
                kind="branch",
                graph_index=graph_index,
                edges=edges,
                seen_links=seen_links,
            )
    else:
        _walk(
            operation_id=write.operation_id,
            fabric_id=write.fabric_id,
            start=source_coordinate,
            end=Coordinate(x=start.x, y=start.y),
            kind="approach",
            graph_index=graph_index,
            edges=edges,
            seen_links=seen_links,
        )
        _walk(
            operation_id=write.operation_id,
            fabric_id=write.fabric_id,
            start=Coordinate(x=start.x, y=start.y),
            end=Coordinate(x=end.x, y=start.y),
            kind="spine",
            graph_index=graph_index,
            edges=edges,
            seen_links=seen_links,
        )
        for x in range(start.x, end.x + 1):
            _walk(
                operation_id=write.operation_id,
                fabric_id=write.fabric_id,
                start=Coordinate(x=x, y=start.y),
                end=Coordinate(x=x, y=end.y),
                kind="branch",
                graph_index=graph_index,
                edges=edges,
                seen_links=seen_links,
            )

    return RectangleTreePlan(
        policy="corner_rectangle_tree_v1",
        operation_id=write.operation_id,
        fabric_id=write.fabric_id,
        source_endpoint_id=write.source_endpoint_id,
        source_router_id=source_router.router_id,
        entry_router_id=entry_router.router_id,
        recipients=tuple(recipients),
        edges=tuple(edges),
    )
