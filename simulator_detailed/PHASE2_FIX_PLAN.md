# Phase 2 Dual-Fabric Hardware-Correction Plan

This plan rebuilds Phase 2 around the updated ADA2S-32 transport model in
`NOC_ARCHITECTURE.md`. Each fix is a focused code change with regression tests
and documentation updates, and should be committed independently in the order
listed below.

## Hardware Baseline

Phase 2 must treat the following as its architectural baseline:

- The data plane has two physically independent 4-column by 8-row meshes. NoC0
  and NoC1 each contain 32 routers and 104 directional inter-router links.
  Separate `cfg_noc` and `sync_noc` control planes are not data channels.
- PE NMC CH0 selects NoC0 and CH1 selects NoC1. Router IDs 0-31 are local to a
  fabric; the route ID does not select the fabric.
- Each NMC channel has independent TX and RX datapaths. One channel is
  full-duplex, and the two channels can operate independently.
- A physical flit is 512 B. One native data-NoC beat is 579 wire bits containing
  512 payload bits, so one flit serializes over eight 64 B payload beats at
  2250 MHz. This is four ACI cycles because `noc_clk` is twice `aci_clk`.
- Effective bulk throughput is approximately 117-120 B per ACI cycle per data
  fabric and direction. The 128 B/ACI-cycle raw rate is an effective rate, not a
  1024-bit native physical phit.
- Every SINGLE/HEAD/BODY/TAIL flit carries up to 512 B of payload. Header, CRC,
  sequence, and tail metadata do not reduce this confirmed payload capacity.
- Simulator time is expressed in 1125 MHz ACI cycles because task, NMC, and all
  measured benchmark timestamps use that domain. Native NoC facts use 2250 MHz
  NoC cycles and must be converted explicitly at two NoC cycles per ACI cycle.
- The measured PE-to-PE distance slope remains 8.5 ACI cycles one-way per hop.
  Native router-stage decomposition is not yet self-consistent in
  `NOC_ARCHITECTURE.md`, so Phase 2 must calibrate to the measured slope instead
  of presenting an inferred RC/SA/ST/LT split as confirmed hardware timing.
- Routing is deterministic X-first XY, with one VC per port and round-robin
  output arbitration.
- PE endpoints use local port 0. GM and DDR endpoints have the fixed router and
  local-port mappings documented in `NOC_ARCHITECTURE.md`.
- `TransType` uses the hardware encoding `SINGLECAST=0`, `FIXPATH=1`,
  `MULTICAST=2`, and `BROADCAST=3`.
- NMC descriptor issue costs approximately 57 ACI cycles and the measured
  outstanding depth is approximately 24 descriptors per channel.

Vendor-confirmed facts supersede older derived hypotheses in the architecture
document. Measured behavior and inferred microarchitecture must remain separate.
The `sync_noc` credit path is established, but its exact topology, arbitration,
and latency decomposition are not. Detailed FIFO depths remain unresolved. The
confirmed `BURST_LEN_DEFAULT` value is `BURST_LEN_7`, an eight-flit grant.

## Phase 2 Scope

Phase 2 owns:

- Dual-fabric PE-to-PE flit transport.
- Fabric-aware endpoint addressing and routing.
- Link serialization, bounded backpressure, wormhole flow, and arbitration.
- A calibrated `sync_noc` credit-return abstraction that does not consume data
  NoC payload bandwidth.
- PE NMC channel selection, full-duplex resources, descriptor limits, and
  calibrated PE-to-PE latency profiles.
- End-to-end integration of PE SEND and RECV tasks.
- Fabric, packet, and operation latency tracing.

Phase 2 does not implement `cfg_noc` register traffic, general `sync_noc`
synchronization/fence protocols, GM/DDR memory-controller behavior, multicast
tree replication, in-router reduction, or FIXPATH routing. Their encodings and
attachments must be representable where relevant, but an unsupported operation
must fail explicitly instead of silently using unicast or PE timing. Those
behaviors stay in their later endpoint and collective phases.

## Effect on Completed Fixes

- Fix 1 needs a focused correction: `clock_mhz=1125` must no longer mean both
  ACI and NoC, and `phit_bytes=128` must no longer be described as native wire
  width. The replacement is Fix 3A below.
- Fix 2 remains correct. Endpoint addresses select CH0 or CH1 data fabrics;
  `cfg_noc` and `sync_noc` must not be added to `NoCChannel`.
- Fix 3's fabric propagation, frozen Flits, ownership checks, trace keys, and
  failure targeting remain correct. Link constructors and trace events will need
  the timing/plane additions in Fixes 3A and 3B, but fabric behavior does not
  need redesign.
