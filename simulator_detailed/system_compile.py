"""Pure `compile_system`: SystemSpec -> ImmutablePlan.

Compilation revalidates the input, checks every entity/resource/port/
attachment/fabric/transaction reference, resolves routes and effective link
timings, classifies terminal transfers, computes counter reachability bounds
and emits a frozen, content-addressed plan. It constructs no SimPy object
and mutates no global state.
"""

from __future__ import annotations

from collections import deque
from typing import Literal, cast

from .configs.schemas.generic_graph import GenericLink, GenericMemoryResource
from .configs.schemas.generic_transactions import (
    GenericCompute,
    GenericLinkTiming,
    GenericSignal,
    GenericTransfer,
    GenericWait,
)
from .configs.schemas.system_spec import (
    ImmutablePlan,
    PlanCompute,
    PlanContent,
    PlanCounter,
    PlanDestinationDistances,
    PlanDynamicLink,
    PlanDynamicNetwork,
    PlanHop,
    PlanMemoryService,
    PlanNodeDistance,
    PlanResource,
    PlanSignal,
    PlanTransaction,
    PlanTransfer,
    PlanWait,
    SystemSpec,
)
from .generic_graph import generic_digest, normalize_generic
from .topology import content_digest


def _bfs_distances(links: list[GenericLink], destination: str) -> dict[str, int]:
    """Directed hop distances to `destination` via reversed edges."""
    reverse: dict[str, list[str]] = {}
    for link in links:
        reverse.setdefault(link.dst_node, []).append(link.src_node)
    distances = {destination: 0}
    queue = deque([destination])
    while queue:
        node = queue.popleft()
        for previous in sorted(reverse.get(node, ())):
            if previous not in distances:
                distances[previous] = distances[node] + 1
                queue.append(previous)
    return distances


