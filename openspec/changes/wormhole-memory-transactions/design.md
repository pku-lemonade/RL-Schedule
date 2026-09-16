## Context

See `proposal.md` for motivation and `specs/wormhole-memory-transactions/spec.md` for MT-D01..10. This is umbrella child 4, following completed child `wormhole-dual-noc-routing` at `a3cc2a4`. The exploration baseline has 134 passing detailed tests, strict Pyright coverage, and executable topology replay versions 1/2; those are inherited results, not memory-runtime validation. No memory implementation exists at planning time.

`NOC_ARCHITECTURE` was not found in the checkout or matching available Git history. The architecture comparison therefore uses committed child designs/delivery evidence and executable code, without inventing missing historical decisions.

| Area | Current behavior | Required extension / compatibility boundary |
|---|---|---|
| `TorusPlan`, `TorusTransport` | Finite predeclared positive-payload packets; request consumption and a fixed delay trigger a predefined response | Admitted addressed operations drive packet availability and memory-dependent delivery; retain v2 fixture behavior |
| `TransportEnvelope`, `LinkContract` | Positive logical payload per flit; packet templates and validation tied to `TorusPlan` | Separate wire layout can include zero-payload headers; share service internals without weakening the v2 schema |
| `VirtualChannelLink`, `RouterPipeline` | Bounded credits, packet ownership, class/dateline routing, one physical serializer | Reuse for every memory packet; extend endpoint/memory wait analysis |
| `DMAEndpoint`, `NMCChannel`, `DMACommandCoordinator` | Configurable issue/datapaths, dual-side rendezvous, single-side control traffic, endpoint-local completion; legacy DMA RX uses an unbounded store | Explicit lifecycle adapter preserves these semantics; do not import that store into the new bounded runtime |
| `ScratchpadMemory` | One free-byte container, anonymous allocation/release with delay, no address/producer ownership; excess release can wait indefinitely | Opt-in exclusive handle adapter reuses the same container, validates ownership, and prevents double accounting; unbound legacy behavior remains compatible |
| Profile/graph memory resources | Stable L1/DRAM identity, capacities and alias references | Construct one capacity/service owner per admitted backing resource |
| `MemoryControllerConfig` | Parsed aggregate bandwidth/atomic fields; no executable controller service | Use an explicit new service policy; do not treat dormant fields as implementation or enable atomics from them |
| `Task`, `LSU`, `TPU` | LOAD/STORE are local scratchpad/LSU actions, FC remains absent, some costs floor sub-quantum work | No DFG or compute migration here; local service clients provide the boundary needed by child 5 |

## Goals / Non-Goals

**Goals:** An executable scheduling/cost model for finite, contiguous, race-free byte transfers; explicit producer readiness and endpoint observations; finite storage and shared memory/network service; small reviewable implementation commits.

**Non-Goals:** Tensor values, arbitrary firmware or registers, exact initiator counters, automatic NIU splitting/VC linking, the full legal alignment matrix, byte-enable/inline/atomic operations, physical L1 banks/ports, DRAM channels/banks/commands/refresh, host/PCIe/Ethernet transfers, multi-ASIC execution, new memory-failure policies, or compute/DFG execution. Existing directed link slowdown may be reused; no unverified memory timing calibration is implied.

## Decisions

### 1. Separate versioned memory contract and pre-runtime admission

Add `configs/schemas/memory_replay.py`, `memory_plan.py`, and `memory_records.py`. Use exact `kind=memory_replay`, strict integer `schema_version=1`, and model revision `addressed_memory_v1`; do not overload topology replay v2 or relax its positive-payload contract. `MemoryPlan.compile` must validate the entire finite workload before creating a SimPy environment.

The input consists of:

