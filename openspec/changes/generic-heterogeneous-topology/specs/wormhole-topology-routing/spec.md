## Purpose

Provide a canonical heterogeneous network graph shared by simulation and topology consumers, with explicit execution boundaries and verifiable directed transport independent of a particular accelerator.

## ADDED Requirements

### Requirement: TR-G01 Canonical identities distinguish topology from compute

A version-one canonical topology SHALL identify one ASIC, physical tiles and their roles, fabric-qualified routers, independently directed links and ports, endpoint attachments, enabled compute eligibility, logical-worker mappings, and unique physical memory resources. Public identities SHALL remain stable under input record reordering. Router existence SHALL NOT imply compute availability. A link SHALL reference its actual source output and destination input without implicitly creating a reverse link; parallel links SHALL retain distinct identities and ports. Invalid versions, duplicate identities, dangling references, inconsistent compute mappings, or conflicting port occupancy SHALL be rejected.

#### Scenario: Directed heterogeneous graph is loaded

- **WHEN** a graph includes enabled workers, a memory tile, a transit tile, a disabled worker, and a link with no reverse edge
- **THEN** all declared routers and directed links survive normalization, and compute eligibility belongs only to explicitly enabled worker identities.

#### Scenario: Input order changes

- **WHEN** equivalent topology records are reordered or loaded from another path
- **THEN** their canonical identity and graph content hash agree, with deterministic published runtime index maps.

### Requirement: TR-G02 Harvesting preserves attachments and shared resources

Disabling compute SHALL preserve separately enabled routers, transit links, physical network attachments, and memory resource identities. A physical network attachment SHALL NOT automatically create a compute endpoint, DMA engine, or memory service. Multiple attachments, including those on different fabrics, referencing one resource SHALL report its capacity once in explicit byte units. Router/link availability and compute eligibility SHALL be independent states.

#### Scenario: Traffic crosses a disabled worker position

- **WHEN** admitted traffic crosses the enabled router of a disabled worker on its way to an enabled destination
- **THEN** the transfer completes through that router and no usable compute endpoint is created for the disabled worker.

#### Scenario: Memory aliases span fabrics

- **WHEN** several physical attachments on two fabrics reference one memory resource
- **THEN** export preserves every alias and one capacity, and does not claim memory request service or bandwidth execution.

### Requirement: TR-G03 Connectivity completeness and profile support are explicit

Topology inspection SHALL distinguish complete connectivity from an inventory whose links/routing are unresolved. Unknown connectivity SHALL NOT be reported as a known zero-link network. Hardware-profile projection SHALL preserve profile identity, physical/fabric coordinate mappings, resource evidence references, and enabled-worker selection without synthesizing runtime defaults. Unspecified attachment ports, direction permissions, and component availability SHALL remain explicitly unknown; instantiated components SHALL require resolved transport bindings and availability. Unresolved graphs and Wormhole hardware profiles SHALL remain rejected by execution until their required adapters and policies are implemented; user-supplied support flags SHALL NOT bypass that decision.

#### Scenario: Initial Wormhole profile is projected

- **WHEN** the committed Wormhole B0 n150 example is inspected as a canonical inventory
- **THEN** it exposes 120 physical tiles, 240 fabric-qualified routers, 240 physical attachments, 72 enabled workers, and 86 unique memory resources with unresolved link connectivity, while Wormhole execution and silicon timing validation remain unavailable.

#### Scenario: Incomplete graph is submitted to replay

- **WHEN** an inventory with unresolved connectivity is submitted for transport
- **THEN** execution fails before simulation resources, workload processing, result files, or timing claims are created.

### Requirement: TR-G04 Legacy mesh behavior uses the canonical graph

Existing supported synthetic mesh configurations SHALL compile into the shared graph and retain their previous router IDs, per-fabric directed link IDs, flattened consumer row ordering, endpoint bindings, deterministic XY paths, and modeled timing behavior. Existing workload, DMA command, failure, and trace contracts SHALL retain their meanings. Supported detailed runtime and topology consumers SHALL obtain connectivity from this graph rather than reconstructing separate meshes.

#### Scenario: Existing mesh example executes

- **WHEN** the existing mesh example with configured fabrics, local ports, packet format, and DMA attachments runs through the graph adapter
- **THEN** its endpoints, directed edges, delivery results, trace ordering/identities, and timing agree with independently retained pre-change expectations.

#### Scenario: Legacy link slowdown executes

- **WHEN** an existing router-and-direction failure targets a supported synthetic mesh
- **THEN** it retains the previous paired-direction slowdown and recovery behavior; the new graph identity does not silently reinterpret that input as a one-direction failure.

### Requirement: TR-G05 Explicit directed graphs support abstract transport replay

A separate version-one replay contract SHALL execute finite, explicitly declared unicast traffic between enabled replay endpoints on complete directed graphs. Every traversed network hop SHALL use a declared enabled link on the selected fabric, including non-grid edges. Same-router delivery SHALL be supported without a network hop. Replay SHALL account for payload and physical flit bytes separately and SHALL report completion only after all declared payload deliveries complete and network resources drain. Replay endpoints SHALL NOT claim compute scheduling, memory visibility, DMA commands, or numerical tensor execution.

#### Scenario: Non-grid traffic follows a declared route

- **WHEN** replay sends an explicitly sized payload through memory/transit routers using a directed route that is not a Manhattan path
- **THEN** the destination receives the declared payload count exactly once and the trace contains precisely the declared directed link identities on that fabric.

#### Scenario: Simulation becomes idle with undelivered traffic

