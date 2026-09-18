"""Independent input/event oracles. No simulator routing, cost or accounting helpers."""

import math
from collections import Counter, defaultdict
from fractions import Fraction
from itertools import groupby

from ..configs.schemas.validation import CheckName
from .adapters import Admission
from .data import Data, array, integer, key, number, obj, parse, require, rows, text


class UnsupportedAudit(ValueError):
    """The selected adapter does not expose this mechanism."""


def close(actual: object, expected: float, label: str) -> None:
    require(math.isclose(number(actual), expected, rel_tol=1e-10, abs_tol=1e-8), label)


def memory_view(admitted: Admission, raw: Data) -> tuple[Data, Data]:
    if "memory_session" in raw:
        session = obj(raw["memory_session"])
        # Dynamic memory operations are exported by the admitted compute session.
        config = parse(text(obj(session["plan"])["configuration_json"]))
        declared = obj(admitted.configuration["memory"])
        require(all(config.get(field) == value for field, value in declared.items()),
                "exported memory settings differ from admitted input")
        return session, config
    if "memory_resources" in raw:
        return raw, admitted.configuration
    raise UnsupportedAudit("addressed memory observations unavailable")


def route_audit(admitted: Admission, raw: Data) -> None:
    if admitted.adapter == "topology_replay_v1":
        graph, config = admitted.graph, admitted.configuration
        links = {(integer(r["fabric_id"]), text(r["link_id"])): r for r in rows(graph["links"])}
        endpoints = {text(r["endpoint_id"]): r for r in rows(graph["attachments"])}
        for route in rows(config["routes"]):
            current = endpoints[text(route["source"])]["router_id"]
            for link_id in array(route["link_ids"]):
                link = links[(integer(route["fabric_id"]), text(link_id))]
                require(link["src_router"] == current and link["enabled"] is True, "invalid explicit hop")
                current = link["dst_router"]
            require(current == endpoints[text(route["destination"])]["router_id"], "wrong explicit destination")
        packet_audit(admitted, raw)
        return
    if admitted.adapter == "torus_replay_v2":
        routes = rows(obj(raw["plan"])["routes"])
        bindings = rows(obj(admitted.configuration["binding"])["fabrics"])
        endpoints = rows(admitted.graph["attachments"])
    else:
        session, config = memory_view(admitted, raw)
        routes = [obj(obj(p["definition"])["route"]) for p in rows(session["wire_packets"])]
        bindings, endpoints = rows(config["routing"]), rows(config["endpoints"])
    graph = admitted.graph
    routers = {(integer(r["fabric_id"]), text(r["router_id"])): r for r in rows(graph["routers"])}
    fabrics = {integer(f["fabric_id"]): obj(f["extent"]) for f in rows(graph["fabrics"])}
    for route in routes:
        fabric = integer(route["fabric_id"])
        binding = next(b for b in bindings if b["fabric_id"] == fabric)
        extent = fabrics[fabric]
        source = next(e for e in endpoints if e["endpoint_id"] == route["source"] and e["fabric_id"] == fabric)
        target = next(e for e in endpoints if e["endpoint_id"] == route["destination"] and e["fabric_id"] == fabric)
        start = obj(routers[(fabric, text(source["router_id"]))]["coordinate"])
        end = obj(routers[(fabric, text(target["router_id"]))]["coordinate"])
        position = [integer(start["x"]), integer(start["y"])]
        target_position = [integer(end["x"]), integer(end["y"])]
        sign = -1 if binding["topology_policy"] == "torus_2d_negative" else 1
        axes = (0, 1) if binding["routing_policy"] == "dimension_order_xy" else (1, 0)
        expected: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        for axis in axes:
            modulus = integer(extent["width" if axis == 0 else "height"])
            while position[axis] != target_position[axis]:
                before = tuple(position)
                position[axis] = (position[axis] + sign) % modulus
                expected.append((before, tuple(position)))
        observed: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        hops = rows(route["hops"])
        require(len(hops) == len(expected) + 2, "route has missing/extra local or network hops")
        require(obj(obj(hops[0]["lane"])["channel"])["identity"] == route["source"], "wrong injection endpoint")
        require(obj(obj(hops[-1]["lane"])["channel"])["identity"] == route["destination"], "wrong ejection endpoint")
        for hop in hops[1:-1]:
            pair: list[tuple[int, ...]] = []
            for side in ("src_router", "dst_router"):
                router = routers[(fabric, text(hop[side]))]
                require(router.get("enabled") is not False, "route uses disabled router")
                coordinate = obj(router["coordinate"])
                pair.append((integer(coordinate["x"]), integer(coordinate["y"])))
            observed.append((pair[0], pair[1]))
        require(observed == expected, "route differs from independent modular direction/axis oracle")
    packet_audit(admitted, raw)


