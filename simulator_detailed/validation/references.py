"""Offline, hash-verified functional JSON and a pinned profiler CSV subset."""

import csv
import io
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from ..configs.schemas.validation import (
    ClockDomain,
    IntervalSampleIdentity,
    MeasurementWindow,
    Metadata,
    MetricIntervalIdentity,
    MetricObservation,
    NormalizedObservations,
    ObservationCounter,
    ObservationEntity,
    ObservationEvent,
    ProfilerSelection,
    SampleStatistics,
    TimePoint,
    ValidationReference,
)
from .data import obj, require
from .identity import canonical_record, content_digest, read_verified_artifact
from .intervals import boundary_rule

EXTRACTOR = "wormhole_reference_import"
EXTRACTOR_VERSION = "1"
CSV_HEADER = ("PCIe slot", "core_x", "core_y", "RISC processor type", "timer_id", "time[cycles since reset]",
              "stat value", "Run ID", "zone name", "zone phase", "source line", "source file")
PINNED_CSV_HEADER = (
    "PCIe slot", "core_x", "core_y", "RISC processor type", "timer_id",
    "time[cycles since reset]", "data", "run host ID", "trace id",
    "trace id counter", "zone name", "type", "source line", "source file",
    "meta data",
)


@dataclass(frozen=True)
class _ProfilerMarker:
    phase: Literal["begin", "end"]
    timestamp: int
    counters: tuple[ObservationCounter, ...] = ()
    details: object | None = None
    pair_identity: tuple[int, int, int | None, int | None] | None = None


def _counter(value: str) -> int:
    require(re.fullmatch(r"[0-9]+", value) is not None, "profiler counters/coordinates/run IDs must be unsigned integers")
    return int(value)


def _optional_counter(value: str) -> int | None:
    return None if value == "" else _counter(value)


def _selected_identity(selection: ProfilerSelection) -> tuple[object, ...]:
    return (
        selection.device,
        selection.core_x,
        selection.core_y,
        selection.risc,
        selection.zone,
        selection.source_file,
        selection.source_line,
    )


def _legacy_profiler_pairs(
    reader: Iterable[list[str]],
    selection: ProfilerSelection,
) -> dict[int, dict[str, _ProfilerMarker]]:
    pairs: dict[int, dict[str, _ProfilerMarker]] = {
        run: {} for run in selection.run_ids
    }
    wanted = _selected_identity(selection)
    for values in reader:
        require(len(values) == len(CSV_HEADER), "malformed profiler row")
        row = dict(zip(CSV_HEADER, (value.strip() for value in values), strict=True))
        run = _counter(row["Run ID"])
        x = _counter(row["core_x"])
        y = _counter(row["core_y"])
        line = _counter(row["source line"])
        timestamp = _counter(row["time[cycles since reset]"])
        _counter(row["timer_id"])
        require(
            _counter(row["stat value"]) == 0,
            "nonzero profiler stat samples are outside the zone subset",
        )
        found = (
            row["PCIe slot"], x, y, row["RISC processor type"],
            row["zone name"], row["source file"], line,
        )
        if found != wanted or run not in pairs:
            continue
        phase_text = row["zone phase"]
        require(phase_text in ("begin", "end"), "unsupported profiler phase")
        phase: Literal["begin", "end"] = (
            "begin" if phase_text == "begin" else "end"
        )
        require(
            phase not in pairs[run],
            "nested/ambiguous repeated zone in one selected run",
        )
        pairs[run][phase] = _ProfilerMarker(phase=phase, timestamp=timestamp)
    return pairs