- **WHEN** the event queue empties or the configured cycle limit is reached while deliveries or transport resources remain outstanding
- **THEN** replay reports incomplete execution and pending identities, returns an unsuccessful status, and does not count the run as a drain or accuracy pass.

### Requirement: TR-G06 Admission validates routes and resource dependencies

Replay SHALL admit only deterministic, explicitly declared routes with valid source/destination attachments, contiguous enabled directed edges, no repeated router, and the correct fabric. The combined channel dependency graph of all admitted routes, including injection and ejection channels, SHALL be acyclic for the supported one-VC model. Endpoints SHALL drain independently of sending and of request/response dependencies. Missing routes, dependency cycles, unsupported routing/transaction modes, cross-plan transport records, and unavailable endpoint or link targets SHALL be rejected before admission consumes credits, queue capacity, or arbitration state. User-declared safety flags SHALL NOT replace validation.

#### Scenario: Individually valid routes create a dependency cycle

- **WHEN** each declared route is simple but the union of their held-channel-to-next-channel dependencies contains a cycle
- **THEN** replay rejects the plan with involved channel identities before traffic is scheduled.

#### Scenario: Packet belongs to another transport plan

- **WHEN** a packet from a different graph or resolved transport configuration is submitted to an otherwise numerically matching endpoint/link
- **THEN** it is rejected before transport state changes.

#### Scenario: A torus policy or fixed-path transfer is requested

- **WHEN** replay requests Wormhole XY/YX torus execution, a FIXPATH packet mode, multicast, broadcast, or a memory request/response transaction
- **THEN** it identifies the unsupported mode and does not substitute the supported explicit-route unicast policy.

### Requirement: TR-G07 Transport retains configurable finite resource behavior

Graph replay SHALL reuse the simulator's modeled serialization, finite link buffers, bounded in-flight transport, credit return, and burst-level round-robin arbitration. Clocks, stage timing, widths, packet format, buffer capacities, arbitration quantum, and replay limits SHALL come from validated configuration, with explicit units and no Wormhole-specific constants in the transport mechanism. For the admitted acyclic routes, finite traffic, independently draining sinks, fair arbitration, and finite configured service times, traffic SHALL drain without loss or credit/capacity violations. This claim SHALL remain a model invariant, not silicon timing conformance.

#### Scenario: Shared directed edge is contended

- **WHEN** two finite flows share an edge with small configured buffers and a slower configured sink
- **THEN** backpressure is observable, accepted bytes are conserved, resource bounds hold, and both flows eventually drain under the declared sink assumptions.

#### Scenario: Link width or clock changes

- **WHEN** the same isolated flow runs with a different valid link width or clock ratio
- **THEN** its service cost changes according to the configured timing model and an independently calculated expectation, without changing route or resource identities.

### Requirement: TR-G08 Export and replay traces share executable identity

Versioned replay results SHALL include canonical topology identity, resolved transport-plan identity, per-fabric canonical-to-runtime router/link/port maps, endpoint/resource references, timing units, execution status, and explicit capability limits. Every network event SHALL resolve to a link/router in the exported graph actually used for that execution. Direction, fabric, wrap metadata, disabled components, and resource aliases SHALL survive export; inventory-only components SHALL be distinguishable from instantiated ones. Existing legacy trace fields and consumer row ordering SHALL remain compatible.

#### Scenario: Two fabrics reuse dense indices

- **WHEN** two fabrics both contain runtime link index zero
- **THEN** their events resolve to different fabric-qualified canonical links, and no trace or export merges them.

#### Scenario: Export includes an unused or disabled edge

- **WHEN** an explicit graph contains an unused enabled edge or a disabled edge with wrap metadata
- **THEN** export retains its structural identity and state while runtime evidence distinguishes whether it was instantiated or traversed.

### Requirement: TR-G09 Unsupported graph consumers fail explicitly

Detailed detector and hardware-encoder boundaries SHALL validate the graph and event contract before feature construction or checkpoint loading. Legacy mesh consumers SHALL preserve feature dimensions, node/link ordering, and shape checks. Heterogeneous replay graphs or result documents SHALL be rejected when their roles, directed connectivity, event semantics, or indexing are unsupported, even if aggregate counts match a legacy mesh. A lightweight compatibility check SHALL be available without optional ML packages. Generic topology support SHALL NOT imply support by the separate top-level detector/RL workflow or compatibility with its saved models.

#### Scenario: Heterogeneous graph has familiar counts

- **WHEN** a graph has the same router/link counts as a supported mesh but a router is memory-only or edge direction differs
- **THEN** the legacy consumer rejects the graph with the incompatible contract identified before tensor/checkpoint work.

#### Scenario: Legacy consumer is retained

- **WHEN** a supported mesh graph is supplied with legacy trace semantics
- **THEN** its graph relation ordering and feature contract are preserved, with model-shape incompatibility still reported explicitly where applicable.

### Requirement: TR-G10 Delivery separates conformance and runtime evidence

The change SHALL provide independent expected topology/route fixtures, legacy mesh regressions, executable heterogeneous replay cases, invalid-admission cases, conservation/drain checks, and relevant type/lint validation. Evidence SHALL identify the tested configuration and implementation, distinguish graph representation from transport/compute/memory support, and keep Wormhole routing and silicon timing unvalidated until their owning children deliver evidence. Missing optional tensor/checkpoint runs SHALL be reported as unavailable, not passed.

#### Scenario: Child validation completes

- **WHEN** graph, transport, compatibility, and static checks pass
- **THEN** the evidence reports the supported synthetic transport subset and its limits, leaves later umbrella requirements pending, and does not infer Wormhole workload accuracy from those passes.
