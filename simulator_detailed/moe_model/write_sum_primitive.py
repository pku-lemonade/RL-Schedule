"""Exact source-backed fused R10 combined WRITE_SUM primitive.

The formula spans GM_WDMA0 receive/accumulate begin through the GM fence_all
terminal.  It intentionally does not expose an independent atomic RMW cost.
"""
from typing import Literal
import json
import math

from pydantic import Field, model_validator

from .component_service import ComponentAction, execute_actions
from .timebase import TimebaseDomain, TimebaseBundle, freeze_timebase_bundle
from .workload import FrozenRecord, stable_sha256


class WriteSumPrimitiveWork(FrozenRecord):
    profile: Literal["transformer-fused"] = "transformer-fused"
    consumer_stage: Literal["R10"] = "R10"
    mechanism: Literal["gm_source_service"] = "gm_source_service"
    operation: Literal["write_sum"] = "write_sum"
    primitive: Literal["gm_receive_sync_channel1_with_sum_combined"] = (
        "gm_receive_sync_channel1_with_sum_combined"
    )
    payload_bytes: int = Field(gt=0)
    contributors: int = Field(gt=0, le=32)
    controller: Literal["GM_WDMA0"] = "GM_WDMA0"
    channel: Literal[1] = 1
    address_distribution: Literal["same-address"] = "same-address"
    buffer_binding: Literal["source-expert-reduce-preinitialized-gsram"] = (
        "source-expert-reduce-preinitialized-gsram"
    )
    contributor_order: tuple[int, ...]
    zero_initialization_scope: Literal[
        "preinitialized outside measured combined accumulation primitive"
    ] = "preinitialized outside measured combined accumulation primitive"
    counter_node: Literal["GM_WDMA0"] = "GM_WDMA0"
    boundary: Literal[
        "GM_WDMA0 counter before WRITE_SUM receive loop -> after GM fence_all terminal"
    ] = "GM_WDMA0 counter before WRITE_SUM receive loop -> after GM fence_all terminal"

    @model_validator(mode="after")
    def identity(self):
        if self.payload_bytes % 4 or self.contributor_order != tuple(range(self.contributors)):
            raise ValueError("WRITE_SUM payload/order identity invalid")
        return self

    @property
    def packet_count(self):
        return math.ceil(self.payload_bytes / 128)

    @property
    def total_payload_bytes(self):
        return self.payload_bytes * self.contributors


class CombinedWriteSumFormula(FrozenRecord):
    revision: Literal["M04-source-write-sum-combined-r1"] = (
        "M04-source-write-sum-combined-r1"
    )
    source_unit: Literal["tsim_counter_default_unit"] = "tsim_counter_default_unit"
    base_counter: float
    per_contributor_counter: float
    extra_packet_base_counter: float
    extra_packet_per_contributor_counter: float
    calibration_payloads: tuple[int, ...]
    calibration_contributors: tuple[int, ...]
    calibration_scene_ids: tuple[str, ...]
    parameter_prefix: Literal["gm.write_sum.combined.fused_r10"] = (
        "gm.write_sum.combined.fused_r10"
    )
    applicability: str
    source_refs: tuple[str, ...]
    formula_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        values = (
            self.base_counter,
            self.per_contributor_counter,
            self.extra_packet_base_counter,
            self.extra_packet_per_contributor_counter,
        )
        if any(not math.isfinite(x) for x in values) or self.per_contributor_counter <= 0:
            raise ValueError("combined WRITE_SUM coefficients invalid")
        if (
            self.calibration_payloads != tuple(sorted(set(self.calibration_payloads)))
            or self.calibration_contributors
            != tuple(sorted(set(self.calibration_contributors)))
            or len(self.calibration_payloads) < 2
            or len(self.calibration_contributors) < 2
            or not self.calibration_scene_ids
            or not self.applicability
            or not self.source_refs
        ):
            raise ValueError("combined WRITE_SUM evidence/domain incomplete")
        if stable_sha256(self.model_dump(mode="json", exclude={"formula_sha256"})) != self.formula_sha256:
            raise ValueError("combined WRITE_SUM formula hash drift")
        return self

    def parameter_paths(self):
        return tuple(
            self.parameter_prefix + "." + name
            for name in (
                "base_counter",
                "per_contributor_counter",
                "extra_packet_base_counter",
                "extra_packet_per_contributor_counter",
            )
        )

    def duration(self, work, changes=None):
        work = WriteSumPrimitiveWork.model_validate_json(work.model_dump_json())
        changes = changes or {}
        if not set(changes) <= set(self.parameter_paths()):
            raise ValueError("WRITE_SUM mutation requires exact combined parameter")
        names = (
            "base_counter",
            "per_contributor_counter",
            "extra_packet_base_counter",
            "extra_packet_per_contributor_counter",
        )
        values = {
            name: changes.get(self.parameter_prefix + "." + name, getattr(self, name))
            for name in names
        }
        if any(
            isinstance(value, bool)
            or type(value) not in (int, float)
            or not math.isfinite(value)
            for value in values.values()
        ):
            raise ValueError("WRITE_SUM mutation must be finite numeric")
        extra_packets = work.packet_count - 1
        result = (
            values["base_counter"]
            + values["per_contributor_counter"] * work.contributors
            + extra_packets
            * (
                values["extra_packet_base_counter"]
                + values["extra_packet_per_contributor_counter"] * work.contributors
            )
        )
        if result <= 0:
            raise ValueError("WRITE_SUM combined duration outside positive domain")
        return result

    def action(self, work, resource_id, changes=None):
        duration = self.duration(work, changes)
        return ComponentAction(
            action_id="R10.write_sum.combined",
            stage="R10",
            component="M04",
            resource_id=resource_id,
            work=float(work.total_payload_bytes),
            work_unit="combined-payload-byte",
            duration_cycles=duration,
            duration_source=(
                "combined WRITE_SUM receive/accumulate counter boundary; "
                "atomic internal service not separable"
            ),
            calibration_sha256=self.formula_sha256,
            bytes=work.total_payload_bytes,
            participants=tuple([f"pe{i}" for i in work.contributor_order] + [work.controller]),
            status="calibrated",
        )


