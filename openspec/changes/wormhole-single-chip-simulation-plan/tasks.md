## 1. Hardware profile and baseline — `wormhole-hardware-profile`

These are umbrella milestones, completed through independently scoped child tasks. Do not apply this file as one implementation change. Each child must define module-scoped tasks small enough for one session, with its own verification commands. All boxes began unchecked because planning artifact completion does not implement these milestones. The 2026-09-20 [evidence audit](verification.md) now verifies 37/38 milestones against all seven delivered children; only main-spec reconciliation (8.3) remains open. Child exit reconciliation means reviewed delta contracts and delivery evidence, not historical main-spec synchronization. Exact requirement/scenario/code/test references are in [evidence-map.json](evidence-map.json).

- [x] 1.1 Explore the profile child against the current detailed schema and supported examples; deliver a current/expected behavior comparison, affected-class and compatibility inventory, supported-versus-represented feature list, and decisions for HP-01..04 and initial VA-01..03/06/07.
- [x] 1.2 Create that child's proposal/design/specs/tasks, including pinned source candidates, explicit enabled-mask policy, units/clock conventions, JSON example changes, and runtime capability gates; verify with `openspec validate wormhole-hardware-profile --strict --no-interactive` before apply.
- [x] 1.3 Establish the intended Python/test/type/lint environment as a scoped child task and record baseline commands/results; verify dependency imports and distinguish environment failures from behavioral failures in the child evidence report.
- [x] 1.4 Apply the child's profile/schema/provenance tasks and documented examples; verify valid/invalid masks, per-ASIC resource identity, parameter overrides, legacy parsing, and rejection of unavailable runtime policies through executable fixtures.
- [x] 1.5 Run relevant detailed tests, targeted type/lint checks, and baseline compatibility checks; review HP-01..04 coverage and explicitly retain runtime portions pending later children, then reconcile delivered specs and record the child evidence before exploring step 2.

## 2. Heterogeneous graph — `generic-heterogeneous-topology`

- [x] 2.1 Explore topology/endpoint construction and downstream graph assumptions using step 1 evidence; deliver canonical identity, attachment, disabled-compute/transit, and consumer compatibility decisions for TR-01 and runtime HP-03/04.
- [x] 2.2 Create and validate this child's artifacts with `openspec validate generic-heterogeneous-topology --strict --no-interactive`; verify tasks name changes to detailed architecture, endpoint registry, topology export, example JSON, and affected predictor/encoder assumptions.
- [x] 2.3 Apply graph construction and endpoint separation through the child's small tasks; verify an executable heterogeneous directed graph, stable resource aliases, transit through disabled compute positions, and synthetic mesh topology equivalence.
- [x] 2.4 Apply supported graph-consumer integration or explicit incompatibility guards; verify exported link/endpoint identity matches execution and incompatible checkpoint/feature contracts fail clearly without changing legacy observation/action shapes silently.
- [x] 2.5 Run relevant tests and targeted type/lint checks, inspect representative graph/trace output, and review regression results; reconcile TR-01 and runtime HP coverage with delivered specs before exploring step 3.

## 3. Wormhole unicast transport — `wormhole-dual-noc-routing`

- [x] 3.1 Explore NoC0/NoC1 coordinate transforms, routing, finite resources, timing stage boundaries, and failure identity; deliver an architecture-linked route oracle and deadlock-prevention argument for TR-02..04 before implementation.
- [x] 3.2 Create and validate the child's artifacts with `openspec validate wormhole-dual-noc-routing --strict --no-interactive`; verify the supported traffic classes, bounded virtual resources, endpoint drain assumptions, clock conversions, and trace changes are specified.
- [x] 3.3 Apply per-fabric torus/routing and topology-consumer tasks; verify both dimension orders, wrap boundaries, same-router delivery, physical destination identity, and route agreement with independently derived cases.
- [x] 3.4 Apply bounded flow-control/timing tasks while preserving selected synthetic policies; verify finite unicast traffic drains under contention, credits and capacity are conserved, and link slowdown affects only the selected directed fabric edge. Use transport-level response fixtures until full memory service arrives in step 4.
- [x] 3.5 Run relevant network/DMA regressions and targeted type/lint checks, report analytical latency/throughput checks and abstraction limits, and reconcile TR-02..04 plus network VA-04 evidence before exploring step 4.

## 4. Memory transactions and service — `wormhole-memory-transactions`

- [x] 4.1 Explore existing DMA issue/completion behavior, scratchpad ownership, packet representation, and dormant controller fields; deliver transaction/address/ordering and L1/DRAM service decisions for MT-01..05, including effects on NoC, endpoint, and task consumers.
- [x] 4.2 Create and validate the child's artifacts with `openspec validate wormhole-memory-transactions --strict --no-interactive`; verify request/response, posted/acknowledged write, fence, alias, packetization, trace identity, and unsupported-mode contracts are concrete.
- [x] 4.3 Apply transaction and real packet-cost tasks with explicit DMA adapters; verify payload boundary sizes, header accounting, response paths, visibility/completion distinctions, and preservation of supported legacy DMA issue slots/datapaths/commands.
- [x] 4.4 Apply shared L1/DRAM resource tasks; verify aliases share capacity and combined read/write service, independent resources overlap, both fabrics contend where appropriate, and scratchpad reservations/releases are not double-counted.
- [x] 4.5 Run relevant memory/NoC/DMA tests and targeted type/lint checks, inspect transaction/resource traces, and reconcile MT-01..05 plus memory VA-04 evidence before exploring step 5.

