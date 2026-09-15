## Context

See `proposal.md` for motivation and impact. This is an umbrella design for future child changes, not an implementation report. The baseline inspected during exploration is commit `ef09b0734894bc4a6168cc93c1d537d36e38ee3e` (`Make simulator hardware parameters configurable`).

The requested `NOC_ARCHITECTURE` context was not found as an available file in the inspected checkout. Current behavior below is grounded in executable code and `simulator_detailed/docs`, rather than inferred from an unavailable architecture document. Repository-wide OpenSpec context also describes older top-level simulator workflows; those descriptions are not authoritative for the detailed simulator.

| Area | Current executable behavior | Gap and affected consumers |
| --- | --- | --- |
| `simulator_detailed/architecture.py`, `endpoint_registry.py` | Builds `x*y` cores; PE identity follows router identity; DMA attachments are configurable | Router presence, tile role, compute availability, and memory aliases must become separate concepts |
| `simulator_detailed/noc.py` | Multiple independently configured mesh fabrics; XY routing; one VC; round-robin arbitration | No torus wraps, fabric-specific dimension order, or executable multicast; adding wraps to the current flow-control assumptions risks deadlock |
| Link/router timing | Flit serialization, configurable launch/propagation/credit timing, finite resources, stalls, slowdown/recovery, packet route reservation | Preserve useful mechanisms; audit their composition before assigning documented hardware timing anchors |
| Packet representation | Physical and payload flit sizes are configurable; header metadata does not consume a separate header flit | Setting flit width to 32 bytes alone does not implement Wormhole packet overhead |
| `dma_endpoint.py` | Descriptor issue, slots, datapath contention, request/response and single/dual command behavior | Existing GM/DDR DMA semantics are not Wormhole NIU semantics; adapt reusable service/transport below a new transaction contract |
| Memory-controller configuration | Controller instances, aggregate bandwidth, and atomic fields exist in the schema | These fields are not connected to an executable shared memory-controller service |
| `core.py` | Scratchpad capacity allocation/release already executes; TPU/LSU service is abstract | Reuse capacity tracking; add shared service, ownership/readiness, operation costs, and pipeline dependencies |
| `utils/task.py` | LOAD/STORE largely allocate local capacity and occupy LSU; FC branch is `pass` | Remote accesses do not currently traverse the full memory path; FC and sub-quantum compute costs need explicit behavior |
| `simulator_detailed/predictor/topology.py`, `embedding/hw_encoder.py` within the same subtree | Predictor rebuilds mesh topology; encoder uses router/link/fabric/coordinate features | Stable graph identity and compatibility gates are required; old checkpoints do not gain Wormhole validity automatically |

Singlecast is executable. Fixed-path metadata, other message modes, and memory/atomic configuration must not be counted as runtime capabilities merely because a type or field exists. DATA/SYNC/CFG labels do not establish three separately simulated physical networks; credit-return timing is distinct from a general synchronization transaction service.

## Goals / Non-Goals

**Goals:**

- Model the resource and dependency effects relevant to placement, mapping, and scheduling on one Wormhole B0 ASIC.
- Keep the model reusable through generic graph, transaction, resource, and pipeline mechanisms selected by policies and profiles.
- Preserve synthetic experiments and expose approximation boundaries in configuration, traces, and validation reports.
- Deliver useful, independently testable increments. Profile support, network execution, memory execution, and workload execution become available at different milestones.

**Non-Goals:**

- Full RISC-V/Tensix ISA, firmware, MMIO register emulation, or numerical tensor execution.
- Multiple ASICs, Ethernet fabric protocols, PCIe/host dispatch timing, and a complete n300 board. Noncompute tile positions can be represented without implementing their external protocols.
- Detailed DRAM commands/refresh, exact silicon arbitration and bank circuitry, DVFS/thermal/power behavior, or guaranteed cycle accuracy.
- New detector architecture, policy training, checkpoint conversion, or numerical collectives. Downstream compatibility checks and topology export are included; wider ML adaptation needs its own scope.

## Decisions

### 1. Generic mechanisms, selected policies, and profiles

Use the following conceptual boundaries; class names and final JSON field names belong to each child's explore phase.

