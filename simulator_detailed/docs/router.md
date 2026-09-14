# Router
The detailed router supports XY routing and round-robin arbitration.
Route computation, switch allocation and switch traversal delays are explicit
`RouterPipelineConfig` inputs.

HEAD establishes packet state and TAIL releases it. Burst arbitration may yield
the output before TAIL while retaining route state. Each packet uses its own
positive burst quantum, or resolves zero through the router default.
Waiting for output credit does not consume the quantum.

Invalid packet-state transitions, changed routing identity, incompatible
formats and cross-fabric transfers fail before consuming transport resources.
