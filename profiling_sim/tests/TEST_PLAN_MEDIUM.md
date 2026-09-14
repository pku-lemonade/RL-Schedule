# Configurable profiling regression coverage
The tests use synthetic configurations rather than a device specification.

- Ports and nodes: arbitrary mesh dimensions, endpoint IDs, local ports,
  ownership collisions, bounds and independent layouts.
- Memory and DMA: selected capacities, clocks, rates, channel counts and atomics.
- Transfer and synchronization: packet metadata, configured control sizes,
  counter counts, FIFO limits and credit return.
- Collective routing: sparse multicast, broadcast and reduction trees.
- Arbitration: configured burst quanta, zero-payload control and bandwidth factors.
- Layouts: element widths, masks, explicit dimension/padding limits and gather entries.
- Pipelines: configurable stage counts, compute stages, occupancy, admission
  intervals and data-dependency regression scenarios.
- Inter-chip transport: configured endpoint IDs, logical relocation, payload
  delivery, atomics and credit return.
- Integration: configured memory attachment, local-memory alignment/sync target
  and the synthetic example.

Run `python -m unittest discover -s profiling_sim/tests`.
