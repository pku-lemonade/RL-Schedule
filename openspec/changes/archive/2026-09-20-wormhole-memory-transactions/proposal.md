## Why

The completed torus runtime moves bounded byte packets, but its fixed request/response fixtures do not read or write addressed memory, charge real packet headers, or share L1/DRAM service. Child 4 of `wormhole-single-chip-simulation-plan` adds those missing costs and observable completion events so subsequent compute/dataflow work can depend on actual data availability.

## What Changes

- Add an opt-in, versioned memory replay for contiguous reads, posted writes, acknowledged writes, local memory accesses, and explicitly scoped fences on one ASIC.
- Compile initiators, fabric attachments, address ranges, buffers, and physical memory resources separately; all aliases of one resource share its capacity and combined read/write service.
- Send real header flits and rounded data flits through the existing bounded torus machinery. Segment large logical transfers into explicitly independent requests, with causal read responses or write acknowledgements.
- Model finite endpoint descriptors/staging, source reads, destination writes, buffer readiness, and distinct acceptance, local handoff, destination visibility, response receipt, and completion events.
- Provide explicit legacy DMA lifecycle and scratchpad-capacity adapters while retaining existing DMA issue/datapath/command semantics. Keep full DFG LOAD/STORE integration for child 5.
- Add independent packet/service oracles, contention and drain tests, runnable examples, and an evidence report separating documented behavior, assumed service policy, and unavailable hardware calibration.

## Capabilities

### New Capabilities

- `wormhole-memory-transactions`: Executable bounded addressed-memory transactions and shared service, refining umbrella MT-01..05 and the memory portion of VA-04. MT-06 scalar synchronization remains assigned to child 7. This path follows the umbrella's existing capability organization; it has not yet been delivered to main specs.

### Modified Capabilities

None. Existing topology/transport behavior remains compatible; their implementation may expose reusable internal contracts without changing their public replay semantics.

## Impact

- **Detailed simulator:** Add memory configuration/plan, packet layout, resource, runtime, record, adapter, and CLI modules under `simulator_detailed/`. Reuse/extract bounded routing/link/router internals in `torus.py`, `virtual_channel.py`, and `torus_transport.py`; an opt-in capacity binding may touch `core.ScratchpadMemory`. Update `hardware_profile.py` capability reporting only when execution is implemented.
- **JSON and traces:** Introduce `kind=memory_replay`, integer `schema_version=1`, `MemoryReplay`, `MemoryPlan`, `MemoryReplayResult`, transaction/buffer/service events, and wire-packet records. Add generic and Wormhole examples under `simulator_detailed/configs/memory_replays/`. Preserve `topology_replay` versions 1/2, legacy `Message`/`Flit`, DMA result types, existing traces, and failure JSON. Memory traces are a separate contract.
- **Detector and encoder:** Reject unsupported memory replay/result inputs through the existing dependency-free compatibility boundary. Preserve 7-D detector and 4-D encoder feature contracts and all checked-in checkpoints; no new memory traces become model inputs implicitly.
- **RL and top-level workflow:** Preserve observation/action shapes, mapper behavior, rewards, 4x4 mesh + Darknet19 + fail-dataset workflow, and top-level modules/assets. Compute/DFG adaptation is explicitly deferred.
- **Dependencies and fidelity:** Reuse Pydantic, SimPy, and the existing test/type/lint environment. No numerical tensor contents, MMIO/register execution, exact bank arbitration, detailed DRAM commands, multicast/atomics, multi-chip traffic, or silicon timing accuracy claim is introduced.
