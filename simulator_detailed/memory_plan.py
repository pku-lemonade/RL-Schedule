"""Pre-runtime memory admission and deterministic plan identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from .configs.schemas.hardware_profile import HardwareProfileConfig
from .configs.schemas.memory_replay import MemoryReplay
from .configs.schemas.topology import CanonicalTopology
from .memory_records import MemoryPlanRecord, MemoryQuantity
from .topology import content_digest, normalize_topology, topology_from_profile
from .torus import TorusEndpointPorts, bind_torus_graph


class MemoryPlan:
    """Validated memory configuration; intentionally has no SimPy runtime."""

    def __init__(self, config: MemoryReplay, record: MemoryPlanRecord, graph: CanonicalTopology) -> None:
        self.config = config
        self.record = record
        self.graph = graph

    @property
    def plan_sha256(self) -> str:
        return self.record.plan_sha256

    def revalidate(self) -> MemoryPlan:
        config = MemoryReplay.model_validate(self.config.model_dump(mode="python"))
        if config.source.kind == "hardware_profile":
            if self.graph.origin.kind != "hardware_profile" or self.graph.origin.document_json is None:
                raise ValueError("profile memory plan requires its original source document")
            source: object = json.loads(self.graph.origin.document_json)
        else:
            source = self.graph.model_dump(mode="python")
        rebuilt = self.compile(config, source)
        if rebuilt.graph != self.graph or rebuilt.record != self.record:
            raise ValueError("memory plan does not match its admitted source and configuration")
        return rebuilt

    @classmethod
    def compile(cls, config: MemoryReplay, source_document: object) -> MemoryPlan:
        if config.source.kind == "canonical_graph":
            graph = normalize_topology(CanonicalTopology.model_validate(source_document))
        else:
            profile = HardwareProfileConfig.model_validate(source_document)
            graph = topology_from_profile(profile).graph
            if config.routing:
                graph = bind_torus_graph(
                    graph, config.routing,
                    tuple(TorusEndpointPorts(e.endpoint_id, e.fabric_id, e.inject_port, e.eject_port,
                                             "initiator" in e.roles) for e in config.endpoints if e.enabled),
                    generate_links=True)
        if graph.connectivity_state != "complete":
            raise ValueError("memory plan requires complete topology connectivity")

        resources = {item.resource_id: item for item in graph.resources}
        configured = {item.resource_id: item for item in config.resources}
        if set(configured) != set(resources):
            missing = sorted(set(resources) - set(configured))
            extra = sorted(set(configured) - set(resources))
            raise ValueError(f"memory resource set differs from graph (missing={missing}, extra={extra})")
        for resource_id, item in configured.items():
            capacity = resources[resource_id].capacity_bytes
            if item.capacity_override_bytes is not None and item.capacity_override_bytes > capacity:
                raise ValueError(f"resource {resource_id}: capacity override exceeds graph capacity")
        graph_endpoints = {item.endpoint_id: item for item in graph.attachments}
        for endpoint in config.endpoints:
            graph_endpoint = graph_endpoints.get(endpoint.endpoint_id)
            if graph_endpoint is None:
                raise ValueError(f"unknown graph endpoint {endpoint.endpoint_id}")
            if endpoint.fabric_id != graph_endpoint.fabric_id or endpoint.router_id != graph_endpoint.router_id:
                raise ValueError(f"endpoint {endpoint.endpoint_id}: fabric/router binding disagrees with graph")
            if endpoint.enabled and (graph_endpoint.enabled is not True or graph_endpoint.replay_enabled is not True):
                raise ValueError(f"endpoint {endpoint.endpoint_id}: graph attachment is unavailable")
            if ((endpoint.inject_port is not None and endpoint.inject_port != graph_endpoint.inject_port)
                    or (endpoint.eject_port is not None and endpoint.eject_port != graph_endpoint.eject_port)):
                raise ValueError(f"endpoint {endpoint.endpoint_id}: local port disagrees with graph permission")
            if any(resource not in graph_endpoint.resource_ids for resource in endpoint.resource_ids):
                raise ValueError(f"endpoint {endpoint.endpoint_id}: resource is not attached in graph")
        for buffer in config.buffers:
            resource = resources[buffer.resource_id]
            if buffer.base_address + buffer.size_bytes > resource.capacity_bytes:
                raise ValueError(f"buffer {buffer.buffer_id}: range exceeds resource capacity")
            if buffer.base_address % config.packet.address_alignment_bytes:
                raise ValueError(f"buffer {buffer.buffer_id}: base address is not aligned")
            if buffer.size_bytes % config.packet.address_alignment_bytes:
                raise ValueError(f"buffer {buffer.buffer_id}: size is not alignment compatible")
        # Normalize the source before hashing so list ordering and source paths do
        # not become accidental plan identity inputs.
        source_json = _canonical_json(graph)
        configuration_json = _canonical_json(config.model_dump(mode="json"))
        source_sha = content_digest(json.loads(source_json))
        configuration_sha = content_digest(_identity_configuration(config))
        quantities = (
            MemoryQuantity(field_path="aci_clock_hz", value=config.aci_clock_hz, unit="Hz", source="configured"),
            MemoryQuantity(field_path="packet.physical_flit_bytes", value=config.packet.physical_flit_bytes, unit="bytes", source="configured"),
            MemoryQuantity(field_path="packet.data_capacity_bytes", value=config.packet.data_capacity_bytes, unit="bytes", source="configured"),
        )
        plan_sha = content_digest({
            "source_sha256": source_sha,
            "configuration_sha256": configuration_sha,
            "source_kind": config.source.kind,
            "resource_ids": sorted(configured),
            "endpoint_ids": sorted(item.endpoint_id for item in config.endpoints),
            "buffer_ids": sorted(item.buffer_id for item in config.buffers),
            "operation_ids": sorted(item.operation_id for item in config.operations),
        })
        record = MemoryPlanRecord(
            source_sha256=source_sha,
            configuration_sha256=configuration_sha,
            plan_sha256=plan_sha,
            source_kind=config.source.kind,
            source_json=source_json,
            configuration_json=configuration_json,
            quantities=quantities,
            resource_ids=tuple(sorted(configured)),
            endpoint_ids=tuple(sorted(item.endpoint_id for item in config.endpoints)),
            buffer_ids=tuple(sorted(item.buffer_id for item in config.buffers)),
            operation_ids=tuple(sorted(item.operation_id for item in config.operations)),
        )
        return cls(config, record, graph)


def _canonical_json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identity_configuration(config: MemoryReplay) -> object:
    value = cast(dict[str, object], config.model_dump(mode="json"))
    source = value.get("source")
    if isinstance(source, dict):
        source_dict = cast(dict[str, object], source)
        source_dict.pop("graph_path", None)
        source_dict.pop("profile_path", None)
    return value


def load_source(path: str, *, base_dir: Path) -> object:
    """Load a tagged source for callers that already parsed a replay config."""
    source_path = (base_dir / path).resolve()
    return json.loads(source_path.read_text())
