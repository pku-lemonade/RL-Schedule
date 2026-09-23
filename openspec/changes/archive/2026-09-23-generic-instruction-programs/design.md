# Design: generic instruction programs

## Context

The unified pipeline executes four transaction kinds with explicit
dependencies, atomic resources, bounded cancellation and deterministic
traces. Instruction-level simulation needs a sequencer model — an execution
unit issuing a stream of operations in order — without forking the runtime
or weakening any correctness invariant from the previous round.

## Decisions

### 1. Programs live in the batch document

A program is workload, not hardware: it belongs in
`generic_transaction_batch` beside `transactions`, `counters` and `timing`.
The field is optional with an empty default, so every existing document is
unchanged and digests of program-free batches are computed exactly as
before. The batch non-emptiness rule moves from "`transactions` non-empty"
to "at least one transaction or program", keeping the honest-result
guarantee (an empty workload still cannot report success).

### 2. Operations expand into plan transactions at compile

Each operation becomes a `PlanTransfer`/`PlanCompute`/`PlanWait`/`PlanSignal`
with identity `program_id/op_id`, in declaration order after the batch
transactions. All reference validation, route resolution, terminal
classification and counter bounds reuse the existing compile paths —
operations are not a parallel transaction system. `NeutralId` already
admits the `/` separator, and plan identity uniqueness covers collisions
between namespaced operations and plain transaction identities.

Dependencies: an operation's `depends_on` may name only earlier operations
of the same program, which makes cycles unrepresentable. An omitted
`depends_on` means "the immediately preceding operation" — strict serial
execution by default. An explicit empty tuple means "no predecessor
dependency", which is how overlap is expressed. Cross-program coordination
uses wait/signal counters, the mechanism built for this.

### 3. In-order issue on the sequencer unit

The runtime spawns one process per program. The process walks its
operations in order: wait for the operation's dependency completions, then
acquire the sequencer unit (atomically, through the registry), hold it for
`issue_cycles` while emitting `issue_acquire`/`issue_release` trace events,
release, then execute the operation body through the existing per-kind
mechanics. Because issue is serialized on the unit, two programs sharing a
sequencer interleave deterministically; because execution happens after
release, a compute operation may occupy the same unit without deadlock, and
independent operations overlap exactly like batch transactions.

The program does not hold its unit between operations: holding it would
deadlock compute operations targeting the sequencer itself and would forbid
useful interleaving, while buying nothing the dependency default does not
already provide.

### 4. Termination mirrors the unified contract

A compile-time-terminal operation (unreachable route, capacity exceeded)
produces an incomplete span with the terminal reason, and every later
operation reports `dependency_unsatisfied` — the program ends. A runtime
interrupt (cycle bound or abort) unwinds the program process exactly like a
transaction process: no later stage runs, nothing reports completion, all
ownership is released. Program spans are derived, not authoritative:
status `complete` iff every operation span completed, end = last operation
end, reason mirroring the failing operation otherwise.

### 5. Additive results only

`generic_simulation_result` gains an optional `programs` tuple of
`GenericProgramSpan` (program identity, unit, status, reason, start, end).
Operation spans ride in the existing `transactions` tuple, so makespan,
utilization and trace accounting are untouched. Two trace actions are added
to the action enumeration; existing traces never contain them.

## Alternatives considered

- **Macro expansion in adapters** (programs desugar into dependency chains
  before compile): no issue occupancy, no program identity in results, and
  every private adapter would reimplement it. Rejected.
- **Program holds the unit for its whole lifetime:** deadlocks compute
  operations on the sequencer unit and serializes all execution; rejected.
- **Out-of-order issue / scoreboarding:** genuinely useful later, but it
  needs hazard rules and renames the in-order guarantee; deferred with the
  rest of the microarchitecture features.

## Risks

- Operation expansion increases plan size linearly; identical digests for
  program-free batches are guarded by a dedicated test.
- Issue occupancy adds queueing on sequencer units; phase-1 through
  correctness-round anchors are unaffected because program-free runs never
  construct program processes (verified by the retained suite).
