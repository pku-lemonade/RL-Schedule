# Part 6 delivery: explicit memory ordering, local clients and scoped fences

## Behavior and class impact

Part 5 (`10221cc`) executed initialized-source, nonconflicting network transactions and rejected dependency, producer, local-client and fence modes. Part 6 admits an explicit race-free ordering subset and executes it in the same memory/network environment. Existing disjoint transactions preserve their timing, byte accounting, bounded transport and drain behavior.

`memory_ordering.py` is a pure admission compiler. It resolves canonical initiator resources, physical read/write ranges, selected source versions and exact event dependencies before any environment is allocated. `MemoryExecutionPlan` invokes it after resource and route validation. The runtime revalidates this boundary at construction, then waits on actual operation events before acquiring access leases or descriptors. The resource owner, aggregate memory server, packet hooks and shared link/router mechanisms remain in use.

The schema now exposes:

| Field / operation | Meaning |
| --- | --- |
| `source_version` | Explicit initial or named producer version. If omitted, select the source buffer's declared producer, otherwise its initialized version. There is no implicit latest-writer selection. |
| `depends_on` | Wait for observable completion of operations belonging to this canonical worker, including aliases on either fabric. |
| `destination_ready_after` | Wait for a producer that writes this client's own canonical L1 resource. A different issuing worker is allowed; remote sender notification is not provided. |
| `fence_operations` | Exact earlier network-operation IDs of one canonical initiator. The set is frozen at compilation. |
| `fence_fabrics` | Optional fabric scope for those IDs. When omitted, the effective scope is their fabric union. No additional operation is selected by a fabric scope. |
| `local_read` | One source range on the worker's own L1; no destination or fabric field. |
| `local_write` | One destination range on the worker's own L1; no source or fabric field. |

The local-operation shape replaces the earlier unexecuted declaration that required two ranges. Local write is an explicit producer/service placeholder without tensor values or a compute engine; local read consumes a selected ready version without making a copy. `MemoryVersion` moves into the schema for reuse and remains importable from `memory_resources`. Existing network input fields and legacy execution interfaces are preserved.

`MemoryRuntimeConfig` adds configurable `local_capacity_operations` (default 1 per canonical L1) and `local_control_aci_cycles` (default 0). These are explicit model assumptions in the effective settings and plan digest, not device-specific port counts or measured latency. A local slot retains at most one resource-sized service chunk at a time. Both fabric aliases share this local-client pool and the same combined read/write service owner. Local clients do not acquire network issue or responder slots.

`MemoryExecutionResult` now exports resolved `ordering` records with source version, canonical initiator, event waits, fabric scope and `explicit_dependencies_v1` policy. Execution uses `addressed_memory_ordered_v1`; the older base result label remains recognized. Local/fence lifecycle records and local descriptor records have an absent segment index; no fictitious wire segment is created. Local-only replay has an empty packet/link inventory while its configured transport geometry is still validated. The full-profile execution gate, legacy DMA, DFG, detector and RL behavior are unchanged.

## Effect and version rules

An operation dependency alone does not prove every memory effect. The compiler builds event ancestry for each operation: source-read finish, request handoff, destination readiness and observable completion, as applicable. For every overlapping physical read/write or write/write pair, one access must finish before the other operation can begin its accesses. Aliases, list order and later numeric start times cannot bypass this requirement. Concurrent reads and independent ranges remain eligible to overlap.

Posted completion aliases final local request handoff. It proves source reading has finished, so a dependent local producer can reuse the source range while remote effects remain in flight. It does not prove destination readiness, so a dependent conflicting remote read/write or a local-handoff fence cannot use it to authorize target reuse. Read completion aliases destination readiness; acknowledged-write completion additionally waits for the real response and its configured sink control work.

A producer version must name an operation that writes the whole requested read range and whose destination effect precedes that read. Naming a producer does not add an ordering edge. Every other overlapping writer must precede that producer or follow the reader's source-read finish. This rejects stale initialized data, an overwritten producer and partial intervening writes. A subset read is allowed when its producer covers it; extending even one byte beyond the produced range is rejected. Destination-ready dependencies conservatively wait for all producer segments, even if the consumer selects only a subset. No whole-range atomicity is claimed.

Ordinary completion dependencies cannot cross canonical workers. Destination-local readiness can follow an incoming write, including a posted write, but cannot be observed as a remote event by the sender or by an arbitrary DRAM client. This is a local scheduling contract; it does not emulate a semaphore, polling protocol or NIU register.

`local_handoff` fences wait for every selected request's final handoff. `remote_completion` fences accept reads/acknowledged writes and wait for all selected completions; posted writes are rejected. Both reject forward references, foreign initiators, nonnetwork references and scope mismatches. Fences generate no memory service, packet, descriptor or fabricated acceptance/visibility fact. Later operations wait only when they explicitly depend on the fence. A fence cannot absorb later independent work.

## Execution, conservation and wait-resource audit

Runtime operation events aggregate all segment facts before releasing a dependency; submission and acceptance retain their existing first-segment reporting. Source and destination leases still release at their own actual last use. Persistent buffer reservations remain charged throughout execution and are released exactly once only after all operation effects, local clients, network work, memory service and delayed credits drain. Incomplete snapshots include unsatisfied dependency identities and local-client ownership; continuation retains the same environment and resources.

