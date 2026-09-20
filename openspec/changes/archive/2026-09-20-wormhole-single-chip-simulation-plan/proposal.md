## Why

The detailed simulator already models useful network, DMA, scratchpad, scheduling, and failure behavior, but its executable topology and task paths do not yet describe a Wormhole B0 ASIC. We need a staged, evidence-driven extension for single-chip placement and scheduling studies, with explicit fidelity limits and reusable hardware mechanisms.

## What Changes

- Define an umbrella roadmap for a simplified Wormhole B0 performance model, initially targeting one ASIC as used by n150. A dual-ASIC n300 card and interchip communication are outside this initial scope.
- Separate generic mechanisms, configurable policies, and hardware profiles. Describe physical tiles, enabled compute tiles, directed network fabrics, memory resources, transactions, and abstract compute pipelines without spreading device-specific branches across the simulator.
- Preserve currently executable synthetic mesh, DMA, scratchpad, failure, and trace behavior through compatibility tests and explicit profile selection.
- Establish seven sequential child changes: hardware profile; heterogeneous topology; dual-NoC routing; memory transactions; compute dataflow; validation harness; multicast and synchronization. Each child must pass its own explore, concrete proposal, apply, validation, and review cycle before the next child's design is finalized.
- Require structural, protocol, functional-reference, and measured timing evidence to be reported separately. Public specifications and functional simulation alone cannot establish silicon timing accuracy.
- Keep the umbrella's requirements pending until executable child implementations and evidence satisfy them. Creating these planning artifacts does not implement Wormhole support or authorize a monolithic apply of the roadmap.

## Capabilities

### New Capabilities

- `wormhole-hardware-profile`: Configurable, provenance-bearing single-ASIC profiles, physical and enabled tile identity, supported feature declarations, and legacy profile compatibility.
- `wormhole-topology-routing`: Shared directed topology, distinct fabric coordinates and routes, bounded flow control, deadlock prevention, and eventual multicast delivery.
- `wormhole-memory-transactions`: Explicit network transactions, packet costs, completion and ordering semantics, shared L1/DRAM service, and eventual scalar atomic synchronization.
- `wormhole-compute-dataflow`: Abstract compute costs, real memory traffic, bounded producer/consumer buffers, and reader/compute/writer overlap.
- `wormhole-validation`: Reproducible evidence, conformance and regression tests, calibration limits, fidelity reporting, and staged delivery gates.

### Modified Capabilities

None. There are no existing main specifications under `openspec/specs`; this change does not modify the separate `improve-rl-local-remap` change.

## Impact

- **Simulator:** Future children primarily affect `simulator_detailed/configs/schemas/arch_config.py`, `simulator_detailed/architecture.py`, `simulator_detailed/endpoint_registry.py`, `simulator_detailed/noc.py`, `simulator_detailed/dma_endpoint.py`, `simulator_detailed/core.py`, `simulator_detailed/utils/task.py`, and corresponding tests/docs. Profile and topology JSON, DFG memory/compute descriptions, and structured traces require explicit compatibility/versioning decisions in the owning child.
- **Traces:** Preserve the meanings of `Event`, `Flit`, `Message`, `FlitEvent`, `NoCLinkIdentity`, and `MessageFabricTiming`. Transactions and resources need identities and timing fields that distinguish descriptor acceptance, local handoff, packet service, response delivery, and operation completion.
- **Detector/predictor:** `simulator_detailed/predictor/topology.py` reconstructs a mesh independently; generic topology consumers must use the simulator's canonical graph. Existing trained models and hardware features are not automatically compatible with heterogeneous tiles, directed torus links, or changed endpoint counts.
- **RL:** Existing action/observation contracts, checkpoints, and local-remap behavior remain part of the legacy regression boundary. A Wormhole-enabled RL pipeline needs an explicit compatibility declaration or a separately scoped adaptation; training a new detector or policy is not promised here.
- **Legacy workflow:** Preserve the existing 4x4 mesh + Darknet19 + failure workflow where it uses the legacy simulator, and preserve the detailed simulator's currently supported synthetic examples. These are separate regression paths; extending `simulator_detailed` does not silently convert the legacy simulator or its model artifacts.
- **Current change:** Planning documents only. Child implementations may introduce opt-in schema extensions; any incompatible contract change must be identified and migrated in its own child proposal.
