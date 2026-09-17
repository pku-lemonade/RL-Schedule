# Wormhole compute/dataflow delivery

Delivered 2026-09-17 on `feiyang-dev`. This child implements finite single-ASIC
abstract FC/matmul execution with explicit effective costs, addressed traffic,
shared memory service, generation-safe bundled slots and bounded stage overlap.
It does not implement numerical tensors, kernels/ISA execution or calibrated
silicon timing. All 35 tasks are covered below; the umbrella is not synced or
archived and no push is performed.

## Incremental commits and final changes

| Part | Commit | Delivered boundary |
| --- | --- | --- |
| 1 | `fa02ace` | Strict workload contracts, normalization, configurable costs and pure admission |
| 2 | `80e85b7` | Shared finite memory session, causal gates and pure compute lowering |
| 3 | `a5ba49d` | Bounded reusable A/B/C slots, generations and backing ownership |
| 4 | `bd47dff` | Single-job memory/compute execution, incomplete snapshots and drain |
| 5 | `be46290` | Bounded FIFO overlap and shared-resource contention |
| 6 | `48332b1` | Explicit legacy FC sidecar import, direct-FC diagnostic and consumer guards |
| 7 | Commit containing this report | CLI/examples, scoped capability, exported accounting and final validation |

Each part was validated and committed before proceeding. Detailed development
oracles and decisions remain in [progress.md](progress.md). Final user-facing
usage and contracts are in [compute_dataflow.md](../../../simulator_detailed/docs/compute_dataflow.md).

Part 7 adds `replay_compute.py` with required workload/runtime settings and four
JSON examples. Pure `load_plan()` resolves paths relative to the input and
admits the complete memory/compute graph before allocating runtime resources.
Output is deterministic, stdout is JSON, optional file output matches stdout,
and invalid input preserves prior output. Exit codes are 0 complete / 1
incomplete / 2 invalid. Existing memory CLI codes and fixtures are unchanged.

`ComputeExecutionResult` explicitly reports `abstract_compute_workload_v1`,
execution support and numerical/timing limits. Its work checks now also verify
math/context intervals and matching physical engine ownership events. A small
legal duration after a large clock is checked using the same representable
addition as the runtime, avoiding subtraction-based false rejection. Aggregate
time is reconstructed from actual intervals, including interrupted math.

Manifest `hardware-profile-4` reports the separately scoped executable path.
`ComputePlan` alone remains planning-only; unsupported CLI input cannot allocate
an environment. Profile inspection and legacy full-profile admission remain
closed. Public predictors/embeddings reject incompatible workload/results
before optional ML work. A stale proposal shorthand for root RL actions was
corrected to the inspected four-coordinate contract; no root RL code changed.

## Requirement coverage

All paths below are under `simulator_detailed/tests/`; these are executed tests,
not merely schema declarations.

