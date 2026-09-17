## 1. Finite workload contracts and configurable costs (CD-D01, D02, D09)

- [x] 1.1 Add strict `compute_workload` v1 input and initial result/cost records in `configs/schemas/compute_workload.py` and `compute_records.py`; verify JSON round trips, extra/invalid fields, nonfinite values, unique IDs, explicit policies and unsupported operation/layout/precision rejection without allocating runtime resources.
- [x] 1.2 Implement pure FC/matmul normalization and `effective_matmul_v1` in `compute_cost.py`; independently verify useful/padded work, bias-free FC/shared-weight equivalence, dense/tiled storage bytes, two block geometries/dtype widths/clocks/rates, positive sub-quantum duration and unrepresentable-cost rejection.
- [x] 1.3 Add pure `compute_plan.py` admission for canonical enabled workers, L1/fabric ownership, exact effective-rate selection, finite FIFO jobs, complete operand/output slot fit and supported dependencies; verify disabled/router-only workers, ambiguous legacy IDs, range/shape mismatches, in-place/split-K work and dependency cycles are rejected before execution.
- [x] 1.4 Record compiled/effective identities and separate planned/executable capability states; test that schema/cost support alone cannot open either abstract workload execution or the legacy full-profile gate.
- [x] 1.5 Add all new implementation modules to strict Pyright coverage, run focused contract/cost tests, `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`, scoped `.venv/bin/ruff check`, and `git diff --check`; record results and commit this part before memory composition.

## 2. Composable finite memory session (CD-D03, D04, D07)

- [x] 2.1 Refactor the memory runtime around an explicit shared-environment session with closed operation activation gates and lifecycle-event access; verify no operation submits early or twice, foreign/unadmitted gates are rejected and default standalone activation remains unchanged.
- [x] 2.2 Separate memory-session completion/snapshot from reservation teardown, leaving the standalone wrapper responsible for its existing finalization; compare complete and incomplete standalone operation/packet/service/ownership traces, digests, timings and exactly-once capacity restoration with established fixtures.
- [x] 2.3 Extend compute plan lowering to predeclare remote reader/writer and local operand/result operations, source versions and slot-reuse ordering; verify the combined memory/compute/FIFO graph is acyclic, preserves existing conflict checks and rejects dependencies requiring zero-cost remote notification.
- [x] 2.4 Test a delayed external compute gate and concurrent local/network clients on shared and independent memory owners; verify actual contention and that early memory-only completion cannot teardown a still-active enclosing workload.
- [x] 2.5 Run focused session, memory ordering/runtime/resource, CLI and transport regressions plus strict type/scoped lint/whitespace checks; inspect wait-resource changes and commit this part with standalone behavior evidence before buffer integration.

## 3. Reusable finite item slots (CD-D01, D05, D07)

- [x] 3.1 Implement `fifo_item_slots_v1` in `compute_buffers.py` using existing canonical memory reservations and whole A/B/C slot bundles; verify total configured footprints fit L1, nonoverlap, no extra capacity owner and exactly one physical reservation per extent.
- [x] 3.2 Implement generation-aware reserve/input-publish/consume/output-publish/drain/release transitions with deterministic FIFO slot assignment; test stale/foreign tokens, premature reads/reuse, double publication/release and version invalidation across repeated reuse.
- [x] 3.3 Add bounded producer waits and buffer event/snapshot records; verify free plus occupied slots equals capacity at every event, waiting producers own no partial bundle, and job counts much larger than slot count do not increase resident storage.
- [x] 3.4 Verify local posted-writer completion can release only the safe local generation while outstanding remote effects remain charged, and failed/incomplete work cannot trigger teardown; exercise the existing exclusive scratchpad capacity adapter without auto-binding active legacy cores.
- [x] 3.5 Run focused slot/resource/version/adapter tests plus strict type/scoped lint/whitespace checks; document conservative bundled lifetime and commit this part before executing jobs.

## 4. Single-job reader compute writer execution (CD-D02..04, D06, D07, D09)

- [x] 4.1 Add `compute_runtime.py` composition for one admitted job with one environment, canonical memory owners and finite per-worker compute context; verify operand readiness and output reservation precede compute acquisition, local operand reads precede arithmetic, and result service follows arithmetic.
- [x] 4.2 Execute FC/matmul reader and writer operations through the existing addressed-memory session, retaining local-only input/result paths; verify real request/response traffic, route-dependent starts, exact versions and no second legacy LSU/SPM/sink delay for the same service.
- [x] 4.3 Export unrounded stage/math/context/resource events and planned versus completed work; independently check useful/storage/network/service bytes, arithmetic duration, result publication, writer completion and shared physical worker identity across fabrics.
- [x] 4.4 Implement incomplete snapshots, resumption and coordinated full drain/finalization; compare split-horizon and uninterrupted runs, require pending posted effects/delayed credits to drain, and verify repeated finalization cannot duplicate releases.
- [x] 4.5 Run focused single-job/FC/local/remote/runtime tests and existing memory regressions plus strict type/scoped lint/whitespace checks; record remaining multi-item scope and commit before enabling overlap.

