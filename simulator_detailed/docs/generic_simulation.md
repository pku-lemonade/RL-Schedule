# Generic system graphs, four-kind transactions and adapters

The generic layer describes and simulates neutral systems that carry no real
device or vendor vocabulary. It is the supported way to model private
accelerators: private inputs are converted by external adapters into two
public documents, and no private type names, parameters, samples or test data
enter this repository. All reported times are model cycles from configured
rates, never hardware measurements.

## System graph documents

`kind: generic_system_graph`, `schema_version: 1`. Every field is explicit;
nothing is device-derived and unknown fields fail validation.

| Field | Meaning |
| --- | --- |
| `nodes` | Grid positions with roles `compute`, `memory`, `transit` |
| `networks` | Independent networks; membership derives from ports/links |
| `ports` | Per-node per-network ports, `network` kind for links, `local` for endpoints |
| `links` | Directed edges between network ports; at most one out- and one in-link per port half |
| `dma_endpoints` | Transfer-capable agents bound to local ports |
| `execution_units` | Compute resources on `compute` nodes, bound to local ports |
| `memory_resources` | Byte capacities with optional reachable service endpoints |
| `static_routes` | Explicit ordered link paths between endpoints, per network |

Identifiers must be synthetic. A case-insensitive denylist rejects real
device and vendor tokens in every identifier. Validation rejects duplicate
identities, dangling references, empty networks, occupied port halves and
routes that are non-contiguous or revisit a node.

Loading compiles the document into the canonical topology machinery and
returns one unified `GenericSystem` object exposing network membership,
endpoint inventories, memory resources, route tables and order-independent
digests:

```python
from simulator_detailed.generic_graph import load_generic_system

system = load_generic_system("configs/generic_graphs/synthetic_grid_2d.json")
inventory = system.export()
```

The existing `--inspect` entry point (`simulator_detailed.replay_topology`)
also dispatches generic graph documents.

## Transaction batches

`kind: generic_transaction_batch`, `schema_version: 1`. Exactly four
transaction kinds exist:

| Kind | Effect |
| --- | --- |
| `transfer` | Moves `payload_bytes` from `source` to `destination` along the declared static route |
| `compute` | Holds `unit_id` exclusively for `duration_cycles` |
| `wait` | Completes when `counter_id` first reaches `threshold` |
| `signal` | Adds `delta` to `counter_id` |

`timing` declares explicit per-network link rates (`bytes_per_cycle`,
`hop_cycles`, `credit_return_cycles`, `buffer_slots`) with optional per-link
overrides. `counters` declares wait/signal state. Every transaction may
declare `depends_on` predecessors and a `start_cycles` earliest start.
Unknown kinds, undeclared counters, dependency cycles and untimed transfer
networks are rejected before simulation. `max_cycles` bounds the run.

Transfers execute store-and-forward: per-link FIFO buffer credits, one
serializer per link, byte-proportional serialization, hop propagation and
delayed credit return. A DMA endpoint may transfer to any reachable endpoint,
including memory service endpoints; payloads exceeding a destination memory's
capacity end incomplete with `capacity_exceeded`. A transfer with no declared
route ends incomplete with `route_unreachable`. Waits complete in the cycle
their threshold is reached (signal-first); a wait that can never be satisfied
ends incomplete with `cycle_limit`, and its dependents with
`dependency_unsatisfied`.

## Running a batch

```bash
.venv/bin/python -m simulator_detailed.replay_generic \
  --batch simulator_detailed/configs/generic_transactions/acceptance_batch.json
```

Exit codes follow the compute replay convention: `0` complete, `1`
incomplete, `2` invalid input. The emitted `generic_simulation_result` v1
records makespan, per-transaction start/end with per-hop serialization
windows, per-resource busy/utilization aggregates, final counter states and
incomplete transactions with machine-readable reasons. Results revalidate
their own consistency: a corrupted or partial output fails validation and
cannot be relabeled as complete.

## External adapters

Implement `GenericInputAdapter` (in `simulator_detailed.generic_adapter`) in
your own code — private launch scripts import the public library directly;
there is no plugin discovery:

