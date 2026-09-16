# Hardware profiles

`wormhole-hardware-profile` adds a validated, inspectable description of one ASIC.
It **does not execute Wormhole workloads**. Existing synthetic `ArchConfig`
configurations retain their executable mesh/DMA path. The new profile is a
separate input, with no conversion into defaulted `core`, `noc`, or `mem` fields.

## Inspect the example

From the repository root, using the environment documented below:

```sh
.venv/bin/python -m simulator_detailed.inspect_profile simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json
```

The command prints a JSON inspection report on stdout. A valid profile returns
exit code 0 even though `can_execute` is false. Invalid input returns nonzero
with stderr diagnostics. Inspection does not fetch sources, construct a SimPy
simulation, run detection, or write logs/traces/results. Ordinary Python import
caches may be created. Package initialization still imports runtime modules,
so the existing runtime import dependencies are needed.

The checked-in example describes:

| Inventory | Value |
| --- | --- |
| Selected ASIC | Wormhole B0, `asic0`; illustrative n150 metadata |
| Physical positions | 10 × 12 = 120 |
| Tile roles | 80 worker, 18 DRAM aliases, 16 Ethernet, 1 PCIe, 1 management, 4 transit |
| Enabled workers | 72; physical worker row **y=11 is assumed disabled** |
| Logical workers | Explicit row-major 8 × 9 simulator mapping |
| Fabric maps | Raw NoC0 `(x,y)` and NoC1 `(9-x,11-y)` |
| Unicast policy metadata | NoC0 X then Y; NoC1 Y then X; execution requires an explicit transport or memory replay binding |
| Attachments | 240 descriptive tile/fabric records, including disabled workers |
| Worker L1 | 1,499,136 bytes each (1464 KiB) |
| Worker L1 inventory | 119,930,880 physical bytes; 107,937,792 bytes associated with enabled workers |
| DRAM | Six distinct 2-GiB resources; 12,884,901,888 bytes total |

Each DRAM resource has three physical aliases and six fabric attachments. Its
capacity is counted once. Worker L1 totals are neither a pooled memory nor a
usable allocation budget. This example's resource subset includes worker L1
and DRAM; it does not imply that Ethernet or other tiles lack local memory.

## Version-one input contract

The schema is `configs/schemas/hardware_profile.py`; helpers are in
`hardware_profile.py`. All models reject extra fields, invalid IDs, coercion of
integer indices, non-finite numbers, and missing evidence. Generic validators
contain no Wormhole grid dimensions or device capacities.

| Section | Contract |
| --- | --- |
| Identity | `kind: "hardware_profile"`, `schema_version: 1`, profile ID/revision, architecture/revision, exactly one `asic_id` |
| Optional board | Product, positive ASIC count, explicit in-range selected index; never multiplies resources |
| `sources` | Stable IDs, title, HTTP(S) URL, immutable hexadecimal revision or SHA-256 snapshot, access date, applicability |
| `parameters` | Stable IDs and literal/derived quantities with units and evidence |
| `layout` | Positive extent and one explicit tile at every physical coordinate; unique tile IDs and positions |
| `worker_selection` | Explicit enabled worker IDs and bijective logical indices/coordinates; no inferred mask |
| `fabrics` | Unique nonnegative IDs, extent, clock reference, descriptive topology/routing policies, complete raw coordinate bijections |
| `memory` | Unique resources, byte-capacity parameter references, local SRAM owner tile, table evidence |
| `attachments` | Unique endpoint IDs, known tile/fabric/resources, at most one attachment per tile/fabric; owned resources attach only to their owner |
| `requested_features` | Optional additional requirements; cannot remove structural requirements or grant support |

Disabled workers retain physical tiles, fabric coordinates, attachments and
physical memory inventory. Logical indices are simulator identifiers, not
firmware-translated addresses. Structural tables carry evidence; individual
tiles, resources and endpoints can carry more specific evidence.

Units are `bytes`, `Hz`, `cycles`, `bytes_per_cycle`, `bytes_per_second`,
`seconds`, and `count`. Bytes/count/width values are integral; clock frequency
and width are positive; reference durations are nonnegative. Cycle quantities
must reference a clock parameter in Hz. The supported derivations are:

