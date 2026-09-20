"""Evidence-linked capabilities and deliberately scoped requirement coverage."""

from pydantic import JsonValue

from ..configs.schemas.validation import (
    CaseCapabilities,
    CaseResult,
    CheckResult,
    CoverageCheck,
    Metadata,
    RequirementCoverage,
    ValidationSuite,
)
from .adapters import Admission
from .comparison import model_metadata

PARENT_REQUIREMENTS = {
    "VA-01": ("VA-D02", "VA-D07", "VA-D09"),
    "VA-02": ("VA-D01", "VA-D03", "VA-D04", "VA-D07"),
    "VA-03": ("VA-D06", "VA-D10", "VA-D11"),
    "VA-04": ("VA-D05", "VA-D06"),
    "VA-05": ("VA-D07", "VA-D08"),
    "VA-06": ("VA-D02", "VA-D09", "VA-D10"),
    "VA-07": ("VA-D11",),
}


def has_faults(value: JsonValue) -> bool:
    if isinstance(value, list):
        return any(has_faults(item) for item in value)
    if isinstance(value, dict):
        return any((key in {"slowdowns", "failures", "faults"} and bool(item)) or has_faults(item)
                   for key, item in value.items())
    return False


def case_capabilities(admitted: Admission, case: CaseResult) -> CaseCapabilities:
    if case.identity is None:
        raise ValueError("capability report requires admitted identity")
    metadata = model_metadata(admitted)
    mixed = admitted.adapter == "multicast_sync_v1" and admitted.configuration.get("runtime") is not None
    mechanisms = [admitted.adapter]
    if admitted.adapter in {"torus_replay_v2", "memory_replay_v1", "compute_workload_v1"}:
        mechanisms += ["bounded_unicast", "credit_flow"]
    if admitted.adapter in {"memory_replay_v1", "compute_workload_v1"}:
        mechanisms += ["addressed_memory", "physical_memory_service", "posted_drain"]
    if admitted.adapter == "compute_workload_v1":
        mechanisms += ["abstract_compute_cost", "bounded_stream_overlap"]
    if admitted.adapter == "multicast_sync_v1":
        mechanisms += (["rectangle_multicast", "shared_physical_transport", "addressed_memory", "shared_l1_atomics",
                        "local_threshold_waits", "retained_resume"] if mixed else
                       ["rectangle_tree_planning", "serial_multicast_projection"])
        if mixed and admitted.configuration.get("compute") is not None:
            mechanisms += ["abstract_compute_cost", "bounded_stream_overlap", "slot_generations"]
    faults = has_faults(admitted.configuration)
    assumptions = ["Rates, capacities, layout and clock domains are taken from the admitted effective plan.",
                   "Finite scheduling/traffic observations do not execute tensor values or device kernels."]
    if mixed:
        assumptions.append("Atomic tree reservation and per-class endpoint storage are configured finite model policies; silicon timing is unvalidated.")
    elif admitted.adapter == "multicast_sync_v1":
        assumptions.append("The child is a prototype: shared transport/memory/compute service and retained runtime resume are not implemented.")
    if faults:
        assumptions.append("Configured faults are simulation experiments; a passing model audit does not establish measured fault behavior.")
    return CaseCapabilities(
        case_id=case.case_id,
        architecture=Metadata[str](state="known", value=str(metadata["architecture"])),
        profile_version=Metadata[str](state="known", value=str(metadata["profile_version"])),
        enabled_mechanisms=tuple(mechanisms),
        clocks=case.observations[-1].clocks if case.observations else (),
        effective_plan_sha256=case.identity.effective_plan_sha256,
        simulation_fault_experiment=faults, assumptions=tuple(assumptions),
        unsupported=("tensor_values", "kernel_execution", "multi_asic", "arbitrary_multicast", "general_atomics") if mixed else
                    ("tensor_values", "kernel_execution", "multi_asic", "shared_multicast_runtime", "scalar_network",
                     "shared_l1_atomics", "pipeline_generations", "retained_resume") if admitted.adapter == "multicast_sync_v1" else
                    ("tensor_values", "kernel_execution", "multicast", "synchronization", "multi_asic"),
    )


def requirement_coverage(suite: ValidationSuite, cases: tuple[CaseResult, ...],
                         gates: tuple[CheckResult, ...]) -> tuple[RequirementCoverage, ...]:
    by_requirement: dict[str, list[CoverageCheck]] = {f"VA-D{i:02}": [] for i in range(1, 12)}
    lookup: dict[tuple[str | None, str], CheckResult] = {(case.case_id, c.check_id): c for case in cases for c in case.checks}
    lookup.update({(None, c.check_id): c for c in gates})
    for case in suite.cases:
        for check in case.checks:
            for requirement in check.requirements:
                by_requirement.setdefault(requirement, []).append(CoverageCheck(case_id=case.case_id, check_id=check.check_id))
    for gate in suite.gates:
        for requirement in gate.requirements:
            by_requirement.setdefault(requirement, []).append(CoverageCheck(case_id=None, check_id=gate.gate_id))
    for parent, children in PARENT_REQUIREMENTS.items():
        by_requirement[parent] = list(dict.fromkeys(c for child in children for c in by_requirement[child]))
    commits = tuple(sorted({c.identity.source.revision.value for c in cases if c.identity is not None
                            and c.identity.source.dirty.value is False and c.identity.source.revision.value is not None}))
    coverage: list[RequirementCoverage] = []
    for requirement, checks in by_requirement.items():
        selected = tuple(lookup[(c.case_id, c.check_id)] for c in checks)
        status = "pending" if not checks else "blocked" if all(c.outcome in {"blocked", "not_run", "unsupported"} for c in selected) else "partial"
        coverage.append(RequirementCoverage(
            requirement_id=requirement, status=status, checks=tuple(checks), child_commits=commits if checks else (),
            reason="No check selected in this run." if not checks else
            "Coverage is limited to these executed outcomes; complete acceptance and predecessor evidence are mapped in the child delivery document."
            + (" Source has local or unknown changes; no committed implementation is asserted." if not commits else ""),
        ))
    mixed_observed = any(case.adapter == "multicast_sync_v1" and any(e.event_id == "snapshot" for o in case.observations for e in o.events) for case in cases)
    for mechanism in (() if mixed_observed else ("multicast", "synchronization")):
        coverage.append(RequirementCoverage(requirement_id=f"pending_{mechanism}", status="pending", checks=(), child_commits=(),
                                            reason="The complete shared runtime remains pending in wormhole-multicast-sync; prototype checks do not establish delivery."))
    return tuple(coverage)
