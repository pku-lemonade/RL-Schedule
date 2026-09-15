## Context

This is child 1 of `wormhole-single-chip-simulation-plan`. See `proposal.md` for motivation and `specs/wormhole-hardware-profile/spec.md` for the delivery contract. Exploration used repository commit `ef09b0734894bc4a6168cc93c1d537d36e38ee3e`. The original design below describes the agreed boundary; the implementation evidence section records the authorized apply results.

| Current behavior | Consequence for this child |
| --- | --- |
| `ArchConfig` has defaulted `core`, `noc`, and `mem` sections; `NoCConfig.type` is a string | A hardware description must not silently acquire a runnable synthetic mesh or imply an executor exists for any topology string |
| `Arch.__init__` creates a SimPy environment and `EndpointRegistry`, then constructs networks and `x*y` cores | Direct construction needs an early type/capability gate, before any resources or mapper calls |
| `EndpointRegistry` creates PE addresses for every router | Keep hardware layout in separate profile records until child 2 changes runtime construction |
| Controller and atomic fields exist only in configuration | Preserve this distinction in support reports; do not map DRAM aliases onto autonomous DMA engines |
| `run.arch_analyzer` returns `ArchConfig`; `simulate` writes a timing log before loading it | Keep the loader's return type and move architecture validation ahead of simulation logging and mapper access |
| Package `__init__` imports architecture/runtime modules | Inspection does not run simulation, but the current package still requires its runtime import dependencies; a Pydantic-only installation is not promised |
| Existing tests execute a configured mesh and DMA traffic through `build_runtime` | Use this as a real compatibility baseline; parser round trips alone are insufficient |

The earlier rounded L1 description is corrected for this profile: the pinned descriptor and L1 documentation both specify **1464 KiB = 1,499,136 bytes** per Wormhole worker. Do not use Blackhole's 1536 KiB or allocate that full physical capacity twice as independent data and weight memories. Runtime reservation and allocation policy remains a later decision.

## Goals / Non-Goals

**Goals:** A validated, inspectable single-ASIC profile; reproducible source/assumption metadata; explicit physical, logical, fabric and memory identities; derived-unit inspection; a gate preventing unsupported execution; unchanged legacy executable configuration semantics.

**Non-Goals:** Constructing a heterogeneous graph, selecting routes, scheduling enabled workers, packetizing Wormhole transfers, enforcing memory bandwidth, implementing atomics, emulating firmware coordinate translation, or calibrating compute/timing. The profile records inputs for those future mechanisms. It does not configure current runtime resources by approximation.

## Decisions

### 1. A separate input type with an explicit execution boundary

Add a Pydantic `HardwareProfileConfig` in `simulator_detailed/configs/schemas/hardware_profile.py`. Keep `ArchConfig` and its existing defaults untouched. New hardware documents use `kind: "hardware_profile"` and `schema_version: 1`; they do not contain legacy `core/noc/mem` sections.

Add classification, inspection, derivation, override and gate helpers in `simulator_detailed/hardware_profile.py`:

- `load_architecture_document(path)` returns a validated `ArchConfig | HardwareProfileConfig`. If either new discriminator is present, validate as a profile and report its errors; never retry a malformed profile as legacy input. Otherwise use existing `ArchConfig` validation, including its rejection of unknown fields.
- `inspect_profile(profile)` returns a typed, versioned inspection report without building an architecture or writing files.
- `override_parameter(profile, parameter_id, value, reason)` returns a newly validated profile with override provenance; it does not mutate its argument or accept arbitrary expressions.
- `require_executable_architecture(document)` returns an `ArchConfig` or raises a dedicated `UnsupportedHardwareProfileError` carrying structured blockers. All hardware profiles are blocked in this child because no profile-to-runtime adapter exists.

```text
JSON input
   |
   +--> legacy ArchConfig --> existing validation --> synthetic runtime
   |
   +--> HardwareProfileConfig --> inspect / validate / override
                 |
                 +--> execution gate --> missing capabilities error
```

