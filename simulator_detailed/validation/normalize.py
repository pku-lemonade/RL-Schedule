"""Lossless counters and explicitly scoped observable semantics around raw results."""

from collections import Counter
from fractions import Fraction

from ..configs.schemas.validation import (
    AddressedEffect,
    CausalEdge,
    ClockDomain,
    ExecutionState,
    MeasurementWindow,
    Metadata,
    MetricObservation,
    MissingObservation,
    NormalizedObservations,
    ObservationCounter,
    ObservationEntity,
    ObservationEvent,
    OccupancyInterval,
    RouteObservation,
    TimePoint,
)
from .adapters import Admission
from .data import Data, array, integer, key, number, obj, rows, text
from .identity import content_digest


def converted_seconds(point: TimePoint, clocks: tuple[ClockDomain, ...]) -> Fraction:
    """Return exact rational seconds while the original TimePoint stays intact."""
    value = Fraction(str(point.value))
    if point.unit == "seconds":
        return value
    if point.unit == "nanoseconds":
        return value / 10**9
    clock = next((c for c in clocks if c.domain_id == point.clock_domain), None)
    if clock is None or clock.hz.value is None:
        raise ValueError("cycle conversion requires an explicit known clock")
    return value / Fraction(str(clock.hz.value))


def point(value: float) -> TimePoint:
    return TimePoint(value=value, unit="cycles", clock_domain="aci")


def execution(raw: Data) -> ExecutionState:
    status = raw.get("status")
    if status in ("complete", "incomplete"):
        return "complete" if status == "complete" else "incomplete"
    return "inspected"


