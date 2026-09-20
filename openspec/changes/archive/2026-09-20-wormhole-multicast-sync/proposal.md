## Why

The completed validation harness covers finite unicast, addressed memory and compute pipelines, but shared multicast delivery and scalar synchronization remain unsupported. Child 7 of `wormhole-single-chip-simulation-plan` must make those mechanisms executable before TR-05, MT-06 and the remaining multicast/synchronization portion of VA-04 can be reconciled.

## What Changes

- Introduce opt-in `multicast_sync_workload` v1 and `multicast_sync_result` v1 contracts for one finite single-ASIC workload, with explicit hardware settings, graph/profile identity, memory ownership, operation dependencies and unsupported-mode errors.
- Support addressed rectangular multicast writes to eligible worker L1s on either configured fabric. Select a bounded, non-wrapping, corner-entry major/minor/major tree subset; specify source inclusion, exact recipients, shared-prefix replication, one source read, per-recipient memory service, posted/acknowledged completion and finite destination backpressure. Reject arbitrary masks, multicast reads/atomics and unsupported tree shapes.
- Admit whole-tree multicast lane reservations without partial acquisition, with finite control/replication storage and a documented resource-dependency argument. Reuse physical link/router service so multicast, ordinary memory traffic and synchronization contend on the same hardware. Reservation timing is an explicit configurable abstraction, not register-accurate path reservation.
- Add initialized, addressed scalar counters, remote posted/returning increments and local threshold waits. Serialize read-modify-write service on the canonical L1 owner, carry actual request/response traffic and release dependencies only after visible state and required data readiness. Distinguish L1 software semaphores from hardware semaphore instructions and numerical tensor reductions.
- Compose the new operations with the existing finite memory/FC/matmul stages in one admitted environment and resource registry. Preserve bounded slot generations and local visibility rules; add explicit notifications rather than allowing zero-cost remote completion observations.
- Extend the validation harness through a new named adapter and independent destination/tree/byte/state/causality/drain audits, including observation-corruption tests. Deliver small analytical examples, a Wormhole-profile example, compatibility checks and a requirement-to-evidence report in separately validated and committed parts.

## Capabilities

### New Capabilities

- `wormhole-multicast-sync`: Finite rectangular shared delivery, bounded tree transport, addressed scalar synchronization, causal pipeline composition and scoped validation evidence for TR-05/MT-06/VA-04.

### Modified Capabilities

None. `openspec/specs/` contains no delivered main specification to modify. The umbrella's pending target deltas remain separate; reconcile their overlap with this child before any later synchronization or archival.

## Impact

- **Simulator:** New schemas/compiler/tree/scalar/composition/result/CLI modules under `simulator_detailed/`; narrow opt-in extensions to the shared `packet_transport`/`packet_runtime`, `memory_service`/`memory_resources`/`memory_session`, and compute stage composition boundaries. Existing legacy broadcast/sync metadata remains unsupported in legacy routing. No independent duplicate NoC or L1 server is created for the new mechanisms.
- **JSON and traces:** Add `multicast_sync_workload` v1, `multicast_sync_result` v1 and tree/branch/reservation/scalar/wait events. Add validation adapter `multicast_sync_v1` and scoped normalized delivery/scalar observations. Existing `topology_replay`/result v1/v2, `memory_replay`/result v1, `compute_workload`/result v1, validation v1 inputs, `ArchConfig`, mapping JSON, legacy `Event` and trace-window meanings remain compatible. New validation fields are additive and default empty; old input/result digest fixtures must remain stable.
- **Detector/predictor:** Reject new documents before tensor or checkpoint work. Preserve detailed 7-D runtime features and existing graph/checkpoint contracts; this does not enable a new predictor or claim Wormhole prediction validity.
- **RL/embedding:** Preserve detailed 4-D hardware features, existing observation/model shapes and root four-coordinate actions `[layer, source core, destination core, operation]`. No policy retraining or new Wormhole RL execution.
- **Compatibility and configuration:** Preserve the root 4x4 mesh + Darknet19 + fail-dataset workflow and detailed synthetic mesh/DMA/failure, topology, memory and compute modes. Dimensions, clocks, packet geometry, capacities, rates, scalar storage and effective costs remain explicit configuration; Wormhole-specific restrictions are validated profile contracts. No new runtime dependency, external vendor-tool launcher or hardware capture is required for offline model checks.
- **Delivery boundary:** These are planning artifacts only. No implementation, hardware measurement, functional-reference run, umbrella completion, spec synchronization, archival or push is established by this proposal.
