# Version-2 torus transport

Version-2 replay executes configurable directed torus unicast, bounded causal
request/response fixtures and directed-link slowdowns. It supports canonical graph
input and an explicit transport binding over the Wormhole profile. This completes
the transport scope of `wormhole-dual-noc-routing`; full profile/DFG execution,
NIU transactions, memory/compute service and silicon timing calibration remain
unsupported. Profile inspection alone does not admit transport.

## Run the examples

From the repository root:

```bash
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/torus_small_v2.json
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/wormhole_transport_v2.json
```

Add `--output /tmp/torus-result.json` to either command to write the same JSON that
appears on stdout. Source paths resolve relative to the replay file. Dispatch
requires exactly `kind: topology_replay` and integer `schema_version: 1` or `2`;
result schemas retain the corresponding version. Boolean, floating-point, string,
missing or unknown versions are rejected. Results are output documents, not replay
inputs. Exit codes are **0** for complete, **1** for invalid, **2** for incomplete.
Invalid admission occurs before environment construction and leaves output files
untouched; incomplete runs still export their pending packets and resources.

| Example | Executing scope | Deterministic result |
| --- | --- | --- |
| `torus_small_v2.json` | 3x2 synthetic graph, shifted datelines, both axis wraps, 11-flit packet with one-flit lanes, directed wrap-link slowdown | 241 payload bytes, 352 packet bytes, 1760 channel bytes, drain at 57 ACI cycles |
| `wormhole_transport_v2.json` | Profile graph with 240 routers/480 network links, two selected endpoints per fabric, one request and one causal response per fabric | 148 payload bytes, 320 packet bytes, 4160 channel bytes, drain at 63.5 ACI cycles |

The Wormhole example inherits the profile's illustrative 72-worker mask (worker
row y=11 disabled), raw coordinate maps, physical memory inventory and source
evidence. Router/link health and endpoint fixture permissions are explicit
assumptions. Native clock, physical flit size and interface width reference profile
parameters. The ACI clock, payload/header approximation, stage decomposition,
capacity, arbitration and endpoint service are synthetic model settings. These
numbers demonstrate model execution; they are not device measurements. The full
grid remains transit-capable even at harvested workers, whose worker initiation
permissions remain restricted. Graph counts describe inventory; the runtime builds
only channels and router pipelines used by the admitted routes.

The sections below describe the seven implementation parts. Validation and
requirement mappings are recorded in
[the child delivery report](../../openspec/changes/archive/2026-09-20-wormhole-dual-noc-routing/delivery.md).

## What part 1 implements

`configs/schemas/torus_replay.py` introduces a separate, strict
`topology_replay` version 2. It rejects unknown fields, other versions, unsupported
traffic/hardware modes, invalid units, nonfinite timings and nonpositive capacities
or clocks. Nested records are frozen and collections are tuples. The schema has no
device-specific clock, width, geometry, capacity or service defaults.

| Configuration | Explicit contents and checks |
| --- | --- |
| Source | Tagged canonical graph or hardware profile; path resolved relative to the replay file |
| Binding | Fabric IDs, positive 2D torus policy, XY/YX order, datelines, availability evidence and endpoint allowlist |
| Endpoints | Request-source, request-sink, responder and response-sink roles; applicable local ports, bounded injection/response queues and service times |
| Hardware | ACI/native NoC clocks in Hz, physical flit bytes, usable payload bytes, wire/usable bits per native cycle |
| Stages | Native router RC/SA/transfer latency, transfer initiation interval and capacity; link launch interval, propagation, credit delay, lane/staging capacity and arbitration quantum |
| Traffic | Finite one-way request-class packets or request/response fixtures with explicit byte sizes and burst quanta; no independent or nested responses |
| Slowdowns | Fabric-qualified directed-link ID, unique failure ID, finite factor ≥1, nonoverlapping half-open intervals per target; adjacent intervals allowed |

For receiving endpoints, sink service is positive and explicitly names the ACI or
NoC timebase. A responder requires bounded descriptor storage and a declared response
service delay; zero response delay is permitted. An endpoint's injection capacity
applies independently to each admitted traffic class. Parsing these declarations
alone does not allocate queues or generate responses; the runtime does that.

Hardware quantities are either evidence-bearing literals or profile-parameter
references. A reference can carry an explicit override with value, reason and evidence.
For example, a sensitivity experiment can retain the source clock while changing
the effective clock:

