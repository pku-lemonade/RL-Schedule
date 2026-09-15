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

## Part 2: topology binding, route oracle and resource order

Tasks 2.1–2.5 are delivered in the commit containing this section. The binder and
static dependency compiler are in `simulator_detailed/torus.py` and
`simulator_detailed/torus_dependencies.py`; focused evidence is in
`simulator_detailed/tests/test_torus.py`.

The profile binding preserves 120 tiles, 240 fabric routers, 480 directed links,
240 attachments, the worker mask and inventory aliases. It does not create compute,
memory or NIU services. A harvested worker retains its router but cannot initiate an
admitted endpoint. NoC0 and NoC1 route through raw XY/YX coordinates respectively;
the independent profile oracle checks 28,800 ordered pairs, including the 4/18-hop
router example. The small canonical fixture checks shifted datelines and unavailable
edge rejection. Static request/response lane, local-channel and packet-owner
dependencies are topologically sorted with explicit rank checks. Existing version-1
explicit-route cycle rejection remains covered by the legacy transport tests.

Validation for this part completed before the checkpoint commit:

```bash
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 101 tests pass
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/torus.py simulator_detailed/torus_dependencies.py simulator_detailed/tests/test_torus.py simulator_detailed/configs/schemas/torus_replay.py simulator_detailed/torus_contract.py simulator_detailed/torus_records.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
# Clean
```

Runtime VC allocation, cut-through forwarding, response generation and CLI execution
remain pending parts 3–7.