- Existing four-ACI-cycle serialization, approximately 4.267-ACI-cycle launch
  interval, and 8.5-ACI-cycle hop acceptance values remain numerically valid.
  Their configuration names and physical explanation need correction.
- The current 3 MiB `CoreConfig.spm` default and legacy 4x4/2 GiB architecture
  JSON used by `simulator_detailed.run` do not match ADA2S-32. Fix 3C creates a
  canonical hardware configuration without rewriting unrelated legacy topology
  fixtures.
- The new Matrix/Vector throughput and SRAM-contention measurements are outside
  Phase 2. They expose limitations in the generic `TPUConfig.flops` model, but
  that compute-model redesign must remain a separate phase.

## Fix 1: Correct Hardware Types and Configuration Semantics

Historical status: the original version is implemented; the revised clock and
wire semantics are applied as the small compatibility fix in Fix 3A.

Code changes:

- Correct `TransType` to the hardware values 0/1/2/3 and restore `FIXPATH`.
- Add a strongly typed `NoCChannel` with `CH0=0` and `CH1=1`.
- Split logical flit capacity from native wire width, native payload bits per NoC
  cycle, effective ACI-cycle launch timing, input-buffer depth, and the effective
  flow-control window.
- Represent `aci_clock_mhz=1125` and `noc_clock_mhz=2250` independently and make
  native-NoC-to-ACI conversion explicit.
- Replace the NMC configuration's incorrect shared 106 B/ACI-cycle channel budget
  with per-channel TX/RX and descriptor parameters.
- Reject unsupported transfer types at the Phase 2 transport boundary.

Tests:

- Exact enum values and invalid channel rejection.
- Default physical values: 512 B/flit, 579 wire bits with 512 payload bits per
  NoC cycle, eight NoC serialization cycles/four ACI serialization cycles, two
  data channels, one VC, and one-flit input buffer.

## Fix 2: Make Endpoint Attachments Fabric-Aware

Code changes:

- Add `fabric_id` to immutable `EndpointAddress`.
- Resolve endpoints with an explicit channel/fabric in `EndpointRegistry`.
- Add a `DMAAttachmentMode` selector for dual-side, single-side, and AIU-local
  attachments so callers do not choose undocumented raw local ports.
- Key physical ownership by `(fabric_id, router_id, local_port)` rather than
  `(router_id, local_port)`.
- Register each PE on both fabrics at the same router ID and local port 0.
- Represent GM/DDR attachments on both fabrics while preserving their documented
  channel-specific and single-side local-port mappings.
- Require a `Message` source and destination to belong to the same fabric.

Tests:

- PE0 resolves to `(NoC0, R0, P0)` and `(NoC1, R0, P0)`.
- Identical router/port tuples on different fabrics do not conflict.
- Cross-fabric messages and invalid DMA port/fabric combinations are rejected.
- Dual-side, single-side, and AIU-download selectors resolve only to their
  documented local-port sets. Phase 2 represents AIU-download addresses but
  rejects their execution until the corresponding DMA endpoint model exists.

## Fix 3: Propagate Fabric Identity Through Transport

Code changes:

- Copy the resolved fabric identity into every `Flit`.
- Give each `Link`, `Router`, tracer event, and failure target a fabric ID.
- Require every router and bound link in one `NoC` to share its tracer instance.
- Include the fabric in diagnostic names and trace keys.
- Reject a flit if it is injected into a link or router from another fabric.

Tests:

- Flit packetization preserves one fabric ID from HEAD through TAIL.
- Cross-fabric injection fails before changing router or link state.
- Link binding rejects a different tracer even when its fabric ID matches.
- Trace records distinguish the same router and link IDs on NoC0 and NoC1.

## Fix 3A: Correct Native NoC and ACI Timing Semantics

Code changes:

- Replace the ambiguous `NoCConfig.clock_mhz` with `aci_clock_mhz=1125` and
  `noc_clock_mhz=2250`; expose a validated two-to-one clock ratio.
- Replace `LinkConfig.phit_bytes=128` with native wire/payload fields:
  `wire_bits_per_noc_cycle=579` and `payload_bits_per_noc_cycle=512`.
- Rename `launch_interval_cycles`, `wire_delay_cycles`, and the current router
  pipeline cycle fields to state that their units and values are effective ACI
  timing. Do not describe this calibrated split as native wire/router timing.
- Compute 512 B flit serialization as eight NoC cycles and convert it to four
  ACI cycles before scheduling SimPy events. Keep SimPy's global timebase in ACI
  cycles so core, NMC, and measured operation profiles remain composable.
- Reject ambiguous legacy `clock_mhz`, `phit_bytes`, and unqualified cycle fields
  rather than silently assigning them to one clock domain.
- Update configuration and link documentation to distinguish native wire facts,
  effective ACI timing, and measured launch throughput.

