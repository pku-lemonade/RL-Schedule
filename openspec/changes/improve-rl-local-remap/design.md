## Context

The current RL mitigation path couples three weak points: the environment exposes remap actions whose implementation is spread across too much experimental logic, `utils/mapper.py` mutates mapping state in ways that are hard to reason about, and the remapped DFG path is no longer simple enough to debug directly. The simulator itself already executes a wide range of DFGs, but the mapper-side remap path does not yet provide a small, explicit implementation that can support valid sequential remaps while preserving the exact slice-coverage assumptions that `gen_dfg()` and the scheduler use for readiness.

The change will stay within the current 4x4 mesh + Darknet19 + fail-dataset workflow and will not modify files under `simulator/`. The hardest technical constraint is not simulator performance but DFG correctness: accepted remaps must keep input/output/weight family memberships aligned per active core and must preserve exact upstream coverage for the consumer slices that `RECV`, `LOAD_FEAT`, `CONV`, and `POOL` tasks expect.

## Goals / Non-Goals

**Goals:**
- Replace the current add/remove-only remap semantics with local remap actions that better fit fail-slow mitigation on still-functional hardware.
- Make mapper-side remap application transactional so no-op or invalid actions do not mutate the persistent mapping or leak across episodes.
- Extend `utils/mapper.py` so accepted local remaps regenerate family-consistent mapping blocks and produce DFGs that remain executable by the existing simulator.
- Keep action selection compact by letting RL choose `type + layer + src + dst` while the environment or mapper computes the amount of workload movement analytically.
- Replace per-step shadow execution with offline regression tests that exercise representative remap cases and simulator drainability.

**Non-Goals:**
- Changing the simulator’s execution model, NoC timing model, or fail-slow injection semantics in `simulator/`.
- Generalizing the detector or RL observation pipeline to arbitrary NoC topologies in this change.
- Redesigning the detector architecture or retraining workflow beyond any compatibility updates required by the new action semantics.
- Solving every possible irregular remap pattern in the first implementation; the initial focus is a certified subset of local edits on the current workload family.

## Decisions

### 1. Rewrite `utils/mapper.py` from `mapper_old.py` around a recorded internal block view instead of continuing the current layered redesign

The implementation will stop extending the current `utils/mapper.py` experiment and will instead rebuild `utils/mapper.py` from the simpler `utils/mapper_old.py` skeleton. The new mapper will keep the old `gen_dfg()` only as a bootstrap path: it will run once on the original mapping, record the resulting block-level internal view, and then apply remap actions against that internal structure for later DFG regeneration.

Rationale:
- The current mapper code has accumulated too much experimental logic and no longer gives a clear mental model for action semantics or DFG generation.
- The user explicitly wants only four remap actions plus one `apply_local_remap()` entrypoint with minimal, transparent behavior.
- Alternatives considered: continue patching the current mapper or continue relying on raw `mapping.json` mutation. Both keep too much redundant logic alive and make it hard to reason about correctness.

### 2. Treat the internal block view, not the serialized feature blocks, as the source of truth after bootstrap

The new mapper will preserve the current `mapping.json`-compatible data models, but after the initial bootstrap it will operate on an internal block view that records the logical per-block information needed for later remaps and DFG generation. Later remaps will update that internal structure directly instead of trying to re-infer everything from `output_partition` and `input_fetch`.

Rationale:
- The original mapping file is regular enough that the current `gen_dfg()` can infer it once, but later action results may produce unequal block sizes that are no longer recoverable from `output_partition` and `input_fetch` alone.
- The user explicitly wants later DFG generation to use recorded block information rather than the old equal-block inference logic.

### 3. Implement only the four explicit remap actions with the user-defined semantics

The new mapper will implement exactly four remap actions:
- `shift(layer, src, dst)`: both `src` and `dst` are active in the same layer; move only part of `src`'s workload to `dst`, with the moved amount chosen from traces so the two blocks finish at approximately the same time.
- `split(layer, src, dst)`: `src` is active in the layer and `dst` is currently inactive for that layer; split `src` into `src + dst`.
- `replace(layer, src, dst)`: both `src` and `dst` are already active in the layer; swap their workloads.
- `remove(layer, src, dst)`: both `src` and `dst` are active in the layer; merge all of `src` into `dst`.

Rationale:
- The user gave the exact semantics required by the RL workflow, including the important correction that `replace` is a workload swap, not a migration to an unused core.
- Alternatives considered: preserve the current experimental action semantics or continue overloading add/remove meanings. That would keep the mismatch between user intent and implementation.

### 4. Keep remap application transactional and return the current RL-facing result format

`apply_local_remap()` will remain the RL-facing entrypoint used by `rl_agent/envs/rl_env.py`. It will apply the selected action on a candidate internal view, regenerate a DFG from the new internal view, and return the current result shape used by the environment: `accepted`, `reason`, and `details`.

Rejected actions will remain rejected actions, not “successful” steps with unchanged mappings.

Rationale:
- The current RL environment already expects that return shape, and the user asked to keep it.
- Transactionality is still needed so invalid actions do not corrupt episode state.

### 5. Use the old `gen_dfg()` as the correctness reference and add a new internal-view DFG generator for remapped states

The rewrite will introduce a second DFG generation path that consumes the internal block view directly. The old `gen_dfg()` logic will remain unchanged and will be used as the reference implementation for the original mapping. A validation step must prove that, before any remaps, the new internal-view DFG generator produces the same DFG behavior as the old generator.

After remaps, only the new internal-view DFG generator will be used.

Rationale:
- The user explicitly wants the old equal-block inference logic preserved only for the original bootstrap case and replaced for later remaps.
- Comparing the new generator against the old one on the original mapping gives a concrete correctness target before any action logic is added.

### 6. Validate support incrementally: internal view, DFG equivalence, action correctness, then RL integration

The implementation workflow will follow the user’s requested order:
1. implement the internal block view,
2. implement a new DFG generator from that internal view and check it against the old generator on the original mapping,
3. implement the four actions against the internal view and validate them with simulator drain checks,
4. align the result format with `rl_agent` through `apply_local_remap()`.

Rationale:
- The current code needs a correctness-first rewrite rather than another broad feature pass.
- This sequence keeps each step observable and testable before the next one is attempted.

## Risks / Trade-offs

- [The new internal-view DFG generator diverges from the old generator on the original mapping] → Keep the old generator intact and compare against it before enabling remapped execution.
- [Analytical movement amount is too coarse under noisy diagnosis or communication effects] → Start with a simple capacity-balancing heuristic and keep the amount solver isolated so it can be refined without changing the RL action contract.
- [Cross-layer dependencies still break after valid remaps] → Keep tests on both simple sequences and the known layer-18 bad case while refining the internal-view generator.

## Migration Plan

1. Replace the current mapper implementation with a clean rewrite based on `utils/mapper_old.py`.
2. Implement and validate the internal block view extracted from the original mapping.
3. Implement the new internal-view DFG generator and verify that it matches the original generator on the unmodified mapping.
4. Implement `shift`, `split`, `replace`, and `remove` on the internal view.
5. Validate action results with direct simulator drain checks on representative sequences, including the known layer-18 bad case.
6. Reconnect `apply_local_remap()` to `rl_agent/envs/rl_env.py` using the current return format.

Rollback strategy:
- Keep the simulator unchanged and keep `mapper_old.py` as the reference baseline while the rewrite is in progress.

## Open Questions

- The user clarified the action semantics and workflow. No additional design questions are open before the rewrite starts.
