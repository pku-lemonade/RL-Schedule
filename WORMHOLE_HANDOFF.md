# Wormhole simulator handoff

Prepared on 2026-09-17 for continuing this work on another machine; delivery status updated on 2026-09-18. Paths in this document are relative to the repository root; the old local checkout path is not a runtime requirement.

## Resume point

- Repository: `git@github.com:pku-lemonade/RL-Schedule.git` (HTTPS alternative: `https://github.com/pku-lemonade/RL-Schedule.git`).
- Working branch: **`feiyang-dev`**.
- Latest planning commit before this handoff: **`7af97842c879a82a77af79596192eb5de2cb25d9`**, `docs: plan Wormhole validation harness and evidence gates`.
- Latest completed child: **`wormhole-validation-harness`**, **35/35 tasks**, with **11 requirements and 25 acceptance scenarios**. Its seven incremental parts deliver offline validation, independent audits, reference import, bounded calibration and a report CLI. See [delivery.md](openspec/changes/wormhole-validation-harness/delivery.md), [progress.md](openspec/changes/wormhole-validation-harness/progress.md), and the [exact identity manifest](openspec/changes/wormhole-validation-harness/delivery-identities.json). The final delivery is the commit containing this update; Part 6 is `736a396`.
- Current action: **repair and continue `wormhole-multicast-sync`**. Planning is complete, but a 2026-09-18 implementation audit reopened overstated task checkmarks: 20/35 tasks are now fully verified after the admission and shared tree-transport repairs. Serial prototypes exist; the required shared transport/memory/compute runtime is not delivered. Read [implementation-audit.md](openspec/changes/wormhole-multicast-sync/implementation-audit.md) and [progress.md](openspec/changes/wormhole-multicast-sync/progress.md) before continuing. No umbrella specification or task was synchronized or archived.
- The original migration authorized a transfer push. That historical authorization is not an unlimited future push policy. **The seven validation implementation parts were committed locally and were not pushed.** Push only on a new request. Obtain this document's commit with `git log -1 --oneline -- WORMHOLE_HANDOFF.md`.

## Working agreement

1. Respond to the user **in English first, then Chinese**. Reason in English. This handoff document is intentionally English-only at the user's request.
2. Preserve useful simulator capabilities. Explain current behavior, expected behavior, affected classes/interfaces, compatibility impact and possible flaws before or alongside changes.
3. Prefer small, incremental fixes. **Commit every completed, validated implementation part before continuing.** Run relevant tests, strict type checks and scoped lint checks; record actual outcomes. Push future changes when requested; this migration explicitly authorizes the transfer push, not an unlimited future push policy.
4. Keep hardware dimensions, clocks, widths, capacities, effective rates and thresholds configurable. Do not silently turn example assumptions into device facts.
5. Distinguish implemented execution from fields merely represented in schemas, unsupported operations, synthetic evidence and measured hardware validation. A passing model test does not establish silicon timing accuracy.
6. Follow the approved sequence: explore a child using the preceding child's findings, create/validate its concrete change, apply and validate/commit its parts, then explore the next child. The current child's planning is already complete; continue with apply.
7. Do not make unrelated changes, create backups, repeat the already completed cleanup/history rewrite, or force-push. Keep generated traces/logs in temporary directories. Preserve unrelated user changes if any appear on the destination machine.
8. Do not sync/archive the umbrella prematurely or mark it delivered merely because its planning files exist. Parent/child requirements need a later evidence-based reconciliation.

## Completed work and remaining roadmap

The umbrella is [wormhole-single-chip-simulation-plan](openspec/changes/wormhole-single-chip-simulation-plan/tasks.md). Its 38 milestone boxes are still unchecked pending reconciliation; that does **not** mean the completed children below are unimplemented. Likewise, OpenSpec `status` can say `isComplete: true` for complete planning artifacts; use `instructions apply` and task boxes to inspect implementation progress.

