# dynamic-routing Specification

## Purpose
Add opt-in dynamic routing to the generic layer: per-network policies that
compute paths from topology (`shortest_path`) or from topology plus runtime
congestion (`adaptive`), executed hop by hop inside the unified pipeline with
deterministic selection and a structural progress guarantee. Static tables
remain the default and are byte-identical.

## Requirements

### Requirement: DR-01 Per-network routing policy

A network MAY declare a routing policy of `static_table`, `shortest_path` or
`adaptive`; absence means `static_table`. A network with a dynamic policy
SHALL NOT declare static routes, and unknown policy values SHALL be
rejected. Transfers on a dynamic network SHALL NOT name routes themselves.

#### Scenario: Mixed configuration is rejected

- **WHEN** a network declared `shortest_path` or `adaptive` also appears in a
  static route
- **THEN** validation fails before any graph object is constructed.

### Requirement: DR-02 Pure compilation of routing tables

Compilation SHALL compute directed BFS distance tables per needed
destination node, attach effective link timings, resolve endpoint nodes and
classify unreachable pairs as `route_unreachable`, all without SimPy objects
or global state. Identical specs SHALL yield identical plan digests.

#### Scenario: Unreachable dynamic pair

- **WHEN** a transfer targets an endpoint with no directed path on a dynamic
  network
- **THEN** the plan marks it `route_unreachable`, mirroring the static rule.

### Requirement: DR-03 Deterministic shortest-path selection

At each node, candidates SHALL be the out-links whose far end is exactly one
BFS step closer to the destination. `shortest_path` SHALL select the first
candidate by link identity, and the chosen hops SHALL be recorded in spans
like static hops.

#### Scenario: Documented path is followed

- **WHEN** a grid offers two equal shortest paths
- **THEN** the transfer follows the link-identity-first one, identically on
  every run.

### Requirement: DR-04 Adaptive selection with progress

`adaptive` SHALL select among the same distance-reducing candidates the one
with the smallest current credit pressure (granted users plus pending
requests), tie-broken by link identity. Selection SHALL strictly decrease
distance every hop, so livelock is impossible, and repeated runs SHALL be
byte-identical.

#### Scenario: Congested branch is avoided

- **WHEN** one shortest branch carries a background transfer and the other is
  free
- **THEN** an adaptive transfer selects the free branch, and its span records
  exactly those links.

### Requirement: DR-05 Compatibility

Networks without a policy keep `static_table`; phase-1/2/3 documents,
fixtures, digests and tests SHALL remain unchanged. A `route_select` trace
event SHALL record each dynamic decision in deterministic sequence order.

#### Scenario: Static behavior is untouched

- **WHEN** prior suites execute
- **THEN** their results and digests match the retained expectations, and no
  dynamic plan tables appear for static networks.
