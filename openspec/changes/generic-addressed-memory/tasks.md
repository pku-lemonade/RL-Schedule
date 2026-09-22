## 1. Hierarchy and address schemas with compilation

- [x] 1.1 Add strict optional `GenericMemoryHierarchy` (banks, stripe, latency, named ports with command cycles, named channels with rates) on memory resources and optional `address` on transfers; verify unknown fields, duplicate port/channel identities, dangling channel references and non-positive values fail validation.
- [x] 1.2 Compile hierarchies into plan resources (bank/port/channel serializers with stable neutral IDs and deterministic stripe mapping) and validate addressed transfers: memory-side rule, hierarchy-required rule and capacity range rule fail at compile as structural errors.
- [x] 1.3 Verify flat memories and unaddressed transfers compile to byte-identical phase-2 plans; run focused compile tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record outcomes in `progress.md` and commit Part 1.

## 2. Memory service in the unified runtime

- [x] 2.1 Implement the service stage in `RuntimeContext`: port command issue, sorted bank/channel data service, read-before-departure and write-after-arrival semantics, full accounting and release on interruption.
- [x] 2.2 Report memory bank/port/channel usage kinds with busy, queue waits and utilization; keep result validators and phase-1/2 outputs unchanged.
- [x] 2.3 Run focused runtime tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 2.

## 3. Public acceptance, documentation and delivery

- [ ] 3.1 Add the independent public suite: different-bank overlap, same-bank serialization, single-port command serialization, channel-rate service bounds, read/write completion order, compile rejection of illegal addresses, untouched flat behavior and byte-identical repeats.
- [ ] 3.2 Re-run the complete detailed suite, strict Pyright, the explicit affected Ruff scope, `git diff --check` and strict OpenSpec validation; record exact counts.
- [ ] 3.3 Update `docs/generic_simulation.md`, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking AM requirements to code/tests, and commit the final validated part locally without pushing or archiving.
