# NoC Data Types

`FlitType` has four states:

- `SINGLE`: a complete one-flit packet; both `is_head` and `is_tail` are true.
- `HEAD`: first flit of a multi-flit packet.
- `BODY`: interior flit.
- `TAIL`: final flit that releases the switch reservation.

`is_head` and `is_tail` are derived properties, not stored booleans.

`Flit` carries payload size, message ID, source/destination router IDs, local
ports, and future multicast/reduction metadata. Phase 2 routes Flits directly;
`Message.packetize()` belongs to Phase 3 and must receive `FlitConfig` plus
explicit source and destination router IDs.

`TransType` is not serialized or interpreted by Phase 2. Its current internal
values therefore must not be treated as the hardware on-wire encoding; that
encoding must be reconciled with `NOC_ARCHITECTURE.md` before multicast work.

`FlitEvent` uses explicit `out_port` and `link_name` fields. It intentionally
does not duplicate flit type or tail state. Callers inspect `NoCTracer.events`
directly with normal list comprehensions.
