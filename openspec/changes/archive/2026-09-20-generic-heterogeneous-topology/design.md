## Context

See `proposal.md` for motivation and the parent `wormhole-single-chip-simulation-plan` for requirement ownership. This design follows the committed profile implementation at `9b4770b`. It is a cross-module design, so the design artifact is required. No simulator implementation is delivered by these planning artifacts.

### Current behavior and coupling found during explore

| Area | Current behavior | Required change and effect |
| --- | --- | --- |
| `architecture.Arch` | `build_cores()` and `initialize()` enumerate `x*y`; each core ID equals its router ID. Hardware profiles are rejected before construction. | Legacy construction uses a compiled graph but keeps its current core list and scheduler mapping. Generic replay has its own transport-only construction path; no heterogeneous DFG scheduler is introduced. |
| `endpoint_registry.EndpointRegistry` | Creates PE addresses on every router/fabric, then explicit typed DMA attachments. | Consume explicit graph bindings; preserve the legacy constructor through an adapter. Separate physical attachment, compute eligibility, and executable endpoint roles. |
| `noc.NoC` / `Router` | Mesh builder creates two links per neighbor pair. `bind_link()` installs paired input/output links. Routing computes XY from integer IDs; one VC and burst round-robin are enforced. | Build links from directed records, support independently bound input/output ports, and inject a validated routing policy while preserving existing flow control. |
| `utils.definitions` | Endpoint addresses enforce `router_id < mesh_x*mesh_y`; flits carry mesh dimensions; fixed paths validate mesh adjacency but transport still rejects FIXPATH. | Introduce explicit graph transport context rather than inventing rectangular geometry. Retain legacy address/message validation and FIXPATH rejection. |
| `predictor.topology.Mesh` | Independently enumerates the same mesh edges as `NoC`, with a comment requiring their orders to match. | Share the legacy graph adapter and preserve current flattened indices. |
| Detailed predictor / encoder | Predictor has 7-D core/link features and dense mesh assumptions. Encoder uses four features and derives coordinates with `to_xy()` / modulo. | Validate a supported legacy graph contract; use canonical identities and coordinate records. Generic replay remains incompatible with these feature semantics. |
| Failures / traces | Detailed link events carry fabric and directed integer link identity. Legacy `Arch.link_fail()` slows both directions of a router port. | Preserve that legacy behavior; export explicit canonical mappings. New directed slowdown input and torus failure behavior belong to child 3. |

Read-only construction checks during this explore confirmed that current runtime and detailed predictor enumerate the same ordered edges for 3x2, 4x4, and 1x1 default two-fabric meshes: respectively 28, 96, and 0 directed links in total. A 4x4 mesh has 16 physical workers, 16 routers and 48 directed links **per fabric**; the predictor merges physical core identities across fabrics, whereas the detailed encoder has one router node per fabric. These are different existing model contracts and must not be conflated.

Child 1 records 52 passing detailed tests, strict Pyright with zero errors/warnings, and passing scoped lint. Those are prior implementation results, not new transport evidence. Torch and Torch Geometric are absent in the inspected `.venv`; graph construction checks did not load a model. No local `NOC_ARCHITECTURE` file or branch was found; the committed umbrella/profile artifacts and current detailed implementation supply the available architectural context.

## Goals / Non-Goals

**Goals:** a reusable immutable topology boundary; real directed transport on a small heterogeneous synthetic graph; legacy behavior preserved by construction and tests; deterministic, reviewable graph/trace identity; explicit incompatible-consumer errors.

**Non-goals:** accepting Wormhole profiles in `Arch`/`simulate`; generating Wormhole torus connectivity or selecting XY/YX torus paths; multiple VCs or a torus deadlock policy; generic-graph failure scheduling; memory service, DMA transactions on new replay terminals, compute execution, heterogeneous workload scheduling, numerical data, model retraining, or RL shape changes. Existing synthetic DMA/compute/failure execution remains supported.

## Decisions

### 1. One normalized graph, with stable public identity and explicit runtime indices

Add `configs/schemas/topology.py` for strict versioned input records and `topology.py` for normalization, adapters, index maps, and deterministic export. The graph document has `kind: canonical_topology`, `schema_version: 1`, a user-facing topology ID, ASIC identity, and these records:

