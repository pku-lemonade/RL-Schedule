"""Exact transformer-fused attention-output RMSNorm primitive.

The source operation is one in-place ``rms_norm<2048, float>`` on every PE,
followed by ``sync_vector_self``.  Timing ownership is split into observable
VECTOR_READ/CAL/WRITE intervals, their issue offsets, and the terminal sync
tail.  It is deliberately not the later residual_add_rms_norm operation.
"""
from typing import Literal
import json, math

from pydantic import Field, TypeAdapter, model_validator

from .binding import SuppliedBinding
from .component_service import ComponentAction, execute_actions
from .timebase import TimebaseBundle, TimebaseDomain, convert_time_value, freeze_timebase_bundle
from .workload import FrozenRecord, stable_sha256


ATTN_NORM_OWNER = "R0.attn_output_norm.inplace_vector_and_self_sync"
ATTN_NORM_BOUNDARY = (
    "earliest PE primitive counter begin -> max PE VECTOR_WRITE completion "
    "and sync_vector_self API return for in-place rms_norm<2048,float>"
)
ENGINES = ("VECTOR_READ", "VECTOR_CAL", "VECTOR_WRITE")


class AttnOutputNormWork(FrozenRecord):
    profile: Literal["transformer-fused"] = "transformer-fused"
    consumer_stage: Literal["R0.attn_output_norm"] = "R0.attn_output_norm"
    operation: Literal["attn_output_norm"] = "attn_output_norm"
    hidden_elements_per_pe: Literal[2048] = 2048
    pe_count: Literal[32] = 32
    activation_element: Literal["fp32"] = "fp32"
    weight_element: Literal["bf16"] = "bf16"
    output_element: Literal["fp32"] = "fp32"
    activation_bytes_per_pe: Literal[8192] = 8192
    weight_bytes_per_pe: Literal[4096] = 4096
    output_bytes_per_pe: Literal[8192] = 8192
    in_place: Literal[True] = True
    vector_launches_per_pe: Literal[1] = 1
    terminal_sync: Literal["sync_vector_self"] = "sync_vector_self"
    boundary: Literal[ATTN_NORM_BOUNDARY] = ATTN_NORM_BOUNDARY
    timing_ownership: Literal[ATTN_NORM_OWNER] = ATTN_NORM_OWNER
    source_commit: Literal["094bcd25e985a4c836fd5521cccd61b14d911aac"] = "094bcd25e985a4c836fd5521cccd61b14d911aac"
    source_callsite: Literal["router_gemm.hpp:340-365"] = "router_gemm.hpp:340-365"


