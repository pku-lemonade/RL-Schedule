# Memory transactions delivery — part 8 and child handoff

The child now delivers the bounded addressed-memory subset described by
MT-D01..10. Tasks 1.1–8.5 are implemented and validated. This report supplements
`delivery-part1.md` through `delivery-part7.md`; it does not complete the umbrella,
sync main specs, archive a change, enable the full-profile workload path, or claim
silicon timing accuracy. Validation date: 2026-09-16.

## Delivered behavior and affected classes

Before part 8, the Python runtime executed generic transactions, ordering and local
clients, but lacked a file CLI and exported wire layouts/byte decompositions.
The profile source tag was accepted by the schema but its inventory could not
pass complete-connectivity admission. Thus profile-backed memory execution was
not already implemented.

`replay_memory.py` now provides a strict executable-file loader and JSON CLI.
`MemoryExecutionReplay` adds required explicit `MemoryRuntimeConfig` settings to
the admission schema. `MemoryExecutionResult` adds all planned packet layouts and
routes, injected-prefix progress and planned/actual accounting; base root totals
retain their previous meanings. `MemoryRuntime.snapshot` computes these records
from existing launches/chunks without changing scheduling or service costs.

`MemoryEndpointBinding` adds optional injection/ejection port declarations.
Canonical graphs retain their existing resolved permissions and reject conflicting
port declarations. Profile input binds explicitly configured ports/availability
through the shared pure torus graph binder. `TorusEndpointPorts` extracts only
physical permissions, so memory does not construct v2 traffic templates or dummy
sink services. The existing v2 binder delegates to that same function; exact v2
result regression fingerprints remain unchanged.

`MemoryPlan.revalidate` reconstructs profile-backed admission from the preserved
original profile and verifies graph/record consistency. Both execution and memory
resource compilation use it; neither attempts to parse a canonical graph as a
profile. Invalid ports, missing connectivity, harvested workers, wrong resource
attachments and modified compiled graphs fail before environment construction.

`hardware-profile-3` adds four opt-in memory capability entries: strict binding,
abstract addressed transactions, aggregate service and explicit ordering. General
profile execution, exact NIU behavior and compute remain unsupported. The generic
full-workload gate cannot be bypassed by declarations. Documentation and three
memory examples expose the actual scope and assumptions.

## Requirement-to-test reconciliation

All test modules below are under `simulator_detailed/tests/`. The support claim is
limited to the child contract and documented abstraction; the umbrella's broader
runtime is not inferred from the presence of records or adapters.

| Requirement | Executed evidence and practical limit |
| --- | --- |
| MT-D01 admission | `test_memory_contracts`, `test_memory_cli`: exact kind/integer version, extra/unsupported modes, capacities, complete source binding, relative paths, path-independent identities, settings-sensitive execution hash; invalid CLI input preserves output and constructs no environment |
| MT-D02 addresses/aliases | `test_memory_packets`, `test_memory_resources`, `test_memory_cli`: permissions/ranges, local owner, same-router routes, alias identity and ambiguity rejection; real profile DRAM aliases on different fabrics share `dram0` |
| MT-D03 packets | `test_memory_packets`, `test_packet_runtime`, `test_memory_runtime`: independent pinned 1/31/32/33/8192/8193-byte cases, generic F/D/H/P/A, headers and tails, exact request/response/channel totals; software segmentation only |
| MT-D04 capacity/readiness | `test_memory_resources`, `test_memory_ordering`, `test_memory_adapters`: one owner, nonoverlapping extents, versions/partial publication/access leases, duplicate/foreign release rejection, scratchpad container charged once |
| MT-D05 service | `test_memory_resources`, `test_memory_runtime`, `test_memory_ordering`: two independent analytical service tables, rounded positive sub-quantum work, combined alias/fabric/local service, independent-resource overlap, bounded queues; aggregate policy only |
| MT-D06 lifecycle | `test_memory_runtime`: independent same-router one-byte timelines, posted handoff before target readiness, acknowledgement after target service, read arrival before local-write finish, all-segment aggregation and delayed-credit drain |
| MT-D07 ordering | `test_memory_ordering`: effect-aware conflicts, explicit producers, no implicit order, local-ready observer scope, frozen local/remote fences, cross-fabric dependencies; stale/foreign/cyclic/posted-remote-fence cases rejected |
| MT-D08 bounded progress | `test_packet_runtime`, `test_memory_runtime`, `test_memory_ordering`: one-slot endpoints, long streaming packets, shared issue budget, finite responder/local pools, congestion/slowdowns, no retained memory grant waiting for network, complete and incomplete ownership audits |
| MT-D09 compatibility | `test_memory_adapters`, full detailed suite: exact direct/adapted DMA traces and times, single/dual-side behavior, descriptor sharing, fault recovery, scratchpad ownership and actual detector/encoder admission guards; optional tensor execution remains skipped |
| MT-D10 export/evidence | Six new `test_memory_cli` methods plus support-report tests: executable generic/profile/incomplete CLI, raw-event byte decomposition, native/ACI conversion, lifecycle reconciliation, profile/source hashes, immutable round-trip and rejection of inconsistent totals |
| Umbrella MT-01 | Initiators and normal read/write transaction paths delivered in opt-in replay. Remote L1/DRAM and same-router cases execute; general DFG workload adaptation remains compute-child work. |
| Umbrella MT-02 | Real headers, physical padding, independent segment cost and route-channel traffic are executable. NIU automatic split/linked forms remain unsupported. |
| Umbrella MT-03 | Canonical shared capacity and bounded combined read/write service delivered for aliases/fabrics/local clients; aggregate model has no bank/channel precision. |
| Umbrella MT-04 | Explicit readiness, completion and scoped ordering delivered for finite replay. Local producer events are not a scalar hardware synchronization protocol. |
| Umbrella MT-05 | Legacy DMA remains explicitly labeled and separately executable; adapter equivalence is tested without adding addressed visibility or duplicate charges. |
| Memory VA-04 | Packet/service arithmetic, contention, backpressure, both fabrics, shared aliases, local access, incomplete diagnostics, conservation and finite drain covered. Compute pipeline, multicast and scalar-sync cases remain pending. |

