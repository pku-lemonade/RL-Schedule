# Wormhole validation harness

The harness runs finite abstract scheduling and traffic models on one ASIC. It
wraps existing inspection, topology-v1, torus-v2, memory-v1 and compute-v1 public
results without changing their formats. Hardware geometry, clocks, widths,
capacities, effective service rates and thresholds remain explicit inputs.
The supplied references and calibration examples are **synthetic**, not device
measurements, ttsim runs, numerical results or calibrated Wormhole performance.

## Run the checked-in examples

Run these commands from the repository root in the environment documented in
[WORMHOLE_HANDOFF.md](../../WORMHOLE_HANDOFF.md). `/tmp` must already exist;
substitute any existing output directory. Each successful command prints JSON
to stdout and a short diagnostic to stderr. `--output` publishes the same JSON
atomically. Relative nested asset paths resolve against their declaring document.

```sh
.venv/bin/python -m simulator_detailed.validate_wormhole --suite simulator_detailed/configs/validation/offline.json --output /tmp/wormhole-offline.json
.venv/bin/python -m simulator_detailed.validate_wormhole --suite simulator_detailed/configs/validation/synthetic_reference_suite.json --output /tmp/wormhole-functional-demo.json
.venv/bin/python -m simulator_detailed.validate_wormhole --import-reference simulator_detailed/configs/validation/references/synthetic_functional.json --output /tmp/wormhole-functional-import.json
.venv/bin/python -m simulator_detailed.validate_wormhole --import-reference simulator_detailed/configs/validation/references/synthetic_profiler.json --output /tmp/wormhole-profiler-import.json
.venv/bin/python -m simulator_detailed.validate_wormhole --calibrate simulator_detailed/configs/validation/calibration/memory_plan.json --output /tmp/wormhole-memory-calibration.json
.venv/bin/python -m simulator_detailed.validate_wormhole --calibrate simulator_detailed/configs/validation/calibration/compute_plan.json --output /tmp/wormhole-compute-calibration.json
```

The offline catalog covers 19 cases: idle/local/hop/wrap latency, packet-size
boundaries, finite sustained traffic, shared links, dual fabrics, minimum buffers,
memory aliases/local access/posted drain/interruption, and bounded compute overlap
with shared engines. An intentionally incomplete case passes only its declared
checks; its pending work is not completed throughput. Optional Torch/PyG absence
is recorded as blocked and does not prevent offline success.

The calibration engine actually executes candidates on fitting cases, freezes
the selected vector and fit-evidence digest, then executes distinct held-out
workloads. Memory selects rate 4 from `[2, 4, 8]`; compute retains a tie at 2 and
4 and selects 2 by declared order. Read the
[independent arithmetic](../configs/validation/calibration/README.md) and
[capture contract](../configs/validation/references/README.md). Copies preserve
original inputs and evidence. Only memory bandwidth/fixed latency and compute
work rate/setup are fit targets; structural parameters are not searched.

The modes are mutually exclusive. Exit codes are **0** success, **1** observed
check/evaluation failure, **2** invalid input/invocation/output, and **3** required
evidence or checks incomplete. A successful import confirms parsing and integrity,
not agreement with the simulator. Invalid admission preserves an existing output.
Outputs cannot replace declared inputs, source profiles or raw references. Missing
output directories are never created. Existing replay CLI exit conventions are
unchanged. No command fetches data, launches vendor software, evaluates expressions
or accepts an arbitrary shell command.

## Read results and evidence

Execution (`inspected/complete/incomplete/rejected/unavailable`), individual checks
(`pass/fail/blocked/unsupported/not_run`) and aggregate report status
(`pass/fail/incomplete`) are separate. Any observed failure fails the report;
required unavailable checks make it incomplete. Optional absence permits success.
An empty required check set cannot pass. Four evidence tiers remain separate:

| Tier | What it establishes |
| --- | --- |
| Model invariant | Configured routing, accounting, service, ownership, stages and drain |
| Architecture/protocol | A named source-backed architectural fact; no general device conformance inferred |
| Functional reference | Admitted external effects and partial-order agreement, with explicit identity mappings |
| Silicon timing | Admitted measured timing agreement under matched device/workload/collection conditions |

Current offline examples leave functional-reference and silicon-timing claims
**unvalidated**. Synthetic import/comparison/fitting cannot promote either tier.
Schema tests with declared external classifications exercise admission logic;
they are not actual hardware execution. Origin metadata is unauthenticated.

