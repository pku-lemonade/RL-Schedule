# Link Model

`Link` is a unidirectional data path with a downstream flit buffer.

The native data NoC carries 512 payload bits on a 579-bit wire beat at 2250 MHz.
A 512-byte flit therefore needs eight native NoC cycles. `Link` converts that to
four cycles in the simulator's 1125 MHz ACI timebase. A separate effective
0.5-ACI-cycle link stage preserves the measured latency calibration without
claiming that value is the physical wire delay. `_out_queue` serializes launches
while delivery runs as a separate SimPy process. The first flit therefore
arrives after 4.5 ACI cycles, while sustained zero-load launches are separated
by `512 / 120`, approximately 4.267 ACI cycles.

The physical downstream input buffer has exactly one slot. A separate bounded
effective in-flight window defaults to two flits: one may occupy that buffer
while another occupies serializer/link-stage capacity. This window includes
pipeline occupancy and is not a second measured FIFO. `send_flit()` consumes an
in-flight credit before enqueueing a flit, and the receiver calls `ack_credit()`
only after the flit has departed its input buffer. Exhausting both credits stops
further launches and bounds outstanding data. Credit return is a SYNC-plane
event and never enqueues a data flit or consumes data-link bandwidth. Its
effective delay is configurable; the default is zero because unresolved
sync-path latency is already absorbed by the calibrated 8.5-ACI-cycle per-hop
behavior.

The two-flit effective window is required by the configured latency and
bandwidth, rather than chosen as an extra buffering assumption. A 120-B/ACI-cycle
link must accept a new 512-byte flit every `512 / 120`, approximately 4.267 ACI
cycles. The first flit occupies the modeled serializer and link stage for
`4.0 + 0.5 = 4.5` ACI cycles before its credit can be returned. A one-flit
end-to-end window would therefore force launches 4.5 cycles apart and cap
throughput at `512 / 4.5`, approximately 113.78 B/ACI-cycle. The minimum window
that preserves the launch rate is
`ceil(4.5 / (512 / 120)) = 2`. This permits pipeline overlap while retaining one
physical input-buffer slot and a strict two-flit bound on unacknowledged data.
If hardware truly allowed only one unacknowledged flit across the complete link,
the specified 4.5-cycle latency and 120-B/ACI-cycle sustained bandwidth could
not both hold.

`scale_link_delay(factor)` scales data serialization and the effective data-link
stage. It does not scale the physically separate sync credit path. Recovery uses
the reciprocal factor. The tracer records DATA-plane sends/receives/stalls and
SYNC-plane credit returns with the owning CH0/CH1 fabric identity.

Fabric latency is measured at router boundaries rather than Link queue
boundaries. `NoCTracer` reports first `INJECT` to first `EJECT` as first-flit
fabric latency and first `INJECT` to final `EJECT` as packet fabric completion
latency. Both values are ACI cycles and exclude endpoint command timing.
