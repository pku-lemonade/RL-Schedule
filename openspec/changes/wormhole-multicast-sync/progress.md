# Implementation progress

> Correction on 2026-09-18: the Part 1–5 entries below are historical records
> of committed prototype work, not proof that their full task scope was met.
> The [implementation audit](implementation-audit.md) supersedes those completion
> claims. Shared transport/memory/compute execution and retained resume remain
> incomplete. The initial correction retained 2/35 tasks; the subsequent tree
> repair brought the checklist to 3/35. The mixed-admission repair below
> completes Part 1. Shared tree transport now completes Part 2: 10/35
> verified at that checkpoint. Canonical memory now completes Part 3:
> 15/35 verified; scalar/compute integration remains open.

## Attached mixed compute and representative fixtures — 2026-09-20

Tasks 5.1–5.5 are complete (25/35). `MixedComputeComponent` attaches the existing
`BoundedPipeline` and `StagePool` scheduler to the mixed session. It allocates no
environment, NoC, byte reservation or memory server. One declared pool per
physical worker/stage serves all streams; duplicate component attachment and
forged/remote lifecycle capabilities are rejected. Standalone compute wrappers
and effective cost policies are unchanged.

Pure lowering binds imported local A/B versions to FIFO slot generations,
charges actual local operand reads and C publication, and lowers acknowledged
outputs to ordinary shared network writes. No duplicate reader transfer is
created for a multicast-populated operand. Slot reuse, stream stage order,
consumer lifetimes, local wait gates and memory conflicts share the pre-allocation
DAG. Local or acknowledged output completion enables scalar signals. The scoped
mixed component rejects posted compute outputs as signal evidence; standalone
posted compute remains supported. Every mixed snapshot includes live slots,
contexts, stage/resource events and pending jobs; finalization includes compute.

The published `mixed_two_rounds.json` has two workers, one slot each, overlapping
math, ordinary competing traffic, one acknowledged result stream and a collector
counter owned by the distributor. Thresholds 2 and 4 enforce the two phases.
`mixed_opposite_fabric.json`, `mixed_slow_clock.json` and
`wormhole_b0_mixed_assumed.json` exercise reversed fabric coordinates, minimum
capacities, changed clocks, a directed slowdown and actual profile binding.
All four complete with 416 source useful bytes and 352 destination useful bytes;
their cycle/channel-byte pairs are 154/2464, 137/2080, 500/2464 and 287/6912.
The slowdown fixture includes its configured end notification at cycle 500.
All timing/rates remain model assumptions, with no measured-silicon promotion.

Checks:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_mixed_compute_runtime simulator_detailed.tests.test_compute_runtime simulator_detailed.tests.test_compute_overlap simulator_detailed.tests.test_compute_buffers simulator_detailed.tests.test_multicast_memory_runtime simulator_detailed.tests.test_scalar_shared_runtime -q` — 55 passed in 17.288 s (initial three mixed tests).
* Expanded `.venv/bin/python -m unittest simulator_detailed.tests.test_mixed_compute_runtime -q` — 5 passed in 1.759 s, adding shared-worker capacity and all four published fixtures with multiple interruptions.
* Published `replay_compute --workload` examples: generic_matmul 91.75,
  streaming_depth1 45, streaming_depth2 33, wormhole_bf16_matmul 105 ACI cycles;
  all exit 0 and complete.
* Strict Pyright: 0 errors/warnings/informations. Scoped Ruff, strict OpenSpec
  and whitespace checks passed. Initial fixture corner/fabric bindings and
  annotation errors were corrected before this checkpoint.

The old serial prototype CLI/adapter remains explicitly scoped until Part 6.
No push, spec synchronization or archive was performed.

## Shared scalar service and local waits — 2026-09-20

Tasks 4.1–4.5 are complete (20/35). Addressed monotonic counters register in the
canonical aggregate memory server. Each atomic update is one indivisible FIFO
job with configured final native cost, old/new values and admission/linearization
sequences. Both fabrics and local clients share that server; independent L1s
serve concurrently. No duplicate base read/write jobs are charged.

Posted and returning operations use actual one-flit request/response routes,
finite issue/responder descriptors and serviced local return inboxes. Replies
can reorder while retaining the value from each update's linearization. Posted
completion is source handoff; counter effects can remain pending. Local waits
subscribe before charging observations on the same FIFO, recheck changes during
service, and release only after threshold and full local data versions are ready.
Blocked waits retain no execution resource. Pure admission proves finite phases,
locality, overflow, granule/inbox disjointness and representable observation costs.

Checks:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_scalar_shared_runtime simulator_detailed.tests.test_multicast_memory_runtime simulator_detailed.tests.test_memory_resources simulator_detailed.tests.test_memory_runtime simulator_detailed.tests.test_compute_runtime simulator_detailed.tests.test_compute_overlap -q` — 72 passed in 20.169 s.
* After adding cross-fabric contention and pre-allocation overflow checks,
  `.venv/bin/python -m unittest simulator_detailed.tests.test_scalar_shared_runtime simulator_detailed.tests.test_packet_runtime simulator_detailed.tests.test_multicast_inventory -q` — 29 passed in 5.064 s.