- Tagged canonical graph or hardware profile source, explicit fabric/availability/routing settings, selected initiator and memory attachment roles, and inherited link/router timing settings. Source paths resolve relative to the replay file; content and effective settings, not absolute paths, determine plan identity.
- One service declaration per used backing resource: `aggregate_shared_rw_v1`, native clock, read/write service granularity, per-chunk latency, shared bytes per native cycle, finite waiting slots, and maximum chunk bytes. Structural capacities come from the canonical graph/profile; any profile override must retain its reason and original evidence.
- Packet settings: physical bytes `F`, useful data capacity `D`, header flits `H`, maximum segment payload `P`, and address alignment `A`. Require positive values, `D <= F`, and segment/data chunk boundaries compatible with the admitted alignment and memory granularity. All timing values are finite and units explicit.
- Initiator issue latency and finite outstanding segment descriptors, shared across its selected fabric interfaces; bounded endpoint request/response descriptors and TX/RX staging, with explicit finite control-handling costs. The initial shared issue policy is a model choice, not register-accurate scheduling of hardware initiators.
- Named buffers with resource-relative base, size, permissions, initialization state, and named operation producers/consumers; a finite list of operations/dependencies/fences, start times and horizon.

Operation kinds are `read`, `write_posted`, `write_acknowledged`, `local_read`, `local_write`, and `fence`. A network operation names an enabled worker initiator, one fabric, its local L1 attachment/buffer, and a target L1/DRAM attachment/buffer. Both attachments must expose the addressed resources. Remote DRAM-to-DRAM copying, device initiators on DRAM aliases, and arbitrary Ethernet/PCIe initiation are rejected. A local access names a local client bound to that L1 resource and incurs memory service without fabric traffic; it is a compute-cost integration point, not an implemented compute engine.

Alternative considered: expand legacy `Message` and `ArchConfig` to infer memory behavior from endpoint types. Rejected because their command/completion semantics differ, and inventory parsing must not silently enable full profile execution.

### 2. Strict supported alignment and independent request segmentation

The initial `aligned_contiguous_v1` subset requires both source/destination addresses aligned to `A`; the Wormhole reference setting is 32 bytes. Positive lengths need not be multiples of that alignment. Range arithmetic is checked before any mutation; accesses cannot cross the selected buffer/resource or reach registers. Generic configurations can use other valid alignments and widths. A Wormhole parameter override is retained and explicitly marked as deviating from the pinned reference when applicable.

The Wormhole reference packet settings are `F=D=32`, `H=1`, `P=8192`. These belong in evidence-backed configuration, not runtime constants. Each logical transfer of size `N` becomes `k=ceil(N/P)` independent, unlinked requests with lengths `s_i=min(P, N-i*P)` and offset addresses. This is explicit software segmentation, **not** the NIU's automatic splitting or VC_LINKED behavior. Segment issue consumes the configured descriptor pool; the parent operation itself is finite control metadata rather than an extra packet buffer. Parent acceptance is first-segment acceptance, with every segment's acceptance separately recorded.

| Packet purpose | Class | Useful data | Physical flits |
|---|---|---:|---:|
| Read request | request | 0 | H |
| Read response | response | s_i | H + ceil(s_i/D) |
| Normal write request | request | s_i | H + ceil(s_i/D) |
| Write acknowledgement | response | 0 | H |

Each wire flit owns its full `F` bytes of storage/service; headers have zero useful data. For `H=1`, a header-only packet's sole flit is both head and tail; for larger generic `H`, only its first/last flits carry those respective boundaries. Data flits preserve exact useful lengths including the final partial flit. The header models metadata cost, not serialized register fields or a bit-accurate header encoding.

Total packet bytes are `F * (k*H + sum ceil(s_i/D))` for posted writes, and `F * (2*k*H + sum ceil(s_i/D))` for reads/acknowledged writes. Channel bytes additionally count actual launches on every local and network channel. Memory-service bytes are another quantity: source reads plus destination writes rounded under the service policy, without header/padding being treated as data.

For the reference configuration, one byte costs 64 packet bytes posted or 96 read/acknowledged; 8,193 bytes costs 8,288 or 8,352 respectively. Independent test tables cover 1, 15, 16, 31, 32, 33, 8191, 8192, 8193 and a second generic configuration. Do not derive the expected table with production packetizer helpers.

Alternative considered: encode headers as dummy positive data or add a fixed delay. Rejected because both corrupt byte accounting and omit actual finite-buffer/link contention.