| Child requirement | Principal implementation / test evidence | Umbrella coverage |
| --- | --- | --- |
| CD-D01 explicit finite admission | `test_compute_contracts`: strict records, disabled/router-only worker rejection, byte/slot bounds, rate/ownership/version checks and dependency/FIFO cycles; `test_compute_memory`, `test_compute_adapters`, `test_compute_cli.test_invalid_input_preserves_output_and_allocates_no_runtime` | CD-01, CD-05 |
| CD-D02 positive configurable costs | `test_compute_contracts`: shared/batched FC equivalence, block geometry, dtype/storage padding, clocks, minimum quantum and nonfinite/unrepresentable rejection; `test_compute_runtime.test_local_fractional_oracle_and_no_legacy_charges`; large-clock CLI accounting regression | CD-01 |
| CD-D03 real local/remote stages | `test_compute_runtime`: local fractional, same-router posted/ack, longer route, full matrix bytes, tiled/service rounding and no duplicated legacy charges; `test_compute_memory` remote/local lowerings | CD-02, CD-04 |
| CD-D04 shared lifecycle/gates | `test_memory_session`: delayed math gate, owner outliving memory, unsupported gate injection, wait-observation protection, shared local/network service, standalone complete/incomplete digest fixtures; `test_compute_memory` | CD-02, CD-04, VA-04 |
| CD-D05 finite generations | `test_compute_buffers`: single physical reservation, stale/foreign tokens, publication/version rules, 12-job bounded reuse, posted slot safety, local consumers, external leases; slot replay in `test_compute_overlap` | CD-03, VA-04 |
| CD-D06 bounded overlap/contention | `test_compute_overlap`: independent 21/14 oracle, integrated 45/33, shared engine 12 versus independent workers 6, two engines sharing L1 8, aliases on both fabrics, context-capacity controls and slow-stage/capacity-one stress | CD-04, VA-04 |
| CD-D07 full drain/resumption | `test_compute_runtime`, `test_compute_overlap`, `test_compute_buffers`, `test_memory_session`: posted effects/credits, retained lease idle state, every capacity conservation, entire-result split-horizon equivalence and exactly-once teardown; CLI snapshot audit | CD-03, CD-04, VA-04 |
| CD-D08 compatibility | Six `test_compute_adapters` tests: all nodes/edges/metadata, immutable DFG snapshots, direct/imported full operational equivalence, direct legacy FC error, real public guards with forbidden ML imports; detailed mesh/DMA/fault regressions and separate root smoke below | CD-05 |
| CD-D09 evidence/fidelity | `test_compute_cli`: shape-derived useful/padded work, storage bytes, actual memory/packet/service/time accounting, complete/partial export, identity relocation, full-profile gate and forged-result rejection; `test_hardware_profile` scoped manifest | CD-01..05 |
| CD-D10 reproducibility/delivery | All four CLI examples twice, outside repository cwd, output roundtrip, exit statuses, invalid-output protection; full suite, strict Pyright/Ruff/OpenSpec/whitespace; this report and source/input identities below | CD-01..05, pipeline VA-04 |

This fulfills the compute child's contribution to pipeline VA-04. It does not
claim completion of all external tiers or all requirements of the larger
validation umbrella.

## Validation performed

Environment: Python **3.12.12**, Pydantic **2.13.5**, SimPy **4.1.2**, project
`.venv`. Final commands:

```sh
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/compute_*.py simulator_detailed/replay_compute.py simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/hardware_profile.py simulator_detailed/utils/task.py simulator_detailed/tests/test_compute_*.py simulator_detailed/tests/test_hardware_profile.py
openspec validate wormhole-compute-dataflow --strict --no-interactive
git diff --check
```

Final suite result: **315 discovered, 314 passed, 1 optional skip**. Strict Pyright: **0 errors, 0 warnings**;
all new production modules, including adapter and CLI, are included. Scoped
Ruff, strict OpenSpec and whitespace checks passed. The single optional
Torch/PyG encoder/model check is unavailable and skipped; no substitute model
run or RL training is claimed. Earlier focused Part 6 regressions: 27
discovered, 26 passed, one optional skip. Part 7 CLI tests cover all four actual
`python -m simulator_detailed.replay_compute --workload <example> [--output ...]`
commands twice and the status/error variants. Existing memory/topology CLI
regressions run in the full suite, retaining old timings and result digests.

The following separate root smoke was run in Parts 6 and 7. It uses actual root
mapping, execution and trace construction, with temporary log/trace paths:

```sh
.venv/bin/python - <<'PY'
import os, tempfile
from pathlib import Path
from configs.schemas.arch_config import ArchConfig
from configs.schemas.failure_configs import FailSlow
from utils.mapper import NetworkMapper, parse_mapping
from utils.definitions import Trace
from simulator.architecture import Arch
from simulator.tracing import process_events
with tempfile.TemporaryDirectory(prefix='compute-legacy-smoke-') as directory:
    os.environ['THERMAL_TIMING_LOG'] = str(Path(directory) / 'timing.jsonl')
    mapper = NetworkMapper(parse_mapping('workloads/darknet19-4-4.json'))
    mapper.gen_dfg()
    arch = Arch(ArchConfig.model_validate_json(Path('configs/instances/gemini4_4.json').read_text()), mapper,
                FailSlow.model_validate_json(Path('configs/instances/normal.json').read_text()))
    arch.execute()
    cores = [c.events for c in arch.cores]
    links = [link.events for link in arch.noc.r2r_links]
    assert all(node.finished for node in mapper.dfg.nodes.values())
    end = max(e.end_time for group in cores + links for e in group)
    trace = process_events(end, 11, cores, links)
    path = Path(directory) / 'trace.json'
    path.write_text(trace.model_dump_json())
    assert Trace.model_validate_json(path.read_text()) == trace
    assert len(trace.time_slices) == 11
    assert all(len(t.cores) == 16 and len(t.links) == 48 for t in trace.time_slices)
    print(len(mapper.dfg.nodes), arch.env.now)
PY
```

