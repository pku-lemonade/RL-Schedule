## ADDED Requirements

### Requirement: MS-01 Explicit finite admission and configurable policies

The simulator SHALL admit the opt-in `multicast_sync_workload` version 1 through strict parsing and pure compilation before allocating runtime resources or replacing output files. The finite plan MUST identify the single ASIC, canonical graph/profile, fabrics, endpoints, addressed buffers, multicast/scalar/compute operations, dependencies and effective policies. Hardware dimensions, clocks, packet geometry, capacities, scalar storage widths and effective service costs SHALL be explicit validated configuration, with additional restrictions for the selected hardware binding. Unsupported kinds, versions, modes, unresolved identities, invalid extents and nonfinite or unrepresentable service SHALL fail explicitly. Existing execution contracts MUST retain their supported behavior.

#### Scenario: Invalid mixed workload is rejected before execution
- **WHEN** an input contains an unknown operation, unavailable route, inconsistent resource alias, out-of-range address or invalid service quantity
- **THEN** admission fails before runtime allocation and an existing output file remains unchanged.

#### Scenario: Generic configuration differs from the example hardware
- **WHEN** an admitted generic workload changes dimensions, clocks, physical flit width, queue capacities or scalar width within its declared policy
- **THEN** the effective plan and execution use those values without fixed Wormhole example constants, while a Wormhole binding rejects values incompatible with its explicit contract.

### Requirement: MS-02 Rectangular destination eligibility and source inclusion

The `corner_rectangle_tree_v1` policy SHALL select recipients from inclusive non-wrapping fabric-coordinate bounds with a declared major axis and source-inclusion flag. X-major sources MUST lie on the start row at or before the start column; Y-major sources MUST lie on the start column at or before the start row. Recipients SHALL be unique enabled worker L1 endpoints inside the rectangle, excluding the initiating endpoint when requested. Nonworker transit routers MUST NOT receive payload. Unavailable or disabled worker targets, unknown permissions, empty recipient sets, duplicate aliases, wrapping bounds, unsupported source placement, arbitrary masks, multicast reads and multicast atomics SHALL be rejected. One common target address and length MUST fit every recipient's declared buffer.

#### Scenario: Source inclusion changes exactly one recipient
- **WHEN** a source at the rectangle entry executes otherwise identical writes with source inclusion enabled and disabled
- **THEN** the included run has exactly one additional local destination effect and ejection, with no second source read, and source/target overlap is rejected.

#### Scenario: Rectangle crosses nonworker positions
- **WHEN** a valid rectangle contains enabled transit-only routers between eligible workers
- **THEN** the tree traverses the declared routers and delivers only to the exact worker set; a required disabled worker endpoint causes admission failure rather than silent omission.

#### Scenario: Opposite fabric selects the same physical workers
- **WHEN** the caller supplies correctly translated rectangle bounds and a supported source placement on the opposite fabric
- **THEN** coordinate translation preserves the declared physical recipient set while all traversed links and events retain the selected fabric identity.

### Requirement: MS-03 Shared tree routing and exact traffic accounting

The compiler SHALL construct the declared major-axis approach, minor-axis spine and major-axis branches as an acyclic directed tree, validating every edge against the canonical graph. A logical source segment SHALL have one injection and one traversal of each admitted tree edge, with explicit replication and recipient ejections. Payload segmentation and header/padding rules SHALL be applied once per source segment. Results MUST distinguish source useful bytes, aggregate recipient useful bytes, packet bytes, physical channel bytes and memory service bytes; an implementation MUST NOT substitute independent full unicasts or producer totals for shared-tree accounting.

#### Scenario: Two destinations share a prefix
- **WHEN** one segment reaches two recipients through a common approach and distinct branches
- **THEN** every flit traverses each shared edge once, every required branch/ejection once, and each recipient receives the complete segment exactly once.

#### Scenario: Payload spans more than one packet
- **WHEN** the source length exceeds the configured maximum packet payload
- **THEN** each segment carries its own configured header and rounded payload, source reads cover the useful range once, and each recipient becomes fully ready only after all required segment effects.

### Requirement: MS-04 Bounded replication and justified reservation

