## Context

See `proposal.md` for motivation and scope. The implementation baseline is `ff37a79`: 84 detailed tests pass, strict Pyright is clean, and the previous child documents its synthetic replay and legacy parity evidence in `simulator_detailed/docs/topology.md`.

`Topology` already separates physical tiles, fabric coordinates, directed edges, attachments and resources. Profile projection deliberately leaves connectivity and endpoint permission unresolved. `ExplicitRouting` admits only route sets with acyclic combined channel dependencies. `Router` has one forwarder per input and can hold an output grant while waiting for downstream credit. `Link` has one receive FIFO and one physical send queue. These last two behaviors are suitable for the retained acyclic path, but adding VC identifiers alone would leave head-of-line blocking and shared-resource cycles in a torus.

This design is required because the change introduces a new resource policy, runtime binding, and versioned transport contract across several modules. The repo's OpenSpec context also describes the separate top-level simulator; the detailed implementation and completed child evidence determine the baseline here.

## Goals / Non-Goals

**Goals:** generate connectivity once, route through canonical identities, and execute finite byte traffic with a resource dependency argument that matches the actual scheduler and endpoints. Generic policies own dimensions, coordinates, capacities and timing; the Wormhole binding supplies profile data and documented policy choices.

**Non-Goals:** emulate the hardware VC encoding/arbitration exactly, interpret MMIO/NIU commands, charge real request/header/acknowledgement packet formats, model memory contents/controllers or compute, or open heterogeneous transport to existing trained models. The current `FlitConfig.header_bytes` is not a separate header-flit packetizer. A 32-byte physical format in this child remains a byte-transport approximation; child 4 owns packet/transaction costs.

## Decisions

### 1. Pin architecture facts separately from model choices

Use the existing ISA-documentation revision `acaf010519f4fdd323df5077e45b8695f70e4279` and descriptor revision `558637320489ee8ccccea5f2b3a5fcd1e1cfd58f`.

| Evidence | Architectural constraint | Use in this child |
| --- | --- | --- |
| [Coordinates.md](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/Coordinates.md), D4 | Raw fabric coordinates identify the same physical tiles through different mappings | Inherit the independently checked profile fixture: NoC0 `(x,y)`, NoC1 `(9-x,11-y)` for the 10x12 layout; derive extents from data |
| [RoutingPaths.md](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/RoutingPaths.md), D6 | NoC0 unicast moves physically right then down; NoC1 moves up then left; both wrap. Forwarding is cut-through and packets can contend on shared links | Independent physical-coordinate route oracle; translated coordinates select increasing XY or YX hops |
| [NoC/README.md](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/README.md), D5 | Hardware VC numbers contain dateline, class and buddy fields; requests and responses have separate classes. Listed link throughput and hop latency have specific boundaries | Motivate class/dateline separation. Preserve public performance values as references, not calibrated RC/SA/ST settings |

On 2026-09-15 D5/D6 were readable through the web tool. D6 raw bytes were additionally fetched and hashed as `656f6fb36b74de0ac30fcd0b76c5c028a6760eb8879c70dd647f1da57b9c006b`. D4 raw retrieval timed out in this exploration; its coordinate evidence and hash `328f014798fe3cec46ec2c9555a8d843074b7eb62eb4de996e6d85a0f7ec9174` are inherited from child 1, not claimed as freshly verified. Do not change the earlier evidence record merely to make it look newly fetched.

The exact dateline placement rule below, four modeled network lanes, capacities, fair arbitration, endpoint fixtures and stage decomposition are **simulator choices**. This evidence does not establish equality with the hardware's full virtual-channel implementation. Static buddy selection, alternate request classes, VC linking, priorities and broadcast reservations are unsupported modes, not silently ignored fields.

### 2. Bind an explicit transport contract to the canonical graph

Add `topology_replay` version 2, separately parsed from version 1. Its source is tagged as either a complete canonical torus graph (`graph_path`) or a hardware profile (`profile_path`) plus a transport binding. Paths resolve relative to the replay document. The effective plan includes source content identity, resolved graph, normalized settings, policy revision, routes and traffic; file location does not determine its hash.

The binding declares fabric IDs, increasing raw-coordinate torus policy, XY/YX order, dateline positions, and availability assumptions/overrides. Profile dimensions, tile IDs, fabric coordinates, worker selection and resource aliases come from the profile, not copied device constants. Unknown availability cannot become executable without an explicit binding declaration carrying its assumed/documented provenance. The supplied Wormhole example will declare the documented complete physical router grid and an explicitly assumed healthy-link state. Harvesting affects worker selection, not transit-router existence.

