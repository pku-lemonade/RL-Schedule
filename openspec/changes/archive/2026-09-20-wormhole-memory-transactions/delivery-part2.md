# Part 2 delivery: packet layouts and addressed routes

## Behavior and class impact

Before this part, MemoryPlan produced structural admission records without packets or routes. Its buffer-bound checks did not validate every operation's address arithmetic or enforce worker initiation. Those operation checks now run in MemoryRoutes.compile, called by MemoryWirePlan.compile. MemoryPlan alone is still not full workload or runtime admission; this clarifies the broader wording in delivery-part1.md.

MemoryPacketLayout and packetize now compile independent software segments, header-only read requests/write acknowledgements, and header-plus-data write requests/read responses. Physical width, useful capacity, header length, segment limit and alignment are configuration values. Immutable structural operation/segment/purpose identities use canonical tuple encoding when mapped to existing transport identities. Partial data flits retain their exact useful byte counts. No byte values or hardware header bitfields are created.

TorusRouting extracts the existing pure graph/path logic from BoundTorus. BoundTorus retains its v2 role checks and replay wrapper, while MemoryRoutes uses its own worker/L1/target admission. Memory routing settings are explicit TorusFabricBinding records; empty settings retain structural admission only and cannot produce a wire plan. Complete canonical torus graphs are supported; memory profile input with unresolved connectivity remains rejected.

MemoryRoutes resolves an initiator's selected fabric interface through its owned L1 resource and worker tile. It requires exactly one enabled target attachment on that fabric for the addressed resource, rejecting ambiguity instead of silently selecting an alias. DRAM can return response-class packets but cannot initiate requests. Buffers retain their canonical resource identity across aliases. Operation range lengths, aligned addresses, buffer permissions, capacity overrides, unavailable attachments and deterministic paths are checked before any environment exists.

MemoryWireEnvelope admits zero-useful-byte headers only under the compiled packet purpose/layout, and validates physical bytes, class, flit bounds, plan identity and exact hop/lane against MemoryWirePlan. The existing v2 TransportEnvelope still rejects zero payload. Legacy DMA, Message serialization, link scheduling and memory service are unchanged.

## Independent evidence

The fixture tests/fixtures/wormhole_memory_packets.json contains literal boundary tables for 1, 15, 16, 31, 32, 33, 8191, 8192 and 8193 bytes, segment address offsets, and synthetic dual-fabric channel totals. It cites the pinned NoC overview, memory map and alignment documents listed in design.md. Their contents were re-read through the web tool. Digests are inherited from the planning-time verification; command-line re-download attempts in this part timed out and did not independently re-verify them.

For the reference geometry, 8193 useful bytes produce two independent segments: 8288 packet bytes posted, or 8352 for read/acknowledged write. Headers are 64 or 128 bytes respectively, and data padding is 31 bytes. A second independent table uses 16 physical bytes, 8 useful bytes, two header flits, 24-byte segments and 4-byte alignment, preventing reference-device constants from masquerading as general behavior.

Synthetic route checks independently specify physical destinations on both fabrics, wraps, same-router injection/ejection, canonical aliases and exact projected channel bytes. Projected channel bytes count every local/network hop but are named planned_channel_bytes: no launches have executed. Existing all-pairs Wormhole route oracles, v1/v2 transport regressions and legacy DMA/serialization tests remain in the full suite.

## Validation

- Focused packet/route/envelope tests: 11 tests passed, including malformed identities/layouts, duplicate operations, ambiguous aliases, role/address/permission failures and cross-plan envelope rejection.
- .venv/bin/python -m unittest discover -s simulator_detailed/tests: 150 tests passed, including the final alias test.
- Strict phase-2 Pyright, including memory_packets.py and memory_routes.py: 0 errors, 0 warnings, 0 informations.
- Scoped Ruff and git diff --check: passed.
- openspec validate wormhole-memory-transactions --strict --no-interactive: passed.

## Remaining limits

This is pure compilation. There is still no executable memory transport, service, capacity reservation, readiness/lease handling, local client, fence scheduling, lifecycle timing, CLI or silicon calibration. Local/fence operations generate no packet templates; their later scheduling/admission obligations remain parts 4–6. Profile connectivity support and timing/service settings still need integration before the final examples can execute.

The routing dependency check concerns network paths and causal request/response ordering only. It is not a proof of memory endpoint liveness. Part 3 must connect these records to a bounded shared transport and validate all envelopes before resource mutation. Automatic NIU splitting, VC_LINKED, inline/byte-enable/atomic/MMIO modes and bank-accurate memory service remain unsupported.
