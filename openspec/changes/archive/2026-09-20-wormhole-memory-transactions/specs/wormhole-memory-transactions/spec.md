## Purpose

Provide executable, bounded addressed-memory transactions on a single-chip network, with shared storage and service costs, observable readiness, and explicit compatibility and fidelity boundaries.

## ADDED Requirements

### Requirement: MT-D01 Explicit memory replay admission
The simulator SHALL accept an opt-in memory replay with exact `kind=memory_replay` and integer `schema_version=1`, finite operations, tagged graph/profile input, explicit resource/endpoint bindings, and configurable packet, issue, queue, clock, and memory-service parameters. Effective settings SHALL retain source evidence or declared assumptions and deterministic configuration identity. Invalid or unsupported inputs MUST fail before runtime resource construction or output replacement. Existing topology replay versions SHALL retain their contracts.

#### Scenario: Admission rejects unsupported transactions
- **WHEN** a replay requests an unknown version, immediate/inline write, byte mask, atomic, linked multi-packet request, MMIO address, automatic NIU splitting mode, or unavailable endpoint
- **THEN** admission fails with the unsupported contract identified and no partial simulation or output replacement occurs.

#### Scenario: Generic settings are executable
- **WHEN** valid generic configurations use different flit widths, capacities, service rates, and clocks
- **THEN** their effective plans expose those differences and execution uses the supplied settings rather than Wormhole-specific constants.

### Requirement: MT-D02 Address and physical resource identity
An operation SHALL distinguish its initiator, selected fabric attachments, source/destination buffer ranges, and backing memory resources. Address arithmetic, permissions, ownership, enabled endpoint roles, and range bounds SHALL be validated. Aliases SHALL resolve to the same physical resource and address space. DRAM aliases SHALL service requests without gaining autonomous initiation rights. Supported network reads SHALL source a remote L1/DRAM range and write an initiator-local L1 range; supported network writes SHALL source initiator-local L1 and write a remote L1/DRAM range, including a same-router network route.

#### Scenario: A read returns into local storage
- **WHEN** an enabled initiator reads a valid DRAM range through one alias
- **THEN** the request addresses the shared DRAM resource, the response returns on the selected fabric, and the declared local destination incurs memory-write service before becoming ready.

#### Scenario: Alias changes do not change storage identity
- **WHEN** operations refer to overlapping physical ranges through different aliases or fabrics
- **THEN** capacity, readiness, access conflicts, and service ownership are resolved against the same backing resource; an alias cannot bypass range checks or create an independent copy.

### Requirement: MT-D03 Real packets and independent segmentation
Packetization SHALL configure physical flit bytes, data bytes per flit, header flits per packet, maximum segment payload, and the accepted address-alignment subset. Each read segment SHALL send a header-only request and a header-plus-data response. Each normal write segment SHALL send header-plus-data and, in acknowledged mode, receive a header-only acknowledgement. Header flits SHALL contain zero logical data bytes while consuming physical link, buffer, arbitration, and routing resources. Large logical operations SHALL use explicitly independent, unlinked requests; every segment SHALL pay its headers and rounded data exactly once. Zero-length operations and unsupported alignment SHALL be rejected.

#### Scenario: Reference packet boundary
- **WHEN** a supported 8,193-byte operation uses 32-byte physical/data flits, one header flit, an 8,192-byte segment limit, and aligned addresses
- **THEN** it produces two segments with 257 data flits in total, read and acknowledged-write traffic each total 8,352 packet bytes, and posted-write traffic totals 8,288 packet bytes before counting route traversal.

#### Scenario: Small header-only traffic is not free
- **WHEN** a one-byte read or acknowledged write runs under the same packet rules
- **THEN** three physical flits traverse their respective request/response routes and the logical operation remains one byte.

### Requirement: MT-D04 Single capacity ownership and readiness
Buffers SHALL have stable identity, resource-relative extent, permissions, and explicit initial or producer-dependent readiness. Reservations and releases SHALL have one capacity owner per physical resource; they SHALL be independent of bandwidth service. An uninitialized range MUST NOT be read before its declared producer's destination effect. Concurrent conflicting accesses without an explicit sufficient dependency SHALL be rejected, including conflicts reached through aliases. Disjoint accesses and concurrent reads SHALL remain eligible for overlap. Duplicate release, foreign handles, overlap between distinct reserved buffers, and out-of-bounds access SHALL fail explicitly.

#### Scenario: Allocation is not data production
- **WHEN** an allocated destination is awaiting a write and a local consumer names that write as its producer
- **THEN** neither free capacity, descriptor acceptance, nor source handoff satisfies readiness; consumption starts only after the relevant destination write has completed service.

#### Scenario: Scratchpad binding has one budget
- **WHEN** the opt-in memory capacity adapter exclusively binds an empty legacy scratchpad and reserves then releases a buffer
- **THEN** both interfaces observe exactly one decrement and one increment of the same capacity, and concurrent unmanaged allocation or duplicate release is rejected.

### Requirement: MT-D05 Shared bounded memory service
Each backing L1/DRAM resource SHALL have a configurable finite service queue, service granularity, service clock, per-chunk latency, and combined read/write bandwidth under a named aggregate policy. Both fabrics, all aliases, and admitted local clients SHALL contend for that resource's single service envelope. Source data reads and destination data writes SHALL each consume service; packet headers and acknowledgements SHALL not count as memory data. Independent resources SHALL execute concurrently. The aggregate policy SHALL be reported as an abstraction without implying bank, channel, or physical-port timing fidelity.

#### Scenario: Both fabrics and a local client contend
- **WHEN** two fabric transfers and a local access use one L1 resource
- **THEN** their memory service events share its configured queue and service envelope, remain bounded, and cannot multiply the bandwidth by the number of clients.