def quantity(value: object, raw: Data, label: str) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    setting = obj(value)
    if setting.get("kind") == "literal":
        return integer(setting["value"])
    resolved = rows(obj(raw["plan"])["quantities"])
    return integer(next(q["value"] for q in resolved if q.get("field_path") == label))


def packet_audit(admitted: Admission, raw: Data) -> None:
    """Check every launched flit against declared payload/format and route membership."""
    # key -> (payload bytes, data capacity, physical width, header flits, channels)
    expected: dict[str, tuple[int, int, int, int, tuple[str, ...]]] = {}
    complete = raw.get("status") == "complete"
    legacy = admitted.adapter == "topology_replay_v1"
    memory = "memory_resources" in raw or "memory_session" in raw
    config = admitted.configuration
    session = raw
    if memory:
        session, config = memory_view(admitted, raw)
        transport = obj(session["transport"])
        fmt = obj(config["packet"])
        definitions: dict[str, Data] = {}
        for item in rows(session["wire_packets"]):
            definition = obj(item["definition"])
            packet = obj(definition["packet"])
            identity = obj(packet["identity"])
            identifier = key([identity["operation_id"], identity["segment_index"], identity["purpose"]])
            require(identifier not in definitions, "duplicate memory packet")
            definitions[identifier] = definition
        wanted: set[str] = set()
        for operation in rows(config["operations"]):
            kind = operation["kind"]
            if kind not in ("read", "write_posted", "write_acknowledged"):
                continue
            length = integer(obj(operation["source"])["size_bytes"])
            segment = integer(fmt["max_segment_payload_bytes"])
            purposes = ("read_request", "read_response") if kind == "read" else ("write_request", "write_ack") if kind == "write_acknowledged" else ("write_request",)
            for index, offset in enumerate(range(0, length, segment)):
                for purpose in purposes:
                    identifier = key([operation["operation_id"], index, purpose])
                    wanted.add(identifier)
                    require(identifier in definitions, "missing declared memory packet")
                    definition = definitions[identifier]
                    payload = min(segment, length - offset) if purpose in ("write_request", "read_response") else 0
                    channels = tuple(key(obj(h["lane"])["channel"]) for h in rows(obj(definition["route"])["hops"]))
                    expected[identifier] = (payload, integer(fmt["data_capacity_bytes"]), integer(fmt["physical_flit_bytes"]), integer(fmt["header_flits"]), channels)
        require(wanted == set(definitions), "fabricated network packet for local or undeclared operation")
    elif admitted.adapter in ("torus_replay_v2", "topology_replay_v1"):
        transport = raw
        for traffic in rows(config["traffic"]):
            fabric = next(f for f in rows(config["fabrics"]) if f["fabric_id"] == traffic["fabric_id"])
            fmt = obj(fabric["flit"])
            classes = ("request", "response") if "response_payload_bytes" in traffic else ("request",)
            for traffic_class in classes:
                payload = integer(traffic["response_payload_bytes" if traffic_class == "response" else "payload_bytes"])
                source, destination = (traffic["destination"], traffic["source"]) if traffic_class == "response" else (traffic["source"], traffic["destination"])
                if legacy:
                    route = next(r for r in rows(config["routes"]) if (r["fabric_id"], r["source"], r["destination"]) == (traffic["fabric_id"], source, destination))
                    channels = tuple(key([k, traffic["fabric_id"], v]) for k, v in [("inject", source), *(("network", v) for v in array(route["link_ids"])), ("eject", destination)])
                    identifier = text(traffic["transfer_id"])
                else:
                    route = next(r for r in rows(obj(raw["plan"])["routes"]) if (r["fabric_id"], r["source"], r["destination"], r["traffic_class"]) == (traffic["fabric_id"], source, destination, traffic_class))
                    channels = tuple(key(obj(h["lane"])["channel"]) for h in rows(route["hops"]))
                    identifier = key({"transfer_id": traffic["transfer_id"], "traffic_class": traffic_class})
                expected[identifier] = (payload, integer(fmt["payload_capacity_bytes"]), quantity(fmt["physical_flit_bytes"], raw, f"fabrics.{traffic['fabric_id']}.flit.physical_flit_bytes"), 0, channels)
    else:
        raise UnsupportedAudit("no packet execution observations")
    seen: dict[tuple[str, str], set[int]] = defaultdict(set)
    legacy_indices: Counter[tuple[str, str]] = Counter()
    total = injected = 0
    for event in rows(transport["trace"]):
        if event["action"] != ("LINK_SEND" if legacy else "link_launch"):
            continue
        packet = obj(event["packet"]) if not legacy else {}
        identifier = text(event["transfer_id"]) if legacy else text(packet["transfer_id"]) if memory else key(packet)
        require(identifier in expected, "launch of undeclared packet")
        payload, capacity, width, headers, channels = expected[identifier]
        channel = text(event["channel_id"]) if legacy else key(event["channel"])
        require(channel in channels, "launch on wrong route hop")
        pair = (identifier, channel)
        index = legacy_indices[pair] if legacy else integer(event["flit_index"])
        legacy_indices[pair] += 1
        count = headers + ((payload + capacity - 1) // capacity if memory else max(1, (payload + capacity - 1) // capacity))
        useful = 0 if index < headers else max(0, min(capacity, payload - (index - headers) * capacity))
        require(0 <= index < count and index not in seen[pair], "duplicate or out-of-range flit")
        require(event["physical_bytes"] == width and event["payload_bytes"] == useful, "flit width/payload arithmetic mismatch")
        seen[pair].add(index)
        total += width
        if channel == channels[0]:
            injected += width
    for identifier, (payload, capacity, width, headers, channels) in expected.items():
        count = headers + ((payload + capacity - 1) // capacity if memory else max(1, (payload + capacity - 1) // capacity))
        if complete:
            for channel in channels:
                require(seen[(identifier, channel)] == set(range(count)), "complete run has missing packet/flit/hop")
    require(integer(transport["transmitted_channel_bytes"]) == total, "channel total differs from actual launches")
    if memory:
        require(session["packet_bytes"] == injected and session["channel_bytes"] == total, "planned bytes counted as observed")
    link_timing_audit(admitted, raw, transport)


def link_timing_audit(admitted: Admission, raw: Data, transport: Data) -> None:
    """A physical channel's launch budget is shared across packets and lanes."""
    legacy = admitted.adapter == "topology_replay_v1"
    gaps: dict[str, float] = {}
    effective = parse(admitted.effective.text)
    if legacy:
        for channel in rows(effective["channels"]):
            gaps[text(channel["channel_id"])] = float(number(obj(channel["settings"])["launch_interval_aci_cycles"]))
    else:
        memory = "runtime" in effective
        config = obj(obj(effective["runtime"])["transport"]) if memory else admitted.configuration
        resolved = {text(q["field_path"]): number(q["value"]) for q in rows(obj(raw.get("plan", {})).get("quantities", []))}

        def value(setting: object, path: str) -> float:
            if isinstance(setting, (float, int)):
                return float(setting)
            item = obj(setting)
            return float(number(item["value"])) if item.get("kind") == "literal" else float(resolved[path])

        for event in rows(transport["trace"]):
            if event["action"] != "link_launch":
                continue
            channel = obj(event["channel"])
            identifier = key(channel)
            if identifier in gaps:
                continue
            fabric = next(f for f in rows(config["fabrics"]) if f["fabric_id"] == channel["fabric_id"])
            kind = "network_link" if channel["kind"] == "network" else "local_link"
            setting = obj(fabric[kind])
            if memory:
                for override in rows(config.get("overrides", [])):
                    if override.get("channel") == channel:
                        setting = obj(override["settings"])
                ratio = number(setting["aci_clock_hz"]) / number(setting["noc_clock_hz"])
            else:
                ratio = value(config["aci_clock"], "aci_clock") / value(fabric["noc_clock"], f"fabrics.{channel['fabric_id']}.noc_clock")
                # Overrides are explicit admitted channel settings, never fitted facts.
                for override in rows(config.get("network_overrides", [])):
                    if channel["kind"] == "network" and (override["fabric_id"], override["link_id"]) == (channel["fabric_id"], channel["identity"]):
                        setting = obj(override["settings"])
                for override in rows(config.get("local_overrides", [])):
                    if (override["endpoint_id"], override["direction"]) == (channel["identity"], channel["kind"]):
                        setting = obj(override["settings"])
            gaps[identifier] = float(number(setting["launch_interval_noc_cycles"]) * ratio)
    previous: dict[str, tuple[float, float]] = {}
    for event in rows(transport["trace"]):
        if event["action"] != ("LINK_SEND" if legacy else "link_launch"):
            continue
        identifier = text(event["channel_id"]) if legacy else key(event["channel"])
        when = float(number(event["time_aci_cycles"]))
        if identifier in previous:
            last, factor = previous[identifier]
            require(when - last + 1e-8 >= gaps[identifier] * factor, "shared channel launches exceed configured bandwidth")
        previous[identifier] = (when, float(number(event.get("launch_factor") or 1)))


def service_audit(admitted: Admission, raw: Data) -> None:
    session, config = memory_view(admitted, raw)
    services = {text(r["resource_id"]): obj(r["service"]) for r in rows(config["resources"])}
    ends: dict[str, float] = {}
    seen: set[str] = set()
    completed = 0
    for chunk in rows(session["chunks"]):
        identifier, resource = text(chunk["service_id"]), text(chunk["resource_id"])
        require(identifier not in seen, "duplicate service chunk")
        seen.add(identifier)
        service = services[resource]
        granule = integer(service["service_granule_bytes"])
        useful = integer(chunk["useful_bytes"])
        require(0 < useful <= integer(service["chunk_bytes"]), "invalid service chunk extent")
        rounded = ((integer(chunk["address"]) % granule + useful + granule - 1) // granule) * granule
        require(chunk["serviced_bytes"] == rounded, "incorrect addressed granule rounding")
        native = number(service["fixed_latency_cycles"]) + rounded / number(service["bytes_per_cycle"])
        duration = native * number(config["aci_clock_hz"]) / number(service["native_clock_hz"])
        close(chunk["native_cycles"], native, "incorrect native service duration")
        close(chunk["service_aci_cycles"], duration, "incorrect service clock conversion")
        start = number(chunk["start_aci_cycles"])
        require(start >= ends.get(resource, 0) - 1e-8, "aliases overlap on one physical service resource")
        if chunk["end_aci_cycles"] is not None:
            end = number(chunk["end_aci_cycles"])
            close(end - start, duration, "uncharged service interval")
            ends[resource] = float(end)
            completed += rounded
        else:
            ends[resource] = math.inf
    require(session["memory_service_bytes"] == completed, "completed memory bytes differ from service events")
    # A publication is an observable addressed effect, and must have paid for
    # every byte of its write service before becoming visible.
    for event in rows(session["ownership_trace"]):
        if event["action"] != "publish":
            continue
        version = obj(event["version"])
        producer = version.get("producer_id")
        if producer is None:
            continue
        start, size = integer(event["address"]), integer(event["size_bytes"])
        covered: list[tuple[int, int]] = []
        for chunk in rows(session["chunks"]):
            if chunk["client_id"] != producer or chunk["direction"] != "write" or chunk["resource_id"] != event["resource_id"]:
                continue
            if chunk["end_aci_cycles"] is not None and number(chunk["end_aci_cycles"]) <= number(event["time_aci_cycles"]) + 1e-8:
                left = integer(chunk["address"])
                covered.append((left, left + integer(chunk["useful_bytes"])))
        cursor = start
        for left, right in sorted(covered):
            if left <= cursor:
                cursor = max(cursor, right)
        require(cursor >= start + size, "published/posted effect lacks completed write service")


def ownership_audit(admitted: Admission, raw: Data) -> None:
    checked = False
    if "memory_resources" in raw or "memory_session" in raw:
        session, _ = memory_view(admitted, raw)
        descriptors = {key([r["kind"], r["owner_id"]]): r for r in rows(session["descriptors"])}
        owners: dict[str, set[str]] = defaultdict(set)
        for event in rows(session["descriptor_trace"]):
            resource = key([event["kind"], event["owner_id"]])
            owner = key([event["operation_id"], event["segment_index"]])
            held = owners[resource]
            if event["action"] == "acquire":
                require(owner not in held, "duplicate descriptor acquire")
                held.add(owner)
            else:
                require(owner in held, "unowned/double descriptor release")
                held.remove(owner)
            require(event["occupied"] == len(held) <= integer(descriptors[resource]["capacity"]), "descriptor capacity/conservation")
        for resource, snapshot in descriptors.items():
            require(snapshot["occupied"] == len(owners[resource]), "descriptor trace/snapshot disagreement")
        reservations: set[str] = set()
        accesses: set[str] = set()
        for event in rows(session["ownership_trace"]):
            action = event["action"]
            buffer = key([event["resource_id"], event["buffer_id"]])
            access = key([event["resource_id"], event["access_id"]])
            if action == "reserve":
                require(buffer not in reservations, "duplicate physical reservation")
                reservations.add(buffer)
            elif action == "release":
                require(buffer in reservations, "double buffer release")
                reservations.remove(buffer)
            elif action in ("read_acquire", "write_acquire"):
                require(access not in accesses and buffer in reservations, "invalid memory access ownership")
                accesses.add(access)
            elif action == "access_release":
                require(access in accesses, "unowned memory access release")
                accesses.remove(access)
        if raw.get("status") == "complete":
            require(not accesses, "complete memory retains live accesses")
            if session.get("teardown_complete"):
                require(not reservations, "teardown omitted reservation release")
        for resource in rows(session["memory_resources"]) + rows(session["released_resources"]):
            require(integer(resource["available_bytes"]) + integer(resource["reserved_bytes"]) == integer(resource["capacity_bytes"]), "physical memory capacity conservation")
        transport = obj(session["transport"])
        for endpoint in rows(transport["endpoint_buffers"]):
            require(integer(endpoint["available"]) + integer(endpoint["occupied"]) == integer(endpoint["capacity"]), "endpoint capacity conservation")
        checked = True
    else:
        transport = raw
    for resource in rows(transport.get("resources", [])):
        if "available" in resource:
            require(integer(resource["available"]) + integer(resource["occupied"]) + integer(resource.get("pending_returns", 0)) == integer(resource["capacity"]), "credit conservation")
            checked = True
    capacities = {key(r["lane"]): integer(r["capacity"]) for r in rows(transport.get("resources", [])) if r.get("lane") is not None}
    credits: dict[str, tuple[str, bool]] = {}
    occupied: Counter[str] = Counter()
    returning: Counter[str] = Counter()
    phases = {"credit_reserve": 0, "credit_release": 1, "credit_return": 2}
    credit_events = [e for e in rows(transport.get("trace", [])) if e["action"] in phases]
    credit_events.sort(key=lambda e: (number(e["time_aci_cycles"]), phases[text(e["action"])]))
    # Trace export sorts equal-time events by fields, not scheduler causality.
    # Apply token transitions then check the state at each distinct timestamp.
    for _, batch in groupby(credit_events, key=lambda e: number(e["time_aci_cycles"])):
        for event in batch:
            action = event["action"]
            lane, token = key(event["lane"]), text(event["token_id"])
            if action == "credit_reserve":
                require(token not in credits, "duplicate credit token")
                credits[token] = (lane, False)
                occupied[lane] += 1
            elif action == "credit_release":
                require(credits.get(token) == (lane, False), "double or unowned credit release")
                credits[token] = (lane, True)
                occupied[lane] -= 1
                returning[lane] += 1
            else:
                require(credits.get(token) == (lane, True), "credit returned before release")
                del credits[token]
                returning[lane] -= 1
        require(all(occupied[lane] + returning[lane] <= capacity for lane, capacity in capacities.items()), "event trace exceeds lane capacity")
    if capacities:
        for snapshot in rows(transport["resources"]):
            if snapshot.get("lane") is not None:
                lane = key(snapshot["lane"])
                require(snapshot["occupied"] == occupied[lane] and snapshot["pending_returns"] == returning[lane], "credit trace/snapshot disagreement")
    if "slot_events" in raw:
        slots: dict[str, tuple[str, int, str]] = {}
        generations: dict[str, int] = {}
        sequence = ("reserve", "publish_inputs", "consume", "publish_output", "drain", "writer_complete", "release")
        for event in rows(raw["slot_events"]):
            action = text(event["action"])
            if action == "wait":
                continue
            slot = key([event["stream_id"], event["slot_id"]])
            job, generation = text(event["job_id"]), integer(event["generation"])
            if action == "reserve":
                require(slot not in slots and generation == generations.get(slot, -1) + 1, "stale or duplicate slot generation")
                generations[slot] = generation
            else:
                require(slot in slots, "unowned/double slot release")
                old_job, old_generation, old_action = slots[slot]
                require((job, generation) == (old_job, old_generation) and sequence.index(action) == sequence.index(old_action) + 1, "invalid slot ownership/stage transition")
            slots[slot] = (job, generation, action)
            if action == "release":
                del slots[slot]
            require(integer(event["free"]) + integer(event["occupied"]) == integer(event["capacity"]), "slot capacity conservation")
        engines: dict[str, str] = {}
        for event in rows(raw["resource_events"]):
            engine = key([event["worker_tile_id"], event["kind"], event["engine_index"]])
            owner = text(event["job_id"])
            require(integer(event["engine_index"]) < integer(event["capacity"]), "engine index exceeds capacity")
            if event["action"] == "acquire":
                require(engine not in engines, "two jobs own one engine")
                engines[engine] = owner
            else:
                require(engines.get(engine) == owner, "unowned engine release")
                del engines[engine]
        if raw["status"] == "complete":
            require(not slots and not engines, "complete compute retains slots/engines")
        checked = True
    if not checked:
        raise UnsupportedAudit("resource ownership observations unavailable")


def compute_audit(admitted: Admission, raw: Data) -> None:
    if "stages" not in raw:
        raise UnsupportedAudit("compute stage observations unavailable")
    config = admitted.configuration
    jobs = {text(j["job_id"]): j for s in rows(config["streams"]) for j in rows(s["jobs"])}
    job_workers = {text(j["job_id"]): text(s["worker_tile_id"]) for s in rows(config["streams"]) for j in rows(s["jobs"])}
    rates = {text(r["rate_id"]): r for r in rows(config["rates"])}
    workers = {text(w["tile_id"]): w for w in rows(config["workers"])}
    dtypes = {text(d["dtype_id"]): integer(d["bytes_per_element"]) for d in rows(config["dtypes"])}
    operation_rows = rows(obj(raw["memory_session"])["operations"])
    memory_operations = {text(o["operation_id"]): o for o in operation_rows}
    expected_operations: set[str] = set()
    for job_id, job in jobs.items():
        roles = ["operand_a", "operand_b", "result"]
        roles += ["read_" + operand for operand in ("a", "b") if obj(job[operand])["mode"] == "remote"]
        if obj(job["output"])["mode"] != "local":
            roles.append("write")
        expected_operations.update(key(["compute", job_id, role]) for role in roles)
    require(set(memory_operations) == expected_operations and len(operation_rows) == len(expected_operations),
            "compute memory operations differ from declared jobs")
    plans = rows(obj(raw["plan"])["jobs"])
    require({text(p["job_id"]) for p in plans} == set(jobs) and len(plans) == len(jobs),
            "exported compute jobs differ from admitted input")
    stages: dict[str, dict[str, float]] = defaultdict(dict)
    for event in rows(raw["stages"]):
        job, action = text(event["job_id"]), text(event["action"])
        if action.endswith("wait"):
            continue
        require(action not in stages[job], "duplicate compute stage")
        stages[job][action] = float(number(event["time_aci_cycles"]))
    planned_useful = planned_padded = completed_useful = completed_padded = 0
    sequence = ("reader_start", "inputs_ready", "operand_start", "operand_end", "math_start", "math_end", "result_start", "output_ready", "writer_start", "writer_complete", "slot_release")
    for plan in plans:
        job = text(plan["job_id"])
        operation, cost = obj(jobs[job]["operation"]), obj(plan["cost"])
        require(plan["worker_tile_id"] == job_workers[job], "compute worker differs from declared stream")
        rate = rates[text(cost["rate_id"])]
        require(cost["rate_id"] in array(workers[job_workers[job]]["rate_ids"]), "compute rate not enabled by declared worker")
        expected_key = {"operation": operation["kind"], "accumulator_precision": operation["accumulator_precision"],
                        "fidelity": operation["fidelity"], "layout": obj(operation["a"])["layout"],
                        **{operand + "_dtype": obj(operation[operand])["dtype"] for operand in ("a", "b", "c")}}
        require(rate["key"] == expected_key, "compute rate key differs from declared operation")
        shape = [integer(v) for v in array(obj(operation["a"])["shape"])]
        batch, m, k = shape
        n = integer(array(obj(operation["c"])["shape"])[-1])
        block = obj(rate["block"])
        useful = 2 * batch * m * n * k
        padded = 2 * batch * math.prod(((v + integer(block[a]) - 1) // integer(block[a])) * integer(block[a]) for a, v in (("m", m), ("n", n), ("k", k)))
        require(cost["useful_work"] == useful and cost["executed_work"] == padded, "matrix/block work mismatch")
        for operand in ("a", "b", "c"):
            tensor = obj(operation[operand])
            dimensions = [integer(v) for v in array(tensor["shape"])]
            size = math.prod(dimensions) * dtypes[text(tensor["dtype"])]
            layout = obj(tensor["layout"])
            if layout["kind"] == "tiled":
                for axis, field in ((-2, "tile_rows"), (-1, "tile_columns")):
                    quantum = integer(layout[field])
                    dimensions[axis] = ((dimensions[axis] + quantum - 1) // quantum) * quantum
            storage = math.prod(dimensions) * dtypes[text(tensor["dtype"])]
            require(obj(cost[operand])["useful_bytes"] == size and obj(cost[operand])["storage_bytes"] == storage, "tensor storage/shape arithmetic")
        q = Fraction(str(rate["quantum_native_cycles"]))
        native = Fraction(str(rate["setup_native_cycles"])) + max(Fraction(str(rate["minimum_native_cycles"])), math.ceil(Fraction(padded) / Fraction(str(rate["work_per_native_cycle"])) / q) * q)
        duration = float(native * Fraction(str(obj(config["memory"])["aci_clock_hz"])) / Fraction(str(workers[text(plan["worker_tile_id"])]["native_clock_hz"])))
        close(cost["service_aci_cycles"], duration, "compute rate/clock arithmetic")
        observed = stages[job]
        for phase, prerequisites in (("inputs_ready", ("read_a", "read_b")), ("math_start", ("operand_a", "operand_b")), ("output_ready", ("result",))):
            if phase in observed:
                for prerequisite in prerequisites:
                    memory_operation = memory_operations.get(key(["compute", job, prerequisite]))
                    if memory_operation is not None:
                        ready = memory_operation["completion_aci_cycles"]
                        require(ready is not None and number(ready) <= observed[phase], "compute stage before memory completion")
        previous_time = 0.0
        for index, action in enumerate(sequence):
            if action in observed:
                require(index == 0 or sequence[index - 1] in observed, "compute stage before prerequisite publication")
                require(observed[action] >= previous_time, "compute causal stage order")
                previous_time = observed[action]
        if "math_end" in observed:
            close(observed["math_end"] - observed["math_start"], duration, "uncharged math work")
            completed_useful += useful
            completed_padded += padded
        planned_useful += useful
        planned_padded += padded
    work = obj(raw["work"])
    require((work["planned_useful_work"], work["planned_executed_work"], work["completed_useful_work"], work["completed_executed_work"]) == (planned_useful, planned_padded, completed_useful, completed_padded), "planned/completed work accounting")
    elapsed = number(raw["elapsed_aci_cycles"])
    busy = sum(s.get("math_end", elapsed) - s["math_start"] for s in stages.values() if "math_start" in s)
    close(work["math_busy_aci_cycles"], busy, "math busy interval accounting")
    if raw["status"] == "complete":
        require(set(stages) == set(jobs) and all("slot_release" in s for s in stages.values()), "complete run omitted a compute job")


def drain_audit(admitted: Admission, raw: Data) -> None:
    if raw.get("status") != "complete":
        raise UnsupportedAudit("drain cannot pass for an incomplete or inspected run")
    require(not raw.get("pending"), "complete status retains pending work")
    packet_audit(admitted, raw)
    if "memory_resources" in raw or "memory_session" in raw:
        session, config = memory_view(admitted, raw)
        require(not session["pending"], "posted effects pending after local completion")
        operations = {text(o["operation_id"]): o for o in rows(session["operations"])}
        require(set(operations) == {text(o["operation_id"]) for o in rows(config["operations"])}, "complete session omitted operations")
        for operation in rows(config["operations"]):
            observed = operations[text(operation["operation_id"])]
            require(observed["completion_aci_cycles"] is not None, "operation has not retired")
            if operation["destination"] is not None:
                require(observed["destination_ready_aci_cycles"] is not None, "posted destination not visible")
        for resource in rows(session["memory_resources"]):
            service = obj(resource["service"])
            require(not service["active"] and not service["queued"] and not service["pending_service_ids"], "complete session retains service charges")
        ownership_audit(admitted, raw)
        service_audit(admitted, raw)
    if "stages" in raw:
        compute_audit(admitted, raw)


def audit(check: CheckName, admitted: Admission, raw: Data) -> str:
    functions = {"routing": route_audit, "packet_accounting": packet_audit, "memory_service": service_audit,
                 "ownership": ownership_audit, "compute_work": compute_audit, "causality": compute_audit, "drain": drain_audit}
    function = functions.get(check)
    if function is None:
        raise UnsupportedAudit(f"{check} is not an event oracle")
    function(admitted, raw)
    return f"{check}: independent declared-input/event audit passed"
