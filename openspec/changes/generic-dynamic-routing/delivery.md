# Generic dynamic routing delivery

## Status

Final delivery recorded on 2026-09-22. The change is **9/9 complete**.
Networks may now declare `shortest_path` or `adaptive` routing policies with
compile-time BFS distance tables and deterministic runtime selection;
`static_table` remains the default and every prior behavior is
byte-identical. The change is neither pushed nor archived at authoring time.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Policies and compilation | `e08846a` | 1.1-1.3 |
| Hop selection runtime | `e08846a` | 2.1-2.3 |
| Public acceptance and delivery | this commit | 3.1-3.3 |

Parts 1 and 2 were validated and committed as one unit because runtime
selection consumes the compiled tables.

## Requirement coverage

| Requirement | Delivered at | Verified by |
| --- | --- | --- |
| DR-01 per-network policy | `configs/schemas/generic_graph.py` | mixed-configuration and unknown-policy rejections |
| DR-02 pure routing tables | `system_compile.py` | distance-table and unreachable-classification tests |
| DR-03 shortest-path selection | `runtime_context.py` | deterministic path with exact times |
| DR-04 adaptive selection | `runtime_context.py` | congested-branch avoidance and byte-identical repeats |
| DR-05 compatibility | additive fields + `route_select` trace | unchanged prior suites, no tables for static networks |

Exact identities are in `delivery-identities.json`; per-part commands and
outcomes are in `progress.md`. User documentation is
[generic_simulation.md](../../../simulator_detailed/docs/generic_simulation.md).

## Verification

The complete detailed suite discovered 718 tests: 717 passed, zero failed or
errored, one optional dependency test skipped (232.2 s). Strict Pyright
reports 0 errors and 0 warnings. The recorded Ruff scope passes. Strict
OpenSpec validation passes and `git diff --check` is clean. All prior
numeric anchors are unchanged (709 prior tests pass).

## Boundaries

Selection is always distance-reducing (no adaptive detours onto longer
paths), pressure is sampled at decision time on the local link only, and no
global traffic estimator exists. Instruction-level simulation, multi-chip,
full collectives, private plugins, real device adapters and any real device
data remain out of scope.
