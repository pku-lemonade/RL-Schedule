"""Exact finite response hypotheses; no discovery, dynamic code or scheduling."""
from fractions import Fraction
from contextlib import ExitStack
from collections import OrderedDict
from functools import lru_cache
import itertools
import math

if __package__:
    from . import causal_timing, causal_full_schema as schema, design_space_schema as wire
    from . import counterfactual as formulas
    from . import causal_full_stream as streaming
else:
    import causal_timing
    import causal_full_schema as schema
    import design_space_schema as wire
    import counterfactual as formulas
    import causal_full_stream as streaming

need = wire.need
result_schema = schema.result_schema


def rational(value):
    value = Fraction(value)
    return dict(numerator=value.numerator, denominator=value.denominator)


@lru_cache(maxsize=16384)
def _number(numerator, denominator):
    return Fraction(numerator, denominator)


def number(value):
    return _number(value['numerator'], value['denominator'])


def hull(values):
    return [rational(min(values)), rational(max(values))]


def _limits(actual, declared, code, path):
    for key, count in actual.items():
        need(count <= declared[key], code, path + '/' + key,
            'required=' + str(count) + '; declared=' + str(declared[key]) + '; no truncation')


def _records(spec):
    result = {key: len(spec[key]) for key in ('clocks', 'families', 'scenarios', 'source_nodes')}
    result.update({key: len(spec['join'][key]) for key in ('actions', 'bindings', 'dependencies')})
    result.update(atoms=len(spec['partition']['atoms']),
        active_memberships=sum(len(atom['active_actions']) for atom in spec['partition']['atoms']),
        binding_candidates=sum(len(binding['candidates']) for binding in spec['join']['bindings']),
        priority_entries=sum(len(row['priority'] or []) for row in spec['scenarios']),
        metadata_bytes=sum(len(row['metadata_json'].encode('utf-8')) for row in spec['source_nodes']))
    return result


def admit(spec, *, admission_only=False):
    counts = _records(spec)
    _limits(counts, spec['capacity']['records'], 'RECORD_CAPACITY', '$/capacity/records')
    work = sum(counts.values()) + counts['dependencies'] * counts['clocks']
    work += sum(count * max(1, count.bit_length()) for count in
        (counts['atoms'], counts['actions'], counts['dependencies']))
    need(work <= spec['capacity']['validation_work'], 'VALIDATION_BUDGET', '$/capacity/validation_work',
        'declared budget must cover records, sorting and transitive clock propagation: ' + str(work))
    clocks = causal_timing._index(spec['clocks'], 'id', '$/clocks')
    sources = causal_timing._index(spec['source_nodes'], 'id', '$/source_nodes')
    actions = {row['id'] for row in spec['join']['actions']}
    need(not actions.intersection(sources), 'SOURCE_ID', '$/source_nodes', 'source and observed action identities are disjoint')
    for source in sources.values():
        need(source['clock'] is None or source['clock'] in clocks, 'SOURCE_CLOCK', '$/source_nodes', 'null or registered clock required')
        metadata = wire.strict_json(source['metadata_json'])
        need(type(metadata) is dict, 'SOURCE_METADATA', '$/source_nodes', 'opaque JSON object required; never executed')
        known = source['cost_status'] == 'known'
        need((source['source_cost_ticks'] is not None) == known and (source['cost_authority'] is not None) == known,
            'SOURCE_COST', '$/source_nodes', 'known costs require explicit ticks and caller authority; unresolved costs stay null')
        need(not known or source['expansion_status'] == source['invocation_status'] == 'known',
            'SOURCE_COST', '$/source_nodes', 'unresolved source nodes cannot gain a cost')
    causal_timing._index(spec['join']['dependencies'], 'id', '$/join/dependencies')
    observed_edges, logical = [], []
    for edge in spec['join']['dependencies']:
        before, after = edge['before'], edge['after']
        need(before in actions or before in sources, 'DEPENDENCY_ENDPOINT', '$/join/dependencies', 'registered endpoint required')
        need(after in actions or after in sources, 'DEPENDENCY_ENDPOINT', '$/join/dependencies', 'registered endpoint required')
        need(before != after, 'DEPENDENCY_ENDPOINT', '$/join/dependencies', 'distinct endpoints required')
        if before in sources or after in sources or edge['basis'] == 'source_protocol':
            need(edge['status'] == 'unresolved' and edge['identity'] is None and edge['unknown'] is not None,
                'SOURCE_DEPENDENCY', '$/join/dependencies', 'source relations never become exact dependencies')
            need(edge['basis'] in ('logical', 'source_protocol', 'correlation'), 'SOURCE_DEPENDENCY',
                '$/join/dependencies', 'source endpoints have no observed happens-before relation')
        else:
            observed_edges.append(edge)
        if edge['status'] == 'unresolved' and edge['basis'] in ('logical', 'source_protocol'):
            logical.append((before, after))
    causal_timing._order({node for edge in logical for node in edge}, logical)
    for scenario in spec['scenarios']:
        need(scenario['exposure_mask'].bit_length() <= len(spec['families']), 'EXPOSURE_MASK', '$/scenarios', 'registered bits only')
        priority = scenario['priority']
        need((priority is not None) == (scenario['sharing'] == 'priority'), 'PRIORITY', '$/scenarios', 'priority policy alone requires a permutation')
        if priority is not None:
            need(sorted(priority) == list(range(len(spec['families']))), 'PRIORITY', '$/scenarios', 'permutation of every registered family index required')
    composition = spec['composition']
    participants = causal_timing._index(composition['participants'], 'clock', '$/composition/participants')
    need(set(participants) <= clocks.keys(), 'LAYER_CLOCK', '$/composition', 'registered participant clocks required')
    need(set(composition['peak_participants']) == set(spec['peak_domain']['participants']) == set(participants),
        'PEAK_PARTICIPANTS', '$/composition', 'peak applicability must explicitly equal selected layer participants, not all clocks')
    need(Fraction(composition['ticks_per_ns']) > 0, 'TIME_CONVERSION', '$/composition/ticks_per_ns', 'positive exact ticks per ns required')
    projected = dict(spec, peak_domain=dict(spec['peak_domain'], participants=list(clocks)),
        join=dict(spec['join'], dependencies=observed_edges))
    timing = causal_timing.admit(projected)
    for participant in participants.values():
        clock = clocks[participant['clock']]
        need(participant['scope'] == clock['scope'], 'LAYER_SCOPE', '$/composition', 'participant scope must match its canonical clock')
        duration = clock['window'][1] - clock['window'][0]
        need(max(participant['attention_offset_ticks'], participant['release_offset_ticks'] + duration) == participant['baseline_layer_ticks'],
            'LAYER_BASELINE', '$/composition/participants', 'fixed local anchors must reproduce the declared all-unit boundary')
    need(max(row['baseline_layer_ticks'] for row in participants.values()) == composition['baseline_layer_ticks'],
        'LAYER_BASELINE', '$/composition', 'declared layer envelope must match the all-unit boundary')
    baseline = None if admission_only else _scenario(spec, timing, spec['scenarios'][0], [Fraction(1)] * len(spec['families']))
    return timing, baseline


