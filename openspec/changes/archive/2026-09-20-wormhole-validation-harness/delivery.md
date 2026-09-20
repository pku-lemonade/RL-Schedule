# Wormhole validation harness delivery

Delivered on `feiyang-dev`, 2026-09-18. This child implements finite offline
validation, normalization, independent event audits, explicit reference import,
condition-gated comparisons, bounded fitting and frozen held-out evaluation.
It preserves the existing topology v1/v2, memory v1 and compute v1 results and
configurable hardware inputs. It does not execute numerical tensors, vendor
kernels or measured hardware validation. All supplied reference examples are
synthetic. The umbrella and next child remain unimplemented by this change.

## Incremental implementation

| Part | Commit | Delivered work |
| --- | --- | --- |
| 1 | `3196ee3` | Five strict document contracts, portable identities and evidence outcomes |
| 2 | `a72d747` | Public adapters, normalized observations and independent event audits |
| 3 | `d6fb333` | Isolated finite suites, 19-case catalog and fixed regression gates |
| 4 | `055f6d4` | Provenance-aware functional JSON/profiler CSV import and comparison admission |
| 5 | `e769295` | Actual finite candidate execution, fit-only selection and sealed evaluation |
| 6 | `736a396` | CLI, atomic reports, capabilities, coverage, consumer guards and runnable docs |
| 7 | Commit containing this document | Consolidated checks, audit tightening, identities and next-child handoff |

Every part was tested, strictly type-checked, scoped-linted and committed before
the next part. [progress.md](progress.md) records the reproduced Linux environment,
315-test baseline, per-part results and final counts. Python is 3.12.12, Node is
22.22.2, local OpenSpec is 1.11.0, and all eleven handoff package pins were restored.
No optional ML stack, vendor tool, new root timing fixture or network-dependent
runtime was introduced. No push, specification sync or archive was performed.

The final audit found that compute memory service settings were being read from
the exported session. They are now checked against the admitted input, while
generated memory operations remain observable runtime records. Compute audits
also verify the complete declared job/operation set, worker binding and rate key.
Three new corruption tests reject self-consistent forged rates, omitted operands
and omitted jobs with plausible partial work totals. Positive generic and
Wormhole cases retain their original behavior.

## Requirement-to-evidence mapping

Paths in the test column refer to modules under `simulator_detailed/tests/`.
These tests ran; none of the following claim a real external comparison.

| Requirement | Implementation and executed evidence | Supported scope |
| --- | --- | --- |
| VA-D01 admission | `configs/schemas/validation.py`, `validation/adapters.py`, `runner.py`, `test_validation_contracts`, `test_validation_runner`, `test_validation_cli` | Strict kinds/versions, finite budgets, named adapters/gates, all-input admission before runtime, invalid output preservation |
| VA-D02 outcomes/tiers | `validation/outcomes.py`, report validators, `test_validation_outcomes`, reference/CLI tests | Required/optional aggregation, no empty success, skipped/unavailable checks distinguished, synthetic evidence cannot promote claims |
| VA-D03 identities | `validation/identity.py`, `test_validation_identity`, calibration/CLI tests, delivery manifest | Exact source/input hashes, dirty/untracked source, effective plans, policy/environment/seed, independent diagnostic paths and reference hashes |
| VA-D04 normalization | `validation/normalize.py`, `matching.py`, `test_validation_adapters`, `test_validation_references` | Raw result identity, exact counters, effects/ranges/causal order, explicit maps/clocks, no invented tensor values |
| VA-D05 independent audits | `validation/audits.py`, 26 adapter/oracle tests, predecessor `test_compute_overlap` | Modular routes, packet arithmetic, aggregate link/service bounds, aliases, ownership/generations/credits, declared jobs, work/stages and drain |
| VA-D06 benchmarks/gates | `runner.py`, `worker.py`, `gates.py`, `gate_worker.py`, offline catalog, runner tests | 19 finite cases, 123 passed model checks, isolation, real enforced timeout, pending/resume semantics and honest gate outcomes |
| VA-D07 references | `references.py`, `comparison.py`, 22 reference tests, two actual CLI imports | Pinned parser, provenance/metadata checks, exact large counters, selected-domain pairing, warmups/aggregation and blocked incompatible evidence |
| VA-D08 calibration | `calibration.py`, 18 calibration tests, memory/compute CLI examples | Copied typed targets, finite search, recomputed split identities, weighted errors/ties, frozen selection, retained held-out failure; synthetic only |
| VA-D09 CLI/reports | `validate_wormhole.py`, `reporting.py`, 13 CLI/consumer tests | JSON/stdout vs diagnostics/stderr, 0/1/2/3 exits, atomic output, input protection, profiles/capabilities/unsupported and simulation-fault labels |
| VA-D10 compatibility | Existing full detailed regressions, public consumer guards, four legacy replay CLI executions, separate actual root smoke | Underlying events/timing/digests and supported legacy contracts retained; optional ML remains unavailable |
| VA-D11 evidence/handoff | This document, `delivery-identities.json`, `progress.md`, `tasks.md`, strict OpenSpec validation | Child completion only; parent reconciliation and multicast/synchronization remain pending |

