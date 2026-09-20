# Wormhole external validation progress

## Profiler-enabled worker correction and clean rerun

Status: validated on 2026-09-21. Progress remains 25/35. The hardware
collector implementation is now executable end to end on a compatible worker,
but this host has no Tenstorrent device and therefore produced no silicon
capture, timing comparison, fit or held-out result.

The previous worker accepted only the 17-argument ttsim interface while the
typed hardware plan emitted 23 arguments. It also hard-coded device zero,
required simulator variables and could not emit profiler CSV or a typed worker
manifest. The corrected worker retains that ttsim interface and adds a fixed
25-argument hardware interface with a sealed collection-plan path, explicit
device index and PCIe binding, exact profiler selection, finite budgets and
three declared outputs. It verifies the campaign, plan, build, conditions,
arguments, environment and output contract before device creation.

The profiler-enabled TT-Metal build now uses `ENABLE_TRACY=ON`. The campaign
seals the host, Metalium, Tracy, UMD, TT-STL, hwloc, ttsim and producer-source
bytes. Both producer modes set the fixed portable
`LD_LIBRARY_PATH=bin/runtime`; legacy four-variable ttsim manifests remain
readable. The compute zone is emitted only by `TRISC_1`, matching its typed
same-RISC selection.

A direct hardware invocation with a valid sealed plan loaded every non-system
runtime library from the kit and reached `MeshDevice::create_unit_mesh`. It
then failed with `No chips detected in the cluster`. The earlier
`TT_METAL_DEVICE_PROFILER requires a Tracy-enabled build` failure is gone. This
is a successful unavailable-host preflight, not hardware evidence.

Two independently generated clean kits then ran all three ttsim recipes and
converted every capture. Their campaign, kit/source manifests, capture trees
and reference trees matched byte for byte. The first tree replaced the
committed functional evidence.

| Identity | Value |
| --- | --- |
| Campaign SHA-256 | `e3138d2f8eefe648cce1c12f0670c9c39f44de7b3838695b6e93385ffc35cb15` |
| Kit identity | `ttsim-kit:7db98738f60681c63e2c3d580590c9e948d8e698ca9dfbeef6d753e97b6729a7` |
| Host executable SHA-256 | `6f7d9158b7b4118ad2c75587dfba86258a55332c64e5048a154c8a451be50e03` |
| Metalium runtime SHA-256 | `0047e781a26932d8a534e8f27a1cc2ae0f809cc3b043c94adbaa17ecd8b38eee` |
| Producer source bundle SHA-256 | `4065dd20549c1f10dbfd1ce92350676b80fd3120195a255c08a0cc4bdfdb1138` |

| Case | Capture bundle | Raw functional SHA-256 | Reference identity |
| --- | --- | --- | --- |
| `noc-64b` | `ttsim-capture:875f6eb456329f0fd7e30ee728789342cc2a8a0c7336bfa6740cd5783dff3239` | `ca65dc818c2cec0947d428ac5ba57b85145ac23195fc29294a6414b8b19917f7` | `external:noc-64b:7c27b9640416e2fe29895aa0d64d44fec25a8fbabf541713fad8e839ad28ede8` |
| `dram-read-256b` | `ttsim-capture:4671a8ee6c0fa73b425408d1d3c80e6f2540f955646d4a6db9535d97192d6a05` | `00883361d077881a6b822608de9cde7e268372dc9f09b658396abbe474f0a73b` | `external:dram-read-256b:d6bd37baaa9c206b522560daff470c1dad3bf9eb8d3de2acd908d786f82ce94b` |
| `compute-bf16-32` | `ttsim-capture:70c696a27a24a7552c55325eaa3a7b9ba2620269fd3957472edc26c9baf2af8e` | `1f6a1c99046aef816e2bf45b78c079de98bafe7fa0d1fe4e4a14df4a0ea19e89` | `external:compute-bf16-32:6aaf88aa8d83d8c4799be37aba9a1fe95e18bccf842b4360ce5d8a52952cfe41` |

Temporary execution roots are
`/tmp/wormhole-tracy-preflight-20260921-eiqgbc51`,
`/tmp/wormhole-tracy-evidence-a-20260921-ipw8o0h_` and
`/tmp/wormhole-tracy-evidence-b-20260921-fvntxnaq`. They are reproducibility
workspaces, not tracked evidence.

Executed verification:

```text
Tracy-enabled TT-Metal configure and wormhole_external_validation build
passed at pinned TT-Metal revision a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e.

direct sealed hardware preflight
plan, environment and portable runtime libraries validated; device creation
blocked with "No chips detected in the cluster"; no silicon evidence emitted.

two independent clean ttsim matrices
6/6 case executions passed; campaign, kit/source manifests, capture trees and
converted reference trees matched byte for byte.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
613 tests discovered in 226.164s; 612 passed; 0 failures; 0 errors;
1 optional skip.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <changed Python implementation and tests>
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --type change --strict --no-interactive
Change 'wormhole-external-validation' is valid.

delivery identity verifier
44 source, test, fixture and evidence identities matched current bytes;
progress is 25/35 with 10 open tasks.
```

## Historical blocked silicon campaign checkpoint

Status: executed on 2026-09-20 after the earlier clean ttsim rerun. Progress remained
25/35 because this host has no Wormhole device. No hardware-dependent task was
marked complete and no unavailable result was promoted to silicon evidence.

The committed three-case ttsim campaign was paired with fresh
`write_unavailable_wormhole_capture` bundles using the observed prerequisite
failure: `/dev/tenstorrent` is absent and `tt-smi` is unavailable. The run
packaged all six case/producer bundles plus three field-level equivalence
results into an external report. Each case produced the same fail-closed
behavior:

- Silicon collection was `blocked` before device access or producer launch.
- Canonical equivalence was `blocked` on 14 unavailable clock, enabled-layout
  and instrumentation fields; workload, mapping and source/build identity were
  not fabricated or rewritten.
- Profiler import rejected the bundle with `profiler conversion requires a
  successful Wormhole capture`.
