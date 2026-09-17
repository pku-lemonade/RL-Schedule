# Finite compute workloads

`abstract_compute_workload_v1` executes finite, explicitly configured bias-free
FC/matmul scheduling with addressed memory traffic. It uses the canonical
physical topology, shared L1/DRAM service, both selected fabrics, reusable slots
and bounded worker contexts. Arithmetic advances time using declared effective
costs; tensor values and TT-Metal/RISC-V/ISA instructions are not executed.
Silicon timing remains unvalidated.

## Run the examples

From the repository root:

```sh
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/generic_matmul.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/streaming_depth1.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/streaming_depth2.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/wormhole_bf16_matmul.json
```

Optional `--output /tmp/compute-result.json` writes the same JSON as stdout.
Graph/profile paths resolve relative to the workload file, independently of the
working directory. All admission finishes before runtime allocation or output
replacement. Invalid input preserves existing output. Complete/incomplete runs
write parseable, deterministic versioned results. Exit codes are **0 complete,
1 incomplete, 2 invalid/error**. These are this CLI's codes; the older memory CLI
retains its existing **0 complete, 2 incomplete, 1 invalid/error** convention.

The following are observed example results, not measured hardware timings:

| Input | Elapsed ACI cycles | Useful / padded work | Math / context cycles | Packet / channel / memory-service bytes |
| --- | ---: | ---: | ---: | ---: |
| Generic matmul | 91.75 | 420 / 1024 | 5 / 12.34375 | 448 / 1984 / 642 |
| Streaming depth 1 | 45 | 6 / 6 | 9 / 18 | 288 / 576 / 15 |
| Streaming depth 2 | 33 | 6 / 6 | 9 / 18 | 288 / 576 / 15 |
| Wormhole BF16 matmul | 105 | 128 / 256 | 2 / 5 | 288 / 2464 / 288 |

The streaming controls are deliberately small scalar jobs with a declared
synthetic one-byte dtype. A same-router read takes nine cycles, local A/B reads
two, math three, and result service one. Depth one starts readers at 0/15/30;
depth two at 0/9/18. These independently derived timelines yield 45/33. The
separate constant-service scheduler test uses R=2/C=3/W=2 and yields 21/14;
it is not the transaction-backed example's service model.

The Wormhole input reuses the pinned B0 profile: one selected ASIC, an explicit
assumed harvest mask (row y=11 disabled), physical worker `tile_1_1`, its two
fabric interfaces and a single canonical L1. The workload uses dense BF16
storage with explicitly declared two-byte elements; no tilize/untilize or
numerical BF16 behavior is implied. Its 1-GHz worker clock, 4×8×4 arithmetic
block, 128 work/native-cycle effective rate, two-cycle setup, service costs,
queue sizes and availability are model assumptions. They are configurable
input data and are not inferred from device peaks. The small workload does not
exercise all enabled workers or all inventory resources.

## Input and admission

`ComputeExecutionWorkload` in `replay_compute.py` extends `ComputeWorkload` with
required `runtime: MemoryRuntimeConfig`. Both reject unknown fields. The
`compute_workload` v1 document declares:

- `memory`: graph/profile binding, explicit fabric routes/permissions, buffers,
  packet geometry, aggregate memory service, capacities and absolute cycle horizon.
- `dtypes`: named storage widths and evidence; labels do not add conversion code.
- `rates`: exact operation/dtype/layout/accumulator/fidelity keys, arithmetic
  M/N/K block dimensions, effective work rate, setup, quantum and minimum cycles.
- `workers`: canonical enabled tile, owned L1, endpoint IDs, clocks, selected
  rate IDs, finite reader/compute/writer capacities and evidence.
- `streams`: finite FIFO jobs and explicit nonoverlapping A/B/C slot bundles.
  Each job defines tensor shapes/storage, addressed sources/destination,
  initial or producer versions, fabrics and supported dependency IDs.

Shapes normalize to A[batch,M,K], shared B[K,N] or batched B[batch,K,N], and
C[batch,M,N]. FC explicitly declares `already_flattened_bmk_v1`. Useful work is
`2*batch*M*N*K`; executed work rounds each M/N/K dimension to the declared
arithmetic block. Effective service rounds positive work/rate to the configured
quantum, enforces the positive minimum and adds setup, then converts native
cycles to ACI cycles. Storage padding for declared tiled tensors is separate
from arithmetic padding and packet padding. The three layouts must already be
compatible; no implicit layout or dtype conversion runs.

Admission checks worker eligibility/ownership, exact extents, supported rate
keys, source versions, slot fit, conflicts and the combined dependency/FIFO/reuse
DAG. Unsupported operations, in-place updates, split-K, reduction/fanout imports,
remote completion dependencies lacking a modeled notification, disabled workers
and insufficient resources fail explicitly. Software stream count never
creates compute engines. Multiple compute contexts explicitly mean independent
configured effective engines; one stream still observes its FIFO compute order.

## Execution and lifecycle

`ComputePlan.compile()` is pure normalization/admission and its planning result
has `execution_supported: false`. `ComputeMemoryPlan.compile()` admits a finite
lowering into one gated memory session. `ComputeOverlapRuntime` executes it;
`ComputeRuntime` remains the explicit one-job entry point. No new global legacy
architecture mode is enabled.

Each stage uses the existing physical capacity and service owners:

```text
reader:  remote source read -> NoC -> local slot write (omitted for ready local inputs)
compute: local A/B reads -> effective arithmetic delay -> local C write/publication
writer:  local C read -> NoC -> destination service (+ ack when requested)
```

A local-only writer is a handoff with no invented network traffic. Local operand
reads and result writes contend with network clients on the same aggregate L1
server. A compute context is acquired after input publication and whole output
reservation; it remains occupied through result publication. Math busy time is
reported separately. No extra legacy TPU/LSU/scratchpad/synthetic-sink charge
is applied for these services.

`fifo_item_slots_v1` reserves each configured physical A/B/C bundle once, then
reuses it through generation-safe reserve/publish/consume/drain/release events.
A blocked next item holds no partial slot or stage grant. Slots conservatively
retain all three extents until writer completion and declared local consumers
and leases finish. This is not a full TT-Metal circular-buffer implementation.

`advance(max_aci_cycles=...)` observes an absolute horizon without teardown.
`run(...)` resumes the same processes and finalizes only after full drain.
Posted writers may retire jobs and release safe local slots while remote
effects/credits remain charged. Workload success additionally requires every
memory operation, descriptor, access lease, service request, transport credit,
stage context and slot to drain. Idle pending state is incomplete, not forced
success. `finalize()` is idempotent after successful drain.

## Results and units

`ComputeExecutionResult` is `compute_workload_result` v1, with the explicit
capability, execution policy, numerical/timing limits, plan/configuration/source
identities, per-job costs, stage and slot-generation events, physical context
ownership, work accounting, pending identities and nested `memory_session`.

Completed work counts only jobs with a `math_end` event; writer completion has
its own job list. Math/context time includes actual partial intervals at a
horizon. Parallel context totals can exceed elapsed wall-clock time. Result
validation rejects inconsistent work, causal stage sequences, math durations
and context intervals. Independent tests reconstruct shapes, padded work,
useful/storage bytes, actual packet injection, per-hop channel bytes, completed
service chunks and occupied intervals from exported evidence.

Nested memory `logical_bytes` and planned packet fields describe the admitted
plan, including operations not yet executed. Actual packet bytes come from
injection events; actual channel bytes count every launched physical hop;
completed memory-service bytes come from completed rounded service chunks.
These units must not be added as though they were the same traffic quantity.

Memory-session status remains separate from workload status. In a drained,
unfinalized workload, `reason: awaiting_finalization` coexists with nested
memory `incomplete/idle_with_pending` and pending `["teardown"]`. After
finalization, `memory_resources` retains the pre-teardown snapshot and
`released_resources` shows restored capacity; zero outstanding work alone does
not prove reservations were released.

`plan.source_sha256` hashes the canonical graph. A profile-derived graph embeds
its normalized profile and profile hash in `origin`. Configuration identity
excludes source path spelling; effective identity includes normalized jobs and
cost settings. The execution-plan digest also binds memory runtime settings,
gates and lowering. These are input/model identities, not source-code hashes or
accuracy certifications; record the Git revision with exported experiments.

## Legacy compatibility and fidelity

`compute_adapters.import_legacy_fc(dfg, sidecar, source_document)` accepts only
the explicit `legacy_fc_chain_v1` subset. The strict sidecar includes physical
worker bindings, complete LOAD_FEAT/LOAD_WGT → FC → STORE node mappings, the
full normalized workload, and `explicit_element_count_reshape_v1`. All edges and
nodes must be accounted for. Matching slices and element counts validate the
asserted reshape; axis semantics and conversion are not inferred. Immutable
DFG/sidecar digests are stored in `legacy_import`, independently of graph
provenance. Mapper readiness flags are captured, not executed or mutated.

Detailed direct legacy FC now raises a diagnostic instead of silently doing no
work. Existing conv/pool, LOAD/STORE/SEND/RECV, custom-mesh timing, DMA,
scratchpad and fail-slow paths remain tested. Public detailed predictor and
hardware-graph entry points reject new workload/result documents before
Torch/PyG or checkpoint work. Detailed seven-feature runtime/four-feature
hardware contracts and root RL shapes/actions are unchanged. Optional model
forward/inference/training checks are not claimed when dependencies are absent.

Manifest `hardware-profile-4` advertises the separately scoped finite runtime;
profile inspection and `require_executable_architecture(profile)` still cannot
execute a general workload. Worker inventory and a passing example are not a
full-profile implementation.

Public architectural anchors and verified source hashes are recorded in the
change's [design](../../openspec/changes/wormhole-compute-dataflow/design.md#9-record-primary-source-anchors-without-turning-peaks-into-fitted-rates).
Pinned Tensix/L1/matrix-unit documentation and TT-Metal reader/compute/writer
examples motivate shared resources, explicit publication and stage separation.
They do not establish this model's rate, queue, overlap or end-to-end timing
accuracy. This child does not run ttsim or hardware measurements.

Numerical validation, kernels/ISA execution, detailed unpack/math/pack overlap,
optimized independent circular buffers, exact NIU/bank/channel behavior,
multicast/atomics/semaphores, general dynamic graphs, automatic operator
lowering and interchip/host execution remain outside this implemented subset.
See the [delivery evidence](../../openspec/changes/wormhole-compute-dataflow/delivery.md)
for requirement coverage, reproducible checks and the validation-harness handoff.
