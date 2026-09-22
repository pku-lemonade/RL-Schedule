## 1. Generic system graph configuration

- [x] 1.1 Add strict version-1 `generic_system_graph` schemas for nodes, ports, links, networks, DMA endpoints, execution units, memory resources and static routing tables under neutral names; verify unknown fields, duplicate identities, dangling references, empty networks and device-derived vocabulary fail validation, and JSON round trips preserve exact typed identities and digests.
- [x] 1.2 Implement `topology_from_generic()` plus a `GenericSystemGraph(Topology)` subclass compiling the generic document into the existing canonical topology machinery; verify legacy/profile adapters, existing digests and unresolved-connectivity behavior remain unchanged.
- [x] 1.3 Expose one unified system-graph object from loading: network membership, endpoint/DMA/execution-unit/memory-resource inventories, per-network static route tables and content digests; verify deterministic export independent of input record order.
- [x] 1.4 Add a fully synthetic two-dimensional acceptance fixture with nodes, links, two networks, DMA endpoints and memory resources; verify the graph loads and every declared adjacency and both networks' member links check out.
- [x] 1.5 Run the focused graph/adapter tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record actual results and source/fixture identities in `progress.md`, then commit Part 1 before continuing.

## 2. Four-kind unified transaction runtime

- [x] 2.1 Add strict version-1 `generic_transaction_batch` schemas admitting exactly `transfer`, `compute`, `wait` and `signal` records with explicit dependencies; verify unknown kinds, unbounded work, undeclared counters and device-era unit names are rejected before simulation.
- [x] 2.2 Implement `transfer` execution composing static next-hop routing, link serialization and the bounded lane/credit kernel, including DMA-initiated transfers between arbitrary endpoints and memory resources; verify queueing order and byte-exact accounting on contested links.
- [x] 2.3 Implement `compute` execution occupying a declared execution unit for a configured duration; verify compute overlaps unrelated transfers and contends correctly for shared execution resources.
- [x] 2.4 Implement counter-based `wait`/`signal`: signals add declared deltas, waits complete at threshold with deterministic signal-first ordering; verify a wait never completes before its enabling signal and unreachably-thresholded waits fail with explicit reasons.
- [x] 2.5 Add the synthetic acceptance suite: two transfers on one shared link queue with non-overlapping occupancy, two transfers on disjoint links overlap, a compute and a transfer overlap, and a wait completes only after its required signals.
- [x] 2.6 Run the focused runtime tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` with measured outcomes and commit Part 2 before continuing.

## 3. Versioned simulation results

- [ ] 3.1 Add strict version-1 `generic_simulation_result` records: makespan completion, per-transaction start/end, per-resource busy/utilization aggregates and incomplete/rejected transactions with machine-readable reason codes; verify an empty transaction set cannot report success.
- [ ] 3.2 Add the public replay entry point dispatching the new kinds alongside existing v1/v2 loaders; verify existing replay results, exit codes and fixture hashes remain byte-identical.
- [ ] 3.3 Add negative-path tests: unsatisfiable waits, unreachable routes and exhausted capacities appear as incomplete transactions with correct reasons, never as passes; verify corrupted or partial outputs cannot be relabeled.
- [ ] 3.4 Run the focused result/CLI tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 3 before continuing.

## 4. External adapter boundary

- [ ] 4.1 Define the public `GenericInputAdapter` abstract interface returning validated `generic_system_graph` and `generic_transaction_batch` documents; verify no dynamic discovery, no shell strings and no network access are introduced.
- [ ] 4.2 Implement the independently designed synthetic example adapter plus a launch example driving the Part 1 acceptance system end to end; verify its format shares no vocabulary with any private source.
- [ ] 4.3 Add the privacy guard test scanning the new public modules, fixtures and documentation for forbidden device/vendor/private tokens; verify seeded violations are detected.
- [ ] 4.4 Run the focused adapter/guard tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 4 before continuing.

## 5. Consolidated verification and delivery

- [ ] 5.1 Re-run the complete detailed unittest suite and the existing replay fixtures; verify legacy/profile/Wormhole inputs retain byte-identical results and legacy consumer guards reject the new document kinds.
- [ ] 5.2 Run strict Pyright, the explicit affected/predecessor Ruff scope, `git diff --check` and strict OpenSpec validation; record exact discovered/pass/fail/skip counts without relabeling prior runs.
- [ ] 5.3 Write the user documentation under `simulator_detailed/docs/`, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking GS-G01..GS-A03 to code, tests and fixtures, and commit the final validated part locally without pushing or archiving.