Tree execution SHALL use explicit finite lane, staging, descriptor and reservation capacities. `atomic_tree_reservation_v1` MUST acquire all required multicast lane owners without partial hold, charge declared controller service, and release the grant after its tree traffic and credit returns drain. Every live flit/copy MUST own counted storage; an incoming flit SHALL retain its credit until all required outputs accept it. All traffic classes MUST share the same physical link/router service, without multiplying bandwidth. The documented dependency argument SHALL cover multicast branches, ordinary unicast, responses, destination service and application dependencies. Unsupported dependency patterns MUST fail admission; timeouts alone MUST NOT be described as deadlock prevention.

#### Scenario: One destination backpressures a finite tree
- **WHEN** a recipient has slow finite service and all relevant staging/credit capacities are one
- **THEN** upstream traffic stalls within those capacities, every copy remains accounted for, and the finite admitted transfer drains without duplicate delivery or leaked credits.

#### Scenario: Conflicting trees and ordinary traffic share hardware
- **WHEN** two trees need a common multicast lane while ordinary request/response traffic uses the same physical link
- **THEN** the waiting tree holds no partial reservation, granted traffic shares the configured serializer fairly, and unrelated virtual classes do not receive extra physical throughput.

### Requirement: MS-05 Addressed multicast effects and observable completion

Multicast writes SHALL read the source once and serve every destination through the existing canonical memory capacity, access/version and aggregate service owners. Lease/response provisioning MUST be bounded and avoid partial acquisition cycles. Recipient visibility SHALL follow actual service of the complete required extent. Posted source completion SHALL mean all source segments have handed off; acknowledged source completion SHALL require returned unicast acknowledgements for every recipient/segment after its visible effect. Global success MUST include all posted effects and resource drain. Remote diagnostic all-effects events MUST NOT act as zero-cost initiator fences or create implicit ordering across fabrics.

#### Scenario: Posted handoff precedes a slow target effect
- **WHEN** a posted multicast hands off while one destination still has pending L1 service
- **THEN** local completion can be reported, but that destination remains unready and the overall run cannot complete or release its live destination ownership early.

#### Scenario: Acknowledged multicast waits for every return
- **WHEN** all target effects finish but one acknowledgement is delayed on its return path
- **THEN** source-observable completion remains pending until that acknowledgement arrives, and return traffic consumes the configured response path and storage.

#### Scenario: Both fabrics and local compute access one L1
- **WHEN** multicast writes, ordinary addressed traffic and local compute service use the same physical memory owner
- **THEN** their storage/service occupancy shares one configured capacity and service envelope rather than independent servers per attachment or operation type.

### Requirement: MS-06 Addressed scalar state and indivisible service

The `monotonic_l1_counter_v1` policy SHALL maintain actual initialized unsigned scalar values in aligned, reserved canonical L1 granules. It SHALL support increment-by-one only, with explicit storage width and a pre-admission bound preventing overflow. Wormhole scalar words MUST be aligned 32-bit values; generic widths MUST fit the admitted inline-control format. Counter granules and return inboxes MUST NOT alias payload reservations or each other. Scalar RMW service SHALL occupy the same bounded memory server as ordinary clients for one configured final duration. Updates MUST linearize exactly once at service completion in deterministic server admission order, with old/new values and sequence identity recorded. Unsupported scalar arithmetic and tensor reductions SHALL fail explicitly.

#### Scenario: Concurrent producers update one counter
- **WHEN** requests from both fabrics contend for a counter initially equal to zero
- **THEN** the shared server serializes their RMW jobs, successive visible values are one and two, old values are assigned in that order independently of response arrival order, and neither increment is lost or applied twice.

#### Scenario: Overflow or ambiguous scalar storage is requested
- **WHEN** the finite program can exceed its scalar width or a counter granule overlaps another declared reservation
- **THEN** compilation rejects the program before simulation rather than wrapping silently or allocating a separate uncharged scalar store.

#### Scenario: Independent L1 resources receive atomic work
- **WHEN** two updates target different canonical L1 owners with sufficient independent capacity
- **THEN** they may execute concurrently; no chip-wide atomic lock serializes them, while ordinary accesses to either owner still contend with its RMW service.

