# instruction-programs Specification (delta)

## ADDED Requirements

### Requirement: IP-01 Program documents

A version-one `generic_transaction_batch` MAY declare a `programs` tuple of
instruction programs. A program SHALL carry a neutral `program_id`, the
`unit_id` of its sequencer execution unit, an optional `start_cycles`
earliest start and a non-empty `ops` tuple. Program identities SHALL be
unique and disjoint from transaction identities, operation identities SHALL
be unique within their program, unknown fields SHALL be rejected, and a
batch SHALL contain at least one transaction or program.

#### Scenario: Program validates strictly

- **WHEN** a program duplicates an identity, names an unknown execution
  unit, carries an unknown field or declares no operations
- **THEN** validation fails before any plan object is constructed.

#### Scenario: Empty workload cannot report success

- **WHEN** a batch declares neither transactions nor programs
- **THEN** validation fails before any simulation state is created.

### Requirement: IP-02 Operation kinds and references

An operation SHALL be exactly one of `transfer`, `compute`, `wait` or
`signal` with the same field rules as batch transactions; a `transfer`
operation MAY carry an `address`. Compilation SHALL validate every network,
endpoint, counter and unit reference of every operation, resolve routes,
terminal classifications and counter bounds exactly as for batch
transactions, and reject any violation before any runtime object exists.

#### Scenario: Illegal operation reference fails at compile

- **WHEN** an operation names an unknown endpoint, counter or network, or a
  transfer operation violates addressed-memory rules
- **THEN** compilation raises with the same behavior as for a batch
  transaction.

### Requirement: IP-03 Deterministic in-order issue

Operations SHALL issue in program order on their sequencer unit: issue
SHALL occupy the unit for the operation's explicit `issue_cycles`, and
issue occupancy SHALL serialize per unit so programs sharing one sequencer
interleave deterministically. An operation SHALL NOT issue before its
dependencies complete; an omitted `depends_on` SHALL mean the immediately
preceding operation, an explicit tuple SHALL name earlier operations of the
same program only, and forward references SHALL be rejected. Each issue
SHALL emit deterministic `issue_acquire` and `issue_release` trace events.

#### Scenario: Default order is serial

- **WHEN** a program declares three operations without `depends_on`
- **THEN** each operation starts only after its predecessor completes, and
  their spans are strictly ordered.

#### Scenario: Explicit dependencies allow overlap

- **WHEN** an operation declares an explicit empty or partial `depends_on`
- **THEN** it may issue once those dependencies complete, overlapping its
  predecessor's execution where resources allow.

### Requirement: IP-04 Unified execution

Operations SHALL execute through the same registry, event bus and pipeline
as batch transactions: identical contention and queueing, identical
cancellation, drain and abort semantics, and shared counters, so a `signal`
operation in one program SHALL resume a `wait` operation or transaction
elsewhere. Operation spans SHALL appear in the unified result under
`program_id/op_id` identities.

#### Scenario: Cross-program coordination

- **WHEN** a `wait` operation in one program targets a threshold reached by
  `signal` operations in another program
- **THEN** the wait completes strictly after the enabling signals.

### Requirement: IP-05 Program termination

An operation classified terminal at compile time SHALL end its program: the
operation reports its terminal reason and every later operation reports
`dependency_unsatisfied`. An interrupted program SHALL release all
ownership, run no later stage and report no completion. The result SHALL
carry an additive `programs` tuple of per-program spans with status,
reason and start/end; a program is complete exactly when every one of its
operations completed.

#### Scenario: Terminal operation stops the program

- **WHEN** a program's second operation is classified `route_unreachable`
- **THEN** it reports `route_unreachable`, the third operation reports
  `dependency_unsatisfied`, the program span is incomplete and no later
  operation acquires any resource.

### Requirement: IP-06 Result and digest integrity

`completion_cycles` SHALL keep its business-completion meaning over all
transaction and operation spans; the `programs` tuple SHALL default to
empty; repeated runs of the same spec SHALL be byte-identical, and a batch
without programs SHALL produce plans and results byte-identical to those
before this capability existed.

#### Scenario: Program-free batches are untouched

- **WHEN** a batch without programs is compiled and executed
- **THEN** its plan digest, trace and result match the pre-change
  expectations exactly, and its `programs` tuple is empty.

### Requirement: IP-07 Neutral vocabulary

Program and operation documents SHALL use neutral synthetic identifiers
only; the committed token guard SHALL cover every new module, fixture and
documentation file introduced by this capability.

#### Scenario: Seeded violation is detected in new modules

- **WHEN** a forbidden token is deliberately seeded into a scanned file
  added by this change
- **THEN** the guard test fails and names the offending file and token
  class.
