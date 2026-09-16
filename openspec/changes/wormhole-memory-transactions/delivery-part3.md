# Part 3 delivery: shared bounded packet transport

## Behavior and class impact

Before this part, memory packets were pure wire-plan records. The v2 runtime only accepted positive useful payloads and scheduled its existing fixed traffic inventory. MemoryWirePlan can now be adapted into an opt-in PacketTransport running real header/data flits through the existing credit, serializer, slowdown and router service machinery.

packet_transport.py supplies the typed LinkService boundary, an internal PacketFlit that permits zero useful bytes, the extracted RouterPipeline and one shared forward_flit service routine. VirtualChannelLink accepts the typed contract; its credit ownership, bounded staging and physical scheduler are unchanged. LinkContract still requires the original positive-payload TransportEnvelope. TorusTransport keeps its public wrapper/results and existing scheduling, including the RouterPipeline import path.

packet_runtime.py supplies finite packet admission, packet handoff/receipt events and trusted producer/consumer hooks in one caller-supplied environment. Each endpoint/class has explicit TX packet descriptors, TX staging and RX staging. One forwarding worker per incoming lane routes the actual received token, allowing runtime packet submission in a different order from the plan inventory. Every packet shares the same object for each physical link/router. Hooks cannot be supplied by JSON.

memory_transport.py validates explicit fabric/link/router settings before network construction, then maps MemoryWireEnvelope layouts and exact routes into the internal transport contract. Physical width and ACI clocks must agree with the admitted memory plan. Slowdowns require enabled directed network overrides; they cannot silently affect every link or local channels. Widths, clocks, delays, credits, queue/staging bounds, router capacity and arbitration quantum are configurable and contribute to the transport identity. MemoryWirePlan now carries its admitted ACI clock for this check; its existing content digest is unchanged.

The finite inventory includes response metadata, but it does not pre-issue responses. Response submission is rejected until its actual request receipt event. A trusted driver can wait for that event and then submit after control/service work; tests exercise this causally in the shared environment. Actual memory service is not attached yet. PacketTransportResult explicitly reports execution=wire_packets_only and memory_service=unsupported; it is not MemoryReplayResult or a claim that an addressed operation completed.

## Wait and ownership audit

| Wait | Retained owner/storage | Release/progress rule |
| --- | --- | --- |
| Packet admission | Finite inventory/control metadata only | Nonblocking try_submit; descriptor acquired only on success |
| Producer hook | One TX staging lease plus admitted packet descriptor | Hook finishes before staged flit enters network admission |
| Injection credit | Same counted TX staging lease | Release staging only after link reservation and make_ready |
| Downstream credit | Existing incoming link token | Keep charged until downstream transfer completes |
| Router admission | Incoming token and reserved outgoing credit | Finite router service starts after downstream credit exists |
| Physical serialization | Existing shared link staging/credit | Serializer waits only on finite service; no endpoint callback |
| RX staging full | Flit stays in charged ejection storage | Take into RX queue only after staging can be acquired |
| Consumer hook | RX staging lease and ejection token | Release both after hook returns; tail then publishes receipt |
| Response readiness/admission | Finite response metadata | Separate response-class descriptor/staging; no request token retained by the driver |
| Delayed credit return | Link pending-return entry | Successful drain includes the return event |

Generators retain only charged flits or finite control metadata. Endpoint queues have no SimPy put waiter containing unaccounted data. Lease traces independently reconstruct occupancy/high-water marks. Packet descriptors retire on final injection; receipt is separate and can occur later. A state-update race found during review was corrected: consumer completion re-reads packet state after yielding so it cannot erase an injection handoff timestamp recorded during the wait.

There is no memory grant in this part. Future hooks must release memory grants before network waits and must not create request/response wait cycles. A hook that never returns intentionally leaves charged storage and reports idle_with_pending. Current tests establish drain for finite configured workloads; they do not prove arbitrary callbacks or the future complete memory protocol deadlock-free.

## Independent checks and evidence

- Full serialized v2 results captured from 4c5f416 remain identical after extraction, including every trace, token ID, timestamp, count and resource snapshot: same-router 9-byte fingerprint f0ea85ad0d2296a7f1355aa6fbae146ff56208e585969052e55f76ef90581482; 240-byte streamed fingerprint 6b0d7e49c68f6987447ac13324a84fb6604f9100aaf230c91f82b3d24fc1643c; two causal requests with response/sink delays fingerprint 6c6304a42de8b98106fa71a70c6867448151b41d2cd94879eb7fae9c20db3e81.
- The source-pinned part-2 literal channel tables now match executed launches for 8193-byte posted writes, acknowledged writes and reads on both fabrics, including wraps and reversed submission order. These remain synthetic transport checks, not silicon measurements.
- One useful byte costs 384 channel bytes posted, 512 acknowledged and 448 read on the selected fabric-0 routes. Headers carry zero useful bytes but consume full physical serialization, credits and router transfers.
- Independent same-router timelines complete at 7 ACI cycles posted and 11 acknowledged/read for the explicitly configured unit-service fixture. A second geometry uses 16-byte physical flits, 8-byte data capacity, two headers, 24-byte segmentation and a different native clock.
- Capacity-one tests cover blocked producers/consumers, 8193-byte streams, mixed read/write traffic on both fabrics, response delay, a directed slowdown/recovery and actual shared-serializer exclusion. A separate 12-operation, two-initiator test merges routes and verifies finite-workload progress and exactly-once receipt.
- Early receipt is not complete drain: a delayed-credit test reports incomplete at the horizon despite all useful bytes arriving, then resumes and drains without duplicate launches. Malformed or cross-plan envelopes fail before link credit/event mutation.

## Validation

- `.venv/bin/python -m unittest simulator_detailed.tests.test_packet_runtime -v`: 12 tests passed.
- `.venv/bin/python -m unittest discover -s simulator_detailed/tests`: 162 tests passed, including legacy network/DMA, route, v1/v2 and profile gates.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: 0 errors, 0 warnings, 0 informations; all three new production modules are included.
- Scoped `.venv/bin/ruff check` on the seven changed Python modules/tests: passed.
- `openspec validate wormhole-memory-transactions --strict --no-interactive`: passed.
- `git diff --check`: passed.

## Remaining scope

This internal transport API does not enable MemoryReplay CLI execution or use its operation issue, memory-service and dependency declarations. MemoryTransportConfig explicitly configures the wire-only kernel; the later composition must reconcile those values with MemoryReplay instead of charging two copies. Local/fence metadata creates no wire packets and has no execution here. Capacity handles, range readiness/access leases, shared L1/DRAM service, operation visibility/completion, local clients, fences, legacy adapters and full memory results remain parts 4–8. The full-profile workload gate remains closed.

No bank/NIU/header-bitfield/register behavior or hardware calibration is claimed. Endpoint capacities, arbitration and timing are explicit model assumptions. The next part is shared capacity/readiness ownership and aggregate memory service; it must be committed independently before transaction composition.
