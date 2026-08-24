# Link Model

`Link` is a unidirectional phit pipeline with a downstream flit buffer.

For the calibrated defaults, one 512-byte flit needs four serialization cycles
on a 128-byte/cycle link. Wire propagation adds 0.5 cycles. Serialization is
serialized by `_out_queue`, while wire delivery runs as a separate SimPy
process, allowing propagation to overlap with serialization of the next flit.

Each downstream buffer slot owns one credit. `send_flit()` consumes a credit
before enqueueing a flit. The receiver calls `ack_credit()` only when the flit
has departed its input buffer. Credit return takes 0.3 cycles. With immediate
consumption, the steady-state receive gap is therefore 4.8 cycles, or about
106.7 bytes/cycle.

`scale_link_delay(factor)` scales serialization, wire, and credit-return delay.
Recovery uses the reciprocal factor. The tracer is mandatory and records link
sends, receives, credit stalls, and credit returns.
