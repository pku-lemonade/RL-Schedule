## Purpose

Make the delivered finite single-chip simulator models reproducibly testable against independent model expectations and explicitly qualified external observations, while keeping calibration, held-out evaluation and unsupported fidelity claims separate.

## ADDED Requirements

### Requirement: VA-D01 Explicit versioned validation admission
The validation interface SHALL admit version-1 `validation_suite`, `validation_reference`, `validation_report`, `calibration_plan` and `calibration_result` documents with explicit kinds, finite values, unique identities and supported policies. Suites SHALL select a finite nonempty set of cases/checks from named supported adapters, identify required checks and declare applicable execution budgets. Device-specific dimensions, clocks, widths, rates, capacities and thresholds MUST come from the admitted inputs or identified fixtures. Unknown fields, ambiguous identities, unsupported versions, arbitrary executable expressions and invalid simulator inputs SHALL be rejected before case execution or replacement of an existing output. Relative assets SHALL resolve from their declaring document.

#### Scenario: Suite runs from another working directory
- **WHEN** a valid suite references workloads and profiles through relative paths and is launched from outside its directory
- **THEN** it admits the same effective cases and identities as a launch from its own directory without changing the source assets.

#### Scenario: Malformed or unbounded request
- **WHEN** a suite has no required checks, duplicate case IDs, nonfinite parameters, an unsupported adapter or an over-budget case expansion
- **THEN** admission identifies the invalid field/case and neither executes the cases nor overwrites an existing report.

### Requirement: VA-D02 Evidence tiers and check outcomes remain distinct
Reports SHALL distinguish architecture/protocol, model-invariant, functional-reference and silicon-timing evidence, separately from simulation execution and requirement coverage. Checks SHALL expose `pass`, `fail`, `blocked`, `unsupported` or `not_run` with reasons. Dependency/data unavailability SHALL be blocked, observed disagreement SHALL fail, and synthetic/reference-import tests MUST NOT imply real functional or hardware agreement. Aggregate status SHALL fail on any executed failed check, otherwise be incomplete when a required check has not passed, and pass only when at least one required check exists and every required check actually passes. Optional unavailable checks SHALL remain visible. Missing measured comparisons SHALL leave silicon timing unvalidated without an accuracy percentage.

#### Scenario: Offline suite passes without a card
- **WHEN** all required architecture/model checks pass and optional external reference checks lack data
- **THEN** the report passes its declared offline scope, lists unavailable external checks and reports unvalidated silicon timing.

#### Scenario: Incomplete execution is the subject of a check
- **WHEN** a deliberately horizon-limited case passes its pending-state/conservation expectations
- **THEN** the check can pass while execution remains incomplete, and unfinished planned work is excluded from completed-run throughput and successful drain claims.

### Requirement: VA-D03 Run and reference identities are reproducible
Reports SHALL identify source/input bytes, effective plans, selected adapters/checks, simulator revision and dirty source content, relevant environment versions, effective profile/layout/resource bindings, clocks, workload policies and seed or explicit deterministic status. Portable identities MUST exclude diagnostic absolute paths, wall-clock timestamps and self-referential output content. Imported references SHALL identify raw artifact hashes, source URL/revision or snapshot identity, extraction method/version, original/normalized units and known collection conditions. Unknown metadata SHALL remain explicit and restrict supported claims; integrity checks MUST NOT be represented as authentication of a supplied hardware origin.

#### Scenario: Same revision with a modified implementation
- **WHEN** the simulator runs from two checkouts with the same Git commit but differing relevant uncommitted source bytes
- **THEN** the run records distinguish their source identities instead of reporting them as the same implementation.

#### Scenario: Artifact does not match its sidecar
- **WHEN** supplied raw reference bytes do not match the recorded digest
- **THEN** import rejects the artifact and does not compare its observations or silently replace the recorded digest.

### Requirement: VA-D04 Normalization preserves observable semantics
Normalization SHALL preserve source result identity, integer counters/bytes/work, endpoint/resource/fabric identity, supported addressed effects, causal relations, time domains and terminal/pending state. It SHALL distinguish planned work from observed work and local completion from target visibility. Unsupported or absent observations MUST remain absent rather than being fabricated. Functional comparisons SHALL use explicitly mapped destinations, byte extents/counts and required partial ordering, without requiring an incidental global event order or claiming tensor-value equivalence. Time conversion SHALL require explicit compatible units and clocks and retain original values.

#### Scenario: Independent events have a different order
- **WHEN** a reference and simulation contain the same mapped addressed effects and all required causal edges but independent events are interleaved differently
- **THEN** the supported functional observations compare successfully without requiring identical timestamps or event-list order.

