## Purpose

Execute declared finite single-chip FC/matmul workloads with configurable abstract compute costs, real memory traffic and bounded pipeline storage, while exposing compatibility and fidelity limits for placement and scheduling studies.

## ADDED Requirements

### Requirement: CD-D01 Explicit finite workload admission
The simulator SHALL admit versioned workloads with explicit worker bindings, operation dimensions, storage dtype/layout, addressed tensors, memory and compute settings, finite job order, buffer slots and supported dependencies. Admission MUST validate physical enabled compute eligibility, local resource ownership, all byte extents, supported precision/rate combinations, source versions and the combined dependency/FIFO/slot-reuse graph before runtime allocation. Hardware worker identity MUST remain distinct from tensor tile shape. Unsupported operations, layouts, implicit conversions, in-place output, split-K reductions and dependencies requiring unmodeled remote notifications SHALL fail explicitly.

#### Scenario: Enabled physical worker receives a supported job
- **WHEN** a finite workload maps a fully specified matrix job to an enabled worker with matching local memory and eligible fabric interfaces
- **THEN** admission produces a deterministic plan with effective source/configuration identities and distinct worker and tensor-tile metadata.

#### Scenario: Invalid dependency or physical placement
- **WHEN** a workload selects a harvested/router-only tile, mismatched local resource, overlapping slot extents, backward FIFO dependency or unsupported cross-worker completion wait
- **THEN** it is rejected before any runtime resource or output file is created, with the offending identity or dependency identified.

### Requirement: CD-D02 Positive configurable FC and matmul costs
Supported dense matmul and bias-free FC SHALL execute with shape-consistent useful and padded arithmetic work, using two operations per multiply-add. Duration SHALL use a declared effective rate, setup, positive minimum service quantum, arithmetic block geometry, precision/fidelity key and clock conversion. Positive admitted work MUST advance simulated time; nonfinite or unrepresentable costs SHALL fail rather than silently complete. FC flattening and shared weights SHALL be explicit. A theoretical hardware peak MUST NOT be silently substituted for an effective configured rate.

#### Scenario: Positive work below one rate quantum
- **WHEN** an admitted FC or matmul has less work than one nominal rate quantum
- **THEN** it occupies the selected compute resource for at least the configured positive minimum quantum and reports its useful/padded work, units and effective rate assumptions.

#### Scenario: Storage format differs from arithmetic assumptions
- **WHEN** a job uses a declared storage dtype and accumulator/fidelity combination
- **THEN** storage bytes and cost are derived from their separate declared contracts, and an absent matching rate or unsupported conversion is rejected.

### Requirement: CD-D03 Transaction-backed local and remote stages
Remote workload readers and writers SHALL execute the existing addressed-memory protocol, including configured routes, descriptors, request/response packets, local/target service and required completion. Local operand reads and result writes SHALL use the same canonical L1 service as network clients. Local-only accesses MUST NOT fabricate network traffic. Already charged transfer service MUST NOT receive another synthetic LSU/allocation delay. A compute result SHALL become available only after arithmetic and required local result service finish.

#### Scenario: Route change delays a dependent computation
- **WHEN** an otherwise identical job reads through a longer or contended valid route
- **THEN** actual memory/network events and dependent compute timing reflect that route while declared arithmetic work remains unchanged.

#### Scenario: Local-only computation
- **WHEN** admitted operands already reside in the worker's ready local buffers and its result remains local
- **THEN** the job consumes applicable local memory and compute service, waits for correct versions and publishes its result without network packets.

### Requirement: CD-D04 Shared execution lifecycle and causal gates
The composed workload SHALL execute memory and compute in one simulation time domain with one owner for each physical capacity and service resource. A finite operation SHALL activate at most once after its admitted dependencies and stage gate permit it. Gating MUST preserve memory ordering/version checks and valid local/remote observation scopes. Standalone memory replay SHALL retain its existing activation, timing, accounting and teardown behavior. Composed execution SHALL defer teardown until the enclosing workload drains.

#### Scenario: Math gates a result write
- **WHEN** a result's addressed local write is present in the finite plan before execution
- **THEN** it cannot submit or publish before its job's arithmetic completion, and independent eligible memory operations can still progress.

#### Scenario: Standalone replay after composition support
- **WHEN** an existing memory replay runs without a workload coordinator
- **THEN** its operation/packet/service/ownership events, completion semantics and capacity restoration match its established behavior.

### Requirement: CD-D05 Finite reusable buffer generations
The initial `fifo_item_slots_v1` policy SHALL reserve each slot's physical input/output footprint exactly once and use generation-aware reserve, input publication, consumption, output publication, drain and release events for reuse. A producer SHALL wait when every slot is occupied, and a consumer SHALL wait for publication of the correct generation. All reservations and in-flight users SHALL count as occupied. Stale generations, double publication/release, premature reuse and double allocation SHALL be rejected. The conservative bundled lifetime MUST be declared rather than presented as a complete TT-Metal circular-buffer implementation.

#### Scenario: Producer outruns the consumer over repeated reuse
- **WHEN** a finite stream contains more items than its slot count and the reader is faster than downstream stages
- **THEN** producers backpressure, free plus occupied slots always equals configured slots, physical L1 reservations stay constant and each admitted item is consumed/released exactly once.

#### Scenario: Posted output leaves remote work pending
- **WHEN** a posted writer completes local handoff and safely releases its current slot generation before target visibility
- **THEN** slot reuse cannot corrupt the transmitted version, and pending remote effects remain charged and prevent overall success until they drain.

