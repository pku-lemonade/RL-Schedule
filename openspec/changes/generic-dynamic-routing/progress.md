# Progress record

## Parts 1-2: routing policies, compilation and hop selection (tasks 1.1-2.3, completed 2026-09-22)

Validated and committed as one unit (runtime selection consumes the compiled
tables).

Delivered:

- `configs/schemas/generic_graph.py`: optional network `routing`
  (`static_table` default / `shortest_path` / `adaptive`); dynamic networks
  declaring static routes fail validation.
- `configs/schemas/system_spec.py` + `system_compile.py`: dynamic plan
  records (links with effective timings, per-destination BFS distance
  tables, per-transfer effective policy and endpoint nodes) compiled purely;
  unreachable dynamic pairs classify as `route_unreachable`; static networks
  emit no dynamic tables and identical digests.
- `runtime_context.py`: distance-reducing next-hop selection for both
  policies (lexicographic tie-break for `shortest_path`; credit pressure =
  users + pending with link tie-break for `adaptive`), shared `_cross_link`
  mechanics with static routes, `route_select` trace events, span hop
  recording; distance strictly decreases every hop so livelock is
  structurally impossible.
- `tests/test_dynamic_routing.py`: 9 synthetic cases — deterministic
  shortest path with exact times and distance tables, adaptive congested
  branch avoidance, byte-identical adaptive repeats, unreachable
  classification, static-route-on-dynamic rejection, no dynamic tables for
  static networks, and dynamic path composed with addressed memory write.

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_dynamic_routing -v
9 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 718 tests in 232.210s — OK (skipped=1, optional torch/PyG check).
709 before this change; +9 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <all six new/changed modules and tests>
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-dynamic-routing --strict --no-interactive
Change 'generic-dynamic-routing' is valid.

git diff --check
Passed with no output.
```