Tests:

- Defaults report 1125 MHz ACI, 2250 MHz NoC, 579 wire bits, and 512 payload bits
  per NoC cycle.
- A 512 B flit serializes in exactly eight NoC cycles and four ACI cycles.
- Native/effective conversion preserves the existing direct-link and 8.5-cycle
  per-hop ACI timing tests.
- Legacy combined-clock, 128 B native-phit, and unqualified timing fields are
  rejected.

## Fix 3B: Separate Data Channels from the Sync Credit Path

Code changes:

- Keep `NoCChannel` restricted to CH0 and CH1 data meshes. Add a separate plane
  identity for DATA, SYNC, and CFG only where timing or tracing needs it.
- Represent flow-control credit return with a `sync_noc` control-path
  abstraction rather than a reverse data flit or consumption of data-link
  payload bandwidth.
- Keep the one-flit downstream buffer and bounded effective credit window. Use a
  calibrated ACI-cycle credit-return parameter until native sync-router timing
  is documented; do not add the measured 17-cycle RTT slope a second time.
- Add plane identity to trace events. A SYNC credit event retains the CH0/CH1
  data channel whose capacity it returns, while data flit events use the DATA
  plane. CFG traffic and general synchronization protocol execution stay outside
  Phase 2.

Tests:

- Returning a credit does not enqueue a data flit or consume CH0/CH1 link
  bandwidth.
- Credit backpressure and recovery preserve the calibrated bounded-window
  behavior without double-counting hop latency.
- Credit traces identify both the SYNC plane and owning CH0/CH1 data channel;
  flit traces identify the DATA plane and channel.

## Fix 3C: Add a Canonical ADA2S-32 Configuration Snapshot

Code changes:

- Change the hardware default PE Local SRAM capacity from 3 MiB to 4 MiB and keep
  Weight SRAM at 16 MiB.
- Create one canonical ADA2S-32 detailed-simulator configuration with a 4x8
  topology, the Fix 3A clock/wire fields, and no 2 GiB scratchpad override.
- Point `simulator_detailed.run` defaults and Phase 2 integration tests at that
  canonical file. Preserve legacy 4x4 topology fixtures for unrelated baseline
  workflows instead of silently reinterpreting them as ADA2S-32.
- Replace the ambiguous DMA `clock_scale` comment/field with explicit endpoint
  clock metadata where it is needed: GM 900 MHz and DDR 1200 MHz relative to the
  1125 MHz ACI simulation timebase. Endpoint bandwidth execution remains outside
  Phase 2.
- Describe the PE-to-router 128 B/ACI-cycle value as an effective interface rate,
  not a native 1024-bit NoC phit.

Tests:

- The canonical configuration validates as 4x8 with 4 MiB Local SRAM and 16 MiB
  Weight SRAM.
- The CLI and architecture-level Phase 2 tests load the canonical configuration,
  while legacy topology fixtures retain their original meanings.
- Clock metadata names its domain explicitly and cannot be mistaken for the
  native 2250 MHz NoC clock.

## Fix 4: Build Two Complete Mesh Instances

Status: implemented. `Arch.nocs` now owns two independent meshes; architecture
traces, simulation return values, hardware embeddings, and predictor identities
all preserve the data-fabric identity. Predictor checkpoints from the
single-fabric topology require retraining or migration for the 208-link output
shape. Dual PE endpoint attachment is implemented by Fix 5 below.

Code changes:

- Keep `NoC` as one self-contained 32-router fabric.
- Construct exactly the two data meshes here. Do not turn `cfg_noc` or
  `sync_noc` into additional `NoCChannel` mesh instances.
- Change `Arch` to construct `nocs[NoCChannel.CH0]` and
  `nocs[NoCChannel.CH1]` with separate routers, links, arbiters, buffers, and
  tracers.
- Remove the previous plan to add lanes inside `Link` or lane-aware state inside
  `Router`.
- Resolve the fabric-qualified router/link fail-slow targets against the new
  `nocs` mapping.
- Migrate trace collection, simulation return values, and other `arch.noc`
  consumers to the explicit `nocs` mapping; do not silently expose CH0 as the
  complete architecture.
- Fabric-qualify predictor link identity and failure labels so equal directional
  link IDs on NoC0 and NoC1 remain distinct.

Tests:

- The architecture contains 64 router objects and 208 directional
  inter-router links.
- No router, link, input buffer, reservation, or arbiter object is shared across
  the two fabrics.
- A failure on NoC0 does not alter NoC1 timing.
- Trace export and simulation results contain both fabrics without link-ID
  collisions.
- Predictor topology and failure labels distinguish the same directional link
  on CH0 and CH1.

## Fix 5: Attach Every PE to Both Fabrics

