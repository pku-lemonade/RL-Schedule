# Incremental delivery

All seven parts are implemented. Part 7 below records the final 134-test validation,
CLI examples, requirement coverage and bounded handoff to memory transactions.
Earlier sections retain their checkpoint-specific status and evidence.

## Part 1: versioned transport contracts and admission

Tasks 1.1–1.5 are delivered in the commit containing this section, titled
`feat: add versioned torus transport contracts`, based on `c1655ba`.
See [implementation scope and evidence](../../../simulator_detailed/docs/torus_transport.md).
The remaining 30 tasks are not implemented; this is not completion of TR-D01..10.

| Requirement subset | Implemented evidence |
| --- | --- |
| TR-D01 / D09 | Strict version-2 configuration, explicit binding assumptions and role allowlist; source validation; version-1 CLI rejection of version 2; graph/endpoint binding remains pending |
| TR-D05 / D07 | Finite response-fixture and slowdown input constraints only; no response or failure execution |
| TR-D06 | Explicit units/capacities, profile reference/override evidence, exact serialization admission and finite clock conversion; no new timing execution |
| TR-D08 | Immutable envelope/plan/result records, structural identity/count/time/credit-drain checks; no runtime event production or exhaustive state verification |

Validation on 2026-09-15:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_torus_contract
# 11 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 95 tests pass
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/python -m ruff check simulator_detailed/configs/schemas/torus_replay.py simulator_detailed/torus_contract.py simulator_detailed/torus_records.py simulator_detailed/tests/test_torus_contract.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
git diff --cached --check
# Clean before commit
```

Strict type coverage includes all three new production modules. Source/timing
assumptions and unsupported functionality are disclosed in the linked report.
No new hardware measurement, external simulator comparison or ML execution is
claimed. Next: part 2, canonical torus binding and independent routing/dependency
validation. No pending parent specifications have been synced or archived.

## Part 2: topology binding, route oracle and resource order

Tasks 2.1–2.5 are delivered in the commit containing this section. The binder and
static dependency compiler are in `simulator_detailed/torus.py` and
`simulator_detailed/torus_dependencies.py`; focused evidence is in
`simulator_detailed/tests/test_torus.py`.

The profile binding preserves 120 tiles, 240 fabric routers, 480 directed links,
240 attachments, the worker mask and inventory aliases. It does not create compute,
memory or NIU services. A harvested worker retains its router but cannot initiate an
admitted endpoint. NoC0 and NoC1 route through raw XY/YX coordinates respectively;
the independent profile oracle checks 28,800 ordered pairs, including the 4/18-hop
router example. The small canonical fixture checks shifted datelines and unavailable
edge rejection. Static request/response lane, local-channel and packet-owner
dependencies are topologically sorted with explicit rank checks. Existing version-1
explicit-route cycle rejection remains covered by the legacy transport tests.

Validation for this part completed before the checkpoint commit:

```bash
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 101 tests pass
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/torus.py simulator_detailed/torus_dependencies.py simulator_detailed/tests/test_torus.py simulator_detailed/configs/schemas/torus_replay.py simulator_detailed/torus_contract.py simulator_detailed/torus_records.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
# Clean
```

Runtime VC allocation, cut-through forwarding, response generation and CLI execution
remain pending parts 3–7.

## Part 3: finite lanes, credits and one physical serializer

Tasks 3.1–3.5 are delivered in the commit containing this section, titled
`feat: add bounded virtual-channel link kernel`, based on `9b6224f`.
Production code is `simulator_detailed/virtual_channel.py`; the fourteen focused
checks are in `simulator_detailed/tests/test_virtual_channel.py`. The version-2
trace action vocabulary and strict Pyright module coverage are extended.

| Requirement subset | Executable evidence |
| --- | --- |
| TR-D03 / D09 | Plan-bound packet/hop/class/phase/format admission before mutation; separate lanes; HEAD-to-TAIL ownership; foreign-instance token and double-return rejection |
| TR-D04 | Bounded reserved/ready/wire/received/held storage, delayed credit conservation after every SimPy event, FIFO long packets, idle/credit-starved lane bypass, four-lane bounded-quantum fairness, including one shared staging slot and unequal packet quanta |
| TR-D06 | Integer serialization and resolved native/ACI clocks, independent width/clock timelines, launch spacing separate from propagation/credit service, capacity-one throughput reduction without enlargement |
| TR-D08 (kernel only) | Token/lane/stage/owner events, byte cost only on actual launches, occupancy/peak inspection, delayed-return and idle-with-owner incompleteness |

Resource audit: each reservation consumes the lane's effective budget B until the
consumer releases it and credit delay finishes. Ready/receive queues and held
forwarder tokens are included in B. Shared staging is the subset of tokens from
physical launch through propagation/arrival, with an independent bound S; it is
not additional receiver capacity. Staging admission occurs in the fair arbiter,
not in producer wakeup order. Every launched token already has destination storage.
The serializer waits only for finite serialization/launch spacing after launch;
propagation and credit return are separate bounded work. No physical grant waits
for a producer, owner, receiver or credit. Idle partial packet ownership remains
pending. Publishing all reserved flits and releasing held tokens are obligations
of the future router/endpoint callers, whose full dependency audit is not yet done.

The independent 32-byte-flit timelines use 500 MHz ACI: 96 usable bits/native cycle
at 1 GHz NoC yields launches 0/2/4, arrivals 3/5/7 and drain 9 ACI; 256 bits at
250 MHz yields launches 0/4/8, arrivals 8/12/16 and drain 21 ACI with their declared
spacing, propagation, sink and credit settings. Synthetic checks establish model
invariants and analytical agreement, not hardware calibration.

Validation on 2026-09-15:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_virtual_channel
# 14 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 115 tests pass, including legacy link/mesh timing, explicit replay and profile gates
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/virtual_channel.py simulator_detailed/torus_records.py simulator_detailed/tests/test_virtual_channel.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
# Existing OpenSpec configuration warning about design rules remains unrelated.
git diff --check
git diff --cached --check
# Clean before commit
```

