# Phase 2 Hardware-Correction Plan

The current Phase 2 implementation is structurally coherent but only partially
calibrated to the latest hardware measurements. Apply the following fixes in
order. Each fix must contain a focused code change, its regression tests, and
the corresponding documentation update.

## Fix 1: Correct flit counting (complete)

- Change `compute_flit_count()` to match measured payload packing.
- Required boundaries: 512 B -> 1 flit, 1024 B -> 2 flits, 2048 B -> 4 flits.
- Do not deduct the estimated header or CRC size from logical payload capacity
  until hardware data confirms that behavior.

## Fix 2: Implement message packetization (complete)

- Add immutable `EndpointAddress` values and an architecture-owned
  `EndpointRegistry` for PE/DMA attachment resolution.
- Add `Message.packetize(flit_config)`; routing comes from the source and
  destination addresses captured when the message is initialized.
- Emit `SINGLE` for one flit, `HEAD`/`TAIL` for two flits, and
  `HEAD`/`BODY`/`TAIL` for longer packets.
- Verify that the output payload sum equals the input message payload.

Implemented in `simulator_detailed/utils/definitions.py` and
`simulator_detailed/endpoint_registry.py`. Multi-port DMA endpoints require an
explicit local-port selection. PE CH0/CH1 identity remains a Fix 5 responsibility
because both lanes use PE local port 0.

## Fix 3: Correct zero-load Link throughput

- Separate physical serialization, wire propagation, and launch interval.
- Preserve 4.0-cycle serialization and 0.5-cycle first-flit wire delay.
- Derive the launch interval from the measured effective payload rate:
  512 B / 120 B/cycle, approximately 4.267 cycles per flit.

## Fix 4: Decouple credits from the launch interval

- Keep the one-flit downstream-buffer capacity and explicit credit state.
- Make credit stalls occur only when downstream capacity is unavailable.
- Do not use credit-return delay to reduce zero-load throughput to 106.7 B/cyc.
- Retain lossless delivery and head-of-line backpressure under a slow receiver.

## Fix 5: Add channel identity

- Add `channel_id` to `Message`, `Flit`, trace events, and packetization.
- Validate channel IDs against the configured lane count.
- Keep CH0 and CH1 traffic distinguishable in all diagnostics.

## Fix 6: Add two independent Link lanes

- Represent every PE-to-PE direction as independent CH0 and CH1 Links.
- Give each lane its own serializer, downstream buffer, and credit state.
- Verify approximately 120 B/cyc per lane and approximately 240 B/cyc when both
  lanes carry independent PE-to-PE traffic.

## Fix 7: Make Router state lane-aware

- Key input state, output resources, reservations, and pending requests by
  `(port, channel_id)`.
- Keep one VC per physical lane.
- Preserve packet-level wormhole reservation from HEAD through TAIL.

## Fix 8: Implement explicit round-robin arbitration

- Replace FIFO `simpy.Resource` selection with a rotating input pointer for each
  output lane.
- Advance the pointer after every packet-level reservation release.
- Verify bounded waiting, repeated alternation, and no starvation.

## Fix 9: Correct trace latency semantics

- Rename the current INJECT-to-EJECT result to fabric latency.
- Add explicit local-transport boundaries for c2r start and r2c completion.
- Do not report either value as NMC operation latency.

## Fix 10: Add hardware latency composition

- Store measured one-way fixed targets: 79.5 cycles for static shape and
  125.0 cycles for dynamic shape.
- Compute the endpoint control residual by subtracting the already modeled
  zero-hop transport latency.
- Apply the residual once per transfer, never once at both endpoints.
- Verify composed RTT values of `159 + 17*hops` and `250 + 17*hops`.

## Fix 11: Enforce strict code quality

- Add complete types for SimPy stores, processes, generators, router state, and
  test helpers.
- Remove wildcard imports, unused state, and false `List[Core]` contracts.
- Require all Phase 2 tests, Pyright strict, Ruff, `compileall`, and
  `git diff --check` to pass before starting Phase 3.

## Final Phase 2 Acceptance

Phase 2 is complete only when packet counts match hardware observations,
single- and dual-lane throughput hit their calibrated targets, zero-load latency
retains the 8.5-cycle hop slope, sustained contention is round-robin and
starvation-free, hardware RTT composition passes, and strict type checking is
clean.
