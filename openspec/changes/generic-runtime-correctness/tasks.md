## 1. Regression tests with pre-fix evidence

- [x] 1.1 Add `tests/test_runtime_correctness.py` covering: atomic acquisition never blocks single-resource acquirers, invalid/duplicate sets rejected before any request, cancellation mid-wait leaves nothing, interrupt propagation through every memory stage, simultaneous port timeouts, bounded cleanup after the cycle bound, background drain with final `credit_release` trace rows, runtime fault aborts honestly, and endpoint network membership for routes and transfers (static and dynamic).
- [x] 1.2 Run the new suite against the unfixed code, record the failing cases verbatim in `progress.md` as the pre-fix baseline.

## 2. Atomic acquisition and runtime lifecycle

- [x] 2.1 Rework `ResourceRegistry`: upfront set validation in `acquire_all`, atomic check-and-grant with a deterministic release broadcast, registry-mediated `release`/`release_all` notifying waiters, and register-at-creation requests in `_acquire`.
- [x] 2.2 Rework `_memory_service`: remove interrupt swallowing, atomic bank+channel acquisition, full cleanup on every exit; simplify `_transfer` accordingly.
- [x] 2.3 Rework `run()`: business / bounded cancellation / background drain phases, all-released invariant check, and abort-with-cleanup on runtime exceptions.
- [x] 2.4 Run focused tests plus strict Pyright and scoped Ruff; update `progress.md`; commit Part 2.

## 3. Endpoint network membership

- [x] 3.1 Reject static routes naming endpoints not attached to the route's network in graph validation; reject transfers naming endpoints not attached to the transfer's network in `compile_system`, for static and dynamic policies.
- [x] 3.2 Run focused membership tests plus strict Pyright and scoped Ruff; update `progress.md`; commit Part 3.

## 4. Full verification and delivery

- [x] 4.1 Re-run the complete detailed suite, strict Pyright, the explicit affected Ruff scope, `git diff --check` and strict OpenSpec validation; record exact counts and the test environment.
- [x] 4.2 Update `docs/generic_simulation.md`, finalize `progress.md`, `delivery.md` and `delivery-identities.json` linking requirements to code/tests, and commit the final validated part locally without pushing or archiving.