```json
{
  "kind": "profile_parameter",
  "parameter": "ai_clock",
  "unit": "Hz",
  "override": {
    "value": 750000000,
    "reason": "Counterfactual clock for a sensitivity experiment",
    "evidence": {
      "status": "assumed",
      "description": "Experiment setting; not a measured device clock."
    }
  }
}
```

`PreparedTorusContract.prepare(config, source_document)` revalidates the configuration
and source, resolves references, retains original values/units/clock/evidence and
override provenance, and checks clock conversion and serialization bounds. Profile
fabric clocks must reference their declared profile clock; physical flit size must
reference a profile parameter. The supported unit conversion is
`bytes_per_cycle → bits_per_cycle` by exact multiplication by eight. Cycle-based
profile quantities must belong to the selected native clock domain.

For physical flit size `F` and usable width `W`, serialization is the integer ceiling
`(8*F + W - 1) // W` native cycles. Launch spacing must be at least that duration.
Converted positive times must remain finite and nonzero. Capacity one remains one;
preparation does not enlarge it to accommodate latency. `header_bytes` is format
metadata, consistent with the existing byte-flit approximation: it neither creates
hardware header flits nor implicitly subtracts from explicit payload capacity.

`PreparedTorusContract.load(path)` resolves and reads the source relative to the
configuration. Its `export()` reports `validation_stage: configuration_only`,
`can_execute: false`, and the pending topology, dependency and runtime admission
checks. No SimPy environment, router, DMA or memory service is constructed.

## Identity and record boundaries

Preparation normalizes unordered configuration tables, endpoint roles and evidence
citation order, and removes the source file path from configuration identity.
Canonical graph input uses the existing graph normalization. Profile input retains
the existing profile serialization rules, including list order, and its original
evidence. Moving a file or reordering normalized tables preserves the contract hash;
changing source contents, an effective setting or its provenance changes identity.

`EffectivePlanRecord` contains immutable source/configuration snapshots, resolved
quantities, graph and routes, with source/contract/plan hashes. `RouteRecord` checks
local path boundaries, router continuity, fabric/class consistency and increasing
declared ranks. `TransportEnvelope` adds plan, packet, lane, hop and sequence identity
without modifying legacy `Flit` fields or serialization. Request and response IDs
use distinct structural classes under their causal transfer ID.

`TorusReplayResult` defines version-2 packet, resource, mapping and trace records.
Its structural checks reconcile packet byte totals, count channel bytes only at
`link_launch`, enforce packet timestamp order and resource capacity conservation,
and forbid `complete` while a listed packet or resource remains pending. Delayed
credit returns and packet owners count as pending even after payload delivery.
Credit-return events require lane/token identity; physical-launch events require
lane, packet, sequence, launch factor and byte cost.

These records are **data contracts, not runtime admission tokens**. Structural
record checks alone do not prove route validity or exhaustive runtime accounting.
Part 1 tests manually construct record fixtures. Parts 2–3 add the actual topology
compiler and per-channel envelope checks described below; complete replay resource
accounting still requires the router and endpoint runtime.

## Validation and remaining scope

Part 1 was developed from `c1655ba`; its implementation, tests and this evidence are
in the commit titled `feat: add versioned torus transport contracts`. The existing
84-test baseline grows to **95 passing detailed tests**, including 11 new contract
tests. Strict Pyright reports **0 errors / 0 warnings**, scoped Ruff passes, and
OpenSpec strict validation and diff checks pass. Commands are recorded in the
[change delivery notes](../../openspec/changes/archive/2026-09-20-wormhole-dual-noc-routing/delivery.md).

The new tests cover version/field/unit admission, immutable round trips, role and
slowdown constraints, relative loading, evidence/override resolution, clock extremes,
normalization/hash changes, exact large integer byte metadata, route/envelope/trace
shape, result totals and credit-drain constraints. A deliberately non-torus graph
can pass configuration preparation, demonstrating that this stage does not claim
topology admission. At the part-1 checkpoint the CLI rejected version-2 input;
part 7 now dispatches admitted version-2 replay. The full suite retains profile gates, version-1 replay and the
legacy mesh timing/failure tests.

Tests use synthetic timings/capacities and the existing assumed Wormhole profile.
Inherited public-source evidence remains unchanged; this part neither fetches new
architecture evidence nor verifies the truth of caller-supplied citations. Requiring
a pinned citation is a provenance-format check. Profile worker selection remains
illustrative, memory capacity remains inventory, and passing tests establish no
silicon timing accuracy.

## Part 2: topology binding, routes and static resource order

