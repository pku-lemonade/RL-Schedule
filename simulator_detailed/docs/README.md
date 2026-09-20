# Detailed simulator
The simulator models configurable mesh fabrics, PE channels, DMA protocols,
arbitration, credit flow and fault timing. Shipped defaults and examples are
synthetic.

The separate [canonical topology and replay entry point](topology.md) also executes
finite synthetic unicast traffic on heterogeneous directed graphs. It supports
profile inventory inspection. [Version-2 torus replay](torus_transport.md) adds
bounded unicast, causal response fixtures and directed slowdowns over an explicit
Wormhole transport binding. [Addressed memory replay](memory_transactions.md) adds
explicit reads/writes, shared memory service, local clients and ordering.
[Finite compute workloads](compute_dataflow.md) execute configured FC/matmul
costs with transaction-backed readers/writers and bounded overlapping streams.
[Finite multicast and local synchronization](multicast_sync.md) compose bounded
rectangle trees, scalar counters, local waits and compute on shared resources.
Numerical/kernel execution, general Wormhole workloads and silicon timing
calibration remain unvalidated. The [validation harness](validation.md) adds
independent offline audits, explicit reference import and synthetic bounded
calibration demonstrations.

- [Configuration](config.md)
- [Hardware profile inspection and current Wormhole limits](hardware_profile.md)
- [Topology and endpoint mapping](topology.md)
- [Version-2 torus transport, examples and limitations](torus_transport.md)
- [Addressed memory replay, accounting and limits](memory_transactions.md)
- [Finite compute workloads, examples and compatibility](compute_dataflow.md)
- [Finite multicast, synchronization, shared runtime and validation](multicast_sync.md)
- [Validation, evidence tiers, reference import and calibration](validation.md)
- [Transport data and supported modes](datatypes.md)
- [Link timing](link.md)
- [Router arbitration](router.md)

Run regressions with `.venv/bin/python -m unittest discover -s simulator_detailed/tests`.
Run strict type checking with `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`.
