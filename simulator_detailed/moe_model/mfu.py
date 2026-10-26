"""Formal Phase-0 MFU and utilization contracts.

No peak value is selected by default.  Full-decode MFU returns an explicit
incomplete result when the non-MoE baseline or peak convention is missing; it
never substitutes a MoE-local metric.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workload import stable_sha256


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TimeBoundary(FrozenRecord):
    boundary_id: str = Field(min_length=1)
    start_event: str = Field(min_length=1)
    end_event: str = Field(min_length=1)
    scope: Literal["moe_local", "full_decode", "engine_active"]
    elapsed_seconds: float | None = None
    elapsed_cycles: float | None = None
    clock_hz: float | None = None
    includes_adalink: bool
    device_count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def timing(self) -> "TimeBoundary":
        for value in (self.elapsed_seconds, self.elapsed_cycles, self.clock_hz):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("time boundary values must be finite and positive")
        if self.elapsed_cycles is not None and self.clock_hz is None and self.elapsed_seconds is None:
            raise ValueError("cycles require clock_hz or an independently supplied elapsed_seconds")
        if self.elapsed_seconds is not None and self.elapsed_cycles is not None:
            if self.clock_hz is None or not math.isclose(self.elapsed_seconds, self.elapsed_cycles / self.clock_hz, rel_tol=1e-9):
                raise ValueError("seconds/cycles/clock unit conversion disagrees")
        return self

    def seconds(self) -> float | None:
        if self.elapsed_seconds is not None:
            return self.elapsed_seconds
        if self.elapsed_cycles is not None and self.clock_hz is not None:
            return self.elapsed_cycles / self.clock_hz
        return None


class PeakConvention(FrozenRecord):
    peak_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    peak_flops_per_second_per_device: float | None = None
    precision: str = Field(min_length=1)
    convention: Literal[
        "organization_standard",
        "advertised_dense_bf16",
        "precision_specific_engine",
        "simulator_modeled",
    ]
    source_refs: tuple[str, ...]

    @model_validator(mode="after")
    def peak(self) -> "PeakConvention":
        if self.peak_flops_per_second_per_device is not None and (
            not math.isfinite(self.peak_flops_per_second_per_device)
            or self.peak_flops_per_second_per_device <= 0
        ):
            raise ValueError("peak FLOPs/s must be finite and positive")
        if not self.source_refs:
            raise ValueError("peak convention requires source references")
        return self


class MetricResult(FrozenRecord):
    metric_name: Literal[
        "useful_model_mfu",
        "issued_engine_utilization",
        "moe_local_mfu",
        "full_decode_mfu",
    ]
    status: Literal["complete", "incomplete"]
    value: float | None
    unit: Literal["ratio"] = "ratio"
    numerator_name: str = Field(min_length=1)
    numerator_value: float | None
    numerator_unit: str = Field(min_length=1)
    denominator_value: float | None
    denominator_unit: str = Field(min_length=1)
    peak_id: str | None
    device_count: int | None = Field(default=None, gt=0)
    boundary_id: str | None
    time_seconds: float | None
    missing_fields: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistency(self) -> "MetricResult":
        numeric = (self.value, self.numerator_value, self.denominator_value, self.time_seconds)
        if any(value is not None and (not math.isfinite(value) or value < 0) for value in numeric):
            raise ValueError("metric values must be finite and non-negative")
        if self.status == "complete":
            if any(value is None for value in numeric) or self.peak_id is None or self.device_count is None or self.boundary_id is None:
                raise ValueError("complete metric is missing required values")
            if self.missing_fields:
                raise ValueError("complete metric cannot list missing fields")
        elif not self.missing_fields or self.value is not None:
            raise ValueError("incomplete metric requires missing fields and null value")
        return self


class EngineUtilizationRequest(FrozenRecord):
    engine_id: str = Field(min_length=1)
    precision: str = Field(min_length=1)
    issued_ops: float = Field(ge=0)
    peak_ops_per_second_per_engine: float = Field(gt=0)
    engine_count: int = Field(gt=0)
    active_boundary: TimeBoundary
    peak_id: str = Field(min_length=1)


class DecodeBaselineManifest(FrozenRecord):
    schema_version: Literal[1] = 1
    manifest_id: str = Field(min_length=1)
    workload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    moe_boundary: TimeBoundary | None
    full_decode_boundary: TimeBoundary | None
    moe_useful_flops: int | None = Field(default=None, ge=0)
    non_moe_useful_flops: int | None = Field(default=None, ge=0)
    full_decode_useful_flops: int | None = Field(default=None, ge=0)
    non_moe_elapsed_seconds: float | None = None
    moe_non_moe_overlap_seconds: float | None = None
    device_count: int | None = Field(default=None, gt=0)
    peak_candidates: tuple[PeakConvention, ...]
    selected_peak_id: str | None = None
    source_refs: tuple[str, ...]
    completeness: Literal["complete", "incomplete"]
    missing_fields: tuple[str, ...]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self) -> "DecodeBaselineManifest":
        for value in (self.non_moe_elapsed_seconds, self.moe_non_moe_overlap_seconds):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("decode baseline times must be finite and non-negative")
        ids = [peak.peak_id for peak in self.peak_candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("peak candidate ids must be unique")
        required = (
            self.workload_sha256,
            self.moe_boundary,
            self.full_decode_boundary,
            self.moe_useful_flops,
            self.non_moe_useful_flops,
            self.full_decode_useful_flops,
            self.non_moe_elapsed_seconds,
            self.moe_non_moe_overlap_seconds,
            self.device_count,
            self.selected_peak_id,
        )
        if self.completeness == "complete":
            if any(value is None for value in required) or self.missing_fields:
                raise ValueError("complete decode manifest is missing required fields")
            selected = next((peak for peak in self.peak_candidates if peak.peak_id == self.selected_peak_id), None)
            if selected is None or selected.peak_flops_per_second_per_device is None:
                raise ValueError("selected peak is absent or unbound")
            if self.full_decode_useful_flops != self.moe_useful_flops + self.non_moe_useful_flops:
                raise ValueError("full decode useful FLOPs do not equal MoE plus non-MoE")
            if self.moe_boundary.scope != "moe_local" or self.full_decode_boundary.scope != "full_decode":
                raise ValueError("decode manifest boundary scope mismatch")
            moe_time, full_time = self.moe_boundary.seconds(), self.full_decode_boundary.seconds()
            if moe_time is None or full_time is None:
                raise ValueError("complete baseline requires MoE and full decode time")
            overlap = self.moe_non_moe_overlap_seconds
            if overlap > min(moe_time, self.non_moe_elapsed_seconds):
                raise ValueError("overlap exceeds either component duration")
            if not math.isclose(full_time, moe_time + self.non_moe_elapsed_seconds - overlap, rel_tol=1e-9):
                raise ValueError("full decode time does not equal MoE plus non-MoE minus overlap")
            for boundary in (self.moe_boundary, self.full_decode_boundary):
                if boundary.device_count is not None and boundary.device_count != self.device_count:
                    raise ValueError("boundary device count disagrees with baseline")
        elif not self.missing_fields:
            raise ValueError("incomplete decode manifest must list exact missing fields")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"manifest_sha256"}))
        if expected != self.manifest_sha256:
            raise ValueError("decode baseline manifest hash mismatch")
        return self


def freeze_decode_baseline(**kwargs: Any) -> DecodeBaselineManifest:
    payload = dict(kwargs)
    payload.setdefault("schema_version", 1)
    digest = stable_sha256({
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
        for key, value in payload.items()
    })
    return DecodeBaselineManifest(**payload, manifest_sha256=digest)


def _mfu(
    metric_name: Literal["useful_model_mfu", "moe_local_mfu"],
    useful_flops: int,
    boundary: TimeBoundary,
    peak: PeakConvention,
    device_count: int,
) -> MetricResult:
    seconds = boundary.seconds()
    missing = []
    if seconds is None:
        missing.append("boundary.elapsed_seconds_or_cycles_and_clock")
    if peak.peak_flops_per_second_per_device is None:
        missing.append("peak_flops_per_second_per_device")
    if missing:
        return MetricResult(
            metric_name=metric_name,
            status="incomplete",
            value=None,
            numerator_name="useful_model_flops",
            numerator_value=float(useful_flops),
            numerator_unit="FLOP",
            denominator_value=None,
            denominator_unit="FLOP",
            peak_id=peak.peak_id,
            device_count=device_count,
            boundary_id=boundary.boundary_id,
            time_seconds=seconds,
            missing_fields=tuple(missing),
        )
    denominator = peak.peak_flops_per_second_per_device * device_count * seconds
    return MetricResult(
        metric_name=metric_name,
        status="complete",
        value=useful_flops / denominator,
        numerator_name="useful_model_flops",
        numerator_value=float(useful_flops),
        numerator_unit="FLOP",
        denominator_value=denominator,
        denominator_unit="FLOP",
        peak_id=peak.peak_id,
        device_count=device_count,
        boundary_id=boundary.boundary_id,
        time_seconds=seconds,
    )


def compute_useful_model_mfu(useful_flops: int, boundary: TimeBoundary, peak: PeakConvention, device_count: int) -> MetricResult:
    if useful_flops < 0 or device_count <= 0:
        raise ValueError("useful FLOPs must be non-negative and device count positive")
    return _mfu("useful_model_mfu", useful_flops, boundary, peak, device_count)


def compute_moe_local_mfu(useful_flops: int, boundary: TimeBoundary, peak: PeakConvention, device_count: int) -> MetricResult:
    if boundary.scope != "moe_local":
        raise ValueError("MoE-local MFU requires a moe_local boundary")
    if useful_flops < 0 or device_count <= 0:
        raise ValueError("useful FLOPs must be non-negative and device count positive")
    return _mfu("moe_local_mfu", useful_flops, boundary, peak, device_count)


def compute_issued_engine_utilization(request: EngineUtilizationRequest) -> MetricResult:
    if request.active_boundary.scope != "engine_active":
        raise ValueError("issued utilization requires an engine_active boundary")
    seconds = request.active_boundary.seconds()
    if seconds is None:
        return MetricResult(
            metric_name="issued_engine_utilization",
            status="incomplete",
            value=None,
            numerator_name=f"issued_{request.precision}_ops",
            numerator_value=request.issued_ops,
            numerator_unit="op",
            denominator_value=None,
            denominator_unit="op",
            peak_id=request.peak_id,
            device_count=request.engine_count,
            boundary_id=request.active_boundary.boundary_id,
            time_seconds=None,
            missing_fields=("active_boundary.elapsed_seconds_or_cycles_and_clock",),
        )
    denominator = request.peak_ops_per_second_per_engine * request.engine_count * seconds
    return MetricResult(
        metric_name="issued_engine_utilization",
        status="complete",
        value=request.issued_ops / denominator,
        numerator_name=f"issued_{request.precision}_ops",
        numerator_value=request.issued_ops,
        numerator_unit="op",
        denominator_value=denominator,
        denominator_unit="op",
        peak_id=request.peak_id,
        device_count=request.engine_count,
        boundary_id=request.active_boundary.boundary_id,
        time_seconds=seconds,
    )


def compute_full_decode_mfu(manifest: DecodeBaselineManifest) -> MetricResult:
    if manifest.completeness != "complete":
        return MetricResult(
            metric_name="full_decode_mfu",
            status="incomplete",
            value=None,
            numerator_name="full_decode_useful_flops",
            numerator_value=None if manifest.full_decode_useful_flops is None else float(manifest.full_decode_useful_flops),
            numerator_unit="FLOP",
            denominator_value=None,
            denominator_unit="FLOP",
            peak_id=manifest.selected_peak_id,
            device_count=manifest.device_count,
            boundary_id=None if manifest.full_decode_boundary is None else manifest.full_decode_boundary.boundary_id,
            time_seconds=None if manifest.full_decode_boundary is None else manifest.full_decode_boundary.seconds(),
            missing_fields=manifest.missing_fields,
            notes=("MoE-local MFU is not substituted for full decode MFU",),
        )
    peak = next(item for item in manifest.peak_candidates if item.peak_id == manifest.selected_peak_id)
    boundary = manifest.full_decode_boundary
    seconds = boundary.seconds()
    denominator = peak.peak_flops_per_second_per_device * manifest.device_count * seconds
    return MetricResult(
        metric_name="full_decode_mfu",
        status="complete",
        value=manifest.full_decode_useful_flops / denominator,
        numerator_name="full_decode_useful_flops",
        numerator_value=float(manifest.full_decode_useful_flops),
        numerator_unit="FLOP",
        denominator_value=denominator,
        denominator_unit="FLOP",
        peak_id=peak.peak_id,
        device_count=manifest.device_count,
        boundary_id=boundary.boundary_id,
        time_seconds=seconds,
    )


__all__ = [
    "DecodeBaselineManifest",
    "EngineUtilizationRequest",
    "MetricResult",
    "PeakConvention",
    "TimeBoundary",
    "compute_full_decode_mfu",
    "compute_issued_engine_utilization",
    "compute_moe_local_mfu",
    "compute_useful_model_mfu",
    "freeze_decode_baseline",
]
