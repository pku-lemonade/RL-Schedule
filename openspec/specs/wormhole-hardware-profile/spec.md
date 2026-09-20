# wormhole-hardware-profile Specification

## Purpose

Describe a single Wormhole B0 ASIC through reusable, configurable hardware contracts with explicit provenance, compatibility, and supported fidelity.

## Contract scope

HP-01..04 describe the single-ASIC capability contract. HP-P01..10 describe
profile inspection and the legacy architecture-construction boundary, including
its execution gate. Inspectable inventory does not grant runtime support.
Explicit transport, memory, compute and mixed-workload admission are governed by
their respective capability specifications; those opt-in paths do not remove the
legacy full-profile gate. Profile-child delivery statements concern that
inspection milestone, not the availability of all later adapters.

## Requirements

### Requirement: HP-01 Single-ASIC identity and scope
The simulator SHALL distinguish chip architecture, ASIC instance, board product, and enabled compute layout. The initial Wormhole execution target SHALL contain one ASIC; selecting a multi-ASIC board MUST NOT silently combine its compute, memory, or bandwidth into one chip.

#### Scenario: Dual-ASIC board requested in single-chip mode
- **WHEN** a user requests the complete n300 board in single-chip mode
- **THEN** configuration is rejected with the scope mismatch, or requires an explicit selection of one ASIC with that ASIC's resources.

### Requirement: HP-02 Configurable hardware parameters with provenance
Device-dependent dimensions, capacities, clocks, bandwidths, packet sizes, service costs, and compute rates SHALL be profile parameters or explicitly selected policies. Every Wormhole reference parameter SHALL record its units, source and revision when available, applicable hardware/software conditions, and status as documented, derived, assumed, or calibrated. Cycle conversion SHALL identify the relevant clock domain.

#### Scenario: Clock or assumed latency is changed
- **WHEN** a user overrides a clock or an assumed latency
- **THEN** the effective configuration and evidence report expose the override, derived time values are recomputed consistently, and the value is not represented as a measured silicon constant.

### Requirement: HP-03 Physical layout and enabled compute are distinct
Profiles SHALL distinguish physical router coordinates, tile roles, enabled compute endpoints, logical worker coordinates, and per-fabric coordinates. A disabled compute tile SHALL NOT remove transit routing unless its router or links are separately disabled. An enabled-core count alone MUST NOT determine a fabricated harvest map presented as an actual device layout.

#### Scenario: Harvested compute tile on a transit route
- **WHEN** a compute tile is excluded by the enabled mask while its network remains enabled
- **THEN** scheduling excludes that compute endpoint and routing can still traverse its physical router.

#### Scenario: Enabled count is known but layout is unavailable
- **WHEN** a profile has a product core count without an authoritative enabled-coordinate mask
- **THEN** it requires an explicit mask for device-specific validation or labels an explicitly selected synthetic mask as an assumption.

### Requirement: HP-04 Explicit compatibility and feature support
Existing supported synthetic configurations SHALL retain their documented semantics. New profiles SHALL declare executable, abstractly modeled, represented-only, and unsupported features separately. Requests for unavailable execution features MUST fail clearly before producing performance results; profile parsing alone MUST NOT imply executable hardware support.

#### Scenario: Profile precedes its runtime implementation
- **WHEN** a valid Wormhole profile requests routing or transactions not yet implemented
- **THEN** its data can be inspected but execution fails with the unavailable features identified.

#### Scenario: Existing supported mesh example
- **WHEN** a previously supported synthetic mesh configuration is run without opting into new policies
- **THEN** its topology, DMA semantics, failure behavior, and trace timing meanings retain the baseline contract.

### Requirement: HP-P01 Versioned profile input is unambiguous

The system SHALL recognize a versioned hardware-profile document separately from the existing executable synthetic architecture document. Unknown profile versions, ambiguous mixed documents, unknown fields, non-finite numeric parameters, and invalid identifiers SHALL be rejected with field-specific diagnostics. A hardware profile SHALL NOT acquire synthetic runtime defaults through parsing.