| Added or affected wait | Retained ownership | Progress / release |
| --- | --- | --- |
| Completion / destination-ready dependency | Finite operation or segment metadata only | Actual selected producer event; no issue/local slot, access lease or memory grant |
| Scoped fence | Frozen finite wait metadata only | All selected all-segment handoff/completion events; no memory or network resource |
| Local source version / access conflict | Operation metadata; no local slot or partial access pair | Resource readiness/ownership change after the admitted dependency |
| Full local-client pool | Operation metadata only | An earlier local operation releases its slot |
| Local control duration | One local-client slot and access lease | Configured finite local delay; no active service grant |
| Local service queue / active slot | One local-client slot, access lease, at most one chunk | Existing bounded FIFO server; the active grant performs only finite local service |
| Network producer/consumer service | Existing charged TX/RX staging and access lease | Existing finite memory service; no active grant waits for the network |
| Final drain | Only actual remaining owners, plus persistent reservations | All effects and clients retire, then one teardown |

The part 5 responder/request/response wait audit still applies. No new response-to-request or memory-grant-to-network edge was introduced. Locals share the memory server but never require network progress while holding its active grant. Dependency cycles and conflicting access orders are rejected before execution. This is the supported finite model's wait audit, not a proof of arbitrary firmware liveness or hardware ordering.

## Requirement coverage and independent evidence

The 18 new tests in `test_memory_ordering.py` cover MT-D04..08 with the existing resource/transaction tests:

| Requirement / concern | Evidence |
| --- | --- |
| MT-D04 physical hazards and producer epochs | Aliases on both fabrics; unordered and posted-only conflicting writes rejected; cyclic/foreign dependencies; stale initial/producer versions; partial intervening writer; latest producer and read-before-overwrite accepted |
| MT-D04 partial readiness and source lifetime | A 65-byte local producer followed by a 33-byte subset read; a 34-byte overrun rejected; exact pre-teardown producer range; source access release at source-read finish; source overwrite while remote posted effects remain pending |
| MT-D05 local service and finite capacity | Pure local read/write exact timeline; generic four-byte chunks and a different memory clock; capacity 1 versus 2; independent L1 resources overlap; local reads/writes and both fabrics share one L1 service interval budget |
| MT-D06/07 observable ordering | Segmented acknowledged write -> remote fence -> opposite-fabric read; incoming posted write -> destination-local consumer; sender/foreign completion waits rejected; local-handoff fence precedes destination visibility |
| MT-D07 scope and segment aggregation | Fence selects operations on both fabrics and excludes later work; independent later issue remains eligible; delayed first segment prevents premature consumer/fence release after later segments have completed |
| MT-D08 bounded wait and drain | One-slot endpoint staging and service queues; slow shared memory; producer-dependent network issue has no early descriptor or wire work; waiting local consumer has no early slot; incomplete/resumed results conserve ownership and teardown once |

Independent local arithmetic: 33 useful bytes split into 32+1 under 32-byte chunks/granules. Each chunk costs `(1 + 32/32) * 500 MHz / 1 GHz = 1 ACI cycle`. A start at 3 plus two control cycles yields service intervals 5–6 and 6–7, 64 serviced bytes, zero wire bytes and completion at 7. A 65-byte producer followed by a 33-byte consumer costs five such chunks, 160 service bytes and completion at 5.

The second local oracle uses four-byte chunks/granules, 250 MHz memory, 500 MHz ACI, four bytes per native cycle and 0.5 native latency. Each chunk costs 1.5 native / 3 ACI cycles. Two five-byte operations require four chunks, 16 service bytes and 12 aggregate service cycles. With one control cycle per operation, local capacity 1 finishes at 14; capacity 2 overlaps control and finishes at 13 without overlapping the single memory server.

The cross-fabric fence case independently expects three segments for each 65-byte operation, 576 total packet bytes and 384 total memory-service bytes for acknowledged write plus readback. Pure local clients and fences add zero packet/channel bytes. Conservation checks replay descriptor acquire/release traces, verify memory intervals do not overlap within one owner, bound endpoint/service occupancy, reconcile actual launches/chunks and validate JSON round trips.

Validation:

- `.venv/bin/python -m unittest discover -s simulator_detailed/tests`: 210 tests passed, including 18 new ordering/local-client tests and the existing 17 transaction tests.
- `.venv/bin/python -m unittest simulator_detailed.tests.test_memory_ordering simulator_detailed.tests.test_memory_runtime -q`: 35 tests passed after the final local descriptor-record refinement.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: 0 errors, 0 warnings, 0 informations; includes `memory_ordering.py`.
- Scoped `.venv/bin/ruff check` on all nine changed/new Python files: passed.
- `openspec validate wormhole-memory-transactions --strict --no-interactive`: passed.
- `git diff --check`: passed.

The existing exact v2 result fingerprints remain `f0ea85ad0d2296a7f1355aa6fbae146ff56208e585969052e55f76ef90581482`, `6b0d7e49c68f6987447ac13324a84fb6604f9100aaf230c91f82b3d24fc1643c`, and `6c6304a42de8b98106fa71a70c6867448151b41d2cd94879eb7fae9c20db3e81` under the regression suite. No new hardware measurement or silicon-accuracy percentage is claimed.

## Remaining scope

This part completes tasks 6.1–6.5; the child is 30/40 tasks complete. It remains an opt-in Python API. Legacy DMA/scratchpad adapters are part 7; the CLI, examples and complete exported byte/evidence reporting are part 8. Compute/DFG integration, dynamic circular buffers and calibration remain later child changes.

The supported ordering is conservative at whole-operation boundaries and requires one selected producer to cover a read. It does not merge several producer epochs into one read, infer ordering from time/list position, emulate arbitrary cross-worker synchronization, or model bank/port arbitration, tensor values, NIU counters, atomics, linked packets, DRAM rows/channels, or firmware. Local capacity/control and aggregate service remain configurable assumptions. Successful tests establish the declared model's semantics, conservation and arithmetic; timing remains hardware-unvalidated.
