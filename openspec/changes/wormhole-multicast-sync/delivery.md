# Wormhole multicast and synchronization delivery

Delivered on 2026-09-20 on `feiyang-dev`: **35/35 tasks**, **12 requirements and
31 acceptance scenarios**, in seven incremental local implementation commits.
This is the approved shared-runtime repair of the earlier serial prototypes.
The exact scenario matrix is [implementation-audit.md](implementation-audit.md);
per-part results are in [progress.md](progress.md), and exact source/input bytes,
plan identities, environment and CLI commands are in
[delivery-identities.json](delivery-identities.json).

## Implemented behavior

Runtime-configured `multicast_sync_workload` v1 now executes finite rectangle
multicast, ordinary traffic, addressed scalar updates, local threshold waits
and optional FC/matmul pipelines through one environment and shared physical
transport/memory owners. Actual `multicast_sync_result` v1 observations include
segmented branch traffic, service, replies, integer values, generations and live
ownership. Interrupted or idle execution retains state for resume; full drain
precedes exactly-once finalization.

Pure admission validates geometry, aliases, common physical target addresses,
packet/control inventories, resource costs, finite overflow, local visibility,
phase dependencies, slot reuse and cycles before runtime allocation. Dimensions,
clocks, widths, rates and capacities remain configurable. The assumed Wormhole
binding enforces its geometry and aligned 32-bit words; generic words may be
1/2/4/8 bytes. The two-round fixture uses two workers, one slot each and collector
thresholds 2/4 with real local/acknowledged output completion before signaling.

Independent input/event oracles reconstruct packet/control costs, recipients,
shared launches, service, scalar transitions, readiness and ownership. They reject
missing traffic despite plausible totals, repeated updates, early returns and
releases, stale generations, hidden leases/grants and transient over-capacity
credits. Normalized details retain the original events, exact integers and live
snapshots. Calibration allowlists, old report schemas and unused extensions remain
compatible. Public predictor/encoder guards reject new document kinds before
optional model work; 7-D/4-D features and four-coordinate root actions remain intact.

## Incremental commits

| Part | Commit | Delivered boundary |
| --- | --- | --- |
| 1 | `9807f0a` | Strict mixed admission and dependency/control inventories |
| 2 | `ae4d937` | Shared bounded physical tree transport and reservations |
| 3 | `e949d9a` | Canonical addressed memory, ordinary clients and actual acknowledgements |
| 4 | `6202ddc` | Shared indivisible scalar service, real control packets and local waits |
| 5 | `ef6a06b` | Attached finite compute, slot generations and profile/generic fixtures |
| 6 | `31f9526` | Public retained replay, bounded harness, independent audits and docs |
| 7 | Commit containing this delivery | Final credit audit, consolidated validation, identities and handoff |

Part 7 adds an independent check of lane occupancy at every distinct event
timestamp. Equal-time exported records are replayed by token transition because
export order does not preserve scheduler order. The new corruption test adds a
fully returned extra token so a clean final snapshot alone cannot pass it.

The source manifest records Part 6 plus these exact hashed Part 7 bytes. The
containing delivery commit is intentionally not embedded in its own hash.
Find it with `git log -1 --oneline -- openspec/changes/wormhole-multicast-sync/delivery.md`.

## Executed validation

Final `.venv/bin/python -m unittest discover -s simulator_detailed/tests -q`:
**557 discovered, 556 passed, 0 failures/errors, 1 optional Torch/PyG skip**, in
**189.838 seconds**. The initial Part 7 run had 556 discovered/555 passed/one skip
in 189.639 seconds before adding the transient-capacity corruption test. The
restored pre-repair baseline was 510 discovered/509 passed/one skip; the validation
predecessor's historical 458-test delivery is separately recorded in its own
artifacts. No historical count was relabeled as a fresh run.

The following also passed:

```sh
.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/compute_*.py simulator_detailed/replay_compute.py simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/hardware_profile.py simulator_detailed/utils/task.py simulator_detailed/tests/test_compute_*.py simulator_detailed/tests/test_hardware_profile.py
.venv/bin/ruff check simulator_detailed/validation simulator_detailed/validate_wormhole.py simulator_detailed/topology_compatibility.py simulator_detailed/configs/schemas/validation.py simulator_detailed/tests/test_validation*.py simulator_detailed/tests/validation_fixtures.py
openspec validate wormhole-multicast-sync --strict --no-interactive
git diff --check
```

Strict Pyright reports 0 errors, warnings or informations. An additional scoped
Ruff invocation covers all changed Python files since `e6ae2e8` plus the validation
predecessor scope: 45 explicit paths, recorded as `scoped_ruff_command` in the
manifest. After the final audit change, the affected oracle/test scope was linted
again. The transient-capacity addition passed the 8-test mixed validation module
in 14.016 seconds before the final full-suite run.

The named actual root smoke was executed, not mocked:

```sh
.venv/bin/python - <<'PYTHON'
from simulator_detailed.configs.schemas.validation import RegressionGate
from simulator_detailed.validation.gates import run_gate
result = run_gate(RegressionGate(
    gate_id='root_darknet19_smoke', gate='root_darknet19_smoke', required=True,
    requirements=('MS-12',), wall_time_seconds=180))
print(result.model_dump_json())
assert result.outcome == 'pass'
PYTHON
```

