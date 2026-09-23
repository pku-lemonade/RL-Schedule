# Generic instruction programs delivery

## Status

Final delivery recorded on 2026-09-23. The change is **9/9 complete**.
Neutral instruction programs now ride the unified pipeline: a batch may
declare `programs` of typed operations bound to a sequencer unit;
compilation expands operations into namespaced plan transactions, and one
sequencer process per program issues them in program order with explicit
`issue_cycles` occupancy. All four operation kinds share the registry,
event bus, cancellation, drain and abort semantics of batch transactions.
Program-free batches are untouched bit for bit; the change is neither
pushed nor archived at authoring time.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Program schemas and pure compilation | `990fb34` | 1.1-1.3 |
| Sequencer runtime | `c55f541` | 2.1-2.3 |
| Public acceptance and delivery | this commit | 3.1-3.3 |

## Requirement coverage

| Requirement | Delivered at | Verified by |
| --- | --- | --- |
| IP-01 program documents | `configs/schemas/generic_transactions.py` | validation tests (strict fields, identity rules, non-empty workload) |
| IP-02 operation kinds and references | `system_compile.py` expansion | compile tests (reference checks, namespacing, terminal classification, counter bounds) |
| IP-03 deterministic in-order issue | `runtime_context.py` `_program` | serial-chain, explicit-overlap and shared-unit trace-invariant tests |
| IP-04 unified execution | `runtime_context.py` `_dispatch` + shared registry/bus | batch-contention, cross-program wait/signal and cycle-limit cleanup tests |
| IP-05 program termination | `runtime_context.py` + derived spans | terminal-stop test (program reason mirrors the stopping operation) |
| IP-06 result and digest integrity | `GenericProgramSpan` + result wiring | byte-identical repeat and program-free digest-stability tests |
| IP-07 neutral vocabulary | `NeutralId` denylist + `scan_forbidden_tokens` over new modules | identity-rejection, clean-scan and seeded-violation tests |

Exact identities are in `delivery-identities.json`; per-part commands and
outcomes are in `progress.md`. User documentation is
[generic_simulation.md](../../../simulator_detailed/docs/generic_simulation.md).

## Verification

The complete detailed suite discovered 763 tests: 762 passed, zero failed
or errored, one optional dependency test skipped (216.9 s). Strict Pyright
reports 0 errors and 0 warnings over the phase-2 scope. The recorded Ruff
scope over all new and changed modules passes. Strict OpenSpec validation
passes and `git diff --check` is clean. Phase-1/phase-2 numeric anchors
(exact cycle outcomes, per-hop windows, result digests) are unchanged.

## Boundaries

Out of scope by requirement and not implemented: multi-chip, full
collectives, private plugins or real device adapters, and any real device
configuration or calibration data. Programs carry no decoder semantics
beyond the four operation kinds. Model cycles derive entirely from
configured rates.
