## Purpose

Describe a single Wormhole B0 ASIC through reusable, configurable hardware contracts with explicit provenance, compatibility, and supported fidelity.

## ADDED Requirements

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
