# Generic addressed memory delivery

## Status

Final delivery recorded on 2026-09-22. The change is **9/9 complete**.
Memory resources may now declare a strict hierarchy (banks, stripe, latency,
ports, channels) and transfers may carry an optional address; service honors
bank/port/channel contention inside the unified pipeline. Flat memories and
every prior behavior are byte-identical. The change is neither pushed nor
archived at authoring time.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Schemas and compilation | `3ae4705` | 1.1-1.3 |
| Contended memory service | `3ae4705` | 2.1-2.3 |
| Public acceptance and delivery | this commit | 3.1-3.3 |

Parts 1 and 2 were validated and committed as one unit because the runtime
stage consumes the plan records introduced by the schema work.

## Requirement coverage

| Requirement | Delivered at | Verified by |
| --- | --- | --- |
| AM-01 optional strict hierarchy | `configs/schemas/generic_graph.py` | hierarchy validation tests |
| AM-02 addressed transfer records | `configs/schemas/generic_transactions.py`, compile address rules | address rejection tests |
| AM-03 deterministic plan resources | `system_compile.py` | mapping determinism and plan resource tests |
| AM-04 contended memory service | `runtime_context.py` | bank/port/channel contention tests with exact windows |
| AM-05 accounting and compatibility | result records + usage kinds | utilization, flat-compat and repeat tests |

Exact identities are in `delivery-identities.json`; per-part commands and
outcomes are in `progress.md`. User documentation is
[generic_simulation.md](../../../simulator_detailed/docs/generic_simulation.md).

## Verification

The complete detailed suite discovered 709 tests: 708 passed, zero failed or
errored, one optional dependency test skipped (220.2 s). Strict Pyright
reports 0 errors and 0 warnings. The recorded Ruff scope passes. Strict
OpenSpec validation passes and `git diff --check` is clean. All phase-1/2
numeric anchors are unchanged (694 prior tests pass).

## Boundaries

Addressed accesses model command plus data service at the memory side; there
are no separate request/response network phases, no per-bank address
interleaving beyond stripe mapping, and no value-level memory contents.
Model cycles derive entirely from configured rates. Dynamic routing,
instruction-level simulation, multi-chip, full collectives, private plugins,
real device adapters and any real device data remain out of scope.