```text
Workload / placement / scheduling inputs
                 |
      Abstract pipeline and buffers
                 |
      Transaction issue and completion
          /                   \
 Directed fabric transport   Shared memory service
          \                   /
    Canonical identities and resource ownership
                 |
       Effective hardware profile

Each layer -> structured events -> validation / supported consumers
```

The profile selects layout, supported policies, rates, capacities, clocks, and operation costs. Generic code owns topology construction, queueing, resource service, transactions, and token dependencies. Device-specific values live in the profile; architecture-specific algorithms live behind selected policies where needed. Alternatives considered were a separate Wormhole simulator, which duplicates existing transport and tracing, and a large constructor full of device checks, which makes shared behavior hard to verify.

The first child introduces a configuration contract and support declaration, not an executable Wormhole machine. Until the required runtime policies exist, profile inspection succeeds while execution reports the missing feature. Old synthetic profiles keep their selected semantics and are not silently reinterpreted as hardware profiles.

### 2. Separate physical, logical, fabric, and resource identities

Use stable identities for ASIC, router, directed link, fabric, endpoint, memory resource, and region/buffer. Keep logical worker numbering as an explicit mapping over enabled compute coordinates. Fabric-specific coordinates translate through canonical physical identity. Several endpoint attachments can address one physical memory resource, so alias count never multiplies capacity or aggregate service.

Harvesting disables compute eligibility independently of network connectivity. Product specifications establish counts, while a device descriptor or explicit enabled mask establishes coordinates. If no measured-device mask is available, require an explicitly labeled assumed layout for synthetic experiments. A flat `x*y` compute index is insufficient because it merges routing and scheduling identities.

The graph export must be used by supported predictors and trace consumers. Extend downstream features only with an explicit contract; reject incompatible model metadata. A mesh-shaped observation tensor or a loadable checkpoint is not evidence of valid Wormhole predictions.

### 3. Keep transport detail that changes contention

Retain finite queues, serialization, credits/backpressure, arbitration costs, packet boundaries, and directed-link failures. Add routing as a policy over a canonical graph, then implement the two Wormhole fabric policies and coordinate translations.

Torus execution requires a bounded, justified deadlock-prevention design. The dual-NoC child must select and document a dateline/virtual-resource policy, request/response separation, and endpoint drain assumptions, and justify the supported dependency graph. It can abstract exact silicon VC arbitration, but it cannot simply add wrap links to the current one-VC mesh. Multicast introduces new dependencies and requires its own extension of that justification in child 7.

Keep logical byte counts separate from transmitted flits. Header traffic, payload rounding, and packet segmentation are applied exactly once. Router/link/NIU timing anchors must be decomposed into the existing stages without charging one documented hop latency multiple times. Synthetic packet accounting remains available under its legacy policy.

### 4. Compose transactions with shared memory service

Separate issue policy, packet transport, memory service, and observable completion. The initial executable subset includes unicast reads, posted and acknowledged writes, and explicitly defined completion/fence behavior. Address abstraction needs region identity, offsets, extents, and readiness; it does not need tensor payload storage. Unsupported byte-mask, linked-transaction, priority, and ordering modes fail early until explicitly modeled.

Requests reach a backing resource through an attachment; responses traverse an actual configured path. Read/write service shares the appropriate DRAM capacity and bandwidth. L1 contention accounts for both fabrics and local pipeline clients under a declared aggregate or port-based abstraction. Bank-level detail is deferred unless evidence shows the aggregate abstraction misses the intended scheduling questions.

Reuse scratchpad capacity allocation with one owner for reservations and releases. Reuse DMA slots, datapaths, and transport where compatible, with a legacy adapter and a distinct Wormhole initiator policy. Turning every DRAM endpoint into an autonomous DMA engine would create incorrect issue behavior and duplicate service limits.

### 5. Abstract compute with explicit dataflow

Use configurable operation/shape/dtype costs and bounded pipeline buffers. Model reader, compute, and writer scheduling with reservation, publication, consumption, and release events. This permits overlap when dependencies and resource ownership permit it, and produces backpressure when buffers fill.

The compute child routes remote LOAD/STORE through the transaction layer, keeps local accesses local, implements FC/matmul costs, and audits integer truncation for positive work. It records the distinction between hardware compute tiles and tensor tiles. A second fixed LSU delay must not duplicate service already charged by a transaction. Detailed unpack/math/pack instruction execution is deferred; effective pipeline stages and shared resource occupancy provide the intended first model.

