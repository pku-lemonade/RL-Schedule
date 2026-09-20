"""Independent input/event oracles for the shared multicast runtime.

No production compiler, packetizer, service/cost calculator or runtime is used.
Expected paths, packet counts, work and costs are reconstructed from input.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from itertools import groupby, pairwise

from .data import Data, array, integer, key, number, obj, require, rows, text
from .multicast import MulticastAuditUnavailable, expected_tree


def ident(*parts: object) -> str:
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


def operations(config: Data) -> list[Data]:
    result = list(rows(config.get("operations", [])))
    compute = config.get("compute")
    if not isinstance(compute, dict):
        return result
    workers = {text(w["tile_id"]): w for w in rows(compute["workers"])}
    for stream in rows(compute["streams"]):
        worker = workers[text(stream["worker_tile_id"])]
        slots = rows(stream["slots"])
        for index, job in enumerate(rows(stream["jobs"])):
            job_id = text(job["job_id"])
            slot = slots[index % len(slots)]
            for side in ("a", "b"):
                result.append(
                    {
                        "operation_id": ident("compute", job_id, side),
                        "kind": "local_read",
                        "initiator_id": array(worker["endpoint_ids"])[0],
                        "source": obj(job[side])["access"],
                    }
                )
            output = obj(job["output"])
            c = {
                **obj(slot["c"]),
                "size_bytes": obj(output["destination"])["size_bytes"],
            }
            rid = ident("compute", job_id, "result")
            result.append(
                {
                    "operation_id": rid,
                    "kind": "local_write",
                    "destination": c,
                    "initiator_id": array(worker["endpoint_ids"])[0],
                }
            )
            if output["mode"] != "local":
                ep = next(
                    e
                    for e in rows(obj(config["memory"])["endpoints"])
                    if e["fabric_id"] == output["fabric_id"]
                    and e["endpoint_id"] in array(worker["endpoint_ids"])
                )
                result.append(
                    {
                        "operation_id": ident("compute", job_id, "writer"),
                        "kind": output["mode"],
                        "source": c,
                        "destination": output["destination"],
                        "fabric_id": output["fabric_id"],
                        "initiator_id": ep["endpoint_id"],
                    }
                )
    return result


def path(
    memory: Data, graph: Data, fabric: int, source: str, destination: str
) -> list[tuple[int, str, str]]:
    eps = {text(e["endpoint_id"]): e for e in rows(memory["endpoints"])}
    routers = {
        text(r["router_id"]): r
        for r in rows(graph["routers"])
        if r["fabric_id"] == fabric
    }
    coords = {text(r["router_id"]): obj(r["coordinate"]) for r in routers.values()}
    links = rows(graph["links"])
    binding = next(b for b in rows(memory["routing"]) if b["fabric_id"] == fabric)
    order = (
        ("x", "y") if binding["routing_policy"] == "dimension_order_xy" else ("y", "x")
    )
    current, target = (
        text(eps[source]["router_id"]),
        text(eps[destination]["router_id"]),
    )
    route = [(fabric, "inject", source)]
    for axis in order:
        for _ in range(len(routers) + 1):
            if coords[current][axis] == coords[target][axis]:
                break
            matches = [
                link
                for link in links
                if link["fabric_id"] == fabric
                and link["src_router"] == current
                and text(link["src_port"]).startswith(axis)
                and link["enabled"] is True
            ]
            require(len(matches) == 1, "independent route has no unique directed step")
            route.append((fabric, "network", text(matches[0]["link_id"])))
            current = text(matches[0]["dst_router"])
        else:
            raise ValueError("independent route failed to terminate")
    require(current == target, "independent route misses target")
    return [*route, (fabric, "eject", destination)]


def packets(
    config: Data, graph: Data
) -> tuple[
    Counter[tuple[str, int, str, str, int]], dict[str, tuple[str, int, str, str]]
]:
    memory = obj(config["memory"])
    geometry = obj(memory["packet"])
    headers, data, segment = (
        integer(geometry[k])
        for k in ("header_flits", "data_capacity_bytes", "max_segment_payload_bytes")
    )
    expected: Counter[tuple[str, int, str, str, int]] = Counter()
    controls: dict[str, tuple[str, int, str, str]] = {}
    buffers = {text(b["buffer_id"]): b for b in rows(memory["buffers"])}

    def target(buffer: str, fabric: int) -> str:
        matches = [
            e
            for e in rows(memory["endpoints"])
            if e["fabric_id"] == fabric
            and buffers[buffer]["resource_id"] in array(e["resource_ids"])
            and "target" in array(e["roles"])
        ]
        require(len(matches) == 1, "ambiguous destination interface")
        return text(matches[0]["endpoint_id"])

    def add(packet: Data, count: int, channels: list[tuple[int, str, str]]) -> None:
        for fabric, kind, channel in channels:
            expected.update(
                (key(packet), fabric, kind, channel, f) for f in range(count)
            )

    def control(
        op: str,
        index: int,
        purpose: str,
        fabric: int,
        source: str,
        dest: str,
        count: int,
    ) -> None:
        pid = ident(op, index, purpose, source, dest)
        controls[pid] = op, index, purpose, source
        add(
            {
                "transfer_id": pid,
                "traffic_class": "request"
                if purpose == "atomic_request"
                else "response",
            },
            count,
            path(memory, graph, fabric, source, dest),
        )

    for write in rows(config.get("writes", [])):
        edges, recipients = expected_tree(write, graph)
        require(
            recipients == {text(d["endpoint_id"]) for d in rows(write["destinations"])},
            "false source inclusion or recipient bindings",
        )
        op, fabric = text(write["operation_id"]), integer(write["fabric_id"])
        for index, offset in enumerate(range(0, integer(write["size_bytes"]), segment)):
            size = min(segment, integer(write["size_bytes"]) - offset)
            channels = [
                (fabric, "inject", text(write["source_endpoint_id"])),
                *((fabric, "network", e) for e in edges),
                *((fabric, "eject", e) for e in recipients),
            ]
            add(
                {
                    "operation_id": op,
                    "segment_index": index,
                    "traffic_class": "multicast",
                },
                headers + (size + data - 1) // data,
                channels,
            )
            if write["completion"] == "write_acknowledged":
                for recipient in recipients:
                    control(
                        op,
                        index,
                        "multicast_ack",
                        fabric,
                        recipient,
                        text(write["source_endpoint_id"]),
                        headers,
                    )
    for operation in operations(config):
        kind = text(operation["kind"])
        if kind not in {"read", "write_posted", "write_acknowledged"}:
            continue
        op, fabric, source = (
            text(operation["operation_id"]),
            integer(operation["fabric_id"]),
            text(operation["initiator_id"]),
        )
        dest = target(
            text(
                obj(operation["source" if kind == "read" else "destination"])[
                    "buffer_id"
                ]
            ),
            fabric,
        )
        total = integer(obj(operation["source"])["size_bytes"])
        for index, offset in enumerate(range(0, total, segment)):
            count = headers + (min(segment, total - offset) + data - 1) // data
            add(
                {
                    "transfer_id": ident(
                        op, index, "read_request" if kind == "read" else "write_request"
                    ),
                    "traffic_class": "request",
                },
                headers if kind == "read" else count,
                path(memory, graph, fabric, source, dest),
            )
            if kind != "write_posted":
                add(
                    {
                        "transfer_id": ident(
                            op,
                            index,
                            "read_response" if kind == "read" else "write_ack",
                        ),
                        "traffic_class": "response",
                    },
                    count if kind == "read" else headers,
                    path(memory, graph, fabric, dest, source),
                )
    counters = {text(c["counter_id"]): c for c in rows(config.get("counters", []))}
    for increment in rows(config.get("increments", [])):
        op, fabric, source = (
            text(increment["operation_id"]),
            integer(increment["fabric_id"]),
            text(increment["source_endpoint_id"]),
        )
        dest = target(
            text(counters[text(increment["counter_id"])]["buffer_id"]), fabric
        )
        control(op, 0, "atomic_request", fabric, source, dest, 1)
        if increment["completion"] == "atomic_returning":
            control(op, 0, "atomic_return", fabric, dest, source, 1)
    return expected, controls


def transport(raw: Data, config: Data, graph: Data) -> None:
    expected, _ = packets(config, graph)
    tree = obj(raw["tree_transport"])
    width = integer(obj(obj(config["memory"])["packet"])["physical_flit_bytes"])
    launches: Counter[tuple[str, int, str, str, int]] = Counter()
    previous: dict[str, float] = {}
    settings = obj(obj(config["runtime"])["transport"])
    fabrics = {integer(f["fabric_id"]): f for f in rows(settings["fabrics"])}
    for event in rows(tree["trace"]):
        if event["action"] != "link_launch":
            continue
        channel = obj(obj(event["lane"])["channel"])
        channel_id = key(channel)
        stamp = number(event["time_aci_cycles"])
        fabric = integer(channel["fabric_id"])
        link = next(
            (
                obj(o["settings"])
                for o in rows(settings.get("overrides", []))
                if o["channel"] == channel
            ),
            obj(
                fabrics[fabric][
                    "network_link" if channel["kind"] == "network" else "local_link"
                ]
            ),
        )
        factor = max(
            [
                1.0,
                *[
                    number(array(s)[3])
                    for s in array(link.get("slowdowns", []))
                    if number(array(s)[1]) <= stamp < number(array(s)[2])
                ],
            ]
        )
        ratio = number(link["aci_clock_hz"]) / number(link["noc_clock_hz"])
        duration = (
            math.ceil(width * 8 / integer(link["payload_bits_per_noc_cycle"]))
            * ratio
            * factor
        )
        require(
            math.isclose(number(event["duration_aci_cycles"]), duration),
            "physical serializer cost differs from input",
        )
        require(
            stamp >= previous.get(channel_id, 0),
            "overlapping physical serializer grants",
        )
        previous[channel_id] = (
            stamp + number(link["launch_interval_noc_cycles"]) * ratio * factor
        )
        require(
            integer(event["physical_bytes"]) == width,
            "physical launch width differs from input",
        )
        launches[
            key(event["packet"]),
            fabric,
            text(channel["kind"]),
            text(channel["identity"]),
            integer(event["flit_index"]),
        ] += 1
    require(not launches - expected, "duplicate or undeclared physical/tree flit")
    require(
        sum(launches.values()) * width
        == integer(raw["physical_channel_bytes"])
        == integer(tree["physical_channel_bytes"]),
        "uncharged physical channel traffic",
    )
    if raw["status"] == "complete":
        require(
            launches == expected, "missing shared prefix, control or recipient flits"
        )
    deliveries: Counter[tuple[str, str]] = Counter()
    for delivery in rows(tree["deliveries"]):
        packet = obj(delivery["packet"])
        endpoint = text(delivery["endpoint_id"])
        write = next(
            w
            for w in rows(config["writes"])
            if w["operation_id"] == packet["operation_id"]
        )
        fabric = integer(write["fabric_id"])
        index = integer(packet["segment_index"])
        geometry = obj(obj(config["memory"])["packet"])
        segment = integer(geometry["max_segment_payload_bytes"])
        size = min(segment, integer(write["size_bytes"]) - index * segment)
        count = sum(
            v
            for k, v in expected.items()
            if k[:4] == (key(packet), fabric, "eject", endpoint)
        )
        require(
            count > 0
            and integer(delivery["received_flits"]) <= count
            and integer(delivery["useful_bytes"]) <= size,
            "false or duplicate destination delivery",
        )
        deliveries[key(packet), endpoint] += 1
        require(deliveries[key(packet), endpoint] == 1, "duplicate recipient record")
        if raw["status"] == "complete":
            require(
                delivery["complete_aci_cycles"] is not None
                and delivery["received_flits"] == count
                and delivery["useful_bytes"] == size,
                "missing recipient effect",
            )
    expected_recipients = {
        (p, ch)
        for (p, _, kind, ch, _) in expected
        if kind == "eject" and obj(json.loads(p)).get("traffic_class") == "multicast"
    }
    require(set(deliveries) == expected_recipients, "missing or false source recipient")


def memory(raw: Data, config: Data) -> None:
    mem = obj(config["memory"])
    buffers = {text(b["buffer_id"]): b for b in rows(mem["buffers"])}
    services = {
        text(r["resource_id"]): obj(r["service"]) for r in rows(mem["resources"])
    }
    expected: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)

    def add(op: str, access: Data, direction: str) -> None:
        buffer = buffers[text(access["buffer_id"])]
        start = integer(buffer["base_address"]) + integer(access["offset_bytes"])
        expected[op, text(buffer["resource_id"]), direction].append(
            (start, start + integer(access["size_bytes"]))
        )

    for write in rows(config.get("writes", [])):
        op = text(write["operation_id"])
        add(op, obj(write["source"]), "read")
        for d in rows(write["destinations"]):
            add(op, {**d, "size_bytes": write["size_bytes"]}, "write")
    for operation in operations(config):
        for side, direction in (("source", "read"), ("destination", "write")):
            if operation.get(side) is not None:
                add(text(operation["operation_id"]), obj(operation[side]), direction)
    for i in rows(config.get("increments", [])):
        if i.get("return_inbox") is not None:
            add(text(i["operation_id"]), obj(i["return_inbox"]), "write")
    observed: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)
    ids: set[tuple[str, str]] = set()
    for chunk in rows(raw["chunks"]):
        owner = text(chunk["resource_id"])
        service = services[owner]
        address = integer(chunk["address"])
        size = integer(chunk["useful_bytes"])
        identity = owner, text(chunk["service_id"])
        require(identity not in ids, "duplicate memory service")
        ids.add(identity)
        g = integer(service["service_granule_bytes"])
        charged = ((address % g + size + g - 1) // g) * g
        native = number(service["fixed_latency_cycles"]) + charged / number(
            service["bytes_per_cycle"]
        )
        duration = (
            native * number(mem["aci_clock_hz"]) / number(service["native_clock_hz"])
        )
        require(
            integer(chunk["serviced_bytes"]) == charged
            and size <= integer(service["chunk_bytes"]),
            "wrong memory granule charge",
        )
        require(
            math.isclose(number(chunk["service_aci_cycles"]), duration)
            and math.isclose(
                number(chunk["end_aci_cycles"]) - number(chunk["start_aci_cycles"]),
                duration,
            ),
            "wrong shared memory duration",
        )
        k = text(chunk["client_id"]), owner, text(chunk["direction"])
        span = address, address + size
        require(
            any(lo <= span[0] < span[1] <= hi for lo, hi in expected[k]),
            "memory chunk outside declared range",
        )
        observed[k].append(span)
    for k, spans in observed.items():
        for first, second in pairwise(sorted(spans)):
            require(first[1] <= second[0], "duplicate memory payload bytes")
    if raw["status"] == "complete":
        for k, spans in expected.items():
            require(
                sum(b - a for a, b in observed[k]) == sum(b - a for a, b in spans),
                "missing canonical memory service",
            )
    for publication in rows(raw["ownership_trace"]):
        if publication["action"] != "publish":
            continue
        producer = array(json.loads(text(obj(publication["version"])["producer_id"])))
        require(
            len(producer) == 2 and producer[1] == publication["resource_id"],
            "publication version has wrong physical owner",
        )
        covered = sorted(
            (integer(c["address"]), integer(c["address"]) + integer(c["useful_bytes"]))
            for c in rows(raw["chunks"])
            if c["client_id"] == producer[0]
            and c["resource_id"] == producer[1]
            and c["direction"] == "write"
            and number(c["end_aci_cycles"]) <= number(publication["time_aci_cycles"])
        )
        cursor = integer(publication["address"])
        for lo, hi in covered:
            if lo <= cursor:
                cursor = max(cursor, hi)
        require(
            cursor
            >= integer(publication["address"]) + integer(publication["size_bytes"]),
            "publication precedes actual write service",
        )
    require(
        sum(
            integer(c["useful_bytes"])
            for c in rows(raw["chunks"])
            if c["direction"] == "read"
        )
        == integer(raw["source_useful_bytes"]),
        "source byte conservation",
    )
    require(
        sum(
            integer(c["useful_bytes"])
            for c in rows(raw["chunks"])
            if c["direction"] == "write"
        )
        == integer(raw["destination_useful_bytes"]),
        "destination byte conservation",
    )
    for owner, service in services.items():
        records = sorted(
            [
                c
                for c in (*rows(raw["chunks"]), *rows(raw["scalar_service"]))
                if c["resource_id"] == owner
            ],
            key=lambda c: number(c["start_aci_cycles"]),
        )
        for first, second in pairwise(records):
            require(
                number(first["end_aci_cycles"]) <= number(second["start_aci_cycles"]),
                "aggregate L1 server jobs overlap",
            )
        for e in rows(raw["service_trace"]):
            if e["resource_id"] == owner:
                require(
                    integer(e["active"]) <= 1
                    and integer(e["queued"]) <= integer(service["queue_capacity"]),
                    "unbounded memory FIFO",
                )


def scalar(raw: Data, config: Data) -> None:
    mem = obj(config["memory"])
    control = obj(config["control"])
    buffers = {text(b["buffer_id"]): b for b in rows(mem["buffers"])}
    services = {
        text(r["resource_id"]): obj(r["service"]) for r in rows(mem["resources"])
    }
    counters = {text(c["counter_id"]): c for c in rows(config.get("counters", []))}
    increments = {
        text(i["operation_id"]): i for i in rows(config.get("increments", []))
    }
    values = {k: integer(c["initial_value"]) for k, c in counters.items()}
    effects: dict[str, Data] = {}
    sequences: dict[str, int] = defaultdict(int)
    for record in sorted(
        rows(raw["scalar_service"]),
        key=lambda r: (
            number(r["end_aci_cycles"]),
            text(r["resource_id"]),
            integer(r["admission_sequence"]),
        ),
    ):
        counter = counters[text(record["counter_id"])]
        buffer = buffers[text(counter["buffer_id"])]
        owner = text(buffer["resource_id"])
        service = services[owner]
        address = integer(buffer["base_address"]) + integer(counter["offset_bytes"])
        width = integer(counter["width_bytes"])
        require(
            record["resource_id"] == owner
            and record["address"] == address
            and record["width_bytes"] == width,
            "scalar effect at wrong address/owner",
        )
        require(
            integer(record["old_value"]) == values[text(counter["counter_id"])],
            "scalar observation or old value disagrees with linearized state",
        )
        if record["direction"] == "atomic":
            op = text(record["client_id"])
            require(op in increments and op not in effects, "duplicate scalar update")
            require(
                increments[op]["counter_id"] == counter["counter_id"],
                "wrong counter update",
            )
            values[text(counter["counter_id"])] += 1
            sequences[owner] += 1
            require(
                record["linearization_sequence"] == sequences[owner],
                "invalid scalar linearization sequence",
            )
            charged = integer(control["atomic_granule_bytes"])
            native = number(control["atomic_native_cycles"])
            require(
                record["write_service_bytes"] == charged,
                "atomic must charge addressed write granule",
            )
            effects[op] = record
        else:
            g = integer(service["service_granule_bytes"])
            charged = ((address % g + width + g - 1) // g) * g
            native = (
                number(service["fixed_latency_cycles"])
                + charged / number(service["bytes_per_cycle"])
                + number(control["local_observation_aci_cycles"])
                * number(service["native_clock_hz"])
                / number(mem["aci_clock_hz"])
            )
            require(record["write_service_bytes"] == 0, "observation cannot write")
        duration = (
            native * number(mem["aci_clock_hz"]) / number(service["native_clock_hz"])
        )
        require(
            record["new_value"] == values[text(counter["counter_id"])]
            and record["read_service_bytes"] == charged,
            "invalid scalar transition/granule",
        )
        require(
            math.isclose(
                number(record["end_aci_cycles"]) - number(record["start_aci_cycles"]),
                duration,
            ),
            "wrong indivisible scalar service cost",
        )
    require(
        {
            text(obj(state["definition"])["counter_id"])
            for state in rows(raw["counters"])
        }
        == set(counters)
        and len(rows(raw["counters"])) == len(counters),
        "missing or duplicate counter state",
    )
    for record in rows(raw["scalar_service"]):
        admissions = [
            e
            for e in rows(raw["service_trace"])
            if e["resource_id"] == record["resource_id"] and e["action"] == "admit"
        ]
        require(
            integer(record["admission_sequence"]) < len(admissions)
            and admissions[integer(record["admission_sequence"])]["service_id"]
            == record["service_id"],
            "scalar admission sequence differs from shared FIFO",
        )
    inboxes = [text(array(pair)[0]) for pair in array(raw["inbox_values"])]
    require(len(set(inboxes)) == len(inboxes), "duplicate return inbox effect")
    if raw["status"] == "complete":
        require(
            set(inboxes)
            == {
                op
                for op, i in increments.items()
                if i["completion"] == "atomic_returning"
            },
            "missing previous value return",
        )
    for state in rows(raw["counters"]):
        cid = text(obj(state["definition"])["counter_id"])
        require(
            state["value"] == values[cid], "counter state differs from linearization"
        )
    for pair in array(raw["inbox_values"]):
        op, old = array(pair)
        require(
            text(op) in effects and effects[text(op)]["old_value"] == old,
            "wrong previous value returned",
        )
        complete = [
            e
            for e in rows(raw["lifecycle"])
            if e["operation_id"] == op and e["action"] == "acknowledgement"
        ]
        require(
            bool(complete)
            and all(
                number(e["time_aci_cycles"])
                >= number(effects[text(op)]["end_aci_cycles"])
                for e in complete
            ),
            "early atomic return",
        )
    if raw["status"] == "complete":
        require(set(effects) == set(increments), "missing scalar update")
    waits = {text(w["wait_id"]): w for w in rows(config.get("waits", []))}
    released: set[str] = set()
    for event in rows(raw["lifecycle"]):
        if event["action"] != "wait_release":
            continue
        op = text(event["operation_id"])
        require(op not in released, "duplicate wait release")
        released.add(op)
        wait = waits[op]
        observations = [
            r
            for r in rows(raw["scalar_service"])
            if r["client_id"] == op
            and r["direction"] == "observe"
            and number(r["end_aci_cycles"]) <= number(event["time_aci_cycles"])
        ]
        require(
            bool(observations)
            and integer(observations[-1]["new_value"]) >= integer(wait["threshold"]),
            "early wait release before charged observation",
        )
        for prereq in rows(wait.get("local_data", [])):
            ready_at(raw, mem, prereq, number(event["time_aci_cycles"]))
    if raw["status"] == "complete":
        require(released == set(waits), "complete run omits local wait")


def ready_at(raw: Data, mem: Data, prerequisite: Data, stamp: float) -> None:
    access, version = obj(prerequisite["access"]), obj(prerequisite["version"])
    buffer = next(
        b for b in rows(mem["buffers"]) if b["buffer_id"] == access["buffer_id"]
    )
    lo = integer(buffer["base_address"]) + integer(access["offset_bytes"])
    hi = lo + integer(access["size_bytes"])
    ready: list[tuple[int, int, Data]] = (
        [
            (
                integer(buffer["base_address"]),
                integer(buffer["base_address"]) + integer(buffer["size_bytes"]),
                {"kind": "initial", "producer_id": None},
            )
        ]
        if buffer.get("initially_ready", False)
        else []
    )
    for event in rows(raw["ownership_trace"]):
        if (
            event["buffer_id"] != access["buffer_id"]
            or number(event["time_aci_cycles"]) > stamp
        ):
            continue
        start = integer(event["address"])
        end = start + integer(event["size_bytes"])
        if event["action"] == "invalidate":
            following: list[tuple[int, int, Data]] = []
            for a, b, v in ready:
                if a < start:
                    following.append((a, min(b, start), v))
                if end < b:
                    following.append((max(a, end), b, v))
            ready = following
        if event["action"] == "publish":
            ready.append((start, end, obj(event["version"])))
    expected = (
        {"kind": "initial", "producer_id": None}
        if version["kind"] == "initial"
        else {
            "kind": "producer",
            "producer_id": ident(version["producer_id"], buffer["resource_id"]),
        }
    )
    cursor = lo
    for a, b, v in sorted(ready, key=lambda r: r[0]):
        if b <= cursor:
            continue
        if a > cursor or v != expected:
            break
        cursor = min(hi, b)
        if cursor == hi:
            return
    raise ValueError(
        "stale generation or early release before full local data publication"
    )


def ownership(raw: Data, config: Data) -> None:
    complete = raw["status"] == "complete"
    tree = obj(raw["tree_transport"])
    for resource in rows(tree["resources"]):
        require(
            integer(resource["available"])
            + integer(resource["occupied"])
            + integer(resource["pending_returns"])
            == integer(resource["capacity"]),
            "physical resource capacity loss",
        )
        require(
            0
            <= integer(resource["occupied"])
            <= integer(resource["peak_occupied"])
            <= integer(resource["capacity"]),
            "physical capacity exceeded",
        )
        if complete:
            require(
                not resource["owners"]
                and not resource["occupied"]
                and not resource["pending_returns"],
                "leaked physical/tree reservation",
            )
    capacities = {
        key(r["lane"]): integer(r["capacity"])
        for r in rows(tree["resources"])
        if r.get("lane") is not None
    }
    tokens: dict[str, tuple[str, bool]] = {}
    lane_occupied: Counter[str] = Counter()
    lane_returning: Counter[str] = Counter()
    phases = {"credit_reserve": 0, "credit_release": 1, "credit_return": 2}
    events = [e for e in rows(tree["trace"]) if e["action"] in phases]
    events.sort(key=lambda e: (number(e["time_aci_cycles"]), phases[text(e["action"])]))
    # Exported same-time events are sorted by fields, not scheduler order.
    # Replay token transitions, then enforce bounds at each distinct timestamp.
    for _, batch in groupby(events, key=lambda e: number(e["time_aci_cycles"])):
        for event in batch:
            lane, token = key(event["lane"]), text(event["token_id"])
            require(lane in capacities, "credit on undeclared physical lane")
            if event["action"] == "credit_reserve":
                require(token not in tokens, "duplicate credit token")
                tokens[token] = (lane, False)
                lane_occupied[lane] += 1
            elif event["action"] == "credit_release":
                require(
                    tokens.get(token) == (lane, False), "credit release without owner"
                )
                tokens[token] = (lane, True)
                lane_occupied[lane] -= 1
                lane_returning[lane] += 1
            else:
                require(
                    tokens.get(token) == (lane, True), "credit return without release"
                )
                del tokens[token]
                lane_returning[lane] -= 1
        require(
            all(
                0 <= lane_occupied[lane] + lane_returning[lane] <= capacity
                for lane, capacity in capacities.items()
            ),
            "event trace exceeds lane capacity",
        )
    for state in rows(tree["resources"]):
        if state.get("lane") is not None:
            lane = key(state["lane"])
            require(
                state["occupied"] == lane_occupied[lane]
                and state["pending_returns"] == lane_returning[lane],
                "credit snapshot conceals live owner",
            )
    if complete:
        require(not tokens, "unreturned physical credits")
    grants: set[str] = set()
    queued: set[str] = set()
    last_credit: dict[str, float] = {}
    for event in rows(tree["trace"]):
        if event["action"] == "credit_return":
            last_credit[key(event["packet"])] = max(
                last_credit.get(key(event["packet"]), 0),
                number(event["time_aci_cycles"]),
            )
    for event in rows(tree["events"]):
        packet = key(event["packet"])
        action = event["action"]
        if action == "reservation_queue":
            require(packet not in queued, "duplicate reservation queue")
            queued.add(packet)
        if action == "reservation_acquire":
            require(packet in queued and packet not in grants, "invalid tree grant")
            grants.add(packet)
        if action == "reservation_release":
            require(packet in grants, "tree release without grant")
            grants.remove(packet)
            queued.remove(packet)
            require(
                number(event["time_aci_cycles"]) >= last_credit.get(packet, 0),
                "tree grant released before delayed credits",
            )
    if complete:
        require(not grants and not queued, "leaked tree reservation despite summary")
    descriptors: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in rows(raw["descriptor_trace"]):
        owners = descriptors[text(event["resource_id"]), text(event["kind"])]
        who = text(event["owner"])
        if event["action"] == "acquire":
            require(who not in owners, "duplicate descriptor")
            owners.add(who)
        else:
            require(who in owners, "descriptor release without ownership")
            owners.remove(who)
        require(len(owners) == integer(event["occupied"]), "descriptor ownership loss")
    for state in rows(raw["descriptors"]):
        require(
            set(array(state["owners"]))
            == descriptors[text(state["resource_id"]), text(state["kind"])],
            "descriptor snapshot differs from events",
        )
        require(
            integer(state["occupied"])
            == len(array(state["owners"]))
            <= integer(state["peak_occupied"])
            <= integer(state["capacity"]),
            "descriptor capacity loss",
        )
        if complete:
            require(not state["owners"], "leaked descriptor")
    reservations: set[tuple[str, str]] = set()
    accesses: set[tuple[str, int]] = set()
    for event in rows(raw["ownership_trace"]):
        b = text(event["resource_id"]), text(event["buffer_id"])
        a = (
            text(event["resource_id"]),
            integer(event["access_id"]) if event.get("access_id") is not None else -1,
        )
        if event["action"] == "reserve":
            require(b not in reservations, "duplicate byte reservation")
            reservations.add(b)
        elif event["action"] == "release":
            require(b in reservations, "double byte teardown")
            reservations.remove(b)
        elif event["action"] in {"read_acquire", "write_acquire"}:
            require(b in reservations and a not in accesses, "invalid memory lease")
            accesses.add(a)
        elif event["action"] == "access_release":
            require(a in accesses, "unowned memory lease release")
            accesses.remove(a)
    if complete:
        require(
            not reservations and not accesses,
            "leaked memory lease or missing teardown event",
        )
    resource_ids = {
        text(r["resource_id"]) for r in rows(obj(config["memory"])["resources"])
    }
    require(
        {text(r["resource_id"]) for r in rows(raw["memory_resources"])} == resource_ids,
        "missing canonical memory resource",
    )
    if complete:
        require(
            {text(r["resource_id"]) for r in rows(raw["released_resources"])}
            == resource_ids,
            "missing released memory resource",
        )
    for resource in rows(raw["memory_resources"]):
        require(
            integer(resource["available_bytes"]) + integer(resource["reserved_bytes"])
            == integer(resource["capacity_bytes"]),
            "memory reservation capacity loss",
        )
        require(
            integer(resource["reserved_bytes"])
            == sum(
                integer(obj(b["buffer"])["size_bytes"])
                for b in rows(resource["buffers"])
            ),
            "memory full reservation loss",
        )
    if complete:
        require(
            raw["teardown_complete"] is True and not raw["pending_operations"],
            "premature finalization",
        )
        require(
            all(
                not r["reserved_bytes"]
                and not obj(r["service"])["active"]
                and not obj(r["service"])["queued"]
                for r in rows(raw["released_resources"])
            ),
            "memory ownership leaked at teardown",
        )
    compute = raw.get("compute")
    if isinstance(compute, dict):
        definition = obj(config["compute"])
        workers = {text(w["tile_id"]): w for w in rows(definition["workers"])}
        engines: dict[tuple[str, str, int], str] = {}
        for event in rows(compute["resource_events"]):
            worker, kind = text(event["worker_tile_id"]), text(event["kind"])
            capacity = integer(
                workers[worker][
                    {
                        "reader": "reader_capacity",
                        "compute": "compute_contexts",
                        "writer": "writer_capacity",
                    }[kind]
                ]
            )
            engine = worker, kind, integer(event["engine_index"])
            require(
                event["capacity"] == capacity and engine[2] < capacity,
                "compute capacity differs from hardware input",
            )
            if event["action"] == "acquire":
                require(engine not in engines, "two jobs share one engine")
                engines[engine] = text(event["job_id"])
            else:
                require(
                    engines.get(engine) == event["job_id"],
                    "unowned compute engine release",
                )
                del engines[engine]
        if complete:
            require(not engines, "compute ownership leak despite summary")
        for resource in rows(compute["resources"]):
            require(
                integer(resource["occupied"])
                == len(array(resource["owners"]))
                <= integer(resource["peak_occupied"])
                <= integer(resource["capacity"]),
                "compute capacity loss",
            )
            if complete:
                require(not resource["occupied"], "leaked compute context")
        for slot in rows(compute["slots"]):
            require(
                integer(slot["free"]) + integer(slot["occupied"])
                == integer(slot["capacity"]),
                "slot capacity loss",
            )
            if complete:
                require(not slot["occupied"], "leaked slot generation")


def causality(raw: Data, config: Data, graph: Data) -> None:
    lifecycle = rows(raw["lifecycle"])
    mem = obj(config["memory"])
    _, controls = packets(config, graph)

    def completion(op: str) -> float:
        values = [
            number(e["time_aci_cycles"])
            for e in lifecycle
            if e["operation_id"] == op
            and e["segment_index"] is None
            and e["action"] in {"complete", "wait_release"}
        ]
        require(len(values) == 1, "missing/duplicate completion fact")
        return values[0]

    declarations = (
        *rows(config.get("writes", [])),
        *rows(config.get("increments", [])),
        *rows(config.get("operations", [])),
    )
    for operation in declarations:
        starts = [
            number(e["time_aci_cycles"])
            for e in lifecycle
            if e["operation_id"] == operation["operation_id"]
            and e["action"] == "acceptance"
        ]
        if not starts:
            continue
        for parent in array(operation.get("depends_on", [])):
            require(
                min(starts) >= completion(text(parent)),
                "operation accepted before dependency",
            )
    for gate in rows(config.get("gates", [])):
        starts = [
            number(e["time_aci_cycles"])
            for e in lifecycle
            if e["operation_id"] == gate["operation_id"] and e["action"] == "acceptance"
        ]
        if not starts:
            continue
        for wait in array(gate.get("after_waits", [])):
            require(min(starts) >= completion(text(wait)), "early phase release")
        for p in rows(gate.get("local_data", [])):
            ready_at(raw, mem, p, min(starts))
    eps = {text(e["endpoint_id"]): e for e in rows(mem["endpoints"])}
    for event in rows(obj(raw["tree_transport"])["trace"]):
        packet = obj(event["packet"]) if event.get("packet") is not None else {}
        if (
            event["action"] != "link_launch"
            or obj(obj(event["lane"])["channel"])["kind"] != "inject"
            or packet.get("traffic_class") != "response"
        ):
            continue
        pid = text(packet["transfer_id"])
        if pid not in controls:
            continue
        op, segment, purpose, source = controls[pid]
        if purpose == "atomic_return":
            effects = [
                number(s["end_aci_cycles"])
                for s in rows(raw["scalar_service"])
                if s["client_id"] == op and s["direction"] == "atomic"
            ]
        else:
            effects = [
                number(e["time_aci_cycles"])
                for e in lifecycle
                if e["operation_id"] == op
                and e["segment_index"] == segment
                and e["action"] == "recipient_effect"
                and e["resource_id"] in array(eps[source]["resource_ids"])
            ]
        require(
            bool(effects) and number(event["time_aci_cycles"]) >= max(effects),
            "early return before target service",
        )


def compute(raw: Data, config: Data) -> None:
    definition = config.get("compute")
    runtime = raw.get("compute")
    if not isinstance(definition, dict):
        require(runtime is None, "undeclared compute capacity")
        return
    require(isinstance(runtime, dict), "missing compute observations")
    runtime = obj(runtime)
    workers = {text(w["tile_id"]): w for w in rows(definition["workers"])}
    dtypes = {
        text(d["dtype_id"]): integer(d["bytes_per_element"])
        for d in rows(definition["dtypes"])
    }
    for stream in rows(definition["streams"]):
        slots = rows(stream["slots"])
        worker = workers[text(stream["worker_tile_id"])]
        for index, job in enumerate(rows(stream["jobs"])):
            events = [
                e for e in rows(runtime["stages"]) if e["job_id"] == job["job_id"]
            ]
            require(
                all(
                    e["generation"] == index // len(slots)
                    and e["slot_id"] == slots[index % len(slots)]["slot_id"]
                    for e in events
                ),
                "stale compute generation",
            )
            times = {text(e["action"]): number(e["time_aci_cycles"]) for e in events}
            require(len(times) == len(events), "duplicate compute stage")
            sequence = (
                "slot_wait",
                "reader_start",
                "inputs_ready",
                "compute_wait",
                "operand_start",
                "operand_end",
                "math_start",
                "math_end",
                "result_start",
                "output_ready",
                "writer_wait",
                "writer_start",
                "writer_complete",
                "slot_release",
            )
            observed = [times[a] for a in sequence if a in times]
            require(
                observed == sorted(observed), "compute stages are not causally ordered"
            )
            if raw["status"] == "complete":
                require(set(times) == set(sequence), "missing compute stage")
            if "inputs_ready" in times:
                for side in ("a", "b"):
                    ready_at(
                        raw,
                        obj(config["memory"]),
                        obj(job[side]),
                        times["inputs_ready"],
                    )
            if "math_end" not in times:
                continue
            operation = obj(job["operation"])
            a = obj(operation["a"])
            c = obj(operation["c"])
            batch, m, k = map(integer, array(a["shape"]))
            n = integer(array(c["shape"])[-1])
            rates = [
                r
                for r in rows(definition["rates"])
                if r["rate_id"] in array(worker["rate_ids"])
                and obj(r["key"])["operation"] == operation["kind"]
                and all(
                    obj(r["key"])[s + "_dtype"] == obj(operation[s])["dtype"]
                    for s in ("a", "b", "c")
                )
                and obj(r["key"])["fidelity"] == operation["fidelity"]
                and obj(r["key"])["layout"] == a["layout"]
                and obj(r["key"])["accumulator_precision"]
                == operation["accumulator_precision"]
            ]
            require(len(rates) == 1, "independent compute rate match failed")
            rate = rates[0]
            block = obj(rate["block"])
            executed = (
                2
                * batch
                * math.prod(
                    ((v + integer(block[s]) - 1) // integer(block[s]))
                    * integer(block[s])
                    for v, s in ((m, "m"), (n, "n"), (k, "k"))
                )
            )
            quantum = Fraction(str(rate["quantum_native_cycles"]))
            work_rate = Fraction(str(rate["work_per_native_cycle"]))
            native = Fraction(str(rate["setup_native_cycles"])) + max(
                Fraction(str(rate["minimum_native_cycles"])),
                math.ceil(Fraction(executed) / (work_rate * quantum)) * quantum,
            )
            expected = float(
                native
                * Fraction(str(obj(config["memory"])["aci_clock_hz"]))
                / Fraction(str(worker["native_clock_hz"]))
            )
            require(
                math.isclose(times["math_end"] - times["math_start"], expected),
                "compute cost differs from independent padded work",
            )
            for side in ("a", "b", "c"):
                tensor = obj(operation[side])
                shape = list(map(integer, array(tensor["shape"])))
                layout = obj(tensor["layout"])
                if layout["kind"] == "tiled":
                    for pos, field in ((-2, "tile_rows"), (-1, "tile_columns")):
                        shape[pos] = math.ceil(
                            shape[pos] / integer(layout[field])
                        ) * integer(layout[field])
                size = math.prod(shape) * dtypes[text(tensor["dtype"])]
                access = (
                    obj(obj(job[side])["access"])
                    if side != "c"
                    else obj(obj(job["output"])["destination"])
                )
                require(
                    integer(access["size_bytes"]) == size,
                    "compute tensor storage footprint mismatch",
                )
    # Independently replay generation owners; plausible final free counts cannot conceal stale reuse.
    generations: dict[str, int] = defaultdict(int)
    owners: dict[str, str] = {}
    for event in rows(runtime["slot_events"]):
        slot = text(event["slot_id"])
        job = text(event["job_id"])
        require(
            integer(event["generation"]) == generations[slot],
            "stale slot generation event",
        )
        if event["action"] == "reserve":
            require(slot not in owners, "slot reused before release")
            owners[slot] = job
        else:
            require(owners.get(slot) == job, "foreign slot owner")
        if event["action"] == "release":
            del owners[slot]
            generations[slot] += 1
    if raw["status"] == "complete":
        require(not owners, "generation retained after completion")


def audit_mixed(check: str, raw: Data, config: Data, graph: Data) -> None:
    if check in {"routing", "multicast", "packet_accounting"}:
        transport(raw, config, graph)
    elif check == "memory_service":
        memory(raw, config)
    elif check == "ownership":
        ownership(raw, config)
    elif check == "synchronization":
        scalar(raw, config)
        causality(raw, config, graph)
    elif check == "causality":
        scalar(raw, config)
        causality(raw, config, graph)
        compute(raw, config)
    elif check == "compute_work":
        compute(raw, config)
    elif check == "drain":
        require(raw["status"] == "complete", "mixed runtime did not drain")
        transport(raw, config, graph)
        memory(raw, config)
        scalar(raw, config)
        ownership(raw, config)
        compute(raw, config)
    elif check == "bounded_execution":
        require(
            0
            <= number(raw["elapsed_aci_cycles"])
            <= number(obj(config["memory"])["max_aci_cycles"]),
            "mixed runtime exceeds admitted horizon",
        )
    else:
        raise MulticastAuditUnavailable(f"mixed runtime does not provide {check}")
