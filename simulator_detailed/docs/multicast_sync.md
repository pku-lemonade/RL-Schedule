# Finite multicast and local synchronization

The opt-in `multicast_sync_workload` v1 executes through one retained environment,
physical transport registry and canonical memory registry when `runtime` is
specified. `MulticastMemoryRuntime` composes ordinary traffic, bounded rectangle
trees, scalar service and optional finite compute. `multicast_sync_result` v1
exports actual lifecycle, resource, service, scalar and generation observations.

```sh
.venv/bin/python -m simulator_detailed.replay_multicast_sync \
  --workload simulator_detailed/configs/multicast_workloads/mixed_two_rounds.json
.venv/bin/python -m simulator_detailed.replay_multicast_sync \
  --workload simulator_detailed/configs/multicast_workloads/wormhole_b0_mixed_assumed.json
.venv/bin/python -m simulator_detailed.validate_wormhole \
  --suite simulator_detailed/configs/validation/multicast_pipeline.json
```

Additional examples are `mixed_analytical.json` (segmentation, acknowledged
multicast and returning scalar), `mixed_opposite_fabric.json` and
`mixed_slow_clock.json`. Suites `multicast_analytical.json` and
`multicast_wormhole.json` cover the other public examples. All rates, clocks,
capacities, queue depths, service granules and control costs remain explicit
configuration. The Wormhole example binds the actual assumed B0 profile, with
its packet geometry and aligned 32-bit scalar contract. It is not a measurement.

A tree covers an inclusive nonwrapping rectangle in selected-fabric coordinates.
X-major entry lies on the start row before the corner; Y-major entry lies on the
start column before the corner. Source inclusion is explicit. Only eligible
workers receive payload; other routers forward or consume a terminal flit with
finite service. Missing or disabled workers cannot silently disappear.
`target_offset_bytes` is a common physical L1 address. A recipient's buffer base
plus its relative binding offset must equal that address. Distinct fabrics alias
one physical L1; accesses and versions are qualified by that owner.

`atomic_tree_reservation_v1` is a finite model policy: a FIFO controller grants
all tree lanes or none, charges configured setup/edge cost and releases only
after terminal effects and returned credits drain. The controller does not claim
silicon reservation messages or a hardware multicast VC assignment. Per-class
endpoint descriptors/staging and branch replication have explicit finite bounds;
request, response and multicast lanes share physical serializers and routers.
Forwarders reserve downstream storage before acquiring a router service grant.
Responders and complete memory-access bundles are provisioned before tree entry.
These rules, acyclic trees, unicast dateline ordering and finite fair service are
the model's resource dependency argument.

Each multicast segment reads its useful source bytes once and services every
recipient's useful bytes; headers and padding incur no payload memory service.
Posted source completion is injection handoff. Source-read release, per-target
publication, diagnostic all-effects and full credit drain are separate facts.
Acknowledged writes return actual bounded unicast packets after each target
segment is serviced. A remote diagnostic is never a legal source completion gate.

`monotonic_l1_counter_v1` reserves a dedicated aligned atomic granule and admits
only finite increment-by-one operations with a proof against overflow. Generic
word widths are 1/2/4/8 bytes; Wormhole requires 4. One shared aggregate L1 FIFO
executes an indivisible RMW at the configured final native cost, without extra
base read/write jobs. Posted updates complete locally at request handoff;
returning updates preserve each linearized previous value through a real
one-flit reply and serviced disjoint local inbox. There is no immediate-source
payload read. Reply arrival order need not equal linearization order.

`local_threshold_wait_v1` charges an initial observation and change-triggered
rechecks on that same server. Subscription precedes observation service so
updates during registration/service are not lost. Release requires both the
threshold and declared full local data versions. Blocked waits retain bounded
control metadata, not memory leases, compute contexts or transport grants.
Admission rejects remote peeks, impossible thresholds, dependency/reuse cycles
and later-round increments that could satisfy an earlier barrier.

Optional `compute` binds each local A/B operand extent and producer version to a
specific FIFO slot generation. It uses the existing FC/matmul effective cost
policy and bounded reader/compute/writer scheduler. Multicast-populated operands
emit no duplicate reader packets, but still pay local operand reads and C writes.
Mixed outputs may publish locally or complete an acknowledged unicast write
before signaling. Standalone compute's posted-output contract is unchanged.
The two-round examples use two workers and a distributor-owned collector counter;
thresholds 2 and 4 control the next distribution and final collection.

`advance(max_aci_cycles=...)` retains live processes and ownership; `run()` resumes
and finalizes only after all components drain. Repeated finalization is
idempotent. Partial results retain pending operations, service/transport owners,
waits and compute generations. No reservation cleanup occurs at an interrupted
horizon. The CLI emits JSON to stdout, diagnostics to stderr, and exits 0 complete,
1 incomplete or 2 invalid/error. `--output` publishes atomically in an existing
directory and protects workload/source assets, including hardlinks.

The harness adapter `multicast_sync_v1` enforces case wall-time and simulation
budgets and supports retained interruptions. Independent checks reconstruct
rectangle recipients/edges, packet/control paths, shared serializer charges,
addressed memory costs, scalar transitions/observations, local publication and
compute costs from input and events. Corruption tests include plausible summary
totals with missing traffic, early returns/releases, stale generations and hidden
resource leaks. Normalization retains complete event details and integer values.
Calibration target allowlists and old validation/result documents are unchanged.
Functional-reference and silicon-timing evidence remain unvalidated without
compatible external captures.

General atomics (reset/decrement/CAS), multicast atomics, tensor reductions,
wrapping/arbitrary trees, dynamic kernels, numerical tensors and exact NIU/ISA
execution are unsupported. Legacy `MULTICAST`/`sync_mode`, 7-D/4-D features, root
four-coordinate actions and checkpoints are not enabled or reinterpreted.
Legacy experimental documents without `runtime`, including
`distribute_compute_collect.json`, retain their explicitly labeled serial
projection behavior; their incomplete capability checks are not shared-runtime
evidence. `wormhole_b0_multicast_assumed.json` is a historical invalid fixture;
use the `wormhole_b0_mixed_assumed.json` example above.
