"""Memory endpoint admission and pure torus routes; no v2 traffic templates."""

from __future__ import annotations

from dataclasses import dataclass

from .configs.schemas.memory_replay import (
    MemoryEndpointBinding,
    MemoryOperation,
    MemorySystemConfig,
)
from .configs.schemas.topology import CanonicalTopology, GraphRecord, Identifier
from .configs.schemas.torus_replay import RouteRecord
from .memory_plan import MemoryPlan
from .torus import TorusRouting
from .torus_dependencies import ResourceDependencies


class MemoryOperationRoutes(GraphRecord):
    operation_id: Identifier
    initiator_resource_id: Identifier
    target_resource_id: Identifier
    request: RouteRecord
    response: RouteRecord | None


@dataclass(frozen=True)
class MemoryRoutes:
    routing: TorusRouting
    operations: tuple[MemoryOperationRoutes, ...]
    dependencies: ResourceDependencies

    @classmethod
    def compile(cls, plan: MemoryPlan) -> MemoryRoutes:
        if not plan.config.routing:
            raise ValueError("memory wire compilation requires explicit routing settings")
        routing = TorusRouting.from_graph(plan.graph, plan.config.routing)
        operations = tuple(_operation_routes(plan, routing, operation)
                           for operation in plan.config.operations
                           if operation.kind in {"read", "write_posted", "write_acknowledged"})
        paths = tuple(route.hops for operation in operations
                      for route in (operation.request, operation.response) if route is not None)
        pairs = tuple((operation.request, operation.response) for operation in operations
                      if operation.response is not None)
        return cls(routing, operations, ResourceDependencies.from_paths(paths, pairs))


def _operation_routes(plan: MemoryPlan, routing: TorusRouting,
                      operation: MemoryOperation) -> MemoryOperationRoutes:
    return operation_routes(plan.config, plan.graph, routing, operation)


def operation_routes(config: MemorySystemConfig, graph: CanonicalTopology, routing: TorusRouting,
                     operation: MemoryOperation) -> MemoryOperationRoutes:
    """Shared addressed route admission for standalone and mixed memory clients."""
    if operation.source is None or operation.destination is None or operation.fabric_id is None:
        raise ValueError("network operation requires both ranges and a fabric")
    endpoints = {item.endpoint_id: item for item in config.endpoints}
    resources = {item.resource_id: item for item in graph.resources}
    resource_configs = {item.resource_id: item for item in config.resources}
    buffers = {item.buffer_id: item for item in config.buffers}
    initiator = endpoints[operation.initiator_id]
    if not initiator.enabled or "initiator" not in initiator.roles:
        raise ValueError(f"operation {operation.operation_id}: endpoint cannot initiate")
    router = routing.routers[(initiator.fabric_id, initiator.router_id)]
    if router.tile_id not in graph.enabled_worker_ids:
        raise ValueError("memory initiation requires an enabled worker")
    if operation.source.size_bytes != operation.destination.size_bytes:
        raise ValueError("memory source and destination lengths must agree")
    for access, write in ((operation.source, False), (operation.destination, True)):
        buffer = buffers[access.buffer_id]
        resource = resources[buffer.resource_id]
        capacity = resource_configs[buffer.resource_id].capacity_override_bytes or resource.capacity_bytes
        address = buffer.base_address + access.offset_bytes
        if access.offset_bytes + access.size_bytes > buffer.size_bytes or address + access.size_bytes > capacity:
            raise ValueError("memory operation range exceeds buffer/resource capacity")
        if address % config.packet.address_alignment_bytes:
            raise ValueError("memory operation address is not aligned")
        if not (buffer.writable if write else buffer.readable):
            raise ValueError("memory operation violates buffer permission")
    local = buffers[(operation.destination if operation.kind == "read" else operation.source).buffer_id]
    remote = buffers[(operation.source if operation.kind == "read" else operation.destination).buffer_id]
    local_resource = resources[local.resource_id]
    if (local.resource_id not in initiator.resource_ids or local_resource.kind != "local_sram"
            or local_resource.owner_tile_id != router.tile_id):
        raise ValueError("initiator must own the local L1 buffer")

    # Multiple fabric aliases identify the same resource; never duplicate its state.
    candidates = tuple(e for e in config.endpoints if e.enabled and e.fabric_id == operation.fabric_id
                       and "initiator" in e.roles and local.resource_id in e.resource_ids
                       and routing.routers[(e.fabric_id, e.router_id)].tile_id == router.tile_id)
    source = _one_endpoint(candidates, "initiator interface")
    targets = tuple(e for e in config.endpoints if e.enabled and e.fabric_id == operation.fabric_id
                    and "target" in e.roles and remote.resource_id in e.resource_ids)
    target = _one_endpoint(targets, "target attachment")
    request = routing.endpoint_path(operation.fabric_id, source.endpoint_id, target.endpoint_id, "request")
    response = None
    if operation.kind != "write_posted":
        if "response_sink" not in source.roles:
            raise ValueError("read/acknowledged write requires a response sink")
        response = routing.endpoint_path(operation.fabric_id, target.endpoint_id, source.endpoint_id, "response")
    return MemoryOperationRoutes(operation_id=operation.operation_id,
                                 initiator_resource_id=local.resource_id, target_resource_id=remote.resource_id,
                                 request=request, response=response)


def _one_endpoint(candidates: tuple[MemoryEndpointBinding, ...], label: str) -> MemoryEndpointBinding:
    if len(candidates) != 1:
        raise ValueError(f"memory operation requires exactly one enabled {label}, found {len(candidates)}")
    return candidates[0]
