# Implementation progress

## Part 1 (tasks 1.1–1.5)

Baseline was reproduced on branch `feiyang-dev` from commit `23f5222` using the
existing `.venv`. The observed environment was Python 3.12.12, Node v22.22.2,
OpenSpec 1.3.1, Pydantic 2.13.5, SimPy 4.1.2, NumPy 2.5.3, SciPy 1.18.1,
Pyright 1.1.414 and Ruff 0.16.7. The source-host handoff records a different
OpenSpec version; this progress file records the executable version in this
workspace.

The detailed baseline command
`.venv/bin/python -m unittest discover -s simulator_detailed/tests` passed
458 tests with one optional skip in 153.421 seconds. Strict Pyright passed with
`--pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`.
The predecessor scoped Ruff command passed over the compute, replay, hardware,
task and predecessor tests. `openspec validate wormhole-validation-harness
--strict --no-interactive` passed. The published topology, memory and compute
examples exited 0 and emitted complete, drained records. The handoff's stale
memory replay path was corrected to the existing
`simulator_detailed/configs/memory_replays/wormhole_ordered.json`; no generated
outputs were retained. Historical root limitations and the optional ML skip
remain separate from this child.

Part 1 adds strict versioned workload/result records, a pure rectangle-tree
compiler, deterministic segment and recipient inventories, scalar admission
checks, source/effective plan digests and an explicit admission-only boundary.
It uses the canonical graph and `MemorySystemConfig` hardware parameters, so
fabric dimensions, packet widths, clocks and service values remain configurable.

Part 1 verification:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_sync -v` — 5 passed.
* `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json` — 0 errors, 0 warnings, 0 informations.
* Scoped Ruff over the new schemas, compiler, planner and tests — all checks passed.
* `git diff --check` — passed.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` — passed before implementation and will be rerun at each part boundary.

All runtime transport allocation and legacy multicast fields remain untouched;
later parts add capabilities behind these new versioned contracts.
