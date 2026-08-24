# Router Model

Each router owns one forwarder process per bound input port. HEAD and SINGLE
flits perform route computation and switch allocation. BODY and TAIL flits use
the input port's existing reservation.

The pipeline is:

1. RC: 1 cycle, X-first deterministic XY routing.
2. SA: FIFO arbitration through a capacity-one `simpy.Resource`.
3. Credit return: issued after SA grant and before switch traversal.
4. ST: 1 cycle.
5. Flit enqueue to the selected output Link.

A HEAD keeps the output resource until its TAIL is sent. SINGLE acquires and
releases the resource in one traversal. An input flit blocked in SA retains its
input-buffer credit, creating hardware-like head-of-line backpressure.

Ports are integer IDs. Directional ports are 100-103; local endpoint ports are
below 100. `is_edge` is represented as `Dict[Direction, bool]`.
