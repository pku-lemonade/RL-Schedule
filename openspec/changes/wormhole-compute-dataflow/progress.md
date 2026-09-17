# Implementation progress

## Part 1 — finite contracts, pure costs and planning admission (2026-09-17)

Tasks 1.1–1.5 implement planning only. `ComputePlan.require_executable()` rejects execution. A `compute_workload_result` at this stage has `status: planned`, `execution_supported: false`, no completed jobs/work and no runtime timestamps. The legacy hardware-profile execution gate remains closed.

### Implemented behavior and scope

- `configs/schemas/compute_workload.py` declares strict finite streams, explicit physical worker/L1/interfaces, bounded stage capacities, disjoint A/B/C slots, tensor layouts, storage dtype widths, exact effective-rate keys and initial/job input versions. FC requires `already_flattened_bmk_v1`: callers supply the actual flattened `[B,M,K]` tensor, without inferring axes or adding bias. Synthetic dtype/precision/fidelity labels require explicit matching rate settings and evidence.
- `compute_cost.py` calculates useful and padded arithmetic separately from useful/storage bytes. Configured compute blocks and storage tiles are independent. Exact decimal quantum arithmetic precedes conversion to finite positive SimPy-compatible durations; a runtime start-time helper rejects durations that cannot advance the clock. This helper is available for the later executor and is not evidence that it already runs.
- `compute_plan.py` binds canonical graphs or hardware profiles, checks owned L1, enabled physical workers, one appropriate fabric interface, route availability, reservations, permissions, range sizes, complete operand/output fit and exact rates. Both NoCs share the declared physical worker. Plans reject implicit broadcasting/layout conversion, in-place output, split-K fields, unknown/ambiguous legacy identifiers, FIFO cycles and unmodeled cross-worker completion. Job input versions require matching producer shapes/dtypes/layouts/ranges; posted writers cannot provide a remote completion version.
- FIFO stage order is recorded independently from explicit predecessor-completion waits. This prevents the later pipeline from treating every previous stream item as a full-completion barrier. Slot indices/generations are **planned assignments**, not an implemented reuse state machine.
- Source, path-independent configuration, effective-cost/settings and plan hashes are recorded and revalidated. Source paths remain in the inspectable configuration JSON. A Wormhole profile can be admitted for these planning checks with explicitly assumed rates; this is not a calibrated or executable Wormhole workload.

### Shared-memory changes and compatibility

The old memory replay contract bundled shared configuration with a mandatory operation list. `MemorySystemConfig` now holds the shared fields and identity checks; `MemoryReplay` retains its existing kind/version/model revision and nonempty operations contract. Pure graph binding, canonical serialization/configuration identity and service/reservation validation are reused by both planners. No placeholder memory operation is fabricated.

These changes affect `MemoryReplay`, `MemoryPlan` and `MemoryResourcePlan` admission. They do not alter standalone memory scheduling, legacy TPU/LSU timing, the legacy FC branch, predictor dimensions or RL interfaces. Existing standalone memory, NoC and CLI regression tests pass. Runtime transport settings and lowered operations will be composed in part 2; this planning input alone is deliberately insufficient for execution.

### Independent checks

For BF16 storage with A `[2,3,5]`, shared B `[5,7]`, C `[2,3,7]`, compute block `(4,8,4)`, rate 128, quantum 0.5, minimum 1, setup 2, compute clock 1 GHz and ACI clock 0.5 GHz:

- Useful work is 420 and padded work is 1024 operations; arithmetic service is 10 native / 5 ACI cycles.
- Dense A/B/C storage is 60/70/84 bytes. Storage tiles of 2×4 change those extents to 128/96/128 bytes without changing arithmetic work.
- A separate unit-block case with one-byte synthetic storage, rate 100, quantum 0.1, minimum 0.3, setup 0.2 and a 2:1 ACI/compute clock ratio yields 420 executed operations and 4.4 native / 8.8 ACI cycles.
- Explicit flattened FC matches matmul cost. Batched weights double B storage in this example without changing arithmetic work. Faster-than-quantum work remains positive; overflow, underflow and lost clock advancement fail.

`simulator_detailed/tests/test_compute_contracts.py` also covers JSON round trips, strict invalid fields, stable/revised identities, bypassed-model revalidation, full slot/reservation fit, local input placement, same-worker producer versions, independent workers/streams, rejected cross-worker dependencies, a 1,100-job finite FIFO, profile binding and both closed execution gates.

