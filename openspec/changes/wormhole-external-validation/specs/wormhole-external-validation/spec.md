## Purpose

Define reproducible campaigns that compare the finite Wormhole model with
independent functional execution and explicitly scoped measurements from a real
Wormhole device without weakening provenance, compatibility or fidelity limits.

## ADDED Requirements

### Requirement: EV-01 External campaigns are explicit and bounded
The system SHALL admit a versioned external-validation campaign that declares a
finite set of named functional and timing cases, producer types, simulator inputs,
capture recipes, reference identities, comparison policies, resource budgets and
required evidence gates. Each producer invocation SHALL use a supported structured
recipe and explicit arguments; arbitrary shell commands, implicit downloads and
implicit hardware discovery MUST be rejected. Admission SHALL finish before any
external producer or simulator case runs.

#### Scenario: Complete campaign admits
- **WHEN** a campaign selects supported ttsim and Wormhole-profiler producers with finite repetitions, cases and time budgets
- **THEN** the system resolves its inputs and publishes an immutable effective plan before collection begins.

#### Scenario: Unbounded or executable input is rejected
- **WHEN** a campaign contains an unlimited repetition count, unknown producer, mutable unpinned source or arbitrary command string
- **THEN** admission fails without launching a process, accessing a device or replacing existing evidence.

### Requirement: EV-02 Capture bundles preserve source and environment identity
Every external capture bundle SHALL retain raw artifact bytes or explicit portable
references, SHA-256 identities, producer and extractor versions, source revision,
build configuration, host environment, device identity where applicable, software
and firmware versions, clocks, enabled layout, workload/mapping identity,
instrumentation settings and collection diagnostics. Unknown values SHALL remain
unknown with reasons and SHALL restrict supported claims. Integrity verification
MUST NOT be represented as authentication of the capture's origin.

#### Scenario: Raw capture changes after collection
- **WHEN** raw bytes no longer match the capture manifest
- **THEN** conversion and comparison fail before the artifact contributes to functional or timing evidence.

#### Scenario: Device metadata is incomplete
- **WHEN** a hardware capture has valid profiler rows but lacks a known firmware revision or enabled layout
- **THEN** the bundle retains the capture and missing-field reasons while the affected measured comparison remains blocked.

### Requirement: EV-03 Paired executions use equivalent admitted workloads
An external case SHALL bind the simulator, functional producer and silicon producer
to one canonical workload intent, including operation kind, byte/work counts,
addresses or tensor shape, data type/fidelity, source/destination mapping,
fabric/path constraints, synchronization/completion rule and configured clocks.
The system SHALL expose material differences and SHALL block paired claims when
equivalence cannot be established. Simulator-specific code paths MUST NOT qualify
as functional agreement with a different silicon path.

#### Scenario: Same program and mapping are paired
- **WHEN** ttsim and silicon execute byte-identical host/device artifacts under the declared equivalent layout and the simulator case expresses the same finite operation
- **THEN** the report records one paired workload identity and lists each abstraction made by the model.

#### Scenario: Producer paths diverge
- **WHEN** the ttsim run uses a simulator-only branch or the silicon run changes operation size, mapping, fidelity or completion semantics
- **THEN** the campaign blocks cross-producer agreement instead of normalizing the difference away.

### Requirement: EV-04 Functional evidence precedes timing evidence
The campaign SHALL validate supported functional outcomes before accepting timing
from the same case. Functional checks SHALL cover declared destination effects,
byte ranges/counts, completion markers and required causal order through explicit
identity mappings. A deterministic payload/status digest MAY establish the
external program's own finite result when independently derived from the input,
but MUST NOT be presented as numerical tensor equivalence produced by the abstract
simulator. A failed, unavailable or incomparable required functional check SHALL
prevent that case from contributing silicon-timing or calibration evidence.

#### Scenario: Independent ttsim result agrees
- **WHEN** a pinned ttsim execution produces the declared addressed effects, completion markers and causal relations for an admitted case
- **THEN** the report marks functional-reference evidence passed for that finite scope and retains the producer/raw/extractor identities.

#### Scenario: Timing is plausible but data delivery is wrong
- **WHEN** profiler duration is within tolerance but the functional capture has a missing destination effect or incorrect deterministic result digest
- **THEN** the case fails functional validation and contributes no accepted timing or calibration evidence.

