## 1. Versioned memory contracts and admission (MT-D01, D02, D09)

- [x] 1.1 Add strict MemoryReplay configuration in simulator_detailed/configs/schemas/memory_replay.py with tagged input, packet/service/issue parameters, bindings, buffers, operation kinds, and explicit dependency/fence policy; verify round trips and rejection of wrong versions, unknown fields, invalid units, nonfinite values, unsupported modes and inconsistent granularity/alignment.
- [x] 1.2 Add immutable effective-plan and memory result/event contracts in memory_plan.py and memory_records.py, retaining source evidence and stable content identity; verify path-independent normalization, setting-sensitive hashes, absent lifecycle facts and independent byte units without constructing a runtime.
- [x] 1.3 Compile physical resource aliases, enabled worker initiators, selected fabric attachments, address arithmetic, permissions and buffer extents; verify invalid roles, foreign resource bindings, out-of-bounds/MMIO ranges and unsupported DRAM initiation fail before environment construction.
- [x] 1.4 Extend pyrightconfig.phase2.json to cover the new modules and preserve dependency-free consumer rejection/full-profile execution gates; verify focused admission tests and existing profile/topology contracts before exposing any new execution capability.
- [x] 1.5 Run the focused contract tests, strict Pyright, scoped Ruff and git diff --check; record the current/expected behavior, validation commands and remaining unsupported runtime in delivery notes, then commit this part before part 2.

## 2. Wire packetization and route compilation (MT-D02, D03, D10)

- [x] 2.1 Add pure packet layout/segmentation code in `memory_packets.py` with stable operation/segment/purpose identities, header-only read requests/acks, normal write/read-data packets and exact partial data flits; verify independent boundary tables for 1, 15, 16, 31, 32, 33, 8191, 8192, 8193 bytes and a second generic width/alignment configuration.
- [x] 2.2 Reuse/extract pure torus binding/routing helpers to compile each request/response route without fabricating v2 traffic or granting DRAM initiation; verify both fabrics, wraps, same-router routes, canonical aliases and unchanged existing route oracles.
- [x] 2.3 Add immutable memory wire envelope/layout validation with zero-useful-byte headers and plan-bound channel/lane paths while retaining v2 positive-payload envelopes; verify malformed layout, identity collisions, wrong class/path and cross-plan inputs fail before mutation.
- [x] 2.4 Add source-pinned packet/address fixtures and separate logical/header/padding/packet/channel expected accounting; verify the 8,193-byte reference totals (8,288 posted, 8,352 read/acknowledged) independently of production helpers and preserve legacy packet serialization.
- [x] 2.5 Run packet/admission/route tests, applicable strict type/lint and `git diff --check`; review explicit software segmentation and unsupported NIU modes, then commit this part with evidence before part 3.

## 3. Reusable bounded transport with real headers (MT-D03, D08, D09)

- [x] 3.1 Extract a typed internal packet/service contract from `virtual_channel.py` and `torus_transport.py`, provisionally into `packet_transport.py`, keeping old public wrappers/results unchanged; verify existing v2 routes, bytes, times, event ordering and v1 compatibility fixtures before adding memory scheduling.
- [x] 3.2 Implement admitted finite packet readiness/delivery hooks with bounded per-class producer/consumer staging in one shared environment; verify header-only and long streamed packets actually consume credits, router work and physical serialization, with no per-transaction network or uncharged full-packet storage.
- [x] 3.3 Connect memory wire envelope validation and real header/data layouts to the shared kernel; verify first/last-flit rules, unequal request/response packet sizes, zero useful payload headers, actual per-channel byte counts, and rejection before resource mutation.
- [x] 3.4 Stress runtime-triggered responses with one-slot staging, both wraps/fabrics and directed slowdown; verify fairness, exactly-once packet delivery, conservation, timeout diagnostics and complete delayed-credit drain while keeping memory service itself gated.
- [x] 3.5 Run focused kernel/transport tests, legacy network/DMA regressions, strict type/lint and `git diff --check`; audit retained flits/grants and commit this extraction/integration part before memory service work.

## 4. Shared capacity, readiness metadata and memory service (MT-D04, D05)

- [x] 4.1 Add one canonical resource owner and handle ledger per L1/DRAM resource in `memory_resources.py`; verify aliases share capacity, distinct reservations cannot overlap, foreign/double release fails, finite replay allocation is charged once and teardown restores the budget.
- [x] 4.2 Add explicit initialized/producer range metadata and access leases without tensor values; verify allocation does not imply readiness, reads require the selected producer version, conflicting accesses cannot proceed early, and disjoint/concurrent-read accesses remain eligible.
- [x] 4.3 Implement `aggregate_shared_rw_v1` with a bounded FIFO waiting queue, one active slot, configurable granularity/chunk size/native latency/bandwidth/clock and service events; verify two independent analytical timelines, positive sub-quantum work and exact rounded read/write service bytes.
- [x] 4.4 Verify generic clients through different aliases/fabrics share combined read/write service while independent resources overlap; test finite admission under saturation, queue/active bounds and that no memory grant waits on a network resource.
- [x] 4.5 Run resource/ownership/service tests, strict type/lint and `git diff --check`; document aggregate-policy and narrow-write approximations, then commit this part before transaction integration.

## 5. Bounded addressed read and write execution (MT-D03..06, D08)

