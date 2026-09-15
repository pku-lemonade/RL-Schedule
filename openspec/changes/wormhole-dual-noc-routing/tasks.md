## 1. Versioned transport contracts and admission (TR-D01, D06, D09)

- [x] 1.1 Add strict version-2 replay configuration in `simulator_detailed/configs/schemas/torus_replay.py` with tagged graph/profile input, transport binding, explicit native clock/timing/capacity fields and policy revision; verify round trips and rejection of unknown versions/fields, invalid units, nonfinite values and missing assumptions.
- [x] 1.2 Define finite packet/causal-response fixture and directed-slowdown records, including endpoint roles and interval rules; verify invalid classes, nested responses, duplicate identities, overlapping intervals and unsupported hardware modes fail at admission.
- [x] 1.3 Define immutable effective-plan/result/envelope contracts and deterministic source/config identity rules without enabling the CLI runtime; verify reordering normalization, path-independent identity, meaningful setting changes, and preservation of legacy flit serialization and version-1 schemas.
- [x] 1.4 Extend strict Pyright coverage for the new module(s) and add focused admission tests; verify the existing profile execution gate and detailed suite remain valid before any new runtime construction is enabled.
- [x] 1.5 Run the focused contract tests, applicable strict type/lint and `git diff --check`; review source/evidence assumptions and commit this completed part before part 2, recording the commands and commit in the change's delivery notes.

## 2. Canonical torus binding, route oracle and resource order (TR-D01..03)

- [x] 2.1 Add the generic torus builder/profile binding in `simulator_detailed/torus.py`, preserving canonical identities, physical roles, raw maps, worker masks and resource aliases; verify configurable small grids and the 240-router/480-link Wormhole inventory, including harvested-worker transit and explicit endpoint admission.
- [x] 2.2 Add deterministic XY/YX modular unicast compilation, local routes and immutable port/link paths; verify all 28,800 ordered pairs on the selected profile against an independently expressed physical-coordinate oracle, including the four-versus-eighteen-hop example and unavailable-edge rejection.
- [x] 2.3 Compile request/response dateline lane paths and their dependency graph/ranks, including local channels and packet ownership; verify configurable shifted datelines on small tori and rejection of wrong-phase, within-axis reset, unsupported turn/class and dependency-cycle mutations.
- [x] 2.4 Add independently transcribed source/route fixtures with immutable URLs, source hashes or inherited evidence explicitly identified; verify the oracle does not call production builders and the version-1 explicit-route DAG guard still rejects its cyclic negative case.
- [x] 2.5 Run binding/routing/negative fixtures, strict type/lint and relevant legacy routing tests; review the written resource-order argument against compiled paths and commit this part before part 3 with its validation evidence.

## 3. Finite lanes and one physical serializer (TR-D03, D04, D06)

- [x] 3.1 Add the generic opt-in lane/credit kernel in `simulator_detailed/virtual_channel.py` with explicit bounded storage and exact token lifecycle; verify tiny capacities, delayed returns, queue/register accounting, overflow/double-return rejection and cross-plan rejection without state mutation.
- [x] 3.2 Implement independent per-lane FIFO/packet ownership and fair eligible-lane arbitration over one physical serializer; verify a zero-credit or idle-owner lane cannot block ready lanes, same-lane packets retain order and different lanes share rather than multiply bandwidth.
- [x] 3.3 Implement explicit native-to-ACI serialization, launch spacing, propagation and bounded staging, reusing/extracting only appropriate pure helpers; verify two width/clock analytical cases and backpressure from small capacity without silently enlarging its budget.
- [x] 3.4 Add lane/token/stage events and occupancy/drain inspection needed to diagnose kernel behavior; verify events reconcile to actual launches, credits and configured bounds, and any shared-helper extraction retains the legacy custom-mesh timing fixture.
- [x] 3.5 Run focused kernel/analytical tests, strict type/lint and affected legacy link tests; audit every shared grant for downstream waits and every retained flit for bounded storage, then commit this part before part 4.

## 4. Cut-through torus router and one-way replay (TR-D02..04, D08)

