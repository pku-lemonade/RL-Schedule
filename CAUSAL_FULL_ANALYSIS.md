# Finite caller-owned causal full analysis, version 1

`causal_full_analysis` is an opt-in mode of `DesignSpace`, using wire version
`caller-causal-full-analysis-v1`. The existing three modes and their wire
versions are unchanged. This is a conditional mathematical envelope, not a
schedule, measurement, source resolver or hardware model. No evidence discovery,
user-defined executable response, subprocess, network or device operation is
part of this evaluator. Only the caller's JSON is consumed.

## API and CLI

Use `DesignSpace(model).evaluate(request)`, `specification()`, `catalog()`,
`schema()` and `template()` as usual. The template deliberately has missing
study/grid/budget values and cannot be evaluated until the caller fills them.

For expanded all-pairs output, use the public bounded-memory binary-sink API:

```python
model = DesignSpace(specification)
with open("result.json.partial", "wb") as sink:
    receipt = model.evaluate_to(request, sink, block_size=1)
```

For existing full-detail requests, `evaluate_to` accepts the same full-analysis request and writes exactly the
canonical JSON bytes that `encoded(model.evaluate(request))` returns, including
the same schema, array order, reduced rationals and output identity. Its return
value is only `{output_identity, bytes_written, sha256}`; `sha256` hashes the
complete emitted file, whereas `output_identity` retains the existing hash of
the result with that identity field omitted. It never constructs or reads back
a complete materialized comparison result. Other modes continue to use
`evaluate`; `evaluate_to` rejects them. The existing defensive materialized API
is intentionally retained for compatible small calls and is **not** a bounded-
memory API. Callers such as full-study runners must call `evaluate_to` directly,
not call `evaluate` and then serialize it.

The full-analysis CLI `evaluate` command uses `evaluate_to` by default, including
the delegated exploration entry point. Redirect stdout to a staged file and
require exit status zero before publishing it. The API likewise returns its
receipt only after all bytes have been written. It handles positive short writes;
zero/invalid writes, sink errors, validation failures, spool corruption and
cancellation propagate without a completion receipt or a materialized fallback.
On any exception, discard the staged destination. The caller owns its sink and
is responsible for closing, flushing, durable storage and atomic publication.

Comparison production has a declared positive integer block size (default one).
Each block shares one exact subtraction per scenario between the comparison
summary and all four dominance predicates. Internal block scheduling cannot
change pair or scenario order. Live extra comparison state is O(B + N), independent
of scenario count apart from integer/rational bit width: at most B pair summaries,
B edge records and B file descriptors, plus four O(N) dominated sets and an O(N)
permutation index pool. Each exact delta is reduced, validated and written to a
file-backed per-pair delta-array segment before advancing to the next scenario.
Only extrema, counts and dominance flags remain in memory; no per-pair or
per-block scenario list is retained. Blocks and their files are released before
the next block and on cancellation. Selected order, multiplicity at the producer
boundary, and count-based coverage classification are unchanged; public request
validation still rejects duplicate selected pairs and grids with fewer than two
entries. These existing schema constraints are not relaxed for probes.

Two temporary binary files hold the complete canonical comparison and edge
arrays as **output spools**, not Python record caches. Together with the per-pair
segments there are at most B+2 open temporary files. Once a pair's summary is
known, its constant-size envelope is validated and emitted in canonical field
order, copying the finished delta segment in bounded 64-KiB reads. Neither the
consumer nor producer rebuilds a delta list or whole-comparison byte string.
Each delta item and edge passes its unchanged item schema and reduced-rational
law; the comparison envelope and delta-array count/schema are checked separately.
The remaining result retains the original grid and other analyses; its unchanged
validator checks those fields and identities. The outer array schemas have no
maximum, minimum zero and no uniqueness condition, so validating every item
before substituting empty arrays in the structural view preserves their
acceptance laws. Delta-array segments retain their original nonempty constraint.
Spool bytes are integrity-checked during assembly, the output-identity pass, the
validator's identity pass and final emission, using bounded reads. No pass
reevaluates scenarios or regenerates pairs. All spools close on success and
failure. Set `TMPDIR` to an authorized output filesystem before starting the
process; sufficient disk space for the expanded arrays and live block's segments
is still required. Spool bytes grow with scenarios; live comparison records do not.

