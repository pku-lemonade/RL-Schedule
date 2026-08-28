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
wire metadata and do not reduce this logical capacity in the simulator. A
partial flit's `payload_bytes` records only meaningful data, while its
`transfer_bytes` is always 512 B because the hardware transfer is padded.

`Flit` is immutable after construction. It carries one message fabric identity
alongside payload size, message ID, source/destination router IDs, local ports,
and future multicast/reduction metadata. Phase 2 routes Flits directly.

`EndpointAddress` is an immutable, type-qualified endpoint identity plus its
resolved `(fabric_id, router_id, local_port)` attachment. Its `node_id` is local
to the node type: PE IDs are 0-31 and each DMA type has instance IDs 0-3.
`Message` stores source and destination `EndpointAddress` values when it is
initialized, avoiding independent fabric, node, router, and port fields that
could contradict one another. Both addresses must belong to the same fabric.

`Message.packetize()` converts an addressed message into fixed-capacity,
payload-bearing flits. It copies fabric identity and routing from the stored
endpoint addresses and uses the hardware `FLIT_BYTES=512` invariant for both
packet count and Link transfer cost. `FlitConfig` retains the corresponding
fields for explicit configuration validation but rejects any non-512 value.
The source route is simulator metadata used for injection tracing; the
documented hardware routing word carries only the destination route and local
port.

`BurstLenMode` preserves the hardware encodings `BURST_LEN_DEFAULT=-1` and
`BURST_LEN_0/1/3/7=0/1/3/7`. The four explicit modes map to arbitration quanta
of 1, 2, 4, and 8 flits without changing packetization or 512 B transfer cost.
`Message.packetize()` copies the selected mode to every immutable `Flit`. The
hardware default remains unresolved; `RouterConfig` converts
`BURST_LEN_DEFAULT` only when an explicit fallback mode is configured and
otherwise raises instead of silently granting the whole message.

Addresses should come from the architecture-owned `EndpointRegistry`. It rejects
out-of-topology routers, invalid type/port combinations, duplicate physical port
bindings, and unresolved DMA paths. PE CH0 and CH1 both use local port 0 but are
distinct addresses because fabric identity is part of the attachment. DMA
lookups use `DMAAttachmentMode.DUAL_SIDE`, `SINGLE_SIDE`, or `AIU_LOCAL`; the
registry maps those modes to documented local ports on the requested fabric.
AIU addresses are representable, but Phase 2 packetization rejects them until
an AIU DMA endpoint model exists.

`PEChannelBinding` is a concrete physical attachment, not a type or mode. It
groups one PE address with distinct TX and RX Links and the matching
fabric-local Router. Every `Core` must own exactly one binding for CH0 and one
for CH1.

Payload direction is also validated: PE and RDMA endpoints may inject data, while
PE and WDMA endpoints may consume it. Control-plane requests that trigger RDMA
work are not payload `Message` objects and belong to the later endpoint model.

`NoCChannel` uses the hardware channel values `CH0=0` and `CH1=1`.
`DMAAttachmentMode` describes attachment topology and has no hardware numeric
encoding.
`TransType` uses the routing-word encoding `SINGLECAST=0`, `FIXPATH=1`,
`MULTICAST=2`, and `BROADCAST=3`. Phase 2 currently executes only SINGLECAST;
packetization raises `NotImplementedError` for the other representable types so
they cannot silently follow the unicast path.

`FlitEvent` stores `fabric_id` with explicit `out_port` and fabric-qualified
`link_name` fields. It intentionally does not duplicate flit type or tail state.
`NoCTracer.per_msg_latency()` keys results by `(fabric_id, msg_id)`, so equal
router, link, and message IDs on NoC0 and NoC1 remain distinct. Callers inspect
`NoCTracer.events` directly with normal list comprehensions.