### Requirement: EV-05 Silicon timing uses supported measurement boundaries
Each measured metric SHALL map one same-clock-domain hardware interval to one
explicit simulator interval with matching completion scope, repetitions, warm-up
policy and aggregation. Supported initial boundaries SHALL be finite source-local
round-trip completion, memory service and compute-stage service for declared cases.
The report SHALL preserve original integer counters and clock rates before any
conversion. Cross-core timestamp subtraction, unsynchronized devices, host dispatch,
full kernels containing unmodeled work and undocumented profiler-overhead correction
MUST remain blocked.

#### Scenario: Same-core acknowledged interval is mapped
- **WHEN** a Wormhole profiler zone begins before an operation and ends after its explicit acknowledgement on one RISC and the simulator exports the corresponding interval
- **THEN** the system compares the declared aggregate in cycles at equal known frequency or in seconds through explicit clock conversion.

#### Scenario: Profiler window includes unmodeled work
- **WHEN** the only supplied interval includes host launch, unrelated kernel stages or timestamps from different cores without a verified synchronization model
- **THEN** timing comparison is blocked with the incompatible boundary identified.

### Requirement: EV-06 Measured calibration is sealed and held out
Measured calibration SHALL target only the existing typed memory
`bytes_per_cycle`/`fixed_latency_cycles` and compute
`work_per_native_cycle`/`setup_native_cycles` fields. Candidate ranges, loss,
tolerances, fit cases and held-out cases SHALL be fixed before evaluation. Fit and
held-out data MUST differ by semantic workload/condition and capture group. The
selected vector, fit evidence and effective configuration SHALL be frozen before
held-out observations are evaluated. Topology, clocks, widths, capacities,
harvesting, fidelity and protocol structure SHALL remain declared inputs rather
than fitted facts.

#### Scenario: Measured fit passes held-out cases
- **WHEN** an admitted hardware-backed candidate minimizes the predeclared fit loss and its frozen vector passes every required held-out tolerance
- **THEN** the result reports measured calibration only for the named device, workloads, mappings, boundaries and collection conditions.

#### Scenario: Best fit fails evaluation or ties
- **WHEN** the selected candidate fails a held-out case or several vectors are indistinguishable at the captured resolution
- **THEN** the failure or ambiguity remains visible and no unique physical parameter or broader accuracy claim is inferred.

### Requirement: EV-07 Reports expose evidence lineage and claim scope
The campaign report SHALL link each claim to its campaign, raw capture, converted
reference, simulator run, comparison, calibration and held-out identities. It SHALL
separately report collection, import, functional-reference, silicon-timing,
calibration and evaluation outcomes as passed, failed, blocked or not run with
reasons. A summary SHALL include signed/absolute error and relative error where
defined, sample count and dispersion, but MUST NOT collapse missing evidence into
an accuracy percentage or promote a partial case to full-device conformance.

#### Scenario: External environment is unavailable
- **WHEN** a required ttsim installation or Wormhole device is absent
- **THEN** the portable plan and offline validation can pass while collection and dependent evidence remain explicitly blocked.

#### Scenario: Mixed campaign outcomes
- **WHEN** functional cases pass, one timing case fails tolerance and another lacks metadata
- **THEN** the report preserves all three outcomes and does not advertise the campaign as validated silicon timing.

### Requirement: EV-08 Existing workflows remain compatible and offline
Existing simulator inputs/results, validation documents, replay CLIs, predictor
features/checkpoints, hardware embeddings and RL observation/action/reward shapes
SHALL retain their supported behavior. The root 4x4 mesh, Darknet19 and fail-dataset
workflow SHALL remain unchanged. New campaign and bundle kinds SHALL be rejected by
legacy consumers before optional ML loading. Normal unit tests and existing offline
validation SHALL require no network, vendor installation or physical device.

#### Scenario: Existing offline validation runs without external tools
- **WHEN** the current validation suite runs on a host without ttsim, TT-Metal or a Tenstorrent device
- **THEN** its existing required checks retain their outcomes and no external collection is attempted.

#### Scenario: Campaign document reaches a legacy consumer
- **WHEN** a predictor, embedding or legacy replay entry point receives an external campaign or evidence bundle
- **THEN** it rejects the unsupported document kind without changing feature dimensions, loading a model or reinterpreting it as an existing workload.