### 3. Share transport mechanics, preserve old wrappers

Extract a small typed internal packet/service contract, provisionally `packet_transport.py`, from `LinkContract` and `TorusTransport`. Share pure graph binding/routing helpers from `torus.py`; do not fabricate a v2 traffic list to bypass validation. Keep `TorusPlan`, `TransportEnvelope`, v2 records/serialization, and `TorusTransport.run` as the compatible public wrapper.

The new memory wire envelope carries immutable plan identity, operation/segment/purpose identity, packet layout, flit index, useful bytes (possibly zero), physical bytes, and the same admitted channel/lane/rank path. Use a collision-free canonical mapping to any existing internal packet identity; do not concatenate ambiguous user IDs. All layout/path validation precedes credit/resource mutation. Old envelopes remain validated against old templates.

The reusable engine accepts finite precompiled packet definitions and runtime readiness/delivery hooks supplied by simulator code. JSON cannot supply callbacks. Packets are injected only when their bounded producer staging is ready; delivery notifies the addressed memory consumer and returns credits after the modeled handoff/service. All operations run in one environment with one shared link/router object per physical resource. No fresh network per transaction, manual replay-result timestamp shifting, or new unbounded full-packet staging is allowed.

The existing dateline/class routing proof still applies to network edges. It does not by itself prove memory endpoint liveness; decision 6 extends the wait-resource audit. v1 hashes/timing and v2 routes/bytes/timing/traces must remain stable under extraction.

Alternative considered: fork the entire transport runtime for memory. Rejected because fairness, slowdown, credit, and route validation would drift in two copies.

### 4. One buffer-capacity owner and explicit producer readiness

`memory_resources.py` owns resource objects keyed by canonical physical `resource_id`, never attachment ID. Each owns one free-capacity container, nonoverlapping buffer reservations, a handle ledger, readiness metadata, and one service scheduler. Aliases only refer to that object. Initial replay buffers are reserved once during setup; their total sizes and bounds are checked at compile time. They remain allocated for the finite replay, and are released exactly once during successful teardown after all users drain. Snapshot readiness/usage before teardown and distinguish persistent allocations from pending work in incomplete results. Dynamic circular-buffer recycling is deferred to child 5.

The runtime buffer API uses owner-bound handles and exact extents. Allocation does not imply initialization; release cannot precede the last access, exceed the reservation, or be repeated. Read/write ranges use explicit initial/producer versions. At compilation, resolve all aliases and reject unordered overlapping accesses if either writes; a valid dependency must wait for the applicable memory access to finish, not merely descriptor acceptance or posted local completion. Concurrent reads and disjoint ranges may overlap. This rejects unsupported data races rather than silently serializing them or inventing tensor values.

Source readiness is checked before issue. Source-read completion releases the source access lease; the source reservation remains owned until its declared lifetime ends. Destination memory effects occur as chunks receive service; the named operation's destination-ready event publishes after all its segments finish writing. That event is a conservative scheduling boundary, **not** a claim that the whole range was atomically written on silicon. Version metadata prevents a late read from observing a producer that has already been overwritten by an insufficiently ordered write.

For `ScratchpadMemory`, add an explicit exclusive adapter binding only to an empty, idle scratchpad in the same environment with matching capacity. The adapter uses its existing container and allocation/release delay exactly once. While bound, reject anonymous external allocation/release; unbound legacy calls retain their original behavior. A handle-aware internal path permits the adapter's own calls. Reject a second binding and detach only after its reservations and pending operations drain. No parallel shadow byte budget and no extra LSU or network charge are introduced by this adapter.

Alternative considered: count every fabric's L1/DRAM aliases separately or allocate once in `Core.spm` and again in the memory layer. Rejected because it multiplies capacity or double-charges real storage.

### 5. Conservative shared service policy