def _pinned_profiler_pairs(
    reader: Iterable[list[str]],
    selection: ProfilerSelection,
    max_compute_cores: int,
) -> dict[int, dict[str, _ProfilerMarker]]:
    wanted = _selected_identity(selection)
    completed: list[tuple[_ProfilerMarker, _ProfilerMarker]] = []
    opened: _ProfilerMarker | None = None
    for values in reader:
        require(len(values) == len(PINNED_CSV_HEADER), "malformed profiler row")
        row = dict(
            zip(
                PINNED_CSV_HEADER,
                (value.strip() for value in values),
                strict=True,
            )
        )
        x = _counter(row["core_x"])
        y = _counter(row["core_y"])
        line = _counter(row["source line"])
        timer_id = _counter(row["timer_id"])
        timestamp = _counter(row["time[cycles since reset]"])
        data = _counter(row["data"])
        run_host_id = _counter(row["run host ID"])
        trace_id = _optional_counter(row["trace id"])
        trace_id_counter = _optional_counter(row["trace id counter"])
        found = (
            row["PCIe slot"], x, y, row["RISC processor type"],
            row["zone name"], row["source file"], line,
        )
        if found != wanted:
            continue
        marker_type = row["type"]
        require(
            marker_type in ("ZONE_START", "ZONE_END"),
            "unsupported profiler phase",
        )
        phase: Literal["begin", "end"] = (
            "begin" if marker_type == "ZONE_START" else "end"
        )
        integer_fields = (
            ("core_x", x),
            ("core_y", y),
            ("timer_id", timer_id),
            ("data", data),
            ("run_host_id", run_host_id),
            ("source_line", line),
            ("max_compute_cores", max_compute_cores),
        )
        if trace_id is not None:
            integer_fields += (("trace_id", trace_id),)
        if trace_id_counter is not None:
            integer_fields += (("trace_id_counter", trace_id_counter),)
        details = {
            "csv_dialect": "tt_metal_device_profiler_csv_v1_pinned",
            "pcie_slot": row["PCIe slot"],
            "core_x": x,
            "core_y": y,
            "risc_processor_type": row["RISC processor type"],
            "timer_id": timer_id,
            "timestamp_cycles_since_reset": timestamp,
            "data": data,
            "run_host_id": run_host_id,
            "trace_id": trace_id,
            "trace_id_counter": trace_id_counter,
            "zone_name": row["zone name"],
            "type": marker_type,
            "source_line": line,
            "source_file": row["source file"],
            "meta_data": row["meta data"],
            "max_compute_cores": max_compute_cores,
        }
        marker = _ProfilerMarker(
            phase=phase,
            timestamp=timestamp,
            counters=tuple(
                ObservationCounter(
                    name=name,
                    value=value,
                    unit="count",
                    scope="observed",
                )
                for name, value in integer_fields
            ),
            details=details,
            pair_identity=(timer_id, run_host_id, trace_id, trace_id_counter),
        )
        if phase == "begin":
            require(opened is None, "nested/ambiguous repeated selected profiler zone")
            opened = marker
            continue
        require(opened is not None, "selected profiler zone end has no matching begin")
        if opened is None:
            raise ValueError("selected profiler zone end has no matching begin")
        require(
            opened.pair_identity == marker.pair_identity,
            "selected profiler begin/end counter identities disagree",
        )
        completed.append((opened, marker))
        opened = None
    require(opened is None, "missing selected profiler zone end")
    require(
        len(completed) == len(selection.run_ids),
        "selected profiler occurrence count disagrees with declared runs",
    )
    return {
        run: {"begin": pair[0], "end": pair[1]}
        for run, pair in zip(selection.run_ids, completed, strict=True)
    }


def import_reference(path: Path) -> ValidationReference:
    reference = ValidationReference.model_validate_json(path.read_bytes())
    require(reference.provenance.extractor == EXTRACTOR and reference.provenance.extractor_version == EXTRACTOR_VERSION,
            "unsupported named extractor/version")
    raw = read_verified_artifact(path, reference.provenance.raw_artifact)
    if reference.format == "normalized_functional_v1":
        document = obj(json.loads(raw))
        require("source_result_sha256" not in document and "observation_id" not in document,
                "raw functional payload excludes generated identity fields")
        document.update(source_result_sha256=reference.provenance.raw_artifact.sha256,
                        observation_id="reference:" + reference.provenance.raw_artifact.sha256)
        observations = NormalizedObservations.model_validate_json(json.dumps(document))
        statistics = None
    else:
        observations, statistics = _profiler(reference, raw.decode("utf-8"))
    if reference.observations is not None:
        require(reference.observations == observations, "embedded observations disagree with verified raw extraction")
    if reference.sample_statistics is not None:
        require(reference.sample_statistics == statistics, "embedded statistics disagree with raw extraction")
    if reference.conditions.clocks.value is not None:
        declared_clocks = {c.domain_id: c for c in reference.conditions.clocks.value}
        for clock in observations.clocks:
            declared = declared_clocks.get(clock.domain_id)
            if declared is not None and declared.hz.state == "known" and clock.hz.state == "known":
                require(declared.hz.value == clock.hz.value, "observation frequency contradicts declared metadata")
    return ValidationReference.model_validate({**reference.model_dump(), "observations": observations, "sample_statistics": statistics})


