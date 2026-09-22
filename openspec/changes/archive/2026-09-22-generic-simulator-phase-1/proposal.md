## Why

The detailed simulator executes configurable Wormhole-bound transport, memory,
compute and multicast models, but every public configuration path still carries
device-era names, assumed structure and scoped transaction types. Upcoming work
must describe and simulate private accelerator systems that cannot appear in
this repository: no private node types, parameters, examples or traces may be
committed. A neutral, strictly validated generic layer is required so that
private adapters can drive the public runtime without disclosing private
formats.

## What Changes

- Add a strict version-1 generic system-graph document (kind
  `generic_system_graph`) describing nodes, ports, links, multiple networks,
  DMA endpoints, execution units, memory resources and static routing tables
  under neutral names and identifiers only. Unknown fields fail, no real
  device names or device-derived defaults are admitted, and loading compiles
  into the existing canonical topology machinery through a new adapter and
  subclassed graph rather than a parallel stack.
- Add a unified version-1 transaction runtime (kind
  `generic_transaction_batch`) supporting exactly four transaction kinds —
  `transfer`, `compute`, `wait` and `signal` — executed by composing the
  existing SimPy kernels: static hop-by-hop routing, link serialization,
  bounded buffers/credits/queueing, resource contention, DMA-to-any-endpoint
  transfers, compute/transfer overlap and counter-based wait/signal
  dependencies. A versioned `generic_simulation_result` document records
  completion time, per-transaction start/end, resource utilization and
  incomplete transactions with machine-readable error reasons.
- Select every new behavior through configuration documents and document-kind
  dispatch; existing legacy/profile/Wormhole paths, result kinds and timing
  fixtures remain unchanged and executable. Existing classes are extended by
  subclassing and adapter composition, not replaced.
- Define a neutral external adapter interface (public abstract base plus JSON
  contracts) that lets external code convert private inputs into the generic
  system graph and transaction batch documents. The public repository never
  parses private binaries or traces and contains no private type names,
  parameters, samples or test data. Phase 1 adds no dynamic plugin discovery;
  private launch scripts may import and call the public library directly.
- Ship an independently designed synthetic adapter and a fully synthetic
  two-dimensional acceptance system (nodes, links, two networks, DMA and
  memory endpoints) demonstrating correct connectivity, same-link queueing
  versus distinct-link parallelism, compute/transfer overlap and
  wait-after-signal ordering.

## Capabilities

### New Capabilities

- `generic-simulation`: Neutral strictly-validated system-graph
  configuration, a unified four-kind transaction runtime with versioned
  results, and a public adapter boundary for private inputs.

### Modified Capabilities

None. Existing `wormhole-*` and validation contracts keep their meanings;
generic behavior is additive and selected through new document kinds.
Subclassed extensions SHALL NOT alter the observable behavior of existing
admitted inputs.

## Impact

- **Simulator:** new schema module(s), a `topology_from_generic()` adapter
  and a `GenericSystemGraph` subclass alongside
  `topology_from_legacy()`/`topology_from_profile()`, a unified transaction
  runtime composing the existing routing/lane-credit/serializer/counter
  kernels, and new replay entry points under `simulator_detailed/`. No
  existing result kind, timing fixture or hardware-profile gate changes.
- **JSON and evidence:** new version-1 kinds `generic_system_graph`,
  `generic_transaction_batch` and `generic_simulation_result` plus synthetic
  fixtures; all identifiers neutral. Existing v1/v2 documents remain readable
  and byte-identical in behavior.
- **External systems:** none; adapters are plain library calls, no vendor
  tools, no network access, no dynamic discovery.
- **Detector and RL:** unchanged; the new document kinds are rejected at
  legacy replay/predictor/embedding consumer boundaries exactly like the
  previously added kinds.
- **Dependencies:** none new; pinned SimPy/pydantic versions unchanged.
