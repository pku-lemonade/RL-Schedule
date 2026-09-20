## Context

See `proposal.md` for motivation. Exploration starts from `694e9eb`, the completed memory child. Its delivery report records 230 passing detailed tests plus one optional Torch/PyG skip, strict type checks and scoped lint; these are inherited evidence, not tests rerun for this planning commit. No `NOC_ARCHITECTURE` file or matching available Git history was found. Earlier child designs, delivery reports and executable code provide the available architecture context.

| Current boundary | Observed behavior | Consequence for this child |
| --- | --- | --- |
| `core.TPU` / `LSU` | Integer floor division of work by rate | In-memory probes with work 1 and rate 64 both finish at time 0; new costs need a positive quantum without changing legacy conv/pool timings |
| `utils.task.Task.execute` | FC branch is `pass`; LOAD/STORE use SPM and LSU only | FC must become executable in the opt-in path; legacy FC must stop falsely reporting successful work |
| `Slice`, DFG and mapper | Slice sizes count elements, with no dtype/address/layout; mutable readiness counters; mesh integer core mapping | Do not infer byte size, physical worker identity, or memory dependencies from these fields |
| Legacy convolution/pooling | Shape-specific work proxies, including a flattened convolution kernel dimension | Do not reinterpret those established proxies as standard FLOPs or silently change them |
| `Core` / `Scheduler` | Starts its own execution loop, rounds event times, polls idle work in large time steps | Reusing it wholesale would import timing and scheduling semantics unrelated to the new model |
| `MemoryRuntime` | Creates its own environment, schedules a finite admitted plan, releases reservations on full drain | Needs controlled composition and deferred teardown, not a second memory ledger or independent per-job simulation |
| `MemoryResource` / `MemoryService` | Canonical backing ownership, versioned readiness, access leases and one bounded aggregate read/write server | Use these same objects for network DMA and local operand/result service |
| Consumers | Detailed predictor/encoder guard legacy mesh/events; new result documents are rejected | Keep new workloads outside existing checkpoints/RL contracts |

The corrected FC probe used a core stub providing only an environment and finished at time 0 without accessing compute or memory resources. An initial probe omitted that required environment and raised `AttributeError`; it is not counted as execution evidence.

## Goals / Non-Goals

**Goals:** an additive finite workload runtime; independently testable costs and admission; executable bias-free FC/matmul; actual local/remote memory effects; bounded storage and stage resources; deterministic overlap and drain; clear compatibility/evidence contracts. Own CD-01..05 and pipeline VA-04. Design is required because composition crosses memory ownership, scheduling, schemas and consumers.

**Non-Goals:** ISA/RISC-V/TT-Metal kernel execution, tensor values, numerical precision validation, automatic operator lowering, convolution/pooling in the new mode, automatic layout conversion, sparse/compressed/mixed-format arithmetic, split-K accumulation, cache/reuse optimization, arbitrary dynamic graphs, exact unpack/math/pack registers or instructions, interchip/host/PCIe work, multicast/atomics/semaphores, new detector/RL models, or silicon calibration. Existing supported legacy operations remain available. Earlier memory/NoC fidelity limits still apply.

## Decisions

### 1. Add a separate opt-in workload and pure admission boundary

Use `kind: compute_workload`, `schema_version: 1`, and policy `finite_compute_dataflow_v1`. Proposed modules are `configs/schemas/compute_workload.py`, `compute_plan.py`, `compute_cost.py`, `compute_buffers.py`, `compute_runtime.py`, `compute_adapters.py`, `compute_records.py`, and `replay_compute.py`; split further only for a concrete responsibility.

The workload declares its graph/profile and memory settings using existing contracts, compute resources/rates, dtype storage definitions, tensor descriptors, worker bindings, finite ordered streams of jobs, and slot geometry. Each job names its operation, A/B/C tensors and addressed ranges, execution worker, reader/writer fabrics and modes, and any supported predecessor. IDs and all derived extents are revalidated before SimPy resources or output files exist. Admission distinguishes a parsed document, a compiled plan and an executable supported plan.

