## Why

Review of the unified generic runtime found three generic correctness
defects that the current public suites do not cover:

1. Multi-resource acquisition is sequential: a waiter for {A, B} holds A
   while waiting for B; an invalid identity mid-set can strand earlier
   grants; requests are registered only after grant, so queued requests
   cannot be cancelled; and an exception in one transaction process aborts
   the environment without cancelling or cleaning up the others.
2. Memory service swallows `simpy.Interrupt`, so cancelled or timed-out
   transactions continue later business stages and can report `complete`
   after the cycle bound; successful runs return while delayed credit
   returns are still pending, reporting `drained` without draining.
3. Endpoint validation resolves endpoint→node globally but never checks
   which network an endpoint is attached to: a route or transfer on one
   network can name endpoints attached only to another network and run
   successfully, silently treating shared nodes as bridges.

This change fixes all three inside the existing
`SystemSpec → compile_system → ImmutablePlan → RuntimeContext` structure.
It adds no features, no document kinds and no device vocabulary.

## What Changes

- **Atomic multi-resource acquisition.** `ResourceRegistry.acquire_all`
  validates the complete set (unknown, duplicate or empty identities are
  rejected before any request exists), then grants all members at one time
  step or none at all: a waiter holds no member while waiting, so
  single-resource acquirers are never blocked by partial holders. All
  requests are registered at creation and are cancellable while queued.
  Memory bank+channel data service uses this mechanism.
- **Bounded cancellation, drain and abort.** Interrupts propagate through
  every memory service stage to the transaction layer; no business stage
  continues after cancellation or timeout and no interrupted transaction
  reports completion. The run loop gains explicit phases — business,
  bounded cancellation cleanup, background drain — so a returned `drained`
  means every resource is released, every queued request is cleared, and
  the trace records the final releases. `completion_cycles` keeps its
  business meaning. A runtime exception aborts the run: in-flight work is
  cancelled, bounded cleanup runs, and the original error is preserved;
  the run never returns success after a failure.
- **Endpoint network membership.** Static routes may name only endpoints
  attached to the route's network, and compilation rejects any transfer
  whose source or destination is not attached to the transfer's network.
  A node participating in several networks is not a bridge; no bridging
  feature is added.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `unified-runtime`: UR-C01 gains endpoint-network membership validation;
  UR-R02 gains atomic pre-validated multi-resource acquisition; new UR-R05
  specifies bounded cancellation, background drain and honest abort.
- `addressed-memory`: AM-04 gains atomic bank+channel acquisition and
  interrupt propagation through service stages.
- `generic-simulation`: GS-G03 gains route endpoint-network membership and
  the explicit no-implied-bridging rule.

## Impact

- **Simulator:** `runtime_context.py` (registry, memory service, run
  loop), `configs/schemas/generic_graph.py` (route membership) and
  `system_compile.py` (transfer membership). No schema shape changes, no
  new document kinds, no CLI changes.
- **Observability:** successful runs now execute pending delayed credit
  returns before returning, so traces gain the final `credit_release`
  events after business completion; span times and `completion_cycles`
  keep their phase-1 meanings.
- **Compatibility:** every existing suite, fixture, digest and the
  phase-1 numeric anchors must stay green and unchanged.
- **Out of scope:** instruction-level simulation, multi-chip, new routing
  features, a full memory data model, buffer content lifecycle or
  read-after-write visibility checks, private plugins or adapters, and
  any real device configuration or calibration data.
