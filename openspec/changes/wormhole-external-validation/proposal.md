## Why

The delivered Wormhole simulator has reproducible internal validation and strict
reference-import contracts, but it still has no admitted real functional capture
or Wormhole silicon measurement. A model-consistency pass therefore cannot answer
whether the supported abstractions agree with an independent implementation or
how accurately their configurable timing parameters describe a measured device.

## What Changes

- Define a versioned external-validation campaign that binds one simulator case
  to independently collected functional and timing evidence through exact source,
  workload, layout, mapping, clock, measurement-boundary and artifact identities.
- Add reproducible capture recipes for a pinned ttsim/TT-Metal functional producer
  and for TT-Metal device-profiler runs on a named Wormhole device. Collection
  remains an explicit operation on a compatible external host; the validation CLI
  does not install vendor tools, access hardware implicitly or fetch mutable data.
- Add a conversion and admission boundary that turns raw producer artifacts into
  existing `validation_reference` v1 documents while retaining raw bytes, hashes,
  extractor versions, unknown metadata and collection diagnostics.
- Extend normalized validation observations with explicitly named model intervals
  for the finite supported subset so same-core profiler zones can be compared to
  corresponding transport, memory-service or compute-stage spans. Unsupported
  host, full-kernel, cross-core timestamp and unmodeled intervals remain blocked.
- Deliver an initial paired campaign for finite one-hop transport, addressed memory
  service and bounded compute work. Functional effects and causal order are checked
  before any timing claim; each metric has a predeclared unit, clock mapping,
  aggregation policy, tolerance and rationale.
- Calibrate only existing memory bandwidth/fixed-latency and compute rate/setup
  fields using measured fit captures, freeze the selected vector, then evaluate it
  against disjoint held-out captures. Structural hardware parameters stay fixed and
  configurable; failed or ambiguous results remain visible.
- Publish a portable evidence bundle and report that distinguish collection,
  import, functional agreement, measured timing, calibration and held-out evidence.
  An unavailable external environment produces an explicit blocked outcome rather
  than a synthetic success.
- Implement in incremental validated parts, committing each completed part before
  continuing. Pushing remains a separate request.

## Capabilities

### New Capabilities

- `wormhole-external-validation`: Reproducible external capture campaigns,
  boundary-matched functional/timing comparison, measured calibration and sealed
  held-out evaluation for the supported finite Wormhole model.

### Modified Capabilities

None. The existing `wormhole-validation-harness` contract already requires strict
provenance, compatible external references and bounded calibration. This change
adds a separately scoped campaign capability and uses that harness without weakening
its evidence tiers or rewriting its version-1 document meanings.

## Impact

- **Simulator and validation:** Add campaign/capture schemas, producer-specific
  converters, supported interval normalization, orchestration and reporting under
  `simulator_detailed/`. Existing replay result kinds, model algorithms, hardware
  dimensions, clocks, widths, capacities and rate inputs retain their meanings.
- **JSON and evidence:** Add versioned campaign and evidence-bundle documents plus
  example inputs. Existing `validation_suite`, `validation_reference`,
  `validation_report`, `calibration_plan` and `calibration_result` v1 documents
  remain readable. Raw captures and generated reports use explicit paths and
  content hashes; no large vendor checkout or unbounded generated log is committed.
- **External systems:** A compatible Linux host with a pinned ttsim/TT-Metal build
  is required for functional collection. A named Wormhole device, matching software
  and firmware, and the TT-Metal device profiler are required for silicon timing.
  The current host has neither environment, so implementation can complete capture
  tooling and blocked-path checks here but cannot honestly complete measured cases
  until those prerequisites are supplied.
- **Detector and RL:** Predictor features/checkpoints, hardware embeddings,
  observations, actions, rewards and the existing root 4x4 mesh + Darknet19 +
  fail-dataset workflow do not change. New document kinds are rejected at legacy
  consumer boundaries before optional ML imports.
- **Dependencies and compatibility:** Default offline tests need no new runtime
  dependency or network access. External commands are never accepted as arbitrary
  shell strings; recipes use named producers and explicit arguments. Existing
  simulator and validation suites remain compatible.