A worker is a canonical enabled physical compute resource with its local L1 and fabric interfaces. Explicit bindings must agree with topology/profile role, ownership and enabled mask; interfaces on two fabrics do not duplicate the compute engine. A router-only or harvested tile cannot execute work. Bindings and rates are data, never branches on an n150 name, a hardcoded worker count, or a 32x32 default.

Initially admit finite single-worker streams, and multiple independent streams sharing the chip. A same-worker job dependency can wait for a predecessor's declared completion; it cannot introduce backward/cyclic FIFO waits. Source/destination version and physical-conflict checks must agree with the memory plan. Reject cross-worker completion dependencies that would require an unmodeled notification; independent workers may still access eligible initialized remote L1 or DRAM. This is a declared subset, not arbitrary DFG support.

**Alternative:** extend the legacy mapping JSON or route every profile through `Arch`. Rejected for this child because it would implicitly change tensor byte semantics, core indexing, legacy scheduling and consumer shapes together.

### 2. Make cost, storage and precision assumptions independent

Initially execute `matmul` and `fc` with positive integer dimensions. Normalize A `[B,M,K]`, B `[B,K,N]`, C `[B,M,N]`; an explicit shared-weight flag permits B `[K,N]`. FC is the same operation with declared flattened input/output dimensions and no bias/activation. The adapter must supply the flattening; do not guess it from four legacy slice axes. Reject zero/negative/mismatched dimensions, unsupported transposes, fused bias, implicit broadcasting and in-place output.

For `effective_matmul_v1`, count one multiply-add as two operations:

```text
useful_work = 2 * B * M * N * K
executed_work = 2 * B * round_up(M, qm) * round_up(N, qn) * round_up(K, qk)
service_native = setup + max(min_quantum, ceil(executed_work / (rate * quantum)) * quantum)
service_aci = service_native * aci_clock_hz / compute_clock_hz
```

`qm/qn/qk`, rate, setup, service quantum, minimum quantum and clock are explicit positive/nonnegative validated settings, with minimum quantum a positive quantum multiple. Reject nonfinite or unrepresentable durations, including a positive duration that cannot advance the current simulation time. Generic examples can set block dimensions to 1. Emit useful and executed work separately. Padding is a cost policy, not proof of a particular kernel's instruction schedule.

A rate entry matches operation, operand/output dtype, accumulator precision, layout and fidelity label exactly. Each entry gives an **effective final** work-per-native-cycle rate and evidence; fidelity is not multiplied into the duration again. No fallback from an unknown tuple to an arbitrary peak. Synthetic dtype labels with positive integral storage bytes are allowed only with explicit matching rate entries. The Wormhole example uses a documented storage format with explicitly assumed effective performance. A public peak is only an upper-bound sanity reference.

Storage is independent of arithmetic work: dense row-major extents use element count times configured dtype bytes; tiled extents use separately declared tensor tile rows/columns and padded storage. Initially require already compatible layouts; the simulator does not tilize/untilize, pack block-floating exponents, or pretend that labels perform conversions. The entire padded transferred extent must fit its reservation and is included in addressed-memory bytes; report useful tensor bytes separately from storage padding and packet padding.

Each job fits its complete operand/output block in its assigned L1 slot. Larger operations require explicit M/N block jobs with full K; split-K reduction and hidden streaming registers are unsupported. This avoids a large mathematical operation fitting into a tiny fictitious on-chip workspace.

**Alternative:** fix `TPU.occupy` globally and call legacy FC. Rejected because a single rate does not establish dtype/layout/fidelity or remote memory semantics and would change existing timing fixtures.

### 3. Compose finite memory operations with external compute gates

Refactor `MemoryRuntime` internally around an attachable finite session, retaining the standalone wrapper and its current result semantics. A session accepts one environment, its admitted memory/resources/transport, a closed set of operation activation gates, and explicit finalization ownership. A public lifecycle-event accessor replaces access to private event dictionaries. No operation is submitted twice; no new operation/range/route is inserted after admission. Standalone replay opens all gates using its current start/dependency rules and owns teardown as before.