| Parent requirement | Child evidence | Remaining boundary |
| --- | --- | --- |
| VA-01 evidence tiers | VA-D02, D07, D09 | No actual ttsim/functional capture or silicon timing validation |
| VA-02 reproducibility | VA-D01, D03, D04, D07 and exact manifest | Capture origin is declared, not authenticated; future producers need actual metadata |
| VA-03 executable gates | VA-D06, D10, D11 and all seven incremental commits | Optional ML gate blocked; historical root remap failures retained |
| VA-04 contention/liveness | VA-D05, D06; independent input/event corruption tests | Finite delivered mechanisms only; multicast/synchronization pending |
| VA-05 calibration/evaluation | VA-D07, D08; fit/evaluation search examples | Synthetic demonstrations are not measured parameter calibration |
| VA-06 visible fidelity/compatibility | VA-D02, D09, D10; explicit capability and fault records | No new predictor/RL model, numerical execution or silicon fidelity inferred |
| VA-07 umbrella delivery | VA-D11; scoped child mapping | No parent task boxes changed; overlapping deltas require later reconciliation before sync/archive |

## Actual final checks

- Final `.venv/bin/python -m unittest discover -s simulator_detailed/tests`: **458 discovered, 457 passed, 0 failures/errors, 1 optional Torch/PyG skip**, 157.371s. Baseline was 315 discovered/314 passed/1 skip; the harness adds 143 tests.
- Final focused adapter/oracle suite: **26/26 passed**, no skips, 14.964s, including the three audit findings above.
- Strict Pyright including every new module/CLI: **0 errors, 0 warnings**. Harness scoped Ruff, predecessor compute scoped Ruff, strict OpenSpec validation and whitespace checks: **passed**.
- All **six documented CLI examples passed** with parseable schema-validated JSON and output files equal to stdout. The four execution examples were rerun after the audit fixes; the importer implementation was unchanged.
- Offline suite: **19 cases, 123 passed model checks, 0 failed checks**; optional ML gate **blocked**. One case is intentionally incomplete and passes only its declared pending-state checks. Both external tiers remain **unvalidated**.
- Both actual reference-import commands passed parsing/integrity checks on **synthetic** raw data. Both calibration examples passed held-out checks with evidence scope **synthetic_demonstration**.
- Existing topology-v1, torus-v2, memory-v1 and compute-v1 CLI examples all returned **0/complete**, preserving their document versions; exact commands and result digests are in the manifest.
- The separate actual root Darknet19 smoke **passed**. Source/input manifest verification passed; umbrella/spec/root/predictor/embedding/RL files were compared with `ea0115a` and remain unchanged (apart from this authorized handoff document).

Reproducible commands are in [validation.md](../../../../simulator_detailed/docs/validation.md), including the full unittest, strict Pyright and scoped Ruff invocations. OpenSpec was checked with `.venv/bin/openspec validate wormhole-validation-harness --strict`; whitespace with `git diff --check`. The pre-existing OpenSpec design-rules warning does not represent a validation failure and its configuration was not changed.