def compile_system(spec: SystemSpec) -> ImmutablePlan:
    """Validate and freeze one system spec into an immutable plan; pure."""
    spec = SystemSpec.model_validate(spec.model_dump(mode="json"))
    graph = normalize_generic(spec.graph)
    batch = spec.batch
    normalized_spec = spec.model_copy(update={"graph": graph})

    endpoints: dict[str, str] = {e.endpoint_id: e.node_id for e in graph.dma_endpoints}
    endpoints.update({u.unit_id: u.node_id for u in graph.execution_units})
    endpoint_network: dict[str, str] = {
        e.endpoint_id: e.network_id for e in graph.dma_endpoints
    }
    endpoint_network.update({u.unit_id: u.network_id for u in graph.execution_units})
    capacities: dict[str, int] = {}
    memories: dict[str, GenericMemoryResource] = {}
    for resource in graph.memory_resources:
        if (
            resource.endpoint_id is not None
            and resource.owner_node is not None
            and resource.network_id is not None
        ):
            endpoints[resource.endpoint_id] = resource.owner_node
            endpoint_network[resource.endpoint_id] = resource.network_id
            capacities[resource.endpoint_id] = resource.capacity_bytes
            memories[resource.endpoint_id] = resource
    units = {u.unit_id for u in graph.execution_units}
    networks = {n.network_id for n in graph.networks}
    policies: dict[str, Literal["static_table", "shortest_path", "adaptive"]] = {
        n.network_id: n.routing for n in graph.networks
    }

    timing: dict[tuple[str, str], GenericLinkTiming] = {}
    for network in batch.timing:
        if network.network_id not in networks:
            raise ValueError(f"timing declared for unknown network {network.network_id}")
        member_links = {link.link_id for link in graph.links if link.network_id == network.network_id}
        for link_id in member_links:
            timing[(network.network_id, link_id)] = network.link
        for override in network.overrides:
            if override.link_id not in member_links:
                raise ValueError(
                    f"timing override for unknown link {override.link_id} "
                    f"in network {network.network_id}"
                )
            timing[(network.network_id, override.link_id)] = override.timing

    deltas: dict[str, int] = {}
    for transaction in batch.transactions:
        if isinstance(transaction, GenericSignal):
            deltas[transaction.counter_id] = deltas.get(transaction.counter_id, 0) + transaction.delta
    counters = tuple(
        PlanCounter(
            counter_id=counter.counter_id,
            initial_value=counter.initial_value,
            upper_bound=counter.initial_value + deltas.get(counter.counter_id, 0),
        )
        for counter in sorted(batch.counters, key=lambda c: c.counter_id)
    )
    bounds = {counter.counter_id: counter.upper_bound for counter in counters}

    transactions: list[PlanTransaction] = []
    dynamic_usage: dict[str, set[str]] = {}
    for transaction in batch.transactions:
        depends_on = tuple(transaction.depends_on)
        start_cycles = transaction.start_cycles
        if isinstance(transaction, GenericTransfer):
            for endpoint_id in (transaction.source, transaction.destination):
                if endpoint_id not in endpoints:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: unknown endpoint {endpoint_id}"
                    )
                attached = endpoint_network[endpoint_id]
                if attached != transaction.network_id:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: endpoint "
                        f"{endpoint_id} is attached to network {attached}, not "
                        f"{transaction.network_id}; cross-network references "
                        "require bridging, which is not supported"
                    )
            memory_service: PlanMemoryService | None = None
            if transaction.address is not None:
                source_memory = transaction.source in memories
                destination_memory = transaction.destination in memories
                if source_memory == destination_memory:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: an addressed access "
                        "must name exactly one memory service endpoint"
                    )
                memory_endpoint = (
                    transaction.destination if destination_memory else transaction.source
                )
                resource = memories[memory_endpoint]
                if resource.hierarchy is None:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: addressed access on "
                        f"flat memory {resource.resource_id}"
                    )
                if transaction.address + transaction.payload_bytes > resource.capacity_bytes:
                    raise ValueError(
                        f"transfer {transaction.transaction_id}: address range "
                        f"[{transaction.address}, {transaction.address + transaction.payload_bytes}) "
                        f"exceeds capacity {resource.capacity_bytes} of {resource.resource_id}"
                    )
                hierarchy = resource.hierarchy
                stripe = transaction.address // hierarchy.stripe_bytes
                bank_index = stripe % hierarchy.banks
                port = hierarchy.ports[stripe % len(hierarchy.ports)]
                memory_service = PlanMemoryService(
                    resource_id=resource.resource_id,
                    direction="write" if destination_memory else "read",
                    bank_id=f"{resource.resource_id}/bank_{bank_index}",
                    port_id=f"{resource.resource_id}/port_{port.port_id}",
                    channel_id=f"{resource.resource_id}/channel_{port.channel_id}",
                    command_cycles=port.command_cycles,
                    latency_cycles=hierarchy.latency_cycles,
                    channel_bytes_per_cycle=next(
                        channel.bytes_per_cycle
                        for channel in hierarchy.channels
                        if channel.channel_id == port.channel_id
                    ),
                )
            if transaction.network_id not in policies:
                raise ValueError(
                    f"transfer {transaction.transaction_id}: unknown network "
                    f"{transaction.network_id}"
                )
            policy = policies[transaction.network_id]
            routing: Literal["static", "shortest_path", "adaptive"] = "static"
            source_node: str | None = None
            destination_node: str | None = None
            route_link_ids: tuple[str, ...] | None = None
            if policy == "static_table":
                route = next(
                    (
                        route
                        for route in graph.static_routes
                        if route.network_id == transaction.network_id
                        and route.source == transaction.source
                        and route.destination == transaction.destination
                    ),
                    None,
                )
                if route is not None:
                    route_link_ids = tuple(route.link_ids)
            else:
                routing = policy
                source_node = endpoints[transaction.source]
                destination_node = endpoints[transaction.destination]
                dynamic_usage.setdefault(transaction.network_id, set()).add(destination_node)
                member_links = [
                    link for link in graph.links if link.network_id == transaction.network_id
                ]
                if source_node in _bfs_distances(member_links, destination_node):
                    route_link_ids = ()
            terminal: Literal["route_unreachable", "capacity_exceeded"] | None = None
            hops: tuple[PlanHop, ...] = ()
            if route_link_ids is None:
                terminal = "route_unreachable"
                memory_service = None
            else:
                capacity = capacities.get(transaction.destination)
                if capacity is not None and transaction.payload_bytes > capacity:
                    terminal = "capacity_exceeded"
                    memory_service = None
                else:
                    hops = tuple(
                        PlanHop(
                            network_id=transaction.network_id,
                            link_id=link_id,
                            timing=timing[(transaction.network_id, link_id)],
                        )
                        for link_id in route_link_ids
                    )
            transactions.append(PlanTransfer(
                transaction_id=transaction.transaction_id,
                kind="transfer",
                network_id=transaction.network_id,
                source=transaction.source,
                destination=transaction.destination,
                payload_bytes=transaction.payload_bytes,
                address=transaction.address,
                depends_on=depends_on,
                start_cycles=start_cycles,
                routing=routing,
                source_node=source_node,
                destination_node=destination_node,
                hops=hops,
                terminal=terminal,
                memory_service=memory_service,
            ))
        elif isinstance(transaction, GenericCompute):
            if transaction.unit_id not in units:
                raise ValueError(
                    f"compute {transaction.transaction_id}: unknown execution unit "
                    f"{transaction.unit_id}"
                )
            transactions.append(PlanCompute(
                transaction_id=transaction.transaction_id,
                kind="compute",
                unit_id=transaction.unit_id,
                duration_cycles=transaction.duration_cycles,
                depends_on=depends_on,
                start_cycles=start_cycles,
            ))
        elif isinstance(transaction, GenericWait):
            transactions.append(PlanWait(
                transaction_id=transaction.transaction_id,
                kind="wait",
                counter_id=transaction.counter_id,
                threshold=transaction.threshold,
                depends_on=depends_on,
                start_cycles=start_cycles,
                reachable=bounds[transaction.counter_id],
            ))
        else:
            transactions.append(PlanSignal(
                transaction_id=transaction.transaction_id,
                kind="signal",
                counter_id=transaction.counter_id,
                delta=transaction.delta,
                depends_on=depends_on,
                start_cycles=start_cycles,
            ))

    resources: list[PlanResource] = []
    for network in sorted(batch.timing, key=lambda n: n.network_id):
        for link in sorted(
            (link for link in graph.links if link.network_id == network.network_id),
            key=lambda link: link.link_id,
        ):
            resources.append(PlanResource(
                resource_id=f"{network.network_id}/{link.link_id}",
                kind="link",
                capacity=timing[(network.network_id, link.link_id)].buffer_slots,
            ))
    for unit_id in sorted(units):
        resources.append(PlanResource(
            resource_id=unit_id,
            kind="execution_unit",
            capacity=1,
        ))
    for resource in sorted(graph.memory_resources, key=lambda r: r.resource_id):
        if resource.hierarchy is None:
            continue
        hierarchy = resource.hierarchy
        for bank_index in range(hierarchy.banks):
            resources.append(PlanResource(
                resource_id=f"{resource.resource_id}/bank_{bank_index}",
                kind="memory_bank",
                capacity=1,
            ))
        for port in sorted(hierarchy.ports, key=lambda p: p.port_id):
            resources.append(PlanResource(
                resource_id=f"{resource.resource_id}/port_{port.port_id}",
                kind="memory_port",
                capacity=1,
            ))
        for channel in sorted(hierarchy.channels, key=lambda c: c.channel_id):
            resources.append(PlanResource(
                resource_id=f"{resource.resource_id}/channel_{channel.channel_id}",
                kind="memory_channel",
                capacity=1,
            ))

    dynamic_tables: list[PlanDynamicNetwork] = []
    for network_id in sorted(dynamic_usage):
        member_links = sorted(
            (link for link in graph.links if link.network_id == network_id),
            key=lambda link: link.link_id,
        )
        dynamic_tables.append(PlanDynamicNetwork(
            network_id=network_id,
            routing=cast(Literal["shortest_path", "adaptive"], policies[network_id]),
            links=tuple(
                PlanDynamicLink(
                    link_id=link.link_id,
                    src_node=link.src_node,
                    dst_node=link.dst_node,
                    timing=timing[(network_id, link.link_id)],
                )
                for link in member_links
            ),
            distances=tuple(
                PlanDestinationDistances(
                    destination_node=destination,
                    distances=tuple(
                        PlanNodeDistance(node=node, distance=distance)
                        for node, distance in sorted(
                            _bfs_distances(member_links, destination).items()
                        )
                    ),
                )
                for destination in sorted(dynamic_usage[network_id])
            ),
        ))

    content = PlanContent(
        spec_id=normalized_spec.spec_id,
        system_id=graph.system_id,
        graph_sha256=generic_digest(graph),
        batch_sha256=content_digest(batch.model_dump(mode="json")),
        resources=tuple(resources),
        counters=counters,
        transactions=tuple(transactions),
        dynamic_networks=tuple(dynamic_tables),
        max_cycles=batch.max_cycles,
    )
    return ImmutablePlan(
        spec_sha256=content_digest(normalized_spec.model_dump(mode="json")),
        content=content,
        plan_sha256=content_digest(content.model_dump(mode="json")),
    )