The workload compiler expands jobs and slot reuse into a finite memory plan. Remote reader operations are existing addressed reads; remote writers are posted or acknowledged writes. Compute operand reads and result writes are ordinary local operations against the **same** L1 owners. A result local write names its existing producer-operation version and is gated by math completion. The memory-only dependency plan remains valid conservatively (for example, a result write depends on operand reads); compute gates add causality and never remove memory conflict/order checks.

Validate the combined memory, compute, FIFO and slot-reuse event DAG before runtime. An external gate must be owned by an admitted stage with a causal path to completion; arbitrary caller-created events cannot bypass admission. The runtime coordinator may observe trace facts from any resource, but may release a hardware-visible dependency only through the existing legal local readiness/completion rules. This prevents a zero-cost remote acknowledgement.

Do not run one standalone memory replay per job: that would recreate shared resources, erase contention and free live reservations. Do not implement an unrestricted `submit()` API in this child. Finite metadata can grow with job count; resident payload, descriptors, active jobs and staging remain bounded by configured resources.

**Alternative:** a general dynamic transaction engine. Deferred; predeclared operations already cover finite placement/overlap experiments and retain current pure admission/version validation.

### 4. Use conservative reusable item slots with one capacity owner

Select `fifo_item_slots_v1`: each stream has a configurable number of reusable slots. A slot is a bundle of disjoint A/B/C extents in the worker's local L1; the union of all slot footprints and other memory reservations is charged once at setup. Each finite job has deterministic FIFO assignment `sequence % slot_count`. Reusing a logical slot does not allocate its L1 bytes again.

```text
free -> reserved(g) -> inputs_published(g) -> consuming(g)
     -> output_published(g) -> draining(g) -> free(g+1)
```

Only the current job/generation can publish, consume or release. Reserve the whole bundle before starting its reader, with no partial bundle ownership while waiting for capacity. Publish inputs only after all required reader memory effects; publish output only after math and local result-write service. Hold the bundle conservatively until the writer's configured local/acknowledged completion, with any source lease already released. A posted writer can release its local slot at local completion while remote effects remain in flight; overall workload success must still wait for those effects. Local-only results can release after their declared local consumer completes. All transitions and occupancy are traced.

The invariant is `free_slots + occupied_slots = configured_slots`; occupied includes reservations, ready items and in-flight users. Physical capacity remains the existing reservation ledger. Slot reuse uses unique producer operation IDs and invalidates old readiness through the existing write access path. Reject stale generations, double publication/release, use before publication and overlap between supposedly distinct physical slot extents.

This bundled policy is intentionally more conservative than independent TT-Metal input/output circular buffers and holds inputs longer than some optimized kernels. Its scope and throughput limitation are reported. It satisfies finite producer/consumer semantics without pretending to reproduce register double buffering or every circular-buffer API. Separate input/output pools can be a later policy with its own lifetime/deadlock argument.

**Alternative:** a new independent `simpy.Container` for every pipeline buffer. Rejected because it would duplicate L1 capacity already owned by `MemoryResource`. The existing scratchpad adapter is used only when explicitly binding an empty idle legacy scratchpad; the new runtime does not auto-bind a running `Core`.

### 5. Schedule independent bounded stages with explicit service boundaries

Each worker shares configurable reader/writer descriptor limits and finite compute engine slots across its streams. The initial math lane is a nonpreemptive FIFO with bounded admission; slots model independent effective engines, not multiple threads multiplying one engine's peak. Rate is per declared engine slot. An example reflecting one Matrix Unit uses one slot; all values remain configurable.

The stages and actual byte charges are:

```text
reader:  remote source read -> NoC -> local L1 write
compute: local L1 operand reads -> bounded math -> local L1 result write
writer:  local L1 output read -> NoC -> remote target write [-> ack]
```

The two L1 accesses at each handoff are distinct producer/consumer work, not duplicate charges for one transfer. A local source already in the assigned slot omits its network read and waits for admitted readiness; a local-only destination omits network write. Do not replace local operand service with mere allocation. Do not add legacy LSU, SPM allocation delays or the old synthetic sink delay on top of these same services.

