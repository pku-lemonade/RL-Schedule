## 1. SystemSpec and pure compilation

- [x] 1.1 Add strict version-1 `system_spec` schemas composing the existing graph and batch documents plus `immutable_plan` records (resources, resolved transfers with hop timings and terminal reasons, computes, waits with reachability bounds, signals, counters); verify unknown fields, unknown kinds and malformed references fail validation.
- [x] 1.2 Implement pure `compile_system(spec) -> ImmutablePlan`: reference/entity/port/attachment/fabric/transaction validation, non-negative capacity checks, route and timing resolution, terminal classification, counter bounds and deterministic digests; verify identical inputs yield identical plans and digests, reordered graph records yield the same digest, and no SimPy object or global state is touched.
- [x] 1.3 Verify compile-time failures: unknown endpoints, execution units, timing networks and override links raise before any runtime object exists.
- [x] 1.4 Run focused compile tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record outcomes in `progress.md`, then commit Part 1.

## 2. RuntimeContext, ResourceRegistry and EventBus

- [x] 2.1 Implement `ResourceRegistry` with stable IDs, single construction per physical resource, acquire/wait/release with queue accounting, sorted multi-resource acquisition and full release on completion, failure or cancellation.
- [x] 2.2 Implement `EventBus` with named events, counted events, wait conditions, deterministic global publish ordering and declared-bound unsatisfiable-wait reporting.
- [x] 2.3 Implement `RuntimeContext` executing all four transaction kinds with phase-1-identical mechanics, deterministic trace collection, per-transaction wait times, per-resource queue waits, error listing, cancellation of survivors at the cycle bound and result assembly including the plan digest.
- [x] 2.4 Convert `run_generic_batch`/`GenericRuntime` into shims through the pipeline; extend legacy consumer guards to the new document kinds; verify every phase-1 test passes unchanged.
- [x] 2.5 Run focused runtime tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 2.

## 3. Public acceptance, documentation and delivery

- [ ] 3.1 Add the independent public suite: parallel transfer/compute, deterministic queueing, wait-after-signal, ring transfers without deadlock, release-after-cancellation, compile rejection of illegal references, explicit never-satisfiable-wait reporting, byte-identical repeated runs, and the unchanged phase-1 suite.
- [ ] 3.2 Re-run the complete detailed suite, strict Pyright, the explicit affected Ruff scope, `git diff --check` and strict OpenSpec validation; record exact counts without relabeling prior runs.
- [ ] 3.3 Update `docs/generic_simulation.md` with the unified pipeline, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking UR requirements to code/tests, and commit the final validated part locally without pushing or archiving.
