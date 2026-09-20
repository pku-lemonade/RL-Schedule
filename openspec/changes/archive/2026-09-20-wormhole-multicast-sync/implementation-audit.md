# Verified implementation audit — 2026-09-20

The approved shared-runtime design and all **35/35 tasks** are complete. Part 7
consolidates the passed checks and delivery evidence below. This audit supersedes the
2026-09-18 prototype findings; the original audit remains in Git history at
`37ca737`. The specification contains **12 requirements and 31 scenarios**;
an earlier progress entry's count of 32 was a counting error, not a removed
scenario or a change to the approved specification.

The actual path is `MulticastMemoryRuntime` with `PhysicalTransportRegistry`,
`TreeTransport`, canonical `MemoryResources`/`MemoryService` and attached
`MixedComputeComponent`. The older serial projection classes remain available
only for their old input contracts and are explicitly excluded from this shared
runtime evidence. Public CLI/harness selection depends on explicit runtime
settings, not the presence of prototype schema fields.

## Acceptance matrix

All rows refer to executed model checks, not external functional or silicon
validation. Test shorthand `name` means
`simulator_detailed/tests/test_name.py`. The final row's exact records are in
this directory. Every scenario title is copied from the unchanged specification.

| Requirement | Acceptance scenario | Primary evidence | Verified behavior |
| --- | --- | --- | --- |
| MS-01 | Invalid mixed workload is rejected before execution | `multicast_inventory; mixed_validation` | Pure rejection before Environment allocation; CLI preserves existing output on invalid settings. |
| MS-01 | Generic configuration differs from the example hardware | `multicast_admission; tree_runtime; scalar_shared_runtime` | Changed dimensions, clocks, flit widths and capacities; generic scalar widths 1/2/4/8; profile word/geometry rejection. |
| MS-02 | Source inclusion changes exactly one recipient | `multicast_tree_admission; multicast_memory_runtime` | Independent source-inclusion trees, exact local ejection/service and physical overlap rejection. |
| MS-02 | Rectangle crosses nonworker positions | `multicast_tree_admission; tree_runtime` | Transit-only terminals consume finite forwarding service without payload; disabled workers rejected. |
| MS-02 | Opposite fabric selects the same physical workers | `multicast_tree_admission; tree_runtime; mixed_compute_runtime` | Translated opposite-fabric coordinates preserve physical recipients; all traversed channels retain fabric identity. |
| MS-03 | Two destinations share a prefix | `tree_runtime; mixed_validation` | Per-flit unique shared-prefix/branch/ejection launches; independent packet reconstruction and missing/duplicate corruption. |
| MS-03 | Payload spans more than one packet | `multicast_memory_runtime; mixed_validation` | Payloads 1/31/32/33/63/64/65/96 and configured headers; full-extent publication only after all serviced chunks. |
| MS-04 | One destination backpressures a finite tree | `tree_runtime; multicast_memory_runtime` | Capacity-one replication/staging/credits with slow recipients, retained credits and exactly-once delivery/drain. |
| MS-04 | Conflicting trees and ordinary traffic share hardware | `tree_runtime` | FIFO conflicting grants, disjoint grants, no partial lane holding; real shared request/response serializers and router grants. |
| MS-05 | Posted handoff precedes a slow target effect | `multicast_memory_runtime` | Posted source-read release/handoff precedes slow target service; target lease and global pending state retained. |
| MS-05 | Acknowledged multicast waits for every return | `multicast_memory_runtime` | Per-recipient/segment actual acknowledgements; delayed final return, response backpressure and credit drain. |
| MS-05 | Both fabrics and local compute access one L1 | `multicast_memory_runtime; scalar_shared_runtime; mixed_compute_runtime` | Canonical owner across both fabrics; ordinary/local/compute/scalar clients use the same bounded memory server. |
| MS-06 | Concurrent producers update one counter | `scalar_shared_runtime` | Opposite-fabric updates and local clients share one FIFO; old/new and sequence values verified independently of reply order. |
| MS-06 | Overflow or ambiguous scalar storage is requested | `multicast_inventory; multicast_admission; scalar_shared_runtime` | Overflow, granule/inbox overlap, alignment and unrepresentable observation duration rejected before allocation. |
| MS-06 | Independent L1 resources receive atomic work | `scalar_shared_runtime` | Different L1 RMW intervals overlap; no chip-wide atomic lock. |
| MS-07 | Posted atomic has only local completion | `scalar_shared_runtime` | Posted request handoff precedes update; no response or invented source read; resume applies one update. |
| MS-07 | Returning atomic result travels back | `scalar_shared_runtime; mixed_validation` | Actual previous-value response and local inbox service; large exact integers/reordered replies; early reply corruption fails. |
| MS-08 | Producer notifications release one local consumer | `scalar_shared_runtime; mixed_compute_runtime; mixed_validation` | Charged observations and collector thresholds 2/4; local full-extent readiness and causal signals verified. |
| MS-08 | Counter is ready before payload | `scalar_shared_runtime; mixed_validation` | Counter-ready/data-not-ready remains blocked; early publication/release and stale version evidence rejected. |
| MS-08 | Update races with wait registration | `scalar_shared_runtime` | Subscription-before-observation and change recheck cover an update during observation service. |
| MS-08 | A fast producer could overtake an earlier barrier | `multicast_inventory; mixed_compute_runtime` | Later-round contribution without collector dependency and cyclic/remote wait proofs rejected. |
| MS-09 | Distribution, compute and collection overlap | `mixed_compute_runtime; mixed_validation` | Two workers overlap actual finite FC/matmul cost with shared traffic/service; no duplicated imported-operand network read. |
| MS-09 | A second generation reuses bounded slots | `mixed_compute_runtime; mixed_validation` | Two rounds reuse one slot per worker, advance generations and thresholds, reject stale generation/cycle corruption. |
| MS-10 | Pause between target effects and final responses | `tree_runtime; multicast_memory_runtime; scalar_shared_runtime; mixed_compute_runtime; mixed_validation` | Multiple interruptions retain grants/leases/service/slots and produce exactly the uninterrupted final record; idempotent finalization. |
| MS-10 | Pending wait cannot make progress | `mixed_compute_runtime` | External read lease stalls broadcast with no future event: idle_with_pending, no blocked-wait execution owners, retained reservations; release resumes to counter 4 and full drain. |
| MS-11 | Shared accounting or delivery is corrupted | `mixed_validation; multicast_validation` | Independent input-derived edges/recipients/control/segment bytes; plausible-total missing/duplicate/prefix/control corruptions fail. |
| MS-11 | Scalar and causal evidence is corrupted | `mixed_validation` | Duplicate RMW, wrong old value, early return/wait/barrier, stale generation, hidden lease/tree grant and transient credit over-allocation all fail. |
| MS-11 | Only model evidence is available | `mixed_validation; validation_runner; validation_cli` | Actual suites pass model checks; functional_reference/silicon_timing stay unvalidated and legacy projection coverage remains scoped. |
| MS-12 | CLI publishes a finite result safely | `mixed_validation; multicast_validation` | Actual generic/profile/partial/invalid CLI; exits 0/1/2, atomic stdout/file agreement, input/hardlink/output preservation and budgets. |
| MS-12 | Legacy consumers and examples remain bounded by their contracts | `packet_runtime; validation_adapters; topology_consumers; memory_adapters; compute_adapters; validation_cli` | Unchanged predecessor version/timing/digest checks, 7-D/4-D and four-coordinate boundaries; actual Darknet19 smoke; optional ML skipped. |
| MS-12 | Final child evidence is recorded | `delivery.md; delivery-identities.json; progress.md; tasks.md` | Exact source/input bytes and commands, seven local part commits, bounded TR-05/MT-06/VA-04 evidence; separate umbrella reconciliation. |