#### Scenario: Large cycle counter and unsupported tensor output
- **WHEN** a reference includes integer cycle counters beyond exact floating-point integer range and requests a tensor-value comparison
- **THEN** counter differences retain integer precision, and tensor-value equivalence is reported unsupported rather than inferred from completed work counts.

### Requirement: VA-D05 Independent audits detect model invariant violations
The harness SHALL check applicable route/endpoint identities, payload/wire/service arithmetic, shared physical ownership, version/generation transitions, finite capacities, compute work and causal stages, and completion/drain using expectations independently derived from admitted inputs and exported observations. A producer's summary totals alone SHALL NOT serve as its own correctness oracle. Generic checks SHALL derive geometry and quantities from configuration. The audit suite SHALL demonstrate detection of corrupted observations and retain independent analytical/golden cases separate from compatibility snapshots. These checks SHALL be labeled as model or architecture evidence, not measured hardware timing.

#### Scenario: Aliases duplicate physical service
- **WHEN** two endpoints alias one declared physical memory resource but an observation trace grants more simultaneous service than that resource permits
- **THEN** an independent audit fails even if each individual request latency matches its isolated expected latency.

#### Scenario: Summary looks correct but trace is invalid
- **WHEN** an exported result retains plausible totals but loses a packet, double-releases a slot or starts arithmetic before input publication
- **THEN** the applicable event-level audit identifies the violation and prevents the case from passing.

### Requirement: VA-D06 Finite benchmark and regression gates expose actual outcomes
The runnable catalog SHALL cover implemented idle/hop latency, packet boundaries, finite interval throughput, dual fabrics, wrap routing, shared-link contention, finite backpressure, memory aliases, pipeline dependencies and drain. Metrics SHALL define numerator, denominator, observation window, warm-up exclusions and completion scope. Cases SHALL execute with declared finite budgets and isolated state; enforced timeouts SHALL be explicit failed bounded checks, not fabricated hardware deadlock findings. Named regression gates SHALL preserve actual test/type/lint outcomes and distinguish missing prerequisites from behavioral failures and skipped tests. Multicast and synchronization checks SHALL remain pending until their mechanisms exist.

#### Scenario: Latency succeeds but aggregate throughput fails
- **WHEN** isolated transfers satisfy latency expectations but simultaneous traffic exceeds the declared shared-link or memory capacity
- **THEN** the contention case fails independently of the isolated latency passes.

#### Scenario: Optional dependency is unavailable
- **WHEN** a selected consumer gate cannot import a declared optional dependency while the offline runtime cases can execute
- **THEN** that gate is blocked with its import diagnostic, offline cases retain their real outcomes, and the unavailable gate is not counted as a pass.

#### Scenario: Executed assertion fails or case times out
- **WHEN** an admitted test fails an assertion or a bounded runtime case exceeds its enforced wall-time budget
- **THEN** the check fails with actual diagnostics rather than being reclassified as a missing dependency or successful drain.

### Requirement: VA-D07 External comparison requires compatible references
The harness SHALL import explicitly identified normalized functional observations and a named supported device-profiler CSV format with sidecar metadata. Profiling import SHALL pair unambiguous same-device/core/processor/zone observations, preserve original counters, identify repetitions/warm-ups and apply a declared aggregation. Ambiguous pairs, unsupported formats and contradictory metadata SHALL be rejected. A silicon-timing comparison SHALL require sufficient device/software/profile/layout/clock/workload/instrumentation/window metadata and a supported mapping to simulator observations. Missing or incompatible conditions SHALL block comparison with specific reasons. Functional-reference or synthetic timing MUST NOT be promoted to silicon evidence, and a documentation sample from another chip MUST NOT be treated as a Wormhole capture.

#### Scenario: Public measurement lacks layout or window
- **WHEN** a reference has a known origin and timing value but lacks required enabled-layout or measurement-boundary information
- **THEN** the importer retains the known provenance while the precise timing comparison remains blocked.

#### Scenario: Unsupported clock or measurement boundary
- **WHEN** a supplied profiler interval subtracts unsynchronized cross-core timestamps or includes host/kernel work absent from the corresponding simulator interval
- **THEN** the timing comparison is blocked instead of assuming synchronized clocks, zero overhead or matching scope.

#### Scenario: Synthetic profiler fixture is parsed correctly
- **WHEN** a labeled synthetic CSV fixture exercises valid begin/end pairing, large counters and repeat aggregation
- **THEN** adapter behavior can pass its tests while the reference remains synthetic and cannot authorize a measured timing claim.

