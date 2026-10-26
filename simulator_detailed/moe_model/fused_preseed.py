"""Exact transformer-fused residual-output preseed timing ownership.

The source action is four independent GM_WDMA channel-1 receive queues.  Each
queue consumes eight PE rows.  The action completes at the maximum of the four
controller completions; no R4 quant-upload, R10 WRITE_SUM, or downstream cost
is owned here.
"""
from typing import Literal
import json, math
from pydantic import Field, TypeAdapter, model_validator

from .binding import SuppliedBinding
from .component_service import ComponentAction, execute_actions
from .timebase import TimebaseBundle, TimebaseDomain, convert_time_value, freeze_timebase_bundle
from .workload import FrozenRecord, stable_sha256

PRESEED_BOUNDARY = (
    "GM_WDMA0..3 active begin before source-exact residual receive loops -> "
    "max of each controller's eighth CHANNEL1 engine completion; API return observed separately"
)
PRESEED_OWNER = "R4.residual_preseed.four_wdma_receive_max"
PRESEED_BUFFER = "tl_sram.prelude.residual_out-fp32-to-tl_gsram.moe.output"


class FusedPreseedWork(FrozenRecord):
    profile: Literal["transformer-fused"] = "transformer-fused"
    consumer_stage: Literal["R4"] = "R4"
    operation: Literal["residual_preseed"] = "residual_preseed"
    payload_bytes_per_pe: Literal[8192] = 8192
    pe_count: Literal[32] = 32
    controller_count: Literal[4] = 4
    transactions_per_controller: Literal[8] = 8
    channel: Literal[1] = 1
    controller_by_pe: tuple[int, ...] = tuple(pe % 4 for pe in range(32))
    source_role: Literal["PE0..31.UPLOAD1"] = "PE0..31.UPLOAD1"
    destination_role: Literal["GM_WDMA0..3.CHANNEL1"] = "GM_WDMA0..3.CHANNEL1"
    buffer_binding: Literal[PRESEED_BUFFER] = PRESEED_BUFFER
    boundary: Literal[PRESEED_BOUNDARY] = PRESEED_BOUNDARY
    timing_ownership: Literal[PRESEED_OWNER] = PRESEED_OWNER
    source_commit: Literal["094bcd25e985a4c836fd5521cccd61b14d911aac"] = "094bcd25e985a4c836fd5521cccd61b14d911aac"
    source_function: Literal["send_residual_out_to_gm_after_quant_upload/gm_wdma_receive_residual_out_from_pes"] = "send_residual_out_to_gm_after_quant_upload/gm_wdma_receive_residual_out_from_pes"
    source_callsite: Literal["router_gemm.hpp:407-410,560-562"] = "router_gemm.hpp:407-410,560-562"

    @model_validator(mode="after")
    def exact(self):
        if self.controller_by_pe != tuple(pe % 4 for pe in range(32)):
            raise ValueError("preseed PE/controller mapping mismatch")
        if self.payload_bytes_per_pe % 128 or self.pe_count != self.controller_count * self.transactions_per_controller:
            raise ValueError("preseed payload/participant identity mismatch")
        return self

    @property
    def logical_source_bytes(self):
        return self.payload_bytes_per_pe * self.pe_count

    @property
    def bytes_per_controller(self):
        return self.payload_bytes_per_pe * self.transactions_per_controller

    @property
    def packets_per_controller(self):
        return self.transactions_per_controller * math.ceil(self.payload_bytes_per_pe / 128)