### 6. Treat evidence as a layered contract

Architecture sources define structure and observable protocol constraints. Analytical expectations check the chosen abstraction. A functional reference can check normalized destinations, bytes, scalar state, and dependencies where available. Measured device data is required to evaluate silicon timing error. These claims remain separate in every report.

Reference candidates reviewed during exploration are listed below. These are discovery links, mostly mutable `main`/`latest` pages, **not pinned fixtures or a downloaded measurement dataset**. Child 1 must record immutable revisions or snapshot hashes for the subset it uses; subsequent children do the same for new evidence.

| ID | Public primary source | Use and limitation |
| --- | --- | --- |
| S1 | [Wormhole B0 architecture](https://github.com/tenstorrent/tt-isa-documentation/tree/main/WormholeB0) | Physical tile roles/layout; does not establish one board's harvested worker coordinates |
| S2 | [Wormhole PCIe products](https://docs.tenstorrent.com/aibs/wormhole/index.html) | n150 has one ASIC and 72 enabled cores; n300 has two ASICs with 64 each. Per-ASIC memory/bandwidth must not be replaced by board totals |
| S3 | [NoC architecture](https://github.com/tenstorrent/tt-isa-documentation/blob/main/WormholeB0/NoC/README.md) | Opposite torus fabrics, 32-byte flits, one header plus up to 256 data flits, response-only DRAM attachments, and timing anchors; no workload-wide timing guarantee |
| S4 | [NoC memory map](https://github.com/tenstorrent/tt-isa-documentation/blob/main/WormholeB0/NoC/MemoryMap.md) | Physical/fabric coordinates, 10x12 extent, NoC0 X-first and NoC1 Y-first routing; product masks still need separate evidence |
| S5 | [NoC ordering](https://github.com/tenstorrent/tt-isa-documentation/blob/main/WormholeB0/NoC/Ordering.md) | Observable ordering restrictions; initial model supports an explicitly selected subset |
| S6 | [DRAM tile](https://github.com/tenstorrent/tt-isa-documentation/blob/main/WormholeB0/DRAMTile/README.md) | Aliasing, controller channels, shared read/write service, clock boundaries; peak throughput is not sustained workload throughput |
| S7 | [Tensix L1](https://github.com/tenstorrent/tt-isa-documentation/blob/main/WormholeB0/TensixTile/L1.md) | Local memory clients and port contention; one interface's rate is not a universal aggregate L1 rate |
| S8 | [Memory for kernel developers](https://docs.tenstorrent.com/tt-metal/latest/tt-metalium/tt_metal/advanced_topics/memory_for_kernel_developers.html) | Local/remote memory programming and tensor tiling; software assumptions need versioning |
| S9 | [ttsim](https://github.com/tenstorrent/ttsim) | Functional/ISA reference through compatible TT-Metal and SoC descriptors; requires a suitable Linux environment and is not assumed to provide silicon timing truth |
| S10 | [Tenstorrent performance tools](https://github.com/tenstorrent/tt-lang/blob/main/docs/sphinx/reference/performance-tools.md) | Profiler/NoC trace collection methods; tooling availability does not supply a matched Wormhole measurement dataset |

S3's approximate five-cycle NIU attachment and nine-cycle router-hop figures are candidate profile anchors, conditional on the stated clock and uncongested interpretation. One 32-byte flit per cycle corresponds to 32 GB/s at 1 GHz; changing clock changes the derived rate. These anchors need stage-boundary checks before use.

Validation paths:

| Evidence tier | Independent expectations / cases | What passing establishes |
| --- | --- | --- |
| Architecture/protocol | Pinned layout tables, independent route enumeration, packet sizes at boundary values, alias maps, declared ordering cases | Agreement with the selected public contract |
| Model invariants | Byte/credit/token conservation, capacity bounds, analytical empty-network service, finite-traffic drain, shared bandwidth | Correct execution of the chosen model, not silicon accuracy |
| Functional reference | Matched abstract operations and normalized observable results from a pinned ttsim/TT-Metal setup or reproducible reference fixtures | Functional agreement for the supported observation subset; no claim that our simulator executes those kernels or reproduces tensor values |
| Silicon timing | Matched single-flow latency/throughput, contention, memory and pipeline measurements, then held-out workloads | Error for those tested cases and conditions only |

Use unicast local/remote transfers, hop sweeps, boundary-size sweeps, contention hot spots, dual-fabric traffic, alias concurrency, and buffer-depth sweeps before representative matmul/streaming workloads. Child 7 adds rectangular multicast and scalar synchronization examples within its supported subset. Tests need independently constructed expected outcomes; serializing the simulator's own route into a fixture is not an independent route oracle.

Timing reports define metric, measurement window, warm-up/repetitions, sample aggregation, fitted parameters, and tolerances before held-out evaluation. Host dispatch, PCIe transfer, and profiler overhead cannot be silently included in a device-only comparison. No universal accuracy percentage is promised now. Missing hardware/reference access can leave optional evidence tiers unavailable while documented conformance and internal correctness still pass; required child behavior and regression gates cannot be waived that way.

### 7. Sequential child changes own implementation and evidence

The five specs contain 27 named target requirements. Prefixes identify hardware profile (HP), topology/routing (TR), memory transactions (MT), compute/dataflow (CD), and validation (VA). Child names below are reserved roadmap names, not existing changes.

| Step / child change | Requirement ownership and affected area | Exit evidence and capability gained |
| --- | --- | --- |
| 1. `wormhole-hardware-profile` | HP-01..04; initial VA-01..03, VA-06..07. Detailed schema, profile docs/examples, baseline environment | Valid/invalid profile fixtures, provenance and mask checks, legacy baseline checks. Profile can be inspected; runtime support is gated |
| 2. `generic-heterogeneous-topology` | TR-01; runtime HP-03/04. Detailed architecture, endpoint registry, graph export and consumer guards | Synthetic mesh equivalence, directed/heterogeneous graph identity and attachment tests. Generic topology executes |
| 3. `wormhole-dual-noc-routing` | TR-02..04; VA-04 network subset. Detailed NoC policies, clocks/flow control, topology consumers | Independent XY/YX/wrap oracle, justified resource dependencies, stress drain, throughput and directed failure tests. Wormhole unicast transport executes |
| 4. `wormhole-memory-transactions` | MT-01..05; VA-04 memory subset. Transaction layer, DMA adapter, L1/DRAM service, packetization | Request/response/visibility tests, packet accounting, shared-alias and read/write limits, legacy DMA regression. Network-backed memory operations execute |
| 5. `wormhole-compute-dataflow` | CD-01..05; VA-04 pipeline subset. Detailed core/task/DFG paths, buffers, consumer guards | Positive FC/matmul costs, actual remote traffic, finite-buffer liveness, shared-resource overlap, workload compatibility. Abstract single-ASIC workloads execute |
| 6. `wormhole-validation-harness` | Consolidate VA-01..07; VA-05 calibration/evaluation mechanics. Fixtures, benchmark runner, trace/report tooling | Reproduce earlier checks, reference import and metadata validation, missing-evidence reporting, calibration/held-out separation. Publish coverage without overstating hardware evidence |
| 7. `wormhole-multicast-sync` | TR-05, MT-06; extend VA-04 and workload evidence. NoC replication, scalar control state, pipeline sync | Delivery/replication/ordering/resource/liveness tests and representative workloads. Supported multicast and scalar synchronization execute |

Each step follows this cycle:

```text
Previous child implementation + evidence + unresolved limits
                         |
                 Explore next child
                         |
          Concrete proposal / design / specs / tasks
                         |
                    Apply that child
                         |
      Tests + targeted type/lint + architecture review
                         |
       Record evidence and reconcile umbrella coverage
                         |
                 Explore following child
```

Before code changes, each child explains current behavior, expected behavior, affected classes/contracts, failure modes, and validation strategy. A child can split into smaller changes if exploration reveals excessive coupling; update this roadmap explicitly while preserving requirement ownership and the gates. Do not pre-create and apply all seven designs based only on this umbrella.

All children add validation as they implement mechanisms. Step 6 consolidates reusable runners, comparison/import tooling, and reports; it is not the first time earlier behavior gets tested. Commit each completed, validated part before continuing to the next part, as requested by the user. Push only when separately requested.

### 8. Keep pending target specs separate from delivered baseline

Parent/child association here is a documented workflow, not an assumed OpenSpec parent-child feature. Child proposals name this umbrella and their requirement IDs. Each child has independently implementable tasks; the parent's tasks are milestones and must not be used as a monolithic code-edit queue.

Children deliver the same canonical capability paths where appropriate, with only their supported requirement subset. The first delivery adds requirements to main specs; later deliveries modify or add against that actual baseline. Before syncing/archiving a child, reconcile its coverage with the umbrella and record evidence in the parent's tasks/design. Before final umbrella archival, reconcile superseded parent deltas against delivered main specs so no duplicate ADDED requirement is replayed. Do not sync pending parent targets into main specs as if implementation were complete. This review is required even when the CLI reports all planning artifacts as done.

## Risks / Trade-offs

- [Generic abstraction misses important silicon contention] -> Keep alias/resource identity explicit, compare contention experiments, refine only when evidence justifies the detail.
- [Physical cycles are double-counted or mixed with ACI time units] -> Record clock domains and stage boundaries; test derived timing with more than one clock and packet size.
- [Torus or multicast introduces dependency cycles] -> Require a documented prevention argument plus bounded drain tests; watchdogs diagnose failures but do not establish safety.
- [Existing useful synthetic behavior regresses] -> Preserve opt-in policies and legacy adapters; compare baseline topology, timing semantics, DMA and failure traces.
- [Schema fields are mistaken for runtime support] -> Gate execution through an explicit capability report and test unavailable modes.
- [Public data cannot support the desired timing claim] -> Publish conformance separately and leave timing unvalidated until matched measured evidence is available.
- [Old detector/RL models silently consume different graphs] -> Validate graph/features/checkpoint contracts and retain legacy workflows; explicitly scope later model adaptation.
- [The roadmap turns into a large rewrite] -> Finalize only the next child, retain small reviewable tasks, and record stage exit evidence before proceeding.

## Migration Plan

1. Preserve baseline examples and record the exact execution environment in child 1. The explored default Python lacks `simpy`, and the attempted pyright invocation lacked the tool; those attempts did not establish test or type-check passes. Resolve the intended environment before making behavioral claims.
2. Use additive, explicitly selected profiles and policies. New topology/workload/trace JSON needs a child-specific version/default/migration decision; old accepted inputs must remain supported or receive an explicit migration in that child's proposal.
3. Execute relevant detailed tests with `python -m unittest discover -s simulator_detailed/tests` and type checks with `python -m pyright --project simulator_detailed/pyrightconfig.phase2.json`. Inspect the pyright include list in each child and cover newly changed modules explicitly. Run targeted lint checks (for example, `python -m ruff check <changed-python-files>`) using the project's chosen tooling; establish that tooling in the first implementation child if absent.
4. Add focused checks for affected downstream consumers. Preserve a separately identified legacy 4x4/Darknet19/failure smoke path when shared contracts are touched; do not assert that detailed-simulator tests cover the legacy pipeline.
5. Review effective configuration, trace identities, supported/unsupported modes, and requirement evidence before each milestone closes. Rollback for users is selecting the prior supported profile/policy; any persisted format migration needs a reversible plan in its owning child. No unsolicited backups are required.
6. Complete the umbrella only after all children and requirement evidence are reconciled. Hardware-calibrated accuracy remains a separate, evidence-dependent claim even then.

## Open Questions

- Child 1: Which pinned descriptor and explicit enabled mask will be used for the initial n150 example, and which source revision becomes the reference baseline?
- Child 3: Which bounded virtual-resource decomposition best preserves the documented routing/dependency constraints within the current router implementation?
- Child 4: Which aggregate/port service parameters best fit the selected L1 and DRAM abstraction, and which ordering subset can be validated without register-level emulation?
- Child 5: Which first dtype/shape cost table and DFG adapter provide useful matmul/streaming coverage with available evidence?
- Child 6: Which compatible Linux ttsim environment and measured Wormhole datasets are available? Their absence limits external evidence, not the obligation to test implemented behavior.
- Child 7: Which documented rectangular multicast and scalar atomic/semaphore subset is the smallest sufficient set for the representative workloads?

These are choices within the committed scope. Any answer requiring multi-chip execution, ISA fidelity, an incompatible public contract, or unsupported accuracy claims must trigger an explicit roadmap revision.