`torus.py` now binds either the normalized profile inventory or a complete canonical
graph. Profile binding generates exactly one positive directed edge per axis and
router, retaining all 120 physical tiles, 240 fabric-qualified routers, 480 directed
links, 240 source attachments, the selected worker mask and memory aliases. A
harvested worker retains its router as transit but cannot be selected as an initiating
endpoint. Endpoint roles, local ports and source permissions remain an explicit
allowlist; memory endpoints cannot become independent request sources. Availability
defaults fill unknown profile fields, while source-disabled routers/links and
unavailable deterministic route edges fail before any runtime allocation.

The route compiler takes positive modular hops in configured dimension order. NoC0
uses raw XY and NoC1 raw YX; both fabric IDs remain explicit. Same-router paths have
only local channels. The `(9,11) → (1,1)` router path is four hops on NoC0 and eighteen
on NoC1. A small canonical torus test uses shifted datelines and a separately written
coordinate oracle; the profile test enumerates 28,800 ordered source/destination pairs
across the two 10x12 fabrics.

`dimension_dateline_v1` assigns request and response classes two modeled phases per
network edge. The dateline edge switches to phase 1, phase resets only at a dimension
turn, and ranks increase through both dimensions. Local injection/ejection channels
and packet owners are included in `torus_dependencies.py`; causal descriptor edges
lead into independently draining response injection resources without reversing the
request dependency. The resulting graph is checked with a topological sort and
declared ranks. This is a static policy check, not proof of runtime scheduling or
silicon VC behavior.

Binding and compilation alone do not run the router/endpoint runtime below. Hardware
VC encoding/buddy/priority modes, NIU packetization/transactions, memory/compute
execution, multicast/synchronization, tensor/checkpoint execution and hardware
calibration are not provided by these records.


## Part 3: bounded lanes and shared physical service

`virtual_channel.py` adds an isolated SimPy link kernel. `LinkContract.from_plan`
projects a compiled plan into immutable channel settings and packet templates. It
resolves network/local overrides and profile quantities through the existing
prepared contract. `envelope(packet, index)` produces metadata for the admitted
channel hop. Reservation revalidates every field against that template, including
plan hash, payload/padding, sequence bounds, endpoints, class, dateline, hop and
burst quantum, before changing any resource or trace. Directed slowdown timing is
described in part 6 below.

Network channels have four modeled lanes; local channels have two class lanes.
`VirtualChannelLink` exposes nonblocking operations: `try_reserve` returns a token
or `None`, `make_ready` publishes its already charged flit, `take` transfers a
received token into consumer-held storage, and `release` starts credit return.
Callers wait on `changed` while retaining their own charged upstream storage.
There is no pending SimPy put/get queue containing additional uncharged flits.
Tokens are checked by instance identity, so equal-looking IDs from another link
instance cannot authorize staging or credit release. Duplicate/out-of-order
flits and double release fail before state changes.

| Resource | Allocation and release boundary |
| --- | --- |
| Per-lane effective budget B | Reservation through delayed credit return; includes reserved, ready, serializing, propagating, received and consumer-held flits |
| Ready/receive lane FIFOs | References to the same tokens already charged to B; each therefore contains at most B flits |
| Shared wire staging S | Launch through completed propagation/arrival; counts serializing and propagating flits, across all lanes |
| Serializer | One active serialization; configured launch spacing may add a finite cooldown |
| Packet owner | HEAD reservation through TAIL launch; an idle partial packet remains pending even with all its current credits returned |

Staging is a **subset** of occupied lane tokens, with an additional shared bound;
its capacity does not add to B or create more lane credits. A ready flit awaits
staging in its lane storage, so producer callback order cannot monopolize shared
staging. The arbiter chooses ready lanes in round-robin order, bounded by the
smaller of configured link quantum and current packet quantum. An empty lane
releases unused quantum, and a new packet receives a fresh turn. Ownership never
interleaves packets within one lane; ownership in one lane does not block others.

The wire reserves staging before launch and waits only for its finite serialization
and launch gap. Propagation is separately scheduled and never waits for receiver
capacity: that storage was reserved before readiness. Receiver-held tokens continue
to consume B until the caller releases them. Arrival, consumption, credit return
and staging release are distinct boundaries. Serialization completion changes
state before another launch, including when their timestamps coincide.

