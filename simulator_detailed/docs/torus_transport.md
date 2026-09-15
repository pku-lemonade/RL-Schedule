# Version-2 torus transport: incremental delivery

The configuration/record contracts and pure topology/route compiler are implemented
(parts 1–2 of `wormhole-dual-noc-routing`). They do **not** execute torus traffic.
The existing CLI still accepts only version-1 replay. Profile inspection remains
inventory-only and full profile/DFG execution remains gated.

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
will apply independently to each admitted traffic class. These declarations do not
allocate queues or generate responses yet.

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

These records are **data contracts, not runtime admission tokens**. They do not yet
prove that routes realize a torus, that ports/targets exist, that the listed packets
and resources exhaust a compiled runtime, or that a trace was produced by execution.
Tests manually construct record fixtures to validate these constraints. The next
parts must compile graph permissions and the dateline dependency policy, validate
envelopes against an admitted plan, and produce complete runtime accounting.

## Validation and remaining scope

Part 1 was developed from `c1655ba`; its implementation, tests and this evidence are
in the commit titled `feat: add versioned torus transport contracts`. The existing
84-test baseline grows to **95 passing detailed tests**, including 11 new contract
tests. Strict Pyright reports **0 errors / 0 warnings**, scoped Ruff passes, and
OpenSpec strict validation and diff checks pass. Commands are recorded in the
[change delivery notes](../../openspec/changes/wormhole-dual-noc-routing/delivery.md).

The new tests cover version/field/unit admission, immutable round trips, role and
slowdown constraints, relative loading, evidence/override resolution, clock extremes,
normalization/hash changes, exact large integer byte metadata, route/envelope/trace
shape, result totals and credit-drain constraints. A deliberately non-torus graph
can pass configuration preparation, demonstrating that this stage does not claim
topology admission. The existing CLI rejects version-2 input without creating an
output artifact. The full suite retains profile gates, version-1 replay and the
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

Still pending: finite VC/credit kernel, cut-through routing, causal response execution,
directed slowdown execution, runtime traces and version-2 CLI integration. Hardware
VC encoding/buddy/priority modes, NIU packetization/transactions, memory/compute
execution, multicast/synchronization, tensor/checkpoint execution and hardware
calibration are not provided by these records.
