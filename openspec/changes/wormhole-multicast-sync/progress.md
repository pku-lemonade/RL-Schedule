# Implementation progress

> Correction on 2026-09-18: the Part 1–5 entries below are historical records
> of committed prototype work, not proof that their full task scope was met.
> The [implementation audit](implementation-audit.md) supersedes those completion
> claims. Shared transport/memory/compute execution and retained resume remain
> incomplete. The task checklist has been corrected to 2/35 verified.

## Verification repair — 2026-09-18

The corrective checkpoint adds a guarded prototype CLI/adapter, lossless event
normalization and independent input/event checks. Missing shared service,
physical ownership, synchronization and compute evidence remains unsupported;
the existing pipeline's early atomic submission fails the causality check.
It also fixes the no-progress resume loop, scalar dependency/horizon handling,
native-clock conversion and owner-qualified address comparisons. This is not
completion of Part 6 or delivery of the shared runtime. See the implementation
audit for the 32 acceptance scenarios and remaining implementation work.

Checks run against the repaired source:

* `.venv/bin/python -m unittest discover -s simulator_detailed/tests` —
  494 discovered, 493 passed and one optional skip, 164.429 seconds.
* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_validation simulator_detailed.tests.test_multicast_scalar simulator_detailed.tests.test_multicast_pipeline`
  — 22 passed after the final CLI status/diagnostic changes.
* `.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`
  — 0 errors, 0 warnings, 0 informations.
* `.venv/bin/ruff check simulator_detailed/multicast_*.py simulator_detailed/replay_multicast_sync.py simulator_detailed/configs/schemas/multicast_sync.py simulator_detailed/configs/schemas/validation.py simulator_detailed/topology_compatibility.py simulator_detailed/validation simulator_detailed/tests/test_multicast_*.py simulator_detailed/tests/test_validation_*.py simulator_detailed/tests/validation_fixtures.py simulator_detailed/compute_*.py simulator_detailed/replay_compute.py simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/hardware_profile.py simulator_detailed/utils/task.py simulator_detailed/tests/test_compute_*.py simulator_detailed/tests/test_hardware_profile.py`
  — passed. An initial command named a nonexistent standalone pipeline schema;
  correcting the scope to the existing modules passed.
* Actual `run_gate(RegressionGate(gate_id="root_darknet19_smoke", gate="root_darknet19_smoke", required=True, wall_time_seconds=60.0, requirements=("MS-12",)))`
  — passed: 37,888 completed nodes, 16 cores, 48 links, 11 windows and a JSON
  round trip. This executes the root simulator with temporary outputs.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` and
  `git diff --check` — passed.

No optional ML execution, functional capture or silicon timing validation was
performed. No umbrella task was promoted, and no commit was pushed.

## Part 1 (tasks 1.1–1.5)

Baseline was reproduced on branch `feiyang-dev` from commit `23f5222` using the
existing `.venv`. The observed environment was Python 3.12.12, Node v22.22.2,
OpenSpec 1.3.1, Pydantic 2.13.5, SimPy 4.1.2, NumPy 2.5.3, SciPy 1.18.1,
Pyright 1.1.414 and Ruff 0.16.7. The source-host handoff records a different
OpenSpec version; this progress file records the executable version in this
workspace.

The detailed baseline command
`.venv/bin/python -m unittest discover -s simulator_detailed/tests` ran
458 tests: 457 passed and one optional skip in 153.421 seconds. Strict Pyright passed with
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

## Part 2 (tasks 2.1–2.5)

Part 2 adds the opt-in composite lane registry, tree/flit identities, FIFO
all-or-none reservations, deterministic cut-through branch accounting and
resumable transport snapshots. Reservation release is recorded only after the
tree drain event; an interrupted result retains the reservation owner in its
snapshot. The registry keeps request, response and multicast lane classes on
one physical serializer identity and does not alter legacy envelopes.

Verification:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_transport -v` — 4 passed.
* Affected topology/torus/packet and multicast tests — passed.
* Strict Pyright and scoped Ruff — passed.

## Part 5 (tasks 5.1–5.5)

Part 5 adds a typed finite pipeline composition over one multicast plan and
session. Stages bind concrete input/output buffers, slot generations and local
waits; rounds enforce increasing thresholds and a complete operation/stage/wait
dependency DAG. The generic two-round fixture reuses a collector slot only
after each local wait and produces a final counter value of two. JSON fixtures
are stored under `simulator_detailed/configs/multicast_workloads/`, with a
separate canonical topology fixture and an explicitly assumed Wormhole profile
fixture.

Verification:

* Pipeline tests — 2 passed; multicast memory/scalar/transport tests — passed.
* Existing compute and memory runtime regressions — passed.
* Strict Pyright and scoped Ruff — passed.
* `git diff --check` and strict OpenSpec validation — passed.

## Part 3 (tasks 3.1–3.5)

Part 3 attaches tree recipients to the existing addressed-memory buffers and
resource/service records through a side-effect-free executor. It performs one
source read, one bounded service charge for each admitted recipient and marks a
destination ready only after the complete segmented extent is written. Posted
and acknowledged completion are distinct: acknowledged operations emit one
bounded header-only return packet per recipient and segment after destination
service, and require the existing response-sink role. Interrupted execution
does not publish readiness or versions; resuming a completed result is
idempotent.

Verification:

* Multicast contract, transport and memory tests — 14 passed.
* Affected memory packet/session/service, topology, torus and DMA regressions — passed.
* Strict Pyright, scoped Ruff, `git diff --check` and strict OpenSpec validation — passed.

## Part 4 (tasks 4.1–4.5)

Part 4 adds the addressed scalar projection. Counters are initialized as exact
integers, naturally aligned and non-overlapping; only monotonic increment by
one is admitted, with width-specific overflow checks. The executor records one
indivisible linearization/effect, one physical request flit, and an optional
return flit carrying the previous value. Local waits charge observations and
require both threshold and declared data prerequisites without retaining a
transport reservation.

Verification:

* Scalar counter/wait tests — 4 passed.
* Multicast contract, transport and memory tests — passed.
* Strict Pyright and scoped Ruff — passed.