Both runs completed **37,888/37,888 nodes**, 16 cores, 48 directed links and 11
trace windows with successful JSON roundtrip. Observed root elapsed times were
19,188,203 and 19,189,606 cycles; the existing stochastic root timing is not a
new deterministic fixture. Optional root detector/embedding/RL execution was
not run. Detailed seven runtime/four hardware features remain unchanged; root
RL remains runtime `(44,64)`, hardware nodes `(64,3)`, hardware edges `(2,96)`,
and actions `[layer, source core, destination core, operation]` with
`replace/split/shift/remove`.

Additional old root remap probes were **not all green**: reference-DFG comparison
and transactional replacement passed, but `test_internal_view_layer5` accesses
removed `LayerView.active_cores`, and `test_safe_layer5_sequence_drains` assumes
core 1 is active in layer 5 when the current mapping rejects it. Root source,
inputs and tests have no diff against the pre-Part-6 commit `be46290`; these
pre-existing stale tests are recorded separately. They were not modified or
counted as successful checks. The required current Darknet19 mapping/execution/
trace smoke above passed.

## Example evidence and independent expectations

All times below are ACI cycles. Generic/Wormhole endpoint times are observed
model outputs, not independent silicon fixtures. Streaming and constant-service
expectations are independently enumerated from configured service/causality.

| Input/job | Reader | Local operands | Math | Local result | Writer boundary / full workload drain |
| --- | --- | --- | --- | --- | --- |
| Generic matmul | 0–45.59375 | 45.59375–50.125 | 50.125–55.125 | 55.125–57.9375 | 90.75 / 91.75 |
| Wormhole BF16 | 0–66 | 66–68 | 68–70 | 70–71 | 104 / 105 |
| Depth 1, job 0/1/2 | 0–9 / 15–24 / 30–39 | 9–11 / 24–26 / 39–41 | 11–14 / 26–29 / 41–44 | 14–15 / 29–30 / 44–45 | 15/30/45; drain 45 |
| Depth 2, job 0/1/2 | 0–9 / 9–18 / 18–27 | 9–11 / 18–20 / 27–29 | 11–14 / 20–23 / 29–32 | 14–15 / 23–24 / 32–33 | 15/24/33; drain 33 |

Generic A/B/C storage is 60/70/84 bytes, useful work 420, padded work 1024,
math 5 and context 12.34375 cycles. It injects 448 packet bytes, launches 1984
channel bytes and completes 642 service bytes. These byte/cost expectations are
independently derived in Part 4 and reconstructed from exported events again.

Wormhole A/B/C storage is 32/32/32 bytes: useful work `2*1*4*4*4=128`, padded
work `2*1*4*8*4=256`; effective math `(2+256/128)*500MHz/1GHz=2` ACI cycles.
Two reads use request/response bytes 32/64, and the write/ack uses 64/32: 288
packet bytes. Configured routes give channel bytes
`2*(32*22 + 64*4) + 64*3 + 32*11 = 2464`. Remote/local producer and consumer
accesses total 288 service bytes; context time is 2 operand + 2 math + 1 result
cycles. These are abstract model calculations, not a hardware prediction.

Streaming reads one remote byte and one local byte per job; R=9, operands=2,
math=3, result=1, local writer=0 yields serial 45 or depth-two 33. Both cases
complete useful/padded work 6/6, math/context 9/18 cycles, packet/channel/service
bytes 288/576/15. At horizon 12, depth two has one executed math cycle, zero
completed work/jobs, charged contexts/slots and pending operations; it exits 1.
Snapshots at 3/12/20/32 followed by finalization reproduce the uninterrupted
full result exactly.

