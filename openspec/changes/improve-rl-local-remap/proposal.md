## Why

The current RL mitigation loop is not reliable enough to support the project’s main claim that RL, using detector output under noisy diagnosis, can mitigate fail-slow behavior on the existing 4x4 mesh workflow. The active remap path mutates mappings in place, mixes too much experimental logic into `utils/mapper.py`, and still produces remaps whose regenerated DFGs do not reliably drain in the simulator.

## What Changes

- Replace the current RL remap semantics with certified local remap actions that operate on layer-local workload partitions instead of global repartition-by-core-count edits.
- Rewrite `utils/mapper.py` from `utils/mapper_old.py` with a small, explicit implementation centered on four action functions and one `apply_local_remap()` entrypoint.
- Introduce an internal block view recorded from the original mapping so later remaps no longer depend on equal-size block inference from `input_fetch` and `output_partition`.
- Keep the existing `gen_dfg()` logic as the bootstrap/reference path for the original mapping and add a new DFG generation path that consumes the internal block view after remaps.
- Preserve the current 4x4 mesh + Darknet19 + fail-dataset workflow while making the remap path simple enough to debug and validate against the simulator.
- Add regression coverage for baseline DFG generation, sequential remaps, and the known layer-18 bad sequence so mapper-side changes are validated before RL training uses them.

## Capabilities

### New Capabilities
- `rl-local-remap-actions`: Internal-view-based local remap actions and DFG regeneration for RL mitigation, including `shift`, `split`, `replace`, `remove`, and `apply_local_remap()`.

### Modified Capabilities

None.

## Impact

- Affected mapper/DFG code: `utils/mapper.py`, and supporting validation helpers that may touch `utils/dfg.py` and `utils/task.py` assumptions without changing `simulator/`.
- Affected RL integration code: `rl_agent/envs/rl_env.py` through the `apply_local_remap()` contract and action semantics.
- Observation/action interfaces may change in the RL environment only as needed to stay aligned with the rewritten mapper entrypoint.
- Validation impact: new offline regression cases will need to cover the original mapping, sequential remaps, regenerated DFGs, and the current `workloads/darknet19-4-4.json` / `configs/instances/gemini4_4.json` / `fail_dataset/*.json` workflow.
