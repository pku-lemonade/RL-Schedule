"""Lossless shared-runtime observations; optional extensions leave v1 records alone."""

from ..configs.schemas.validation import (
    AddressedEffect,
    ClockDomain,
    Metadata,
    MetricObservation,
    MissingObservation,
    NormalizedObservations,
    ObservationCounter,
    ObservationEntity,
    ObservationEvent,
    OccupancyInterval,
    TimePoint,
)
from .data import Data, array, integer, key, number, obj, rows, text
from .identity import canonical_record, content_digest
from .intervals import interval_metric


def normalize_mixed(raw: Data, configuration: Data) -> NormalizedObservations:
    complete = raw["status"] == "complete"
    entities: dict[str, ObservationEntity] = {}
    events: list[ObservationEvent] = []
    effects: list[AddressedEffect] = []
    intervals: list[OccupancyInterval] = []
    metrics: list[MetricObservation] = []
    lifecycle_points: dict[str, dict[str, list[tuple[str, TimePoint]]]] = {}

    def point(value: float) -> TimePoint:
        return TimePoint(value=value, unit="cycles", clock_domain="aci")

    def entity(role: str, value: object) -> str:
        identifier = role + ":" + key(value)
        entities.setdefault(
            identifier,
            ObservationEntity.model_validate({"entity_id": identifier, "role": role}),
        )
        return identifier

    groups = [
        (name, rows(raw.get(name, [])))
        for name in (
            "lifecycle",
            "ownership_trace",
            "service_trace",
            "descriptor_trace",
        )
    ]
    tree = obj(raw["tree_transport"])
    groups += [("transport", rows(tree["trace"])), ("tree", rows(tree["events"]))]
    compute = raw.get("compute")
    if isinstance(compute, dict):
        groups += [
            ("compute:" + name, rows(compute[name]))
            for name in ("stages", "resource_events", "slot_events")
        ]
    for group, items in groups:
        for index, row in enumerate(items):
            event_id = f"{group}:{index}"
            subject = entity(
                "job" if group.startswith("compute:") and row.get("job_id") is not None else "transfer",
                row.get(
                    "operation_id",
                    row.get(
                        "packet", row.get("job_id", row.get("resource_id", "runtime"))
                    ),
                ),
            )
            counters = tuple(
                ObservationCounter(
                    name=k,
                    value=integer(row[k]),
                    unit="bytes" if k.endswith("bytes") else "count",
                    scope="observed",
                )
                for k in (
                    "physical_bytes",
                    "payload_bytes",
                    "size_bytes",
                    "occupied",
                    "free",
                )
                if row.get(k) is not None
            )
            events.append(
                ObservationEvent(
                    event_id=event_id,
                    action=text(row["action"]),
                    subject_id=subject,
                    time=point(number(row["time_aci_cycles"])),
                    counters=counters,
                    generation=integer(row["generation"])
                    if row.get("generation") is not None
                    else None,
                    details=canonical_record(row),
                )
            )
            if group == "lifecycle":
                operation_id = text(row["operation_id"])
                action = text(row["action"])
                lifecycle_points.setdefault(operation_id, {}).setdefault(action, []).append(
                    (event_id, point(number(row["time_aci_cycles"])))
                )
            if group == "ownership_trace" and row["action"] == "publish":
                effects.append(
                    AddressedEffect(
                        effect_id="effect:" + event_id,
                        destination_id=entity("endpoint", row["buffer_id"]),
                        resource_id=entity("resource", row["resource_id"]),
                        offset_bytes=integer(row["address"]),
                        size_bytes=integer(row["size_bytes"]),
                        count=1,
                        visibility_event=event_id,
                    )
                )
    acknowledged = {
        text(operation["operation_id"])
        for operation in rows(configuration.get("operations", []))
        if operation.get("kind") == "write_acknowledged"
    }
    acknowledged.update(
        text(operation["operation_id"])
        for operation in rows(configuration.get("writes", []))
        if operation.get("completion") == "write_acknowledged"
    )
    for operation_id in sorted(acknowledged):
        actions = lifecycle_points.get(operation_id, {})
        submissions = actions.get("submission", [])
        completions = actions.get("complete", [])
        if complete and submissions and completions:
            start_event, start = min(submissions, key=lambda item: item[1].value)
            end_event, end = max(completions, key=lambda item: item[1].value)
            metrics.append(interval_metric(
                metric_id="operation_submission_to_acknowledged_completion:" + key(operation_id),
                boundary="operation_submission_to_acknowledged_completion",
                semantic_scope="acknowledged_operation",
                subject_id=entity("transfer", operation_id), resource_id=None,
                start_event_id=start_event, end_event_id=end_event, start=start, end=end,
                value=end.value - start.value,
            ))
    for group in ("chunks", "scalar_service"):
        for index, row in enumerate(rows(raw[group])):
            subject = entity("transfer", row["client_id"])
            identifier = f"{group}:{index}"
            start = point(number(row["start_aci_cycles"]))
            end = point(number(row["end_aci_cycles"]))
            start_event = f"service-begin:{group}:{index}"
            events.append(
                ObservationEvent(
                    event_id=start_event,
                    action="memory_service_begin" if group == "chunks" else "scalar_service_begin",
                    subject_id=subject,
                    time=start,
                    details=canonical_record(row),
                )
            )
            events.append(
                ObservationEvent(
                    event_id=identifier,
                    action=text(row["direction"]),
                    subject_id=subject,
                    time=end,
                    details=canonical_record(row),
                    counters=tuple(
                        ObservationCounter(
                            name=k,
                            value=integer(row[k]),
                            unit="bytes" if k.endswith("bytes") else "count",
                            scope="observed",
                        )
                        for k in (
                            "old_value",
                            "new_value",
                            "useful_bytes",
                            "serviced_bytes",
                            "read_service_bytes",
                            "write_service_bytes",
                        )
                        if k in row
                    ),
                )
            )
            intervals.append(
                OccupancyInterval(
                    interval_id=identifier,
                    resource_id=entity("resource", row["resource_id"]),
                    owner_id=subject,
                    start=start,
                    end=end,
                )
            )
            if complete and group == "chunks":
                resource = entity("resource", row["resource_id"])
                metrics.append(interval_metric(
                    metric_id="memory_service_begin_to_end:" + identifier,
                    boundary="memory_service_begin_to_end", semantic_scope="memory_service",
                    subject_id=subject, resource_id=resource, start_event_id=start_event,
                    end_event_id=identifier, start=start, end=end, value=end.value - start.value,
                ))
    if isinstance(compute, dict):
        active: dict[str, tuple[str, str, TimePoint, str, bool]] = {}
        for index, row in enumerate(rows(compute["resource_events"])):
            resource = entity("resource", [row["worker_tile_id"], row["kind"], row["engine_index"]])
            subject = entity("job", row["job_id"])
            when = point(number(row["time_aci_cycles"]))
            event_id = f"compute:resource_events:{index}"
            if row["action"] == "acquire":
                active[resource] = (subject, "compute-resource:" + event_id, when, event_id, row["kind"] == "compute")
            elif resource in active:
                owner, interval_id, start, start_event, is_compute = active.pop(resource)
                intervals.append(OccupancyInterval(interval_id=interval_id, resource_id=resource,
                                                   owner_id=owner, start=start, end=when))
                if complete and is_compute:
                    metrics.append(interval_metric(
                        metric_id="compute_resource_acquire_to_release:" + start_event,
                        boundary="compute_resource_acquire_to_release", semantic_scope="compute_service",
                        subject_id=owner, resource_id=resource, start_event_id=start_event,
                        end_event_id=event_id, start=start, end=when, value=when.value - start.value,
                    ))
        for resource, (owner, interval_id, start, _event, _is_compute) in active.items():
            intervals.append(OccupancyInterval(interval_id=interval_id, resource_id=resource,
                                               owner_id=owner, start=start, end=None))
    # Snapshots retain pending owners, destinations, returns and integer state verbatim.
    records = {
        name: raw[name]
        for name in (
            "memory_resources",
            "released_resources",
            "descriptors",
            "counters",
            "inbox_values",
        )
    }
    records.update(
        {
            name: tree[name]
            for name in ("resources", "deliveries", "submitted", "pending")
        }
    )
    if isinstance(compute, dict):
        records["compute"] = {
            name: compute[name] for name in ("resources", "slots", "pending")
        }
    events.append(
        ObservationEvent(
            event_id="snapshot",
            action="snapshot",
            subject_id=entity("resource", "session"),
            time=point(number(raw["elapsed_aci_cycles"])),
            details=canonical_record(records),
        )
    )
    pending = {text(p) for p in array(raw["pending_operations"])}
    if isinstance(compute, dict):
        pending.update(text(p) for p in array(compute["pending"]))
    return NormalizedObservations(
        observation_id="obs:" + content_digest(raw),
        source_result_sha256=content_digest(raw),
        execution="complete" if complete else "incomplete",
        clocks=(
            ClockDomain(
                domain_id="aci",
                hz=Metadata[float](
                    state="known",
                    value=number(obj(configuration["memory"])["aci_clock_hz"]),
                ),
            ),
        ),
        entities=tuple(entities.values()),
        events=tuple(events),
        effects=tuple(effects),
        causal_edges=(),
        routes=(),
        intervals=tuple(intervals),
        metrics=tuple(metrics),
        pending=tuple(sorted(pending)),
        missing=(
            MissingObservation(
                name="tensor_values", outcome="unsupported", reason="abstract compute"
            ),
            MissingObservation(
                name="silicon_timing",
                outcome="not_run",
                reason="requires compatible hardware capture",
            ),
        ),
    )
