# Router Model

Each router owns one forwarder process per bound input port. HEAD and SINGLE
flits establish immutable packet route state. BODY and TAIL flits reuse that
route, but request the output again whenever the previous burst grant ended.

The pipeline is:

1. RC: 1 cycle, X-first deterministic XY routing.
2. SA: rotating round-robin arbitration among requesting input ports.
3. Credit return: issued after SA grant and before switch traversal.
4. ST: 1 cycle.
5. Flit enqueue to the selected output Link.

Every output port has its own `RoundRobinArbiter`. The pointer advances after
each released grant, so continuously backlogged inputs take turns without
sharing state across outputs, routers, or NoC fabrics.

Packet route state remains reserved from HEAD through TAIL. Temporary switch
ownership lasts until the earlier of TAIL or the resolved 1/2/4/8-flit burst
quantum. Only flits accepted by the output Link count against the grant. A
packet with remaining flits keeps its route and requests another grant; this
prevents BODY/TAIL bypass while allowing competing packets to alternate.

Re-arbitration adds no fixed bubble. A HEAD or a grant that waited behind a
competitor pays the configured SA delay. With no waiting competitor, a
continuing packet is re-granted at the same simulation time and resumes without
a second SA delay. An input blocked in SA retains its input credit, preserving
head-of-line backpressure.

`ROUTER_SA_RELEASE` trace events record `grant_flits`, allowing exact burst
lengths and early TAIL release to be inspected.

Ports are integer IDs. Directional ports are 100-103; local endpoint ports are
below 100. `is_edge` is represented as `Dict[Direction, bool]`.
