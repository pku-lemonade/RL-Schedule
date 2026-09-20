## 1. Campaign contracts and fail-closed admission

- [ ] 1.1 Add strict version-1 `external_validation_campaign`, `external_capture_bundle` and `external_validation_report` schemas with finite producer/case/budget, artifact-lineage, condition, boundary-map and outcome records; verify JSON round trips preserve integer counters, unknown metadata reasons and exact typed identities.
- [ ] 1.2 Implement pure preflight admission for named producer adapters, source/build identities, finite repetitions/timeouts, declared output paths and supported case families; verify unknown producers, mutable sources, unbounded work, arbitrary shell strings and duplicate/path-escaping identities fail before process or device access.
- [ ] 1.3 Add the two new document kinds to legacy replay/predictor/embedding guards without importing optional ML code; verify existing document-kind tests and focused malformed-consumer tests retain the current 7-D/4-D features, checkpoints and four-coordinate root actions.
- [ ] 1.4 Add valid, unavailable-environment and adversarial synthetic campaign/bundle fixtures under `simulator_detailed/configs/validation/external/`; verify fixture hashes, relative-path resolution and output-preservation failures from another working directory.
- [ ] 1.5 Run the focused contract/identity/consumer tests, strict Pyright, scoped Ruff and strict OpenSpec validation; record actual results and source/fixture identities in `progress.md`, then commit Part 1 before continuing.

## 2. Supported model intervals and comparison admission

