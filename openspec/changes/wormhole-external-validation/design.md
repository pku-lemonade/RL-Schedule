## Context

See [proposal.md](proposal.md) for motivation. The current validation harness
already imports hash-verified normalized functional JSON and a pinned subset of
TT-Metal device-profiler CSV, compares explicit addressed effects/partial order,
checks strict condition equivalence and performs bounded memory/compute calibration.
Its timing path intentionally accepts only `simulation_start_to_snapshot`, while
the model's mixed-runtime normalization currently exposes no interval metrics.
Synthetic fixtures prove those mechanisms but provide no external agreement.

This host has no `/dev/tenstorrent`, `tt-smi`, repository-local TT-Metal checkout
or ttsim checkout. External execution therefore has to be portable and resumable:
tooling can be implemented and verified here, while functional and silicon gates
remain blocked until a compatible worker supplies exact artifacts.

Update 2026-09-22: the silicon path is abandoned. No compatible Wormhole
worker is available or planned, so the hardware-gated tasks (4.3-4.5, 5.4-5.5,
6.3-6.5, 7.1, 7.5) will never be executed. The portable/resumable design below
is retained as delivered, offline-verified tooling, but it has no planned
silicon execution and must not be cited as silicon evidence.

Official sources inspected on 2026-09-20 establish the producer boundaries:

- [ttsim](https://github.com/tenstorrent/ttsim) commit
  `40bb1a2ad6a755279c4628ddc65e30b10721fdef`, README SHA-256
  `0f3871ba51747387f7ff7f3f0e896b221b220da5238f7df939ddb6910ca6b072`:
  a virtual Wormhole device used through TT-Metalium. It is a functional source,
  not a silicon timing source. Its guidance requires the same host/device path on
  simulator and silicon rather than simulator-specific conditionals.
- [TT-Metal device profiler](https://docs.tenstorrent.com/tt-metal/latest/tt-metalium/tools/device_program_profiler.html),
  retrieved HTML SHA-256
  `63c41e358a294ac2f603da10991d46934c204c8b67fe5cf6762b178569bc1dd9`:
  profiling is enabled with `TT_METAL_DEVICE_PROFILER=1`, emits
  `profile_log_device.csv`, adds instrumentation overhead, and documents precise
  same-core counters but possible inter-core skew and unsynchronized devices.
- The TT-Metal `perf_microbenchmark` tree at commit
  `a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e` contains NoC, DRAM and compute
  benchmark families. Those upstream cases are discovery inputs only; a campaign
  must pin exact source/build artifacts and prove its own model-boundary match.

## Goals / Non-Goals

**Goals:**

- Produce portable capture kits and evidence bundles whose identities survive
  movement between this repository, a ttsim worker and a Wormhole worker.
- Establish functional agreement before comparing or fitting timing.
- Compare only observable same-domain intervals with explicit simulator mappings.
- Preserve every hardware parameter as an input and fit only the four fields the
  existing calibration engine already admits.
- Make missing hardware, incompatible producer revisions, failed checks and
  ambiguous calibration first-class outcomes.

**Non-Goals:**

- Installing TT-Metal/ttsim, provisioning a device or remotely controlling an
  external host from the default validation CLI.
- Treating ttsim latency as device timing, public peak figures as measurements,
  or profiler documentation/example rows as Wormhole evidence.
- Numerical tensor equivalence, arbitrary kernels, full application performance,
  cache/bank/NIU fidelity, cross-device timing or broad silicon conformance.
- Changing the root simulator, predictor, embeddings, RL environment or existing
  replay/document versions.

## Decisions

### 1. Use a three-stage, artifact-mediated campaign

The campaign is separated into planning, collection and evaluation:

```text
repository planner
  |  external_validation_campaign v1 + capture kit
  +---------------------+----------------------+
                        |                      |
                 ttsim worker          Wormhole worker
                 functional raw        functional raw + profiler CSV
                        |                      |
                        +----------+-----------+
                                   |
                         external_capture_bundle v1
                                   |
                    hash verification + conversion
                                   |
                   existing validation/calibration core
                                   |
                     external_validation_report v1
```

`external_validation_campaign` records cases, named producers, expected artifacts,
source/build identities, budgets, boundary maps, repetitions, warm-ups, tolerances
and fit/evaluation partitions. `external_capture_bundle` records the immutable
manifest and collection outcomes; raw artifacts remain adjacent or in an explicit
portable store. `external_validation_report` links the campaign, bundle, generated
`validation_reference` documents and existing reports/results.

Collection workers accept the campaign and a named producer adapter. They assemble
fixed argument vectors from typed fields, record the final argv/environment and
never evaluate user shell. Planner/import/report modes remain offline. This keeps
device access explicit and makes interrupted collection resumable without letting
a JSON document become a remote command channel.

Alternative: make the current CLI install and launch vendor tools. Rejected because
it couples validation to mutable external state, complicates device safety and makes
offline behavior non-reproducible.

### 2. Run the same finite TT-Metal program through ttsim and silicon

The capture kit pins source revision, build configuration and exact binary hashes.
The same host/device artifacts implement three finite case families:

| Case family | External operation | Model boundary | Evidence use |
| --- | --- | --- | --- |
| `noc_ack_roundtrip` | finite remote L1 write plus explicit returned acknowledgement | operation submission to acknowledged completion | functional effects/order and measured comparison |
| `dram_read_return` | finite DRAM transfer returned to one worker with an explicit completion marker | addressed memory submission/service/completion | functional effects and memory fit/evaluation |
| `compute_service` | bounded declared matmul/FC-shaped work inside one profiled service zone | compute resource acquire to release | work/completion and compute fit/evaluation |

The producer emits a compact functional record containing declared inputs, selected
physical/logical identities, completion/status markers and independently computed
sentinel/status digests. The converter maps only supported effects and causal
relations into `normalized_functional_v1`. Sentinel checks establish the external
program's finite operation; they are kept separate from the abstract simulator,
which does not produce tensor values.

ttsim collection is the functional-reference gate. Silicon repeats the same program
and must also pass its functional gate before its profiler rows are eligible. A
binary, mapping, fidelity or operation mismatch blocks pairing.

Alternative: use unrelated upstream benchmark output tables. Rejected because
workload, layout, synchronization and measurement scope cannot be shown equivalent.

### 3. Add an explicit interval metric registry

Extend normalization additively by deriving metrics from already exported lifecycle,
service and resource events. Initial boundary names are:

- `operation_submission_to_acknowledged_completion`
- `memory_service_begin_to_end`
- `compute_resource_acquire_to_release`

Each metric retains its start/end points, clock domain, operation/resource identity,
completion scope and raw-event references. Campaign selection identifies exactly one
model interval or an explicit aggregation. Ambiguous/missing intervals block the
comparison. Existing `simulation_start_to_snapshot` metrics remain unchanged.

The profiler converter continues to select one device/core/RISC/zone/source/run and
pair begin/end on that same counter. A boundary map joins that zone to one allowed
model boundary. The comparison layer replaces its single hard-coded boundary check
with an allowlist plus semantic identity validation. It still rejects host time,
full kernels with unmodeled phases, cross-core subtraction and undocumented overhead
correction.

For a measured aggregate of N retained runs, the campaign runner executes or records
N corresponding deterministic model repetitions and emits matching sample policy;
one model sample does not silently stand for repeated hardware data. Comparisons in
seconds require both original frequencies and an explicit domain map.

Alternative: compare all cases by total elapsed time. Rejected because launch,
queueing and unrelated work would dominate and the result would not identify which
model parameter it tests.

### 4. Reuse strict v1 references and calibration internally

Producer converters create ordinary `validation_reference` v1 documents and feed
ordinary `validation_suite`/`calibration_plan` v1 inputs to the existing engine.
The new documents orchestrate provenance and lineage; they do not fork comparison
math or redefine evidence tiers. Generated files are written to an explicit output
directory and include their content hashes in the enclosing report.

The measured calibration grid remains limited to:

- memory `bytes_per_cycle` and `fixed_latency_cycles` by resource identity;
- compute `work_per_native_cycle` and `setup_native_cycles` by effective rate ID.

Transport cases are comparison cases, not new unconstrained routing/link fitting.
Fit/evaluation campaign entries have disjoint semantic fingerprints and capture
groups. Evaluation artifact identities are committed in the sealed plan; selection
reads fit observations only. Tolerances and the chosen vector are sealed before
held-out observations are evaluated. A tie remains ambiguous even if a canonical
order chooses a runnable candidate.

Alternative: add an optimizer for every timing field. Rejected because current
measurements cannot identify structural facts and an expanded parameter space would
make good aggregate fits easy to overinterpret.

### 5. Keep source claims and executed evidence separate

Planning records the official-source identities above, but those documents do not
count as execution. A campaign case changes state through `planned`, `collected`,
`imported`, `functionally_checked`, `timing_checked` and, where selected,
`fit`/`evaluated`; terminal outcomes are pass/fail/blocked. Reports preserve each
transition and never infer later evidence from an earlier state.

The first complete delivery requires actual ttsim functional captures and actual
Wormhole profiler captures for the declared case matrix. On hosts without those
prerequisites, implementation parts may be completed and committed through tested
blocked collection, but final external-evidence tasks remain unchecked. Synthetic
fixtures test converters only and keep their classification.

### 6. Preserve consumer and repository boundaries

Campaign code stays under `simulator_detailed/configs/schemas/` and
`simulator_detailed/validation/`, with a module entry point under
`simulator_detailed/`. Capture examples live under
`simulator_detailed/configs/validation/external/`; generated/raw captures live in
explicit user-selected storage and are committed only when small, redistributable
and useful for reproducibility.

Legacy input guards gain the two new document kinds. The 7-D detector inputs, 4-D
hardware features, checkpoints, root four-coordinate action space and existing
trace structures do not change. No default hardware size, rate, clock or tolerance
is introduced.

## Risks / Trade-offs

- **No external worker is available** -> keep collection tasks blocked with exact
  prerequisites; never replace them with synthetic passes.
- **ttsim and silicon execute different code** -> hash host/device artifacts and
  build settings, ban simulator-specific branches, compare effective mappings.
- **Profiler instrumentation changes timing** -> record instrumentation and zone
  density, use sparse zones, retain raw samples and make no hidden correction.
- **A zone does not match a model interval** -> require allowlisted boundary maps
  and block host/full-kernel/cross-core windows.
- **A model fits aggregate data for the wrong reason** -> require functional gates,
  multiple sizes/conditions, disjoint held-out captures and visible parameter ties.
- **Raw evidence is too large or restricted** -> retain hash-addressed manifests and
  portable references; a missing artifact blocks reproduction.
- **Producer formats drift** -> pin revisions and extractor versions; a new format
  requires a new named adapter and fixtures.
- **Hardware origin is spoofed** -> label integrity separately from authentication;
  add attestation only if an independently trusted mechanism is later supplied.

## Migration Plan

1. Add campaign/bundle/report contracts, compatibility guards and synthetic invalid
   cases without changing existing validation documents.
2. Add interval normalization and comparison admission with regression coverage for
   every supported and blocked boundary.
3. Add the finite TT-Metal capture-kit generator and ttsim functional converter,
   then verify a real pinned ttsim capture when a worker is available.
4. Add Wormhole device collection and profiler conversion, preserving raw samples
   and device/build metadata; verify blocked behavior on ordinary hosts.
5. Add campaign orchestration, paired functional gates and measured comparisons for
   the three case families.
6. Add measured fit/seal/held-out orchestration using the existing bounded engine.
7. Execute the external matrix, publish exact identities and limitations, run the
   complete regression/type/lint/OpenSpec checks and reconcile the new capability.

Each completed part updates progress evidence and is committed before the next.
Rollback removes the opt-in campaign layer and interval additions while retaining
the existing v1 harness and simulator behavior. No push occurs without a request.

## Open Questions

- The first worker's exact Wormhole board identifier, TT-Metal revision, firmware
  and device clock will be recorded in campaign inputs when that environment is
  supplied; none is assumed by this design.
- Raw captures may be committed when redistribution and size permit, or stored in
  a separately identified artifact location. Either choice must preserve the same
  manifest and hash-verification contract.
