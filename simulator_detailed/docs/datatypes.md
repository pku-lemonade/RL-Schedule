# NoC Data Types

`FlitType` has four states:

- `SINGLE`: a complete one-flit packet; both `is_head` and `is_tail` are true.
- `HEAD`: first flit of a multi-flit packet.
- `BODY`: interior flit.
- `TAIL`: final flit that releases packet route state and any active burst grant.

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
Its `nmc_shape_mode` is local SEND-command metadata and defaults to `DYNAMIC`;
the source NMC consumes it during command admission.

`Message.packetize()` converts an addressed message into fixed-capacity,
payload-bearing flits. It copies fabric identity and routing from the stored
endpoint addresses and uses the hardware `FLIT_BYTES=512` invariant for both
packet count and Link transfer cost. `FlitConfig` retains the corresponding
fields for explicit configuration validation but rejects any non-512 value.
The source route is simulator metadata used for injection tracing; the
documented hardware routing word carries only the destination route and local
port. Static and dynamic shape modes produce identical Flits because shape
construction changes endpoint setup timing, not packetization or fabric
service. The destination RECV command selects its own shape mode; the sender's
mode is therefore not copied into a Flit.

`DFGNode` stores `fabric_id` and `nmc_shape_mode` for every operation. They are
consumed by SEND and RECV tasks and default to CH0 and dynamic shape for legacy
DFGs. A SEND-to-RECV edge must use one fabric, while the two endpoint shape
modes may differ. The paired RECV node ID is the message ID, keeping parallel
edges globally distinguishable during destination reassembly.

`BurstLenMode` preserves the hardware encodings `BURST_LEN_DEFAULT=-1` and
`BURST_LEN_0/1/3/7=0/1/3/7`. The four explicit modes map to arbitration quanta
of 1, 2, 4, and 8 flits without changing packetization or 512 B transfer cost.
`Message.packetize()` copies the selected mode to every immutable `Flit`. The
confirmed hardware default equals `BURST_LEN_7`; `RouterConfig` therefore
converts `BURST_LEN_DEFAULT` to an eight-flit arbitration quantum while
rejecting contradictory default configuration.

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
fabric-local Router. `NMCChannel` is the corresponding runtime transport object.
It owns separate TX and RX datapath resources and separate data queues while
retaining the immutable physical binding. Hardware data-FIFO depths remain
unknown, so these data queues are unbounded. Each channel separately owns a FIFO
descriptor slot pool with `max_outstanding_descriptors` entries. Every `Core`
owns exactly one runtime channel and one binding for CH0 and CH1.

`NMCTransmitEntry` stores one immutable tuple of packetized flits, the source
command's shape mode, operation-submission time, descriptor-acceptance time,
endpoint-ready time, and its local TX-service completion event. All timestamps
use the ACI-cycle simulation timebase. Descriptor acceptance is recorded after
the configured posting interval. For an idle command, source-router `INJECT`
occurs before the configured 79.5/125-cycle total endpoint target. The source
residual reserves the fixed source-router pipeline, destination PE-link, and
first-flit RX service after injection, so command-level receive completion
reaches that target plus 8.5 ACI cycles per router hop.
`NMCTransmitResult` names submission, descriptor acceptance, endpoint readiness,
final local-Link handoff, and SEND operation completion as separate ACI-cycle
timestamps. The final two timestamps are equal under the current SEND contract,
but remain separate observations so a later completion contract cannot silently
redefine the local handoff boundary. Its operation latency is completion minus
submission.

