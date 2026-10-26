"""Exact fused E4 down-GEMM to E5 row-reduce local handoff.

The accepted E4 formula owns all Matrix engine work through the last named
MATRIX_WRITE completion.  This module owns only the source synchronization
tail after that boundary: PE columns 0..2 use MATRIX->UPLOAD1 and column 3
uses MATRIX->DOWNLOAD1 before the row-reduce consumer starts.
"""
from typing import Literal
import json, math

from pydantic import Field, TypeAdapter, model_validator

from .binding import SuppliedBinding
from .component_service import ComponentAction, execute_actions
from .timebase import TimebaseBundle, TimebaseDomain, convert_time_value, freeze_timebase_bundle
from .workload import FrozenRecord, stable_sha256


E4_E5_OWNER = "E4.down_gemm_to_E5.row_reduce.local_handoff_tail"
E4_E5_BOUNDARY = "max PE last MATRIX_WRITE completion -> max PE MATRIX-to-UPLOAD1/DOWNLOAD1 acquire/API completion"
E4_E5_PREDECESSOR_ENTRY = "m05-c05-transformer-fused-down-V64"


def role_for_pe(pe):
    if type(pe) is not int or pe < 0 or pe >= 32:
        raise ValueError("invalid E4/E5 PE")
    return "matrix_to_download1" if pe % 4 == 3 else "matrix_to_upload1"


class E4E5HandoffWork(FrozenRecord):
    profile: Literal["transformer-fused"] = "transformer-fused"
    producer_stage: Literal["E4"] = "E4"
    consumer_stage: Literal["E5"] = "E5"
    operation: Literal["down_gemm_row_reduce_handoff"] = "down_gemm_row_reduce_handoff"
    V: Literal[64] = 64
    pe_count: Literal[32] = 32
    pe_rows: Literal[8] = 8
    pe_columns: Literal[4] = 4
    down_shape_per_selection: tuple[Literal[1], Literal[256], Literal[256]] = (1, 256, 256)
    precision: Literal["W4A8-group256"] = "W4A8-group256"
    output_elements_per_pe: Literal[16384] = 16384
    output_bytes_per_pe: Literal[65536] = 65536
    channel: Literal[1] = 1
    upload_waiters: Literal[24] = 24
    download_waiters: Literal[8] = 8
    predecessor_entry_id: Literal[E4_E5_PREDECESSOR_ENTRY] = E4_E5_PREDECESSOR_ENTRY
    boundary: Literal[E4_E5_BOUNDARY] = E4_E5_BOUNDARY
    timing_ownership: Literal[E4_E5_OWNER] = E4_E5_OWNER
    source_commit: Literal["094bcd25e985a4c836fd5521cccd61b14d911aac"] = "094bcd25e985a4c836fd5521cccd61b14d911aac"
    source_callsite: Literal["moe_gemm.hpp:223-253;utils.hpp:41-51"] = "moe_gemm.hpp:223-253;utils.hpp:41-51"

    @model_validator(mode="after")
    def exact(self):
        if self.upload_waiters + self.download_waiters != self.pe_count or self.output_elements_per_pe * 4 != self.output_bytes_per_pe:
            raise ValueError("E4/E5 source work identity mismatch")
        return self


class E4E5PeTiming(FrozenRecord):
    pe: int = Field(ge=0, lt=32)
    role: Literal["matrix_to_upload1", "matrix_to_download1"]
    post_matrix_tail_ns: float = Field(ge=0)

    @model_validator(mode="after")
    def exact(self):
        if self.role != role_for_pe(self.pe) or not math.isfinite(self.post_matrix_tail_ns):
            raise ValueError("E4/E5 PE role/timing mismatch")
        return self


class E4E5HandoffFormula(FrozenRecord):
    revision: Literal["M07-fused-E4-E5-local-handoff-r1"] = "M07-fused-E4-E5-local-handoff-r1"
    source_unit: Literal["reported_ns"] = "reported_ns"
    pe_timings: tuple[E4E5PeTiming, ...]
    calibration_scene_ids: tuple[str, ...]
    applicability: str
    formula_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete(self):
        if tuple(row.pe for row in self.pe_timings) != tuple(range(32)) or len(self.calibration_scene_ids) < 2 or len(set(self.calibration_scene_ids)) != len(self.calibration_scene_ids) or not self.applicability:
            raise ValueError("E4/E5 formula inventory incomplete")
        if stable_sha256(self.model_dump(mode="json", exclude={"formula_sha256"})) != self.formula_sha256:
            raise ValueError("E4/E5 formula hash drift")
        return self

    def parameter_paths(self):
        return tuple(f"sync.e4_e5.pe{row.pe}.{row.role}.post_matrix_tail_reported_ns" for row in self.pe_timings)

    def duration(self, pe, changes=None):
        changes = changes or {}
        if not set(changes) <= set(self.parameter_paths()) or len(changes) > 1:
            raise ValueError("E4/E5 accepts one exact local parameter change")
        row = self.pe_timings[pe]
        path = f"sync.e4_e5.pe{pe}.{row.role}.post_matrix_tail_reported_ns"
        value = changes.get(path, row.post_matrix_tail_ns)
        if isinstance(value, bool) or type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("E4/E5 parameter must be finite and nonnegative")
        return float(value)

    def terminal(self, changes=None):
        return max(self.duration(pe, changes) for pe in range(32))