Wait for input readiness and reserve the output slot before acquiring a math grant. Local operand reads finish before math begins; the output local write starts after math ends. This conservative job policy permits reader/compute/writer overlap across slots, without claiming detailed unpack/math/pack overlap within one job. Store any intermediate ownership in the already charged slot and a bounded stage/compute context; do not hide an unbounded queue of computed results. Keep the active compute context charged through result publication, distinguishing arithmetic busy time from context occupancy and output-service stall.

One item slot serializes that stream's reader/compute/writer lifetimes. Two slots can overlap different jobs if resources permit; more slots do not guarantee a speedup under shared L1/DRAM/NoC bottlenecks. A test with three independent constant-duration stages `R=2,C=3,W=2` and three jobs expects depth-one makespan 21 and depth-two makespan 14 under this bundled policy. This is a scheduler oracle, not a Wormhole timing prediction. Integrated memory tests instead derive start/finish events from actual service and routes.

Wait-resource argument for the admitted subset:

| Wait | Retained ownership | Progress reason |
| --- | --- | --- |
| Free item slot | Finite declared job metadata; no partial slot or stage grant | Earlier same-stream FIFO item reaches its independent writer |
| Input readiness / eligible stage | Charged whole slot, no math grant | Acyclic admitted producer and bounded fair memory/transport service |
| Math grant | Ready input/output slot, bounded stage admission | Earlier finite math/context work releases after local result service |
| Result/local memory service | Slot and bounded compute context | Memory grant never waits on compute/network; finite chunk service |
| Writer/network completion | Slot and counted writer/transport state | Independent response/consumer paths and prior memory-child drain argument |
| Final teardown | Persistent physical reservations | All stages, operations, effects, descriptors, leases, credits and slots have drained |

Reject dependencies that make an active stage wait for a later item on its own blocked FIFO. The event DAG alone is not the capacity/deadlock proof: test capacity-one resource combinations and inspect actual acquisition order against this table. A finite horizon means incomplete observation; idle with pending work is diagnosed, never called success.

### 6. Keep legacy adaptation narrow and consumer contracts explicit

Add `legacy_fc_chain_v1` import of simple LOAD_FEAT/LOAD_WGT -> FC -> STORE chains. Sidecar metadata names the physical worker, dtype/layout/normalized dimensions, addresses, fabrics, slots and all rates. It must account for every imported node/edge and reproduce the direct workload's plan/behavior except for source provenance. Validate against an immutable snapshot; do not mutate mapper readiness, infer bytes from elements, or assume integer mesh core IDs identify Wormhole workers.

Reject incomplete sidecars, unsupported nodes including CONV/POOL/SEND/RECV, partial reductions, fanout/reuse not expressible by the admitted stream, and cyclic or unmatched load/store edges. Users retain the legacy path for those existing supported operations. Replace only legacy FC's empty branch with a diagnostic requiring the explicit compute adapter; direct FC previously had no useful execution to preserve. Legacy TPU/LSU floor costs and rounded `Event` times remain documented compatibility behavior; the new path uses unrounded cost/stage records.

The new runtime does not return legacy `NoC` mappings or fabricate legacy `Event` rows. Predictor and embedding entry points must reject workload/result objects before Torch/PyG loading. Root RL receives no new runtime selector; preserve its 4x4/16-worker/48-directed-link/11-window and checkpoint assumptions where applicable. Detailed feature contracts remain 7-D runtime and 4-D hardware. Tests cover admission with optional ML packages absent, and record available model tests separately.

Expose `abstract_compute_workload_v1` as a separate capability only when executable. Keep `Arch`/`simulate()` full-profile admission closed: a supported finite matmul workload does not implement arbitrary legacy mappings, all operators, or TT-Metal kernels. Parsing/compilation/execution/numerical fidelity/hardware timing are separate status fields or clearly scoped report entries.