Status: implemented. Every `Core` owns an immutable-view mapping from CH0 and
CH1 to distinct `PEChannelBinding` objects. Task-level channel selection and the
replacement of obsolete SEND/RECV transport calls remain assigned to Fix 11.

Code changes:

- Replace `Core.data_in`, `Core.data_out`, and `Core.router` with two explicit
  channel bindings.
- Bind CH0 upload/download links to NoC0 and CH1 upload/download links to NoC1.
- Keep local port 0 in each fabric; channel identity comes from the fabric, not
  from the local port.
- Validate that each binding agrees with the endpoint registry.

Tests:

- All 32 PEs have exactly two channel bindings.
- CH0 reaches only NoC0 router `pe_id`; CH1 reaches only NoC1 router `pe_id`.
- TX and RX local links are distinct in each channel.

## Fix 6: Enforce Confirmed 512 B Payload Packetization

Status: implemented. `FLIT_BYTES` is the single packetization and transfer-size
constant. Non-512 `FlitConfig` values are rejected, packetization and Link APIs
no longer accept size overrides, and partial flits retain their actual payload
count while consuming one padded 512 B transfer.

Code changes:

- Define packet count as `max(1, ceil(payload_bytes / 512))`: 512 B is one
  payload flit, 1024 B is two, and 2048 B is four.
- Give every SINGLE/HEAD/BODY/TAIL flit a 512 B payload capacity and a fixed
  512 B transfer cost, including a padded sub-flit transfer and final partial
  flit.
- Keep header, CRC, sequence, and tail fields as control metadata; never deduct
  them from payload capacity or add non-payload framing flits.
- Keep `SINGLE`, `HEAD`, `BODY`, and `TAIL` sequencing and payload conservation.

Tests:

- Boundary packetization and zero-padding wire cost.
- Payload bytes are conserved while transferred bytes are a multiple of 512.
- Control metadata cannot change flit count, payload capacity, or link timing.

## Fix 7: Correct Link Throughput and Bounded Backpressure

Status: implemented. The physical downstream input buffer remains one flit,
while a separately named two-flit effective in-flight window covers serializer,
link-stage, and buffer occupancy. Zero-load launches sustain `512 / 120` ACI
cycles, credit exhaustion is bounded, and fail-slow scaling remains reversible.
The window size follows from the calibrated contract:
`ceil(4.5 / (512 / 120)) = 2`; a one-flit end-to-end window would limit the link
to `512 / 4.5`, approximately 113.78 B/ACI-cycle instead of 120 B/ACI-cycle.

Code changes:

- Keep eight-NoC-cycle/four-ACI-cycle native serialization separate from the
  effective ACI-cycle router/link delay needed to reproduce the measured
  8.5-ACI-cycle one-way hop slope. Do not claim the effective split is the native
  physical stage decomposition.
- Add a calibrated launch interval of `512 / 120`, approximately 4.267 ACI
  cycles, rather than adding credit delay to every zero-load flit.
- Model the one-flit downstream input buffer separately from a bounded effective
  in-flight/credit window. The window includes pipeline occupancy and must not
  be presented as a measured FIFO depth.
- Stop launching when the bounded window is exhausted and return capacity when
  downstream progress permits it through the Fix 3B sync credit path.
- Preserve deterministic delivery and fail-slow scaling.

Tests:

- First direct-link arrival retains four-ACI-cycle serialization plus the
  configured effective ACI link-stage delay.
- Sustained zero-load arrivals approach 120 B per ACI cycle.
- A slow receiver causes bounded credit stalls without loss or unbounded
  in-flight processes.
- Recovery from backpressure restores the calibrated launch interval.

## Fix 8: Implement Explicit Round-Robin Output Arbitration

Status: implemented. Fixes 8A-1 and 8A-2 carry the variable burst contract and
explicitly resolve configured defaults. Fix 8B uses independent rotating
output arbiters and releases temporary switch ownership at burst boundaries.

### Fix 8A-1: Carry the Variable Burst Contract

Status: implemented. Messages and immutable flits preserve the hardware mode,
and explicit modes expose their 1/2/4/8-flit quantum without changing transport
behavior.

Code changes:

- Add the hardware `BurstLenMode` values `BURST_LEN_DEFAULT=-1`,
  `BURST_LEN_0=0`, `BURST_LEN_1=1`, `BURST_LEN_3=3`, and `BURST_LEN_7=7` to
  the Phase 2 data contract.
- Map the explicit modes to arbitration quanta of 1, 2, 4, and 8 flits,
  respectively. Burst mode does not change the fixed 512 B flit size or
  `ceil(payload / 512)` packetization.
- Add `burst_len_mode` to each `Message` and preserve it in immutable flit
  metadata so every traversed output can enforce the same transfer contract.