#### Scenario: DRAM group alias bandwidth is conserved
- **WHEN** concurrent reads and writes arrive through several aliases of one DRAM group while another group is active
- **THEN** the first group's combined service obeys one configured limit while the second group can overlap independently.

### Requirement: MT-D06 Transaction lifecycles and completion
Operation results SHALL distinguish submission, descriptor acceptance, source-read completion where applicable, final request handoff, destination visibility/readiness, response receipt where applicable, and observable completion. Posted-write completion SHALL mean final local request handoff, without a fabricated response or remote-visibility guarantee. Acknowledged-write responses SHALL be generated only after each segment's destination writes finish, and operation completion SHALL require all segment acknowledgements. Read completion SHALL require all response data to finish writing into local memory. An initiator's local completion MUST NOT terminate a replay with pending remote effects or resource work.

#### Scenario: Posted write completes before remote visibility
- **WHEN** the source finishes handing off a posted write while destination service is delayed
- **THEN** its local completion is recorded, the destination remains unready, and replay success waits for destination service and resource drain.

#### Scenario: Response arrival is not read completion
- **WHEN** the final read response arrives while its local memory write is waiting for service
- **THEN** response receipt is recorded separately and read completion/readiness remain pending until that service finishes.

### Requirement: MT-D07 Explicit dependencies and scoped fences
The supported ordering policy SHALL require explicit acyclic dependencies and SHALL NOT impose implicit global program order or cross-fabric sequential consistency. A local-handoff fence SHALL snapshot earlier selected operations of one initiator and gate later dependent issue until their requests are handed off. A remote-completion fence SHALL cover reads and acknowledged writes, completing after their observable completion; inclusion of posted writes SHALL be rejected. Destination-readiness dependencies SHALL be available to local consumers of that resource without inventing remote notification to the issuing worker. Unsupported hardware ordering modes SHALL fail explicitly.

#### Scenario: Remote fence cannot prove a posted write visible
- **WHEN** a remote-completion fence includes a posted write
- **THEN** admission rejects it rather than using simulator knowledge of the destination to report an unavailable sender-side observation.

#### Scenario: Explicit cross-fabric dependency
- **WHEN** a single initiator submits a read on one fabric after an acknowledged write on the other through a remote-completion fence
- **THEN** the read cannot issue until the acknowledgement arrives; independent operations remain eligible to overlap.

### Requirement: MT-D08 Bounded transport integration and drain
All memory packets SHALL traverse the admitted routes and existing physical link/router service with class-separated bounded request/response resources. Endpoint descriptors, receive storage, transmit staging, service queues, and active work SHALL have declared finite bounds and conservation traces. No full uncharged payload queue or per-operation private network SHALL substitute for shared contention. Memory grants SHALL perform finite service without waiting for downstream network capacity. Read responses and write acknowledgements SHALL be causally triggered by actual request service. Success SHALL require all effects, packets, endpoint work, memory service, ownership, and delayed credits to drain; timeout or idle-with-pending SHALL report incomplete with diagnostics.

#### Scenario: Transfers exceed every staging buffer
- **WHEN** bidirectional reads and writes on both fabrics use one-slot endpoint limits, slow memory, and packets larger than network/staging buffers
- **THEN** finite supported traffic drains, shared physical bandwidth and all capacity bounds are conserved, and each response is generated exactly once.

#### Scenario: Incomplete operation is not success
- **WHEN** the configured horizon expires before destination service or final credit return
- **THEN** the result identifies pending work and is incomplete even if every posted write has locally completed.

### Requirement: MT-D09 Legacy and consumer compatibility
Legacy DMA adapters SHALL preserve supported GM/DDR direction, dual-side rendezvous, single-side command/response, descriptor sharing, issue limits, datapath service, configured packet sizes, and completion behavior without duplicate service charges. Adapter records SHALL label their legacy policy and leave unavailable memory-visibility facts absent. Unsupported FIXPATH/local-memory DMA modes SHALL remain rejected. Legacy replay, detector/encoder feature contracts, checkpoints, RL shapes, and top-level workflows SHALL remain compatible; memory replay/result inputs SHALL be rejected at incompatible model boundaries.

#### Scenario: Adapter exposes existing completion honestly
- **WHEN** a legacy DMA transfer runs through the adapter and directly under identical inputs
- **THEN** existing timings, packet/service traces, and resource drain agree, while the adapter does not relabel a local handoff as addressed-memory visibility.

#### Scenario: Memory traces are not legacy detector inputs
- **WHEN** a memory replay or result is supplied to an unsupported detailed detector/encoder boundary
- **THEN** it fails clearly before tensor or checkpoint work and does not silently alter feature shapes.

### Requirement: MT-D10 Executable evidence and support reporting
The change SHALL provide generic and Wormhole memory examples, a CLI with success/invalid/incomplete outcomes, deterministic effective plans, and records reconciling logical bytes, packet/header/padding bytes, actual channel bytes, and rounded memory-service bytes. Evidence SHALL map requirements to independent packet/service oracles, contention/drain tests, legacy regressions, and source identities. Capability reporting SHALL distinguish configuration representation from executed memory support and retain unsupported compute/DFG, exact NIU/bank/DRAM, and calibration claims.

#### Scenario: Reproducible example without silicon calibration
- **WHEN** the checked-in Wormhole memory example runs successfully
- **THEN** it emits the effective profile/graph/configuration identities, explicit service assumptions and model policy, and no claim of measured silicon timing accuracy.

#### Scenario: CLI preserves an output on invalid input
- **WHEN** an invalid replay is run with an existing output file
- **THEN** the CLI reports invalid input and leaves that file unchanged; an incomplete valid run instead emits its diagnostics with the incomplete exit status.
