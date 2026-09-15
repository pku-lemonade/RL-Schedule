## Why

The committed hardware-profile child describes physical tiles, harvested workers, fabrics, and shared memory identities, but execution still equates each mesh router with a compute core and reconstructs connectivity separately in consumers. This second child of `wormhole-single-chip-simulation-plan` establishes one graph contract and executable synthetic heterogeneous transport before Wormhole routing is introduced.

## What Changes

- Add a versioned canonical directed graph with separate physical tiles, fabric-qualified routers, directed links/ports, endpoint attachments, compute eligibility, logical-worker mappings, and unique physical resource identities. Disabled compute does not remove transit connectivity.
- Compile existing synthetic mesh configurations into that graph while preserving router/link indices, construction order, PE/DMA bindings, routing, timing, and legacy failure behavior. Make detailed runtime construction and supported topology consumers use the shared graph.
- Project hardware-profile inventories into the same identity model with explicitly unresolved connectivity. Retain the original profile hash and evidence; do not invent torus links, runtime timing, compute cores, or memory services.
- Add opt-in transport replay for explicit directed graphs. Reuse the existing finite-buffer, credit, serialization, and round-robin machinery; support configurable deterministic unicast route tables whose combined channel dependencies are acyclic. Reject unsupported routes and modes before resource admission.
- Export the graph and replay trace identity maps together. Provide explicit consumer compatibility checks; preserve the legacy detector/encoder contract and reject generic replay data from unsupported model/RL paths.
- Deliver independent graph/route fixtures, mesh parity regressions, replay completion/backpressure tests, negative admission tests, and evidence documentation. Commit each completed, validated implementation part before starting the next.

This child owns umbrella TR-01 and the graph/attachment portion of runtime HP-03/04. Its executable milestone is **transport replay on a synthetic heterogeneous graph**, not execution of a Wormhole hardware profile or heterogeneous DFG workloads. TR-02..04 torus policies, virtual resources, and directed failure semantics remain in child 3; memory service and compute scheduling remain in children 4 and 5.

## Capabilities

### New Capabilities

- `wormhole-topology-routing`: Deliver TR-G01..10 for canonical identity, complete versus unresolved connectivity, legacy mesh adaptation, explicit directed unicast execution, route admission, trace identity, consumer guards, and validation. This uses the umbrella's reserved capability path; no delivered main spec currently exists there.

### Modified Capabilities

None. The unsynced child-1 `wormhole-hardware-profile` delta remains authoritative for its delivered subset; this child adds graph contracts without weakening HP-P08's profile execution gate. Reconcile child/umbrella deltas before later synchronization or archival.

## Impact

- **Detailed simulator:** new `configs/schemas/topology.py`, `topology.py`, `topology_compatibility.py`, `routing.py`, `transport.py`, and `replay_topology.py`; changes to `architecture.py`, `endpoint_registry.py`, `noc.py`, `utils/definitions.py`, and trace integration. Keep `Core`, DFG, mapper, LSU, TPU, and DMA command semantics in their existing boundaries. New replay terminals move bytes and do not impersonate memory/compute services.
- **JSON and API contracts:** add `kind: canonical_topology`, `kind: topology_replay`, and `kind: topology_replay_result`, each at schema version 1. Add synthetic examples under `simulator_detailed/configs/topologies/` and `configs/replays/`. Existing architecture/profile, workload, failure, and legacy trace JSON retain their accepted meanings. New graph-context fields on internal transport records are opt-in and excluded from legacy trace output; the replay result contains graph/plan hashes and canonical-to-runtime ID maps.
- **Detector:** `simulator_detailed/predictor/topology.py` consumes the legacy graph adapter instead of duplicating mesh edge generation. The detailed builder/predictor boundary must reject incompatible graph semantics before creating features or loading a checkpoint. Preserve 7-D core/link features and their existing row ordering; no checkpoint migration or training.
- **Encoder and RL:** `simulator_detailed/embedding/hw_encoder.py` uses graph coordinates/identities for its supported legacy inputs and explicitly rejects other graph contracts. Preserve its four input features and existing ordering. The separate top-level `simulator/`, `predictor/`, `embedding/`, and `rl_agent/` pipeline remains unchanged, including observation/action shapes, checkpoints, and the 4x4 mesh + Darknet19 + fail-dataset workflow; no generic-graph RL support is claimed.
- **Validation/dependencies:** use the existing Python 3.12 environment, Pydantic, SimPy, unittest, Pyright, and Ruff. Add a dependency-free consumer contract check so optional Torch/PyG absence does not hide graph incompatibility. Actual tensor/checkpoint smoke results require those optional packages and must be reported separately. Do not change the repository's dependency export for this design.