`NMCReceiveEntry` stores one serviced flit and its ACI-cycle RX-service
completion timestamp. `NMCReceiveResult` stores the validated packet plus
receive command submission, descriptor acceptance, endpoint readiness, matching
TAIL/SINGLE RX-service completion, and operation completion timestamps. RECV
operation latency is also completion minus submission. Neither endpoint result
contains router `INJECT` or `EJECT` times; those belong exclusively to
`MessageFabricTiming`.
`NMCChannel.send()` validates that the message source exactly
matches the channel binding and packetizes before enqueueing, so later mutation
cannot alter an in-flight packet. Its returned process means the packet has
completed NMC TX service and has been handed to the source Link; its process
value is the corresponding `NMCTransmitResult`, not a remote-delivery result.
`recv_message()` posts one independently shaped receive
command, filters incoming entries by message ID, validates packet metadata and
HEAD/BODY/TAIL sequence, and completes no earlier than both endpoint readiness
and TAIL RX service. `recv_flit()` remains a diagnostic API that returns one
serviced flit without posting a receive descriptor. A channel cannot mix these
receive APIs because a raw consumer could steal a command's matching flit.

A TX command must acquire one channel descriptor slot before its immutable flit
tuple enters `tx_data_queue`. The slot remains occupied until local TX service
hands the final flit to the source Link, matching the current SEND completion
boundary. Additional commands wait in FIFO order when the configured capacity
is full. Command-level RECV uses the same channel descriptor issuer and slot
pool, retaining its slot through operation completion. CH0 and CH1 use distinct
pools. `recv_flit()` is not a receive command, so it does not consume one
descriptor per flit.

SEND and RECV setup timestamps are local command boundaries and may overlap
when commands are posted concurrently. A one-way transfer therefore must not
sum both endpoint targets and call that an RTT. The measured RTT fits require
an explicit sequential ping-pong or acknowledgement workload.

Each channel also owns one `descriptor_issuer`. Same-channel commands serialize
through this resource, acquire descriptor capacity in submission order, and
spend `descriptor_issue_cycles` before entering `tx_data_queue`. The issuer is
released after posting so descriptor programming can overlap earlier data
service. Entries remain FIFO while their endpoint residual setup can overlap,
so mixed static/dynamic commands cannot overtake each other and descriptor posts
remain 57 cycles apart. The descriptor slot itself remains held through local
TX completion. Separate CH0 and CH1 issuers allow their posting intervals to
overlap.

The TX worker separately enforces
`inter_command_turnaround_aci_cycles` after a command's final local-Link
handoff. A queued successor waits only for the unelapsed part of that boundary;
if the worker has already been idle long enough, it starts immediately. This is
a size-independent command boundary calibrated from the N=32, 32 KB throughput
measurements, not a per-packet-size branch or an added interval between flits.

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
`link_name` fields. `ROUTER_SA_RELEASE` events also record `grant_flits`; the
trace does not duplicate the full flit type. It retains the derived `is_tail`
marker so a partial live trace cannot be reported as a completed packet. Its
`time` field is always an ACI-cycle timestamp.

`MessageFabricTiming` stores the first router `INJECT`, first destination-router
`EJECT`, and final destination-router `EJECT` timestamps for one completed DATA
packet. First-flit fabric latency is first injection to first ejection. Packet
fabric completion latency is first injection to final ejection. These metrics
exclude descriptor posting, endpoint setup, PE-link delivery before injection,
and NMC RX service after ejection. Timing records are keyed by
`(fabric_id, msg_id)`, so equal router, link, and message IDs on NoC0 and NoC1
remain distinct. `per_msg_latency()` is retained only as a compatibility alias
for packet fabric completion latency.

`NMCBenchmarkScenario` names the supported end-to-end acceptance schedules:
sequential ping-pong, single-channel batch, dual-channel same-direction batch,
and dual-channel full-duplex batch. `BatchedNMCStream` describes one fixed-size
directed command stream. `replay_sequential_ping_pong()` posts each reply only
after the forward RECV completes; its `SequentialPingPongResult` exposes total
operation RTT and the two directional operation latencies. The three batch
helpers post all SEND and matching RECV commands at one simulation timestamp
and perform one final fence. Their `BatchedNMCReplayResult` aggregates payload,
elapsed time, total throughput, and named `BatchedNMCStreamResult` records from
typed endpoint completion timestamps. None of these workload results infer
end-to-end performance from router trace latency.