The remaining grid/OAT/joint/migration/surface storage is unchanged. Full wire
output still has O(N*N*S) bytes, and disk I/O, exact arithmetic and the original
logical work budgets remain real costs. This change removes retained all-pairs
Python state, not the output cardinality or any CPU/wall limit. A bounded-prefix
2,401-design/832-scenario synthetic probe is not a completed full study, nor
does it establish the cause of any prior SIGKILL. Full compatibility and signal
attribution require separately authorized completion evidence.

`causal_full_analysis.validate_result(result)` validates the closed output,
canonical rational encoding, grid/scenario/clock identities, local composition,
extrema and terminal ties, and complete input/output hashes. It is not an
authentication mechanism or an independent proof of the supplied observations.

The standalone entry point supports the commands below. Full detail remains
standard-library-only; summary computation requires the normal public dependencies
in `requirements-summary.txt` (`python -m pip install -r requirements-summary.txt`).

```sh
python simulator_detailed/moe_model/design_space.py schema --kind model --mode causal_full_analysis
python simulator_detailed/moe_model/design_space.py schema --kind request --mode causal_full_analysis
python simulator_detailed/moe_model/design_space.py schema --kind result --mode causal_full_analysis
python simulator_detailed/moe_model/design_space.py template --model model.json
python simulator_detailed/moe_model/design_space.py evaluate --model model.json --request request.json
```

Rejection is a structured decision with code, path and reason; CLI exit status
is 2. The existing exploration CLI delegates to this same entry point. Every
object is closed, all fields required, including explicit nulls. JSON duplicate
keys, nonfinite numbers, booleans in integer positions and unknown policies
reject. Inputs/results are defensive snapshots. Canonical hashing includes all
metadata, array ordering, capacity declarations, requests and result fields
(except the output hash itself). No private workload fixture is included.

## Explicit lossless analysis summary

Full detail remains the default, with request/result version
`caller-causal-full-analysis-v1`; its schemas, canonical bytes and three-field
completion receipt are unchanged. Summary is a separate closed request/result
version, **`caller-causal-full-analysis-summary-v1`**, retaining mode
`causal_full_analysis` and the unchanged v1 embedded model/input identity.
There is no optional request flag and no evaluation-time detail override.
Missing, unknown, wrong-type or conflicting versions/fields reject.

Use `model.schema(output_detail='summary')` and
`model.template(output_detail='summary')` to select this request version, or:

```sh
python simulator_detailed/moe_model/design_space.py schema --kind request --mode causal_full_analysis --output-detail summary
python simulator_detailed/moe_model/design_space.py schema --kind result --mode causal_full_analysis --output-detail summary
python simulator_detailed/moe_model/design_space.py template --model model.json --output-detail summary
```

The selector rejects unsupported legacy modes and model-schema requests. Default
schema/template calls still select full detail. Summary has exactly the same
required request keys and result top-level keys. Its only structural omission is
`comparisons[*].deltas`, which is **forbidden**, not nullable, optional or empty.
Every comparison retains `left`, `right`, `delta_ns`, `faster`, `equal`, `slower`.
All grid/scenario latencies/MFU, all Pareto edges/frontiers, **OAT deltas**, joint,
migrations/bottleneck transitions, surfaces/break-even/saturation and null/unknown
classifications remain. Scenario and directed-pair scope/work charges do not shrink.

For each retained pair `(left, right)` and each model scenario in its existing
order, recover the omitted exact delta by subtracting the corresponding identified
grid endpoint latencies, **left minus right**, using reduced rational arithmetic.
For endpoints `a/b` and `c/d`, reduce `(a*d-c*b)/(b*d)` by the gcd with a positive
denominator; zero is `0/1`. This reconstructs every diagnostic record, including
tied-valued scenarios with different IDs. No interpolation, extrema substitution,
deduplication, sampling or omitted-sequence digest is involved. The producer still
reduces every pair/scenario relation once for the same extrema/counts/dominance.
Summary does not construct a discarded delta list, per-pair spool or delta string.
Its two outer spools, bounded reads and O(B+N) extra comparison state remain.