- [ ] 2.1 Derive `operation_submission_to_acknowledged_completion`, `memory_service_begin_to_end` and `compute_resource_acquire_to_release` metrics from existing normalized lifecycle/service/resource events without changing raw replay results; verify exact start/end event identities, clocks, completion scopes and configurable values across memory, compute and mixed fixtures.
- [ ] 2.2 Add typed interval selection and matching repetition/aggregation records to campaign admission, and execute deterministic model repetitions when measured references retain multiple runs; verify one model sample cannot silently represent a repeated hardware aggregate.
- [ ] 2.3 Replace the hard-coded total-run comparison gate with a strict supported-boundary registry and semantic identity checks; verify total-run compatibility remains intact while host dispatch, full kernels, ambiguous intervals, cross-core subtraction and undocumented overhead correction remain blocked.
- [ ] 2.4 Add independent positive/corruption tests for each interval and for cycles/seconds conversion, large counters, clock-domain mappings, warm-ups, mean/median aggregation and sample dispersion; verify mismatched frequency, completion or aggregation cannot pass.
- [ ] 2.5 Run focused normalization/comparison and existing validation regression tests, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` with measured outcomes and commit Part 2 before continuing.

## 3. Portable capture kit and ttsim functional evidence

- [ ] 3.1 Implement a capture-kit generator with named `ttsim_tt_metal_v1` recipes, fixed argument construction, source/build/binary manifests, finite budgets and explicit output contracts; verify generated kits are deterministic, portable and contain no repository-local absolute paths or evaluated shell text.
- [ ] 3.2 Add finite TT-Metal producer assets for `noc_ack_roundtrip`, `dram_read_return` and `compute_service`, including deterministic sentinel/status output and explicit completion markers; verify build inputs and operation/layout/fidelity parameters are fully recorded rather than defaulted.
- [ ] 3.3 Implement conversion from the producer's raw functional record into hash-verified `validation_reference` v1 observations with explicit effect/event mappings; verify altered payloads, missing markers, wrong addresses/counts and simulator-specific-path declarations fail or block as specified.
- [ ] 3.4 On a compatible pinned ttsim/TT-Metal worker, build and run the three case families with the generated kit and return raw capture bundles; verify the imported references are classified `functional_capture`, all required functional comparisons pass, and no ttsim timing is classified as silicon evidence. Leave this task unchecked with exact blocked prerequisites if no worker is supplied.
- [ ] 3.5 Run converter/kit tests plus the actual ttsim evidence checks when available, strict Pyright, scoped Ruff and strict OpenSpec validation; record producer revisions, raw hashes, results and limitations in `progress.md`, then commit Part 3 only when its required functional evidence is complete.

## 4. Wormhole silicon collection and profiler import

- [ ] 4.1 Implement the named `wormhole_tt_metal_profiler_v1` collector with explicit device selection, inventory/software/firmware/clock capture, finite repetitions/warm-ups and safe unavailable-device diagnostics; verify ordinary hosts return a blocked bundle without probing unrelated devices or fabricating metadata.
- [ ] 4.2 Instrument the same pinned producer artifacts with sparse same-RISC zones around the three supported boundaries, retaining functional outputs alongside profiling; verify source/line/zone identities are unique and the ttsim/silicon binary and effective workload manifests match.
- [ ] 4.3 Extend profiler conversion only as needed for live pinned Wormhole CSV while retaining raw integer counters, samples and metadata; verify unsupported headers/phases, ambiguous/nested pairs, wrong architecture/frequency and multi-core/device subtraction are rejected.
- [ ] 4.4 On a compatible named Wormhole worker, execute the three finite case families and return functional records, profiler CSV and complete capture manifests; verify functional gates pass before each capture is admitted as `hardware_capture`. Leave this task unchecked with exact blocked prerequisites if no device environment is supplied.
- [ ] 4.5 Run collector/importer tests and actual hardware import checks, strict Pyright, scoped Ruff and strict OpenSpec validation; record board/software/firmware/clocks, raw hashes, sample statistics and outcomes in `progress.md`, then commit Part 4 only when its required silicon captures are complete.

## 5. Paired campaign execution and scoped reports

- [ ] 5.1 Implement a resumable campaign state machine for planned, collected, imported, functionally checked and timing-checked cases using only hash-addressed intermediate artifacts; verify interruption/restart does not rerun completed producers or accept stale/changed outputs.
- [ ] 5.2 Implement canonical equivalence checks across simulator, ttsim and silicon source/binary, operation, byte/work count, address/shape/fidelity, mapping, fabric, clock and completion fields; verify every material mismatch blocks the paired claim with a field-level reason.
- [ ] 5.3 Gate silicon timing on required ttsim and silicon functional effects/order/status checks, keeping deterministic external sentinel digests separate from abstract model values; verify a plausible duration with a corrupted effect contributes no timing evidence.
- [ ] 5.4 Run matched comparisons for the three case families with predeclared boundaries, repetitions, units, domain maps and tolerances, and emit a lineage-complete report with errors/sample dispersion and scoped claims; verify mixed pass/fail/blocked cases remain distinct.
- [ ] 5.5 Run campaign integration tests and the actual paired external matrix, strict Pyright, scoped Ruff and strict OpenSpec validation; update `progress.md` with exact report/capture identities and commit Part 5 before continuing.

## 6. Measured fitting and sealed held-out evaluation

- [ ] 6.1 Generate bounded existing-format calibration plans from admitted campaign cases for only memory bandwidth/fixed latency and compute work rate/setup; verify topology, clocks, widths, capacities, harvesting, fidelity and protocol structure cannot become fit targets.
- [ ] 6.2 Seal candidate order, metric scales/weights, tolerances, fit captures, held-out capture identities and semantic/capture-group splits before selection; verify renamed workloads, reused raw captures, changed thresholds and post-seal evidence mutation are rejected.
- [ ] 6.3 Execute measured fitting on the admitted fit captures, freeze the selected vector/configuration digest, and evaluate disjoint hardware captures without refitting; verify candidate failures, ties, all-invalid searches and held-out failures remain visible and source inputs are unchanged.
- [ ] 6.4 Publish candidate history, fit/evaluation errors, sample statistics, ambiguity and exact device/workload/window scope in the external report; verify no result claims unique physical identification, untested workloads or full-device timing accuracy.
- [ ] 6.5 Run calibration/lineage regression tests and the actual measured fit/held-out campaign, strict Pyright, scoped Ruff and strict OpenSpec validation; record exact outcomes in `progress.md` and commit Part 6 before continuing.

## 7. Consolidated external evidence and delivery

- [ ] 7.1 Re-run the final pinned ttsim and Wormhole capture matrix from clean kits, import every raw artifact and reproduce the paired reports/calibration results; verify all published hashes and required functional/timing/evaluation outcomes from fresh temporary output directories.
- [ ] 7.2 Independently audit campaign-to-raw-to-reference-to-model-to-report lineage, case coverage, source/build identities, hardware configuration and fidelity labels; inject changed/missing evidence and verify the audit detects it without relying on producer summary fields.
- [ ] 7.3 Re-run existing offline validation and replay compatibility suites plus the separate root 4x4 Darknet19 smoke and optional ML gate; verify external tooling remains opt-in, unavailable optional dependencies remain visible and predictor/RL shapes are unchanged.
- [ ] 7.4 Run the complete relevant unittest suite, strict Pyright, the explicit affected/predecessor Ruff scope, `git diff --check` and strict OpenSpec validation; record exact discovered/pass/fail/skip counts and do not relabel prior executions as fresh.
- [ ] 7.5 Write delivery and identity manifests linking EV-01..08 to code, tests and actual external evidence, update the handoff with remaining fidelity limits, mark tasks complete only for executed evidence, and commit the final validated part locally without pushing or archiving.
