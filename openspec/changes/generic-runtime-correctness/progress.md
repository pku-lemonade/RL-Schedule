# Progress record

## Part 1: regression suite with pre-fix evidence (tasks 1.1-1.2, 2026-09-23)

`tests/test_runtime_correctness.py` — 15 independently designed synthetic
cases in three classes: atomic acquisition (6), memory cancellation and
drain (5), endpoint network membership (4).

Pre-fix run against the unfixed code at `d2ac69c` (this host, 2026-09-23):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_runtime_correctness -v
Ran 15 tests in 0.022s — FAILED (failures=13, errors=1)
```

- ok: `test_same_network_transfers_still_pass` (positive control).
- Atomicity: `test_waiting_set_holds_nothing` — a single-resource acquirer
  blocked behind a partial holder; `test_unknown_identity_rejected_*` —
  `all_released()` False (earlier grant stranded by a mid-set ValueError);
  `test_duplicate_identity_rejected` / `test_empty_set_rejected` — accepted
  silently; `test_memory_waiter_holds_no_bank` — `z_free` ended at 418.0
  instead of 108.0 (bank_0 held while waiting for ch1);
  `test_cancel_while_waiting_leaves_nothing` — ERROR: Interrupt escaped the
  harness driver (test-side catch added afterwards).
- Cancellation/drain: `test_interrupt_during_command_is_incomplete`,
  `test_interrupt_during_data_wait_and_service`,
  `test_simultaneous_port_timeout_leaves_nothing` — results reported
  `complete` after the cycle bound (swallowed interrupts);
  `test_delayed_credits_drain_before_return` — no final `credit_release`
  in the trace and the registry still held credits at return;
  `test_runtime_fault_aborts_and_cleans_up` — registry not released after
  the injected fault.
- Membership: all three cross-network cases ran or compiled without
  `ValueError`.

## Parts 2-3: fixes (tasks 2.1-3.2, completed 2026-09-23)

Validated together and committed as `393e58e`.

Delivered:

- `runtime_context.py`:
  - `ResourceRegistry.acquire_all` validates the full identity set before
    any request exists (empty/duplicate/unknown rejected) and grants
    atomically: all members at one time step or none; waiters hold nothing
    and wake on a deterministic registry release broadcast.
  - Registry-mediated `release`/`release_all`: normal releases, interrupt
    cleanup and pending-request cancellation share one path that notifies
    waiters; `_acquire` registers requests at creation.
  - `_memory_service` drops interrupt swallowing, acquires bank+channel
    atomically and cleans up on every exit; `_transfer` simplified
    accordingly.
  - `run()` gains explicit phases: business (`drained` or `max_cycles`),
    bounded cancellation cleanup (plan-order interrupts, all-processes
    ended, finite backstop), background drain (delayed credit returns run
    to completion). The all-released invariant is checked before any
    result is built; a runtime exception cancels in-flight work, performs
    bounded cleanup and raises a chained `RuntimeError` — no success
    result after a failure.
- `configs/schemas/generic_graph.py`: static routes naming endpoints not
  attached to the route's network are rejected at validation.
- `system_compile.py`: transfers naming endpoints not attached to the
  transfer's network are rejected at compile, static and dynamic alike.
- Existing fixture rewiring (same intent, new rule): five `t_bad`
  constructions in `tests/test_generic_results.py` and
  `tests/test_unified_compile.py` now target `eu_10` (net_alpha, no
  declared route) instead of cross-network `dma_b`; every assertion is
  unchanged, and `route_unreachable` coverage is preserved.

Post-fix verification (this host, 2026-09-23):

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_runtime_correctness -v
Ran 15 tests — OK (all 13 former failures and the former error pass).

.venv/bin/python -m unittest discover -s simulator_detailed/tests
Ran 733 tests in 234.072s — OK (skipped=1, optional torch/PyG check).
718 before this change; +15 new, every pre-existing test passing.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <all six new/changed modules and tests>
All checks passed.

git diff --check
Passed with no output.
```

Test environment: Linux 6.1.0-35-amd64 x86_64, Python 3.12.12 (`.venv`),
SimPy 4.1.2, ruff 0.16.7, pyright (phase-2 project scope).

## Part 4: documentation and delivery (tasks 4.1-4.2, completed 2026-09-23)

- `docs/generic_simulation.md` updated: endpoint/network membership rules,
  atomic acquisition and interrupt propagation, bounded run phases, drain
  semantics and honest abort.
- `progress.md`, `delivery.md` and `delivery-identities.json` finalized;
  tasks checked off; strict OpenSpec validation re-run.