The embedded request version is bound by `request_identity`; result version,
request, endpoints, scenario order and all retained data are bound by the existing
`output_identity` rule. Mode-specific closed validation requires matching result
and embedded-request versions and forbids cross-contract records. Existing exact
rational/grid/MFU/clock/terminal/null checks remain. B153 S1 is unchanged:
validation is not independent recomputation of derived comparisons or frontiers;
self-consistently rehashed derived errors can still pass. Producer tests, not a
silent validator expansion, prove equivalence and lossless reconstruction.

### Mandatory certified capacity gate

Summary calls require a separate capacity certificate, not a new request field.
Use admission-only construction to ensure even the baseline convenience evaluation
does not run before the gate:

```python
model = DesignSpace(specification, admission_only=True)
certificate = model.summary_capacity(summary_request, frozen_limits)
with open("summary.json.partial", "wb") as sink:
    receipt = model.evaluate_to(summary_request, sink, capacity=certificate, block_size=1)
```

`admission_only=True` preserves the entire model admission prefix; only the terminal
baseline convenience evaluation is deferred. It is supported only for full-analysis
models. Request admission/work validation and static certification never call the
numerical evaluator or expand the Cartesian grid/pair list. Full-detail diagnostic
construction/evaluation remains unchanged. A normal full-detail constructor can
already have evaluated its baseline; it is not the pre-evaluation summary path.

`frozen_limits` is a closed object with all of these required keys:

| Key | Contract |
| --- | --- |
| `version` | `caller-summary-capacity-limits-v1` |
| `filesystem` | Existing caller-authorized output directory |
| `device` | Its frozen `stat().st_dev`, matching output and `TMPDIR` |
| `frozen_available_bytes` | Caller-frozen `f_bavail * f_frsize`, never increased by fresher space |
| `output_limits_bytes` | Every explicit positive single-output ceiling, or `[]` when none exists |
| `reserve_bytes` | Exactly 1,073,741,824 (1 GiB) |
| `logs_and_gate_bytes` | At least 1,048,576; covers gate/receipt artifacts and caller-capped logs |
| `additional_temporary_bytes` | Nonnegative allowance for any additional staging copies |

No logical output-record budget is treated as bytes. The certificate binds the
exact admitted model/request/timing hashes, unchanged seven-work-formula receipt,
summary schema/version, affected engine/schema/entry-point/capacity source hashes,
canonical serializer identity, frozen limit identity, per-group encoded bounds,
rational digit bounds and exact escaped-string-length digest. No identifier
contents are exposed in the capacity receipt. Runtime source binding is restricted
to the affected public components, not a general repository-integrity mechanism.

`causal_full_capacity` derives conservative **canonical encoded upper bounds** from
admitted inputs. Let H be lcm(1..family_count), and D be H times the lcm of all
admitted inverse-grid-rate denominators (including rate one). Every histogram
response mean and sum has denominator dividing D. With W the maximum admitted
clock histogram duration sum, F the maximum inverse rate or one, O the maximum
fixed anchor offset, and ticks/ns = u/v, local numerators are at most W*F*D+O*D.
All grid latency/MoE values have a common denominator D*u and numerator at most
(W*F*D+O*D)*v. Thus pair subtraction has the same common denominator and at most
twice that numerator; joint-minus-OAT uses at most 2*(axis_count+1) times it.

MFU useful-work/latency and numeric peak reciprocals use exact unreduced rational
magnitude products. For bounds |a|<=A, b<=B, |c|<=C, d<=D, nonzero division is
bounded by numerator A*D and denominator B*C; a nonzero integer numerator has
magnitude at least one. Exact zeros and null alternatives are explicitly covered.
Reduction only shortens these bounds. No Decimal rounding or observed output
widths enter the proof.

