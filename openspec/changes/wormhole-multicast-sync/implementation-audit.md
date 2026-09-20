# Implementation audit — 2026-09-18

The historical prototype audit below reopened overstated checkmarks. Its runtime
gaps have since been repaired and verified in Parts 1–6: **30/35 tasks**. The
shared runtime, scalar/memory/compute integration, retained resume, real profile
fixtures, CLI and independent audits now exist. Part 7 will replace the historical
scenario table with consolidated current evidence. The child is not yet claimed
delivered; the validation predecessor remains delivered.

The findings and scenario table below describe the pre-repair serial prototypes,
which remain explicitly scoped for old inputs. They are not the current shared
runtime behavior. See the dated repair/check records in [progress.md](progress.md).

## Concrete findings

* `SharedPhysicalTransportRegistry` stores lane metadata and owners. It does
  not construct or attach to `VirtualChannelLink`, `RouterPipeline` or the
  packet runtime. The existing test named `shares_serializer` checks metadata
  only; it does not test shared serialization or bandwidth.
* `TreeForwardingEngine.run` advances a scalar elapsed-time accumulator. It
  does not charge input tokens, replication slots, endpoint ejections or
  delayed returned credits. Branch capacity divides a synthetic delay; it is
  not an occupancy bound. Reservation setup cost is also used as edge time.
* `MulticastMemoryExecutor` runs the transport before recording the source
  read, uses standalone whole-range service arithmetic, then sequentially
  publishes destinations. It does not acquire canonical leases or use the
  existing aggregate service server. It emits one acknowledgement event per
  recipient although its summary counts one per recipient/segment. Actual
  return paths are absent; posted completion is not local handoff.
* `ScalarExecutor` maintains a separate integer ledger. This repair now honors
  declared increment dependencies, bounds unserviced effects by the horizon,
  converts atomic native cycles to ACI cycles, and reports the linearization
  event time consistently. Address overlap now includes the physical owner and
  absolute address. Shared RMW service, atomic granule reservations, request
  paths, return inboxes and subscription/recheck races still need implementation.
* `FinitePipelineExecutor` independently runs memory, scalar and fixed-duration
  stages. The fixture's stage at `ep-t0_0` reads another worker's buffer. There
  is no attachable FC/matmul component, generation-aware slot ownership, shared
  environment, or causal collector barrier between rounds. The generated
  increment can be timestamped before its declared memory dependency finishes.
* The pipeline previously resumed until its pending list emptied, including
  after non-progressing execution; an unready source could loop forever. It
  now stops on incomplete/no-progress results and cannot label those results
  complete. The adapter no longer silently ignores requested interruptions.
* The CLI/harness reject a result exceeding its declared horizon before
  publishing JSON. A valid mid-service retained snapshot is not yet available:
  an overrun is an explicit error, not a fabricated bounded snapshot.
* `wormhole_b0_multicast_assumed.json` combines synthetic endpoint IDs with a
  profile source and has no executable binding. It is an invalid/rejected
  fixture today, not evidence of a working Wormhole replay.

## Acceptance-scenario review

“Partial” means the named prototype checks cover some structure or arithmetic;
it does not mean the requirement is delivered. Every scenario in the approved
specification is covered by a row below.

| Requirement / scenarios | Current evidence | Remaining implementation/evidence |
| --- | --- | --- |
| MS-01: invalid input; changed generic configuration | Strict parsing, canonical memory/service admission, locator-independent identities, pure profile binding, width checks and forged-plan rejection; CLI error preserves output | Complete mixed hardware/control settings, route and dependency admission |
| MS-02: source inclusion; nonworkers; opposite fabric | Task 1.3: independently enumerated X/Y trees on both fabrics, common physical address, unavailable workers/aliases, degenerate/local and transit-terminal inventories | Actual local ejection and terminal execution under bounded transport |
| MS-03: shared prefix; segmented payload | Independent declared-coordinate edge and recipient oracle, including missing segment and duplicate flit corruption | Physical injection/ejection and response traffic; actual per-segment services and visibility |
| MS-04: capacity-one backpressure; conflicting trees with ordinary traffic | FIFO reservation metadata tests only | Shared links/router stages, finite tokens/replication/descriptor occupancy, fair mixed traffic, directed slowdowns and drain proof |
| MS-05: posted slow tail; delayed acknowledgement; shared L1 | Prototype useful-byte arithmetic and source/target event records | Existing canonical registry, real service contention, bounded per-segment returns, distinct source/target completion |
| MS-06: concurrent producers; overflow/storage; independent L1 | Integer increments/overflow and owner-qualified scalar overlap tests; native clock conversion corrected | Atomic access granules, Wormhole aligned 32-bit contract, shared bounded indivisible RMW service and independent-owner concurrency |
| MS-07: posted handoff; returning previous value | Inline width summaries and old/new integer values | Actual one-flit network requests/responses, handoff/effect separation, inbox service and response backpressure |
| MS-08: local consumer; data late; registration race; phase overtake | Threshold/data membership checks, missing-dependency guard | Local generation proof, serviced initial/change observations, race-safe subscriptions, phase-overtake admission and charged notifications |
| MS-09: overlap; second generation reuse | Serial two-round prototype and cycle checks | Shared FC/matmul component/session, local operands, causal collection, real A/B/C slot generations, overlap and reuse |
| MS-10: partial responses; idle wait | Incomplete projections, no-progress loop fixed | Live retained owner snapshots, one-shot/resume equivalence without replayed effects, posted tails and exactly-once teardown |
| MS-11: tree corruption; scalar/causal corruption; evidence tiers | Input/event tree oracle, exact scalar transition checks, current early-release defect detected, unavailable ownership/service checks stay unsupported, external tiers stay unvalidated | Full shared resource/service/control/generation and causality oracles once runtime events exist |
| MS-12: safe CLI; legacy consumers; delivery record | Atomic JSON output, hardlink/input protection, versioned plain output, guarded horizon, adapter budgets, legacy checks | Valid Wormhole/partial snapshots, complete producer-typed result, all runtime scenarios and final delivery/source-input manifest |

## Validation boundary added by this repair

The new `multicast_sync_v1` adapter can inspect the serial prototype. Its
`multicast` and `routing` checks reconstruct rectangle edges and recipients
from the admitted input, without importing production tree helpers. Missing or
duplicate recipients, entire missing segments, wrong fabrics and plausible but
incomplete totals fail. Event normalization retains original details, scalar
integers and each publication's actual address and endpoint. It does not copy
final buffers into invented effects or connect unrelated timelines by list
order. Optional event details are omitted from all old observation records.

Full `packet_accounting`, `memory_service`, `ownership`, `synchronization`,
`compute_work` and `drain` checks are **unsupported** when their required shared
runtime evidence is missing. Observable contradictions (for example leaked
reservations, repeated scalar effects or early causal release) still **fail**.
A suite requiring the missing checks is incomplete, even when the projection
reports `complete`. Capabilities and coverage retain the multicast/synchronization
implementation as pending. No silicon or functional-reference evidence has been
promoted.

## Next implementation boundary

Finish Part 1's mixed admission and response/control inventories, then implement Part 2's composite internal link
contract on the existing physical serializer/router kernel. Add mixed-traffic
capacity-one tests that fail against the current metadata-only registry. Only
then attach multicast effects and scalar RMW work to the existing canonical
memory service and refactor finite compute into a shared component. The CLI and
independent audits must consume that runtime's retained snapshots before Parts
3–7 can close. This is existing approved scope, not a proposed scope reduction.

The umbrella's milestones remain untouched. Do not sync, archive, publish a
final delivery, or push on the strength of these prototype results.