Tests:

- All explicit enum values resolve to 1, 2, 4, and 8 flits, invalid encodings
  are rejected, and unresolved `DEFAULT` cannot silently become a quantum.
- Message packetization preserves burst metadata without changing flit count,
  payload bytes, transfer bytes, or HEAD/BODY/TAIL sequencing.

### Fix 8A-2: Resolve the Hardware Default

Status: implemented. Explicit transfer modes resolve independently, while
`BURST_LEN_DEFAULT` resolves to the confirmed `BURST_LEN_7` hardware value.

Code changes:

- Resolve `BURST_LEN_DEFAULT` through a validated `BURST_LEN_7` architecture
  value rather than treating it as an unlimited whole-message grant.

Tests:

- Contradictory configured defaults are rejected, DEFAULT resolves to eight
  flits, and explicit transfer modes remain independent of that default.

### Fix 8B: Arbitrate and Release at Burst Boundaries

Status: implemented. Packet route state remains live through TAIL, while each
temporary round-robin grant is limited by the transfer's resolved burst quantum.

Code changes:

- Replace FIFO `simpy.Resource` grants with one rotating arbiter per output port.
- Track requests by input port and advance the pointer whenever a burst grant is
  released.
- Count only successfully transmitted flits against the grant. Release on the
  earlier of the configured 1/2/4/8-flit quantum or TAIL; if the packet has
  remaining flits, retain its route state and request the output again.
- Separate packet route state (HEAD through TAIL) from temporary switch ownership
  (one burst). Preserve wormhole ordering and prevent BODY/TAIL flits from
  bypassing their HEAD.
- Keep arbiters independent per output port, router, and NoC fabric. Do not add
  the old profiling simulator's unmeasured fixed one-cycle burst bubble.
- Defer `shrBufPortPriority` until shared-buffer topology, capacity, allocation,
  release, and priority behavior are known. Do not map it onto the measured
  equal-priority output round-robin arbiter.

Tests:

- Two and three saturated inputs alternate fairly with bounded waiting.
- No requester starves and aggregate output remains near link capacity.
- Different fabrics arbitrate independently.
- A ten-flit packet produces grant lengths `[1, ...]`, `[2, ...]`, `[4, 4, 2]`,
  or `[8, 2]` under the four explicit modes, with TAIL releasing early.
- Competing transfers with different burst modes re-arbitrate at their own burst
  boundaries and preserve each packet's flit order.
- With no competitor, re-arbitration does not add an unmeasured bubble; link
  serialization and per-flit credit behavior remain unchanged.

## Fix 9: Add Full-Duplex PE NMC Channels

Status: implemented. Fix 9A adds runtime channel ownership and independent
directional resources. Fix 9B services immutable flits through those resources
at calibrated rates while preserving endpoint/Link pipeline overlap. Descriptor
capacity remains assigned to Fix 10.

### Fix 9A: Create Independent Runtime Channel Resources

Status: implemented. `PEChannelBinding` remains immutable topology, while every
PE now owns CH0 and CH1 `NMCChannel` runtime objects. Each channel has distinct
TX/RX datapath resources and unbounded data queues. The queues intentionally do
not reuse the measured descriptor depth as an unconfirmed data-FIFO depth.

Code changes:

- Add an `NMCChannel` transport object and create CH0 and CH1 for every PE.
- Give each channel separate TX and RX datapath resources and data queues.
- Do not serialize same-channel TX against RX or CH0 traffic against CH1.

Tests:

- Every PE owns exactly two runtime channels using its existing physical
  bindings.
- TX and RX resources are distinct within one channel and across CH0/CH1.
- Repeated requests serialize only on the same directional resource.
- Runtime channels cannot cross PE, fabric, or SimPy-environment boundaries.

### Fix 9B: Apply Calibrated Directional Data Service

Status: implemented. `NMCChannel.send()` validates exact source ownership,
packetizes once, and queues one ordered packet operation. Independent upload and
download workers apply `512 / bytes_per_cycle` service intervals. Link queues,
router flow control, and NMC service overlap as pipeline stages, so the slower
stage controls sustained throughput. RX returns credit only after directional
service. SEND completion currently means all flits have completed NMC TX service
and entered the source Link; remote packet completion remains assigned to Fix
11.

Code changes:

- Service queued TX and RX data independently at each channel's configured
  bytes-per-ACI-cycle rate without adding that rate serially to an equivalent
  Link delay.
- Preserve streaming overlap between endpoint service and flit injection or
  ejection so steady-state throughput is not double-counted.
- Keep descriptor queue ownership at the channel level unless later evidence
  proves separate TX and RX command queues.
