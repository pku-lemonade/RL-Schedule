## Why

The unified pipeline (`2026-09-22-generic-unified-runtime`) passed acceptance,
which unlocks the deferred memory-subsystem work. Today a memory resource is
a flat byte capacity with an optional endpoint: transfers can stream to or
from it, but there are no addresses, no reads versus writes and no internal
contention. Real system studies need addressed reads/writes and honest
bank/port/channel contention inside memory resources, still fully synthetic
and still inside the single compile/runtime pipeline.

## What Changes

- Extend `generic_system_graph` memory resources with an optional strict
  `hierarchy`: `banks` with `stripe_bytes`, `latency_cycles`, named `ports`
  with `command_cycles`, and named `channels` with `bytes_per_cycle`; every
  value explicit, every name neutral, unknown fields rejected. Resources
  without a hierarchy keep the phase-1 flat behavior unchanged.
- Extend `transfer` with an optional `address`: an addressed transfer whose
  destination is a memory service endpoint is a write, whose source is one is
  a read. Addressed accesses require the target memory to declare a
  hierarchy; address ranges must fit the capacity; addresses on non-memory or
  flat endpoints fail at compile.
- Compile memory hierarchies into plan resources — per memory: bank
  serializers, port serializers and channel serializers with stable neutral
  IDs — with deterministic address-to-bank/port/channel mapping
  (stripe-based), no SimPy objects, no global state.
- Execute memory service in `RuntimeContext`: a write services after network
  arrival, a read services before network departure; command issue occupies
  the mapped port for `command_cycles`, data service holds the mapped bank
  and channel for `latency_cycles + ceil(bytes / channel_bytes_per_cycle)`,
  with sorted-order acquisition (deadlock-free by construction) and full
  release on completion, failure or cancellation.
- Report bank/port/channel busy, queue waits and utilization in the unified
  result; reads and writes keep per-hop network spans plus explicit service
  windows.
- Preserve phase-1/2 behavior bit for bit: flat memories, unaddressed
  transfers, all existing tests and digests are unchanged; legacy guards
  reject nothing new (no new document kinds).

## Capabilities

### New Capabilities

- `addressed-memory`: Addressed read/write transfers over hierarchically
  structured memory resources with bank, port and channel contention inside
  the unified pipeline.

### Modified Capabilities

None. `generic-simulation` and `unified-runtime` contracts keep their
meanings; hierarchy and address fields are optional and additive.

## Impact

- **Simulator:** additive schema fields (`GenericMemoryHierarchy`,
  `GenericTransfer.address`); compile emits memory plan resources;
  `RuntimeContext` gains the memory service stage. No existing fixture or
  result changes.
- **JSON and evidence:** no new document kinds; result resource kinds gain
  `memory_bank`/`memory_port`/`memory_channel` entries for hierarchical
  memories only.
- **External systems:** none; everything remains synthetic.
- **Detector and RL:** unchanged.
- **Dependencies:** none new.
- **Out of scope:** dynamic routing, instruction-level simulation,
  multi-chip, full collectives, private plugins or real device adapters, and
  any real device configuration or calibration data.
