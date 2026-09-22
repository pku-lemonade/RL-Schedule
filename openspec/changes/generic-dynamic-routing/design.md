## Context

Static routing is fully delivered: graphs declare per-pair ordered link
sequences, compile resolves them into timed hops, and missing routes classify
as `route_unreachable`. Dynamic routing must slot into the same plan/runtime
ownership: the plan is the only runtime input, so any topology knowledge the
selector needs must be compiled into it. Determinism is a hard contract:
byte-identical repeated runs including adaptive choices.

## Decisions

1. **Policy per network, static by default.** `GenericNetwork.routing`
   accepts `static_table` (default), `shortest_path` or `adaptive`. A
   dynamic network must not declare static routes — mixing models in one
   network is rejected at validation. Transfers on a dynamic network never
   name routes themselves; the network policy governs.
2. **Compile computes, runtime only selects.** For each dynamic network,
   compile runs directed BFS backwards from each needed destination node and
   stores the distance table plus effective link timings in the plan.
   Reachability is checked here: a pair with no path classifies as
   `route_unreachable` (viability, not a structural error), mirroring the
   static rule.
3. **Selection always shortens distance.** At each node, candidates are the
   out-links whose far end has distance exactly one less than the current
   node. `shortest_path` picks the first by link identity; `adaptive` picks
   the candidate with the smallest credit pressure (granted users plus
   pending requests) with link-identity tie-break. Distance strictly
   decreases every hop, so termination and deadlock freedom are structural.
4. **Same crossing mechanics.** Once a next link is selected, the transfer
   crosses it with the identical credit/serializer/hop mechanics as static
   routes; chosen hops land in spans indistinguishably from static hops, and
   a `route_select` trace event records each decision with its ordering
   sequence.
5. **Explicit plan fields.** Dynamic transfers carry `routing`,
   `source_node` and `destination_node` in the plan; dynamic networks carry
   links with timings and per-destination distance tables. Everything else
   (resources, counters, memory service) is untouched.

## Acceptance plan

Synthetic grid and diamond topologies demonstrate: shortest-path selects the
documented deterministic path; adaptive avoids a congested branch and
selects the free one; distance-reducing selection completes multi-transfer
batches without livelock; unreachable pairs classify as `route_unreachable`;
static routes on dynamic networks fail validation; phase-1/2/3 suites are
unchanged; adaptive runs are byte-identical on repeat.
