## Why

The completed topology child can replay a safe, acyclic subset of explicit routes, but the Wormhole profile still has no executing torus transport. Enabling both fabrics requires coordinate-correct routing and finite virtual resources: adding wrap edges to the existing one-VC router would admit cyclic dependencies.

## What Changes

- Add a reusable directed 2D torus builder and deterministic XY/YX routing over canonical fabric coordinates. Bind the Wormhole profile through an explicit transport overlay, preserving physical identities, harvested-worker transit, shared memory inventory, and the original profile evidence.
- Add an opt-in bounded virtual-channel kernel with per-class/dateline resources, packet ownership, cut-through forwarding, and fair arbitration over each shared physical output. Keep the existing mesh and version-1 explicit replay execution paths and their dependency checks.
- Implement finite unicast byte replay and causally generated request/response transport fixtures. Separate request and response resources through endpoints as well as routers. Publish the resource-order argument and its sink/fairness assumptions; do not identify the simplified VC policy with silicon arbitration.
- Make capacities, flit format, native clock domains, stage service, arbitration quantum, endpoint service, and dateline positions explicit configuration. Separate physical-link serialization from router service and end-to-end completion.
- Add fabric-qualified directed-link slowdown schedules and stage/VC-aware traces. Validate targets before execution, preserve routes under slowdown, and report incomplete traffic accurately.
- Validate against pinned architecture sources, independent route/resource oracles, analytical timing and bandwidth bounds, bounded contention/drain tests, and the retained legacy baseline. Validate and commit each completed part before continuing.

This is child 3 of `wormhole-single-chip-simulation-plan`, owning TR-02..04 and the network subset of VA-04. Its milestone is **single-ASIC Wormhole-topology unicast transport with disclosed timing and VC abstractions**. Packet header/NIU transaction semantics, memory service, compute/DFG execution, multicast, synchronization, and hardware calibration remain later work. Response fixtures are not memory transactions.

## Capabilities

### New Capabilities

- `wormhole-topology-routing`: Add TR-D01..10 for torus binding, routes, virtual resources, bounded execution, response dependencies, timing, directed slowdown, trace identity, admission/compatibility, and evidence. Reuse the umbrella and child-2 capability path; there is no delivered main spec at this path yet.

### Modified Capabilities

None. Child-1 and child-2 deltas remain unsynced. This child adds an opt-in transport entry point; it does not relax the full-profile architecture/DFG gate or weaken the existing explicit-route admission rules. Reconcile the children with pending umbrella requirements before any later spec synchronization or archive.

## Impact

- **Detailed simulator:** extend canonical graph binding and routing through new `simulator_detailed/torus.py`, a generic `virtual_channel.py` transport kernel, and `torus_transport.py`. Reuse immutable graph maps, flit definitions, and applicable timing/identity utilities. Touch `noc.py` or shared helpers only where an independently tested extraction is necessary; its one-VC packet/grant behavior stays the legacy path. The existing `Router` and `Link` cannot safely acquire multi-VC behavior merely through a larger configuration value.
- **JSON/API:** introduce `topology_replay` schema version 2 in `configs/schemas/torus_replay.py`, with tagged graph/profile sources, explicit transport bindings, finite traffic, endpoint response fixtures, and directed slowdown schedules. Produce `topology_replay_result` version 2 with resolved graph/plan identities, capacities, policy/evidence labels, causal response IDs, lane/stage events, and completion accounting. Dispatch by exact version in `replay_topology.py`. Preserve version-1 graph/profile/architecture/replay/result meanings and legacy event dictionaries; no implicit migration.
- **Hardware support reporting:** `hardware_profile.py` and documentation must distinguish profile inventory, availability of the opt-in transport binder, and a particular successfully compiled replay. Plain profile inspection must still report the full profile workload path as unavailable; memory capacities remain inventory.
- **Detector/encoder/RL:** retain `topology_compatibility.py` guards and the detailed predictor's 7-D features, encoder's 4-D features, row ordering, and checkpoint assumptions. Reject version-2 transport output at unsupported model boundaries. Do not change models or the separate top-level simulator/predictor/embedding/RL pipeline, observation/action shapes, remapping/rewards, or the 4x4 mesh + Darknet19 + fail-dataset workflow.
- **Assets/validation:** add small configurable torus and Wormhole replay examples, independent source/route fixtures, focused tests, and transport evidence documentation. Extend strict Pyright coverage and use existing unittest/Ruff/OpenSpec checks. No new package dependency, hardware run, ttsim result, or timing-accuracy percentage is implied.