Initial policy `aggregate_shared_rw_v1` is a fair, nonpreemptive server per backing resource, with a bounded FIFO waiting queue and one active service slot. Ready clients enter in deterministic arrival order with a stable tie-break. Backpressured submissions hold no memory grant and no uncharged payload. Each chunk charges touched service granules of size `G`; addressed traffic is split so interior boundaries do not partially reuse a granule. For this initial subset, require `A` and resource chunk size `M` to be multiples of `G`, `D` to be a multiple of each accessed resource's `M`, and `P` to be a multiple of both `A` and `D`. A network data flit reserves one full staging slot and performs its one or more size-`M` memory chunks without coalescing other flits; a local client also splits work at `M`. Final partial chunks charge their touched granules while readiness tracks only useful bytes. These admission rules prevent a configured chunk from requiring more staging than a capacity-one endpoint can supply.

For chunk offset `a` and useful length `n`, serviced bytes are `G*ceil(((a mod G)+n)/G)`. Native duration is configured per-chunk latency plus serviced bytes divided by the single shared bytes-per-cycle rate. Convert once using the native clock and the existing ACI reference clock. A finite positive access always has positive service. Read and write jobs both consume this same interval budget; no separate unlimited bandwidth pools are created for aliases, fabrics, or directions. Chunk size/latency policy is part of the effective plan because it changes performance.

Read flow: header request -> target memory read -> bounded response data staging -> response network -> local memory write -> read ready. Write flow: local memory read -> bounded request staging -> request network -> target memory write -> optional ack. Only explicit memory endpoint control costs and this actual memory service apply: do not additionally charge the v2 fixture's synthetic sink/response delay or legacy DMA payload service. Local clients enter the same resource queue, allowing tests of local/fabric contention without claiming DFG integration. Narrow writes consume the selected rounded aggregate write service; internal L1 read-modify-write micro-operations are not reproduced and remain an explicit approximation.

The initial DRAM abstraction combines both directions and all aliases at group level. It does not predict channel address imbalance or row behavior. L1 uses an explicitly assumed aggregate rate; the documented per-NoC bandwidth must not be relabeled as all-client L1 bandwidth. Changing the aggregate rate/granularity/latency requires configuration/evidence, not code changes. Initial service settings are assumed unless a matching measurement supports them.

Alternative considered: one fixed remote delay, independent read/write budgets, or a full DRAM/banked L1 model now. The first two miss the shared bottleneck; the latter exceeds this child's useful abstraction and evidence.

### 6. Bounded endpoint execution and extended liveness argument

`memory_runtime.py` composes the reusable transport and resource objects. Packet routes/layouts are finite metadata; resident data/control work must occupy a named bound. Distinguish initiator segment descriptors, responder descriptors, per-class TX/RX staging, service waiting entries, one active memory-service slot, and network/router credits. Do not hide data in one coroutine per flit or an unbounded store. Capacity-one tests and payloads larger than all staging buffers are required.

The critical ordering of resource acquisition is:

1. Check readiness/dependencies and reserve valid buffers before issuing traffic. Waiting on an unready producer holds no descriptor, packet lane, or memory grant.
2. For a producer read, reserve counted output staging before entering memory service. Memory service then performs only finite local work, releases its grant, and publishes the staged chunk. Network waits can retain that staging slot, but never the shared memory grant.
3. A destination consumes arrived data into bounded RX staging or retains its existing charged network token while awaiting service; it must not take data into an uncounted variable/queue. Its write job requires no response TX staging or other network resource while holding the memory grant.
4. Requests that need a response reserve bounded responder state. Read data and ack production use response-class queues/resources independent of request ingress. Release responder state after all response data/control is handed off, not after an upstream request slot becomes available. Issue descriptors retire at posted handoff or required read/ack completion according to operation mode.
5. Response sinks write into already reserved destination capacity and do not need an initiator/request slot. An acknowledgement sink performs finite control handling without memory data service. No response generates a new request.

This adds a one-way protocol dependency from request completion to response creation. Response progress can require memory service, but an occupied memory grant never waits on network progress; ready admitted jobs have finite service and fair scheduling. Independent response-class network lanes and drain-capable sinks prevent a request queue from retaining the resources needed to retire it. Source/destination buffer dependencies are an admitted DAG, and no partial access-lease acquisition is held while waiting for another buffer.

