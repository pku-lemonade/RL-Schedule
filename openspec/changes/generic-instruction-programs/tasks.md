## 1. Program schemas and pure compilation

- [x] 1.1 Add `GenericInstructionProgram`/`GenericInstructionOp` records and an optional `programs` tuple to the batch schema; enforce strict fields, identity uniqueness and disjointness from transaction identities, per-program operation identity uniqueness, earlier-operation-only `depends_on`, and the at-least-one-transaction-or-program rule.
- [x] 1.2 Add optional plan records for programs and expand operations in `compile_system` into namespaced plan transactions with resolved dependencies, terminal classifications and counter bounds; validate every operation reference; identical specs yield identical digests; no SimPy object or global state.
- [x] 1.3 Verify program-free batches compile to byte-identical plans; run focused compile tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record outcomes in `progress.md` and commit Part 1.

## 2. Sequencer runtime

- [x] 2.1 Implement program processes: in-order issue with `issue_cycles` occupancy on the sequencer unit, `issue_acquire`/`issue_release` trace events, operation execution through the existing per-kind mechanics, and program-level `start_cycles`.
- [x] 2.2 Implement termination: terminal operations end the program with `dependency_unsatisfied` followers; interrupts unwind programs with full release; derive program spans and expose the additive `programs` result tuple.
- [x] 2.3 Run focused runtime tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 2.

## 3. Public acceptance, documentation and delivery

- [ ] 3.1 Add the independent public suite: serial default ordering, explicit-overlap, issue contention between two programs on one unit, contention with batch transactions, cross-program wait/signal, terminal-stop semantics, cycle-limit cleanup, compile rejections, addressed-memory composition, byte-identical repeats and program-free digest stability.
- [ ] 3.2 Re-run the complete detailed suite, strict Pyright, the explicit affected Ruff scope, `git diff --check` and strict OpenSpec validation; record exact counts.
- [ ] 3.3 Update `docs/generic_simulation.md`, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking IP requirements to code/tests, and commit the final validated part locally without pushing or archiving.
