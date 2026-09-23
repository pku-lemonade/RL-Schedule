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
