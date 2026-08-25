# Mesh Topology

The Phase 2 topology is the ADA2S-32 4-column by 8-row mesh:

```text
id = y * 4 + x
x = id % 4
y = id // 4
```

There are 32 routers and 52 undirected neighbor pairs, represented as 104
unidirectional Links. Every neighbor pair is explicitly bound in both
directions. X-first XY routing first resolves the column and then the row.

PE links are not created by `NoC.build_connection_mesh()`. `Arch.build_cores()`
creates one PE-to-router and one router-to-PE Link for each PE and binds them to
local port 0. Two NMC channel pairs replace these links in Phase 3.

`EndpointRegistry` owns the logical-to-physical attachment map. PE `n` resolves
to router `n`, local port 0. DMA entries are built from `NoCConfig.dma_engines`
and validated against the hardware local-port map. A DMA with more than one
configured local port must be resolved with an explicit port; the registry never
selects a channel implicitly.

For a SINGLE flit crossing `N` inter-router hops, the current calibrated
NoC-only endpoint latency is:

```text
latency = 8.5 * N + 13.0 cycles
```