- `rate_from_clock(bytes_per_cycle, Hz)` → bytes/second.
- `duration_from_cycles(cycles, Hz)` → seconds.
- `sum_bytes(bytes...)` → bytes.

Recipes have explicit operand IDs and no cached value. Validation checks units,
clock identity, missing references, cycles and finite results. There is no
expression evaluator. Dependencies shared by multiple recipes reuse resolved
values and evidence traversal. Relations between unrelated literal parameters
are not inferred from their names; changing packet reference constants requires
reviewing their related values. Runtime packetization belongs to a later child.

Evidence states are `documented`, `derived`, `assumed`, and `calibrated`.
Documented claims need source IDs; assumptions need a rationale; calibrated
claims need measurement artifact/method/conditions metadata. Derived reports
retain transitive dependency states. Source hashes and calibration metadata
are supplied evidence: inspection does not verify remote contents or reproduce
measurements. `silicon_timing` remains `unvalidated` even if input metadata is
labeled calibrated.

## Parameter overrides and mask replacement

```sh
.venv/bin/python - <<'PY'
from simulator_detailed.configs.schemas.hardware_profile import HardwareProfileConfig
from simulator_detailed.hardware_profile import (
    inspect_profile, load_architecture_document, override_parameter,
)

profile = load_architecture_document(
    'simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json'
)
assert isinstance(profile, HardwareProfileConfig)
effective = override_parameter(profile, 'ai_clock', 800_000_000, 'Illustrative 800 MHz scenario')
report = inspect_profile(effective)
print(report.resolved_parameters['interface_rate'].value)  # 25600000000
print(report.can_execute)  # False
PY
```

The original 1-GHz profile retains its 32,000,000,000-byte/s reference rate.
Overrides return a deeply copied, revalidated profile, append previous values
and evidence with reasons, and mark the new value assumed. Derived values and
the profile hash update. Override base parameters rather than derived results.
Neither rate is measured useful throughput or a runtime resource setting.

To replace the illustrative harvest mask, edit a dumped profile document's
`worker_selection.enabled_worker_ids`, `logical_workers`, and selection
evidence, then call `HardwareProfileConfig.model_validate(document)`. Replace
the rationale with the actual source or measurement metadata and revise the
profile identity/revision as appropriate. Do not delete harvested physical
tiles, resource inventory, or fabric attachments. There is no general patch
interpreter or live device discovery in this child.

## Report and execution boundary

The typed `InspectionReport` contains `report_schema_version`,
`manifest_version`, `profile_sha256`, the normalized `profile` (including
selected ASIC/board, mappings, sources and provenance), `counts`, `memory`,
`resolved_parameters`, `features`, sorted `blockers`, `can_execute`,
`silicon_timing`, and `evidence_verification`.

Canonical SHA-256 uses normalized validated JSON with sorted object keys and
ID tables. Ordered derivation operands and override history stay ordered.
Input file paths and generated timestamps are excluded. Reports and overrides
own separate nested data. The hash identifies profile contents, not simulator
source code, a passing test run, or hardware accuracy. Gate and inspection
boundaries revalidate models, including nested data modified by Python callers.

| Capability state | Meaning in this implementation |
| --- | --- |
| `executable` | Profile validation/inspection and separately scoped transport/memory replay compilers are implemented |
| `abstract` | Opt-in unicast/causal response transport and addressed memory transactions/service/ordering execute with disclosed model assumptions |
| `represented_only` | Physical layout, coordinate mappings and memory inventory are descriptive data |
| `unsupported` | Full-workload profile adapter, heterogeneous workload construction, mask scheduling, exact NIU behavior, workload memory integration, compute/dataflow and calibrated hardware timing |

Manifest `hardware-profile-3` distinguishes `profile data`, `opt-in version-2
transport`, `opt-in memory replay` and `profile execution` scopes. Available
compilers are implementation capabilities, not admission of the inspected
profile. Explicit availability, endpoint permissions, timing and resource
settings are required; see [transport examples](torus_transport.md) and
[addressed memory replay](memory_transactions.md).
`can_execute: false` and `blockers` still describe full-workload execution.

