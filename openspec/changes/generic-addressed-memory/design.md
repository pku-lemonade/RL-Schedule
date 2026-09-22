## Context

The unified runtime owns resources through `ResourceRegistry` (stable IDs,
sorted multi-resource acquisition, release-on-cancel), events through
`EventBus`, and plans through `compile_system`. Phase-1 memories are flat:
`capacity_bytes` plus an optional service endpoint; transfers check payload
against capacity at compile (`capacity_exceeded`) but model no internal
memory structure. Phase 3 must add addresses and contention without
disturbing any of that behavior.

## Decisions

1. **One optional hierarchy, flat by default.** A memory resource may declare
   `hierarchy` with `banks`, `stripe_bytes`, `latency_cycles`, named `ports`
   (each with `command_cycles`) and named `channels` (each with
   `bytes_per_cycle`). All values are explicit inputs. Without a hierarchy
   the resource keeps phase-1 streaming semantics exactly; hierarchical
   memories also still accept unaddressed transfers, which use the phase-1
   path and engage no internal resources.
2. **Address means memory-side semantics.** `transfer.address` is optional.
   When present, exactly one of source/destination must be a memory service
   endpoint: destination memory = write, source memory = read. An address on
   a non-memory endpoint pair, on a flat memory, or a range exceeding
   capacity fails at compile as a structural error.
3. **Deterministic mapping.** Bank = `(address // stripe_bytes) % banks`.
   Port and channel are assigned by the same stripe index: port =
   `(address // stripe_bytes) % len(ports)`, channel is the port's declared
   channel. No runtime hashing, no randomness, identical every run.
4. **Two-stage service.** Command issue occupies the mapped port for
   `command_cycles`; data service then holds the mapped bank and the port's
   channel for `latency_cycles + ceil(bytes / channel_bytes_per_cycle)`.
   Writes service after the network traversal arrives; reads service before
   departure (documented abstraction: no separate request/response network
   phases in this phase). Acquisition follows the registry's sorted order,
   so port→bank→channel waits cannot deadlock; same bank serializes,
   different banks run in parallel, channels cap aggregate bandwidth.
5. **Same ownership rules.** Memory resources live in the same
   `ResourceRegistry` with stable IDs (`<resource>/<bank|port|channel>_<id>`),
   contribute busy/queue-wait/utilization to the unified result with new
   usage kinds, and release fully on interruption.
6. **Compatibility.** No new document kinds, no changed defaults, no renamed
   fields. Phase-1/2 tests, fixtures and digests are untouched.

## Acceptance plan

Synthetic hierarchical memories demonstrate: different-bank writes overlap,
same-bank accesses serialize, one port serializes command issue, channel
rate bounds service time, reads complete after service-plus-network while
writes complete after network-plus-service, out-of-range/flat/non-memory
addresses fail at compile, unaddressed phase-1 traffic is untouched, and
repeated runs are byte-identical.
