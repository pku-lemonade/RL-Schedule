"""Input/event checks for the multicast prototype; missing evidence cannot pass.

This module deliberately imports no production tree, transport, service, scalar
or pipeline helper. Full-runtime checks stay unavailable until shared-resource
observations exist; tree and integer checks can still expose corrupt prototypes.
"""

import math
from collections import Counter

from .data import Data, array, integer, number, obj, require, rows, text


class MulticastAuditUnavailable(ValueError):
    """The prototype does not expose evidence required by the selected check."""


def memory_results(raw: Data) -> list[Data]:
    return rows(raw["memory"]) if "memory" in raw else [raw]


def _configuration(configuration: Data) -> tuple[Data, Data]:
    workload = obj(configuration.get("multicast", configuration))
    return workload, obj(workload["memory"])


def _tree(write: Data, graph: Data) -> tuple[set[str], set[str]]:
    """Reconstruct a corner rectangle directly from coordinates and endpoints."""
    fabric = integer(write["fabric_id"])
    routers = {text(router["router_id"]): router for router in rows(graph["routers"])
               if router["fabric_id"] == fabric}
    coordinates = {(integer(obj(router["coordinate"])["x"]), integer(obj(router["coordinate"])["y"])): router
                   for router in routers.values()}
    attachments = [endpoint for endpoint in rows(graph["attachments"]) if endpoint["fabric_id"] == fabric]
    source = next(endpoint for endpoint in attachments if endpoint["endpoint_id"] == write["source_endpoint_id"])
    source_coordinate = obj(routers[text(source["router_id"])] ["coordinate"])
    sx, sy = integer(source_coordinate["x"]), integer(source_coordinate["y"])
    rectangle = obj(write["rectangle"])
    start, end = obj(rectangle["start"]), obj(rectangle["end"])
    x0, y0, x1, y1 = integer(start["x"]), integer(start["y"]), integer(end["x"]), integer(end["y"])
    expected_edges: set[str] = set()

    def walk(begin: tuple[int, int], finish: tuple[int, int]) -> None:
        x, y = begin
        while (x, y) != finish:
            following = (x + int(x < finish[0]), y + int(y < finish[1]))
            require(following != (x, y), "unsupported rectangle approach")
            left, right = coordinates[x, y], coordinates[following]
            require(left["enabled"] is True and right["enabled"] is True, "disabled tree router")
            matches = [link for link in rows(graph["links"])
                       if link["fabric_id"] == fabric and link["src_router"] == left["router_id"]
                       and link["dst_router"] == right["router_id"] and link["enabled"] is True
                       and link["wrap"] is False]
            require(len(matches) == 1, "missing or ambiguous tree link")
            identifier = text(matches[0]["link_id"])
            require(identifier not in expected_edges, "tree edge repeats")
            expected_edges.add(identifier)
            x, y = following

    walk((sx, sy), (x0, y0))
    if rectangle["major_axis"] == "x":
        walk((x0, y0), (x0, y1))
        for y in range(y0, y1 + 1):
            walk((x0, y), (x1, y))
    else:
        walk((x0, y0), (x1, y0))
        for x in range(x0, x1 + 1):
            walk((x, y0), (x, y1))
    recipients: set[str] = set()
    enabled = array(graph["enabled_worker_ids"])
    for router in routers.values():
        coord = obj(router["coordinate"])
        if (router["tile_id"] not in enabled or not x0 <= integer(coord["x"]) <= x1
                or not y0 <= integer(coord["y"]) <= y1):
            continue
        endpoints = [endpoint for endpoint in attachments
                     if endpoint["router_id"] == router["router_id"] and endpoint["role"] == "compute"]
        require(len(endpoints) == 1, "missing or aliased worker recipient")
        endpoint = endpoints[0]
        require(endpoint["enabled"] is True and endpoint["replay_enabled"] is True,
                "unavailable worker recipient")
        identifier = text(endpoint["endpoint_id"])
        if identifier != write["source_endpoint_id"] or rectangle["include_source"] is True:
            recipients.add(identifier)
    return expected_edges, recipients