class FusedPreseedFormula(FrozenRecord):
    revision: Literal["M04-fused-residual-preseed-r2"] = "M04-fused-residual-preseed-r2"
    source_unit: Literal["reported_ns"] = "reported_ns"
    startup_reported_ns_by_controller: tuple[float, float, float, float]
    packet_service_reported_ns_by_controller: tuple[float, float, float, float]
    calibration_payloads: tuple[int, ...]
    calibration_scene_ids: tuple[str, ...]
    applicability: str
    formula_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        values = self.startup_reported_ns_by_controller + self.packet_service_reported_ns_by_controller
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("preseed formula coefficient invalid")
        if len(self.calibration_payloads) < 2 or len(self.calibration_scene_ids) < 2 or not self.applicability:
            raise ValueError("preseed calibration identity incomplete")
        if stable_sha256(self.model_dump(mode="json", exclude={"formula_sha256"})) != self.formula_sha256:
            raise ValueError("preseed formula hash drift")
        return self

    def parameter_paths(self):
        return tuple(
            f"gm.controllers.gm_wdma{controller}.residual_preseed.{parameter}"
            for controller in range(4)
            for parameter in ("startup_reported_ns", "packet_service_reported_ns")
        )

    def duration(self, work, controller, changes=None):
        work = FusedPreseedWork.model_validate_json(work.model_dump_json())
        if isinstance(controller, bool) or controller not in range(4):
            raise ValueError("preseed controller outside exact domain")
        changes = changes or {}
        paths = self.parameter_paths()
        if not set(changes) <= set(paths):
            raise ValueError("preseed requires an exact controller parameter path")
        selected = {
            f"gm.controllers.gm_wdma{controller}.residual_preseed.startup_reported_ns",
            f"gm.controllers.gm_wdma{controller}.residual_preseed.packet_service_reported_ns",
        }
        startup = changes.get(next(path for path in selected if path.endswith("startup_reported_ns")), self.startup_reported_ns_by_controller[controller])
        packet = changes.get(next(path for path in selected if path.endswith("packet_service_reported_ns")), self.packet_service_reported_ns_by_controller[controller])
        if any(isinstance(value, bool) or type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in (startup, packet)):
            raise ValueError("preseed local parameter must be finite and nonnegative")
        duration = startup + packet * work.packets_per_controller
        if duration <= 0:
            raise ValueError("preseed duration must be positive")
        return duration


class FusedPreseedDomain(FrozenRecord):
    work: FusedPreseedWork
    formula: FusedPreseedFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counter_callsites: tuple[int, int, int, int]
    terminal_counter_nodes: tuple[str, ...]
    source_refs: tuple[str, ...]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        timebase = self.timebase
        if (
            timebase.component_ids != ("M04",)
            or timebase.operation != self.work.operation
            or timebase.profile != self.work.profile
            or timebase.boundary_id != self.work.boundary
            or timebase.accepted_work_values != (self.work.payload_bytes_per_pe,)
            or timebase.source_unit != self.formula.source_unit
            or timebase.status != "validated_simulation"
            or self.terminal_counter_nodes != tuple(f"GM_WDMA{i}" for i in range(4))
            or len(self.counter_callsites) != 4
            or not self.source_refs
        ):
            raise ValueError("preseed domain identity/timebase mismatch")
        if stable_sha256(self.model_dump(mode="json", exclude={"domain_sha256"})) != self.domain_sha256:
            raise ValueError("preseed domain hash drift")
        return self


class FusedPreseedBinding(FrozenRecord):
    binding: SuppliedBinding
    resources: dict[str, str]
    buffer_binding: Literal[PRESEED_BUFFER] = PRESEED_BUFFER
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timing_owner: Literal[PRESEED_OWNER] = PRESEED_OWNER
    source_refs: tuple[str, ...]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        expected = {f"GM_WDMA{i}" for i in range(4)}
        units = {row["unit_id"] for row in self.binding.graph["execution_units"]}
        if set(self.resources) != expected or len(set(self.resources.values())) != 4 or not set(self.resources.values()) <= units or not self.source_refs:
            raise ValueError("preseed supplied controller binding incomplete")
        if stable_sha256(self.model_dump(mode="json", exclude={"binding_sha256"})) != self.binding_sha256:
            raise ValueError("preseed binding hash drift")
        return self


def _freeze(cls, hash_field, raw):
    raw = TypeAdapter(dict).dump_python(raw, mode="json")
    raw[hash_field] = stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))


def freeze_fused_preseed_formula(**kwargs):
    return _freeze(FusedPreseedFormula, "formula_sha256", dict(revision="M04-fused-residual-preseed-r2", source_unit="reported_ns", **kwargs))


def freeze_fused_preseed_domain(**kwargs):
    return _freeze(FusedPreseedDomain, "domain_sha256", kwargs)


def freeze_fused_preseed_binding(**kwargs):
    return _freeze(FusedPreseedBinding, "binding_sha256", dict(buffer_binding=PRESEED_BUFFER, timing_owner=PRESEED_OWNER, **kwargs))


def work_from_operation_plan(plan):
    if plan.profile != "transformer-fused":
        raise ValueError("residual preseed exists only in transformer-fused plan")
    stage = next(row for row in plan.stages if row.stage_id == "R4")
    if stage.attributes.get("gm_operation") != "write" or stage.dependencies != ("R3",):
        raise ValueError("preseed lowering requires the source R4 quant-upload predecessor")
    return FusedPreseedWork()