## Ownership, waits and drain audit

One environment owns canonical memory resources, bounded packet/response paths,
stage pools and slots. Physical L1 reservations are made once; fabrics and
software streams do not create new capacity or engines. Slot events replay
free+occupied conservation and generation ownership. Local/network accesses
share aggregate service; completed chunks, actual flit launches, descriptors,
leases, credits and contexts reconcile in both full and interrupted snapshots.

Prerequisite waits own no stage context. A stream feeder holds only the next
job's metadata while waiting for a whole slot. After reservation, reader context
admission precedes gate activation. Compute admission follows input publication
and output reservation; its context covers operand/math/result service. Writer
context releases at its completion boundary before local-consumer retention.
Slots stay charged through required consumers/leases; posted remote effects
remain independently charged after safe local slot reuse.

The combined dependency/FIFO/reuse DAG rejects cycles. For admitted finite work,
finite FIFO stage grants and service release ownership, with no stage grant
held across a prerequisite wait. Capacity-one response/credit tests exercise
the critical network paths. This is a model-specific progress argument and
stress-tested invariant, not a universal kernel deadlock proof. An unreleased
external lease remains incomplete. Before finalization a drained workload says
`awaiting_finalization`; nested memory still says `idle_with_pending` with only
`["teardown"]` pending. Finalization restores reservations once and retains both
pre/post snapshots. Standalone memory wrapper semantics and digest fixtures
remain unchanged.

## Provenance, limits and next child

