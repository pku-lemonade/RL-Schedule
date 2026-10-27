# Caller-owned causal timing partitions

For the separately versioned fixed-scenario rational layer-analysis extension,
see `CAUSAL_FULL_ANALYSIS.md`. The `causal_timing` contract below is unchanged.

`causal_timing` is an architecture-neutral, CPU-only mode of the public
`simulator_detailed.moe_model.design_space.DesignSpace` API. It accepts an
already canonicalized action/dependency join and a once-only local wall-time
partition. It does not discover evidence, reconstruct identities, execute a
workload, infer topology, or instantiate a causal schedule.

## Entry points

The existing API and CLI handle this mode without a separate runner:

```sh
python simulator_detailed/moe_model/design_space.py schema --kind model --mode causal_timing
python simulator_detailed/moe_model/design_space.py schema --kind request --mode causal_timing
python simulator_detailed/moe_model/design_space.py catalog --model model.json
python simulator_detailed/moe_model/design_space.py template --model model.json
python simulator_detailed/moe_model/design_space.py evaluate --model model.json --request request.json
```

The standalone entry point needs only the Python standard library. Installed
package users can use `python -m simulator_detailed.moe_model.design_space`, or
the exploration CLI's `design-space` subcommand. Neither evaluation entry point
calls an execution runner. Generate schemas rather than guessing field names;
every object is closed, and every field is required, including explicit nulls.
The template is deliberately incomplete until the caller supplies the grid.

Supply the study ID, budget, axes, domains, grids and exposure intervals in the
caller request before evaluating it.

```python
from simulator_detailed.moe_model.design_space import DesignSpace

model = DesignSpace(caller_specification)
catalog = model.catalog()
request = model.template()
result = model.evaluate(caller_request)
```

Inputs and returned containers are defensive snapshots. The identity binds the
whole model, including join status, clocks, family order, partition and scenarios.
The common design-space workload/source/overlap/route identity hooks remain
caller-owned opaque labels; they neither provide nor authenticate a topology.
No architecture defaults, time conversion, peak or family domains are supplied.

All design-space modes use fresh local decimal arithmetic contexts with precision
28, half-even rounding, exponent bounds -999999 and 999999, capitals enabled and
clamping disabled. Only invalid operation, division by zero and overflow trap;
flags start clear. Neither the active context nor mutable `decimal.DefaultContext`
is inherited or modified, including on rejection. Schemas retain their existing
decimal-string and strict integer admission rules.

## Canonical join contract

- `clocks` declare distinct IDs, nonnegative integer rank labels and positive
  half-open local tick windows. All clocks use the caller's declared time unit.
  Rank is metadata: even clocks with the same rank remain separate. Peak-domain
  participants must name exactly these clock IDs.
- `join.actions` contain canonical IDs, clock IDs, nonnegative integer intervals
  and `source_cost_ticks: null`. Intervals may cross or lie outside the window;
  active membership uses their intersection with that window. Zero-length
  actions are markers, not duration. Duplicate observations must be canonicalized
  upstream; this consumer cannot authenticate that two opaque IDs are aliases.
- `join.bindings` cover actions bijectively. An `exact` binding requires an opaque
  caller identity and null `unknown`; that dynamic identity cannot be reused on
  the same clock. An `unresolved` binding requires null identity and an explicit
  missing-input label. `candidates` are retained, even a singleton, never selected
  or promoted. Identity hooks are assertions by the caller, not external proof.
- `join.dependencies` distinguish `happens_before`, `logical` and `correlation`.
  Exact edges require exact endpoint bindings and a unique caller edge identity;
  completion must precede acquisition on the same clock, including every exact
  dependency path that leaves that clock and returns through other clocks.
  Only reachable ancestors constrain an action; unrelated actions need not have
  a total timestamp order. Cross-clock edges can express caller-declared order,
  but never compare or subtract timestamps from different clocks.
  Exact and logical graphs are independently cycle-checked. Logical relations
  and correlations stay unresolved and never become scheduling edges or costs.

`source_cost_ticks`, `exclusive_causal_owner` and `causal_latency_ticks` stay null
in this version. Resolving an action identity does **not** establish an exclusive
causal service cost. The consumer rejects non-null values for these fields and
rejects an exact dependency whose endpoint identity is unresolved.

## Once-only accounting and response

`partition.atoms` must tile every clock window without gaps or overlaps, with
positive integer durations equal to interval length and unique accounting
owners. Membership must list **all and only** active canonical actions from that
clock, and an atom may not cross a membership-change endpoint. Missing actions,
duplicates and cross-clock substitutions reject, even if counts match.

Ownership is `residual` for no active action, `exclusive_observation` for one,
and `shared_observation` for several. These are accounting categories, not
exclusive causal ownership. Observed action durations are never added to atom
durations. Residual atoms must have zero masks and cannot receive rate credit.

The `families` array order defines bit positions. Both
`family_mask_conservative` and `family_mask_inclusive` must be nonnegative Python
integers / JSON integer tokens, not booleans, floats or numeric strings. Unknown
bits reject, and conservative membership must be a subset of inclusive
membership. Masks express caller hypotheses, not inferred physical identities.
The schema's integer type follows JSON Schema semantics; API admission additionally
rejects integral floating-point representations. Collection/grid limits are CPU
safety bounds inherited from the evaluator, not hardware sizes or family defaults.

Every scenario explicitly selects a membership mask and overlap law. For a family
selected by the request, its factor is `1 - exposure + exposure / scale`;
unselected families have factor one. For each atom:

- Zero mask: factor one, including fixed/unattributed observations.
- One bit: the exclusive single-family factor.
- Multiple bits with `shared_serial`: arithmetic mean of selected family factors.
- Multiple bits with `co_bottleneck`: maximum of selected family factors.

The shared mean is an explicit equal-share hypothesis, not a fitted allocation.
Unselected families still participate with factor one; they are not dropped
from a shared mask. Each atom contributes `duration_ticks * factor` **once**.
Sparse mask histograms preserve that sum without a dense mask table or an
action-by-atom Cartesian join. Exact dependencies are validated and reported,
not added as predecessor costs to these histograms.

## Interpretation and compatibility

The result reports separate local durations and their maximum-duration envelope.
This envelope is **not a global synchronized completion time or causal schedule**.
There is no inferred launch offset, inter-clock synchronization or rank sum.
Join unknowns remain visible in `causal_join`, the specification snapshot and
the explicit null causal-latency outputs. Caller MFU is conditional on this
envelope and the supplied peak applicability contract; unavailable peak inputs
remain unavailable. Physical capacity and physical bottlenecks remain unavailable.

The evaluator reuses explicit request grids, frozen identities, shared exposure
boxes, fixed categorical scenarios, pairwise comparisons and conservative Pareto
results. No per-point refitting occurs. `hypothetical_joint` and
`evidence_portfolio` keep their existing schemas and behavior; their fields
cannot be mixed into `causal_timing`. There is deliberately no conversion of
observed atoms into fabricated resource nodes or a measured-stage DAG.

Invented examples live in `tests/causal_timing_fixtures.py`; no captured inputs
are necessary. Focused CPU validation:

```sh
python -m unittest discover -s tests -p 'test_causal_timing.py' -v
python -m unittest discover -s tests -p 'test_design_space.py' -v
python -m unittest discover -s tests -p 'test_moe_design_space_cli.py' -v
```

Schema-conformance tests use `jsonschema`; package CLI integration tests also
need the repository's normal pure-Python simulator dependencies.
