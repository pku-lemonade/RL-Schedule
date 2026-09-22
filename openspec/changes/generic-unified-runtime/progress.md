# Progress record

## Part 1: SystemSpec and pure compilation (tasks 1.1-1.4, completed 2026-09-22)

Delivered `configs/schemas/system_spec.py` (`system_spec` v1 composing the
existing graph and batch documents; `immutable_plan` records with resources,
resolved hop timings, terminal classifications, counter reachability bounds
and deterministic digests) and `system_compile.py` (pure `compile_system`:
full reference validation, route/timing resolution, terminal classification,
counter bounds; no SimPy object or global state). `tests/test_unified_compile.py`
adds 10 tests: identical inputs give identical plans/digests, reordered graph
records keep the digest, plan immutability and digest revalidation, terminal
classification, and compile-time rejection of unknown endpoints, units,
timing networks and override links.

Executed verification: 10 focused tests passed; strict Pyright 0 errors /
0 warnings; scoped Ruff clean; strict OpenSpec validation passed.

## Part 2: RuntimeContext, ResourceRegistry, EventBus (tasks 2.1-2.5, completed 2026-09-22)

Delivered `runtime_context.py` with `ResourceRegistry` (stable IDs, one
construction per physical resource, capacity acquire/wait/release with queue
accounting, sorted multi-resource acquisition, `finally`-guaranteed release
plus pending-request cancel on interruption), `EventBus` (named events,
counted events with declared upper bounds, deterministic global-sequence
publish ordering) and `RuntimeContext` (one environment executing all four
transaction kinds with phase-1-identical mechanics, deterministic trace,
per-transaction wait cycles, per-resource queue waits, error records,
survivor interruption at the cycle bound, plan digest in results). Result
records gained backward-compatible fields (`wait_cycles`,
`queue_wait_cycles`, `errors`, `trace`, `plan_sha256`) with defaults, plus a
trace-ordering validator. `generic_runtime.py` is now a shim through
`compile_system` + `RuntimeContext`; legacy guards reject `system_spec` and
`immutable_plan`. `tests/test_unified_runtime.py` adds the 10-case public
acceptance suite (parallelism, deterministic queueing, wait-after-signal,
ring deadlock freedom, sorted acquisition, release-after-cancel, compile
rejection, never-satisfiable reporting, byte-identical repeats).

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_unified_runtime simulator_detailed.tests.test_unified_compile simulator_detailed.tests.test_generic_runtime simulator_detailed.tests.test_generic_results simulator_detailed.tests.test_generic_adapter simulator_detailed.tests.test_generic_graph
79 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 692 tests in 218.269s — OK (skipped=1, optional torch/PyG check).
672 before this change; +20 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/runtime_context.py simulator_detailed/generic_runtime.py simulator_detailed/topology_compatibility.py simulator_detailed/configs/schemas/generic_transactions.py simulator_detailed/configs/schemas/system_spec.py simulator_detailed/system_compile.py simulator_detailed/tests/test_unified_runtime.py simulator_detailed/tests/test_unified_compile.py simulator_detailed/tests/test_generic_adapter.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-unified-runtime --strict --no-interactive
Change 'generic-unified-runtime' is valid.

git diff --check
Passed with no output.
```