Validation commands:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_compute_contracts
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/compute_records.py simulator_detailed/compute_cost.py simulator_detailed/compute_plan.py simulator_detailed/configs/schemas/memory_replay.py simulator_detailed/memory_plan.py simulator_detailed/memory_resources.py simulator_detailed/tests/test_compute_contracts.py
openspec validate wormhole-compute-dataflow --strict --no-interactive
git diff --check
```

Focused tests: **23 passed**. Full detailed suite: **254 discovered, 253 passed, 1 skipped** (optional Torch/PyG unavailable). Strict Pyright: **0 errors / 0 warnings** with all four new implementation modules included. Scoped Ruff, strict OpenSpec validation and whitespace checks passed. A final result-schema strictness adjustment was followed by the 23 focused tests, strict Pyright and scoped Ruff again; no runtime or shared-memory behavior changed in that adjustment.

## Part 2 — composable memory session and compute lowering (2026-09-17)

Tasks 2.1–2.5 add a shared-clock `MemorySession` while retaining `MemoryRuntime` as the standalone owner of its original environment and teardown. `MemorySessionPlan` admits a closed set of local activation gates, validates operation ownership and same-worker waits, and checks the combined operation/gate DAG before any SimPy resources are allocated. Sessions expose relay lifecycle events, support bounded `advance()` snapshots and require an owner capability for exactly-once finalization. Attached snapshots report memory drain without treating unrelated workload events as a reason to keep the memory transport incomplete; attached finalization never steps those unrelated callbacks. Standalone runs retain their prior activation, complete/incomplete continuation, capacity restoration and full-result digests.

`compute_memory.py` provides pure lowering from an admitted `ComputePlan` into one finite addressed-memory plan. Remote inputs become existing network reads into the assigned slot, both operands use ordinary local-read service, results use a local write, and remote outputs use the declared posted/acknowledged writer. Producer versions point at the corresponding admitted result/writer operation. Deterministic stream FIFO and slot-generation ordering add explicit operation dependencies and stage gates; no compute engine, tensor value, or SimPy environment is created by lowering. Local-only jobs omit network traffic while preserving local memory service. The lowering reuses `MemoryPlan`, `MemoryExecutionPlan`, `MemoryOrderingPlan` and `MemorySessionPlan`, so range, permission, version, capacity, route and cycle checks remain active. Cross-worker gate waits and other dependencies requiring an unmodeled remote notification are rejected.

The stage controller remains declarative in this part: gates can delay or release admitted memory work, but compute arithmetic, reusable-slot state transitions, overlapping reader/compute/writer execution and workload result accounting are deferred to parts 3–5. This distinction is reflected in the absence of a compute execution API.

### Part 2 checks

- `simulator_detailed/tests/test_memory_session.py`: 8 tests covering closed gates, absolute starts, lifecycle-event relays, owner tokens, deferred teardown, delayed credits, shared/independent owners, cross-worker rejection and standalone equivalence hashes.
- `simulator_detailed/tests/test_compute_memory.py`: 3 tests covering remote/local lowering, operation and gate ownership, source versions, writer/FIFO dependencies, slot generations and pure no-runtime admission.
- Focused memory/compute/session/runtime/ordering tests: **69 passed**.
- Full detailed suite: **265 discovered, 264 passed, 1 skipped** (optional Torch/PyG dependency unavailable).
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: **0 errors / 0 warnings**.
- Scoped `.venv/bin/ruff check` on all changed/new Part 2 Python files and `git diff --check`: passed.

### Remaining work after Part 2

Reusable slot lifecycle, actual compute execution, bounded overlapping stages, final workload evidence, the legacy FC adapter, compute CLI and source/silicon accuracy validation remain unimplemented. No source-level or silicon timing validation is claimed for the assumed compute rates.

## Part 3 — reusable finite item slots (2026-09-17)

Tasks 3.1–3.5 implement `ComputeBuffers` and `fifo_item_slots_v1` over the session's existing canonical memory reservations. Previously, slot assignments and generations existed only in the compiled plan. They now have enforced runtime transitions: reserve, publish inputs, consume, publish output, drain, complete writer and release. A token identifies one exact job and generation; copied, foreign, stale and wrong-stage tokens cannot advance it. Publication requires the admitted memory version and completed service gates. Reuse uses the existing memory write/version invalidation rules.

The ledger admits only the next FIFO job in each stream and tracks at most one waiting producer per stream. Failed admission owns no partial A/B/C bundle and repeated attempts create no additional wait records or payload storage. Byte reservations remain charged for the session lifetime; releasing a logical slot advances its generation without returning or reacquiring physical capacity. A slot ledger creates no extra SimPy capacity container or service resource. Buffer footprints, nonoverlap and L1 ownership continue to be checked by plan revalidation.

### Lifetime boundaries and shared interfaces

- `MemoryResource.range_is_idle()` checks active leases over the exact reserved subrange. A live overlapping reader or writer pins that slot, while a disjoint lease in the same buffer does not pin it. Whole-session drain still accounts for both.
- `MemorySession.lifecycle_time()` exposes already observed facts without allocating another event or waiter. The existing relay event API retains its behavior.
- Owner component tokens register the slot ledger before stage activation. The session cannot complete its enclosing owner or finalize while any admitted slot generation remains unreleased, even when its memory operations have drained. Duplicate slot ledgers and foreign completion tokens fail.
- Lowered job records now identify operations consuming a local result. Writer/job completion is distinct from slot release: a dependent consumer may start after completion, while the producer slot remains retained until that consumer has finished. This avoids blocking the consumer behind the release it must enable; cyclic one-slot reuse is rejected during pure admission.
- A posted writer can release its safe local source generation after local completion, while remote destination service and delayed credits remain pending and charged. Released slots alone do not permit final session teardown.

The conservative A/B/C bundle remains occupied through writer completion and all declared local-result consumers. This can reduce overlap compared with separate operand/result rings. Waiting producers hold no partial slot, and the ledger itself acquires no service server, transport credit or descriptor. Existing memory operations retain their own acquisition and release rules. This is a slot-lifetime audit, not yet the full scheduler liveness argument required by Part 5.

Existing exclusive scratchpad adapter regressions exercise the shared environment, original capacity owner, exactly-once delay, idle-only binding, unmanaged-operation rejection and drained detach. No active legacy Core is automatically bound, and no second scratchpad ledger is introduced. Standalone memory timing/digest fixtures and legacy regressions remain passing.

### Part 3 checks

`simulator_detailed/tests/test_compute_buffers.py` adds **8 tests** covering:

- Matching session binding, duplicate-ledger rejection, unchanged memory reservations and absence of shadow capacity/service resources.
- Strict transition/token checks, double publication/release rejection, pending component diagnostics and guarded owner completion/finalization.
- Twenty-four jobs over two slots, twelve generations per slot, constant resident storage, bounded FIFO backpressure, exactly-once release and event-by-event occupancy reconstruction. Snapshot/event JSON round trips preserve the records.
- Remote input version invalidation across reuse, premature publication rejection and prevention of publishing the previous generation's output.
- Posted output release before remote destination readiness, continued remote/credit ownership and eventual full capacity restoration.
- Local-result consumer retention with independently published job completion, and pure rejection of cyclic single-slot reuse.
- Overlapping versus disjoint external leases and rejection of invalid ranges or released/replaced backing handles.

The test controller explicitly inserts a synthetic five-cycle math delay to exercise these boundaries. It is not a production compute executor and does not validate the configured arithmetic cost or pipeline throughput.

Validation commands:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_compute_buffers
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/compute_buffers.py simulator_detailed/tests/test_compute_buffers.py simulator_detailed/compute_memory.py simulator_detailed/compute_plan.py simulator_detailed/memory_resources.py simulator_detailed/memory_runtime.py simulator_detailed/memory_session.py
openspec validate wormhole-compute-dataflow --strict --no-interactive
git diff --check
```

Focused slot tests: **8 passed**. Full detailed suite: **273 discovered, 272 passed, 1 skipped** (optional Torch/PyG unavailable), including existing memory resource/version, adapter, transport and standalone compatibility tests. Strict Pyright: **0 errors / 0 warnings**, with `compute_buffers.py` included. Scoped Ruff, strict OpenSpec validation and whitespace checks passed.

### Remaining work after Part 3

Actual configured arithmetic execution, automatic reader/compute/writer scheduling, bounded stage overlap, workload result accounting, the legacy FC adapter and compute CLI remain unimplemented. `ComputePlan.require_executable()` still rejects execution; its diagnostic now reflects the implemented memory composition and slot lifecycle. Source/silicon accuracy validation remains later work, and no calibrated timing claim is made.
