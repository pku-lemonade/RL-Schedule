# simulator_detailed NoC Model

`simulator_detailed` is the cycle-level ADA2S-32 simulator. Phase 2 models the
on-chip 4-column by 8-row NoC at flit granularity. It includes independent
runtime resources for both full-duplex PE NMC channels and enforces per-channel
descriptor capacity and posting time. Static and dynamic shape modes and their
measured per-endpoint setup targets are defined. Source SEND metadata reaches
NMC admission without entering Flits, and the non-duplicated residual setup
stage calibrates command-level one-way completion to those intercepts plus the
measured per-hop slope. Router injection occurs earlier so the fixed
post-injection router and destination stages are counted exactly once. The
command-level RECV path independently applies destination readiness timing,
reassembles by message ID, and completes after TAIL RX service. DFG
communication nodes select their channel and endpoint shape mode, and Task
SEND/RECV execution uses these NMC command APIs.

Phase 3 has started with configured GM endpoint ownership and functional command
execution. GM_RDMA can inject a GM-to-PE payload and GM_WDMA can complete a
posted PE-to-GM receive. Each endpoint has one internal datapath shared by CH0
and CH1. Concurrent same-fabric RDMA commands retain per-message flit order and
share that engine round-robin at the configured burst quantum. GM_WDMA descriptor
admission uses the measured 40-cycle post time and independent four-entry CH0/CH1
pools; one endpoint-wide issuer remains a conservative assumption until
cross-channel issuing is measured. Its shared datapath is capped at 110
B/ACI-cycle, small command completions are separated by at least 273 cycles, and
the paired operation floor is `138 + 17 * hops` cycles. A timing-only dual-side
admission gate prevents data from outrunning a full receive-descriptor queue.
GM_RDMA uses a shared 110 B/ACI-cycle service rate and a measured `246 + 17 *
hops` receive-operation floor. Its descriptor timing remains provisional and
single-side request/response traffic is not yet modeled, so GM endpoints remain
intentionally absent from the canonical configuration.

Hardware benchmark measurements live in `benchmark_references.py`, outside the
runtime architecture configuration. The rb53 shared-link measurements, rb54
latency and packetization evidence, GM upload/rb55 and GM download/outcast
measurements, and rb56/rb58 32 KB batched-throughput evidence are separate
validation targets. They cannot
select a runtime shape mode or alter static and dynamic endpoint timing.
`benchmark_workloads.py`
provides typed sequential ping-pong, single-channel batch, shared-link
contention, dual-channel same-direction, and dual-channel full-duplex replay
schedules at the endpoint command boundary.

The Phase 2 model is deterministic:

- 512-byte payload flits serialized as eight native NoC cycles/four ACI cycles;
- 579 wire bits carrying 512 payload bits per native 2250 MHz NoC cycle;
- one VC and one-flit input buffers;
- credit-based flow control with credit events on the separate SYNC plane;
- native serialization converted into the 1125 MHz ACI simulation timebase;
- an effective calibrated ACI link stage kept separate from physical wire facts;
- deterministic X-first XY routing;
- per-message wormhole route state from HEAD through TAIL, including messages
  interleaved on one input by upstream burst arbitration;
- burst-level rotating round-robin output arbitration;
- independent CH0/CH1 PE NMC TX and RX workers with calibrated directional
  service rates;
- independent CH0/CH1 descriptor issuers with configurable posting time and
  outstanding capacity;
- an idle-aware, size-independent TX inter-command turnaround calibrated from
  the N=32, 32 KB schedules, with excess fabric stalls overlapping the fixed
  command bubble;
- mandatory event tracing through `NoCTracer`.

Run the standalone validation suite with a Python environment containing
SimPy and Pydantic:

```bash
python -m unittest \
  simulator_detailed.tests.test_phase2_noc \
  simulator_detailed.tests.test_phase3_dma
```

Run the production Phase 2 strict check from the repository root with:

```bash
pyright --project simulator_detailed/pyrightconfig.phase2.json
```

The tests cover direct flit injection, `NMCChannel` packet service, command-level
receive reassembly, and architecture-owned Core/Task SEND/RECV execution on both
fabrics and in opposite directions. They also replay static, dynamic, and mixed
sequential RTT fits, the rb53 offered-load transition, and the named 32 KB
simplex, dual-channel, and full-duplex throughput schedules.
