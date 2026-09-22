# Progress record

## Part 1: generic system graph (tasks 1.1-1.5, completed 2026-09-22)

Delivered:

- `simulator_detailed/configs/schemas/generic_graph.py`: strict version-1
  `generic_system_graph` document (`extra="forbid"`, frozen) covering nodes,
  ports, links, networks, DMA endpoints, execution units, memory resources
  and static routing tables. All identifiers are neutral: a case-insensitive
  forbidden-token check rejects real device/vendor vocabulary. Validation
  rejects unknown fields, duplicate identities, dangling references, empty
  networks, occupied port halves, non-contiguous or node-revisiting routes,
  and execution units on non-compute nodes. No field carries a default.
- `simulator_detailed/generic_graph.py`: `normalize_generic()`,
  `generic_digest()`, `topology_from_generic()` and the
  `GenericSystem(Topology)` subclass. The adapter emits a `CanonicalTopology`
  and reuses `Topology.compile`; the subclass adds neutral views (network
  membership, endpoint inventories, memory resources, per-network static
  route tables) and a deterministic `generic_system_inspection` export.
  Digests are input-order independent.
- `simulator_detailed/replay_topology.py`: additive `--inspect` dispatch for
  `kind: generic_system_graph`; canonical/legacy/profile branches unchanged.
- `simulator_detailed/configs/generic_graphs/synthetic_grid_2d.json`: fully
  synthetic acceptance fixture — 6 nodes (3 compute, 2 memory, 1 transit),
  2 networks (14 alpha links, 6 beta links), 2 DMA endpoints, 3 execution
  units, 2 memory resources with service endpoints, 4 static routes.
- `simulator_detailed/tests/test_generic_graph.py`: 15 contract tests,
  including the acceptance connectivity checks and order-independence.
- `simulator_detailed/pyrightconfig.phase2.json`: the two new production
  modules added to strict coverage.

Executed verification (this host, 2026-09-22, Python 3.12.12, pydantic
2.13.5):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_generic_graph -v
15 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 628 tests in 221.807s — OK (skipped=1, optional torch/PyG check).
Baseline before this change was 613 tests; the 15 new tests account for the
increase and every pre-existing test still passes unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/generic_graph.py simulator_detailed/generic_graph.py simulator_detailed/replay_topology.py simulator_detailed/tests/test_generic_graph.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-simulator-phase-1 --strict --no-interactive
Change 'generic-simulator-phase-1' is valid. (The pre-existing
"Rules for 'design'..." warning from openspec/config.yaml remains as recorded
in the handoff; it is unrelated to this change.)

git diff --check
Passed with no output.
```

Source identities (SHA-256):

| File | SHA-256 |
| --- | --- |
| `configs/schemas/generic_graph.py` | `755acdc5ec4aa2d094c664d81f6f6670f1d6954a72a876d1c091ff184f8e0d75` |
| `generic_graph.py` | `f8d56b732ff2c1794af00d4ea87c7473863b5b539e43e785832c52cc3bad6413` |
| `configs/generic_graphs/synthetic_grid_2d.json` | `0b383c53369ca29619e114497206f4910f06be2c5c1086d231db18d11ea049be` |
| `tests/test_generic_graph.py` | `1233d9235f955928fcfe7caea483e0b03c6cb1a883e974e1c66242a4ee2f1e80` |
| `replay_topology.py` | `61122d5dfb170cfbbcec76a8ad19c461fde66c2f2e952bee1fbd1b6d813b1cff` |
| `pyrightconfig.phase2.json` | `4b5c7e6cf62f05b41f2b6ae74b4068eaf4f887e1f53aa99c59306c366a7bea70` |

Naming note: the compiled subclass is `GenericSystem(Topology)` (the schema
document keeps the `GenericSystemGraph` name, mirroring
`CanonicalTopology` → `Topology`); design.md wording updated accordingly.

## Part 2: four-kind unified transaction runtime (tasks 2.1-2.6, completed 2026-09-22)

Delivered:

- `simulator_detailed/configs/schemas/generic_transactions.py`: strict
  version-1 `generic_transaction_batch` admitting exactly `transfer`,
  `compute`, `wait` and `signal` (discriminated union on `kind`, shared
  `GenericTransactionBase` envelope with explicit `depends_on` and
  `start_cycles`). Undeclared counters, unknown/cyclic dependencies,
  transfers on untimed networks, unknown kinds, extra fields and device-era
  identifiers are rejected before simulation. Result records
  (`generic_simulation_result` v1) landed with the runtime because execution
  output needed them: makespan, per-transaction spans with per-hop
  serialization windows, per-resource busy/utilization and final counters.
- `simulator_detailed/generic_runtime.py`: `GenericRuntime` composing the
  `GenericSystem` graph substrate with SimPy. Transfers walk the declared
  static route store-and-forward: per-link FIFO buffer credits, a single
  serializer per link, byte-proportional serialization, hop propagation and
  delayed credit return. Compute holds an exclusive execution-unit resource
  for its configured duration. Signals add counter deltas; waits block on a
  re-armed counter event and complete in the cycle the threshold is reached
  (signal-first). Incomplete transactions are classified
  `cycle_limit`/`dependency_unsatisfied`. The link kernel follows the
  `virtual_channel.py` discipline with neutral identity types because the
  existing kernel's identities are torus-bound record families (design.md
  decision 1 updated).
- `simulator_detailed/tests/test_generic_runtime.py`: 20 tests including the
  acceptance suite — same-link queueing with non-overlapping serialization
  windows (exact 33 vs 43 cycles), disjoint-link overlap, compute/transfer
  overlap, wait completing exactly at the enabling signal's cycle,
  DMA-to-memory transfer, dependency ordering, incomplete classification,
  deterministic repeat runs and admission rejections.

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_generic_runtime -v
20 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 648 tests in 218.075s — OK (skipped=1, optional torch/PyG check).
628 before this part; +20 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/generic_transactions.py simulator_detailed/generic_runtime.py simulator_detailed/tests/test_generic_runtime.py
All checks passed (three import-format findings auto-fixed with --fix and re-verified).

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-simulator-phase-1 --strict --no-interactive
Change 'generic-simulator-phase-1' is valid.

git diff --check
Passed with no output.
```

Source identities (SHA-256):

| File | SHA-256 |
| --- | --- |
| `configs/schemas/generic_transactions.py` | `fdf04e730e5eac3170a40de0e9ce1419de997ee105618d1017c2419b3636eef9` |
| `generic_runtime.py` | `337452b15e47b45e2e4a7198e012596a0ee458d61af0a030afb2fcc14c02aa96` |
| `tests/test_generic_runtime.py` | `635cf60b81b9621ed98d34d615140ac0e655ee898e299f841cd35f2840cc5abc` |
| `pyrightconfig.phase2.json` | `438d5610b45e3db5f0aa62e25f789d4de294b521690c0cf3e3fc3b88cb1f4c80` |