The manifest belongs to the implementation. Unknown feature/policy names are
retained as unsupported requirements, never executed. Every profile requires
`profile_runtime_adapter`; deleting requested features cannot bypass it.
Shared aliases, multiple fabrics, masks and policy names add structural
requirements. Input `support` fields are rejected.

`load_architecture_document(path)` returns `ArchConfig | HardwareProfileConfig`.
Presence of either `kind` or `schema_version` selects profile validation, with
no fallback to legacy defaults on failure. `require_executable_architecture`
returns the original legacy config or raises `UnsupportedHardwareProfileError`
with `profile_id` and typed `blockers`. Unrelated Python objects raise TypeError.

The gate applies to `run.arch_analyzer`, direct `Arch(...)`, `simulate`, and
`simulate_old`. Direct construction rejects before environment/resource setup;
`simulate` validates architecture before mapper access, failure-file loading
and timing logs. It loads the architecture only once. Existing synthetic
execution remains owned by `ArchConfig`, `NoC`, `Core`, DMA and their existing
validation paths. No full-workload hardware-profile adapter exists. The separate
torus runtime creates transport routers/links/endpoints without Core, DMA, memory
service or DFG execution. The separate memory runtime adds addressed transactions
and aggregate shared service without enabling Core/DFG workload execution.

Runtime traces, workload/failure JSON, detector checkpoints and RL
observation/action shapes retain their existing contracts. Inspection JSON is
not a detector input. Firmware translation, ISA/numerical compute, inter-ASIC
links, full n300 boards, host timing, DRAM timing and measured silicon timing
are not delivered here.

## Validation environment and evidence

Validation date: 2026-09-15. Base repository:
`ef09b0734894bc4a6168cc93c1d537d36e38ee3e`, plus the child implementation in the
commit containing this evidence.
Base Python: `/Users/feiyangwu/miniconda3/bin/python`, **3.12.12**.
Validation Python: `/Users/feiyangwu/codes/RL-Schedule/.venv/bin/python`.

```sh
python -m venv .venv
.venv/bin/python -m pip install pydantic==2.13.5 numpy==2.5.3 simpy==4.1.2 scipy==1.18.1 pyright==1.1.414 ruff==0.16.7
.venv/bin/python -m unittest discover -s simulator_detailed/tests
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
.venv/bin/python -m ruff check --select E9,F63,F7,F82 simulator_detailed/architecture.py simulator_detailed/run.py simulator_detailed/configs/schemas/hardware_profile.py simulator_detailed/hardware_profile.py simulator_detailed/inspect_profile.py simulator_detailed/tests/test_hardware_profile.py
.venv/bin/python -m ruff check simulator_detailed/configs/schemas/hardware_profile.py simulator_detailed/hardware_profile.py simulator_detailed/inspect_profile.py simulator_detailed/tests/test_hardware_profile.py
.venv/bin/python -m unittest simulator_detailed.tests.test_phase2_noc.Phase2NoCTests.test_json_example_runs_with_custom_fabric_overrides simulator_detailed.tests.test_phase3_dma
openspec validate wormhole-hardware-profile --strict --no-interactive
```

The ignored environment adds no project runtime dependency. The original
Linux/CUDA Conda export is not a macOS pip requirements file and was not changed.
The first sandboxed dependency install failed on DNS; authorized network access
completed installation. An initial Pyright invocation discovered the base
interpreter and produced 1,482 dependency-related errors. Explicit
`--pythonpath .venv/bin/python` corrected discovery; no diagnostics were disabled.

Baseline: **29 tests pass**, strict Pyright **0 errors / 0 warnings**, selected
Ruff correctness rules pass on the two existing production files being edited.
Final validation: **52 tests pass** (29 existing, 23 new); strict Pyright
**0 errors / 0 warnings** with all three new production modules included;
selected correctness lint on changed Python files and applicable Ruff rules on
new files pass. The explicitly invoked synthetic mesh-transfer plus DMA check
passes **9 tests**. OpenSpec strict validation passes. These are contract,
source-conformance and synthetic-runtime results, not Wormhole timing evidence.

