# Generic runtime correctness delivery

## Status

Final delivery recorded on 2026-09-23. The change is **10/10 complete**.
Multi-resource acquisition is atomic and pre-validated, cancellation and
timeouts can no longer report success, successful runs drain delayed credit
returns before returning, runtime faults abort with bounded cleanup, and
endpoint network membership is enforced for routes and transfers. The
change is neither pushed nor archived at authoring time.

| Part | Commit | Completed tasks |
| --- | --- | --- |
| Regression suite with pre-fix evidence | `393e58e` | 1.1-1.2 |
| Atomic acquisition and runtime lifecycle | `393e58e` | 2.1-2.4 |
| Endpoint network membership | `393e58e` | 3.1-3.2 |
| Documentation and delivery | this commit | 4.1-4.2 |

All fixes were validated and committed as one unit so every commit stays
green: the regression suite spans all three areas.

## Requirement coverage

| Requirement | Delivered at | Verified by |
| --- | --- | --- |
| UR-R02 atomic pre-validated acquisition | `runtime_context.py` `ResourceRegistry` | `TestAtomicAcquisition` (6 tests) |
| UR-R05 bounded cancellation, drain and abort | `runtime_context.py` `run`/`_abort` | `TestMemoryCancellationAndDrain` (5 tests) |
| AM-04 atomic bank/channel + interrupt propagation | `runtime_context.py` `_memory_service` | memory-stage cancellation and timeout cases |
| GS-G03 route endpoint membership | `configs/schemas/generic_graph.py` | `test_cross_network_static_route_rejected` |
| UR-C01 transfer endpoint membership | `system_compile.py` | dynamic and static cross-network rejections |

## Compatibility

- Full detailed suite: 733 tests OK (718 pre-existing unchanged plus 15
  new), 1 optional skip (torch/PyG), on Linux 6.1.0-35-amd64, Python
  3.12.12, SimPy 4.1.2.
- Phase-1 numeric anchors, digests, CLI and compatibility shims unchanged.
- Five negative-path fixture constructions now produce their terminal
  classifications through a same-network endpoint with no declared route;
  assertions and coverage are unchanged.
- Pre-fix baseline: the new suite reported 13 failures and 1 error against
  the unfixed code; post-fix all 15 pass. Details in `progress.md`.

Exact identities are in `delivery-identities.json`.
