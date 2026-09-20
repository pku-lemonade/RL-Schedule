# Remaining specification reconciliation (task 8.3)

The [verification report](verification.md) reviews all seven completed children and
27 parent requirements. Their code is delivered, but `openspec/specs/` is empty.
This document prepares the next separate part; it does not itself synchronize a
main specification or complete 8.3. The parent remains **37/38**.

## Reviewed input inventory

There are 12 delta documents: five parent documents and one from each of seven
children. Together they contain **100 unique requirement IDs and 178 scenarios**.
The [evidence map](evidence-map.json) records the exact delta input paths and hashes
under `specification_inventory`. No duplicate IDs were found; several capability
contracts deliberately overlap at different levels of detail.

| Canonical capability | Parent contracts | Child contracts | Requirements / scenarios |
| --- | --- | --- | --- |
| `wormhole-hardware-profile` | HP-01..04 | HP-P01..10 | 14 / 21 |
| `wormhole-topology-routing` | TR-01..05 | TR-G01..10, TR-D01..10 | 25 / 42 |
| `wormhole-memory-transactions` | MT-01..06 | MT-D01..10 | 16 / 27 |
| `wormhole-compute-dataflow` | CD-01..05 | CD-D01..10 | 15 / 25 |
| `wormhole-validation` | VA-01..07 | Cross-child evidence policy | 7 / 7 |
| `wormhole-validation-harness` | Parent VA policy applies | VA-D01..11 | 11 / 25 |
| `wormhole-multicast-sync` | Parent TR-05/MT-06/VA-04 apply | MS-01..12 | 12 / 31 |

## Merge decisions to preserve

1. Keep the stable parent IDs as cross-child capability/policy obligations, and
   the child IDs as the specific admitted mechanism contracts. Preserve their
   normative bodies and named acceptance scenarios. Overlapping subject matter
   does not authorize broadening a narrow implementation or deleting evidence
   links. `evidence-map.json` provides the explicit parent-to-child mapping.
2. Put each requirement ID in the canonical baseline exactly once. Combine the
   three topology inputs and each overlapping profile/memory/compute pair into
   their single capability files. Main specs use a purpose and `## Requirements`,
   not delta `## ADDED Requirements` headings. These input totals are an inventory
   target, not proof that the merge has occurred.
3. Preserve general profile execution gates and adapter-specific capability
   reports. The available opt-in transport/memory/compute/mixed paths do not make
   every inspected hardware profile executable through the legacy scheduler.
4. Preserve explicit assumed masks, configurable dimensions/clocks/widths/rates,
   finite admission, fair draining endpoints and the model's stated resource
   policies. The admitted rectangle/scalar subset does not imply general hardware
   multicast, arbitrary atomics, tensor reductions or exact virtual-channel logic.
5. Preserve the difference between aggregate model conformance, synthetic adapter
   contract tests, real functional observations and measured silicon timing.
   Calibration mechanics alone do not establish calibrated timing accuracy.
6. Resolve historical support statements by scope, not by silently rewriting
   earlier deliveries. A child-1 inspection gate, a later opt-in runtime and a
   legacy projection may legitimately expose different capability lists.
7. Keep archival separate from synchronization. Once the baseline has been merged,
   an eventual archive must not replay already incorporated ADDED blocks. Review
   the archive tool's supported skip-sync path or equivalent idempotent handling
   against the resulting main specs. Do not remove active changes as part of this
   audit and do not push without a new request.

## Completion gate

Read the current OpenSpec synchronization instructions and resolve each selected
change through the CLI's authoritative path metadata before writing. Recheck the
input hashes in `specification_inventory`; a changed input requires renewed review.
Prepare the seven canonical files, compare all requirement IDs and scenarios,
inspect the overlapping capability text, and validate the main specs and all
eight related changes strictly. Review the resulting diff for premature claims,
duplicate requirements, or silently dropped scenarios. Record the actual output
paths and validation results, complete 8.3, and commit that validated part locally.
Archival is a subsequent action; hardware validation and optional ML evidence
remain independent work even after specification reconciliation.
