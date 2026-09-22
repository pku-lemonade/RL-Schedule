## Purpose

Provide a neutral, strictly validated generic simulation layer: a system-graph
configuration free of real device vocabulary, a unified runtime supporting
exactly transfer/compute/wait/signal transactions, and a public adapter
boundary that lets private inputs drive the public runtime without entering
this repository.

## ADDED Requirements

### Requirement: GS-G01 Neutral strict system-graph documents

A version-one `generic_system_graph` document SHALL describe nodes, ports,
links, one or more networks, DMA endpoints, execution units, memory resources
and static routing tables using neutral names and identifiers only. Unknown
fields, duplicate identities, dangling references, empty networks and any real
device or vendor vocabulary SHALL be rejected. No field SHALL carry a
device-derived default; every dimension, rate, capacity and threshold SHALL be
an explicit configured input.

#### Scenario: Synthetic two-network graph loads

- **WHEN** a fully synthetic two-dimensional grid declares nodes, ports,
  links, two distinct networks, DMA endpoints and memory resources
- **THEN** loading succeeds, unknown-field and duplicate-identity probes fail
  validation, and every hardware parameter in the document is explicit.

#### Scenario: Device vocabulary is rejected

- **WHEN** a document or fixture contains a real device or vendor identifier
  in any name, type or parameter field
- **THEN** validation fails before any graph object is constructed.

### Requirement: GS-G02 Compilation into the canonical graph

Loading a generic system graph SHALL compile it into the existing canonical
topology machinery through a dedicated adapter and expose one unified
system-graph object covering network membership, endpoint inventories and
static route tables. Compilation SHALL NOT change the behavior, digests or
rejection paths of legacy, hardware-profile or Wormhole inputs, which SHALL
remain executable with byte-identical results.

#### Scenario: Unified graph object is produced

- **WHEN** the synthetic acceptance graph is compiled
- **THEN** one object exposes both networks' member links, all DMA endpoints,
  execution units, memory resources and route tables, with deterministic
  export independent of input record order.

#### Scenario: Existing inputs are unaffected

- **WHEN** previously admitted legacy mesh, profile-derived and torus replay
  documents execute after the adapter lands
- **THEN** their results, hashes and exit codes match the retained fixtures.

### Requirement: GS-G03 Static routing tables

Each network SHALL admit an explicit static routing table mapping
(source, destination) endpoint pairs to ordered hop sequences. Routes SHALL
resolve only declared ports and links, SHALL NOT be inferred from geometry,
and unreachable pairs SHALL be reported as such rather than guessed.

#### Scenario: Explicit route is followed

- **WHEN** a transfer targets an endpoint whose table entry lists a multi-hop
  path across two networks
- **THEN** its occupancy records touch exactly those links in order, and a
  pair without a table entry is reported unreachable.

### Requirement: GS-T01 Exactly four transaction kinds

A version-one `generic_transaction_batch` SHALL admit exactly `transfer`,
`compute`, `wait` and `signal` records with explicit identities and
dependencies. Unknown kinds, unbounded work, undeclared counters and
device-era unit names SHALL be rejected before simulation resources are
created.

#### Scenario: Unknown kind is rejected

- **WHEN** a batch contains a record kind outside the four admitted kinds
- **THEN** loading fails with a typed error and no simulation state is
  created.

### Requirement: GS-T02 Transport mechanics

Transfer execution SHALL compose static hop-by-hop routing, link
serialization, bounded buffering, credits and queueing. A DMA endpoint SHALL
be able to initiate a transfer between any readable source and any writable
destination endpoint, including memory resources, subject to the traversed
networks' route tables, serializers and credit limits.

#### Scenario: DMA transfer crosses to a memory endpoint

- **WHEN** a DMA endpoint transfers from a compute-side endpoint to a memory
  resource across declared hops
- **THEN** each traversed link serializes the bytes, bounded credits gate
  staging, and completion is recorded per hop.

### Requirement: GS-T03 Contention and overlap semantics

Transactions contending for one link or resource SHALL queue with
non-overlapping occupancy; transactions using disjoint links or resources
SHALL proceed in parallel. A compute transaction SHALL occupy its declared
execution unit for its configured duration and SHALL overlap unrelated
transfers.

#### Scenario: Same link queues, distinct links overlap

- **WHEN** two transfers share one link and two others use disjoint links
- **THEN** the shared-link pair completes in queueing order with serialized
  occupancy while the disjoint pair overlaps in time.

#### Scenario: Compute overlaps transfer

- **WHEN** a compute transaction and an independent transfer run concurrently
- **THEN** their active intervals overlap and neither extends the other's
  service time beyond declared contention.

### Requirement: GS-T04 Counter-based wait and signal

Signal transactions SHALL add declared deltas to named counters; events SHALL
be boolean counters. A wait transaction SHALL complete at the first cycle its
counter reaches the declared threshold, with simultaneous updates resolved
signal-first, and SHALL fail with an explicit reason when the threshold is
unreachable within the bounded run.

#### Scenario: Wait completes only after signal

- **WHEN** a wait targets threshold N and the signals raising the counter to
  N are scheduled later
- **THEN** the wait completes strictly after the enabling signals, and a wait
  whose threshold never becomes reachable ends incomplete with a
  dependency-unsatisfied reason.

### Requirement: GS-T05 Versioned result contract

A version-one `generic_simulation_result` SHALL record makespan completion,
per-transaction start/end, per-resource busy and utilization aggregates, and
incomplete or rejected transactions with machine-readable reason codes. An
empty transaction set SHALL NOT report success, and partial or corrupted
outputs SHALL NOT be relabelable as complete.

#### Scenario: Mixed outcomes stay distinct

- **WHEN** a batch completes some transactions and leaves others incomplete
  for dependency, reachability or capacity reasons
- **THEN** the result lists each group separately with exact identities,
  spans and reason codes, and utilization covers every declared resource.

### Requirement: GS-A01 Neutral adapter boundary

The public package SHALL expose an abstract adapter interface returning
validated `generic_system_graph` and `generic_transaction_batch` documents.
Phase 1 SHALL perform no dynamic plugin discovery, no dynamic imports, no
shell evaluation and no network access; private launch scripts MAY import the
public library and call the adapter and driver directly.

#### Scenario: Adapter output is validated like any document

- **WHEN** an adapter returns a graph or batch document
- **THEN** it passes through the same strict validation and compilation as a
  file-loaded document, with identical rejection behavior.

### Requirement: GS-A02 Privacy constraints

The public repository SHALL NOT parse private binaries or traces and SHALL
NOT contain private type names, parameters, samples or test data. A committed
guard test SHALL scan the new public modules, fixtures and documentation for
forbidden device/vendor/private tokens and fail on any hit.

#### Scenario: Seeded violation is detected

- **WHEN** a forbidden token is deliberately seeded into a scanned file in a
  test
- **THEN** the guard test fails and names the offending file and token class.

### Requirement: GS-A03 Synthetic adapter acceptance

The public repository SHALL ship exactly one example adapter whose input
format is independently designed for this change and matches no private
format. The example SHALL drive the synthetic two-network acceptance system
end to end through graph loading, batch execution and result emission.

#### Scenario: Synthetic adapter runs end to end

- **WHEN** the example adapter converts its synthetic input and the driver
  executes the batch
- **THEN** connectivity, contention, overlap and wait/signal acceptance
  outcomes match the file-loaded equivalents exactly.