- Paired functional and timing outcomes remained blocked, and the report status
  was `incomplete`.
- Independent final-evidence audit rejected the unavailable software condition
  instead of accepting the incomplete report as delivery evidence.
- Measured-calibration planning rejected an empty hardware binding set with
  `external calibration requires case bindings`.

Temporary execution identities (the generated directory is intentionally not
tracked evidence):

| Identity | Value |
| --- | --- |
| Campaign SHA-256 | `5754e19d3e7e30be85a59c8bab162fe2f2c6fe6409c44ed32360c735850a3be3` |
| Blocked report ID | `external-report:b284fa5ea8571d170127c4b526b5e9325664e9edf3b19dcd34b3cdc44abe6293` |
| Blocked report SHA-256 | `8c6b2afb8e10dd0ef2f637d255735238ec4fb57a2395cafff5e89912213db8e7` |
| Bundles / derived artifacts / outcomes | `6 / 3 / 9` |

This exercise confirms the downstream evidence gates using the actual pinned
ttsim captures, but it does not satisfy tasks 4.3-4.5, 5.4-5.5, 6.3-6.5,
7.1 or 7.5. Those tasks still require live CSV and complete manifests from a
named Wormhole worker, followed by matched comparison, disjoint measured fit
and held-out evaluation, a clean silicon rerun and final delivery identities.

## Part 3 - Portable capture kit and ttsim functional evidence completion

Status: complete on 2026-09-20. Tasks 3.1-3.5 are complete and the change is
25/35. The generated producer was built and all three finite recipes were run
on the reconstructed pinned ttsim/TT-Metal worker. The committed evidence is
functional evidence only; no ttsim counter or duration is classified as
silicon timing.

Delivered and verified:

- The host dispatches bounded TT-Metal programs for NoC acknowledgement, DRAM
  read/return and one 32x32x32 BF16 HiFi2 compute tile. It derives L1 and DRAM
  addresses from the allocator, uses the declared logical cores and bank, runs
  the declared repetitions, checks completion/status sentinels and validates
  the full compute output before atomically emitting a functional record and
  capture manifest.
- The kit includes the host, data-movement and compute kernels, a portable
  Wormhole ttsim SoC descriptor, build instructions and the exact single-rank
  TT-Metal worker patch. The patch implements the identity result for a
  one-rank control-plane `all_reduce`; its SHA-256 participates in the
  effective TT-Metal source snapshot.
- The binary manifest seals the host executable, patched Metalium runtime,
  Tracy, UMD, TT-STL, hwloc, ttsim runtime and producer source bundle.
  Collection rechecks those bytes, fixed arguments, five allowed environment variables, budgets and all raw
  output identities before admitting evidence.
- Each raw bundle was independently admitted and converted. Conversion
  rechecked effective operation/mapping/layout/fidelity, every repetition,
  event order, addresses, byte/work counts, completion markers and independently
  derived sentinel payloads. All three references are `functional_capture`
  with complete execution and two verified effects, and reproduce byte for
  byte from the committed raw bundles.
- The worker patch is limited to ttsim. Hardware collection continues to use
  the pinned upstream silicon path. Compute v1 deliberately supports exactly
  one 32x32x32 tile; other shapes fail admission rather than being defaulted or
  silently reinterpreted. Cores, banks, addresses, counts and patterns remain
  explicit campaign parameters.

Pinned worker and build identities:

| Identity | Value |
| --- | --- |
| ttsim revision | `40bb1a2ad6a755279c4628ddc65e30b10721fdef` |
| ttsim source snapshot SHA-256 | `92d33ef15728f17ed5488d28d24dac13550ef58d3b2da16c9fa8a63bd6bb69d0` |
| ttsim runtime SHA-256 | `cc5ccdbde14e92226a014e47167aaf02b5d8b7b3ad50f074eb9bb0bc7d084b50` |
| TT-Metal revision | `a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e` |
| TT-Metal base tree SHA-256 | `b678cb541691a4ae1d5a388bac9c0518ab62b48a1f427383ff35b3eee9a7cc71` |
| worker patch SHA-256 | `7f8e51483c46d92f0cf272ae3901cf67add10c10d9d479adf4b3a26282e9bae0` |
| effective TT-Metal snapshot SHA-256 | `b21803746332ef792157ddc26e33a1baba0664669fe331419899ed0d7450a169` |
| host executable SHA-256 | `6f7d9158b7b4118ad2c75587dfba86258a55332c64e5048a154c8a451be50e03` |
| Metalium runtime SHA-256 | `0047e781a26932d8a534e8f27a1cc2ae0f809cc3b043c94adbaa17ecd8b38eee` |
| producer source bundle SHA-256 | `4065dd20549c1f10dbfd1ce92350676b80fd3120195a255c08a0cc4bdfdb1138` |
| campaign SHA-256 | `e3138d2f8eefe648cce1c12f0670c9c39f44de7b3838695b6e93385ffc35cb15` |
| kit identity | `ttsim-kit:7db98738f60681c63e2c3d580590c9e948d8e698ca9dfbeef6d753e97b6729a7` |

Committed functional evidence:

| Case | Capture bundle | Raw functional SHA-256 | Reference identity |
| --- | --- | --- | --- |
| `noc-64b` | `ttsim-capture:875f6eb456329f0fd7e30ee728789342cc2a8a0c7336bfa6740cd5783dff3239` | `ca65dc818c2cec0947d428ac5ba57b85145ac23195fc29294a6414b8b19917f7` | `external:noc-64b:7c27b9640416e2fe29895aa0d64d44fec25a8fbabf541713fad8e839ad28ede8` |
| `dram-read-256b` | `ttsim-capture:4671a8ee6c0fa73b425408d1d3c80e6f2540f955646d4a6db9535d97192d6a05` | `00883361d077881a6b822608de9cde7e268372dc9f09b658396abbe474f0a73b` | `external:dram-read-256b:d6bd37baaa9c206b522560daff470c1dad3bf9eb8d3de2acd908d786f82ce94b` |
| `compute-bf16-32` | `ttsim-capture:70c696a27a24a7552c55325eaa3a7b9ba2620269fd3957472edc26c9baf2af8e` | `1f6a1c99046aef816e2bf45b78c079de98bafe7fa0d1fe4e4a14df4a0ea19e89` | `external:compute-bf16-32:6aaf88aa8d83d8c4799be37aba9a1fe95e18bccf842b4360ce5d8a52952cfe41` |

