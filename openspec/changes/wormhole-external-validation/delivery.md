# Wormhole external validation delivery checkpoint

## Status

This is an external-evidence checkpoint updated on 2026-09-21. The change is
25/35 complete. The pinned ttsim/TT-Metal worker has executed all three
functional cases and the evidence is committed. It is not final delivery: no
named Wormhole device is available, so silicon timing, paired comparison,
measured calibration and held-out evidence have not been collected. The 10
hardware-dependent task boxes stay open and the change is not archived.

The implementation and evidence are split into validated local commits plus
the current profiler-enabled correction checkpoint. Nothing in this change has
been pushed.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Campaign contracts | `c4884ca` | 1.1-1.5 |
| Supported intervals | `db13ecb` | 2.1-2.5 |
| Portable capture kit | `cecaa96` | 3.1, 3.3 |
| Wormhole collection | `56bf613` | 4.1, 4.2 |
| Paired orchestration | `aa11a7f` | 5.1-5.3 |
| Sealed calibration | `c5e9cb8` | 6.1, 6.2 |
| Independent audit | `f43b774` | 7.2-7.4 |
| Pinned ttsim evidence | `3f1b86f` | 3.2, 3.4, 3.5 |
| Pinned profiler CSV dialect | `668cf3e` | supporting implementation |
| Clean ttsim rerun | `886c0d0` | supporting evidence |
| Blocked silicon matrix | `ae4bedf` | supporting evidence |
| Profiler-enabled worker correction | current checkpoint | no task relabeled |

## Requirement coverage

| Requirement | Implemented and offline-tested scope | Actual external evidence |
| --- | --- | --- |
| EV-01 | Strict bounded campaign and capture contracts; pure preflight admission; deterministic portable kits | Not required to validate the contract |
| EV-02 | Hash-addressed bundles, environment/source/build identities, raw preservation and mutation rejection | Three committed ttsim bundles; Wormhole bundles pending |
| EV-03 | Canonical workload equivalence over source, build, operation, layout, fidelity, mapping, fabric and clocks | Pending paired live executions |
| EV-04 | Functional conversion, effect/order/status gates and timing exclusion after functional failure | Three passing ttsim functional captures; silicon functional captures pending |
| EV-05 | Three typed interval mappings, repetition/aggregation admission and strict clock/entity boundaries | Pending same-RISC Wormhole profiler captures |
| EV-06 | Typed target planning, sealed fit/evaluation splits, bounded existing calibration engine and result publication | Pending disjoint hardware fit and held-out captures |
| EV-07 | Portable report package, full outcome separation and independent rooted-lineage audit | Pending final report from actual captures |
| EV-08 | Legacy guards, full offline suite, compatibility selection, root Darknet19 smoke and optional-ML blocked behavior | Verified offline; no vendor dependency required |

Exact source, fixture, commit and requirement mappings are recorded in
`delivery-identities.json`. Detailed commands and measured outcomes are in
`progress.md`.

## Verification

The current focused capture-kit/collector selection passes 22 tests. The fresh
complete detailed suite discovered 613 tests: 612 passed, zero failed or
errored, and one optional dependency test skipped. The selected compatibility suite discovered
46 tests: 45 passed and one optional dependency test skipped. Strict Pyright,
the explicit affected/predecessor Ruff scope, strict OpenSpec validation and
`git diff --check` pass at this checkpoint.

The offline validation suite passed all 123 checks across 19 cases while
retaining functional-reference and silicon-timing as unvalidated. The root 4x4
Darknet19 gate completed 37,888 nodes on 16 cores and 48 links with 11
JSON-round-tripped windows. The optional ML gate was blocked because `torch`
and `torch_geometric` are absent; it was not relabeled as a pass.

## Remaining work

The open tasks are 4.3-4.5, 5.4, 5.5, 6.3-6.5, 7.1 and 7.5. They require a
named Wormhole device with inventory, firmware, clocks and live profiler CSV.
The fixed collector now validates a sealed plan and reaches device discovery;
on this host it stops at `No chips detected in the cluster`. Fresh hardware
capture bundles must be returned to this repository and admitted before the
existing comparison, calibration, audit and delivery paths can complete.

No claim in this checkpoint establishes tensor-level correctness, universal
ttsim timing, full-kernel timing, unique physical parameter identification or
full-device timing accuracy. Hardware dimensions, clocks, widths, capacities,
effective rates, fidelities and thresholds remain declared inputs.
