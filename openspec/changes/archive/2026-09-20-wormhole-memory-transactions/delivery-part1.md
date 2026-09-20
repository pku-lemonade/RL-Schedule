# Part 1 delivery: contracts and admission

## Current and expected behavior

The detailed simulator currently has canonical topology/profile inventory and executable bounded topology replay, but no addressed memory replay. Legacy DMA and ScratchpadMemory remain unchanged. This part adds strict memory_replay parsing plus a pre-runtime MemoryPlan.compile boundary. It validates resources, aliases, endpoints, buffer ranges, operation shapes, dependencies, and supported fence modes without creating a SimPy environment.

Canonical graph sources with complete connectivity can produce a deterministic plan record. Hardware profile sources are accepted as configuration data but remain inventory-only when their projected graph is unresolved; compilation rejects them before runtime construction. Source path spelling does not affect the plan digest. Unknown memory modes, invalid packet/service geometry, unavailable bindings, out-of-range buffers, dependency cycles, and posted-write remote-completion fences fail during admission.

## Validation

- .venv/bin/python -m unittest simulator_detailed.tests.test_memory_contracts -v — 5 tests passed.
- .venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json — 0 errors, 0 warnings.
- .venv/bin/ruff check simulator_detailed/configs/schemas/memory_replay.py simulator_detailed/memory_records.py simulator_detailed/memory_plan.py simulator_detailed/tests/test_memory_contracts.py — passed.
- openspec validate wormhole-memory-transactions --strict --no-interactive — passed before implementation edits.

## Explicit limits

MemoryPlan is an admission record only. It does not allocate SimPy resources, route packets, perform memory service, model data values, execute atomics/MMIO, or make a hardware timing claim. Profile connectivity, packet transport, shared capacity/service, transaction execution, local clients, and legacy adapters remain later tasks.