| Record | Required meaning |
| --- | --- |
| Physical tile | Stable string ID, physical coordinates, tile role. Coordinates locate a tile; they do not generate edges or imply compute. |
| Fabric | Existing nonnegative fabric identity, optional coordinate extent and coordinate records, topology/routing metadata. |
| Router | Fabric-qualified stable string ID, physical tile reference, independent enabled state. |
| Directed network link | Fabric-qualified stable string ID, source router/output port, destination router/input port, enabled state, optional direction/wrap metadata. Reverse connectivity is a separate record. |
| Attachment | Stable endpoint ID, router reference, local injection/ejection port declarations, role, independent availability, resource references, and optional legacy typed address binding. Inventory-only records can explicitly leave port/direction capability unresolved. |
| Compute selection | Explicit enabled worker identities and logical-worker bijection, distinct from the physical attachment inventory. |
| Physical resource | Stable ID, memory kind, capacity in exact bytes, ownership/alias references, source parameter/evidence references where applicable. Capacity is stored once. |
| Origin / completeness | Legacy config identity or effective profile hash and provenance references; `connectivity_state: complete` or `unresolved`. |

IDs need not be numeric or encode coordinates. A router/link key includes its fabric; physical resource keys do not, so aliases on multiple fabrics share one capacity. Ports have stable names and independently occupied input/output halves. Reject repeated IDs, missing references, multiple links driving one input, multiple outgoing links occupying one output, and attachment/network port collisions. Parallel edges are allowed only with different IDs and unambiguous ports. Compute eligibility requires a worker tile. Executable compute bindings, when declared, additionally require an eligible worker and an available attachment; neither a network attachment nor inventory eligibility alone establishes an executable compute service.

Normalize unordered records deterministically and hash semantic structure, including availability, roles, origin, and any declared compatibility index mapping. Input record order and file path are excluded. Preserve profile evidence references rather than relabeling capacities or assumptions as measured. Export unresolved link count as unknown (for example, null with completeness status), distinct from a complete one-router graph with zero links.

Runtime kernels can keep efficient integer router/link/port handles. Publish their bijections to canonical IDs. For explicit graphs, derive handles deterministically from canonical IDs; for legacy meshes, use explicit compatibility indices preserving row-major router IDs and the existing east-pair then north-pair construction order. No generic consumer may infer physical coordinates or link direction from a dense handle. The compiled plan is immutable during execution; graph mutations require a new plan.

Alternative rejected: use `x*y` and sparse core arrays as the topology contract. That leaves identity, link direction, and endpoint assumptions scattered across the same consumers. Also reject a second independent graph implementation used only by visualization.

### 2. Adapters separate structural inventory from executable policy

```text
Legacy ArchConfig ----> mesh adapter --------+
                                            |
Explicit graph JSON --> validated graph ----+--> canonical topology
                                            |          |
Hardware profile -----> inventory adapter --+          +--> export / contract checks
                                                       |
                         complete connectivity + validated transport plan
                                                       |
                                  shared Router / Link transport machinery
                                     |                         |
                           legacy Core / DMA             replay terminals
```

The legacy adapter creates graph records for configured PEs and DMA bindings and preserves the existing runtime settings. `Arch` compiles once, passes the same graph to network construction and the registry, and retains `cores: list[Core]` with the current dense mesh semantics. `Arch.build_nocs()` and `NoC.build_connection_mesh()` remain compatibility wrappers for current callers/tests; their edge construction delegates to the adapter instead of keeping a second mesh generator. Refactor core binding to use attachment router references, even though legacy IDs still coincide. Do not introduce unused synthetic memory resources or reinterpret SPM service through graph inventory.

The hardware-profile adapter uses the already validated, normalized profile to preserve tile/fabric IDs, coordinate maps, logical workers, physical attachments, resources, and evidence references. It creates no runtime cores, links, service queues, or timings. The initial Wormhole projection contains 120 physical tiles, 240 fabric routers, 240 physical attachments, 72 enabled workers, and 86 resources (80 worker L1 plus 6 shared DRAM groups). These are inventory counts, not counts of instantiated services. Profile attachment capability is not inferred from tile role. Child 1 declares no local transport port numbers or request/response direction permissions, so those remain explicitly unresolved; execution requires them to be resolved rather than defaulting to bidirectional port zero. Undeclared component availability is similarly unknown, distinct from disabled. In this child connectivity is unresolved; the later routing child will supply the profile's edge/policy adapter.

Keep child-1 input/report semantics and the implementation-owned hardware-profile gate intact: `can_execute` remains false. New inventory projection is a separate graph view, not evidence that `profile_runtime_adapter`, Wormhole routing, or memory/compute services are executable. Do not weaken a requested-feature blocker merely because the same generic mechanism exists for synthetic graphs.