#### Scenario: Hardware profile parses without a synthetic runtime configuration

- **WHEN** a valid version-one hardware profile contains physical layout and resource descriptions
- **THEN** it can be inspected without manufacturing synthetic core, NoC, or memory runtime settings.

#### Scenario: Profile and legacy fields are mixed

- **WHEN** an input combines a hardware-profile discriminator with legacy runtime configuration fields, or names an unknown schema version
- **THEN** parsing fails clearly and does not retry the document as a legacy synthetic configuration.

### Requirement: HP-P02 Exactly one ASIC is selected

A hardware profile SHALL identify architecture/revision, profile identity, and exactly one ASIC instance separately from optional board metadata. Board ASIC count SHALL NOT multiply the selected ASIC's resources. A multi-ASIC board description SHALL require an explicit selected ASIC within its bounds; a document containing multiple ASIC instances SHALL be rejected in this version.

#### Scenario: One ASIC from a dual-ASIC board is described

- **WHEN** board metadata names two ASICs and the profile explicitly selects one
- **THEN** inspection totals only the selected ASIC and does not report full-board simulation support.

### Requirement: HP-P03 Physical layout survives compute harvesting

The profile SHALL distinguish physical tiles/router positions, tile roles, enabled workers, and logical worker mappings. Tile identifiers and physical coordinates SHALL be unique and in bounds. The enabled set SHALL be explicit, contain only worker tiles, and match the declared logical mappings. Disabling a worker SHALL preserve its physical tile, fabric coordinates, and network attachments in the inspection result. This contract describes eligibility data; scheduling and transit execution remain outside this child.

#### Scenario: A worker row is disabled

- **WHEN** an explicit mask disables one physical worker row
- **THEN** enabled-worker and logical-worker counts decrease while physical router and attachment counts remain unchanged.

#### Scenario: Invalid worker selection

- **WHEN** an enabled mask references a DRAM tile, an unknown tile, a duplicate worker, or a logical mapping omits or duplicates an enabled worker
- **THEN** validation reports the offending reference rather than inferring a replacement layout.

### Requirement: HP-P04 Coordinate and resource identities are independently validated

Each declared fabric SHALL map every physical tile to an explicit fabric coordinate within its declared extent, with a one-to-one mapping. Endpoints SHALL identify a tile, fabric, and referenced physical memory resources independently of logical worker numbering. Resource capacity SHALL belong to the resource identity, so multiple attachments to one resource do not duplicate capacity. These mappings SHALL remain data until a compatible topology executor is implemented.

#### Scenario: Several DRAM attachments share one backing resource

- **WHEN** three tile positions on each of two fabrics reference one DRAM group
- **THEN** inspection reports six attachments to one backing capacity and counts that capacity once.

#### Scenario: Fabric translation is invalid

- **WHEN** a fabric coordinate mapping is incomplete, out of bounds, or maps two physical tiles to the same coordinate
- **THEN** validation rejects that fabric mapping with the conflicting or missing identities.

### Requirement: HP-P05 Hardware quantities have units and evidence

Hardware quantities SHALL carry explicit units, applicable clock-domain references when cycle-based, and evidence status as documented, derived, assumed, or calibrated. Sourced quantities and structural tables SHALL reference recorded immutable source revisions or snapshot hashes. Assumptions SHALL provide a rationale; calibrated claims SHALL reference measurement metadata. Derived quantities SHALL identify a supported derivation and valid acyclic dependencies. Inspection SHALL retain source conditions and evidence limits without treating a documented timing anchor as executed or calibrated service.

#### Scenario: Cycle-based timing is inspected

- **WHEN** an idle-latency reference is specified in cycles of a declared hardware clock
- **THEN** inspection identifies both the native clock and converted time, labeling it as a reference rather than runtime latency.

#### Scenario: Evidence or units are inconsistent

- **WHEN** a capacity references a rate quantity, a cycle quantity lacks a clock, a derived dependency is missing or cyclic, or an assumed value lacks a rationale
- **THEN** the profile is rejected before it produces an inspection report presented as valid.