def _profiler(reference: ValidationReference, raw: str) -> tuple[NormalizedObservations, SampleStatistics]:
    selection = reference.profiler
    require(selection is not None, "CSV import requires explicit profiler selection")
    if selection is None:
        raise ValueError("CSV import requires selection")
    lines = raw.splitlines()
    require(len(lines) >= 3, "empty profiler capture")
    reader = csv.reader(io.StringIO("\n".join(lines[1:])), skipinitialspace=True)
    header = tuple(value.strip() for value in next(reader))
    require(
        header in (CSV_HEADER, PINNED_CSV_HEADER),
        "unsupported profiler CSV columns/order",
    )
    if header == CSV_HEADER:
        metadata = re.fullmatch(
            r"ARCH:\s*([A-Za-z0-9_]+),\s*CHIP_FREQ\[MHz\]:\s*([0-9]+(?:\.[0-9]+)?)\s*",
            lines[0],
        )
        max_compute_cores = None
    else:
        metadata = re.fullmatch(
            r"ARCH:\s*([A-Za-z0-9_]+),\s*CHIP_FREQ\[MHz\]:\s*([0-9]+),\s*Max Compute Cores:\s*([0-9]+)\s*",
            lines[0],
        )
        max_compute_cores = (
            _counter(metadata.group(3)) if metadata is not None else None
        )
        require(
            max_compute_cores is not None and max_compute_cores > 0,
            "pinned profiler metadata requires positive Max Compute Cores",
        )
    require(metadata is not None, "unsupported profiler metadata header")
    if metadata is None:
        raise ValueError("unsupported metadata")
    architecture, frequency = metadata.group(1), Fraction(metadata.group(2)) * 10**6
    require(frequency > 0, "profiler frequency must be positive")
    conditions = reference.conditions
    if conditions.architecture.value is not None:
        require(conditions.architecture.value == architecture, "architecture contradicts CSV header")
    if conditions.clocks.value is not None:
        clock = next((c for c in conditions.clocks.value if c.domain_id == selection.clock_domain), None)
        require(clock is not None, "selected profiler clock is absent from metadata")
        if clock is not None and clock.hz.value is not None:
            require(Fraction(str(clock.hz.value)) == frequency, "frequency contradicts clock metadata")
    if header == CSV_HEADER:
        pairs = _legacy_profiler_pairs(reader, selection)
    else:
        if max_compute_cores is None:
            raise ValueError("pinned profiler metadata is incomplete")
        pairs = _pinned_profiler_pairs(reader, selection, max_compute_cores)
    samples: list[int] = []
    events: list[ObservationEvent] = []
    for run, pair in pairs.items():
        require(set(pair) == {"begin", "end"}, "missing same-device/core/RISC/zone/run begin/end pair")
        duration = pair["end"].timestamp - pair["begin"].timestamp
        require(duration >= 0, "profiler end precedes begin")
        if run not in selection.warmup_run_ids:
            samples.append(duration)
        for phase in ("begin", "end"):
            marker = pair[phase]
            events.append(ObservationEvent(
                event_id=f"run:{run}:{phase}",
                action=phase,
                subject_id="profiler_zone",
                time=TimePoint(
                    value=marker.timestamp,
                    unit="cycles",
                    clock_domain=selection.clock_domain,
                ),
                counters=marker.counters,
                details=(
                    canonical_record(marker.details)
                    if marker.details is not None
                    else None
                ),
            ))
    ordered = sorted(samples)
    if selection.aggregation == "mean":
        aggregate = Fraction(sum(samples), len(samples))
    elif selection.aggregation == "median":
        middle = len(samples) // 2
        aggregate = Fraction(ordered[middle]) if len(samples) % 2 else Fraction(ordered[middle - 1] + ordered[middle], 2)
    else:
        aggregate = Fraction(samples[0])
    value = aggregate.numerator if aggregate.denominator == 1 else float(aggregate)
    mean = Fraction(sum(samples), len(samples))
    dispersion = float(sum(abs(Fraction(s) - mean) for s in samples) / len(samples))
    statistics = SampleStatistics(sample_count=len(samples), durations_cycles=tuple(samples), minimum_cycles=min(samples),
                                  maximum_cycles=max(samples), mean_absolute_deviation_cycles=dispersion)
    retained_runs = tuple(
        run for run in selection.run_ids if run not in selection.warmup_run_ids
    )
    if len(retained_runs) == 1:
        retained_pair = pairs[retained_runs[0]]
        window_start = retained_pair["begin"].timestamp
        window_end = retained_pair["end"].timestamp
    else:
        window_start, window_end = 0, value
    window = MeasurementWindow(boundary=selection.boundary,
                               start=TimePoint(value=window_start, unit="cycles", clock_domain=selection.clock_domain),
                               end=TimePoint(value=window_end, unit="cycles", clock_domain=selection.clock_domain),
                               excluded_warmups=tuple(str(r) for r in selection.warmup_run_ids), repetitions=len(samples), aggregation=selection.aggregation)
    if conditions.measurement.value is not None:
        declared = conditions.measurement.value
        require((declared.boundary, declared.repetitions, declared.aggregation, declared.excluded_warmups, declared.start.unit, declared.start.clock_domain)
                == (window.boundary, window.repetitions, window.aggregation, window.excluded_warmups, "cycles", selection.clock_domain),
                "profiler selection contradicts measurement metadata")
    interval_identity: MetricIntervalIdentity | None = None
    completion_scope: str = "complete_run"
    profiler_entities = [ObservationEntity(entity_id="profiler_zone", role="worker")]
    try:
        rule = boundary_rule(selection.boundary)
    except ValueError:
        rule = None
    if rule is not None and rule.semantic_scope is not None:
        resource_id = "profiler_resource" if rule.resource_required else None
        if resource_id is not None:
            profiler_entities.append(ObservationEntity(entity_id=resource_id, role="resource"))
        interval_identity = MetricIntervalIdentity(
            semantic_scope=rule.semantic_scope,
            subject_id="profiler_zone",
            resource_id=resource_id,
            samples=tuple(
                IntervalSampleIdentity(
                    repetition_id=str(run),
                    start_event_id=f"run:{run}:begin",
                    end_event_id=f"run:{run}:end",
                )
                for run in selection.run_ids
                if run not in selection.warmup_run_ids
            ),
        )
        completion_scope = "interval"
    from ..configs.schemas.validation import CausalEdge
    observation = NormalizedObservations(
        observation_id="reference:" + content_digest({"raw": reference.provenance.raw_artifact.sha256, "selection": selection.model_dump(mode="json")}),
        source_result_sha256=reference.provenance.raw_artifact.sha256, execution="complete",
        clocks=(ClockDomain(domain_id=selection.clock_domain, hz=Metadata[float](state="known", value=float(frequency))),),
        entities=tuple(profiler_entities), events=tuple(events), effects=(),
        causal_edges=tuple(CausalEdge(before=f"run:{run}:begin", after=f"run:{run}:end") for run in pairs), routes=(), intervals=(),
        metrics=(MetricObservation(metric_id=selection.metric_id, value=value, unit="cycles", clock_domain=selection.clock_domain,
                                   numerator="same-core zone end minus begin", denominator="one retained run" if selection.aggregation == "none" else f"{selection.aggregation} of retained runs",
                                   window=window, completion_scope=completion_scope,
                                   interval=interval_identity),), pending=(), missing=())
    return observation, statistics