Curve intercepts/slopes share denominator H*u and numerator bound
C=(W+O)*H*v. Differences therefore cancel that common denominator in line
intersections: intersection numerator/denominator magnitudes are at most 2*C.
Combine these with exact reciprocal domain endpoints and one. Evaluating an
affine line at endpoint magnitude bounds X/Y gives numerator at most C*(X+Y)
and denominator (H*u)*Y. Reciprocals cover finite saturation and break-even scales;
asymptotes are bounded intercepts. For P participating clocks, each of at most
two response regions has at most choose(P,2)+choose(2*P,2)+1 segments. Knots are
at most total_segments+1; break-even intervals at most total_segments; tied
terminals at most 2*P. All bounds use exact integers, including decimal digit
counts and possible minus signs. Scientific notation and escaped identifier
contents forbidden by existing admission remain forbidden; valid long decimal
tokens and exact JSON escaping are handled without changing those grammars.

Canonical byte accounting includes every retained field, embedded input, index,
count, scalar/null alternative, key, punctuation, two-space indentation and final
newline. Every actual admitted identifier uses its own exact `json.dumps` UTF-8
escaped length, never an average or character count. For a nonempty array at
depth d with n items whose encoded sizes total T, bytes = T+n*(2*d+4)+2*d+2.
For an object, add exact escaped key sizes and n*(2*d+6)+2*d+2 to child sizes.
Empty containers use two bytes. Multiplicities use admitted counts, not expanded
design/scenario results. Per-group bounds and separate comparison/edge spool
bounds are recorded; Python allocation estimates are not storage proofs.

Before evaluation, the public API recomputes the complete static certificate and
requires exact canonical equality, including types and closed keys. It then
requires U_result to fit every explicit output ceiling and requires

```
2*U_result + U_comparison_spool + U_edge_spool
  + additional_temporary_bytes + logs_and_gate_bytes + reserve_bytes
```

to fit **min(frozen_available_bytes, fresh same-filesystem f_bavail*f_frsize)**.
This conservatively covers two sequential results with one retained, two live
outer spools, staging and the 1-GiB reserve. Summary has no other internal spool.
Already occupied immutable inputs are not charged again. Callers must account for
their own extra copies and bound their logs within the declared allowances.
The certificate plus two completion receipts must fit the artifact allowance.
Unknown/unproved bounds, stale bindings, insufficient space or any changed limit
reject before the numerical phase. No evaluated outputs repair a failed proof.

Summary binary sinks must be regular files on the certified `TMPDIR` filesystem;
unsupported pipes, in-memory sinks and cross-filesystem sinks reject. Full-detail
sinks retain their existing general short-write contract. Summary still returns
only after successful final emission. Its receipt adds `capacity` and
`receipt_identity` to the original three fields, binding the complete capacity
proof and output identity/bytes/hash. `causal_full_capacity.validate_receipt`
checks that closed binding without claiming authentication or recomputing results.
The caller still owns flushing, durability and staged-file publication.

The public CLI exposes certification and requires a new receipt destination for
summary evaluation (stdout must be redirected to the certified output filesystem):

```sh
python simulator_detailed/moe_model/design_space.py capacity --model model.json --request summary-request.json --limits frozen-limits.json > capacity.json
python simulator_detailed/moe_model/design_space.py evaluate --model model.json --request summary-request.json --capacity capacity.json --receipt summary-receipt.json > summary.json.partial
```

Storage certification does not establish CPU/wall completion, private numerical
compatibility or constant-memory operation for unchanged grid/OAT/joint/surface
storage. No external 600-second wall, 480-second CPU or 16-GiB address-space limit
is raised or replaced. Historical SIGKILL provenance remains unknown. Independent
acceptance and separately authorized private completion remain prerequisites for
any full compatibility or signal-attribution claim.

## Sources, joins and once-only atoms

`source_nodes` is separate from observed `join.actions`. Source and action IDs
must be disjoint; IDs are opaque caller labels, never parsed for provenance,
rank, topology or cost. A source has nullable clock, explicit expansion and
invocation status, cost status, nullable `source_cost_ticks` and authority,
null `exclusive_causal_owner`, and losslessly retained `metadata_json`.
The latter must parse as a strict JSON object, but is never interpreted or run.
Candidates, alias uncertainty, missing bindings and arbitrary original source
metadata belong there. Its original JSON string is retained and identity-bound.

