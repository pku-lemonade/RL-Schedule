## Why

The committed memory runtime can execute addressed transactions, but detailed DFG LOAD/STORE tasks still use only scratchpad/LSU delays, FC is a no-op, and positive TPU/LSU work can truncate to zero cycles. A single-ASIC workload model now needs configurable compute costs, reusable finite buffers, and causal reader/compute/writer execution on the existing memory and network resources.

## What Changes

- Deliver child 5 of `wormhole-single-chip-simulation-plan`, covering CD-01..05 and the pipeline subset of VA-04 through seven separately tested implementation parts.
- Add an opt-in, versioned finite workload contract with explicit worker/resource bindings, tensor shapes, storage dtype/layout, compute precision/fidelity policy, addressed transfers, and buffer generations. Initially support dense matmul and bias-free FC; FC lowers explicitly to matmul. Reject unsupported operations, ambiguous layouts, implicit conversions, and unsupported dependency patterns before execution.
- Compute configurable positive durations and finite compute occupancy, with explicit useful/padded work, minimum quantum, clock conversion, and evidence. Do not treat a tensor tile's dimensions as a hardware worker identity.
- Compose a predeclared memory-operation plan with compute gates in one environment. Reuse actual request/response transport and canonical L1/DRAM service; retain local-only accesses and distinct completion/visibility events. Do not add a second LSU delay to already charged transfers.
- Add bounded FIFO buffer slots with generation-aware reserve/publish/consume/release, shared backing capacity, backpressure, and deterministic finite scheduling. Expose reader, local operand service, math, result service, writer, and drain events.
- Add a narrow, explicit legacy DFG adapter for LOAD_FEAT/LOAD_WGT -> FC -> STORE chains with complete sidecar metadata. Preserve the existing supported conv/pool and communication workflow. Intentionally replace legacy FC's silent no-op with a clear unsupported-mode error directing callers to the adapter; this corrects false success rather than removing executable FC computation.
- Deliver generic and Wormhole-profile matmul/streaming examples, independent arithmetic/timeline oracles, consumer guards, capability reporting, and a requirement-to-evidence handoff. Successful runs represent abstract scheduling and traffic, not tensor values, kernel execution, or calibrated silicon timing.

## Capabilities

### New Capabilities

- `wormhole-compute-dataflow`: Validated finite workloads, configurable FC/matmul costs, shared memory/compute execution, bounded pipeline buffers, explicit legacy adaptation, and executable evidence for CD-01..05.

### Modified Capabilities

None. `openspec/specs/` has no delivered main specs for this capability. The umbrella's target delta remains pending; this child supplies its concrete supported subset and must be reconciled before eventual sync/archive.

## Impact

- **Simulator:** Add compute/workload schemas and pure compilation, cost, buffer, runtime, adapter, result, and CLI modules under `simulator_detailed/`. Extend the memory runtime's composition boundary while preserving standalone replay results. Reuse `MemoryResource`, `MemoryService`, packet transport, topology, profile bindings, and the exclusive scratchpad adapter. Make the small explicit FC rejection in `utils/task.py`; avoid a broad `Core`/`Scheduler`/`NetworkMapper` rewrite.
- **JSON and traces:** Introduce `compute_workload` schema version 1 and `compute_workload_result` version 1 with named execution/cost/buffer policies, source/plan digests, stage/buffer/compute-resource events, nested addressed-memory evidence, and pending/drain state. Add examples under `configs/compute_workloads/`. Existing `ArchConfig`, mapping JSON, `MemoryExecutionReplay`/result formats, `Event`, legacy trace windows, and existing example inputs retain their supported meanings. Any additive capability-report fields require explicit tests.
- **Detector/predictor:** Reject the new workload/result format before optional tensor/model work. Keep legacy 7-D runtime features, mesh graph contracts, and checkpoints unchanged; no retraining or Wormhole accuracy claim.
- **RL/embedding:** Keep legacy 4-D hardware features and current observation/action/checkpoint contracts, including `MultiDiscrete([num_layers, max_cores_per_layer, num_ops])`. No Wormhole remapping/training path is enabled. Router count is not a substitute for enabled worker count.
- **Regression boundary:** Preserve the root 4x4 mesh + Darknet19 + fail-dataset path separately from detailed mesh, DMA/fail-slow, topology/torus, and memory replay behavior. New execution is explicitly selected and has no new external runtime dependency. Generated traces/logs stay in temporary test directories; no model artifacts, backups, or downloaded vendor sources are committed.
- **This commit:** Planning artifacts only. No compute runtime capability is delivered by creating this change.
