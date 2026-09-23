# unified-runtime Specification (delta)

## MODIFIED Requirements

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

## ADDED Requirements

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
