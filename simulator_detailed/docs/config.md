# Phase 2 Configuration

The relevant configuration objects are:

- `FlitConfig`: `flit_size=512`, `header_bytes=12`, `body_overhead=4`.
- `RouterPipelineConfig`: RC=1, SA=2, ST=1 cycles.
- `RouterConfig`: XY routing, one VC, FIFO round-robin arbitration.
- `LinkConfig`: `phit_width=128`, `wire_delay=0.5`,
  `buffer_depth=1`, `credit_return_cycles=0.3`.
- `NoCConfig`: X=4, Y=8, separate r2r `link` and PE-side `c2r_link` configs.

`header_bytes` and `body_overhead` describe estimated wire metadata. The
measured packet-count model exposes 512 logical payload bytes per flit, so these
values are not deducted when computing `Message.flit_count()`.

The Link derives serialization time as `flit_size / phit_width`; it is not a
second independently tunable latency. Phase 2 accepts only `Mesh`, `XY`, and
one VC. Legacy topology JSON files are reference artifacts and are not
supported by the Phase 2 NoC implementation.
