# wormhole-topology-routing Specification

## Purpose

Provide reusable directed network topology and routing behavior sufficient to study single-chip Wormhole traffic, congestion, failures, and eventual multicast.

## Contract scope

TR-01..05 describe the network capability across admitted interfaces. TR-G01..10
apply to canonical inventory, supported legacy mesh consumers and version-one
explicit-route replay. TR-D01..10 apply to version-two torus transport and its
causal byte-response fixtures. Unresolved profile projection remains distinct
from an explicitly bound executable torus. Unsupported-mode clauses apply to
the selected interface; a later adapter does not broaden an older replay version.
Shared rectangular multicast is specified in
[wormhole-multicast-sync](../wormhole-multicast-sync/spec.md). Child-delivery
statements retain their original milestone scope.

## Requirements

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

### Requirement: TR-D01 Explicit torus transport binding
The system SHALL compile an explicit transport binding over a complete canonical directed 2D torus graph or a validated hardware profile. It SHALL preserve physical tile, fabric, directed-link, endpoint and shared-resource identities; derive dimensions and coordinate maps from the source; and export the binding's availability and endpoint-permission provenance. Unknown availability or permissions MUST NOT silently enable execution. Worker harvesting SHALL NOT remove transit routers. Inventory resources MUST NOT become compute or memory services through transport binding.

#### Scenario: Wormhole profile becomes a transport plan
- **WHEN** the 10x12 Wormhole profile is supplied with an admissible healthy-grid transport binding and selected endpoint fixtures
- **THEN** the plan contains 240 fabric-qualified routers and 480 directed network links across two fabrics, preserves the selected worker mask and unique resource inventory, and instantiates only the selected transport endpoints.

#### Scenario: Unresolved or incompatible binding
- **WHEN** a binding leaves required availability or permissions unresolved, selects a disabled worker as a worker initiator, or labels a nonconforming graph as the supported torus
- **THEN** compilation fails before runtime resource allocation and identifies the incompatible binding.

### Requirement: TR-D02 Coordinate-correct deterministic unicast
The Wormhole binding SHALL resolve the same physical source/destination through each fabric's raw coordinates and select NoC0 XY and NoC1 YX positive modular routing. Each hop SHALL be an available directed edge in the selected fabric, and compiled output SHALL expose physical identities, dimension order, wrap information and hop count. Same-router delivery SHALL use local channels only. Generic supported dimensions, fabric IDs, datelines and dimension-order choices SHALL be configurable without changing Wormhole profile semantics. Unsupported dimensions or policies SHALL fail explicitly; unavailable edges SHALL NOT cause implicit adaptive routing.

#### Scenario: Asymmetric routes reach the same tile
- **WHEN** physical tile (9,11) sends to physical tile (1,1) on each fabric of the 10x12 reference layout
- **THEN** NoC0 traverses four network hops in XY order and NoC1 traverses eighteen in YX order, and both deliver to the same physical destination.

#### Scenario: A deterministic path uses a disabled edge
- **WHEN** an admitted request or generated response would require an unavailable router or directed link
- **THEN** the complete plan is rejected before traffic starts, without selecting a different path or fabric.

### Requirement: TR-D03 Declared and justified virtual resources
The supported torus policy SHALL provide separate request/response resources and two dateline phases per class on each network link, with finite configurable capacity. Its published dependency argument SHALL cover dimension turns, dateline transitions, packet ownership, local channels and request-to-response dependencies. Compiled routes SHALL be checked against that policy. Trace/result metadata SHALL identify these as modeled lanes and disclose the difference from hardware VC encoding, buddy allocation and arbitration. A physical-channel DAG check alone MUST NOT be claimed as general torus deadlock prevention.

#### Scenario: Cyclic physical topology has ordered virtual resources
- **WHEN** all ordered unicast pairs exercise both wraps of a supported torus under the declared policy
- **THEN** the compiled virtual-resource dependency union is acyclic even though the corresponding one-VC physical dependency union can be cyclic.

