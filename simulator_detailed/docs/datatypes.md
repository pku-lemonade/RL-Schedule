# Transport data
`EndpointAddress` contains endpoint type and ID, router, local port, fabric,
DMA attachment mode, mesh dimensions and an immutable `FlitConfig`.

`Message` carries resolved source/destination addresses, a tensor slice,
command protocol, transmission mode, optional fixed path and burst quantum.
The source format determines logical packet capacity and physical transfer cost;
the destination must have the same transport configuration.
Zero-byte commands occupy one flit.

`Flit` carries SINGLE/HEAD/BODY/TAIL state, payload size, format, routing
identity and protocol role. Partial flits consume the configured physical
transfer size. Request/response control flits inherit the same format.

Static and dynamic are the two endpoint shape modes. Sync is part of command
coordination rather than a third shape mode. Dual-side DMA commands wait for
both descriptors. Single-side reads send a request and return payload;
single-side writes return a completion response. Control and payload share
ordinary fabric arbitration.

The executable detailed transport currently supports SINGLECAST. FIXPATH
metadata validates bounds, neighboring hops and a simple path against the
configured geometry, but executable FIXPATH, multicast, broadcast and local
memory DMA modes remain unsupported and fail before admission.

Result objects distinguish descriptor acceptance, endpoint readiness, final
local handoff, tail service and operation completion. They must not be
interchanged when measuring latency.