* Strict Pyright: 0 errors/warnings/informations. Scoped Ruff: passed after
  import formatting. Strict OpenSpec and whitespace checks: passed.

Coverage includes generic widths 1/2/4/8, exact large integers, response
reordering, distinct-L1 overlap, FIFO ties, observation-registration races,
late payload readiness, increasing thresholds and retained resume. Existing
packet digest fixtures and standalone memory/compute regressions pass. Compute
composition and public CLI/adapter replacement remain for Parts 5–6. No push.

## Canonical addressed multicast memory — 2026-09-20

Tasks 3.1–3.5 now execute in `MulticastMemoryRuntime` through one environment,
one physical transport registry and the existing canonical `MemoryResources` /
`MemoryService` owners. `try_acquire_bundle` preflights every source/recipient
lease without mutation; issue and responder capacities are checked/acquired in
the same non-yielding admission step before any tree reservation. Failed bundle
admission neither invalidates ready data nor retains provisional leases.

Streaming source hooks service each useful source byte once; recipient hooks
service each useful byte at every actual destination. Header/padding flits do
not cause payload service. Segment leases publish physical-owner-qualified
producer versions through the canonical registry; full-extent queries require
all segments. Source-read release, injection handoff, per-recipient service,
returned acknowledgements, source completion and diagnostic all-effects remain
separate. Each acknowledgement uses its admitted real response route after
recipient service and request-credit release. Configured header counts apply to
acknowledgements as well as data packets. Reply descriptors are preprovisioned
and released after response handoff; finalization waits for all returned credits.

Ordinary read/posted/acknowledged and local memory operations use the same
servers and physical objects. Both fabrics alias the same L1 owners. Snapshots
retain live service, leases, descriptors and grants; resume is identical to an
uninterrupted run. Exactly-once finalization releases full buffer reservations.
Scalar-containing execution is explicitly rejected until Part 4 is attached.

Checks:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_memory_runtime simulator_detailed.tests.test_multicast_inventory simulator_detailed.tests.test_tree_runtime simulator_detailed.tests.test_memory_resources simulator_detailed.tests.test_memory_runtime simulator_detailed.tests.test_memory_session simulator_detailed.tests.test_packet_runtime simulator_detailed.tests.test_phase3_dma -q` — 82 passed in 13.991 s.
* A subsequently added canonical alias test, `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_memory_runtime.MulticastMemoryRuntimeTests.test_aliases_on_both_fabrics_share_canonical_l1_service -q` — 1 passed in 0.250 s.
* Boundary sizes 1/31/32/33/63/64/65/96, source inclusion, slow target tails,
  source reuse, failed bundle immutability, response backpressure, configured
  three-flit headers, delayed credits, mixed unicast readback/local clients,
  full-extent readiness and live resume all pass independent byte/lifetime checks.
* Strict Pyright — 0 errors, warnings or informations. Scoped Ruff over new
  runtime/tests, `memory_resources.py`, `multicast_network.py` and
  `multicast_inventory.py` — passed. Strict OpenSpec and whitespace — passed.

No legacy result schema or standalone timing contract changed. The old prototype
CLI remains explicitly labeled until Part 6; no commits were pushed.

## Shared tree transport repair — 2026-09-20

Tasks 2.1–2.5 now execute through `MulticastNetworkPlan`, the single
`PhysicalTransportRegistry`, and `TreeTransport`. Distinct tree packet/branch
identities and lanes use the existing `VirtualChannelLink` serializer and
`RouterPipeline`; unicast attaches to those same objects. Duplicate component
attachment is rejected. The legacy public envelope and result serializers stay
unchanged; mixed snapshots explicitly include tree identities and all resources.

FIFO controller capacity bounds waiting and active grants. Each grant claims
all tree channels without yielding, charges configured setup/edge service, and
releases exactly once after terminal/ejection effects and delayed credits drain.
Forwarders retain incoming credits and charged replication slots, reserve each
child/ejection before finite router service, and retain bounded pending-output
metadata. Transit terminals consume finite router service without payload writes.
A blocked branch owns no router grant. Request/response lanes remain independent
of multicast reservation ownership while sharing physical bandwidth. Disjoint
trees can hold simultaneous grants. Fair finite physical arbitration, the pure
acyclic tree, preprovisioned responders/accesses (Part 3), and the existing
unicast dateline ranks are the stated dependency assumptions; no hardware VC or
reservation-control packet timing is inferred.

Checks:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_tree_runtime simulator_detailed.tests.test_packet_runtime simulator_detailed.tests.test_torus_transport simulator_detailed.tests.test_memory_runtime simulator_detailed.tests.test_memory_session simulator_detailed.tests.test_topology_transport simulator_detailed.tests.test_phase3_dma -q` — 75 passed in 12.744 s.
* New tests independently replay credit tokens, physical launch intervals,
  per-channel/per-flit uniqueness, source/recipient bytes and grant lifetimes;
  cover both fabrics, terminal opt-outs, minimal capacities, disjoint trees,
  actual shared unicast/response traffic, directed slowdown and mixed clocks.
  Interrupted execution retains live grants/credits and resumes to exactly the
  uninterrupted snapshot. Prior exact packet-runtime digest fixtures pass.