Unresolved cost means null ticks and null authority. A caller may separately
declare a known cost only for a known expansion and invocation, with explicit
nonnegative ticks and authority. **Even this known cost is metadata only:** it
does not enter the partition, timing, ownership, scheduling or causal MFU.
Unresolved sources never acquire costs. Observed action costs and every
exclusive causal owner remain null, exactly as in the timing-partition mode.

Source endpoints can participate only in unresolved logical, source-protocol
or correlation relations. Logical/source-protocol graphs are cycle checked;
correlations remain correlations. Exact observed-action edges retain the
original exact binding, identity reuse, DAG and transitive same-clock
completion-before-acquisition validation, including paths through other clocks.
No cross-clock timestamp comparison or subtraction occurs.

The existing timing admission code validates the original atom partition:
IDs, intervals, durations, active membership, exclusive accounting
owners, conservative/inclusive masks, no holes/overlap and once-only coverage.
Static source edges are validated separately, not converted into observations.
No anchor creates an extra atom or a source-owned service duration.

Only in `causal_full_analysis`, an unobserved residual interval may carry an
explicit conditional singleton family sensitivity: its conservative and
inclusive masks must be equal and contain exactly one registered family bit.
Zero/zero remains valid. Inclusive-only, unequal, multi-bit and unregistered
residual masks reject, as do negative masks, booleans and non-integer tokens.
The complete active set must still be empty, the owner kind must be `residual`,
the accounting owner must remain unique, and the exclusive causal owner null.
This is a caller family-membership hypothesis, not an observation, a resolved
source/binding identity, or an exclusively causal service. Source costs,
dependencies, causal latency and unresolved identities are not promoted.

For either membership and every admitted sharing/priority law, a singleton
family has response `1-e+e/s`. Compared with zeroing its masks, its conditional
contribution changes by `duration*e*(1/s-1)`. Unit rate, no exposure, or an
unselected family gives no change. Unweighted partition and residual ownership
durations remain identical: no observation/source duration is added as another
charge. The original `causal_timing` mode retains its zero-mask-only residual
rule in `CAUSAL_TIMING.md`.

## Fixed finite scenarios

Family array order defines bit indices. Scenario array order and IDs are
preserved everywhere. Each scenario declares membership (`conservative` or
`inclusive`), `sharing`, nullable `priority`, and one fixed `exposure_mask`.
Masks must be unsigned strict integers using only registered bits. Priority
requires a permutation of **every** family index; other policies require null.

For family rate `s`, exposure bit `e`, the factor is `1-e+e/s`. Unselected
families have rate one. For an atom with membership mask zero, the response is
one. Otherwise the only admitted policies are:

- `priority`: factor of the first masked family in the declared permutation;
- `shared_serial`: arithmetic mean of all masked factors;
- `co_bottleneck`: maximum of all masked factors (independent-pipeline alias).

The exposure assignment is shared by every design and comparison, never
replaced by a continuous box or global low/high corners. These are explicit
finite response hypotheses, not physical identity resolutions. No strings are
compiled, evaluated or imported as policies.

## Clock-local layer envelope and units

Every canonical clock has a distinct ID, scope label, rank metadata and local
window. Equal ranks do not merge clocks. `composition.participants` explicitly
selects layer clocks and repeats their scopes. Each carries fixed nonnegative
release and attention offsets from its caller-established local origin, and an
accepted all-unit `baseline_layer_ticks`. They are offsets, not observations or
cross-clock timestamps. `composition.baseline_layer_ticks` declares the envelope.

For each clock, `D = sum(duration_ticks * response)`. For selected layer clocks,
`L = max(attention_offset_ticks, release_offset_ticks + D)`. Layer latency is
the maximum selected `L`; the separate MoE envelope is the maximum selected
`D`. All other clocks and their reweighted `D` remain in output, with null layer
duration. Admission requires every local baseline and the envelope to match at
unit rates; no fitting, enlarged windows, or invented prefix/suffix atoms.

`composition.peak_participants` must explicitly equal the selected clocks and
the unchanged peak-domain participants. Additional standalone clocks cannot
expand peak authority or silently sum peaks. `ticks_per_ns` is a positive exact
decimal string. Durations divide by it to obtain ns. The independently declared
`peak_rate_time_unit` is `tick` or `ns`; peak values and authority are never
rewritten. Output `peak_to_per_ns` is the explicit conversion multiplier.

