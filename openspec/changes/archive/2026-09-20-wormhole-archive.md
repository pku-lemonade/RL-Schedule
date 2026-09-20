# Wormhole archive record — 2026-09-20

The seven completed implementation children (233/233 tasks) and their umbrella
(38/38 milestones) are archived. The user explicitly requested this operation.
OpenSpec 1.11.0 archived each change with `--skip-specs --yes --json` after strict
change validation and exact comparison of all 12 delta documents against the
seven synchronized main specifications. All 100 requirement bodies and 178
scenarios were already present; no contract was excluded or overwritten.
Overlapping profile, topology, memory and compute deltas are all included in the
baseline delivered by `9a1e2b5`. The main-spec bytes are unchanged by archival.

| Completed change | Archive directory | Tasks |
| --- | --- | --- |
| Hardware profile | [2026-09-20-wormhole-hardware-profile](2026-09-20-wormhole-hardware-profile/tasks.md) | 23/23 |
| Heterogeneous topology | [2026-09-20-generic-heterogeneous-topology](2026-09-20-generic-heterogeneous-topology/tasks.md) | 30/30 |
| Dual NoC routing | [2026-09-20-wormhole-dual-noc-routing](2026-09-20-wormhole-dual-noc-routing/tasks.md) | 35/35 |
| Memory transactions | [2026-09-20-wormhole-memory-transactions](2026-09-20-wormhole-memory-transactions/tasks.md) | 40/40 |
| Compute dataflow | [2026-09-20-wormhole-compute-dataflow](2026-09-20-wormhole-compute-dataflow/tasks.md) | 35/35 |
| Validation harness | [2026-09-20-wormhole-validation-harness](2026-09-20-wormhole-validation-harness/tasks.md) | 35/35 |
| Multicast/synchronization | [2026-09-20-wormhole-multicast-sync](2026-09-20-wormhole-multicast-sync/tasks.md) | 35/35 |
| Umbrella | [2026-09-20-wormhole-single-chip-simulation-plan](2026-09-20-wormhole-single-chip-simulation-plan/tasks.md) | 38/38 |

## Evidence after relocation

[Archive identities](2026-09-20-wormhole-archive.json) map original repository-relative
prefixes to archived prefixes and record every archived file's original and
current hashes. Apply the prefix map to historical JSON `path`/`logical_path`
values before resolving against the current checkout. JSON evidence manifests,
delta specs and `.openspec.yaml` metadata retain their exact bytes. Markdown
navigation and current handoff references were relocated; original document
identities remain recoverable at the recorded pre-archive Git revision.
Historical validation commands in reports describe their delivery checkpoint;
archived changes are no longer selectable as active `--change` names. Current
main-spec validation uses `openspec validate --specs --strict --no-interactive`.

Runtime/configuration source, recorded fixtures and all seven main specs remain
unchanged. Archive validation passed eight strict change checks before moving,
seven strict main-spec checks after moving, 12 identity tests (0.386 s), strict
Pyright and the recorded 45-path Ruff scope. Link and identity checks cover the
relocated evidence. No full runtime suite was rerun for this document-only move.
The separate completed `improve-rl-local-remap` change was not selected or moved.
Nothing was pushed. Archival establishes no new functional or silicon evidence.
