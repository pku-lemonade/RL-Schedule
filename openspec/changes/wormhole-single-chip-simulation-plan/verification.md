# Verification report: Wormhole single-chip simulation plan

**Closure update:** task 8.3 is now complete. The [reconciliation delivery](reconciliation.md) and [canonical identities](spec-sync-identities.json) record seven validated main specs, 100 preserved requirement IDs and 178 scenarios. The umbrella is **38/38**. The report below is the historical evidence audit at `9d5f000`; its 37/38 status, empty-main-spec finding and pending VA-07 baseline disposition describe that earlier checkpoint. That specification-closure issue is resolved. Optional ML, historical root local-remap and external accuracy limitations remain. No change has been archived or pushed.

Reviewed on 2026-09-20 against `f6ca63b` and its exact source/input manifest.
The seven implementation children are complete. This audit closes the retrospective
child milestones and umbrella tasks 8.1–8.2, bringing the parent to **37/38**.
**Task 8.3 remains open:** no canonical main specifications have been delivered,
and the umbrella is not ready for archival.

This is a documentation-only review of the delivered finite single-ASIC abstract
model. It does not expand the supported protocol, workload, consumer or accuracy
claims. The machine-readable [evidence map](evidence-map.json) records all 27
parent requirements, exact implementation symbols, executable test identifiers,
all 30 named scenarios, child requirement links and the boundary of each claim.
The [reconciliation plan](reconciliation.md) identifies the remaining baseline work.

## Scorecard

| Dimension | Reviewed result |
| --- | --- |
| Child task completeness | 233/233 across seven independently delivered children |
| Parent milestone completeness | 37/38; only specification reconciliation, 8.3, remains |
| Parent requirement evidence | 27/27 mapped: 26 have executable regressions; VA-07 is an explicitly reviewed process obligation with baseline delivery pending |
| Parent scenario evidence | 30/30 mapped: 29 executable scenarios and the VA-07 planning-versus-delivery process scenario |
| Fresh executable verification | 77 existing tests passed in two batches; no failures, errors or skips |
| Coherence | Explicit opt-in adapters, configurable resources, one physical owner, versioned compatibility and bounded evidence tiers retained |
| Critical closure issue | Main-spec directory is empty; do not archive until task 8.3 is complete |

“Reviewed” means supported within the bounds recorded in the child contracts and
evidence map. It does not mean every requirement is implemented by every adapter,
that hardware accuracy is validated, or that the main specification baseline has
already been synchronized. VA-07 is respected by retaining the incomplete closure
milestone rather than promoting planning artifacts to delivered specifications.

## Milestone reconciliation

The artifact and delivery records below establish exploration decisions, concrete
plans, environment/baseline checks, implementation and exit gates. Each child's
proposal/design/specs/tasks were reviewed alongside its delivery, rather than
using checked boxes alone. Historical “pending next child” statements remain
correct records of those delivery boundaries; this audit provides their current
cross-child disposition.