`run.arch_analyzer` uses classification and the gate while retaining its `ArchConfig` return type. Move its call to the start of `simulate`, before mapper access and `log_timing`; reuse the loaded config rather than reading twice. `simulate_old` already loads architecture before DFG generation; preserve that order. `Arch.__init__` accepts the union and applies the same gate before creating `env`, accessing `mapper`, or setting up endpoints. After narrowing, its existing implementation continues using `ArchConfig`.

No profile conversion is provided. Direct callers of `ArchConfig.model_validate` still receive ordinary schema errors for profile-shaped JSON. Low-level `NoC` and `EndpointRegistry` still accept only their legacy config types. Supplying an unrelated Python object remains a type error, not a supported conversion path.

Alternatives considered: adding an optional profile to `ArchConfig` creates two potentially contradictory layouts and allows synthetic defaults to look authoritative; replacing all runtime configs with one generic graph schema moves child 2 into this change. The separate type keeps the migration small and makes support boundaries observable.

### 2. Concrete version-one data contract

Use nonempty string IDs for tiles/endpoints/resources/parameters/sources, strict nonnegative integers for fabric/worker indices and coordinates, finite numeric quantities, and `extra="forbid"`. Normalize records and defensively copy collections at override/report boundaries; freezing a Pydantic model alone does not make nested dictionaries immutable. Reject bools/fractions where integers are required. Source text such as names and URLs remains data and is never executed or automatically fetched by the loader.

| Profile section | Version-one contents and validation |
| --- | --- |
| Identity | `kind`, `schema_version`, `profile_id`, `profile_revision`, `architecture`, `architecture_revision`, and one `asic_id`. Optional `board` contains product name, positive `asic_count`, and explicit in-range `selected_asic_index`; there is no ASIC array |
| Sources | Map of source IDs to title, URL, optional repository revision, SHA-256 snapshot hash, access date, and applicability notes. Every referenced source must exist; each source needs an immutable revision or content hash |
| Parameters | Map from stable IDs to typed literal or derived quantities, units and evidence records. Quantities represent architecture facts or explicit assumptions; none implicitly controls runtime service |
| Physical layout | Positive grid extent and explicit tile records with stable `tile_id`, physical `(x,y)`, and role (`worker`, `memory`, `ethernet`, `pcie`, `management`, `transit`). One tile per grid coordinate in v1; inactive positions remain explicit transit or disabled-worker records |
| Worker selection | Explicit `enabled_worker_ids`, logical-worker records mapping stable worker index and `(logical_x, logical_y)` to tile IDs, and selection evidence. Exact bijection with enabled workers; no inference from counts |
| Fabrics | Nonempty unique IDs, extent, clock-parameter reference, topology/routing policy names, and complete physical-tile-to-raw-fabric-coordinate maps. Coordinate maps are bijections; policy names are descriptive requirements, not executable dispatch strings |
| Resources | Unique physical memory IDs, kind (`local_sram` or `dram`), byte-capacity parameter reference, and owner tile where applicable. Shared resources have no duplicate per-alias capacity. This example represents worker L1 and DRAM; other local memories remain outside its represented resource subset |
| Endpoints | Unique endpoint IDs referencing tile ID, fabric ID, and zero or more valid resource IDs. At most one NIU-style attachment per tile/fabric in this v1 profile; no legacy PE/DMA integer or local-port assignment is fabricated |
| Structural evidence | Evidence records on layout, selection, fabrics, resource and endpoint tables, with optional more specific records on exceptions. These table-level records cover their contained identities and mappings without repeating a citation on every coordinate |
| Requirements | Optional additional requested feature IDs. The inspector derives mandatory requirements from structure and adds these requests; they cannot subtract requirements or assign support states |

Generic validation checks referential integrity, table completeness, coordinate bounds, mask/mapping bijections, evidence and quantity dimensions. It does not hardcode 10x12, 80 workers, 72 enabled workers, two fabrics, or 2 GiB into every profile's validators. Those constants are facts of the checked-in Wormhole example, verified by independent conformance fixtures.

Product names are descriptive; they are not hidden constructors. A dual-ASIC board can be represented only as metadata around one selected ASIC. Changing its name or count never scales the modeled resources. Genuine multi-ASIC descriptions require a later schema extension.

