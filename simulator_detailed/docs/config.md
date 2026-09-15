# Configuration
All defaults describe a small synthetic device. They are not a hardware profile.

Single-ASIC hardware descriptions use a separate versioned document with
`kind: "hardware_profile"`. See [hardware profiles](hardware_profile.md) for the
Wormhole B0 example, inspection command, parameter provenance and execution
gate. Hardware profiles do not populate these synthetic runtime defaults.

`NoCConfig` selects mesh dimensions, enabled `fabric_ids`, PE local port,
simulation and network clocks, links, routers, and DMA attachments. Fabric IDs
are nonnegative integers. Each selected fabric owns an independent mesh.
`CoreConfig` supplies memory capacities, compute rates and NMC service settings.

`router.flit` configures physical transfer size, logical payload capacity and
header metadata size. Payload capacity may be smaller than physical size.
Packet count is `max(1, ceil(payload_bytes / payload_capacity_bytes))`.
Headers do not subtract from the configured logical capacity. The physical
size drives link serialization and PE/DMA service costs.

`LinkConfig` selects wire and payload widths, launch interval, propagation
stage, credit-return delay, receiver buffer depth and in-flight capacity.
Serialization rounds up to complete native beats and uses
`noc_clock_mhz / aci_clock_mhz` for conversion to simulation cycles. Launch
interval cannot be shorter than serialization, and the in-flight window must
cover configured residence time.

`RouterConfig.default_burst_len_mode` is a positive number of flits. A message
or flit value of zero (`BurstLenMode.DEFAULT`) selects that default; a positive
value overrides it. The values are simulator quanta, without wire encodings.
The router arbitrates round-robin at burst boundaries and releases a short
final burst at TAIL.

`core.nmc.channel_default` supplies TX/RX rates, descriptor capacity, descriptor
issue time and turnaround. `overrides` selects alternative settings by fabric
ID. `shape_timing` configures static/dynamic total endpoint setup targets.
Already represented transport work is subtracted, with remaining setup
clamped to zero. These are optional model inputs, not built-in measurements.

Each `DMAEngineConfig` supplies its type, instance ID, router, attachment mode,
enabled fabrics and local ports. Supply one port reused on each selected fabric
or one port per fabric; `channels` must equal the number of supplied ports.
Instance IDs and ports have no device-specific upper bound. Physical port
ownership and router/fabric bounds are validated before construction.

DMA clocks, payload bandwidth, clock-crossing penalty, descriptor timing,
descriptor capacity, internal datapath count and whether descriptor issuers
are shared are all configuration choices. Payload execution requires
`port_bw`; paired command execution also requires descriptor settings.
Missing execution inputs raise an error instead of selecting a measured value.

Configuration is selected when constructing an architecture. Old profiles and
numeric hardware encodings are not migration inputs; create a new configuration
using the current schema. See [the synthetic example](../configs/instances/mesh_example.json).