All arithmetic uses `Fraction`, independent of active and default Decimal
contexts. Latency, MoE, deltas, equations and MFU coefficients serialize as
reduced `{numerator, denominator}` integers with positive denominator. The MFU
coefficient is exactly `useful_flops / latency_ns`. Coefficient interval
endpoints therefore invert latency endpoints, retaining all scenario witnesses.
Numeric MFU is coefficient divided by `peak * peak_to_per_ns`, with the peak
interval inverted as well. Symbolic peak parameter/domain/authority are retained
unchanged; its numeric MFU stays null. Missing peak yields unavailable numeric
MFU but does not erase the known useful-work/latency coefficient.

## Declared capacity and bounded work

There are no inherited fixed array caps on full-mode clocks, actions, bindings,
dependencies, source nodes, atoms, families, scenarios, active memberships,
binding candidates, priority entries, peak participants, axes or grid points.
Caller `capacity.records` explicitly bounds each principal record class plus
membership/candidate/priority entries and source metadata UTF-8 bytes. Fixed-size
tuples and bounded identity/decimal string grammars still apply. In particular,
raising scenario/grid capacity alone does **not** admit oversized model arrays.

The model's `validation_work` must cover the sum of these record counts, plus
`dependencies * clocks`, plus `n * max(1, bit_length(n))` for each of atom,
action and dependency counts. This is a conservative logical work charge for
record admission, sorting and transitive clock propagation, not a wall-clock
deadline, byte-memory allocator or arbitrary-precision bit-operation bound.
The JSON container must already fit the host; decoding/schema validation and
defensive copying occur before semantic admission. A caller must separately
impose its own byte/transport/process limits for untrusted or enormous inputs.

Request `budget` has seven independent limits; missing/negative/boolean limits
reject. Before allocating a Cartesian product, evaluating any response or
expanding comparison pairs, `_prepare` computes and checks every charge:

- `designs`: the full product of grid lengths, never truncated or sampled;
- `scenario_evaluations`: designs times registered scenarios;
- `response_terms`: designs times scenarios times histogram terms times
  `(families + 1)`; terms count both membership histograms of all clocks;
- `comparison_evaluations`: selected directed pairs times scenarios times
  `(axes + 1)`;
- `analysis_units`: scenarios times `(OAT count + designs*(axes+1) + neighbours)`;
- `surface_work`: curves times scenarios times `(terms+clocks+1)^3*(families+1)^2`;
- `output_records`: a conservative logical output charge for retained model
  records, design/scenario/local rows, comparisons, OAT/joint rows, neighbouring
  terminal ties and surface branches, as defined by `_prepare` in this version.

These are deterministic contract charges, not measured runtime counters. Equality
with the charge admits; insufficient budget rejects with required and declared
values. No partial result is returned. Request axis IDs and families are unique,
grids are increasing exact positive rates, contain one and both domain endpoints.

Comparisons explicitly choose `selected` global-index pairs or `all` with an
empty pair list. No mandatory all-pairs work is hidden in a selected request.
All grid/scenario rows, global indices, model identity, atom IDs/masks and fixed
assignments remain available for versioned caller-side analysis. This version
does not implement batching or imply that arbitrary host memory is available.

## Closed analysis output

The full original model and request are returned once, including the canonical
join, atom masks/owners and unresolved metadata. Each design retains every
scenario's local durations, composed layer/MoE ns values, MFU coefficient and
all tied terminal clock/rank/branch labels. Each design's interval extrema retain
all scenario witnesses. Array rate order is request-axis order; global design
indices are the exact Cartesian product order (rightmost axis varies fastest).

`oat` compares every one-axis non-unit point against the common baseline.
`comparisons` contains the requested directed pairs. Delta is left minus right
latency; negative means faster. Hulls and faster/equal/slower counts use the
**same** scenario identity on both sides. `joint_minus_oat` gives baseline-minus-
joint latency minus the sum of baseline-minus-OAT latency under each same
scenario, for every design. No independent interval-endpoint subtraction is
substituted for these paired calculations.

