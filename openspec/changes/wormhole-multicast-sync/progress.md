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