At every observable boundary, `B = available + occupied + pending_returns`.
`resources()` returns immutable lane counters, peaks and owners. `inspect()` adds
individual token stages, shared staging occupancy/peak, serializer status, effective
native/ACI timing, and actual launched bytes. Lane/token events record reservation,
readiness, ownership, launch, serialization/propagation, arrival, take and return.
Only `link_launch` charges physical/payload bytes. `is_drained` includes delayed
credits, partial packet owners, wire work and cooldown, not just received bytes.

The dependency audit for this part is limited to the link kernel: packet owners
may wait for their lane budget; ready lane tokens may wait for shared staging and
wire service. Once wire work starts it has destination capacity and finite service,
so it cannot wait on another lane, owner or sink while holding the serializer.
Eventually publishing each reserved flit and releasing consumer-held storage remain
caller obligations. The router/endpoint resource order is described below and in
the child delivery report.

Fourteen focused tests check accounting after every SimPy event, bounded shared
staging, four-lane fairness with unequal quanta, idle/credit-starved lane bypass,
FIFO packets longer than capacity, early delivery before TAIL launch, foreign or
repeated tokens, exact payload/padded bytes, and idle-with-owner incompleteness.
Independent three-flit timelines use a 32-byte physical flit and 500 MHz ACI clock:

| Usable width / NoC clock / spacing / propagation | Launch times (ACI) | Arrival times (ACI) | Drain after 1 ACI sink service and 2 native credit cycles |
| --- | --- | --- | --- |
| 96 bits / 1 GHz / 4 native / 3 native | 0, 2, 4 | 3, 5, 7 | 9 |
| 256 bits / 250 MHz / 2 native / 3 native | 0, 4, 8 | 8, 12, 16 | 21 |

A separate capacity-one case launches at 0, 6, 12 ACI cycles where capacity three
permits 0, 1, 2; no minimum window is imposed or enlarged. One shared staging slot
also limits four ready lanes to one launch every 2.5 ACI cycles in its fixture.
These values are analytical checks of explicit synthetic settings, not silicon
measurements. The generic kernel reuses the existing integer flit-count helper;
legacy `Link`, `Router`, flit serialization and timing helpers remain unchanged.

Router transfer pipelines and multi-hop forwarding (part 4), causal response
production (part 5), and directed failure timing (part 6) build on this kernel.
Manually driving response-class lanes alone tests class separation. A compiled
plan still reports `can_execute: false` with pending slowdown/runtime admission;
only an admitted runtime produces a transport execution result. No full-profile,
NIU, memory, compute, detector or hardware timing support is implied.

## Part 4: cut-through router and one-way replay

`torus_transport.py` adds the first executing runtime around a compiled
`TorusPlan`. Part 4 established finite `one_way` request-class traffic. Every
route hop is bound to one shared `VirtualChannelLink`; repeated route use therefore
shares the physical serializer and lane credits. Part 5 extends the same runtime
with bounded causal responses, and part 6 adds directed slowdowns.

Injection, forwarding and ejection are separate bounded processes. Injection
packet slots use each admitted source endpoint's finite queue capacity. A
forwarder takes a received token from its input lane, validates the next hop
through the next link's plan-bound envelope, reserves downstream lane capacity,
then waits for a finite router transfer stage. It publishes the downstream token
and releases the input credit only after that transfer. A blocked downstream
reservation therefore holds a charged input token but no physical serializer or
router grant. HEAD/BODY/TAIL sequence and packet ownership checks remain in the
link kernel; every hop uses the same packet identity and flit index.

`RouterPipeline` is shared by all outputs of one fabric-qualified router. It has
explicit transfer latency, initiation spacing and finite capacity. Waiting output
identities are served FIFO, and downstream link arbitration remains round-robin
and quantum-bounded. With capacity greater than one, independent outputs overlap;
the transfer stage does not turn router latency into a serialized wire cost.
Transfer start/end events carry the canonical router and output channel. Pipeline
resources report active owners, occupancy peaks and pending output requests.

The runtime creates no alternate routes and does not infer missing links. Local
same-router paths still pass through their admitted local injection/ejection
channels and a finite router stage. Ejection applies the configured sink service
in its declared ACI or native timebase, records receipt byte counts, and releases
credits only after consumption. Completion requires every packet sink to finish,
all lane credits and packet owners to drain, all transfer stages to drain, and no
scheduled work to remain. A cycle limit or a SimPy idle state with held resources
returns `incomplete`; receiving all payload bytes alone is insufficient.

`TorusReplayResult` is produced in memory for this runtime with plan identity,
packet counts/timestamps, physical launch bytes, lane/pipeline resources and
transport trace events. The CLI dispatches this result after runtime admission;
the compiled-plan export alone still has pending slowdown/runtime checks and
does not establish execution. The result explicitly marks NIU,
memory, compute and silicon timing support as unavailable/unvalidated.

