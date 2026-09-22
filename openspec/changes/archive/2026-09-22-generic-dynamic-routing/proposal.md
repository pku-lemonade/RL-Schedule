## Why

The unified pipeline and addressed memory are delivered and archived. The
remaining deferred network item is dynamic routing: today every transfer
follows a static route declared per (network, source, destination), so
studies that need runtime path selection — topology-computed shortest paths
or congestion-aware choices — cannot be expressed. This change adds opt-in
per-network routing policies while keeping static tables as the default and
all prior behavior byte-identical.

## What Changes

- Extend `generic_system_graph` networks with an optional routing policy:
  `static_table` (default, current behavior), `shortest_path` or `adaptive`.
  A network with a dynamic policy must not declare static routes; ambiguous
  configuration fails validation.
- Compile dynamic networks purely: directed BFS distance tables per needed
  destination node, effective link timings, endpoint node resolution and
  compile-time reachability (an unreachable pair classifies as
  `route_unreachable` exactly like a missing static route). No SimPy object,
  no global state, deterministic plan digests.
- Execute hop-by-hop selection in `RuntimeContext`: at each node the
  transfer chooses among out-links that strictly decrease the BFS distance
  to the destination, so progress is guaranteed and livelock is impossible.
  `shortest_path` picks the lexicographically first candidate; `adaptive`
  picks the candidate with the smallest current credit queue (users plus
  pending), tie-broken by link identity — deterministic for repeated runs.
- Record the chosen hops in spans exactly like static transfers, emit a
  deterministic `route_select` trace event per hop decision, and keep
  accounting, cancellation and result contracts unchanged.
- Compatibility: networks without a policy keep `static_table`; every
  phase-1/2/3 fixture, test and digest is untouched; no new document kinds.

## Capabilities

### New Capabilities

- `dynamic-routing`: Per-network shortest-path and congestion-adaptive
  hop-by-hop routing with compile-time distance tables and deterministic
  selection inside the unified pipeline.

### Modified Capabilities

None. `generic-simulation`, `unified-runtime` and `addressed-memory`
contracts keep their meanings; the routing policy field is optional and
additive.

## Impact

- **Simulator:** additive `routing` on networks; plan gains dynamic network
  tables and per-transfer effective policy/nodes; runtime gains hop
  selection. Static path unchanged.
- **JSON and evidence:** no new document kinds; one additive trace action
  (`route_select`).
- **External systems:** none; everything remains synthetic.
- **Detector and RL:** unchanged.
- **Dependencies:** none new.
- **Out of scope:** instruction-level simulation, multi-chip, full
  collectives, private plugins or real device adapters, and any real device
  configuration or calibration data.
