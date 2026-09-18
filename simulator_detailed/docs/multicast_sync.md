# Multicast implementation status

The `wormhole-multicast-sync` child is in development. Its current executors
are serial analytical prototypes, not the shared transport/memory/compute
runtime required by the approved design. See the
[implementation audit](../../openspec/changes/wormhole-multicast-sync/implementation-audit.md)
for the reopened tasks and exact limitations.

Inspect the generic projection with:

```sh
.venv/bin/python -m simulator_detailed.replay_multicast_sync \
  --workload simulator_detailed/configs/multicast_workloads/distribute_compute_collect.json
```

The CLI writes JSON to stdout and diagnostics to stderr. `--output` atomically
replaces a file in an existing directory, while protecting workload/graph
assets, including hardlinks. Exit 0 means the projection completed, 1 means it
has pending work, and 2 means invalid input or an execution/publication error.
A projection that exceeds its configured horizon is rejected before publication;
a bounded mid-service retained snapshot is not implemented yet. Profile-backed
multicast inputs are also explicitly rejected. The file named
`wormhole_b0_multicast_assumed.json` is not a working Wormhole execution example.

`multicast_sync_v1` can run this prototype in the validation harness with
finite case budgets. Rectangle/recipient checks derive expected edges and
flits from the admitted input. Full packet, ownership, service, synchronization,
compute and drain checks remain unsupported where shared runtime evidence is
missing; requiring those checks yields an incomplete report. An observed
contradiction still fails. Normalization preserves event details and publication
addresses but exports no unified elapsed-time metric or fabricated cross-component
causality. Functional-reference and silicon-timing evidence remain unvalidated.

Clocks, widths, capacities, rates and thresholds remain configuration fields.
Their presence in the schema does not establish that every field is implemented
by the current prototype. Legacy predictor/encoder inputs, root actions and
existing topology/memory/compute replay contracts retain their prior scope.
