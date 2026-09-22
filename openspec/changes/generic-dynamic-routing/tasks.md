## 1. Routing policy schemas and pure compilation

- [ ] 1.1 Add optional `routing` (`static_table`/`shortest_path`/`adaptive`) to networks; verify dynamic networks declaring static routes fail validation and unknown policy values are rejected.
- [ ] 1.2 Add dynamic plan records (per-network links with timings, per-destination BFS distance tables, per-transfer effective policy and endpoint nodes) and compile them purely: reachability classification, deterministic tables and digests, no SimPy object or global state.
- [ ] 1.3 Verify static networks compile to byte-identical plans; run focused compile tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record outcomes in `progress.md` and commit Part 1.

## 2. Hop-by-hop selection in the unified runtime

- [ ] 2.1 Implement distance-reducing next-hop selection for both policies with deterministic tie-breaks, shared link-crossing mechanics with static routes, `route_select` trace events and span hop recording.
- [ ] 2.2 Implement adaptive pressure sampling (credit users plus pending) with deterministic ordering; verify byte-identical repeated adaptive runs.
- [ ] 2.3 Run focused runtime tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` and commit Part 2.

## 3. Public acceptance, documentation and delivery

- [ ] 3.1 Add the independent public suite: deterministic shortest path, adaptive branch avoidance, multi-transfer livelock freedom, unreachable classification, static-route-on-dynamic rejection, untouched phase-1/2/3 behavior and byte-identical repeats.
- [ ] 3.2 Re-run the complete detailed suite, strict Pyright, the explicit affected Ruff scope, `git diff --check` and strict OpenSpec validation; record exact counts.
- [ ] 3.3 Update `docs/generic_simulation.md`, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking DR requirements to code/tests, and commit the final validated part locally without pushing or archiving.
