## Why

The generic layer models independent transactions, but accelerator
workloads are instruction streams: an execution unit issues a sequence of
operations — transfers, compute bursts, waits and signals — in program
order, paying a dispatch cost per operation and overlapping execution only
where dependencies allow. Today such a stream can only be approximated by
hand-built dependency chains, with no issue occupancy on the sequencing
unit, no program-level identity in results, and no termination semantics
for a faulting operation. This change adds neutral instruction programs to
the existing unified pipeline.

## What Changes

- **Program documents.** A `generic_transaction_batch` MAY declare
  `programs`: named sequences of operations bound to one execution unit
  (the sequencer), with an optional program-level `start_cycles`. A batch
  must still contain at least one transaction or program. All fields are
  strict; program identities are unique and disjoint from transaction
  identities; unknown fields are rejected.
- **Operations.** Each operation is exactly one of the four existing kinds
  with the same field rules as batch transactions (`transfer` may carry an
  `address`). References to networks, endpoints, counters and units are
  validated at compile exactly like batch transactions. `depends_on` names
  earlier operations in the same program only — forward references are
  rejected, so programs are acyclic by construction. The default dependency
  is the immediately preceding operation (strict in-order completion); an
  explicit, possibly empty, `depends_on` enables overlap.
- **Deterministic in-order issue.** Operations issue in program order on
  the sequencer unit: issue occupies the unit for the operation's explicit
  `issue_cycles` and is serialized per unit, so two programs on one unit
  interleave deterministically. Execution of an operation may overlap later
  issues where its dependencies permit. Each issue emits deterministic
  trace events.
- **Unified execution and termination.** Operations expand at compile into
  the same immutable plan as namespaced transactions (`program_id/op_id`)
  and execute through the same registry, bus and pipeline — identical
  contention, cancellation, drain and abort semantics. A terminal-classified
  operation (`route_unreachable` / `capacity_exceeded`) ends its program:
  remaining operations report `dependency_unsatisfied`.
- **Unified results.** Operation spans appear in the existing transactions
  tuple under namespaced identities; an additive optional `programs` tuple
  reports per-program status, reason and start/end. `completion_cycles`
  keeps its business meaning. Batches without programs produce
  byte-identical plans and results.

## Capabilities

### New Capabilities

- `instruction-programs`: Neutral instruction programs — strictly validated
  operation sequences with deterministic in-order issue on a sequencer
  unit, compiled into the unified plan and executed inside the unified
  runtime with program-level result spans.

### Modified Capabilities

None. The batch, plan and result documents gain only optional fields with
defaults; `generic-simulation`, `unified-runtime`, `addressed-memory` and
`dynamic-routing` contracts keep their meanings.

## Impact

- **Simulator:** `configs/schemas/generic_transactions.py` (program and
  operation records, batch non-emptiness rule), `configs/schemas/system_spec.py`
  (optional program plan records), `system_compile.py` (operation expansion
  and validation), `runtime_context.py` (sequencer processes and issue
  accounting), result schemas (optional `programs` tuple, two additive
  trace actions). CLI, adapters and phase-1 entry points are unchanged.
- **JSON and evidence:** additive optional fields only; two new trace
  actions (`issue_acquire`, `issue_release`); no new document kinds.
- **External systems:** none; everything remains synthetic.
- **Out of scope:** out-of-order or speculative issue, register or cache
  models, branch prediction, multi-chip, full collectives, private plugins
  or adapters, and any real device configuration or calibration data.