Executed verification:

```text
TT-Metal CMake configure and wormhole_external_validation target build
passed against revision a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e with the
recorded single-rank worker patch.

pinned ttsim collection and functional conversion
noc-64b: pass, functional_capture, complete;
dram-read-256b: pass, functional_capture, complete;
compute-bf16-32: pass, functional_capture, complete.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_capture_kit
12 tests passed; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_capture_kit simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
151 tests passed; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <Part 3 implementation and regression scope>
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --type change --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

Remaining external prerequisite: Parts 4-7 still require a named Wormhole
silicon worker with device inventory, firmware, clocks and profiler CSV. This
host has no `/dev/tenstorrent` device and no `tt-smi`; ttsim functional evidence
does not satisfy any silicon timing, paired comparison, measured fitting or
held-out evaluation task.

## Part 7 - Consolidated external evidence and delivery checkpoint (historical)

Historical status at commit `f43b774`: offline implementation checkpoint on
2026-09-20; tasks 7.2, 7.3 and
7.4 are complete. The change is 22/35. Tasks 7.1 and 7.5 remain open because
the final clean external matrix and the actual-evidence delivery links cannot
be produced on this host. This checkpoint is not final delivery and is not an
archive candidate.

Current addendum after commit `668cf3e`: the ttsim half of task 7.1 was
re-executed from a newly generated kit in
`/tmp/wormhole-final-clean-20260920-ouh26klr`. The Wormhole half remains
blocked, so task 7.1 is still unchecked.

Delivered and verified:

- The portable report package now includes the campaign's simulator inputs and
  each imported reference's raw bytes. It re-admits the copied campaign and
  references before publication, rejects escaping or aliased raw paths, and
  checks the raw digest again after copying.
- `audit_external_report` independently recomputes the report identity, verifies
  every declared byte and path, re-admits the packaged campaign, captures and
  references, and reconstructs lineage as a rooted acyclic graph. It checks
  exact case/producer coverage, source/build identity, canonical workload,
  layout, fidelity, mapping, clocks, declared output roles and direct parentage
  without trusting producer summary fields.
- The audit ties each reference to exactly one direct capture bundle, requires
  comparison lineage to include the model and reference, checks calibration
  plan/result seals, and rejects passing timing based on non-hardware evidence.
  Corruption tests cover changed bytes, missing packaged raw evidence,
  re-sealed source/fidelity mutations and missing producer coverage.
- Existing workflows remain offline and compatible. The root 4x4 Darknet19
  smoke completed 37,888 nodes on 16 cores and 48 links across 11
  JSON-round-tripped windows. The separate compatibility selection passed 45
  tests with one optional dependency skip. The optional ML gate remained
  visibly blocked because `torch` and `torch_geometric` are unavailable.
- The offline validation suite passed all 123 checks in 19 cases. Its optional
  ML gate remained blocked, and its functional-reference and silicon-timing
  tiers remained unvalidated. No external tool or device was accessed.
- The latest clean profiler-enabled ttsim kits regenerated identity
  `ttsim-kit:7db98738f60681c63e2c3d580590c9e948d8e698ca9dfbeef6d753e97b6729a7`.
  Two independent NoC, DRAM and compute executions reproduced all three
  committed capture and converted-reference directories byte for byte,
  including bundle and reference identities. This re-execution supplies
  functional evidence only.

Implementation source SHA-256 identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `validation/external_campaign.py` | `c96c24ffdb10f7051a16043ac4c639112c6fc56138237b6b8591cce27d800c88` |
| `validation/external_audit.py` | `e6477ec7be9fdb17a21170ab1fa08ff1de9acddb4c0cc63d3c6b6793a9b01f9f` |
| `tests/test_external_campaign.py` | `4edb5e5bcd5b9db6ec357f94e96a223ca23f8ad18b2ca2923103f832b9ff86d0` |
| `tests/test_external_audit.py` | `1e7607ac149ee09e69e4837d6d8c2d3ff7edc1430e2867da4f52448dd240658e` |

Executed verification:

```text
clean pinned ttsim matrix from a fresh generated kit
noc-64b: bundle 875f6eb4..., reference raw ca65dc81..., byte-identical;
dram-read-256b: bundle 4671a8ee..., reference raw 00883361..., byte-identical;
compute-bf16-32: bundle 70c696a2..., reference raw 1f6a1c99..., byte-identical;
kit manifest and producer source manifest: byte-identical to committed evidence.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_audit simulator_detailed.tests.test_external_campaign simulator_detailed.tests.test_external_calibration
12 tests passed in 4.275s; 0 failures; 0 errors; 0 skips.

root Darknet19 regression gate
RegressionGate root_darknet19_smoke, EV-08, 180 seconds: passed;
completed_nodes=37888, cores=16, links=48, windows=11,
json_roundtrip=true.

.venv/bin/python -m unittest simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_topology_consumers simulator_detailed.tests.test_memory_adapters simulator_detailed.tests.test_compute_adapters simulator_detailed.tests.test_multicast_validation
46 tests discovered in 20.997s; 45 passed; 0 failures; 0 errors;
1 optional skip.

optional ML regression gate
blocked; not executed; missing torch and torch_geometric.

.venv/bin/python -m simulator_detailed.validate_wormhole --suite simulator_detailed/configs/validation/offline.json --output /tmp/wormhole-external-offline-20260920.json
status pass; 19 cases; 123 checks passed; 0 failed; 0 blocked case
checks; optional ML blocked; functional-reference and silicon-timing
unvalidated; suite SHA-256
3e17ba0f21ff9ee3a7d4c07f98bd2d1d0017596a2e55b406fcd2800f8b72ee7b.