class AttnOutputNormPeTiming(FrozenRecord):
    pe: int = Field(ge=0, lt=32)
    dispatch_to_read_ns: float = Field(ge=0)
    read_service_ns: float = Field(ge=0)
    read_to_cal_issue_ns: float = Field(ge=0)
    cal_service_ns: float = Field(ge=0)
    cal_to_write_issue_ns: float = Field(ge=0)
    write_service_ns: float = Field(ge=0)
    sync_tail_ns: float = Field(ge=0)

    @model_validator(mode="after")
    def finite(self):
        values = (
            self.dispatch_to_read_ns,
            self.read_service_ns,
            self.read_to_cal_issue_ns,
            self.cal_service_ns,
            self.cal_to_write_issue_ns,
            self.write_service_ns,
            self.sync_tail_ns,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("attn-output-norm PE timing must be finite")
        return self

    @property
    def write_issue_ns(self):
        return self.read_to_cal_issue_ns + self.cal_to_write_issue_ns


class AttnOutputNormFormula(FrozenRecord):
    revision: Literal["M06-fused-attn-output-norm-r1"] = "M06-fused-attn-output-norm-r1"
    source_unit: Literal["reported_ns"] = "reported_ns"
    pe_timings: tuple[AttnOutputNormPeTiming, ...]
    calibration_scene_ids: tuple[str, ...]
    applicability: str
    formula_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete(self):
        if tuple(row.pe for row in self.pe_timings) != tuple(range(32)):
            raise ValueError("attn-output-norm needs one ordered timing row per PE")
        if len(self.calibration_scene_ids) < 2 or len(set(self.calibration_scene_ids)) != len(self.calibration_scene_ids):
            raise ValueError("attn-output-norm needs disjoint calibration identities")
        if not self.applicability:
            raise ValueError("attn-output-norm applicability required")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"formula_sha256"}))
        if expected != self.formula_sha256:
            raise ValueError("attn-output-norm formula hash drift")
        return self

    def parameter_paths(self):
        suffixes = (
            "dispatch_to_read_reported_ns",
            "vector_read.service_reported_ns",
            "read_to_cal_issue_reported_ns",
            "vector_cal.service_reported_ns",
            "cal_to_write_issue_reported_ns",
            "vector_write.service_reported_ns",
            "sync_vector_self.tail_reported_ns",
        )
        return tuple(f"vector.attn_output_norm.pe{pe}.{suffix}" for pe in range(32) for suffix in suffixes)

    def _values(self, pe, changes=None):
        if type(pe) is not int or pe < 0 or pe >= 32:
            raise ValueError("invalid attn-output-norm PE")
        changes = changes or {}
        known = set(self.parameter_paths())
        if not set(changes) <= known or len(changes) > 1:
            raise ValueError("attn-output-norm accepts one exact local parameter change")
        row = self.pe_timings[pe]
        names = (
            "dispatch_to_read_reported_ns",
            "vector_read.service_reported_ns",
            "read_to_cal_issue_reported_ns",
            "vector_cal.service_reported_ns",
            "cal_to_write_issue_reported_ns",
            "vector_write.service_reported_ns",
            "sync_vector_self.tail_reported_ns",
        )
        base = (
            row.dispatch_to_read_ns,
            row.read_service_ns,
            row.read_to_cal_issue_ns,
            row.cal_service_ns,
            row.cal_to_write_issue_ns,
            row.write_service_ns,
            row.sync_tail_ns,
        )
        values = []
        for name, value in zip(names, base):
            candidate = changes.get(f"vector.attn_output_norm.pe{pe}.{name}", value)
            if isinstance(candidate, bool) or type(candidate) not in (int, float) or not math.isfinite(candidate) or candidate < 0:
                raise ValueError("attn-output-norm parameter must be finite and nonnegative")
            values.append(float(candidate))
        return tuple(values)

    def pe_terminal(self, pe, changes=None):
        dispatch, read, to_cal, cal, to_write, write, tail = self._values(pe, changes)
        return dispatch + max(read, to_cal + cal, to_cal + to_write + write) + tail

    def terminal(self, changes=None):
        return max(self.pe_terminal(pe, changes) for pe in range(32))


class AttnOutputNormDomain(FrozenRecord):
    work: AttnOutputNormWork
    formula: AttnOutputNormFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counter_callsite: int = Field(ge=0)
    source_refs: tuple[str, ...]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact(self):
        timebase = self.timebase
        if (
            timebase.component_ids != ("M06",)
            or timebase.operation != self.work.operation
            or timebase.profile != self.work.profile
            or timebase.boundary_id != self.work.boundary
            or timebase.source_unit != self.formula.source_unit
            or timebase.accepted_work_values != (2048,)
            or timebase.status != "validated_simulation"
            or not self.source_refs
        ):
            raise ValueError("attn-output-norm exact domain mismatch")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"domain_sha256"}))
        if expected != self.domain_sha256:
            raise ValueError("attn-output-norm domain hash drift")
        return self


class AttnOutputNormBinding(FrozenRecord):
    binding: SuppliedBinding
    resources: dict[str, str]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timing_owner: Literal[ATTN_NORM_OWNER] = ATTN_NORM_OWNER
    source_refs: tuple[str, ...]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact(self):
        suffixes = ("VECTOR_ISSUE", "VECTOR_READ", "VECTOR_CAL", "VECTOR_WRITE", "VECTOR_SYNC")
        expected = {f"PE{pe}.{suffix}" for pe in range(32) for suffix in suffixes}
        units = {row["unit_id"] for row in self.binding.graph["execution_units"]}
        if set(self.resources) != expected or len(set(self.resources.values())) != len(expected):
            raise ValueError("attn-output-norm supplied resource map incomplete")
        if not set(self.resources.values()) <= units or not self.source_refs:
            raise ValueError("attn-output-norm resource absent from supplied graph")
        expected_hash = stable_sha256(self.model_dump(mode="json", exclude={"binding_sha256"}))
        if expected_hash != self.binding_sha256:
            raise ValueError("attn-output-norm binding hash drift")
        return self