def template(model, *, output_detail='full'):
    return dict(version=schema.output_version(output_detail), mode=schema.MODE, input_identity=model.identity, study_id=None,
        claim='conditional_not_physical_truth', budget={key: None for key in schema.WORK}, axes=[],
        comparisons=dict(policy='selected', pairs=[]), surfaces=[])


def _response(mask, factors, scenario):
    if not mask:
        return Fraction(1)
    if scenario['sharing'] == 'priority':
        return next(factors[index] for index in scenario['priority'] if mask & (1 << index))
    selected = [factor for index, factor in enumerate(factors) if mask & (1 << index)]
    return sum(selected) / len(selected) if scenario['sharing'] == 'shared_serial' else max(selected)


def _scenario(spec, timing, scenario, rates):
    factors = [1 / rate if scenario['exposure_mask'] & (1 << index) else Fraction(1)
        for index, rate in enumerate(rates)]
    local, durations, layers, terminals = [], {}, {}, []
    participants = {row['clock']: row for row in spec['composition']['participants']}
    for clock in timing['clocks']:
        duration = sum((row['duration_ticks'] * _response(row['family_mask'], factors, scenario)
            for row in clock['histogram'][scenario['membership']]), Fraction(0))
        name = clock['clock']
        durations[name] = duration
        layer = None
        if name in participants:
            participant = participants[name]
            branches = dict(attention=Fraction(participant['attention_offset_ticks']),
                release_plus_moe=participant['release_offset_ticks'] + duration)
            layer = max(branches.values())
            layers[name] = layer
            terminals.extend((value, dict(clock=name, rank=clock['rank'], branch=branch)) for branch, value in branches.items())
        local.append(dict(clock=name, rank=clock['rank'], moe_ticks=rational(duration),
            layer_ticks=None if layer is None else rational(layer)))
    ticks_per_ns = Fraction(spec['composition']['ticks_per_ns'])
    latency = max(layers.values()) / ticks_per_ns
    moe = max(durations[name] for name in participants) / ticks_per_ns
    return dict(id=scenario['id'], latency_ns=rational(latency), moe_ns=rational(moe),
        mfu_coefficient=rational(spec['useful_flops'] / latency), clocks=local,
        terminals=[terminal for value, terminal in terminals if value == latency * ticks_per_ns])


def _bounds(rows, key):
    lower = upper = rows[0][key]
    lower_scenarios, upper_scenarios = [], []
    for row in rows:
        value = row[key]
        below = value['numerator'] * lower['denominator'] - lower['numerator'] * value['denominator']
        above = value['numerator'] * upper['denominator'] - upper['numerator'] * value['denominator']
        if below < 0:
            lower, lower_scenarios = value, []
        if below <= 0:
            lower_scenarios.append(row['id'])
        if above > 0:
            upper, upper_scenarios = value, []
        if above >= 0:
            upper_scenarios.append(row['id'])
    return dict(interval=[rational(number(lower)), rational(number(upper))],
        lower_scenarios=lower_scenarios, upper_scenarios=upper_scenarios)


def _mfu(spec, rows):
    coefficient = _bounds(rows, 'mfu_coefficient')
    peak = spec['peak']
    peak_to_per_ns = Fraction(spec['composition']['ticks_per_ns']) if spec['composition']['peak_rate_time_unit'] == 'tick' else Fraction(1)
    numeric = None
    if peak['kind'] in ('accepted', 'hypothesis'):
        peak_lower, peak_upper = map(Fraction, peak['interval'])
        numeric = [rational(number(coefficient['interval'][0]) / (peak_upper * peak_to_per_ns)),
            rational(number(coefficient['interval'][1]) / (peak_lower * peak_to_per_ns))]
    return dict(coefficient=coefficient, numeric_interval=numeric, parameter=peak['parameter'],
        status='numeric' if numeric is not None else 'symbolic' if peak['kind'] == 'symbolic' else 'unavailable',
        peak_to_per_ns=rational(peak_to_per_ns))