.venv/bin/python -m unittest discover -s simulator_detailed/tests
606 tests discovered in 222.104s; 605 passed; 0 failures; 0 errors;
1 optional skip.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check <13 affected implementation paths and 14 affected/predecessor test paths>
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --type change --strict --no-interactive
Change 'wormhole-external-validation' is valid.

delivery identity verifier
33 source, test and fixture identities matched their recorded SHA-256 values
and sizes where recorded; progress is 22/35 with 13 open tasks.

git diff --check
Passed with no output.
```

Blocked prerequisites observed on this host:

- `/dev/tenstorrent` is absent and `tt-smi` is unavailable. The reconstructed
  pinned ttsim/TT-Metal worker completed the fresh functional half of task 7.1,
  but no named Wormhole worker, firmware inventory or live profiler output is
  available.
- Task 7.1 still requires fresh Wormhole runs from the clean producer assets. Task 7.5
  requires those actual artifacts for its EV-02 through EV-07 evidence links.
  The checkpoint manifest records the implementation and test identities but
  does not substitute them for external capture identities.

## Part 6 - Measured fitting and sealed held-out evaluation

Status: implementation checkpoint on 2026-09-20; tasks 6.1 and 6.2 are
complete. Tasks 6.3, 6.4 and 6.5 remain open because no admitted Wormhole fit
and held-out captures exist on this host. Synthetic references exercise the
mechanics but are explicitly labeled and cannot produce a measured-calibration
claim.

Delivered and verified:

- An external campaign planner packages the campaign, each admitted simulator
  input and source, and each raw/reference pair into a portable existing-format
  calibration plan. One plan accepts one supported family and only the existing
  typed memory bandwidth/fixed-latency or compute work-rate/setup targets; NoC,
  topology, clocks, dimensions, capacities, harvesting, fidelity and protocol
  fields cannot enter the candidate grid.
- Every case selects its exact metric and retains explicit entity/clock maps.
  The planner verifies campaign/reference conditions, family/target agreement,
  boundary identity, evidence classification, finite budgets, source targets
  and disjoint fit/evaluation bindings before atomically publishing the plan.
- Calibration admission now seals and verifies the source campaign in addition
  to the existing plan, source, reference and raw-capture identities. Candidate
  declaration order, metric weights/scales/tolerances, semantic fingerprints,
  capture groups and reference identities remain part of the plan or selection
  seals. Existing plans without the additive per-case selectors retain their
  prior all-metric behavior.
- The existing bounded engine applies only typed numeric targets to copied
  configurations, re-admits every candidate, freezes the first declared
  minimum and its configuration/fit-evidence digests, and evaluates held-out
  cases without refitting. Tests retain candidate failures, all-invalid search,
  ties, held-out failure and unchanged source inputs.
- A result publisher atomically emits the existing calibration-result document
  and distinct calibration/evaluation outcomes for the external report. It
  includes candidate history, fit/evaluation error text, per-reference sample
  statistics, exact admitted case conditions, selected targets, ambiguity and
  explicit limits against unique physical identification, untested workloads
  and full-device timing claims. Its portable report path is covered with
  synthetic evidence only; task 6.4 awaits a real measured result.

Implementation source SHA-256 identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/validation.py` | `c51bea93b96d64d882248817ed8538c8bd5b89f439cfbad8dbe52af7b94c41dd` |
| `validation/calibration.py` | `f31574f2ce8644fe9cf20422281d55640cc7831fde6c97575f6953593e8a947c` |
| `validation/external_calibration.py` | `a3f12db9a5d8733ad27920a57306e16941af1e39259becd5966c28b061ae208c` |
| `tests/test_external_calibration.py` | `b4d17073954c12714eca5ee53a94a4e8b36384ed78ee214db02488812886378b` |
| `tests/test_validation_calibration.py` | `597835ce8cae5c8d0e3c6b45bfb6e23365d8dbecb846037c0d44eb1bdcaae82a` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_calibration simulator_detailed.tests.test_validation_calibration
21 tests passed in 27.857s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_calibration simulator_detailed.tests.test_validation_calibration simulator_detailed.tests.test_external_campaign simulator_detailed.tests.test_external_wormhole_collector simulator_detailed.tests.test_external_capture_kit simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
147 tests passed in 108.390s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/validation.py simulator_detailed/validation/calibration.py simulator_detailed/validation/external_calibration.py simulator_detailed/tests/test_external_calibration.py simulator_detailed/tests/test_validation_calibration.py
All checks passed.
```

Blocked prerequisites observed on this host:

- `/dev/tenstorrent` is absent, `tt-smi` is unavailable and no ttsim shared
  library was found in the repository. No pinned TT-Metal build, named Wormhole
  board, firmware inventory or live profiler CSV has been supplied.
- Tasks 6.3-6.5 require disjoint admitted hardware captures for measured fitting
  and held-out evaluation. The publisher is ready for those artifacts, but no
  synthetic result is promoted to measured evidence or counted as completion.

## Part 5 - Paired campaign execution and scoped reports

Status: implementation checkpoint on 2026-09-20; tasks 5.1, 5.2 and 5.3 are
complete. Tasks 5.4 and 5.5 remain open because no actual ttsim/Wormhole capture
pair is available. No synthetic fixture is reported as external evidence.

Delivered and verified:

- A resumable state machine records the ordered `planned`, `collected`,
  `imported`, `functionally_checked` and `timing_checked` prefix independently
  for every case/producer pair. Every transition commits the campaign and prior
  artifact hashes, stores new output under its SHA-256, advances atomically and
  re-verifies all retained bytes on restart. Completed stages are reused; stale
  bytes, out-of-order stages, failed producers and output-budget violations do
  not advance state.
- Canonical field-level equivalence checks compare the campaign intent, ttsim
  capture and silicon capture across pinned source/build/binary identities,
  architecture/profile, layout, workload fields (including operation,
  bytes/work, address, shape and fidelity), mapping/fabric, instrumentation and
  clocks. A mismatch remains a named blocked field rather than being normalized
  away.
- Paired functional gates require both converted ttsim and silicon observations
  to match addressed effects and causal order before timing is eligible. A
  corrupted effect blocks an otherwise plausible timing result and removes its
  accepted timing artifact IDs. External sentinel bytes remain producer
  validation data and are not compared as simulator tensor output.
- Boundary maps now carry explicit metric policies and clock/entity mappings.
  The timing wrapper reuses the existing strict comparison engine and reports
  signed, absolute and relative errors plus preserved sample count and
  dispersion. No hardware tolerance, frequency or mapping is defaulted.
- The report writer atomically publishes a portable campaign, independently
  admissible capture bundles, generated artifacts, complete hashes and lineage.
  Mixed pass/fail/blocked outcomes remain distinct and determine honest report
  status. Runtime-selected device, firmware and clocks are preserved during
  functional conversion while canonical workload/layout identity still has to
  match the campaign.
- The complete suite exposed an interval-addition compatibility issue: derived
  intervals had displaced legacy metrics from `metrics[0]`. Legacy metrics are
  again emitted first and additive interval metrics follow, restoring existing
  calibration consumers without changing interval selection by identity.

Implementation source SHA-256 identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `5c203ce9fa99a80b763217a7fb1fcff67f589d4ae852d15c28f8869873a7cc92` |
| `validation/external_campaign.py` | `b307c1f56805fe10d78a44d929a16e4d4eb935db9e91a9907cf1dc382fcd0963` |
| `validation/external_capture.py` | `4f9512aa3cb36457207d7319f56ee80266f16ff9b004421214f505e9b4af6196` |
| `validation/normalize.py` | `8737cadb3d7746ef9a92066c3d772ff844b420b2a9c5e693f74f85276ed6ba57` |
| `tests/test_external_campaign.py` | `77ff06a8d4e69bfc09bab3e9ef275c58b1868a4376af479c4239b8bedb95f7e9` |
| `tests/test_external_capture_kit.py` | `9e1d59b821abac7d38486aec4abea2d5c9f862a5689ff7b24297c61aa6208351` |
| `tests/test_external_validation_intervals.py` | `d6c29b0831992ff64bea9070b01cdc31097415a5658f751cdfb9c41bc8f4f31a` |

Revised synthetic fixture identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `36a2b9d5711b87ad9dd20bcfd6d66249649affda27f9b3ee3f1ed183808060c5` |
| `capture.valid.json` | `e2662ac44210a0361af5af2e0453f98ecc9ca7d5a2ec1f438dae0df7209f3672` |
| `capture.unavailable.json` | `7d0a5e58a745118b9d2eb4f5fafb7e3d130c18c0592cabe672054826bc108f4e` |
| `report.blocked.json` | `224581cca68bf638689807217a6beaa31820102007b4299d7ca21f7b77cf050f` |

Executed verification:

```text
.venv/bin/python -m unittest discover -s simulator_detailed/tests
599 tests run in 213.890s; 598 passed; 0 failures; 0 errors; 1 optional Torch/PyG gate skipped.

