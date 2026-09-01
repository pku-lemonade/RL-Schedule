# simulator_detailed Phase 2

`simulator_detailed` is the cycle-level ADA2S-32 simulator. Phase 2 models the
on-chip 4-column by 8-row NoC at flit granularity. It includes independent
runtime resources for both full-duplex PE NMC channels and enforces per-channel
descriptor capacity and posting time. Static and dynamic shape modes and their
measured per-endpoint setup targets are defined. Source SEND metadata reaches
NMC admission without entering Flits, and the non-duplicated residual setup
stage calibrates idle first-flit injection to those targets. The command-level
RECV path, Task SEND/RECV integration, and DMA endpoint execution remain later
work.

Hardware benchmark measurements live in `benchmark_references.py`, outside the
runtime architecture configuration. The rb54 latency and packetization evidence
is separate from the rb56/rb58 32 KB batched-throughput evidence. These values
are validation targets; they cannot select a runtime shape mode or alter static
and dynamic endpoint timing.

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
- independent CH0/CH1 PE NMC TX and RX workers with calibrated directional
  service rates;
- independent CH0/CH1 descriptor issuers with configurable posting time and
  outstanding capacity;
- mandatory event tracing through `NoCTracer`.

Run the standalone validation suite with a Python environment containing
SimPy and Pydantic:

```bash
python -m unittest simulator_detailed.tests.test_phase2_noc
```

The tests cover both direct flit injection and `NMCChannel` packet service. The
existing Core and Task SEND/RECV path remains assigned to Fix 11.