The independent fixture is
`simulator_detailed/tests/fixtures/wormhole_b0_profile_reference.json`. It was
transcribed from pinned source tables separately from production-profile
generation. On the validation date, the D1–D5 raw source hashes were retrieved
and verified. D6 routing order was checked at its immutable revision; its
raw-byte hash fetch timed out and no snapshot hash is claimed. Routine tests are offline.

| Source | Repository revision |
| --- | --- |
| TT-Metal Wormhole B0 descriptor (D1) | `558637320489ee8ccccea5f2b3a5fcd1e1cfd58f` |
| ISA chip inventory, L1, coordinates, NoC/routing (D2–D6) | `acaf010519f4fdd323df5077e45b8695f70e4279` |

Exact immutable URLs, per-source SHA-256 hashes and extraction facts are in the
fixture and production profile. D1 exposes 80 workers and an empty harvest list;
D4 states that actual fused rows vary by ASIC. The y=11 mask and 1-GHz clock
remain assumptions. No hardware or ttsim run was performed.

| Artifact identity | SHA-256 |
| --- | --- |
| Effective normalized example | `e42618fe13d0d7978bc6622cb2050dd146efa693a18190b9042913b552baa132` |
| Example file bytes | `a959592f39b257091c509d95abde6842e10e08d84123fde6788ed55372429b9d` |
| Independent fixture bytes | `04ef81eee6b197646903102ebc72becfe40946be06519c5425539f3bd41ea8fe` |

All following tests are in `simulator_detailed/tests/test_hardware_profile.py`:

| Requirement | Evidence |
| --- | --- |
| HP-P01/P02 | `ProfileSchemaTests.test_generic_profile_has_no_runtime_defaults`, `test_identity_versions_strict_ids_and_board_selection`; classification and board-total inspection cases |
| HP-P03/P04 | Schema layout/selection/fabric/resource/endpoint negatives; `test_unique_capacities_and_disabled_worker_inventory`; exact raw maps and alias groups in `WormholeConformanceTests` |
| HP-P05 | Literal/evidence/source-pin and typed-derivation negatives; calibrated-metadata disclosure; shared dependency graph and conformance reference quantities |
| HP-P06 | Override origin, multiple history entries, recomputed rates/durations, derived-override refusal, immutable inputs and exact example override |
| HP-P07 | Normalized report equality across paths/key/table order; CLI success/errors/no result files; no environment construction; implementation-owned capability states |
| HP-P08 | `ProfileExecutionGateTests`: real parsing/gating at loader, direct constructor, `simulate` and `simulate_old`; mapper sentinel and side-effect guards; direct positive legacy construction |
| HP-P09 | Four `WormholeConformanceTests` methods independently check source pins, every role/coordinate, mask, endpoints, DRAM groups, exact L1 bytes and packet/clock references |
| HP-P10 | Entire detailed suite, real mesh/DMA regressions, strict types, lint, source hashes and explicit absence of hardware timing validation |

## Handoff to heterogeneous topology exploration

Child 2 has since delivered [canonical topology and synthetic replay](topology.md).
It projects this profile's inventory without inventing ports, availability or torus
edges. Its generic runtime does not change this profile's `can_execute: false`
gate. Children 3 and 4 have subsequently added explicit torus transport and
addressed memory replay; workload scheduling and compute remain pending. The
following handoff records the historical child-1 boundary and inputs.

At the child-1 handoff, `generic-heterogeneous-topology` received validated
physical tile IDs, roles, enabled/logical worker mappings, fabric coordinates,
endpoint records and unique memory identities. Its responsibilities were separate
router/worker/resource construction, a generic graph and downstream compatibility. Profile inspection itself still constructs no
runtime resources and selects no routes.

Umbrella HP-01..04 are covered only for this child's data/inspection boundary.
HP-03 scheduling and compute/validation-harness requirements remain pending;
transport and memory delivery are documented in their separately scoped children.
VA evidence begins here, but measured accuracy remains unavailable. Each completed, validated part is committed before continuing to the next part.
Spec synchronization, archival and push are separate requested actions.