* Strict Pyright — 0 errors, warnings or informations. Scoped Ruff over
  `tree_runtime.py`, `tree_wire.py`, `multicast_network.py`, `virtual_channel.py`,
  `packet_runtime.py`, `packet_transport.py`, and `tests/test_tree_runtime.py` — passed.
* Strict OpenSpec and `git diff --check` — passed.

An opposite-fabric fixture initially mismatched its graph/routing policy and
was corrected. A broader command initially named nonexistent `test_dma_endpoint`;
the recorded passing command uses `test_phase3_dma`. The old serial prototypes
remain pending replacement in the CLI/adapter; their output is not promoted by
this transport checkpoint. Part 3 is next; no commits were pushed.

## Mixed execution admission repair — 2026-09-20

Tasks 1.2 and 1.4 are now verified. The strict opt-in runtime contract declares
shared physical link/router settings, responder capacity, scalar control
geometry, return inboxes, ordinary addressed operations, local data extents and
wait gates. Pure compilation inventories every segment's injection/tree/ejection
channels, recipient acknowledgement route, scalar request/return route and
responder reservation. It reuses the existing route and physical-setting
validators without allocating an environment or modifying output files.

The initial mixed dependency graph checks physical-owner completion visibility,
local full-extent producer versions, source/target and inter-operation conflicts,
dedicated atomic granules/inboxes, cycles, finite threshold reachability and
later-phase overtake. Tree reservation assumptions are explicitly recorded,
separately from hardware facts. These admission checks do not establish the
shared runtime or complete Parts 2–7.

Validation:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_inventory simulator_detailed.tests.test_multicast_sync simulator_detailed.tests.test_multicast_tree_admission simulator_detailed.tests.test_multicast_admission simulator_detailed.tests.test_memory_packets simulator_detailed.tests.test_packet_runtime simulator_detailed.tests.test_torus simulator_detailed.tests.test_hardware_profile -q` — 81 passed in 8.990 s, including the existing exact serialized packet-runtime compatibility fingerprints.
* `.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json` — 0 errors, warnings or informations.
* `.venv/bin/ruff check simulator_detailed/multicast*.py simulator_detailed/configs/schemas/multicast_sync.py simulator_detailed/memory_routes.py simulator_detailed/memory_transport.py simulator_detailed/tests/test_multicast*.py` — passed.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` and `git diff --check` — passed.

An initial route-helper extraction failed import/type checks and was corrected
before this checkpoint. No legacy workload/result schemas or hardware constants
were changed. Runtime settings remain explicit and generic clocks/dimensions
remain configurable. No commits were pushed.

## Tree and canonical admission repair — 2026-09-18

Task 1.3 is now verified. Recipient resolution uses the selected fabric's
coordinates, including opposite-fabric mappings. Independently enumerated
X/Y-major cases cover shared approaches, spines, branches, source inclusion,
local/row/column trees and transit-only terminal leaves. Parent/child/ejection
inventory is explicit. Disabled or unresolved workers, missing endpoints,
duplicate aliases, disabled transit, invalid source entry and wrapping bounds
are rejected. Common target addresses are physical L1 offsets; bindings may
use different buffer bases. Source/target overlap is owner-qualified.

