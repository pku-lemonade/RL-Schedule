# Part 5 delivery: bounded addressed transaction execution

## Behavior and class impact

Before this part, the shared packet kernel and memory resource/server APIs ran independently. `MemoryExecutionPlan.compile` now composes their admission checks, and `MemoryRuntime` executes initialized-source, nonconflicting reads, posted writes and acknowledged writes in one environment. This is an opt-in Python API; the memory CLI, dependency/local/fence dispatcher and legacy adapters remain later parts.

The new `memory_execution.py` holds the pure execution plan, explicit runtime settings and immutable result records. `memory_runtime.py` owns transaction control and implements the existing packet producer/consumer hooks. `MemoryResources`, the memory server, routing, packetization and link/router scheduling retain their existing mechanics. No memory grant waits for the network. Both new modules are included in strict Pyright.

Admission rejects dependencies, producer-dependent buffer declarations, local operations, fences, uninitialized sources and physical read/write or write/write conflicts, including aliases and overlapping source/destination ranges in one operation. Concurrent reads of the same source remain supported. Existing resource capacity, address, permission, route and packet/service geometry checks still run before the environment is allocated. Runtime construction recompiles this boundary rather than trusting mutable nested plan objects. Endpoint queue/staging capacities must agree between memory and transport settings.

`MemoryRuntimeConfig` explicitly supplies the transport settings, responder capacity per target endpoint, and request/response control service in ACI cycles, with evidence. The replay supplies issue latency and maximum outstanding segments per canonical initiator L1 resource. These issue slots are shared by that worker's aliases on both fabrics. Control handling is charged once at the first header; real header flits independently pay physical transport. Issue latency is a per-segment delay, not a calibrated NIU issue-rate/port model. Different endpoint aliases share backing memory capacity/service while retaining their separate per-fabric responder and packet staging budgets.

`MemoryOperationRecord` previously imposed one linear order on all timestamps. That was unsuitable for reads, whose request handoff precedes source reading, and posted writes, whose local completion can precede destination readiness. It now checks the operation-specific partial order and the exact posted/read observable boundary. `MemoryReplayResult` admits the explicit `addressed_memory_disjoint_v1` execution label. Its original default remains unchanged. The composed transport snapshot reports `memory_service=external_hooks`; standalone wire-only results retain their existing label and serialized behavior. No full-profile capability gate has been opened.

## Execution and observation boundaries

The finite segment inventory and its coroutines contain control metadata, not resident payload. Readiness is checked before issuing a segment. Source/destination access leases are acquired without yielding between them; failed destination acquisition releases the source lease before waiting. Once admitted, a segment owns a canonical issue slot until its mode's completion boundary.

Source reads occur only after the packet kernel charges TX staging. Each data flit performs the configured resource-sized memory chunks, releases completed service, and then waits for network admission under its staging charge. The source access lease releases after its last useful byte has been serviced. Headers and padding do not create memory service. Destination data retains counted RX staging and its ejection token while waiting for write service; completed chunks publish precise producer-version ranges, and the segment publishes destination readiness after its final write.

| Mode | Request/response work | Observable completion and issue retirement |
| --- | --- | --- |
| Posted write | Stream source reads and request data into target writes; no response | Final request flit handed to the local injection link, possibly before target readiness |
| Acknowledged write | Same write path; generate a real header-only response after target write service and request consumption | Ack consumption after configured response control service |
| Read | Real header request; stream target reads into response packets; write received useful data into local storage | Final local destination write, after all response data arrives |

Responder state is acquired at request-tail consumption, after any target write service, while the tail remains in counted RX storage. The responder waits for request consumption, submits its response through the independent response class, and releases its state after the response's final local handoff. It never waits for an initiator/request slot. There is one responder coroutine per acquired responder slot, not an unbounded set of resident response jobs.

The audit considered whether allocating responders at headers could block an earlier packet's tail. Inspection showed that the existing link kernel already serializes packets within a lane; the competing-initiator test verifies this rather than claiming flit interleaving. Tail-stage admission makes the response wait order independent of that packet-ownership property: an occupied responder has no remaining request data to consume. A separate test actually saturates responder state, request RX and response TX together, then verifies drain with slow source memory.

Response-wire receipt is derived from the actual response tail's ejection `link_arrive` event. The packet kernel's receipt event occurs after RX consumption, so it cannot represent this earlier boundary. This distinction is observable in incomplete read snapshots where the response has arrived but L1 service, completion and the issue descriptor remain pending.

Operation submission and descriptor acceptance are the first actual segment facts. Source completion, final handoff, response receipt, destination readiness and completion aggregate only when every segment has the corresponding fact, using the latest timestamp. A test perturbs issue scheduling to complete read segments in order 1, 2, 0 and verifies that the operation cannot complete after only segments 1 and 2. The default scheduler remains deterministic; this is not a new hardware response-reordering claim.

## Accounting, drain and diagnostics

Logical bytes count the requested operation ranges once. Packet bytes count actual injection launches; channel bytes count actual launches over every local/network channel. Partial results therefore do not claim untransmitted planned bytes. Completed service chunks contribute rounded service bytes; queued/active work appears in pending state instead. Header/padding/layout export is still completed in part 8; the compiled wire plan already retains those definitions.

The result includes operation/segment facts, descriptor acquisition/retirement, counted endpoint staging, network credit/router state, memory ownership/readiness, queue/service traces and full native/ACI chunk records. The base service projection and detailed chunks both round-trip through JSON without silently losing subclass fields. All labels continue to say silicon timing is unvalidated.

