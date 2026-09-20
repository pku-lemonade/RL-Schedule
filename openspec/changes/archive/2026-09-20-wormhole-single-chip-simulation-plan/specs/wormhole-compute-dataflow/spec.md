## Purpose

Represent resource-limited compute and explicitly scheduled data movement for single-chip mapping studies without requiring instruction or numerical tensor execution.

## ADDED Requirements

### Requirement: CD-01 Configurable abstract compute costs
Supported compute operations SHALL derive nonnegative durations and resource occupancy from declared operation, shape, dtype, layout assumptions, and effective hardware rates. Positive work MUST NOT silently complete in zero time due to truncation or an unimplemented operation. The supported FC/matmul abstraction SHALL be executable; unsupported shapes or operations SHALL fail explicitly. A compute tile's hardware identity SHALL remain distinct from a tensor tile's shape.

#### Scenario: Small nonempty matrix operation
- **WHEN** a supported FC or matmul has positive work below one nominal throughput quantum
- **THEN** it consumes at least the selected model's minimum service quantum and reports its effective cost assumptions.

### Requirement: CD-02 Loads and stores generate memory traffic
Wormhole workload loads and stores SHALL issue the modeled memory transactions, consume applicable local capacity and bandwidth, and wait for the required data or completion events. A local allocation or load/store-unit delay alone MUST NOT stand in for a remote transfer. Local-only accesses SHALL remain expressible without fabricated network traffic.

#### Scenario: Same computation uses a more distant DRAM attachment
- **WHEN** the data source changes to a DRAM attachment with a longer or contended route
- **THEN** the workload's memory events and dependent start times reflect that route and service contention while its declared compute work stays constant.

### Requirement: CD-03 Bounded producer-consumer buffer semantics
Pipeline buffers SHALL have finite capacity, ownership, reservation, publication, consumption, and release semantics. Consumers SHALL wait for published data; producers SHALL backpressure when space is unavailable. Resident data and in-flight reservations SHALL be accounted consistently with the backing scratchpad capacity, without double allocation or release.

#### Scenario: Producer outruns consumer
- **WHEN** a reader fills a finite buffer faster than compute consumes it
- **THEN** further production waits for released space, capacity is never exceeded, and valid finite work drains without lost or duplicated tokens.

### Requirement: CD-04 Reader compute writer overlap follows dependencies
Independent reader, compute, and writer activities SHALL overlap when resources and data dependencies permit. Shared NoC, L1, DRAM, or compute resources SHALL constrain that overlap. Transfer service already charged to a transaction MUST NOT be added again as a second synthetic load/store delay for the same work.

#### Scenario: Double buffering permits overlap
- **WHEN** an otherwise identical workload changes from one to two buffer slots with independent stage resources
- **THEN** stage intervals expose permitted overlap without violating data readiness, and end-to-end time follows the critical path rather than an unconditional sum of stage durations.

### Requirement: CD-05 Workload and consumer compatibility is declared
Workloads SHALL declare the memory locations, buffer extents, dependencies, operations, and shapes required by their selected execution model. Legacy DFGs SHALL retain their supported mode or use an explicit documented adapter. Predictor and RL consumers SHALL validate topology/features and observation/action/checkpoint compatibility before claiming support for the new workload model.

#### Scenario: Mesh-trained consumer receives a heterogeneous workload
- **WHEN** a consumer lacks a declared compatible topology or feature contract
- **THEN** it rejects the input with the incompatibility identified, rather than silently interpreting router count as compute count or reusing an incompatible checkpoint.