| Parent milestones | Child / tasks | Delivery revision | Reviewed evidence and handoff |
| --- | --- | --- | --- |
| 1.1–1.5 | `wormhole-hardware-profile`, 23/23 | `9b4770b` | [Design and baseline](../wormhole-hardware-profile/design.md), [52-test delivery and topology handoff](../../../simulator_detailed/docs/hardware_profile.md#validation-environment-and-evidence); the original 29-test baseline and unavailable initial tools are distinguished from successful checks |
| 2.1–2.5 | `generic-heterogeneous-topology`, 30/30 | `ff37a79` | [Design](../generic-heterogeneous-topology/design.md), [84-test delivery, independent graph/legacy oracles and routing handoff](../../../simulator_detailed/docs/topology.md#integrated-delivery-evidence-and-next-child-boundary) |
| 3.1–3.5 | `wormhole-dual-noc-routing`, 35/35 | `a3cc2a4` | [Design](../wormhole-dual-noc-routing/design.md), [134-test delivery, route/resource argument, directed faults and memory boundary](../wormhole-dual-noc-routing/delivery.md) |
| 4.1–4.5 | `wormhole-memory-transactions`, 40/40 | `694e9eb` | [Design](../wormhole-memory-transactions/design.md), [231 discovered / 230 passed / one optional skip, resource ownership and compute handoff](../wormhole-memory-transactions/delivery.md) |
| 5.1–5.5 | `wormhole-compute-dataflow`, 35/35 | `007f4e5` | [Design](../wormhole-compute-dataflow/design.md), [315 discovered / 314 passed / one optional skip, finite pipelines, CLI and validation handoff](../wormhole-compute-dataflow/delivery.md) |
| 6.1–6.5 | `wormhole-validation-harness`, 35/35 | `ca964a5` | [Design](../wormhole-validation-harness/design.md), [458 discovered / 457 passed / one optional skip, evidence/import/calibration gates and multicast handoff](../wormhole-validation-harness/delivery.md) |
| 7.1–7.5 | `wormhole-multicast-sync`, 35/35 | `f6ca63b` | [Design](../wormhole-multicast-sync/design.md), [repaired implementation audit](../wormhole-multicast-sync/implementation-audit.md), [557 discovered / 556 passed / one optional skip and real shared-runtime delivery](../wormhole-multicast-sync/delivery.md) |
| 8.1 | 27 parent requirements / 30 scenarios | This audit | Exact code/test references and scope decisions in [evidence-map.json](evidence-map.json) |
| 8.2 | Examples, exported contracts, retention and consumer assumptions | This audit | Workflow review below and byte-for-byte verification against the final delivery manifest |
| 8.3 | Canonical specification baseline | Pending | [Concrete input inventory and merge policy](reconciliation.md); no main-spec writes or archival performed |

The “reconcile” clauses in child exit milestones are satisfied here by reviewing
their delivered delta contracts and evidence against later children. They are
**not** claims that canonical main specs were written at those historical exits.
That separate operation is explicitly required by 8.3 and remains incomplete.
This retrospective audit also does not re-date historical tests as current runs.

## Requirement and scenario evidence

The table below indexes reviewed code and representative tests. The JSON map
contains the full 77-test selection, all named scenarios, their exact test
identifiers, additional implementation symbols and requirement-specific limits.
VA-03 additionally relies on each child's independently recorded exit gate;
VA-07 is a documentation/process review, not a fabricated runtime test.

| Parent target | Child contracts | Representative implementation / executable evidence |
| --- | --- | --- |
| HP-01 | HP-P01, HP-P02 | [normalize_profile](../../../simulator_detailed/hardware_profile.py#L216); [test_identity_versions_strict_ids_and_board_selection](../../../simulator_detailed/tests/test_hardware_profile.py#L171) |
| HP-02 | HP-P05, HP-P06, HP-P07 | [override_parameter](../../../simulator_detailed/hardware_profile.py#L263); [test_overrides_preserve_provenance_and_recompute](../../../simulator_detailed/tests/test_hardware_profile.py#L383) |
| HP-03 | HP-P03, HP-P04, HP-P09, TR-G01, TR-G02, TR-D01 | [topology_from_profile](../../../simulator_detailed/topology.py#L118); [test_profile_inventory_preserves_identity_and_unknowns](../../../simulator_detailed/tests/test_topology_adapters.py#L31) |
| HP-04 | HP-P08, TR-G04, TR-G09, TR-D09, CD-D08, MS-12 | [require_executable_architecture](../../../simulator_detailed/hardware_profile.py#L463); [test_capabilities_are_implementation_owned_and_not_bypassable](../../../simulator_detailed/tests/test_hardware_profile.py#L456) |
| TR-01 | TR-G01, TR-G02, TR-G03, TR-G04, TR-G08, TR-G09 | [Topology](../../../simulator_detailed/topology.py#L63); [test_profile_inventory_preserves_identity_and_unknowns](../../../simulator_detailed/tests/test_topology_adapters.py#L31) |
| TR-02 | TR-D01, TR-D02 | [TorusRouting](../../../simulator_detailed/torus.py#L254); [test_all_pairs_match_independent_physical_coordinate_oracle](../../../simulator_detailed/tests/test_torus.py#L111) |
| TR-03 | TR-G06, TR-G07, TR-D03, TR-D04, TR-D05 | [ResourceDependencies](../../../simulator_detailed/torus_dependencies.py#L52); [test_dependency_mutations_are_rejected_before_runtime](../../../simulator_detailed/tests/test_torus.py#L194) |
| TR-04 | TR-G08, TR-D06, TR-D07, TR-D08 | [TorusTransport](../../../simulator_detailed/torus_transport.py#L111); [test_slowdown_half_open_snapshot_and_recovery](../../../simulator_detailed/tests/test_torus_transport.py#L150) |
| TR-05 | MS-01, MS-02, MS-03, MS-04, MS-05, MS-10, MS-11 | [MulticastSyncPlan](../../../simulator_detailed/multicast_plan.py#L82); [test_capacity_one_exact_prefix_delivery_and_terminal_discard](../../../simulator_detailed/tests/test_tree_runtime.py#L87) |
| MT-01 | MT-D01, MT-D02, MT-D06, MT-D08, CD-D03 | [MemorySession](../../../simulator_detailed/memory_runtime.py#L119); [test_segmented_transactions_both_fabrics_literal_byte_oracles](../../../simulator_detailed/tests/test_memory_runtime.py#L135) |
| MT-02 | MT-D03 | [MemorySession](../../../simulator_detailed/memory_runtime.py#L119); [test_segmented_transactions_both_fabrics_literal_byte_oracles](../../../simulator_detailed/tests/test_memory_runtime.py#L135) |
| MT-03 | MT-D02, MT-D04, MT-D05, MS-05, MS-06 | [MemoryResources](../../../simulator_detailed/memory_resources.py#L398); [test_aliases_share_one_capacity_and_teardown_restores_once](../../../simulator_detailed/tests/test_memory_resources.py#L72) |
| MT-04 | MT-D04, MT-D06, MT-D07, MS-05, MS-08 | [MemorySession](../../../simulator_detailed/memory_runtime.py#L119); [test_posted_completion_keeps_pending_target_effects_and_capacity](../../../simulator_detailed/tests/test_memory_runtime.py#L154) |
| MT-05 | MT-D09 | [LegacyDMAAdapter](../../../simulator_detailed/memory_adapters.py#L55); [test_exact_matrix_packets_issuers_finite_slots_and_drain](../../../simulator_detailed/tests/test_memory_adapters.py#L102) |
| MT-06 | MS-06, MS-07, MS-08, MS-09, MS-10, MS-11 | [CounterState](../../../simulator_detailed/scalar_service.py#L63); [test_atomic_job_is_one_final_service_on_the_read_write_fifo](../../../simulator_detailed/tests/test_scalar_shared_runtime.py#L37) |
| CD-01 | CD-D01, CD-D02 | [compute_cost](../../../simulator_detailed/compute_cost.py#L70); [test_subquantum_math_stays_positive_and_failed_math_cannot_publish](../../../simulator_detailed/tests/test_compute_runtime.py#L306) |
| CD-02 | CD-D03, CD-D04 | [ComputeRuntime](../../../simulator_detailed/compute_runtime.py#L151); [test_longer_route_delays_compute_by_independently_counted_hops](../../../simulator_detailed/tests/test_compute_runtime.py#L145) |
| CD-03 | CD-D05, CD-D07, MS-09, MS-10 | [ComputeBuffers](../../../simulator_detailed/compute_buffers.py#L98); [test_bounded_fifo_backpressure_and_many_generations](../../../simulator_detailed/tests/test_compute_buffers.py#L124) |
| CD-04 | CD-D04, CD-D06 | [ComputeOverlapRuntime](../../../simulator_detailed/compute_runtime.py#L387); [test_independent_constant_service_depth_oracle](../../../simulator_detailed/tests/test_compute_overlap.py#L177) |
| CD-05 | CD-D01, CD-D08, CD-D09, CD-D10, MS-12 | [import_legacy_fc](../../../simulator_detailed/compute_adapters.py#L117); [test_import_matches_direct_costs_stages_and_all_memory_evidence](../../../simulator_detailed/tests/test_compute_adapters.py#L61) |
| VA-01 | VA-D02, VA-D07, VA-D08, VA-D09 | [requirement_coverage](../../../simulator_detailed/validation/reporting.py#L79); [test_ttsim_and_synthetic_origin_cannot_claim_hardware](../../../simulator_detailed/tests/test_validation_references.py#L143) |
| VA-02 | HP-P05, HP-P06, VA-D03, VA-D07 | [collect_source_identity](../../../simulator_detailed/validation/identity.py#L94); [test_unknown_metadata_retained_but_blocks_comparison](../../../simulator_detailed/tests/test_validation_references.py#L132) |
| VA-03 | HP-P10, TR-G10, TR-D10, MT-D10, CD-D10, VA-D06, MS-12 | [run_gate](../../../simulator_detailed/validation/gates.py#L59); [test_gate_missing_tool_is_blocked](../../../simulator_detailed/tests/test_validation_runner.py#L78) |
| VA-04 | VA-D05, VA-D06, MS-11 | [MemoryService](../../../simulator_detailed/memory_service.py#L132); [test_dual_fabric_alias_and_local_clients_share_service_independent_resources_overlap](../../../simulator_detailed/tests/test_memory_resources.py#L310) |
| VA-05 | VA-D08 | [admit_calibration](../../../simulator_detailed/validation/calibration.py#L80); [test_perfect_fit_can_fail_heldout_without_refitting](../../../simulator_detailed/tests/test_validation_calibration.py#L100) |
| VA-06 | HP-P07, HP-P08, VA-D09, VA-D10, VA-D11, MS-12 | [case_capabilities](../../../simulator_detailed/validation/reporting.py#L37); [test_fault_labels_and_requirement_scope](../../../simulator_detailed/tests/test_validation_cli.py#L161) |
| VA-07 | VA-D11, MS-12 | [This process review](#findings-and-remaining-closure); canonical baseline pending in 8.3 |

## Supported workflow and compatibility review (8.2)

The final multicast [delivery manifest](../wormhole-multicast-sync/delivery-identities.json)
records exact argv, inputs, schema versions and outcomes for 18 actual replay/suite
invocations plus six predecessor validation invocations. Their source and asset
hashes still match this checkout. Those executions were reviewed, not rerun under
the name of this documentation audit. The fresh 77-test selection separately
exercises the principal cross-child invariants and published multicast suites.

| Workflow or contract | Recorded executable path and observation | Retained boundary |
| --- | --- | --- |
| Profile inspection | `simulator_detailed.inspect_profile`; profile source/override/mask tests in this audit | Assumed enabled mask and provenance remain visible; inventory alone does not enable general profile execution |
| Canonical topology / unicast | `simulator_detailed.replay_topology`; v1 heterogeneous 44 cycles, v2 small/profile 57/63.5 cycles; independent route tests and exact v2 result fingerprints | Physical/fabric/link identity survives export; old synthetic trace semantics and paired-link fault behavior remain distinct |
| Addressed memory | `simulator_detailed.replay_memory`; complete generic/profile examples 387/1382 cycles and incomplete example 80 cycles | Public result v1; incomplete legacy exit 2; packet service, capacity and visibility are aggregate modeled behavior |
| Finite compute | `simulator_detailed.replay_compute`; four examples 91.75/45/33/105 cycles | FC/matmul scheduling costs and real LOAD/STORE traffic; no tensor arithmetic or arbitrary DFG lowering |
| Shared multicast/scalars/compute | `simulator_detailed.replay_multicast_sync`; five new fixtures 79.5/154/137/500/287 cycles; three suites with 1/3/1 cases | Only runtime-configured mixed workloads provide shared transport/service, local waits and generation-safe attached compute; old serial projections do not gain these capabilities |
| Evidence and calibration | `simulator_detailed.validate_wormhole`; original offline 19-case/123-check suite, synthetic imports and two calibration plans | Sealed fitting and held-out mechanics are validated; supplied references are synthetic, so functional-reference and silicon-timing tiers remain unvalidated |
| Legacy consumers | Actual public admission guards and feature/order tests; separate executed root Darknet19 gate: 37,888 nodes, 16 cores, 48 links, 11 JSON-round-tripped windows | Existing 7-D/4-D feature and four-coordinate action contracts remain; incompatible new kinds fail before optional model imports |

Generated traces and CLI outputs were held in temporary directories. Committed
artifacts retain input/source identities, commands, expected outcomes and compact
reports, not large trace dumps. The detailed source manifest covers 167 Python
files; the root smoke manifest covers 18 source files; all 14 recorded asset hashes
were verified. These identify the reused executions independently of later
documentation commits. Arbitrary external captures are not retained or invented.

The old `wormhole_b0_multicast_assumed.json` serial-projection/profile fixture is
not an executable Wormhole mixed-runtime example. The supported replacement is
`wormhole_b0_mixed_assumed.json`. Legacy report language about “pending multicast”
belongs to its selected projection/case contract and is not the current umbrella
status. The new mixed adapter reports its actual implemented capabilities.

## Validation and reproducibility

Fresh checks on 2026-09-20:

- The 77 distinct existing tests referenced by `evidence-map.json`: **77 passed**
  in two batches, zero failures/errors/skips. The initial 75-test selection took
  42.574 s. Two supplemental tests explicitly check harvested-worker admission and
  the legacy mesh timing/fault baseline; their invocation took 0.428 s including
  test loading (runner time 0.176 s). The already-passing all-pairs route oracle
  also appears under HP-03 to substantiate transit through harvested positions.
- Strict Pyright using `.venv/bin/python` and
  `simulator_detailed/pyrightconfig.phase2.json`: **0 errors, warnings or informations**.
- The exact 45-path `scoped_ruff_command` in the multicast delivery manifest:
  **all checks passed**.
- Strict OpenSpec validation of all seven children and the parent: **passed**.
- Evidence integrity: every referenced code symbol/test exists at its recorded
  location; exact parent/scenario inventory, child requirement IDs, 233 checked
  child tasks, complete current Python source membership, and recorded source/input
  hashes agree. Parent plus child inputs have 100 distinct requirement IDs and 178
  scenarios across seven capability paths.
- Whitespace and local Markdown target checks: **passed**.

The last full detailed suite remains the recorded multicast run: **557 discovered,
556 passed, one optional Torch/PyG skip**, 189.838 s. The audit rechecked the exact
source/input bytes instead of claiming another full-suite execution. It adds no
runtime code, dependencies or tests. One initial audit-reference lookup used a
nonexistent adapter class name; it failed before writing the evidence map or
running tests, was corrected to the actual `LegacyDMAAdapter`, and the complete
reference check was then rerun successfully. The supplemental two-test invocation
initially used system Python 3.13, which lacks SimPy, and failed at import. It was
rerun successfully with the documented `.venv` Python 3.12.12. Those initial
import errors are environment failures, not behavioral test results or passes.

Reproduce the selected executable audit from the repository root:

```sh
.venv/bin/python - <<'PY'
import json
import unittest
from pathlib import Path

path = Path('openspec/changes/wormhole-single-chip-simulation-plan/evidence-map.json')
report = json.loads(path.read_text())
names = sorted({test['unittest'] for requirement in report['requirements']
                for test in requirement['tests']})
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/python - <<'PY'
import json
import subprocess
from pathlib import Path

path = Path('openspec/changes/wormhole-multicast-sync/delivery-identities.json')
subprocess.run(json.loads(path.read_text())['scoped_ruff_command'], check=True)
PY
openspec validate wormhole-single-chip-simulation-plan --strict --no-interactive
git diff --check
```

## Findings and remaining closure

**CRITICAL — task 8.3:** `openspec/specs/` contains no delivered specifications.
Reconcile the parent and child deltas according to [reconciliation.md](reconciliation.md),
review the resulting main specs against this evidence, run strict validation and
commit that separate part before marking 8.3 complete. Do not archive while it is
open. No missing finite-model implementation was found within the approved child
contracts; this remaining issue concerns delivery of the specification baseline.

**WARNING — optional consumer evidence:** Torch/PyG inference/checkpoint/training
is unvalidated. The two historical root local-remap failures
(`test_internal_view_layer5`, removed `LayerView.active_cores`, and
`test_safe_layer5_sequence_drains`, inactive mapping) remain outside this delivery.
Keep the actual passing Darknet19 smoke separate from those unverified workflows;
investigate them in the owning RL/local-remap change before claiming that support.

**WARNING — external accuracy evidence:** no compatible real functional-reference
capture or measured silicon timing is available. Obtain compatible provenance,
units, layout, clocks and measurement conditions before enabling those evidence
tiers or assigning calibrated status. The absence does not invalidate the bounded
model implementation, but it limits its claims.

**SUGGESTION — historical wording:** retain old child reports as historical evidence,
and use this report plus the updated handoff for current status. Scope old report
fields to their selected adapter when consuming them programmatically; do not
interpret a legacy projection's pending support as missing shared-runtime work.

One critical closure issue remains. Nothing was synchronized, archived or pushed.
