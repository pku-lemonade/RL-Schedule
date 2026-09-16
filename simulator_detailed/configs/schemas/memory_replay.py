"""Strict, executable-memory admission records.

These records describe a future bounded runtime. Parsing and planning do not
construct SimPy resources or claim that memory traffic is executable yet.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .topology import GraphRecord, Identifier, Index, PositiveInt, unique
from .torus_replay import EvidenceSource, TorusFabricBinding, TransportEvidence

NonNegative = Annotated[float, Field(strict=True, ge=0)]
Positive = Annotated[float, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Address = Annotated[int, Field(strict=True, ge=0)]


class MemoryGraphSource(GraphRecord):
    kind: Literal["canonical_graph"]
    graph_path: Annotated[str, Field(min_length=1)]


class MemoryProfileSource(GraphRecord):
    kind: Literal["hardware_profile"]
    profile_path: Annotated[str, Field(min_length=1)]


MemorySource = Annotated[MemoryGraphSource | MemoryProfileSource, Field(discriminator="kind")]


class MemoryClock(GraphRecord):
    hz: Positive
    evidence: TransportEvidence


class MemoryPacketConfig(GraphRecord):
    physical_flit_bytes: PositiveInt
    data_capacity_bytes: PositiveInt
    header_flits: PositiveInt = 1
    max_segment_payload_bytes: PositiveInt
    address_alignment_bytes: PositiveInt
    evidence: TransportEvidence

    @model_validator(mode="after")
    def valid_layout(self) -> Self:
        if self.data_capacity_bytes > self.physical_flit_bytes:
            raise ValueError("data capacity cannot exceed physical flit size")
        if self.max_segment_payload_bytes % self.data_capacity_bytes:
            raise ValueError("segment payload must be a data-flit multiple")
        if self.max_segment_payload_bytes % self.address_alignment_bytes:
            raise ValueError("segment payload must be an alignment multiple")
        return self


class MemoryServiceConfig(GraphRecord):
    policy: Literal["aggregate_shared_rw_v1"]
    native_clock_hz: Positive
    service_granule_bytes: PositiveInt
    chunk_bytes: PositiveInt
    bytes_per_cycle: Positive
    fixed_latency_cycles: NonNegative
    queue_capacity: PositiveInt
    evidence: TransportEvidence

    @model_validator(mode="after")
    def valid_service(self) -> Self:
        if self.chunk_bytes % self.service_granule_bytes:
            raise ValueError("service chunk must be a granule multiple")
        if self.chunk_bytes < self.service_granule_bytes:
            raise ValueError("service chunk cannot be smaller than a granule")
        return self


class MemoryResourceConfig(GraphRecord):
    resource_id: Identifier
    capacity_override_bytes: PositiveInt | None = None
    service: MemoryServiceConfig
    evidence: TransportEvidence


class MemoryEndpointBinding(GraphRecord):
    endpoint_id: Identifier
    fabric_id: Index
    router_id: Identifier
    roles: tuple[Literal["initiator", "target", "response_sink"], ...] = Field(min_length=1)
    resource_ids: tuple[Identifier, ...] = ()
    enabled: StrictBool = True
    evidence: TransportEvidence

    @model_validator(mode="after")
    def unique_roles(self) -> Self:
        unique(self.roles, f"endpoint {self.endpoint_id} role")
        if "initiator" in self.roles and not self.resource_ids:
            raise ValueError("an initiator must expose a local resource")
        return self


class MemoryBuffer(GraphRecord):
    buffer_id: Identifier
    resource_id: Identifier
    base_address: Address
    size_bytes: PositiveInt
    writable: StrictBool = True
    readable: StrictBool = True
    initially_ready: StrictBool = False
    producer_operation_id: Identifier | None = None

    @model_validator(mode="after")
    def producer_state(self) -> Self:
        if self.initially_ready and self.producer_operation_id is not None:
            raise ValueError("an initially ready buffer cannot require a producer")
        return self


class MemoryRange(GraphRecord):
    buffer_id: Identifier
    offset_bytes: NonNegativeInt
    size_bytes: PositiveInt


class MemoryOperation(GraphRecord):
    operation_id: Identifier
    initiator_id: Identifier
    fabric_id: Index | None = None
    kind: Literal[
        "read",
        "write_posted",
        "write_acknowledged",
        "local_read",
        "local_write",
        "fence",
    ]
    source: MemoryRange | None = None
    destination: MemoryRange | None = None
    depends_on: tuple[Identifier, ...] = ()
    fence_mode: Literal["local_handoff", "remote_completion"] | None = None
    fence_operations: tuple[Identifier, ...] = ()
    start_aci_cycles: NonNegative = 0

    @model_validator(mode="after")
    def shape(self) -> Self:
        network = self.kind in {"read", "write_posted", "write_acknowledged"}
        local = self.kind in {"local_read", "local_write"}
        fence = self.kind == "fence"
        if network and (self.fabric_id is None or self.source is None or self.destination is None):
            raise ValueError("network memory operations require fabric, source and destination")
        if local and (self.fabric_id is not None or self.source is None or self.destination is None):
            raise ValueError("local operations require source and destination without a fabric")
        if fence and (
            self.fabric_id is not None
            or self.source is not None
            or self.destination is not None
            or self.fence_mode is None
            or not self.fence_operations
        ):
            raise ValueError("fences require mode and operation IDs without memory ranges")
        if not fence and (self.fence_mode is not None or self.fence_operations):
            raise ValueError("only fences may declare fence fields")
        if self.kind == "write_posted" and self.fence_mode == "remote_completion":
            raise ValueError("posted writes cannot provide remote-completion fences")
        return self


class MemoryReplay(GraphRecord):
    kind: Literal["memory_replay"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    model_revision: Literal["addressed_memory_v1"]
    source: MemorySource
    aci_clock_hz: Positive
    fabrics: tuple[Index, ...] = Field(min_length=1)
    # Empty means admission records only; wire compilation requires explicit routing.
    routing: tuple[TorusFabricBinding, ...] = ()
    packet: MemoryPacketConfig
    issue_latency_aci_cycles: NonNegative
    max_outstanding_segments: PositiveInt
    endpoint_queue_capacity_packets: PositiveInt
    endpoint_staging_capacity_flits: PositiveInt
    resources: tuple[MemoryResourceConfig, ...] = Field(min_length=1)
    endpoints: tuple[MemoryEndpointBinding, ...] = Field(min_length=1)
    buffers: tuple[MemoryBuffer, ...] = Field(min_length=1)
    operations: tuple[MemoryOperation, ...] = Field(min_length=1)
    max_aci_cycles: Positive
    evidence: tuple[EvidenceSource, ...] = ()

    @model_validator(mode="after")
    def identities_and_modes(self) -> Self:
        unique(self.fabrics, "memory fabric")
        unique(tuple(f.fabric_id for f in self.routing), "memory routing fabric")
        unique(tuple(r.resource_id for r in self.resources), "memory resource")
        unique(tuple(e.endpoint_id for e in self.endpoints), "memory endpoint")
        unique(tuple(b.buffer_id for b in self.buffers), "memory buffer")
        unique(tuple(o.operation_id for o in self.operations), "memory operation")
        unique(tuple(s.url for s in self.evidence), "memory evidence source")
        resource_ids = {r.resource_id for r in self.resources}
        endpoint_ids = {e.endpoint_id for e in self.endpoints}
        buffer_ids = {b.buffer_id for b in self.buffers}
        operation_ids = {o.operation_id for o in self.operations}
        for endpoint in self.endpoints:
            if endpoint.fabric_id not in self.fabrics:
                raise ValueError(f"endpoint {endpoint.endpoint_id}: unknown fabric")
            if any(resource not in resource_ids for resource in endpoint.resource_ids):
                raise ValueError(f"endpoint {endpoint.endpoint_id}: unknown resource")
        for buffer in self.buffers:
            if buffer.resource_id not in resource_ids:
                raise ValueError(f"buffer {buffer.buffer_id}: unknown resource")
            if buffer.producer_operation_id is not None and buffer.producer_operation_id not in operation_ids:
                raise ValueError(f"buffer {buffer.buffer_id}: unknown producer")
        for operation in self.operations:
            if operation.initiator_id not in endpoint_ids:
                raise ValueError(f"operation {operation.operation_id}: unknown initiator")
            if operation.fabric_id is not None and operation.fabric_id not in self.fabrics:
                raise ValueError(f"operation {operation.operation_id}: unknown fabric")
            for dependency in operation.depends_on + operation.fence_operations:
                if dependency not in operation_ids:
                    raise ValueError(f"operation {operation.operation_id}: unknown dependency {dependency}")
            for access in (operation.source, operation.destination):
                if access is not None and access.buffer_id not in buffer_ids:
                    raise ValueError(f"operation {operation.operation_id}: unknown buffer")
        self._check_dependency_cycles()
        self._check_supported_modes()
        return self

    def _check_dependency_cycles(self) -> None:
        operations = {operation.operation_id: operation for operation in self.operations}
        edges = {
            operation_id: set(operation.depends_on + operation.fence_operations)
            for operation_id, operation in operations.items()
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(operation_id: str) -> None:
            if operation_id in visiting:
                raise ValueError("memory operation dependencies contain a cycle")
            if operation_id in visited:
                return
            visiting.add(operation_id)
            for dependency in edges[operation_id]:
                visit(dependency)
            visiting.remove(operation_id)
            visited.add(operation_id)

        for operation_id in operations:
            visit(operation_id)

    def _check_supported_modes(self) -> None:
        for operation in self.operations:
            if operation.kind == "fence" and operation.fence_mode == "remote_completion":
                referenced = {item.operation_id: item for item in self.operations}
                if any(referenced[item].kind == "write_posted" for item in operation.fence_operations):
                    raise ValueError("remote-completion fences cannot include posted writes")
