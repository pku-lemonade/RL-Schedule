# Synthetic calibration examples

These are hand-calculated synthetic references, not measurements. The engine
actually executes every admitted fitting candidate, records its loss and runtime
identity, freezes the first minimum, then executes the distinct held-out workload.

Memory fits `l1-a.bytes_per_cycle` over `[2, 4, 8]`, preserving the 0.5 native-cycle
setup, 4-byte granule/chunk, finite capacities and 500/250 MHz clock ratio. A 33-byte
write costs `9 * (0.5 + 4/rate) * 2`, giving 45, 27 and 18 ACI cycles. Against the
27-cycle reference, losses are 18, 0 and 9; rate 4 wins. The held-out 65-byte write
uses 17 granules and costs 51 cycles with the frozen rate. The original inputs
retain rate 2; fitted copies do not rewrite their assumptions/evidence.

Compute fits `matrix.work_per_native_cycle` over `[1, 2, 4]`. The one-job fitting
case has reader time 9, operand service 2, math `1 + ceil(2/rate)` and result service
1. Total times are 15, 14 and 14; losses against 14 are 1, 0 and 0. Both minima
remain in the report; declared order selects rate 2. The held-out output width 2
has reader 9, operand service 3, math `1 + ceil(4/2) = 3` and result service 2,
giving 17 cycles. The alternative tied rate 4 would give 16, but held-out data
does not choose between ties. The independent predecessor pipeline oracles also
remain regression checks; these numbers are not universal device timings.

Each sidecar declares a different semantic workload and capture group. The runner
recomputes these identities and rejects renamed duplicates, reused capture bytes,
missing group declarations and misleading supplied fingerprints. Only physical
memory bandwidth/fixed latency and compute work rate/setup may be fitted; all
candidate configurations pass the existing full admission boundary. Invalid
candidates and missing evidence remain visible, with no default winner.

Loss is the weighted mean of absolute metric errors divided by declared scales.
Fitting computes a loss even outside the acceptance threshold. Held-out checks
use the unchanged declared tolerances. A zero reference needs positive absolute
tolerance or an explicit exact policy (both tolerances zero). Rates, thresholds,
candidate domains, source bytes and inputs are part of recorded identities.