The separate root gate ran `NetworkMapper(parse_mapping(...)).gen_dfg()`, root
`simulator.architecture.Arch.execute()` and root `process_events(...)`, using
`workloads/darknet19-4-4.json`, `configs/instances/gemini4_4.json` and
`configs/instances/normal.json`. **37,888/37,888 nodes finished**, with **16 cores,
48 links and 11 windows**, followed by `Trace` JSON serialization/revalidation.
Trace/timing paths were temporary. No stochastic timing value became a golden
fixture. The exact shell-free smoke program is `validation/gates.py:ROOT_SMOKE`;
its actual result is retained in the delivery manifest.

Historical root remap tests `test_internal_view_layer5` (removed
`LayerView.active_cores`) and `test_safe_layer5_sequence_drains` (inactive
layer-5/core assumption) were not silently repaired, rerun or counted as passes.
Actual optional tensor inference, training and checkpoint loading were not run.
Existing structural/guard tests and source inspection retain detailed 7-D runtime
features, 4-D hardware features and root four-coordinate actions
`[layer, source core, destination core, operation]` with replace/split/shift/remove.

## Independent expectations and limits

Route expectations derive modular coordinates, direction and dimension order
from declared graph/bindings. Packet expectations derive payload/header/flit
arithmetic from configured widths and compare individual launches, not only totals.
Service expectations recompute addressed granule rounding, native/ACI conversion
and physical-resource intervals. Ownership checks reconstruct token, descriptor,
access, engine and generation transitions. Compute checks derive matrix/storage
work, quantized configured service and stage/memory prerequisites. The oracles
do not import simulator route/cost/accounting helpers or unittest classes.

Corruption cases preserve plausible summaries while changing hops, packets,
flits, aggregate launch cadence, alias service, posted visibility, releases,
generations, math/publication, source settings or declared job membership.
Existing independent pipeline schedules remain **21/14 cycles** for depths 1/2
and **45/33** for the integrated reader/compute example. Direct/wrapped event and
digest comparisons are compatibility snapshots, separately from these analytical
expectations. Finite testing is not a proof of all future traffic/model behavior.

Memory calibration rates 2/4/8 yield fit losses **18/0/9**, selecting 4; independent
held-out service is **51 cycles**. Compute rates 1/2/4 yield **1/0/0**, retaining
both minima and choosing 2 by declared order; held-out service is **17 cycles**.
Tests change held-out observations without changing the fit digest/winner, retain
perfect-fit/failed-evaluation status, reject split leakage and verify sealing
before evaluation. Sources/evidence remain unchanged. Unavailable or all-invalid
fit evidence never creates a default winner or a measured claim.

Architecture facts remain covered by predecessor source-pin/profile tests
(`test_hardware_profile`, particularly physical coordinates, raw fabric maps,
memory alias totals and packet quantities) and the routing regressions. The named
generic `architecture` selection has no universal fact oracle and remains
unsupported/blocked; a passing offline suite does not assert whole-device
architecture/protocol conformance. Numerical values, kernel/ISA execution,
multicast, atomics/semaphores, general synchronization, interchip/host/PCIe and
exact NIU/DRAM-bank/cache timing remain unsupported or unvalidated.

Profiler import preserves counters above 2^60 and pairs only the selected
device/core/RISC/zone/source/run. Samples 25/27/29 yield aggregate 27 with a
separate excluded 100-cycle warmup. Mean/median/statistics parsing is checked,
but one model run cannot silently match a repeated measurement aggregate.
Cross-core subtraction, unmatched kernel/host work and unknown conditions block
comparison. Finite throughput uses simulation start to snapshot, includes the
declared idle interval, and is not claimed as asymptotic saturation.

## Source and input identities