It completed **37,888 nodes, 16 cores, 48 links and 11 trace windows**, with JSON
round-trip verification, using the original root 4x4 Darknet19 inputs. Full root
local-remap tests were not rerun: the historical `test_internal_view_layer5`
(`LayerView.active_cores`) and `test_safe_layer5_sequence_drains` (inactive mapping)
failures remain separate limitations. Optional model inference/training is not
validated without Torch/PyG. The initial smoke invocation omitted the schema's
required `requirements` field and failed before execution; the corrected named
gate above is the actual passing run.

## CLI and compatibility observations

All commands used temporary output directories and checked file JSON against
stdout. Their exact module/flag/input/output-placeholder argv are in the manifest.
Every new executable fixture emits `multicast_sync_result` v1 and exits zero:

| Workload | ACI cycles | Evidence |
| --- | --- | --- |
| `mixed_analytical.json` | 79.5 | Segmented acknowledged tree and returning scalar |
| `mixed_two_rounds.json` | 154 | Two workers, shared ordinary traffic, two slot generations |
| `mixed_opposite_fabric.json` | 137 | Translated coordinates and selected fabric |
| `mixed_slow_clock.json` | 500 | Minimum capacities, changed clocks, directed temporary slowdown |
| `wormhole_b0_mixed_assumed.json` | 287 | Actual assumed B0 profile binding |

The three new suites (`multicast_analytical`, `multicast_pipeline`,
`multicast_wormhole`) pass all required checks over 1/3/1 cases respectively,
including retained interruptions at 15/35/60 ACI cycles. Both external evidence
tiers stay `unvalidated`. Actual partial/invalid CLI behavior is covered by
`test_mixed_validation.test_real_cli_profile_partial_and_invalid_output_preservation`:
partial exit 1, invalid exit 2 with preserved output and empty stdout. Existing
hardlink/input protection and atomic publication tests also pass.

Predecessor CLI results retain their public versions and expected timings:

| Contract / input | Version | ACI cycles | Exit/status |
| --- | --- | --- | --- |
| topology `heterogeneous_unicast` | 1 | 44 | 0 / complete |
| torus `torus_small_v2` | 2 | 57 | 0 / complete |
| torus `wormhole_transport_v2` | 2 | 63.5 | 0 / complete |
| memory `generic_ordered` | 1 | 387 | 0 / complete |
| memory `wormhole_ordered` | 1 | 1382 | 0 / complete |
| memory `generic_incomplete` | 1 | 80 | 2 / incomplete |
| compute `generic_matmul` | 1 | 91.75 | 0 / complete |
| compute `streaming_depth1` | 1 | 45 | 0 / complete |
| compute `streaming_depth2` | 1 | 33 | 0 / complete |
| compute `wormhole_bf16_matmul` | 1 | 105 | 0 / complete |

The existing memory CLI intentionally retains its exit 2 for incompleteness;
an initial temporary driver incorrectly expected the new multicast exit 1 and
was corrected. A temporary report extractor also used `runs` instead of the
existing `cases` field; corrected extraction verified all 18 replay/suite
invocations. These were verification-driver errors, not changed public contracts.
Existing exact packet fingerprints and normalized predecessor contracts pass
within the full suite. The six predecessor validation CLI examples are recorded
separately in the identity manifest, including synthetic imports/calibration;
none supplies actual vendor or silicon evidence.

## Bounded umbrella mapping and next boundary

| Parent target | Delivered subset | Boundary retained |
| --- | --- | --- |
| TR-05 | Explicit source inclusion/eligibility; nonwrapping corner rectangle trees; shared-prefix/branch delivery, finite flow control and posted/acknowledged completion | No arbitrary destination masks/trees, multicast reads/atomics or inferred silicon VC/reservation encoding |
| MT-06 | Addressed initialized monotonic increment-by-one counters; shared bounded L1 RMW; real posted/returning traffic/inboxes; serviced local threshold/data-version waits and phase proofs | No reset/decrement/CAS, tensor reduction, general ordering or full hardware semaphore API |
| VA-04 | Independent destination/shared-channel/service/scalar/causal/ownership audits; corruption, dual-fabric contention, backpressure, finite drain, retained resume and slot-generation cases | Model evidence only; arbitrary traffic/protocols and hardware timing accuracy are not established |

Liveness assumes enabled draining sinks, finite service, fair arbitration,
acyclic admitted dependencies and absence of permanent failures. Atomic tree
reservation and aggregate memory service are explicit model policies. Fixed
example rates/clocks/capacities are assumptions, not universal device facts.
Numerical tensors, vendor kernels/ISA execution, general atomics, dynamic kernels
and multi-ASIC execution remain unsupported. No compatible external captures
were supplied; functional-reference and silicon-timing validation remain open.

Old serial projection inputs stay explicitly scoped; they do not acquire these
capabilities. The old `wormhole_b0_multicast_assumed.json` synthetic-ID/profile
fixture remains invalid; use the new working `wormhole_b0_mixed_assumed.json`.

The next separate action is an evidence-based audit of
`wormhole-single-chip-simulation-plan`: reconcile its 38 milestone boxes and 27
parent requirements against all seven child deliveries and overlapping deltas.
The parent checklist/specifications are unchanged. No change was synchronized
or archived, and **no commits were pushed**.
