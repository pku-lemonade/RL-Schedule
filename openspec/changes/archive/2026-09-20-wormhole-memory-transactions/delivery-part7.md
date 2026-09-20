# Part 7 delivery: legacy DMA lifecycle and scratchpad capacity adapters

## Behavior and class impact

Part 6 (`e7cb3b8`) added ordering and local clients to the addressed-memory runtime. Legacy GM/DDR transport and core scratchpad allocation still used separate interfaces. Part 7 provides explicit adapters at those boundaries, preserving native execution and keeping DFG/compute migration for the next child.

`LegacyDMAAdapter` wraps one existing `DMAEndpoint` or `NMCChannel`. It validates the supported GM/DDR direction, delegates exactly one native call, and observes its returned process without an additional timeout or hardware resource. The caller still posts both partners of a dual-side command; single-side commands still originate at the PE. NMC receive requires its existing explicit shape mode. Endpoint/channel/coordinator implementations, packetization, descriptor sharing/limits, payload service, fault factors and response traffic are unchanged.

The result contains the original native result object and a serializable `LegacyDMALifecycle` labeled `execution_policy=legacy_dma`. Submission, descriptor acceptance and completion project the corresponding native timestamps. Send records expose final local handoff; receive records expose tail service completion. Source-memory-read finish, addressed destination readiness and response-receipt timestamps remain `None`: the wrapper does not infer them from operation completion. There is no addressed-memory service charge, new memory controller, or automatic peer posting. FIXPATH and local-memory DMA attachments remain rejected. PE-to-PE transport remains supported by its native API and is outside this DMA facade.

`ScratchpadCapacityAdapter(env, scratchpad, resource_id=..., capacity_bytes=...)` requires an empty, idle scratchpad in the same environment with matching capacity and a nonnegative integer delay. It binds one exclusive owner token and uses the original `simpy.Container`. No parallel container, service server or shadow byte budget is created. `reserve(MemoryBuffer)` validates identity, physical bounds and nonoverlap before admission, including pending allocations/releases. It returns a plain completion event yielding an owner-bound `BufferHandle`; `release(handle)` returns a plain completion event and rejects foreign, pending or repeated releases. Admitted operations cannot be interrupted through these public event handles.

The adapter invokes the original allocation/release delay and container operation once each. Its ledger distinguishes `allocating`, `reserved` and `releasing`; snapshots report actual used/free container bytes and pending-operation counts. Ownership traces contain reserve/release events, without fabricated access leases or readiness publication. Buffer readable/writable/producer fields remain metadata in this capacity-only facade; they do not execute memory accesses. Detach requires all reservations and pending operations to drain. Following detach, ordinary legacy calls work again; an already detached adapter rejects further work.

`ScratchpadMemory` now tracks pending public calls at invocation, before their generator first runs. This prevents binding between scheduling an old allocation and its first SimPy step. Both used capacity and queued raw container requests also prevent binding. While bound, calls without the owner's token fail before capacity or timing work. Unbound allocation/release generator behavior and configured delays remain unchanged. A created call remains pending until its generator executes/finishes; discarding an unexecuted call is not an adapter cancellation operation. Direct container/private-field mutation is outside the managed ownership API. `LSU`, `TPU`, `Task` and the DFG scheduler do not automatically use this facade.

## Detector/encoder admission

The existing dependency-free guards already rejected memory schemas, but importing the public detailed detector/encoder imported optional tensor packages before reaching those guards. `predictor.predict.detect` now imports dataset/model code after architecture and event admission; `build_hardware_graph` imports Torch after runtime admission. Invalid memory replay/result inputs therefore fail clearly even without Torch/PyG installed.

The encoder's tensor class is moved into `_hardware_embedding.py` and lazily exported as `hw_encoder.HardwareEmbedding`. Its two GCN layers, dropout, linear layer, constructor defaults, state-dict names, public class name and pickle module location are preserved. Graph feature construction and detector model computation are unchanged. This is an import-boundary adjustment; 7-D detector / 4-D encoder feature contracts, checked-in checkpoints, RL observations/actions/rewards and top-level modules/assets are not migrated or extended for memory traces.

Tests invoke the actual public detector and encoder with replay/result objects, serialized documents and invalid event streams. A fresh interpreter prohibits all Torch/PyG imports while exercising admission. No fake tensor modules or checkpoint substitutes are used. Installed-package inspection finds both Torch and PyG absent. The real encoder graph/forward/state-dict/public-class-pickle smoke test is explicitly skipped for that reason. Full detector inference, checkpoint loading and RL execution remain unverified in this environment; successful admission tests do not claim those checks passed.

## Requirement coverage and independent oracles

The 15 tests in `test_memory_adapters.py` comprise 14 executed tests and one optional tensor test:

| Requirement / concern | Evidence |
| --- | --- |
| MT-D09 legacy transport equivalence | 32 direct/adapted scenario pairs: four GM/DDR directions, both command modes, two packet formats, shared/separate issuers; each pair runs six concurrent commands across both fabrics with one outstanding endpoint slot |
| MT-D09 native result/charges | Exact equality of all native results, completion/drain times, complete per-fabric NoC traces and DMA service streams; native endpoint called once and returned object retained by identity |
| MT-D09 rendezvous and faults | Four delayed-peer dual-side cases produce no payload/network work before the peer posts; eight read/write/mode fault cases compare exact service/traces across slowdown and recovery |
| MT-D09 unsupported modes | FIXPATH, LOCAL attachment and non-DMA direction rejection before admission; single-side endpoint submission and invalid receive shape arguments rejected; no pending coordinator/descriptor/traffic artifacts |
| MT-D04 one scratchpad owner | Constructor rejects another environment, capacity mismatch, invalid delay, second binding, used capacity, unstarted/pending operations and queued container requests |
| MT-D04 conservation and release | Original container identity and one get/put; configurable delays 0/3/7; pending and complete snapshots; full-capacity disjoint reservations; overlap, overflow, foreign handle, duplicate release and early detach rejection; reuse only after release completes |
| MT-D09 legacy calls/tasks | Unbound capacity contention still progresses; negative allocation recovers pending tracking; real legacy LOAD_FEAT/LOAD_WGT and STORE tasks run on example-configured cores with unchanged scratchpad/LSU costs |
| MT-D09 model boundaries | Actual detector/encoder reject new model/doc/event inputs before optional tensor imports; accepted legacy event rows remain unchanged; optional real encoder checks are separately skipped |

Independent arithmetic used in tests:

- A 13-byte scratchpad reserves five bytes at configured time `d` and releases at `2d`; available bytes are 13 -> 8 -> 13. Delays 0, 3 and 7 exercise zero and nondefault settings. Disjoint five/eight-byte reservations fill capacity concurrently at time 3, with no double charge.
- Legacy DMA with seven useful payload bytes in a nine-byte physical flit and `port_bw=1` costs **9 ACI cycles** per flit, or **27** at slowdown factor 3. This legacy bandwidth field is physical bytes per ACI cycle; the configured endpoint clock applies to issue timing, not a second conversion of payload bandwidth. Both before- and after-fault flits retain factor 1.
- Legacy LOAD of 13 bytes with a two-cycle scratchpad delay and LSU width 2.5 completes at time 7 (`2 + floor(13/2.5)`). STORE then ends at time 14 with zero occupied bytes. The existing floor behavior is preserved, without treating it as an addressed-memory service policy.

## Wait-resource audit

| Wait | Retained ownership | Progress / release |
| --- | --- | --- |
| DMA observation | One observation process and native result metadata | Existing native process completes; no second descriptor, datapath, memory grant or packet |
| Dual-side rendezvous / single-side control | Existing coordinator and endpoint/channel ownership | Existing partner posting or real control traffic; adapter does not create a partner |
| Scratchpad allocation delay/container event | Admitted nonoverlapping physical extent and exclusive owner token | Original finite delay and one get; total admitted extents cannot exceed physical capacity |
| Scratchpad release delay/container event | Exact handle/extent, still unavailable for reuse | Original finite delay and one put, then ledger retirement |
| Detach | No waiting process is created | Reject until ownership and pending work are drained |

The adapter introduces no memory-server-to-network wait edge and never holds a new grant. Native DMA's legacy queues retain their existing behavior, including its receive-store limitation; they are not imported into the bounded addressed-memory runtime. Under exclusive API use, admitted scratchpad extents fit capacity, so adapter capacity operations do not create a circular capacity wait. This is a model ownership audit, not a hardware liveness proof.

## Validation and remaining work

- `.venv/bin/python -m unittest discover -s simulator_detailed/tests -q`: 225 discovered, **224 passed, 1 skipped** (optional Torch/PyG encoder test).
- `.venv/bin/python -m unittest simulator_detailed.tests.test_memory_adapters simulator_detailed.tests.test_topology_consumers -q`: 21 discovered, 20 passed, the same one skipped.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json`: 0 errors, 0 warnings, 0 informations. The existing strict simulator scope now includes `memory_adapters.py`; optional tensor modules are not part of that strict scope.
- `.venv/bin/ruff check simulator_detailed/core.py simulator_detailed/memory_adapters.py simulator_detailed/tests/test_memory_adapters.py simulator_detailed/embedding/hw_encoder.py simulator_detailed/embedding/_hardware_embedding.py`: passed.
- `.venv/bin/ruff check --ignore UP006,UP035,RUF046,RUF059 simulator_detailed/predictor/predict.py`: passed. These exclusions cover that file's pre-existing typing-alias, redundant-conversion and unused-unpacking diagnostics; unrelated cleanup was not performed.
- `.venv/bin/python -m py_compile simulator_detailed/embedding/_hardware_embedding.py`: passed; this is a syntax check, not optional dependency execution.
- `openspec validate wormhole-memory-transactions --strict --no-interactive` and `git diff --check`: passed.

The regression suite preserves the checked-in legacy custom-mesh timing/failure fixture, v1 replay behavior and all three exact pre-extraction v2 result fingerprints. No hardware measurement or silicon-accuracy percentage is added.

This completes tasks 7.1–7.5; the child is **35/40** tasks complete. The adapters are opt-in Python APIs. Part 8 remains responsible for the memory CLI, examples, complete exported accounting/evidence and compute-child handoff. DFG/compute integration, dynamic circular buffers, detailed NIU/bank/DRAM behavior and silicon calibration remain unsupported or assigned to later changes.