### 3. Coordinates and aliases are data with a future runtime owner

Canonical physical coordinates in the example follow raw NoC0 coordinates from the descriptor. NoC0 is the identity map; NoC1 maps `(x,y)` to `(width-1-x,height-1-y)`. Store the expanded maps in the profile; test them against the source formula independently. This child validates mappings, not route paths, dimension-order timing, firmware MMIO translation tables, or dateline behavior.

All 120 physical positions remain present. Each has two profile attachments, including positions that have no compute role. A disabled worker keeps its tile and attachments but has no enabled logical-worker mapping. Its recorded L1 capacity is physical inventory, not usable workload memory.

Each of six DRAM groups owns one 2-GiB capacity. Three physical tile aliases, each with two fabric attachments, reference that same resource. Inspection sums unique IDs. Worker L1 resources are separate per physical worker; report physical-worker L1 inventory and enabled-worker L1 inventory separately. No global pool spanning workers or a usable allocation budget is inferred.

Logical worker indices are explicit simulator identifiers. For the example, enumerate enabled workers in increasing physical row, then column, giving a logical 8x9 worker grid. Label this mapping as a simulator convention rather than firmware-translated NoC addresses. Child 2 will consume these identities while changing runtime PE/router separation.

### 4. A small typed quantity system, not an expression language

Support the units needed here: `bytes`, `Hz`, `cycles`, `bytes_per_cycle`, `bytes_per_second`, `seconds`, and dimensionless `count`. Capacity/count/width values require appropriate integral constraints; clocks are positive; reference latencies are nonnegative. Cycle-based quantities identify a clock parameter in Hz. Convert display KiB/GiB using 1024-based units, and display GB/s explicitly as decimal.

Literal quantities contain a value and evidence. Derived quantities contain an operation and operand IDs, with no cached literal value. Limit operations to the following typed recipes; reject unknown operations, missing references and cycles in the dependency graph:

- `rate_from_clock(bytes_per_cycle, Hz)` produces bytes per second; clock identities must agree.
- `duration_from_cycles(cycles, Hz)` produces seconds; clock identities must agree.
- `sum_bytes(bytes...)` produces a byte quantity when a declared total is useful.

Derived evidence records preserve dependency status, so deriving from an assumed clock cannot turn a reference into documented measured performance. Do not translate these values into existing ACI-stage delays in this child. ACI clocks and existing serialization calculations remain owned by legacy runtime configs.

Evidence contains `status`, source IDs, locator/conditions, and rationale where assumed; calibrated entries also require an identifiable measurement artifact and metadata. A record labeled calibrated is still externally supplied evidence, not an assertion that this tool reproduced that calibration. Inspection discloses that limitation. A base quantity override appends previous value/evidence and reason, marks the new ordinary override assumed, and recalculates dependencies. Reject direct overrides of derived quantities; change their base operands. Structural mask edits require full profile revalidation and updated selection evidence, not a general patch interpreter.

The initial example has an **assumed illustrative AI clock of 1 GHz**, a documented 32-byte flit and 32-byte/cycle reference interface width, and documented packet limits. If idle NIU/router timing anchors are included, label them as reference quantities with source conditions; they do not claim decomposition into simulator stages. Memory bandwidth, queue depths, effective compute rates, and software-reserved L1 budgets can remain absent until their owning child chooses a supported abstraction. Missing data is preferable to invented Wormhole defaults.

### 5. Implementation-owned capability report and inspection command

Use a manifest in `simulator_detailed/hardware_profile.py` mapping feature IDs to support state, scope and reason. States are `executable`, `abstract`, `represented_only`, and `unsupported`; `abstract` means an executable abstraction, while `represented_only` means data with no executing mechanism. Profile inspection/validation is executable. Hardware layout and coordinate/resource tables are represented-only. Profile-to-runtime conversion, Wormhole torus transport, shared memory service and compute dataflow are unavailable. Existing mesh/DMA behavior must not be presented as Wormhole support.

