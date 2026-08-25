# Mesh Topology

The Phase 2 topology is the ADA2S-32 4-column by 8-row mesh:

```text
id = y * 4 + x
x = id % 4
y = id // 4
```

Each fabric has 32 routers and 52 undirected neighbor pairs, represented as 104
directional Links. `Arch.nocs` contains independent CH0 and CH1 meshes, for 64
router objects and 208 directional inter-router Link objects in total. Every
neighbor pair is explicitly bound in both directions. X-first XY routing first
resolves the column and then the row. Each `NoC` object owns exactly one
`NoCChannel`; all of its routers, links, tracer events, and flits must carry that
same fabric identity. Link diagnostics are prefixed with `CH0:` or `CH1:`. Flit
movement is traced on `NoCPlane.DATA`; credit returns are traced on
`NoCPlane.SYNC` while retaining the data fabric ID.

PE links are not created by `NoC.build_connection_mesh()`. `Arch.build_cores()`
creates one PE-to-router TX Link and one router-to-PE RX Link for every PE on
each fabric, producing 128 distinct endpoint Links. Both fabrics use local port
0 in their own router instances. A `Core` exposes these attachments through its
read-only `channel_bindings` mapping; each `PEChannelBinding` contains the
resolved endpoint address, TX Link, RX Link, and fabric-local Router.

The legacy task contract names CH0 explicitly until Fix 11 adds channel
selection to DFG communication operations. This compatibility choice does not
merge or alias the two physical PE bindings.

`EndpointRegistry` owns the logical-to-physical attachment map. PE `n` resolves
on both NoC0 and NoC1 to router `n`, local port 0. DMA entries are built from
`NoCConfig.dma_engines` and validated against the hardware local-port map. Every
lookup names an explicit fabric, and DMA lookups also name a dual-side,
single-side, or AIU-local attachment mode. The registry maps that mode to a
local port; callers do not select a raw port.

The `Arch` runtime constructs both meshes and does not expose a single-fabric
`arch.noc` compatibility alias. Architecture-level consumers use the explicit
fabric mapping. Transport rejects a foreign-fabric flit before consuming credit
or reserving router state.

For a SINGLE flit crossing `N` inter-router hops, the current calibrated
NoC-only endpoint latency is:

```text
latency = 8.5 * N + 13.0 ACI cycles
```