.venv/bin/python -m unittest simulator_detailed.tests.test_validation_calibration simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_external_campaign
33 tests passed in 23.767s after restoring legacy metric order; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/external_validation.py simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/tests/test_external_campaign.py simulator_detailed/tests/test_external_capture_kit.py simulator_detailed/tests/test_external_validation_intervals.py simulator_detailed/tests/test_external_wormhole_collector.py simulator_detailed/tests/test_validation_adapters.py simulator_detailed/tests/test_mixed_validation.py simulator_detailed/tests/test_validation_references.py simulator_detailed/tests/test_external_validation_contracts.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

Blocked prerequisites observed on this host:

- Tasks 5.4 and 5.5 require successful captures from both the pinned ttsim
  environment and one explicitly selected Wormhole device for all three case
  families. The host still has no `/dev/tenstorrent`, `tt-smi`, pinned TT-Metal
  checkout, matching built binaries or ttsim shared library.
- The implemented timing/report mechanics are covered with synthetic unit data,
  but an actual three-family comparison, real error/dispersion report and paired
  integration result cannot be claimed until those capture bundles arrive.

## Part 4 - Wormhole collection and profiler import

Status: implementation checkpoint on 2026-09-20; tasks 4.1 and 4.2 are
complete. The task 4.3 converter implementation is source-compatible with the
pinned profiler revision, but tasks 4.3, 4.4 and 4.5 remain open until it is
checked against output from a named Wormhole worker. No Wormhole device was
accessed and no silicon timing evidence is claimed.

Delivered and verified:

- A strict `wormhole_tt_metal_profiler_v1` collection plan with an explicit
  worker, device index, PCIe slot and Wormhole architecture; fixed argv and
  profiler environment; finite repetition, warm-up, timeout and output-byte
  limits; exact source/build/binary identity; effective conditions; and three
  declared output roles.
- A bounded collector that verifies the copied campaign, case input and every
  binary before launching the named host program. It preserves existing worker
  and bundle outputs, passes the explicit device selector, suppresses unbounded
  process output, parses a typed worker result, retains captured host/device,
  software, firmware, clocks and enabled layout, and publishes an atomic,
  hash-verified capture bundle. Missing prerequisites and producer failures
  produce a blocked bundle with unknown metadata reasons and no raw evidence.
- Sparse `DeviceZoneScopedN` regions in the shared NoC, DRAM and compute
  producer sources. The DRAM zone ends at the read barrier, before the separate
  completion-marker write. Tests require each source/line/zone tuple to be
  unique and require the ttsim kit and silicon plan to carry the same
  build/binary manifest and effective campaign conditions. The newly generated
  instrumented kit identity is
  `ttsim-kit:d9142e2abd6a4283ccd24809ad4450a6d60bc26730bd8beac670131c5ef22a56`.
- Hardware capture admission now requires all three declared output roles and
  one exact profiler selection per boundary. The selection fixes device, core,
  RISC, zone, source file/line, clock, metric, run IDs, warm-ups and aggregation.
  This stricter rule is scoped to successful hardware captures so existing
  functional-capture contracts remain compatible.
