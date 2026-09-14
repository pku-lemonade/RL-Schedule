# Link timing and flow control
A link serializes one configured physical flit into native payload-width beats.
The last beat is padded. The clock ratio converts native cycles into simulation
cycles. Configured propagation and credit-return delays are accounted for
separately from the launch interval.

Input-buffer depth and in-flight capacity are independent configuration inputs.
The minimum sustainable window is the ceiling of zero-load residence divided
by launch interval. Configuration validation rejects a smaller window.
Downstream congestion can still extend residence beyond this minimum.

Sending consumes a credit. The receiver returns it after consumption, including
the configured sync delay. Credit traffic does not become payload traffic.
Fault injection scales link timing until the matching recovery operation.

Tracing records serialization, router admission, stalls, first-flit arrival and
packet completion separately. Fabric-qualified link identities remain stable
for the configured mesh.
