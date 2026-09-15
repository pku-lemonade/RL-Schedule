# Incremental delivery

## Part 1: versioned transport contracts and admission

Tasks 1.1–1.5 are delivered in the commit containing this section, titled
`feat: add versioned torus transport contracts`, based on `c1655ba`.
See [implementation scope and evidence](../../../simulator_detailed/docs/torus_transport.md).
The remaining 30 tasks are not implemented; this is not completion of TR-D01..10.

| Requirement subset | Implemented evidence |
| --- | --- |
| TR-D01 / D09 | Strict version-2 configuration, explicit binding assumptions and role allowlist; source validation; version-1 CLI rejection of version 2; graph/endpoint binding remains pending |
| TR-D05 / D07 | Finite response-fixture and slowdown input constraints only; no response or failure execution |
| TR-D06 | Explicit units/capacities, profile reference/override evidence, exact serialization admission and finite clock conversion; no new timing execution |
| TR-D08 | Immutable envelope/plan/result records, structural identity/count/time/credit-drain checks; no runtime event production or exhaustive state verification |

Validation on 2026-09-15:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_torus_contract
# 11 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 95 tests pass
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/python -m ruff check simulator_detailed/configs/schemas/torus_replay.py simulator_detailed/torus_contract.py simulator_detailed/torus_records.py simulator_detailed/tests/test_torus_contract.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
git diff --cached --check
# Clean before commit
```

Strict type coverage includes all three new production modules. Source/timing
assumptions and unsupported functionality are disclosed in the linked report.
No new hardware measurement, external simulator comparison or ML execution is
claimed. Next: part 2, canonical torus binding and independent routing/dependency
validation. No pending parent specifications have been synced or archived.