### Requirement: HP-P06 Overrides preserve origin and recompute derived values

A supported parameter override SHALL create a validated effective profile without mutating the source profile. The effective value, previous value/evidence, and override rationale SHALL remain inspectable. An ordinary user override SHALL be labeled assumed unless separately supplied with valid supporting evidence. Affected derived values and the effective profile's content identity SHALL update consistently; stale stored derived values SHALL be rejected or avoided.

#### Scenario: Clock override changes a derived reference rate

- **WHEN** a profile's clock changes from 1000 MHz to 800 MHz while its reference transfer width remains 32 bytes per cycle
- **THEN** the derived rate changes from 32,000,000,000 to 25,600,000,000 bytes per second, the original profile is unchanged, and the override is visible as an assumption.

### Requirement: HP-P07 Inspection is deterministic and truthful

Inspection SHALL return versioned JSON containing the effective profile identity, physical and enabled counts, coordinate/worker mappings, unique memory capacities, resolved quantities, assumptions, and implementation-owned capability states. It SHALL distinguish executable behavior, abstract execution, represented-only data, and unsupported behavior, and SHALL report silicon timing as unvalidated in this child. Repeated inspection of the same normalized profile with the same implementation SHALL produce the same semantic report and content identity, independent of file location and JSON object-key order.

#### Scenario: The same profile is inspected twice

- **WHEN** identical profile contents are loaded from different file paths with reordered JSON object keys
- **THEN** normalized reports and profile content hashes agree, and no simulation processes or runtime trace files are created.

### Requirement: HP-P08 Hardware-profile execution is gated

The execution loader and direct architecture-construction entry point SHALL reject hardware-profile execution before constructing simulator resources, processing a workload, or producing simulation result/trace files. Diagnostics SHALL identify the profile and missing support, including the profile-to-runtime adapter. Required capabilities SHALL be derived from the profile structure and implementation manifest; user-provided declarations SHALL NOT grant support or remove mandatory blockers. Legacy configurations SHALL continue through their existing executable path and validations.

#### Scenario: A valid Wormhole profile is submitted to execution

- **WHEN** a user supplies the initial profile to a simulation entry point or directly to architecture construction
- **THEN** execution fails with capability diagnostics before network/core construction and does not emit simulated timing, run detection, or silently use a mesh.

#### Scenario: Capability declarations attempt to bypass the gate

- **WHEN** requested feature names are removed or a profile attempts to declare its runtime support as available
- **THEN** mandatory blockers remain effective or the invalid field is rejected; no Wormhole runtime is created.

### Requirement: HP-P09 Initial Wormhole example has explicit fidelity

The supplied n150 example SHALL describe the pinned Wormhole B0 physical layout, two raw fabric-coordinate maps, distinct DRAM groups/aliases, and an explicitly assumed 72-worker mask. Exact capacities SHALL follow the recorded source, including 1,499,136 bytes of L1 per physical worker. The example SHALL distinguish the all-worker architectural descriptor from the assumed product mask and SHALL NOT claim the latter describes a measured board or emulates firmware coordinate translation.

#### Scenario: Initial example is inspected

- **WHEN** the provided example is validated and inspected
- **THEN** it reports 120 physical router positions, 80 physical workers, 72 enabled workers, six unique 2-GiB DRAM resources, and the assumed harvest row without reporting executable Wormhole support.

### Requirement: HP-P10 Profile delivery includes independent conformance evidence

The change SHALL provide offline profile tests with expected layout/capacity facts independently extracted from the recorded sources, negative validation and execution-gate cases, parameter override checks, and a supported synthetic-network smoke regression. Relevant type and lint checks SHALL cover the new modules. Evidence SHALL separate architecture conformance from model behavior and hardware measurement, report dependency/tool failures as unavailable checks, and leave the umbrella's later runtime requirements pending.

#### Scenario: Profile tests pass without hardware measurements

- **WHEN** source-conformance, input/gate, synthetic runtime, and static checks pass
- **THEN** the child reports profile support and those check results while leaving Wormhole runtime execution and silicon timing accuracy unsupported/unvalidated respectively.