| Child | Progress at handoff | Delivered or planned scope |
| --- | --- | --- |
| `wormhole-hardware-profile` | 23/23 | Strict configurable profile/provenance/availability contracts and capability gates |
| `generic-heterogeneous-topology` | 30/30 | Canonical physical identities, heterogeneous directed topology, finite replay and consumer guards |
| `wormhole-dual-noc-routing` | 35/35 | Two configurable torus fabrics, directed routing, bounded flow control, causal responses and directed slowdowns |
| `wormhole-memory-transactions` | 40/40 | Addressed transactions, segmentation, shared L1/DRAM service and capacity, ordering/versions and local clients |
| `wormhole-compute-dataflow` | 35/35 | Finite FC/matmul costs, real memory traffic, shared sessions, reusable slots, causal reader/compute/writer overlap and CLI/examples |
| **`wormhole-validation-harness`** | **35/35; delivered** | Unified reproducible model validation, reference imports, comparison admission, bounded calibration/held-out evaluation and reports |
| `wormhole-multicast-sync` | 20/35 fully verified after audit and Parts 1–4 repair; implementation in progress | Schema/tree/serial prototypes and partial validation exist. Shared runtime integration, physical service/ownership, causal pipeline, retained resume and valid Wormhole fixture remain pending. |
| Umbrella final audit | Pending | Audit 27 parent target requirements, reconcile overlapping deltas and delivered evidence before eventual sync/archive |

The separate `improve-rl-local-remap` change is already 12/12 and is not the active task. Do not select it just because multiple OpenSpec changes exist.

## Read these files first

1. The active multicast child's [proposal](openspec/changes/wormhole-multicast-sync/proposal.md), [design](openspec/changes/wormhole-multicast-sync/design.md), [requirements](openspec/changes/wormhole-multicast-sync/specs/wormhole-multicast-sync/spec.md), [tasks](openspec/changes/wormhole-multicast-sync/tasks.md) and [implementation audit](openspec/changes/wormhole-multicast-sync/implementation-audit.md).
2. The preceding compute child's [delivery report](openspec/changes/wormhole-compute-dataflow/delivery.md) and [progress evidence](openspec/changes/wormhole-compute-dataflow/progress.md), including independent oracles, exact identities, compatibility checks and known limitations.
3. The umbrella [design](openspec/changes/wormhole-single-chip-simulation-plan/design.md) and [VA-01..07 validation requirements](openspec/changes/wormhole-single-chip-simulation-plan/specs/wormhole-validation/spec.md).
4. The maintained [detailed simulator documentation](simulator_detailed/docs/README.md), especially [compute](simulator_detailed/docs/compute_dataflow.md), [memory](simulator_detailed/docs/memory_transactions.md), [torus transport](simulator_detailed/docs/torus_transport.md) and [profile limits](simulator_detailed/docs/hardware_profile.md).

No tracked `NOC_ARCHITECTURE` document was found in the inspected checkout. Do not claim to have read one; use the existing code, detailed documentation and OpenSpec history for architecture context.

`openspec/config.yaml` contains stale historical summaries. In particular, its claims about a mostly smoke-tested baseline, empty requirements and the old three-coordinate RL action space are not current. Some commands also emit the pre-existing warning `Rules for 'design' must be an array of strings, ignoring this artifact's rules`. Do not silently repair unrelated configuration as part of the harness; inspect actual code and the concrete child artifacts.

## Current runtime boundary

The supported Wormhole path is an explicitly admitted **finite single-ASIC abstract scheduling/traffic model**. It executes configured topology/NoC contention, addressed memory operations and effective FC/matmul work, not tensor values or real kernels. Worker inventory is not a general workload implementation.

Important code boundaries:

| Area | Read/extend points |
| --- | --- |
| Strict records | `simulator_detailed/configs/schemas/topology.py` (`GraphRecord`, identifiers and digests); `memory_replay.py`, `compute_workload.py`, `torus_replay.py` in the same directory |
| Profile/capability | `simulator_detailed/hardware_profile.py`, `inspect_profile.py` |
| Topology/transport | `topology.py`, `torus.py`, `torus_contract.py`, `torus_transport.py`, `torus_records.py`, `virtual_channel.py`, `replay_topology.py` under `simulator_detailed/` |
| Memory | `memory_plan.py`, `memory_execution.py`, `memory_runtime.py`, `memory_session.py`, `memory_resources.py`, `memory_service.py`, `replay_memory.py` |
| Compute | `compute_cost.py`, `compute_plan.py`, `compute_memory.py`, `compute_buffers.py`, `compute_pipeline.py`, `compute_runtime.py`, `compute_adapters.py`, `replay_compute.py` |
| Tests/checks | `simulator_detailed/tests/`, `simulator_detailed/pyrightconfig.phase2.json` |
| Consumer boundaries | `simulator_detailed/topology_compatibility.py`, public detailed predictor/embedding entry points |

