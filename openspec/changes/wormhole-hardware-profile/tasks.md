## 1. Baseline and source evidence

- [x] 1.1 Identify a compatible Python 3.12 environment and required existing imports plus pyright/Ruff; record executable path, package versions and reproduction commands in the future `simulator_detailed/docs/hardware_profile.md`. Verify imports before behavioral work; if setup is needed, use an isolated environment without rewriting the Linux/CUDA dependency export.
- [x] 1.2 Run `python -m unittest discover -s simulator_detailed/tests` and `python -m pyright --project simulator_detailed/pyrightconfig.phase2.json` as the baseline, plus targeted lint on the existing Python files this child will edit; record results and existing failures without treating missing tools as test passes.
- [x] 1.3 Create `simulator_detailed/tests/fixtures/wormhole_b0_profile_reference.json` from the pinned references in `design.md`, including hashes, independent coordinate/alias facts, exact L1/DRAM bytes and mask assumptions; verify its facts against the source tables rather than generating it from the production profile.

## 2. Profile schema

- [x] 2.1 Add versioned identity, source/evidence and literal quantity models in `simulator_detailed/configs/schemas/hardware_profile.py`; verify HP-P01/P02/P05 with valid single-ASIC input and negative version, extra-field, ID, unit, nonfinite and missing-evidence cases in `simulator_detailed/tests/test_hardware_profile.py`.
- [x] 2.2 Add physical layout and worker-selection models with complete coordinate coverage, unique tile roles/IDs, explicit enabled sets and logical-worker bijections; verify HP-P03 through out-of-bounds, duplicate, nonworker-selection and missing-mapping cases plus disabled-worker inventory preservation.
- [x] 2.3 Add fabric-coordinate, endpoint and memory-resource records with evidence and reference validation; verify HP-P04 using coordinate bijections, unknown/duplicate attachment references, capacity-unit checks and multiple attachments sharing one resource identity.
- [x] 2.4 Add typed derived-quantity recipes and dependency validation; verify HP-P05 rejects unknown operations, invalid operand dimensions, incompatible clock references and cycles, and accepts independently calculated duration/rate/byte-total cases.

## 3. Inspection and override behavior

- [x] 3.1 Implement normalization, quantity resolution and `override_parameter` in `simulator_detailed/hardware_profile.py`; verify HP-P06 with the 1000-to-800-MHz rate example, retained previous evidence/rationale, derived-value recomputation, direct-derived override rejection and unchanged source profile.
- [x] 3.2 Implement implementation-owned feature states, structural requirement derivation and deterministic blocker evaluation; verify HP-P07/P08 cannot enable profile execution by removing requested features, inventing a policy name or supplying a support declaration.
- [x] 3.3 Implement the typed inspection report and canonical content hash; verify HP-P04/P07 totals unique DRAM capacity, separates physical/enabled worker L1, preserves mappings/evidence, and produces stable JSON semantics across input paths and object-key ordering without changing the input.
- [x] 3.4 Add `simulator_detailed/inspect_profile.py` as a small command wrapper; verify its subprocess output is parseable report JSON, invalid input has nonzero status and stderr diagnostics, and inspection creates no simulation/log/trace output or simulator processes.

## 4. Legacy input and execution gates

- [x] 4.1 Implement explicit document classification and `require_executable_architecture` in the profile helper; verify HP-P01/P08 rejects mixed or malformed profile input without legacy fallback, passes an unchanged legacy `ArchConfig`, and returns structured capability errors for valid profiles.
- [x] 4.2 Integrate the gate at the beginning of `simulator_detailed/architecture.py` construction, narrowing back to `ArchConfig` before existing code; verify a hardware profile is rejected before environment/endpoint/network/core construction or mapper access, while direct legacy construction still follows its existing path.
- [x] 4.3 Integrate classification/gating into `run.arch_analyzer` while preserving its `ArchConfig` return contract, and move loading before mapper/logging work in `simulate`; verify real profile input fails before failure-file access, timing logs, DFG/network processing or detector calls using temporary-directory and mapper-sentinel tests.
- [x] 4.4 Verify `simulate_old` also rejects profile input before DFG/output creation and preserves the successful legacy startup order; run the real synthetic transfer smoke `python -m unittest simulator_detailed.tests.test_phase2_noc.Phase2NoCTests.test_json_example_runs_with_custom_fabric_overrides` plus relevant DMA regressions to establish positive compatibility coverage.

## 5. Wormhole example and documentation

- [x] 5.1 Add `simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json` with pinned source records, all physical tiles, both raw coordinate maps, unique memory resources, explicit y=11 harvest assumption and logical mapping; verify HP-P09 against the independent fixture, including 120 positions, 80/72 workers, 240 descriptive attachments, six unique 2-GiB groups and 1,499,136 bytes per worker L1.
- [x] 5.2 Populate the example's explicit clock/packet reference quantities and derivations, marking the illustrative 1-GHz clock assumed and hardware timing execution unavailable; verify the real example through the inspection CLI and parameter-override tests without introducing runtime ACI or compute defaults.
- [x] 5.3 Document the new input/report contracts, inspection and override usage, source pins, mask replacement, capability states, environment and validation results in `simulator_detailed/docs/hardware_profile.md`; link it from the detailed README/config docs and verify every shown command against the implemented example. Explicitly state that runtime traces, workload/failure JSON, detector checkpoints and RL observation/action shapes retain their existing contracts.

## 6. Validation and child handoff

- [x] 6.1 Extend `simulator_detailed/pyrightconfig.phase2.json` to include the profile schema/helper/command modules and run `python -m pyright --project simulator_detailed/pyrightconfig.phase2.json`; verify strict checks cover every new production module without disabling diagnostics.
- [x] 6.2 Run `python -m unittest discover -s simulator_detailed/tests`, including the new profile and gate cases; verify the real synthetic NoC/DMA regression suite passes and no Wormhole timing/compute/transaction execution is claimed from parser or inspection tests.
- [x] 6.3 Run `python -m ruff check --select E9,F63,F7,F82` on changed Python files and applicable existing lint rules on new modules, then check whitespace/JSON formatting; verify only scoped findings are fixed and no unrelated legacy cleanup or backups enter the diff.
- [x] 6.4 Record the final requirement-to-test evidence, exact commands/versions, source and example identities, capability limits and pending umbrella coverage in the child design/docs; verify HP-P01..10 are covered, runtime HP-03/NoC/memory/compute requirements remain pending, and no implementation task is checked solely because planning artifacts exist.
- [x] 6.5 Validate with `openspec validate wormhole-hardware-profile --strict --no-interactive` and review the final diff before handing evidence to the next `generic-heterogeneous-topology` explore; commit the completed, validated child before continuing; keep spec synchronization/archival and push as separately requested actions.