No new source facts or device-specific defaults were introduced. The kernel reuses
the existing integer flit-count helper; legacy `noc.py` and timing/flit definitions
are unchanged. Response-class fixtures are manually driven link tests, not causal
response generation. Configured slowdowns explicitly fail kernel admission pending
part 6. Full torus replay, router pipelines, endpoint service, result/CLI integration
and silicon accuracy remain unsupported/pending parts 4–7; no parent specs were
synced, no archive was created and no push was performed.

## Part 4: cut-through torus router and one-way replay

Tasks 4.1–4.5 are delivered in the commit containing this section, titled
`feat: add cut-through torus one-way transport`, based on `76d4d66`.
Production runtime is `simulator_detailed/torus_transport.py`; its focused tests
are in `simulator_detailed/tests/test_torus_transport.py`.

| Requirement subset | Executable evidence |
| --- | --- |
| TR-D02 | Runtime consumes the immutable compiled route hop sequence, including local/same-router and modular wrap paths; it never recomputes or detours routes |
| TR-D03 / TR-D04 | Per-hop plan-bound envelopes, input-lane take, downstream reserve-before-stage, HEAD/BODY/TAIL ordering, finite router transfer stage, shared physical serializers and credit release after downstream acceptance |
| TR-D06 | Router transfer latency/initiation and endpoint sink service use explicit native/ACI conversion; link serialization/propagation/credit boundaries remain separate |
| TR-D08 | In-memory version-2 result records packet payload/flit/physical totals, transfer/sink trace events, lane and pipeline occupancy, delayed credits, owners and completion/incomplete reasons |

The runtime's resource order is: input lane receive/held token → downstream lane
reservation → router transfer stage → downstream lane readiness → input credit
release. The downstream physical serializer is acquired only after this sequence
reaches a ready token, so a blocked downstream capacity or pipeline stage cannot
retain an unrelated physical grant. A blocked input token is counted by the
upstream lane budget. Router-stage requests are finite because each waiting
request already owns a downstream lane token; the stage's output identity queue
is bounded by the admitted channel inventory. Independent outputs can overlap
when transfer capacity permits; each physical link still has exactly one
serializer.

One-way traffic and bounded causal request/response traffic are now executable in
memory through `TorusTransport.run`, while slowdown schedules still fail explicitly
with a scope error. The runtime uses the existing plan/profile quantities and endpoint service declarations;
no device-specific clock, width or buffer constant was introduced. The output
record is not yet a CLI artifact and no top-level `simulate()` or full profile/DFG
path is enabled.

