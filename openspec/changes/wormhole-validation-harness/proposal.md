## Why

The first five Wormhole children now expose executable profile/topology, transport, addressed memory and bounded compute models, with substantial independent tests. Their evidence is still distributed across tests and delivery notes: there is no common reproducible benchmark report, reference-import boundary, comparison admission check, or calibration/held-out evaluation workflow, so passing model tests cannot establish silicon timing accuracy.

## What Changes

- Deliver child 6 of `wormhole-single-chip-simulation-plan`, concretizing VA-01..07 for the mechanisms already implemented; multicast/synchronization coverage remains assigned to child 7.
- Introduce strict version-1 `validation_suite`, `validation_reference`, `validation_report`, `calibration_plan` and `calibration_result` JSON contracts. Record input/code/environment identities, effective profile/layout/clocks, workload and measurement conditions, evidence tiers, per-check outcomes and requirement coverage.
- Add an offline, finite benchmark runner using existing public profile/topology, torus, memory and compute interfaces. Normalize exported observations and independently check routing, bytes, resource ownership, causal stages, contention and drain. Keep architecture/model checks separate from functional-reference and silicon-timing comparisons.
- Import explicitly identified normalized functional observations and a bounded TT-Metal device-profiler CSV subset with sidecar metadata. Check provenance, units, clocks, layout, workload, instrumentation, window and aggregation before comparison; missing tools/data or unsupported observations cannot become passes. Synthetic fixtures exercise importers without pretending to be vendor or hardware evidence.
- Implement deterministic bounded parameter search over selected existing effective memory/compute timing parameters. Separate fitting from held-out cases, freeze the selected configuration and declared tolerances before evaluation, and retain reference provenance and candidate results. Synthetic fitting demonstrates mechanics only.
- Deliver a CLI, small generic/Wormhole suites, malformed/mismatched reference fixtures, compatibility gates, requirement-to-evidence reports and reproducible usage documentation. Keep all device parameters and comparison thresholds explicit in versioned inputs.
- Implement in seven incremental parts, each with relevant tests, strict type/scoped lint checks, a progress record and a commit before the next part.

## Capabilities

### New Capabilities

- `wormhole-validation-harness`: Reproducible finite model validation, independently audited observations, admitted reference comparisons, bounded calibration/evaluation and evidence-aware delivery reports.

### Modified Capabilities

None. There are no delivered main specs under `openspec/specs/` for this capability. The umbrella's `wormhole-validation` target delta remains a planning contract; this child supplies concrete evidence without prematurely syncing or archiving it.

## Impact

- **Simulator:** Add `configs/schemas/validation.py`, a focused `validation/` package and `validate_wormhole.py` under `simulator_detailed/`. Wrap existing public results; preserve `topology_replay_result` v1/v2, `memory_replay_result` v1 and `compute_workload_result` v1 behavior and established timing/digest fixtures. Retain the existing NMC `benchmark_workloads.py` API. Extract reusable pure audits from tests only where useful and without importing test classes into production code or changing simulation algorithms.
- **JSON/traces/assets:** Add the five named contracts, explicitly nested normalized observations, provenance and check/coverage records, plus examples under `simulator_detailed/configs/validation/`. Existing architecture/mapping/failure inputs, packet/service/stage events and legacy trace-window structures remain compatible. Generated reports/logs use explicit output paths or temporary test directories; no model artifacts, backups or vendor source trees are added.
- **Detector/predictor:** Keep 7-D runtime features, legacy graph construction and checkpoints. Guard new validation document kinds before optional Torch/PyG work where those documents can reach existing consumers; no new accuracy or model-training claim.
- **RL/embedding:** Preserve current mesh constraints, detailed 4-D hardware features and root `MultiDiscrete([num_layers, num_cores, num_cores, num_ops])` with replace/split/shift/remove operations. Validation does not enable Wormhole training or change observations, rewards or checkpoints.
- **Compatibility/dependencies:** Preserve detailed synthetic/DMA/fail-slow workflows and separately exercise root 4x4 mesh + Darknet19 execution with temporary outputs. Default validation uses existing Python dependencies and requires neither network access, ttsim nor a physical card. External evidence is supplied explicitly; optional dependency failures are visible blocked checks.
- **This commit:** Planning artifacts only. Creating this change delivers no harness implementation, numerical tensor simulation, real reference execution or measured timing calibration.
