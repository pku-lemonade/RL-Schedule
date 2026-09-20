"""Lossless shared-runtime observations; optional extensions leave v1 records alone."""

from ..configs.schemas.validation import (
    AddressedEffect,
    ClockDomain,
    Metadata,
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


def normalize_mixed(raw: Data, configuration: Data) -> NormalizedObservations:
    entities: dict[str, ObservationEntity] = {}
    events: list[ObservationEvent] = []
    effects: list[AddressedEffect] = []
    intervals: list[OccupancyInterval] = []

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
                "transfer",
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
    for group in ("chunks", "scalar_service"):
        for index, row in enumerate(rows(raw[group])):
            subject = entity("transfer", row["client_id"])
            identifier = f"{group}:{index}"
            events.append(
                ObservationEvent(
                    event_id=identifier,
                    action=text(row["direction"]),
                    subject_id=subject,
                    time=point(number(row["end_aci_cycles"])),
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
                    start=point(number(row["start_aci_cycles"])),
                    end=point(number(row["end_aci_cycles"])),
                )
            )
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
        execution="complete" if raw["status"] == "complete" else "incomplete",
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
        metrics=(),
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