This is an obligation to audit, not a completed proof by documentation. Before enabling the runtime, enumerate actual waits/owners in code and include request RX saturation, response staging saturation, mixed reads/writes/local clients, both fabrics/wraps, and slow shared memory in stress tests. If implementation introduces a response-to-request or grant-to-network wait, change it before claiming bounded drain.

Alternative considered: pre-read entire transfers into an unlimited list or hold a memory port during network injection. Rejected for hiding capacity or creating network/memory cycles.

### 7. Completion and explicit ordering policy

Keep per-segment events and aggregate operation events; expose absent facts as null/unsupported, not copied timestamps.

| Operation | Observable completion | Destination readiness | Descriptor retirement |
|---|---|---|---|
| Posted write | Final local request handoff over all segments | After all target write service, possibly later | Per-segment request handoff |
| Acknowledged write | Receipt of every segment ack | Before its corresponding ack is generated | Per-segment ack receipt |
| Read | Every response byte stored in local destination | Same final local write boundary | Per-segment local response write completion |
| Local read/write | Final local service | Local write publishes its destination; read consumes existing version | Local service admission/completion policy |

Submission, first descriptor acceptance, each segment acceptance, source-read completion, final request handoff, response-wire receipt, memory visibility, operation completion, and overall drain are distinct records. Some can share a time but must not be inferred equivalent. A read request's handoff can precede the remote source read. An ack is generated only after its segment's target writes; it costs real response header traffic.

Operations are an explicit dependency DAG under `explicit_dependencies_v1`. List order alone gives no global memory order. Ordinary completion dependencies and fences operate within one canonical initiator, including its interfaces on both fabrics. Resource-local consumers may depend on a producer's destination-ready event if they are bound to that resource; this represents local readiness and does not send a zero-cost remote notification. Cross-worker sender notification requires an actual supported response or later synchronization protocol.