Use existing public admission/execution interfaces: topology inspection/replay, `replay_memory.load_plan/run_replay`, and `replay_compute.load_plan/run_workload`. Preserve `topology_replay_result` v1/v2, **`memory_replay_result` v1** and `compute_workload_result` v1. Wrap results with validation evidence rather than changing their meanings or established digest/timing fixtures. `benchmark_workloads.py` is an existing NMC-specific API and must remain usable.

Key invariants already implemented:

- One physical memory/resource owner across aliases and fabrics; local and network clients contend on the same declared service.
- One shared simulation time domain for composed memory/compute. No duplicate legacy LSU/scratchpad delay for already charged addressed service.
- Explicit useful/padded arithmetic work and storage/packet/service bytes; planned totals are distinct from observed completed work.
- Bounded FIFO A/B/C slot bundles with generations, capacities and safe reuse. `fifo_item_slots_v1` is conservative and is not a full TT-Metal circular-buffer implementation.
- Incomplete execution retains charged state and can resume. Posted completion is distinct from target visibility; full success requires all jobs, effects, descriptors, leases, credits, contexts and slots to drain before exactly-once teardown.
- Pure compute planning does not execute. The scoped `abstract_compute_workload_v1` runtime is available only for admitted inputs; the general legacy full-profile gate remains closed.

Unsupported or unvalidated: numerical tensors, TT-Metal/RISC-V/ISA execution, detailed unpack/math/pack engines, automatic tilize/conversion/general operator lowering, split-K/reductions, arbitrary cross-worker notifications, exact NIU/DRAM-bank/channel/cache behavior, multicast/atomics/semaphores, dynamic kernels, interchip/host/PCIe modeling, new predictor/RL models and calibrated silicon timing. No ttsim run or physical hardware measurement has been performed.

The explicit legacy FC-chain adapter is supported with complete sidecar metadata. Detailed direct legacy FC now reports an unsupported-mode error instead of silently doing no work. Preserve existing supported conv/pool, LOAD/STORE/SEND/RECV, mesh/DMA and fail-slow behavior. Do not conflate root modules with similarly named modules under `simulator_detailed/`.

## Completed validation child: implementation record

The existing [35-task checklist](openspec/changes/wormhole-validation-harness/tasks.md) is complete. Each numbered part ended with validation, a progress record and a commit. Keep this sequence as the delivered record; do not repeat its implementation.

| Part | Tasks | Scope |
| --- | --- | --- |
| 1 | 1.1–1.5 | Strict contracts, source/input/environment identities and evidence outcomes |
| 2 | 2.1–2.5 | Public-result adapters, normalization and independent event-level audits |
| 3 | 3.1–3.5 | Finite benchmark runner, case catalog and fixed regression gates |
| 4 | 4.1–4.5 | Functional JSON / profiler CSV import and comparison admission |
| 5 | 5.1–5.5 | Bounded parameter fitting and isolated held-out evaluation |
| 6 | 6.1–6.5 | Report CLI, consumer guards, examples and documentation |
| 7 | 7.1–7.5 | Consolidated checks, legacy smoke, requirement evidence and next-child handoff |

The implementation is in `simulator_detailed/configs/schemas/validation.py`, `simulator_detailed/validation/` and `simulator_detailed/validate_wormhole.py`, with strict Pyright coverage. Its five v1 document kinds are `validation_suite`, `validation_reference`, `validation_report`, `calibration_plan` and `calibration_result`. [Validation usage](simulator_detailed/docs/validation.md) contains six verified CLI examples; [progress.md](openspec/changes/wormhole-validation-harness/progress.md) records actual per-part checks. Functional-reference and silicon-timing evidence remain unvalidated; both fitting examples are synthetic demonstrations.

