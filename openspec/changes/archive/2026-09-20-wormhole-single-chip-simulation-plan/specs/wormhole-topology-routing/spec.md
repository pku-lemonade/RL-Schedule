## Purpose

Provide reusable directed network topology and routing behavior sufficient to study single-chip Wormhole traffic, congestion, failures, and eventual multicast.

## ADDED Requirements

### Requirement: TR-01 Canonical heterogeneous directed topology
Simulation and supported topology consumers SHALL share one canonical graph of stable router, directed link, fabric, endpoint, and physical resource identities. Router presence SHALL NOT imply compute availability. Exports SHALL retain tile roles, endpoint attachments, direction, wrapping links, fabric identity, and disabled components.

#### Scenario: Export includes memory-only and transit routers
- **WHEN** a heterogeneous topology is exported to tracing or a predictor
- **THEN** the export matches the executable graph without creating compute endpoints for memory-only or transit routers, and unsupported consumers reject it explicitly.

### Requirement: TR-02 Distinct configurable fabric routing
The Wormhole profile SHALL select two oppositely directed two-dimensional torus fabrics and their documented dimension orders. Routing SHALL translate canonical physical identities into the correct fabric coordinate convention before applying the policy. Generic mesh and alternative policy choices SHALL remain available without changing the Wormhole profile's meaning.

#### Scenario: Route crosses a wrap boundary
- **WHEN** a unicast transaction crosses a torus boundary on either fabric
- **THEN** every traversed edge exists in that fabric, the route reaches the same physical destination, and its dimension order and hop count agree with an independent architecture-based route oracle.

### Requirement: TR-03 Bounded flow control and justified deadlock prevention
Network execution SHALL account for finite buffering, backpressure, configurable service timing, and link throughput. Supported torus traffic classes SHALL use a documented deadlock-prevention policy covering routing dependencies and request/response resource dependencies. Infinite queues or eventual watchdog termination MUST NOT be presented as deadlock prevention. Exact silicon VC arbitration is not required when the abstraction and its limits are disclosed.

#### Scenario: Admissible traffic drains through cyclic physical topology
- **WHEN** finite unicast request/response traffic exercises both dimension wraps with enabled sinks and no permanent resource failures
- **THEN** accepted traffic drains without lost credits, negative capacities, or permanent dependency cycles, and completion respects configured link capacity.

### Requirement: TR-04 Trace and failure identity is preserved
Directed links and fabrics SHALL be individually addressable in traces and supported failure/slowdown injection. Timing output SHALL distinguish configured clock domains, serialization, propagation, contention, and end-to-end completion where those quantities are reported. Unsupported adaptive rerouting MUST NOT be inferred from the presence of failure injection.

#### Scenario: One fabric link is slowed
- **WHEN** a slowdown targets a directed link on one fabric
- **THEN** events identify that link unambiguously, affected traffic experiences its modeled cost, and unrelated links are not implicitly assigned the same failure.

### Requirement: TR-05 Multicast uses shared network delivery
Supported multicast and broadcast SHALL deliver one logical transfer to the declared destination set with explicit source-inclusion, destination eligibility, replication, flow-control, and completion semantics. Traces SHALL expose shared traversals and branch replication so shared links are not charged as independent full unicasts. Unsupported destination sets or modes MUST fail explicitly.

#### Scenario: Two destinations share a route prefix
- **WHEN** a supported multicast reaches two destinations over a shared prefix and separate branches
- **THEN** the prefix carries the modeled shared packet traffic once, each destination receives exactly one delivery, and reported completion follows the declared multicast completion rule.
