"""Phase-0 component cost-provider contracts with no fabricated costs."""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hardware import ApplicabilityDomain
from .workload import stable_sha256


ComponentId = Literal["M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08"]


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CalibrationScene(FrozenRecord):
    scene_id: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    role: Literal["training", "heldout", "intervention"]


class CalibrationManifest(FrozenRecord):
    schema_version: Literal[1] = 1
    manifest_id: str = Field(min_length=1)
    component_id: ComponentId
    formula_id: str = Field(min_length=1)
    training_scenes: tuple[CalibrationScene, ...]
    heldout_scenes: tuple[CalibrationScene, ...]
    intervention_scenes: tuple[CalibrationScene, ...] = ()
    parameter_dependencies: tuple[str, ...]
    calibration_outputs: tuple[str, ...]
    coefficient_artifact_ref: str | None = None
    applicability: ApplicabilityDomain
    status: Literal["planned", "calibrated", "frozen"]
    source_refs: tuple[str, ...]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self) -> "CalibrationManifest":
        groups = [self.training_scenes, self.heldout_scenes, self.intervention_scenes]
        ids = [[scene.scene_id for scene in group] for group in groups]
        if any(len(group) != len(set(group)) for group in ids):
            raise ValueError("duplicate calibration scene id within a split")
        if set(ids[0]) & set(ids[1]) or set(ids[0]) & set(ids[2]) or set(ids[1]) & set(ids[2]):
            raise ValueError("calibration, heldout and intervention scenes must be disjoint")
        if any(scene.role != "training" for scene in self.training_scenes):
            raise ValueError("training scene role mismatch")
        if any(scene.role != "heldout" for scene in self.heldout_scenes):
            raise ValueError("heldout scene role mismatch")
        if any(scene.role != "intervention" for scene in self.intervention_scenes):
            raise ValueError("intervention scene role mismatch")
        if not self.parameter_dependencies or not self.calibration_outputs or not self.source_refs:
            raise ValueError("manifest dependencies, outputs and sources are required")
        if self.status in ("calibrated", "frozen") and not self.coefficient_artifact_ref:
            raise ValueError("calibrated manifest requires a coefficient artifact reference")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"manifest_sha256"}))
        if expected != self.manifest_sha256:
            raise ValueError("calibration manifest hash mismatch")
        return self


def freeze_calibration_manifest(**kwargs: Any) -> CalibrationManifest:
    payload = dict(kwargs)
    payload.setdefault("schema_version", 1)
    digest = stable_sha256({
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
        for key, value in payload.items()
    })
    return CalibrationManifest(**payload, manifest_sha256=digest)


class WorkItem(FrozenRecord):
    name: str = Field(min_length=1)
    value: int | float
    unit: str = Field(min_length=1)

    @model_validator(mode="after")
    def finite(self) -> "WorkItem":
        if isinstance(self.value, bool) or not math.isfinite(float(self.value)) or self.value < 0:
            raise ValueError("work item value must be finite and non-negative")
        return self


class CostRequest(FrozenRecord):
    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    component_id: ComponentId
    operation: str = Field(min_length=1)
    workload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hardware_parameter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_ids: tuple[str, ...]
    hardware_parameter_paths: tuple[str, ...]
    work: tuple[WorkItem, ...]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self) -> "CostRequest":
        if not self.resource_ids or not self.hardware_parameter_paths or not self.work:
            raise ValueError("cost request requires resources, parameters and work")
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("cost request resource ids must be unique")
        if len(self.hardware_parameter_paths) != len(set(self.hardware_parameter_paths)):
            raise ValueError("cost request parameter paths must be unique")
        names = [item.name for item in self.work]
        if len(names) != len(set(names)):
            raise ValueError("cost request work item names must be unique")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"request_sha256"}))
        if expected != self.request_sha256:
            raise ValueError("cost request hash mismatch")
        return self


def freeze_cost_request(**kwargs: Any) -> CostRequest:
    payload = dict(kwargs)
    payload.setdefault("schema_version", 1)
    digest = stable_sha256({
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else list(value) if isinstance(value, tuple) else value)
        for key, value in payload.items()
    })
    return CostRequest(**payload, request_sha256=digest)


class CostQuote(FrozenRecord):
    schema_version: Literal[1] = 1
    quote_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_id: ComponentId
    status: Literal["calibrated", "uncalibrated", "unavailable"]
    duration_value: float | None = None
    duration_unit: Literal["cycles", "seconds", "reported_units"] | None = None
    calibration_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    applicability: ApplicabilityDomain
    uncertainty_lower: float | None = None
    uncertainty_upper: float | None = None
    source_refs: tuple[str, ...]
    missing_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def quote_contract(self) -> "CostQuote":
        for value in (self.duration_value, self.uncertainty_lower, self.uncertainty_upper):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("cost quote values must be finite and non-negative")
        if not self.source_refs:
            raise ValueError("cost quote requires source references")
        if self.status == "calibrated":
            if self.duration_value is None or self.duration_unit is None or self.calibration_manifest_sha256 is None:
                raise ValueError("calibrated quote requires value, unit and manifest")
            if self.missing_fields:
                raise ValueError("calibrated quote cannot list missing fields")
        elif self.status == "unavailable":
            if self.duration_value is not None or self.duration_unit is not None or not self.missing_fields:
                raise ValueError("unavailable quote must have no duration and exact missing fields")
        elif self.duration_value is not None or self.duration_unit is not None or self.calibration_manifest_sha256 is not None:
            raise ValueError("uncalibrated quote must have null duration, unit and calibration manifest")
        return self


class CostProvider(Protocol):
    def quote(self, request: CostRequest) -> CostQuote: ...


__all__ = [
    "CalibrationManifest",
    "CalibrationScene",
    "ComponentId",
    "CostProvider",
    "CostQuote",
    "CostRequest",
    "WorkItem",
    "freeze_calibration_manifest",
    "freeze_cost_request",
]
