# Progress record

## Part 1: program schemas and pure compilation (tasks 1.1-1.3, 2026-09-23)

Delivered:

- `configs/schemas/generic_transactions.py` — `GenericOpBase` with
  `issue_cycles` and optional `depends_on` (`None` = previous operation);
  `GenericOpTransfer`/`GenericOpCompute`/`GenericOpWait`/`GenericOpSignal`;
  the `GenericInstructionOp` discriminated union; `GenericInstructionProgram`
  (`program_id`, `unit_id`, `start_cycles`, strict op identity uniqueness,
  earlier-operation-only dependencies). The batch schema now accepts an
  optional `programs` tuple, enforces program/transaction identity
  disjointness, requires at least one transaction or program, and validates
  operation references against declared timing networks and counters.
  `GenericProgramSpan` plus the additive `programs` result tuple and the
  `issue_acquire`/`issue_release` trace actions complete the contract.
- `configs/schemas/system_spec.py` — `PlanProgramOp` and
  `PlanInstructionProgram` plan records with an optional `programs` tuple on
  `PlanContent`.
- `system_compile.py` — per-kind plan builders shared between batch
  transactions and program operations; operations expand into namespaced
  plan transactions (`<program_id>/<op_id>`) with resolved dependencies
  (default: previous operation), terminal classifications and counter upper
  bounds extended to in-program signals; every operation reference is
  validated; compilation stays pure (no SimPy, no global state).

Public tests: `tests/test_instruction_programs.py` — 17 independently
designed synthetic compile-level cases over an original three-node line
graph (validation 9, compilation 8).

Validation on this host (2026-09-23):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_instruction_programs
Ran 17 tests in 0.010s — OK

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 750 tests in 218.168s — OK (skipped=1)

.venv/bin/python -m pyright --pythonpath .venv/bin/python \
    --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations

.venv/bin/ruff check simulator_detailed/configs/schemas/generic_transactions.py \
    simulator_detailed/configs/schemas/system_spec.py \
    simulator_detailed/system_compile.py \
    simulator_detailed/tests/test_instruction_programs.py
All checks passed!

git diff --check — clean

openspec validate generic-instruction-programs --strict
Change 'generic-instruction-programs' is valid
```

Program-free batches compile to byte-identical plans: the `programs` tuple
defaults to empty and the digest covers the same plan content as before
(covered by the phase-2 determinism tests, still green above).


## Part 2: sequencer runtime (tasks 2.1-2.3, 2026-09-23)

Delivered in `runtime_context.py`:

- `RuntimeContext._program` — one sequencer process per program.
  Dependency-gated issue: an operation becomes its own registered SimPy
  process (via the new `_dispatch`, which routes to the unchanged per-kind
  mechanics) once its dependencies have completed, so independent
  operations of one program overlap while the default previous-operation
  dependency keeps a serial chain. The sequencer unit is held only for
  `issue_cycles` per operation (`issue_acquire`/`issue_release` trace
  events, busy-cycle and service accounting on the unit), so a program
  never occupies its unit while waiting on data, dependencies or
  counters; issue requests on one unit serialize in deterministic order.
- Termination: a terminal operation ends the program without running and
  later operations never issue, reporting `dependency_unsatisfied`;
  interrupts unwind the program at its current yield point with full
  release, and spawned operation processes are covered by the shared
  cancellation/abort paths through the process registry.
- `run()` spawns batch transactions and program processes, skipping
  program-expanded operations as standalone processes; program spans are
  derived from operation spans (complete iff every operation completes;
  start = first operation start; end = last operation end; an incomplete
  program mirrors the reason of the operation that stopped it) and exposed
  through the additive `programs` result tuple.

Public tests: `tests/test_instruction_programs.py` gains
`TestProgramRuntime` — 6 cases: serial-chain timing, explicit overlap,
issue-occupancy mutual exclusion on a shared unit (trace invariant),
cross-program wait/signal, terminal stop with derived program reason, and
cycle-limit interruption with full release (cancel trace plus incomplete
spans).

Validation on this host (2026-09-23):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_instruction_programs
Ran 23 tests in 0.018s — OK

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 756 tests in 219.743s — OK (skipped=1)

.venv/bin/python -m pyright --pythonpath .venv/bin/python \
    --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations

.venv/bin/ruff check simulator_detailed/runtime_context.py \
    simulator_detailed/tests/test_instruction_programs.py
All checks passed!

git diff --check — clean

openspec validate generic-instruction-programs --strict
Change 'generic-instruction-programs' is valid
```


## Part 3: public acceptance, documentation and delivery (tasks 3.1-3.3, 2026-09-23)

Public suite: `tests/test_instruction_programs.py` now covers all ten
acceptance categories across its classes — validation and compile
rejections (TestProgramValidation/TestProgramCompilation, Part 1), serial
default ordering, explicit overlap, issue contention between two programs
on one unit, cross-program wait/signal, terminal stop and cycle-limit
cleanup (TestProgramRuntime, Part 2), and the new TestProgramAcceptance:
issue contention with batch transactions (exact cycle outcomes), addressed
memory composition inside a program (exact network + service windows),
byte-identical repeated runs (full result JSON equality) and program-free
digest stability (absent vs empty `programs` key yields identical plan and
spec digests; program-free results carry an empty `programs` tuple).
TestProgramNeutralVocabulary covers IP-07: forbidden tokens are rejected in
program and operation identities, the new public modules scan clean, and a
seeded violation in a copied module is detected.

Documentation: `simulator_detailed/docs/generic_simulation.md` gains the
"Instruction programs" section. Delivery recorded in `delivery.md` and
`delivery-identities.json`.

Final validation on this host (2026-09-23):

```text
.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 763 tests in 216.869s — OK (skipped=1)

.venv/bin/python -m pyright --pythonpath .venv/bin/python \
    --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations

.venv/bin/ruff check simulator_detailed/runtime_context.py \
    simulator_detailed/system_compile.py \
    simulator_detailed/configs/schemas/generic_transactions.py \
    simulator_detailed/configs/schemas/system_spec.py \
    simulator_detailed/tests/test_instruction_programs.py
All checks passed!

git diff --check — clean

openspec validate generic-instruction-programs --strict
Change 'generic-instruction-programs' is valid
```
