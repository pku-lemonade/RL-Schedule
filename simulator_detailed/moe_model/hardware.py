"""Phase-0 physical hardware parameter and mutation contracts."""

from __future__ import annotations

import math
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workload import stable_sha256


CalibrationStatus = Literal["calibrated", "uncalibrated", "unknown"]
MutationOperator = Literal["set", "scale", "add"]
BindingArtifactId = Literal[
    "pe_engines",
    "gm_nodes",
    "gsram",
    "data_noc_links",
    "data_noc_routes",
    "multicast_route_trees",
    "write_sum_mapping",
    "adalink_rank_map",
]

REQUIRED_BINDING_ARTIFACTS: tuple[str, ...] = (
    "pe_engines",
    "gm_nodes",
    "gsram",
    "data_noc_links",
    "data_noc_routes",
    "multicast_route_trees",
    "write_sum_mapping",
    "adalink_rank_map",
)


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ApplicabilityDomain(FrozenRecord):
    description: str = Field(min_length=1)
    lower_bound: float | None = None
    upper_bound: float | None = None
    categories: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounds(self) -> "ApplicabilityDomain":
        for value in (self.lower_bound, self.upper_bound):
            if value is not None and not math.isfinite(value):
                raise ValueError("applicability bounds must be finite")
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise ValueError("applicability lower bound exceeds upper bound")
        return self


class ParameterValue(FrozenRecord):
    value: int | float | None
    unit: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    calibration_status: CalibrationStatus
    applicability: ApplicabilityDomain
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def value_contract(self) -> "ParameterValue":
        if self.value is not None and (isinstance(self.value, bool) or not math.isfinite(float(self.value))):
            raise ValueError("parameter value must be finite numeric or null")
        if self.minimum is not None and not math.isfinite(self.minimum):
            raise ValueError("minimum must be finite")
        if self.maximum is not None and not math.isfinite(self.maximum):
            raise ValueError("maximum must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.value is not None:
            numeric = float(self.value)
            if self.minimum is not None and numeric < self.minimum:
                raise ValueError("parameter value is below minimum")
            if self.maximum is not None and numeric > self.maximum:
                raise ValueError("parameter value is above maximum")
        if self.calibration_status == "calibrated" and self.value is None:
            raise ValueError("calibrated parameter cannot have null value")
        return self


class HardwareParameter(FrozenRecord):
    path: str = Field(min_length=1, pattern=r"^\S+$")
    resource_id: str = Field(min_length=1, pattern=r"^\S+$")
    component: str = Field(min_length=1)
    quantity: ParameterValue


class BindingArtifact(FrozenRecord):
    artifact_id: BindingArtifactId
    status: Literal["available", "partial", "missing"]
    artifact_ref: str | None = None
    available_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    workload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def availability(self) -> "BindingArtifact":
        if not self.source_refs:
            raise ValueError("binding artifact requires source references")
        if self.status == "available":
            if self.artifact_ref is None or self.missing_fields:
                raise ValueError("available binding artifact requires a reference and no missing fields")
        elif self.status == "partial":
            if not self.available_fields or not self.missing_fields:
                raise ValueError("partial binding artifact requires available and missing fields")
        elif not self.missing_fields:
            raise ValueError("missing binding artifact requires exact missing fields")
        return self


class TopologyBindingInventory(FrozenRecord):
    schema_version: Literal[1] = 1
    inventory_id: str = Field(min_length=1)
    pe_count: int = Field(gt=0)
    gm_rdma_count: int = Field(ge=0)
    gm_wdma_count: int = Field(ge=0)
    gsram_present: bool
    artifacts: tuple[BindingArtifact, ...]
    source_refs: tuple[str, ...]
    complete_for_real_moe: bool
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def completeness(self) -> "TopologyBindingInventory":
        ids = [item.artifact_id for item in self.artifacts]
        if len(ids) != len(set(ids)) or set(ids) != set(REQUIRED_BINDING_ARTIFACTS):
            raise ValueError("topology binding inventory must cover each required artifact exactly once")
        inferred = all(item.status == "available" for item in self.artifacts)
        if self.complete_for_real_moe != inferred:
            raise ValueError("complete_for_real_moe disagrees with artifact statuses")
        if not self.source_refs:
            raise ValueError("topology binding inventory requires source references")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"inventory_sha256"}))
        if expected != self.inventory_sha256:
            raise ValueError("topology binding inventory hash mismatch")
        return self


class HardwareMutation(FrozenRecord):
    mutation_id: str = Field(min_length=1, pattern=r"^\S+$")
    path: str = Field(min_length=1, pattern=r"^\S+$")
    operator: MutationOperator
    operand: float
    unit: str | None = None

    @model_validator(mode="after")
    def finite_operand(self) -> "HardwareMutation":
        if not math.isfinite(self.operand):
            raise ValueError("mutation operand must be finite")
        if self.operator == "scale" and self.operand <= 0:
            raise ValueError("scale operand must be positive")
        return self


class MutationScope(FrozenRecord):
    scope_id: str = Field(min_length=1)
    allowed_paths: tuple[str, ...]
    allowed_coupled_path_sets: tuple[tuple[str, ...], ...] = ()
    allow_multiple_independent: bool = False

    @model_validator(mode="after")
    def unique_paths(self) -> "MutationScope":
        if not self.allowed_paths or len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("allowed_paths must be non-empty and unique")
        known = set(self.allowed_paths)
        for group in self.allowed_coupled_path_sets:
            if len(group) < 2 or len(set(group)) != len(group) or not set(group) <= known:
                raise ValueError("invalid coupled mutation path set")
        return self


