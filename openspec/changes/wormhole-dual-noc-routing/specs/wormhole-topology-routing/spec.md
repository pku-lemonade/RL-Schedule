## Purpose

Provide configurable single-ASIC Wormhole-topology unicast transport with finite virtual resources, reproducible timing and directed failure evidence, while preserving supported legacy simulation contracts.

## ADDED Requirements

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
