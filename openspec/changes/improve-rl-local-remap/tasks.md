## 1. Rebuild mapper.py from the old baseline

- [x] 1.1 Replace the current `utils/mapper.py` implementation with a clean rewrite based on `utils/mapper_old.py`, keeping only the required helper logic plus `shift`, `split`, `replace`, `remove`, and `apply_local_remap()`.
- [x] 1.2 Implement an internal block view extracted from the original mapping and verify that it records the information needed for later remaps without depending only on `input_fetch` and `output_partition`.

## 2. Add a new DFG generator for the internal block view

- [x] 2.1 Keep the current `gen_dfg()` logic as the bootstrap/reference path for the original mapping.
- [x] 2.2 Implement a new DFG generation path that consumes the internal block view directly after remaps.
- [x] 2.3 Add validation that the new internal-view DFG generator matches the behavior of the original generator on the unmodified checked-in mapping.

## 3. Implement the four explicit remap actions on the internal view

- [x] 3.1 Implement `shift(layer, src, dst)` so it moves only part of `src` into active `dst`, using trace-derived fail-slow information to balance finish times.
- [x] 3.2 Implement `split(layer, src, dst)` so an active `src` block can be split onto a `dst` core that is inactive for that layer.
- [x] 3.3 Implement `replace(layer, src, dst)` so it swaps workloads between two cores that are both already active in the layer.
- [x] 3.4 Implement `remove(layer, src, dst)` so it merges all of active `src` into active `dst`.

## 4. Validate sequential remaps and reconnect RL integration

- [x] 4.1 Add direct simulator drain checks for baseline behavior, simple sequential remaps, and the known layer-18 bad sequence using the new internal-view DFG generator.
- [x] 4.2 Implement `apply_local_remap()` in the current RL-facing format (`accepted`, `reason`, `details`) and reconnect it cleanly to `rl_agent/envs/rl_env.py`.
- [x] 4.3 Keep the timing-log workflow aligned with the rewritten mapper once the new remap path is stable.
