# Caller-owned sensitivity and design-space exploration

This pure CPU feature accepts explicit versioned JSON contracts. It does not
load evidence, discover topology, execute workloads, fit costs, or select default
architecture domains. Its observations, identities and authorities come only
from the caller. Treat those identity hooks as bindings, not authentication.

## CLI and API

Standalone, without importing execution components:

```sh
python simulator_detailed/moe_model/design_space.py schema --kind model --mode hypothetical_joint
python simulator_detailed/moe_model/design_space.py schema --kind request --mode hypothetical_joint
python simulator_detailed/moe_model/design_space.py catalog --model model.json
python simulator_detailed/moe_model/design_space.py template --model model.json
python simulator_detailed/moe_model/design_space.py evaluate --model model.json --request request.json > result.json
```

In an installed simulator package, use
`python -m simulator_detailed.moe_model.design_space` with the same arguments,
or the existing exploration CLI's `design-space` subcommand. The Python API is
`DesignSpace(specification).evaluate(request)`. `catalog()`, `specification()`,
`schema()`, `template()` and evaluation results return independent deep snapshots.
Mutating inputs or returned nested containers cannot change later admission,
baseline MFU, outputs, or the frozen identity.

Both model and request **require `mode`**. Generate the schema for the selected
mode; modes have separate exact field sets, not generic alternative recovery.
Unknown fields, duplicate JSON keys, floats, non-finite values, absent parameters,
and inconsistent identity/domain/scenario choices reject with a code and path.
Decimal quantities use strings matching `-?(0|[1-9][0-9]*)(\.[0-9]+)?`: spaces,
underscores and exponents are not wire or API decimal syntax. Counts are integers.
`template` emits incomplete placeholders, never admitted implicit defaults.

The request supplies every dimensionless axis domain, sorted finite grid
(including domain endpoints and scale one), and exposure interval in `[0,1]`.
All axes have distinct exposure identities and registered family IDs. The full
Cartesian product must fit the explicit `grid_budget`. Limits on grid size,
axes and schema collection sizes are CPU safety limits, not hardware domains.
One frozen cost/exposure box and scenario set are shared across all points.

## Modes

**`causal_timing`** consumes a caller-owned canonical action/dependency join and
once-only local timing partition. It validates exact versus unresolved bindings
without promoting unknowns, keeps clocks separate, and applies explicit shared
or co-bottleneck response laws without inventing a schedule or adding observed
service twice. See [CAUSAL_TIMING.md](CAUSAL_TIMING.md) for its closed schema,
accounting rules, interpretation limits and the existing API/CLI entry points.

**`hypothetical_joint`** requires a complete caller DAG, exclusive per-node
service and residual intervals, one common reference, source clock boundaries,
observed node/event intervals, and an exactly-once event partition. All events
are either service evidence for one matching-clock node/resource, or context
with a null node. Neither event nor observed node durations are added as costs.
Each registered inverse-service family owns disjoint aggregate service nodes:

```
cost[node] = service[node] * (1 - exposure + exposure / scale) + residual[node]
finish[node] = max(finish[parents], 0) + cost[node]
latency = max(finish[terminals])
```

Every source clock supplies all original DAG sinks and a full interval. Each
scenario must reproduce those durations at the same caller reference. This is
a consistency check, **not measured stage-cost evidence**. Cross-clock edges,
cycles, partial event/resource/route coverage, duplicate cost credits and
per-configuration fitting are rejected. Residuals and unselected service costs
never receive rate credits. Observations, dependencies, workload, routes,
overlap and peak contracts remain frozen identity inputs.

Every resource explicitly supplies caller bank/port/route aliases. Each route
chooses either `no_added_order` with an empty order, or same-clock `serialize`
with an exact permutation of bound nodes. These categorical aliases assert no
physical capacity or service law. Unknown physical truth remains unavailable.

**`evidence_portfolio`** registers evidence-bound response caps and source-owned
full, fixed-terminal and responsive-terminal intervals for each local clock.
It retains the accepted common completion-offset law:

```
delta = exposure * cap * (1 / scale - 1)
full_end = max(fixed_end, responsive_end + delta + readiness_offset)
latency = max(full_end - source_start across local clocks)
```

Starts and observed overlap stay fixed; no fixed/responsive serial addition is
performed. `shared` readiness requires explicit zero radius and path count.
`bounded` readiness supplies a nonnegative radius and affected-path count;
its symmetric envelope is a conservative outer bound, not a feasible witness.
The baseline at scale one is unchanged. Residuals remain explicitly unresolved
rather than zero, and physical sharing is not inferred. Multiple axes enumerate
**separate conditional views**; caps are never summed into joint latency or
joint Pareto claims. Minimal missing joint bindings are returned explicitly.

## Interpretation

- Arithmetic delegates to the existing max-plus and MFU helpers under their
  28-digit decimal precision and operation order, with no per-configuration fit.
- Joint scenario intervals are monotone independent-box endpoint hulls. Their
  union is enclosed across explicitly supplied categorical scenarios.
- Guaranteed strict dominance requires interval separation in **every matching
  scenario**. Shared inverse-rate monotonicity also certifies nonregression.
- Joint opportunity requires a same-scenario witness at one of two fixed global
  service/residual/exposure corners, shared across the entire grid. Absence of
  these witnesses does not prove impossibility.
- Pareto membership is a conservative latency-only outer set of points not
  guaranteed dominated. It is not an exact physical or investment Pareto set.
- Bottleneck outputs are critical-path classes, not utilization. Adjacent-grid
  changes bracket certificate transitions, not measured hardware thresholds.
- Break-even outputs retain symbolic max-plus recurrences, scenario edges,
  terminal equalities and critical-region inequalities. Portfolio rows include
  terminal/readiness equalities and sufficient radius frontiers. They do not
  invent roots, capacity thresholds, or missing residual values.
- Useful work and applicable peak domain are fixed. Symbolic peak produces MFU
  coefficients, missing peak produces an explicit unavailable result, and
  numeric peaks preserve their caller `accepted` or `hypothesis` qualification.
  Service scaling never co-scales peak or clips MFU to one.
- Source-boundary baselines carry caller evidence identity; hypothetical cost
  boxes and cap/exposure/readiness responses are labeled separately. The public
  engine cannot certify the caller's evidence authenticity or physical claims.

Schema identifiers are `caller-sensitivity-model-v1`,
`caller-design-space-request-v1`, and `caller-design-space-result-v1`.
Results include full input/request fingerprints and explicit missing contracts
for physical capacity, physical bottlenecks, absent numeric peak and unsupported
joint outputs. An unregistered requested family rejects with its missing law
and ownership contract rather than receiving a guessed implementation.

## Public Tests

All fixtures are invented unit-clock examples, independent of private data:

```sh
python -m unittest discover -s tests -p test_design_space.py
```

The overlay's companion CLI integration regressions run in the existing CPU
regression environment. The public feature and its tests contain no private
adapters or input discovery paths. Keep caller-private files and outputs out of
public patch exports and version control.
