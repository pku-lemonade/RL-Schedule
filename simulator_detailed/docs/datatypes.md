# NoC Data Types

`FlitType` has four states:

- `SINGLE`: a complete one-flit packet; both `is_head` and `is_tail` are true.
- `HEAD`: first flit of a multi-flit packet.
- `BODY`: interior flit.
- `TAIL`: final flit that releases the switch reservation.

`is_head` and `is_tail` are derived properties, not stored booleans.

Logical payload accounting follows the measured transfer-size behavior: each
flit carries up to 512 logical payload bytes. Therefore 512 B uses one flit,
1024 B uses two, and 2048 B uses four. The estimated header and CRC sizes are
wire metadata and do not reduce this logical capacity in the simulator.

`Flit` carries payload size, message ID, source/destination router IDs, local
ports, and future multicast/reduction metadata. Phase 2 routes Flits directly.

`EndpointAddress` is an immutable, type-qualified endpoint identity plus its
resolved `(router_id, local_port)` attachment. Its `node_id` is local to the node
type: PE IDs are 0-31 and each DMA type has instance IDs 0-3. `Message` stores
source and destination `EndpointAddress` values when it is initialized, avoiding
independent node, router, and port fields that could contradict one another.

`Message.packetize(flit_config)` converts an addressed message into
payload-bearing flits. It copies routing from the stored endpoint addresses and
uses `FlitConfig.flit_size` as payload capacity. The source route is simulator
metadata used for injection tracing; the documented hardware routing word carries
only the destination route and local port.

Addresses should come from the architecture-owned `EndpointRegistry`. It rejects
out-of-topology routers, invalid type/port combinations, duplicate physical port
bindings, and ambiguous multi-port DMA lookups. PE CH0 and CH1 both use local port
0, so lane identity cannot be represented by `EndpointAddress`; it is added by
Fix 5 at the transport layer.

Payload direction is also validated: PE and RDMA endpoints may inject data, while
PE and WDMA endpoints may consume it. Control-plane requests that trigger RDMA
work are not payload `Message` objects and belong to the later endpoint model.

`TransType` is not serialized or interpreted by Phase 2. Its current internal
values therefore must not be treated as the hardware on-wire encoding; that
encoding must be reconciled with `NOC_ARCHITECTURE.md` before multicast work.

`FlitEvent` uses explicit `out_port` and `link_name` fields. It intentionally
does not duplicate flit type or tail state. Callers inspect `NoCTracer.events`
directly with normal list comprehensions.