## 5. Abstract compute and pipelines — `wormhole-compute-dataflow`

- [x] 5.1 Explore detailed core/task/mapper behavior using step 4 evidence; deliver CD-01..05 decisions for operation/shape/dtype costs, FC/matmul, local versus remote accesses, finite-buffer ownership, and DFG compatibility.
- [x] 5.2 Create and validate the child's artifacts with `openspec validate wormhole-compute-dataflow --strict --no-interactive`; verify small tasks cover compute costs, transaction-backed LOAD/STORE, buffer lifecycle, pipeline scheduling, and representative workload JSON changes.
- [x] 5.3 Apply compute-cost and task/DFG adapter tasks; verify positive sub-quantum work has nonzero service, supported FC/matmul executes, remote loads/stores produce traffic and readiness events, and unsupported operations fail clearly.
- [x] 5.4 Apply bounded-buffer and reader/compute/writer scheduling tasks; verify capacity/token conservation, producer backpressure, legal overlap, shared resource contention, no duplicate transfer delay, and finite workload drain.
- [x] 5.5 Run an executable single-ASIC matmul/streaming smoke workload plus relevant regression and targeted type/lint checks; record stage timelines and downstream contract compatibility, then reconcile CD-01..05 plus pipeline VA-04 evidence before exploring step 6.

## 6. Evidence and comparison tooling — `wormhole-validation-harness`

- [x] 6.1 Explore existing child fixtures/reports and available public/reference/hardware evidence; deliver a coverage/gap matrix for VA-01..07 and choose reproducible metadata, benchmark, import, calibration, and evaluation formats without inventing missing measurements.
- [x] 6.2 Create and validate the child's artifacts with `openspec validate wormhole-validation-harness --strict --no-interactive`; verify that architecture/model checks, functional observations, and silicon timing comparisons have separate acceptance criteria and unavailable-evidence states.
- [x] 6.3 Apply reusable benchmark, trace-normalization, fixture/import, and report tasks; verify earlier child cases run reproducibly and public/reference data with incompatible units, revisions, layout, or measurement windows cannot produce an unsupported timing claim.
- [x] 6.4 Apply calibration/evaluation mechanics and relevant comparison adapters; verify fitting and held-out inputs remain separate, configured tolerances precede evaluation, and normalized functional observations do not imply ISA or tensor-value execution in this simulator. If external runs are unavailable, test the adapter contract with clearly synthetic fixtures and report that external evidence tier as unavailable.
- [x] 6.5 Run the consolidated suite and targeted type/lint checks, publish a requirement-to-evidence report identifying absent hardware validation, and reconcile VA-01..07 coverage before exploring step 7; retain multicast/synchronization coverage pending step 7.

## 7. Multicast and scalar synchronization — `wormhole-multicast-sync`

- [x] 7.1 Explore the smallest useful documented multicast and scalar atomic/semaphore subset using prior workload evidence; deliver TR-05/MT-06 decisions for destination eligibility, source inclusion, replication, ordering, completion, and deadlock prevention. Explicitly distinguish scalar synchronization from tensor reductions.
- [x] 7.2 Create and validate the child's artifacts with `openspec validate wormhole-multicast-sync --strict --no-interactive`; verify tasks specify supported destination sets, new traffic dependencies, shared resource costs, traces, and representative workload fixtures.
- [x] 7.3 Apply multicast transport tasks; verify exactly-once destination delivery, shared-prefix traffic accounting, destination backpressure, completion behavior, rejection of unsupported modes, and finite-traffic drain.
- [x] 7.4 Apply scalar control-state and synchronization tasks; verify atomic service serialization, network costs, visible updates, dependent consumer release, and combined pipeline/multicast liveness.
- [x] 7.5 Run relevant multicast/synchronization and legacy regression tests plus targeted type/lint checks; extend representative workload reports and reconcile TR-05/MT-06 and final VA-04 evidence before umbrella closure.

## 8. Umbrella reconciliation

- [x] 8.1 Audit all 27 target requirements against concrete child changes, executable tests, reviewed results, and explicit fidelity limits; verify no requirement is marked implemented solely because a schema field or planning document exists.
- [x] 8.2 Review updated JSON examples, topology/trace contracts, generated evidence artifact retention, and predictor/RL model assumptions; verify every claimed supported workflow has a recorded executable validation path and missing calibration remains visible.
- [ ] 8.3 Reconcile parent deltas with delivered main specs before umbrella archival, verify there are no duplicate ADDED requirements or prematurely delivered targets, and run strict OpenSpec validation on the reconciled artifacts. Commit each completed, validated part before continuing; push only if separately requested.