class HardwareParameterSet(FrozenRecord):
    schema_version: Literal[1] = 1
    architecture_id: str = Field(min_length=1)
    clock_domain: str = Field(min_length=1)
    parameters: tuple[HardwareParameter, ...]
    source_refs: tuple[str, ...]
    parent_parameter_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mutations: tuple[HardwareMutation, ...] = ()
    parameter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self) -> "HardwareParameterSet":
        paths = [item.path for item in self.parameters]
        if len(paths) != len(set(paths)):
            raise ValueError("hardware parameter paths must be unique")
        if not self.source_refs or any(not ref for ref in self.source_refs):
            raise ValueError("hardware parameter set requires source references")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"parameter_sha256"}))
        if expected != self.parameter_sha256:
            raise ValueError("hardware parameter hash mismatch")
        return self

    def by_path(self) -> dict[str, HardwareParameter]:
        return {item.path: item for item in self.parameters}


def freeze_hardware_parameters(
    architecture_id: str,
    clock_domain: str,
    parameters: Sequence[HardwareParameter],
    source_refs: Sequence[str],
    *,
    parent_parameter_sha256: str | None = None,
    mutations: Sequence[HardwareMutation] = (),
) -> HardwareParameterSet:
    data: dict[str, Any] = {
        "schema_version": 1,
        "architecture_id": architecture_id,
        "clock_domain": clock_domain,
        "parameters": tuple(sorted(parameters, key=lambda item: item.path)),
        "source_refs": tuple(source_refs),
        "parent_parameter_sha256": parent_parameter_sha256,
        "mutations": tuple(mutations),
    }
    json_shape = {
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else list(value) if isinstance(value, tuple) else value)
        for key, value in data.items()
    }
    return HardwareParameterSet(**data, parameter_sha256=stable_sha256(json_shape))


def freeze_topology_binding_inventory(
    inventory_id: str,
    pe_count: int,
    gm_rdma_count: int,
    gm_wdma_count: int,
    gsram_present: bool,
    artifacts: Sequence[BindingArtifact],
    source_refs: Sequence[str],
) -> TopologyBindingInventory:
    rows = tuple(sorted(artifacts, key=lambda item: item.artifact_id))
    data: dict[str, Any] = {
        "schema_version": 1,
        "inventory_id": inventory_id,
        "pe_count": pe_count,
        "gm_rdma_count": gm_rdma_count,
        "gm_wdma_count": gm_wdma_count,
        "gsram_present": gsram_present,
        "artifacts": rows,
        "source_refs": tuple(source_refs),
        "complete_for_real_moe": all(item.status == "available" for item in rows),
    }
    json_shape = {
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else list(value) if isinstance(value, tuple) else value)
        for key, value in data.items()
    }
    return TopologyBindingInventory(**data, inventory_sha256=stable_sha256(json_shape))


def apply_mutations(
    base: HardwareParameterSet,
    mutations: Sequence[HardwareMutation],
    scope: MutationScope,
) -> HardwareParameterSet:
    rows = tuple(mutations)
    if not rows:
        raise ValueError("at least one mutation is required")
    paths = [row.path for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError("a parameter path can be mutated at most once per case")
    if not set(paths) <= set(scope.allowed_paths):
        raise ValueError("mutation path is outside the declared scope")
    if len(paths) > 1 and not scope.allow_multiple_independent:
        selected = frozenset(paths)
        if selected not in {frozenset(group) for group in scope.allowed_coupled_path_sets}:
            raise ValueError("multi-parameter mutation is not a declared coupled set")
    by_path = base.by_path()
    updated = dict(by_path)
    for mutation in rows:
        if mutation.path not in by_path:
            raise ValueError("mutation path does not exist in baseline")
        parameter = by_path[mutation.path]
        quantity = parameter.quantity
        if quantity.value is None:
            raise ValueError("cannot mutate an unbound parameter")
        if mutation.unit is not None and mutation.unit != quantity.unit:
            raise ValueError("mutation unit does not match parameter unit")
        old = float(quantity.value)
        if mutation.operator == "set":
            new = mutation.operand
        elif mutation.operator == "scale":
            new = old * mutation.operand
        else:
            new = old + mutation.operand
        if quantity.minimum is not None and new < quantity.minimum:
            raise ValueError("mutated value is below minimum")
        if quantity.maximum is not None and new > quantity.maximum:
            raise ValueError("mutated value is above maximum")
        new_value: int | float = int(new) if isinstance(quantity.value, int) and float(new).is_integer() else new
        updated[mutation.path] = parameter.model_copy(update={
            "quantity": quantity.model_copy(update={"value": new_value}),
        })
    return freeze_hardware_parameters(
        base.architecture_id,
        base.clock_domain,
        tuple(updated.values()),
        base.source_refs,
        parent_parameter_sha256=base.parameter_sha256,
        mutations=rows,
    )


__all__ = [
    "ApplicabilityDomain",
    "BindingArtifact",
    "HardwareMutation",
    "HardwareParameter",
    "HardwareParameterSet",
    "MutationScope",
    "ParameterValue",
    "REQUIRED_BINDING_ARTIFACTS",
    "TopologyBindingInventory",
    "apply_mutations",
    "freeze_hardware_parameters",
    "freeze_topology_binding_inventory",
]
