# Implementation progress

## Destination environment and baseline (2026-09-17)

Starting commit: `ea0115a` on `feiyang-dev`, with a clean worktree. The active
change was ready for implementation with 0/35 tasks; its planning was retained.
The handoff, active proposal/design/spec/tasks, predecessor delivery/progress,
umbrella validation contract and detailed simulator documentation supplied the
implementation context. The stale summaries in `openspec/config.yaml` were not
used to reinterpret current simulator or RL behavior.

The destination is Linux x86_64 (kernel `6.1.0-35-amd64`), rather than the source
host's Darwin arm64. CPython **3.12.12** was installed with uv and an isolated
`.venv` was created. All eleven handoff package pins were reproduced:

```text
annotated-types==0.8.0
nodeenv==1.10.0
numpy==2.5.3
pydantic==2.13.5
pydantic-core==2.46.5
pyright==1.1.414
ruff==0.16.7
scipy==1.18.1
simpy==4.1.2
typing-extensions==4.16.0
typing-inspection==0.4.4
```

`pip check` passed. The existing Node **22.22.2** satisfies OpenSpec's documented
minimum. OpenSpec **1.11.0** is installed locally at `.venv/openspec`, with
`.venv/bin/openspec` as its entry point; activating `.venv` selects it instead of
the host's older global 1.3.1. The ignored Codex apply integration was regenerated
using `init --tools codex --no-animation`. No tracked OpenSpec configuration or
dependency export was replaced, and no optional ML/GPU stack was installed.

Before any runtime/schema implementation edits:

