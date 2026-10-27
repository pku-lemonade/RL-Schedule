"""Caller-owned canonical joins and once-only local timing, never a schedule.

Identity labels are caller assertions, not authentication. No event duration or
dependency becomes an extra cost, and no timestamp is compared across clocks.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import heapq

if __package__:
    from . import counterfactual as formulas
    from . import design_space_schema as wire
else:
    import counterfactual as formulas
    import design_space_schema as wire

need = wire.need


def _index(rows, key, path):
    result = {row[key]: row for row in rows}
    need(len(result) == len(rows), 'DUPLICATE_ID', path, 'unique canonical identities required')
    return result


def _order(nodes, edges):
    indegree = dict.fromkeys(nodes, 0)
    successors = defaultdict(set)
    for before, after in edges:
        if after not in successors[before]:
            successors[before].add(after)
            indegree[after] += 1
    ready = [name for name in nodes if not indegree[name]]
    heapq.heapify(ready)
    result = []
    while ready:
        before = heapq.heappop(ready)
        result.append(before)
        for after in sorted(successors[before]):
            indegree[after] -= 1
            if not indegree[after]:
                heapq.heappush(ready, after)
    need(len(result) == len(nodes), 'CAUSAL_CYCLE', '$/join/dependencies', 'acyclic exact and logical relations required')
    return result


def _causal_order(actions, edges):
    order = _order({node for edge in edges for node in edge}, edges)
    successors = defaultdict(set)
    for before, after in edges:
        successors[before].add(after)
    completions = defaultdict(dict)
    for name in order:
        action = actions[name]
        inherited = completions[name]
        need(inherited.get(action['clock'], action['interval'][0]) <= action['interval'][0],
            'CAUSAL_ORDER', '$/join/dependencies', 'transitive completion must precede acquisition on the same clock')
        inherited[action['clock']] = action['interval'][1]
        for after in successors[name]:
            target = completions[after]
            for clock, completion in inherited.items():
                target[clock] = max(target.get(clock, completion), completion)
        del completions[name]
    return order


def _join(spec, clocks):
    join = spec['join']
    actions = _index(join['actions'], 'id', '$/join/actions')
    bindings = _index(join['bindings'], 'action', '$/join/bindings')
    need(actions.keys() == bindings.keys(), 'JOIN_COVERAGE', '$/join/bindings', 'one binding per canonical action required')
    identities = set()
    for name, action in actions.items():
        path = '$/join/actions/' + name
        need(action['clock'] in clocks and action['interval'][0] <= action['interval'][1],
            'ACTION_CLOCK_ORDER', path, 'known clock and ordered local interval required')
        binding = bindings[name]
        if binding['status'] == 'unresolved':
            need(binding['identity'] is None and binding['unknown'] is not None,
                'UNRESOLVED_IDENTITY', path, 'unresolved identity must stay null with an explicit missing input')
        else:
            need(binding['identity'] is not None and binding['unknown'] is None,
                'EXACT_IDENTITY', path, 'exact caller binding and null missing input required')
            identity = (action['clock'], binding['identity'])
            need(identity not in identities, 'IDENTITY_REUSE', path, 'a local dynamic identity may bind only once')
            identities.add(identity)
    dependencies = _index(join['dependencies'], 'id', '$/join/dependencies')
    exact, logical, identities = [], [], set()
    for edge in dependencies.values():
        path = '$/join/dependencies/' + edge['id']
        before, after = edge['before'], edge['after']
        need(before in actions and after in actions and before != after,
            'DEPENDENCY_ENDPOINT', path, 'known distinct canonical actions required')
        if edge['status'] == 'unresolved':
            need(edge['identity'] is None and edge['unknown'] is not None,
                'UNRESOLVED_IDENTITY', path, 'unresolved dependency must stay explicit, never promoted')
            if edge['basis'] == 'logical':
                logical.append((before, after))
            continue
        need(edge['basis'] == 'happens_before' and edge['identity'] is not None and edge['unknown'] is None,
            'EXACT_DEPENDENCY', path, 'exact caller causal binding required; correlation is not causation')
        need(bindings[before]['status'] == bindings[after]['status'] == 'exact',
            'UNRESOLVED_ENDPOINT', path, 'exact dependencies cannot promote unresolved action identities')
        need(edge['identity'] not in identities, 'IDENTITY_REUSE', path, 'dependency binding may be used only once')
        identities.add(edge['identity'])
        if actions[before]['clock'] == actions[after]['clock']:
            need(actions[before]['interval'][1] <= actions[after]['interval'][0],
                'CAUSAL_ORDER', path, 'completion must precede acquisition on the same clock')
        exact.append((before, after))
    return actions, dict(exact_order=_causal_order(actions, exact),
        logical_order=_order({node for edge in logical for node in edge}, logical),
        exact_edges=[list(edge) for edge in sorted(exact)], logical_edges=[list(edge) for edge in sorted(logical)])


def admit(spec):
    """Validate a schema-checked model and compile sparse per-clock histograms."""
    clocks = _index(spec['clocks'], 'id', '$/clocks')
    need(clocks.keys() == set(spec['peak_domain']['participants']), 'CLOCK_COVERAGE', '$/clocks',
        'peak participants must exactly name the independent local clocks')
    for clock in clocks.values():
        need(clock['window'][0] < clock['window'][1], 'CLOCK_WINDOW', '$/clocks/' + clock['id'], 'positive local window required')
    actions, orders = _join(spec, clocks)
    atoms = _index(spec['partition']['atoms'], 'id', '$/partition/atoms')
    _index(spec['partition']['atoms'], 'accounting_owner', '$/partition/atoms/accounting_owner')
    _index(spec['scenarios'], 'id', '$/scenarios')
    grouped = defaultdict(list)
    for atom in atoms.values():
        path = '$/partition/atoms/' + atom['id']
        need(atom['clock'] in clocks, 'ATOM_CLOCK', path, 'known local clock required')
        first, last = atom['interval']
        need(first < last and last - first == atom['duration_ticks'], 'ATOM_DURATION', path, 'positive exact interval duration required')
        for membership in ('conservative', 'inclusive'):
            mask = atom['family_mask_' + membership]
            need(type(mask) is int and 0 <= mask < 1 << len(spec['families']),
                'FAMILY_MASK', path, 'nonnegative integer containing only registered family bits required')
        need(atom['family_mask_conservative'] & atom['family_mask_inclusive'] == atom['family_mask_conservative'],
            'MASK_SUBSET', path, 'conservative membership must be a subset of inclusive membership')
        count = len(atom['active_actions'])
        kind = 'residual' if count == 0 else 'exclusive_observation' if count == 1 else 'shared_observation'
        need(atom['owner_kind'] == kind, 'OWNER_KIND', path, 'ownership kind must match complete observation membership')
        inclusive = atom['family_mask_inclusive']
        need(count > 0 or inclusive == 0 or (spec['mode'] == 'causal_full_analysis'
            and atom['family_mask_conservative'] == inclusive and inclusive > 0
            and inclusive & (inclusive - 1) == 0),
            'RESIDUAL_MASK', path, 'unobserved residual cannot receive a rate credit')
        grouped[atom['clock']].append(atom)
    starts, ends = defaultdict(lambda: defaultdict(set)), defaultdict(lambda: defaultdict(set))
    for action in actions.values():
        clock = action['clock']
        first = max(action['interval'][0], clocks[clock]['window'][0])
        last = min(action['interval'][1], clocks[clock]['window'][1])
        if first < last:
            starts[clock][first].add(action['id'])
            ends[clock][last].add(action['id'])
    compiled = []
    for name, clock in sorted(clocks.items()):
        local = sorted(grouped[name], key=lambda row: row['interval'])
        endpoints = sorted(set(starts[name]) | set(ends[name]))
        position, cursor, active = 0, clock['window'][0], set()
        histogram = {membership: Counter() for membership in ('conservative', 'inclusive')}
        kinds = Counter()
        for atom in local:
            path = '$/partition/atoms/' + atom['id']
            first, last = atom['interval']
            need(first == cursor and last <= clock['window'][1], 'PARTITION_COVERAGE', path, 'no gaps, overlaps or out-of-window charges')
            while position < len(endpoints) and endpoints[position] <= first:
                endpoint = endpoints[position]
                active.difference_update(ends[name][endpoint])
                active.update(starts[name][endpoint])
                position += 1
            need(position == len(endpoints) or endpoints[position] >= last,
                'MISSING_ENDPOINT', path, 'an atom may not cross an action membership change')
            need(set(atom['active_actions']) == active, 'ACTIVE_MEMBERSHIP', path, 'all and only active same-clock actions required')
            for membership in histogram:
                histogram[membership][atom['family_mask_' + membership]] += atom['duration_ticks']
            kinds[atom['owner_kind']] += atom['duration_ticks']
            cursor = last
        need(cursor == clock['window'][1], 'PARTITION_COVERAGE', '$/clocks/' + name, 'complete local window required')
        duration = clock['window'][1] - clock['window'][0]
        need(sum(kinds.values()) == duration, 'ONCE_ONLY_TOTAL', '$/clocks/' + name, 'every local tick charged once')
        compiled.append(dict(clock=name, rank=clock['rank'], duration_ticks=duration,
            ownership_ticks={kind: kinds[kind] for kind in ('residual', 'exclusive_observation', 'shared_observation')},
            histogram={membership: [dict(family_mask=mask, duration_ticks=ticks) for mask, ticks in sorted(counts.items())]
                for membership, counts in histogram.items()},
            causal_latency_ticks=None))
    return dict(clocks=compiled, **orders)


def _response(mask, factors, overlap):
    selected = [factor for index, factor in enumerate(factors) if mask & (1 << index)]
    if not selected:
        return Decimal(1)
    if overlap == 'shared_serial':
        return sum(selected) / len(selected)
    return max(selected)


def evaluate(model, request, axes):
    """Reuse design-space grids, shared exposure boxes, comparisons and MFU."""
    spec = model._specification
    bindings = {axis['family']['id']: axis for axis in axes}
    points = model._points(axes)
    for point in points:
        factors, corners = [], [[], []]
        for family in spec['families']:
            axis = bindings.get(family['id'])
            scale = Decimal(1) if axis is None else Decimal(point['rates'][axis['source']['id']])
            exposure = (Decimal(0), Decimal(0)) if axis is None else axis['exposure']
            values = tuple(1 - value + value / scale for value in exposure)
            factors.append((min(values), max(values)))
            for corner in (0, 1):
                corners[corner].append(values[corner])
        point['scenarios'] = []
        for scenario in spec['scenarios']:
            local_rows, corner_rows = [], []
            for clock in model._timing['clocks']:
                histogram = clock['histogram'][scenario['membership']]
                def duration(values):
                    return sum((row['duration_ticks'] * _response(row['family_mask'], values, scenario['overlap'])
                        for row in histogram), Decimal(0))
                bounds = [duration([factor[endpoint] for factor in factors]) for endpoint in (0, 1)]
                corner_rows.append([duration(values) for values in corners])
                local_rows.append(dict(clock=clock['clock'], rank=clock['rank'], latency_interval=formulas.strings(bounds),
                    causal_latency_ticks=None))
            bounds = [max(Decimal(row['latency_interval'][endpoint]) for row in local_rows) for endpoint in (0, 1)]
            need(bounds[0] > 0, 'ZERO_LATENCY', '$/axes', 'positive local-duration envelope required')
            point['scenarios'].append(dict(id=scenario['id'], latency_interval=formulas.strings(bounds), mfu=model._mfu(bounds),
                clocks=local_rows, fixed_shared_corner_latency=formulas.strings([max(row[corner] for row in corner_rows) for corner in (0, 1)])))
        bounds = [min(Decimal(row['latency_interval'][0]) for row in point['scenarios']),
            max(Decimal(row['latency_interval'][1]) for row in point['scenarios'])]
        point.update(latency_interval=formulas.strings(bounds), mfu=model._mfu(bounds))
    baseline = next(point for point in points if all(Decimal(rate) == 1 for rate in point['rates'].values()))
    for point in points:
        point['improvement'] = model._relation(point, baseline)
    comparisons = [dict(left=left['id'], right=right['id'], **model._relation(left, right))
        for left in points for right in points if left is not right]
    return dict(grid=points, comparisons=comparisons, pareto=model._pareto(points, comparisons), baseline_grid_id=baseline['id'],
        axes=[axis['source'] for axis in axes], uncertainty=request['uncertainty'], causal_join=spec['join'],
        causal_order=model._timing, causal_latency_ticks=None, authority='caller_partition_with_hypothetical_response',
        accounting_basis='once_only_clock_local_wall_partition', aggregation='maximum_local_duration_not_cross_clock_schedule',
        interval_kind='shared_exposure_endpoint_hull_per_fixed_scenario',
        response_laws=dict(exclusive='1-exposure+exposure/scale', shared_serial='mean(selected_family_factors)',
            co_bottleneck='max(selected_family_factors)', residual='1'),
        unselected_family_rule='factor=1', unresolved_identities_promoted=False)