def resolve_fused_preseed(domains, *, work, model_library_sha256, systemc_library_sha256, observer_sha256, counter_callsites, physical=False):
    work = FusedPreseedWork.model_validate_json(work.model_dump_json())
    matches = [
        domain
        for raw in domains
        for domain in (FusedPreseedDomain.model_validate_json(raw.model_dump_json()),)
        if not physical
        and domain.work == work
        and domain.timebase.model_library_sha256 == model_library_sha256
        and domain.timebase.systemc_library_sha256 == systemc_library_sha256
        and domain.observer_sha256 == observer_sha256
        and domain.counter_callsites == tuple(counter_callsites)
    ]
    if len(matches) != 1:
        raise ValueError(f"preseed requires one exact simulation domain, found {len(matches)}")
    return matches[0]


def preseed_actions(domain, binding, *, before=("R4.quant_upload.complete",), changes=None):
    domain = FusedPreseedDomain.model_validate_json(domain.model_dump_json())
    binding = FusedPreseedBinding.model_validate_json(binding.model_dump_json())
    if binding.domain_sha256 != domain.domain_sha256:
        raise ValueError("preseed binding/domain mismatch")
    actions = []
    for controller in range(4):
        node = f"GM_WDMA{controller}"
        actions.append(
            ComponentAction(
                action_id=f"R4.residual_preseed.{node}",
                stage="R4.residual_preseed",
                component="M04",
                resource_id=binding.resources[node],
                work=float(domain.work.bytes_per_controller),
                work_unit="source-byte-per-controller",
                duration_cycles=domain.formula.duration(domain.work, controller, changes),
                duration_source=PRESEED_OWNER,
                calibration_sha256=domain.formula.formula_sha256,
                dependencies=tuple(before),
                bytes=domain.work.bytes_per_controller,
                participants=tuple(f"PE{pe}" for pe in range(32) if pe % 4 == controller) + (node,),
                status="calibrated",
            )
        )
    actions.append(
        ComponentAction(
            action_id="R4.residual_preseed.complete",
            stage="R4.residual_preseed",
            component="M04",
            resource_id=binding.resources["GM_WDMA0"],
            work=1,
            work_unit="four-controller-max-terminal",
            duration_cycles=0,
            duration_source="zero-cost dependency max; controller service already owned once",
            calibration_sha256=domain.domain_sha256,
            dependencies=tuple(action.action_id for action in actions),
            participants=tuple(f"PE{pe}" for pe in range(32)) + domain.terminal_counter_nodes,
            status="calibrated",
        )
    )
    return tuple(actions)


def execute_fused_preseed(domain, binding, hardware, *, before=("R4.quant_upload.complete",), changes=None, extra_actions=(), production=True, cancel=None):
    domain = FusedPreseedDomain.model_validate_json(domain.model_dump_json())
    binding = FusedPreseedBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:
        raise ValueError("production preseed requires real supplied binding")
    if any(action.stage == "R4.residual_preseed" or action.action_id.startswith("R4.residual_preseed") for action in extra_actions):
        raise ValueError("preseed timing ownership forbids duplicate charging")
    actions = preseed_actions(domain, binding, before=before, changes=changes)
    result = execute_actions(actions + tuple(extra_actions), binding.binding, hardware, production=production, cancel=cancel)
    result.receipt.update(
        fused_preseed_domain_sha256=domain.domain_sha256,
        fused_preseed_binding_sha256=binding.binding_sha256,
        timing_owner=PRESEED_OWNER,
        logical_source_bytes=domain.work.logical_source_bytes,
        endpoint_delivered_bytes=domain.work.logical_source_bytes,
        logical_transactions=32,
        controller_transactions={f"GM_WDMA{i}": 8 for i in range(4)},
        parameter_paths=list(domain.formula.parameter_paths()),
        source_unit=domain.formula.source_unit,
        simulation_seconds=convert_time_value(domain.timebase, max(action.duration_cycles for action in actions[:4])),
        physical_publishable=False,
        model_cycle_mapping=False,
        excluded_cost_owners=["R4.quant_upload", "R10.WRITE_SUM", "DataNoC", "Vector", "Matrix"],
    )
    if result.result.status == "complete":
        from .adalink_execute import _critical_to_completion
        result.receipt["event_resource_critical_path"] = _critical_to_completion(result.receipt["causal_graph"], "R4.residual_preseed.complete")
    return result


def append_fused_preseed_timebase(base, domain):
    base = TimebaseBundle.model_validate_json(base.model_dump_json())
    domain = FusedPreseedDomain.model_validate_json(domain.model_dump_json())
    if domain.timebase.domain_id in {row.domain_id for row in base.domains}:
        raise ValueError("duplicate preseed timebase")
    return freeze_timebase_bundle(
        bundle_id="T81-C09-plus-fused-residual-preseed",
        fixture_only=False,
        domains=base.domains + (domain.timebase,),
        source_refs=base.source_refs + domain.source_refs,
    )
