# Canonical topology and transport evidence

This document tracks the incremental `generic-heterogeneous-topology` delivery.
The implementation now includes immutable topology records, profile/mesh adapters,
directed runtime bindings, validated routing policies and standalone synthetic replay.
Hardware-profile execution and silicon timing remain unsupported/unvalidated.

## Baseline (2026-09-15)

At commit `0cb748d`, Python 3.12.12 in `.venv` passed 52 detailed tests,
strict Pyright (zero errors/warnings), and Ruff correctness checks across
`simulator_detailed`. Pydantic 2.13.5, SimPy 4.1.2, NumPy 2.5.3,
SciPy 1.18.1, Pyright 1.1.414 and Ruff 0.16.7 are the existing environment.
Torch/PyG are absent; no tensor/checkpoint or hardware measurement is claimed.

```bash
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/python -m ruff check --select E9,F63,F7,F82 simulator_detailed
```

`tests/fixtures/topology_baseline.json` records a pre-graph execution of the
existing custom 3x2 mesh example: fabrics 0/2, PE local port 9, configured DMA
attachments, and a 29-byte PE transfer. Completion is 38.104761905 ACI cycles
on fabric 0 and 40.75 on fabric 2. A factor-three router-0 east-port slowdown
over cycles 0..100 raises fabric-0 completion to 55.438095238; fabric 2 stays
at 40.75. The failure retains paired-direction behavior and both directions
recover. The fixture records route computation times and ordered link identities.
These are simulator regression expectations, not hardware measurements.

`tests/fixtures/topology_reference.json` is a separately hand-authored structural
oracle: two workers, one disabled worker, a memory tile, a transit tile, a directed
non-neighbor hop, and cross-fabric aliases to one memory resource. It also lists
the 14 ordered directed edges of a 3x2 mesh, independently of the new adapter.

## Graph contract

`configs/schemas/topology.py` defines `kind: canonical_topology`, version 1.
Tiles, routers, endpoints, eligible workers and physical resources are separate.
All nested records are frozen and collections are tuples. Availability can be
true, false, or unknown; unresolved permissions do not create port zero.
Network edges occupy a source output and destination input independently; no
reverse edge is inferred. Local and network ports occupy distinct namespaces.
Parallel links require distinct ports and IDs. A disabled compute tile can retain
its network router. Resource capacities are exact bytes, counted once per ID.

`Topology.compile()` revalidates and normalizes records, hashes semantic contents,
and exposes read-only router/link/port index maps. Legacy compatibility indices,
when supplied, must be complete dense router/link bijections. Generic indices
are deterministic and do not imply coordinates. `export()` returns separate
graph records, maps and counts. An unresolved link count is null, distinct from
a complete graph with zero links. Export is inventory, not resource instantiation.

## Legacy execution and adapters

The executable legacy NoC is an XY mesh with dimensions selected by
`NoCConfig.x/y`. A router ID is `y * x_dimension + x`. One PE is attached to
each router on every configured fabric using `pe_local_port`. DMA locations and
ports come from `dma_engines`; no controller location/port map is inferred from
its type. `EndpointRegistry` resolves a type-qualified endpoint ID, fabric and
explicit DMA attachment mode. Addresses include mesh geometry and packet format;
incompatible configurations cannot form a message. Local port IDs are nonnegative
and internal network port IDs are negative. Data uses the selected fabric; credit
return is accounted for as sync-plane timing.

`topology_from_legacy()` generates explicit directed mesh records and old numeric
indices. `Arch` shares the resulting graph with its registry and NoCs. The
registry constructs only declared legacy PE/DMA bindings, never endpoints inferred
from router presence. NoC constructs directed links from this graph and binds each input/output half independently.
Legacy XY uses canonical coordinates and output ports; it no longer regenerates edges.

`topology_from_profile()` preserves the normalized profile and evidence as
immutable canonical JSON with its source hash. The Wormhole example projects to
120 tiles, 240 fabric routers, 240 physical attachments, 72 selected workers and
86 resources. Connectivity, port permissions and undeclared availability remain
unknown. Resource capacity appears once, with source-parameter/evidence references.
The hardware-profile runtime gate is unchanged. Generic graph/replay documents
are explicitly rejected by the legacy execution loader.

## Incremental validation