## 5. Bounded overlapping streams and contention (CD-D05..07, D09)

- [x] 5.1 Add independent FIFO reader/compute/writer scheduling with configured bounded stage admission, compute contexts and per-stream reusable slots; verify one-slot serial execution, deterministic multi-slot order, and no hidden queue of resident operands/results.
- [x] 5.2 Verify the independent three-item `R=2,C=3,W=2` oracle gives depth-one 21 and depth-two 14, then add integrated memory timelines whose expected causal boundaries come from explicit service/route arithmetic rather than recorded simulator output.
- [x] 5.3 Exercise multiple streams/workers sharing or separating compute, L1, DRAM aliases and both fabrics; verify shared-engine serialization, eligible independent overlap, aggregate service limits and that software thread count cannot multiply one engine's rate.
- [x] 5.4 Stress more jobs than slots, slow readers/writers, minimum staging/descriptors/queues, posted/acknowledged output and incomplete resumption; check every token/byte/credit/context invariant and audit actual acquisition order against the design's wait-resource table.
- [x] 5.5 Run pipeline/liveness/ownership and all affected memory/NoC tests plus strict type/scoped lint/whitespace checks; record the supported finite-model drain argument and conservative overlap limitation, then commit before compatibility adapters.

## 6. Explicit DFG adapter and consumer guards (CD-D01, D08, D09)

- [x] 6.1 Add `legacy_fc_chain_v1` sidecar admission/import in `compute_adapters.py` for complete LOAD_FEAT/LOAD_WGT -> FC -> STORE chains; verify every node/edge and explicit shape/dtype/address/worker mapping is accounted for, mapper state is unchanged and unsupported nodes/fanout/reductions/missing metadata fail.
- [x] 6.2 Compare imported FC plans, costs, traffic and stage behavior with equivalent directly declared workloads, distinguishing source digests; add rejection in legacy `utils/task.py` for its previously empty FC branch and verify the diagnostic directs callers to the supported adapter.
- [x] 6.3 Preserve legacy detailed LOAD/STORE/CONV/POOL/SEND/RECV, scratchpad, DMA and failure behavior; run existing custom-mesh timing/fault fixtures and separately verify root Darknet19 mapping/workflow behavior using temporary trace/log destinations without enabling a new legacy mode.
- [x] 6.4 Verify workload/result documents are rejected by detailed predictor/embedding before optional Torch/PyG/model work and no new RL runtime path is enabled; record unchanged 7-D runtime, 4-D hardware, mesh/checkpoint and observation/action contracts, including unavailable optional model checks.
- [x] 6.5 Run adapter/legacy/consumer regressions plus strict type/scoped lint/whitespace checks; review the intentional legacy FC error and supported adapter subset, then commit before CLI delivery.

## 7. CLI examples, validation evidence and next-child handoff (CD-D01..10)

- [x] 7.1 Add `simulator_detailed/replay_compute.py` and small generic matmul, streaming depth-control and Wormhole single-ASIC JSON examples under `configs/compute_workloads/`; verify `.venv/bin/python -m simulator_detailed.replay_compute --workload <each-example>`, relative source paths, deterministic parseable output, exit codes 0/1/2 and preservation of existing output on invalid input.
- [x] 7.2 Complete workload result accounting, source/effective-plan identities, capacity/pending diagnostics and pre/post-finalization evidence; independently reconstruct completed work and byte/timeline totals from exported events for complete and interrupted cases, keeping nested memory-session status distinct from workload success.
- [x] 7.3 Update scoped `hardware_profile.py` capability reporting and detailed compute documentation; verify `abstract_compute_workload_v1` becomes executable only for supported admitted inputs, legacy full-profile admission remains closed, pinned source facts and assumed rates are separate, and no numerical/kernel/calibrated timing claim is made.
- [x] 7.4 Run `.venv/bin/python -m unittest discover -s simulator_detailed/tests`, strict Pyright with all new modules included, scoped Ruff, all new workload and existing memory/topology CLI regressions, the separate legacy workflow smoke, `openspec validate wormhole-compute-dataflow --strict --no-interactive` and `git diff --check`; preserve old timing/digest fixtures and keep generated traces/logs in temporary directories.
- [x] 7.5 Record CD-D01..10 and umbrella CD-01..05/pipeline VA-04 requirement-to-test coverage, actual commands/results, code/input/source identities, example stage timelines, conservation/drain audit and remaining limitations in `delivery.md`; commit the validated child before exploring `wormhole-validation-harness`, without syncing/archiving the umbrella or pushing.