```python
from simulator_detailed.generic_adapter import run_generic_adapter

result = run_generic_adapter(my_private_adapter)  # documents revalidated at the boundary
```

`load_system_graph()` and `load_transactions()` return the public documents;
the boundary revalidates both through a dump/parse round trip, so
validation-bypassing constructors cannot leak through. The public repository
ships exactly one example, `SyntheticGridWorldAdapter`
(`configs/generic_adapters/synthetic_world_2d.json`), whose invented
vocabulary matches no private format. `scan_forbidden_tokens()` is available
for private CI to check that no private identifier leaks into shared files.

## Boundaries and limitations

- Credits and buffers are accounted per packet, not per flit; transfers are
  store-and-forward without cut-through.
- Endpoint injection/ejection occupies no local-link time in this phase.
- Incomplete transactions record no partial-hop progress.
- Structural reference errors (unknown endpoints, units, networks) fail
  before simulation; only viability failures appear inside results.
- Model cycles come entirely from configured rates; no hardware timing claim
  is made or implied.

## Unified compile and runtime pipeline

New code should go through one pipeline instead of driving the runtime
pieces directly:

```
SystemSpec --compile_system()--> ImmutablePlan --RuntimeContext--> SimulationResult
```

- `SystemSpec` (`kind: system_spec`, v1) composes one graph document with one
  transaction batch. `compile_system(spec)` in `simulator_detailed.system_compile`
  is pure: it validates every entity, resource, port, attachment, fabric and
  transaction reference, resolves routes and effective timings, classifies
  terminal transfers, computes counter reachability bounds and returns an
  immutable, content-addressed plan. Identical input yields identical plans
  and digests; compilation creates no simulation objects.
- `RuntimeContext` (`simulator_detailed.runtime_context`) holds the
  environment and time, one `ResourceRegistry`, one `EventBus`, transaction
  states, the deterministic trace and the error accounting, and executes all
  four transaction kinds in a single run. The registry builds each physical
  resource at most once per plan, acquires multiple resources in sorted ID
  order and releases everything on completion, failure or cancellation. The
  bus owns named events and counted events; waits whose threshold exceeds the
  declared reachable bound are reported explicitly in the result error list.
- The result additionally carries per-transaction `wait_cycles`,
  per-resource `queue_wait_cycles`, an `errors` list, a deterministic `trace`
  and the `plan_sha256`, all defaulting so phase-1 documents stay valid.

The phase-1 entry points (`run_generic_batch`, `GenericRuntime`, the CLI)
are compatibility shims over this pipeline; their behavior is unchanged.

## Addressed memory

A memory resource may declare an optional `hierarchy`; without one it keeps
flat streaming behavior. A hierarchy is fully explicit:

| Field | Meaning |
| --- | --- |
| `banks` | Number of independently serviceable banks |
| `stripe_bytes` | Address stripe used for bank/port mapping |
| `latency_cycles` | Fixed data-service latency per access |
| `ports` | Named command ports, each with `command_cycles` and a bound channel |
| `channels` | Named data channels, each with `bytes_per_cycle` |

A transfer with an optional `address` becomes an addressed access: memory
destination is a write, memory source is a read. The address must name
exactly one memory service endpoint, the memory must declare a hierarchy,
and `address + payload_bytes` must fit the capacity — violations fail at
compile time.

Mapping is deterministic: `stripe = address // stripe_bytes`, bank =
`stripe % banks`, port = `ports[stripe % len(ports)]`, channel = the port's
bound channel. A write services after the network traversal arrives; a read
services before departure. Command issue occupies the mapped port for
`command_cycles`; data service then holds the mapped bank and channel for
`latency_cycles + ceil(bytes / channel_bytes_per_cycle)`. Same bank
serializes, different banks overlap, one port serializes commands, and one
channel bounds aggregate data rate. Spans record the service window
(`span.service`) and memory resources report busy/queue-wait/utilization
under the `memory_bank`/`memory_port`/`memory_channel` usage kinds.