Part 1: eight focused graph/baseline checks pass; strict Pyright reports zero
errors/warnings; all applicable Ruff checks on the four new Python files pass.
The baseline slowdown observation checks both directions at cycle 1 (factor 3),
an unrelated edge and fabric (factor 1), and recovery (factor 1).
Later runtime requirements remain unchecked in the OpenSpec task list.

Part 2: the full 64-test detailed suite passes, strict Pyright reports zero
errors/warnings, applicable Ruff on new modules/tests and correctness Ruff on
changed existing modules pass. Profile projection is checked against the existing
validated profile inventory; custom mesh timing and paired failure behavior match
the pre-change fixture. Graph ownership is shared; directed construction remains
the next implementation part.

## Directed transport and route safety

`routing.py` separates next-hop selection from router arbitration. Explicit replay
routes resolve fabric-qualified endpoint/port identities, visit each router at most
once, and require enabled contiguous directed edges. A reverse path is never inferred.
Graph flits carry the compiled plan identity and no mesh dimensions. Link admission
checks plan, fabric, format and admitted channel before allocating credits; router
admission checks the resolved input before creating reservations. Legacy FIXPATH
remains unsupported. Local endpoints have explicit resolved transport records.

The route compiler checks the union of channel dependencies across **all admitted
routes**, including injection and ejection channels. A physical ring is allowed only
when the admitted subset is acyclic. A finite packet may hold a switch grant while
waiting for downstream credit; this follows the same input-to-output dependency.
The input credit is returned at switch traversal, reservations do not themselves
block other packets, and output arbitration is round-robin at finite burst boundaries.
The replay runner must serialize whole packets on each source injection channel,
use consistent per-fabric burst resolution, and drain sinks independently in finite
time. These assumptions prevent an additional endpoint or burst-production wait
cycle; this is a restricted model progress argument, not a general torus proof.

Part 3: 70 detailed tests pass, including all-pairs independent XY checks on
1x1/3x2/4x4 meshes, explicit/local paths, cyclic route unions, unchanged state on
invalid admission and atomic/one-sided bindings. All saved legacy routes, completion
times, burst/backpressure behavior and paired slowdown/recovery regressions pass.
Strict Pyright reports zero errors/warnings; applicable new-file Ruff and changed-file
correctness checks pass. The NMC contention benchmark now rejects geometry-free
routers explicitly; its legacy calculations and behavior are unchanged.

## Executable synthetic replay

`replay_topology.py --inspect PATH` accepts canonical topology, legacy architecture,
or hardware-profile inventory. `--replay PATH` accepts only version-one
`topology_replay` input. Its `graph_path` resolves relative to the replay document;
the path is excluded from plan identity. Graph/config/routes and all overrides are
validated before constructing SimPy resources. Unknown fields and unsupported
policies are rejected. Explicit per-fabric settings declare clocks in MHz, physical
flit size/payload/header in bytes, wire/payload width in bits per NoC cycle, router
stages and link/service times in ACI cycles, buffer/window sizes in flits, and burst
quanta in flits. No Wormhole timing constants are embedded. Named network/local
channel overrides replace full link settings, with serialization/window validation.

The runner constructs only enabled routers/edges and explicitly replay-enabled
terminals. Memory/transit routers forward packets; worker eligibility does not
instantiate a core. Sources serialize whole packets; every sink drains independently
with its configured finite service time. Completion requires every transfer, queue,
credit and router grant/reservation to drain. A cycle limit or empty event queue with
pending work yields `incomplete` and pending identities; the deadline is diagnostic,
not a deadlock-avoidance mechanism. A runtime can run only once.

Version-one `topology_replay_result` exports normalized graph/maps, graph and plan
hashes, effective configuration, instantiated identities, byte totals, completions,
and canonical fabric-qualified trace events. Network channels and attachment
injection/ejection channels remain distinct. `packet_physical_bytes` counts padded
flits once per packet; `transmitted_channel_bytes` counts each actual LINK_SEND,
including local channels. Each trace event's physical bytes describes its flit and
must not be summed across different actions. Completion times include sink service;
router INJECT/EJECT events retain the legacy boundary meaning. Memory resources
remain inventory: no memory service or numerical data is executed.

```bash
.venv/bin/python -m simulator_detailed.replay_topology --inspect simulator_detailed/configs/topologies/heterogeneous_example.json
.venv/bin/python -m simulator_detailed.replay_topology --inspect simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/heterogeneous_unicast.json
```

