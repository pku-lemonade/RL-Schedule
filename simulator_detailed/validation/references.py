"""Offline, hash-verified functional JSON and a pinned profiler CSV subset."""

import csv
import io
import json
import re
from fractions import Fraction
from pathlib import Path

from ..configs.schemas.validation import (
    ClockDomain,
    IntervalSampleIdentity,
    MeasurementWindow,
    Metadata,
    MetricIntervalIdentity,
    MetricObservation,
    NormalizedObservations,
    ObservationEntity,
    ObservationEvent,
    SampleStatistics,
    TimePoint,
    ValidationReference,
)
from .data import obj, require
from .identity import content_digest, read_verified_artifact
from .intervals import boundary_rule

EXTRACTOR = "wormhole_reference_import"
EXTRACTOR_VERSION = "1"
CSV_HEADER = ("PCIe slot", "core_x", "core_y", "RISC processor type", "timer_id", "time[cycles since reset]",
              "stat value", "Run ID", "zone name", "zone phase", "source line", "source file")


def _counter(value: str) -> int:
    require(re.fullmatch(r"[0-9]+", value) is not None, "profiler counters/coordinates/run IDs must be unsigned integers")
    return int(value)


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
    metadata = re.fullmatch(r"ARCH:\s*([A-Za-z0-9_]+),\s*CHIP_FREQ\[MHz\]:\s*([0-9]+(?:\.[0-9]+)?)\s*", lines[0])
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
    reader = csv.reader(io.StringIO("\n".join(lines[1:])), skipinitialspace=True)
    header = tuple(v.strip() for v in next(reader))
    require(header == CSV_HEADER, "unsupported profiler CSV columns/order")
    pairs: dict[int, dict[str, int]] = {run: {} for run in selection.run_ids}
    for values in reader:
        require(len(values) == len(CSV_HEADER), "malformed profiler row")
        row = dict(zip(CSV_HEADER, (v.strip() for v in values), strict=True))
        phase = row["zone phase"]
        require(phase in ("begin", "end"), "unsupported profiler phase")
        run = _counter(row["Run ID"])
        x, y, line = _counter(row["core_x"]), _counter(row["core_y"]), _counter(row["source line"])
        timestamp = _counter(row["time[cycles since reset]"])
        _counter(row["timer_id"])
        require(_counter(row["stat value"]) == 0, "nonzero profiler stat samples are outside the zone subset")
        wanted = (selection.device, selection.core_x, selection.core_y, selection.risc, selection.zone, selection.source_file, selection.source_line)
        found = (row["PCIe slot"], x, y, row["RISC processor type"], row["zone name"], row["source file"], line)
        if found != wanted or run not in pairs:
            continue
        require(phase not in pairs[run], "nested/ambiguous repeated zone in one selected run")
        pairs[run][phase] = timestamp
    samples: list[int] = []
    events: list[ObservationEvent] = []
    for run, pair in pairs.items():
        require(set(pair) == {"begin", "end"}, "missing same-device/core/RISC/zone/run begin/end pair")
        duration = pair["end"] - pair["begin"]
        require(duration >= 0, "profiler end precedes begin")
        if run not in selection.warmup_run_ids:
            samples.append(duration)
        for phase in ("begin", "end"):
            events.append(ObservationEvent(event_id=f"run:{run}:{phase}", action=phase, subject_id="profiler_zone",
                                          time=TimePoint(value=pair[phase], unit="cycles", clock_domain=selection.clock_domain)))
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
    window = MeasurementWindow(boundary=selection.boundary,
                               start=TimePoint(value=0, unit="cycles", clock_domain=selection.clock_domain),
                               end=TimePoint(value=value, unit="cycles", clock_domain=selection.clock_domain),
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