- [x] 4.1 Implement plan-bound routing reservations and per-input-lane forwarding with HEAD/BODY/TAIL validation; verify local delivery, same-VC packet contention, turns/wraps, cut-through packets longer than buffers and rejection before resource changes for invalid envelopes.
- [x] 4.2 Add bounded router transfer pipelines with separate latency/initiation settings and fair output selection; verify downstream capacity is acquired without retaining a blocked physical grant, independent outputs overlap, and pipeline occupancy/throughput match configured limits.
- [x] 4.3 Add `simulator_detailed/torus_transport.py` plan/runtime construction for admitted one-way byte traffic with class-separated bounded local channels; verify two-fabric contention and small-grid wrap stress drain without modifying the existing one-VC runtime.
- [x] 4.4 Implement resource-complete termination and version-2 accounting for payload, padded packet bytes, actual channel launches, pending owners/credits/pipeline work and deterministic paths; verify timeout/idle-with-pending remain incomplete and delayed final credits postpone completion.
- [x] 4.5 Run router/replay stress, analytical multi-hop and relevant legacy tests with strict type/lint; reconcile actual wait/ownership resources to the dependency proof and commit this part before part 5.

## 5. Bounded causal request/response fixtures (TR-D03, D05, D08)

- [x] 5.1 Add finite responder descriptors, active service registers and exactly-one response generation after request consumption/service; verify byte counts, causal IDs, same-fabric response routes and role restrictions without invoking DMA or memory services.
- [x] 5.2 Add independent request/response injection ownership and sink drain over shared local physical links; verify full request/descriptor queues cannot retain response resources or shared physical grants, and all endpoint storage stays within its declared bound.
- [x] 5.3 Stress simultaneous request/response traffic with descriptor capacity one, slow sinks, small lane buffers, unequal quanta and both wraps/fabrics; verify exactly-once responses, causal timing, credit conservation and complete resource drain.
- [x] 5.4 Extend the dependency audit and result pending-state reports through request ejection, descriptors, response injection and terminal sinks; verify a response-to-request dependency is rejected and a deliberately non-draining fixture reports incomplete instead of passing.
- [x] 5.5 Run focused response/endpoint/transport regressions and strict type/lint; document the finite-service/fairness/drain assumptions and transport-only scope, then commit this part before part 6.

## 6. Directed slowdown and reconstructable timing traces (TR-D06..08)

- [ ] 6.1 Resolve failure IDs and fabric-qualified directed inter-router targets before environment construction; verify missing/disabled/local targets, invalid factors/intervals and same-target overlap fail without creating output artifacts.
- [ ] 6.2 Apply half-open schedules at physical launch with per-flit serialization/spacing/propagation snapshots and deterministic interval boundaries; verify unchanged routes, adjacent intervals, recovery, and per-lane order when a later flit has shorter propagation.
- [ ] 6.3 Export effective launch factors, failure start/end and stage/arrival-order-wait events with canonical identities and native/ACI units; verify an independent timeline reconstructs first-flit, packet/response and resource-drain completion without double-counting overlapping stages.
- [ ] 6.4 Test a slowed directed wrap link, matching dense IDs on different fabrics, all lanes sharing its physical cost, and independent-resource control traffic; verify untargeted parameters and legacy paired-link/router slowdown behavior remain unchanged.
- [ ] 6.5 Run directed-failure/timing and affected transport/legacy checks plus strict type/lint; review scheduler boundary determinism and byte/capacity accounting, then commit this part before part 7.

## 7. CLI integration, examples, support claims and handoff (TR-D01..10)

- [ ] 7.1 Dispatch exact replay versions in `simulator_detailed/replay_topology.py` and add small generic/Wormhole examples under `configs/topologies/` and `configs/replays/`; verify `.venv/bin/python -m simulator_detailed.replay_topology --replay <each-example>` produces parseable JSON and matching optional output, with correct success/invalid/incomplete exit codes.
- [ ] 7.2 Update `hardware_profile.py` support scopes and detailed documentation so inventory, opt-in transport admission, full workload execution and unsupported NIU/memory/compute features are distinguishable; verify plain profile inspection remains gated and public examples retain explicit mask/timing/availability assumptions.
- [ ] 7.3 Verify detailed detector/encoder boundaries accept real legacy objects and reject version-2 replay data before tensor/checkpoint work; confirm 7-D/4-D features and checkpoint assumptions stay unchanged, and report unavailable Torch/PyG checks without modifying models, RL shapes or top-level datasets.
- [ ] 7.4 Run `.venv/bin/python -m unittest discover -s simulator_detailed/tests`, strict Pyright with `simulator_detailed/pyrightconfig.phase2.json`, scoped Ruff, published CLI examples, `openspec validate wormhole-dual-noc-routing --strict --no-interactive` and `git diff --check`; verify child-2 cycle-44 hashes/results and the legacy custom-mesh timing/failure fixture remain stable.
- [ ] 7.5 Record requirement-to-test mappings, source/code/artifact identities, actual commands/results, resource proof assumptions and unsupported behavior in delivery documentation; reconcile the bounded TR-02..04/network VA-04 handoff without syncing pending umbrella specs, then review and commit the completed child before exploring `wormhole-memory-transactions`.