- `.venv/bin/python -m unittest discover -s simulator_detailed/tests`:
  **315 discovered, 314 passed, 0 failures/errors, 1 optional Torch/PyG skip**
  (69.329 seconds on this host).
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`:
  **0 errors, 0 warnings**.
- The exact preceding compute child's scoped Ruff command from the handoff:
  **passed**.
- OpenSpec list/status/apply inspection and strict validation of
  `wormhole-validation-harness`: **passed**, confirming 0/35 implementation tasks.
  The documented pre-existing design-rules warning remains unchanged.
- `git diff --check`: **passed**.

These are fresh destination results. Historical root remap failures remain
historical: `test_internal_view_layer5` uses removed `LayerView.active_cores`, and
`test_safe_layer5_sequence_drains` assumes an inactive layer/core mapping. They
were neither repaired nor rerun or counted as passes in Part 1. No new root smoke,
optional model inference/training, ttsim execution or hardware capture was run.

## Part 1 — contracts, identities and evidence outcomes

Tasks 1.1–1.5 are complete in the commit containing this progress record.
Previously only simulator-specific public records and distributed test
evidence existed. The new contracts describe evidence without running a suite,
importing profiler observations or performing calibration. Those executors stay
assigned to Parts 2–6.

### Implemented behavior

- `configs/schemas/validation.py` defines the five strict v1 document kinds:
  `validation_suite`, `validation_reference`, `validation_report`,
  `calibration_plan`, and `calibration_result`. Records are frozen, forbid extra
  fields/nonfinite values/coercive scalar parsing, and revalidate nested model
  instances. Named adapter/check/gate selections accept no executable expression,
  dynamic import or shell command. Cases declare finite wall/simulation budgets;
  suites require nonempty required checks and reject ambiguous identities and
  dangling reference bindings.
- Normalized observation contracts preserve exact integer counters, explicit
  clock domains, addressed effects, scoped identities, routes, causal edges,
  intervals and metric windows. Unknown metadata needs a reason; absent fields
  never inherit simulator defaults. Contract checks reject invalid identities,
  cyclic causal edges, mixed-domain windows and complete-run metrics on incomplete
  evidence. Parsing these records is not event extraction or an independent
  runtime audit; those remain Part 2.
- Calibration contracts permit only physical memory resource
  `bytes_per_cycle`/`fixed_latency_cycles` and compute rate
  `work_per_native_cycle`/`setup_native_cycles` targets. Explicit bounds,
  candidate and case-run budgets, metric weights/scales/tolerances, split
  identities, candidate outcomes and frozen selections are checked. Structural
  targets and overlapping declared fit/held-out semantic or capture-group
  identities are rejected. Part 5 must additionally derive these fingerprints
  from admitted workloads/captures, apply candidates to copies, run actual fits
  and seal held-out execution; declaring hashes alone does not prove separation.
- `validation/identity.py` collects exact input byte hashes, sorted source
  membership/hashes/sizes, Git revision and selected-source dirty state,
  canonical effective plans/check selections, explicit seed state and relevant
  Python/platform/dependency versions. Source roots are explicit (default:
  `simulator_detailed/**/*.py`, including tests), with dirty/untracked files and
  tracked deletions retained. Missing Git/distributions are marked unknown.
  Absolute paths and timestamps are separate diagnostics; outputs are excluded
  from source membership and cannot also be declared identity inputs. Adapters
  must supply admitted semantic plans without diagnostic paths; the collector
  does not guess which workload fields to strip. Reference bytes are checked
  against their supplied digest before being returned, with no implicit fetch
  or replacement of the sidecar hash. Asset paths resolve from each declaring
  document.
- `validation/outcomes.py` separates simulation completion from executed check
  outcomes and evidence tiers. Any failed check fails aggregation, including an
  optional failed check. Required blocked/unsupported/not-run checks yield
  incomplete; empty or optional-only evidence cannot pass. Optional missing
  hardware evidence can coexist with an offline pass and unvalidated timing.
  Skipped tests remain unavailable checks, including mixed pass/skip gates.
  Synthetic evidence cannot become either an external agreement or a measured
  disagreement. Reports validate their summaries, reference classifications,
  observation links and scoped coverage against actual supplied check records.
  Delivered coverage requires passed checks and explicit implementing commits.

### Compatibility and limitations

All hardware geometry, clocks, rates, capacities and tolerance choices stay in
the admitted input contracts. This part changes no simulator algorithm, existing
result schema, timing/digest fixture, public replay API, NMC benchmark, consumer,
predictor feature, embedding dimension or RL action. The only modified existing
production-support file is `pyrightconfig.phase2.json`, adding strict coverage
for the new schema and `validation/` package.

The records check internal consistency and integrity, not authenticity of a
supplied hardware origin. Synthetic fixtures that exercise a structurally
admissible measured record are explicitly test data, not actual measurements.
Profile/workload execution admission, event audits, case timeouts, external
comparison admission, fitting and report publication are still future parts.
Multicast/synchronization stays pending and silicon timing stays unvalidated.
No umbrella milestone/spec was synced, archived or marked delivered.

### Part 1 checks

```sh
.venv/bin/python -m unittest simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_outcomes
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/tests/test_validation_*.py simulator_detailed/tests/validation_fixtures.py
.venv/bin/openspec validate wormhole-validation-harness --strict --no-interactive
git diff --check
```

Focused contract/identity/outcome checks: **52 passed**. Full detailed suite:
**367 discovered, 366 passed, 0 failures/errors, 1 optional Torch/PyG skip**
(71.127 seconds). Strict Pyright: **0 errors / 0 warnings**. Scoped Ruff, strict
OpenSpec validation and whitespace checks passed. Existing CLI, replay
timing/digest and detailed compatibility tests remain passing in this run.

The tests independently hash exact bytes in temporary Git repositories and
exercise dirty/staged/untracked/deleted sources, path/mtime/timestamp relocation,
output exclusion, raw-reference corruption and unavailable tools/dependencies.
Contract tests cover all five JSON round trips, forbidden fields/versions,
nonfinite/coerced values, counters above `2**53`, duplicate/dangling identities,
candidate budgets/targets/split leakage, tie reporting and held-out failure
despite a perfect fit. Outcome tests enumerate all 25 required/optional outcome
pairs and exercise skipped tests, incomplete execution, synthetic evidence and
forged report/coverage claims. These are evidence-layer tests, not producer
timing snapshots or hardware accuracy claims.

Final selected Python source membership: **115 files** under
`simulator_detailed/`. The new collector's bundle SHA-256 is
`f1f427ee9d5ba26eb9532b1f07b96252e195e0f1b5bb0e88d0a04e909470badd`, computed from
sorted canonical JSON records with `logical_path`, raw `sha256`, and `size_bytes`.
This manifest format includes file sizes and differs from the predecessor's
two-field delivery-note digest; the old fixture was not changed. Git revision
and dirty state are separate fields. At validation time the source revision was
`ea0115a` with the new source files untracked; the commit containing this report
records their delivery. Report/documentation/output files do not hash themselves.

Progress after this commit: **5/35 tasks complete**. Next part: **2.1–2.5**, public
result adapters, normalization and independent event audits. No Part 2 runtime
implementation is included here, and no push was performed.

## Part 2 — adapters, observations and independent audits

Tasks 2.1–2.5 completed. Six named adapters use existing public admission
boundaries and preserve raw result schemas and canonical result hashes. Memory
and compute support snapshots followed by resumption on the same runtime.
Normalization exposes exact counters, scoped identities, addressed publication
effects, per-subject causal edges, service/engine intervals, routes, clock domains
and explicit metric windows. Incomplete results have only partial metrics.
Tensor values remain unsupported. Clock conversion uses exact rational arithmetic.
Explicit one-to-one mappings compare addressed effects and partial-order reachability
without demanding an incidental event-list order.

Independent audits derive modular routes, flit counts and byte extents, memory
granule/service cost, matrix/block/storage work and native/ACI durations from
admitted configurations. They replay descriptor, slot/generation, engine and
credit transitions, check shared physical service and publication charges, and
require drained operations/effects/resources for complete runs. Equal-time credit
events are checked as a group because public exports sort simultaneous events by
fields; each token still requires reserve/release/return causality. No producer
routing, costing or accounting helper is called by these audits. Dynamic compute
memory operations are consumed from its session export; compute shapes and stage
prerequisites are separately checked against the admitted compute configuration.

Validation on this host:

- Adapter/oracle suite: **22 tests, 22 passed, 0 failures/errors/skips** (14.859s).
  Includes direct/wrapped equality, generic and Wormhole geometries/widths/clocks,
  local-only and pending traffic, exact large counters, partial orders, resume,
  corrupt hops/flits/packet sets/shared service, unpaid posted publication,
  descriptor/slot/credit release, generation and premature compute stages.
- Affected group (adapters before the final credit test, compute overlap/CLI,
  memory CLI/runtime, torus routing/transport): **87 tests, 87 passed**, no skips
  (64.806s). Existing independent constant-stage **21/14** and integrated
  **45/33** oracles remain; direct result equality is explicitly a compatibility
  check, not an independent correctness expectation.
- Strict Pyright: **0 errors, 0 warnings**; scoped Ruff on validation/schema/tests
  and `git diff --check`: **passed** after the final edits.

No runtime mechanisms, hardware constants, replay formats or legacy consumers
changed. No external functional execution or silicon timing was validated.
