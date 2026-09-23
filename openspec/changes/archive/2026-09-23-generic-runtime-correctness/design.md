# Design: generic runtime correctness fixes

## Context

The unified runtime executes all four generic transaction kinds in one
SimPy environment with one `ResourceRegistry` and one `EventBus`. Three
correctness defects survived because no suite probed them:

- `acquire_all` yielded one request at a time and validated identities
  lazily, so partial holds, stranded grants and uncancellable queued
  requests were all reachable.
- `_memory_service` ended with `except simpy.Interrupt: pass`, so an
  interrupted access silently returned `None` and the transfer layer
  continued to later stages and even to `_finish`.
- `run()` stopped at the business-drained event, abandoning delayed
  credit-return processes, and its cancellation path ran the environment
  without a bound, letting interrupt-swallowing stages finish business
  after the cycle limit.
- Graph validation resolved endpoints to nodes globally; neither routes
  nor transfers checked the endpoint's attached network.

## Decisions

### 1. Atomic acquisition via check-and-grant plus a release broadcast

`acquire_all` validates the full identity set first (unknown, duplicate
or empty identities raise before any request object exists), then loops:
if every member serializer has free capacity and an empty queue, request
all members at once and yield their combined event — all grants land in
one time step, so no other process can interleave. Otherwise wait on a
registry-wide release event and retry. A waiter therefore holds nothing,
which is exactly the required semantics; sorted identities keep the
attempt order deterministic.

The release broadcast is a single SimPy event recreated after each
notification, mirroring the existing counter `changed` pattern. Every
release path funnels through the registry (`release` / `release_all`),
so waiters never miss a notification; the single-threaded check-then-yield
order closes the subscription window. Starvation of a large set under
continuous single-resource traffic is theoretically possible but
deterministic and bounded by `max_cycles`; we accept it and document it.

### 2. Requests are registered at creation

`_acquire` now takes the caller's `granted` list and registers the request
before yielding it, matching what `_cross_link` already did. Interrupt
cleanup (`release_all`) releases triggered requests and cancels pending
ones, so nothing stays queued after a cancellation.

### 3. Interrupts propagate; the transaction layer owns the catch

`_memory_service` drops its `except simpy.Interrupt: pass`. Any interrupt —
during port wait, command issue, bank/channel wait or data service —
unwinds through `finally` cleanup and reaches `_transfer`, whose catch ends
the transaction incomplete. Data service acquires bank and channel through
the atomic mechanism, so a cancelled access never holds one while awaiting
the other.

### 4. `run()` becomes three explicit phases

1. **Business:** `env.run(until=AnyOf(drained, timeout(max_cycles)))` —
   unchanged.
2. **Cancellation cleanup (only on timeout):** interrupt every unfinished
   process in plan order (each interrupt is traced as before), then run
   until all business processes have ended, with a finite backstop bound.
   Interrupts now propagate everywhere, so this phase ends at the same
   time step in practice.
3. **Drain (always):** run until every background process — delayed
   credit returns spawned by completed hops — has finished. These are
   plain finite timeouts, so the condition is explicit and always met.

After the phases, the registry must report no holders and no queued
requests; a violation raises `RuntimeError` instead of returning a
dishonest result. An exception in any phase aborts the run: remaining
processes are interrupted, bounded cleanup executes, and a `RuntimeError`
chaining the original cause is raised. A failed run never returns a
result. `completion_cycles` remains the maximum business span end; drain
activity appears only as later trace rows, preserving phase-1 timing
anchors byte-identically.

### 5. Endpoint network membership, enforced at both layers

Graph validation builds the endpoint→network mapping it already
validates for ports and reuses it: a static route may name only endpoints
attached to the route's network. Compilation performs the same check for
transfers — on static and dynamic policies alike — and rejects
cross-network references with a `ValueError` before any runtime object
exists. A node holding ports in two networks remains legal but confers no
connectivity between them; no bridge construct is introduced.

## Risks

- Drain adds trailing `credit_release` trace rows to previously leaking
  runs. No existing suite pins generic trace contents exhaustively
  (byte-identity checks compare two runs of the same spec), and span
  times are untouched, so anchors hold; the full suite verifies this.
- Atomic acquisition changes wait ordering versus the old sequential
  grants. Existing multi-resource tests assert outcomes that the new
  mechanism reproduces exactly (verified by the retained suite).
