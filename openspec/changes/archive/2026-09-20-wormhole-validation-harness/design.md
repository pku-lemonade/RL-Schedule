## Context

See `proposal.md` for motivation. This is child 6 of `wormhole-single-chip-simulation-plan`, following `wormhole-compute-dataflow` at `007f4e5` (35/35 tasks). Its final delivery records 315 detailed tests discovered, 314 passed and one optional Torch/PyG skip, strict Pyright with zero errors/warnings, scoped lint and CLI checks. Those are historical results, not checks run for this new implementation.

Current behavior and integration boundaries:

| Area | Delivered behavior | Gap this child addresses |
| --- | --- | --- |
| Profile/topology | Typed inspection, physical/canonical identities, pinned fact fixtures, opt-in admitted execution | No common coverage/provenance report; metadata does not itself validate a fact |
| Torus/transport | Two fabrics, configurable routes/clocks/packet timing, finite queues/credits, pending/drain evidence | Reusable observation audits and finite comparable benchmark cases |
| Addressed memory | Canonical shared L1/DRAM resources, segmentation, service, versions, posted/acknowledged completion | Unified alias/byte/conservation checks and explicit measurement boundaries |
| Compute | Finite matmul/FC, effective costs, bounded slots, causal overlap and complete/incomplete results | Common stage/traffic metrics; abstract scheduling is not numerical execution |
| Tests/delivery | Independent path, byte, resource and pipeline oracles; manual code/input identities | Runnable evidence catalog, normalization, external import and calibration mechanics |
| Legacy paths | Detailed mesh/DMA/fail-slow and separate root Darknet19 execution remain usable | Repeatable compatibility gates without importing optional ML by accident |

`benchmark_workloads.py` currently contains NMC channel scenarios, not a generic Wormhole validation harness. `replay_topology.run_replay`, `replay_memory.load_plan/run_replay`, `replay_compute.load_plan/run_workload` and profile/topology inspection provide existing admission/execution boundaries. The result kinds are `topology_replay_result` v1/v2, `memory_replay_result` v1 and `compute_workload_result` v1. Existing inputs/results already carry effective configuration and plan/source hashes; they do not consistently carry code/environment identities or external measurement conditions.

The previous child's root smoke completed 37,888 DFG nodes and exported 16 cores, 48 directed links and 11 trace windows. Two separately probed old root remap tests have stale expectations (`LayerView.active_cores` and a fixed active layer/core pair). They are recorded limitations, not successful checks and not silently repaired in this child. Current root actions have four coordinates (layer, source, destination, operation), not the stale three-coordinate summary in the OpenSpec context. No `NOC_ARCHITECTURE` document was available in the inspected tree; current code and prior child evidence remain the implementation baseline.

Exploration on this host found Darwin arm64/Python 3.12.12 and no optional Torch/PyG imports. No matched hardware captures or verified configured ttsim execution environment were found. These observations do not prove that external resources cannot be supplied later.

## Goals / Non-Goals

**Goals:**

- Produce offline evidence for the implemented finite single-ASIC mechanisms, with deterministic identities and independently checked observations.
- Make comparisons fail closed on incompatible conditions while keeping missing evidence distinguishable from a model defect.
- Demonstrate reference ingestion and real fitting/evaluation mechanics with honestly labeled synthetic fixtures; accept actual external captures when explicitly supplied with adequate metadata.
- Leave an extensible requirement/check registry for child 7's multicast and synchronization tests.

**Non-Goals:**

- Execute TT-Metal kernels, emulate RISC-V instructions, calculate tensor values, infer hidden hardware state, or implement additional NoC/memory/compute mechanisms.
- Run or install ttsim, deploy profiling kernels to a card, download vendor builds, or turn public peak specifications into measured simulator accuracy.
- Fit routing, physical topology, enabled workers, capacities, clocks or protocol widths to compensate for timing error.
- Claim full Wormhole conformance, multi-card timing, detector accuracy or RL readiness. Syncing/archiving the umbrella and resolving unrelated root remap tests remain outside this child.

## Decisions

### 1. Add a validation layer around existing public outputs

Use strict frozen records consistent with existing detailed schemas. Keep five versioned top-level document kinds:

| Document | Responsibility |
| --- | --- |
| `validation_suite` v1 | Finite cases, named adapters/checks, requirement bindings, required/optional checks, source paths, configured limits and predeclared comparison policies |
| `validation_reference` v1 | Source classification, raw artifact identity, extractor identity, conditions and normalized observations/metrics; unknown metadata remains explicit |
| `validation_report` v1 | Run identity, case execution state, independent checks, evidence tiers, diagnostic reasons, capability limits and scoped coverage |
| `calibration_plan` v1 | Base cases, permitted parameter targets/candidates/constraints, finite search budget, fit/evaluation membership, loss and acceptance policy |
| `calibration_result` v1 | Candidate fit outcomes, frozen winner/configuration identity, held-out outcomes and strictly scoped evidence claims |

Place schema records in `configs/schemas/validation.py`; place identity, adapter/normalization, oracle, runner, reference, calibration and reporting logic in small modules under `validation/`. Add `validate_wormhole.py` as the explicit entry point. Module boundaries may be split as implementation warrants; the document contracts and observable behavior are fixed by this design/spec.

Registry entries are named implementations, not dynamic Python imports, expressions or arbitrary shell commands supplied by JSON. Relative paths resolve from the declaring document, including nested references. Main suite/calibration inputs and required simulator assets are admitted before execution. External evidence paths are explicit and can be unavailable without disabling offline cases. Existing results are wrapped or referenced with hashes, not given a new incompatible shape.

Alternative: extend each simulator result with validation state. Rejected because it couples evidence policy to execution and churns existing formats/digests. Alternative: run test methods directly as benchmarks. Rejected because tests are regression gates, not a production observation API.

### 2. Separate execution, check outcome, evidence tier and coverage

Keep these as independent fields:

- Execution: inspected, complete, incomplete, rejected or unavailable, based on the actual selected adapter and result. Inspection cannot count as execution.
- Check outcome: `pass`, `fail`, `blocked`, `unsupported` or `not_run`, always with a reason and applicable observation identities. `pass` requires an actual check; `fail` means observed disagreement or a tested contract violation. `blocked` means required data/tool/metadata is unavailable or incompatible for comparison. `unsupported` means the requested mechanism/observable has no supported implementation. `not_run` means an explicit nonselected tier or a prerequisite prevented execution.
- Evidence tier: architecture/protocol, model invariant, functional reference or silicon timing. Synthetic data can exercise any adapter but contributes only model/adapter evidence. Supplied provenance is recorded and checked for consistency/integrity, not treated as independently authenticated simply because its hash is valid.
- Requirement coverage: implemented and checked, partial, blocked, unsupported or pending, with the exact checks and child commits supporting it. Planned tasks are not delivered evidence.

Aggregate report status is `fail` if any executed check fails, otherwise `incomplete` if any required check is blocked/unsupported/not run, otherwise `pass` only if at least one required check actually passed and every required check passed. Optional unavailable comparisons remain visible and do not invalidate a declared offline-only suite. Empty suites/check sets are invalid. There is no combined accuracy percentage across tiers. A passing offline suite still has `silicon_timing=unvalidated` without an admitted measured comparison.

An expected short-horizon test may pass an incomplete-state invariant while its simulation remains incomplete. Its unfinished work never contributes complete-run throughput or successful drain. An intentionally rejected input is tested as an admission behavior, not counted as runtime execution.

Alternative: a single success boolean or a pass/skip count. Rejected because it hides whether the model ran, whether a reference exists and what claim a pass supports.

### 3. Record reproducibility as an explicit dependency manifest

For each case, record schema/adapter versions; exact source/input byte hashes plus canonical effective-plan hashes; profile version, resolved enabled layout, worker/resource/fabric mapping and clocks; workload parameters, policies and failure conditions; simulator Git revision and dirty state; a deterministic digest of the relevant source-file bundle including uncommitted/untracked Python inputs; Python/platform and relevant dependency versions; seed or an explicit deterministic/not-applicable state.

Keep absolute local paths and wall-clock timestamps as diagnostic metadata outside portable content identity. Bundle membership and ordering are explicit; report/output files do not hash themselves. Different check selections and calibration parameters change the appropriate effective identity. Verify source bytes against supplied hashes before admitting reference observations. A source URL/revision alone without the extracted artifact identity is insufficient for reproducible imported measurements.