class WriteSumPrimitiveDomain(FrozenRecord):
    work: WriteSumPrimitiveWork
    formula: CombinedWriteSumFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counter_callsite: int = Field(ge=0)
    source_refs: tuple[str, ...]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        if (
            self.timebase.component_ids != ("M04",)
            or self.timebase.operation != "write_sum"
            or self.timebase.profile != self.work.profile
            or self.timebase.boundary_id != self.work.boundary
            or self.timebase.accepted_work_values != (self.work.total_payload_bytes,)
            or self.timebase.source_unit != self.formula.source_unit
            or self.timebase.status != "validated_simulation"
            or not self.source_refs
        ):
            raise ValueError("WRITE_SUM formula/timebase/work identity mismatch")
        if stable_sha256(self.model_dump(mode="json", exclude={"domain_sha256"})) != self.domain_sha256:
            raise ValueError("WRITE_SUM primitive domain hash drift")
        return self


def freeze_write_sum_formula(**kwargs):
    raw = dict(
        revision="M04-source-write-sum-combined-r1",
        source_unit="tsim_counter_default_unit",
        parameter_prefix="gm.write_sum.combined.fused_r10",
        **kwargs,
    )
    raw["formula_sha256"] = stable_sha256(raw)
    return CombinedWriteSumFormula.model_validate_json(json.dumps(raw))


def freeze_write_sum_domain(**kwargs):
    raw = dict(kwargs)
    raw["domain_sha256"] = stable_sha256(raw)
    return WriteSumPrimitiveDomain.model_validate_json(json.dumps(raw))


def resolve_write_sum_primitive(
    domain,
    *,
    work,
    model_library_sha256,
    systemc_library_sha256,
    observer_sha256,
    counter_callsite,
    physical=False,
):
    domain = WriteSumPrimitiveDomain.model_validate_json(domain.model_dump_json())
    work = WriteSumPrimitiveWork.model_validate_json(work.model_dump_json())
    if physical:
        raise ValueError("WRITE_SUM simulation counter domain is not physical")
    if (
        work != domain.work
        or (model_library_sha256, systemc_library_sha256)
        != (domain.timebase.model_library_sha256, domain.timebase.systemc_library_sha256)
        or observer_sha256 != domain.observer_sha256
        or counter_callsite != domain.counter_callsite
    ):
        raise ValueError("WRITE_SUM primitive requires exact work/provider/boundary identity")
    return domain


def convert_write_sum_duration(domain, raw_value, **identity):
    domain = resolve_write_sum_primitive(domain, **identity)
    from .timebase import convert_time_value

    return convert_time_value(domain.timebase, raw_value)


def execute_write_sum_primitive(
    domain,
    binding,
    hardware,
    *,
    resource_id,
    changes=None,
    extra_actions=(),
    production=True,
    cancel=None,
):
    if production or not binding.fixture_only:
        raise ValueError("WRITE_SUM physical binding/service publication unavailable")
    domain = WriteSumPrimitiveDomain.model_validate_json(domain.model_dump_json())
    action = domain.formula.action(domain.work, resource_id, changes)
    terminal = ComponentAction(
        action_id="R10.complete",
        stage="R10",
        component="M04",
        resource_id=resource_id,
        work=1.0,
        work_unit="combined-primitive-terminal",
        duration_cycles=0.0,
        duration_source="GM fence_all terminal dependency",
        calibration_sha256=domain.formula.formula_sha256,
        dependencies=(action.action_id,),
        participants=(domain.work.controller,),
        status="calibrated",
    )
    execution = execute_actions(
        (action, terminal) + tuple(extra_actions),
        binding,
        hardware,
        production=False,
        cancel=cancel,
    )
    execution.receipt.update(
        write_sum_domain_sha256=domain.domain_sha256,
        write_sum_work=domain.work.model_dump(mode="json"),
        combined_primitive_only=True,
        atomic_internal_service=None,
        physical_publishable=False,
        source_unit=domain.formula.source_unit,
    )
    if execution.result.status == "complete":
        from .adalink_execute import _critical_to_completion

        execution.receipt["write_sum_event_resource_critical_path"] = _critical_to_completion(
            execution.receipt["causal_graph"], "R10.complete"
        )
    return execution


def append_write_sum_timebase(base, domain):
    base = TimebaseBundle.model_validate_json(base.model_dump_json())
    domain = WriteSumPrimitiveDomain.model_validate_json(domain.model_dump_json())
    if any(item.domain_id == domain.timebase.domain_id for item in base.domains):
        raise ValueError("WRITE_SUM timebase domain duplicate")
    return freeze_timebase_bundle(
        bundle_id="T81-C09-plus-source-WRITE_SUM-counter-domain",
        fixture_only=False,
        domains=base.domains + (domain.timebase,),
        source_refs=base.source_refs + domain.source_refs,
    )


__all__ = [
    "CombinedWriteSumFormula",
    "WriteSumPrimitiveDomain",
    "WriteSumPrimitiveWork",
    "append_write_sum_timebase",
    "convert_write_sum_duration",
    "execute_write_sum_primitive",
    "freeze_write_sum_domain",
    "freeze_write_sum_formula",
    "resolve_write_sum_primitive",
]
