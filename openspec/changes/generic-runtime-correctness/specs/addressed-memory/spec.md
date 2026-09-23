# addressed-memory Specification (delta)

## MODIFIED Requirements

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