All commands emit JSON on stdout. `--output PATH` also writes the result after
admission and execution; invalid admission creates no result file. Exit status is
0 for inspection/complete replay, 1 for invalid input, 2 for incomplete replay.

Part 4: all 78 detailed tests pass; strict Pyright has zero errors/warnings and
applicable/correctness Ruff passes. The example has five physical tiles, ten routers,
eight network links, six attachments and two physical memory resources (1088 bytes).
It sends 29/25 bytes along the independent a→ram→off→hop→z route on fabrics 0/7,
plus five bytes locally: 59 payload bytes, seven flits, 112 packet physical bytes,
608 channel bytes, and complete drain at cycle 44. Tests check every trace identity,
parallel/disabled edges, 240-byte competing flows with unequal burst quanta and slow
sinks, conserved credits, clock/width serialization and packet boundaries against
analytical expectations, and timeout/premature-idle diagnostics. Profile inspection
and all three published commands pass; generated traces stay outside the repository.

Example graph SHA-256:
`5ef712709c1afbfda6f795e119a4e4f2ce0f0437d7431f2a7be6285ffb15a6cc`.
Example plan SHA-256:
`1a59e3e5b7ed7c6155fe618ca13b025a9908959093d174eb89d9f1e3c8e09fba`.
These identify synthetic test inputs, not hardware validation.

## Existing model consumer contracts

`topology_compatibility.require_legacy_topology()` accepts only named detailed
predictor/encoder consumers and the legacy event contract. It revalidates the graph,
checks the embedded NoC configuration/source hash, and compares full canonical
structure and index maps with the shared legacy adapter. Equal counts or a supplied
compatibility label do not establish support. Changed roles, directed edges,
endpoint semantics or maps are incompatible. `legacy_event_rows()` rejects versioned
graph/replay wrappers and raw replay events before feature construction.

`predictor.topology.Mesh` is now a canonical legacy graph view: its constructor,
sorted fabric order, flattened link indices and intermediate path-node ordering
remain supported. Edges come from the shared adapter, and coordinates come from
canonical records. The detailed builder and predictor accept an optional compiled
`topology=`; supplied dimensions/fabrics must match. `detect()` uses the actual legacy
NoC configuration; the predictor cache includes its graph identity. Guards run before
feature generation/checkpoint loading. Non-Mesh builder inputs fail explicitly.

The detailed predictor keeps **7-D** core and link features: core FLOP/rate/duration
statistics and instruction density; link byte/throughput/per-hop-delay statistics and
communication density. Physical cores are shared across fabrics; directed links are
fabric-qualified. The detailed encoder keeps **4-D** rows `[kind, fabric, x, y]`,
with a router row per fabric and a link row using its source-router coordinates.
Its guard validates the complete fabric set and common topology before tensors.
Existing checkpoint shape errors still require migration/retraining; shape agreement
does not establish predictive accuracy on new hardware. Heterogeneous replay graphs
and replay traces are unsupported by both consumers.

Part 5: all 82 detailed tests pass, including four offline consumer tests and the
legacy runtime regressions. Independent 3x2 expectations verify flattened edge,
relation and XY path-node ordering; same-count role/edge changes and spoofed origins
are rejected. Strict Pyright covers the dependency-free guard/view with zero errors
or warnings. Applicable guard/view/test Ruff, correctness Ruff and Python syntax
checks on the four changed optional-ML modules pass. Their real guard call sites and
unchanged 7-D/4-D feature expressions were inspected. Torch and torch_geometric are
unavailable: **tensor execution, checkpoint loading and model accuracy were not
validated**. No fake-package tests substitute for those checks. Separate top-level
simulator/predictor/embedding/RL code, model files, observations/actions, rewards and
failure/workload datasets are unchanged.

Part-5 follow-up: the late strict-type run flagged a redundant `isinstance` under
a `Topology`-only annotation. The public guard now accepts `object`, narrows it
explicitly, and tests rejection of raw result dictionaries. Four focused consumer
tests, strict Pyright (zero errors/warnings) and applicable Ruff pass after the fix.

Replay accounting follow-up: all packet counts now reuse integer
`compute_flit_count()`. An incomplete transfer above the float exact-integer range
retains its final partial flit in the result, without allocating that packet. Nine
replay tests, strict Pyright and applicable Ruff pass. Example identities/timing
remain unchanged; this does not claim that impractically large traffic was executed.