Part 6 concretizes the input fields as `depends_on` (ordinary completion), `destination_ready_after` (the producer writes this client's canonical local L1), and optional `source_version` (`initial` or a named `producer`). An omitted source version selects the buffer's declared producer, otherwise its initialized version; it never selects an implicit latest writer. Producer selection does not add an ordering edge. Admission proves that the selected producer covers the entire read range and that no intervening overlapping writer can invalidate that version. Physical conflicts require access-finish event ancestry, so posted completion can authorize source reuse but cannot prove a remote target effect. Destination-ready waits conservatively include all producer segments, even for a subset read.

Local reads have only a source range; local writes have only a destination range. They use one worker's L1 and perform no copy, compute or network operation. This replaces the earlier unexecuted declaration that required both ranges. Runtime settings expose `local_capacity_operations` (default 1 per canonical L1) and `local_control_aci_cycles` (default 0), both included in effective configuration/evidence. Each occupied local-client slot admits one service chunk at a time, bounding staging by the resource's configured chunk size. Local clients share the existing aggregate server and use no network issue/responder slots. Lifecycle and descriptor traces use an absent segment index for local operations and fences.

A fence lists earlier operation IDs of one initiator and selected fabrics; compile time freezes that set and rejects forward/cyclic references, foreign initiators, or mismatched fabrics. `local_handoff` waits for all selected network requests to be injected. `remote_completion` waits for completed reads/acknowledged writes and rejects posted writes. Fences themselves consume no wire bytes under this declared scheduling policy. Later operations are gated only by explicit dependencies on that fence. No readback barrier for posted writes, response ordering shortcut, global serialization, register counter emulation, or VC_LINKED promise is implemented.

The optional `fence_fabrics` field explicitly scopes the selected IDs; if omitted, compile time freezes the union of those operations' fabrics. It does not select any additional operations or absorb later work. Effective result ordering records expose the resolved source version, canonical initiator, exact event waits, fabric scope and policy. Ordered runtime results use `addressed_memory_ordered_v1`; the previous result label remains recognized for older records.

Replay completion is stricter than any operation's observable completion: all target effects, network packets, endpoint jobs, memory queues/grants, owners and delayed credits must drain, then buffers are safely released. A horizon or idle-with-pending result is incomplete with pending identities/occupancies; it must never report success based only on locally completed posted writes.

Alternative considered: treat destination service completion as sender completion for every operation. Rejected because simulator omniscience would supply ordering the issuer cannot observe.

### 8. Legacy adapters and downstream boundaries

`memory_adapters.py` provides a legacy DMA lifecycle wrapper that delegates to existing endpoint/channel operations and their coordinator. It exposes neutral submission/acceptance/handoff/completion fields with `execution_policy=legacy_dma`; remote addressed-memory visibility remains unavailable. It must not add memory service, re-packetize messages, or enqueue a second descriptor. Test direct versus adapted GM/DDR read/write, both fabrics, both command modes, issue-sharing settings, finite descriptor slots, configured packet sizes, and fault recovery. FIXPATH and local-memory DMA remain unsupported.

This preserves legacy DMA through explicit adapters; it does **not** migrate legacy DMA onto the new memory/torus runtime or make old GM/DDR endpoints autonomous Wormhole memory controllers. The scratchpad adapter from decision 4 is a separate opt-in capacity bridge, not automatic rebinding of running `Core` instances. Child 5 decides how DFG LOAD/STORE and compute clients use these boundaries without duplicate costs.

`topology_compatibility.py` and detailed predictor/encoder boundaries must reject new replay/result inputs before importing unavailable tensor dependencies. Existing 7-D/4-D contracts, checkpoints, RL observation/action spaces, top-level simulator and datasets are unchanged. Record unavailable Torch/PyG execution checks as unavailable rather than passing them through mocks and claiming real inference.

### 9. CLI, tracing, and evidence

Add `replay_memory.py` with `.venv/bin/python -m simulator_detailed.replay_memory --replay <file> [--output <file>]`. Exit 0 for drained success, 1 for invalid input, 2 for valid incomplete runs. Parse/compile before replacing output, using the existing CLI conventions. Keep `replay_topology.py` dispatch unchanged. Generic and profile examples exercise local contention, both fabrics, DRAM aliases, segmentation, all network operation modes, and a legal fence.

`MemoryReplayResult` contains source/effective-plan identities, policy/evidence, operation and segment lifecycles, packet layouts/routes, transport events, buffer/readiness events, memory queue/service events, capacity snapshots, pending work, and completion status. Include logical operation bytes once, header bytes, data-padding bytes, packet bytes, actual channel bytes, useful memory read/write bytes, and rounded serviced bytes. Service records identify native/ACI units, client, physical resource, address range, direction, rounded cost, queue/active occupancy, and start/end. Reconcile every count/timestamp to executed events, not just the compiled plan.

Update capability reporting only as stages become executable: config parsing, plan compilation, wire transport, addressed memory service, and full workload execution are separate claims. The general full-profile execution gate remains closed because compute/DFG support is still absent. Assumed memory service and hardware-unvalidated timing remain visible in successful results.

Validation layers:

1. Independent source-backed packet/address fixtures and graph aliases; explicit invalid modes/alignment/identity mutations.
2. Independent arithmetic/timeline oracles for two generic widths/clocks, rounded source and destination costs, and reference payload boundaries. These validate the chosen model, not silicon.
3. Resource conservation and behavior under alias/local/dual-fabric contention, saturation, long packets, slow service, legal fences, delayed credits, and timeouts.
4. Exact legacy direct/adapter comparisons and prior v1/v2 regression fixtures; strict types, scoped lint, CLI parsing/output behavior.
5. Requirement-to-evidence delivery report with limitations. No hardware run is available for matched validation; no new fitting or accuracy percentage is asserted. Cross-implementation/hardware comparison tooling remains child 6.

### 10. Pinned public evidence and configuration status

Primary source revision: `tenstorrent/tt-isa-documentation@acaf010519f4fdd323df5077e45b8695f70e4279`, WormholeB0. Sources read and raw bytes SHA-256 verified on 2026-09-16; no downloaded source copy is added to the repository. Existing profile and routing evidence remains separately inherited.

| Source | Use and limit | SHA-256 |
|---|---|---|
| [NoC overview](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/README.md) | Flit/header rules and DRAM alias roles; modeled lanes still have their own abstraction policy | `3e273cd45beb4ff1e589c676a0a41d3eb0746d6f83ac2d7cc3e4bc8925201c0c` |
| [NoC memory map](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/MemoryMap.md) | Normal read/write packet forms and unsupported variants; no MMIO implementation | `bd48815f846f85d4cf72dab43323206306b7ab32a5033338bf06146e7648a9a2` |
| [NoC counters](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/Counters.md) | Distinguish acceptance, outgoing data, ack and read-return effects; no counter/register or automatic-split fidelity | `edc253ebb37c33629bce33186c650632f90a576e782f78eb708c1ce353df5d01` |
| [NoC alignment](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/Alignment.md) | Justifies a valid stricter aligned-address subset; do not claim all documented congruence cases | `7b898f80a5e6df75046c53ad22bba3283e67ec56e390b13644f650b5e6a43875` |
| [NoC ordering](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/Ordering.md) | Avoid implicit strong order and whole-range atomicity; supported explicit dependency policy remains a model choice | `a3446b2bb51961838855e51b27e6e43c69fdc3ad2ff9a777f37c442fac884cdc` |
| [DRAM tile](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/DRAMTile/README.md) | Shared resource/address organization and combined bandwidth constraints; group aggregate cannot reproduce channel imbalance | `977434c7e01f3942537d0a6a85d4bc4da9b92ab29a9b918c8b70f0de4086440e` |
| [Tensix L1](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/L1.md) | Shared local storage and bank/port limitations; aggregate L1 rate, arbitration and narrow-write cost remain approximations | `276d09a25442beba658a81ff19a5b82462c20b744ddf582130c69680c4257335` |

Public architecture facts support structural and semantic checks. Endpoint capacities, issue/service latency, shared aggregate rates, chunk policy, fairness, and readiness abstraction require explicit assumptions unless separately supported by matching evidence. Published peak bandwidth is a constraint/reference, not a calibration dataset for this model's measured latency.

## Risks / Trade-offs

- **Transport extraction changes stable timing** -> Preserve public v2 contracts and run exact existing fixtures after each extraction; do not combine extraction with memory policy changes in one commit.
- **A nominally finite design hides queued data in generators** -> Account for every retained chunk and descriptor, audit waits/owners, and test packets larger than all buffers.
- **Alias state or scratchpad capacity duplicates** -> Canonical resource keys, exclusive binding, one ledger/container and direct conservation assertions.
- **Aggregate service overstates contention or misses bank/channel effects** -> Name the policy and assumed parameters in every result; use independent-resource controls and reserve refined policies for later changes.
- **Range readiness appears to promise atomic writes or remote synchronization** -> Publish local scheduling events with explicit ownership, reject unordered conflicts and impossible sender-side fences, and keep byte values unsupported.
- **Too much adapter machinery for a finite replay** -> Keep adapters narrow and explicit; preserve old execution internally and defer full DFG/circular-buffer migration.
- **Old dormant fields imply features such as atomics** -> Require a separately implemented capability rather than inferring support from parsed data.
- **OpenSpec CLI ignores malformed existing design rules** -> The current config emits `Rules for design must be an array of strings`; manually apply the read design constraints. Do not repair unrelated configuration within this planning change.

## Migration Plan

Implement the eight independently validated parts in `tasks.md`, committing each completed part before continuing. New behavior is opt-in throughout: contracts, packet compilation, shared transport extraction, memory resources, transaction runtime, ordering/local clients, legacy adapters, then CLI/evidence. Preserve existing examples and traces; add new examples under their own kind/version.

Every part records its focused tests/type/lint and any changed fidelity claims. The final part runs the complete detailed suite, strict Pyright, scoped Ruff, generic/Wormhole/legacy CLI examples, and strict OpenSpec validation. A failed part is corrected before proceeding. Rollback is by reverting the relevant opt-in commit; no configuration migration, history rewrite, backup, push, spec sync, or archive is part of this change. Umbrella completion is not inferred from planning files; child 5 begins only after this child's implementation and evidence are delivered.