def _audit_trees(raw: Data, workload: Data, memory: Data, graph: Data) -> None:
    writes = {text(write["operation_id"]): write for write in rows(workload.get("writes", []))}
    packet = obj(memory["packet"])
    width = integer(packet["physical_flit_bytes"])
    segment_size, capacity = integer(packet["max_segment_payload_bytes"]), integer(packet["data_capacity_bytes"])
    seen_operations: set[str] = set()
    for result in memory_results(raw):
        transport = obj(result.get("transport", {}))
        operations = rows(result.get("operations", []))
        selected = {text(operation["operation_id"]) for operation in operations}
        expected_launches: Counter[tuple[str, int, int, str]] = Counter()
        expected_deliveries: Counter[tuple[str, int, int, str]] = Counter()
        expected_packet_bytes = 0
        for operation_id in selected:
            require(operation_id in writes, "undeclared multicast operation")
            write = writes[operation_id]
            links, recipients = _tree(write, graph)
            require(recipients == {text(destination["endpoint_id"]) for destination in rows(write["destinations"])},
                    "input destinations differ from rectangle workers")
            for segment, offset in enumerate(range(0, integer(write["size_bytes"]), segment_size)):
                size = min(segment_size, integer(write["size_bytes"]) - offset)
                flit_count = integer(packet["header_flits"]) + (size + capacity - 1) // capacity
                expected_packet_bytes += flit_count * width
                for flit in range(flit_count):
                    expected_launches.update((operation_id, segment, flit, link) for link in links)
                    expected_deliveries.update((operation_id, segment, flit, recipient) for recipient in recipients)
        launches: Counter[tuple[str, int, int, str]] = Counter()
        deliveries: Counter[tuple[str, int, int, str]] = Counter()
        channel_bytes = 0
        for event in rows(transport.get("events", [])):
            action = event["action"]
            operation_id = text(event["operation_id"])
            require(operation_id in selected, "event belongs to undeclared operation")
            require(event["fabric_id"] == writes[operation_id]["fabric_id"], "event fabric differs from input")
            if action in {"tree_inject", "tree_forward", "tree_replicate", "recipient_deliver"}:
                key = (operation_id, integer(event["segment_index"]), integer(event["flit_index"]),
                       text(event["recipient_endpoint_id"] if action == "recipient_deliver" else event["link_id"]))
                if action == "recipient_deliver":
                    deliveries[key] += 1
                else:
                    require(integer(event["physical_bytes"]) == width, "tree flit width differs from input")
                    launches[key] += 1
                    channel_bytes += width
        require(not launches - expected_launches and not deliveries - expected_deliveries,
                "duplicate or undeclared tree flit/recipient")
        require(channel_bytes == integer(transport.get("launched_channel_bytes", 0)), "uncharged tree edge flits")
        if transport.get("status") == "complete" and selected:
            require(launches == expected_launches, "missing declared tree edge flits")
            require(deliveries == expected_deliveries, "missing declared recipient flits")
            require(integer(transport["packet_physical_bytes"]) == expected_packet_bytes,
                    "packet geometry differs from declared header/payload")
        for operation in operations:
            if operation["status"] == "complete":
                identifier = text(operation["operation_id"])
                require(identifier not in seen_operations, "duplicate completed multicast operation")
                seen_operations.add(identifier)
    if raw.get("status") == "complete":
        require(seen_operations == set(writes), "complete result omits multicast operations")


def _audit_scalar(raw: Data, workload: Data, memory: Data) -> None:
    scalar = obj(raw.get("scalar", {}))
    counters = {text(counter["counter_id"]): counter for counter in rows(workload.get("counters", []))}
    increments = {text(increment["operation_id"]): increment for increment in rows(workload.get("increments", []))}
    values = {identifier: integer(counter["initial_value"]) for identifier, counter in counters.items()}
    completed: set[str] = set()
    linearization: dict[str, float] = {}
    for event in rows(scalar.get("events", [])):
        if event["action"] == "atomic_effect":
            identifier, counter_id = text(event["operation_id"]), text(event["counter_id"])
            require(identifier in increments and identifier not in completed, "undeclared or duplicate scalar effect")
            require(increments[identifier]["counter_id"] == counter_id, "scalar effect targets wrong counter")
            require(integer(event["old_value"]) == values[counter_id]
                    and integer(event["new_value"]) == values[counter_id] + 1, "invalid scalar transition")
            values[counter_id] += 1
            completed.add(identifier)
            linearization[identifier] = float(number(event["time_aci_cycles"]))
        elif event["action"] == "atomic_return":
            identifier = text(event["operation_id"])
            require(identifier in completed, "scalar return precedes visible update")
            require(number(event["time_aci_cycles"]) >= linearization[identifier], "early scalar return")
    states = rows(scalar.get("counters", []))
    require(len(states) == len(counters) and {text(state["counter_id"]) for state in states} == set(counters),
            "missing or duplicate scalar state")
    require(all(integer(state["value"]) == values[text(state["counter_id"])] for state in states),
            "scalar state differs from visible transitions")
    records = rows(scalar.get("increments", []))
    require(len({text(record["operation_id"]) for record in records}) == len(records), "duplicate scalar record")
    for record in records:
        if record["status"] == "complete":
            identifier = text(record["operation_id"])
            require(identifier in completed, "completed scalar record has no effect")
            require(integer(record["new_value"]) == integer(record["old_value"]) + 1, "scalar increment is not exactly one")
            require(number(record["linearization_aci_cycles"]) == linearization[identifier],
                    "scalar record disagrees with linearization event")
            require(integer(record["request_physical_bytes"]) == integer(obj(memory["packet"])["physical_flit_bytes"]),
                    "wrong inline request width")
    waits = {text(wait["wait_id"]): wait for wait in rows(workload.get("waits", []))}
    for wait in rows(scalar.get("waits", [])):
        identifier = text(wait["wait_id"])
        require(identifier in waits and wait["threshold"] == waits[identifier]["threshold"], "undeclared wait/threshold")
        if wait["status"] == "complete":
            require(integer(wait["observed_value"]) >= integer(wait["threshold"]) and wait["data_ready"] is True,
                    "wait released before threshold/data")
    if raw.get("status") == "complete":
        require(completed == set(increments), "complete result omits scalar updates")
        require({text(wait["wait_id"]) for wait in rows(scalar.get("waits", [])) if wait["status"] == "complete"} == set(waits),
                "complete result omits local waits")


