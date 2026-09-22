## Context

The detailed simulator already provides the pieces this change composes:

- `configs/schemas/topology.py`: `GraphRecord` (`extra="forbid"`, frozen) and
  the version-1 `CanonicalTopology` (tiles, fabrics, routers, ports, directed
  links, attachments, workers, resources, explicit routes), compiled by
  `Topology.compile()` with canonical JSON digests and deterministic index
  maps. `topology_from_legacy()` and `topology_from_profile()` are the
  existing config-to-graph adapters; neither is generic or neutral.
- `routing.py`: `ExplicitRouting` separates static next-hop selection from
  arbitration; explicit routes resolve fabric-qualified endpoint identities.
- `noc.py` and `virtual_channel.py`: SimPy link serializers, round-robin
  arbiters and the bounded lane/credit kernel with staged storage and credit
  return.
- `scalar_service.py`: strict counter definitions with atomic/observe
  operations, the closest existing prototype for wait/signal semantics.
- `replay_topology.py`, `replay_memory.py`, `replay_compute.py`: document
  loaders that dispatch on `kind` + integer `schema_version`, rejecting
  unknown kinds before execution.

What is missing: a neutral graph document with no device-era vocabulary, a
single transaction model unifying transfer/compute/wait/signal, a unified
result contract with utilization and incomplete-transaction reasons, and a
public boundary that lets private code feed the runtime without committing
private formats.

## Decisions

1. **Subclass and select via configuration; never fork.** The generic graph
   document compiles through a new `topology_from_generic()` adapter into the
   existing canonical topology, exposed as a `GenericSystem(Topology)`
   subclass only for added neutral views (networks, DMA endpoints, execution
   units, memory resources, static route tables). The transaction runtime
   builds on the same graph substrate and SimPy discipline
   (charged storage, bounded credits, explicit release). Its link/credit
   kernel is a neutral reimplementation at that discipline level: the
   existing `VirtualChannelLink` kernel's identity types
   (`TransportEnvelope`/`LaneIdentity`/`PacketIdentity`) are torus-bound
   record families that cannot appear in generic documents or traces.
   New behavior is reachable only
   through the new document kinds; every previously admitted input produces
   unchanged results, hashes and exit codes.
2. **Neutral vocabulary is a hard contract, not a style choice.** New schema
   fields, examples, fixtures and tests use synthetic names only. A committed
   forbidden-token guard test scans the new public modules and fixtures for
   real device/vendor identifiers and fails on any hit. No field carries a
   device-derived default; every hardware dimension, rate, capacity and
   threshold is an explicit required input.
3. **wait/signal collapse to counters.** A signal transaction adds a declared
   delta to a named counter (events are boolean counters with delta 1). A
   wait transaction completes at the first cycle its counter reaches its
   declared threshold, failing if the threshold can never be reached within
   the bounded run. Counter updates have a deterministic total order;
   simultaneous signal/wait pairs resolve signal-first so a wait never
   completes before its enabling signal.
4. **DMA endpoints are transfer agents.** A DMA endpoint may initiate a
   transfer from any readable endpoint to any writable endpoint, including
   memory resources; the transfer still obeys static routes, serialization
   and credit limits of every traversed network.
5. **Neutral abstract time.** New documents use configured per-link and
   per-resource rate/latency fields with neutral names (`cycles`), integer or
   exact rational accounting, and no device-era unit names. Model times are
   never presented as hardware measurements.
6. **Adapter boundary is a library contract.** A public abstract base
   (`GenericInputAdapter`) returns validated `generic_system_graph` and
   `generic_transaction_batch` documents. Phase 1 performs no plugin
   discovery and no dynamic imports; private launch scripts import the public
   package and call the adapter/driver directly. The public repository ships
   exactly one example: an independently designed synthetic adapter whose
   input format was invented for this change and matches no private format.
7. **Honest results.** Malformed input (unknown kinds, unknown endpoints or
   units, undeclared counters, dependency cycles) is rejected before
   simulation resources exist. Viability failures surface inside the result
   as incomplete transactions with machine-readable reason codes:
   `route_unreachable`, `capacity_exceeded`, `dependency_unsatisfied` and
   `cycle_limit`. The result document revalidates its own consistency:
   status, per-span reasons, timing fields and makespan must agree, so a
   corrupted or partial output cannot be relabeled as complete. Utilization
   is reported per declared resource as busy cycles over the makespan; an
   empty transaction set cannot report success.

## Acceptance plan

- A fully synthetic two-dimensional grid with nodes, ports, links, two
  distinct networks, DMA endpoints and memory resources loads into one
  unified system-graph object; connectivity checks confirm every declared
  adjacency and both networks' member links.
- Two transfers routed over one shared link complete in queueing order with
  non-overlapping link occupancy; two transfers over disjoint links overlap.
- A compute transaction and a transfer transaction with no dependency overlap
  in time.
- A wait transaction targeting counter threshold N completes only after the
  signal transactions that raise the counter to N, never before.
- The synthetic adapter drives the same acceptance system end to end; a
  privacy guard test proves no private identifier appears in public sources,
  fixtures or documentation.
