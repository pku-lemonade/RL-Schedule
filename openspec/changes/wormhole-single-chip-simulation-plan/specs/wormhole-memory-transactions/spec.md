## Purpose

Model observable memory transactions and shared memory service so Wormhole data movement consumes actual network and storage resources with meaningful completion events.

## ADDED Requirements

### Requirement: MT-01 Initiators and transaction paths
The simulator SHALL distinguish a transaction initiator, network attachment, addressed memory region, and backing physical memory resource. Reads SHALL carry a request and return data; writes SHALL carry data and any acknowledgement required by the selected mode. DRAM aliases SHALL act as service endpoints for addressed transactions, not acquire autonomous compute-side issue behavior merely through a renamed DMA endpoint.

#### Scenario: Compute reads remote DRAM
- **WHEN** a compute endpoint issues a supported DRAM read
- **THEN** request delivery, shared DRAM service, response traffic, and local data availability occur in dependency order and consume their respective modeled resources.

### Requirement: MT-02 Packet overhead and segmentation are executable
Transaction traffic SHALL account for configurable physical flit width, data capacity, real packet-header traffic, payload limits, and alignment or rounding rules. Segmentation SHALL charge each packet's overhead exactly once. Logical byte count SHALL remain distinguishable from transmitted bytes. Short immediate operations SHALL either follow a separately declared packet rule or be rejected.

#### Scenario: Payload exceeds one packet
- **WHEN** a transfer exceeds the selected profile's maximum packet payload
- **THEN** it becomes multiple packets, each consumes the configured header and rounded payload traffic, and completion waits for all required packet effects.

### Requirement: MT-03 Shared memory capacity and service
Aliases of the same DRAM resource SHALL share capacity, queues, and read/write service limits. Compute, local transfers, and both NoC interfaces SHALL contend for the L1 resources they actually share in the selected abstraction. Capacity allocation, bandwidth service, and data readiness SHALL be separate constraints; detailed DRAM command scheduling and bit-accurate memory contents are not required.

#### Scenario: DRAM aliases are used concurrently
- **WHEN** concurrent reads and writes reach multiple aliases of one DRAM group
- **THEN** the combined traffic cannot exceed that group's configured shared service envelope or multiply its storage capacity, while independent groups can operate concurrently.

#### Scenario: Both fabrics target one local memory resource
- **WHEN** traffic from both fabrics and a local consumer access the same modeled L1 resource
- **THEN** their completion times reflect the configured shared-port or aggregate-bandwidth policy and finite capacity.

### Requirement: MT-04 Completion, readiness, and ordering are explicit
Operations SHALL distinguish acceptance, injection/local handoff, destination effect, response receipt when applicable, and observable completion. Memory regions or buffers SHALL carry sufficient identity, extent, and readiness metadata to enforce supported dependencies without requiring tensor contents. Posted writes, acknowledged writes, read completion, and fences SHALL have explicit supported semantics. Unimplemented ordering modes MUST NOT silently behave as a globally serialized memory system.

#### Scenario: A consumer follows an asynchronous write
- **WHEN** a consumer depends on data from an asynchronous write
- **THEN** descriptor acceptance or injection alone cannot satisfy the dependency, and the consumer runs only after the selected visibility or synchronization condition is met.

### Requirement: MT-05 Existing DMA contracts remain distinguishable
Supported legacy DMA issue slots, datapaths, command sequencing, and completion semantics SHALL remain testable through the new transport or adapters. Wormhole transactions SHALL be labeled separately where their semantics differ. Previously unsupported fixed-path, local-memory DMA, or other modes MUST NOT become silently accepted without an implemented contract.

#### Scenario: Legacy and Wormhole transfers use shared transport
- **WHEN** a legacy DMA workload and an equivalent-size Wormhole transaction are executed in their respective modes
- **THEN** each preserves its declared issue and completion semantics even when they reuse serialization and resource-service mechanisms.

### Requirement: MT-06 Scalar synchronization has resource cost
The supported synchronization subset SHALL define scalar atomic or semaphore effects, address scope, ordering, service serialization, network traffic, and completion dependencies. Scalar control state SHALL be updated sufficiently to enforce those effects. Hardware tensor reductions and numerical collective execution MUST NOT be implied by scalar atomic support.

#### Scenario: Producers signal one consumer
- **WHEN** supported remote scalar updates satisfy a declared semaphore condition
- **THEN** the consumer releases only after the required visible updates, and updates contend for the addressed synchronization resource and network service.