def _freeze(cls, field, raw):
    raw = TypeAdapter(dict).dump_python(raw, mode="json")
    raw[field] = stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))


def freeze_attn_output_norm_formula(**kwargs):
    return _freeze(
        AttnOutputNormFormula,
        "formula_sha256",
        dict(revision="M06-fused-attn-output-norm-r1", source_unit="reported_ns", **kwargs),
    )


def freeze_attn_output_norm_domain(**kwargs):
    return _freeze(AttnOutputNormDomain, "domain_sha256", kwargs)


def freeze_attn_output_norm_binding(**kwargs):
    return _freeze(
        AttnOutputNormBinding,
        "binding_sha256",
        dict(timing_owner=ATTN_NORM_OWNER, **kwargs),
    )


def resolve_attn_output_norm(
    domains,
    *,
    work,
    model_library_sha256,
    systemc_library_sha256,
    observer_sha256,
    counter_callsite,
    physical=False,
):
    work = AttnOutputNormWork.model_validate_json(work.model_dump_json())
    matches = []
    for raw in domains:
        domain = AttnOutputNormDomain.model_validate_json(raw.model_dump_json())
        identity = (
            domain.timebase.model_library_sha256,
            domain.timebase.systemc_library_sha256,
            domain.observer_sha256,
            domain.counter_callsite,
        )
        requested = (model_library_sha256, systemc_library_sha256, observer_sha256, counter_callsite)
        if not physical and domain.work == work and identity == requested:
            matches.append(domain)
    if len(matches) != 1:
        raise ValueError(f"attn-output-norm requires one exact domain, found {len(matches)}")
    return matches[0]


def attn_output_norm_actions(domain, binding, *, before=("activation_bridge.complete",), changes=None):
    domain = AttnOutputNormDomain.model_validate_json(domain.model_dump_json())
    binding = AttnOutputNormBinding.model_validate_json(binding.model_dump_json())
    if binding.domain_sha256 != domain.domain_sha256:
        raise ValueError("attn-output-norm binding/domain mismatch")
    actions = []
    for pe in range(32):
        dispatch, read, to_cal, cal, to_write, write, tail = domain.formula._values(pe, changes)
        resource = lambda suffix: binding.resources[f"PE{pe}.{suffix}"]
        dispatch_id = f"R0.attn_output_norm.pe{pe}.dispatch_to_read"
        read_id = f"R0.attn_output_norm.pe{pe}.VECTOR_READ"
        cal_issue_id = f"R0.attn_output_norm.pe{pe}.read_to_cal_issue"
        cal_id = f"R0.attn_output_norm.pe{pe}.VECTOR_CAL"
        write_issue_id = f"R0.attn_output_norm.pe{pe}.cal_to_write_issue"
        write_id = f"R0.attn_output_norm.pe{pe}.VECTOR_WRITE"
        sync_id = f"R0.attn_output_norm.pe{pe}.sync_vector_self"
        common = dict(stage="R0.attn_output_norm", component="M06", calibration_sha256=domain.formula.formula_sha256, participants=(f"PE{pe}",), status="calibrated")
        actions.extend((
            ComponentAction(action_id=dispatch_id, resource_id=resource("VECTOR_ISSUE"), work=1, work_unit="kernel-to-vector-read-dispatch", duration_cycles=dispatch, duration_source=ATTN_NORM_OWNER+".dispatch", dependencies=tuple(before), **common),
            ComponentAction(action_id=read_id, resource_id=resource("VECTOR_READ"), work=12288, work_unit="activation-and-weight-byte", duration_cycles=read, duration_source=ATTN_NORM_OWNER+".VECTOR_READ", dependencies=(dispatch_id,), bytes=12288, **common),
            ComponentAction(action_id=cal_issue_id, resource_id=resource("VECTOR_ISSUE"), work=1, work_unit="read-to-cal-issue", duration_cycles=to_cal, duration_source=ATTN_NORM_OWNER+".read_to_cal_issue", dependencies=(dispatch_id,), **common),
            ComponentAction(action_id=cal_id, resource_id=resource("VECTOR_CAL"), work=2048, work_unit="fp32-element", duration_cycles=cal, duration_source=ATTN_NORM_OWNER+".VECTOR_CAL", dependencies=(cal_issue_id,), **common),
            ComponentAction(action_id=write_issue_id, resource_id=resource("VECTOR_ISSUE"), work=1, work_unit="cal-to-write-issue", duration_cycles=to_write, duration_source=ATTN_NORM_OWNER+".cal_to_write_issue", dependencies=(cal_issue_id,), **common),
            ComponentAction(action_id=write_id, resource_id=resource("VECTOR_WRITE"), work=8192, work_unit="inplace-output-byte", duration_cycles=write, duration_source=ATTN_NORM_OWNER+".VECTOR_WRITE", dependencies=(write_issue_id,), bytes=8192, **common),
            ComponentAction(action_id=sync_id, resource_id=resource("VECTOR_SYNC"), work=1, work_unit="sync-vector-self-terminal", duration_cycles=tail, duration_source=ATTN_NORM_OWNER+".sync_tail", dependencies=(read_id, cal_id, write_id), **common),
        ))
    actions.append(ComponentAction(
        action_id="R0.attn_output_norm.complete",
        stage="R0.attn_output_norm",
        component="M06",
        resource_id=binding.resources["PE0.VECTOR_SYNC"],
        work=32,
        work_unit="after-all-PE-terminals",
        duration_cycles=0,
        duration_source="zero-cost max; PE segment costs already owned",
        calibration_sha256=domain.domain_sha256,
        dependencies=tuple(f"R0.attn_output_norm.pe{pe}.sync_vector_self" for pe in range(32)),
        participants=tuple(f"PE{pe}" for pe in range(32)),
        status="calibrated",
    ))
    return tuple(actions)


