# generic-simulation Specification (delta)

## MODIFIED Requirements

### Requirement: GS-G03 Static routing tables

Each network SHALL admit an explicit static routing table mapping
(source, destination) endpoint pairs to ordered hop sequences. Routes SHALL
resolve only declared ports and links, SHALL name only endpoints attached
to the route's network, SHALL NOT be inferred from geometry, and
unreachable pairs SHALL be reported as such rather than guessed. A node
participating in multiple networks SHALL NOT be treated as a bridge;
without an explicit and supported bridge construct, cross-network endpoint
references SHALL be rejected.

#### Scenario: Explicit route is followed

- **WHEN** a transfer targets an endpoint whose table entry lists a multi-hop
  path across two networks
- **THEN** its occupancy records touch exactly those links in order, and a
  pair without a table entry is reported unreachable.

#### Scenario: Cross-network route is rejected

- **WHEN** a static route on one network names a source or destination
  endpoint attached only to another network
- **THEN** validation fails before any graph object is constructed, even if
  the named nodes participate in both networks.
