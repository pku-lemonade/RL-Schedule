## Why

The detailed simulator's configuration describes executable synthetic meshes but cannot distinguish a chip's physical layout, enabled workers, shared memory aliases, and sourced hardware parameters. The first child of `wormhole-single-chip-simulation-plan` needs an inspectable hardware-profile contract that makes these distinctions without implying that Wormhole routing or memory execution already exists.

## What Changes

- Add a versioned, standalone hardware-profile JSON contract with one selected ASIC, product metadata, tile/resource identities, explicit enabled-worker mappings, per-fabric coordinates, clock domains, and parameter provenance.
- Keep the existing `ArchConfig` JSON contract as the legacy executable input. Introduce explicit input classification and a shared execution gate; do not populate synthetic `core/noc/mem` defaults from a hardware profile.
- Add deterministic profile inspection with physical/enabled counts, shared capacity totals, resolved parameter units, source/assumption records, content identity, and implementation-owned capability status.
- Provide a Wormhole B0 n150 example using a pinned 80-worker physical descriptor and an explicitly assumed one-row harvest mask. Its 72-worker layout is an illustrative configuration, not a measured device's mask. Use the source-confirmed worker L1 capacity of 1,499,136 bytes.
- Reject profile execution before architecture construction or simulation output, listing missing capabilities. A profile cannot enable unimplemented behavior by declaring itself supported.
- Add profile conformance tests, a real synthetic-network regression path, and targeted type/lint checks. Record source revisions and environment limitations separately from hardware timing validation.

## Capabilities

### New Capabilities

- `wormhole-hardware-profile`: Versioned single-ASIC profile parsing, layout and provenance validation, deterministic inspection, capability gating, and legacy configuration compatibility.

### Modified Capabilities

None. This capability does not yet exist in `openspec/specs`. The umbrella contains pending target requirements, not a delivered baseline.

## Impact

- **Simulator:** Add `simulator_detailed/configs/schemas/hardware_profile.py`, `simulator_detailed/hardware_profile.py`, and the small command wrapper `simulator_detailed/inspect_profile.py`; integrate the input gate into `simulator_detailed/run.py` and the beginning of `simulator_detailed/architecture.py` construction. Keep `ArchConfig` fields/defaults, runtime PE creation, `EndpointRegistry`, NoC transport, DMA, memory service, and compute behavior intact in this child.
- **JSON and artifacts:** Add `simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json` and a versioned inspection-report JSON contract. Add profile documentation and a small source/conformance fixture manifest under detailed tests. Existing `mesh_example.json`, mappings, failure JSON, and runtime trace structures (`Event`, `Trace`, `Flit`, `Message`, `FlitEvent`, `NoCLinkIdentity`, `MessageFabricTiming`) keep their contracts.
- **Detector:** No graph/features or trained model artifacts change. Profile execution is refused before detection; an inspection report is not a predictor input.
- **RL:** Observation/action shapes, local-remap behavior, rewards, and checkpoints do not change. The new profile cannot silently reach the current mesh-based RL execution path.
- **Compatibility:** Preserve the supported detailed synthetic examples and the separate top-level 4x4 mesh + Darknet19 + failure workflow. Older rejected numeric architecture encodings are not migrated by this work.
- **Tooling:** Extend the detailed pyright include list for new modules. Reuse existing runtime dependencies and establish a documented test/type/lint environment; the repository's Linux Conda export is not a portable macOS pip requirements file.
- **Delivery boundary:** This child addresses the data/inspection portion of umbrella HP-01..04 and establishes initial VA-01..03/06/07 evidence practices. Runtime enabled-worker scheduling, torus routing, transaction service, and general validation tooling belong to later children. The user has authorized apply for this child; implementation evidence and remaining runtime limits are recorded in the design and hardware-profile documentation.