Validation on 2026-09-15:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_torus_transport
# 8 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 123 tests pass, including the legacy topology/link/runtime suite
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/torus_transport.py simulator_detailed/virtual_channel.py simulator_detailed/tests/test_torus_transport.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
.venv/bin/python -m py_compile simulator_detailed/torus_transport.py
git diff --check
# Clean
```

The profile replay test uses the assumed Wormhole inventory and checks both fabric
IDs; it is a bounded transport invariant, not a silicon comparison. The delayed
credit fixture reaches the expected payload before returning all credits and stays
incomplete. Transfer and sink events use canonical plan/lane identities. Directed
slowdown execution is now covered below; CLI dispatch, NIU transactions,
memory/compute service and hardware timing calibration remain pending or unsupported.

## Part 5: bounded causal request/response fixtures

Tasks 5.1–5.5 are delivered in the commit containing this section, titled
`feat: add bounded causal request response transport`, based on the next checkpoint
commit. Production changes are in `simulator_detailed/torus_transport.py`; focused
coverage is in `simulator_detailed/tests/test_torus_transport.py`.

| Requirement subset | Executable evidence |
| --- | --- |
| TR-D03 / D05 | Finite responder descriptor resources, active owners, response service delay and exactly one response packet per admitted request; response packet IDs retain the request transfer ID with a separate traffic class |
| TR-D04 | Request and response injection slots are independent endpoint resources; response and request lanes share physical links through the existing bounded serializer without sharing packet ownership |
| TR-D08 | Version-2 result records include both packet classes, reverse-route launches, `response_ready` events, descriptor occupancy/peaks and incomplete descriptor/service pending state |

The causal resource order is: request ejection and sink service → responder
descriptor → response service → response injection queue/local lane → reverse route
forwarding/ejection. Request sink credits are released before descriptor waiting, so
a full descriptor queue does not retain a request physical grant. A response
descriptor is released only after all response flits have been admitted to the
responder's local injection channel. Descriptor capacity is finite and its owners
are reported as request packet identities. The static dependency audit already
rejects response-to-request edges and the runtime result remains incomplete when a
descriptor or response service is pending.

Validation on 2026-09-15:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_torus_transport
# 12 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 127 tests pass, including the legacy topology/link/runtime suite
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/torus_transport.py simulator_detailed/tests/test_torus_transport.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
# Clean
```

The response tests use configurable synthetic service, flit and queue values;
they do not claim Wormhole silicon service latency. Both request and response
transport remain in-memory and transport-only. Directed slowdown execution,
version-2 CLI dispatch, NIU transactions, memory/compute service and hardware
timing calibration remain pending or unsupported; no parent specs were synced,
no archive was created and no push was performed.

## Part 6: directed slowdown and reconstructable timing traces

Tasks 6.1–6.5 are delivered in the commit containing this section, titled
`feat: add directed slowdown timing traces`, based on the next checkpoint commit.
Production changes are in `simulator_detailed/virtual_channel.py` and
`simulator_detailed/torus_transport.py`; focused coverage is in the corresponding
virtual-channel and transport tests.

| Requirement subset | Executable evidence |
| --- | --- |
| TR-D06 | Slowdown targets are resolved against enabled canonical directed links before `SimPy` environment/resource construction; local, missing and disabled targets fail explicitly |
| TR-D07 | Launch-time half-open schedules snapshot factor per flit and scale serialization, launch spacing and propagation without changing route selection or unrelated fabrics |
| TR-D08 | Failure start/end, launch factor, stage durations and arrival-order-wait events use canonical channel identities and ACI timestamps; physical bytes remain charged once per launch |

The runtime stores only the slowdown intervals that match a physical network
channel. At each launch it evaluates `[start,end)`, records the active failure ID
and factor, and applies that factor to the current flit's serialization, next-launch
spacing and propagation. Failure boundaries are scheduled as channel events. A
per-lane arrival tail retains staging ownership when recovery would otherwise let a
later flit arrive first, and the explicit `arrival_order_wait` duration makes that
delay reconstructable. Adjacent intervals use the recovered factor exactly at the
end boundary. Local links and independent fabric-qualified links remain unchanged.

The targeted wrap-link and long-propagation fixtures verify recovery, half-open
boundaries, lane order, failure events, invalid target rejection and complete
resource drain. The existing legacy paired-link/router slowdown fixture remains
unchanged. All factors, clocks, widths, propagation values and capacities are
configurable synthetic settings; these tests establish model invariants and
analytical timing behavior, not Wormhole silicon calibration.

Validation on 2026-09-16:

```bash
.venv/bin/python -m unittest simulator_detailed.tests.test_virtual_channel simulator_detailed.tests.test_torus_transport
# 30 tests pass
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 130 tests pass, including the legacy topology/link/runtime suite
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/virtual_channel.py simulator_detailed/torus_transport.py simulator_detailed/tests/test_virtual_channel.py simulator_detailed/tests/test_torus_transport.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
# Clean
```

The in-memory runtime now supports directed slowdown timing for the admitted
transport scope. CLI dispatch, NIU transactions, memory/compute service,
multicast, hardware VC/buddy/priority behavior and silicon timing calibration
remain pending or unsupported; no parent specs were synced, no archive was
created and no push was performed.

## Part 7: CLI, support boundaries and final handoff

Tasks 7.1–7.5 complete this child. This part is based on `c08de14` and is delivered
in the commit containing this section, titled `feat: expose versioned torus replay CLI`.
The preceding implementation checkpoints are `0150500` (contracts), `9b6224f`
(binding/routes), `76d4d66` (lane kernel), `1e1f6ec` (one-way runtime), `29f33f7`
(causal responses), and `c08de14` (directed slowdown).

`replay_topology.run_replay` now dispatches exact input kind and integer version.
Version 1 retains its existing plan/runtime/result; version 2 reads its graph or
profile relative to the replay file and passes through contract, graph, route,
dependency and slowdown admission before constructing the environment. Output is
JSON on stdout with optional identical file output. Invalid input returns 1 and
leaves output files untouched; complete/incomplete runs return 0/2 respectively.
Result documents are not accepted as replay configurations.

Manifest `hardware-profile-2` reports the available opt-in compiler and executing
transport abstractions separately from profile inventory and full-workload
execution. The latter still has `can_execute: false`; the transport manifest does
not establish admission of a specific inspected profile. The compiled-plan export
also retains its pending runtime/slowdown checks. A runtime result reports what
actually executed. The profile graph has 240 routers and 480 network links; runtime
channels and router pipelines are instantiated only for admitted routes, with no
Core, DMA, NIU, memory service or DFG executor.

### Final requirement-to-test mapping

All test paths below are under `simulator_detailed/tests/`.

| Requirement | Executable evidence |
| --- | --- |
| TR-D01 | `test_torus_contract.py` strict source/binding/permissions/units; `test_torus.py` profile inventory, disabled-worker initiation rejection and transit preservation; `test_torus_cli.py` public profile example |
| TR-D02 | `test_torus.py` all 28,800 router pairs against the independent physical-coordinate oracle, pinned 4/18-hop paths, shifted datelines, disabled-edge rejection; transport local/two-fabric delivery |
| TR-D03 | `test_torus.py` wrong-phase/reset/class/cycle mutations and dependency ranks; `test_virtual_channel.py` packet ownership, FIFO, independent lanes and no foreign token acceptance |
| TR-D04 | `test_virtual_channel.py` event-by-event conservation, blocked-lane bypass, shared serializer capacity, unequal quantum fairness, bounded staging and small-capacity throughput; `test_torus_transport.py` long-packet cut-through, contention and concurrent router outputs |
| TR-D05 | `test_torus_transport.py` exactly-once causal responses, service timing, capacity-one descriptors, both fabrics and pending response-owner reporting |
| TR-D06 | `test_torus_contract.py` clocks/units/serialization admission and retained profile override evidence; `test_virtual_channel.py` independently calculated two-clock/width launch/arrival/drain timelines |
| TR-D07 | `test_torus_transport.py` directed wrap slowdown, half-open launch snapshots, recovery ordering and invalid targets; CLI invalid slowdown causes no environment or output artifact; retained legacy paired-link failure fixture |
| TR-D08 | Contract record validation plus transport launch-byte reconciliation, packet/class/fabric identities, delayed final credit and descriptor pending state, actual resource drain; CLI JSON round trips preserve the version-2 result schema |
| TR-D09 | `test_torus_cli.py` exact headers, relative sources, success/invalid/incomplete and untouched output files; `test_topology_replay.py` retained cycle-44 graph/plan hashes; `test_topology_baseline.py` unchanged custom mesh/failure observations; `test_topology_consumers.py` actual version-2 configuration/result/trace rejection and real legacy object acceptance; profile execution gates |
| TR-D10 | Pinned source/route fixtures, full suite, analytical kernel checks, published example commands and this report; external simulator and silicon comparisons remain unavailable |