def normalize(admission: Admission, raw: Data) -> NormalizedObservations:
    state = execution(raw)
    config = admission.configuration
    memory_config = obj(config["memory"]) if "memory" in config else config
    hz = memory_config.get("aci_clock_hz")
    if hz is None and "aci_clock_mhz" in config:
        hz = number(config["aci_clock_mhz"]) * 10**6
    if hz is None and isinstance(config.get("aci_clock"), dict):
        hz = obj(config["aci_clock"]).get("value")
    # Resolved quantities cover profile-backed clocks without guessing defaults.
    if hz is None and isinstance(raw.get("plan"), dict):
        for quantity in rows(obj(raw["plan"]).get("quantities", [])):
            if quantity.get("field_path") == "aci_clock":
                hz = quantity.get("value")
    clock = ClockDomain(domain_id="aci", hz=Metadata[float](state="known", value=float(number(hz))) if hz is not None else Metadata[float](state="unknown", reason="clock not exported for this adapter"))
    entities: dict[str, ObservationEntity] = {}
    events: list[ObservationEvent] = []
    edges: list[CausalEdge] = []
    effects: list[AddressedEffect] = []
    intervals: list[OccupancyInterval] = []
    routes: list[RouteObservation] = []
    occurrences: Counter[str] = Counter()

    def entity(role: str, value: object) -> str:
        identifier = role + ":" + key(value)
        entities[identifier] = ObservationEntity.model_validate({"entity_id": identifier, "role": role})
        return identifier

    for resource in rows(admission.graph.get("resources", [])):
        entity("resource", resource["resource_id"])
    for endpoint in rows(admission.graph.get("attachments", [])):
        identifier = entity("endpoint", endpoint["endpoint_id"])
        owners = endpoint.get("resource_ids", [])
        if isinstance(owners, list) and len(owners) == 1:
            owner = entity("resource", owners[0])
            entities[identifier] = entities[identifier].model_copy(update={"physical_owner": owner, "fabric_id": endpoint["fabric_id"]})

    def event(prefix: str, row: Data, subject: str) -> str:
        action = text(row["action"])
        stem = key([prefix, subject, action])
        count = occurrences[stem]
        occurrences[stem] += 1
        identifier = stem + ":" + str(count)
        counters = tuple(ObservationCounter(name=k, value=integer(row[k]), unit="bytes", scope="observed") for k in ("physical_bytes", "payload_bytes", "size_bytes") if k in row)
        events.append(ObservationEvent(event_id=identifier, action=action, subject_id=subject,
                                      time=point(number(row["time_aci_cycles"])), counters=counters,
                                      generation=integer(row["generation"]) if row.get("generation") is not None else None))
        return identifier

    session = obj(raw["memory_session"]) if "memory_session" in raw else raw
    previous: dict[str, str] = {}
    for group, role, field in (("stages", "job", "job_id"), ("lifecycle", "transfer", "operation_id")):
        source = raw if group == "stages" else session
        for row in rows(source.get(group, [])):
            subject = entity(role, row[field])
            identifier = event(group, row, subject)
            # Per-subject orders are meaningful; independent subjects have no invented edges.
            if subject in previous:
                edges.append(CausalEdge(before=previous[subject], after=identifier))
            previous[subject] = identifier
    for index, row in enumerate(rows(session.get("ownership_trace", []))):
        resource = entity("resource", row["resource_id"])
        subject = entity("endpoint", ["buffer", row["buffer_id"]])
        identifier = event("ownership", row, subject)
        if row["action"] == "publish" and integer(row["size_bytes"]) > 0:
            effects.append(AddressedEffect(effect_id=f"publish:{index}", destination_id=subject,
                                           resource_id=resource, offset_bytes=integer(row["address"]),
                                           size_bytes=integer(row["size_bytes"]), count=1, visibility_event=identifier))
    for row in rows(session.get("chunks", [])):
        resource = entity("resource", row["resource_id"])
        owner = entity("transfer", row["client_id"])
        if row.get("start_aci_cycles") is not None:
            intervals.append(OccupancyInterval(interval_id="service:" + text(row["service_id"]), resource_id=resource,
                                              owner_id=owner, start=point(number(row["start_aci_cycles"])),
                                              end=point(number(row["end_aci_cycles"])) if row.get("end_aci_cycles") is not None else None))
    active: dict[str, tuple[str, str, TimePoint]] = {}
    for row in rows(raw.get("resource_events", [])):
        resource = entity("resource", [row["worker_tile_id"], row["kind"], row["engine_index"]])
        owner = entity("job", row["job_id"])
        when = point(number(row["time_aci_cycles"]))
        if row["action"] == "acquire":
            active[resource] = (owner, resource + ":" + owner, when)
        elif resource in active:
            saved_owner, identifier, start = active.pop(resource)
            intervals.append(OccupancyInterval(interval_id=identifier, resource_id=resource, owner_id=saved_owner, start=start, end=when))
    for resource, (owner, identifier, start) in active.items():
        intervals.append(OccupancyInterval(interval_id=identifier, resource_id=resource, owner_id=owner, start=start, end=None))
    transport = obj(session["transport"]) if "transport" in session else raw
    for row in rows(transport.get("trace", [])):
        packet = row.get("packet")
        subject = entity("transfer", packet if packet is not None else row.get("transfer_id", "transport"))
        event("transport", row, subject)
    plan = obj(raw["plan"]) if "plan" in raw else {}
    raw_routes = rows(plan.get("routes", []))
    raw_routes += [obj(obj(row["definition"])["route"]) for row in rows(session.get("wire_packets", []))]
    for index, route in enumerate(raw_routes):
        source, destination = entity("endpoint", route["source"]), entity("endpoint", route["destination"])
        transfer = entity("transfer", ["route", index])
        links = tuple(entity("link", obj(obj(hop["lane"])["channel"])) for hop in rows(route["hops"]))
        routes.append(RouteObservation(transfer_id=transfer, source_id=source, destination_id=destination,
                                       fabric_id=integer(route["fabric_id"]), link_ids=links))
    if admission.adapter == "topology_replay_v1":
        for traffic in rows(config["traffic"]):
            route = next(r for r in rows(config["routes"]) if (r["fabric_id"], r["source"], r["destination"]) == (traffic["fabric_id"], traffic["source"], traffic["destination"]))
            routes.append(RouteObservation(transfer_id=entity("transfer", traffic["transfer_id"]),
                                           source_id=entity("endpoint", traffic["source"]), destination_id=entity("endpoint", traffic["destination"]),
                                           fabric_id=integer(traffic["fabric_id"]),
                                           link_ids=tuple(entity("link", [traffic["fabric_id"], link]) for link in array(route["link_ids"]))))
    elapsed = number(raw.get("elapsed_aci_cycles", 0))
    window = MeasurementWindow(boundary="simulation_start_to_snapshot", start=point(0), end=point(elapsed),
                               excluded_warmups=(), repetitions=1, aggregation="none")
    metrics: list[MetricObservation] = []

    def metric(name: str, value: float, unit: str, numerator: str, denominator: str = "one run") -> None:
        metrics.append(MetricObservation(metric_id=name, value=value, unit=unit, clock_domain="aci" if "cycle" in unit else None,
                                         numerator=numerator, denominator=denominator, window=window,
                                         completion_scope="complete_run" if state == "complete" else "partial"))

    if state != "inspected":
        metric("elapsed", elapsed, "cycles", "elapsed ACI cycles")
    for field in ("received_payload_bytes", "transmitted_channel_bytes", "packet_bytes", "channel_bytes", "memory_service_bytes"):
        if field in raw:
            metric(field, integer(raw[field]), "bytes", "observed " + field)
    if "work" in raw:
        for name, value in obj(raw["work"]).items():
            if name.startswith("completed_"):
                metric(name, integer(value), "work", name)
            elif name.endswith("_aci_cycles"):
                metric(name, number(value), "cycles", name)
        # Planned work is an exact counter on a separate non-execution event.
        work = obj(raw["work"])
        events.append(ObservationEvent(event_id="planned_work", action="plan", subject_id=entity("job", "workload"), time=None,
                                      counters=tuple(ObservationCounter(name=k, value=integer(v), unit="work", scope="planned") for k, v in work.items() if k.startswith("planned_"))))
    payload = raw.get("received_payload_bytes")
    if state == "complete" and elapsed > 0 and payload is not None:
        metric("payload_throughput", integer(payload) / elapsed, "bytes_per_cycle", "received payload bytes", "elapsed ACI cycles")
    pending = tuple(text(p) if isinstance(p, str) else key(p) for p in array(raw.get("pending", [])))
    if state == "incomplete" and not pending:
        pending = ("runtime reports unfinished work; inspect raw result",)
    digest = content_digest(raw)
    return NormalizedObservations(observation_id="obs:" + digest, source_result_sha256=digest, execution=state,
                                  clocks=(clock,), entities=tuple(entities.values()), events=tuple(events), effects=tuple(effects),
                                  causal_edges=tuple(edges), routes=tuple(routes), intervals=tuple(intervals), metrics=tuple(metrics), pending=pending,
                                  missing=(MissingObservation(name="tensor_values", outcome="unsupported", reason="abstract traffic and work only"),
                                           MissingObservation(name="silicon_timing", outcome="not_run", reason="requires compatible external measurements")))