[delivery-identities.json](delivery-identities.json) records exact final detailed
Python bytes (including tests), a separate root smoke Python bundle, environment
pins, checked-in input/capture hashes and four legacy CLI result digests. The
source boundary is explicit: default detailed runs hash `simulator_detailed/**/*.py`;
the root smoke bundle hashes `simulator/`, `utils/` and `configs/schemas/` Python.
Outputs, documentation and timestamps are excluded from those source bundles.
The manifest identifies the Part 6 base revision plus Part 7 source modifications;
the final commit contains exactly those checked bytes. Full runtime reports are
temporary outputs reproducible with the documented commands, not new fixtures.

- Detailed bundle: **134 files**, SHA-256
  `e88588c8fb20399b19f38799d0ee59ce84baaa13f42fb3d80c50230e3a7ba526`.
- Root smoke bundle: **18 files**, SHA-256
  `271593ce005c95b560891f90944945b2da4384ef4f39051c3fcb334d51c28b39`.
- **50 input/capture files** have exact path/hash/size entries in the manifest.

| Principal input | SHA-256 |
| --- | --- |
| `offline.json` | `3e17ba0f21ff9ee3a7d4c07f98bd2d1d0017596a2e55b406fcd2800f8b72ee7b` |
| `calibration/memory_plan.json` | `93e74033eedcc66fe8000de1011bf2f3f5ffd8facebb53e29c0a8a37e94cd458` |
| `calibration/compute_plan.json` | `df63f2a5e39e03a01aa5db7f6fd36bfb3ecf4c3abe724cff04626d7051108244` |
| `references/synthetic_profiler.csv` | `7aedae787b904518f2cb2299b4ef00cb38c3c23c3be730f4ead1cb7bc2e9417f` |
| `references/synthetic_functional_raw.json` | `d257d6916e534e0d5fad8d0e27cb6ac764be50c52838b3d4eecb7b8bee526a71` |

Reference extractor identity is **`wormhole_reference_import`, version `1`**.
The normalized format is `normalized_functional_v1`; the pinned CSV format is
`tt_metal_device_profiler_csv_v1`. All committed raw captures are synthetic.
The design's [ttsim README](https://raw.githubusercontent.com/tenstorrent/ttsim/90a5b1ab85c4e995de3db663ff660bc1e073a562/README.md)
pin is commit `90a5b1ab85c4e995de3db663ff660bc1e073a562`, raw SHA-256
`0f3871ba51747387f7ff7f3f0e896b221b220da5238f7df939ddb6910ca6b072`.
The design's [profiler documentation](https://docs.tenstorrent.com/tt-metal/latest/tt-metalium/tools/device_program_profiler.html)
HTML snapshot hash is `63c41e358a294ac2f603da10991d46934c204c8b67fe5cf6762b178569bc1dd9`;
its column contract was inspected again during import implementation. These are
documentation/extractor context, not executed vendor or measured hardware evidence.

## Handoff to `wormhole-multicast-sync`

Explore the smallest documented single-ASIC multicast and scalar atomic/semaphore
subset as its own child. Preserve public unicast, addressed memory, bounded
compute, configurable hardware and all legacy consumer boundaries. Do not treat
scalar synchronization as tensor reduction or infer general kernel support.

The next design must specify destination eligibility/source inclusion, exactly-once
delivery, replication point and shared-prefix physical bytes, finite destination
backpressure, branch completion, source-local versus target-visible completion,
scalar state/address width, service serialization, ordering and release semantics.
Extend normalized effects/causal edges only for observable supported mechanisms;
add corruption tests for duplicated/missing destinations, uncharged shared links,
early synchronization release, resource leaks and combined pipeline deadlock.
Retain independent small oracles and replay/resume/drain checks under constrained
capacities. Update capability/coverage entries only after those mechanisms execute.

No matching hardware capture or verified ttsim run is available from this child.
Future external captures must satisfy the published metadata, hash, clock/window
and split contracts. Current synthetic calibration is a demonstration of fitting
mechanics. Keep silicon and functional tiers unvalidated until actual admitted
comparisons run, and retain optional ML and historical remap limitations.
The umbrella's overlapping parent/child deltas remain for the final reconciliation;
no umbrella milestone is completed by this harness report alone.
