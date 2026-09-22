## Why

Phase 1 delivered the generic layer (system graph, four transaction kinds,
result contract, adapters), but the runtime grew as one driver per concern:
link kernels, unit resources and counters are constructed inside
`GenericRuntime` without a unified compile step, a shared resource registry,
a shared event bus or a deterministic trace. Continuing this way would add a
new independent execution path per feature. This change establishes the
single pipeline `SystemSpec → ImmutablePlan → RuntimeContext →
SimulationResult` and routes every existing entry point through it.

## What Changes

- Add a strict version-1 `system_spec` document composing one
  `generic_system_graph` with one `generic_transaction_batch`, and a pure
  `compile_system(spec) -> ImmutablePlan` entry: validates every entity,
  resource, port, attachment, fabric and transaction reference; checks ID
  uniqueness, reference existence, non-negative capacities and connection
  legality; emits an immutable deterministic plan whose digest is identical
  for identical input; creates no SimPy process and touches no global state.
- Add `RuntimeContext` holding the SimPy environment and time, one
  `ResourceRegistry`, one `EventBus`, transaction states, a deterministic
  trace and metrics collector, and the error/incomplete accounting. A single
  context executes `transfer`, `compute`, `wait` and `signal` together.
- Add `ResourceRegistry`: stable IDs, no duplicate construction of one
  physical resource per plan, capacity acquire/wait/release, queueing on a
  shared resource with parallelism across distinct resources, deterministic
  ordered multi-resource acquisition, and full release on completion, failure
  or cancellation.
- Add `EventBus`: named events, counted events and wait conditions; `signal`
  publishes, `wait` resumes when its condition holds; same-time publishes are
  ordered by a deterministic global sequence (never dict order or object
  addresses); waits that can never be satisfied are detected from declared
  counter bounds and reported as incomplete with explicit reasons.
- Extend the unified result with per-transaction wait times, per-resource
  queue waits, an error list, a deterministic trace and the plan digest;
  existing phase-1 fields, kinds, exit codes and numeric semantics are
  preserved exactly.
- Route the phase-1 `run_generic_batch`/`GenericRuntime`/CLI path through the
  new pipeline as compatibility shims. No second runtime is created; root
  `simulator/` and `profiling_sim/` are untouched; no real device names,
  parameters, topologies, profiles or test data are introduced.

## Capabilities

### New Capabilities

- `unified-runtime`: Pure system compilation into immutable plans and one
  runtime context owning resources, events, dependencies, time, traces and
  results for all four transaction kinds.

### Modified Capabilities

None. `generic-simulation` contracts keep their meanings; the phase-1
documents, CLI, examples and numeric behavior are preserved through the
compatibility layer.

## Impact

- **Simulator:** new modules `configs/schemas/system_spec.py`,
  `system_compile.py` and `runtime_context.py`; `generic_runtime.py` becomes
  a shim delegating to the pipeline. Additive result fields (wait times,
  queue waits, errors, trace, plan digest) carry defaults so phase-1 v1
  documents remain readable and valid.
- **JSON and evidence:** new kinds `system_spec` and `immutable_plan`;
  existing v1 documents unchanged. Legacy consumer guards extended to the new
  kinds.
- **External systems:** none; adapters are unchanged library calls.
- **Detector and RL:** unchanged; new kinds rejected at legacy boundaries.
- **Dependencies:** none new; SimPy/pydantic versions unchanged.
- **Out of scope (deferred by requirement):** addressed memory reads/writes,
  bank/port/channel contention, dynamic routing, instruction-level
  simulation, multi-chip, full collectives, private plugins or real device
  adapters, and any real device configuration or calibration data.