Focused transport checks cover same-router delivery, 10-flit packets longer than
the configured lane capacity, shared-route packet contention, a direct two-output
pipeline overlap, both fabrics on the assumed 10x12 profile, cycle-limit pending
state, delayed final credits after payload delivery, and explicit rejection of
invalid slowdown targets. The profile run is an assumed inventory/transport fixture,
not a
hardware execution or timing calibration.

## Part 5: bounded causal request/response fixtures

`TorusTransport` now admits the schema's finite `request_response` traffic. The
compiled request route is consumed first; after the responder's request sink has
received and serviced every flit, one bounded response descriptor is acquired and
the declared response service time runs. Exactly one response packet is then
created with the same transfer identity and `traffic_class="response"`, using the
compiled reverse route on the same fabric. Independent request and response packet
states preserve byte/flit accounting and causal identity without invoking DMA,
memory or compute services.

Each responder has a finite `response_descriptors` resource with an explicit
capacity and active request owners. Response injection has its own endpoint queue
ownership and uses the responder's injection channel/lane, while the originating
request source's queue remains independent. Response forwarding and the terminal
response sink are started as separate bounded processes, so request and response
classes share physical serializers through their plan-bound channels while retaining
separate lane ownership. A descriptor is released only after the response packet
has been admitted to its local injection channel.

Result accounting includes both request and response packets, actual launches on
both directions, response-ready events, descriptor occupancy/peaks and pending
owners. A request can be fully received while a slow responder service leaves its
response descriptor active; the result stays `incomplete` until response injection,
ejection, delayed credits, router stages and descriptors all drain. Descriptor
capacity-one and multi-request tests establish exactly-once response generation,
causal byte totals, same-fabric reverse paths and bounded endpoint storage. This is
transport-only behavior: NIU packetization, memory transactions, compute execution,
multicast and silicon timing remain outside the supported scope.

## Part 6: directed slowdown and reconstructable timing traces

Slowdown targets are resolved before `SimPy` resources are created. Each target
must be a fabric-qualified, enabled directed inter-router link in the compiled
canonical graph; local channels, missing links and disabled links fail admission.
The schema's finite factor and non-overlap checks remain in force, including
adjacent intervals. The legacy paired-link/router-wide failure path is unchanged.

At each physical `link_launch`, the target schedule is evaluated as a half-open
interval `[start,end)`. The launch records its effective factor and failure ID;
serialization duration, launch spacing and propagation duration are all multiplied
by that snapshot. A flit already launched retains its snapshot after recovery, and
untargeted channels/fabrics retain factor `1`. Failure start/end events are emitted
on the canonical network channel. This keeps route identities and byte accounting
unchanged while making timing effects explicit.

Propagation arrivals are ordered per modeled lane. If a recovered flit would arrive
before an earlier slowed flit, the link retains the charged staging token until the
earlier arrival and emits `arrival_order_wait`; the lane's FIFO order is preserved
without granting a second physical serializer. `link_launch`, serialization,
propagation, arrival-order-wait and failure events contain ACI timestamps and
durations, so launch, arrival and drain timelines can be reconstructed without
adding overlapping stages together.

Focused tests cover half-open start/end boundaries, recovery snapshots, a slowed
wrap link, disabled/local target rejection, long propagation with arrival-order
waiting, both assumed fabrics, and the unchanged legacy slowdown fixture. Factors,
intervals, clocks, widths and capacities remain configurable synthetic values; this
is a timing model and analytical invariant check, not silicon calibration. Full NIU
packetization, memory service, compute/DFG execution, multicast and hardware
VC/buddy/priority behavior remain outside this child.

## Part 7: admission and consumer boundaries

`replay_topology.run_replay` dispatches exact versions to the existing version-1
runtime or the version-2 compiler/runtime. Plain profile inspection retains
unresolved connectivity and permissions. The hardware support manifest separately
reports the available version-2 binder and modeled transport; its full-workload
`can_execute` gate remains false. Memory capacities remain inventory.

Existing detailed predictor/encoder guards accept verified legacy mesh objects
and reject version-2 configurations, results and trace rows before feature
construction. The predictor's 7-D features, encoder's 4-D features, row ordering,
checkpoints and separate top-level RL contracts are unchanged. Dependency-free
boundary tests run here; Torch/PyG tensor and checkpoint execution is unvalidated
when those optional dependencies are unavailable.
