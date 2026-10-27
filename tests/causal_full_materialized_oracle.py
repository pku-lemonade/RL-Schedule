"""Small-only B158 materialized reference; never used by the production engine."""
from fractions import Fraction
import itertools
import math
import causal_full_analysis as full
from causal_full_analysis import number, rational, hull, schema, formulas, validate_result
from causal_full_analysis import _prepare, _scenario, _bounds, _mfu, _surface

def _paired(left, right):
    deltas = [number(row['latency_ns']) - number(other['latency_ns']) for row, other in zip(left['scenarios'], right['scenarios'])]
    return dict(left=left['index'], right=right['index'], delta_ns=hull(deltas),
        faster=sum(value < 0 for value in deltas), equal=sum(value == 0 for value in deltas), slower=sum(value > 0 for value in deltas),
        deltas=[dict(scenario=row['id'], delta_ns=rational(value)) for row, value in zip(left['scenarios'], deltas)])


def _dominance(left, right):
    rates = [number(rate) for rate in left['rates']]
    other_rates = [number(rate) for rate in right['rates']]
    rate_weak = all(rate <= other for rate, other in zip(rates, other_rates))
    rate_strict = any(rate < other for rate, other in zip(rates, other_rates))
    deltas = [number(row['latency_ns']) - number(other['latency_ns']) for row, other in zip(left['scenarios'], right['scenarios'])]
    weak = [rate_weak and delta <= 0 for delta in deltas]
    strict = [valid and (rate_strict or delta < 0) for valid, delta in zip(weak, deltas)]
    return dict(left=left['index'], right=right['index'], robust=all(weak) and any(strict),
        opportunity=any(strict), guaranteed=all(strict), latency_guaranteed_strict=all(delta < 0 for delta in deltas))


def evaluate(model, request):
    assert math.prod(len(axis["grid"]) for axis in request["axes"]) <= 64
    assert len(model._specification["scenarios"]) <= 64
    spec, timing = model._specification, model._timing
    grids, work = _prepare(spec, timing, request)
    grid_positions = [{rate: index for index, rate in enumerate(grid)} for grid in grids]
    coordinates = list(itertools.product(*grids))
    lookup = {coordinate: index for index, coordinate in enumerate(coordinates)}
    unit = (Fraction(1),) * len(grids)
    baseline = lookup[unit]
    family_indices = {row['id']: index for index, row in enumerate(spec['families'])}
    points = []
    for index, coordinate in enumerate(coordinates):
        rates = [Fraction(1)] * len(family_indices)
        for axis, rate in zip(request['axes'], coordinate):
            rates[family_indices[axis['family']]] = rate
        rows = [_scenario(spec, timing, scenario, rates) for scenario in spec['scenarios']]
        points.append(dict(index=index, rates=list(map(rational, coordinate)), scenarios=rows,
            latency_ns=_bounds(rows, 'latency_ns'), moe_ns=_bounds(rows, 'moe_ns'), mfu=_mfu(spec, rows)))
    selection = request['comparisons']
    pairs = itertools.permutations(range(len(points)), 2) if selection['policy'] == 'all' else selection['pairs']
    comparisons, edges = [], []
    for left, right in pairs:
        comparisons.append(_paired(points[left], points[right]))
        edges.append(_dominance(points[left], points[right]))
    complete = len(edges) == len(points) * (len(points) - 1)
    pareto = dict(coverage='complete' if complete else 'selected_pairs', edges=edges)
    for output, field in (('robust_frontier', 'robust'), ('opportunity_frontier', 'opportunity'),
            ('guaranteed_frontier', 'guaranteed'), ('latency_only_outer_set', 'latency_guaranteed_strict')):
        dominated = {edge['right'] for edge in edges if edge[field]}
        pareto[output] = [point['index'] for point in points if point['index'] not in dominated] if complete else None
    oat, joint, migrations = [], [], []
    for point, coordinate in zip(points, coordinates):
        if sum(rate != 1 for rate in coordinate) == 1:
            oat.append(_paired(point, points[baseline]))
        single_indices = [lookup[unit[:axis] + (rate,) + unit[axis + 1:]] for axis, rate in enumerate(coordinate)]
        gains = []
        for scenario_index, row in enumerate(point['scenarios']):
            base = number(points[baseline]['scenarios'][scenario_index]['latency_ns'])
            gain = base - number(row['latency_ns']) - sum(base - number(points[single]['scenarios'][scenario_index]['latency_ns']) for single in single_indices)
            gains.append(dict(scenario=row['id'], gain_ns=rational(gain)))
        joint.append(dict(index=point['index'], interval=hull([number(row['gain_ns']) for row in gains]), scenarios=gains))
        for axis, rate in enumerate(coordinate):
            position = grid_positions[axis][rate]
            if position + 1 == len(grids[axis]):
                continue
            neighbour = list(coordinate)
            neighbour[axis] = grids[axis][position + 1]
            right = points[lookup[tuple(neighbour)]]
            for row, other in zip(point['scenarios'], right['scenarios']):
                migrations.append(dict(left=point['index'], right=right['index'], scenario=row['id'],
                    before=row['terminals'], after=other['terminals'], changed=row['terminals'] != other['terminals']))
    surfaces = [_surface(spec, timing, request, surface, scenario) for surface in request['surfaces'] for scenario in spec['scenarios']]
    result = dict(version=schema.VERSION, mode=schema.MODE, input_identity=model.identity,
        request_identity=formulas.identity(request), evaluation_status='evaluated', model=spec, request=request, work=work,
        baseline_index=baseline, grid=points, oat=oat, comparisons=comparisons, joint_minus_oat=joint, pareto=pareto,
        migrations=migrations, surfaces=surfaces, unavailable=dict(source_costs='caller_metadata_only_not_timing',
            exclusive_causal_owner=None, causal_latency_ns=None, causal_mfu=None, physical_promotion=False,
            hardware_investment_break_even=None, numeric_identity_status='unavailable_without_physical_binding', no_public_evaluation=False))
    result['output_identity'] = formulas.identity(result)
    validate_result(result)
    return result
