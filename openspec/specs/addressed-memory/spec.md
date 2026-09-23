# addressed-memory Specification

## Purpose
Extend the generic layer with addressed memory reads/writes over
hierarchically structured memory resources — banks, ports and channels with
honest contention — inside the unified compile/runtime pipeline, keeping
flat-memory behavior byte-identical.

## Requirements

### Requirement: AM-01 Optional strict memory hierarchy

A memory resource MAY declare a `hierarchy` with explicit `banks`,
`stripe_bytes`, `latency_cycles`, named `ports` carrying `command_cycles`
and named `channels` carrying `bytes_per_cycle`. Unknown fields, duplicate
identities, dangling channel references and non-positive values SHALL be
rejected. Resources without a hierarchy SHALL keep flat phase-1 behavior.

#### Scenario: Hierarchy validates strictly

- **WHEN** a hierarchy duplicates a port identity or references an unknown
  channel
- **THEN** validation fails before any graph object is constructed.

### Requirement: AM-02 Addressed transfer records

A transfer MAY carry an `address`. An addressed transfer SHALL name exactly
one memory service endpoint: memory destination is a write, memory source is
a read. An address on a non-memory pair, on a flat memory, or a range
exceeding capacity SHALL fail at compile time.

#### Scenario: Illegal addresses fail at compile

- **WHEN** a transfer addresses a flat memory, a non-memory endpoint pair, or
  an address range beyond capacity
- **THEN** compilation raises before any runtime object exists.

### Requirement: AM-03 Deterministic memory plan resources

Compilation SHALL emit one serializer per bank, port and channel with stable
neutral identities and a deterministic stripe mapping: bank and port indices
derive from `address // stripe_bytes` and the port binds its declared
channel. Identical specs SHALL yield identical plan digests; no SimPy object
or global state is created.

#### Scenario: Mapping is deterministic

- **WHEN** two accesses use addresses in different stripes
- **THEN** their mapped bank/port/channel identities are exactly the
  documented stripe function of the address.

### Requirement: AM-04 Contended memory service

A write SHALL service after network arrival and a read SHALL service before
network departure. Command issue SHALL occupy the mapped port for
`command_cycles`; data service SHALL then acquire the mapped bank and
channel atomically through the registry's multi-resource mechanism and
hold them for `latency_cycles + ceil(bytes / channel_bytes_per_cycle)`.
Interrupts SHALL propagate to the transaction layer: a cancelled or
timed-out access SHALL NOT start later business stages, SHALL NOT report
completion, and SHALL release or cancel every request it created.

#### Scenario: Bank parallelism and serialization

- **WHEN** two writes map to different banks and two others map to one bank
- **THEN** the different-bank pair overlaps in service while the same-bank
  pair serializes.

#### Scenario: Channel bounds service time

- **WHEN** two accesses share one channel with a fixed `bytes_per_cycle`
- **THEN** their data services cannot exceed the channel rate and overlap is
  serialized by the channel.

#### Scenario: Cancellation in any stage propagates

- **WHEN** an access is interrupted while waiting for the port, during
  command issue, while waiting for bank and channel, or during data service
- **THEN** the transfer ends incomplete, no later stage starts, and the
  bank, port and channel show no held or queued ownership afterwards.

#### Scenario: Simultaneous timeouts leave nothing behind

- **WHEN** two accesses contend for one memory port and both are cancelled
  at the cycle bound
- **THEN** neither reports completion and no ownership or queue entry
  survives on the port, bank or channel.

### Requirement: AM-05 Unified accounting and compatibility

Memory bank/port/channel busy, queue waits and utilization SHALL appear in
the unified result with distinct usage kinds. Flat memories, unaddressed
transfers, phase-1/2 documents, fixtures, digests and tests SHALL remain
byte-identical.

#### Scenario: Flat behavior is untouched

- **WHEN** a flat memory accepts an unaddressed transfer
- **THEN** its behavior and result digests match the phase-2 expectations,
  and hierarchical usage kinds do not appear.
