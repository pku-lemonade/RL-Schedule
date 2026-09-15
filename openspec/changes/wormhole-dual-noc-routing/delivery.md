# Incremental delivery

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
incomplete. Transfer and sink events use canonical plan/lane identities. Causal
responses, failure snapshots, CLI dispatch, NIU transactions, memory/compute
service and hardware timing calibration remain pending or unsupported.

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
