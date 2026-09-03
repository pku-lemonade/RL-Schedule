# ADA2S-32 Detailed-Simulator Adaptation Roadmap

## Authority and Status

This document is the forward roadmap for `simulator_detailed`. It describes the
implemented Phase 2 boundary and the remaining hardware-modeling phases. It is
not a second hardware specification.

Use these sources in this order:

1. `NOC_ARCHITECTURE.md` is the hardware and benchmark authority.
2. `PHASE2_FIX_PLAN.md` records the fine-grained Phase 2 decisions and fixes.
3. `docs/` describes the current runtime contracts.
4. `configs/instances/ada2s32.json` is the canonical executable snapshot.
5. This file defines future implementation order and acceptance boundaries.

Phase 2 is implemented for calibrated PE-to-PE SINGLECAST transport. GM/DDR
execution, collectives, route reduction, detailed compute/SRAM contention, and
the optional ML predictor adaptation remain separate later phases.

## Confirmed Hardware Baseline

| Area | Current contract |
|---|---|
| Data fabrics | NoC0 and NoC1 are independent 4-column by 8-row meshes |
| Data topology | 32 routers and 104 directional inter-router links per fabric |
| Control planes | `cfg_noc` and `sync_noc` are separate from both data meshes |
| PE channels | CH0 selects NoC0; CH1 selects NoC1 |
| Duplex | Each PE channel has independent TX and RX datapaths |
| Clocks | ACI 1125 MHz, data NoC 2250 MHz, GM 900 MHz, DDR 1200 MHz |
| Native beat | 579 wire bits carry 512 payload bits per NoC cycle |
| Flit | 512 B user payload, serialized in 8 NoC cycles or 4 ACI cycles |
| Routing | Deterministic X-first XY, one VC per port |
| Arbitration | Rotating round-robin with 1/2/4/8-flit burst grants |
| Default burst | `BURST_LEN_DEFAULT` resolves to `BURST_LEN_7`, or 8 flits |
| Flow control | One-flit physical input buffer plus bounded effective credits |
| PE Local SRAM | 4 MiB per PE; Weight SRAM is a separate 16 MiB bank |
| Descriptor behavior | About 57 ACI cycles per post, depth about 24 per channel |
| Hop timing | Measured one-way first-flit slope is 8.5 ACI cycles per hop |
| Bulk PE rate | About 117-120 B/ACI-cycle per fabric and direction |
| Dual-fabric rate | About 234-240 B/ACI-cycle in one direction |
| Dual full duplex | About 468 B/ACI-cycle aggregate PE capability |

The 128 B/ACI-cycle value is an effective payload rate after converting the
2250 MHz native interface to the 1125 MHz ACI timebase. It is not a native
1024-bit phit. Header, CRC, sequence, and tail metadata do not reduce the
confirmed 512 B user payload carried by each flit.

## Implemented Phase 2 Boundary

### Configuration and Types

- `NoCConfig` represents the two clock domains and one 4x8 fabric shape.
- `Arch.nocs` owns two complete `NoC` instances keyed by `NoCChannel`.
- Every PE resolves independently on CH0 and CH1 at local port 0.
- `EndpointAddress` includes fabric, router, and local-port identity.
- `Message` owns resolved source and destination addresses and packetizes with
  `ceil(payload_bytes / 512)`.
- `Flit` is immutable and retains fabric, route, message, and burst metadata.
- Unsupported transfer types and unimplemented DMA execution fail explicitly.

### Fabric Transport

- Links serialize one 512 B flit in four ACI cycles and launch at the calibrated
  `512 / 120` ACI-cycle interval.
- DATA movement and SYNC credit-return traces are separate.
- Routers implement deterministic XY routing and one rotating arbiter per
  output port.
- A switch grant is temporary and ends at TAIL or at the selected burst
  quantum. A message route remains live from HEAD through TAIL.
- Upstream arbitration may interleave multiple messages on one downstream input.
  Route state is therefore keyed by `(input_port, message_id)`, while temporary
  switch ownership remains keyed by input port and records its owning message.
- Bounded credits propagate receiver and link pressure without creating reverse
  DATA traffic.

### PE NMC Endpoint

- Every PE owns one independent runtime `NMCChannel` on each data fabric.
- Each channel has separate TX and RX datapaths, descriptor issuer, descriptor
  capacity, command queues, and endpoint timing.
- Static and dynamic are the only shape modes. Their endpoint setup targets are
  79.5 and 125 ACI cycles.
- `send_with_sync` is an API/workload pattern, not a third timing mode.
- The 101-cycle TX command turnaround follows ideal local flit service. Excess
  NoC backpressure overlaps this fixed bubble before delaying the next command;
  transient contention is not counted twice.
- SEND completes at final local-Link handoff. Command-level RECV completes after
  both endpoint readiness and matching TAIL/SINGLE RX service.

### Workload and Trace Integration

- DFG SEND and RECV nodes select an explicit fabric and local shape mode.
- `Task` resolves the architecture-owned channel and executes through the NMC
  command APIs.
- Fabric timing and endpoint operation timing use distinct typed results.
- Hardware measurements are immutable validation references, not runtime tuning
  profiles.
- Named replays cover rb53 shared-link contention, rb54 sequential ping-pong,
  and rb56/rb58 batched simplex, dual-fabric, and full-duplex workloads.

