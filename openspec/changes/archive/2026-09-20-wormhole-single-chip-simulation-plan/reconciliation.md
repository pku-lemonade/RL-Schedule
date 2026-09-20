# Specification reconciliation delivery (task 8.3)

**Archive update (2026-09-20):** this completed change is now archived. Statements below about pending archival describe the prior delivery checkpoint. See the [archive record](../2026-09-20-wormhole-archive.md) for relocated evidence and preserved identities.

Task 8.3 is complete on 2026-09-20. Seven canonical specifications now contain
all **100 distinct requirement IDs and 178 scenarios** from the reviewed parent
and seven children. All normative requirement/scenario bodies are preserved;
the parent checklist is **38/38**, and the seven children remain **233/233**.

The preceding [verification report](verification.md) and [evidence map](evidence-map.json)
record the implementation audit at `9d5f000`. This separate part resolves that
audit's only critical closure finding, the empty main-spec baseline. Exact final
specification hashes, input identities, CLI context and checks are in
[spec-sync-identities.json](spec-sync-identities.json). No runtime, hardware
parameters, dependencies, legacy formats or evidence tiers changed. No change
has been archived or pushed.

## Reviewed inputs and delivered specifications

There are 12 delta documents: five parent documents and one from each of seven
children. Together they contain **100 unique requirement IDs and 178 scenarios**.
The [evidence map](evidence-map.json) records the original delta input paths and
hashes under `specification_inventory`; all 12 still match exactly. All eight
changes were resolved through OpenSpec 1.11.0 status metadata, and only its
`artifactPaths.specs.existingOutputPaths` supplied merge inputs. The main-spec
root came from `planningHome.root`, which resolves to this repository. One valid
specs-instruction snapshot was read for each change before writing; no additional
specs rules were configured. No duplicate IDs or dropped scenarios were found.

| Canonical capability | Parent contracts | Child contracts | Requirements / scenarios |
| --- | --- | --- | --- |
| [wormhole-hardware-profile](../../../specs/wormhole-hardware-profile/spec.md) | HP-01..04 | HP-P01..10 | 14 / 21 |
| [wormhole-topology-routing](../../../specs/wormhole-topology-routing/spec.md) | TR-01..05 | TR-G01..10, TR-D01..10 | 25 / 42 |
| [wormhole-memory-transactions](../../../specs/wormhole-memory-transactions/spec.md) | MT-01..06 | MT-D01..10 | 16 / 27 |
| [wormhole-compute-dataflow](../../../specs/wormhole-compute-dataflow/spec.md) | CD-01..05 | CD-D01..10 | 15 / 25 |
| [wormhole-validation](../../../specs/wormhole-validation/spec.md) | VA-01..07 | Cross-child evidence policy | 7 / 7 |
| [wormhole-validation-harness](../../../specs/wormhole-validation-harness/spec.md) | Parent VA policy applies | VA-D01..11 | 11 / 25 |
| [wormhole-multicast-sync](../../../specs/wormhole-multicast-sync/spec.md) | Parent TR-05/MT-06/VA-04 apply | MS-01..12 | 12 / 31 |

## Applied merge decisions

Stable parent IDs remain cross-child capability/policy obligations; child IDs
remain the specific admitted mechanism contracts. Each ID appears exactly once
in the canonical baseline. The three topology inputs and each overlapping
profile/memory/compute pair are merged into their single capability files. The
normative bodies and named acceptance scenarios remain intact, with no broader
claim inferred from overlapping subject matter. The evidence map preserves the
parent-to-child requirement relationships.

Each main spec has a title, Purpose and one `## Requirements` section, with no
delta operation headings. Purpose text was copied from the parent input for its
five capabilities and from the harness input for its capability. The multicast
delta has no Purpose; the new main spec's initial placeholder was filled from
its reviewed proposal and implemented bounded contract. No placeholder remains.

Each main spec includes a contract-scope note distinguishing broad parent
obligations, the selected adapter and historical child delivery conditions.
Inspection-only gates and old replay restrictions remain valid without
contradicting separately admitted newer interfaces. The available opt-in
transport/memory/compute/mixed paths do not make every inspected profile
executable through the general legacy scheduler.

Assumed masks, configurable dimensions/clocks/widths/rates/capacities, finite
admission, draining endpoints and declared resource policies remain explicit.
The rectangle/scalar subset does not imply arbitrary multicast, general atomics,
tensor reductions or exact virtual-channel logic. Aggregate model conformance,
synthetic adapter checks, real functional observations and measured silicon
timing remain distinct. Calibration mechanics do not establish measured accuracy.
Historical child reports remain evidence of their own delivery boundaries.

## Executed validation

- OpenSpec 1.11.0 strict main-spec validation: **7 passed, 0 failed**.
- Strict validation of the parent and seven children: **8 passed**.
- Every original normative block, requirement ID and scenario retained; 12 delta
  byte hashes unchanged. Reapplying the reviewed merge wrote **zero files** and
  retained all seven main-spec hashes.
- `.venv/bin/python -m unittest simulator_detailed.tests.test_validation_identity -q`:
  **12 passed**, zero failures/errors/skips, 0.348 s. This includes the regression
  that documentation edits do not alter the selected simulator source identity.
- Strict Pyright: **0 errors, 0 warnings, 0 informations**. The recorded 45-path
  multicast/predecessor Ruff scope passes.
- Main-spec structure, links, source/input identities, task counts and whitespace
  checks pass. The final apply status reports **38 complete, 0 remaining**.

No runtime source or fixture changed. The previous audit's 77 passing tests and
the multicast delivery's full **557 discovered / 556 passed / one optional
Torch/PyG skip** remain their separately recorded executions, not new suite runs for
this specification-only part. The 167 detailed Python files, 18 root smoke source
files and 14 recorded assets still match the final multicast manifest exactly.

The initially installed global OpenSpec **1.3.1** omitted the path metadata
required by the synchronization skill, so no main spec was written through that
version. A pinned temporary npm invocation of **1.11.0**, under Node **22.22.2**,
resolved the mismatch without replacing the global CLI or adding a project
dependency. After creating main specs, five unqualified validation names matched
both a change and a spec and returned ambiguity errors. Repeating those checks
with `--type change` passed. Those initial command-selection errors are recorded
as such, not as model failures or successful validation.

Reproduce the specification checks using the compatible CLI:

```sh
npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate --specs --strict --no-interactive
for wormhole_change in wormhole-single-chip-simulation-plan wormhole-hardware-profile generic-heterogeneous-topology wormhole-dual-noc-routing wormhole-memory-transactions wormhole-compute-dataflow wormhole-validation-harness wormhole-multicast-sync; do
  npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate "$wormhole_change" --type change --strict --no-interactive || exit 1
done
npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec instructions apply --change wormhole-single-chip-simulation-plan --json
.venv/bin/python -m unittest simulator_detailed.tests.test_validation_identity -q
.venv/bin/pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
git diff --check
```

## Remaining independent actions

Specification closure is complete. Archival has not been performed. The reviewed
OpenSpec 1.11.0 `archive --help` provides `--skip-specs`; a later explicit archive
of these already synchronized changes can use it to avoid replaying ADDED blocks,
while retaining validation. Check the then-current specification hashes and
update moved evidence links as part of that action. Do not use `--no-validate`.

Compatible external functional captures, silicon measurements and optional ML
execution remain unavailable; historical root local-remap failures remain outside
this delivery. Those evidence or consumer tasks require their own scope and do
not become completed through main-spec synchronization. Nothing was pushed.