Derive mandatory requirements from the selected document kind and structural policies. A hardware profile always needs the missing `profile_runtime_adapter`; a torus policy also requests corresponding transport, and shared resource aliases request memory service. Unknown policy/feature names can be retained as descriptive requests, but their support is unknown/unsupported and never enables dispatch. Input `support` fields are rejected.

Provide `python -m simulator_detailed.inspect_profile <profile.json>` through a small `simulator_detailed/inspect_profile.py` command wrapper, with a JSON report on stdout. Keep the wrapper separate from the helper imported by `Arch`, avoiding module re-execution through the package's eager imports. Success means a structurally valid profile was inspected, even when `can_execute` is false. Invalid input produces a nonzero exit code and diagnostics on stderr. Inspection has no default output file, network access, simulator process construction or time-varying timestamp. It needs the package's current import dependencies, but not a detector checkpoint or hardware.

Report schema v1 contains profile identity and normalized content SHA-256, manifest/report version, selected ASIC/board metadata, physical/role/enabled counts, coordinate and logical-worker mappings, unique resource summaries, resolved parameters, evidence/override summaries, feature states and sorted blockers, and `silicon_timing: "unvalidated"`. Hash canonical JSON after validation with fixed key ordering and normalized ID-table order; preserve semantically ordered override history. Exclude path and generated timestamps. The hash identifies profile contents, not a test pass or implementation revision. Validation evidence separately records repository revision and tool versions.

### 6. Pinned reference and illustrative mask

References were fetched and hashed on 2026-09-15. Repository pins are:

- TT-Metal: `558637320489ee8ccccea5f2b3a5fcd1e1cfd58f`.
- ISA documentation: `acaf010519f4fdd323df5077e45b8695f70e4279`.

