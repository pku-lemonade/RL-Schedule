# Reference fixture contract

Every capture here is **synthetic**, hand specified for importer tests. The local
write has 33 addressed bytes and nine service chunks: eight full 4-byte chunks
and one byte rounded to a 4-byte granule. Each costs
`(0.5 + 4/4) * (500 MHz / 250 MHz) = 3 ACI cycles`, giving 27 cycles.
The functional fixture describes one 33-byte effect; chunk partitioning and
independent event-list order are not functional differences.

The CSV fixture has counters above 2^60, a 100-cycle excluded warm-up and retained
durations 25, 27 and 29. Mean and median are 27; mean absolute deviation is 4/3.
It is not a TT-Metal or device capture.

The `tt_metal_device_profiler_csv_v1` extractor, version `1`, pins the metadata
line and twelve column names shown in the [device profiler documentation](https://docs.tenstorrent.com/tt-metal/latest/tt-metalium/tools/device_program_profiler.html).
The format was checked on 2026-09-18; the planning artifact records the earlier
documentation snapshot identity. This implementation accepts only unsigned
integer counters, zero stat values, begin/end phases, and one unambiguous pair
per explicitly selected device/core/RISC/zone/source-file/source-line/run.
Nested/repeated selected zones within one run are rejected. Timer IDs may differ
between begin and end. No cross-core subtraction or overhead correction occurs.
Live changes to the documentation do not change this parser.

To supply a real capture, retain its raw bytes and SHA-256, identify its immutable
source/extractor and collection scope, and supply a `validation_reference` v1
sidecar. Use extractor `wormhole_reference_import`, version `1`. Functional raw
JSON contains the normalized observation fields except the two generated identity
fields (`observation_id`, `source_result_sha256`). CSV sidecars additionally select
the exact domain, zone, runs, warm-ups, metric and boundary. Import fills observations
and sample statistics; supplied embedded values must agree with raw extraction.

Comparisons require explicit matching architecture/profile/layout/workload/mapping;
timing additionally requires device scope, software, firmware, clocks, instrumentation
and measurement metadata. Unknown values remain unknown. Cases declare their expected
conditions separately; model fields are checked against admitted simulator inputs.
Identity mappings are one-to-one. Cycle comparisons need equal frequencies; seconds
conversion requires explicit known clocks and domain mappings. Aggregation, repetition
and warm-up policies must match. A single simulator run cannot silently stand for a
repeated measurement aggregate. Full kernel/host dispatch intervals are unsupported.

The documentation's Grayskull example is not a Wormhole measurement. ttsim may
supply functional observations but cannot supply silicon timing. Synthetic producer
labels cannot be relabeled as external evidence. Integrity does not authenticate
origin. The harness never launches, installs or downloads vendor software.

`negative_hash.json` and `negative_grayskull.json` intentionally fail import;
`negative_unknown_metadata.json` imports but cannot authorize precise comparison.
