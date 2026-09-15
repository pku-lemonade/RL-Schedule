# Canonical topology and transport evidence

This document tracks the incremental `generic-heterogeneous-topology` delivery.
The first two parts add an immutable graph contract, deterministic inventory export,
profile/mesh adapters and explicit legacy endpoint bindings. Directed graph runtime
construction and explicit-graph replay are not implemented at this point.
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
from router presence. The old NoC edge builder is replaced in the next part.

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
