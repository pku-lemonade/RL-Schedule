# simulator_detailed Phase 2

`simulator_detailed` is the cycle-level ADA2S-32 simulator. Phase 2 models the
on-chip 4-column by 8-row NoC at flit granularity. It includes independent
runtime resources for both full-duplex PE NMC channels, while descriptor
processing and the Task SEND/RECV packetization path remain later Phase 2 fixes.
DMA endpoint execution remains outside this phase.

The Phase 2 model is deterministic:

- 512-byte payload flits serialized as eight native NoC cycles/four ACI cycles;
- 579 wire bits carrying 512 payload bits per native 2250 MHz NoC cycle;
- one VC and one-flit input buffers;
- credit-based flow control with credit events on the separate SYNC plane;
- native serialization converted into the 1125 MHz ACI simulation timebase;
- an effective calibrated ACI link stage kept separate from physical wire facts;
- deterministic X-first XY routing;
- wormhole packet route reservation from HEAD through TAIL;
- burst-level rotating round-robin output arbitration;
- independent CH0/CH1 PE NMC TX and RX runtime resources;
- mandatory event tracing through `NoCTracer`.

Run the standalone validation suite with a Python environment containing
SimPy and Pydantic:

```bash
python -m unittest simulator_detailed.tests.test_phase2_noc
```

The tests inject flits directly into PE-to-router links. The existing Core and
Task SEND/RECV path remains a Phase 3 responsibility.