Bind existing profile quantities by parameter reference where applicable, including fabric clocks and physical flit width. Any deliberate runtime override must retain the source value, effective value, evidence and reason; conflicting duplicate values cannot silently replace profile data. Stage splits and capacities absent from the profile are explicitly assumed model settings. Counterfactual overrides do not become documented Wormhole characteristics.

Replay attachment permissions are an explicit endpoint allowlist with local injection/ejection bindings and request/response fixture roles. These are transport-test permissions, not claims about arbitrary NIU commands. The builder never creates `Core`, `DMAEndpoint`, memory service or scheduler objects. Non-enabled workers cannot be selected as worker traffic initiators merely because they retain a router. Transit and other inventory endpoints remain unbound unless expressly selected for a supported fixture role. DRAM fixture endpoints can consume requests and emit causally generated responses, but cannot issue independent requests.

For every raw coordinate and each axis, generate one directed edge to the next coordinate modulo that axis extent. A full 10x12 fabric has 240 directed inter-router links; two fabrics have 480. Validate coordinate bijections, port halves and all canonical references before allocating runtime resources. Export stable fabric-qualified IDs, both endpoint coordinates, physical direction, and physical-wrap metadata. Dateline boundaries are virtual-resource policy and can differ from a physical wrap; export them separately.

For graph input, verify that the graph realizes this same directed-torus contract; do not infer missing/reverse edges or accept arbitrary shortcut/parallel edges under a torus label. Dimensions below two are outside the initial torus policy and fail explicitly; legacy 1x1 mesh support remains. Disabled links/routers remain in inventory, and any admitted request or response route needing one fails before execution. No adaptive detour is generated.

Keep plain profile inspection as inventory projection. Report transport-binder support in a separate scope; only a compiled version-2 plan establishes actual transport admission. Full `Arch`/DFG execution remains gated even after the transport binder is implemented. This avoids treating a global manifest flag as permission to execute an incomplete hardware profile.

### 3. Route first, then assign a dateline resource path

Resolve physical endpoints to canonical fabric routers, translate to raw coordinates, and take positive modular hops in the configured dimension order. The profile selects XY on NoC0 and YX on NoC1. There is no shortest-path comparison between the two fabrics and no implicit fabric selection.

For example, physical `(9,11)` to `(1,1)` takes four NoC0 hops (two X, then two Y) and eighteen NoC1 hops (ten physical up, then eight left). Both deliver to the same tile. Same-router delivery uses only local injection/ejection channels. The route compiler returns canonical links, input/output ports and the complete resource path, so the runtime does not rediscover routes from dense indices.

All-pairs route verification concerns router connectivity. It does not grant every physical tile permission to initiate traffic: executable endpoint pairs still pass the separate role/availability allowlist, including the harvested-worker restriction.

Use policy `dimension_dateline_v1`: each network link has request/response classes, each with two dateline phases. This is four modeled lanes per physical network link, not a claim that hardware has four VCs. Local injection/ejection has independent request/response lanes; it does not need an axis dateline bit. A packet starts phase 0 in each traversed dimension, switches to phase 1 **on the dateline edge itself**, and retains phase 1 until the dimension ends. A dimension turn resets phase for the new axis. The network never changes traffic class.

The configurable dateline for an axis is the edge from coordinate `d` to `(d+1) mod n`. Rotate its source-coordinate numbering as `q = (k-d-1) mod n`, making the dateline edge's source `q=n-1`. A route travels fewer than `n` edges in each dimension. Give a network resource this rank:

```
first dimension base = 0
second dimension base = 2 * first_dimension_extent
phase 0 rank = base + q
phase 1 rank = base + n + ((q + 1) mod n)
```

Every consecutive resource in a dimension increases rank, including crossing the dateline. All first-dimension ranks precede all second-dimension ranks. Injection is before network ranks, ejection after them. At turns the phase can reset because the dimension rank increases. Class and fabric resources are disjoint; there are no cross-fabric network dependencies.

An exploration-only, in-memory enumeration covered 72 ordered routes on a two-fabric 3x2 grid and 28,800 on a two-fabric 10x12 grid, including local paths. The latter had 480 distinct edges, 20,220 routes using a physical wrap and a maximum of 20 hops. All consecutive ranks increased and the virtual-resource dependency union was acyclic; the corresponding one-VC union was cyclic. This checks the proposed mathematics at the default wrap datelines, **not an implemented router or a runtime drain test**. Implementation tests must add shifted datelines, an independently expressed oracle and dependency mutations that the validator rejects.