Each case records exact input hashes, source revision and relevant dirty/untracked
Python bytes, environment versions, deterministic seed state, selected policy,
effective plan and original result hash. Normalized observations retain events,
addressed effects, causal edges, resource/stage intervals and missing observables.
Capabilities link to the admitted plan digest; its full configuration includes
layout, mappings, buffers, service assumptions and evidence. Fault cases are
explicit simulation experiments. Coverage links selected checks to VA-D01..11
and VA-01..07, remains scoped to this run, and treats the unfinished
`multicast_sync_v1` prototype as a separate opt-in adapter. Shared multicast,
scalar network/service, pipeline generations and retained resume remain pending.
Missing runtime evidence produces unsupported checks, not passes. It never updates
OpenSpec or completes a whole requirement from one run.

Metrics state units, clock domains, completion scope, numerator/denominator and
measurement window. Catalog elapsed time and throughput use simulation start to
snapshot, including initial idle time and with no excluded warmups. Finite traffic
is not an asymptotic saturation measurement. Planned work and observed work are
separate. Native/ACI conversion requires explicit known clocks; large integer
counter subtraction occurs before conversion. Clock domains are not interchangeable.

## Supply external references

Supply a versioned sidecar and raw normalized functional JSON or the pinned
device-profiler CSV subset. Sidecars require raw hashes, producer, classification,
source revision/snapshot, extractor/version, original/normalized units and explicit
known/unknown conditions. Functional comparison requires matching admitted
architecture/profile/layout/workload/mapping and explicit one-to-one entity/event
maps; address-range coverage and causal order can match despite different event
interleavings or chunk partitions. Numerical tensor values remain unsupported.

Timing additionally requires device scope, clocks, software, firmware,
instrumentation, measurement boundary, warmups, repetitions and aggregation.
CSV import pairs one device/core/RISC/zone/source-file/source-line/run and supports
explicit none/mean/median aggregation with retained sample counts, extrema and
mean absolute deviation. Nested/ambiguous pairs, unsupported phases, mismatched
frequencies and contradictory architecture fail admission. A repeated measured
aggregate cannot silently match one simulated run. Cross-core subtraction,
arbitrary kernel intervals and host/unmodeled overhead have no inferred mapping.
Missing/incompatible evidence blocks comparison; admitted disagreement fails.

Calibration requires distinct recomputed semantic workload/condition fingerprints
and capture groups, with no reused raw capture across splits. Declare candidates,
finite search/runtime budgets, weights, positive scales and tolerances before
execution. Loss is weighted mean absolute scaled error. Acceptance uses
`abs(actual-reference) <= absolute_tolerance + relative_tolerance*abs(reference)`;
zero references require a positive absolute tolerance or an explicit exact policy.
Held-out failures remain failures, and ties do not imply unique physical parameter
identification. Measured calibration requires compatible actual measured fit and
held-out evidence; its validity is limited to those tested conditions.

## Compatibility and checks

Legacy event formats, predictor/embedding contracts, detailed 7-D runtime and
4-D hardware features, and root four-coordinate RL actions are preserved. All
five new validation document kinds are rejected before optional ML/model imports.
Supported legacy execution remains covered by existing regression fixtures.
Optional Torch/PyG checks are skipped/blocked when unavailable, never fabricated.

```sh
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/validation simulator_detailed/validate_wormhole.py simulator_detailed/topology_compatibility.py simulator_detailed/configs/schemas/validation.py simulator_detailed/tests/test_validation*.py simulator_detailed/tests/validation_fixtures.py
```

Named shell-free suite gates can run detailed/compute/memory/torus unittests,
strict Pyright, scoped Ruff, the actual root Darknet19 smoke, or optional ML.
Each has a declared wall-time budget. Any unittest skip keeps that gate blocked;
executed assertion/type/lint failures remain failures. Do not include the full
detailed gate in a test-created suite that recursively runs itself.

No numerical tensor, TT-Metal/RISC-V/ISA/kernel, general dynamic collective,
interchip, host/PCIe or new predictor/RL execution is provided. Physical
NIU/DRAM-bank/cache fidelity and measured device timing remain unvalidated.
Historical root remap tests still refer to removed
`LayerView.active_cores` and an inactive layer-5/core mapping; the separate passing
root smoke does not repair or pass those tests. See the
[child delivery evidence](../../openspec/changes/archive/2026-09-20-wormhole-validation-harness/delivery.md)
and the [finite multicast child](../../openspec/changes/archive/2026-09-20-wormhole-multicast-sync/progress.md)
for exact checks and supported scope.
