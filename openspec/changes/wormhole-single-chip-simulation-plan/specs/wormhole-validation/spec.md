## Purpose

Make architecture conformance, model correctness, functional equivalence, and measured timing accuracy independently reproducible throughout incremental Wormhole simulator development.

## ADDED Requirements

### Requirement: VA-01 Claims identify their evidence tier
Validation reports SHALL distinguish architecture/protocol conformance, internal model invariants, functional-reference comparisons, and measured silicon timing comparisons. A functional reference such as ttsim MUST NOT serve as a silicon timing oracle. Missing measurements SHALL be reported as unvalidated timing, not a pass or an implied accuracy percentage.

#### Scenario: Only public documents and functional results are available
- **WHEN** a workload agrees with a pinned architecture contract and normalized functional reference observations but has no matched hardware measurement
- **THEN** the report identifies that coverage and explicitly leaves silicon timing accuracy unvalidated.

### Requirement: VA-02 References and run conditions are reproducible
Reference fixtures and results SHALL identify source URL, immutable revision or recorded snapshot hash, extraction method, units, profile version, enabled layout, clock settings, workload parameters, simulator revision, and random seed when applicable. Hardware comparisons SHALL additionally record device and software versions, profiler/instrumentation state, measurement window, and aggregation method. Unknown metadata SHALL remain marked unknown and limit the claim.

#### Scenario: Published benchmark has incomplete metadata
- **WHEN** a public timing result lacks the enabled layout or measurement window needed for direct comparison
- **THEN** it is retained only at its supported evidence strength and is not used as a precise matched-device acceptance threshold.

### Requirement: VA-03 Every child has an executable validation gate
Every implementation child SHALL provide relevant tests and targeted type/lint checks for its changed modules, independent expected outcomes for architecture claims, compatibility coverage, and a requirement-to-evidence report. Dependency or tool import failures SHALL be reported as blocked checks rather than behavioral failures or passes. The next child's detailed design SHALL incorporate the preceding child's measured findings and unresolved limitations.

#### Scenario: Topology child completes before the common harness exists
- **WHEN** the topology child requests completion
- **THEN** it supplies its own route/identity/compatibility evidence and relevant check results; it cannot defer those checks to the later validation-harness child.

### Requirement: VA-04 Validation covers contention and liveness
The validation suite SHALL include idle latency, packet-boundary byte accounting, sustained throughput, dual-fabric traffic, wrap routing, finite-buffer backpressure, admissible traffic drain, memory-alias contention, and pipeline dependencies as their mechanisms are introduced. Multicast and synchronization SHALL add destination delivery, shared-link accounting, ordering, and liveness coverage when implemented. Tests SHALL distinguish analytical checks of the chosen model from independent hardware evidence.

#### Scenario: Single-flow timing passes but shared bandwidth is wrong
- **WHEN** independent flows target aliases of one physical memory resource
- **THEN** the suite checks aggregate service and completion behavior and detects duplicated resource bandwidth even if individual-flow latency tests pass.

### Requirement: VA-05 Calibration is separated from evaluation
Calibration SHALL record fitted parameters, constraints, reference provenance, fitting workloads, and held-out evaluation workloads. Acceptance metrics and tolerances SHALL be defined before evaluating the corresponding held-out results, be justified by the reference and abstraction, and remain scoped to the tested configurations. Unmeasured parameters SHALL not acquire calibrated status through fitting to documentation alone.

#### Scenario: A parameter is fitted to a bandwidth microbenchmark
- **WHEN** that fitted model is evaluated
- **THEN** the fitting case is identified separately, independent held-out cases report their errors, and the report does not generalize accuracy to untested operations or device conditions.

### Requirement: VA-06 Fidelity and compatibility are visible outputs
Each Wormhole run SHALL expose its effective profile, enabled capabilities, approximation assumptions, unsupported requested features, and evidence coverage. Legacy synthetic simulation, traces, and explicitly supported downstream workflows SHALL retain executable regression checks. Hardware failure injection SHALL be labeled as a simulation experiment unless matched hardware evidence exists.

#### Scenario: A failure experiment uses an uncalibrated memory model
- **WHEN** its results are exported
- **THEN** the output records both the injected failure conditions and the memory timing evidence limit without presenting the result as a measured Wormhole prediction.

### Requirement: VA-07 Umbrella completion requires delivered evidence
The umbrella SHALL track child changes and requirement coverage explicitly. Planning completion SHALL remain distinct from implementation completion. A requirement SHALL enter the delivered specification baseline only with its implementing child's reviewed evidence; parent and child deltas MUST be reconciled before archival to avoid duplicated or premature specification delivery.

#### Scenario: All planning documents exist but no child is applied
- **WHEN** the umbrella status is reviewed
- **THEN** the plan is ready for staged work while implementation milestones remain unchecked and Wormhole runtime support is not claimed.