### Requirement: MS-07 Scalar network cost and return semantics

Remote scalar increments SHALL send a single admitted immediate request flit over the ordinary unicast request path. Posted mode SHALL have no response and SHALL distinguish handoff from target visibility. Returning mode SHALL send one unicast response flit containing the previous value only after the target update, then serve the reserved initiator inbox before source completion. Inline geometry MUST hold the scalar/control representation. Immediate requests MUST NOT invent source payload reads. No request credit or active atomic server grant SHALL be retained while waiting for a response to inject.

#### Scenario: Posted atomic has only local completion
- **WHEN** a posted increment is injected but target atomic service has not completed
- **THEN** the source can observe local handoff while the counter value and target wait condition remain unchanged, and no response packet is counted.

#### Scenario: Returning atomic result travels back
- **WHEN** a returning increment changes a target from seven to eight
- **THEN** its response carries seven after the visible update and source completion follows real response transport and local inbox service, with both flits charged.

### Requirement: MS-08 Local semaphore observations and causal release

The `local_threshold_wait_v1` policy SHALL observe a counter owned by the waiter's physical worker and release only when a serviced local observation finds `value >= threshold` and all declared local data-version prerequisites hold. Initial and change-triggered observations SHALL consume configured local service without missed notifications. Waiting MUST NOT occupy a compute context, network credit, atomic server slot or tree grant. The finite compiler MUST reject unknown producers, unreachable thresholds, remote peeks and cyclic operation/stage/slot dependencies. For a declared producer barrier, admission SHALL establish that later-phase or unrelated updates cannot satisfy its threshold prematurely; the runtime MUST NOT pretend that one scalar counter filters increments by sender. Scalar counts alone MUST NOT publish payload data or establish general hardware memory ordering.

#### Scenario: Producer notifications release one local consumer
- **WHEN** two visible increments satisfy a threshold of two and the required payload extent is locally ready
- **THEN** the consumer releases after a charged observation sees the satisfied condition, with the two updates, data publication and observation all present in its causal evidence.

#### Scenario: Counter is ready before payload
- **WHEN** a counter reaches its threshold while the required multicast-produced slot generation remains incomplete
- **THEN** the consumer stays blocked until that exact local data prerequisite becomes ready, and a forged release before publication fails validation.

#### Scenario: Update races with wait registration
- **WHEN** a visible increment occurs while a local observation or subscription is being established
- **THEN** the waiter rechecks safely and cannot miss the satisfied threshold or release based on an unserviced stale observation.

#### Scenario: A fast producer could overtake an earlier barrier
- **WHEN** a barrier claims completion of two producers in one round but an unordered second-round increment could supply its second count
- **THEN** admission rejects that barrier proof until an explicit causal phase dependency prevents the later-round contribution.

### Requirement: MS-09 Shared finite compute and synchronization composition

The mixed runtime SHALL compose the admitted memory, multicast, scalar and existing FC/matmul stages in one environment with one physical transport/memory registry and one finalization owner. Compute slots SHALL preserve generation-aware reserve/publish/consume/release and existing cost semantics. Imported multicast operands MUST avoid duplicate unicast reader transfers while retaining actual local operand/result service. Cross-worker release SHALL require modeled notifications and legal local data readiness. Standalone v1 compute/memory wrappers MUST retain their supported results. Disconnected replay stitching or direct observation of another worker's private completion event MUST NOT constitute implementation.

#### Scenario: Distribution, compute and collection overlap
- **WHEN** one source multicasts an operand to two workers that execute finite jobs and signal a collector after required output completion
- **THEN** data, control, local memory and compute costs share resources, each worker respects its local prerequisites, and the collector releases after the expected visible scalar updates.

#### Scenario: A second generation reuses bounded slots
- **WHEN** two rounds reuse the same finite pipeline slots and advance monotonic thresholds
- **THEN** generation two waits for legal reuse, stale readiness/signals cannot publish the new operand, and all jobs and notifications drain without duplicating capacity.

### Requirement: MS-10 Finite snapshots, resume and complete drain