def audit_multicast(check: str, raw: Data, configuration: Data, graph: Data) -> None:
    workload, memory = _configuration(configuration)
    if check in {"multicast", "routing", "packet_accounting"}:
        _audit_trees(raw, workload, memory, graph)
        if check == "packet_accounting":
            raise MulticastAuditUnavailable("tree edge accounting is observable; physical injection/ejection and return paths are missing")
        return
    if check in {"synchronization", "causality", "compute_work"}:
        _audit_scalar(raw, workload, memory)
        if check == "causality":
            finish = {text(operation["operation_id"]): number(operation["completion_aci_cycles"])
                      for result in memory_results(raw) for operation in rows(result.get("operations", []))
                      if operation["status"] == "complete"}
            for event in rows(obj(raw.get("scalar", {})).get("events", [])):
                if event["action"] == "atomic_submit":
                    operation = next(item for item in rows(workload.get("increments", []))
                                     if item["operation_id"] == event["operation_id"])
                    for dependency in array(operation.get("depends_on", [])):
                        if text(dependency) in finish:
                            require(number(event["time_aci_cycles"]) >= finish[text(dependency)],
                                    "scalar submitted before its memory dependency completed")
        raise MulticastAuditUnavailable("shared scalar service, notification paths and compute generation evidence are missing")
    if check in {"memory_service", "ownership", "drain"}:
        for result in memory_results(raw):
            transport = obj(result.get("transport", {}))
            if raw.get("status") == "complete":
                require(not obj(transport.get("snapshot", {})).get("active_reservation_ids", []),
                        "complete result retains tree reservations")
            for reservation in rows(transport.get("reservations", [])):
                release = reservation.get("released_aci_cycles")
                if release is not None:
                    require(number(release) >= number(reservation["acquired_aci_cycles"]), "early reservation release")
            for operation in rows(result.get("operations", [])):
                if operation["status"] == "complete":
                    require(integer(operation["destination_write_useful_bytes"]) == integer(operation["recipient_count"])
                            * integer(operation["source_read_useful_bytes"]), "recipient byte conservation failed")
                    require(integer(operation["source_read_service_bytes"]) >= integer(operation["source_read_useful_bytes"]),
                            "source service does not cover useful bytes")
                    require(integer(operation["destination_write_service_bytes"]) >= integer(operation["destination_write_useful_bytes"]),
                            "destination service does not cover useful bytes")
        if check == "drain":
            require(raw.get("status") == "complete", "child did not drain")
            _audit_trees(raw, workload, memory, graph)
            _audit_scalar(raw, workload, memory)
        raise MulticastAuditUnavailable("canonical memory/credit owners and shared service records are unavailable")
    if check == "bounded_execution":
        horizon = number(memory["max_aci_cycles"])
        elapsed = number(raw["elapsed_aci_cycles"])
        require(math.isfinite(elapsed) and 0 <= elapsed <= horizon, "multicast result exceeded input horizon")
        return
    raise MulticastAuditUnavailable(f"multicast adapter does not expose {check}")
