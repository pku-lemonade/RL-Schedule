# Detailed simulator
The simulator models configurable mesh fabrics, PE channels, DMA protocols,
arbitration, credit flow and fault timing. Shipped defaults and examples are
synthetic.

- [Configuration](config.md)
- [Topology and endpoint mapping](topology.md)
- [Transport data and supported modes](datatypes.md)
- [Link timing](link.md)
- [Router arbitration](router.md)

Run regressions with `python -m unittest discover -s simulator_detailed/tests`.
Run strict type checking with `python -m pyright --project simulator_detailed/pyrightconfig.phase2.json`.