## Resource and dependency review

Pure compilation rejects cyclic application, memory/version, slot reuse and
phase/FIFO dependencies. A remote diagnostic effect is not accepted as a local
completion fence. Common physical target addresses and canonical owners are
validated across aliases and fabrics before any environment is allocated.

Tree admission preprovisions all source/target memory leases and response
capacity in one non-yielding bundle step; failed admission changes no readiness.
A FIFO controller grants every tree lane or none. Branch copies retain counted
incoming/replication storage until every required output accepts them. Downstream
credit admission precedes router service, so a blocked branch holds no router
grant. Tree lane grants drain after all terminal effects and delayed credits.
Independent checks reconstruct token ownership and enforce lane capacity at every
distinct event timestamp as well as in final/interrupted snapshots.

Ordinary request/response and multicast lanes share physical link/router service.
Unicast uses the existing dateline ordering. Responders consume finite service,
release request credits and atomic server grants before waiting for return
injection, and use preprovisioned bounded reply resources. Scalar observation
waits subscribe before service and recheck updates; a blocked wait owns no
compute context, network credit, active server grant or tree reservation.

Compute waits for local operand readiness before reserving a generation.
Generation reuse is ordered with prior readers/writers, and legal local or
acknowledged output completion precedes explicit signals. Finalization waits
for every operation, target effect, response, observation, job and owner. An
idle or cycle-limited session retains its state rather than manufacturing drain.

Finite-drain evidence assumes enabled sinks, finite service, fair arbitration,
acyclic admitted dependencies and no permanent failures. Slow branches, minimal
capacities, directed temporary slowdowns and changed clocks are exercised.
These model policies are not proofs of exact hardware multicast VC assignment,
NIU arbitration, arbitrary semaphore protocols or vendor kernel execution.

## Evidence boundaries

The working assumed Wormhole fixture is `wormhole_b0_mixed_assumed.json`.
The historical synthetic-ID/profile fixture `wormhole_b0_multicast_assumed.json`
remains invalid and is never counted as an executable hardware example. Neither
fixture provides a physical measurement. Numerical tensor execution, arbitrary
multicast trees, general atomics, dynamic kernels and multi-ASIC execution remain
unsupported. Functional-reference and silicon-timing tiers remain unvalidated.

The final delivery manifest and progress record identify actual test totals,
commands, source/input identities and local commits. Optional Torch/PyG execution
is skipped because dependencies are unavailable. Historical root local-remap
failures remain separate from the newly executed Darknet19 smoke. No umbrella
milestone/specification was synchronized, no change archived and no commit pushed.
