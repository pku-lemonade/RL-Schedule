## Context

Phase 1 (`2026-09-22-generic-simulator-phase-1`) delivered the graph
document, the four transaction kinds, a result contract, a CLI and the
adapter boundary. Its runtime works, but resources (link credit pools and
serializers, execution units, counters) are constructed ad hoc inside one
driver, there is no shareable plan object, no unified trace, and no explicit
cancellation path. The phase-1 tests pin exact numeric outcomes (33/43/22/66
cycle anchors, per-hop serialization windows, result digests), so the unified
pipeline must reproduce the existing mechanics exactly while changing their
ownership structure.

## Decisions

1. **One pipeline, old entry points become shims.** `SystemSpec` composes the
   existing graph and batch documents. `run_generic_batch` and
   `GenericRuntime` delegate to `compile_system` + `RuntimeContext.run`; the
   CLI is untouched. Phase-1 numeric semantics, classifications
   (`cycle_limit`/`dependency_unsatisfied`/`route_unreachable`/
   `capacity_exceeded`) and result digests are preserved bit for bit.
2. **Compilation is pure.** `compile_system` revalidates input, resolves
   routes and effective link timings, classifies terminal transfers
   (unreachable/oversized), computes counter reachability bounds, and emits
   an `ImmutablePlan` whose content digest is deterministic for identical
   input. It constructs no SimPy object and mutates no global state.
   Structural reference errors (unknown endpoints, units, networks, links)
   fail here; viability failures stay inside results as before.
3. **Registry owns every physical resource once.** Each plan link yields one
   entry (credit pool plus serializer) and each execution unit one entry,
   keyed by stable neutral IDs. Multi-resource acquisition happens in sorted
   ID order, so circular waits cannot form; every process releases held
   resources in `finally` blocks, and cancellation interrupts survivors so
   the registry drains completely.
4. **EventBus owns conditions.** Counters are bus state with deterministic
   publish order stamped by a global sequence; named events share the same
   ordering rule. Counter reachability bounds (initial value plus declared
   signal deltas) are computed at compile time; waits beyond the bound are
   reported in the result error list while their spans keep the phase-1
   `cycle_limit` classification.
5. **Results stay v1-compatible.** New fields (per-transaction `wait_cycles`,
   per-resource `queue_wait_cycles`, `errors`, `trace`, `plan_sha256`)
   default to empty/None so phase-1 documents and validators remain valid.
   The trace is `(sequence, time, action, transaction, resource)` ordered by
   construction, deterministic across repeated runs.
6. **Time-triggered credit release is retained.** A held credit always
   releases after its configured delay regardless of downstream acquisition,
   so hold-and-wait cycles cannot block progress; combined with sorted
   multi-resource acquisition, deadlock freedom is structural, not tested
   luck.

## Acceptance plan

The public suite covers: parallel transfer/compute on distinct resources,
deterministic queueing on a shared link, wait-after-signal, a three-transfer
ring with single-slot buffers completing without deadlock, full resource
release after cancellation, compile-time rejection of illegal references,
explicit reporting of a never-satisfiable wait, byte-identical repeated runs
(plan digest, transaction order, trace, result), and the unchanged phase-1
suite.
