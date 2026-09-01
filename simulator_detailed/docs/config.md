# Phase 2 Configuration

The relevant configuration objects are:

- `FlitConfig`: fixed `physical_flit_bytes=512` and
  `payload_capacity_bytes=512`; non-512 values are rejected.
- `RouterPipelineConfig`: effective RC=1, SA=2, and ST=1 ACI cycles.
- `RouterConfig`: XY routing, one VC, burst-level round-robin arbitration, and an
  invariant `default_burst_len_mode=BURST_LEN_7` used only to resolve a
  transfer's `BURST_LEN_DEFAULT` mode.
- `LinkConfig`: `wire_bits_per_noc_cycle=579`,
  `payload_bits_per_noc_cycle=512`,
  `launch_interval_aci_cycles=512/120`,
  `effective_link_stage_aci_cycles=0.5`, a fixed one-flit input buffer, a
  two-flit `effective_in_flight_window_flits`, and a separately calibrated
  `sync_credit_return_aci_cycles`.
- `NMCChannelConfig`: independent TX/RX rates of 120 B/ACI-cycle, a 57-ACI-cycle
  descriptor issue cost, and 24 outstanding descriptors. Fix 9B converts each
  directional rate to a `512 / bytes_per_cycle` flit service interval. Fix 10A
  uses `max_outstanding_descriptors` as the capacity of each channel's runtime
  descriptor slot pool. Fix 10B uses `descriptor_issue_cycles` as the serialized
  posting time before an admitted command enters directional data service.
- `NMCConfig`: explicit `ch0` and `ch1` configurations. There is no shared
  106 B/cycle channel budget. Its `shape_timing` stores the measured static and
  dynamic total endpoint setup targets.
- `NoCConfig`: X=4, Y=8, `aci_clock_mhz=1125`, `noc_clock_mhz=2250`, a validated
  2:1 clock ratio, and separate r2r `link` and PE-side `c2r_link` configs.
- `DMAEngineConfig`: a type-qualified DMA instance, attached router, channel
  count, and corresponding local ports used by `EndpointRegistry`.

Header, CRC, sequence, and tail fields are control metadata. They do not reduce
the confirmed 512 B logical payload capacity used by `Message.flit_count()`.
The size is a hardware invariant, not a simulator tuning parameter.

The four explicit burst modes always resolve to 1, 2, 4, or 8 flits and ignore
the router default. The confirmed hardware `BURST_LEN_DEFAULT` value is
`BURST_LEN_7`, so the canonical configuration resolves it to an eight-flit
arbitration quantum. Contradictory configured values are rejected rather than
silently changing hardware behavior.

The Link derives native serialization from the fixed `FLIT_BYTES` constant as
`512 B * 8 / 512 bits = 8` NoC cycles,
then divides by the validated 2:1 clock ratio to schedule four ACI cycles. The
measured launch interval is a separate calibrated value of approximately 4.267
ACI cycles. The 0.5-ACI-cycle link stage is an effective calibration term, not a
claim about physical wire propagation. The confirmed one-flit downstream input
buffer and effective bounded flow-control window are also separate concepts.
The canonical two-flit window covers a flit in the physical input buffer while
the next flit occupies serializer/link-stage capacity. It is a simulator
parameter, not a claim about another hardware FIFO. A Link rejects a window too
small to sustain its configured zero-load launch interval.

NMC directional service and Link transport are consecutive pipeline stages,
not additive whole-message delays. TX can service the next flit while the Link
transmits the previous flit; RX Link delivery can overlap service of the prior
flit. Sustained throughput is therefore limited by the slowest TX, Link, or RX
rate, with credit backpressure propagating a slow receiver to the sender.

Descriptor posting is a per-command stage, not a per-flit penalty. Commands on
one channel are issued at `descriptor_issue_cycles` intervals, while CH0 and CH1
have independent issuers. A full descriptor pool stalls the oldest command at
the channel issuer until an earlier command completes and releases a slot.

`NMCShapeMode` contains exactly `STATIC` and `DYNAMIC`. `NMCShapeTimingConfig`
stores their measured total per-endpoint setup targets as 79.5 and 125 ACI
cycles. These targets already include descriptor programming, command decode,
outer-sync ACQUIRE, and first-flit injection; they are not additive delays to
place on top of every existing endpoint stage. For source SEND, the simulator
subtracts descriptor posting, first-flit NMC TX service, and nominal PE-link
serialization/stage time, then pipelines only the non-negative residual before
TX service. With default values, the represented time is
`57 + 512/120 + 4 + 0.5 = 65.7667` cycles, leaving residuals of approximately
13.7333 static and 59.2333 dynamic cycles. A deliberately slower modeled stage
can already exceed a target, in which case the residual is zero. `send_with_sync`
is an API used by the measured operations, not a shape mode, and the rb54
204-cycle RTT intercept is benchmark data rather than runtime configuration.

Credits are traced on `NoCPlane.SYNC` while retaining the CH0/CH1 data fabric
whose capacity they return. The default effective credit-return delay is zero
because the current 8.5-ACI-cycle hop calibration already absorbs unresolved
`sync_noc` timing; a nonzero value is available for isolated calibration.

Phase 2 accepts only `Mesh`, `XY`, one VC, and `TransType.SINGLECAST`. The
hardware encodings for FIXPATH, MULTICAST, and BROADCAST are representable, but
packetization rejects their execution until their transport behavior is
implemented. Ambiguous legacy fields such as `clock_mhz`, `phit_bytes`, and
unqualified cycle names are rejected rather than silently interpreted.

`EndpointRegistry` additionally requires each configured DMA to have one local
port per declared channel. Router IDs must match the fixed hardware attachment
map, ordered port layouts must correspond to a supported single-side, local, or
CH0/CH1 interface, and endpoint keys must be unique. The configured port layout
selects a `DMAAttachmentMode`; message construction resolves that mode on an
explicit `NoCChannel` instead of supplying a raw local-port number. Physical
ownership is unique per `(fabric_id, router_id, local_port)`, so matching router
and port IDs on NoC0 and NoC1 are separate hardware attachments.

`RouterFail` and `LinkFail` also carry `fabric_id`. Existing single-fabric
fail-slow datasets default to CH0 during the transition; new CH1 targets must be
explicit. The runtime resolves each target through `Arch.nocs`, so a CH0 failure
cannot alter CH1 router or link state and vice versa.
