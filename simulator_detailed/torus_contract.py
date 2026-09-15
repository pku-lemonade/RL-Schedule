"""Pure configuration preparation; topology/routing admission belongs to the binder."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import JsonValue, model_validator

from .configs.schemas.hardware_profile import HardwareProfileConfig, resolve_parameters
from .configs.schemas.topology import CanonicalTopology, GraphRecord, Identifier
from .configs.schemas.torus_replay import (
    LiteralQuantity,
    Number,
    ProfileQuantity,
    ProfileSource,
    Quantity,
    Text,
    TorusLinkSettings,
    TorusReplay,
    Unit,
    check_quantity,
)
from .topology import canonical_json, content_digest, normalize_topology


def require_canonical_object(value: str) -> None:
    decoded = cast(JsonValue, json.loads(value))
    if not isinstance(decoded, dict) or canonical_json(decoded) != value:
        raise ValueError("record requires a canonical JSON object snapshot")


class ResolvedQuantity(GraphRecord):
    field_path: Text
    value: Number
    unit: Unit
    parameter: Identifier | None
    source_value: Number
    source_unit: Text
    source_clock_parameter: Identifier | None
    source_evidence_json: str
    override_reason: Text | None
    override_evidence_json: str | None

    @model_validator(mode="after")
    def valid_quantity(self) -> ResolvedQuantity:
        check_quantity(self.value, self.unit)
        require_canonical_object(self.source_evidence_json)
        if (self.override_reason is None) != (self.override_evidence_json is None):
            raise ValueError("quantity override requires reason and evidence together")
        if self.override_evidence_json is not None:
            require_canonical_object(self.override_evidence_json)
        return self


def normalize_replay(config: TorusReplay) -> TorusReplay:
    """Revalidate even model_copy callers and normalize only unordered collections."""
    config = TorusReplay.model_validate(config.model_dump(mode="json"))
    binding = config.binding.model_copy(update={
        "fabrics": tuple(sorted(config.binding.fabrics, key=lambda f: f.fabric_id)),
        "endpoints": tuple(e.model_copy(update={"roles": tuple(sorted(e.roles))})
                           for e in sorted(config.binding.endpoints, key=lambda e: e.endpoint_id)),
        "router_overrides": tuple(sorted(config.binding.router_overrides, key=lambda r: (r.fabric_id, r.router_id))),
        "link_overrides": tuple(sorted(config.binding.link_overrides, key=lambda e: (e.fabric_id, e.link_id))),
    })
    data = config.model_copy(update={
        "binding": binding,
        "fabrics": tuple(sorted(config.fabrics, key=lambda f: f.fabric_id)),
        "traffic": tuple(sorted(config.traffic, key=lambda t: t.transfer_id)),
        "slowdowns": tuple(sorted(config.slowdowns, key=lambda s: (s.fabric_id, s.link_id, s.start_aci_cycles))),
        "network_overrides": tuple(sorted(config.network_overrides, key=lambda o: (o.fabric_id, o.link_id))),
        "local_overrides": tuple(sorted(config.local_overrides, key=lambda o: (o.endpoint_id, o.direction))),
    }).model_dump(mode="json")

    def order_evidence(value: JsonValue) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "sources" and isinstance(child, list):
                    child.sort(key=canonical_json)
                order_evidence(child)
        elif isinstance(value, list):
            for child in value:
                order_evidence(child)

    order_evidence(cast(JsonValue, data))
    return TorusReplay.model_validate(data)


@dataclass(frozen=True)
class PreparedTorusContract:
    """Immutable configuration evidence, deliberately without an execution token."""

    config: TorusReplay
    source_json: str
    source_sha256: str
    configuration_json: str
    quantities: tuple[ResolvedQuantity, ...]

    @property
    def contract_sha256(self) -> str:
        return content_digest({
            "contract_version": 1,
            "source_sha256": self.source_sha256,
            "configuration": json.loads(self.configuration_json),
            "quantities": [q.model_dump(mode="json") for q in self.quantities],
        })

    def export(self) -> dict[str, object]:
        return {
            "kind": "torus_replay_contract", "schema_version": 1,
            "validation_stage": "configuration_only", "can_execute": False,
            "source_sha256": self.source_sha256, "contract_sha256": self.contract_sha256,
            "configuration": json.loads(self.configuration_json),
            "quantities": [q.model_dump(mode="json") for q in self.quantities],
            "pending_validation": ["topology_binding", "route_dependencies", "runtime_admission"],
        }

    @classmethod
    def load(cls, replay_path: str | Path) -> PreparedTorusContract:
        path = Path(replay_path)
        config = TorusReplay.model_validate_json(path.read_text())
        source_path = config.source.profile_path if isinstance(config.source, ProfileSource) else config.source.graph_path
        source_document: object = json.loads((path.parent / source_path).read_text())
        return cls.prepare(config, source_document)

    @classmethod
    def prepare(cls, config: TorusReplay, source_document: object) -> PreparedTorusContract:
        config = normalize_replay(config)
        profile: HardwareProfileConfig | None = None
        if isinstance(config.source, ProfileSource):
            profile = HardwareProfileConfig.model_validate_json(canonical_json(source_document))
            source_json = canonical_json(profile.model_dump(mode="json"))
        else:
            graph = normalize_topology(CanonicalTopology.model_validate(source_document))
            if graph.connectivity_state != "complete":
                raise ValueError("version-two graph source requires complete connectivity")
            source_json = canonical_json(graph.model_dump(mode="json"))
        parameters = {} if profile is None else resolve_parameters(profile.parameters)
        resolved: list[ResolvedQuantity] = []

        def quantity(item: Quantity, label: str, clock_parameter: str | None = None) -> int | float:
            if isinstance(item, LiteralQuantity):
                record = ResolvedQuantity(
                    field_path=label, value=item.value, unit=item.unit, parameter=None,
                    source_value=item.value, source_unit=item.unit, source_clock_parameter=None,
                    source_evidence_json=canonical_json(item.evidence.model_dump(mode="json")),
                    override_reason=None, override_evidence_json=None,
                )
            else:
                if profile is None or item.parameter not in parameters:
                    raise ValueError(f"{label}: profile parameter is unavailable: {item.parameter}")
                original = profile.parameters[item.parameter]
                original_value = parameters[item.parameter]
                if original.unit == item.unit:
                    value = original_value
                elif original.unit == "bytes_per_cycle" and item.unit == "bits_per_cycle":
                    value = original_value * 8
                else:
                    raise ValueError(f"{label}: incompatible profile units {original.unit}/{item.unit}")
                if original.clock_parameter is not None and original.clock_parameter != clock_parameter:
                    raise ValueError(f"{label}: profile quantity belongs to another clock domain")
                override = item.override
                if override is not None:
                    value = override.value
                evidence = {
                    "quantity": original.model_dump(mode="json"),
                    "sources": {sid: profile.sources[sid].model_dump(mode="json")
                                for sid in original.evidence.source_ids},
                }
                record = ResolvedQuantity(
                    field_path=label, value=value, unit=item.unit, parameter=item.parameter,
                    source_value=original_value, source_unit=original.unit,
                    source_clock_parameter=original.clock_parameter,
                    source_evidence_json=canonical_json(evidence),
                    override_reason=None if override is None else override.reason,
                    override_evidence_json=None if override is None else canonical_json(override.evidence.model_dump(mode="json")),
                )
            resolved.append(record)
            return record.value

        aci_clock = quantity(config.aci_clock, "aci_clock")
        by_fabric = {f.fabric_id: f for f in config.fabrics}
        profile_fabrics = {} if profile is None else {f.fabric_id: f for f in profile.fabrics}
        physical_sizes: dict[int, int] = {}
        ratios: dict[int, float] = {}
        clocks: dict[int, str | None] = {}

        def converted(value: float, ratio: float, label: str) -> float:
            try:
                duration = value / ratio
            except OverflowError as exc:
                raise ValueError(f"{label}: timing overflow") from exc
            if not math.isfinite(duration) or (value > 0 and duration <= 0):
                raise ValueError(f"{label}: nonfinite or underflowed timing")
            return duration

        def link(settings: TorusLinkSettings, fabric_id: int, label: str) -> None:
            wire = quantity(settings.wire_bits_per_noc_cycle, f"{label}.wire_bits_per_noc_cycle", clocks[fabric_id])
            payload = quantity(settings.payload_bits_per_noc_cycle, f"{label}.payload_bits_per_noc_cycle", clocks[fabric_id])
            if type(wire) is not int or type(payload) is not int or payload > wire:
                raise ValueError(f"{label}: invalid resolved link widths")
            serialization = (8 * physical_sizes[fabric_id] + payload - 1) // payload
            if settings.launch_interval_noc_cycles < serialization:
                raise ValueError(f"{label}: physical launch interval is shorter than serialization")
            for duration in (serialization, settings.launch_interval_noc_cycles,
                             settings.propagation_noc_cycles, settings.credit_return_noc_cycles):
                converted(duration, ratios[fabric_id], label)

        for fabric in config.fabrics:
            label = f"fabrics.{fabric.fabric_id}"
            if profile is not None:
                source_fabric = profile_fabrics.get(fabric.fabric_id)
                if source_fabric is None or not isinstance(fabric.noc_clock, ProfileQuantity) or (
                    fabric.noc_clock.parameter != source_fabric.clock_parameter
                ):
                    raise ValueError(f"{label}: clock must reference the profile fabric clock; use an explicit override")
                if not isinstance(fabric.flit.physical_flit_bytes, ProfileQuantity):
                    raise ValueError(f"{label}: physical flit size must reference a profile parameter")
            clock = quantity(fabric.noc_clock, f"{label}.noc_clock")
            ratio = converted(clock, aci_clock, f"{label}.clock_ratio")
            ratios[fabric.fabric_id] = ratio
            clocks[fabric.fabric_id] = fabric.noc_clock.parameter if isinstance(fabric.noc_clock, ProfileQuantity) else None
            physical = quantity(fabric.flit.physical_flit_bytes, f"{label}.flit.physical_flit_bytes")
            if type(physical) is not int or max(fabric.flit.payload_capacity_bytes, fabric.flit.header_bytes) > physical:
                raise ValueError(f"{label}: invalid resolved flit format")
            physical_sizes[fabric.fabric_id] = physical
            link(fabric.network_link, fabric.fabric_id, f"{label}.network_link")
            link(fabric.local_link, fabric.fabric_id, f"{label}.local_link")
            for duration in (fabric.router.rc_noc_cycles, fabric.router.sa_noc_cycles,
                             fabric.router.transfer_noc_cycles, fabric.router.transfer_initiation_interval_noc_cycles):
                converted(duration, ratio, f"{label}.router")
        for override in config.network_overrides:
            link(override.settings, override.fabric_id, f"network_overrides.{override.fabric_id}.{override.link_id}")
        endpoints = {e.endpoint_id: e for e in config.binding.endpoints}
        for override in config.local_overrides:
            fabric_id = endpoints[override.endpoint_id].fabric_id
            link(override.settings, fabric_id, f"local_overrides.{override.endpoint_id}.{override.direction}")
        for endpoint in endpoints.values():
            for service in (endpoint.sink_service, endpoint.response_service):
                if service is not None:
                    converted(service.cycles, ratios[endpoint.fabric_id] if service.timebase == "noc" else 1,
                              f"endpoint.{endpoint.endpoint_id}.service")
        for failure in config.slowdowns:
            settings = next((o.settings for o in config.network_overrides
                             if (o.fabric_id, o.link_id) == (failure.fabric_id, failure.link_id)),
                            by_fabric[failure.fabric_id].network_link)
            for duration in (settings.launch_interval_noc_cycles, settings.propagation_noc_cycles):
                converted(duration * failure.factor, ratios[failure.fabric_id], f"slowdown.{failure.failure_id}")
        data = config.model_dump(mode="json")
        data["source"] = {"kind": config.source.kind}
        return cls(config, source_json, content_digest(json.loads(source_json)), canonical_json(data),
                   tuple(sorted(resolved, key=lambda q: q.field_path)))