- Atomic profiler-reference conversion from an admitted hardware bundle through
  the existing version-1 importer, including a second raw/campaign hash check.
  A discovered single-retained-run defect was fixed so its measurement window
  retains the original integer begin/end counters instead of rewriting them as
  zero/duration. Tests cover counters above `2**53`, raw mutation and output
  preservation.
- The importer now accepts the exact 15-column CSV and metadata header emitted
  by `tt_metal/impl/profiler/profiler.cpp` at revision
  `a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e`, while retaining the existing
  strict 12-column fixture dialect. It maps ordered `ZONE_START`/`ZONE_END`
  occurrences to declared repetition IDs, requires matching timer, host-run and
  optional trace identities, and retains every selected integer field plus raw
  row metadata on the normalized events. Header/phase drift, nested or orphaned
  zones, counter-identity changes, wrong architecture/frequency and cross-core
  or cross-device ends all fail conversion.
- The pinned CSV's misleading `PCIe slot` column is treated according to the
  producer source, which writes numeric `chip_id`; worker-result validation
  binds it to the selected device index and continues to admit the prior BDF
  representation for version-1 compatibility. The actual BDF remains separately
  recorded in the explicit worker selection and device metadata.

Implementation source SHA-256 identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `7dd7d344890b0c18806ad75ee2b26503c48009437266c8672f4cb54a9b162804` |
| `validation/external.py` | `af5b679027d9b7089fa81459ef0735fc8f8e67c5471243b3bcaa1a35f2ca42b4` |
| `validation/external_collector.py` | `52f549ae47aa14f94a97659331bba4d69edc54a7592d2b71d6690149e9861485` |
| `validation/references.py` | `78de05b37429dd966c6c19d0f29c7f433c10c559ebae506e5a287ff053b17a42` |
| `tests/test_external_wormhole_collector.py` | `bd606944143b4de0e1a4d99ad31d73ecfa256c1cde8fdd5d411db7132f11d5d1` |
| `capture_assets/ttsim_tt_metal_v1/kernels/noc_ack_roundtrip.cpp` | `fb1803dbb60f33d43c6e98af1c481feec95cf779f728c2921e06a9ec6ef6a77f` |
| `capture_assets/ttsim_tt_metal_v1/kernels/dram_read_return.cpp` | `84809bb95c6c6b61fcb4fbbf258ca807ced5bb4386c1d53008862dde37863cd7` |
| `capture_assets/ttsim_tt_metal_v1/kernels/compute_service.cpp` | `4fa6490d3df50d5d04b6536625a00790b3b7c1a9822285d72f8b41e78267f432` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_wormhole_collector simulator_detailed.tests.test_external_capture_kit simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
160 tests passed in 116.873s; 0 failures; 0 errors; 0 skips after adding
the exact pinned profiler dialect and corruption coverage.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_wormhole_collector simulator_detailed.tests.test_external_capture_kit simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
153 tests passed in 115.747s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m unittest simulator_detailed.tests.test_external_wormhole_collector simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts
42 tests passed in 2.624s after the final PCIe-selection guard; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/external_validation.py simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/tests/test_external_wormhole_collector.py simulator_detailed/tests/test_external_capture_kit.py simulator_detailed/tests/test_external_validation_intervals.py simulator_detailed/tests/test_validation_adapters.py simulator_detailed/tests/test_mixed_validation.py simulator_detailed/tests/test_validation_references.py simulator_detailed/tests/test_external_validation_contracts.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

Blocked prerequisites observed on this host:

- `/dev/tenstorrent` remains absent and `tt-smi` is not installed.
- Reconstructed pinned TT-Metal and ttsim checkouts and the functional build are
  available only under `/tmp/wormhole-external-worker`; no named Wormhole board,
  silicon worker build, firmware inventory or device-profiler output was supplied.
- Task 4.3 remains open until the converter is checked against CSV from that
  pinned live environment. Tasks 4.4 and 4.5 require the three actual functional
  records, profiler CSVs and complete worker manifests; unit-test worker bytes
  do not satisfy those evidence requirements.

## Part 1 - Campaign contracts and fail-closed admission

Status: complete on 2026-09-20. Implementation started from
`72a12c4598c135c915a74d984e7fdf53ead4d876` on Python 3.12.12 with
Pydantic 2.13.5, Pyright 1.1.414 and Ruff 0.16.7. No producer process,
vendor tool or hardware device was invoked.

Delivered:

- Strict immutable `external_validation_campaign`, `external_capture_bundle`
  and `external_validation_report` version-1 records. They retain finite case,
  invocation, timeout and output budgets; immutable source/build identities;
  explicit conditions and boundary maps; exact integer counters; artifact
  lineage; known/unknown metadata; scoped outcomes and honest report status.
- Pure campaign and capture preflight. It resolves portable paths from the
  declaring document, verifies declared byte sizes and SHA-256 identities,
  matches capture producer/build identity to the admitted campaign, protects
  inputs from output aliases and never evaluates command text.
- Dependency-free legacy consumer rejection for campaign, capture and external
  report documents before Torch/PyG or checkpoint loading. Static compatibility
  checks retain 7-D predictor features, 4-D detailed hardware features, the
  `models/best_model.pth` checkpoint location and four-coordinate root actions.
- Valid, unavailable-environment and adversarial fixtures for the three initial
  case families. These fixtures are synthetic contract evidence only.

Pinned planning source identities:

- TT-Metal source revision:
  `a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e`.
- ttsim source revision reserved by the design for later collection:
  `40bb1a2ad6a755279c4628ddc65e30b10721fdef`.
- The fixture build/source hashes are explicit synthetic identities and do not
  assert that either external project was built or run.