Pareto minimizes latency **and each requested normalized rate**. Unselected
family rates are fixed at one and cannot change dominance. In each fixed
scenario, weak dominance means every objective is no worse; strict dominance
also requires at least one strict objective. The versioned quantifiers are:

- robust: weak in every scenario and strict in at least one;
- opportunity: strict in at least one scenario;
- guaranteed: strict in every scenario.

Directed edges report all three flags. Frontiers are returned only for a complete
directed-pair selection; otherwise they are explicitly null (`selected_pairs`),
not misleading partial frontiers. The separately named `latency_only_outer_set`
uses all-scenario strict latency and ignores rate costs, preserving the meaning
of the older outer-set concept without conflating it with multi-objective Pareto.

`migrations` includes every increasing adjacent-grid edge for each axis and
scenario, with all before/after tied clock/rank/branch terminals, even unchanged
ties. These are envelope terminal migrations, never FIFO/bank/port/link claims.

## Exact OAT/diagonal surfaces

Each declared curve names one axis (OAT) or a subset moved together (diagonal),
holding other rates at one, and a nonempty domain within each selected axis's
registered domain. The complete multidimensional domain remains in the request.
Curves must explicitly forbid extrapolation or declare the same inverse-scale
hypothesis toward infinite scale. No implicit hardware or investment domain
extension is made.

With `x=1/scale`, factors are exactly `x` or one. Priority/serial responses are
affine; co-bottleneck can switch at one. The evaluator constructs their exact
affine sums, intersects local/attention/release envelope lines, and returns
piecewise `intercept + slope*x` equations for layer and MoE ns. This is finite
rational algebra, not numerical fitting or a general symbolic-code facility.
Segments are ordered by increasing `x`. Interior active terminal ties are
retained; `knots` contains exact endpoint values and **all** terminal ties at
branch break-even crossings, including ties lost on either open interval.

`baseline_break_even_scale_intervals` is the union of exact points/plateaus where
the curve equals the accepted unit-rate baseline. `finite_saturation_scale`
is the earliest plateau scale in the analyzed domain (or in the explicitly
declared infinite-scale extension). Without extrapolation an absent plateau is
null; `asymptotic_latency_ns` is always null. With declared extrapolation the
exact limit is returned; finite saturation remains null for a strictly decaying
curve that only approaches its limit. No hardware-investment break-even follows.

`unavailable` explicitly retains null causal latency/MFU/owner and investment
claims, false physical promotion, unavailable physical numeric identity, and
`no_public_evaluation: false`. Thus unresolved numerical identity is not
confused with absence of a public synthetic evaluation.

## Synthetic verification

Summary responses use a request-derived integer lattice. Its denominator is the
LCM of rate numerators multiplied by the LCM of shared-serial divisors. Priority,
serial means and co-bottleneck maxima are exact integers on that lattice. Sparse
histogram reductions run in bounded scenario blocks, using int64 only when the
complete positive duration-sum bound fits; otherwise NumPy object arithmetic uses
Python integers. Local composition, latency, inverted MFU and joint gains reduce
by integer gcd. No rates, masks, scenarios, clocks or output records are removed.

Result-local bounded caches share equal rational/local-clock/terminal records;
scenario dictionaries and their ordered identities remain separate. Summary
validation reuses equal compiled schema shapes and checks shared local records
once per validation call, with fresh state on each call. B174 canonical string
encoding, comparison reductions, full-detail behavior, capacity identities and
the existing B153 S1 boundary remain unchanged. `test_causal_full_response_lattice`
checks public high-entropy responses against the scalar exact oracle.

`tests/causal_full_fixtures.py` contains only invented labels and arithmetic.
The bounded large fixture uses an independently chosen prime atom/action/binding
length, a small prime scenario inventory and a prime-width two-axis grid. They
exercise admission beyond the former public caps; they do not encode real-study
counts or values. All directed pairs deliberately exceed its comparison budget,
while its selected pairs and complete raw grid/scenario rows fit. Tests include
schema closure, exact arithmetic, source nonpromotion, legacy timing admission,
rank separation, transitive DAG, fixed terminals, budget preflight, paired
analysis, terminal ties, surfaces, deterministic replays and CLI rejection.