#### Scenario: A phase assignment violates the policy
- **WHEN** a compiled resource path assigns the dateline edge to the wrong phase, resets phase within an axis, or introduces an unsupported class dependency
- **THEN** validation rejects that path before credits or packet ownership are acquired.

### Requirement: TR-D04 Bounded cut-through service over shared physical links
Execution SHALL use finite, reported lane, staging and pipeline capacities with conserved credits and ordered HEAD-through-TAIL packet ownership. Forwarding SHALL be cut-through. All lanes on one physical link SHALL share its configured serialization capacity. Fair bounded-quantum arbitration SHALL select eligible work; a lane awaiting capacity, ownership or a future flit MUST NOT retain a shared physical grant and block eligible lanes indefinitely. Independent outputs SHALL be able to progress concurrently. Each accepted flit's retained storage and delayed credit return SHALL be included in capacity accounting.

#### Scenario: One lane stalls while another is ready
- **WHEN** a request or dateline lane exhausts its credit and another lane on that physical output has eligible traffic
- **THEN** the ready lane progresses through fair physical arbitration without exceeding aggregate link bandwidth, and the blocked lane resumes when its own capacity returns.

#### Scenario: Long packets drain through small buffers
- **WHEN** finite packets longer than lane buffers contend under enabled sinks and finite service
- **THEN** forwarding begins before entire packets arrive, packet/flit order is preserved, capacities remain within bounds, and all credits, owners and pipeline work eventually drain.

### Requirement: TR-D05 Finite causal response fixtures
Transport replay SHALL support finite one-way request-class packets and requests that generate exactly one configured response on the same fabric. A response SHALL begin only after complete request consumption and the declared service delay. Response descriptors and active endpoint storage SHALL have explicit finite bounds. Response injection and sink drain SHALL be independent of blocked request resources. Responses MUST NOT generate further traffic. Results SHALL distinguish these byte-transport fixtures from NIU operations, memory service, hardware acknowledgements and software synchronization.

#### Scenario: Full response queue backpressures requests safely
- **WHEN** simultaneous requests cross both dimension wraps with a bounded response queue of one, finite service, and slow independently draining response sinks
- **THEN** request backpressure is observable, each request causes exactly one response with the correct causal identity, and finite accepted traffic and resources drain without response-to-request dependency cycles.

### Requirement: TR-D06 Explicit clocks and timing boundaries
Configuration SHALL identify native NoC clock domains and the simulation timebase and expose physical flit size, usable link width, launch spacing, router stage latency/initiation, propagation, credit-return and endpoint service. It SHALL reject invalid units, unsafe serialization spacing, nonfinite values or nonpositive clocks/capacities. Latency and initiation interval SHALL remain distinct. Results SHALL distinguish measured waits, stage service, first-flit latency, packet/response completion and resource drain; physical-byte throughput SHALL respect shared serialization capacity. Hardware reference values and assumed stage decomposition SHALL retain distinct evidence labels.

#### Scenario: Width and clock alter an analytical transfer
- **WHEN** otherwise equivalent zero-load local, one-hop or multi-hop transfers run with two explicit width/clock settings
- **THEN** serialization and timestamps agree with an independent calculation in the declared timebase, and changing modeled lane count or traffic does not multiply a physical link's capacity.

### Requirement: TR-D07 Directed reproducible slowdown schedules
Version-2 replay SHALL accept finite slowdowns targeting one instantiated inter-router link by fabric and canonical directed-link ID. Each entry SHALL have a unique failure ID, finite factor at least one, and a valid half-open interval; overlapping intervals on the same target SHALL be rejected. At physical launch the active factor SHALL be snapshotted for that flit's serialization, launch spacing and propagation. Already launched flits SHALL retain their snapshot, lane order SHALL survive recovery, and no route SHALL change. Other links, credit-return delays and router stages MUST NOT be implicitly assigned the factor. Observable contention effects elsewhere SHALL remain distinguishable from direct parameter mutation.

