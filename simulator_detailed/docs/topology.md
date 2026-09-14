# Topology
The executable NoC is an XY mesh with dimensions selected by `NoCConfig.x/y`.
A router ID is `y * x_dimension + x`. One PE is attached to each router on
every enabled fabric using `pe_local_port`. DMA locations and ports come from
`dma_engines`; no controller location or port map is inferred from its type.

`EndpointRegistry` resolves a type-qualified endpoint ID, fabric and explicit
DMA attachment mode. Its immutable addresses include mesh geometry and packet
format. Addresses from incompatible configurations cannot form a message.

Local ports use nonnegative IDs. Negative direction IDs are simulator-internal
labels and cannot collide with configured local ports. Data travels on the
selected fabric; credit return is accounted for as sync-plane timing.
