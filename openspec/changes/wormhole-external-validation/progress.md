# Wormhole external validation progress

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
complete, while tasks 4.3, 4.4 and 4.5 remain open. No Wormhole device was
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

Implementation source SHA-256 identities before this progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `893f5eb05cae3f0a2c19792a59b1fe178a11b9b9f55227e3208b4e9977a3e1fe` |
| `validation/external.py` | `af5b679027d9b7089fa81459ef0735fc8f8e67c5471243b3bcaa1a35f2ca42b4` |
| `validation/external_collector.py` | `52f549ae47aa14f94a97659331bba4d69edc54a7592d2b71d6690149e9861485` |
| `validation/references.py` | `8c320e583270c9d71c6141fcac16a993c4345b28199107771e8102b1fdb734a0` |
| `tests/test_external_wormhole_collector.py` | `8d6318aedcd3523e5835ac42e5d4f3647d5a39f6987dc0d60d69a521483c2b40` |
| `capture_assets/ttsim_tt_metal_v1/kernels/noc_ack_roundtrip.cpp` | `fb1803dbb60f33d43c6e98af1c481feec95cf779f728c2921e06a9ec6ef6a77f` |
| `capture_assets/ttsim_tt_metal_v1/kernels/dram_read_return.cpp` | `84809bb95c6c6b61fcb4fbbf258ca807ced5bb4386c1d53008862dde37863cd7` |
| `capture_assets/ttsim_tt_metal_v1/kernels/compute_service.cpp` | `4fa6490d3df50d5d04b6536625a00790b3b7c1a9822285d72f8b41e78267f432` |

Executed verification:

```text
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
- No pinned TT-Metal checkout, matching built host/device artifacts, named
  Wormhole board, firmware inventory or device-profiler output was supplied.
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

## Part 3 - Portable capture kit and functional conversion

Status: implementation checkpoint on 2026-09-20; tasks 3.1 and 3.3 are
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