Fixture SHA-256 identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `8ecbd87dc5aaf9720368a3a8091e237014cb9ae2d8efec568375f6e977cf558c` |
| `campaign.adversarial.json` | `7383637d2e258e8b777fca89382149a05fc7ac08b1f166e2defb26c5d41899ac` |
| `capture.valid.json` | `57ce641fdec9fbc5937cac8bc6bcdada3467c79089dd17f3fe0bc2173caf73ca` |
| `capture.unavailable.json` | `0b525eef2115f62c9c8121706cb6c63e6b27041f3f4b34dedf9992eac5ec515d` |
| `capture.adversarial.json` | `b835cdc0ce13d3ce9ac1f4a07a1385a4992d13a4181e4c355cec6fec76f09541` |
| `report.blocked.json` | `9562badfc4f807ab0a0f0e49831ebdccc3d8e5702eb135e7d286d3e73068470a` |
| `inputs/noc_ack_roundtrip.json` | `4a28f188c343c2280993759d56d3fdeff0956dfb3eb86a306eff4e446b09a662` |
| `inputs/dram_read_return.json` | `8f03972b819ca9624b2d37667a58e401fb84ceda7f5b8ae976ab3802a8cd78c4` |
| `inputs/compute_service.json` | `dfe3e52b101bdcd6e101ba46ad2504ca27e89454a36158f6a19111af72b74d35` |
| `raw/functional_capture.json` | `4bf7a65c868859e2138f165f5806e29d64ec6eee4a48c803435f4349cf2de809` |

Implementation source SHA-256 identities before the progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `c32469b9e56d36760c44905d3d9e31bd45938d56061d8044b3c88d82f2eab8ef` |
| `validation/external.py` | `7d0b160c8fd80f7f237ecf13d2fca5d17611c937af7774989478d89d6592fe35` |
| `topology_compatibility.py` | `2f4c198ac03395798221d14c7293d0bd08d89101a2430944d2c85ae59958ba85` |
| `tests/test_external_validation_contracts.py` | `c3c6f539c4736ba5ff4363c7b8495dff44e0f0fb787741ff7e8b839687950f12` |
| `pyrightconfig.phase2.json` | `7f93f41158de4ff8b6c89ad506c0fcec75e290d1089afc5491e613a1684801d9` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_cli simulator_detailed.tests.test_topology_consumers
67 tests passed in 17.513s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/external_validation.py simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/topology_compatibility.py simulator_detailed/tests/test_external_validation_contracts.py simulator_detailed/tests/test_validation_*.py simulator_detailed/tests/validation_fixtures.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

## Part 3 - Portable capture kit initial checkpoint (superseded)

Historical status at commit `cecaa96`: implementation checkpoint on
2026-09-20; tasks 3.1 and 3.3 are
complete, while tasks 3.2, 3.4 and 3.5 remain open. No ttsim execution or
external functional evidence is claimed, and Part 3 has not been committed as
complete.

Delivered and verified:

- A deterministic `ttsim_tt_metal_v1` capture-kit generator. It admits and
  hashes the campaign first, copies only declared inputs and checked producer
  assets, emits three fixed argument vectors, fixes the four simulator
  environment variables, carries the source/build/binary manifests and checks
  finite invocation/output budgets. Generation from an unrelated working
  directory produced byte-identical kits with identity
  `ttsim-kit:1ef14dab165ed67d903dfe2bbbb6b2ae6a52013c2ec74de0d8d700022e63329a`.
- Strict producer functional-record contracts and conversion to ordinary
  `validation_reference` version 1 plus explicit entity/event/effect mapping
  sidecars. Conversion starts from an admitted capture bundle, rechecks the raw
  SHA-256, exact build/input/condition identities, all repetitions, causal
  events, completion markers, independently recomputed sentinel bytes and the
  declared effect address, size and count before writing atomically.
- Fully explicit fixture inputs for NoC destination address/core/count/pattern,
  DRAM address/bank/core/count/pattern and compute shape, work, BF16 HiFi2
  fidelity, tile layouts, output range/core and sentinel selection. No producer
  or converter default supplies these hardware/workload parameters.
- Bounded TT-Metal device source assets for the three case families and a fixed
  host invocation/build contract. These assets are not yet a completed task
  3.2 producer: the host intentionally refuses evidence until the pinned worker
  links the recipe-specific Metalium dispatch and deterministic record writer.
  Static source presence is not treated as a successful build or run.

Pinned ttsim source snapshot:

- Source URL: `https://github.com/tenstorrent/ttsim`.
- Revision: `40bb1a2ad6a755279c4628ddc65e30b10721fdef`.
- GitHub revision tarball SHA-256:
  `92d33ef15728f17ed5488d28d24dac13550ef58d3b2da16c9fa8a63bd6bb69d0`.

Implementation identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `fffb808d9acabdd6d105ddce0fe4f1cbc064b5e67fddefbd833179975ed59c39` |
| `validation/external_capture.py` | `9ecc4c23320c51c5579e54f8608ad2a9bf0063a786e71140f34b9d37c7f765ca` |
| `tests/test_external_capture_kit.py` | `79904d3b37879e48485f8c7310bc946f8f6f3aa99eba889a4c4e209046512d2b` |
| `capture_assets/ttsim_tt_metal_v1/CMakeLists.txt` | `dc4a65ee1cd4561d78ef8a51b11834286eb2a02fee1a7285fa7c97f78f3b0cdf` |
| `capture_assets/ttsim_tt_metal_v1/host/wormhole_external_validation.cpp` | `49a3982366a9a31500d2ffa73d3ed3aed0daa643b577dd67ea271d3be332afd7` |
| `capture_assets/ttsim_tt_metal_v1/kernels/noc_ack_roundtrip.cpp` | `6b9d98b4a1b3fc91651c68d898ddf77270d20bfc03b49bfb359330deac7ec7e6` |
| `capture_assets/ttsim_tt_metal_v1/kernels/dram_read_return.cpp` | `b05cc66876252b7c6d073dba311fd51d4d4e2ca508819091a7ef56f3f5d92172` |
| `capture_assets/ttsim_tt_metal_v1/kernels/compute_service.cpp` | `500aaf139631606bff6e4415e50e3a707472d4fba06ba215dc15d5f90f66a7e5` |