Additional Part 1 admission repairs reuse canonical memory binding and
reservation/service validation, normalize source content without locator paths,
use integer packet arithmetic, validate finite converted scalar/controller
costs, and reject modified plan contents before executor admission. Pure profile
binding now recognizes worker NIU endpoints and checks profile packet geometry
and aligned 32-bit Wormhole counters. Changed generic dimensions, clocks,
widths and capacities remain configurable. These profile tests allocate no
runtime and do not establish a working Wormhole CLI fixture.

Tasks 1.2 and 1.4 remain open: complete mixed hardware/control settings,
response-path/responder inventories and the mixed access/version/dependency
compiler still need implementation. No later part is promoted by these tests.

Verification:

* `.venv/bin/python -m unittest discover -s simulator_detailed/tests` —
  510 discovered, 509 passed, one optional skip in 164.774 seconds.
* Earlier focused `.venv/bin/python -m unittest discover -s simulator_detailed/tests -p 'test_multicast*.py'`
  — 45 passed; the full run above also includes seven subsequent admission tests.
* `.venv/bin/python -m unittest simulator_detailed.tests.test_hardware_profile simulator_detailed.tests.test_torus simulator_detailed.tests.test_memory_packets`
  — 40 passed.
* `.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`
  — 0 errors, 0 warnings, 0 informations.
* The full scoped Ruff command recorded in the preceding verification repair,
  including new multicast files and predecessor validation/compute scopes — passed.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` and
  `git diff --check` — passed.

An initial profile admission test exposed that the existing profile binder
retains worker NIU attachments as `network`, rather than `compute`. Eligibility
now checks the physical worker and profile origin while retaining the existing
graph format. The corrected test and final full suite pass. Earlier lint import
ordering findings were fixed. Existing root smoke evidence is the separate run
in the verification-repair entry; it was not rerun for this admission-only change.
No commits were pushed.

## Verification repair — 2026-09-18

The corrective checkpoint adds a guarded prototype CLI/adapter, lossless event
normalization and independent input/event checks. Missing shared service,
physical ownership, synchronization and compute evidence remains unsupported;
the existing pipeline's early atomic submission fails the causality check.
It also fixes the no-progress resume loop, scalar dependency/horizon handling,
native-clock conversion and owner-qualified address comparisons. This is not
completion of Part 6 or delivery of the shared runtime. See the implementation
audit for the 32 acceptance scenarios and remaining implementation work.

Checks run against the repaired source:

* `.venv/bin/python -m unittest discover -s simulator_detailed/tests` —
  494 discovered, 493 passed and one optional skip, 164.429 seconds.
* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_validation simulator_detailed.tests.test_multicast_scalar simulator_detailed.tests.test_multicast_pipeline`
  — 22 passed after the final CLI status/diagnostic changes.
* `.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`
  — 0 errors, 0 warnings, 0 informations.
* `.venv/bin/ruff check simulator_detailed/multicast_*.py simulator_detailed/replay_multicast_sync.py simulator_detailed/configs/schemas/multicast_sync.py simulator_detailed/configs/schemas/validation.py simulator_detailed/topology_compatibility.py simulator_detailed/validation simulator_detailed/tests/test_multicast_*.py simulator_detailed/tests/test_validation_*.py simulator_detailed/tests/validation_fixtures.py simulator_detailed/compute_*.py simulator_detailed/replay_compute.py simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/hardware_profile.py simulator_detailed/utils/task.py simulator_detailed/tests/test_compute_*.py simulator_detailed/tests/test_hardware_profile.py`
  — passed. An initial command named a nonexistent standalone pipeline schema;
  correcting the scope to the existing modules passed.