class E4E5ObservedEvent(FrozenRecord):
    pe: int = Field(ge=0, lt=32)
    role: Literal["matrix_to_upload1", "matrix_to_download1"]
    matrix_write_end_ns: float = Field(ge=0)
    release_issue_ns: float = Field(ge=0)
    acquire_start_ns: float = Field(ge=0)
    acquire_end_ns: float = Field(ge=0)
    api_begin_ns: float = Field(ge=0)
    api_end_ns: float = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        values = (self.matrix_write_end_ns, self.release_issue_ns, self.acquire_start_ns, self.acquire_end_ns, self.api_begin_ns, self.api_end_ns)
        if not all(math.isfinite(value) for value in values) or self.role != role_for_pe(self.pe):
            raise ValueError("E4/E5 observed identity invalid")
        if self.acquire_end_ns < self.acquire_start_ns or self.api_end_ns < self.api_begin_ns or self.release_issue_ns > self.acquire_end_ns:
            raise ValueError("E4/E5 observed event order invalid")
        return self

    def terminal_ns(self):
        return max(self.matrix_write_end_ns, self.acquire_end_ns, self.api_end_ns)

    def post_matrix_tail_ns(self):
        return self.terminal_ns() - self.matrix_write_end_ns


def validate_e4_e5_event_inventory(events):
    events = tuple(E4E5ObservedEvent.model_validate_json(event.model_dump_json()) for event in events)
    if len(events) != 32 or {event.pe for event in events} != set(range(32)) or len({event.pe for event in events}) != 32:
        raise ValueError("E4/E5 duplicate/missing event inventory")
    if sum(event.role == "matrix_to_upload1" for event in events) != 24 or sum(event.role == "matrix_to_download1" for event in events) != 8:
        raise ValueError("E4/E5 role inventory mismatch")
    return tuple(sorted(events, key=lambda event: event.pe))


class E4E5HandoffDomain(FrozenRecord):
    work: E4E5HandoffWork
    formula: E4E5HandoffFormula
    timebase: TimebaseDomain
    observer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counter_callsite: int = Field(ge=0)
    source_refs: tuple[str, ...]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact(self):
        timebase = self.timebase
        if timebase.component_ids != ("M07",) or timebase.operation != self.work.operation or timebase.profile != self.work.profile or timebase.boundary_id != self.work.boundary or timebase.source_unit != self.formula.source_unit or timebase.accepted_work_values != (64,) or timebase.status != "validated_simulation" or not self.source_refs:
            raise ValueError("E4/E5 exact domain mismatch")
        if stable_sha256(self.model_dump(mode="json", exclude={"domain_sha256"})) != self.domain_sha256:
            raise ValueError("E4/E5 domain hash drift")
        return self


class E4E5HandoffBinding(FrozenRecord):
    binding: SuppliedBinding
    resources: dict[str, str]
    domain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timing_owner: Literal[E4_E5_OWNER] = E4_E5_OWNER
    source_refs: tuple[str, ...]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact(self):
        expected = {f"PE{pe}.{role_for_pe(pe)}" for pe in range(32)}
        units = {row["unit_id"] for row in self.binding.graph["execution_units"]}
        if set(self.resources) != expected or len(set(self.resources.values())) != 32 or not set(self.resources.values()) <= units or not self.source_refs:
            raise ValueError("E4/E5 supplied resource binding incomplete")
        if stable_sha256(self.model_dump(mode="json", exclude={"binding_sha256"})) != self.binding_sha256:
            raise ValueError("E4/E5 binding hash drift")
        return self


def _freeze(cls, field, raw):
    raw = TypeAdapter(dict).dump_python(raw, mode="json")
    raw[field] = stable_sha256(raw)
    return cls.model_validate_json(json.dumps(raw))


def freeze_e4_e5_formula(**kwargs):
    return _freeze(E4E5HandoffFormula, "formula_sha256", dict(revision="M07-fused-E4-E5-local-handoff-r1", source_unit="reported_ns", **kwargs))


def freeze_e4_e5_domain(**kwargs): return _freeze(E4E5HandoffDomain, "domain_sha256", kwargs)
def freeze_e4_e5_binding(**kwargs): return _freeze(E4E5HandoffBinding, "binding_sha256", dict(timing_owner=E4_E5_OWNER, **kwargs))