- Keep SRAM/Matrix/Vector contention outside the base transport resource; later
  overlap modeling may apply the measured contention penalty separately.

Tests:

- Single-channel simplex reaches approximately 117-120 B/ACI-cycle bulk.
- Same-channel full duplex approaches twice simplex throughput.
- Dual-channel same-direction traffic approaches 234-240 B/ACI-cycle aggregate.
- Dual-channel full duplex approaches 468 B/ACI-cycle aggregate for bulk
  traffic.
- Slower TX, RX, or PE-side Link rates become the measured bottleneck; a slow RX
  throttles source injection through normal credit backpressure.
- Concurrent packet submissions retain packet and flit order, and a channel
  rejects a message owned by another source endpoint.

## Fix 10: Add Descriptor Limits and Shape-Dependent Endpoint Timing

Status: in progress. Fix 10A enforces the measured per-channel outstanding
descriptor capacity. Fix 10B applies the configurable 57-cycle posting cost
through independent channel-local issuers. The remaining work must model the
two documented shape modes, static and dynamic. `send_with_sync` is the API used
by those commands, not a third latency mode. The rb54 `204 + 17*hops` fit remains
a benchmark result whose exact shape construction is not documented.

### Fix 10A: Enforce Per-Channel Descriptor Capacity

Status: implemented. Every `NMCChannel` owns a FIFO descriptor slot pool sized
by `max_outstanding_descriptors` (24 by default). A TX command acquires one slot
before entering the data-service queue and holds it until local TX completion.
Further commands backpressure at admission when all slots are occupied. CH0 and
CH1 own independent pools. The current receive API is flit-level; RX command
admission will use the same channel pool when Fix 11 introduces receive
descriptors.

Tests:

- Every runtime channel owns an independent descriptor pool with the configured
  capacity.
- Commands up to the configured capacity are admitted, the next command waits,
  and completion releases a slot to the oldest waiter.
- Descriptor gating preserves packet order and does not alter directional data
  service timing.

### Fix 10B: Apply Serialized Descriptor Posting Cost

Status: implemented. Every `NMCChannel` owns an independent single-command
descriptor issuer. Same-channel commands post in FIFO order and each spends
`descriptor_issue_cycles` (57 ACI cycles by default) before entering TX data
service. An older command waiting for descriptor capacity retains issuer order,
so a newer command cannot bypass it. CH0 and CH1 issuers operate concurrently.
The descriptor slot remains occupied after posting until local TX completion.

Tests:

- Same-channel command posting is separated by the configured issue interval,
  independent of payload size.
- CH0 and CH1 can post descriptors over the same time interval.
- Posting remains linear before capacity saturation; when full, the oldest
  command backpressures the same-channel issuer until a slot is released.
- Directional bulk throughput is unchanged because posting is a per-command
  pipeline stage rather than a per-flit service delay.

### Fix 10C-1: Define the Two Shape Modes

Status: implemented. `NMCShapeMode` contains exactly `STATIC` and `DYNAMIC`,
and `NMCShapeTimingConfig` stores their measured total per-endpoint setup
targets. The removed profile draft is rejected by configuration validation.

Code changes:

- Add `NMCShapeMode` with exactly `STATIC` and `DYNAMIC`.
- Define measured endpoint setup targets in the ACI domain: 79.5 cycles for a
  static endpoint and 125 cycles for a dynamic endpoint. These are the halves
  of the measured RTT intercepts 159 and 250.
- Remove `SEND_WITH_SYNC` from latency-profile selection. Do not expose rb54 as
  a runtime mode or store its 204-cycle RTT intercept in the architecture
  configuration.
- Remove the empirical/compositional mode switch. Phase 2 uses one canonical
  staged model so configuration cannot select a combination that double-counts
  descriptor posting.

Tests:

- The shape-mode enum contains only static and dynamic.
- Defaults expose endpoint targets 79.5 and 125 ACI cycles.
- Configuration rejects `send_with_sync`, rb54, and unknown shape modes.

### Fix 10C-2: Carry Shape Mode on Endpoint Commands

Status: implemented for the current SEND command boundary. `Message` carries
the source command's mode with a dynamic compatibility default, and
`NMCTransmitEntry` preserves it through admission without adding it to `Flit`.
The current `recv_flit()` API is not a receive command; Fix 11 will bind an
independently selected mode to command-level receive admission.

Code changes:

- Add shape mode to SEND and RECV command metadata with `DYNAMIC` as the
  compatibility default; static timing requires an explicit static command.
- Pass shape mode to NMC command admission. Do not copy it into `Flit`, because
  shape construction changes endpoint setup but not packetization, routing, or
  fabric service.
- Let send and receive select their modes independently. A mixed static/dynamic
  pair is valid and its endpoint costs are additive.