#### Scenario: Slowdown and recovery affect only the intended physical service
- **WHEN** a schedule slows a wrap link on one fabric and later recovers, including launches exactly at interval boundaries
- **THEN** events identify that directed link and each launch factor deterministically, affected packets retain lane order, and control traffic with no shared resources retains its baseline timing.

#### Scenario: Invalid failure target or overlap
- **WHEN** a schedule targets a missing, disabled or local channel, uses an invalid factor/interval, or overlaps another slowdown on the same target
- **THEN** admission fails before execution or output-file creation.

### Requirement: TR-D08 Versioned trace identity and completion accounting
Version-2 results SHALL include source/graph/plan identity, resolved policy/settings/assumptions, canonical-to-runtime mappings, compiled resource paths, capacity bounds/peaks, causal packet identities, class/lane/stage/failure events and exact payload/physical-byte accounting. Physical transmitted bytes SHALL count each actual launch once. Credit events SHALL identify their lane/token. A complete result SHALL require logical delivery and release of credits, packet owners, descriptors and pipeline work. Cycle-limit or idle-with-pending outcomes SHALL identify incomplete work without claiming deadlock prevention or successful delivery.

#### Scenario: Same local indices occur on both fabrics
- **WHEN** both fabrics emit events for identically numbered runtime routers, links or packets
- **THEN** every event resolves unambiguously through fabric-qualified canonical identities and the plan that admitted it.

#### Scenario: Payload completion precedes credit drain
- **WHEN** all expected bytes have arrived but a delayed credit or response descriptor remains outstanding
- **THEN** the run does not report complete until that state drains, and a reached execution limit reports the pending state.

### Requirement: TR-D09 Explicit admission and legacy compatibility
The CLI SHALL dispatch `topology_replay` and `topology_replay_result` by exact schema version, preserving version-1 inputs/results, legacy mesh routing/timing/failure behavior and legacy trace serialization. Invalid versions, settings, routes, unsupported modes or cross-plan packets SHALL fail before the affected runtime resources mutate. Plain profile inspection and full architecture/DFG execution SHALL remain distinct from opt-in transport admission. Unsupported detector/encoder/RL consumers SHALL reject new transport data without silently reinterpreting feature rows, changing checkpoints or claiming heterogeneous workload support.

#### Scenario: Existing replay and model contracts remain valid
- **WHEN** the version-1 heterogeneous example and legacy custom-mesh timing/failure fixture run after this change
- **THEN** their accepted identities, results and baseline behavior remain unchanged, structurally valid legacy consumer inputs remain accepted, and version-2 results are rejected at unsupported model boundaries.

#### Scenario: Unsupported hardware functionality is requested
- **WHEN** input requests hardware VC linking/priorities/buddy modes, multicast, NIU command execution, memory or compute behavior outside the transport contract
- **THEN** admission rejects the unsupported feature and does not advertise a fully executable Wormhole profile.

### Requirement: TR-D10 Layered evidence and honest support claims
Delivery SHALL include pinned primary-source references and independent route expectations; executable resource/credit, contention, response-causality and drain checks; analytical timing/bandwidth checks; directed failure checks; and retained legacy regressions. Validation SHALL cover both fabric route families on the selected Wormhole layout and configurable small tori with shifted datelines. The evidence report SHALL identify commands, code/artifact identities, assumptions, unsupported behavior and unavailable checks, and separate architecture conformance and internal model checks from external-reference/hardware calibration. No hardware accuracy percentage SHALL be inferred from synthetic passing tests.

#### Scenario: Child delivery is reviewed
- **WHEN** this change is declared implemented
- **THEN** its report maps TR-D01..10 to actual executable evidence, reports test/type/lint results, and states that NIU packet costs, memory/compute execution and silicon timing calibration remain outside this milestone.