Alternative rejected: convert a Wormhole profile to a fake mesh with synthetic timing defaults. It would change architecture meaning and undermine the validation hierarchy established by child 1.

### 3. Decouple routing from transport and bind directed channels independently

Extract the next-hop decision behind a small policy interface in `routing.py`; the current router remains responsible for HEAD/body reservation, arbitration, pipeline costs, output waiting, and credits. A legacy XY policy consults the canonical mesh's coordinate and port maps. An explicit-route policy uses a compiled route table; neither policy changes the packet's transmission mode from SINGLECAST.

`Router` gains independent input and output binding operations. Creating an input starts its forwarder; creating an output creates its arbiter. Existing paired `bind_link()` delegates to both operations after validating both, avoiding partially installed bindings. A directed `Link` is bound to exactly one source output and one destination input. Iteration over ports, including slowdown helpers, must handle input-only and output-only ports without assuming dictionary keys match. Preserve pair-based legacy failure semantics through the old entry point.

Pass a compiled topology/transport context to graph-bound links and routers. The context resolves dense handles, attachments, format, and routing. Add an opt-in transport-plan identity to flits and graph address records; graph-mode mesh dimensions are explicitly absent and graph membership is validated against that context. Existing `EndpointAddress` and `Message` constructors retain their default legacy geometry and DMA role validation. Do not treat a non-PE network terminal as a DMA endpoint to satisfy the old enum contract. Use a separate resolved replay endpoint record for generic attachments.

Validation is mandatory at public admission and direct link/router boundaries: reject a wrong graph/plan identity, fabric, format, endpoint/port, or unsupported mode before state allocation. The plan identity hashes the graph plus resolved route and transport configuration, so two plans with identical small integer handles cannot exchange packets accidentally. Legacy packets stay in the legacy context. Keep new internal fields out of existing legacy trace JSON.

Alternative rejected: duplicate the router or reuse mesh arithmetic with fabricated dimensions. Both would hide behavioral divergence behind a graph-shaped API.

### 4. Limit first generic execution to proven acyclic channel dependencies

Replay routing configuration explicitly lists `(fabric, source_attachment, destination_attachment, ordered_directed_link_ids)` routes. Require unique endpoint pairs, matching endpoints, contiguous enabled edges, no repeated routers, and no cross-fabric hop. A zero-link route is valid only when source and destination share a router. Multiple local endpoints remain distinct by attachment and port. A route compiler derives the output port at each visited router; it does not require a new source-routing wire encoding or enable the existing FIXPATH mode.

Construct one channel dependency graph for the **union of all admitted routes**, not only the traffic selected in one run. Vertices are finite transport channels, including local injection/ejection links. Consecutive held-channel/next-channel pairs form directed dependencies. Reject cycles before any SimPy process or resource is created and report the involved stable channel identities. Physical graph cycles are allowed in data, and an acyclic admitted route subset can execute on such a graph. This is not general torus route support.

The scoped progress argument assumes one VC, finite traffic, finite configured service/credit delays, fair round-robin allocation, no permanent failures, and sinks draining independently of transmission and request/response activity. In an acyclic channel dependency graph, a terminal dependency can drain without waiting on another held channel; recursively freeing channels removes the circular-wait condition. The implementation review must check that router grants, input buffers, and in-flight windows introduce no unmodeled dependency beyond those channels. Include a small counterexample whose individually simple routes collectively cycle. A watchdog detects a broken assumption; it is not the prevention policy.

No adaptive routing, automatic shortest-path fallback, request/reply dependencies, multicast, or virtual-channel expansion is included. Missing reverse paths are not synthesized. The later torus child replaces/extends the routing and finite-resource policy without replacing the canonical graph or transport accounting.

### 5. Provide a transport-only API and CLI with explicit configuration

Add `transport.py` for plan validation, graph construction, and replay terminal send/drain behavior. Terminals packetize byte counts using the configured flit format and existing physical-transfer accounting; they do not schedule DFG tasks, access a memory address, or compute tensor values. Only explicitly enabled replay attachments inject/receive. Physical network attachments at memory/disabled tiles can remain inventory or transit neighbors without becoming executable services. The synthetic fixture uses a disabled worker router with no active compute/replay endpoint.

