# Phase 2 Configuration

The relevant configuration objects are:

- `FlitConfig`: `physical_flit_bytes=512` and
  `payload_capacity_bytes=512`.
- `RouterPipelineConfig`: RC=1, SA=2, ST=1 cycles.
- `RouterConfig`: XY routing, one VC, FIFO round-robin arbitration.
- `LinkConfig`: `phit_bytes=128`, `launch_interval_cycles=512/120`,
  `wire_delay_cycles=0.5`, `input_buffer_depth_flits=1`, and
  `flow_control_window_flits=1`.
- `NMCChannelConfig`: independent TX/RX rates of 120 B/cycle, a 57-cycle
  descriptor issue cost, and 24 outstanding descriptors.
- `NMCConfig`: explicit `ch0` and `ch1` configurations. There is no shared
  106 B/cycle channel budget.
- `NoCConfig`: X=4, Y=8, `clock_mhz=1125`, and separate r2r `link` and PE-side
  `c2r_link` configs.
- `DMAEngineConfig`: a type-qualified DMA instance, attached router, channel
  count, and corresponding local ports used by `EndpointRegistry`.

Header, CRC, sequence, and tail fields are control metadata. They do not reduce
the confirmed 512 B logical payload capacity used by `Message.flit_count()`.

The Link derives physical serialization as
`physical_flit_bytes / phit_bytes = 4` cycles. The measured launch interval is
a separate calibrated value of approximately 4.267 cycles. Wire delay, the
confirmed one-flit downstream input buffer, and the effective bounded
flow-control window are also separate configuration concepts. The window is a
simulator parameter, not a claim about an additional hardware FIFO.

Phase 2 accepts only `Mesh`, `XY`, one VC, and `TransType.SINGLECAST`. The
hardware encodings for FIXPATH, MULTICAST, and BROADCAST are representable, but
packetization rejects their execution until their transport behavior is
implemented. Ambiguous legacy transport field names are rejected rather than
silently interpreted.

`EndpointRegistry` additionally requires each configured DMA to have one local
port per declared channel. Router IDs must match the fixed hardware attachment
map, ordered port layouts must correspond to a supported single-side, local, or
CH0/CH1 interface, and endpoint keys must be unique. The configured port layout
selects a `DMAAttachmentMode`; message construction resolves that mode on an
explicit `NoCChannel` instead of supplying a raw local-port number. Physical
ownership is unique per `(fabric_id, router_id, local_port)`, so matching router
and port IDs on NoC0 and NoC1 are separate hardware attachments.