Successful replay requires all segment completions and target effects, all access leases and memory work, all responder/issue slots, packet delivery, endpoint staging, network/router work and delayed credits to drain. Persistent buffer reservations do not count as pending work. The successful result preserves their readiness/capacity snapshot before teardown, then records the restored resource capacities after exactly one release per buffer. Repeated `run` returns the cached result rather than releasing twice.

Cycle-limited runs retain ownership for continuation. Idle-with-pending runs report blocked segment/access state instead of succeeding. Pending identifiers include segment completion/readiness, descriptor owners, packets, memory jobs/accesses, and network resources with fabric/lane identity. Tests cover posted local completion with pending remote writes, physically arrived read responses with pending local writes, completed effects with delayed credits, waiting start times, invalidated sources before issue and externally blocked destination leases with no partial source lease retained.

## Independent oracles and wait audit

The one-byte same-router oracle uses one-cycle source/destination memory chunks, one-cycle links and one-cycle router transfers, with zero issue/control/credit-return delay. The request header travels at 0–3. Its injection credit remains held until router transfer ends at 2, so write data travels at 2–5 and destination service runs at 5–6. A read response starts after request receipt at 3; its source chunk runs at 3–4, tail arrives at 8 and destination service ends at 9.

| Mode | Source read done | Request handoff | Response tail arrival | Destination ready | Completion | Drain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Posted | 1 | 2 | absent | 6 | 2 | 6 |
| Acknowledged | 1 | 2 | 9 | 6 | 9 | 9 |
| Read | 4 | 0 | 8 | 9 | 9 | 9 |

These cases independently expect 64 rounded memory-service bytes (32 read + 32 write), 64/96/96 packet bytes and 128/192/192 channel bytes. They also verify result serialization and idempotent successful teardown.

The pinned 8,193-byte cases execute all three modes on both fabrics: 8,288 posted or 8,352 paired packet bytes, 16,448 serviced bytes, 514 memory chunks and exactly one response per non-posted segment. Fabric-0 channel totals are 49,728 posted, 49,984 acknowledged and 33,536 read; fabric-1 totals are 33,152, 33,536 and 49,984 respectively. The fixtures exercise asymmetric request/response lengths and wrap routes.

A second generic configuration uses 16-byte physical flits, 8-byte data capacity, two headers, 24-byte segments, four-byte service chunks/granules and a 250 MHz memory clock against 500 MHz ACI. A 25-byte read independently expects 192 packet bytes, 896 channel bytes and 56 service bytes. Its fourteen chunks each cost 1.5 native or 3 ACI cycles, including the one-byte tails. These expectations do not use production cost helpers.

| Wait | Retained ownership | Release/progress condition |
| --- | --- | --- |
| Start/source readiness | Finite operation metadata | Start time or resource readiness; no issue/network/service grant |
| Initiator issue full | Finite segment metadata | Earlier segment's mode-specific completion |
| Access conflict | No partial source/destination pair | Resource ownership change |
| Issue delay/request TX full | Issue descriptor and access metadata | Configured delay or bounded TX admission |
| Source service queue/active | TX staging and source access | FIFO finite local memory work; grant released before network admission |
| Injection/downstream backpressure | Counted endpoint/network storage | Existing fair physical transport and credits |
| Destination service queue/active | RX staging, ejection token and destination access | Finite memory write; no network grant required by active service |
| Responder full | Request-tail RX staging/token; no active memory grant | Earlier response handoff, independent of request ingress |
| Response production/TX full | Bounded responder; data uses counted TX staging | Independent response class, finite source reads and transport |
| Response sink | RX staging/token and reserved destination access | Finite local write/control; no issue/request slot acquisition |
| Posted effects or delayed credits | Their actual remaining owners | Full effect/resource drain before teardown |

The tests replay descriptor ownership transitions to check exactly-once release and high-water marks, reconcile actual launch/service bytes, check memory service intervals never overlap within one owner, and check all reported capacity/credit conservation. Mixed traffic uses both fabrics, configurable directed slowdown, capacity-one endpoint staging and service queues, and slow shared memory. A separate test proves one canonical issue slot is shared by both aliases; another admits concurrent reads of a common source with two slots.

## Validation and remaining scope

- `.venv/bin/python -m unittest discover -s simulator_detailed/tests`: 192 tests passed, including 17 new transaction tests and prior memory, v1/v2, DMA and compatibility coverage. Existing exact v2 serialized-result fingerprints remain unchanged.
- `.venv/bin/python -m unittest simulator_detailed.tests.test_memory_runtime`: focused transaction checks also pass after the final diagnostic-identity refinement.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: 0 errors, 0 warnings, 0 informations.
- Scoped Ruff on memory_execution.py, memory_runtime.py, memory_records.py, packet_runtime.py and test_memory_runtime.py: passed.
- `openspec validate wormhole-memory-transactions --strict --no-interactive`: passed.
- `git diff --check`: passed.

This is a generic scheduling/cost simulation with useful-byte readiness metadata, not tensor values or a full Wormhole execution model. FIFO aggregate memory service, chunk granularity, responder/control policy and service timings remain configurable assumptions. There is no calibrated NIU/register/port model, DRAM bank/row model, atomic/multicast/VC_LINKED behavior or hardware validation claim. Dependencies, local memory operations and scoped fences are deliberately rejected until part 6; legacy adapters are part 7; CLI/examples and complete exported evidence are part 8. DFG/compute integration remains a later child change.
