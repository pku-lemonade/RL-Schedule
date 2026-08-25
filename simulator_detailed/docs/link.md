# Link Model

`Link` is a unidirectional data path with a downstream flit buffer.

The native data NoC carries 512 payload bits on a 579-bit wire beat at 2250 MHz.
A 512-byte flit therefore needs eight native NoC cycles. `Link` converts that to
four cycles in the simulator's 1125 MHz ACI timebase. A separate effective
0.5-ACI-cycle link stage preserves the measured latency calibration without
claiming that value is the physical wire delay. `_out_queue` serializes launches
while delivery runs as a separate SimPy process.

Each downstream buffer slot owns one credit. `send_flit()` consumes a credit
before enqueueing a flit. The receiver calls `ack_credit()` only when the flit
has departed its input buffer. Credit return is a SYNC-plane event and never
enqueues a data flit or consumes data-link bandwidth. Its effective delay is
configurable; the default is zero because unresolved sync-path latency is
already absorbed by the calibrated 8.5-ACI-cycle per-hop behavior.

`scale_link_delay(factor)` scales data serialization and the effective data-link
stage. It does not scale the physically separate sync credit path. Recovery uses
the reciprocal factor. The tracer records DATA-plane sends/receives/stalls and
SYNC-plane credit returns with the owning CH0/CH1 fabric identity.