def _prepare(spec, timing, request):
    axes = request['axes']
    causal_timing._index(axes, 'id', '$/axes')
    causal_timing._index(axes, 'family', '$/axes')
    families = [row['id'] for row in spec['families']]
    grids = []
    for axis in axes:
        need(axis['family'] in families, 'UNSUPPORTED_FAMILY', '$/axes', 'registered family required')
        grid = list(map(Fraction, axis['grid']))
        domain = list(map(Fraction, axis['domain']))
        need(grid == sorted(set(grid)) and grid[0] > 0 and domain == [grid[0], grid[-1]] and Fraction(1) in grid,
            'GRID_DOMAIN', '$/axes', 'ordered positive full endpoint grid including one required')
        grids.append(grid)
    size = math.prod(map(len, grids))
    scenarios = len(spec['scenarios'])
    comparison = request['comparisons']
    if comparison['policy'] == 'all':
        need(not comparison['pairs'], 'COMPARISON_SELECTION', '$/comparisons', 'all policy requires empty explicit selection')
        pairs = size * (size - 1)
    else:
        pairs = len(comparison['pairs'])
        for left, right in comparison['pairs']:
            need(left < size and right < size and left != right, 'COMPARISON_SELECTION', '$/comparisons', 'distinct valid global design indices required')
    causal_timing._index(request['surfaces'], 'id', '$/surfaces')
    by_id = {axis['id']: axis for axis in axes}
    for surface in request['surfaces']:
        lower, upper = map(Fraction, surface['domain'])
        need(0 < lower < upper and set(surface['axes']) <= by_id.keys(), 'SURFACE_DOMAIN', '$/surfaces', 'positive nonempty registered curve domain required')
        need(all(Fraction(by_id[name]['domain'][0]) <= lower < upper <= Fraction(by_id[name]['domain'][1]) for name in surface['axes']),
            'SURFACE_DOMAIN', '$/surfaces', 'curve must remain inside every declared multidimensional axis domain')
    terms = sum(len(clock['histogram'][membership]) for clock in timing['clocks'] for membership in ('conservative', 'inclusive'))
    neighbours = sum((len(grid) - 1) * (size // len(grid)) for grid in grids)
    oat_count = sum(len(grid) - 1 for grid in grids)
    surface_work = len(request['surfaces']) * scenarios * (terms + len(spec['clocks']) + 1) ** 3 * (len(families) + 1) ** 2
    work = dict(designs=size, scenario_evaluations=size * scenarios,
        response_terms=size * scenarios * terms * (len(families) + 1),
        comparison_evaluations=pairs * scenarios * (len(axes) + 1),
        analysis_units=scenarios * (oat_count + size * (len(axes) + 1) + neighbours), surface_work=surface_work)
    work['output_records'] = sum(_records(spec).values()) + size * (1 + len(axes) + scenarios * (len(spec['clocks']) * 4 + 4))
    work['output_records'] += (pairs + oat_count) * (scenarios + 2) + size * (scenarios + 1) + neighbours * scenarios * (4 * len(spec['clocks']) + 1) + surface_work
    _limits(work, request['budget'], 'EVALUATION_BUDGET', '$/budget')
    return grids, work


def _pair(left, right, *, deltas):
    rates = [number(rate) for rate in left['rates']]
    other_rates = [number(rate) for rate in right['rates']]
    rate_weak = all(rate <= other for rate, other in zip(rates, other_rates))
    rate_strict = any(rate < other for rate, other in zip(rates, other_rates))
    minimum = maximum = None
    faster = equal = slower = 0
    all_weak = all_strict = latency_strict = True
    any_strict = False
    for row, other in zip(left['scenarios'], right['scenarios']):
        delta = number(row['latency_ns']) - number(other['latency_ns'])
        minimum = delta if minimum is None else min(minimum, delta)
        maximum = delta if maximum is None else max(maximum, delta)
        faster += delta < 0
        equal += delta == 0
        slower += delta > 0
        weak = rate_weak and delta <= 0
        strict = weak and (rate_strict or delta < 0)
        all_weak = all_weak and weak
        all_strict = all_strict and strict
        any_strict = any_strict or strict
        latency_strict = latency_strict and delta < 0
        if deltas is not None:
            deltas.append(dict(scenario=row['id'], delta_ns=rational(delta)))
    paired = dict(left=left['index'], right=right['index'], delta_ns=[rational(minimum), rational(maximum)],
        faster=faster, equal=equal, slower=slower, deltas=deltas)
    edge = dict(left=left['index'], right=right['index'], robust=all_weak and any_strict,
        opportunity=any_strict, guaranteed=all_strict, latency_guaranteed_strict=latency_strict)
    return paired, edge


def _paired(left, right):
    return _pair(left, right, deltas=[])[0]


def _dominance(left, right):
    return _pair(left, right, deltas=None)[1]


class _SummaryScenarios:
    """Bounded exact response lattices; identities remain separate output rows."""

    def __init__(self, spec, timing, block_size=128):
        self.spec, self.timing = spec, timing
        self.groups, self.indices, keys = [], [], {}
        self.expressions, expression_ids = [], {}
        for scenario in spec['scenarios']:
            masks = sorted({row['family_mask'] for clock in timing['clocks'] for row in clock['histogram'][scenario['membership']]})
            descriptors = []
            for mask in masks:
                selected = tuple(index for index in range(len(spec['families'])) if mask & (1 << index))
                if scenario['priority'] is not None:
                    selected = tuple(index for index in scenario['priority'] if mask & (1 << index))[:1]
                exposed = tuple(index for index in selected if scenario['exposure_mask'] & (1 << index))
                descriptor = (scenario['sharing'], exposed, len(selected) - len(exposed)) if exposed else ('unit', (), 0)
                if descriptor not in expression_ids:
                    expression_ids[descriptor] = len(self.expressions)
                    self.expressions.append(descriptor)
                descriptors.append(expression_ids[descriptor])
            key = (scenario['membership'], tuple(descriptors))
            if key not in keys:
                keys[key] = len(self.groups)
                self.groups.append((scenario, descriptors))
            self.indices.append(keys[key])
        self.cache = OrderedDict()
        self.block_size = block_size
        self.divisor = math.lcm(*(len(exposed) + unexposed for sharing, exposed, unexposed in self.expressions
            if sharing == 'shared_serial'))
        self.terms, self.weights, self.offsets = [], [], [0]
        for scenario, descriptors in self.groups:
            masks = sorted({row['family_mask'] for clock in timing['clocks'] for row in clock['histogram'][scenario['membership']]})
            expressions = dict(zip(masks, descriptors))
            for clock in timing['clocks']:
                histogram = clock['histogram'][scenario['membership']]
                self.terms.extend(expressions[row['family_mask']] for row in histogram)
                self.weights.extend(row['duration_ticks'] for row in histogram)
                self.offsets.append(len(self.terms))
        self.maximum_duration = max(sum(self.weights[start:end]) for start, end in zip(self.offsets, self.offsets[1:]))
        self.participants = {row['clock']: row for row in spec['composition']['participants']}
        self.ticks_per_ns = Fraction(spec['composition']['ticks_per_ns'])
        self.encode = lru_cache(maxsize=16384)(self._encode)
        self.local = lru_cache(maxsize=16384)(self._local)
        self.terminals = lru_cache(maxsize=256)(self._terminals)

    @staticmethod
    def _encode(numerator, denominator):
        divisor = math.gcd(numerator, denominator)
        return dict(numerator=numerator // divisor, denominator=denominator // divisor)

    def _local(self, index, numerator, denominator):
        clock = self.timing['clocks'][index]
        participant = self.participants.get(clock['clock'])
        layer = None if participant is None else self.encode(max(participant['attention_offset_ticks'] * denominator,
            participant['release_offset_ticks'] * denominator + numerator), denominator)
        return dict(clock=clock['clock'], rank=clock['rank'], moe_ticks=self.encode(numerator, denominator), layer_ticks=layer)

    def _terminals(self, selected):
        return [dict(clock=self.timing['clocks'][index]['clock'], rank=self.timing['clocks'][index]['rank'], branch=branch)
            for index, branch in selected]

    def _template(self, durations, denominator):
        divisor = math.gcd(denominator, *durations)
        key = (denominator // divisor, *(duration // divisor for duration in durations))
        if key not in self.cache:
            local, branches, moe = [], [], []
            for index, duration in enumerate(durations):
                divisor = math.gcd(duration, denominator)
                local.append(self.local(index, duration // divisor, denominator // divisor))
                participant = self.participants.get(self.timing['clocks'][index]['clock'])
                if participant is not None:
                    moe.append(duration)
                    branches.extend(((participant['attention_offset_ticks'] * denominator, (index, 'attention')),
                        (participant['release_offset_ticks'] * denominator + duration, (index, 'release_plus_moe'))))
            latency = max(value for value, terminal in branches)
            ticks = self.ticks_per_ns
            self.cache[key] = dict(latency_ns=self.encode(latency * ticks.denominator, denominator * ticks.numerator),
                moe_ns=self.encode(max(moe) * ticks.denominator, denominator * ticks.numerator),
                mfu_coefficient=self.encode(self.spec['useful_flops'] * denominator * ticks.numerator, latency * ticks.denominator),
                clocks=local, terminals=self.terminals(tuple(terminal for value, terminal in branches if value == latency)))
            if len(self.cache) > 4096:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def rows(self, rates):
        import numpy as np
        denominator = math.lcm(*(rate.numerator for rate in rates)) * self.divisor
        inverse = [rate.denominator * (denominator // rate.numerator) for rate in rates]
        responses = []
        for sharing, exposed, unexposed in self.expressions:
            values = [inverse[index] for index in exposed]
            response = denominator
            if sharing == 'priority':
                response = values[0]
            elif sharing == 'shared_serial':
                response = (sum(values) + unexposed * denominator) // (len(values) + unexposed)
            elif sharing == 'co_bottleneck':
                response = max(values + ([denominator] if unexposed else []))
            responses.append(response)
        dtype = np.int64 if max(responses) * self.maximum_duration <= (1 << 63) - 1 else object
        responses = np.array(responses, dtype=dtype)
        templates = []
        clocks = len(self.timing['clocks'])
        for start in range(0, len(self.groups), self.block_size):
            end = min(start + self.block_size, len(self.groups))
            lower, upper = self.offsets[start * clocks], self.offsets[end * clocks]
            products = responses[self.terms[lower:upper]] * np.array(self.weights[lower:upper], dtype=dtype)
            offsets = np.array(self.offsets[start * clocks:end * clocks]) - lower
            durations = np.add.reduceat(products, offsets).reshape(-1, clocks).tolist()
            templates.extend(self._template(row, denominator) for row in durations)
        return [dict(templates[index], id=scenario['id']) for scenario, index in zip(self.spec['scenarios'], self.indices)]


class _SummaryPairs:
    """Exact integer-lattice reductions, with one left endpoint cached at a time."""

    def __init__(self, points, selection):
        import numpy as np
        self.np = np
        self.points = points
        self.denominator = math.lcm(*(row['latency_ns']['denominator'] for point in points for row in point['scenarios']))
        values = [[row['latency_ns']['numerator'] * (self.denominator // row['latency_ns']['denominator'])
            for row in point['scenarios']] for point in points]
        origin = values[0]
        values = [[value - base for value, base in zip(row, origin)] for row in values]
        maximum = max(abs(value) for row in values for value in row)
        self.values = np.array(values, dtype=np.int64 if maximum <= (1 << 62) - 1 else object)
        self.rates = np.array([[number(rate) for rate in point['rates']] for point in points], dtype=object)
        self.selected = None
        if selection['policy'] != 'all':
            self.selected = {}
            for left, right in selection['pairs']:
                self.selected.setdefault(left, set()).add(right)
        self.left = None
        self.aggregates = None

    def pair(self, left, right):
        np = self.np
        if left != self.left:
            rights = [index for index in range(len(self.points)) if index != left] if self.selected is None else sorted(self.selected[left])
            rows = {}
            for start in range(0, len(rights), 128):
                indices = rights[start:start + 128]
                delta = self.values[left] - self.values[indices]
                minimum, maximum = delta.min(axis=1), delta.max(axis=1)
                faster, slower = (delta < 0).sum(axis=1), (delta > 0).sum(axis=1)
                weak = (self.rates[left] <= self.rates[indices]).all(axis=1)
                strict = (self.rates[left] < self.rates[indices]).any(axis=1)
                rows.update(zip(indices, zip(minimum.tolist(), maximum.tolist(), faster.tolist(), slower.tolist(), weak.tolist(), strict.tolist())))
            self.left, self.aggregates = left, rows
        minimum, maximum, faster, slower, weak, strict = self.aggregates[right]
        paired = dict(left=self.points[left]['index'], right=self.points[right]['index'],
            delta_ns=[rational(Fraction(minimum, self.denominator)), rational(Fraction(maximum, self.denominator))],
            faster=faster, equal=self.values.shape[1] - faster - slower, slower=slower)
        edge = dict(left=paired['left'], right=paired['right'],
            robust=weak and maximum <= 0 and (strict or minimum < 0),
            opportunity=weak and (minimum <= 0 if strict else minimum < 0),
            guaranteed=weak and (maximum <= 0 if strict else maximum < 0),
            latency_guaranteed_strict=maximum < 0)
        return paired, edge


def _comparison_blocks(points, selection, block_size=1, *, reverse=False, summary=False):
    if type(block_size) is not int or block_size < 1:
        raise ValueError('positive integer comparison block_size required')
    pairs = iter(itertools.permutations(range(len(points)), 2) if selection['policy'] == 'all' else selection['pairs'])
    delta_schema = result_schema()['properties']['comparisons']['items']['properties']['deltas']['items']
    exact = _SummaryPairs(points, selection) if summary and type(points) is list else None
    while selected := list(itertools.islice(pairs, block_size)):
        with ExitStack() as stack:
            block = [None] * len(selected)
            try:
                order = range(len(selected) - 1, -1, -1) if reverse else range(len(selected))
                for index in order:
                    left, right = selected[index]
                    deltas = None if summary else stack.enter_context(streaming.ArraySpool(3, delta_schema, _check_rationals))
                    block[index] = exact.pair(left, right) if exact is not None else _pair(points[left], points[right], deltas=deltas)
                    if summary and exact is None:
                        del block[index][0]['deltas']
                    if not summary:
                        deltas.finish()
                yield block
            finally:
                block.clear()


def _materialized_pair(left, right, summary):
    paired, edge = _pair(left, right, deltas=None if summary else [])
    if summary:
        del paired['deltas']
    return paired, edge


def _comparisons(points, selection, comparisons, edges, block_size=1, *, summary=False):
    fields = (('robust_frontier', 'robust'), ('opportunity_frontier', 'opportunity'),
        ('guaranteed_frontier', 'guaranteed'), ('latency_only_outer_set', 'latency_guaranteed_strict'))
    dominated = {field: set() for output, field in fields}
    count = 0
    if summary or isinstance(comparisons, streaming.ArraySpool):
        producer = _comparison_blocks(points, selection, block_size, summary=True) if summary else _comparison_blocks(points, selection, block_size)
    else:
        pairs = itertools.permutations(range(len(points)), 2) if selection['policy'] == 'all' else selection['pairs']
        producer = ([_materialized_pair(points[left], points[right], summary)] for left, right in pairs)
    try:
        for block in producer:
            for paired, edge in block:
                comparisons.append(paired)
                edges.append(edge)
                count += 1
                for output, field in fields:
                    if edge[field]:
                        dominated[field].add(edge['right'])
            del paired, edge
    finally:
        producer.close()
    complete = count == len(points) * (len(points) - 1)
    pareto = dict(coverage='complete' if complete else 'selected_pairs', edges=edges)
    for output, field in fields:
        pareto[output] = [index for index in range(len(points)) if index not in dominated[field]] if complete else None
    return pareto


def _output_identity(result):
    return streaming.identity(result) if isinstance(result, streaming.CanonicalOutput) or isinstance(result['comparisons'], streaming.ArraySpool) else formulas.identity(result)


def _check_rationals(value):
    if type(value) is dict:
        if set(value) == {'numerator', 'denominator'}:
            need(math.gcd(value['numerator'], value['denominator']) == 1, 'OUTPUT_RATIONAL', '$', 'reduced rational required')
        for child in value.values():
            _check_rationals(child)
    elif type(value) is list:
        for child in value:
            _check_rationals(child)


def _capacity_gate(model, request, certificate, sink=None):
    if __package__:
        from . import causal_full_capacity
    else:
        import causal_full_capacity
    if request['version'] == schema.SUMMARY_VERSION:
        return causal_full_capacity.verify(model, request, certificate, sink=sink)
    need(certificate is None, 'CAPACITY_MODE', '$', 'summary capacity certificate forbidden for full detail')
    return None


def evaluate(model, request, *, capacity=None):
    _capacity_gate(model, request, capacity)
    result = _build_result(model, request, [], [], 1)
    result['output_identity'] = _output_identity(result)
    validate_result(result)
    return result


def evaluate_to(model, request, sink, *, block_size=1, capacity=None, _prepared=None):
    if type(block_size) is not int or block_size < 1:
        raise ValueError('positive integer comparison block_size required')
    if _prepared is not None:
        if __package__:
            from . import certified_setup
        else:
            import certified_setup
        certified_setup.check_execution(_prepared, model, request)
    capacity_receipt = _capacity_gate(model, request, capacity, sink) if _prepared is None else _prepared.capacity
    prepared_arguments = {} if _prepared is None else dict(_prepared=_prepared)
    properties = result_schema(request['version'])['properties']
    with streaming.ArraySpool(1, properties['comparisons']['items'], _check_rationals) as comparisons, \
            streaming.ArraySpool(2, properties['pareto']['properties']['edges']['items'], _check_rationals) as edges:
        comparisons.fast = edges.fast = request['version'] == schema.SUMMARY_VERSION
        result = _build_result(model, request, comparisons, edges, block_size, **prepared_arguments)
        comparisons.finish()
        edges.finish()
        with streaming.CanonicalOutput(result) as canonical:
            canonical.assemble()
            result['output_identity'] = _output_identity(canonical)
            validate_result(result, _canonical=canonical, **prepared_arguments)
            count, checksum = canonical.emit_to(sink)
            receipt = dict(output_identity=result['output_identity'], bytes_written=count, sha256=checksum)
            if capacity_receipt is not None:
                need(count <= capacity_receipt['bounds']['result_bytes'], 'CAPACITY_PROOF', '$', 'certified encoded upper bound exceeded')
                receipt['capacity'] = capacity_receipt
                receipt['receipt_identity'] = formulas.identity(receipt)
            return receipt


def _build_result(model, request, comparisons, edges, block_size, *, _prepared=None):
    spec, timing = model._specification, model._timing
    grids, work = _prepare(spec, timing, request) if _prepared is None else (_prepared.grids, _prepared.work)
    grid_positions = [{rate: index for index, rate in enumerate(grid)} for grid in grids]
    coordinates = list(itertools.product(*grids))
    lookup = {coordinate: index for index, coordinate in enumerate(coordinates)}
    unit = (Fraction(1),) * len(grids)
    baseline = lookup[unit]
    family_indices = {row['id']: index for index, row in enumerate(spec['families'])}
    summary = request['version'] == schema.SUMMARY_VERSION
    scenario_groups = _SummaryScenarios(spec, timing) if summary else None
    points = []
    for index, coordinate in enumerate(coordinates):
        rates = [Fraction(1)] * len(family_indices)
        for axis, rate in zip(request['axes'], coordinate):
            rates[family_indices[axis['family']]] = rate
        rows = scenario_groups.rows(rates) if summary else [_scenario(spec, timing, scenario, rates) for scenario in spec['scenarios']]
        points.append(dict(index=index, rates=list(map(rational, coordinate)), scenarios=rows,
            latency_ns=_bounds(rows, 'latency_ns'), moe_ns=_bounds(rows, 'moe_ns'), mfu=_mfu(spec, rows)))
    pareto = _comparisons(points, request['comparisons'], comparisons, edges, block_size,
        summary=request['version'] == schema.SUMMARY_VERSION)
    oat, joint, migrations = [], [], []
    for point, coordinate in zip(points, coordinates):
        if sum(rate != 1 for rate in coordinate) == 1:
            oat.append(_paired(point, points[baseline]))
        single_indices = [lookup[unit[:axis] + (rate,) + unit[axis + 1:]] for axis, rate in enumerate(coordinate)]
        gains = []
        grouped_gains = {}
        for scenario_index, row in enumerate(point['scenarios']):
            group = scenario_groups.indices[scenario_index] if summary else scenario_index
            if group not in grouped_gains:
                if summary:
                    values = [points[baseline]['scenarios'][scenario_index]['latency_ns'], row['latency_ns']]
                    values.extend(points[single]['scenarios'][scenario_index]['latency_ns'] for single in single_indices)
                    denominator = math.lcm(*(value['denominator'] for value in values))
                    scaled = [value['numerator'] * (denominator // value['denominator']) for value in values]
                    grouped_gains[group] = _SummaryScenarios._encode(sum(scaled[2:]) - scaled[1] - (len(single_indices) - 1) * scaled[0], denominator)
                else:
                    base = number(points[baseline]['scenarios'][scenario_index]['latency_ns'])
                    gain = base - number(row['latency_ns']) - sum(base - number(points[single]['scenarios'][scenario_index]['latency_ns']) for single in single_indices)
                    grouped_gains[group] = rational(gain)
            gains.append(dict(scenario=row['id'], gain_ns=grouped_gains[group]))
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
    result = dict(version=request['version'], mode=schema.MODE, input_identity=model.identity,
        request_identity=formulas.identity(request) if _prepared is None else _prepared.request_identity,
        evaluation_status='evaluated', model=spec, request=request, work=work,
        baseline_index=baseline, grid=points, oat=oat, comparisons=comparisons, joint_minus_oat=joint, pareto=pareto,
        migrations=migrations, surfaces=surfaces, unavailable=dict(source_costs='caller_metadata_only_not_timing',
            exclusive_causal_owner=None, causal_latency_ns=None, causal_mfu=None, physical_promotion=False,
            hardware_investment_break_even=None, numeric_identity_status='unavailable_without_physical_binding', no_public_evaluation=False))
    return result


def _validate_summary_row(row, clocks, participants, ticks_per_ns, useful_flops, validated_locals):
    encode = _SummaryScenarios._encode
    need([local['clock'] for local in row['clocks']] == sorted(clocks),
        'OUTPUT_CLOCKS', '$/grid', 'every distinct clock retained exactly once')
    denominator = math.lcm(*(local['moe_ticks']['denominator'] for local in row['clocks']))
    branches, moe = [], []
    for local in row['clocks']:
        name = local['clock']
        value = local['moe_ticks']
        duration = value['numerator'] * (denominator // value['denominator'])
        participant = participants.get(name)
        if id(local) not in validated_locals:
            need(duration > 0 and local['rank'] == clocks[name]['rank'], 'OUTPUT_CLOCKS', '$/grid', 'positive local duration and unchanged rank required')
            if participant is None:
                need(local['layer_ticks'] is None, 'OUTPUT_CLOCKS', '$/grid', 'standalone clocks cannot gain layer participation')
            else:
                layer = max(participant['attention_offset_ticks'] * denominator, participant['release_offset_ticks'] * denominator + duration)
                need(local['layer_ticks'] == encode(layer, denominator), 'OUTPUT_LAYER', '$/grid', 'fixed-anchor composition required')
            validated_locals.add(id(local))
        if participant is not None:
            moe.append(duration)
            branches.extend(((participant['attention_offset_ticks'] * denominator, dict(clock=name, rank=local['rank'], branch='attention')),
                (participant['release_offset_ticks'] * denominator + duration, dict(clock=name, rank=local['rank'], branch='release_plus_moe'))))
    latency = max(value for value, terminal in branches)
    numerator_ns, denominator_ns = ticks_per_ns.denominator, denominator * ticks_per_ns.numerator
    need(row['latency_ns'] == encode(latency * numerator_ns, denominator_ns)
        and row['moe_ns'] == encode(max(moe) * numerator_ns, denominator_ns)
        and row['mfu_coefficient'] == encode(useful_flops * denominator_ns, latency * numerator_ns),
        'OUTPUT_LAYER', '$/grid', 'exact latency, MoE and inverted MFU required')
    need(row['terminals'] == [terminal for value, terminal in branches if value == latency],
        'OUTPUT_TERMINALS', '$/grid', 'all and only tied declared terminals required')


def validate_result(result, *, _prepared=None, _canonical=None):
    view = streaming.validation_view(result) if isinstance(result.get('comparisons'), streaming.ArraySpool) else result
    summary = result.get('version') == schema.SUMMARY_VERSION
    validation_schema = result_schema(result.get('version'))
    if _prepared is not None:
        if __package__:
            from . import certified_setup
        else:
            import certified_setup
        certified_setup.check_execution(_prepared, _prepared.model if type(_prepared) is certified_setup.Execution else None,
            result['request'])
        _prepared.validate_inputs(result)
        view = dict(view, model={}, request={})
        validation_schema['properties']['model'] = wire._object({})
        validation_schema['properties']['request'] = wire._object({})
    if summary:
        wire.compile_validator(validation_schema, memo=set())(view)
    else:
        wire.validate(view, result_schema(result.get('version')))
    visited = set()
    def visit(value):
        if summary and type(value) in (dict, list):
            if id(value) in visited:
                return
            visited.add(id(value))
        if type(value) is dict:
            if set(value) == {'numerator', 'denominator'}:
                need(math.gcd(value['numerator'], value['denominator']) == 1, 'OUTPUT_RATIONAL', '$', 'reduced rational required')
            for child in value.values():
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)
    visit(result)
    spec, request = result['model'], result['request']
    scenario_ids = [row['id'] for row in spec['scenarios']]
    clocks = {row['id']: row for row in spec['clocks']}
    participants = {row['clock']: row for row in spec['composition']['participants']}
    expected_size = math.prod(len(axis['grid']) for axis in request['axes'])
    need(len(result['grid']) == expected_size and result['baseline_index'] < expected_size,
        'OUTPUT_GRID', '$/grid', 'complete globally indexed Cartesian grid required')
    coordinates = itertools.product(*(map(Fraction, axis['grid']) for axis in request['axes']))
    validated_rows = set()
    validated_locals = set()
    ticks_per_ns = Fraction(spec['composition']['ticks_per_ns'])
    for index, (point, coordinate) in enumerate(zip(result['grid'], coordinates)):
        need(point['index'] == index and list(map(number, point['rates'])) == list(coordinate),
            'OUTPUT_GRID', '$/grid', 'design indices and rates must match the declared product order')
        need([row['id'] for row in point['scenarios']] == scenario_ids,
            'OUTPUT_SCENARIOS', '$/grid', 'complete shared scenario identity and order required')
        for row in point['scenarios']:
            row_key = tuple(id(row[key]) for key in ('clocks', 'terminals', 'latency_ns', 'moe_ns', 'mfu_coefficient'))
            if summary and row_key in validated_rows:
                continue
            if summary:
                validated_rows.add(row_key)
                _validate_summary_row(row, clocks, participants, ticks_per_ns, spec['useful_flops'], validated_locals)
                continue
            need([local['clock'] for local in row['clocks']] == sorted(clocks),
                'OUTPUT_CLOCKS', '$/grid', 'every distinct clock retained exactly once')
            layers, moe, expected_terminals = [], [], []
            for local in row['clocks']:
                name = local['clock']
                duration = number(local['moe_ticks'])
                need(duration > 0 and local['rank'] == clocks[name]['rank'], 'OUTPUT_CLOCKS', '$/grid', 'positive local duration and unchanged rank required')
                if name not in participants:
                    need(local['layer_ticks'] is None, 'OUTPUT_CLOCKS', '$/grid', 'standalone clocks cannot gain layer participation')
                    continue
                participant = participants[name]
                layer = max(participant['attention_offset_ticks'], participant['release_offset_ticks'] + duration)
                need(local['layer_ticks'] == rational(layer), 'OUTPUT_LAYER', '$/grid', 'fixed-anchor composition required')
                layers.append(layer)
                moe.append(duration)
                expected_terminals.extend((value, dict(clock=name, rank=local['rank'], branch=branch)) for branch, value in
                    (('attention', Fraction(participant['attention_offset_ticks'])), ('release_plus_moe', participant['release_offset_ticks'] + duration)))
            latency = max(layers) / Fraction(spec['composition']['ticks_per_ns'])
            need(row['latency_ns'] == rational(latency) and row['moe_ns'] == rational(max(moe) / Fraction(spec['composition']['ticks_per_ns']))
                and row['mfu_coefficient'] == rational(spec['useful_flops'] / latency), 'OUTPUT_LAYER', '$/grid', 'exact latency, MoE and inverted MFU required')
            need(row['terminals'] == [terminal for value, terminal in expected_terminals if value == max(layers)],
                'OUTPUT_TERMINALS', '$/grid', 'all and only tied declared terminals required')
        need(point['latency_ns'] == _bounds(point['scenarios'], 'latency_ns') and point['moe_ns'] == _bounds(point['scenarios'], 'moe_ns')
            and point['mfu'] == _mfu(spec, point['scenarios']), 'OUTPUT_INTERVAL', '$/grid', 'exact extrema and witnesses required')
    need(all(number(rate) == 1 for rate in result['grid'][result['baseline_index']]['rates']),
        'OUTPUT_GRID', '$/baseline_index', 'all-unit baseline required')
    if _prepared is None:
        need(formulas.identity(result['model']) == result['input_identity'] and
            formulas.identity(result['request']) == result['request_identity'], 'OUTPUT_IDENTITY', '$', 'complete input identities required')
    if _canonical is not None:
        need(_canonical.result is result, 'OUTPUT_IDENTITY', '$', 'execution-owned canonical result required')
    need(_output_identity(_canonical if _canonical is not None else
        {key: value for key, value in result.items() if key != 'output_identity'}) == result['output_identity'],
        'OUTPUT_IDENTITY', '$/output_identity', 'complete output identity required')


def _line_value(line, inverse_scale):
    return line[0] + line[1] * inverse_scale


def _response_line(mask, factors, scenario, midpoint):
    if not mask:
        return Fraction(1), Fraction(0)
    if scenario['sharing'] == 'priority':
        return next(factors[index] for index in scenario['priority'] if mask & (1 << index))
    selected = [factor for index, factor in enumerate(factors) if mask & (1 << index)]
    if scenario['sharing'] == 'shared_serial':
        return tuple(sum(factor[coefficient] for factor in selected) / len(selected) for coefficient in (0, 1))
    return max(selected, key=lambda line: _line_value(line, midpoint))


def _curve_lines(spec, timing, scenario, selected, midpoint):
    factors = [(Fraction(0), Fraction(1)) if index in selected and scenario['exposure_mask'] & (1 << index)
        else (Fraction(1), Fraction(0)) for index in range(len(spec['families']))]
    participants = {row['clock']: row for row in spec['composition']['participants']}
    moe_lines, layer_lines = [], []
    ticks_per_ns = Fraction(spec['composition']['ticks_per_ns'])
    for clock in timing['clocks']:
        if clock['clock'] not in participants:
            continue
        intercept, slope = Fraction(0), Fraction(0)
        for row in clock['histogram'][scenario['membership']]:
            local_intercept, local_slope = _response_line(row['family_mask'], factors, scenario, midpoint)
            intercept += row['duration_ticks'] * local_intercept
            slope += row['duration_ticks'] * local_slope
        moe_lines.append((intercept / ticks_per_ns, slope / ticks_per_ns))
        participant = participants[clock['clock']]
        for branch, line in (
                ('attention', (Fraction(participant['attention_offset_ticks']) / ticks_per_ns, Fraction(0))),
                ('release_plus_moe', ((participant['release_offset_ticks'] + intercept) / ticks_per_ns, slope / ticks_per_ns))):
            layer_lines.append((line, dict(clock=clock['clock'], rank=clock['rank'], branch=branch)))
    return moe_lines, layer_lines


def _curve_segments(spec, timing, scenario, selected, lower, upper):
    boundaries = sorted({lower, upper} | ({Fraction(1)} if lower < 1 < upper else set()))
    segments = []
    for first, last in zip(boundaries, boundaries[1:]):
        moe_lines, layer_lines = _curve_lines(spec, timing, scenario, selected, (first + last) / 2)
        knots = {first, last}
        for lines in (moe_lines, [line for line, terminal in layer_lines]):
            for left, right in itertools.combinations(lines, 2):
                if left[1] != right[1]:
                    crossing = (right[0] - left[0]) / (left[1] - right[1])
                    if first < crossing < last:
                        knots.add(crossing)
        ordered = sorted(knots)
        for start, end in zip(ordered, ordered[1:]):
            midpoint = (start + end) / 2
            moe = max(moe_lines, key=lambda line: _line_value(line, midpoint))
            layer = max((line for line, terminal in layer_lines), key=lambda line: _line_value(line, midpoint))
            terminals = [terminal for line, terminal in layer_lines if _line_value(line, midpoint) == _line_value(layer, midpoint)]
            segments.append(dict(inverse_scale_interval=[rational(start), rational(end)],
                layer_intercept=rational(layer[0]), layer_slope=rational(layer[1]),
                moe_intercept=rational(moe[0]), moe_slope=rational(moe[1]), terminals=terminals))
    return segments


def _surface(spec, timing, request, surface, scenario):
    axis_families = {axis['id']: axis['family'] for axis in request['axes']}
    selected = {index for index, family in enumerate(spec['families']) if family['id'] in {axis_families[name] for name in surface['axes']}}
    lower_scale, upper_scale = map(Fraction, surface['domain'])
    lower, upper = 1 / upper_scale, 1 / lower_scale
    segments = _curve_segments(spec, timing, scenario, selected, lower, upper)
    baseline = Fraction(spec['composition']['baseline_layer_ticks']) / Fraction(spec['composition']['ticks_per_ns'])
    equal_intervals = []
    for segment in segments:
        first, last = map(number, segment['inverse_scale_interval'])
        intercept, slope = number(segment['layer_intercept']), number(segment['layer_slope'])
        if slope == 0:
            if intercept == baseline:
                equal_intervals.append((1 / last, 1 / first))
        else:
            crossing = (baseline - intercept) / slope
            if first <= crossing <= last:
                equal_intervals.append((1 / crossing, 1 / crossing))
    merged = []
    for first, last in sorted(equal_intervals):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    knots = []
    for inverse_scale in sorted({number(endpoint) for segment in segments for endpoint in segment['inverse_scale_interval']}):
        rates = [1 / inverse_scale if index in selected else Fraction(1) for index in range(len(spec['families']))]
        row = _scenario(spec, timing, scenario, rates)
        knots.append(dict(inverse_scale=rational(inverse_scale), latency_ns=row['latency_ns'], terminals=row['terminals']))
    saturation_segments = segments
    asymptotic = None
    if surface['extrapolation'] == 'inverse_scale_to_infinity':
        saturation_segments = _curve_segments(spec, timing, scenario, selected, Fraction(0), upper)
        asymptotic = saturation_segments[0]['layer_intercept']
    finite = None
    if number(saturation_segments[0]['layer_slope']) == 0:
        plateau = number(saturation_segments[0]['layer_intercept'])
        endpoint = number(saturation_segments[0]['inverse_scale_interval'][1])
        for segment in saturation_segments[1:]:
            if number(segment['layer_slope']) != 0 or number(segment['layer_intercept']) != plateau:
                break
            endpoint = number(segment['inverse_scale_interval'][1])
        finite = rational(1 / endpoint)
    return dict(id=surface['id'], scenario=scenario['id'], axes=surface['axes'], domain=surface['domain'],
        extrapolation=surface['extrapolation'], segments=segments, knots=knots,
        baseline_break_even_scale_intervals=[[rational(first), rational(last)] for first, last in merged],
        finite_saturation_scale=finite, asymptotic_latency_ns=asymptotic)