Tests:

- Existing communication commands resolve to dynamic mode by default.
- Explicit static mode reaches NMC admission unchanged.
- Packetized flits are identical for static and dynamic commands with the same
  payload, route, channel, and burst mode.

### Fix 10C-3: Complete Endpoint Timing Without Double Counting

Status: implemented for source SEND. Command submission and descriptor
acceptance are retained on the admitted entry, while the existing source-router
`INJECT` event defines first fabric injection. The default staged composition
adds only the residual above 57-cycle posting, NMC TX service, and PE-link
delivery to reach 79.5/125 cycles. Receive-side application and complete RTT
acceptance remain blocked on the command-level RECV path in Fix 11.

Code changes:

- Define operation submission, descriptor acceptance, and first fabric
  injection timestamps in the ACI timebase.
- Treat the existing 57-cycle descriptor-posting stage as part of the measured
  79.5/125-cycle endpoint setup target. Add only the residual endpoint delay
  not already represented by descriptor posting, NMC service, and PE-side
  injection stages at the chosen timestamp boundary.
- Apply the same accounting to receive commands when Fix 11 introduces
  command-level receive admission. Never add a whole 79.5/125-cycle endpoint
  delay on top of the existing 57-cycle posting delay.
- Keep router/link fabric timing independent and preserve the measured
  8.5-ACI-cycle one-way hop slope.

Tests:

- Two static endpoints plus fabric timing reproduce `159 + 17*hops` RTT.
- Two dynamic endpoints plus fabric timing reproduce `250 + 17*hops` RTT.
- Mixed endpoints reproduce `79.5 + 125 + 17*hops` RTT.
- Fabric-only timestamps exclude descriptor and endpoint setup time.

### Fix 10C-4: Use rb54 as Benchmark Validation, Not a Mode

Code changes:

- Preserve the rb54 `204 + 17*hops` result as benchmark metadata or a named
  acceptance fixture, not as an `NMCShapeMode` or runtime latency profile.
- Reconstruct rb54's exact command sequence and shape construction before using
  its 204-cycle intercept as an acceptance requirement. Until then, use rb54 to
  confirm the 17-cycle RTT hop slope, fixed 512 B flit packetization, hidden
  serialization through 4 KB, and asymptotic service rate.
- Do not tune endpoint residual delay to 204, because the architecture does not
  identify rb54 as either the static or dynamic command path.

Tests:

- No runtime configuration can select rb54 or `send_with_sync` as a shape mode.
- An rb54-equivalent fixture preserves the measured hop slope and payload-size
  behavior without changing static/dynamic endpoint targets.
- Under the benchmark-equivalent 32 KB batched schedule, effective throughput
  approaches approximately 87 B/ACI-cycle per channel and approximately
  340 B/ACI-cycle for dual-channel full duplex.

## Fix 11: Repair SEND/RECV Integration

Code changes:

- Add explicit channel selection to the DFG/task communication contract, with
  CH0 as a documented compatibility default.
- Replace obsolete `router.start_up_time`, `Link.put(Message)`, and direct
  `Link.get()` calls in `Task.execute()` with `NMCChannel.send()` and receive
  completion APIs.
- Packetize once at the source and reassemble by message ID at the destination;
  complete RECV only after TAIL arrival.
- Reject a channel mismatch between paired SEND and RECV operations.
- Reject GM/DDR and collective paths until their endpoint models are bound.

Tests:

- Architecture-level PE SEND/RECV completes on each channel.
- Concurrent CH0 and CH1 transfers complete without shared fabric state.
- Same-channel opposite-direction SEND/RECV completes concurrently.
- Unsupported endpoint and transfer types fail with explicit errors.

## Fix 12: Correct Trace Semantics and Enforce Acceptance

Code changes:

- Define separate timestamps for operation submission, descriptor acceptance,
  fabric injection, fabric ejection, and operation completion.
- Record each timestamp's ACI-cycle timebase and distinguish CH0/CH1 DATA events
  from SYNC credit events; native NoC-cycle diagnostics must be explicitly
  converted rather than mixed into the same numeric field.
- Report first-flit fabric latency, packet fabric completion latency, and
  end-to-end operation latency under distinct names.
- Update `docs/`, `ADAPTATION_PLAN.md`, and calibration comments to remove the
  single-mesh, lane-aware, shared-NMC-bandwidth, and universal-startup models.
- Add strict types to every Phase 2 file touched by these fixes.

Tests and checks:

- Hop slope: 8.5 ACI cycles per one-way router hop for first-flit fabric latency.
- Per-fabric bulk throughput: approximately 117-120 B/ACI-cycle/direction.
- Dual-fabric same-direction throughput: approximately 234-240 B/ACI-cycle.
- Full aggregate PE capability: approximately 468 B/ACI-cycle in dual-channel
  full-duplex bulk traffic.