* Actual `run_gate(RegressionGate(gate_id="root_darknet19_smoke", gate="root_darknet19_smoke", required=True, wall_time_seconds=60.0, requirements=("MS-12",)))`
  — passed: 37,888 completed nodes, 16 cores, 48 links, 11 windows and a JSON
  round trip. This executes the root simulator with temporary outputs.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` and
  `git diff --check` — passed.

No optional ML execution, functional capture or silicon timing validation was
performed. No umbrella task was promoted, and no commit was pushed.

## Part 1 (tasks 1.1–1.5)

Baseline was reproduced on branch `feiyang-dev` from commit `23f5222` using the
existing `.venv`. The observed environment was Python 3.12.12, Node v22.22.2,
OpenSpec 1.3.1, Pydantic 2.13.5, SimPy 4.1.2, NumPy 2.5.3, SciPy 1.18.1,
Pyright 1.1.414 and Ruff 0.16.7. The source-host handoff records a different
OpenSpec version; this progress file records the executable version in this
workspace.

The detailed baseline command
`.venv/bin/python -m unittest discover -s simulator_detailed/tests` ran
458 tests: 457 passed and one optional skip in 153.421 seconds. Strict Pyright passed with
`--pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`.
The predecessor scoped Ruff command passed over the compute, replay, hardware,
task and predecessor tests. `openspec validate wormhole-validation-harness
--strict --no-interactive` passed. The published topology, memory and compute
examples exited 0 and emitted complete, drained records. The handoff's stale
memory replay path was corrected to the existing
`simulator_detailed/configs/memory_replays/wormhole_ordered.json`; no generated
outputs were retained. Historical root limitations and the optional ML skip
remain separate from this child.

Part 1 adds strict versioned workload/result records, a pure rectangle-tree
compiler, deterministic segment and recipient inventories, scalar admission
checks, source/effective plan digests and an explicit admission-only boundary.
It uses the canonical graph and `MemorySystemConfig` hardware parameters, so
fabric dimensions, packet widths, clocks and service values remain configurable.

Part 1 verification:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_sync -v` — 5 passed.
* `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json` — 0 errors, 0 warnings, 0 informations.
* Scoped Ruff over the new schemas, compiler, planner and tests — all checks passed.
* `git diff --check` — passed.
* `openspec validate wormhole-multicast-sync --strict --no-interactive` — passed before implementation and will be rerun at each part boundary.

All runtime transport allocation and legacy multicast fields remain untouched;
later parts add capabilities behind these new versioned contracts.

## Part 2 (tasks 2.1–2.5)

Part 2 adds the opt-in composite lane registry, tree/flit identities, FIFO
all-or-none reservations, deterministic cut-through branch accounting and
resumable transport snapshots. Reservation release is recorded only after the
tree drain event; an interrupted result retains the reservation owner in its
snapshot. The registry keeps request, response and multicast lane classes on
one physical serializer identity and does not alter legacy envelopes.

Verification:

* `.venv/bin/python -m unittest simulator_detailed.tests.test_multicast_transport -v` — 4 passed.
* Affected topology/torus/packet and multicast tests — passed.
* Strict Pyright and scoped Ruff — passed.

## Part 5 (tasks 5.1–5.5)

Part 5 adds a typed finite pipeline composition over one multicast plan and
session. Stages bind concrete input/output buffers, slot generations and local
waits; rounds enforce increasing thresholds and a complete operation/stage/wait
dependency DAG. The generic two-round fixture reuses a collector slot only
after each local wait and produces a final counter value of two. JSON fixtures
are stored under `simulator_detailed/configs/multicast_workloads/`, with a
separate canonical topology fixture and an explicitly assumed Wormhole profile
fixture.

Verification:

* Pipeline tests — 2 passed; multicast memory/scalar/transport tests — passed.
* Existing compute and memory runtime regressions — passed.
* Strict Pyright and scoped Ruff — passed.
* `git diff --check` and strict OpenSpec validation — passed.

## Part 3 (tasks 3.1–3.5)

Part 3 attaches tree recipients to the existing addressed-memory buffers and
resource/service records through a side-effect-free executor. It performs one
source read, one bounded service charge for each admitted recipient and marks a
destination ready only after the complete segmented extent is written. Posted
and acknowledged completion are distinct: acknowledged operations emit one
bounded header-only return packet per recipient and segment after destination
service, and require the existing response-sink role. Interrupted execution
does not publish readiness or versions; resuming a completed result is
idempotent.

Verification:

* Multicast contract, transport and memory tests — 14 passed.
* Affected memory packet/session/service, topology, torus and DMA regressions — passed.
* Strict Pyright, scoped Ruff, `git diff --check` and strict OpenSpec validation — passed.

## Part 4 (tasks 4.1–4.5)

Part 4 adds the addressed scalar projection. Counters are initialized as exact
integers, naturally aligned and non-overlapping; only monotonic increment by
one is admitted, with width-specific overflow checks. The executor records one
indivisible linearization/effect, one physical request flit, and an optional
return flit carrying the previous value. Local waits charge observations and
require both threshold and declared data prerequisites without retaining a
transport reservation.

Verification:

* Scalar counter/wait tests — 4 passed.
* Multicast contract, transport and memory tests — passed.
* Strict Pyright and scoped Ruff — passed.
