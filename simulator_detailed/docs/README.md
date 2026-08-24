# simulator_detailed Phase 2

`simulator_detailed` is the cycle-level ADA2S-32 simulator. Phase 2 models the
on-chip 4-column by 8-row NoC at flit granularity. It intentionally excludes
the Phase 3 PE NMC command and packetization path and all Phase 4 DMA nodes.

The Phase 2 model is deterministic:

- 512-byte flits and 128-byte phits;
- one VC and one-flit input buffers;
- credit-based flow control;
- independent serialization and wire propagation;
- deterministic X-first XY routing;
- wormhole switch reservation from HEAD through TAIL;
- FIFO switch allocation using `simpy.Resource`;
- mandatory event tracing through `NoCTracer`.

Run the standalone validation suite with a Python environment containing
SimPy and Pydantic:

```bash
python -m unittest simulator_detailed.tests.test_phase2_noc
```

The tests inject flits directly into PE-to-router links. The existing Core and
Task SEND/RECV path remains a Phase 3 responsibility.