MT-06, CD-01..05, later multicast and calibration/evaluation requirements are
**pending**. This is the step-4 implementation/evidence handoff. Umbrella task
checkboxes remain subject to its separate reconciliation workflow; no planning
checkbox is used as implementation evidence and no main-spec synchronization is
performed here.

## Validation commands and results

Environment: Python 3.12.12, SimPy 4.1.2, Pydantic 2.13.5, Pyright 1.1.414 and
Ruff 0.16.7 in the existing `.venv`. From the repository root:

```sh
.venv/bin/python -m unittest discover -s simulator_detailed/tests -q
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/configs/schemas/memory_replay.py simulator_detailed/memory_records.py simulator_detailed/memory_execution.py simulator_detailed/memory_runtime.py simulator_detailed/memory_plan.py simulator_detailed/memory_resources.py simulator_detailed/replay_memory.py simulator_detailed/torus.py simulator_detailed/hardware_profile.py simulator_detailed/tests/test_memory_cli.py simulator_detailed/tests/test_hardware_profile.py
openspec validate wormhole-memory-transactions --strict --no-interactive
git diff --check
```

- Full detailed suite: **231 discovered, 230 passed, 1 skipped** in 25.709 seconds.
  The skipped test requires real Torch/PyG. No detector inference, checkpoint or
  RL run is claimed by import/admission tests.
- Strict Pyright: **0 errors, 0 warnings, 0 informations**; scope adds the memory
  CLI to the existing simulator scope. Optional tensor modules are outside it.
- Scoped Ruff, strict OpenSpec validation and whitespace checks: passed.
- Focused CLI suite: six tests passed; independent raw-event auditing covers both
  example families and the incomplete snapshot. Invalid/missing/malformed files,
  header coercion, unknown operations and mismatched capacities return 1, leave
  stdout empty and preserve an existing result.
- Prior v1 example remains at **44 ACI cycles**, graph hash
  `5ef712709c1afbfda6f795e119a4e4f2ce0f0437d7431f2a7be6285ffb15a6cc`,
  routing-plan hash `1a59e3e5b7ed7c6155fe618ca13b025a9908959093d174eb89d9f1e3c8e09fba`.
- All three complete serialized pre-extraction v2 result fingerprints in
  `test_packet_runtime.SharedTransportCompatibilityTests` pass unchanged:
  `f0ea85ad0d2296a7f1355aa6fbae146ff56208e585969052e55f76ef90581482`,
  `6b0d7e49c68f6987447ac13324a84fb6604f9100aaf230c91f82b3d24fc1643c`,
  `6c6304a42de8b98106fa71a70c6867448151b41d2cd94879eb7fae9c20db3e81`.
- `test_topology_baseline.test_custom_mesh_route_timing_and_slowdown_baseline`
  and existing DMA direction/command/fault suites pass unchanged.

The three memory commands documented in
`simulator_detailed/docs/memory_transactions.md` were run as subprocesses, each
with stdout exactly matching `--output`. Each was also executed twice to compare
complete serialized output bytes. Their stdout SHA-256 values (including the
final newline) were: generic
`9c26b7399a15902620662793fcde7c4af3b9b5e38271c189fbe7aeb10606fde3`;
`2b73987354ed2ddc0f5b59d59f5a2871005534ecf7b9108d66dc94f04429697b`
(for the incomplete example), and
`4a7e72413713a54ead9df4718674aebe8e7ec2f0c4dfcc18ecd0fa0f4483a4af`
(for Wormhole). Prior topology CLI commands were run with the
same stdout/output check:

```sh
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/heterogeneous_unicast.json
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/torus_small_v2.json
.venv/bin/python -m simulator_detailed.replay_topology --replay simulator_detailed/configs/replays/wormhole_transport_v2.json
```

All three returned 0/complete at 44, 57 and 63.5 ACI cycles respectively, with
608, 1,760 and 4,160 launched channel bytes. CLI verification outputs used temporary
files; no trace dump or backup is committed.

## Representative memory traces and independent accounting

| Example | Exit / cycles | Logical network + local | Injected packet bytes | Launched channel bytes | Completed rounded service bytes |
| --- | --- | --- | --- | --- | --- |
| Generic | 0 / 387 | 75 + 131 = 206 | 480 | 2,496 | 308 |
| Incomplete | 2 / 80 | 75 + 131 = 206 planned | 128 of 480 planned | 576 of 2,496 planned | 116 |
| Wormhole | 0 / 1,382 | 8,235 + 8,291 = 16,526 | 8,544 | 183,936 | 25,024 |

Generic full packets contain 75 useful + 288 header + 117 padding bytes. The
incomplete injected prefix contains 25 useful + 64 header + 39 padding bytes;
never-issued packets still appear with zero injected flits. Wormhole full packets
contain 8,235 useful + 224 header + 85 padding bytes. The 8,193-byte acknowledged
write alone costs 8,352 packet bytes; the read and independent posted write make
up the remaining 192 bytes.

Generic completed useful reads/writes are 173/108 bytes, rounded to 188/120.
Incomplete useful reads/writes are 65/42, rounded to 68/48. Wormhole completed
useful reads/writes are 8,333/16,428, rounded to 8,480/16,544. Local accesses explain
why useful service exceeds network logical bytes. Headers consume no memory
service. Active/queued chunks are excluded from completed totals and retained in
pending resource state. Tests reconstruct these totals from serialized launches
and chunks, and independently check each chunk's native-cycle and ACI conversion.

| Example | Operations / segments / planned packets | Transport / endpoint events | Lifecycle / descriptor / ownership events | Service events / completed chunks | Pending IDs |
| --- | --- | --- | --- | --- | --- |
| Generic | 7 / 5 / 9 | 1,774 / 138 | 48 / 24 / 66 | 231 / 77 | 0 |
| Incomplete | 7 / 5 / 9 | 386 / 32 | 20 / 7 / 29 | 90 / 29 | 41 |
| Wormhole | 7 / 4 / 7 | 63,099 / 1,082 | 41 / 20 / 548 | 2,346 / 782 | 0 |

Complete runs retain pre-teardown capacity/readiness evidence and then restore
all reservations exactly once. The incomplete run retains descriptors, accesses,
service and network ownership, with no post-teardown snapshot. A short horizon
is an incomplete observation, not a deadlock diagnosis or failed timing oracle.

## Source and code identities

The seven preceding implementation commits are `8f3ed12`, `4c5f416`, `2edda46`,
`2d5793a`, `10221cc`, `e7cb3b8` and `87de394`; planning is `bfe4c62`.
Part 8 is the commit containing this report, based on `87de394`. For a code identity
independent of this report's own commit hash, the 88 Python files recursively under
`simulator_detailed/` have aggregate SHA-256
`ffd40b3dea96fd2233e372054b47c16dc8bf80444e01290f287aba8633b93b83`.
This is `content_digest({repository_relative_path: SHA256(file_bytes)})`, including
tests, using sorted JSON object keys. It excludes documentation and JSON inputs.

The original normalized profile digest remains
`e42618fe13d0d7978bc6622cb2050dd146efa693a18190b9042913b552baa132`.
The memory source digest covers the bound graph including that original profile,
not merely its pathname. The packet and profile pins are unchanged. The three
packet-example pins and all seven memory evidence sources/revisions/SHA-256 values
are listed in design decision 10 and the example/fixture. No fresh external
reference execution or hardware measurement is represented by these identifiers.