### Requirement: CD-D06 Bounded stage overlap and shared contention
Independent reader, compute and writer stages SHALL overlap across admitted item slots when dependencies and resources permit. Stage/compute admission, active contexts and result storage SHALL be bounded; multiple streams on one physical worker SHALL share its compute resources. Configured rates SHALL describe each declared engine slot and MUST NOT multiply implicitly with software thread count. Local memory, DRAM and network contention SHALL constrain stage timing. The runtime MUST NOT hold a math grant while waiting for unpublished inputs or unavailable output capacity.

#### Scenario: One versus two item slots
- **WHEN** three FIFO items use independent constant-duration stages with reader 2, compute 3 and writer 2 time units under the bundled slot policy
- **THEN** one slot yields makespan 21 and two slots yield makespan 14, with no compute before input publication or writer before output publication.

#### Scenario: Independent versus shared compute resources
- **WHEN** two ready streams switch from distinct physical compute resources to one shared single-slot resource
- **THEN** their arithmetic intervals serialize on the shared resource, while eligible reader/writer activity remains able to progress and no capacity limit is exceeded.

### Requirement: CD-D07 Full drain and resumable incomplete state
Workload success SHALL require every admitted job, writer effect, memory operation, descriptor, access lease, service request, transport credit and buffer generation to drain. Finalization SHALL restore physical reservations exactly once. A horizon-limited or idle-with-pending run SHALL return incomplete status with pending identities and charged resource state, never successful completion. Resuming an incomplete run SHALL continue existing state without duplicating transfers, computation or ownership transitions.

#### Scenario: Short observation resumes to completion
- **WHEN** a workload is stopped while compute, memory or posted destination effects remain pending and is then resumed
- **THEN** concatenated execution evidence and final accounting agree with an uninterrupted run, and capacity is released only at successful finalization.

#### Scenario: Jobs retired but delayed credits remain
- **WHEN** all job-level completion events have fired but transport or service resources still retain work
- **THEN** the result remains incomplete until those resources drain, and repeated final result access cannot release capacity twice.

### Requirement: CD-D08 Explicit legacy DFG and consumer compatibility
The simulator SHALL retain the supported legacy conv/pool, memory, communication, fail-slow and trace behavior. An explicit `legacy_fc_chain_v1` adapter SHALL accept the supported LOAD_FEAT/LOAD_WGT -> FC -> STORE subset only with complete physical, tensor, memory, slot and compute metadata; every imported node/edge SHALL be accounted for without mutating legacy mapper state. Unsupported imported nodes or missing metadata SHALL fail. Legacy direct FC SHALL report its unimplemented execution mode instead of succeeding without work. New workload/results SHALL be rejected by incompatible predictor/embedding consumers before optional tensor or checkpoint work; existing model and RL contracts MUST NOT be silently reinterpreted.

#### Scenario: Complete FC sidecar matches direct workload
- **WHEN** a supported FC chain is imported with an explicit sidecar describing all required semantics
- **THEN** its normalized costs, memory traffic and stage behavior match an equivalent direct workload, with the source provenance distinguished.

#### Scenario: Unsupported FC path or consumer
- **WHEN** FC is executed directly through the legacy no-adapter path, or a new workload/result is passed to a legacy-only consumer
- **THEN** execution reports the unsupported mode and required compatible path rather than fabricating successful FC work or loading an incompatible model.

### Requirement: CD-D09 Executed workload evidence and fidelity reporting
Versioned workload results SHALL include effective policies/configuration, graph/profile/plan identities, planned costs, actual completed work, stage and buffer-generation events, compute occupancy, nested memory/transport evidence, capacity state and completion/pending diagnostics. Reports SHALL distinguish tensor useful/storage bytes, packet overhead, local/remote service, arithmetic busy time and context occupancy. Parsing, compilation, abstract workload execution, numerical fidelity and hardware timing validation SHALL remain distinct capability claims. The general legacy full-profile entry point MUST NOT be enabled merely because the finite workload path is executable.

#### Scenario: Incomplete run contains planned but unexecuted jobs
- **WHEN** a result is exported before all admitted jobs execute
- **THEN** planned arithmetic and packets are not counted as completed work/traffic, and independently reconstructible events explain every executed total and pending owner.

#### Scenario: Wormhole example succeeds with assumed rates
- **WHEN** a single-ASIC example completes using configurable, unmeasured compute/memory parameters
- **THEN** it reports abstract scheduling/traffic support and explicit assumptions, without claiming numerical, ISA/kernel, optimized circular-buffer or silicon timing equivalence.

### Requirement: CD-D10 Reproducible examples and incremental validation
The change SHALL provide executable generic and Wormhole matmul/streaming examples, deterministic versioned output and explicit complete/incomplete/invalid exit status. Invalid input SHALL preserve existing output files. Required evidence SHALL include independent cost and stage-timeline expectations, capacity/version conservation, shared-resource controls, capacity-one finite drain, legacy compatibility and strict type/lint/spec checks. Each completed implementation part SHALL be committed before continuing. Public architectural evidence, synthetic model oracles and unavailable external/hardware validation SHALL be labeled separately.

#### Scenario: Reproducible CLI and invalid-input protection
- **WHEN** the same valid example is run repeatedly, or an invalid workload is sent to an existing output path
- **THEN** valid runs produce deterministic equivalent evidence and appropriate exit codes, while invalid admission returns a diagnostic without replacing the prior output.

#### Scenario: Final child handoff
- **WHEN** implementation is declared complete
- **THEN** a delivery report maps CD-D01..10 to actual tests and CD-01..05/pipeline VA-04, records exact commands/results and limits, and leaves unexecuted hardware/reference tiers explicitly unavailable.