Retain a static dependency validator for the compiled resource paths, including local channels and response transitions. A rank rule explains why the whole supported family is safe; a compiled-graph check catches binding/compiler mistakes. Do not repurpose the old physical-channel DAG validator to accept a cyclic torus by removing its safety check.

Alternatives considered: a restricted one-VC route subset does not meet all-pairs transport; unbounded buffers avoid realistic backpressure; full store-and-forward changes cut-through behavior; reproducing every silicon buddy/priority mechanism is unnecessary for this milestone. A bounded dateline/class abstraction supplies a concrete smaller contract.

### 4. Separate lane ownership from physical service

Add a generic opt-in `virtual_channel.py` kernel rather than modifying the meaning of legacy `RouterConfig.vc`. Reuse canonical bindings, `FlitConfig`, integer flit counting, identity types and applicable pure timing calculations. Extract a shared utility only when both paths genuinely use it and a legacy regression proves parity. Do not layer four independent existing `Link` instances onto an edge: that would multiply bandwidth. Do not put all lanes behind its current single blocking receive FIFO either.

Each network lane has finite capacity, FIFO order and per-packet HEAD-through-TAIL ownership. Competing packets cannot interleave within one lane, while different lanes can share the physical serializer. The packet's class, compiled hop and dateline assignment live in an immutable plan-bound transport envelope; validate them before credits, reservations, queues or trace output change. BODY/TAIL must agree with the HEAD and the compiled route. Legacy graph flits do not gain access to the new kernel merely by supplying a matching-looking ID.

Capacity `B` is an explicit effective lane budget covering reserved downstream storage, including flits awaiting transmission, in flight, queued at the receiver or held by a forwarder. Return its credit exactly once after the flit releases that storage. A delayed credit return still occupies the sender's token budget. At every observable point:

```
B = available credits + outstanding flit tokens + pending credit returns
```

Any receiver FIFO or staging queue has a named finite bound. Reserve the downstream lane token before launching a flit, so arrival cannot block a shared wire process on a full receive FIFO. A small `B` can reduce throughput; do not silently enlarge it to a bandwidth-delay product or mistake separate VC credits for additional physical wire capacity. Pending SimPy operations and forwarding registers count where they retain modeled flits. Finite scheduled traffic metadata is not receiver storage.

Routing/packet ownership can wait on the next ranked lane. The **shared physical switch/serializer cannot retain a grant while waiting for lane ownership, buffer credit, a full endpoint, or the next flit of an idle packet**. Fair arbitration selects eligible lanes, with a configured positive maximum quantum; it releases unused quantum when its lane stops being eligible. A fresh packet waits for lane ownership without blocking another lane's physical service. Include packet-owner resources in the dependency audit.

Router stages have configurable latencies and a separately declared transfer initiation interval and finite pipeline capacity. A candidate acquires the necessary downstream token and transfer-stage capacity without holding an unrelated physical grant while waiting. Once admitted, its pipeline service is finite and its wire-bound flit already owns destination capacity; wire scheduling cannot feed back into a blocked destination. Independent outputs can progress concurrently. This preserves pipeline overlap rather than equating total stage latency with throughput.

A zero-credit lane therefore cannot prevent a ready response or alternate-dateline lane from using the wire. Tests must demonstrate that property directly, as well as packet ordering, cut-through arrival before source TAIL completion, fairness and aggregate physical-link throughput. The routing DAG alone does not prove these scheduler properties.

### 5. Extend the proof through finite request/response endpoints

Version 2 accepts one-way request-class byte packets and finite request fixtures that produce exactly one response. A fixture declares request/response byte sizes, eligible endpoints on the **same fabric**, finite service delay, bounded pending-response descriptor capacity, and sink service. Response IDs are derived from their request IDs in a separate namespace and traced causally. A response starts only after that request's final flit has been consumed and its configured service has completed. Responses do not create more requests or responses.

Maintain separate source injection queues/packet owners and sink consumers for request and response classes, while sharing each local physical link's serializer. Whole-packet serialization across both classes, as in the version-1 replay source, is unsuitable here. A request blocked behind a full response-descriptor queue can retain request-class storage, but must not retain the response drainer, response injection owner, or shared physical grant. Include the finite active sink/producer registers in the reported descriptor/storage bounds; avoid one unbounded coroutine/queue per arriving response.

Request ejection can depend on descriptor capacity, and descriptors can depend on response injection and delivery. Responses terminate at an independently draining sink and never require request resources. The resource order is therefore:

```
request injection --> request network --> request ejection
                                               |
                                               v
                       bounded response descriptors
                                               |
                                               v
response injection --> response network --> response ejection --> consume
```

The vertical arrow into response resources, together with their internal ranks, has no return edge to request resources. Response descriptors are finite; request backpressure is allowed. With finite traffic/packets, fair eligible arbitration, positive finite service, eventual clock progress, enabled sinks and no permanent failure, terminal responses drain and release capacity backward through this order. Finite slowdowns preserve that argument. An execution cycle limit only bounds a run and reports incomplete state; it is not the proof.

This fixture deliberately excludes memory allocation, DRAM arbitration, transaction IDs/order guarantees, acknowledgements with hardware formats, and software-visible barriers. Child 4 must retain or extend this dependency argument when it adds actual services; separating classes alone will not protect an endpoint that holds a response sink while waiting on a request.

### 6. Make timing boundaries and directed slowdowns reproducible

Version-2 timing is explicit in native NoC cycles and a named fabric clock; the simulation result uses ACI-cycle timestamps and declares the ACI clock. Endpoint service explicitly names its timebase. Resolve values and overrides before the environment exists, retain input/effective units and evidence, and reject nonfinite/nonpositive clocks or capacities. Latencies can be zero where meaningful; physical launch intervals and finite sink service ensure progress. Fabric IDs need not be contiguous. No hidden 1 GHz or Wormhole buffer default is permitted.

For a physical flit of `F` bytes, usable width `W` bits/NoC cycle and ratio `r = f_noc / f_aci`:

```
serialization_noc_cycles = ceil(8 * F / W)
serialization_aci_cycles = serialization_noc_cycles / r
physical launch interval >= serialization duration
```

Router RC/SA/transfer latency, transfer initiation interval, wire serialization, wire launch spacing, propagation, credit-return delay and sink service are separate settings/events. Buffer or arbitration stalls are measured waits, not additional constants secretly folded into propagation. Finite pipeline capacity can constrain initiation even if the configured interval would allow more overlap. End-to-end completion includes endpoint service and, for request fixtures, response consumption. Pipeline/credit drain may finish later and is reported separately.

Public aggregate hop references do not uniquely determine this stage decomposition. Example assumptions must be labeled and their zero-load timing computed from the implemented event boundaries; do not add the public hop latency to independently charged stages and claim a match. Compare physical bytes to serialization capacity; payload efficiency remains affected by this child's explicitly simplified packet format.

Add slowdown entries `(failure_id, fabric_id, directed_link_id, start_aci_cycles, end_aci_cycles, factor)` with finite `factor >= 1`, `0 <= start < end`, and validated instantiated inter-router targets. Reject overlapping intervals on the same target in this first version; adjacent intervals are allowed. Local-link/router/permanent-drop failures are not part of this new schedule. The legacy paired-link and router-wide failure APIs remain unchanged.

For a version-2 flit, evaluate the half-open schedule `[start,end)` at the physical launch timestamp, independently of SimPy callback registration order. Snapshot that factor for its serialization, launch spacing and propagation. Already launched flits retain their snapshot; later launches use the current schedule. Credit return, router stages and other links keep their own timing. Preserve FIFO order on a lane when a faster post-recovery flit would otherwise overtake an older flit with a longer propagation delay; account for any resulting arrival-order wait explicitly. No transmission changes route. This is a documented stage model, not instantaneous analog link recovery.

Expose failure start/end events, launch factor and stage timestamps/durations, keyed by canonical physical link and fabric. A targeted slowdown affects every modeled lane sharing that physical link, but does not mutate another directed link or the other fabric. Traffic elsewhere can still be delayed by genuine shared-resource contention; only disconnected-resource control traffic is expected to have identical completion time.

### 7. Version evidence and reject unsupported consumers

The version-2 result contains the graph/profile identity, effective plan hash, policy revision, declared assumptions and support scope; canonical/runtime ID maps; compiled paths with classes/dateline transitions; configured capacities and occupancy peaks; per-transfer/response payload and physical-byte counts; per-link physical sends; and pending packets, descriptors, credits, owners and pipeline work.

Trace rows identify class and modeled lane, canonical link/router/endpoint, packet and causal request, flit sequence, event timebase and stage/failure information where applicable. Credit events identify the returned lane/token. Count physical bytes once per actual launch; report source packet bytes separately. Define flit acceptance/release boundaries so an analytical timeline can be reconstructed without summing overlapping intervals as if they were serial service. `complete` requires both logical completion and resource drain; timeout or idle-with-pending stays incomplete even if all payload counters happen to match.

