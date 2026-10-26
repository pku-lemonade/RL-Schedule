"""Invented unit-clock contracts, with no measured or device-specific inputs."""
from copy import deepcopy


def joint_model():
    domain = dict(timebase='unit_tick', convention='synthetic_useful_work', participants=['clock-a'],
        engine_scope='synthetic_engine', rate_unit='work_per_tick')
    nodes = []
    for name, duration, parents, observed in [('stage-a', '12', [], ['0', '12']),
            ('stage-b', '18', ['stage-a'], ['12', '30'])]:
        nodes.append(dict(id=name, clock='clock-a', dependencies=parents, owner=name + '-owner',
            resources=[name + '-resource'], service=dict(interval=[duration, duration], reference=duration),
            residual=dict(interval=['0', '3'], reference='0'), observed_interval=observed))
    resources = [node['resources'][0] for node in nodes]
    return dict(version='caller-sensitivity-model-v1', mode='hypothetical_joint',
        identity_hooks={key: 'synthetic-' + key for key in ('workload', 'source', 'routes', 'overlap', 'timebase_contract')},
        timebase='unit_tick', useful_flops=120, peak_domain=domain,
        peak=dict(kind='hypothesis', domain=deepcopy(domain), parameter=None, interval=['6', '6'],
            evidence='synthetic-peak-not-measured', missing_input=None), evidence_identity='invented-boundaries', unavailable=[],
        nodes=nodes, boundaries=[dict(clock='clock-a', interval=['0', '30'], terminals=['stage-b'])],
        event_inventory=[dict(id=node['id'] + '-event', owner=node['resources'][0], clock='clock-a', interval=node['observed_interval']) for node in nodes],
        event_partition=[dict(event=node['id'] + '-event', node=node['id'], role='service_evidence_only') for node in nodes],
        scenarios=[dict(id='synthetic-serial', assignments=[dict(resource=resource, bank='virtual-bank', port='virtual-port', route='virtual-route')
            for resource in resources], route_contention=[dict(route='virtual-route', choice='serialize', node_order=['stage-a', 'stage-b'])])],
        families=[dict(id=node['id'] + '-rate', law='inverse_exclusive_service', nodes=[node['id']], authority='caller_hypothesis') for node in nodes])


def portfolio_model():
    base = joint_model()
    for key in ('nodes', 'boundaries', 'event_inventory', 'event_partition', 'scenarios'):
        del base[key]
    base.update(mode='evidence_portfolio', terminals=[dict(id='clock-a', full=['0', '30'], fixed=['0', '20'], responsive=['9', '30'])],
        families=[dict(id=name, law='inverse_response_cap', cap=cap, binding_identity='synthetic-' + name, authority='caller_evidence')
            for name, cap in [('transfer', '12'), ('compute', '6')]])
    return base


def request(model):
    catalog = model.catalog()
    uncertainty = dict(coupling='shared_box_and_scenario_across_grid')
    if catalog['mode'] == 'hypothetical_joint':
        uncertainty.update(costs='model_independent_box', sharing='model_scenarios')
    else:
        uncertainty.update(costs='unresolved_not_instantiated', sharing='fixed_observed_overlap',
            readiness=dict(mode='shared', radius='0', path_count=0))
    return dict(version='caller-design-space-request-v1', mode=catalog['mode'], input_identity=model.identity,
        study_id='synthetic-grid', claim='conditional_not_physical_truth', grid_budget=9,
        axes=[dict(id=family['id'], family=family['id'], unit='dimensionless_service_scale', domain=['0.5', '2'],
            grid=['0.5', '1', '2'], exposure=dict(identity=family['id'] + '-exposure', interval=['1', '1'])) for family in catalog['families']],
        uncertainty=uncertainty)


def scenario_model():
    spec = joint_model()
    spec['nodes'][1]['dependencies'] = []
    for node, bounds in zip(spec['nodes'], (['30', '30'], ['0', '6'])):
        node['service'] = dict(interval=bounds, reference=bounds[0])
        node['residual'] = dict(interval=['0', '0'], reference='0')
    spec['boundaries'][0]['terminals'] = ['stage-a', 'stage-b']
    overlap = deepcopy(spec['scenarios'][0])
    overlap.update(id='synthetic-overlap', route_contention=[dict(route='virtual-route', choice='no_added_order', node_order=[])])
    spec['scenarios'].append(overlap)
    return spec
