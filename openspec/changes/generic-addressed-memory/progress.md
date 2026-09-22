# Progress record

## Parts 1-2: hierarchy/address schemas, compilation and contended service (tasks 1.1-2.3, completed 2026-09-22)

Validated and committed as one unit (the runtime stage consumes the plan
records introduced by the schema work).

Delivered:

- `configs/schemas/generic_graph.py`: optional strict `GenericMemoryHierarchy`
  (banks, `stripe_bytes`, `latency_cycles`, named ports with
  `command_cycles`, named channels with `bytes_per_cycle`; unique identities,
  port-channel references checked). Flat memories unchanged.
- `configs/schemas/generic_transactions.py`: optional `transfer.address`;
  `GenericMemoryServiceSpan`; memory usage kinds
  (`memory_bank`/`memory_port`/`memory_channel`); new trace actions
  (`memory_command`/`memory_service_start`/`memory_service_end`). All
  additive with defaults; phase-1/2 documents stay valid.
- `configs/schemas/system_spec.py` + `system_compile.py`: `PlanMemoryService`
  with deterministic stripe mapping (bank/port/channel identities), plan
  resources for banks/ports/channels, and compile-time address rules
  (exactly one memory endpoint, hierarchy required, range within capacity).
- `runtime_context.py`: the memory service stage — port command issue, sorted
  bank/channel data service (`latency + ceil(bytes / channel rate)`),
  read-before-departure and write-after-arrival, per-resource accounting and
  interrupt-safe release. The registry now preserves memory resource kinds;
  a per-hop `queued_cycles` fidelity fix landed alongside.
- `tests/test_addressed_memory.py`: 15 synthetic cases — write/read exact
  timing (24 cycles end-to-end), different-bank overlap, same-bank
  serialization, single-port command serialization, shared-channel data
  serialization, channel-rate bounds, address rejections, deterministic
  mapping and plan resources, flat compatibility and byte-identical repeats.

Executed verification (this host, 2026-09-22):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_addressed_memory -v
15 tests passed; 0 failures; 0 errors.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 709 tests in 220.192s — OK (skipped=1, optional torch/PyG check).
694 before this change; +15 new, every pre-existing test unchanged.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <all six new/changed modules and tests>
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate generic-addressed-memory --strict --no-interactive
Change 'generic-addressed-memory' is valid.

git diff --check
Passed with no output.
```