**Alternative:** silently reinterpret every legacy DFG operation. Rejected because dtype, memory ownership, shape conventions and synchronization are missing and cannot be guessed safely.

### 7. Export enough evidence to distinguish planned work from execution

Use `compute_workload_result` version 1 with effective cost/buffer/execution policies, source/plan hashes, operation costs, worker/engine identities, stage waits/start/end, token generations/occupancy, nested addressed-memory records, completed arithmetic work, pending IDs and pre/post-teardown state. Planned cost estimates are not completed compute work. Preserve the memory result's own units/accounting and label its status as memory-session status when the enclosing workload is still pending. Do not claim standalone memory teardown occurred for a composed session.

Separate tensor useful/storage bytes, network logical/header/padding/channel bytes, memory useful/rounded service bytes, math duration, resource occupancy and end-to-end completion. A short run must retain all charged state and can resume without duplicate work. Successful finalization is exactly once and restores physical reservations; repeated result reads/finalization do not repeat releases.

The CLI `python -m simulator_detailed.replay_compute --workload <file>` follows memory replay conventions: deterministic JSON, optional output, relative input paths resolved against the workload file, exit 0 complete / 1 incomplete / 2 invalid, no overwritten prior output for invalid admission. Examples are small generic matmul, generic streaming depth controls, and single-ASIC Wormhole BF16 matmul with explicitly assumed rates and enabled-mask provenance. Runtime tests may generate larger variants in temporary directories; do not commit generated traces or vendor sources.

### 8. Validate model mechanisms before external accuracy claims

Required layers:

1. Pure admission and independent arithmetic checks for at least two tensor block geometries, element widths, clocks and rates; nonempty sub-quantum work, FC normalization and unsupported cases.
2. Standalone memory session equivalence; gates actually delay admission, while shared local/network users still contend and old packet/plan/timing fixtures remain stable.
3. Token/version/capacity conservation over more jobs than slots, mixed aliases/fabrics, stale-handle rejection, and short-run continuation.
4. Independently enumerated stage timelines, depth-one/two controls, shared-versus-independent compute/memory resources, slow readers/writers and capacity-one drain. Reconstruct byte/work totals from exported executed events.
5. Direct versus imported FC workloads, generic and Wormhole CLI smoke, legacy detailed mesh/fault/DMA regression, separate root mapping/workflow checks, and consumer rejection before optional model work.

Do not run this simulator to manufacture an "independent" expected fixture. Small schedule oracles enumerate service dependencies; source-backed facts and synthetic model assumptions have different labels. ttsim and hardware measurements remain unavailable until actually run/imported; this child does not move calibration tooling out of child 6.

### 9. Record primary source anchors without turning peaks into fitted rates

Read on 2026-09-17; raw contents were SHA-256 checked in memory and not copied into the repository. ISA revision is `tenstorrent/tt-isa-documentation@acaf010519f4fdd323df5077e45b8695f70e4279`; software example revision is `tenstorrent/tt-metal@975015c2f03bb818eaee2422c3845fba381eaf8c`. These independently pinned sources are architectural/programming references, not a tested compatible execution environment.