The command remains `python -m simulator_detailed.replay_topology --replay <config> [--output <path>]`, with exact kind/version dispatch. JSON-only stdout and exit codes 0/1/2 retain the success/invalid/incomplete meanings. Reject invalid configuration before environment/resource construction and before creating an output artifact. Version-1 examples and hashes remain stable. New internal envelope/trace records do not change legacy `Flit.model_dump()` or event output.

Existing detailed detector/encoder guards must reject version-2 input explicitly and still admit structurally verified legacy objects. Their 7-D/4-D feature shapes, ordering and checkpoints do not expand here. Torch/PyG execution remains unvalidated when dependencies are unavailable. Separate top-level DFG generation, feature graphs, RL observations/actions/rewards and fail datasets are unaffected; transport support alone does not imply end-to-end remapping support.

### 8. Layer validation and commit boundaries

Use independent physical-coordinate route fixtures, including asymmetric pairs, both single-axis wraps, double wraps, YX turns, local delivery and paths through harvested workers. Enumerate both fabric route families on the selected 10x12 inventory; separately verify rank/dependency graphs on configurable small tori and shifted datelines. The independent oracle must not call production route or edge builders. Mutate phase-on-wrap, reset-within-axis, class dependencies and disabled edges to ensure negative checks detect meaningful errors.

For the kernel, test small capacities, packets longer than buffers, multiple packets sharing a VC, blocked VC versus ready VC, finite pipeline occupancy, unequal traffic quanta, different outputs, credit conservation and aggregate wire bandwidth. For endpoints, stress simultaneous causal responses with a response queue of one and slow independently draining sinks on both fabrics and across both axes. Verify request-to-response causality and complete drain, not just receipt of bytes.

Use independent analytical timelines for local/one-hop/multi-hop routes at two widths and clock ratios; distinguish first-flit and packet completion. Exercise one directed slowdown, a wrap link, same numeric IDs on different fabrics, start/end boundaries, recovery without overtaking, invalid/overlapping targets, and unrelated-resource controls. Retain child-2 cycle-44 example identities and the legacy custom-mesh timing/failure fixture.

Each part in `tasks.md` ends in focused tests, strict type coverage for the changed production modules, scoped lint, review and a local commit. Final validation runs the full detailed suite, published CLI examples, strict OpenSpec and diff checks, then records exact commands, source/artifact hashes and limitations. Evidence distinguishes architecture conformance, simulator invariants/analytical checks, external-reference comparison and hardware calibration. Only the first two are promised here.

## Risks / Trade-offs

- **Shared grants can invalidate a correct routing proof** -> require eligible-only physical arbitration, bounded staging and an explicit resource audit; test blocked lanes before torus integration.
- **Recovery can reorder a lane when propagation shrinks** -> preserve launch order per lane and expose arrival-order waiting; snapshot factors per flit for deterministic boundaries.
- **Response queues can introduce protocol cycles** -> independent response drain and injection, bounded descriptors/registers, and a one-way request-to-response dependency; actual memory services require another audit.
- **A separate opt-in kernel can drift from the legacy path** -> share only justified primitives, retain exact baseline tests, and document why legacy whole-link buffering/grants cannot implement this policy directly.
- **Configurable buffer/pipeline choices can dominate results** -> report effective capacity, clocks, initiation/latency and assumptions; do not equate reference throughput or a 32-byte flit with validated silicon timing.
- **A complete router grid can be confused with a fully enabled device** -> preserve the illustrative N150 worker-mask assumption, explicit binding availability, separate endpoint permissions, and full-profile execution gates.
- **Large route/trace tables cost host memory** -> compile deterministic admitted route sets for execution, use exhaustive families in bounded offline validation, and report trace cost separately from modeled hardware storage. No new scale claim is needed for this child.

## Migration Plan

Implement the seven ordered parts in `tasks.md`, committing each validated part. Add the version-2 CLI dispatch only when its runtime is reviewable. Existing saved inputs continue selecting their original path; no migration, model conversion or backup is required. A regression can be isolated by disabling the opt-in version-2 entry point or reverting its relevant commit while retaining version-1 execution.

At completion, record exactly which TR-02..04/network VA-04 behaviors execute and hand off packetization/NIU/memory work to `wormhole-memory-transactions`. Do not mark the umbrella complete, synchronize pending umbrella targets as delivered, archive, or push as a side effect of this child.