Add a `kind: topology_replay`, version-one configuration referencing one complete graph file (relative paths resolve against the replay file), explicit routes, per-fabric transport settings, and a finite traffic list. Reuse validated `FlitConfig`, link timing/buffer fields, router pipeline costs, and burst quantum semantics. Require explicit ACI/NoC clocks and reference named settings for every network/local channel; allow per-link and per-attachment overrides. Router policy is separate from microarchitecture configuration so the existing `RouterConfig.type == XY` field cannot silently select a different policy. Resolve and validate effective settings before computing the plan hash. Hardware-profile timing anchors are never automatically copied into these settings.

Traffic records identify a unique transfer, source, destination, fabric, injection time in ACI cycles, and positive payload byte count. Configure finite sink drain service and a positive finite simulation cycle limit. Unsupported failure/transaction fields are rejected. Admission validates the entire plan and traffic before constructing resources or opening result files. The runner succeeds only when declared deliveries, queues, credits, in-flight transfers, and reservations have drained; an empty event queue alone does not prove completion. On timeout or premature quiescence, return nonzero with an explicit incomplete result and pending transfer/resource identities.

Add `replay_topology.py` with these planned interfaces:

```bash
.venv/bin/python -m simulator_detailed.replay_topology --inspect simulator_detailed/configs/topologies/heterogeneous_example.json
.venv/bin/python -m simulator_detailed.replay_topology --inspect simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/heterogeneous_unicast.json
```

Inspection accepts canonical graph, legacy config, or hardware profile through explicit classification; unknown/mixed kinds never fall back to another parser. Replay config and graph docs are not accepted by legacy `arch_analyzer()`/`simulate()`. Default output is versioned JSON on stdout. An optional output path is opened only after successful input admission; implementation tests use temporary output directories. Commit illustrative configuration and compact independent expected fixtures, not generated run traces.

The replay result has `kind: topology_replay_result`, version 1, status, graph/plan hashes, effective configuration, graph export, runtime index maps, endpoint/resource references, payload versus physical-byte totals, per-transfer completion, trace events, and capability limits. Network events map dense handles back to canonical IDs and fabric. Local attachment events are distinguished from router-to-router links. Structural disabled/uninstantiated entries remain in the export. `silicon_timing: unvalidated` remains explicit.

Alternative rejected: teach `Arch` and the workload mapper about sparse heterogeneous cores immediately. That expands into child 5 and conflates byte transport with supported workloads.

### 6. Integrate supported consumers and guard incompatible semantics

Add a lightweight `topology_compatibility.py` with no Torch/PyG dependency. It validates the graph and event format for a named consumer contract; accept only verified legacy-adapter semantics in this child. Count equality is insufficient: check complete rectangular mesh connectivity, physical worker/core bijection, fabric list, required legacy indices, endpoint semantics, and legacy trace format. A user-supplied `compatible: true` label grants nothing.

`predictor.topology.Mesh` becomes a compatibility view over the canonical legacy graph, retaining its public constructor, path-node output, and flattened fabric/link ordering. The detailed builder/predictor accepts that view or a graph passed through the same check; explicit unsupported `noc_type`/graph inputs fail before feature construction or checkpoint loading. The encoder obtains coordinates and directed identity from the supported graph and calls the guard before constructing tensors. Exported replay results cannot be passed as legacy event dictionaries without a format rejection.

Preserve these model contracts: detailed predictor core/link input width 7, detailed encoder input width 4, legacy feature values and ordering, and existing checkpoint tensor-shape diagnostics. No graph-conditioned model accuracy is claimed merely because weight shapes load. The separate top-level RL workflow imports its own simulator/encoder; it gains no new input contract from this child. Do not edit top-level code, action spaces, observations, rewards, checkpoints, or legacy failure datasets.

Test the common guard and mesh view offline without ML packages. Wire the guard into real detailed consumer call sites. If actual Torch/PyG smoke checks remain unavailable, record that limitation; do not replace them with fake-package tests and claim model integration passed. New production helpers and changed dependency-free modules must be included in strict typing. Scope static checks on older optional-ML modules without broad type-ignore suppression or unrelated cleanup.

Alternative rejected: feed every router to the existing predictor as a compute core, or retrain models as part of network construction. The former changes feature meaning; the latter is separate modeling work.

### 7. Validate independently and commit six completed parts

The six implementation sections in `tasks.md` are sequential commit boundaries. Run each section's focused behavior checks plus relevant typing/lint before committing; record its evidence, check completed tasks, commit, and only then proceed. The final section runs the full detailed regression set and consolidates the evidence. Smaller commits within a section are appropriate if they form completed, validated units. Do not defer all commits to the final section, push without a separate request, or mark planning completion as implementation completion.

