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
    GenericOpCompute,
    GenericOpSignal,
    GenericOpTransfer,
    GenericOpWait,
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
    PlanInstructionProgram,
    PlanMemoryService,
    PlanNodeDistance,
    PlanProgramOp,
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
    for program in batch.programs:
        for op in program.ops:
            if isinstance(op, GenericOpSignal):
                deltas[op.counter_id] = deltas.get(op.counter_id, 0) + op.delta
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

    def transfer_plan(
        label: str,
        network_id: str,
        source: str,
        destination: str,
        payload_bytes: int,
        address: int | None,
        depends_on: tuple[str, ...],
        start_cycles: float,
    ) -> PlanTransfer:
        for endpoint_id in (source, destination):
            if endpoint_id not in endpoints:
                raise ValueError(f"transfer {label}: unknown endpoint {endpoint_id}")
            attached = endpoint_network[endpoint_id]
            if attached != network_id:
                raise ValueError(
                    f"transfer {label}: endpoint {endpoint_id} is attached to "
                    f"network {attached}, not {network_id}; cross-network "
                    "references require bridging, which is not supported"
                )
        memory_service: PlanMemoryService | None = None
        if address is not None:
            source_memory = source in memories
            destination_memory = destination in memories
            if source_memory == destination_memory:
                raise ValueError(
                    f"transfer {label}: an addressed access must name exactly "
                    "one memory service endpoint"
                )
            memory_endpoint = destination if destination_memory else source
            resource = memories[memory_endpoint]
            if resource.hierarchy is None:
                raise ValueError(
                    f"transfer {label}: addressed access on flat memory "
                    f"{resource.resource_id}"
                )
            if address + payload_bytes > resource.capacity_bytes:
                raise ValueError(
                    f"transfer {label}: address range "
                    f"[{address}, {address + payload_bytes}) exceeds capacity "
                    f"{resource.capacity_bytes} of {resource.resource_id}"
                )
            hierarchy = resource.hierarchy
            stripe = address // hierarchy.stripe_bytes
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
        if network_id not in policies:
            raise ValueError(f"transfer {label}: unknown network {network_id}")
        policy = policies[network_id]
        routing: Literal["static", "shortest_path", "adaptive"] = "static"
        source_node: str | None = None
        destination_node: str | None = None
        route_link_ids: tuple[str, ...] | None = None
        if policy == "static_table":
            route = next(
                (
                    route
                    for route in graph.static_routes
                    if route.network_id == network_id
                    and route.source == source
                    and route.destination == destination
                ),
                None,
            )
            if route is not None:
                route_link_ids = tuple(route.link_ids)
        else:
            routing = policy
            source_node = endpoints[source]
            destination_node = endpoints[destination]
            dynamic_usage.setdefault(network_id, set()).add(destination_node)
            member_links = [
                link for link in graph.links if link.network_id == network_id
            ]
            if source_node in _bfs_distances(member_links, destination_node):
                route_link_ids = ()
        terminal: Literal["route_unreachable", "capacity_exceeded"] | None = None
        hops: tuple[PlanHop, ...] = ()
        if route_link_ids is None:
            terminal = "route_unreachable"
            memory_service = None
        else:
            capacity = capacities.get(destination)
            if capacity is not None and payload_bytes > capacity:
                terminal = "capacity_exceeded"
                memory_service = None
            else:
                hops = tuple(
                    PlanHop(
                        network_id=network_id,
                        link_id=link_id,
                        timing=timing[(network_id, link_id)],
                    )
                    for link_id in route_link_ids
                )
        return PlanTransfer(
            transaction_id=label,
            kind="transfer",
            network_id=network_id,
            source=source,
            destination=destination,
            payload_bytes=payload_bytes,
            address=address,
            depends_on=depends_on,
            start_cycles=start_cycles,
            routing=routing,
            source_node=source_node,
            destination_node=destination_node,
            hops=hops,
            terminal=terminal,
            memory_service=memory_service,
        )

    def compute_plan(
        label: str,
        unit_id: str,
        duration_cycles: float,
        depends_on: tuple[str, ...],
        start_cycles: float,
    ) -> PlanCompute:
        if unit_id not in units:
            raise ValueError(f"compute {label}: unknown execution unit {unit_id}")
        return PlanCompute(
            transaction_id=label,
            kind="compute",
            unit_id=unit_id,
            duration_cycles=duration_cycles,
            depends_on=depends_on,
            start_cycles=start_cycles,
        )

    def wait_plan(
        label: str,
        counter_id: str,
        threshold: int,
        depends_on: tuple[str, ...],
        start_cycles: float,
    ) -> PlanWait:
        return PlanWait(
            transaction_id=label,
            kind="wait",
            counter_id=counter_id,
            threshold=threshold,
            depends_on=depends_on,
            start_cycles=start_cycles,
            reachable=bounds[counter_id],
        )

    def signal_plan(
        label: str,
        counter_id: str,
        delta: int,
        depends_on: tuple[str, ...],
        start_cycles: float,
    ) -> PlanSignal:
        return PlanSignal(
            transaction_id=label,
            kind="signal",
            counter_id=counter_id,
            delta=delta,
            depends_on=depends_on,
            start_cycles=start_cycles,
        )

    for transaction in batch.transactions:
        depends_on = tuple(transaction.depends_on)
        start_cycles = transaction.start_cycles
        if isinstance(transaction, GenericTransfer):
            transactions.append(transfer_plan(
                transaction.transaction_id,
                transaction.network_id,
                transaction.source,
                transaction.destination,
                transaction.payload_bytes,
                transaction.address,
                depends_on,
                start_cycles,
            ))
        elif isinstance(transaction, GenericCompute):
            transactions.append(compute_plan(
                transaction.transaction_id,
                transaction.unit_id,
                transaction.duration_cycles,
                depends_on,
                start_cycles,
            ))
        elif isinstance(transaction, GenericWait):
            transactions.append(wait_plan(
                transaction.transaction_id,
                transaction.counter_id,
                transaction.threshold,
                depends_on,
                start_cycles,
            ))
        else:
            transactions.append(signal_plan(
                transaction.transaction_id,
                transaction.counter_id,
                transaction.delta,
                depends_on,
                start_cycles,
            ))

    programs: list[PlanInstructionProgram] = []
    for program in batch.programs:
        if program.unit_id not in units:
            raise ValueError(
                f"program {program.program_id}: unknown execution unit "
                f"{program.unit_id}"
            )
        plan_ops: list[PlanProgramOp] = []
        previous: str | None = None
        for op in program.ops:
            label = f"{program.program_id}/{op.op_id}"
            if op.depends_on is None:
                op_deps = (previous,) if previous is not None else ()
            else:
                op_deps = tuple(
                    f"{program.program_id}/{dep}" for dep in op.depends_on
                )
            if isinstance(op, GenericOpTransfer):
                transactions.append(transfer_plan(
                    label, op.network_id, op.source, op.destination,
                    op.payload_bytes, op.address, op_deps, 0.0,
                ))
            elif isinstance(op, GenericOpCompute):
                transactions.append(compute_plan(
                    label, op.unit_id, op.duration_cycles, op_deps, 0.0,
                ))
            elif isinstance(op, GenericOpWait):
                transactions.append(wait_plan(
                    label, op.counter_id, op.threshold, op_deps, 0.0,
                ))
            else:
                transactions.append(signal_plan(
                    label, op.counter_id, op.delta, op_deps, 0.0,
                ))
            plan_ops.append(PlanProgramOp(
                op_id=op.op_id,
                transaction_id=label,
                issue_cycles=op.issue_cycles,
            ))
            previous = label
        programs.append(PlanInstructionProgram(
            program_id=program.program_id,
            unit_id=program.unit_id,
            start_cycles=program.start_cycles,
            ops=tuple(plan_ops),
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
        programs=tuple(programs),
        max_cycles=batch.max_cycles,
    )
    return ImmutablePlan(
        spec_sha256=content_digest(normalized_spec.model_dump(mode="json")),
        content=content,
        plan_sha256=content_digest(content.model_dump(mode="json")),
    )