### Validation on 2026-09-16

Python remains the existing `.venv` Python 3.12.12 environment. No dependency was
added. The focused CLI/consumer/replay/profile run passed **41 tests**. Final checks:

```bash
.venv/bin/python -m unittest discover -s simulator_detailed/tests
# 134 tests pass
.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
# 0 errors, 0 warnings
.venv/bin/ruff check simulator_detailed/configs/schemas/torus_replay.py simulator_detailed/torus_contract.py simulator_detailed/torus_records.py simulator_detailed/torus.py simulator_detailed/torus_dependencies.py simulator_detailed/virtual_channel.py simulator_detailed/torus_transport.py simulator_detailed/replay_topology.py simulator_detailed/hardware_profile.py simulator_detailed/tests/test_torus*.py simulator_detailed/tests/test_virtual_channel.py simulator_detailed/tests/test_topology_consumers.py simulator_detailed/tests/test_topology_replay.py simulator_detailed/tests/test_hardware_profile.py
# All checks passed
openspec validate wormhole-dual-noc-routing --strict --no-interactive
# Valid
git diff --check
# Clean
```

An initial Ruff run requested `TypeError` for non-object replay input; that was
corrected before final validation. No type/lint rule was disabled. Torch and
`torch_geometric` are absent (`importlib.util.find_spec` returned `None` for both).
Consumer guards run without those dependencies. Source inspection confirms guard
calls precede tensor construction/checkpoint loading and the existing 7-D predictor
and 4-D encoder features remain unchanged. Tensor execution and checkpoint loading
were **not** tested; no stand-ins, model changes or RL shape changes were introduced.

The following executable command batch was run with temporary outputs, validating
parseable JSON, matching stdout/file contents, zero stderr and zero return status:

```python
import json, subprocess, tempfile
from pathlib import Path

base = Path('simulator_detailed')
with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / 'result.json'
    for mode, name in (
        ('--inspect', 'configs/topologies/heterogeneous_example.json'),
        ('--inspect', 'configs/profiles/wormhole_b0_n150_assumed.json'),
        ('--inspect', 'configs/instances/mesh_example.json'),
        ('--replay', 'configs/replays/heterogeneous_unicast.json'),
        ('--replay', 'configs/replays/torus_small_v2.json'),
        ('--replay', 'configs/replays/wormhole_transport_v2.json'),
    ):
        run = subprocess.run([
            '.venv/bin/python', '-m', 'simulator_detailed.replay_topology',
            mode, str(base / name), '--output', str(output),
        ], capture_output=True, text=True, check=True)
        assert json.loads(run.stdout) == json.loads(output.read_text())
        assert not run.stderr
```

The separate `inspect_profile` command also passes and reports manifest version 2
with `can_execute: false`. Invalid and incomplete CLI cases are subprocess-tested
in `test_torus_cli.py`; a failed run also preserves a pre-existing output file.

| Replay | Payload / packet / channel bytes | Drain (ACI cycles) | Plan SHA-256 |
| --- | --- | --- | --- |
| Version-1 heterogeneous | 59 / 112 / 608 | 44 | `1a59e3e5b7ed7c6155fe618ca13b025a9908959093d174eb89d9f1e3c8e09fba` |
| Version-2 small torus | 241 / 352 / 1760 | 57 | `1a178e789e37287ba3c872ed4b346a7963674a0c1d5edbd967bb367a6091bf4c` |
| Version-2 Wormhole fixture | 148 / 320 / 4160 | 63.5 | `92d234b5806f7831c768fd199c8da56d4cb51b35d850c3f1bf260dbda1be7702` |

Version-1 graph identity remains
`5ef712709c1afbfda6f795e119a4e4f2ce0f0437d7431f2a7be6285ffb15a6cc`.
The full suite also matches the saved custom-mesh trace/timing/paired-failure
fixture exactly. New replay timings are synthetic model results, not silicon data.

### Source and artifact identity