Reference metadata additionally includes extraction method/version, raw artifact hash, original and normalized units, source evidence classification and collection conditions. Hardware references require exact chip/board identifier or explicit model identity with documented scope, enabled/harvested layout, software/firmware/profiler versions/settings, clocks, workload mapping/shape/dtype/fidelity, measurement window, repetitions, excluded warm-ups and aggregation. Fields relevant to a claim cannot be guessed from filenames or filled with simulator defaults.

Existing profile source records remain useful architecture provenance; a successful fetch or parse does not prove that every recorded assertion is true. Architecture fixture checks cite the specific fact/expectation being checked. Hardware evidence identity describes its supplied origin; this child does not authenticate a remote machine.

Alternative: Git SHA alone. Rejected because a dirty checkout or changed input can produce different results with the same SHA.

### 4. Normalize observable semantics and independently audit them

Adapters initially cover profile/topology inspection, topology replay v1, torus replay v2, addressed memory and compute execution. They retain raw result identity and expose only supported fields in a common nested observation record: scoped endpoint/resource identities, addressed transfers and bytes, route/fabric/hops, named lifecycle/stage events, partial-order edges, service/occupancy intervals, terminal/pending state and explicitly defined metrics. Absent fields remain absent with a diagnostic; normalization must not invent a tensor result, memory value or globally synchronized device timestamp.

Use exact integers for byte/work/counter values. Time units always name their domain and clock; conversions retain the original values and use checked arithmetic. Match external identifiers through explicit one-to-one mappings with range/role validation. Compare functional destinations, extents, counts and required causal edges, not incidental global event ordering or simulator-specific flit/credit internals.

Pure oracles consume declared inputs and exported observations. They cannot invoke the same routing/cost/accounting helper that produced the claimed expected result. Existing standalone tests may share a pure audit helper after extraction, but independent golden arithmetic and deliberately corrupted observations still test that helper. Audit at least:

| Mechanism | Independent expectation | Defect-sensitive test |
| --- | --- | --- |
| Topology/routing | Endpoint eligibility, physical resource identity, configured dimensions, modular hop direction/axis order, local/remote endpoints | Wrong wrap hop, disabled worker or alias treated as a second physical resource |
| Packet accounting | Declared payload boundaries, physical/header/padding/flit arithmetic and actual emitted events | Lost/duplicated packet/flit, planned bytes counted as emitted bytes |
| Memory | Rounded service from addressed extents, shared-owner service intervals, versions and completion/visibility dependencies | Duplicated alias bandwidth, local operation with fabricated network bytes |
| Finite resources | Event-by-event capacities, ownership transitions, generation/descriptor/credit conservation, pre/post teardown snapshots | Double release, stale slot, uncharged pending work |
| Compute/pipeline | Declared matrix/storage dimensions, block work, math/context intervals, causal publication/consumption and stage capacities | Math before input readiness, writer before output, software threads multiplying an engine |
| Completion | All required finite jobs/effects/resources drain; incomplete runs retain charges | Posted output retires before destination effects but is falsely reported fully complete |

Hardware width, tile counts, rates and buffer sizes come from the case/profile; only a specifically labeled Wormhole fact fixture encodes its pinned architecture expectations. The exhaustive 28,800-pair route test, independent 21/14 stage oracle and integrated 45/33 compute timeline remain useful regression gates; those numbers are not universal device timings.

Alternative: compare a result only with a saved result from the same simulator. Retained for compatibility digests where useful, but insufficient for model correctness.

### 5. Use finite benchmark cases and separate regression gates

Suites declare a finite list of admitted case files and check IDs. Small variant generators, where needed, use explicit dimensions/packet sizes/load patterns and a bounded expansion budget; no hidden hardware constants or arbitrary configuration patch scripts. Cases expose metrics with clear numerator/denominator and observation window: submitted/completed operations, payload/wire/service bytes, arithmetic/context busy time, completion latency and interval throughput.

Initial catalog covers idle/local/remote/hop latency, packet-boundary sizes, finite repeated traffic, shared-link hotspots, opposite/parallel fabric use, wrap paths, minimum queue/credit backpressure, shared versus independent memory resources, local-only service, one/two slot pipelines, shared compute engines and incomplete/resume/drain checks. A throughput case uses an explicit finite measurement interval and excludes declared warm-up traffic; it is not called asymptotic saturation merely because a large batch completed. Hardware throughput additionally requires matched collection conditions.