Use hand-authored expected fixtures for ordered mesh edges, representative baseline trace/timing outcomes, and directed replay routes. Do not derive the test oracle using the production graph/route compiler. The heterogeneous fixture includes two enabled worker endpoints, memory/transit routers, a disabled worker router still on a route, two fabrics sharing a memory identity, and a non-Manhattan directed edge with no implicit reverse. Add parallel-link identity, local delivery, invalid reference/port, reordered-record, undeclared route, cross-plan packet, cyclic dependency, slow sink, and incomplete-run cases. A cyclic physical graph with an acyclic admitted route subset is a positive case; three simple routes whose dependencies form a ring are a negative case.

| Requirement | Planned evidence |
| --- | --- |
| TR-G01/02 | Independent structural fixture, negative schema/reference tests, resource alias totals, disabled-compute transit execution |
| TR-G03 | Profile projection versus child-1 independent fixture; unknown link count; early execution gate tests |
| TR-G04 | 1x1, 3x2, 4x4, custom-fabric/port mesh parity; existing DMA/NoC tests; paired link slowdown/recovery trace |
| TR-G05/06 | Directed and local deliveries; route table rejection; union dependency-cycle rejection; context admission state unchanged; incomplete status |
| TR-G07 | Multi-flow backpressure/drain and credit conservation; analytical isolated-link costs under two widths/clocks and packet boundaries |
| TR-G08 | Graph export matched to observed runtime object and event identities; two-fabric index collision; local versus network events |
| TR-G09 | Dependency-free consumer contract checks; legacy mesh relation ordering; real optional tensor smoke when available |
| TR-G10 | Exact environment/commands, graph/plan/example hashes, full detailed test/type/lint results, limits and umbrella coverage |

Required final commands include:

```bash
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/python -m ruff check --select E9,F63,F7,F82 <changed-python-files>
.venv/bin/python -m ruff check <new-python-files>
openspec validate generic-heterogeneous-topology --strict --no-interactive
git diff --check
```

The file lists above are task-time placeholders to replace with the actual changed modules. Add every new production module to the strict project. Preserve a separately identified 4x4/Darknet19/failure smoke path for the top-level workflow if any shared contract unexpectedly needs editing; stop and rescope that dependency instead of silently importing it into this child. Current planned edits are confined to detailed code and its documentation/examples/tests.

## Risks / Trade-offs

- [Canonical IDs coexist with legacy numeric handles] -> Keep one immutable mapping, validate every boundary, and test reordered inputs plus non-row-major names. Never independently renumber exports and runtime.
- [A route DAG omits a real held resource] -> Review the existing grant/credit lifecycle before accepting the proof; include injection/ejection dependencies and independently draining sinks. If another dependency is found, correct the plan before enabling execution.
- [Refactoring bindings changes event ordering or credit timing] -> Capture compact pre-change analytical/observed baselines and rerun existing packet, burst, DMA, and failure tests at the directed-binding commit boundary.
- [Inventory projection looks like Wormhole support] -> Export unresolved connectivity and instantiated state, retain the profile gate, and report separate generic replay versus Wormhole support.
- [Adding a replay API duplicates packet semantics] -> Reuse flit configuration and transport machinery; verify byte boundary cases against existing packetization behavior without introducing DMA or tensor semantics into terminals.
- [Optional consumer dependencies conceal integration defects] -> Require offline contract checks and call-site wiring; list actual tensor/checkpoint runs as unavailable until executed.
- [Child grows into scheduler/torus redesign] -> Keep its executable exit at synthetic byte transport. Re-explore child 3 only after this child's implementation, evidence, and commits are complete.

## Migration Plan

1. Deliver additive graph records/adapters first; legacy public entry points continue to accept their current documents.
2. Move the legacy runtime onto the canonical graph with parity checks before exposing explicit-graph replay.
3. Add opt-in replay and separately versioned outputs; users retain the legacy path for DFG/DMA/detector workflows.
4. Record implementation evidence in `simulator_detailed/docs/topology.md`, update child task status, and commit each completed part. These planning artifacts are committed before apply starts.
5. Reconcile umbrella TR-01 and graph portions of HP-03/04 after delivery. HP workload scheduling, TR-02..05, memory service, and silicon timing remain pending. Sync/archive only through separately requested actions; do not replay the umbrella's pending ADDED requirements over delivered child specs.
6. Rollback is selecting the earlier supported legacy revision or input path; there is no persisted model/workload/failure migration and no backup creation.