### Requirement: VA-D08 Bounded calibration uses independent held-out evaluation
Calibration SHALL perform actual deterministic bounded candidate evaluation over declared effective memory bandwidth/fixed latency and compute rate/setup parameters, using full workload admission and immutable source inputs. Structural parameters, clocks, capacities and protocol widths SHALL NOT be fitted. Plans SHALL predeclare candidate bounds/budget, metrics, loss scales/weights, fit and held-out cases, reference identities and justified acceptance tolerances. Splits SHALL be disjoint by semantic workload/condition and measurement-group identity, not only case name. Candidate selection SHALL use fitting observations only; its chosen vector and configuration digest MUST be fixed before held-out execution and remain unchanged by evaluation. Invalid candidates, ties, absent references and unsuccessful evaluation SHALL remain visible. Synthetic fitting SHALL prove mechanics only; measured claims SHALL be limited to admitted measured cases and conditions.

#### Scenario: Renamed fitting case appears in evaluation
- **WHEN** an evaluation case duplicates a fitting workload/condition or measurement group under a new ID
- **THEN** calibration admission rejects the split before candidate execution.

#### Scenario: Excellent fit fails unseen cases
- **WHEN** a candidate has the best fitting loss but exceeds a predeclared held-out metric tolerance
- **THEN** evaluation fails and retains the frozen candidate and tolerance without refitting or relabeling the case as training data.

#### Scenario: Candidate changes forbidden facts or has no valid fit
- **WHEN** a plan targets a topology/clock/capacity field, exceeds its candidate budget, or every otherwise admitted candidate fails fitting execution
- **THEN** invalid plans are rejected or failed candidates are reported without a selected vector, and source profile/workload files remain unchanged.

### Requirement: VA-D09 CLI output and fidelity claims are inspectable
The CLI SHALL expose explicit suite, reference-import and calibration modes with parseable versioned JSON and an optional output destination. Valid reports SHALL include effective capabilities, assumptions, unsupported requests, checks, reference/normalized-result identities and scoped coverage. Invalid input SHALL preserve existing output; publication of a complete serialized document SHALL be atomic. Exit codes SHALL distinguish success (0), observed validation/evaluation failure (1), invalid invocation/input (2) and incomplete required evidence (3). A successful import MUST NOT claim successful model validation. Fault injection SHALL remain labeled a simulation experiment without matched measured evidence. Default execution SHALL require neither network access nor vendor tools/hardware.

#### Scenario: Invalid input targets an existing report
- **WHEN** a malformed suite or calibration document is passed with an existing output path
- **THEN** the CLI returns invalid-input status with a diagnostic and leaves that file intact.

#### Scenario: Required hardware evidence is absent
- **WHEN** a valid suite requires a measured comparison but the supplied capture is unavailable
- **THEN** the CLI emits a report with the blocked comparison and unvalidated timing, and returns incomplete-evidence status rather than success.

### Requirement: VA-D10 Existing simulator and consumer contracts remain compatible
Existing detailed mesh/DMA/fail-slow, profile/topology, torus, memory and compute public APIs/result formats SHALL retain their supported behavior and established regression expectations. The root 4x4 mesh + Darknet19 mapping/execution/trace path SHALL retain a separate executable smoke check using temporary outputs. New validation document kinds SHALL be rejected by incompatible legacy consumers before optional tensor/model loading. Detector features/checkpoints, RL observations/actions/rewards and embedding dimensions MUST NOT be silently reinterpreted. Historical failed checks and unavailable optional checks SHALL remain accurately documented rather than being presented as validated coverage.

#### Scenario: Existing replay is wrapped by validation
- **WHEN** a previously supported memory or compute workload executes directly and through the validation adapter
- **THEN** underlying events, effective identities, accounting and completion semantics agree, and only the enclosing evidence report is new.

#### Scenario: Validation report is passed to a legacy consumer
- **WHEN** a predictor or embedding entry point receives one of the new validation document kinds
- **THEN** it reports an unsupported input format before importing optional ML dependencies or loading a model.

### Requirement: VA-D11 Coverage records delivered evidence and pending work
The child SHALL provide a requirement-to-check/command/evidence mapping for VA-D01..11 and parent VA-01..07, incorporating predecessor limitations and actual outcomes. Every implementation part SHALL record relevant test/type/lint checks and be committed before the next part. Coverage SHALL distinguish planning, implementation, checked model behavior and available external validation. Pending multicast/synchronization and unvalidated silicon timing SHALL remain explicit; harness success MUST NOT automatically complete umbrella milestones or synchronize/archive its specification. The next child's exploration SHALL receive the actual findings and unresolved limitations.

#### Scenario: Harness passes before multicast exists
- **WHEN** all required delivered-mechanism checks pass but multicast/synchronization is not implemented and no hardware captures were supplied
- **THEN** the delivery report records the achieved model-validation scope, pending mechanism coverage and unvalidated silicon timing without claiming the umbrella is complete.

#### Scenario: Planning artifacts are complete
- **WHEN** this change has a proposal, design, specification and unchecked implementation tasks
- **THEN** its status is ready for staged implementation and no runtime validation capability is claimed as delivered.