Keep these design decisions intact:

- Architecture/protocol, model-invariant, functional-reference and silicon-timing evidence are separate tiers. Synthetic import/fitting fixtures never become real vendor/hardware evidence.
- Execution state, check outcome and coverage are separate. Checks use `pass/fail/blocked/unsupported/not_run`; reports aggregate to `pass/fail/incomplete`. Missing data/tools are blocked, observed disagreement fails, and an empty check set cannot pass.
- Default execution is offline. Named adapters/gates do not accept arbitrary shell commands or Python expressions. Use public APIs, bounded cases and enforced timeouts.
- Independent audits reconstruct routing, bytes, service, ownership, causal stages and drain from declared inputs/events. Reusing a producer's own totals or arithmetic helper is insufficient; corrupted observations must fail checks.
- Reference comparison requires matched units, clock domains, layout, workload, instrumentation, measurement boundaries and aggregation. No guessed metadata or undocumented profiler overhead correction. Initial profiler import pairs same-domain zones; no unsupported cross-core timestamp subtraction.
- ttsim is a potential functional source, not a timing oracle. There is no assumed universal ttsim trace exporter and no automatic vendor-tool launcher in this child. Pinned design source identities are already recorded in the active design.
- Candidate fitting initially targets only memory `bytes_per_cycle` / `fixed_latency_cycles` and compute `work_per_native_cycle` / `setup_native_cycles`. Keep topology, widths, capacities, clocks and structural facts fixed. Apply candidates to copies and re-admit workloads.
- Fit and held-out cases are disjoint by semantic workload/condition and capture-group identity, not just names. Declare metrics/tolerances first, select using fitting cases only, freeze the winner before evaluation, retain held-out failures and ambiguous fits.
- Keep legacy traces/model contracts intact. Detailed runtime/hardware features remain 7-D/4-D. Root RL uses four-coordinate actions `[layer, source core, destination core, operation]` with `replace/split/shift/remove`; do not infer new Wormhole training support.

After this child is actually delivered and committed, explore `wormhole-multicast-sync`. Do not implement its mechanisms as an incidental harness change.

## Clone and recreate local tooling

```sh
git clone --branch feiyang-dev git@github.com:pku-lemonade/RL-Schedule.git
cd RL-Schedule
git status --short --branch
git log -3 --oneline
```

The branch is already checked out by `--branch`; on an existing clone, fetch and check out `feiyang-dev` normally, preserving any local changes. No force reset/history rewrite is needed.

`.venv/`, `.agents/` and `.codex/` are ignored and do **not** travel with Git. All change artifacts, simulator inputs, tests and source needed for the offline work are tracked. Recreate dependencies and Codex skills on the new host.

The source host used Python **3.12.12**, macOS arm64, Node **24.20.0**, OpenSpec **1.11.0**, and the following Python package versions. This is an observed working environment, not proof that a different OS/Python build has already passed its checks.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  pydantic==2.13.5 pydantic-core==2.46.5 annotated-types==0.8.0 \
  typing-inspection==0.4.4 typing-extensions==4.16.0 \
  simpy==4.1.2 numpy==2.5.3 scipy==1.18.1 \
  pyright==1.1.414 nodeenv==1.10.0 ruff==0.16.7
