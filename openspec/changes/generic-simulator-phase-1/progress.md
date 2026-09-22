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

## Part 3: versioned simulation results (tasks 3.1-3.4, completed 2026-09-22)

Delivered:

- Result contract hardening in `configs/schemas/generic_transactions.py`:
  span reasons extended with `route_unreachable` and `capacity_exceeded`;
  per-span consistency rules (complete spans require times and reason
  completed; incomplete spans carry no times and never claim completion;
  hops only on transfers) and result-level consistency rules (status/reason
  must match span outcomes, `completion_cycles` must equal the latest span
  end, an empty transaction set cannot report anything). Corrupted or
  partial outputs fail revalidation.
- `generic_runtime.py`: two-tier honesty. Malformed input (unknown
  endpoints/units, bad timing references) raises before simulation; viability
  failures become incomplete result spans — missing static routes classify as
  `route_unreachable`, oversized memory payloads as `capacity_exceeded`, and
  their dependents as `dependency_unsatisfied`. Terminal transactions are
  excluded from the drain condition so runs stop promptly.
- `simulator_detailed/replay_generic.py`: public CLI
  `python -m simulator_detailed.replay_generic --batch <doc>` with compute
  replay exit conventions (0 complete / 1 incomplete / 2 invalid); file
  batches resolve `graph_path` relative to the batch document.
- `configs/generic_transactions/acceptance_batch.json`: shared-link queueing,
  compute overlap and wait/signal acceptance batch over the 2D fixture.
- `tests/test_generic_results.py`: 15 negative-path, integrity and CLI tests.

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_generic_runtime simulator_detailed.tests.test_generic_results simulator_detailed.tests.test_generic_graph
50 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 663 tests in 218.720s — OK (skipped=1, optional torch/PyG check).
648 before this part; +15 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/generic_transactions.py simulator_detailed/generic_runtime.py simulator_detailed/replay_generic.py simulator_detailed/tests/test_generic_runtime.py simulator_detailed/tests/test_generic_results.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-simulator-phase-1 --strict --no-interactive
Change 'generic-simulator-phase-1' is valid.

git diff --check
Passed with no output.
```

CLI observations: the acceptance batch exits 0 with t_a ending at 33 cycles
and w_1 at 10; an unsatisfiable-wait batch exits 1; wrong-kind, malformed and
missing inputs exit 2. Existing v1/v2 replay loaders and exit codes are
untouched (full suite green).

Source identities (SHA-256):

| File | SHA-256 |
| --- | --- |
| `configs/schemas/generic_transactions.py` | `243dee2bac4b1584da14bdf8b8364d0ce8409165f46cf5590d1680447cbf0f3e` |
| `generic_runtime.py` | `a11b874d5ec4272dae74b00074410bae061d08515339c80e851df714f1c001da` |
| `replay_generic.py` | `75539bc1333814ec6f4fa8df3fcf43e9edf10ad7bf9aeef3accc353df2455e44` |
| `configs/generic_transactions/acceptance_batch.json` | `25235b04ff286913efe8b9fa6fb236949c172b471590a0438fb884bf3b6c069f` |
| `tests/test_generic_results.py` | `99abceea1ea513b5f50a235e72ab5062ea05e250a6e5bbaa69d5563e3b1e92eb` |
| `tests/test_generic_runtime.py` | `9bb8606ab25d0abd80965bfc9760e00e049f671b96b34704395a9484be35b201` |

## Part 4: external adapter boundary (tasks 4.1-4.4, completed 2026-09-22)

Delivered:

- `simulator_detailed/generic_adapter.py`: the public `GenericInputAdapter`
  ABC returning `generic_system_graph`/`generic_transaction_batch` documents,
  `run_generic_adapter()` which revalidates both documents through a
  dump/parse round trip before compile/execute (blocking
  validation-bypassing constructors), and `scan_forbidden_tokens()`, the
  case-insensitive guard helper private CI can reuse. No dynamic discovery,
  dynamic imports, shell evaluation or network access.
- `simulator_detailed/configs/schemas/synthetic_world.py`: the independently
  designed example input format (`synthetic_grid_world` v1) with a
  deliberately different vocabulary (sites/planes/sockets/wires/movers/
  workers/stores/paths/gauges/jobs). Local consistency checks only; deep
  semantics are re-checked by the generic schemas on conversion.
- `simulator_detailed/synthetic_adapter.py`: `SyntheticGridWorldAdapter`, a
  pure total conversion into generic documents.
- `simulator_detailed/configs/generic_adapters/synthetic_world_2d.json`: the
  synthetic encoding of the acceptance 2D system and batch.
- `simulator_detailed/tests/test_generic_adapter.py`: 7 tests — converted
  graph is byte-identical (same canonical digest) to the file fixture, the
  adapter-driven run matches the file-loaded run except declared batch
  identity, boundary revalidation rejects `model_copy`-smuggled invalid
  documents, the guard scan is clean on the public surface and detects a
  seeded violation, and the public modules contain no dynamic-discovery or
  network references.

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_generic_adapter -v
7 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 670 tests in 217.865s — OK (skipped=1, optional torch/PyG check).
663 before this part; +7 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/synthetic_world.py simulator_detailed/generic_adapter.py simulator_detailed/synthetic_adapter.py simulator_detailed/tests/test_generic_adapter.py
All checks passed (one forward-reference quote auto-fixed and re-verified).

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-simulator-phase-1 --strict --no-interactive
Change 'generic-simulator-phase-1' is valid.

git diff --check
Passed with no output.
```

Source identities (SHA-256):

| File | SHA-256 |
| --- | --- |
| `configs/schemas/synthetic_world.py` | `3167592fff6513ddc8da2597106821b60aabf5788e6396aab30287ed4a4a07d4` |
| `generic_adapter.py` | `41ac87d9e7b526406ab330718a710ef91624348f0f3877c3d9205558e2015426` |
| `synthetic_adapter.py` | `0b8bb64247ce9997bb56d4e222e964724f6837371b38a23e857449487134182d` |
| `configs/generic_adapters/synthetic_world_2d.json` | `1ec78e0135d1a3d9f7a22cfb51f679cd390385e46d87658dbac3dce5f6256e98` |
| `tests/test_generic_adapter.py` | `7e223a7984586148068bc204818d756f2f2e310bc164bc6da435cac8fad57402` |
| `pyrightconfig.phase2.json` | `29ee33e4c586fbf042d9d3b7eea8ed22786b8c026263438a87f10205a73a305b` |