Results SHALL distinguish complete execution from cycle-limit or idle-with-pending incompleteness and SHALL expose pending recipients, operations, waits and charged resources. A snapshot MUST preserve live state for resume. Resume SHALL neither reinitialize counters nor duplicate source reads, branch effects, updates or releases. Successful completion MUST require all admitted effects, acknowledgements, scalar observations, jobs, credits, leases, descriptors, reservations, contexts and slot generations to drain before exactly-once teardown. The finite-drain claim SHALL state enabled sinks, finite service, fair arbitration and absence of permanent failures as assumptions.

#### Scenario: Pause between target effects and final responses
- **WHEN** a finite horizon stops a run after some recipients and scalar updates complete but other responses or credits remain
- **THEN** the snapshot is incomplete with accurate live owners, and resuming reaches the same final values, destinations and accounting as uninterrupted execution.

#### Scenario: Pending wait cannot make progress
- **WHEN** an execution fault leaves a wait pending with no future event
- **THEN** the runtime reports idle-with-pending and retains diagnostic ownership instead of declaring success or clearing resources to manufacture drain.

### Requirement: MS-11 Independent multicast and synchronization validation

The validation harness SHALL provide the named `multicast_sync_v1` adapter and preserve separate architecture/protocol, model-invariant, functional-reference and silicon-timing evidence. Audits MUST derive expected recipients/tree edges and packet costs from input configuration, reconstruct scalar transitions and release dependencies from observations, and verify bounded ownership/drain without calling production tree/accounting helpers. Corrupted event streams with plausible summary totals MUST fail. Existing validation documents and legacy normalized output/digest fixtures SHALL remain compatible; new observation extensions SHALL be optional and omitted when unused. Coverage SHALL be specific to selected cases and actually executed checks.

#### Scenario: Shared accounting or delivery is corrupted
- **WHEN** an observation duplicates a destination, omits a branch or removes a charged prefix flit while retaining plausible totals
- **THEN** the independent check fails against the declared destination/tree/packet expectation.

#### Scenario: Scalar and causal evidence is corrupted
- **WHEN** an observation repeats an increment, returns a value before its update, releases a consumer early or leaks a tree reservation
- **THEN** the relevant state, causality or conservation audit fails rather than accepting a self-consistent producer summary.

#### Scenario: Only model evidence is available
- **WHEN** the new offline examples pass but no compatible actual vendor or hardware capture is supplied
- **THEN** reports scope their pass to executed model checks, retain external tiers as unvalidated and do not mark unrelated legacy cases or the umbrella complete.

### Requirement: MS-12 CLI, compatibility and incremental delivery

The child SHALL provide a documented CLI with versioned JSON, atomic output publication, input/output protection and explicit complete/incomplete/invalid exits, plus generic analytical and explicitly assumed Wormhole-profile examples. Existing topology v1/v2, memory v1, compute v1, synthetic mesh/DMA/failure and root 4x4 mesh/Darknet19/fail-dataset workflows MUST retain supported behavior. Predictor/encoder consumers SHALL reject the new document kinds before model execution; feature/action/checkpoint contracts MUST remain unchanged. Each implementation part SHALL be tested, type-checked, scoped-linted and committed before the next part. Delivery SHALL map requirements to actual evidence and retain unsupported/unavailable checks; umbrella reconciliation, spec synchronization, archival and push MUST remain explicit separate actions.

#### Scenario: CLI publishes a finite result safely
- **WHEN** the CLI executes a valid complete or incomplete workload, or rejects invalid input
- **THEN** it uses exits zero, one or two respectively, emits parseable versioned results for executions, preserves existing output on errors and leaves input assets intact.

#### Scenario: Legacy consumers and examples remain bounded by their contracts
- **WHEN** predecessor replay examples run and a detector/encoder is given the new result kind
- **THEN** old result versions, timing/digest fixtures and supported legacy behavior remain stable, and the new kind is rejected before optional model work rather than interpreted as old features.

#### Scenario: Final child evidence is recorded
- **WHEN** all implementation tasks have been completed and checked
- **THEN** the delivery identifies actual commands/results/commits, source and input identities, the TR-05/MT-06/VA-04 subset and remaining evidence limits, without silently changing umbrella milestones or pushing commits.
