# Wormhole external validation delivery checkpoint

## Status

This is an offline implementation checkpoint dated 2026-09-20. The change is
22/35 complete. It is not final delivery: no compatible pinned ttsim/TT-Metal
worker or named Wormhole device was available, so the required functional,
silicon-timing, measured-calibration and held-out evidence has not been
collected. The remaining task boxes stay open and the change is not archived.

The implementation is split into six validated local commits plus this Part 7
checkpoint. Nothing in this change has been pushed.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Campaign contracts | `c4884ca` | 1.1-1.5 |
| Supported intervals | `db13ecb` | 2.1-2.5 |
| Portable capture kit | `cecaa96` | 3.1, 3.3 |
| Wormhole collection | `56bf613` | 4.1, 4.2 |
| Paired orchestration | `aa11a7f` | 5.1-5.3 |
| Sealed calibration | `c5e9cb8` | 6.1, 6.2 |
| Independent audit | this checkpoint commit | 7.2-7.4 |

## Requirement coverage

| Requirement | Implemented and offline-tested scope | Actual external evidence |
| --- | --- | --- |
| EV-01 | Strict bounded campaign and capture contracts; pure preflight admission; deterministic portable kits | Not required to validate the contract |
| EV-02 | Hash-addressed bundles, environment/source/build identities, raw preservation and mutation rejection | Pending live ttsim and Wormhole bundles |
| EV-03 | Canonical workload equivalence over source, build, operation, layout, fidelity, mapping, fabric and clocks | Pending paired live executions |
| EV-04 | Functional conversion, effect/order/status gates and timing exclusion after functional failure | Pending ttsim and silicon functional captures |
| EV-05 | Three typed interval mappings, repetition/aggregation admission and strict clock/entity boundaries | Pending same-RISC Wormhole profiler captures |
| EV-06 | Typed target planning, sealed fit/evaluation splits, bounded existing calibration engine and result publication | Pending disjoint hardware fit and held-out captures |
| EV-07 | Portable report package, full outcome separation and independent rooted-lineage audit | Pending final report from actual captures |
| EV-08 | Legacy guards, full offline suite, compatibility selection, root Darknet19 smoke and optional-ML blocked behavior | Verified offline; no vendor dependency required |

Exact source, fixture, commit and requirement mappings are recorded in
`delivery-identities.json`. Detailed commands and measured outcomes are in
`progress.md`.

## Verification

The focused audit/campaign/calibration selection passed 12 tests. The complete
detailed suite discovered 606 tests: 605 passed, zero failed or errored, and one
optional dependency test skipped. The selected compatibility suite discovered
46 tests: 45 passed and one optional dependency test skipped. Strict Pyright,
the explicit affected/predecessor Ruff scope, strict OpenSpec validation and
`git diff --check` pass at this checkpoint.

The offline validation suite passed all 123 checks across 19 cases while
retaining functional-reference and silicon-timing as unvalidated. The root 4x4
Darknet19 gate completed 37,888 nodes on 16 cores and 48 links with 11
JSON-round-tripped windows. The optional ML gate was blocked because `torch`
and `torch_geometric` are absent; it was not relabeled as a pass.

## Remaining work

The open tasks are 3.2, 3.4, 3.5, 4.3-4.5, 5.4, 5.5, 6.3-6.5, 7.1 and 7.5.
They require a pinned TT-Metal checkout and build, a compatible ttsim runtime,
and a named Wormhole device with inventory, firmware, clocks and profiler CSV.
Fresh capture bundles must be returned to this repository and admitted before
the existing comparison, calibration, audit and delivery paths can complete.

No claim in this checkpoint establishes tensor-level correctness, universal
ttsim timing, full-kernel timing, unique physical parameter identification or
full-device timing accuracy. Hardware dimensions, clocks, widths, capacities,
effective rates, fidelities and thresholds remain declared inputs.