- Saturated shared-link traffic is fair and approaches the measured aggregate
  utilization without a hard-coded packet-size exemption.
- The measured `<=8 KB` no-contention result is reproduced only through the
  benchmark's offered load and descriptor timing, not through special-case
  packet-size logic.
- All Phase 2 tests, architecture-level integration tests, Pyright strict, Ruff,
  `compileall`, and `git diff --check` pass.

## Final Phase 2 Acceptance Boundary

Phase 2 is complete when PE-to-PE communication selects the correct physical
fabric, both meshes remain structurally and behaviorally independent, each
fabric is full-duplex, packet and link timing match calibrated measurements,
backpressure is bounded, arbitration is fair, endpoint latency is not
double-counted, native NoC timing is converted consistently into the ACI
simulation timebase, and the real SEND/RECV execution path uses the flit model.

Completion of Phase 2 does not imply calibrated GM/DDR, multicast, broadcast,
FIXPATH, reduction, or compute/SRAM-contention behavior. Those paths must remain
explicitly unsupported until their corresponding endpoint or collective phase
implements the measurements in `NOC_ARCHITECTURE.md`.

## Architecture Traceability

| Architecture requirement | Plan coverage | Phase 2 status |
|---|---|---|
| Two independent data NoC0/NoC1 meshes (§2.1) | Fixes 2-5 | Implement fully |
| Separate CFG, SYNC, and dual-DATA planes (§2.0) | Fixes 3B and 4 | SYNC credit abstraction only; CFG and general sync deferred |
| 4x8 coordinates and fixed endpoint routers (§2.2-2.3) | Fixes 2 and 4 | Implement fully |
| 579-bit wire/512-bit payload native NoC beat (§2.4) | Fixes 1, 3A, and 7 | Preserve native facts and schedule in converted ACI cycles |
| Confirmed 512 B payload/flit with sideband metadata (§2.4, §3.1) | Fixes 1 and 6 | Implement fully |
| Fixed endpoint local-port modes (§2.5) | Fix 2 | Represent and validate |
| Hardware `TransType` encoding (§3.2) | Fix 1 | Encode all; execute unicast only |
| Deterministic XY and one VC (§3.3) | Fixes 1, 4, and 8 | Implement fully |
| Round-robin and variable burst behavior (§3.5, §9.15-9.16) | Fixes 8A-1, 8A-2, and 8B | Implemented; DEFAULT is confirmed as `BURST_LEN_7` |
| Shared-buffer priority (§3.6) | Fix 8B boundary | Deferred until shared-buffer allocation behavior is confirmed |
| One-flit input buffering and sync credit flow (§2.0, §3.7) | Fixes 3B and 7 | Bounded calibrated model; exact sync topology/FIFO internals unresolved |
| 1125 MHz ACI and 2250 MHz NoC clocks (§1.3, §2.4) | Fix 3A | Explicit two-domain conversion; ACI simulation timebase |
| 4 MiB PE Local SRAM and explicit GM/DDR clocks (§1.2-1.3) | Fix 3C | Canonical config only; endpoint execution deferred |
| Two independent, full-duplex NMC channels (§4.1-4.2, §9.19) | Fixes 5 and 9 | Implement fully for PE-to-PE |
| NMC descriptor cost and depth (§9.14) | Fix 10 | Implement measured effective behavior |
| Static/dynamic endpoint timing and rb54 benchmark (§9.1, §9.16) | Fix 10 | Two shape modes; rb54 is validation only; no double counting |
| Link contention and fair sharing (§9.15) | Fixes 8 and 12 | Reproduce through offered load and arbitration |
| Dual-channel bulk and 32 KB throughput (§9.13, §9.19) | Fixes 9, 10, and 12 | Calibration acceptance targets |
| GM/DDR shared internal DMA bottlenecks (§2.5, §9.9-9.10, §9.17) | Fixes 2 and 11 | Addressable but execution deferred |
| Multicast, broadcast, FIXPATH, and reduce (§3.2-3.4, §9.11-9.13) | Fixes 1 and 11 | Encoded or rejected; execution deferred |
| Per-stream FIFO IDs/depths (§3.7) | Fix 7 boundary | Generic bounded flow control only; exact depths remain TBD |
| Outer sync and global fences (§6) | Fix 10 boundary | Reflected by measured endpoint targets; protocol execution deferred |
| Strided/gather NMC addressing (§4.4-4.6) | Fix 11 boundary | Contiguous payload timing only; descriptor shapes deferred |
| Matrix/Vector/SRAM overlap (§9.18-9.20) | Fix 9 boundary | Deferred beyond Phase 2 |