| Source | Relevant constraint / limit | SHA-256 |
| --- | --- | --- |
| [Tensix tile](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/README.md) | Local memory, NoC interfaces and compute components; not equivalent to one software tensor tile | `9eff3b22876ddf6b30cb09c8422098fa2770b4ad7c132e72cdd48c15ac6cd12d` |
| [Coprocessor](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/TensixCoprocessor/README.md) | Concurrent control threads share backend resources; threads do not multiply one matrix engine's rate | `e8eb366b73d3f509185790b61500c32d9d4b8d7d04bd881767f51c07bfd3a8ce` |
| [Matrix Unit](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/TensixCoprocessor/MatrixUnit.md) | Throughput depends on operation and fidelity; theoretical peaks are not sustained matmul rates | `599e8420c956532d0717cf7d8a40c068ff090f813851a571ff76fc9a1a20a0b4` |
| [L1](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/L1.md) | Local compute clients and network clients share storage/service; aggregate policy remains approximate | `276d09a25442beba658a81ff19a5b82462c20b744ddf582130c69680c4257335` |
| [Matmul reader](https://raw.githubusercontent.com/tenstorrent/tt-metal/975015c2f03bb818eaee2422c3845fba381eaf8c/tt_metal/programming_examples/matmul/matmul_single_core/kernels/dataflow/reader_single_core_mm.cpp) | Reserves storage, issues reads, waits for completion, publishes input | `0183b19af95ebf17f0a43b74f8f02e88731c9a753f4beb49be57a99739cb2658` |
| [Matmul writer](https://raw.githubusercontent.com/tenstorrent/tt-metal/975015c2f03bb818eaee2422c3845fba381eaf8c/tt_metal/programming_examples/matmul/matmul_single_core/kernels/dataflow/writer_single_core_mm.cpp) | Waits for output, writes and waits before reuse; local handoff and final drain are distinct | `e9e1eb6323ff57924132a6d14d905c5b466a943d5969afb275e999475d231030` |
| [Matmul compute](https://raw.githubusercontent.com/tenstorrent/tt-metal/975015c2f03bb818eaee2422c3845fba381eaf8c/tt_metal/programming_examples/matmul/matmul_single_core/kernels/compute/mm.cpp) | Input readiness, math, packing and publication are separate responsibilities; its detailed reduction/register schedule is not reproduced | `ce3e1d5a0293d7310a486af062ea2e1114f23cfd48e86d174ff093d39f751b27` |

## Risks / Trade-offs

- [Conservative bundled slots understate optimized overlap] -> Name the policy in results, test its exact model and state that independent circular buffers/register pipelining are deferred.
- [Static pre-expansion becomes a hidden unbounded data queue] -> Permit finite descriptors as metadata only; every live job, buffer, staging flit and compute result retains a configured resource owner.
- [External gates bypass memory ordering or create cycles] -> Validate one combined event graph and legal local/remote event scopes; retain existing memory hazard/version validation.
- [Memory session refactor changes standalone behavior] -> Preserve default activation/finalization and compare full standalone events, digests, timings, capacity and incomplete continuation.
- [Storage dtype is confused with arithmetic fidelity] -> Exact rate keys, separate accumulator/fidelity labels, no implicit conversions or automatic peak-rate fallback.
- [Early posted completion frees remote effects] -> Free only the safe local slot; track destination visibility, credits, leases and outstanding operations through global drain.
- [Legacy FC rejection affects callers relying on false completion] -> State the diagnostic and migration explicitly; supported legacy conv/pool workloads keep their prior behavior.
- [OpenSpec configuration parser ignores design rules] -> The CLI warns that one existing YAML design-rule entry is not a string; apply the inspected repository boundary/count/consumer constraints manually for this change. Do not edit unrelated workflow configuration here.

## Migration Plan

Implement seven tested commits in `tasks.md`: contracts/costs; composable memory session; reusable slots; single-job execution; bounded overlapping streams; legacy adapter/consumer guards; CLI/evidence. Revisit this design within the child if concrete integration requires a different policy; do not weaken the acceptance tests or expand to arbitrary kernels silently.

Select the new CLI/adapter explicitly. Existing profile and replay JSON keep their contracts. Rollback for workload users is using the earlier supported legacy or standalone memory execution mode; no backup artifacts are needed. The only deliberate legacy behavior correction is rejecting unimplemented FC instead of succeeding without work.

Each implementation part runs relevant tests, strict Pyright including all new modules, scoped Ruff and whitespace checks before its commit. Final validation includes all detailed tests, existing CLI regressions, a separate legacy workflow smoke, strict OpenSpec validation and a delivery report. Optional Torch/PyG or hardware evidence absent from the environment is reported unavailable, never passed.

Planning artifacts do not complete any implementation tasks. Reconcile CD-01..05 and pipeline VA-04 against executed evidence at delivery, then explore `wormhole-validation-harness`; multicast/synchronization remains child 7. Do not sync/archive the umbrella, alter its pending targets, or push without a separate request.
