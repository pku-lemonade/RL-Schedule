# Detailed simulator
The simulator models configurable mesh fabrics, PE channels, DMA protocols,
arbitration, credit flow and fault timing. Shipped defaults and examples are
synthetic.

The separate [canonical topology and replay entry point](topology.md) also executes
finite synthetic unicast traffic on heterogeneous directed graphs. It supports
profile inventory inspection. [Version-2 torus replay](torus_transport.md) adds
bounded unicast, causal response fixtures and directed slowdowns over an explicit
Wormhole transport binding. [Addressed memory replay](memory_transactions.md) adds
explicit reads/writes, shared memory service, local clients and ordering. Full
Wormhole workloads and silicon timing calibration remain unavailable.

- [Configuration](config.md)
- [Hardware profile inspection and current Wormhole limits](hardware_profile.md)
- [Topology and endpoint mapping](topology.md)
- [Version-2 torus transport, examples and limitations](torus_transport.md)
- [Addressed memory replay, accounting and limits](memory_transactions.md)
- [Transport data and supported modes](datatypes.md)
- [Link timing](link.md)
- [Router arbitration](router.md)

Run regressions with `.venv/bin/python -m unittest discover -s simulator_detailed/tests`.
Run strict type checking with `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`.