Revised fixture identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `a1b116f38e009113afbe3b1d8b7b5db5b0ab24e41a4edd78c7d002abcd3e5325` |
| `capture.valid.json` | `0037271f55589d579ce192fdd6a0abe09e8d7b2d0adf3e0fb869a03460c0adc9` |
| `capture.unavailable.json` | `aef2c9868d00c34336d37fbad0c43f14555aaf3164d3166a9ec78ae05b004982` |
| `report.blocked.json` | `9288992be7435e7fce35631556ac4a6c76fdffa69513e3f0964bb12716cdf68f` |
| `inputs/noc_ack_roundtrip.json` | `f5ee0bd5197561801e07435d246977c7e5823314e1052869863efe67ff756a88` |
| `inputs/dram_read_return.json` | `6b63840a699975521080434ddb1153ba8eb1b0a93a25db986fc0c95bc61049df` |
| `inputs/compute_service.json` | `41afb6bd06c3b7dd869dac1104f2cd6d52ce6f2e5b531b1e0af25d3de39d178c` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_capture_kit simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
145 tests passed in 112.284s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/external_validation.py simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/tests/test_external_capture_kit.py simulator_detailed/tests/test_external_validation_intervals.py simulator_detailed/tests/test_validation_adapters.py simulator_detailed/tests/test_mixed_validation.py simulator_detailed/tests/test_validation_references.py simulator_detailed/tests/test_external_validation_contracts.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

Blocked prerequisites observed on this host:

- `/dev/tenstorrent` is absent and `tt-smi` is not installed.
- No pinned TT-Metal checkout or `libttsim_wh.so`/`libttsim.so` was found under
  the workspace or supplied worker roots.
- Task 3.2 requires the worker-side Metalium recipe dispatch/record writer and a
  successful build against the declared TT-Metal revision.
- Task 3.4 then requires that exact build plus the pinned ttsim revision and
  shared-library identity to run all three generated recipes and return raw
  bundles. Task 3.5 requires those actual bundles; synthetic unit-test records
  cannot satisfy it.

Remaining external evidence: none of the fixtures is an admitted ttsim or
Wormhole execution. Parts 3 and 4 retain their explicit worker/device gates.

## Part 2 - Supported model intervals and comparison admission

Status: complete on 2026-09-20. Work started from Part 1 commit
`c4884ca`. No replay result contract, configured hardware value, external
producer or device environment was changed or invoked.

Delivered:

- Additive interval metrics derived from normalized source events for
  acknowledged operation completion, memory service and compute-resource
  service. Each metric retains its exact start/end event identities, subject,
  optional resource, ACI clock, completion scope and raw configured duration.
  Incomplete replay behavior remains compatible and raw replay outputs remain
  byte-for-byte independent of normalization.
- Typed campaign model-interval selectors and repetition policies with explicit
  repetition/warm-up identities and none/mean/median aggregation. Campaign
  admission ties each policy to its finite case budget and supported family.
- Deterministic model repetition execution that runs every declared repetition,
  excludes only named warm-ups, preserves per-run endpoint events, emits the
  matching retained sample policy and reports mean absolute deviation.
- A strict boundary registry for the legacy total-run window and the three
  source-local intervals. Comparison checks completion semantics, exact local
  identities, explicit cross-producer entity and clock maps, equal frequencies
  for cycles, and explicit conversion for seconds. Host dispatch, whole kernels,
  ambiguous intervals, cross-core subtraction and overhead-adjusted boundaries
  remain blocked.
- Profiler imports now retain endpoint identities for supported interval
  boundaries while preserving raw integer timestamps, selected runs, warm-ups
  and sample statistics.

Implementation source SHA-256 identities before the progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/validation.py` | `39c3ca6afeae7d91d408a773473071a4b294b3f9b1be402573d428b36dad7756` |
| `configs/schemas/external_validation.py` | `b2513a9bd1c7283f2eb3f58e1653d7ad46660de1274e282de6eafed558c4a73f` |
| `validation/normalize.py` | `74a9e268a548075fbe4dc46cbaf36a87ed8ec8cff906d907588784238d0a2536` |
| `validation/mixed_normalize.py` | `4581a8fd00045d206743b09fa52149462624a335126751aa7b5064bc89df6e02` |
| `validation/intervals.py` | `ffa77a4621a6a4f8e6ff2d836f9cbbb4370b33001516e5c1811998ad2b2fbc9d` |
| `validation/comparison.py` | `a2ba74a896316166a4335bf7b53ca9d4e15a3c21f30cd609fe2e01ac52536352` |
| `validation/references.py` | `3444c75aa41f004725b92a2dad473e7c197008589ed73dd266eab941fc935a44` |
| `validation/external.py` | `d26b86144dde41c1cb45f8b3de31e327728164f4caf63a5c389c39f01380423d` |
| `tests/test_external_validation_intervals.py` | `027d1c0ae7f2c9c08aee39edd64169e084e6a716623db401f2b47f9a93e8cfce` |
| `tests/test_validation_references.py` | `15005fcf3ce4bc1a6eda1fb0cef408204cc65d12c40185e0ec489d08f04f408a` |

Revised fixture SHA-256 identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `072d7a47373f7f6cc12ffe5584e71b400ff4c08727353bfbcfe759bbb9ab9973` |
| `capture.valid.json` | `fafe6d1cc3728d661bcfce1659579eac6f7adb1f1b3ce516d31da5ad3a55fb6b` |
| `capture.unavailable.json` | `cf889771911f609af039c9f83b9cb57248a7809e17c844d441dde4ae9df93cfd` |
| `report.blocked.json` | `f39ecb785d70f2c48c841e1f0445c3032d0e50be4c2ce249e4cecb6d004ba36f` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
139 tests passed in 111.447s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/validation.py simulator_detailed/configs/schemas/external_validation.py simulator_detailed/validation simulator_detailed/tests/test_external_validation_intervals.py simulator_detailed/tests/test_validation_adapters.py simulator_detailed/tests/test_mixed_validation.py simulator_detailed/tests/test_validation_references.py simulator_detailed/tests/test_external_validation_contracts.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```