Public facts are anchored to ISA documentation revision
`acaf010519f4fdd323df5077e45b8695f70e4279` and TT-Metal example revision
`975015c2f03bb818eaee2422c3845fba381eaf8c`. The seven compute-related source
URLs, full content hashes and specific claims are in [design.md section 9](design.md#9-record-primary-source-anchors-without-turning-peaks-into-fitted-rates).
Profile and memory inputs retain their own pinned coordinate/routing/packet
source metadata. Public facts establish architectural structure and programming
boundaries; they do not fit effective rates or establish numerical/timing
accuracy. All runtime costs/capacities and the illustrative harvest mask remain
explicit assumptions in the examples. No vendor source copies or generated
trace/result artifacts are committed.

The supported subset remains finite, bias-free, already normalized compatible
FC/matmul. `fifo_item_slots_v1` conservatively retains whole A/B/C bundles; it is
not a full optimized circular-buffer model. Local-only versions and supported
producer dependencies execute; unsupported cross-worker notification semantics
are rejected. Detailed unpack/pack/ISA/scalar engines, numerical matrices,
automatic tilize/conversion/operator lowering, split-K/reductions, exact NIU,
bank/channel/cache fidelity, multicast/atomics/semaphores, dynamic kernels,
interchip/host/PCIe, new predictor/RL models and calibrated timing remain outside
this implementation.

The next child, `wormhole-validation-harness`, should consume these explicit
input/code identities and raw events; automate mathematical/conservation and
reference comparisons; add measured/ttsim tiers only when actually available;
and report unsupported/skipped tiers separately. No ttsim execution or silicon
measurement was performed in this child. Umbrella sync/archive and that next
child's exploration remain subsequent work.

## Reproducible identities

The code bundle is the SHA-256 of compact sorted-key JSON containing the sorted
list `[{"path": <repository-relative path>, "sha256": <raw file hash>}, ...]`
for all `simulator_detailed/**/*.py` (107 production/test Python files). Its
value for this delivery is
`bc03020b1f97d569f407b7c26826d680efa24c1404ec33b970e822bbbd4c79ad`.
The enclosing Git commit also records docs, inputs, strict-check configuration
and this report. It is intentionally not embedded as a self-referential hash.

Raw source-file identities (distinct from normalized graph/profile identities):

| Source file | SHA-256 |
| --- | --- |
| `configs/topologies/memory_small.json` | `98126ffea777b9dc764ed4179344a896d2d4a8c8b6d3b095dde0c417125dbba6` |
| `configs/profiles/wormhole_b0_n150_assumed.json` | `a959592f39b257091c509d95abde6842e10e08d84123fde6788ed55372429b9d` |

The following identities were obtained by pure admission. Relocating a source
path preserves effective identities; changing effective rates or runtime
controls changes the appropriate plan identities. DFG imports additionally
carry immutable DFG and sidecar digests inside configuration provenance.

### generic_matmul.json

| Identity | SHA-256 |
| --- | --- |
| Raw input | `ebe5cf6c616d6778ef507883c1f34551312dd92af9ab450af90564e11e0a812a` |
| Canonical graph | `1cf56e48bf11cabb6878df76faa8ea900d41bb2803999a589d27d91f3cf81c8e` |
| Configuration | `cc0a320b017e674ede4fe2cbf364a637144d6877dd0cf594ec49d8f8d67cb162` |
| Effective costs/configuration | `73a3fe8a20fe6f6ca44127d195d2cd5558fcc279dd23acc348e79cb0973f3518` |
| Compute plan | `60a95a9f17b6b7cf410f31af6c81b0ae3d2dba0a371bf2af78368c81aa055090` |
| Executable lowering | `d96a4de08770af883d8356da7a41beb48bd67b3d11361655cef1749c0f4fee63` |

### streaming_depth1.json

| Identity | SHA-256 |
| --- | --- |
| Raw input | `8b12a32d469b531f545e8602c77b42f58c1edd26657f362f8ca7ce35bcbd913d` |
| Canonical graph | `1cf56e48bf11cabb6878df76faa8ea900d41bb2803999a589d27d91f3cf81c8e` |
| Configuration | `267a5dc082806c21c7d955f76842994e6d0ab4dcd33685c86b6e5c3ddf93ff29` |
| Effective costs/configuration | `4d4b4982c53594aa0974e0629087089a3304ab9ccc078e9fbd72c623f0b45a6e` |
| Compute plan | `053600dfb071074a4a2936d0d2185ac4e0302ebe62351948c053a2ba80eca362` |
| Executable lowering | `d3a8c90ad25101a055de066113d65e078c061a83f03a2ed78c6becd95e47941e` |

### streaming_depth2.json

| Identity | SHA-256 |
| --- | --- |
| Raw input | `03b09015a4db24f40e29985d84dcfe8e24d4d2a1dfeac48363cf1f6adf6e58d9` |
| Canonical graph | `1cf56e48bf11cabb6878df76faa8ea900d41bb2803999a589d27d91f3cf81c8e` |
| Configuration | `4414e0a6c9bed477be5d6cf551348e4c2e8a5d42ea74b4c28b21494c30567527` |
| Effective costs/configuration | `e0731bc5ae79c24e3d16aee824b33d6737af8c2c64a389cb9740f12ffcd543f6` |
| Compute plan | `7a9ef5bea7f31b9daf616ce1b78844447aaa9155fb9c2147de5eb5ab6451c5a1` |
| Executable lowering | `c43c2b60ef28f4d8225d52afc127393d450bbfe7b0948eb135da8c85903ca84a` |

### wormhole_bf16_matmul.json

| Identity | SHA-256 |
| --- | --- |
| Raw input | `fce1f057bd9da45cd0242c3f81bb2bfc86b856cb452040a54ef3a580823b8cf6` |
| Canonical graph | `1522580f920df21f2699c89c9076d9e01d28011d514b18f3feef83b657199f1e` |
| Configuration | `40802c2c61c350c6f6ab18b2f532ea182f83c5e9713344fa172d1720a9b599eb` |
| Effective costs/configuration | `e20e94e84749979e25c489d031b93fa06c2f21b3f7ae19cb0ba9edb69f77735d` |
| Compute plan | `b04f926e7b85a78f318a969b2d2372737be93721bc5dd158ee3d2a1af99169c1` |
| Executable lowering | `2f8266a38928563984cbff63953d00fa060b194e74da2c46604c6e3770b5a73d` |