def execute_attn_output_norm(
    domain,
    binding,
    hardware,
    *,
    before=("activation_bridge.complete",),
    changes=None,
    extra_actions=(),
    production=True,
    cancel=None,
):
    domain = AttnOutputNormDomain.model_validate_json(domain.model_dump_json())
    binding = AttnOutputNormBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:
        raise ValueError("production attn-output-norm requires supplied binding")
    if any(action.stage == "R0.attn_output_norm" or action.action_id.startswith("R0.attn_output_norm") for action in extra_actions):
        raise ValueError("attn-output-norm duplicate timing owner")
    actions = attn_output_norm_actions(domain, binding, before=before, changes=changes)
    result = execute_actions(actions + tuple(extra_actions), binding.binding, hardware, production=production, cancel=cancel)
    result.receipt.update(
        attn_output_norm_domain_sha256=domain.domain_sha256,
        attn_output_norm_binding_sha256=binding.binding_sha256,
        timing_owner=ATTN_NORM_OWNER,
        operation=domain.work.operation,
        profile=domain.work.profile,
        hidden_elements_per_pe=2048,
        pe_count=32,
        vector_launches_per_pe=1,
        activation_bytes_per_pe=8192,
        weight_bytes_per_pe=4096,
        output_bytes_per_pe=8192,
        in_place=True,
        weight_sram_not_data_noc=True,
        parameter_paths=list(domain.formula.parameter_paths()),
        simulation_seconds=convert_time_value(domain.timebase, domain.formula.terminal(changes)),
        excluded_cost_owners=["activation_bridge", "R1.residual_add_rms_norm", "DataNoC", "Matrix", "GM"],
        physical_publishable=False,
        model_cycle_mapping=False,
    )
    if result.result.status == "complete":
        from .adalink_execute import _critical_to_completion
        result.receipt["event_resource_critical_path"] = _critical_to_completion(result.receipt["causal_graph"], "R0.attn_output_norm.complete")
    return result


def append_attn_output_norm_timebase(base, domain):
    base = TimebaseBundle.model_validate_json(base.model_dump_json())
    domain = AttnOutputNormDomain.model_validate_json(domain.model_dump_json())
    if domain.timebase.domain_id in {row.domain_id for row in base.domains}:
        raise ValueError("duplicate attn-output-norm timebase")
    return freeze_timebase_bundle(
        bundle_id="T81-C09-plus-fused-attn-output-norm",
        fixture_only=False,
        domains=base.domains + (domain.timebase,),
        source_refs=base.source_refs + domain.source_refs,
    )
