"""Version-one hardware inventory, deliberately independent of runtime defaults."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Index = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Number = StrictInt | StrictFloat
Unit = Literal[
    "bytes", "Hz", "cycles", "bytes_per_cycle", "bytes_per_second", "seconds", "count"
]
EvidenceStatus = Literal["documented", "derived", "assumed", "calibrated"]
TileRole = Literal["worker", "memory", "ethernet", "pcie", "management", "transit"]


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Source(ProfileModel):
    title: Text
    url: Annotated[str, Field(pattern=r"^https?://\S+$")]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")] | None = None
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    accessed_on: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    applicability: Text

    @model_validator(mode="after")
    def pinned(self) -> Self:
        if self.revision is None and self.sha256 is None:
            raise ValueError(
                "source needs an immutable repository revision or SHA-256 snapshot"
            )
        return self


class Measurement(ProfileModel):
    artifact: Text
    method: Text
    conditions: Text


class Evidence(ProfileModel):
    status: EvidenceStatus
    source_ids: list[Identifier] = Field(default_factory=list)
    locator: Text
    conditions: Text
    rationale: Text | None = None
    measurement: Measurement | None = None

    @model_validator(mode="after")
    def supported_claim(self) -> Self:
        unique(self.source_ids, "evidence source_ids")
        if self.status == "documented" and not self.source_ids:
            raise ValueError("documented evidence needs source_ids")
        if self.status == "assumed" and self.rationale is None:
            raise ValueError("assumed evidence needs a rationale")
        if self.status == "calibrated" and self.measurement is None:
            raise ValueError("calibrated evidence needs measurement metadata")
        return self


class OverrideRecord(ProfileModel):
    previous_value: Number
    previous_evidence: Evidence
    reason: Text


class QuantityBase(ProfileModel):
    unit: Unit
    clock_parameter: Identifier | None = None
    evidence: Evidence

    @model_validator(mode="after")
    def clock_dimension(self) -> Self:
        if (self.unit in {"cycles", "bytes_per_cycle"}) != (
            self.clock_parameter is not None
        ):
            raise ValueError("only cycle-based quantities must carry clock_parameter")
        return self


class LiteralQuantity(QuantityBase):
    kind: Literal["literal"]
    value: Number
    overrides: list[OverrideRecord] = Field(default_factory=list[OverrideRecord])

    @model_validator(mode="after")
    def literal_constraints(self) -> Self:
        if self.evidence.status == "derived":
            raise ValueError("derived evidence requires a derived quantity recipe")
        for value in [self.value, *(item.previous_value for item in self.overrides)]:
            check_value(value, self.unit)
        return self


class DerivedQuantity(QuantityBase):
    kind: Literal["derived"]
    operation: Literal["rate_from_clock", "duration_from_cycles", "sum_bytes"]
    operands: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def derived_evidence(self) -> Self:
        if self.evidence.status != "derived":
            raise ValueError("derived quantities must retain derived evidence")
        return self


Quantity = Annotated[LiteralQuantity | DerivedQuantity, Field(discriminator="kind")]


def check_value(value: float, unit: Unit) -> None:
    # Avoid coercion of capacities/widths and overflow in derived floating rates.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("quantity must be finite")
    if unit in {"bytes", "count", "bytes_per_cycle"} and type(value) is not int:
        raise ValueError(f"{unit} requires an integer value")
    if value < 0 or (unit in {"Hz", "bytes_per_cycle"} and value == 0):
        raise ValueError(f"invalid nonpositive value for {unit}")


def resolve_parameters(parameters: dict[str, Quantity]) -> dict[str, int | float]:
    """Validate the dependency graph and evaluate only the three typed recipes."""
    values: dict[str, int | float] = {}
    active: set[str] = set()

    def visit(key: str) -> int | float:
        if key in values:
            return values[key]
        if key in active:
            raise ValueError(f"parameters.{key}: cyclic dependency")
        if key not in parameters:
            raise ValueError(f"parameters.{key}: unknown dependency")
        active.add(key)
        item = parameters[key]
        if item.clock_parameter is not None:
            visit(item.clock_parameter)
            if parameters[item.clock_parameter].unit != "Hz":
                raise ValueError(f"parameters.{key}: clock_parameter must reference Hz")
        if isinstance(item, LiteralQuantity):
            value = item.value
        else:
            operands = [visit(operand) for operand in item.operands]
            units = [parameters[operand].unit for operand in item.operands]
            if item.operation == "sum_bytes":
                if item.unit != "bytes" or any(unit != "bytes" for unit in units):
                    raise ValueError(
                        f"parameters.{key}: sum_bytes needs bytes operands/output"
                    )
                value = sum(operands)
            else:
                input_unit, output_unit = (
                    ("bytes_per_cycle", "bytes_per_second")
                    if item.operation == "rate_from_clock"
                    else ("cycles", "seconds")
                )
                if units != [input_unit, "Hz"] or item.unit != output_unit:
                    raise ValueError(f"parameters.{key}: incompatible recipe units")
                if parameters[item.operands[0]].clock_parameter != item.operands[1]:
                    raise ValueError(f"parameters.{key}: incompatible clock references")
                try:
                    value = (
                        operands[0] * operands[1]
                        if item.operation == "rate_from_clock"
                        else operands[0] / operands[1]
                    )
                except OverflowError as exc:
                    raise ValueError(
                        f"parameters.{key}: derived value overflow"
                    ) from exc
        check_value(value, item.unit)
        active.remove(key)
        values[key] = value
        return value

    for key in parameters:
        visit(key)
    return values


def unique(values: list[str] | list[int] | list[tuple[int, int]], label: str) -> None:
    seen: set[str | int | tuple[int, int]] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{label}: duplicate {value}")
        seen.add(value)


class Board(ProfileModel):
    product: Text
    asic_count: PositiveInt
    selected_asic_index: Index

    @model_validator(mode="after")
    def selected(self) -> Self:
        if self.selected_asic_index >= self.asic_count:
            raise ValueError("selected_asic_index outside board asic_count")
        return self


class Extent(ProfileModel):
    width: PositiveInt
    height: PositiveInt


class Coordinate(ProfileModel):
    x: Index
    y: Index

    def pair(self) -> tuple[int, int]:
        return self.x, self.y


class Tile(Coordinate):
    tile_id: Identifier
    role: TileRole
    evidence: Evidence | None = None


def check_coordinates(
    coordinates: list[Coordinate], extent: Extent, label: str
) -> None:
    unique([coord.pair() for coord in coordinates], label)
    for coord in coordinates:
        if coord.x >= extent.width or coord.y >= extent.height:
            raise ValueError(f"{label}: coordinate {coord.pair()} outside extent")
    if len(coordinates) != extent.width * extent.height:
        raise ValueError(f"{label}: coordinates must cover the complete extent")


class Layout(ProfileModel):
    extent: Extent
    tiles: list[Tile] = Field(min_length=1)
    evidence: Evidence

    @model_validator(mode="after")
    def inventory(self) -> Self:
        unique([tile.tile_id for tile in self.tiles], "layout.tiles")
        check_coordinates(list(self.tiles), self.extent, "layout.tiles")
        return self


class LogicalWorker(ProfileModel):
    worker_index: Index
    logical_x: Index
    logical_y: Index
    tile_id: Identifier


class WorkerSelection(ProfileModel):
    enabled_worker_ids: list[Identifier]
    logical_workers: list[LogicalWorker]
    evidence: Evidence

    @model_validator(mode="after")
    def bijection(self) -> Self:
        unique(self.enabled_worker_ids, "enabled_worker_ids")
        unique(
            [worker.tile_id for worker in self.logical_workers],
            "logical_workers.tile_id",
        )
        unique(
            [worker.worker_index for worker in self.logical_workers],
            "logical_workers.worker_index",
        )
        unique(
            [(worker.logical_x, worker.logical_y) for worker in self.logical_workers],
            "logical_workers.coordinates",
        )
        if set(self.enabled_worker_ids) != {
            worker.tile_id for worker in self.logical_workers
        }:
            raise ValueError("logical_workers must bijectively map enabled_worker_ids")
        return self


class Fabric(ProfileModel):
    fabric_id: Index
    extent: Extent
    clock_parameter: Identifier
    topology_policy: Identifier
    routing_policy: Identifier
    coordinates: dict[Identifier, Coordinate]
    evidence: Evidence

    @model_validator(mode="after")
    def coordinate_bijection(self) -> Self:
        check_coordinates(
            list(self.coordinates.values()), self.extent, f"fabric {self.fabric_id}"
        )
        return self


class MemoryResource(ProfileModel):
    resource_id: Identifier
    kind: Literal["local_sram", "dram"]
    capacity_parameter: Identifier
    owner_tile_id: Identifier | None = None
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def local_owner(self) -> Self:
        if self.kind == "local_sram" and self.owner_tile_id is None:
            raise ValueError("local_sram requires owner_tile_id")
        return self


class ResourceTable(ProfileModel):
    resources: list[MemoryResource]
    evidence: Evidence


class Endpoint(ProfileModel):
    endpoint_id: Identifier
    tile_id: Identifier
    fabric_id: Index
    resource_ids: list[Identifier] = Field(default_factory=list)
    evidence: Evidence | None = None


class EndpointTable(ProfileModel):
    endpoints: list[Endpoint]
    evidence: Evidence


class HardwareProfileConfig(ProfileModel):
    kind: Literal["hardware_profile"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    profile_id: Identifier
    profile_revision: Identifier
    architecture: Identifier
    architecture_revision: Identifier
    asic_id: Identifier
    board: Board | None = None
    sources: dict[Identifier, Source]
    parameters: dict[Identifier, Quantity]
    layout: Layout
    worker_selection: WorkerSelection
    fabrics: list[Fabric] = Field(min_length=1)
    memory: ResourceTable
    attachments: EndpointTable
    requested_features: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self) -> Self:
        values = resolve_parameters(self.parameters)
        tiles = {tile.tile_id: tile for tile in self.layout.tiles}
        for worker_id in self.worker_selection.enabled_worker_ids:
            if worker_id not in tiles or tiles[worker_id].role != "worker":
                raise ValueError(
                    f"enabled_worker_ids: {worker_id} is not a physical worker"
                )

        unique([fabric.fabric_id for fabric in self.fabrics], "fabrics.fabric_id")
        for fabric in self.fabrics:
            if set(fabric.coordinates) != set(tiles):
                raise ValueError(
                    f"fabric {fabric.fabric_id}: coordinates must map every physical tile"
                )
            clock = self.parameters.get(fabric.clock_parameter)
            if clock is None or clock.unit != "Hz":
                raise ValueError(
                    f"fabric {fabric.fabric_id}: clock_parameter must reference Hz"
                )

        unique(
            [resource.resource_id for resource in self.memory.resources],
            "memory.resource_id",
        )
        resources = {
            resource.resource_id: resource for resource in self.memory.resources
        }
        for resource in resources.values():
            capacity = self.parameters.get(resource.capacity_parameter)
            if (
                capacity is None
                or capacity.unit != "bytes"
                or values[resource.capacity_parameter] <= 0
            ):
                raise ValueError(
                    f"resource {resource.resource_id}: capacity must reference positive bytes"
                )
            if (
                resource.owner_tile_id is not None
                and resource.owner_tile_id not in tiles
            ):
                raise ValueError(
                    f"resource {resource.resource_id}: unknown owner_tile_id"
                )

        unique(
            [endpoint.endpoint_id for endpoint in self.attachments.endpoints],
            "attachments.endpoint_id",
        )
        pairs: set[tuple[str, int]] = set()
        fabric_ids = {fabric.fabric_id for fabric in self.fabrics}
        for endpoint in self.attachments.endpoints:
            if endpoint.tile_id not in tiles or endpoint.fabric_id not in fabric_ids:
                raise ValueError(
                    f"endpoint {endpoint.endpoint_id}: unknown tile/fabric reference"
                )
            pair = endpoint.tile_id, endpoint.fabric_id
            if pair in pairs:
                raise ValueError(
                    f"endpoint {endpoint.endpoint_id}: duplicate tile/fabric attachment"
                )
            pairs.add(pair)
            unique(
                endpoint.resource_ids, f"endpoint {endpoint.endpoint_id}.resource_ids"
            )
            for resource_id in endpoint.resource_ids:
                if resource_id not in resources:
                    raise ValueError(
                        f"endpoint {endpoint.endpoint_id}: unknown resource {resource_id}"
                    )
                resource = resources[resource_id]
                if (
                    resource.owner_tile_id is not None
                    and resource.owner_tile_id != endpoint.tile_id
                ):
                    raise ValueError(
                        f"endpoint {endpoint.endpoint_id}: resource {resource_id} belongs to another tile"
                    )

        evidence = [
            self.layout.evidence,
            self.worker_selection.evidence,
            self.memory.evidence,
            self.attachments.evidence,
        ]
        evidence.extend(fabric.evidence for fabric in self.fabrics)
        evidence.extend(
            tile.evidence for tile in self.layout.tiles if tile.evidence is not None
        )
        evidence.extend(
            resource.evidence
            for resource in resources.values()
            if resource.evidence is not None
        )
        evidence.extend(
            endpoint.evidence
            for endpoint in self.attachments.endpoints
            if endpoint.evidence is not None
        )
        for record in evidence:
            if record.status == "derived":
                raise ValueError(
                    "structural evidence needs a source, assumption, or calibration; derived is for recipes"
                )
        for parameter in self.parameters.values():
            evidence.append(parameter.evidence)
            if isinstance(parameter, LiteralQuantity):
                evidence.extend(
                    override.previous_evidence for override in parameter.overrides
                )
        for record in evidence:
            for source_id in record.source_ids:
                if source_id not in self.sources:
                    raise ValueError(
                        f"evidence at {record.locator}: unknown source_id {source_id}"
                    )
        unique(self.requested_features, "requested_features")
        return self