- [x] 5.1 Add `memory_runtime.py` composition with shared initiator segment issue slots, responder state and counted request/response TX/RX staging; initially gate execution to initialized-source, disjoint-access cases and reject dependency/local/fence modes until part 6; verify source readiness precedes issue, saturation cannot hide resident data, and each descriptor has exactly one retirement path.
- [x] 5.2 Implement posted and acknowledged writes with source-read service, streamed real request packets, destination-write service and causal header-only acks; verify posted local completion can precede visibility, acknowledgements follow target service, and large segmented writes account for every effect once.
- [x] 5.3 Implement reads with real header requests, target-read service, bounded streamed responses and initiator-local write service; verify response arrival cannot prematurely complete a read, aliases use shared resources, and segment aggregation permits different arrival orders.
- [x] 5.4 Add operation/segment lifecycle traces and resource-complete termination; stress mixed read/write traffic through both fabrics with capacity-one endpoints and slow memory, verifying wait-resource ordering, no grant-to-network cycle, exactly-once responses and pending diagnostics on timeout/idle-with-pending.
- [x] 5.5 Run transaction/transport/resource tests, strict type/lint and `git diff --check`; audit actual waits against design decision 6 and commit the bounded runtime with explicit remaining ordering/local-client scope before part 6.

## 6. Explicit dependencies, local clients and scoped fences (MT-D04..08)

- [ ] 6.1 Compile operation dependencies and physical-range conflict/version checks across aliases; verify cyclic/foreign/insufficient dependencies and unordered overlapping writes fail before execution, while independent operations are not globally serialized.
- [ ] 6.2 Add local L1 read/write clients and producer destination-ready dependencies using the same memory service owner; verify local consumption waits for remote writes and both fabrics plus local clients contend without generating fictitious local network traffic or claiming DFG integration.
- [ ] 6.3 Implement frozen per-initiator/fabric operation sets for local-handoff and remote-completion fences; verify legal cross-fabric ack/read ordering, posted writes rejected from remote fences, later issue gated only by explicit dependencies, and no zero-cost remote notification.
- [ ] 6.4 Test producer overwrite hazards, partial-range readiness, source lease release and all-segment completion with backpressure; verify no buffer is freed early and the successful replay snapshot/teardown distinguishes readiness, persistent capacity and pending service.
- [ ] 6.5 Run focused ordering/local-client and mixed-traffic liveness tests, strict type/lint and `git diff --check`; review the explicit ordering subset and commit this part before adapter work.

## 7. Legacy DMA and scratchpad adapters (MT-D04, D09)

- [ ] 7.1 Add an explicit legacy DMA lifecycle wrapper in `memory_adapters.py` that delegates existing endpoint/channel/coordinator work; verify `legacy_dma` labeling, unchanged result semantics, absent remote-memory visibility and no extra descriptors, packetization or service charges.
- [ ] 7.2 Compare direct and adapted GM/DDR reads/writes in single-side/dual-side modes across fabrics, descriptor-sharing policies, finite outstanding limits, custom packet sizes and fault recovery; verify exact timing/service traces, rendezvous, drain and existing FIXPATH/local-memory DMA rejection.
- [ ] 7.3 Add an exclusive capacity adapter for an empty, idle `core.ScratchpadMemory` in the same environment; verify its original container/delay is used exactly once, duplicate binding/release and unmanaged concurrent operations are rejected, and detach requires drained ownership.
- [ ] 7.4 Verify unbound scratchpad calls and legacy detailed examples remain compatible, and memory replay/result inputs are rejected before detailed detector/encoder tensor work; record unchanged 7-D/4-D/checkpoint/RL assumptions and any unavailable Torch/PyG checks accurately.
- [ ] 7.5 Run adapter/DMA/scratchpad and affected compatibility regressions, strict type/lint and `git diff --check`; review the boundary that legacy transport and DFG have not migrated, then commit this part before CLI delivery.

## 8. CLI, examples, evidence and compute-child handoff (MT-D01..10)

- [ ] 8.1 Add `simulator_detailed/replay_memory.py` and generic/Wormhole examples under `configs/memory_replays/`; verify `.venv/bin/python -m simulator_detailed.replay_memory --replay <each-example>` produces parseable deterministic JSON, matching optional output, exit codes 0/1/2, and preserves existing output on invalid input.
- [ ] 8.2 Complete `MemoryReplayResult` accounting/trace export and pending resource reports; independently reconcile logical/header/padding/packet/channel/service bytes, operation events, source identities and native-to-ACI times against executed events, including an incomplete example.
- [ ] 8.3 Update `hardware_profile.py` support scopes and detailed memory documentation with executable commands, pinned sources, assumptions and unsupported modes; verify the general profile workload gate remains closed and example successes do not imply bank/NIU fidelity, compute/DFG execution or hardware calibration.
- [ ] 8.4 Run `.venv/bin/python -m unittest discover -s simulator_detailed/tests`, `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`, scoped `.venv/bin/ruff check`, generic/Wormhole memory CLI examples, prior topology CLI examples, `openspec validate wormhole-memory-transactions --strict --no-interactive` and `git diff --check`; verify prior v1 cycle-44 graph/plan hashes, v2 packet/drain fixtures and the legacy custom-mesh timing/failure fixture remain stable.
- [ ] 8.5 Record MT-D01..10 and umbrella MT-01..05/memory VA-04 requirement-to-test coverage, actual commands/results, source/code identities, representative trace counts, wait-resource audit and fidelity gaps in delivery notes; retain MT-06/compute/calibration as pending, reconcile the child handoff without syncing/archiving the umbrella, then commit the completed child before exploring `wormhole-compute-dataflow`.
