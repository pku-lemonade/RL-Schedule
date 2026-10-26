"""CPU-only, state-isolated exploration of explicit caller sensitivity contracts.

No evidence files are discovered or loaded by the engine. Observations stay
separate from hypothetical exclusive costs and never become additional credits.
"""
import argparse
from copy import deepcopy
from decimal import Decimal, localcontext
import itertools
import json
from pathlib import Path

if __package__:
    from . import counterfactual as formulas
    from . import design_space_schema as wire
else:
    import counterfactual as formulas
    import design_space_schema as wire

Rejected = wire.Rejected
need = wire.need


def _span(value, path, positive=False, unit=False):
    lower, upper = map(Decimal, value)
    need(lower <= upper and lower >= 0 and (not positive or lower > 0) and (not unit or upper <= 1),
        'INTERVAL', path, 'ordered endpoints in the declared nonnegative domain required')
    return lower, upper


def _index(rows, key, path):
    result = {row[key]: row for row in rows}
    need(len(result) == len(rows), 'DUPLICATE_ID', path, 'unique identities required')
    return result


def _unavailable(missing):
    return dict(status='unavailable', minimal_missing_input=missing)


class DesignSpace:
    """Own all mutable inputs and return defensive snapshots at every public API."""

    def __init__(self, specification):
        owned = deepcopy(specification)
        self._mode = wire.mode_of(owned)
        wire.validate(owned, wire.model_schema(self._mode))
        self._specification = owned
        self._identity = formulas.identity(owned)
        with localcontext() as context:
            context.prec = 28
            self._admit()

    @property
    def identity(self):
        return self._identity

    def catalog(self):
        return deepcopy(dict(input_identity=self.identity, mode=self._mode,
            identity_hooks=self._specification['identity_hooks'], families=self._specification['families'],
            baseline=self._baseline, unavailable=self._unavailable_outputs(),
            claim='caller evidence identity is a binding hook, not independent authentication'))

    def specification(self):
        return deepcopy(self._specification)

    def schema(self):
        return wire.request_schema(self._mode)

    def template(self):
        uncertainty = dict(coupling=wire.COUPLING)
        if self._mode == 'hypothetical_joint':
            uncertainty.update(costs='model_independent_box', sharing='model_scenarios')
        else:
            uncertainty.update(costs='unresolved_not_instantiated', sharing='fixed_observed_overlap',
                readiness=dict(mode=None, radius=None, path_count=None))
        return dict(version=wire.REQUEST_VERSION, mode=self._mode, input_identity=self.identity,
            study_id=None, claim='conditional_not_physical_truth', grid_budget=None,
            axes=[], uncertainty=uncertainty)

    def _peak(self):
        peak = self._specification['peak']
        if peak['kind'] == 'unavailable':
            return None
        return dict(peak['domain'], **{key: peak[key] for key in ('kind', 'parameter', 'interval', 'evidence') if peak[key] is not None})

    def _mfu(self, latency):
        if self._peak() is None:
            return _unavailable(self._specification['peak']['missing_input'])
        return formulas.mfu(self._specification['useful_flops'], latency, self._peak(), self._specification['peak_domain'])

    def _unavailable_outputs(self):
        result = {row['output']: _unavailable(row['missing_input']) for row in self._specification['unavailable']}
        result['physical_capacity'] = _unavailable('direct_service_capacity_and_physical_binding_contract')
        result['physical_bottlenecks'] = _unavailable('direct_physical_service_ownership_evidence')
        if self._peak() is None:
            result['numeric_mfu'] = _unavailable(self._specification['peak']['missing_input'])
        elif self._peak()['kind'] == 'symbolic':
            result['numeric_mfu'] = _unavailable('applicable_numeric_peak_authority')
        if self._mode == 'evidence_portfolio':
            result['joint_latency'] = _unavailable('exclusive_joint_service_law_and_cost_ownership')
        return result

    def _admit(self):
        spec = self._specification
        _index(spec['unavailable'], 'output', '$/unavailable')
        need(spec['peak_domain']['timebase'] == spec['timebase'], 'TIMEBASE', '$/peak_domain', 'matching timebase required')
        peak = spec['peak']
        kind = peak['kind']
        required = {'symbolic': {'domain', 'parameter'}, 'accepted': {'domain', 'interval', 'evidence'},
            'hypothesis': {'domain', 'interval', 'evidence'}, 'unavailable': {'missing_input'}}[kind]
        need({key for key, value in peak.items() if key != 'kind' and value is not None} == required,
            'PEAK_FIELDS', '$/peak', 'explicit values only for the selected peak kind; all other fields null')
        if kind != 'unavailable':
            need(peak['domain'] == spec['peak_domain'], 'PEAK_DOMAIN', '$/peak/domain', 'exact applicability contract required')
        if kind in ('accepted', 'hypothesis'):
            _span(peak['interval'], '$/peak/interval', positive=True)
        self._families = _index(spec['families'], 'id', '$/families')
        if self._mode == 'evidence_portfolio':
            self._admit_portfolio()
        else:
            self._admit_joint()

    def _admit_portfolio(self):
        spec = self._specification
        terminals = _index(spec['terminals'], 'id', '$/terminals')
        for row in terminals.values():
            for field in ('full', 'fixed', 'responsive'):
                _span(row[field], '$/terminals/' + row['id'] + '/' + field)
        for family in self._families.values():
            need(Decimal(family['cap']) >= 0, 'CAP', '$/families/' + family['id'], 'nonnegative evidence-bound cap required')
        self._profile = dict(workload_identity=spec['identity_hooks']['workload'], evidence_identity=spec['evidence_identity'],
            timebase=spec['timebase'], useful_flops=spec['useful_flops'], peak_domain=spec['peak_domain'], post_moe_terminals=[],
            ranks=[dict(rank=row['id'], full=row['full'], attention=row['fixed'], moe=row['responsive']) for row in terminals.values()])
        try:
            projection = formulas.profile_counterfactual(self._profile, self._profile['workload_identity'], peak=self._peak())
        except formulas.ContractError as error:
            raise Rejected('SOURCE_BOUNDARY', '$/terminals', str(error)) from error
        self._baseline = dict(authority='caller_evidence', evidence_identity=spec['evidence_identity'],
            latency_interval=projection['baseline']['layer_interval'], mfu=self._mfu(projection['baseline']['layer_interval']),
            source_projection=projection)

    def _admit_joint(self):
        spec = self._specification
        nodes = self._nodes = _index(spec['nodes'], 'id', '$/nodes')
        boundaries = self._boundaries = _index(spec['boundaries'], 'clock', '$/boundaries')
        need(set(boundaries) == {node['clock'] for node in nodes.values()} == set(spec['peak_domain']['participants']),
            'CLOCK_COVERAGE', '$/boundaries', 'exact local-clock coverage required')
        _index(spec['nodes'], 'owner', '$/nodes/owner')
        resources = {resource for node in nodes.values() for resource in node['resources']}
        reference = {}
        for name, node in nodes.items():
            path = '$/nodes/' + name
            need(set(node['dependencies']) <= set(nodes) and name not in node['dependencies'], 'DEPENDENCY', path, 'known distinct parents required')
            need(all(nodes[parent]['clock'] == node['clock'] for parent in node['dependencies']),
                'TIMEBASE', path, 'no cross-clock timestamp splicing')
            boundary = _span(boundaries[node['clock']]['interval'], '$/boundaries/' + node['clock'])
            observed = _span(node['observed_interval'], path + '/observed_interval')
            need(boundary[0] <= observed[0] <= observed[1] <= boundary[1], 'SOURCE_BOUNDARY', path, 'observation inside its source boundary required')
            for field in ('service', 'residual'):
                bounds = _span(node[field]['interval'], path + '/' + field)
                need(bounds[0] <= Decimal(node[field]['reference']) <= bounds[1], 'REFERENCE_BOX', path + '/' + field,
                    'one common reference inside the independent caller box required')
            reference[name] = Decimal(node['service']['reference']) + Decimal(node['residual']['reference'])
        dependencies = {name: node['dependencies'] for name, node in nodes.items()}
        try:
            formulas.ordered(nodes, dependencies)
        except formulas.ContractError as error:
            raise Rejected('CYCLE', '$/nodes', 'acyclic operation graph required') from error
        for clock, boundary in boundaries.items():
            local = {name for name, node in nodes.items() if node['clock'] == clock}
            sinks = local - {parent for name in local for parent in nodes[name]['dependencies']}
            need(set(boundary['terminals']) == sinks, 'TERMINAL_COVERAGE', '$/boundaries/' + clock,
                'all and only frozen local DAG sinks required')
        events = _index(spec['event_inventory'], 'id', '$/event_inventory')
        partition = _index(spec['event_partition'], 'event', '$/event_partition')
        need(set(events) == set(partition), 'EVENT_COVERAGE', '$/event_partition', 'every source event assigned exactly once')
        for name, event in events.items():
            path = '$/event_partition/' + name
            need(event['clock'] in boundaries and event['owner'] in resources, 'EVENT_OWNER', path, 'known source resource and clock required')
            span = _span(event['interval'], path)
            boundary = tuple(map(Decimal, boundaries[event['clock']]['interval']))
            need(boundary[0] <= span[0] <= span[1] <= boundary[1], 'SOURCE_BOUNDARY', path, 'event inside its source boundary required')
            assignment = partition[name]
            if assignment['role'] == 'context_only':
                need(assignment['node'] is None, 'NO_DOUBLE_COUNT', path, 'context has no cost owner')
            else:
                target = assignment['node']
                need(target in nodes and nodes[target]['clock'] == event['clock'] and event['owner'] in nodes[target]['resources'],
                    'EVENT_OWNER', path, 'one matching-clock node and declared resource required')
        owned = set()
        for family in self._families.values():
            selected = set(family['nodes'])
            need(selected <= set(nodes) and not owned.intersection(selected), 'NO_DOUBLE_COUNT', '$/families/' + family['id'],
                'disjoint known exclusive service nodes required; no residual or child credit')
            owned.update(selected)
        self._scenarios = []
        _index(spec['scenarios'], 'id', '$/scenarios')
        for scenario in spec['scenarios']:
            path = '$/scenarios/' + scenario['id']
            assignments = _index(scenario['assignments'], 'resource', path + '/assignments')
            need(set(assignments) == resources, 'RESOURCE_COVERAGE', path, 'explicit bank/port/route aliases for every resource required')
            routes = {assignment['route'] for assignment in assignments.values()}
            relations = _index(scenario['route_contention'], 'route', path + '/route_contention')
            need(set(relations) == routes, 'ROUTE_COVERAGE', path, 'explicit choice for every route alias required')
            changed = deepcopy(dependencies)
            for route, relation in relations.items():
                order = relation['node_order']
                if relation['choice'] == 'no_added_order':
                    need(not order, 'RESOURCE_ORDER', path, 'no_added_order requires an empty order')
                    continue
                members = {name for name, node in nodes.items() if any(assignments[resource]['route'] == route for resource in node['resources'])}
                need(set(order) == members and len(order) >= 2, 'RESOURCE_ORDER', path, 'serialized order must exactly cover bound service nodes')
                need(len({nodes[name]['clock'] for name in order}) == 1, 'TIMEBASE', path, 'same-clock serialization only')
                for predecessor, successor in zip(order, order[1:]):
                    if predecessor not in changed[successor]:
                        changed[successor].append(predecessor)
            try:
                topological = formulas.ordered(nodes, changed)
            except formulas.ContractError as error:
                raise Rejected('CYCLE', path, 'sharing order conflicts with frozen dependencies') from error
            for clock, boundary in boundaries.items():
                duration = Decimal(boundary['interval'][1]) - Decimal(boundary['interval'][0])
                elapsed, unused = formulas.elapsed(topological, changed, reference, boundary['terminals'])
                need(elapsed == duration and elapsed > 0, 'BASELINE_REPRODUCTION', path + '/' + clock,
                    'one common caller reference must reproduce the frozen local boundary; no fitting')
            self._scenarios.append(dict(id=scenario['id'], dependencies=changed, order=topological))
        duration = max(Decimal(row['interval'][1]) - Decimal(row['interval'][0]) for row in boundaries.values())
        self._baseline = dict(authority='caller_evidence', evidence_identity=spec['evidence_identity'],
            latency_interval=formulas.strings((duration, duration)), mfu=self._mfu((duration, duration)),
            boundaries=spec['boundaries'], caller_reference_is_not_measured_stage_cost=True)

    def evaluate(self, request):
        owned = deepcopy(request)
        mode = wire.mode_of(owned)
        wire.validate(owned, wire.request_schema(mode))
        need(mode == self._mode, 'MODE', '$/mode', 'request mode must match frozen model')
        need(owned['input_identity'] == self.identity and formulas.identity(self._specification) == self.identity,
            'IDENTITY', '$/input_identity', 'unchanged frozen workload, routes, observations and model required')
        with localcontext() as context:
            context.prec = 28
            axes = self._axes(owned)
            result = self._joint(owned, axes) if mode == 'hypothetical_joint' else self._portfolio(owned, axes)
        return deepcopy(dict(version=wire.RESULT_VERSION, mode=mode, input_identity=self.identity,
            request_identity=formulas.identity(owned), identity_hooks=self._specification['identity_hooks'],
            study_id=owned['study_id'], evidence_backed_baseline=self._baseline, unavailable=self._unavailable_outputs(),
            frozen_peak_unchanged=True, observed_intervals_added_as_cost=False, refitted=False, **result))

    def _axes(self, request):
        _index(request['axes'], 'id', '$/axes')
        _index(request['axes'], 'family', '$/axes/family')
        need(len({axis['exposure']['identity'] for axis in request['axes']}) == len(request['axes']),
            'EXPOSURE_IDENTITY', '$/axes', 'distinct independent exposure identities required')
        axes, size = [], 1
        for axis in request['axes']:
            path = '$/axes/' + axis['id']
            need(axis['family'] in self._families, 'UNSUPPORTED_FAMILY', path + '/family',
                'missing input: registered exclusive law and ownership for this family')
            domain = _span(axis['domain'], path + '/domain', positive=True)
            grid = tuple(map(Decimal, axis['grid']))
            need(list(grid) == sorted(set(grid)) and grid[0] == domain[0] and grid[-1] == domain[1] and Decimal(1) in grid,
                'GRID_DOMAIN', path, 'strictly increasing positive grid includes domain endpoints and fixed baseline 1')
            exposure = _span(axis['exposure']['interval'], path + '/exposure', unit=True)
            axes.append(dict(source=axis, grid=grid, exposure=exposure, family=self._families[axis['family']]))
            size *= len(grid)
        need(size <= request['grid_budget'], 'GRID_BUDGET', '$/grid_budget', 'full Cartesian product exceeds explicit CPU grid budget')
        return axes

    def _points(self, axes):
        return [dict(id='point-' + str(index), rates={axis['source']['id']: str(scale) for axis, scale in zip(axes, coordinates)})
            for index, coordinates in enumerate(itertools.product(*(axis['grid'] for axis in axes)))]

    @staticmethod
    def _relation(left, right):
        strict, nonregression, witnesses = [], [], []
        for row, other in zip(left['scenarios'], right['scenarios']):
            need(row['id'] == other['id'], 'SCENARIO_IDENTITY', '$', 'matching scenarios required')
            strict.append(Decimal(row['latency_interval'][1]) < Decimal(other['latency_interval'][0]))
            nonregression.append(Decimal(row['latency_interval'][1]) <= Decimal(other['latency_interval'][0]))
            for edge in (0, 1):
                if Decimal(row['fixed_shared_corner_latency'][edge]) < Decimal(other['fixed_shared_corner_latency'][edge]):
                    witnesses.append(dict(scenario=row['id'], global_corner=edge))
        monotone = all(Decimal(left['rates'][name]) >= Decimal(right['rates'][name]) for name in left['rates'])
        return dict(guaranteed_strict=all(strict), guaranteed_nonregression=all(nonregression) or monotone,
            opportunity_with_fixed_shared_witness=bool(witnesses), witnesses=witnesses)

    @staticmethod
    def _pareto(points, comparisons):
        dominated = {row['right'] for row in comparisons if row['guaranteed_strict']}
        return dict(guaranteed_dominated=sorted(dominated), conservative_outer_set=[row['id'] for row in points if row['id'] not in dominated],
            interpretation='latency-only conservative robust Pareto outer set; not exact physical or investment Pareto')

    def _joint(self, request, axes):
        nodes = self._nodes
        bindings = {name: axis for axis in axes for name in axis['family']['nodes']}
        terminals = [name for boundary in self._boundaries.values() for name in boundary['terminals']]
        owners = {name: [node['owner'] + '/exclusive_service', node['owner'] + '/exclusive_residual'] for name, node in nodes.items()}
        points = self._points(axes)
        for point in points:
            costs, corners = {}, [{}, {}]
            for name, node in nodes.items():
                service, residual = tuple(map(Decimal, node['service']['interval'])), tuple(map(Decimal, node['residual']['interval']))
                axis = bindings.get(name)
                scale = Decimal(1) if axis is None else Decimal(point['rates'][axis['source']['id']])
                exposure = (Decimal(0), Decimal(0)) if axis is None else axis['exposure']
                factors = tuple(1 - value + value / scale for value in exposure)
                costs[name] = service[0] * min(factors) + residual[0], service[1] * max(factors) + residual[1]
                for edge in (0, 1):
                    corners[edge][name] = service[edge] * factors[edge] + residual[edge]
            point['scenarios'] = []
            for scenario in self._scenarios:
                order, dependencies = scenario['order'], scenario['dependencies']
                endpoints = [formulas.elapsed(order, dependencies, {name: cost[edge] for name, cost in costs.items()}, terminals)[0] for edge in (0, 1)]
                need(endpoints[0] > 0, 'ZERO_LATENCY', '$/axes', 'entire parameter box requires a positive MFU denominator')
                corner_latency = [formulas.elapsed(order, dependencies, corner, terminals)[0] for corner in corners]
                point['scenarios'].append(dict(id=scenario['id'], latency_interval=formulas.strings(endpoints), mfu=self._mfu(endpoints),
                    fixed_shared_corner_latency=formulas.strings(corner_latency),
                    bottlenecks=formulas.bottlenecks(order, dependencies, costs, terminals, endpoints, owners)))
            envelope = [min(Decimal(row['latency_interval'][0]) for row in point['scenarios']),
                max(Decimal(row['latency_interval'][1]) for row in point['scenarios'])]
            point['latency_interval'] = formulas.strings(envelope)
            point['mfu'] = self._mfu(envelope)
        baseline = next(point for point in points if all(Decimal(rate) == 1 for rate in point['rates'].values()))
        for point in points:
            point['improvement'] = self._relation(point, baseline)
        comparisons = [dict(left=left['id'], right=right['id'], **self._relation(left, right))
            for left in points for right in points if left is not right]
        transitions = []
        lookup = {tuple(point['rates'].values()): point for point in points}
        for point in points:
            for axis_index, axis in enumerate(axes):
                name = axis['source']['id']
                position = axis['grid'].index(Decimal(point['rates'][name]))
                if position == len(axis['grid']) - 1:
                    continue
                coordinates = list(point['rates'].values())
                coordinates[axis_index] = str(axis['grid'][position + 1])
                neighbor = lookup[tuple(coordinates)]
                for left, right in zip(point['scenarios'], neighbor['scenarios']):
                    if left['bottlenecks'] != right['bottlenecks']:
                        transitions.append(dict(left=point['id'], right=neighbor['id'], axis=name, scenario=left['id'],
                            bracket=[point['rates'][name], neighbor['rates'][name]]))
        surfaces = dict(nodes={name: dict(service_symbol=name + '/service', residual_symbol=name + '/residual',
            axis=None if name not in bindings else bindings[name]['source']['id'],
            formula='F[node]=max(F[parents],0)+service[node]*(1-exposure+exposure/scale)+residual[node]') for name in nodes},
            unselected_node_rule='scale=1; exposure has no effect',
            scenario_dependencies={row['id']: row['dependencies'] for row in self._scenarios}, terminals=terminals,
            latency='L=max(F[terminals])', break_even='L(scales,shared_latents,scenario)=L(all_scales_1,same_latents,same_scenario)',
            critical_region='chosen predecessor finish >= every competing predecessor finish; equality defines ties',
            coordinates='bounded inverse dimensionless rates from the explicit request',
            representation='symbolic max-plus surface, not a solved physical threshold')
        return dict(grid=points, comparisons=comparisons, pareto=self._pareto(points, comparisons), baseline_grid_id=baseline['id'],
            axes=[axis['source'] for axis in axes], uncertainty=request['uncertainty'],
            bottleneck_transition_brackets=transitions, break_even_surfaces=surfaces,
            authority='caller_hypothesis', interval_kind='independent shared-box endpoint hull per fixed categorical scenario',
            ownership_partition_identity=formulas.identity(self._specification['event_partition']),
            shared_scenario_identity=formulas.identity(self._specification['scenarios']), residual_costs_unchanged=True,
            corner_rule='two fixed global service/residual/exposure corners shared by every configuration; no fitting')

    def _portfolio(self, request, axes):
        readiness = request['uncertainty']['readiness']
        radius, count = Decimal(readiness['radius']), readiness['path_count']
        need(radius >= 0 and (count > 0 or radius == 0), 'READINESS', '$/uncertainty/readiness', 'nonnegative radius and explicit affected-path count required')
        need(readiness['mode'] != 'shared' or (radius == 0 and count == 0), 'READINESS', '$/uncertainty/readiness', 'shared mode requires explicit zero radius and path count')
        baseline = Decimal(self._baseline['latency_interval'][0])
        facets = {}
        for axis in axes:
            rows = []
            cap = Decimal(axis['family']['cap'])
            for scale in axis['grid']:
                delta = sorted(value * cap * (1 / scale - 1) for value in axis['exposure'])
                envelope = Decimal(0) if scale == 1 else radius * count
                response = delta[0] - envelope, delta[-1] + envelope
                minimum = min(Decimal(row['moe'][1]) - Decimal(row['moe'][0]) for row in self._profile['ranks'])
                need(minimum + response[0] >= 0, 'NEGATIVE_DURATION', '$/axes/' + axis['source']['id'], 'response box crosses source start; refine explicit domain')
                hypothesis = None if response == (0, 0) else dict(status='hypothesis', reason='explicit inverse-cap sensitivity and readiness enclosure',
                    workload_identity=self._profile['workload_identity'], interval=formulas.strings(response))
                projection = formulas.profile_counterfactual(self._profile, self._profile['workload_identity'], hypothesis, peak=self._peak())
                bounds = tuple(map(Decimal, projection['counterfactual']['layer_interval']))
                need(bounds[0] > 0, 'ZERO_LATENCY', '$/axes', 'positive latency box required')
                threshold = Decimal(projection['transition_thresholds']['moe_completion_offset'])
                rows.append(dict(id=str(scale), rate=str(scale), latency_interval=formulas.strings(bounds), mfu=self._mfu(bounds),
                    service_delta_interval=formulas.strings((delta[0], delta[-1])), response_interval=formulas.strings(response),
                    bottlenecks=projection['bottlenecks'],
                    improvement=dict(guaranteed_strict=bounds[1] < baseline, guaranteed_nonregression=bounds[1] <= baseline,
                        opportunity_with_box_endpoint_witness=readiness['mode'] == 'shared' and bounds[0] < baseline,
                        possible_outer_not_witness=readiness['mode'] == 'bounded' and bounds[0] < baseline),
                    break_even=dict(equation='exposure*cap*(1/scale-1)+eta=offset', cap=str(cap), offset=str(threshold),
                        eta_interval=formulas.strings((-envelope, envelope)), analytic_rate='exposure*cap/(exposure*cap+offset-eta)',
                        applicability='positive denominator and within the caller domain; not an asserted physical threshold',
                        sufficient_radius_upper_exclusive=None if count == 0 or delta[-1] >= 0 else str(-delta[-1] / count)),
                    authority='caller_evidence_cap_with_hypothetical_exposure_and_readiness'))
            comparisons = []
            for left in rows:
                for right in rows:
                    if left is right:
                        continue
                    comparisons.append(dict(left=left['id'], right=right['id'],
                        guaranteed_strict=Decimal(left['latency_interval'][1]) < Decimal(right['latency_interval'][0]),
                        guaranteed_nonregression=Decimal(left['latency_interval'][1]) <= Decimal(right['latency_interval'][0]) or
                            readiness['mode'] == 'shared' and Decimal(left['rate']) >= Decimal(right['rate'])))
            facets[axis['source']['id']] = dict(results=rows, comparisons=comparisons, pareto=self._pareto(rows, comparisons),
                bottleneck_transition_brackets=[dict(left=left['id'], right=right['id'], bracket=[left['rate'], right['rate']])
                    for left, right in zip(rows, rows[1:]) if left['bottlenecks'] != right['bottlenecks']])
        points = self._points(axes)
        for point in points:
            point['conditional_views'] = {name: next(row for row in facets[name]['results'] if row['rate'] == rate)
                for name, rate in point['rates'].items()}
            point['joint_architecture_latency'] = None
        return dict(grid=points, axes=[axis['source'] for axis in axes], uncertainty=request['uncertainty'], conditional_facets=facets,
            joint_pareto=None, authority='separate_conditional_views_not_additive_joint_credit',
            residual_costs='unresolved_not_instantiated', interval_kind='readiness outer enclosure' if readiness['mode'] == 'bounded' else 'shared exposure endpoint hull')


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    schema = commands.add_parser('schema')
    schema.add_argument('--kind', choices=('model', 'request'), required=True)
    schema.add_argument('--mode', choices=('hypothetical_joint', 'evidence_portfolio'), required=True)
    for command in ('catalog', 'template', 'evaluate'):
        child = commands.add_parser(command)
        child.add_argument('--model', required=True)
        if command == 'evaluate':
            child.add_argument('--request', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'schema':
            result = wire.model_schema(args.mode) if args.kind == 'model' else wire.request_schema(args.mode)
        else:
            model = DesignSpace(wire.strict_json(Path(args.model).read_bytes()))
            if args.command == 'evaluate':
                result = model.evaluate(wire.strict_json(Path(args.request).read_bytes()))
            else:
                result = getattr(model, args.command)()
        print(formulas.encoded(result).decode(), end='')
        return 0
    except Rejected as error:
        print(json.dumps(error.decision, sort_keys=True))
        return 2
    except (OSError, formulas.ContractError) as error:
        print(json.dumps(dict(status='rejected', code='INPUT_OR_FORMULA', reason=str(error)), sort_keys=True))
        return 2


def main():
    raise SystemExit(cli())


if __name__ == '__main__':
    main()
