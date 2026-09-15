# Canonical topology and transport evidence

This document tracks the incremental `generic-heterogeneous-topology` delivery.
The first part adds an immutable graph contract and deterministic inventory export;
runtime adapters and explicit-graph replay are not implemented at this point.
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

## Incremental validation

Part 1: eight focused graph/baseline checks pass; strict Pyright reports zero
errors/warnings; all applicable Ruff checks on the four new Python files pass.
The baseline slowdown observation checks both directions at cycle 1 (factor 3),
an unrelated edge and fabric (factor 1), and recovery (factor 1).
Later runtime requirements remain unchecked in the OpenSpec task list.