| Identity | Generic | Incomplete | Wormhole |
| --- | --- | --- | --- |
| Input file SHA-256 | `417ba4953ef12a20bd3d13d74bedd8910cd83ce4e3c9085382eb591cfaede043` | `83998cb1c4e6b6fa1c1dc0ef2f9bd5b445af19c527afafe8780ef75ee1a1eccb` | `65fad11bbde9cf5394ec082510b24b5e42618e21d44e0f06e5e26f1b0033a5a3` |
| Bound source SHA-256 | `1cf56e48bf11cabb6878df76faa8ea900d41bb2803999a589d27d91f3cf81c8e` | `1cf56e48bf11cabb6878df76faa8ea900d41bb2803999a589d27d91f3cf81c8e` | `1522580f920df21f2699c89c9076d9e01d28011d514b18f3feef83b657199f1e` |
| Admission configuration SHA-256 | `7bf1554e7ecb431e404a062374d5cf5b7f807eef3eef7e7756a965980f9c478e` | `b01028abe5349275963d5c1bb260b7de568fdde65495def23453f5fe5cf6bd9f` | `da13460f79d0d33e9dce079ea4a15f641e16080e55918b5193805086d77d43ce` |
| Memory plan SHA-256 | `ba6090455e2349cda0ed6ff379f6ccb45c93d0383a7c03831fc87383296c4f9a` | `c6b088bd81bc97a57d9e2523d45d76579beabb34701351cfaf256dec60a3e5a8` | `d697e043e775b126650c7efd1242778324309c03769eb235836a74cc6481662e` |
| Execution plan SHA-256 | `571642128d2da911c84cc7b0e3880042ec328c8bef271d7a5905f0f4f01a3b3f` | `71f957457f97089ca59c63fd7037096bc6a9cacd69fd631a6533b06c8aac7cb1` | `0f14aab0a3fbb2733a24e26cd123c64e8243159599e86c686ae1303735829f86` |

## Wait-resource audit and fidelity gaps

The CLI, binder and accounting additions create no new runtime wait edge. The
part-5/6/7 audits still apply:

| Wait | Retained ownership | Progress condition |
| --- | --- | --- |
| Explicit dependency/fence/start time | Finite control metadata, no descriptor/access/service grant | Admitted causal event or configured time; cyclic/insufficient physical ordering rejected |
| Issue/local/responder admission | Declared bounded slot only when admitted; no hidden payload array | Earlier bounded work retires through its operation-specific completion |
| Source/destination service | Access lease and counted TX/RX or local chunk slot | One canonical bounded FIFO server performs finite service; active grant never waits on network |
| Transport backpressure | Counted per-class staging/input credit; no retained shared physical grant while waiting | Reserved downstream capacity and independently draining response/consumer work |
| Posted local completion | Actual in-flight effects remain charged | Destination service, ownership and delayed credits must drain before replay success |
| Final teardown | Persistent buffer reservations | All operation effects, clients, service, network and owners drained; release once |

This is a supported finite-model audit, not a hardware deadlock or firmware-order
proof. Assumed queue sizes, service costs, arbitration and aggregate bandwidth are
not measured hardware parameters. Exact NIU/register/counter behavior, automatic
splitting/linked commands, bank/row/channel/refresh details, MMIO, immediate/masked
writes, multicast and atomics/semaphores remain unsupported. Per-operation target
alias selection on one fabric is not exposed; one enabled target per backing
resource per fabric is the supported choice. Tensor values, firmware/ISA and
multi-chip/host execution remain outside the model.

## Next exploration: `wormhole-compute-dataflow`

The next child should use this committed boundary and make concrete CD-01..05
choices before changing runtime behavior:

1. Inspect `core.py`, task/DFG/mapping code and existing compute/LSU duration rules.
   Define shape/dtype/layout-aware abstract matmul/FC costs, finite compute slots
   and nonzero service for positive sub-quantum work, with configurable rates.
2. Decide how workload LOAD/STORE dispatch shares this environment and canonical
   resource owners. Current `MemoryRuntime` owns a finite operation plan and final
   teardown; it is not yet an attachable dynamic DFG service. Do not silently
   replace a remote transfer with scratchpad allocation/LSU delay or double-charge
   already executed memory service.
3. Design bounded reserve/publish/consume/release tokens against one scratchpad
   owner. Current replay reservations are persistent and readiness is versioned;
   dynamic slot reuse and circular-buffer ownership need their own admission and
   lifetime rules.
4. Define reader/compute/writer stage events, overlap, backpressure and final drain
   with independent service/timeline oracles and one/two-buffer controls. Local
   memory operations provide service primitives but are not implemented compute.
5. Preserve legacy DFG behavior through an explicit mode/adapter and keep detailed
   predictor/encoder/RL capability checks honest. Deliver a single-ASIC synthetic
   matmul/streaming smoke workload without claiming numerical or calibrated
   silicon execution.

Create and validate that child's proposal/design/specs/tasks during exploration,
then apply incremental tested parts with a commit after each. MT-06 remains with
`wormhole-multicast-sync`; comparison/calibration infrastructure remains with
`wormhole-validation-harness`. No push was requested for this child.