| ID | Immutable source | SHA-256 of fetched raw bytes |
| --- | --- | --- |
| D1 | [Wormhole descriptor](https://raw.githubusercontent.com/tenstorrent/tt-metal/558637320489ee8ccccea5f2b3a5fcd1e1cfd58f/tt_metal/soc_descriptors/wormhole_b0_80_arch.yaml) | `24fd3dfae80435a7d9113e255d6d6af9cdf83f6ae3b1c385e1d48a24795ccf49` |
| D2 | [Physical chip inventory](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/README.md) | `9fadd2d7173c5a9e83495ccc08f0342b325ff674cfa51d12833ddcf94b2efe9b` |
| D3 | [Worker L1](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/TensixTile/L1.md) | `276d09a25442beba658a81ff19a5b82462c20b744ddf582130c69680c4257335` |
| D4 | [Raw and translated coordinates](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/Coordinates.md) | `328f014798fe3cec46ec2c9555a8d843074b7eb62eb4de996e6d85a0f7ec9174` |
| D5 | [NoC reference](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/README.md) | `3e273cd45beb4ff1e589c676a0a41d3eb0746d6f83ac2d7cc3e4bc8925201c0c` |
| D6 | [Unicast routing paths](https://raw.githubusercontent.com/tenstorrent/tt-isa-documentation/acaf010519f4fdd323df5077e45b8695f70e4279/WormholeB0/NoC/RoutingPaths.md) | Not recorded; immutable ISA revision above is pinned |

D1 provides grid and tile tables; its empty `harvested_workers` list describes the architectural 80-worker layout, not an observed n150. D4 documents that the harvested row varies by ASIC. The checked-in example uses an explicit **assumed y=11 worker-row mask**; all eight worker positions in that row are excluded. Changing to a real device requires replacing the mask and its evidence, not relabeling this assumption as measured.

Independent expected facts for the conformance fixture:

| Fact | Expected value |
| --- | --- |
| Physical extent / roles | 10x12; 80 workers, 18 memory aliases, 16 Ethernet, 1 PCIe, 1 management, 4 transit positions |
| Worker coordinates | Columns 1,2,3,4,6,7,8,9; rows 1,2,3,4,5,7,8,9,10,11 |
| Special/transit positions | PCIe (0,3); management (0,10); transit (0,2), (0,4), (0,8), (0,9) |
| DRAM alias groups | D0: (0,0),(0,1),(0,11); D1: (0,5),(0,6),(0,7); D2: (5,0),(5,1),(5,11); D3: (5,2),(5,9),(5,10); D4: (5,3),(5,4),(5,8); D5: (5,5),(5,6),(5,7) |
| Unique DRAM capacity | 6 x 2,147,483,648 = 12,884,901,888 bytes; attachment count does not multiply it |
| Worker L1 | 1,499,136 bytes per physical worker; 119,930,880 bytes physical inventory; 107,937,792 bytes associated with the 72 enabled workers |
| Endpoint inventory | 240 tile/fabric attachments in this descriptive profile; this is not an executable endpoint count |
| Assumed worker selection | Exclude eight workers at y=11, leaving an explicitly mapped 8x9 logical worker grid |

Store extracted expected facts and source metadata in `simulator_detailed/tests/fixtures/wormhole_b0_profile_reference.json`, separate from the production example. Tests must not generate expectations by serializing the profile under test. No live source fetch is required for normal tests or inspection, and a generic YAML-import subsystem is outside this child. Source checksum retrieval can be a documented maintainer verification step.

### 7. Validation and environment gate

Before apply, the default interpreter was `/Users/feiyangwu/miniconda3/bin/python`, Python 3.12.12. Read-only discovery found Pydantic/NumPy available, but `simpy`, `scipy`, `pyright`, and `ruff` absent. Planning did not establish a runtime suite or type/lint pass. Apply established the isolated environment and checks recorded below. Existing `requirements.txt` is a Linux Conda export with CUDA packages, not the empty file described in stale OpenSpec context and not a file to install with macOS pip.

During apply, identify a compatible existing environment or create an isolated project environment using the required existing package imports and check tools; record exact versions and reproduction instructions in profile documentation. Do not rewrite the full Linux dependency export or add a new runtime dependency just to parse a one-off descriptor. Do not change package import architecture to disguise missing dependencies.

Validation matrix:

| Contract | Required evidence |
| --- | --- |
| HP-P01/P02 | Legacy round trip; mixed/unknown-version input rejection; exactly-one-ASIC selection and no board-total multiplication |
| HP-P03/P04 | Full source layout cross-check; invalid mask/mapping/reference cases; raw coordinate bijections; unique resource totals; disabled workers retain attachments |
| HP-P05/P06 | Invalid units/nonfinite/cyclic references rejected; native and derived unit calculations; independent 1000-to-800-MHz override expectation; original profile unchanged |
| HP-P07 | Stable report/hash across JSON key order and paths; no file/process creation; truthful manifest and evidence states |
| HP-P08 | Direct `Arch` and loader refusal; `simulate` rejects before mapper/failure-file access, timing log, trace output, resource construction or detector invocation; feature deletion cannot bypass the adapter blocker |
| HP-P09 | Checked-in example agrees with independently extracted facts, exact L1 bytes and assumed row label |
| HP-P10 | Full relevant detailed suite, real mesh/DMA smoke, targeted type/lint results, and a report distinguishing unavailable hardware evidence |

Use `unittest` for focused profile tests and the existing detailed suite. Retain the existing synthetic example smoke `Phase2NoCTests.test_json_example_runs_with_custom_fabric_overrides`, which actually transfers messages. The `simulate` gate test uses a temporary working directory and a mapper sentinel that fails if touched; mocks may assert absence of calls, but input parsing and capability evaluation remain real. A synthetic-network test provides positive execution coverage, so the gate is not validated only by mocked exceptions.

Type-check new modules through additions to `simulator_detailed/pyrightconfig.phase2.json`; run strict checks using its established settings. Run Ruff's selected correctness rules (`E9,F63,F7,F82`) on changed Python files; baseline unrelated style/import debt is not part of this child. New modules also receive the project's applicable lint checks once the available configuration is confirmed. Record command exit codes and versions; do not suppress diagnostics or call import failures behavioral regressions.

### 8. Requirement ownership and handoff

| Umbrella target | This child's deliverable | Still pending |
| --- | --- | --- |
| HP-01 | HP-P02/P09: one selected ASIC and illustrative n150 identity | Executing that ASIC |
| HP-02 | HP-P05/P06: typed quantities, sources, assumptions and derived inspection | Applying rates/timing to future resource models |
| HP-03 | HP-P03/P04: physical/enabled/logical/fabric identity data | Scheduler eligibility and actual transit through harvested positions |
| HP-04 | HP-P01/P07/P08: compatibility and truthful execution gating | Incrementally enabling validated profile execution in later children |
| VA-01..03/06/07 | HP-P10 and child validation report: pinned facts, explicit evidence strength and pending work | General harness, measured timing, and final umbrella reconciliation |

This child uses concrete HP-Pxx requirement names under the canonical `wormhole-hardware-profile` capability. The parent HP-xx names remain target-level tracking IDs. At archival, deliver only this child's implemented requirements; later children add/modify against that baseline. Do not blindly sync the umbrella's pending targets or claim that its HP-03 runtime scenario is already satisfied. Apply is authorized for this child; the association is recorded here without marking the umbrella's later runtime requirements delivered.

After apply and validation, child 2 explores generic heterogeneous topology using these records and the actual test evidence. It decides runtime router/worker/resource construction, graph export and downstream compatibility, while preserving the separate profile and legacy input boundary.

## Risks / Trade-offs

- [The profile becomes an untyped bag of constants] -> Typed quantities, resource references, bounded derivations and explicit table validators keep its semantics inspectable.
- [Data-only layouts look like runtime support] -> No adapter, implementation-owned states, and early gates on both loader and constructor.
- [Two schemas diverge over time] -> Keep their roles explicit; later adapters consume one validated profile rather than copying it into defaulted legacy fields.
- [The assumed harvest row is mistaken for a product fact] -> Include `assumed` in filename/profile identity and selection evidence, and test that the report retains it.
- [Source revisions change or capacities are rounded] -> Pin raw source hashes and test exact extracted bytes; update profile revision when facts change.
- [Gate integration changes legacy startup] -> Load once, preserve validated `ArchConfig` values and existing successful runtime behavior; test logging/mapper ordering only where the early gate requires it.
- [Dependency work grows into unrelated cleanup] -> Establish a scoped environment and document versions; leave top-level Linux/CUDA workflow and unrelated lint debt intact.

## Migration Plan

Add new files and the two entry-point guards incrementally. Existing users keep their accepted `ArchConfig` JSON and runtime APIs. New users inspect the separately named profile and receive explicit refusal when trying to execute it. No checkpoint, trace, DFG or failure-data migration is needed.

Review each implementation task's behavior and tests, then publish exact commands, source/fixture identity, implemented/represented-only capabilities and unresolved limits. No measured hardware or ttsim run is required to validate this data-contract child, and none is implied by its completion. Commit each completed, validated part before continuing, as requested by the user. Push, spec synchronization and archival remain separate requested actions.

## Implementation evidence — 2026-09-15

The user authorized apply for this child. The standalone schema, profile
inspection/override helpers, CLI, early execution gates, full illustrative
Wormhole example, independent fixture, documentation and regressions are
implemented. No profile-to-runtime conversion was introduced.

The current `simulate` gate runs before mapper access, failure parsing and
simulation logging, with one architecture read. Direct `Arch` construction
narrows to `ArchConfig` before any resource setup. `simulate_old` retains its
architecture-first startup. Existing Core/NoC/DMA/EndpointRegistry internals
and their runtime contracts were not modified.

Contract details resolved during implementation:

- Input tables are named `layout`, `worker_selection`, `fabrics`, `memory` and
  `attachments`. Structural `derived` evidence is rejected because this v1 has
  quantity recipes only, not structural derivation recipes.
- Report schema v1 embeds the normalized profile, keeping identities,
  selected-ASIC metadata, source records and coordinate tables together.
  `resolved_parameters` carries transitive dependency states and override
  history; source/calibration verification is explicitly metadata-only.
- Recipe evaluation and dependency evidence reuse shared dependencies. A
  30-level shared byte-sum graph verifies exact values and retained assumptions.
- Boundary revalidation deep-copies nested collections. `require_executable_architecture`
  also accepts an arbitrary Python object at its type boundary so it can reject
  unrelated objects with TypeError; valid callers still receive `ArchConfig`.
- Literal overrides of derived quantities raise TypeError. Invalid values and
  missing provenance fail validation; a normal override always becomes assumed.
- Profile constants remain configurable references. Cross-field relationships
  between unrelated literals are not inferred from names; no packet/runtime
  service semantics are claimed from these records.

Environment: Python 3.12.12 at
`/Users/feiyangwu/codes/RL-Schedule/.venv/bin/python`; Pydantic 2.13.5, NumPy
2.5.3, SimPy 4.1.2, SciPy 1.18.1, Pyright 1.1.414, Ruff 0.16.7. No dependency
export changes. The initial restricted-network install failed; installation
succeeded with authorized network access. Pyright required an explicit
`--pythonpath .venv/bin/python`; the unqualified invocation reported 1,482
missing-dependency-related diagnostics. No diagnostics were disabled.

| Check | Result |
| --- | --- |
| Pre-change detailed regression baseline | 29 tests passed |
| Baseline strict type check with explicit environment | 0 errors, 0 warnings |
| Baseline selected correctness lint | Passed |
| Final detailed regression suite | 52 tests passed: 29 existing and 23 new |
| Explicit configured mesh transfer + DMA suite | 9 tests passed |
| Strict type check including all three new production modules | 0 errors, 0 warnings |
| Correctness lint on changed Python; applicable Ruff rules on new files | Passed |
| Documented inspection and override commands | Passed; 120/80/72 positions/workers/enabled workers and 25,600,000,000-byte/s overridden reference |
| Strict OpenSpec validation | Passed |

Reproduction commands and HP-P01..P10-to-test mapping are recorded in
`simulator_detailed/docs/hardware_profile.md`. Schema negatives cover the
single-ASIC boundary, strict IDs/units, mappings, evidence, references and
acyclic recipes. Gate tests exercise real parsing/capability evaluation and
use mapper/side-effect sentinels to establish rejection ordering. A direct
legacy constructor and actual synthetic transfer regressions provide positive
execution coverage.

All D1..D5 raw source bytes were re-read and their recorded SHA-256 hashes
verified during apply. The independently transcribed fixture checks all tile
roles, raw fabric coordinates, logical mask, DRAM aliases and exact capacities.
It is not generated from the production profile. Normal tests remain offline.

| Identity | SHA-256 |
| --- | --- |
| Normalized effective example | `e42618fe13d0d7978bc6622cb2050dd146efa693a18190b9042913b552baa132` |
| Example JSON file | `a959592f39b257091c509d95abde6842e10e08d84123fde6788ed55372429b9d` |
| Independent fixture JSON file | `04ef81eee6b197646903102ebc72becfe40946be06519c5425539f3bd41ea8fe` |

These identities apply to profile/fixture content, not simulator implementation
or hardware fidelity. The base revision above plus the child implementation in the commit containing
this evidence identifies this validation run. No silicon measurement or ttsim execution was
performed. The y=11 mask and 1-GHz clock remain assumed.

Child 2, `generic-heterogeneous-topology`, can now explore runtime construction
against validated stable tile, worker, fabric, endpoint and resource identities.
It must decide router/worker separation, graph export and downstream
compatibility. Umbrella HP-03 scheduling/transit, NoC routing, memory service,
compute and the later validation harness remain pending; the adapter blocker
must remain until a compatible runtime is actually delivered. The completed child is committed before continuing, per the user's workflow.
Push, synchronization and archival remain separate requested actions.

Pre-commit review corrected the descriptive NoC1 routing policy to
`dimension_order_yx`; NoC0 remains `dimension_order_xy`. D6 was checked at the
pinned ISA revision, and the independent fixture now asserts both policy names.
The raw-byte hash fetch for D6 timed out, so its snapshot hash remains absent;
the immutable revision satisfies the source contract. Both policies remain
unsupported for profile execution.
