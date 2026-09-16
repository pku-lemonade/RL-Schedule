# Part 4 delivery: shared capacity, range ownership and memory service

## Behavior and class impact

Before this part, MemoryReplay declared buffers and service parameters, but no runtime enforced their capacity, readiness or bandwidth. The new opt-in MemoryResourcePlan, MemoryResources and MemoryResource implement the resource portion independently of the packet runtime. memory_service.py contains the finite aggregate server; memory_resources.py owns capacity, handles, range versions and service pins. Existing NoC, DMA, ScratchpadMemory and public replay/result behavior are unchanged.

MemoryResourcePlan.compile is a pure admission stage layered on structural MemoryPlan. It rejects overlapping reservations, buffers outside effective capacity overrides, incompatible packet alignment/data capacity versus service granule/chunk, and unrepresentable service durations before constructing runtime resources. Earlier wire-only plans can still compile without satisfying service settings they do not execute. This is not full operation/dependency admission; the later replay compiler must compose the required stages.

MemoryResources constructs one resource object per canonical physical ID in the shared caller environment. Its enabled attachment aliases return that same object. Each resource has one SimPy free-capacity container, opaque reservation handles, a nonoverlapping extent ledger, range readiness and one combined read/write server. Initial buffers are charged once. Teardown preflights all owners and handles, requires access/service drain, and restores capacity once; duplicate, foreign or replaced handles cannot release it. Persistent reservations are distinguished from pending work in snapshots.

Readiness uses tagged initial/producer versions and useful byte ranges, without tensor values. Reads require exactly the selected version. Write acquisition excludes overlapping access and invalidates the selected extent; each serviced chunk then publishes only its useful bytes. Untouched ranges preserve their previous version. Adjacent same-version ranges merge, including chunks serviced in reverse address order. Completed portions are observable in metadata while an overlapping write lease still prevents readers from acquiring the range. This is a conservative scheduling policy, not atomic silicon visibility.

Concurrent reads and disjoint accesses may acquire leases together; conflicting leases return backpressure without hidden waiters. A queued or active service chunk pins its lease and buffer. Chunks within one access cannot be serviced twice under different IDs, and useful-range bounds and permissions are checked before mutation. Callers explicitly release completed access leases; partial release with no pending service preserves only effects that actually completed. Whole-operation readiness and compile-time producer/conflict dependencies remain part 6.

Strict revalidation uses Python-mode model data: JSON serialization was found to normalize a mutated boolean count into integer 1 before validation. New resource/service admission preserves and rejects the malformed value. This fix is scoped to the new modules.

## Service policy and independent timelines

The named aggregate_shared_rw_v1 server has a configurable FIFO waiting capacity plus one active slot. It chooses ties by synchronous admission order and never bypasses an already queued job. Rejected admission creates no event/job or resource charge. Read and write directions, aliases and fabric clients consume the same interval budget. Independent physical resources have independent servers.

For address a and positive useful length n no larger than configured chunk M, the server charges G * ceil(((a mod G) + n) / G) bytes. Native duration is fixed per-chunk latency plus charged bytes divided by configured bandwidth. It converts once into ACI cycles using the configured clocks. Configuration and admission guard overflow, underflow and queued finish times that cannot advance the environment clock. No positive sub-quantum access becomes zero service.

The test expectations below are literal independent tables, not computed through production cost helpers. All three jobs arrive at time zero; directions are read/write/read.

| Configuration | Address/useful bytes | Serviced bytes | Native cycles | ACI start/end |
| --- | --- | --- | --- | --- |
| G=32, M=32, bandwidth=32, latency=1, native=1 GHz, ACI=500 MHz | 0/1 | 32 | 2 | 0/1 |
| same | 32/32 | 32 | 2 | 1/2 |
| same | 63/2 | 64 | 3 | 2/3.5 |
| G=8, M=16, bandwidth=4, latency=0.5, native=250 MHz, ACI=1 GHz | 0/1 | 8 | 2.5 | 0/10 |
| same | 16/16 | 16 | 4.5 | 10/28 |
| same | 7/2 | 16 | 4.5 | 28/46 |

The generic chunk primitive can charge unaligned subranges; this does not relax the replay's admitted aligned-address subset. The later composition must split addressed traffic on the configured chunk/granule boundaries. Another test services one useful byte in 0.0625 cycles at zero fixed latency, and capacity-one saturation produces exact intervals 0–5, 5–10, 10–15 with no hidden third job.

Synthetic generic clients through two DRAM fabric aliases and a resource-local handle share intervals 0–1, 1–2, 2–3. A different L1 resource overlaps at 0–1. These are calls to the shared ownership/service API; packet hooks have not yet been connected. Service records preserve useful/rounded bytes, address, client, direction, queue-enter/start/end, clocks and native/ACI duration. Queue events and snapshots expose finite waiting/active occupancy and pending IDs.

## Wait and ownership audit

| Boundary | Retained state | Progress/release condition |
| --- | --- | --- |
| Capacity reservation | One exact buffer extent and opaque handle | Synchronous validated container charge; no waiting capacity request |
| Unready/conflicting access | Caller metadata only | try_acquire returns None, creating no lease or queued request |
| Service queue full | Caller owns its existing access and any external staging | try_service returns None; no service grant or hidden job |
| Admitted waiting chunk | One bounded FIFO entry and pinned access | Older admitted work finishes; the active slot cannot wait on a client |
| Active chunk | One memory service slot and pinned access | The worker yields only its finite local timeout |
| Completion | Useful-range publication and completed metadata | Memory grant is released before waking the client; lease remains caller-owned |
| Downstream network wait | Caller-controlled staging, after memory completion | No active memory grant is returned to or retained by the caller |
| Teardown | Persistent buffer reservations | All access leases and service work must drain before any release |

The server accepts no network callback or payload container. A regression leaves a completed client's downstream gate permanently blocked and verifies unrelated memory work still completes. The later packet/memory composition must keep actual flits in counted TX/RX staging while waiting for this API; this part cannot prove the complete transaction protocol live.

## Validation and remaining limits

- 13 focused resource/service tests passed as part of the full detailed suite.
- `.venv/bin/python -m unittest discover -s simulator_detailed/tests`: 175 tests passed, including all prior v1/v2, memory-wire and legacy DMA regressions.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: 0 errors, 0 warnings, 0 informations, including both new production modules.
- Scoped `.venv/bin/ruff check` on memory_resources.py, memory_service.py and test_memory_resources.py: passed.
- `openspec validate wormhole-memory-transactions --strict --no-interactive`: passed.
- `git diff --check`: passed.

This executes generic memory resource/service primitives. It does not yet execute end-to-end addressed reads/writes, operation issue/completion, fences, local-operation scheduling, legacy adapters or the memory CLI. No capability flag or full-profile workload gate has been opened. MemoryReplayResult remains gated, and PacketTransportResult still accurately says its own execution has no memory service.

Aggregate bandwidth, fairness, granularity, chunking and fixed latency are model assumptions retained in configuration/evidence. Narrow writes pay rounded aggregate write service without simulating L1 read-modify-write internals. DRAM bank/row/channel imbalance, hardware port arbitration and silicon timing calibration remain unsupported. The next separately committed part composes bounded packet staging with this service for initialized-source, disjoint-access transactions.