def resolve_e4_e5_handoff(domains, *, work, model_library_sha256, systemc_library_sha256, observer_sha256, counter_callsite, physical=False):
    work = E4E5HandoffWork.model_validate_json(work.model_dump_json())
    matches = []
    for raw in domains:
        domain = E4E5HandoffDomain.model_validate_json(raw.model_dump_json())
        identity = (domain.timebase.model_library_sha256, domain.timebase.systemc_library_sha256, domain.observer_sha256, domain.counter_callsite)
        if not physical and domain.work == work and identity == (model_library_sha256, systemc_library_sha256, observer_sha256, counter_callsite):
            matches.append(domain)
    if len(matches) != 1: raise ValueError(f"E4/E5 handoff requires one exact domain, found {len(matches)}")
    return matches[0]


def e4_e5_handoff_actions(domain, binding, *, before=("E4.down.complete",), changes=None):
    domain = E4E5HandoffDomain.model_validate_json(domain.model_dump_json())
    binding = E4E5HandoffBinding.model_validate_json(binding.model_dump_json())
    if binding.domain_sha256 != domain.domain_sha256: raise ValueError("E4/E5 binding/domain mismatch")
    actions = []
    for pe in range(32):
        role = role_for_pe(pe)
        actions.append(ComponentAction(action_id=f"E4_E5.handoff.pe{pe}.{role}", stage="E4_E5.handoff", component="M07", resource_id=binding.resources[f"PE{pe}.{role}"], work=65536, work_unit="down-result-byte-ready", duration_cycles=domain.formula.duration(pe, changes), duration_source=E4_E5_OWNER, calibration_sha256=domain.formula.formula_sha256, dependencies=tuple(before), bytes=0, participants=(f"PE{pe}.MATRIX", f"PE{pe}.{'DOWNLOAD1' if pe % 4 == 3 else 'UPLOAD1'}"), status="calibrated"))
    actions.append(ComponentAction(action_id="E4_E5.handoff.complete", stage="E4_E5.handoff", component="M07", resource_id=binding.resources["PE0.matrix_to_upload1"], work=32, work_unit="after-all-local-handoffs", duration_cycles=0, duration_source="zero-cost max; local tails already owned", calibration_sha256=domain.domain_sha256, dependencies=tuple(action.action_id for action in actions), participants=tuple(f"PE{pe}" for pe in range(32)), status="calibrated"))
    return tuple(actions)


def execute_e4_e5_handoff(domain, binding, hardware, *, before=("E4.down.complete",), changes=None, extra_actions=(), production=True, cancel=None):
    domain = E4E5HandoffDomain.model_validate_json(domain.model_dump_json())
    binding = E4E5HandoffBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only: raise ValueError("production E4/E5 handoff requires supplied binding")
    if any(action.stage == "E4_E5.handoff" or action.action_id.startswith("E4_E5.handoff") for action in extra_actions): raise ValueError("E4/E5 duplicate timing owner")
    actions = e4_e5_handoff_actions(domain, binding, before=before, changes=changes)
    result = execute_actions(actions + tuple(extra_actions), binding.binding, hardware, production=production, cancel=cancel)
    result.receipt.update(e4_e5_domain_sha256=domain.domain_sha256, e4_e5_binding_sha256=binding.binding_sha256, timing_owner=E4_E5_OWNER, predecessor_entry_id=E4_E5_PREDECESSOR_ENTRY, profile="transformer-fused", V=64, pe_count=32, output_bytes_per_pe=65536, channel=1, upload_waiters=24, download_waiters=8, parameter_paths=list(domain.formula.parameter_paths()), simulation_seconds=convert_time_value(domain.timebase, domain.formula.terminal(changes)), excluded_cost_owners=["E4.down.Matrix", "E5.row_chain", "E6.GM_scatter", "DataNoC", "Vector"], physical_publishable=False, model_cycle_mapping=False)
    if result.result.status == "complete":
        from .adalink_execute import _critical_to_completion
        result.receipt["event_resource_critical_path"] = _critical_to_completion(result.receipt["causal_graph"], "E4_E5.handoff.complete")
    return result


def append_e4_e5_timebase(base, domain):
    base = TimebaseBundle.model_validate_json(base.model_dump_json())
    domain = E4E5HandoffDomain.model_validate_json(domain.model_dump_json())
    if domain.timebase.domain_id in {row.domain_id for row in base.domains}: raise ValueError("duplicate E4/E5 timebase")
    return freeze_timebase_bundle(bundle_id="T81-C09-plus-fused-E4-E5-handoff", fixture_only=False, domains=base.domains + (domain.timebase,), source_refs=base.source_refs + domain.source_refs)
