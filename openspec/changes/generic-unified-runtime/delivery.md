# Generic unified runtime delivery

## Status

Final delivery recorded on 2026-09-22. The change is **12/12 complete**. The
unified pipeline `SystemSpec → ImmutablePlan → RuntimeContext →
SimulationResult` now owns compilation, resources, events, dependencies,
time, traces and results for all four generic transaction kinds. Phase-1
behavior is preserved bit for bit through compatibility shims; the change is
neither pushed nor archived at authoring time.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| SystemSpec and pure compilation | `2f746dc` | 1.1-1.4 |
| RuntimeContext, registry and bus | `67908b0` | 2.1-2.5 |
| Public acceptance and delivery | this commit | 3.1-3.3 |

## Requirement coverage

| Requirement | Delivered at | Verified by |
| --- | --- | --- |
| UR-C01 pure compilation | `configs/schemas/system_spec.py`, `system_compile.py` | determinism, immutability and compile-failure tests |
| UR-C02 immutable plan | plan records with content digests | order-independence and digest-tamper tests |
| UR-R01 unified context | `runtime_context.py` | mixed four-kind batches in one run |
| UR-R02 registry discipline | `ResourceRegistry` | ring deadlock-freedom, sorted acquisition and release-after-cancel tests |
| UR-R03 event bus semantics | `EventBus` + counter bounds | wait-after-signal and never-satisfiable reporting tests |
| UR-R04 unified result | extended result records | byte-identical repeat and integrity tests |
| UR-X01 phase-1 compatibility | `generic_runtime.py` shims + guards | unchanged phase-1 suite and legacy guard tests |

Exact identities are in `delivery-identities.json`; per-part commands and
outcomes are in `progress.md`. User documentation is
[generic_simulation.md](../../../simulator_detailed/docs/generic_simulation.md).

## Verification

The complete detailed suite discovered 692 tests: 691 passed, zero failed or
errored, one optional dependency test skipped (218.3 s). Strict Pyright
reports 0 errors and 0 warnings over the 92-entry phase-2 scope. The recorded
Ruff scope over all new and changed modules passes. Strict OpenSpec
validation passes and `git diff --check` is clean. Phase-1 numeric anchors
(exact cycle outcomes, per-hop windows, result digests) are unchanged.

## Boundaries

Out of scope by requirement and not implemented: addressed memory
reads/writes, bank/port/channel contention, dynamic routing,
instruction-level simulation, multi-chip, full collectives, private plugins
or real device adapters, and any real device configuration or calibration
data. Model cycles derive entirely from configured rates.
