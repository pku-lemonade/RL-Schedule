"""Phase-0 TSim parameter controllability audit contracts."""

from __future__ import annotations

from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workload import stable_sha256


ControllabilityStatus = Literal[
    "DIRECT_INTERVENTION",
    "COUPLED_INTERVENTION",
    "SCENE_STRESS_ONLY",
    "NOT_CONTROLLABLE",
    "UNKNOWN",
]

ParameterGroup = Literal[
    "data_noc_bandwidth",
    "gm_bandwidth",
    "adalink_bandwidth",
    "packet",
    "fifo_credit",
    "multicast",
    "write_sum",
    "matrix",
    "vector",
    "sync_fence",
    "clock_timebase",
    "peak_compute",
]

REQUIRED_PARAMETER_GROUPS: tuple[str, ...] = (
    "data_noc_bandwidth",
    "gm_bandwidth",
    "adalink_bandwidth",
    "packet",
    "fifo_credit",
    "multicast",
    "write_sum",
    "matrix",
    "vector",
    "sync_fence",
    "clock_timebase",
    "peak_compute",
)


# Scene coverage cannot substitute for any of these physical parameter rows.
REQUIRED_HARDWARE_PARAMETER_ROWS: dict[str, str] = {'adalink.links.*.bytes_per_cycle': 'adalink_bandwidth', 'clock.active_timebase_and_cycle_mapping': 'clock_timebase', 'data_noc.links.*.payload_bytes_per_cycle': 'data_noc_bandwidth', 'data_noc.packet.payload_header_alignment': 'packet', 'data_noc.resources.*.credit_return_cycles': 'fifo_credit', 'data_noc.resources.*.fifo_slots': 'fifo_credit', 'gm.rdma_wdma_gsram.*.bytes_per_cycle': 'gm_bandwidth', 'matrix.router_bf16_and_expert_w4a8.throughput': 'matrix', 'multicast.replication_nodes.*.cycles': 'multicast', 'peak.organization_reference_flops_per_second': 'peak_compute', 'sync.inner_outer_fence.latency': 'sync_fence', 'vector.named_stages.throughput': 'vector', 'write_sum.controllers.*.rmw_throughput': 'write_sum'}


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceLocation(FrozenRecord):
    path: str = Field(min_length=1)
    symbol_or_field: str = Field(min_length=1)
    line_range: str | None = None
    evidence_kind: Literal["source", "api", "config", "report", "binary_audit", "missing_contract"]


class ParameterControllabilityRow(FrozenRecord):
    parameter_path: str = Field(min_length=1, pattern=r"^\S+$")
    parameter_group: ParameterGroup
    unit: str = Field(min_length=1)
    status: ControllabilityStatus
    config_locations: tuple[SourceLocation, ...] = ()
    api_locations: tuple[SourceLocation, ...] = ()
    directly_mutable: bool | None
    requires_restart: bool | None
    requires_rebuild: bool | None
    coupled_parameter_paths: tuple[str, ...] = ()
    same_workload_route_preserved: bool | None
    observable_boundaries: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    supported_range: str | None = None
    notes: str = Field(min_length=1)
    intervention_executed: Literal[False] = False

    @model_validator(mode="after")
    def state_contract(self) -> "ParameterControllabilityRow":
        if not self.observable_boundaries or not self.evidence_refs:
            raise ValueError("controllability row requires boundaries and evidence")
        if self.status == "DIRECT_INTERVENTION":
            if self.directly_mutable is not True or self.same_workload_route_preserved is not True:
                raise ValueError("direct intervention must be mutable and preserve workload/route")
            if not self.config_locations and not self.api_locations:
                raise ValueError("direct intervention requires a config or API location")
            if self.coupled_parameter_paths:
                raise ValueError("direct intervention cannot declare coupled paths")
        elif self.status == "COUPLED_INTERVENTION":
            if self.directly_mutable is not True or self.same_workload_route_preserved is not True:
                raise ValueError("coupled intervention must be mutable and preserve workload/route")
            if not self.coupled_parameter_paths:
                raise ValueError("coupled intervention requires coupled paths")
        elif self.status in ("SCENE_STRESS_ONLY", "NOT_CONTROLLABLE"):
            if self.directly_mutable is not False:
                raise ValueError("non-intervention status must set directly_mutable=false")
        else:
            if self.directly_mutable is not None:
                raise ValueError("unknown status must leave mutability unknown")
        return self


class ParameterControllabilityMatrix(FrozenRecord):
    schema_version: Literal[1] = 1
    audit_id: str = Field(min_length=1)
    source_repository_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    audit_scope: str = Field(min_length=1)
    required_parameter_groups: tuple[str, ...]
    rows: tuple[ParameterControllabilityRow, ...]
    intervention_executed: Literal[False] = False
    source_refs: tuple[str, ...]
    matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def completeness(self) -> "ParameterControllabilityMatrix":
        paths = [row.parameter_path for row in self.rows]
        if len(paths) != len(set(paths)):
            raise ValueError("controllability parameter paths must be unique")
        if tuple(self.required_parameter_groups) != REQUIRED_PARAMETER_GROUPS:
            raise ValueError("required parameter group contract changed")
        covered = {row.parameter_group for row in self.rows}
        missing = set(REQUIRED_PARAMETER_GROUPS) - covered
        if missing:
            raise ValueError("controllability matrix missing groups: " + ",".join(sorted(missing)))
        by_path = {row.parameter_path: row for row in self.rows}
        for path, group in REQUIRED_HARDWARE_PARAMETER_ROWS.items():
            if path not in by_path or by_path[path].parameter_group != group:
                raise ValueError("required hardware parameter row missing or misgrouped: " + path)
            if by_path[path].status == "SCENE_STRESS_ONLY":
                raise ValueError("hardware row cannot be replaced by scene stress: " + path)
        if not self.source_refs:
            raise ValueError("controllability matrix requires source references")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"matrix_sha256"}))
        if expected != self.matrix_sha256:
            raise ValueError("controllability matrix hash mismatch")
        return self


def freeze_controllability_matrix(
    audit_id: str,
    source_repository_head: str,
    audit_scope: str,
    rows: Sequence[ParameterControllabilityRow],
    source_refs: Sequence[str],
) -> ParameterControllabilityMatrix:
    data: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": audit_id,
        "source_repository_head": source_repository_head,
        "audit_scope": audit_scope,
        "required_parameter_groups": REQUIRED_PARAMETER_GROUPS,
        "rows": tuple(sorted(rows, key=lambda row: row.parameter_path)),
        "intervention_executed": False,
        "source_refs": tuple(source_refs),
    }
    json_shape = {
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else list(value) if isinstance(value, tuple) else value)
        for key, value in data.items()
    }
    return ParameterControllabilityMatrix(**data, matrix_sha256=stable_sha256(json_shape))


__all__ = [
    "ControllabilityStatus",
    "ParameterControllabilityMatrix",
    "ParameterControllabilityRow",
    "REQUIRED_PARAMETER_GROUPS",
    "REQUIRED_HARDWARE_PARAMETER_ROWS",
    "SourceLocation",
    "freeze_controllability_matrix",
]
