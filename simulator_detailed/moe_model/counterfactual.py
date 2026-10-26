"""Identity-bound, conditional max-plus counterfactuals in an explicit timebase.

This module has no native, filesystem, topology-discovery or fitting path. Binding
authorities are inputs, not inferred from parameter names. Resource arbitration
is represented by explicit predecessor orders; unknown sharing is a hypothesis.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json


class ContractError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ContractError(reason)


def number(value):
    require(type(value) in (str, int, Decimal), 'exact decimal string or integer required')
    try:
        result = Decimal(value)
    except Exception as error:
        raise ContractError('invalid decimal') from error
    require(result.is_finite(), 'finite number required')
    return result


def interval(value, nonnegative=True):
    require(isinstance(value, (list, tuple)) and len(value) == 2, 'two interval endpoints required')
    lower, upper = map(number, value)
    require(lower <= upper and (not nonnegative or lower >= 0), 'ordered interval required')
    return lower, upper


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def identity(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strings(values):
    return [str(value) for value in values]


def ordered(nodes, dependencies):
    pending, result = set(nodes), []
    while pending:
        ready = sorted(name for name in pending if not pending.intersection(dependencies[name]))
        require(ready, 'acyclic dependencies required')
        result.extend(ready)
        pending.difference_update(ready)
    return result


def law_value(law, parameters):
    require(set(law) == {'fixed', 'terms'}, 'explicit additive mechanism law required')
    result = number(law['fixed'])
    require(result >= 0, 'nonnegative fixed service work')
    for term in law['terms']:
        require(set(term) == {'parameter', 'kind', 'work'}, 'exact mechanism term required')
        work = number(term['work'])
        require(work >= 0 and term['parameter'] in parameters, 'bound nonnegative mechanism extent')
        value = number(parameters[term['parameter']])
        require(value > 0, 'positive mechanism parameter required')
        require(term['kind'] in ('inverse_rate', 'linear_latency'), 'unsupported mechanism law')
        result += work / value if term['kind'] == 'inverse_rate' else work * value
    return result


def path_expressions(order, dependencies, costs, residual_order, terminals):
    bits = {name: 1 << index for index, name in enumerate(residual_order)}
    completed = {}
    for name in order:
        incoming = {}
        for parent in dependencies[name]:
            for mask, constant in completed[parent].items():
                incoming[mask] = max(incoming.get(mask, constant), constant)
        incoming = incoming or {0: Decimal(0)}
        completed[name] = {mask | bits.get(name, 0): constant + (Decimal(0) if name in bits else costs[name])
            for mask, constant in incoming.items()}
    final = {}
    for name in terminals:
        for mask, constant in completed[name].items():
            final[mask] = max(final.get(mask, constant), constant)
    return completed, final


def anchored(base, lower_target, upper_target, bound, deltas):
    lower, upper = interval(bound)
    require(base and set(base) == set(lower_target) == set(upper_target), 'unchanged correlated path ownership')
    require(upper >= max(base.values()), 'anchor must contain accepted point-only path')
    changes = []
    for mask in base:
        low, high = lower_target[mask] - base[mask], upper_target[mask] - base[mask]
        for index, change in enumerate(deltas):
            low_delta, high_delta = interval(change, False)
            if mask & (1 << index):
                low += low_delta
                high += high_delta
        changes.append((low, high))
    result = (max(max(lower_target.values()), lower + min(row[0] for row in changes)),
        upper + max(row[1] for row in changes))
    require(result[0] <= result[1], 'nonempty conditional interval required')
    return result


def elapsed(order, dependencies, costs, terminals, blocked=None):
    completed = {}
    for name in order:
        parents = [completed[parent] for parent in dependencies[name] if parent in completed]
        if name == blocked or dependencies[name] and not parents:
            continue
        completed[name] = max(parents, default=Decimal(0)) + costs[name]
    return max((completed[name] for name in terminals if name in completed), default=None), completed


def bottlenecks(order, dependencies, costs, terminals, latency, owners):
    children = {name: [] for name in order}
    for name, parents in dependencies.items():
        for parent in parents:
            children[parent].append(name)
    lower_cost = {name: value[0] for name, value in costs.items()}
    upper_cost = {name: value[1] for name, value in costs.items()}
    unused, lower_finish = elapsed(order, dependencies, lower_cost, terminals)
    unused, upper_finish = elapsed(order, dependencies, upper_cost, terminals)
    suffix = {}
    for name in reversed(order):
        suffix[name] = tuple(max((costs[child][edge] + suffix[child][edge] for child in children[name]),
            default=Decimal(0)) for edge in (0, 1))
    possible, definite = [], []
    for name in owners:
        if upper_finish[name] + suffix[name][1] < latency[0]:
            continue
        possible.append(name)
        avoiding, unused = elapsed(order, dependencies, upper_cost, terminals, blocked=name)
        if avoiding is None or lower_finish[name] + suffix[name][0] >= avoiding:
            definite.append(name)
    return dict(definite=sorted(definite), possible=sorted(possible),
        interpretation='membership on at least one critical path; possible is a conservative outer set, not utilization',
        definite_resources=sorted({resource for name in definite for resource in owners[name]}),
        possible_resources=sorted({resource for name in possible for resource in owners[name]}))


def mfu(useful_flops, latency, peak, domain):
    require(type(useful_flops) is int and useful_flops > 0, 'complete useful-FLOP count required')
    lower, upper = interval(latency)
    require(lower > 0, 'positive MFU denominator')
    require(all(peak[key] == domain[key] for key in ('timebase', 'convention', 'participants', 'engine_scope', 'rate_unit')),
        'peak applicability identity mismatch')
    coefficients = (Decimal(useful_flops) / upper, Decimal(useful_flops) / lower)
    if peak['kind'] == 'symbolic':
        require(bool(peak['parameter']), 'named symbolic peak required')
        return dict(kind='symbolic', coefficients=strings(coefficients), parameter=peak['parameter'],
            formula='coefficient / positive_applicable_peak', unit='dimensionless', numeric_interval=None)
    require(peak['kind'] in ('accepted', 'hypothesis') and peak.get('evidence'), 'qualified numeric peak required')
    low_peak, high_peak = interval(peak['interval'])
    require(low_peak > 0, 'positive peak required')
    return dict(kind=peak['kind'], numeric_interval=strings((coefficients[0] / high_peak, coefficients[1] / low_peak)),
        unit='dimensionless', peak_identity=identity(peak), clipped_to_one=False)


def pipeline_interval(latency, useful_flops, pipeline, timebase):
    if pipeline is None:
        return None, None
    require(pipeline['timebase'] == timebase and pipeline.get('description'), 'explicit pipeline timebase/description')
    rows = {row['id']: row for row in pipeline['nodes']}
    require(rows and len(rows) == len(pipeline['nodes']), 'unique pipeline instances')
    require(type(pipeline['model_depth']) is int and pipeline['model_depth'] > 0 and
        sum(row['kind'] == 'layer' for row in rows.values()) == pipeline['model_depth'], 'explicit model-depth instance coverage')
    dependencies = {name: row['dependencies'] for name, row in rows.items()}
    require(all(len(parents) == len(set(parents)) and set(parents) <= set(rows) for parents in dependencies.values()),
        'known unique pipeline dependencies')
    costs, flops = {}, 0
    for name, row in rows.items():
        require(row['kind'] in ('layer', 'external'), 'pipeline operation kind')
        costs[name] = interval(latency if row['kind'] == 'layer' else row['interval'])
        count = useful_flops if row['kind'] == 'layer' else row['useful_flops']
        require(type(count) is int and count >= 0, 'complete pipeline FLOP ledger')
        flops += count
    order = ordered(rows, dependencies)
    result = [elapsed(order, dependencies, {name: cost[edge] for name, cost in costs.items()}, rows)[0] for edge in (0, 1)]
    return result, flops


class CounterfactualModel:
    """Frozen workload, costs, DAG, resource orders and admitted parameter laws."""

    def __init__(self, specification):
        with localcontext() as context:
            context.prec = 28
            self._initialize(deepcopy(specification))

    def _initialize(self, specification):
        self.specification = specification
        self.fingerprint = identity(specification)
        require(specification['workload_identity'] and specification['timebase'] and specification['evidence_identity'],
            'fixed workload/timebase/evidence identities required')
        require(specification['scope'] in ('moe', 'layer'), 'explicit operation scope required')
        require('peak_domain' not in specification or specification['peak_domain']['timebase'] == specification['timebase'],
            'peak timebase must match the service ledger')
        self.nodes = {row['id']: row for row in specification['nodes']}
        require(self.nodes and len(self.nodes) == len(specification['nodes']), 'unique DAG nodes')
        self.dependencies = {name: list(row['dependencies']) for name, row in self.nodes.items()}
        self.costs, self.owners = {}, {}
        owned = set()
        for name, row in self.nodes.items():
            require(row['kind'] in ('point', 'conditional', 'structural'), 'unknown work cannot be free')
            cost = interval(row['interval'])
            require(row['kind'] != 'point' or cost[0] == cost[1], 'point owner requires exact accepted duration')
            if row['kind'] == 'structural':
                require(cost == (0, 0) and not row.get('owner') and not row.get('resources'), 'structural nodes cannot own cost')
            else:
                require(row.get('owner') and row['owner'] not in owned and row.get('evidence'), 'unique evidenced cost owner')
                require(len(row['resources']) == len(set(row['resources'])), 'unique owner resources')
                owned.add(row['owner'])
                self.owners[name] = row['resources']
            self.costs[name] = cost
        require(all(len(parents) == len(set(parents)) and set(parents) <= set(self.nodes)
            for parents in self.dependencies.values()), 'known unique dependencies')
        for resource, sequence in specification['resource_orders'].items():
            require(len(sequence) == len(set(sequence)) and all(name in self.nodes and
                resource in self.nodes[name].get('resources', []) for name in sequence), 'exact resource service ordering')
            for first, second in zip(sequence, sequence[1:]):
                if first not in self.dependencies[second]:
                    self.dependencies[second].append(first)
        self.order = ordered(self.nodes, self.dependencies)
        children = {parent for parents in self.dependencies.values() for parent in parents}
        self.terminals = sorted(set(self.nodes) - children)
        self.parameters = specification['parameters']
        self.bindings = specification['bindings']
        values = {name: row['value'] for name, row in self.parameters.items()}
        for name, row in self.parameters.items():
            require(row.get('unit') and row.get('resource') and row.get('evidence') in specification['admitted_bindings'],
                'admitted parameter resource/unit/evidence required')
            lower, upper = interval(row['domain'])
            require(0 < lower <= number(row['value']) <= upper, 'parameter applicability domain')
        covered = set()
        for name, binding in self.bindings.items():
            require(name in self.nodes and self.nodes[name]['kind'] == 'point' and
                binding['evidence'] in specification['admitted_bindings'] and
                binding['workload_identity'] == specification['workload_identity'] and
                binding['timebase'] == specification['timebase'], 'source-identical accepted point-service binding')
            used = {term['parameter'] for term in binding['law']['terms']}
            require(used and used <= set(self.parameters) and all(self.parameters[param]['resource'] in
                self.nodes[name]['resources'] for param in used), 'parameter-to-owner resource locality')
            covered.update(used)
            require(law_value(binding['law'], values) == self.costs[name][0], 'baseline mechanism must reproduce accepted cost without refit')
        require(covered == set(self.parameters), 'no unused or synthetic parameter bindings')
        self.correlation = specification.get('correlation')
        if self.correlation:
            residuals = self.correlation['residual_order']
            require(len(residuals) == len(set(residuals)) and set(residuals) ==
                {name for name, row in self.nodes.items() if row['kind'] == 'conditional'}, 'complete unique correlated residual ownership')
            require(len(self.correlation['deltas']) == len(residuals), 'matched differential envelope extent')
            base = {int(mask): number(value) for mask, value in self.correlation['base_expression'].items()}
            upper = interval(self.correlation['anchor_interval'])[1]
            require(all(0 <= mask < (1 << len(residuals)) for mask in base), 'known residual path masks')
            for index, name in enumerate(residuals):
                containing = [upper - value for mask, value in base.items() if mask & (1 << index)]
                require(containing and min(containing) >= 0, 'finite causal readiness cap')
                cap = max(Decimal(0), min(containing) + interval(self.correlation['deltas'][index], False)[1])
                require(self.costs[name][0] == 0 and self.costs[name][1] >= cap, 'completion bounds must enclose correlated readiness')
        self._result(self.costs, self.correlation)
        self.state_identity = self._state_identity()

    def _state_identity(self):
        return identity(dict(dependencies=self.dependencies, costs={name: strings(value) for name, value in self.costs.items()},
            owners=self.owners, order=self.order, terminals=self.terminals, parameters=self.parameters,
            bindings=self.bindings, correlation=self.correlation))

    def _result(self, costs, correlation):
        if correlation:
            expressions = [path_expressions(self.order, self.dependencies, {name: cost[edge] for name, cost in costs.items()},
                correlation['residual_order'], self.order)[1] for edge in (0, 1)]
            latency = anchored({int(mask): number(value) for mask, value in correlation['base_expression'].items()},
                expressions[0], expressions[1], correlation['anchor_interval'], correlation['deltas'])
        else:
            latency = tuple(elapsed(self.order, self.dependencies, {name: cost[edge] for name, cost in costs.items()},
                self.terminals)[0] for edge in (0, 1))
        completed = [elapsed(self.order, self.dependencies, {name: cost[edge] for name, cost in costs.items()}, self.terminals)[1]
            for edge in (0, 1)]
        return dict(operation_interval=strings(latency),
            services={name: strings(value) for name, value in sorted(costs.items())},
            completion={name: strings((completed[0][name], completed[1][name])) for name in self.order},
            bottlenecks=bottlenecks(self.order, self.dependencies, costs, self.terminals, latency, self.owners))

    def evaluate(self, workload_identity, changes=None, hypotheses=(), peak=None, counterfactual_peak=None, pipeline=None):
        with localcontext() as context:
            context.prec = 28
            return self._evaluate(workload_identity, changes or {}, hypotheses, peak, counterfactual_peak, pipeline)

    def _evaluate(self, workload_identity, changes, hypotheses, peak, counterfactual_peak, pipeline):
        require(identity(self.specification) == self.fingerprint and workload_identity == self.specification['workload_identity'],
            'fixed workload/model identity mismatch')
        require(self._state_identity() == self.state_identity, 'derived model state changed')
        require(set(changes) <= set(self.parameters), 'unsupported hardware mapping; supply an explicit response hypothesis instead')
        parameters = {name: row['value'] for name, row in self.parameters.items()}
        for name, value in changes.items():
            lower, upper = interval(self.parameters[name]['domain'])
            require(lower <= number(value) <= upper, 'counterfactual outside admitted mechanism domain')
            parameters[name] = value
        costs = dict(self.costs)
        changed, offsets = set(), {name: (Decimal(0), Decimal(0)) for name in self.nodes}
        correlation = deepcopy(self.correlation)
        for name, binding in self.bindings.items():
            value = law_value(binding['law'], parameters)
            costs[name] = value, value
            offsets[name] = value - self.costs[name][0], value - self.costs[name][1]
            if costs[name] != self.costs[name]:
                changed.add(name)
        hypothesis_owners = set()
        for hypothesis in hypotheses:
            require(hypothesis.get('id') and hypothesis.get('reason') and hypothesis['services'], 'named explicit hypothesis required')
            require(hypothesis['kind'] in ('offset', 'multiplier'), 'bounded response hypothesis required')
            bounds = interval(hypothesis['interval'], hypothesis['kind'] == 'multiplier')
            require(hypothesis['kind'] != 'multiplier' or bounds[0] > 0, 'positive response multiplier')
            for name in hypothesis['services']:
                require(name in self.owners and name not in hypothesis_owners and name not in changed,
                    'hypothesis must own a unique non-intervened service')
                hypothesis_owners.add(name)
                if hypothesis['kind'] == 'multiplier':
                    require(not correlation or name not in correlation['residual_order'], 'correlated readiness requires an additive response envelope')
                    products = [(factor - 1) * duration for factor in bounds for duration in self.costs[name]]
                    offsets[name] = min(products), max(products)
                    costs[name] = self.costs[name][0] * bounds[0], self.costs[name][1] * bounds[1]
                else:
                    offsets[name] = bounds
                    costs[name] = tuple(self.costs[name][edge] + bounds[edge] for edge in (0, 1))
                    if correlation and name in correlation['residual_order']:
                        index = correlation['residual_order'].index(name)
                        correlation['deltas'][index] = strings(tuple(number(correlation['deltas'][index][edge]) + bounds[edge]
                            for edge in (0, 1)))
                        costs[name] = Decimal(0), max(Decimal(0), self.costs[name][1] + bounds[1])
                require(costs[name][0] >= 0, 'response cannot make service readiness negative')
                changed.add(name)
        baseline = self._result(self.costs, self.correlation)
        counterfactual = self._result(costs, correlation)
        delta_low, delta_high = {}, {}
        for name in self.order:
            delta_low[name] = min((delta_low[parent] for parent in self.dependencies[name]), default=Decimal(0)) + offsets[name][0]
            delta_high[name] = max((delta_high[parent] for parent in self.dependencies[name]), default=Decimal(0)) + offsets[name][1]
        delta = (min(delta_low[name] for name in self.terminals), max(delta_high[name] for name in self.terminals))
        if not changed:
            delta = (Decimal(0), Decimal(0))
        delta = (max(delta[0], number(counterfactual['operation_interval'][0]) - number(baseline['operation_interval'][1])),
            min(delta[1], number(counterfactual['operation_interval'][1]) - number(baseline['operation_interval'][0])))
        require(delta[0] <= delta[1], 'compatible coupled and absolute response bounds')
        for result, selected_peak in ((baseline, peak), (counterfactual, counterfactual_peak or peak)):
            result['layer_interval'] = result['operation_interval'] if self.specification['scope'] == 'layer' else None
            require(pipeline is None or self.specification['scope'] == 'layer', 'MoE is not a full layer/decode baseline')
            decode, flops = pipeline_interval(result['operation_interval'], self.specification.get('useful_flops'),
                pipeline, self.specification['timebase'])
            result['decode_interval'] = None if decode is None else strings(decode)
            result['mfu'] = None if selected_peak is None else mfu(flops if decode is not None else self.specification['useful_flops'],
                decode if decode is not None else result['operation_interval'], selected_peak, self.specification['peak_domain'])
        numeric_dependent = any(selected and selected['kind'] == 'hypothesis' for selected in (peak, counterfactual_peak))
        decode_delta = None
        if pipeline is not None:
            decode_delta = ['0', '0'] if not changed else strings((number(counterfactual['decode_interval'][0]) -
                number(baseline['decode_interval'][1]), number(counterfactual['decode_interval'][1]) - number(baseline['decode_interval'][0])))
        direction = 'unchanged' if delta == (0, 0) else 'nonincreasing' if delta[1] <= 0 else 'nondecreasing' if delta[0] >= 0 else 'undetermined'
        return dict(schema='conditional-counterfactual-v1', model_identity=self.fingerprint, workload_identity=workload_identity,
            timebase=self.specification['timebase'], baseline=baseline, counterfactual=counterfactual,
            delta_interval=strings(delta), delta_semantics='coupled same-workload path response intersected with absolute interval subtraction',
            decode_delta_interval=decode_delta,
            changed_services=sorted(changed), unchanged_services=sorted(set(self.nodes) - changed),
            assumptions=deepcopy(list(hypotheses)), parameter_changes=deepcopy(changes),
            classification='hypothesis_dependent' if hypotheses or numeric_dependent or pipeline or self.correlation or any(
                value[0] != value[1] for value in self.costs.values()) else 'robust_within_admitted_domain',
            robust_conclusions=dict(direction_within_stated_hypotheses=direction,
                definite_bottlenecks=counterfactual['bottlenecks']['definite']),
            transition_thresholds=self.transitions(next(iter(changes))) if len(changes) == 1 and not hypotheses else
                dict(status='not_derived', reason='requires one admitted parameter and no response hypotheses'),
            new_cost_owners=0, refitted=False)

    def transitions(self, parameter):
        require(parameter in self.parameters, 'known transition parameter')
        if self.correlation or any(cost[0] != cost[1] for cost in self.costs.values()):
            return dict(status='not_derived', reason='conditional critical paths are not an exact affine envelope')
        kinds = {term['kind'] for binding in self.bindings.values() for term in binding['law']['terms'] if term['parameter'] == parameter}
        if len(kinds) != 1:
            return dict(status='not_derived', reason='mixed parameter response bases')
        kind = next(iter(kinds))
        base_value = number(self.parameters[parameter]['value'])
        variable = Decimal(1) / base_value if kind == 'inverse_rate' else base_value
        expressions = {}
        for name in self.order:
            slope = sum((number(term['work']) for term in self.bindings.get(name, {}).get('law', {}).get('terms', [])
                if term['parameter'] == parameter), Decimal(0))
            constant = self.costs[name][0] - slope * variable
            incoming = {pair for parent in self.dependencies[name] for pair in expressions[parent]} or {(Decimal(0), Decimal(0))}
            expressions[name] = {(prior + constant, coefficient + slope) for prior, coefficient in incoming}
            if len(expressions[name]) > 4096:
                return dict(status='not_derived', reason='bounded exact path enumeration limit')
        paths = sorted({pair for name in self.terminals for pair in expressions[name]})
        if len(paths) > 4096:
            return dict(status='not_derived', reason='bounded exact path enumeration limit')
        lower, upper = interval(self.parameters[parameter]['domain'])
        found = set()
        for index, (first, slope) in enumerate(paths):
            for second, other in paths[index + 1:]:
                if slope == other:
                    continue
                crossing = (second - first) / (slope - other)
                if crossing <= 0:
                    continue
                value = Decimal(1) / crossing if kind == 'inverse_rate' else crossing
                if lower <= value <= upper and first + slope * crossing == max(constant + coefficient * crossing for constant, coefficient in paths):
                    found.add(value)
        return dict(status='derived', parameter=parameter, values=strings(sorted(found)),
            interpretation='exact affine critical-path ties within the admitted domain; not a refit')

    def sweep(self, workload_identity, cases, **options):
        require(len({row['id'] for row in cases}) == len(cases), 'unique sweep cases')
        results = []
        for case in cases:
            require(len(case.get('changes', {})) + len(case.get('hypotheses', [])) <= 1 or case.get('combined') is True,
                'combined sweep must be explicitly selected')
            try:
                result = self.evaluate(workload_identity, case.get('changes'), case.get('hypotheses', ()), **options)
                results.append(dict(id=case['id'], status='evaluated', result=result))
            except ContractError as error:
                results.append(dict(id=case['id'], status='not_evaluated', reason=str(error)))
        return results


def profile_components(full, attention, moe):
    full, attention, moe = interval(full), interval(attention), interval(moe)
    require(all(full[0] <= start <= finish <= full[1] for start, finish in (attention, moe)), 'source-complete profile containment')
    overlap = max(Decimal(0), min(attention[1], moe[1]) - max(attention[0], moe[0]))
    covered = attention[1] - attention[0] + moe[1] - moe[0] - overlap
    return dict(full_ns=str(full[1] - full[0]), attention_ns=str(attention[1] - attention[0]),
        moe_ns=str(moe[1] - moe[0]), overlap_ns=str(overlap), union_ns=str(covered),
        residual_ns=str(full[1] - full[0] - covered), serial_addition=False)


def profile_counterfactual(profile, workload_identity, moe_completion_response=None, peak=None, pipeline=None, counterfactual_peak=None):
    """Project a declared completion response onto source-owned layer terminals.

    This does not graft another workload's DAG onto a profile. A response is an
    explicit hypothesis: fixed start, attention and producer readiness, with only
    the downstream MoE completion shifted. No profile span becomes a point owner.
    """
    with localcontext() as context:
        context.prec = 28
        require(workload_identity == profile['workload_identity'] and profile.get('evidence_identity'), 'profile workload identity')
        require(profile['peak_domain']['timebase'] == profile['timebase'], 'peak timebase must match the profile')
        require(profile['post_moe_terminals'] == [], 'unmodeled post-MoE terminals')
        response = (Decimal(0), Decimal(0))
        if moe_completion_response is not None:
            require(moe_completion_response['status'] == 'hypothesis' and moe_completion_response.get('reason') and
                moe_completion_response['workload_identity'] == workload_identity, 'explicit same-workload terminal response hypothesis')
            response = interval(moe_completion_response['interval'], False)
        ranks = []
        for row in profile['ranks']:
            baseline = profile_components(row['full'], row['attention'], row['moe'])
            require(number(row['full'][1]) == max(number(row['attention'][1]), number(row['moe'][1])), 'source-owned terminal rule')
            projected = []
            for change in response:
                moe = (number(row['moe'][0]), number(row['moe'][1]) + change)
                full = (number(row['full'][0]), max(number(row['attention'][1]), moe[1]))
                projected.append(profile_components(full, row['attention'], moe))
            ranks.append(dict(rank=row['rank'], baseline=baseline, counterfactual=projected))
        require(len({row['rank'] for row in ranks}) == len(ranks) and ranks and
            {row['rank'] for row in ranks} == set(profile['peak_domain']['participants']), 'unique complete profile participants')
        baseline = max(number(row['baseline']['full_ns']) for row in ranks)
        projected = tuple(max(number(row['counterfactual'][edge]['full_ns']) for row in ranks) for edge in (0, 1))
        outputs = []
        for latency, selected_peak in (((baseline, baseline), peak), (projected, counterfactual_peak or peak)):
            decode, flops = pipeline_interval(latency, profile['useful_flops'], pipeline, profile['timebase'])
            outputs.append(dict(layer_interval=strings(latency), decode_interval=None if decode is None else strings(decode),
                mfu=None if selected_peak is None else mfu(flops if decode is not None else profile['useful_flops'],
                    decode if decode is not None else latency, selected_peak, profile['peak_domain'])))
        attention_end = max(number(row['attention'][1]) - number(row['full'][0]) for row in profile['ranks'])
        moe_end = max(number(row['moe'][1]) - number(row['full'][0]) for row in profile['ranks'])
        threshold = attention_end - moe_end
        possible = [name for name, admitted in (('attention_terminal', response[0] <= threshold),
            ('moe_terminal', response[1] >= threshold)) if admitted]
        definite = [name for name, admitted in (('attention_terminal', response[1] <= threshold),
            ('moe_terminal', response[0] >= threshold)) if admitted]
        peak_hypothesis = any(selected and selected['kind'] == 'hypothesis' for selected in (peak, counterfactual_peak))
        return dict(ranks=ranks, baseline=outputs[0], counterfactual=outputs[1], delta_interval=strings(tuple(value - baseline for value in projected)),
            decode_delta_interval=None if pipeline is None else strings((number(outputs[1]['decode_interval'][0]) - number(outputs[0]['decode_interval'][1]),
                number(outputs[1]['decode_interval'][1]) - number(outputs[0]['decode_interval'][0]))),
            classification='hypothesis_dependent' if moe_completion_response or pipeline or peak_hypothesis else 'observed_baseline_conditional_rank_origin',
            bottlenecks=dict(definite=definite, possible=possible, interpretation='mandatory-terminal limiter, not an isolated service owner'),
            transition_thresholds=dict(moe_completion_offset=str(threshold),
                status='derived_under_fixed_attention_and_source_starts', timebase=profile['timebase']),
            rank_origin='explicit synchronized rank-local origins; no cross-rank timestamp splicing',
            response=deepcopy(moe_completion_response), new_point_owners=0, numerical_hardware_transfer_validated=False)