npm install -g @fission-ai/openspec@1.11.0
openspec init --tools codex --no-animation
git status --short
```

Use an available Python 3.12 installation (`python3.12` above) and supported Node installation; the inspected OpenSpec package requires Node >=20.19.0. `openspec init` here regenerates ignored local Codex integration for the existing project; preserve tracked `openspec/config.yaml` and all existing changes. Read the generated apply skill if available. The CLI commands and committed artifacts also identify the exact work independently of local skills.

The repository's `requirements.txt` is an old **Conda export**, not a pip requirements file. `environment.yml` describes a much larger Linux/CUDA/ML environment and differs from the small tested environment above. Do not blindly run `pip install -r requirements.txt` or install the full GPU stack for this offline child. Torch, PyG, Gymnasium and Stable-Baselines3 were absent on the source host; optional model/RL execution was not validated.

## Verification and baseline evidence

First inspect current progress and validate the existing change:

```sh
openspec list --json
openspec status --change wormhole-validation-harness --json
openspec instructions apply --change wormhole-validation-harness --json
openspec validate wormhole-validation-harness --strict --no-interactive
```

Before modifying runtime code on the destination, reproduce the baseline. Core commands:

```sh
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/ruff check simulator_detailed/compute_*.py simulator_detailed/replay_compute.py simulator_detailed/configs/schemas/compute_workload.py simulator_detailed/hardware_profile.py simulator_detailed/utils/task.py simulator_detailed/tests/test_compute_*.py simulator_detailed/tests/test_hardware_profile.py
git diff --check
```

The Ruff command is the preceding child's scoped check, not a claim that every legacy module is lint-clean. For each new part, lint all changed/new relevant modules and add production modules to strict Pyright coverage. Do not start a repository-wide cleanup.

Historical final compute validation at `007f4e5`: **315 tests discovered, 314 passed, one optional Torch/PyG check skipped**; strict Pyright **0 errors, 0 warnings**; scoped Ruff, strict OpenSpec and whitespace checks passed. The planning-only `7af9784` change passed strict OpenSpec, artifact consistency (11 requirements/25 scenarios/35 unchecked tasks) and staged whitespace checks. These historical results must not be reported as destination-host runs.

During this documentation-only handoff, all 16 local Markdown links and the syntax of all five shell blocks were checked, strict validation of `wormhole-validation-harness` passed again, and the four compute CLI examples below completed with the documented elapsed times. The full unit/type/lint suite was not rerun for this documentation change; its preceding results remain the historical baseline above.

Executable example smoke:

```sh
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/generic_matmul.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/streaming_depth1.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/streaming_depth2.json
.venv/bin/python -m simulator_detailed.replay_compute --workload simulator_detailed/configs/compute_workloads/wormhole_bf16_matmul.json
```

Observed model elapsed ACI cycles are respectively **91.75, 45, 33 and 105**. They are model fixtures, not hardware measurements. The independent constant-stage pipeline oracle uses R=2/C=3/W=2 and gives **21/14** for one/two slots; the integrated transaction-backed example gives **45/33**. Do not confuse them. Compute CLI codes are 0 complete / 1 incomplete / 2 invalid; existing memory/topology replay codes are 0 complete / 2 incomplete / 1 invalid. Preserve these conventions; the planned validation CLI has its own documented 0/1/2/3 policy.

The preceding [delivery report](openspec/changes/wormhole-compute-dataflow/delivery.md#validation-performed) contains the exact separate root Darknet19 smoke script. It runs actual mapping, architecture execution and trace construction with temporary outputs, without importing the full optional ML pipeline. It completed **37,888/37,888 nodes**, **16 cores**, **48 directed links** and **11 windows** with a JSON round trip. Its unseeded stochastic elapsed time is not a deterministic fixture. `simulator.run` has optional ML imports; do not substitute it blindly for that smoke.

Known baseline limitations:

- Old root `test_internal_view_layer5` accesses removed `LayerView.active_cores`.
- Old root `test_safe_layer5_sequence_drains` assumes core 1 is active in layer 5 when the current mapping rejects it.
- Those two probes failed before this child; they were recorded, not repaired or counted as passes. Root reference-DFG and transactional replacement probes passed. Keep new failures distinct from these historical results.
- No optional model forward/inference/RL training, ttsim functional run or matched hardware timing measurement was performed. Missing tools/imports are unavailable checks, not successes.

## Short prompt for the next agent

```text
Continue the Wormhole simulator work in this RL-Schedule checkout on feiyang-dev. Read WORMHOLE_HANDOFF.md and the planning artifacts, implementation-audit.md and progress.md in openspec/changes/wormhole-multicast-sync/. The validation predecessor is delivered (35/35). The multicast child has 20/35 fully verified tasks after correcting overstated prototype completion claims and repairing tree admission. Parts 1–4 are repaired; integrate compute stages, then the CLI/harness and consolidated evidence. Test, type-check, lint and commit each completed part before proceeding. Preserve compatibility, configurable hardware parameters and honest evidence tiers. Respond in English first, then Chinese. Do not recreate the plan, repeat the history cleanup, or push future commits unless requested.
```