## Phase 2 Acceptance Matrix

| Behavior | Acceptance |
|---|---|
| Structure | Two independent 4x8 meshes, 64 routers total |
| Packetization | 512 B payload per flit for every flit type |
| Native timing | 8 NoC cycles equals 4 ACI cycles per flit serialization |
| First-flit path | 8.5 ACI cycles per one-way inter-router hop |
| Bulk one fabric | Approximately 117-120 B/ACI-cycle/direction |
| Dual same direction | Approximately 234-240 B/ACI-cycle |
| Dual full duplex | Approximately 468 B/ACI-cycle aggregate |
| 32 KB batches | rb56/rb58 rates within 5 percent |
| 4/8 KB shared link | At least 95 percent of combined isolated offered rate |
| 16 KB shared link | Fair streams and aggregate near rb53's 110.5 B/ACI-cycle |
| Descriptor behavior | Serialized 57-cycle posts and 24-entry channel depth |
| Isolation | Failures, credits, traces, and resources stay fabric-qualified |
| Unsupported paths | Explicit exception rather than unicast fallback |

The rb53 transition must emerge from offered load, descriptor timing,
backpressure, and arbitration. Runtime code must not branch on 4 KB, 8 KB,
16 KB, 32 KB, or any benchmark name.

## Strict-Check Boundary

The Phase 2 strict project covers the production modules modified by the
correction series: architecture, relevant configuration schemas, core/task
integration, endpoint registry, NoC, NMC, tracing, DFG/data types, benchmark
references/replays, CLI loading, and the dual-fabric predictor topology adapter.
The exact file list is checked in as `pyrightconfig.phase2.json`. These files
must pass Pyright strict; those production files and the Phase 2 test module must
also pass Ruff, `compileall`, the
Phase 2 unittest suite, and `git diff --check`.

The optional learned predictor/model pipeline and dynamically constructed test
harness are not part of this production strict project. The Torch/PyG-dependent
loaders, model definitions, embedding encoder, prediction wrapper, and legacy
timing logger still require a dedicated typing and dependency pass in the
predictor phase. This exclusion is explicit; it must not be represented as a
repository-wide strict pass.

## Phase 3: GM and DDR Endpoints (In Progress)

Implement in small, independently tested commits:

1. **Fix 13A, implemented locally:** create configured GM RDMA/WDMA endpoint
   objects, bind their validated local ports to both data fabrics, and give each
   endpoint one internal datapath shared by CH0 and CH1. This is structural
   ownership only; it does not claim executable or calibrated GM commands.
2. Add GM RDMA injection and GM WDMA receive commands on those resources.
3. Add executable DDR RDMA/WDMA endpoint resources and explicit 1200 MHz to ACI
   conversion.
4. Make command service use the shared internal RDMA/WDMA datapath introduced by
   Fix 13A, so CH0 and CH1 do not incorrectly double GM/DDR aggregate bandwidth.
5. Add dual-side and single-side command execution without changing address
   resolution semantics.
6. Calibrate PE-to-GM and PE-to-DDR latency, direction asymmetry, and aggregate
   caps against the corresponding architecture measurements.
6. Add endpoint failures and endpoint-level trace collection.

Do not reuse PE NMC shape targets for GM/DDR. The current canonical config keeps
DMA execution empty until this phase provides complete runtime resources.

## Deferred Phase 4: Collective Transport

1. Implement FIXPATH validation and source-provided route execution.
2. Implement multicast and broadcast tree construction with explicit in-router
   replication state.
3. Implement route-reduction metadata matching and cut-through ALU timing.
4. Implement the required broadcast release and synchronization lifecycle.
5. Calibrate collective latency and bandwidth independently on CH0 and CH1.

The existing `TransType` values and flit metadata are representational support,
not evidence that these operations execute today.

## Deferred Phase 5: Compute and SRAM Contention

1. Replace the generic TPU FLOP approximation with measured Matrix profiles.
2. Add Vector throughput and VMEM behavior in the correct clock domain.
3. Represent Local SRAM and Weight SRAM as separate banks and ports.
4. Add NMC/Matrix/Vector overlap and fair SRAM-port contention only from the
   measured workloads.
5. Validate command ordering, queue depth, overlap, and three-way contention.

Do not add `shrBufPortPriority` to the current NoC until shared-buffer topology,
allocation, and arbitration behavior are sufficiently specified.

## Deferred Phase 6: Predictor and Embedding

1. Establish pinned Torch and PyG dependencies with available type information.
2. Type and test the dataset loader independently of simulation execution.
3. Update node/link features for two 4x8 fabrics and later DMA endpoint nodes.
4. Type the model, predictor wrapper, hardware encoder, and timing logger.
5. Add a separate Pyright strict project for the optional ML stack.

The active `predictor/topology.py` dual-fabric adapter is already part of the
Phase 2 gate; this phase owns the learned model pipeline around it.

## Change Policy

- Change hardware facts in `NOC_ARCHITECTURE.md` first.
- Keep measured references separate from executable configuration.
- Name every latency boundary and clock domain.
- Preserve fabric identity through addresses, flits, failures, and traces.
- Add one behavioral regression for every corrected inconsistency.
- Keep future endpoint or collective execution explicitly unsupported until its
  complete resource, timing, and validation path exists.