Architecture evidence is inherited from the pinned route/profile fixtures and
earlier source retrievals, not freshly fetched or independently remeasured in this
part. ISA documentation revision is `acaf010519f4fdd323df5077e45b8695f70e4279`;
the profile descriptor revision is `558637320489ee8ccccea5f2b3a5fcd1e1cfd58f`.
Coordinates raw-byte SHA-256 is
`328f014798fe3cec46ec2c9555a8d843074b7eb62eb4de996e6d85a0f7ec9174`;
RoutingPaths raw-byte SHA-256 is
`656f6fb36b74de0ac30fcd0b76c5c028a6760eb8879c70dd647f1da57b9c006b`.
Immutable URLs and inherited/fetched provenance remain in the route fixture and
design. The public Wormhole replay embeds those citations and separately labels
healthy availability, endpoint permissions, stage splits and capacities as assumed.
Native clock/flit/interface settings reference the unchanged profile parameters.

Paths in this table are relative to `simulator_detailed/`; hashes are file bytes.

| Artifact | SHA-256 |
| --- | --- |
| `configs/profiles/wormhole_b0_n150_assumed.json` | `a959592f39b257091c509d95abde6842e10e08d84123fde6788ed55372429b9d` |
| `configs/topologies/torus_small_v2.json` | `6afb9057b7e852645be5fb26f5a0fa4edbf6ed1178c53aa9e5bd8480781f7b16` |
| `configs/replays/torus_small_v2.json` | `f6d4ffaa6d3b7fb2993e875d3b9f7349b6c83841ff8d9f1740a88f768feb0ce5` |
| `configs/replays/wormhole_transport_v2.json` | `8774d3c3949940e1ed5684ee332ca52689197ad1cf81e959481a6a8b1cd67994` |
| `tests/fixtures/wormhole_torus_routes.json` | `427b083973184f37c2748cfa14a03a2c8ac0458e876a9193847497b1b687cce5` |
| `tests/fixtures/wormhole_b0_profile_reference.json` | `04ef81eee6b197646903102ebc72becfe40946be06519c5425539f3bd41ea8fe` |
| `tests/fixtures/topology_baseline.json` | `c572fe5b906d1ef3c7f4e961dde840cffc75e3d5bad2d14a510f4ff6ebab2ad7` |
| `replay_topology.py` | `2169ec058b874a2804987c97f94219b98ab236f128ea4ebf542880760dd72f9a` |
| `hardware_profile.py` | `ca38f57b4c09a4d7860d222da7df3dae4ebb3c6f4455c8308e1f2efb355c8417` |

### Resource argument and bounded umbrella handoff

Request/response classes have disjoint lane/endpoint resources; each network class
has two modeled dateline phases. Compiled network ranks increase within an axis,
across the dateline and at dimension turns. Local injection precedes the network;
ejection follows it. Request ejection leads through finite descriptors/service to
response injection, and terminal response sinks never require request resources.
Packet owners are lane-local. Every retained flit owns a bounded lane token;
staging is a bounded subset, and delayed returns still consume the lane budget.

Downstream lane capacity is reserved before finite router/wire service. A blocked
forwarder holds charged input storage without retaining a shared physical grant.
Eligible link service is fair and quantum-bounded; admitted router/wire service
cannot wait for an unreserved destination. This progress argument assumes finite
traffic/packets, finite positive service where required, eventual clock progress,
enabled independently draining sinks, and no permanent resource failure. Finite
slowdowns preserve those assumptions. Cycle-limit/idle-with-pending outcomes stay
incomplete and are not evidence of deadlock freedom. Static dependency checks and
dynamic resource/fairness tests cover different obligations.

**TR-02** is delivered for both Wormhole raw-coordinate route families and configurable
generic torus policy, retaining legacy mesh behavior. **TR-03** is delivered for
this bounded unicast byte/request-response scope and its disclosed class/dateline
abstraction. **TR-04** is delivered for canonical directed network slowdowns and
stage/clock/byte trace identities. **Network VA-04** covers model latency,
serialization throughput, packet accounting, dual fabrics, wraps, finite capacity,
contention and drain. Memory-alias contention, compute pipelines, multicast and
synchronization parts of VA-04 remain with later children.

Next exploration is `wormhole-memory-transactions`: define real transaction/address,
ordering, packet/header cost, visibility/completion and shared L1/DRAM service;
audit those endpoint dependencies before reusing this transport. Current causal
responses are byte fixtures, not NIU reads, writes or acknowledgements. Hardware
VC/buddy/priority behavior, adaptive routing, multicast, atomics/semaphores,
compute/DFG workloads, tensor/checkpoint execution, external simulator comparisons
and measured silicon timing remain unsupported or unvalidated. No hardware accuracy
percentage follows from these passing tests. Pending umbrella specs/tasks are not
synced or declared complete; this child is not archived and no push is performed.
