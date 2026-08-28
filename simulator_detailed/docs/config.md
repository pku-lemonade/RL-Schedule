# Phase 2 Configuration

The relevant configuration objects are:

- `FlitConfig`: fixed `physical_flit_bytes=512` and
  `payload_capacity_bytes=512`; non-512 values are rejected.
- `RouterPipelineConfig`: effective RC=1, SA=2, and ST=1 ACI cycles.
- `RouterConfig`: XY routing, one VC, FIFO round-robin arbitration, and an
  optional explicit `default_burst_len_mode` used only to resolve a transfer's
  `BURST_LEN_DEFAULT` mode.
- `LinkConfig`: `wire_bits_per_noc_cycle=579`,
  `payload_bits_per_noc_cycle=512`,
  `launch_interval_aci_cycles=512/120`,
  `effective_link_stage_aci_cycles=0.5`, a fixed one-flit input buffer, a
  two-flit `effective_in_flight_window_flits`, and a separately calibrated
  `sync_credit_return_aci_cycles`.
- `NMCChannelConfig`: independent TX/RX rates of 120 B/ACI-cycle, a 57-ACI-cycle
  descriptor issue cost, and 24 outstanding descriptors.
- `NMCConfig`: explicit `ch0` and `ch1` configurations. There is no shared
  106 B/cycle channel budget.
- `NoCConfig`: X=4, Y=8, `aci_clock_mhz=1125`, `noc_clock_mhz=2250`, a validated
  2:1 clock ratio, and separate r2r `link` and PE-side `c2r_link` configs.
- `DMAEngineConfig`: a type-qualified DMA instance, attached router, channel
  count, and corresponding local ports used by `EndpointRegistry`.

Header, CRC, sequence, and tail fields are control metadata. They do not reduce
the confirmed 512 B logical payload capacity used by `Message.flit_count()`.
The size is a hardware invariant, not a simulator tuning parameter.

The four explicit burst modes always resolve to 1, 2, 4, or 8 flits and ignore
the router fallback. Because the hardware meaning of `BURST_LEN_DEFAULT` remains
unknown, the canonical configuration leaves `default_burst_len_mode` unset. A
router can still resolve every explicit transfer mode, but resolving
`BURST_LEN_DEFAULT` raises until an explicit fallback is configured. The
fallback itself cannot be `BURST_LEN_DEFAULT`, preventing recursive or silent
whole-message interpretation.

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
