## ADDED Requirements

### Requirement: RL environment exposes certified local remap actions
The RL mitigation environment SHALL expose local remap actions that identify an action type, target layer, source core, and destination core, and SHALL interpret those actions as layer-local workload edits rather than global repartition-by-core-count mutations.

#### Scenario: Policy requests a local split onto an unused healthy core
- **WHEN** a policy chooses a local split action for a layer, with a source core currently assigned to that layer and a destination core not currently assigned to that layer
- **THEN** the environment SHALL pass a candidate local remap request to the mapper and SHALL not reinterpret the request as a global full-layer repartition

#### Scenario: Policy requests a shift between active neighboring blocks
- **WHEN** a policy chooses a local shift action for a layer, with both source and destination cores currently active for that layer
- **THEN** the environment SHALL treat the request as a boundary adjustment between existing layer-local workload blocks and SHALL preserve the current layer’s active core set unless the action type explicitly changes it

#### Scenario: Replace swaps workloads between active layer cores
- **WHEN** a policy chooses a replace action for a layer with both source and destination cores already active in that layer
- **THEN** the mapper SHALL swap the workloads owned by those two active layer cores rather than interpreting the action as a migration to an unused core

#### Scenario: Remove merges one active block into another active block
- **WHEN** a policy chooses a remove action for a layer with both source and destination cores active in that layer
- **THEN** the mapper SHALL merge all of the source workload into the destination workload and SHALL remove the source block from the layer’s active block set

### Requirement: Accepted remaps SHALL be mapper-certified before they mutate RL state
The mapper SHALL apply candidate remaps transactionally and SHALL accept a candidate only if it changes the layer layout, preserves family-consistent input/output/weight mappings, and produces a constructible DFG for the existing simulator workflow.

#### Scenario: No-op or illegal action is rejected
- **WHEN** a policy proposes an action that leaves the canonical layer layout unchanged, targets an invalid source or destination core, or cannot be certified
- **THEN** the mapper SHALL reject the action, SHALL leave the committed mapping unchanged, and the environment SHALL report the step as a rejected action instead of a successful remap

#### Scenario: Certified local remap regenerates a DFG-safe mapping
- **WHEN** a policy proposes a local remap that passes mapper certification
- **THEN** the mapper SHALL regenerate layer mappings for the affected tensor families, rebuild the DFG through the existing generation path, and commit the candidate mapping only after those checks succeed

### Requirement: The mapper SHALL maintain an internal block view after the original mapping bootstrap
The mapper SHALL record an internal block-level representation from the original mapping using the existing DFG inference logic and SHALL apply later remap actions to that internal structure instead of continuing to infer block sizes from `input_fetch` and `output_partition` alone.

#### Scenario: Original mapping initializes internal block view
- **WHEN** the mapper loads the unmodified original mapping
- **THEN** it SHALL derive an internal view of per-block workload information that is sufficient to regenerate later DFGs without relying only on regular partition dimensions

#### Scenario: Later remap uses recorded internal blocks
- **WHEN** the mapper applies a remap after the original mapping has already been initialized
- **THEN** it SHALL update the recorded internal block structure and SHALL not assume that all resulting blocks remain equal in size

### Requirement: The new internal-view DFG generator SHALL match the original generator on the unmodified mapping
Before any remap actions are applied, the internal-view DFG generation path SHALL preserve the behavior of the existing DFG generation logic on the original checked-in mapping.

#### Scenario: Original mapping DFG equivalence check
- **WHEN** the mapper generates a DFG from the original unmodified mapping
- **THEN** the internal-view DFG generator SHALL produce the same effective block-level execution behavior as the current reference generator

### Requirement: Mapper certification SHALL prove exact data-coverage invariants
For every accepted local remap, the mapper SHALL certify exact output coverage, family membership consistency, fetch legality, and exact upstream coverage for consumer slices so that readiness accounting remains valid for the regenerated DFG.

#### Scenario: Candidate remap introduces a gap or overlap
- **WHEN** a candidate local remap produces output blocks or derived consumer coverage with a gap, overlap, or zero-sized slice
- **THEN** the mapper SHALL reject the candidate and SHALL report the failure before the environment commits the remap

#### Scenario: Candidate remap preserves grouped or halo-derived families
- **WHEN** a candidate local remap changes the boundary of a layer whose input or weight families are shared, grouped, mirrored, or halo-derived from the output partition
- **THEN** the mapper SHALL regenerate those families according to their inferred signature and SHALL reject the candidate if the resulting active-core memberships no longer align with the regenerated DFG inputs

### Requirement: Environment resets SHALL restore a clean baseline mapping
The RL environment SHALL start each episode from a clean baseline mapping state and SHALL not reuse in-place mutations from earlier episodes.

#### Scenario: Prior episode changed a layer layout
- **WHEN** an episode ends after one or more accepted local remaps
- **THEN** the next environment reset SHALL restore the baseline mapping for all layers before sampling a new fail-slow scenario or running the initial simulation
