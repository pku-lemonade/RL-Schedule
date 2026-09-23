# unified-runtime Specification

## Purpose
Provide one compile-and-execute pipeline — `SystemSpec → ImmutablePlan →
RuntimeContext → SimulationResult` — that owns resources, events,
dependencies, time, traces and results for all four generic transaction
kinds, replacing per-concern execution paths while preserving phase-1
behavior exactly.

## Requirements

### Requirement: UR-C01 Pure SystemSpec compilation

A version-one `system_spec` document SHALL compose one `generic_system_graph`
with one `generic_transaction_batch`. `compile_system` SHALL validate all
entity, resource, port, attachment, fabric and transaction references —
including the network membership of every endpoint a transfer names;
reject duplicate identities, missing references, negative capacities and
illegal connections; and emit an immutable deterministic plan. Identical
inputs SHALL yield identical plans and digests, and compilation SHALL create
no SimPy process and modify no global state.

#### Scenario: Illegal reference fails at compile

- **WHEN** a spec references an unknown endpoint, execution unit, timing
  network or override link
- **THEN** compilation raises before any runtime object exists.

#### Scenario: Cross-network endpoint reference fails at compile

- **WHEN** a transfer on one network names a source or destination endpoint
  attached only to another network
- **THEN** compilation raises before any runtime object exists, regardless
  of whether the transfer uses a static route or a dynamic policy.

#### Scenario: Compilation is deterministic and side-effect free

- **WHEN** the same spec is compiled twice, or its graph records are reordered
- **THEN** plans and digests are identical, and no simulation environment,
  process or module-level state has been created.

### Requirement: UR-C02 Immutable deterministic plan

The plan SHALL carry stable resource identities, resolved routes with
effective hop timings, terminal transfer classifications, counter
reachability bounds, dependency tables and content digests. The plan digest
SHALL cover the semantic content and SHALL NOT depend on input record order
or object identity.

#### Scenario: Plan content is order independent

- **WHEN** equivalent specs differ only in graph record order
- **THEN** their plan digests agree.

### Requirement: UR-R01 Unified RuntimeContext

One `RuntimeContext` SHALL hold the simulation environment and time, one
`ResourceRegistry`, one `EventBus`, transaction states, a deterministic
trace and metrics collector, and error/incomplete accounting, and SHALL
execute `transfer`, `compute`, `wait` and `signal` concurrently within one
run.

#### Scenario: Mixed batch executes in one context

- **WHEN** a batch contains all four transaction kinds
- **THEN** a single context executes them with shared resources, events,
  dependencies and time, producing one result.

### Requirement: UR-R02 ResourceRegistry discipline

Every physical resource SHALL be queryable by stable ID and constructed at
most once per plan. Acquisition SHALL support capacity wait and release;
transactions on one resource SHALL queue while distinct resources allow
parallel progress. Multi-resource acquisition SHALL validate the complete
identity set — rejecting unknown, duplicate or empty identities — before
any request is created, and SHALL be atomic: a transaction waiting for a
set SHALL hold none of its members, so a single-resource acquirer is never
blocked by a partial holder. Requests SHALL be registered at creation so a
queued request can be cancelled while waiting, and resources SHALL be fully
released on completion, failure or cancellation.

#### Scenario: Opposing transfers on a ring complete

- **WHEN** single-slot buffered links form a ring and transfers circulate
  through shared links in cyclic order
- **THEN** time-triggered releases and ordered acquisition let every transfer
  complete; no transaction remains stuck holding partial resources.

#### Scenario: Cancellation releases everything

- **WHEN** a run hits its cycle bound with transactions mid-service
- **THEN** interrupted processes release every held resource and the registry
  reports no held or pending ownership.

#### Scenario: A waiting set holds nothing

- **WHEN** a transaction requests resources A and B while B is busy
- **THEN** it holds neither A nor B, and another transaction requesting only
  A proceeds.

#### Scenario: Invalid sets are rejected before any request

- **WHEN** a multi-resource request names an unknown or duplicate identity
- **THEN** validation fails before any request object exists, and no
  ownership or queue entry survives.

### Requirement: UR-R03 EventBus semantics

The bus SHALL support named events, counted events and wait conditions.
`signal` SHALL publish and `wait` SHALL resume once its condition holds.
Same-time publishes SHALL be ordered by a deterministic global sequence,
never by dictionary order or object addresses. Waits that can never be
satisfied SHALL be detected from declared bounds and reported with explicit
reasons.

#### Scenario: Never-satisfiable wait is reported

- **WHEN** a wait threshold exceeds the counter's maximum reachable value
- **THEN** the result reports the wait incomplete with an explicit reason and
  an explanatory error entry.

### Requirement: UR-R04 Unified result content

The result SHALL record makespan, per-transaction status with start/end,
resource utilization and queue wait times, per-transaction wait times,
incomplete transactions with reasons, an error list, a deterministic trace
and plan/input digests. Phase-1 v1 fields, kinds and validators SHALL remain
valid.

#### Scenario: Repeated runs are byte identical

- **WHEN** the same spec runs twice
- **THEN** plan digest, transaction order, trace and result content agree
  exactly.

### Requirement: UR-X01 Phase-1 compatibility

Existing phase-1 documents, CLI, examples, exit codes and interfaces SHALL
keep working through compatibility shims, and phase-1 tests SHALL pass
unchanged. Legacy consumer guards SHALL reject the new document kinds.

#### Scenario: Phase-1 entry points keep their behavior

- **WHEN** phase-1 batches execute through `run_generic_batch` or the CLI
- **THEN** numeric outcomes, classifications and digests match the retained
  phase-1 expectations, and the new kinds are rejected by legacy consumers.

### Requirement: UR-R05 Bounded cancellation, drain and abort

Interrupts SHALL propagate through every service stage to the transaction
layer: after cancellation or timeout no business stage SHALL continue and
no interrupted transaction SHALL report completion. Cleanup SHALL have
explicit end conditions: after the cycle bound the run SHALL end its
cancellation phase once every business process has ended, and before
returning the run SHALL drain delayed credit returns and other background
processes. A result reporting `drained` SHALL mean truly drained — no
resource holds or awaits ownership and the trace records the final
releases — while `completion_cycles` keeps its business-completion meaning.
A runtime exception SHALL abort the run: in-flight transactions are
cancelled, bounded cleanup executes, and the original error is preserved;
a failed run SHALL NOT return a success result.

#### Scenario: Timeout is never success

- **WHEN** the cycle bound arrives with transactions mid-service
- **THEN** every interrupted transaction ends incomplete, no business stage
  runs afterwards, and the result cannot report complete.

#### Scenario: Delayed credits drain before return

- **WHEN** business completes with delayed credit returns still pending
- **THEN** the run continues until they finish, `completion_cycles` keeps
  its business meaning, the trace records the final releases and the
  registry is empty at return.

#### Scenario: A runtime fault aborts honestly

- **WHEN** any transaction process raises an exception
- **THEN** the run cancels the remaining work, performs bounded cleanup that
  releases every resource, and raises an error preserving the original
  cause instead of returning a result.