Run cases sequentially initially for deterministic isolation. A configured finite simulation horizon and wall-time/event budget, where supported by the adapter, bound execution; enforced wall time uses an isolated worker process so a stuck simulation cannot hang the harness. Admit the whole case before allocating resources. Timeouts are visible failed bounded-liveness checks with whatever diagnostic state is actually available; they are not proofs of hardware deadlock or fabricated snapshots. A horizon-limited complete-run benchmark is incomplete, whereas a declared interruption test checks that state explicitly.

Regression gates use a fixed registry: targeted existing test groups, strict Pyright, scoped Ruff and supported compatibility smokes. Execute fixed argument lists without a shell; suite files select known gates and do not inject commands. Classify missing executable/import prerequisites as blocked; assertions, runtime defects and type/lint errors remain failures. Preserve actual exit status and concise diagnostics. A discovered test skip is reported by reason, not included among passes. Do not treat all nonzero commands as unavailable dependencies.

The root smoke uses the actual mapper/architecture/tracer with temporary outputs, separately from detailed graph/model checks. Optional Torch/PyG/model gates declare prerequisites. Existing root remap failures remain explicitly listed baseline limitations; their historical status is not a new pass or an excuse to reclassify any newly observed failure.

Alternative: external benchmark process scheduling framework. Deferred; a finite named registry suffices and is reviewable.

### 6. Import external observations conservatively

Two initial external input paths:

1. **Normalized functional JSON:** a `validation_reference` containing explicitly mapped destinations/byte ranges/counts and causal observations from an identified external producer. Record raw capture and extractor identities. The producer can be ttsim/TT-Metal instrumentation, but there is no assumed universal ttsim trace exporter. No automatic ttsim launcher is delivered. Synthetic producer fixtures test the import/comparison path and remain synthetic.
2. **Device-profiler CSV plus metadata sidecar:** recognize a named pinned header format, parse integer cycle counters without float precision loss, select one device/core/RISC/zone/run, and pair unambiguous begin/end events within the same clock domain. Use explicit zone identity/nesting rules; do not pair only by timer ID. Reject unmatched/ambiguous pairs, unsupported phases/formats or contradictory frequency/architecture metadata. Exclude explicitly enumerated warm-up runs; aggregate repeated same-domain durations with a declared supported reducer (initially mean or median). Retain sample count and dispersion, not just a single average.

A profile trace alone does not establish equivalent workload boundaries. Sidecars must map the chosen zone to an actually observable simulator interval, such as arithmetic service or a specifically defined transfer lifecycle span. Full kernel/host-dispatch timings with unmodeled work are incomparable. Counter subtraction happens before conversion; cross-core absolute timestamps are not subtracted without a supplied verified synchronization model, which is outside the initial CSV adapter. No undocumented profiler overhead correction is applied.

Before numerical comparison, check architecture/device configuration, profile/layout mapping, operation/memory/fabric parameters, units/clock domain, software/instrumentation conditions, window, warm-ups/repetitions and aggregation. Known differences require a declared supported normalization; otherwise emit blocked comparison reasons. Source-level archival metadata can be incomplete, but incomplete metadata cannot authorize a timing threshold. Unknown reference fields are never silently copied from the simulation.

Design sources inspected on 2026-09-17:

| Source | Recorded identity | Design use and limit |
| --- | --- | --- |
| [ttsim README](https://raw.githubusercontent.com/tenstorrent/ttsim/90a5b1ab85c4e995de3db663ff660bc1e073a562/README.md) | Commit `90a5b1ab85c4e995de3db663ff660bc1e073a562`; raw SHA-256 `0f3871ba51747387f7ff7f3f0e896b221b220da5238f7df939ddb6910ca6b072` | Functional-reference integration context; not a silicon timing oracle or evidence of a local run |
| [TT-Metal device profiler](https://docs.tenstorrent.com/tt-metal/latest/tt-metalium/tools/device_program_profiler.html) | Retrieved HTML SHA-256 `63c41e358a294ac2f603da10991d46934c204c8b67fe5cf6762b178569bc1dd9` | Instrumented zone/CSV format and clock-domain cautions; the documentation's Grayskull example is not Wormhole measurement data |

These source identities were fetched for design inspection only, without committing downloaded files. Implementation must name the CSV format/extractor version and provide independently specified synthetic fixtures. A future live-format change needs explicit adapter admission/tests; `latest` URLs cannot silently redefine an accepted format.

Alternative: scrape arbitrary performance tables or execute vendor tools automatically. Deferred because neither guarantees workload/window equivalence, and no verified external runner/capture is available here.

### 7. Deliver actual bounded fitting with a sealed held-out phase

Use deterministic finite candidate search over typed targets in existing configuration:

- Memory resource ID: `bytes_per_cycle` and `fixed_latency_cycles` in its aggregate service settings.
- Compute rate ID: `work_per_native_cycle` and `setup_native_cycles` in its effective rate settings.

Targets are explicit physical resource/rate identities, not unconstrained JSON paths. Candidates satisfy positive/nonnegative finite bounds and each resulting workload goes through existing full admission. Duplicate/unknown targets and over-budget Cartesian products are rejected before simulation. Apply candidates to in-memory copies; never overwrite profile/workload files. Retain original evidence and record proposed fitted overrides separately rather than rewriting an assumed value as a measured hardware fact. No fitting of clocks, capacities, widths, harvesting, topology, quantization policy or unsupported model mechanisms.

A calibration plan fixes fit cases, distinct held-out cases, candidate lists, reference identities, metrics, weights, positive normalization scales, loss and acceptance tolerances with rationale. Reject overlap by semantic workload/conditions fingerprint as well as ID; renaming an identical case or splitting the same measurement group must not create a held-out case. The fingerprint excludes the candidate timing values being fitted but retains workload shape, resources, mappings, policies, clocks and collection conditions.

Procedure:

1. Validate the entire split and metric policy; hash the plan and admitted fit references. Evaluation reference identities are committed without using evaluation observations in candidate selection.
2. Execute the base workload copies for each candidate on fitting cases only. Minimize a declared weighted mean absolute error divided by fixed positive metric scales. Candidate admission/runtime/check failures are retained and disqualify that candidate. Resolve equal losses by declared canonical candidate order. Missing fit evidence blocks calibration rather than selecting defaults.
3. Freeze the selected vector, fit evidence and configuration digest before running evaluation; record the selection digest as the evaluation prerequisite. All-invalid candidates produce no selected vector.
4. Run the selected model once per held-out case/repetition policy, with the predeclared metric tolerances. A metric passes when `abs(model - reference) <= absolute_tolerance + relative_tolerance * abs(reference)`; absolute tolerance handles zero references. Store signed/absolute error and relative error only where defined. Evaluation cannot refit or adjust tolerances.
5. Export candidate history, frozen configuration, fit and evaluation outcomes and scope. Hardware-supported calibration requires admitted measured fit/evaluation evidence; synthetic data yields only a synthetic fitting demonstration. Documentation and ttsim timings cannot calibrate silicon timing. A failed held-out result remains a failure even when the fit loss is zero.

Flat or tied candidates are reported as ambiguous at the available resolution; no confidence interval or uniquely identified physical parameter is invented. Passing a finite measured evaluation supports only its tested device/workload/window conditions and selected aggregate model. Changing thresholds, split, source data or parameters creates a new plan identity.

Alternative: an unconstrained numerical optimizer or automatic architecture tuning. Rejected for this child because bounded candidate search is deterministic, inspectable and sufficient to validate the calibration boundary. This is working fitting logic, not just parameter metadata storage.

### 8. Publish scoped reports and preserve consumer boundaries

CLI commands are explicit modes: `--suite <path>`, `--import-reference <sidecar>` or `--calibrate <plan>`, with optional `--output <path>`. The sidecar declares format and raw artifact paths. JSON is the stdout payload; diagnostics go to stderr. Exit status: 0 for a passing requested validation operation, 1 for observed validation/evaluation failure, 2 for invalid invocation/input, 3 for incomplete required evidence/checks. An imported valid reference is an import success, not a model validation pass. Existing replay CLIs retain their current distinct exit conventions.

Validate input before publishing output. Write a fully serialized result atomically to the requested path after execution; rejected input must preserve an existing output. No implicit report/log/source directories or network fetches. Relative source paths behave independently of the caller's working directory. Report links/hashes identify the original results and normalized observations so a failed oracle is inspectable.

Each report enumerates enabled mechanisms, approximations, unavailable requested features, checked requirements and unvalidated evidence tiers. Fault-injection cases remain simulation experiments unless supported by matched measured evidence. New validation document kinds must be rejected by incompatible predictor/embedding consumers before optional ML/model work, without changing existing legacy feature dimensions, trace semantics or RL shapes.

Requirement coverage maps this child's VA-D01..11 to parent VA-01..07 and predecessor evidence. Child 7 entries for multicast destination/shared-link delivery and scalar synchronization ordering/liveness stay pending. The coverage catalog never edits OpenSpec task boxes or infers implementation from a change's existence. The later umbrella audit must reconcile overlapping parent/child deltas before sync/archive.

The intended mapping below is planning scope, not delivered coverage:

| Parent requirement | Concrete child requirements | Evidence still needed after planning |
| --- | --- | --- |
| VA-01 evidence tiers | VA-D02, D07, D09 | Executed claim/status tests; real external agreement only when admissible captures exist |
| VA-02 reproducibility | VA-D01, D03, D04, D07 | Identity mutation/roundtrip tests, extraction fixtures and comparison admission checks |
| VA-03 executable gates | VA-D06, D10, D11 | Actual per-part and final test/type/lint/compatibility outcomes |
| VA-04 contention/liveness | VA-D05, D06 | Delivered-mechanism catalog and corrupted-trace audits; multicast/sync stays pending for child 7 |
| VA-05 calibration/evaluation | VA-D07, D08 | Executed candidate fitting and held-out isolation tests; measured calibration requires actual compatible measurements |
| VA-06 visible fidelity/compatibility | VA-D02, D09, D10 | CLI/report/consumer regression evidence and explicit simulation-experiment labels |
| VA-07 umbrella evidence | VA-D11 | Child delivery mapping now; parent/child spec reconciliation and umbrella closure in the later final audit |

Alternative: mark the umbrella complete when the harness passes. Rejected because a harness cannot implement pending mechanisms or create missing hardware evidence.

## Risks / Trade-offs

- **Self-confirming oracles:** Shared producer/checker logic can hide defects. Use input-derived arithmetic, independent path enumeration and deliberate trace/identity corruption tests; retain existing independent examples.
- **Misleading timing comparisons:** A shared unit name does not imply the same clock or window. Require explicit semantic boundaries and reject unsupported normalization, cross-core subtraction and unmodeled kernel/host work.
- **False calibration:** Synthetic traces and documentation can make a fit look excellent. Preserve source classification end to end, enforce split identities and freeze selection/tolerances before held-out evaluation.
- **Unidentifiable parameters:** Aggregate timings can fit multiple vectors. Expose ties and finite tested scope, keep structural facts fixed and avoid physical-parameter certainty claims.
- **Harness complexity:** A plugin/benchmark platform could outgrow the simulator. Use five strict document kinds, a finite adapter/check registry and four timing target fields initially.
- **Large results or stalled cases:** Finite workloads can still be expensive. Enforce declared case/search budgets and process timeouts, keep raw artifacts addressable, and avoid unbounded trace duplication in summaries.
- **Dependency and historical failures:** Optional ML and stale root remap checks must not be hidden or block unrelated offline mechanics by accident. Preserve reasoned statuses and separate required gates from explicitly optional coverage.
- **Public-source drift:** Recorded source identities document this design; future external captures need their own raw/extractor identities and compatibility checks.

## Migration Plan

1. Add contracts/status/identity records and tests without changing simulator outputs.
2. Add normalization and pure independent audits; preserve existing independent regression expectations.
3. Add finite runner/benchmark catalog and named regression gates.
4. Add functional/profiler import and comparability checks, with synthetic and negative fixtures.
5. Add bounded fitting and sealed held-out evaluation over existing effective timing fields.
6. Add reporting CLI, generic/Wormhole examples, capability/consumer guards and usage documentation.
7. Run consolidated validation, separate root compatibility smoke and requirement audit; record actual available/blocked evidence and commit before exploring `wormhole-multicast-sync`.

Every implementation part updates the child's progress evidence and is committed before the next. Rollback is removal/reversion of the opt-in validation modules/assets and any narrowly extracted audit helper changes; no profile/workload migration or model retraining is required. Do not rewrite prior completed child history, sync/archive the umbrella or push without a request.

## Open Questions

- Which exact real Wormhole board, software revision, profiled kernel/zone and workload mapping will be supplied for the first external timing comparison? The import boundary deliberately supports unavailable evidence; the answer selects data, not a different harness design.
- Which ttsim/TT-Metal instrumentation producer will provide the first normalized functional capture? No universal exporter or numerical equivalence is assumed, so this remains an optional source integration after the generic import contract is implemented.
